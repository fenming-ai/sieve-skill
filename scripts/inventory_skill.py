#!/usr/bin/env python3
"""Create a deterministic, secret-aware inventory and reference graph for a Skill."""

from __future__ import annotations

import argparse
import json
import re
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


TEXT_SUFFIXES = {".md", ".yaml", ".yml", ".json", ".py", ".sh", ".go", ".toml"}
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore"}
SECRET_NAMES = {".env", "id_rsa", "id_ed25519", "credentials", "credentials.json"}
IGNORED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}
MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\(([^)#]+)(?:#[^)]+)?\)")
CODE_PATH = re.compile(r"`([^`\n]+)`")


def is_sensitive(relative: Path) -> bool:
    name = relative.name.lower()
    return (
        name in SECRET_NAMES
        or name.startswith(".env.")
        or name.startswith("secret.")
        or name.startswith("secrets.")
        or relative.suffix.lower() in SECRET_SUFFIXES
    )


def looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return b"\x00" in handle.read(4096)
    except OSError:
        return False


def artifact_role(relative: Path, binary: bool, sensitive: bool) -> str:
    if sensitive:
        return "secret"
    if binary:
        return "binary"
    parts = relative.parts
    if relative == Path("SKILL.md"):
        return "instruction"
    if parts[0] == "tests" or relative.name.endswith("_test.go") or relative.name.startswith("test_"):
        return "test"
    if parts[0] in {"scripts", "cmd", "internal", "src"} or relative.suffix in {".py", ".sh", ".go"}:
        return "source"
    if parts[0] == "assets":
        return "asset"
    if parts[0] == "agents":
        return "metadata"
    if parts[0] == "learns" or "history" in parts or "archive" in parts:
        return "historical"
    if parts[0] in {"references", "data", "templates"} or relative.suffix == ".md":
        return "reference"
    return "metadata"


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def markdown_headings(text: str) -> list[dict[str, object]]:
    headings: list[dict[str, object]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            if 1 <= level <= 6 and len(stripped) > level and stripped[level] == " ":
                headings.append({"line": number, "level": level, "text": stripped[level + 1 :]})
    return headings


def normalize_reference(root: Path, source: Path, raw: str, known: set[Path]) -> Path | None:
    candidate = raw.strip().split("#", 1)[0]
    if not candidate or "://" in candidate or candidate.startswith(("$", "~", "/", "<")):
        return None
    try:
        relative = (root / source.parent / candidate).resolve().relative_to(root.resolve())
    except ValueError:
        return None
    return relative if relative in known else None


def external_reference(raw: str) -> dict[str, str] | None:
    candidate = raw.strip().split("#", 1)[0]
    if candidate.startswith(("http://", "https://")):
        parsed = urlsplit(candidate)
        target = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        return {"target": target, "scope": "url"}
    if candidate.startswith("~/"):
        return {"target": candidate, "scope": "home_path"}
    if candidate.startswith("/"):
        home = str(Path.home())
        target = "~" + candidate[len(home):] if candidate.startswith(home + "/") else candidate
        return {"target": target, "scope": "absolute_path"}
    if candidate.startswith("$"):
        return {"target": candidate, "scope": "variable_path"}
    return None


def extract_references(
    root: Path, relative: Path, text: str, known: set[Path]
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    refs: dict[tuple[str, str], dict[str, str]] = {}
    external: dict[tuple[str, str, str], dict[str, str]] = {}
    for raw in MARKDOWN_LINK.findall(text):
        target = normalize_reference(root, relative, raw, known)
        if target:
            refs[(str(target), "markdown_link")] = {"target": str(target), "kind": "markdown_link"}
        elif item := external_reference(raw):
            item["kind"] = "markdown_link"
            external[(item["target"], item["scope"], item["kind"])] = item
    for raw in CODE_PATH.findall(text):
        token = raw.strip().split()[0] if raw.strip() else ""
        target = normalize_reference(root, relative, token, known)
        if target:
            refs[(str(target), "path_mention")] = {"target": str(target), "kind": "path_mention"}
        elif item := external_reference(token):
            item["kind"] = "path_mention"
            external[(item["target"], item["scope"], item["kind"])] = item
    return (
        sorted(refs.values(), key=lambda item: (item["target"], item["kind"])),
        sorted(external.values(), key=lambda item: (item["target"], item["kind"])),
    )


def inventory(root: Path) -> dict[str, object]:
    if not root.is_dir():
        raise ValueError(f"not a directory: {root}")
    if not (root / "SKILL.md").is_file():
        raise ValueError(f"SKILL.md not found: {root}")

    paths = [
        path for path in sorted(root.rglob("*"))
        if path.is_file() and not any(part in IGNORED_PARTS for part in path.relative_to(root).parts)
    ]
    known = {path.relative_to(root) for path in paths}
    files: list[dict[str, object]] = []
    text_cache: dict[Path, str] = {}

    for path in paths:
        relative = path.relative_to(root)
        sensitive = is_sensitive(relative)
        binary = False if sensitive else looks_binary(path)
        item: dict[str, object] = {
            "path": str(relative),
            "bytes": path.stat().st_size,
            "artifact_role": artifact_role(relative, binary, sensitive),
            "sensitive": sensitive,
            "content_inspected": False,
            "references": [],
            "external_references": [],
            "referenced_by": [],
            "dependency_state": "unreferenced",
            "external_callers_checked": False,
        }
        if not sensitive and not binary and path.suffix.lower() in TEXT_SUFFIXES:
            text = read_text(path)
            if text is not None:
                text_cache[relative] = text
                item["content_inspected"] = True
                item["headings"] = markdown_headings(text) if path.suffix.lower() == ".md" else []
        files.append(item)

    by_path = {Path(item["path"]): item for item in files}
    for relative, content in text_cache.items():
        refs, external_refs = extract_references(root, relative, content, known)
        by_path[relative]["references"] = refs
        by_path[relative]["external_references"] = external_refs
        for ref in refs:
            by_path[Path(ref["target"])]["referenced_by"].append({"source": str(relative), "kind": ref["kind"]})

    entry = Path("SKILL.md")
    by_path[entry]["dependency_state"] = "entrypoint"
    queue: deque[tuple[Path, int]] = deque([(entry, 0)])
    seen = {entry}
    while queue:
        current, depth = queue.popleft()
        for ref in by_path[current]["references"]:
            target = Path(ref["target"])
            state = "direct" if depth == 0 else "transitive"
            if by_path[target]["dependency_state"] == "unreferenced":
                by_path[target]["dependency_state"] = state
            if target not in seen:
                seen.add(target)
                queue.append((target, depth + 1))

    return {
        "schema_version": "sieve-inventory/v2",
        "root": str(root.resolve()),
        "file_count": len(files),
        "sensitive_file_count": sum(bool(item["sensitive"]) for item in files),
        "files": files,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = inventory(args.skill_directory.resolve())
    except ValueError as error:
        parser.error(str(error))
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

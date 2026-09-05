#!/usr/bin/env python3
"""Validate sieve-audit/v2 reports while retaining v1 compatibility."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


ASSET_TYPES = {"generic", "temporary", "proprietary", "mixed", "unknown", "not_applicable"}
ARTIFACT_ROLES = {"instruction", "reference", "source", "test", "asset", "metadata", "generated", "binary", "secret", "historical"}
LIFECYCLES = {"active", "possibly_redundant", "model_dependent", "task_dependent", "needs_test", "historical", "not_applicable"}
EVIDENCE_LEVELS = {"E0", "E1", "E2", "E3"}
VALIDATION_STATUSES = {"untested", "passed", "failed", "not_applicable"}
DEPENDENCY_STATES = {"entrypoint", "direct", "transitive", "unreferenced", "external_unknown"}
CONFIDENCE = {"low", "medium", "high"}
RECOMMENDATIONS = {"keep", "compress", "externalize", "temporary", "archive_candidate", "needs_comparison", "human_review"}


def validate_v1(data: dict[str, object]) -> list[str]:
    errors: list[str] = []
    context = data.get("audit_context") if isinstance(data.get("audit_context"), dict) else {}
    compared = context.get("compared_without_skill") is True
    model_unknown = context.get("model") in {None, "", "unknown"}
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    items = data.get("items") if isinstance(data.get("items"), list) else []
    if not items:
        errors.append("items must be a non-empty array")
    actual: Counter[str] = Counter()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"items[{index}] must be an object")
            continue
        classification = item.get("classification")
        if classification not in ASSET_TYPES - {"not_applicable"}:
            errors.append(f"items[{index}].classification is invalid")
        else:
            actual[classification] += 1
        evidence = item.get("evidence_level")
        if not compared and evidence in {"E2", "E3"}:
            errors.append(f"items[{index}] cannot use {evidence} without comparison")
        if item.get("recommendation") == "archive_candidate" and evidence != "E3":
            errors.append(f"items[{index}].archive_candidate requires E3")
        if item.get("requires_human_approval") is not True:
            errors.append(f"items[{index}].requires_human_approval must be true")
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    for name in ASSET_TYPES - {"not_applicable"}:
        if counts.get(name) != actual.get(name, 0):
            errors.append(f"summary.counts.{name} must equal {actual.get(name, 0)}")
    if model_unknown and summary.get("confidence") in {"medium", "high"}:
        errors.append("unknown model caps summary confidence at low")
    return errors


def validate_v2(data: dict[str, object]) -> list[str]:
    errors: list[str] = []
    context = data.get("audit_context") if isinstance(data.get("audit_context"), dict) else {}
    model_unknown = context.get("model") in {None, "", "unknown"}
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    if summary.get("recommendation") not in RECOMMENDATIONS:
        errors.append("summary.recommendation is invalid")
    if summary.get("confidence") not in CONFIDENCE:
        errors.append("summary.confidence is invalid")
    if model_unknown and summary.get("confidence") in {"medium", "high"}:
        errors.append("unknown model caps summary confidence at low")

    items = data.get("items") if isinstance(data.get("items"), list) else []
    if not items:
        errors.append("items must be a non-empty array")
    asset_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    for index, item in enumerate(items):
        prefix = f"items[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be an object")
            continue
        asset_type = item.get("asset_type")
        role = item.get("artifact_role")
        if asset_type not in ASSET_TYPES:
            errors.append(f"{prefix}.asset_type is invalid")
        else:
            asset_counts[asset_type] += 1
        if role not in ARTIFACT_ROLES:
            errors.append(f"{prefix}.artifact_role is invalid")
        else:
            role_counts[role] += 1
        if item.get("lifecycle") not in LIFECYCLES:
            errors.append(f"{prefix}.lifecycle is invalid")
        if role in {"secret", "binary", "generated"} and asset_type != "not_applicable":
            errors.append(f"{prefix}.{role} must use asset_type not_applicable")

        replacement = item.get("replacement_evidence")
        if not isinstance(replacement, dict):
            errors.append(f"{prefix}.replacement_evidence must be an object")
            replacement = {}
        level = replacement.get("level")
        if level not in EVIDENCE_LEVELS:
            errors.append(f"{prefix}.replacement_evidence.level is invalid")
        if level in {"E2", "E3"} and not replacement.get("comparison_id"):
            errors.append(f"{prefix}.{level} requires comparison_id")
        if not replacement.get("basis"):
            errors.append(f"{prefix}.replacement_evidence.basis is required")

        behavior = item.get("behavior_validation")
        if not isinstance(behavior, dict):
            errors.append(f"{prefix}.behavior_validation must be an object")
            behavior = {}
        status = behavior.get("status")
        if status not in VALIDATION_STATUSES:
            errors.append(f"{prefix}.behavior_validation.status is invalid")
        if status in {"passed", "failed"} and not behavior.get("evidence"):
            errors.append(f"{prefix}.behavior_validation.evidence is required for {status}")
        checks_run = behavior.get("checks_run", 0)
        if status in {"passed", "failed"} and (
            not isinstance(checks_run, int) or isinstance(checks_run, bool) or checks_run < 1
        ):
            errors.append(f"{prefix}.behavior_validation.checks_run must be at least 1 for {status}")

        dependency = item.get("dependency")
        if not isinstance(dependency, dict):
            errors.append(f"{prefix}.dependency must be an object")
            dependency = {}
        if dependency.get("state") not in DEPENDENCY_STATES:
            errors.append(f"{prefix}.dependency.state is invalid")

        confidence = item.get("confidence")
        if confidence not in CONFIDENCE:
            errors.append(f"{prefix}.confidence is invalid")
        if model_unknown and confidence in {"medium", "high"}:
            errors.append(f"{prefix} confidence must be low for unknown model")
        recommendation = item.get("recommendation")
        if recommendation not in RECOMMENDATIONS:
            errors.append(f"{prefix}.recommendation is invalid")
        if recommendation == "archive_candidate":
            if level != "E3":
                errors.append(f"{prefix}.archive_candidate requires E3")
            if dependency.get("external_callers_checked") is not True:
                errors.append(f"{prefix}.archive_candidate requires external caller review")
        if role == "secret" and recommendation not in {"keep", "human_review"}:
            errors.append(f"{prefix}.secret recommendation must be keep or human_review")
        if item.get("requires_human_approval") is not True:
            errors.append(f"{prefix}.requires_human_approval must be true")
        source = item.get("source")
        if not isinstance(source, dict) or not source.get("path"):
            errors.append(f"{prefix}.source.path is required")
        if not item.get("reason"):
            errors.append(f"{prefix}.reason is required")

    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    for group, allowed, actual in (("asset_types", ASSET_TYPES, asset_counts), ("artifact_roles", ARTIFACT_ROLES, role_counts)):
        reported = counts.get(group) if isinstance(counts.get(group), dict) else {}
        for name in allowed:
            if reported.get(name) != actual.get(name, 0):
                errors.append(f"summary.counts.{group}.{name} must equal {actual.get(name, 0)}")
    return errors


def validate(data: object) -> list[str]:
    if not isinstance(data, dict):
        return ["report must be a JSON object"]
    version = data.get("schema_version")
    if version == "sieve-audit/v1":
        errors = validate_v1(data)
    elif version == "sieve-audit/v2":
        errors = validate_v2(data)
    else:
        return ["schema_version must be sieve-audit/v1 or sieve-audit/v2"]
    not_performed = data.get("not_performed")
    if not isinstance(not_performed, list) or not any(
        isinstance(value, str) and all(word in value for word in ("修改", "移动", "归档", "删除"))
        for value in not_performed
    ):
        errors.append("not_performed must state that no modify/move/archive/delete occurred")
    return errors


def validate_source_facts(data: dict[str, object], skill_directory: Path) -> list[str]:
    errors: list[str] = []
    root = skill_directory.resolve()
    if not (root / "SKILL.md").is_file():
        return [f"skill directory has no SKILL.md: {root}"]
    items = data.get("items") if isinstance(data.get("items"), list) else []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("source"), dict):
            continue
        source = item["source"]
        raw_path = source.get("path")
        if not isinstance(raw_path, str):
            continue
        candidate = (root / raw_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            errors.append(f"items[{index}].source.path escapes skill directory")
            continue
        if not candidate.exists():
            errors.append(f"items[{index}].source.path does not exist: {raw_path}")
            continue
        line = source.get("line", 0)
        if isinstance(line, int) and line > 0 and candidate.is_file():
            try:
                line_count = len(candidate.read_text(encoding="utf-8").splitlines())
            except (OSError, UnicodeDecodeError):
                continue
            if line > line_count:
                errors.append(f"items[{index}].source.line exceeds file length: {raw_path}:{line}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--skill-directory", type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}")
        return 1
    errors = validate(data)
    if args.skill_directory is not None and isinstance(data, dict):
        errors.extend(validate_source_facts(data, args.skill_directory))
    if errors:
        print("FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"PASS: {data['schema_version']} report is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

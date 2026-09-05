#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_validator():
    path = ROOT / "scripts" / "validate_report.py"
    spec = importlib.util.spec_from_file_location("validate_report", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class InventoryTests(unittest.TestCase):
    def test_inventory_finds_skill_and_headings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            references = root / "references"
            references.mkdir()
            (root / "SKILL.md").write_text(
                "---\nname: demo\n---\n# Demo\n## Rules\nRead [rules](references/rules.md).\n",
                encoding="utf-8",
            )
            (references / "rules.md").write_text("# Rules\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "inventory_skill.py"), str(root)],
                check=True,
                capture_output=True,
                text=True,
            )
            data = json.loads(result.stdout)
            self.assertEqual(data["schema_version"], "sieve-inventory/v2")
            self.assertEqual(data["file_count"], 2)
            files = {item["path"]: item for item in data["files"]}
            self.assertEqual(files["SKILL.md"]["artifact_role"], "instruction")
            self.assertEqual(files["SKILL.md"]["headings"][1]["text"], "Rules")
            self.assertEqual(files["references/rules.md"]["dependency_state"], "direct")
            self.assertEqual(files["references/rules.md"]["referenced_by"][0]["source"], "SKILL.md")

    def test_inventory_never_reads_sensitive_content(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "SKILL.md").write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
            (root / ".env").write_text("DO_NOT_READ=this-must-not-appear\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "inventory_skill.py"), str(root)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertNotIn("this-must-not-appear", result.stdout)
            data = json.loads(result.stdout)
            secret = next(item for item in data["files"] if item["path"] == ".env")
            self.assertTrue(secret["sensitive"])
            self.assertFalse(secret["content_inspected"])
            self.assertEqual(secret["artifact_role"], "secret")
            self.assertEqual(data["sensitive_file_count"], 1)

    def test_inventory_marks_binary_without_parsing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "SKILL.md").write_text("---\nname: demo\n---\n# Demo\n", encoding="utf-8")
            (root / "tool").write_bytes(b"\x7fELF\x00payload")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "inventory_skill.py"), str(root)],
                check=True,
                capture_output=True,
                text=True,
            )
            data = json.loads(result.stdout)
            binary = next(item for item in data["files"] if item["path"] == "tool")
            self.assertEqual(binary["artifact_role"], "binary")
            self.assertFalse(binary["content_inspected"])

    def test_inventory_records_external_references_without_following_them(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "SKILL.md").write_text(
                "# Demo\nRead `~/repos/other/SKILL.md` and "
                "[docs](https://example.com/guide?token=must-not-survive).\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "inventory_skill.py"), str(root)],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertNotIn("must-not-survive", result.stdout)
            data = json.loads(result.stdout)
            external = data["files"][0]["external_references"]
            self.assertEqual(
                {(item["target"], item["scope"]) for item in external},
                {
                    ("~/repos/other/SKILL.md", "home_path"),
                    ("https://example.com/guide", "url"),
                },
            )

    def test_inventory_rejects_missing_skill_file(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "inventory_skill.py"), directory],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("SKILL.md not found", result.stderr)

    def test_inventory_ignores_generated_dependency_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
            dependency = root / ".venv" / "lib"
            dependency.mkdir(parents=True)
            (dependency / "third_party.py").write_text("secret = 'dependency'\n", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "inventory_skill.py"), str(root)],
                check=True,
                capture_output=True,
                text=True,
            )
            data = json.loads(result.stdout)
            self.assertEqual(data["file_count"], 1)
            self.assertEqual(data["files"][0]["path"], "SKILL.md")


class ReportValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_validator()
        cls.valid = json.loads(
            (ROOT / "tests" / "fixtures" / "valid-report.json").read_text(encoding="utf-8")
        )

    def test_valid_report(self):
        self.assertEqual(self.validator.validate(self.valid), [])

    def test_unknown_model_caps_confidence(self):
        data = json.loads(json.dumps(self.valid))
        data["summary"]["confidence"] = "medium"
        errors = self.validator.validate(data)
        self.assertTrue(any("caps summary confidence" in error for error in errors))

    def test_comparison_required_for_e2(self):
        data = json.loads(json.dumps(self.valid))
        data["items"][0]["evidence_level"] = "E2"
        errors = self.validator.validate(data)
        self.assertTrue(any("without comparison" in error for error in errors))

    def test_archive_candidate_requires_e3_and_human_approval(self):
        data = json.loads(json.dumps(self.valid))
        data["items"][0]["recommendation"] = "archive_candidate"
        data["items"][0]["requires_human_approval"] = False
        errors = self.validator.validate(data)
        self.assertTrue(any("requires E3" in error for error in errors))
        self.assertTrue(any("must be true" in error for error in errors))

    def test_counts_must_match_items(self):
        data = json.loads(json.dumps(self.valid))
        data["summary"]["counts"]["unknown"] = 0
        errors = self.validator.validate(data)
        self.assertTrue(any("counts.unknown" in error for error in errors))


class ReportV2ValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_validator()
        cls.valid = json.loads(
            (ROOT / "tests" / "fixtures" / "valid-report-v2.json").read_text(encoding="utf-8")
        )

    def clone(self):
        return json.loads(json.dumps(self.valid))

    def test_valid_v2_report(self):
        self.assertEqual(self.validator.validate(self.valid), [])

    def test_secret_must_be_not_applicable_and_conservative(self):
        data = self.clone()
        secret = data["items"][1]
        secret["asset_type"] = "temporary"
        secret["recommendation"] = "compress"
        errors = self.validator.validate(data)
        self.assertTrue(any("secret must use asset_type not_applicable" in error for error in errors))
        self.assertTrue(any("secret recommendation" in error for error in errors))

    def test_e2_requires_comparison_identifier(self):
        data = self.clone()
        data["items"][0]["replacement_evidence"]["level"] = "E2"
        errors = self.validator.validate(data)
        self.assertTrue(any("E2 requires comparison_id" in error for error in errors))

    def test_passed_behavior_requires_evidence(self):
        data = self.clone()
        data["items"][0]["behavior_validation"] = {
            "status": "passed",
            "evidence": "",
            "checks_run": 0,
        }
        errors = self.validator.validate(data)
        self.assertTrue(any("evidence is required for passed" in error for error in errors))
        self.assertTrue(any("checks_run must be at least 1" in error for error in errors))

    def test_archive_requires_e3_and_external_caller_review(self):
        data = self.clone()
        item = data["items"][0]
        item["recommendation"] = "archive_candidate"
        errors = self.validator.validate(data)
        self.assertTrue(any("requires E3" in error for error in errors))
        self.assertTrue(any("external caller review" in error for error in errors))

    def test_role_and_asset_counts_must_match(self):
        data = self.clone()
        data["summary"]["counts"]["artifact_roles"]["secret"] = 0
        errors = self.validator.validate(data)
        self.assertTrue(any("artifact_roles.secret" in error for error in errors))

    def test_source_fact_validation_rejects_missing_path_and_bad_line(self):
        data = self.clone()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
            data["items"][0]["source"] = {"path": "SKILL.md", "line": 2}
            data["items"][1]["source"] = {"path": ".env", "line": 0}
            errors = self.validator.validate_source_facts(data, root)
            self.assertTrue(any("exceeds file length" in error for error in errors))
            self.assertTrue(any("does not exist: .env" in error for error in errors))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.cli import main
from ci_kit.migrate import CHECKS_AFTER, MARKER, MigrateResult, legacy_check_names, migrate, parse_marker, render_marker
from ci_kit.plan import Finding, build_plan
from module_test_support import git, legacy_scenario_repo, lifecycle_template_repo, mkdtemp

VALIDATE = ".github/workflows/validate.yml"
_TEMPLATE: Path | None = None


def template() -> Path:
    global _TEMPLATE
    if _TEMPLATE is None:
        _TEMPLATE = lifecycle_template_repo()
    return _TEMPLATE


def subjects(repo: Path) -> list[str]:
    return git(repo, "log", "--format=%s").splitlines()


class CheckNamesTests(unittest.TestCase):
    def test_component_less_legacy_has_only_the_contract_job(self):
        repo = legacy_scenario_repo(template(), "allure-external")
        self.assertEqual(legacy_check_names(repo / VALIDATE), ["Shared template contract"])

    def test_component_jobs_follow_in_file_order(self):
        repo = legacy_scenario_repo(template(), "java")
        names = legacy_check_names(repo / VALIDATE)
        self.assertEqual(names[0], "Shared template contract")
        self.assertGreater(len(names), 1)

    def test_job_id_used_when_name_missing(self):
        path = mkdtemp("names-") / "validate.yml"
        path.write_text("name: V\non: push\njobs:\n  alpha:\n    runs-on: x\n  beta:\n    name: Beta Job\n    runs-on: x\n")
        self.assertEqual(legacy_check_names(path), ["alpha", "Beta Job"])


class PreconditionTests(unittest.TestCase):
    def setUp(self):
        self.src = str(template())
        self.repo = legacy_scenario_repo(template(), "allure-external")
        self.head = git(self.repo, "rev-parse", "HEAD")

    def run_migrate(self, to: str = "v99.0.0") -> MigrateResult:
        return migrate(self.repo, "core", to, src_override=self.src)

    def assert_untouched(self):
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), self.head)

    def test_dirty_tree_is_001(self):
        (self.repo / "stray.txt").write_text("x")
        result = self.run_migrate()
        self.assertEqual([f.code for f in result.findings], ["CIK-MIGRATE-001"])
        self.assertEqual(result.commits, [])
        self.assert_untouched()

    def test_ref_mismatch_is_002(self):
        result = self.run_migrate("v99.1.0")
        self.assertEqual([f.code for f in result.findings], ["CIK-MIGRATE-002"])
        self.assertIn("v99.0.0", result.findings[0].message)
        self.assertEqual(result.commits, [])
        self.assert_untouched()

    def test_hand_edited_validate_is_003_with_diff(self):
        path = self.repo / VALIDATE
        path.write_text(path.read_text().replace("Shared template contract", "My contract"))
        git(self.repo, "commit", "-qam", "edit")
        head = git(self.repo, "rev-parse", "HEAD")
        result = self.run_migrate()
        self.assertEqual([f.code for f in result.findings], ["CIK-MIGRATE-003"])
        self.assertIn("+++ repo/" + VALIDATE, result.findings[0].message)
        self.assertIn("--- template/" + VALIDATE, result.findings[0].message)
        self.assertLessEqual(len(result.findings[0].message.splitlines()), 45)
        self.assertEqual(result.commits, [])
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)

    def test_toolkit_ref_pin_difference_is_not_drift(self):
        path = self.repo / VALIDATE
        path.write_text(path.read_text().replace("@v99.0.0", "@v98.0.0"))
        git(self.repo, "commit", "-qam", "pin")
        self.assertEqual(self.run_migrate().findings, [])

    def test_already_migrated_is_004(self):
        self.assertEqual(self.run_migrate().findings, [])
        head = git(self.repo, "rev-parse", "HEAD")
        result = self.run_migrate()
        self.assertEqual([f.code for f in result.findings], ["CIK-MIGRATE-004"])
        self.assertEqual(result.commits, [])
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)

    def test_module_without_area_is_004(self):
        result = migrate(self.repo, "java", "v99.0.0", src_override=self.src)
        self.assertEqual([f.code for f in result.findings], ["CIK-MIGRATE-004"])
        self.assert_untouched()

    def test_missing_legacy_answers_is_002(self):
        (self.repo / ".copier-answers.yml").unlink()
        git(self.repo, "commit", "-qam", "rm")
        result = self.run_migrate()
        self.assertEqual([f.code for f in result.findings], ["CIK-MIGRATE-002"])


class MigrateTests(unittest.TestCase):
    def setUp(self):
        self.src = str(template())
        self.repo = legacy_scenario_repo(template(), "allure-external")

    def test_two_commits_and_ownership_handoff(self):
        result = migrate(self.repo, "core", "v99.0.0", src_override=self.src)
        self.assertEqual(result.findings, [])
        self.assertEqual((result.module, result.area, result.ref), ("core", "validate", "v99.0.0"))
        self.assertEqual(result.checks_before, ["Shared template contract"])
        self.assertEqual(result.checks_after, CHECKS_AFTER["core"])
        self.assertEqual(subjects(self.repo)[:2], ["chore(ci-kit): add core module", "chore(ci-kit): hand off validate to core"])
        self.assertEqual(result.commits, [git(self.repo, "rev-parse", "HEAD~1"), git(self.repo, "rev-parse", "HEAD")])
        self.assertEqual(yaml.safe_load((self.repo / ".copier-answers.yml").read_text())["handed_off"], ["validate"])
        self.assertTrue((self.repo / ".copier-answers.core.yml").is_file())
        self.assertIn("name: Validate", (self.repo / VALIDATE).read_text())
        self.assertNotIn("Shared template contract", (self.repo / VALIDATE).read_text())
        self.assertEqual(build_plan(self.repo, src_override=self.src).findings, [])
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_step_two_failure_rolls_back(self):
        head = git(self.repo, "rev-parse", "HEAD")
        with mock.patch("ci_kit.migrate.copier_copy_command", return_value=["false"]):
            result = migrate(self.repo, "core", "v99.0.0", src_override=self.src)
        self.assertEqual([f.code for f in result.findings], ["CIK-UPDATE-001"])
        self.assertEqual(result.commits, [])
        self.assertEqual(git(self.repo, "rev-parse", "HEAD"), head)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")

    def test_option_like_ref_refused(self):
        result = migrate(self.repo, "core", "--evil", src_override=self.src)
        self.assertEqual(result.commits, [])
        self.assertTrue(result.findings)


class MarkerTests(unittest.TestCase):
    def result(self) -> MigrateResult:
        return MigrateResult("core", "validate", "v3.2.0", ["Shared template contract"], ["ci / gate"], ["a", "b"], [])

    def test_round_trip_has_exact_keys(self):
        body = "intro\n\n" + render_marker(self.result()) + "\n\ntrailer"
        data = parse_marker(body)
        self.assertEqual(
            data,
            {"module": "core", "area": "validate", "ref": "v3.2.0", "checks_before": ["Shared template contract"],
             "checks_after": ["ci / gate"], "commits": ["a", "b"], "findings": []},
        )
        self.assertTrue(render_marker(self.result()).startswith(MARKER + "\n```json\n"))

    def test_findings_serialised(self):
        result = self.result()
        result.findings = [Finding("CIK-MIGRATE-001", "core", "dirty", None)]
        data = parse_marker(render_marker(result))
        self.assertEqual(data["findings"], [{"code": "CIK-MIGRATE-001", "module": "core", "message": "dirty", "path": None}])

    def test_missing_or_malformed_marker_raises(self):
        for body in ("", "no marker", MARKER + "\ntext", MARKER + "\n```json\n{bad\n```", MARKER + "\n```json\n[1]\n```", MARKER + "\n```json\n{}"):
            with self.subTest(body=body), self.assertRaises(ValueError):
                parse_marker(body)


class CliTests(unittest.TestCase):
    def test_json_and_exit(self):
        repo = legacy_scenario_repo(template(), "allure-external")
        out = mkdtemp("json-") / "out.json"
        code = main(["migrate", "--module", "core", "--repo", str(repo), "--to", "v99.0.0", "--template-src", str(template()), "--json", str(out)])
        self.assertEqual(code, 0)
        data = json.loads(out.read_text())
        self.assertEqual(data["module"], "core")
        self.assertEqual(data["area"], "validate")
        self.assertEqual(len(data["commits"]), 2)
        self.assertEqual(data["findings"], [])

    def test_findings_exit_1(self):
        repo = legacy_scenario_repo(template(), "allure-external")
        code = main(["migrate", "--module", "core", "--repo", str(repo), "--to", "v99.1.0", "--template-src", str(template())])
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()

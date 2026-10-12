from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.drift import drift_findings
from ci_kit.plan import build_plan, discover
from module_test_support import overlap_template_repo, rendered_from, rendered_repo, shared_template_repo


def codes(findings) -> list[str]:
    return [finding.code for finding in findings]


def bump_ref(repo: Path, old: str, new: str) -> None:
    path = repo / ".github/workflows/validate.yml"
    path.write_text(path.read_text().replace(f"@{old}", f"@{new}"))


class DriftTests(unittest.TestCase):
    def setUp(self):
        self.src = str(shared_template_repo())
        self.repo = rendered_repo("core", "java")
        self.answers = discover(self.repo)[0]

    def drift(self):
        return drift_findings(self.repo, self.answers, self.src)

    def test_clean_repo_has_no_drift(self):
        self.assertEqual(self.drift(), [])

    def test_edited_declaration_is_cik_core_010_with_diff(self):
        path = self.repo / ".ci/modules/java.yml"
        path.write_text(path.read_text().replace('"21"', '"17"'))
        findings = self.drift()
        self.assertEqual(codes(findings), ["CIK-CORE-010"])
        self.assertEqual(findings[0].module, "java")
        self.assertEqual(findings[0].path, ".ci/modules/java.yml")
        self.assertIn("+++ repo/.ci/modules/java.yml", findings[0].message)
        self.assertIn("--- template/.ci/modules/java.yml", findings[0].message)
        self.assertLessEqual(len(findings[0].message.splitlines()), 41)

    def test_bumped_caller_ref_is_not_drift(self):
        bump_ref(self.repo, "v99.0.0", "v99.0.7")
        self.assertEqual(self.drift(), [])

    def test_deleted_owned_file_is_cik_core_010(self):
        (self.repo / ".github/workflows/validate.yml").unlink()
        findings = self.drift()
        self.assertEqual(codes(findings), ["CIK-CORE-010"])
        self.assertEqual(findings[0].path, ".github/workflows/validate.yml")
        self.assertIn("missing", findings[0].message)

    def test_project_files_outside_render_set_are_not_drift(self):
        (self.repo / "README.md").write_text("mine\n")
        (self.repo / ".github/workflows/custom.yml").write_text("name: x\n")
        self.assertEqual(self.drift(), [])

    def test_two_modules_rendering_same_path_is_cik_core_011(self):
        src = str(overlap_template_repo())
        repo = rendered_from(Path(src), "core", "java")
        findings = drift_findings(repo, discover(repo)[0], src)
        self.assertEqual(codes(findings), ["CIK-CORE-011"])
        self.assertEqual(findings[0].path, ".ci/modules/core.yml")
        self.assertIn("core", findings[0].message)
        self.assertIn("java", findings[0].message)

    def test_unrenderable_commit_is_cik_core_010_not_exception(self):
        answers = self.answers["java"]
        answers.write_text(answers.read_text().replace("_commit: v99.0.0", "_commit: v0.0.0-missing"))
        findings = self.drift()
        self.assertEqual(codes(findings), ["CIK-CORE-010"])
        self.assertTrue(findings[0].message.startswith("cannot render module at v0.0.0-missing: "))

    def test_build_plan_runs_drift_for_valid_modules_only(self):
        path = self.repo / ".ci/modules/java.yml"
        path.write_text(path.read_text().replace('"21"', '"17"'))
        self.assertEqual(codes(build_plan(self.repo, src_override=self.src).findings), ["CIK-CORE-010"])
        self.assertEqual(build_plan(self.repo, drift=False).findings, [])

    def test_broken_declaration_does_not_also_report_drift(self):
        (self.repo / ".ci/modules/java.yml").write_text("a: [")
        self.assertEqual(codes(build_plan(self.repo, src_override=self.src).findings), ["CIK-CORE-004"])


if __name__ == "__main__":
    unittest.main()

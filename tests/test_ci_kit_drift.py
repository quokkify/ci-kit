from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from unittest import mock

from ci_kit.drift import STARTER_PATHS, drift_findings, render_module
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

    def test_starter_paths_match_copier_skip_if_exists(self):
        import yaml

        config = yaml.safe_load((Path(__file__).resolve().parents[1] / "copier.yml").read_text())
        self.assertEqual(STARTER_PATHS, {path.lstrip("/") for path in config["_skip_if_exists"]})

    def test_edited_starter_files_are_not_drift(self):
        (self.repo / "README.md").write_text("# Project\n")
        (self.repo / ".github/pull_request_template.md").write_text("## Why\n")
        self.assertEqual(self.drift(), [])
        (self.repo / "docs/ci-kit.md").write_text("edited\n")
        self.assertEqual(codes(self.drift()), ["CIK-CORE-010"])

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

    def test_untrusted_src_path_is_cik_core_010(self):
        answers = self.answers["java"]
        answers.write_text(answers.read_text().replace(self.src, "https://evil.example/quokkify/ci-kit"))
        findings = drift_findings(self.repo, {"java": answers})
        self.assertEqual(codes(findings), ["CIK-CORE-010"])
        self.assertIn("untrusted template source: https://evil.example/quokkify/ci-kit", findings[0].message)

    def test_trailing_newline_src_path_is_untrusted(self):
        answers = self.answers["java"]
        answers.write_text(answers.read_text().replace(f"_src_path: {self.src}", '_src_path: "https://github.com/quokkify/ci-kit\\n"'))
        findings = drift_findings(self.repo, {"java": answers})
        self.assertEqual(codes(findings), ["CIK-CORE-010"])
        self.assertIn("untrusted template source", findings[0].message)

    def test_trusted_github_sources_are_accepted_by_pattern(self):
        from ci_kit.drift import TRUSTED_SRC_RE

        for src in ("https://github.com/quokkify/ci-kit", "https://github.com/quokkify/ci-kit.git", "gh:quokkify/ci-kit", "git@github.com:quokkify/ci-kit.git"):
            self.assertTrue(TRUSTED_SRC_RE.fullmatch(src), src)
        for src in ("https://github.com/quokkify/ci-kit-evil", "https://github.com/evil/ci-kit", "/tmp/ci-kit", "https://github.com/quokkify/ci-kit\n"):
            self.assertFalse(TRUSTED_SRC_RE.fullmatch(src), src)

    def test_option_like_commit_is_cik_core_010(self):
        answers = self.answers["java"]
        answers.write_text(answers.read_text().replace("_commit: v99.0.0", "_commit: --help"))
        findings = self.drift()
        self.assertEqual(codes(findings), ["CIK-CORE-010"])
        self.assertIn("cannot render module", findings[0].message)

    def test_option_like_src_override_is_cik_core_010(self):
        findings = drift_findings(self.repo, self.answers, "--help")
        self.assertEqual(set(codes(findings)), {"CIK-CORE-010"})

    def test_render_command_is_untrusted_and_separates_args(self):
        # Copier lets core read the seeded legacy answers inside the resolved destination without --trust;
        # drift runs on fork pull requests, so the render never trusts the template or runs its tasks.
        real = subprocess.run
        seen = []

        def spy(cmd, *args, **kwargs):
            seen.append(cmd)
            return real(cmd, *args, **kwargs)

        with mock.patch("ci_kit.drift.subprocess.run", spy), tempfile.TemporaryDirectory() as tmp:
            render_module(self.answers["java"], Path(tmp) / "r", self.src)
        self.assertNotIn("--trust", seen[0])
        self.assertIn("--skip-tasks", seen[0])
        self.assertEqual(seen[0][-3], "--")

    def test_changed_path_before_ref_is_still_drift(self):
        path = self.repo / ".github/workflows/validate.yml"
        path.write_text(path.read_text().replace("workflows/validate.yml@", "workflows/other.yml@"))
        self.assertEqual(codes(self.drift()), ["CIK-CORE-010"])

    def test_glued_quote_after_ref_is_not_masked(self):
        path = self.repo / ".github/workflows/validate.yml"
        path.write_text(path.read_text().replace("@v99.0.0", '@v99.0.0"'))
        self.assertEqual(codes(self.drift()), ["CIK-CORE-010"])

    def test_unreadable_answers_message_has_no_none(self):
        answers = self.answers["java"]
        answers.write_text("a: [")
        findings = self.drift()
        self.assertEqual(codes(findings), ["CIK-CORE-010"])
        self.assertTrue(findings[0].message.startswith("cannot read answers file: "))

    def test_copier_timeout_is_cik_core_010(self):
        with mock.patch("ci_kit.drift.subprocess.run", side_effect=subprocess.TimeoutExpired("copier", 300)):
            findings = self.drift()
        self.assertEqual(set(codes(findings)), {"CIK-CORE-010"})
        self.assertIn("timed out", findings[0].message)

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

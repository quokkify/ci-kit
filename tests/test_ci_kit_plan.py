from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.cli import main
from ci_kit.plan import build_plan, discover
from module_test_support import rendered_repo


def codes(plan) -> list[str]:
    return [finding.code for finding in plan.findings]


def repo_with_java_decl(text: str) -> Path:
    repo = rendered_repo("core", "java")
    (repo / ".ci/modules/java.yml").write_text(text)
    return repo


class PlanTests(unittest.TestCase):
    def test_valid_core_java_repo_has_no_findings(self):
        plan = build_plan(rendered_repo("core", "java"), drift=False)
        self.assertEqual(plan.findings, [])
        self.assertEqual(plan.modules, ["core", "java"])
        self.assertEqual(plan.matrices["java"][0]["id"], "app-java")
        self.assertEqual(plan.matrices["core"], [])

    def test_discover_ignores_legacy_answers(self):
        repo = rendered_repo("core")
        (repo / ".copier-answers.yml").write_text("module: legacy\n")
        answers, declarations = discover(repo)
        self.assertEqual(list(answers), ["core"])
        self.assertEqual(list(declarations), ["core"])

    def test_missing_declaration_is_cik_core_002(self):
        repo = rendered_repo("core", "java")
        (repo / ".ci/modules/java.yml").unlink()
        self.assertEqual(codes(build_plan(repo, drift=False)), ["CIK-CORE-002"])

    def test_missing_answers_is_cik_core_002(self):
        repo = rendered_repo("core", "java")
        (repo / ".copier-answers.java.yml").unlink()
        self.assertEqual(codes(build_plan(repo, drift=False)), ["CIK-CORE-002"])

    def test_unparseable_or_non_mapping_declaration_is_cik_core_004(self):
        for text in ("a: [", "- 1\n- 2\n"):
            with self.subTest(text=text):
                self.assertEqual(codes(build_plan(repo_with_java_decl(text), drift=False)), ["CIK-CORE-004"])

    def test_schema_violation_is_cik_core_004(self):
        self.assertEqual(codes(build_plan(repo_with_java_decl("schema_version: 1\ncomponents: []\n"), drift=False)), ["CIK-CORE-004"])

    def test_unknown_schema_version_is_cik_core_001(self):
        self.assertEqual(codes(build_plan(repo_with_java_decl("schema_version: 99\ncomponents: []\n"), drift=False)), ["CIK-CORE-001"])

    def test_unknown_module_is_cik_core_001(self):
        repo = rendered_repo("core")
        (repo / ".copier-answers.foo.yml").write_text("module: foo\n")
        plan = build_plan(repo, drift=False)
        self.assertEqual(codes(plan), ["CIK-CORE-001"])
        self.assertIn("module foo is not supported by this ci-kit", plan.findings[0].message)

    def test_java_without_core_is_cik_core_003(self):
        self.assertEqual(codes(build_plan(rendered_repo("java"), drift=False)), ["CIK-CORE-003"])

    def test_broken_core_yields_one_finding_for_core_only(self):
        repo = rendered_repo("core", "java")
        (repo / ".ci/modules/core.yml").write_text("a: [")
        plan = build_plan(repo, drift=False)
        self.assertEqual(codes(plan), ["CIK-CORE-004", "CIK-CORE-003"])


class CliTests(unittest.TestCase):
    def run_cli(self, *argv: str, env: dict | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_doctor_exit_code_and_annotation_format(self):
        repo = rendered_repo("core", "java")
        self.assertEqual(self.run_cli("doctor", "--repo", str(repo))[0], 0)
        (repo / ".ci/modules/java.yml").unlink()
        code, out, _ = self.run_cli("doctor", "--repo", str(repo), env={"GITHUB_ACTIONS": ""})
        self.assertEqual(code, 1)
        self.assertRegex(out, r"^CIK-CORE-002 java \.ci/modules/java\.yml: .+ \(docs/errors/CIK-CORE-002\.md\)$")
        code, out, _ = self.run_cli("doctor", "--repo", str(repo), env={"GITHUB_ACTIONS": "true"})
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("::error title=CIK-CORE-002::java: "))

    def test_plan_writes_compact_github_output(self):
        repo = rendered_repo("core", "java")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out"
            code, _, _ = self.run_cli("plan", "--repo", str(repo), "--github-output", str(target))
            lines = target.read_text().splitlines()
        self.assertEqual(code, 0)
        self.assertEqual(lines[0], 'modules=["core","java"]')
        self.assertEqual(lines[1], 'java=[{"id":"app-java","name":"Application Java","path":".","java_version":"21"}]')

    def test_plan_fails_on_findings(self):
        repo = rendered_repo("java")
        code, _, _ = self.run_cli("plan", "--repo", str(repo))
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.add import add_module
from ci_kit.cli import main
from ci_kit.codes import CODES
from ci_kit.plan import Finding, Plan, build_plan
from module_test_support import ROOT, git, lifecycle_template_repo, mkdtemp, rendered_from

_TEMPLATE: Path | None = None


def template() -> Path:
    global _TEMPLATE
    if _TEMPLATE is None:
        _TEMPLATE = lifecycle_template_repo()
    return _TEMPLATE


def commit_all(repo: Path, message: str = "initial") -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


def core_repo() -> Path:
    repo = rendered_from(template(), "core", ref="v99.0.0")
    git(repo, "init", "-q")
    commit_all(repo)
    return repo


def repo_without_core() -> Path:
    repo = mkdtemp("no-core-")
    git(repo, "init", "-q")
    (repo / "README.md").write_text("no ci-kit modules\n")
    commit_all(repo)
    return repo


def head(repo: Path) -> str:
    return git(repo, "rev-parse", "HEAD")


def status(repo: Path) -> str:
    return git(repo, "status", "--porcelain", "--ignored")


def add(repo: Path, module: str = "labels", to: str = "v99.0.0"):
    return add_module(repo, module, to, src_override=str(template()))


class AddTests(unittest.TestCase):
    def assertUnchanged(self, repo: Path, before_head: str, before_status: str) -> None:
        self.assertEqual(head(repo), before_head)
        self.assertEqual(status(repo), before_status)

    def test_adds_module_in_one_commit(self):
        repo = core_repo()
        before = head(repo)
        result = add(repo)
        self.assertTrue(result.installed)
        self.assertEqual(result.findings, [])
        self.assertEqual((result.module, result.ref), ("labels", "v99.0.0"))
        self.assertEqual(git(repo, "log", "--format=%s", f"{before}..HEAD").splitlines(), ["chore(ci-kit): add labels module"])
        self.assertEqual(result.commits, [head(repo)])
        answers = yaml.safe_load((repo / ".copier-answers.labels.yml").read_text())
        self.assertEqual(answers["_commit"], "v99.0.0")
        self.assertEqual(build_plan(repo, src_override=str(template())).findings, [])
        self.assertEqual(git(repo, "status", "--porcelain"), "")

    def test_already_installed_is_noop(self):
        repo = core_repo()
        add(repo)
        before = head(repo)
        result = add(repo)
        self.assertFalse(result.installed)
        self.assertEqual(result.findings, [])
        self.assertEqual(result.commits, [])
        self.assertEqual(head(repo), before)

    def test_dirty_tree_001(self):
        repo = core_repo()
        (repo / "README.md").write_text("local edit\n")
        before_head, before_status = head(repo), status(repo)
        result = add(repo)
        self.assertEqual([f.code for f in result.findings], ["CIK-ADD-001"])
        self.assertFalse(result.installed)
        self.assertUnchanged(repo, before_head, before_status)

    def test_unknown_module_002(self):
        repo = core_repo()
        before_head, before_status = head(repo), status(repo)
        for module in ("nope", "legacy"):
            with self.subTest(module=module):
                result = add(repo, module)
                self.assertEqual([f.code for f in result.findings], ["CIK-ADD-002"])
                self.assertUnchanged(repo, before_head, before_status)

    def test_missing_core_003(self):
        repo = repo_without_core()
        before_head, before_status = head(repo), status(repo)
        result = add(repo)
        self.assertEqual([f.code for f in result.findings], ["CIK-ADD-003"])
        self.assertUnchanged(repo, before_head, before_status)

    def test_core_commit_mismatch_003(self):
        repo = core_repo()
        before_head, before_status = head(repo), status(repo)
        result = add(repo, to="v99.1.0")
        self.assertEqual([f.code for f in result.findings], ["CIK-ADD-003"])
        self.assertUnchanged(repo, before_head, before_status)

    def test_untrusted_core_source_003(self):
        repo = core_repo()
        before_head, before_status = head(repo), status(repo)
        result = add_module(repo, "labels", "v99.0.0")
        self.assertEqual([f.code for f in result.findings], ["CIK-ADD-003"])
        self.assertIn("untrusted template source", result.findings[0].message)
        self.assertUnchanged(repo, before_head, before_status)

    def test_existing_file_004(self):
        repo = core_repo()
        (repo / ".github/workflows/labels.yml").write_text("name: Labels\n")
        commit_all(repo, "own labels workflow")
        before_head, before_status = head(repo), status(repo)
        result = add(repo)
        self.assertEqual([f.code for f in result.findings], ["CIK-ADD-004"])
        self.assertEqual(result.findings[0].path, ".github/workflows/labels.yml")
        self.assertUnchanged(repo, before_head, before_status)

    def test_plan_findings_roll_back_005(self):
        repo = core_repo()
        before_head, before_status = head(repo), status(repo)
        bad = Plan(findings=[Finding("CIK-CORE-010", "labels", "drifted", "x")])
        with mock.patch("ci_kit.add.build_plan", return_value=bad):
            result = add(repo)
        self.assertEqual([f.code for f in result.findings], ["CIK-ADD-005"])
        self.assertFalse(result.installed)
        self.assertEqual(result.commits, [])
        self.assertUnchanged(repo, before_head, before_status)

    def test_unexpected_exception_rolls_back_005(self):
        repo = core_repo()
        before_head, before_status = head(repo), status(repo)
        with mock.patch("ci_kit.add.build_plan", side_effect=ValueError("boom")):
            result = add(repo)
        self.assertEqual([f.code for f in result.findings], ["CIK-ADD-005"])
        self.assertFalse(result.installed)
        self.assertUnchanged(repo, before_head, before_status)

    def test_copier_failure_rolls_back(self):
        repo = core_repo()
        (repo / ".gitignore").write_text("cache/\n*.log\n")
        commit_all(repo, "ignore cache")
        (repo / "cache").mkdir()
        (repo / "cache/keep").write_text("pre-existing ignored file\n")
        before_head, before_status = head(repo), status(repo)
        fake = mkdtemp("fake-copier-") / "copier.py"
        fake.write_text(
            "import os, subprocess, sys\n"
            "dest = sys.argv[-1]\n"
            "if os.path.isdir(os.path.join(dest, '.git')):\n"
            "    open(os.path.join(dest, 'stray.txt'), 'w').write('x')\n"
            "    open(os.path.join(dest, 'debug.log'), 'w').write('x')\n"
            "    print('copier exploded', file=sys.stderr)\n"
            "    sys.exit(1)\n"
            "sys.exit(subprocess.run(sys.argv[1:]).returncode)\n"
        )
        with mock.patch("ci_kit.add._copier", return_value=[sys.executable, str(fake), shutil.which("copier")]):
            result = add(repo)
        self.assertEqual([f.code for f in result.findings], ["CIK-ADD-005"])
        self.assertIn("copier exploded", result.findings[0].message)
        self.assertUnchanged(repo, before_head, before_status)
        self.assertTrue((repo / "cache/keep").is_file())

    def test_codes_documented(self):
        codes = [f"CIK-ADD-00{n}" for n in range(1, 6)]
        for code in codes:
            with self.subTest(code=code):
                self.assertIn(code, CODES)
                self.assertTrue((ROOT / "docs/errors" / f"{code}.md").is_file())
        self.assertEqual(sorted(c for c in CODES if c.startswith("CIK-ADD-")), codes)


class CliTests(unittest.TestCase):
    def run_cli(self, repo: Path) -> tuple[int, dict]:
        out = mkdtemp("add-json-") / "out.json"
        code = main([
            "add", "--module", "labels", "--repo", str(repo), "--to", "v99.0.0",
            "--template-src", str(template()), "--json", str(out),
        ])
        return code, json.loads(out.read_text())

    def test_cli_json(self):
        repo = core_repo()
        code, payload = self.run_cli(repo)
        self.assertEqual(code, 0)
        self.assertEqual(set(payload), {"module", "ref", "installed", "commits", "findings"})
        self.assertEqual(payload["module"], "labels")
        self.assertEqual(payload["ref"], "v99.0.0")
        self.assertTrue(payload["installed"])
        self.assertEqual(payload["commits"], [head(repo)])
        self.assertEqual(payload["findings"], [])

    def test_cli_json_with_finding_exits_1(self):
        repo = core_repo()
        (repo / "README.md").write_text("local edit\n")
        code, payload = self.run_cli(repo)
        self.assertEqual(code, 1)
        self.assertFalse(payload["installed"])
        self.assertEqual(len(payload["findings"]), 1)
        finding = payload["findings"][0]
        self.assertEqual(set(finding), {"code", "module", "message", "path"})
        self.assertEqual(finding["code"], "CIK-ADD-001")
        self.assertEqual(finding["module"], "labels")


if __name__ == "__main__":
    unittest.main()

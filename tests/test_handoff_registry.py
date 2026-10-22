from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.handoff import AREAS, Area, area_for_module
from module_test_support import ROOT, git, make_template_repo, mkdtemp, render, render_tree, shared_template_repo

PIN = {"toolkit_version": "v99.0.0"}
NEXT_REF = "v99.0.1"
LEGACY_006 = "CIK-LEGACY-006"
ALLOWED_LIST = re.compile(r"allowed_handed_off\s*=\s*\[([^\]]*)\]")  # captures the quoted items of the allowed-area list
QUOTED_ITEM = re.compile(r"'([^']+)'")  # one single-quoted area name
HANDED_OFF_ANSWER = re.compile(r"^handed_off:\n(?:\s+- .*\n)*", re.MULTILINE)  # the key and its block-list items


def areas_in_validator(validator: str) -> list[str]:
    match = ALLOWED_LIST.search(validator)
    assert match, "allowed_handed_off list not found in validator"
    return QUOTED_ITEM.findall(match.group(1))


class HandoffRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo = shared_template_repo()
        cls.cfg = yaml.safe_load((ROOT / "copier.yml").read_text())

    def test_registry_shape(self) -> None:
        self.assertEqual(AREAS["validate"], Area(module="core", legacy_paths=(".github/workflows/validate.yml",)))
        self.assertEqual(area_for_module("core"), "validate")
        self.assertIsNone(area_for_module("java"))

    def test_registry_matches_copier_yml(self) -> None:
        self.assertEqual(set(AREAS), set(areas_in_validator(self.cfg["handed_off"]["validator"])))
        for area, info in AREAS.items():
            for path in info.legacy_paths:
                shipped = (ROOT / "template" / f"{path}.jinja").exists() or (ROOT / "template" / path).exists()
                guarded = any(f"'{area}' in" in e and path in e for e in self.cfg["_exclude"])
                self.assertTrue(guarded or not shipped, path)
        self.assertFalse((ROOT / "template/.github/workflows/validate.yml.jinja").exists())

    def test_question_is_legacy_only(self) -> None:
        question = self.cfg["handed_off"]
        self.assertEqual(question["type"], "yaml")
        self.assertEqual(question["when"], "{{ module == 'legacy' }}")
        default = question["default"]
        self.assertIsInstance(default, str)
        self.assertIn("_external_data", default)
        self.assertIn("[validate]", default)

    def copy(self, *args: str) -> tuple[subprocess.CompletedProcess, Path]:
        dest = mkdtemp("handoff-")
        result = subprocess.run(
            [shutil.which("copier"), "copy", "--trust", "--defaults", "--vcs-ref", "HEAD",
             "--data", "toolkit_version=v99.0.0", *args, str(self.repo), str(dest)],
            capture_output=True, text=True,
        )
        return result, dest

    def test_default_hands_validate_off(self) -> None:
        tree = render_tree(self.repo, data=PIN)
        self.assertEqual(yaml.safe_load(tree[".copier-answers.yml"])["handed_off"], ["validate"])
        self.assertNotIn(".github/workflows/validate.yml", tree)
        self.assertIn(".github/workflows/gitleaks.yml", tree)

    def test_copy_without_validate_refuses(self) -> None:
        result, dest = self.copy("--data", "handed_off=[]")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(LEGACY_006, result.stdout + result.stderr)
        self.assertFalse((dest / ".github/workflows").exists())

    def test_validator_requires_validate(self) -> None:
        result, _ = self.copy("--data", "handed_off=[]")
        self.assertIn("validate must stay handed off", result.stdout + result.stderr)

    def test_unknown_area_rejected(self) -> None:
        for value in ("[foo]", "[validate, validate]", "[1]", "validate"):
            with self.subTest(value=value):
                result, _ = self.copy("--data", f"handed_off={value}")
                self.assertNotEqual(result.returncode, 0)

    def test_modules_render_without_guard(self) -> None:
        tree = render_tree(self.repo, data={"module": "core", **PIN})
        self.assertIn(".github/workflows/validate.yml", tree)

    def test_modules_do_not_ask_handed_off(self) -> None:
        tree = render_tree(self.repo, data={"module": "core", **PIN})
        self.assertNotIn("handed_off", tree[".copier-answers.yml"].decode())


class LegacyValidateUpdateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.template = make_template_repo(mkdtemp("ci-kit-legacy-006-"))
        readme = cls.template / "README.md"
        readme.write_text(readme.read_text() + "\n")
        git(cls.template, "commit", "-q", "-am", "next release")
        git(cls.template, "tag", NEXT_REF)

    def legacy_repo(self) -> Path:
        repo = mkdtemp("legacy-006-")
        subprocess.run(
            [shutil.which("copier"), "copy", "--trust", "--defaults", "--vcs-ref", "v99.0.0",
             "--data", "handed_off=[validate]", "--data", "toolkit_version=v99.0.0", str(self.template), str(repo)],
            check=True, capture_output=True, text=True,
        )
        git(repo, "init", "-q")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "legacy")
        return repo

    def update(self, repo: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            [shutil.which("copier"), "update", "--trust", "--defaults", "--vcs-ref", NEXT_REF],
            cwd=repo, capture_output=True, text=True,
        )

    def test_update_handed_off_repo_passes(self) -> None:
        repo = self.legacy_repo()
        result = self.update(repo)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"_commit: {NEXT_REF}", (repo / ".copier-answers.yml").read_text())
        self.assertFalse((repo / ".github/workflows/validate.yml").exists())

    def test_update_without_handed_off_key_refuses(self) -> None:
        # Regression guard: a pre-v3.2 answers file has no handed_off key, and --defaults must not
        # silently hand validate off (and drop validate.yml) through the question default.
        repo = self.legacy_repo()
        answers = repo / ".copier-answers.yml"
        answers.write_text(HANDED_OFF_ANSWER.sub("", answers.read_text()))
        self.assertNotIn("handed_off", answers.read_text())
        git(repo, "commit", "-q", "-am", "pre-v3.2 answers")
        head = git(repo, "rev-parse", "HEAD")
        result = self.update(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(LEGACY_006, result.stdout + result.stderr)
        self.assertEqual(git(repo, "status", "--porcelain"), "")
        self.assertEqual(git(repo, "rev-parse", "HEAD"), head)

    def test_update_with_empty_handed_off_refuses(self) -> None:
        # Regression guard: stored handed_off: [] still relies on legacy validate.yml.
        repo = self.legacy_repo()
        answers = repo / ".copier-answers.yml"
        answers.write_text(HANDED_OFF_ANSWER.sub("handed_off: []\n", answers.read_text()))
        self.assertEqual(yaml.safe_load(answers.read_text())["handed_off"], [])
        git(repo, "commit", "-q", "-am", "empty handoff")
        head = git(repo, "rev-parse", "HEAD")
        result = self.update(repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(LEGACY_006, result.stdout + result.stderr)
        self.assertEqual(git(repo, "status", "--porcelain"), "")
        self.assertEqual(git(repo, "rev-parse", "HEAD"), head)


if __name__ == "__main__":
    unittest.main()

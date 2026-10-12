from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from module_test_support import ROOT, git, make_template_repo, normalize_answers, render, render_tree, strip_module_update_block

COPIER_UPDATE = ".github/workflows/copier-update.yml"

PIN = {"toolkit_version": "v99.0.0"}


class LegacyCompatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="module-selector-")).resolve()
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        try:
            base = git(ROOT, "merge-base", "HEAD", "origin/main")
        except Exception as error:
            raise AssertionError(f"origin/main is required as the legacy baseline: {error}") from error
        cls.baseline = cls.tmp / "baseline"
        git(ROOT, "worktree", "add", "--detach", str(cls.baseline), base)
        cls.addClassCleanup(lambda: git(ROOT, "worktree", "remove", "--force", str(cls.baseline)))
        cls.candidate = make_template_repo(cls.tmp)

    def test_legacy_scenarios_render_identically_to_main(self) -> None:
        for scenario in sorted((ROOT / "tests/scenarios").glob("*.yml")):
            with self.subTest(scenario=scenario.name):
                candidate = normalize_answers(render_tree(self.candidate, scenario, PIN), "legacy")
                candidate[COPIER_UPDATE] = strip_module_update_block(candidate[COPIER_UPDATE].decode()).encode()
                self.assertEqual(normalize_answers(render_tree(self.baseline, scenario, PIN)), candidate)

    def test_empty_handed_off_is_byte_identical(self) -> None:
        for scenario in sorted((ROOT / "tests/scenarios").glob("*.yml")):
            with self.subTest(scenario=scenario.name):
                plain = render_tree(self.candidate, scenario, PIN)
                explicit = render_tree(self.candidate, scenario, {**PIN, "handed_off": []})
                for tree in (plain, explicit):
                    self.assertIn(b"\nhanded_off: []\n", tree[".copier-answers.yml"])
                self.assertEqual(
                    normalize_answers(plain, "legacy"), normalize_answers(explicit, "legacy")
                )
                self.assertEqual(
                    {k: v for k, v in plain.items() if k != ".copier-answers.yml"},
                    {k: v for k, v in explicit.items() if k != ".copier-answers.yml"},
                )

    def test_module_answer_defaults_to_legacy(self) -> None:
        dest = render(self.candidate, ROOT / "tests/scenarios/python.yml", PIN)
        try:
            answers = yaml.safe_load((dest / ".copier-answers.yml").read_text())
        finally:
            shutil.rmtree(dest, ignore_errors=True)
        self.assertEqual(answers.get("module"), "legacy")


class StripModuleUpdateBlockTests(unittest.TestCase):
    def test_block_removed_exactly(self) -> None:
        text = (
            "  a\n\n"
            "  if compgen -G '.copier-answers.*.yml' >/dev/null; then\n"
            "    if x; then\n      y\n    fi\n"
            "  fi\n\n"
            "  b\n"
        )
        self.assertEqual(strip_module_update_block(text), "  a\n\n  b\n")

    def test_missing_or_duplicate_block_asserts(self) -> None:
        with self.assertRaises(AssertionError):
            strip_module_update_block("  a\n  b\n")
        block = "if compgen -G '.copier-answers.*.yml'; then\nfi\n"
        with self.assertRaises(AssertionError):
            strip_module_update_block(block + block)


if __name__ == "__main__":
    unittest.main()

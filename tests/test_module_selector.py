from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from module_test_support import ROOT, make_template_repo, normalize_answers, render, render_tree

PIN = {"toolkit_version": "v99.0.0"}


class LegacyCompatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="module-selector-")).resolve()
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.candidate = make_template_repo(cls.tmp)

    def test_explicit_legacy_module_is_byte_identical(self) -> None:
        for scenario in sorted((ROOT / "tests/scenarios").glob("*.yml")):
            with self.subTest(scenario=scenario.name):
                self.assertEqual(
                    render_tree(self.candidate, scenario, PIN),
                    render_tree(self.candidate, scenario, {**PIN, "module": "legacy"}),
                )

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


if __name__ == "__main__":
    unittest.main()

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
from module_test_support import ROOT, mkdtemp, render_tree, shared_template_repo

PIN = {"toolkit_version": "v99.0.0"}
ALLOWED_LIST = re.compile(r"allowed_handed_off\s*=\s*\[([^\]]*)\]")  # captures the quoted items of the allowed-area list
QUOTED_ITEM = re.compile(r"'([^']+)'")  # one single-quoted area name


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
                self.assertTrue(any(f"'{area}' in" in e and path in e for e in self.cfg["_exclude"]))

    def test_question_is_legacy_only(self) -> None:
        question = self.cfg["handed_off"]
        self.assertEqual(question["type"], "yaml")
        self.assertEqual(question["default"], [])
        self.assertEqual(question["when"], "{{ module == 'legacy' }}")

    def test_handed_off_validate_excludes_validate_yml(self) -> None:
        tree = render_tree(self.repo, data={"handed_off": ["validate"], **PIN})
        self.assertNotIn(".github/workflows/validate.yml", tree)
        self.assertIn(".github/workflows/gitleaks.yml", tree)

    def test_default_keeps_validate_yml(self) -> None:
        self.assertIn(".github/workflows/validate.yml", render_tree(self.repo, data=PIN))

    def test_unknown_area_rejected(self) -> None:
        for value in ("[foo]", "[validate, validate]", "[1]", "validate"):
            with self.subTest(value=value):
                dest = mkdtemp("reject-")
                result = subprocess.run(
                    [shutil.which("copier"), "copy", "--trust", "--defaults", "--vcs-ref", "HEAD",
                     "--data", f"handed_off={value}", "--data", "toolkit_version=v99.0.0", str(self.repo), str(dest)],
                    capture_output=True, text=True,
                )
                self.assertNotEqual(result.returncode, 0)

    def test_modules_do_not_ask_handed_off(self) -> None:
        tree = render_tree(self.repo, data={"module": "core", **PIN})
        self.assertNotIn("handed_off", tree[".copier-answers.yml"].decode())


if __name__ == "__main__":
    unittest.main()

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

from module_test_support import ROOT, make_template_repo, render


class ModuleSelectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="module-selector-")).resolve()
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.candidate = make_template_repo(cls.tmp)

    def test_module_defaults_to_core(self) -> None:
        dest = render(self.candidate)
        try:
            answers = yaml.safe_load((dest / ".copier-answers.core.yml").read_text())
            self.assertFalse((dest / ".copier-answers.yml").exists())
            self.assertTrue((dest / ".ci/modules/core.yml").is_file())
        finally:
            shutil.rmtree(dest, ignore_errors=True)
        self.assertEqual(answers.get("module"), "core")

    def test_no_legacy_choice(self) -> None:
        dest = self.tmp / "legacy"
        result = subprocess.run(
            [shutil.which("copier"), "copy", "--trust", "--defaults", "--vcs-ref", "HEAD",
             "-d", "module=legacy", str(self.candidate), str(dest)],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("legacy", result.stderr + result.stdout)
        self.assertFalse((dest / ".copier-answers.yml").exists())


class NoLegacyTemplateTests(unittest.TestCase):
    def test_no_template_dir_references(self) -> None:
        self.assertFalse((ROOT / "template").exists())
        tracked = subprocess.run(
            ["git", "ls-files", "--", "scripts", "tools", ".github"],
            cwd=ROOT, check=True, capture_output=True, text=True,
        ).stdout.split()
        hits = []
        for name in tracked:
            path = ROOT / name
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                for match in re.finditer(r"template/", line):
                    if line[match.end():].startswith("{"):
                        continue  # a diff label such as f"template/{path}", not a path in this repository
                    prefix = line[: match.start()]
                    # Every remaining template path is a module's: modules/<name>/template/.
                    if not re.search(r"modules/\S*/$", prefix) and not re.search(r"[A-Za-z0-9_-]$", prefix):
                        hits.append(f"{name}:{number}: {line.strip()}")
                        break
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()

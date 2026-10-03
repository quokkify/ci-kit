"""Generated files must pass the consumers' whitespace and Markdown formatting checks."""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ("allure-pages", "allure-external", "polyglot")


class GeneratedOutputHygieneTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        copier = shutil.which("copier")
        if copier is None:
            raise AssertionError("copier is required to render the template")
        cls.temporary = tempfile.TemporaryDirectory(prefix="generated-output-")
        source = Path(cls.temporary.name) / "template-source"
        shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(".git", ".worktrees", "__pycache__", "node_modules"))
        cls.projects = {}
        for scenario in SCENARIOS:
            destination = Path(cls.temporary.name) / scenario
            subprocess.run(
                [copier, "copy", "--trust", "--defaults", "--data-file", str(ROOT / f"tests/scenarios/{scenario}.yml"),
                 str(source), str(destination)],
                check=True, capture_output=True, text=True,
            )
            cls.projects[scenario] = destination

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_text_files_have_no_trailing_whitespace_or_blank_line_at_eof(self) -> None:
        for scenario, project in self.projects.items():
            for path in sorted(project.rglob("*")):
                if not path.is_file() or path.suffix not in {".yml", ".yaml", ".md", ".json", ".mjs", ".py"}:
                    continue
                text = path.read_text(encoding="utf-8")
                relative = path.relative_to(project)
                with self.subTest(scenario=scenario, path=str(relative)):
                    self.assertTrue(text.endswith("\n") and not text.endswith("\n\n"), "file must end with exactly one newline")
                    self.assertFalse([line for line in text.split("\n") if line != line.rstrip()], "trailing whitespace")

    def test_onboarding_table_is_aligned_like_prettier(self) -> None:
        for scenario, project in self.projects.items():
            lines = (project / "docs/ci-kit.md").read_text(encoding="utf-8").splitlines()
            start = lines.index("## What this project received")
            self.assertEqual(lines[start + 1], "")
            table = []
            for line in lines[start + 2:]:
                if not line.startswith("|"):
                    break
                table.append(line)
            with self.subTest(scenario=scenario):
                self.assertGreater(len(table), 3)
                self.assertEqual(len({len(line) for line in table}), 1, "rows must share one padded width")
                header, separator = table[0], table[1]
                self.assertTrue(header.startswith("| Path "))
                for cell, rule in zip(header.split("|")[1:-1], separator.split("|")[1:-1]):
                    self.assertEqual(rule, " " + "-" * (len(cell) - 2) + " ")


if __name__ == "__main__":
    main()

"""Generated files must pass the consumers' whitespace and Markdown formatting checks."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ("core-features", "core-polyglot", "core-node-docker")


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
            for module, answers in yaml.safe_load((ROOT / f"tests/scenarios/modules/{scenario}.yml").read_text()).items():
                data = []
                for key, value in (answers or {}).items():
                    data += ["--data", f"{key}={json.dumps(value) if isinstance(value, (list, dict)) else value}"]
                subprocess.run(
                    [copier, "copy", "--trust", "--defaults", "-a", f".copier-answers.{module}.yml",
                     "--data", f"module={module}", *data, str(source), str(destination)],
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


if __name__ == "__main__":
    main()

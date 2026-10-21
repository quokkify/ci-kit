from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / ".github/release-please/config.json"
TEMPLATE_CONFIG_PATH = ROOT / "template/.github/release-please/config.json.jinja"
RENOVATE_PATH = ROOT / "renovate/default.json"


class ReleaseNotesConfigTests(unittest.TestCase):
    def load_config(self, path: Path) -> dict:
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertIsInstance(payload, dict)
        return payload

    def test_config_backed_single_omits_strategy_overrides(self) -> None:
        workflow = (ROOT / ".github/workflows/release-please.yml").read_text()
        for name in ("release-type", "path"):
            self.assertIn(
                name + ": ${{ !inputs.config-backed-single && inputs." + name + " || '' }}",
                workflow,
            )
            self.assertNotIn("inputs.config-backed-single && '' ||", workflow)
        for name in ("config-file", "manifest-file"):
            self.assertIn(
                name + ": ${{ inputs.config-backed-single && inputs." + name + " || '' }}",
                workflow,
            )

    def run_dependency_section_guard(self, config: dict) -> subprocess.CompletedProcess[str]:
        workflow = yaml.safe_load((ROOT / ".github/workflows/release-please.yml").read_text())
        steps = {step.get("name"): step for step in workflow["jobs"]["validate"]["steps"]}
        step = steps["Require a visible dependencies changelog section"]
        checkout = steps["Check out repository"]
        self.assertEqual(step["shell"], "python")
        self.assertEqual(step["if"], "${{ inputs.mode == 'manifest' || inputs.config-backed-single }}")
        self.assertEqual(checkout["if"], step["if"])
        # Release Please reads the config from target-branch, so the guard must too.
        self.assertEqual(checkout["with"]["ref"], "${{ inputs.target-branch }}")
        with tempfile.TemporaryDirectory(prefix="deps-section-guard-") as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-I", "-c", step["run"]],
                env={"CONFIG_FILE": str(path)},
                cwd=temporary,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_dependency_section_guard_accepts_toolkit_and_template_configs(self) -> None:
        for path in (CONFIG_PATH, TEMPLATE_CONFIG_PATH):
            with self.subTest(path=path):
                result = self.run_dependency_section_guard(self.load_config(path))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_dependency_section_guard_ignores_whether_the_release_has_dependency_commits(self) -> None:
        result = self.run_dependency_section_guard(self.load_config(TEMPLATE_CONFIG_PATH))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_dependency_section_guard_accepts_top_level_sections(self) -> None:
        sections = self.load_config(CONFIG_PATH)["packages"]["."]["changelog-sections"]
        result = self.run_dependency_section_guard(
            {"changelog-sections": sections, "packages": {".": {}, "api": {}}}
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_dependency_section_guard_rejects_default_or_hidden_deps(self) -> None:
        cases = {
            "release-please defaults": {"packages": {".": {"package-name": "skills"}}},
            "hidden deps": {"packages": {".": {"changelog-sections": [
                {"type": "fix", "section": "Fixes"},
                {"type": "deps", "section": "Dependencies", "hidden": True},
            ]}}},
            "one package without deps": {
                "changelog-sections": [{"type": "deps", "section": "Dependencies"}],
                "packages": {".": {}, "web": {"changelog-sections": [{"type": "feat", "section": "Features"}]}},
            },
        }
        for name, config in cases.items():
            with self.subTest(name):
                result = self.run_dependency_section_guard(config)
                self.assertEqual(result.returncode, 1)
                self.assertIn("::error file=", result.stdout)
                self.assertIn("would drop deps commits", result.stdout)
        result = self.run_dependency_section_guard(cases["one package without deps"])
        self.assertIn("for web:", result.stdout)

    def test_dependency_mapping_and_documentation_release_contract(self) -> None:
        for path in (CONFIG_PATH, TEMPLATE_CONFIG_PATH):
            with self.subTest(path=path):
                package = self.load_config(path)["packages"]["."]
                sections = package["changelog-sections"]
                self.assertIn(
                    {"type": "deps", "section": "📦 Dependencies"},
                    sections,
                )
                chore = next(section for section in sections if section["type"] == "chore")
                docs = next(section for section in sections if section["type"] == "docs")
                self.assertEqual(chore["section"], "🧹 Chores")
                self.assertNotIn("hidden", chore)
                self.assertTrue(docs["hidden"])
                self.assertEqual(package["changelog-path"], "CHANGELOG.md")

    def test_renovate_produces_deps_dependency_type(self) -> None:
        renovate = self.load_config(RENOVATE_PATH)
        self.assertEqual(renovate["semanticCommits"], "enabled")
        self.assertEqual(renovate["semanticCommitType"], "deps")
        # Scope is the manager name, as in quokkify/renovate-presets; Enrich only adds authored rich sections.
        self.assertEqual(renovate["semanticCommitScope"], "{{manager}}")

    def test_release_please_17_stages_chore_dependencies_for_enrichment(self) -> None:
        npm = shutil.which("npm")
        node = shutil.which("node")
        if npm is None or node is None:
            self.skipTest("node and npm are required for Release Please behavior coverage")
        with tempfile.TemporaryDirectory(prefix="release-please-17-") as temporary:
            install_root = Path(temporary) / "release-please"
            install = subprocess.run(
                [
                    npm,
                    "install",
                    "--prefix",
                    str(install_root),
                    "--no-package-lock",
                    "--ignore-scripts",
                    "release-please@17.6.0",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(install.returncode, 0, install.stderr or install.stdout)
            result = subprocess.run(
                [
                    node,
                    str(ROOT / "tests/release_please_17_notes.cjs"),
                    str(install_root / "node_modules/release-please"),
                    str(CONFIG_PATH),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_root_and_template_have_matching_section_semantics(self) -> None:
        root_package = self.load_config(CONFIG_PATH)["packages"]["."]
        template_package = self.load_config(TEMPLATE_CONFIG_PATH)["packages"]["."]
        for package in (root_package, template_package):
            self.assertEqual(package["release-type"], "simple")
            self.assertTrue(package["bump-minor-pre-major"])
        self.assertEqual(
            root_package["changelog-sections"], template_package["changelog-sections"]
        )
        # Alert when release behavior in ci-kit drifts from generated projects.
        for section in root_package["changelog-sections"]:
            if section["type"] == "chore":
                self.assertEqual(section["section"], "🧹 Chores")
                self.assertNotIn("hidden", section)
            if section["type"] == "docs":
                self.assertTrue(section["hidden"])

    def test_copier_rendered_config_keeps_dependency_mapping(self) -> None:
        copier = shutil.which("copier")
        if copier is None:
            self.skipTest("copier is required for rendered template coverage")
        version_output = subprocess.run(
            [copier, "--version"], capture_output=True, text=True, check=False
        ).stdout
        version_match = re.search(r"(\d+)\.(\d+)\.(\d+)", version_output)
        with tempfile.TemporaryDirectory(prefix="release-notes-config-") as temporary:
            source = Path(temporary) / "template-source"
            shutil.copytree(
                ROOT,
                source,
                ignore=shutil.ignore_patterns(".git", "__pycache__"),
            )
            copier_config = source / "copier.yml"
            min_match = re.search(r'_min_copier_version: "(\d+)\.(\d+)\.(\d+)"', copier_config.read_text(encoding="utf-8"))
            assert min_match is not None
            if version_match and tuple(map(int, version_match.groups())) < tuple(map(int, min_match.groups())):
                copier_config.write_text(
                    copier_config.read_text(encoding="utf-8").replace(
                        min_match.group(0),
                        f'_min_copier_version: "{version_match.group(0)}"',
                        1,
                    ),
                    encoding="utf-8",
                )
            destination = Path(temporary) / "fixture"
            result = subprocess.run(
                [
                    copier,
                    "copy",
                    "--trust",
                    "--defaults",
                    "--data",
                    "project_name=release-notes-fixture",
                    "--data",
                    "release_please=true",
                    str(source),
                    str(destination),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            rendered = self.load_config(
                destination / ".github/release-please/config.json"
            )["packages"]["."]
            self.assertEqual(rendered["changelog-path"], "CHANGELOG.md")
            self.assertIn(
                {"type": "deps", "section": "📦 Dependencies"},
                rendered["changelog-sections"],
            )
            chore = next(
                section for section in rendered["changelog-sections"]
                if section["type"] == "chore"
            )
            docs = next(
                section for section in rendered["changelog-sections"]
                if section["type"] == "docs"
            )
            self.assertEqual(chore["section"], "🧹 Chores")
            self.assertNotIn("hidden", chore)
            self.assertTrue(docs["hidden"])
            pull_request_template = (
                destination / ".github/pull_request_template.md"
            ).read_text(encoding="utf-8")
            self.assertNotIn("## Release notes", pull_request_template)
            self.assertNotIn("## Breaking change", pull_request_template)


if __name__ == "__main__":
    unittest.main()

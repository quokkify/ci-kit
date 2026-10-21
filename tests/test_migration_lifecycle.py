from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.migrate import migrate
from ci_kit.plan import build_plan
from ci_kit.update import run_update
from module_test_support import component_less_scenarios, component_scenarios, git, legacy_scenario_repo, lifecycle_template_repo


def snapshot(repo: Path) -> dict[str, bytes]:
    return {
        p.relative_to(repo).as_posix(): p.read_bytes()
        for p in sorted(repo.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(repo).parts
    }


class MigrationLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = lifecycle_template_repo()
        cls.src = str(cls.template)
        cls.scenarios = component_less_scenarios()

    def test_scenarios_are_discovered(self):
        for name in ("allure-external", "wave1-release", "allure-component-less-default"):
            self.assertIn(name, self.scenarios)
        self.assertNotIn("java", self.scenarios)

    def test_migrate_then_update_is_clean(self):
        for scenario in self.scenarios:
            with self.subTest(scenario=scenario):
                repo = legacy_scenario_repo(self.template, scenario)
                result = migrate(repo, "core", "v99.0.0", src_override=self.src)
                self.assertEqual(result.findings, [])
                self.assertEqual(len(result.commits), 2)
                self.assertTrue((repo / ".copier-answers.core.yml").is_file())
                self.assertIn("name: Validate", (repo / ".github/workflows/validate.yml").read_text())
                updates, findings = run_update(repo, "v99.1.0", src_override=self.src)
                self.assertEqual(findings, [])
                self.assertEqual([u.module for u in updates], ["legacy", "core"])
                self.assertEqual(list(repo.rglob("*.rej")), [])
                self.assertEqual(build_plan(repo, src_override=self.src).findings, [])

    def test_revert_restores_pre_migration_tree(self):
        for scenario in self.scenarios:
            with self.subTest(scenario=scenario):
                repo = legacy_scenario_repo(self.template, scenario)
                before = snapshot(repo)
                result = migrate(repo, "core", "v99.0.0", src_override=self.src)
                self.assertEqual(result.findings, [])
                self.assertNotEqual(snapshot(repo), before)
                git(repo, "revert", "--no-edit", result.commits[1], result.commits[0])
                self.assertEqual(snapshot(repo), before)


class ComponentMigrationLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = lifecycle_template_repo()
        cls.src = str(cls.template)
        cls.scenarios = component_scenarios()

    def edited_repo(self, scenario: str, overrides: dict | None = None) -> Path:
        repo = legacy_scenario_repo(self.template, scenario, overrides=overrides)
        path = repo / ".github/workflows/validate.yml"
        text = path.read_text()
        edited, count = re.subn(
            r'(\n    uses: quokkify/ci-kit/\.github/workflows/(?:python|node|java)-ci\.yml@\S+\n    with:\n)',
            r'\1      test-command: "make hand-edited-test"\n',
            text,
            count=1,
        )
        if count:
            path.write_text(edited)
            git(repo, "commit", "-qam", "hand-edit component test command")
        return repo

    def test_scenarios_are_discovered(self):
        for name in ("polyglot", "docker", "java", "node"):
            self.assertIn(name, self.scenarios)
        self.assertNotIn("allure-external", self.scenarios)

    def test_migrate_update_revert_with_components(self):
        for scenario in self.scenarios:
            with self.subTest(scenario=scenario):
                repo = self.edited_repo(scenario)
                pre = git(repo, "rev-parse", "HEAD").strip()
                result = migrate(repo, "core", "v99.0.0", src_override=self.src)
                self.assertEqual(result.findings, [])
                self.assertEqual(len(result.commits), len(result.modules) + 1)
                declared = "".join(p.read_text() for p in (repo / ".ci/modules").glob("*.yml"))
                self.assertIn("hand-edited-test", declared)
                post = git(repo, "rev-parse", "HEAD").strip()
                updates, findings = run_update(repo, "v99.0.0", src_override=self.src)
                self.assertEqual(findings, [])
                self.assertEqual(git(repo, "status", "--porcelain").strip(), "")
                self.assertEqual(git(repo, "rev-parse", "HEAD").strip(), post)
                git(repo, "revert", "--no-edit", *reversed(result.commits))
                self.assertEqual(git(repo, "diff", pre, "HEAD").strip(), "")

    def test_allure_artifact_names_use_external_prefix(self):
        repo = legacy_scenario_repo(self.template, "allure-polyglot")
        result = migrate(repo, "core", "v99.0.0", src_override=self.src)
        self.assertEqual(result.findings, [])
        uploading = [
            entry
            for matrix in build_plan(repo, src_override=self.src).matrices.values()
            for entry in matrix
            if entry.get("upload_test_artifacts")
        ]
        self.assertTrue(uploading)
        for entry in uploading:
            self.assertTrue(entry["test_artifact_name"].startswith("allure-results-"), entry)


if __name__ == "__main__":
    unittest.main()

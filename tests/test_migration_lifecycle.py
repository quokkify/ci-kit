from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.migrate import migrate
from ci_kit.plan import build_plan
from ci_kit.update import run_update
from module_test_support import component_less_scenarios, git, legacy_scenario_repo, lifecycle_template_repo


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


if __name__ == "__main__":
    unittest.main()

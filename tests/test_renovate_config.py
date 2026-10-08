from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RENOVATE_PATH = ROOT / "renovate/default.json"
CHECKED_IN_RENOVATE_PATH = ROOT / ".github/renovate.json"
TEMPLATE_RENOVATE_PATH = ROOT / "template/.github/renovate.json.jinja"


class RenovateConfigTests(unittest.TestCase):
    def test_toolkit_docs_updater_is_excluded_from_release(self) -> None:
        config = json.loads(CHECKED_IN_RENOVATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            config["semanticCommitType"],
            "{{#if (equals depName 'quokkify/ci-kit')}}docs{{else}}deps{{/if}}",
        )
        self.assertNotIn("semanticCommitScope", config)
        template = TEMPLATE_RENOVATE_PATH.read_text(encoding="utf-8")
        self.assertIn('"semanticCommitType": "deps"', template)
        self.assertNotIn("semanticCommitScope", template)

    def test_dependency_titles_are_deps_in_local_and_generated_configs(self) -> None:
        config = json.loads(RENOVATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(config["semanticCommits"], "enabled")
        self.assertEqual(config["semanticCommitType"], "deps")
        self.assertEqual(config["semanticCommitScope"], "{{manager}}")

        toolkit_config = json.loads(CHECKED_IN_RENOVATE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(toolkit_config["semanticCommits"], "enabled")
        self.assertIn("{{else}}deps{{/if}}", toolkit_config["semanticCommitType"])
        self.assertNotIn("semanticCommitScope", toolkit_config)

        template = TEMPLATE_RENOVATE_PATH.read_text(encoding="utf-8")
        self.assertIn('"semanticCommits": "enabled"', template)
        self.assertIn('"semanticCommitType": "deps"', template)
        self.assertNotIn("semanticCommitScope", template)
        self.assertNotIn('"semanticCommitType": "chore"', template)

    def test_ci_kit_documentation_updates_are_docs_and_automerge_immediately(self) -> None:
        config = json.loads(CHECKED_IN_RENOVATE_PATH.read_text(encoding="utf-8"))
        rule = next(
            rule for rule in config["packageRules"]
            if rule.get("matchPackageNames") == ["/^quokkify\\/ci-kit$/"]
        )
        self.assertEqual(rule["semanticCommitType"], "docs")
        self.assertIsNone(rule["semanticCommitScope"])
        self.assertIsNone(rule["minimumReleaseAge"])
        self.assertFalse(rule["dependencyDashboardApproval"])
        self.assertEqual(rule["prCreation"], "immediate")
        self.assertTrue(rule["automerge"])

    def test_allure_action_updates_share_one_cross_manager_group(self) -> None:
        config = json.loads(RENOVATE_PATH.read_text(encoding="utf-8"))
        rules = [
            rule
            for rule in config["packageRules"]
            if rule.get("matchPackageNames") == ["quokkify/allure-report-action"]
        ]

        self.assertEqual(len(rules), 1)
        self.assertEqual(
            rules[0],
            {
                "description": "Deduplicate Allure action updates across built-in and custom managers.",
                "matchManagers": ["github-actions", "custom.regex"],
                "matchPackageNames": ["quokkify/allure-report-action"],
                "matchUpdateTypes": ["minor", "patch", "digest"],
                "groupName": "quokkify/allure-report-action non-major updates",
            },
        )

    def test_allure_action_update_is_not_hidden_by_a_broad_ignore(self) -> None:
        config = json.loads(RENOVATE_PATH.read_text(encoding="utf-8"))
        self.assertIn("github-actions", config["enabledManagers"])
        self.assertIn("custom.regex", config["enabledManagers"])
        self.assertFalse(any("allure-report-action" in rule.get("matchPackageNames", []) and rule.get("enabled") is False for rule in config["packageRules"]))

    def test_python_pip_and_runtime_custom_managers_are_present(self) -> None:
        for path in (CHECKED_IN_RENOVATE_PATH,):
            config = json.loads(path.read_text(encoding="utf-8"))
            managers = config["customManagers"]
            pip = next(item for item in managers if item.get("depNameTemplate") == "pip")
            runtime = next(item for item in managers if item.get("depNameTemplate") == "python")
            self.assertEqual(pip["datasourceTemplate"], "pypi")
            self.assertEqual(pip["versioningTemplate"], "pep440")
            self.assertEqual(runtime["datasourceTemplate"], "python-version")
            self.assertEqual(runtime["versioningTemplate"], "python")
        template = TEMPLATE_RENOVATE_PATH.read_text(encoding="utf-8")
        self.assertIn('"depNameTemplate": "pip"', template)
        self.assertIn('"depNameTemplate": "python"', template)
        self.assertIn('"datasourceTemplate": "python-version"', template)
        self.assertIn('"versioningTemplate": "python"', template)


if __name__ == "__main__":
    unittest.main()

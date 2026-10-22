from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.migration_marker import MARKER, MappingError, check_contexts, legacy_check_names, parse_marker
from module_test_support import legacy_scenario_repo, lifecycle_template_repo, mkdtemp

VALIDATE = ".github/workflows/validate.yml"
_TEMPLATE: Path | None = None


def template() -> Path:
    global _TEMPLATE
    if _TEMPLATE is None:
        _TEMPLATE = lifecycle_template_repo()
    return _TEMPLATE


VERSION = "v3.3.0"
WORKFLOWS_URL = "quokkify/ci-kit/.github/workflows"
CONTRACT = {"name": "Shared template contract", "runs-on": "ubuntu-latest"}


def uses(workflow: str, ref: str = VERSION) -> str:
    return f"{WORKFLOWS_URL}/{workflow}.yml@{ref}"


SKILLS = {
    "jobs": {
        "template-contract": CONTRACT,
        "app-python": {
            "name": "App Python",
            "uses": uses("python-ci"),
            "with": {
                "working-directory": ".",
                "install-command": "python -m pip install pytest allure-pytest",
                "test-command": "python -m pytest --alluredir=allure-results tests/",
                "upload-test-artifacts": True,
                "test-artifact-path": "allure-results",
                "test-artifact-name": "allure-results-app-python",
            },
        },
    }
}

MARKETDESK = {
    "jobs": {
        "template-contract": CONTRACT,
        "marketdesk-node": {
            "name": "MarketDesk",
            "uses": uses("node-ci"),
            "with": {"working-directory": "web", "node-version": 24},
        },
        "java-test-automation": {
            "name": "Java test automation",
            "uses": uses("java-ci"),
            "with": {
                "working-directory": "tests",
                "run-build": False,
                "test-artifact-name": "allure-results-marketdesk-java",
            },
        },
        "docker": {"uses": uses("docker-build"), "with": {"push": False}},
    }
}


def with_job(job: dict) -> dict:
    return {"jobs": {"template-contract": CONTRACT, "job": job}}


class CheckContextsTests(unittest.TestCase):
    def test_skills(self):
        self.assertEqual(check_contexts(SKILLS), ["Shared template contract", "App Python / ci"])

    def test_marketdesk(self):
        self.assertEqual(
            check_contexts(MARKETDESK),
            ["Shared template contract", "MarketDesk / ci", "Java test automation / ci", "docker / build"],
        )

    def test_bad_ref_still_has_a_context(self):
        data = with_job({"name": "Old", "uses": uses("python-ci", "v0.0.1")})
        self.assertEqual(check_contexts(data), ["Shared template contract", "Old / ci"])

    def test_job_without_uses_refused(self):
        with self.assertRaises(MappingError) as raised:
            check_contexts(with_job({"name": "Complete", "runs-on": "ubuntu-latest"}))
        self.assertEqual(raised.exception.job_id, "job")

    def test_no_jobs(self):
        self.assertEqual(check_contexts({"on": "push"}), [])


class CheckNamesTests(unittest.TestCase):
    def test_component_less_legacy_has_only_the_contract_job(self):
        repo = legacy_scenario_repo(template(), "allure-external")
        self.assertEqual(legacy_check_names(repo / VALIDATE), ["Shared template contract"])

    def test_component_jobs_get_exact_contexts(self):
        repo = legacy_scenario_repo(template(), "java")
        self.assertEqual(legacy_check_names(repo / VALIDATE), ["Shared template contract", "Application Java / ci"])

    def test_docker_job_context(self):
        repo = legacy_scenario_repo(template(), "docker")
        self.assertEqual(legacy_check_names(repo / VALIDATE)[-1], "docker / build")

    def test_job_without_mapping_is_refused(self):
        path = mkdtemp("names-") / "validate.yml"
        path.write_text("name: V\non: push\njobs:\n  alpha:\n    runs-on: x\n")
        with self.assertRaisesRegex(MappingError, "alpha"):
            legacy_check_names(path)


class MarkerTests(unittest.TestCase):
    PAYLOAD = {
        "module": "core", "area": "validate", "ref": "v3.2.0", "checks_before": ["Shared template contract"],
        "checks_after": ["ci / gate"], "commits": ["a", "b"], "findings": [], "modules": ["core"],
    }

    def render(self, payload: dict) -> str:
        return MARKER + "\n```json\n" + json.dumps(payload) + "\n```"

    def test_round_trip_has_exact_keys(self):
        body = "intro\n\n" + self.render(self.PAYLOAD) + "\n\ntrailer"
        self.assertEqual(parse_marker(body), self.PAYLOAD)

    def test_findings_serialised(self):
        findings = [{"code": "CIK-MIGRATE-001", "module": "core", "message": "dirty", "path": None}]
        data = parse_marker(self.render({**self.PAYLOAD, "findings": findings}))
        self.assertEqual(data["findings"], findings)

    def test_missing_or_malformed_marker_raises(self):
        for body in ("", "no marker", MARKER + "\ntext", MARKER + "\n```json\n{bad\n```", MARKER + "\n```json\n[1]\n```", MARKER + "\n```json\n{}"):
            with self.subTest(body=body), self.assertRaises(ValueError):
                parse_marker(body)


if __name__ == "__main__":
    unittest.main()

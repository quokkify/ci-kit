from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.legacy_jobs import (
    LegacyJob,
    MappingError,
    check_contexts,
    component_job_ids,
    legacy_to_modules,
    parse_jobs,
)

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


class ParseTests(unittest.TestCase):
    def test_skills_shape(self):
        self.assertEqual(
            legacy_to_modules(parse_jobs(SKILLS, VERSION)),
            [
                ("core", {}),
                (
                    "python",
                    {
                        "python_components": [
                            {
                                "id": "app-python",
                                "name": "App Python",
                                "path": ".",
                                "install_command": "python -m pip install pytest allure-pytest",
                                "test_command": "python -m pytest --alluredir=allure-results tests/",
                                "upload_test_artifacts": True,
                                "test_artifact_path": "allure-results",
                                "test_artifact_name": "allure-results-app-python",
                            }
                        ]
                    },
                ),
            ],
        )

    def test_marketdesk_shape(self):
        modules = dict(legacy_to_modules(parse_jobs(MARKETDESK, VERSION)))
        self.assertEqual(
            modules["node"]["node_components"],
            [{"id": "marketdesk-node", "name": "MarketDesk", "path": "web", "node_version": "24"}],
        )
        self.assertEqual(
            modules["java"]["java_components"],
            [
                {
                    "id": "java-test-automation",
                    "name": "Java test automation",
                    "path": "tests",
                    "java_version": "17",
                    "run_build": False,
                    "test_artifact_name": "allure-results-marketdesk-java",
                }
            ],
        )
        self.assertEqual(modules["docker"], {"docker_images": [{"id": "docker", "name": "docker"}]})
        self.assertEqual([m for m, _ in legacy_to_modules(parse_jobs(MARKETDESK, VERSION))], ["core", "java", "node", "docker"])

    def test_job_fields(self):
        jobs = parse_jobs(MARKETDESK, VERSION)
        self.assertEqual(jobs[-1], LegacyJob("docker", "docker", "docker", {}))

    def test_java_without_version_gets_17(self):
        data = with_job({"uses": uses("java-ci"), "with": {"working-directory": "."}})
        _, java = legacy_to_modules(parse_jobs(data, VERSION))[1]
        self.assertEqual(java["java_components"][0]["java_version"], "17")

    def test_java_version_kept(self):
        data = with_job({"uses": uses("java-ci"), "with": {"java-version": 21}})
        _, java = legacy_to_modules(parse_jobs(data, VERSION))[1]
        self.assertEqual(java["java_components"][0]["java_version"], "21")

    def test_refusals(self):
        cases = {
            "if": {"uses": uses("python-ci"), "if": "true"},
            "needs": {"uses": uses("python-ci"), "needs": "x"},
            "secrets": {"uses": uses("python-ci"), "secrets": "inherit"},
            "ref": {"uses": uses("python-ci", "v0.0.1")},
            "unknown workflow": {"uses": uses("ruby-ci")},
            "input": {"uses": uses("python-ci"), "with": {"bogus": "x"}},
            "float": {"uses": uses("python-ci"), "with": {"python-version": 3.10}},
            "no uses": {"name": "Complete", "runs-on": "ubuntu-latest"},
            "docker push": {"uses": uses("docker-build"), "with": {"push": True}},
            "docker input": {"uses": uses("docker-build"), "with": {"tags": "x"}},
            "input expression": {"uses": uses("python-ci"), "with": {"test-command": "${{ vars.TEST_CMD }}"}},
            "name expression": {"uses": uses("python-ci"), "name": "${{ github.event_name }} App"},
        }
        for label, job in cases.items():
            with self.subTest(label), self.assertRaises(MappingError) as raised:
                parse_jobs(with_job(job), VERSION)
            self.assertEqual(raised.exception.job_id, "job")
            self.assertIn("job", str(raised.exception))

    def test_expression_refusals_name_the_input_and_the_name(self):
        with self.assertRaisesRegex(MappingError, "input test-command uses a GitHub expression"):
            parse_jobs(with_job({"uses": uses("python-ci"), "with": {"test-command": "${{ vars.X }}"}}), VERSION)
        with self.assertRaisesRegex(MappingError, "name uses a GitHub expression"):
            parse_jobs(with_job({"uses": uses("python-ci"), "name": "${{ github.event_name }} App"}), VERSION)

    def test_explicit_empty_build_command_is_kept(self):
        data = with_job({"uses": uses("node-ci"), "with": {"build-command": ""}})
        _, node = legacy_to_modules(parse_jobs(data, VERSION))[1]
        self.assertEqual(node["node_components"][0]["build_command"], "")

    def test_refusal_names_the_offending_key(self):
        with self.assertRaisesRegex(MappingError, "bogus"):
            parse_jobs(with_job({"uses": uses("python-ci"), "with": {"bogus": "x"}}), VERSION)
        with self.assertRaisesRegex(MappingError, "needs"):
            parse_jobs(with_job({"uses": uses("python-ci"), "needs": "x"}), VERSION)


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


class ComponentJobIdsTests(unittest.TestCase):
    def test_ids_of_ci_kit_workflow_jobs(self):
        data = {
            "jobs": {
                "template-contract": CONTRACT,
                "good": {"uses": uses("python-ci")},
                "bad-ref": {"uses": uses("node-ci", "v0.0.1"), "if": "true"},
                "docker": {"uses": uses("docker-build")},
                "plain": {"runs-on": "ubuntu-latest"},
                "foreign": {"uses": "other/repo/.github/workflows/x.yml@v1"},
            }
        }
        self.assertEqual(component_job_ids(data, VERSION), {"good", "bad-ref", "docker"})

    def test_no_jobs(self):
        self.assertEqual(component_job_ids({}, VERSION), set())


if __name__ == "__main__":
    unittest.main()

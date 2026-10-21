from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.equivalence import effective, equivalence_findings
from ci_kit.inputs import fill, load_inputs
from ci_kit.legacy_jobs import LegacyJob
from ci_kit.plan import Plan


def plan(**matrices: list[dict]) -> Plan:
    return Plan(modules=["core", *matrices], matrices=matrices)


def python_job(**with_: object) -> LegacyJob:
    return LegacyJob("app", "App", "python", {"working-directory": ".", **with_})


def python_entry(**fields: object) -> dict:
    return fill("python", {"id": "app", "name": "App", "path": ".", **fields})


class EffectiveTests(unittest.TestCase):
    def test_effective_overlays_defaults(self):
        table = load_inputs()["python-ci"]
        result = effective("python", {"test-command": "pytest -q"})
        self.assertNotIn("working-directory", result)
        self.assertEqual(set(result), set(table) - {"working-directory"})
        self.assertEqual(result["test-command"], "pytest -q")
        self.assertEqual(result["python-version"], table["python-version"]["default"])

    def test_docker_has_only_build_inputs_and_no_push(self):
        result = effective("docker", {"target": "runtime"})
        self.assertEqual(result, {"context": ".", "dockerfile": "Dockerfile", "target": "runtime", "push": False})


class EquivalenceTests(unittest.TestCase):
    def test_matching_entries_have_no_findings(self):
        job = python_job(**{"test-command": "pytest", "run-build": False})
        entry = python_entry(test_command="pytest", run_build=False)
        self.assertEqual(equivalence_findings([job], plan(python=[entry])), [])

    def test_false_is_not_replaced_by_default(self):
        found = equivalence_findings([python_job(**{"run-build": False})], plan(python=[python_entry(run_build=True)]))
        self.assertEqual([f.code for f in found], ["CIK-MIGRATE-005"])
        self.assertIn("app", found[0].message)
        self.assertIn("run-build", found[0].message)

    def test_explicit_empty_build_command_against_default_is_005(self):
        job = LegacyJob("web", "Web", "node", {"build-command": ""})
        default = fill("node", {"id": "web", "name": "Web", "path": "."})
        found = equivalence_findings([job], plan(node=[default]))
        self.assertEqual([f.code for f in found], ["CIK-MIGRATE-005"])
        self.assertIn("build-command", found[0].message)
        explicit = fill("node", {"id": "web", "name": "Web", "path": ".", "build_command": ""})
        self.assertEqual(equivalence_findings([job], plan(node=[explicit])), [])

    def test_working_directory_differs(self):
        found = equivalence_findings([python_job(**{"working-directory": "backend"})], plan(python=[python_entry()]))
        self.assertEqual([f.code for f in found], ["CIK-MIGRATE-005"])
        self.assertIn("working-directory", found[0].message)

    def test_missing_working_directory_is_the_default(self):
        job = LegacyJob("app", "App", "python", {})
        self.assertEqual(equivalence_findings([job], plan(python=[python_entry()])), [])

    def test_job_set_must_match(self):
        extra = fill("python", {"id": "other", "name": "Other", "path": "."})
        found = equivalence_findings([python_job()], plan(python=[python_entry(), extra]))
        self.assertEqual([f.code for f in found], ["CIK-MIGRATE-005"])
        self.assertIn("other", found[0].message)
        found = equivalence_findings([python_job()], plan())
        self.assertEqual([f.code for f in found], ["CIK-MIGRATE-005"])
        self.assertIn("app", found[0].message)

    def test_same_id_in_another_module_does_not_pair(self):
        entry = fill("node", {"id": "app", "name": "App", "path": "."})
        found = equivalence_findings([python_job()], plan(node=[entry]))
        self.assertEqual(len(found), 2)

    def test_docker_pairs_without_path(self):
        job = LegacyJob("docker", "docker", "docker", {})
        entry = fill("docker", {"id": "docker", "name": "docker"})
        self.assertEqual(equivalence_findings([job], plan(docker=[entry])), [])
        entry = fill("docker", {"id": "docker", "name": "docker", "dockerfile": "Containerfile"})
        found = equivalence_findings([job], plan(docker=[entry]))
        self.assertIn("dockerfile", found[0].message)


if __name__ == "__main__":
    unittest.main()

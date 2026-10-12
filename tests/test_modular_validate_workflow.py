import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(rel):
    return yaml.safe_load((ROOT / rel).read_text())


class ModularValidateWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.wf = load(".github/workflows/validate.yml")

    def test_jobs_and_gate_contract(self):
        jobs = self.wf["jobs"]
        self.assertEqual(set(jobs), {"plan", "core-checks", "java", "gate"})
        self.assertEqual(jobs["gate"]["if"], "always()")
        self.assertEqual(set(jobs["gate"]["needs"]), {"plan", "core-checks", "java"})

    def test_gate_needs_cover_every_other_job(self):
        jobs = self.wf["jobs"]
        self.assertEqual(set(jobs["gate"]["needs"]), set(jobs) - {"gate"})

    def test_permissions_and_trigger(self):
        self.assertEqual(self.wf["permissions"], {"contents": "read", "actions": "read"})
        triggers = self.wf.get("on", self.wf.get(True))
        self.assertEqual(set(triggers), {"workflow_call"})

    def test_uses_are_static(self):
        for job in self.wf["jobs"].values():
            self.assertNotIn("${{", job.get("uses", ""))

    def test_no_expression_interpolated_in_run(self):
        for name, job in self.wf["jobs"].items():
            for step in job.get("steps", []):
                self.assertNotIn("${{", step.get("run", ""), f"{name}: {step.get('name')}")

    def test_toolkit_checkout_uses_job_workflow_sha(self):
        for name in ("plan", "gate"):
            steps = self.wf["jobs"][name]["steps"]
            toolkit = [s for s in steps if s.get("with", {}).get("path") == ".toolkit"]
            self.assertEqual(len(toolkit), 1, name)
            self.assertEqual(toolkit[0]["with"]["ref"], "${{ job.workflow_sha }}")
            self.assertEqual(toolkit[0]["with"]["repository"], "${{ job.workflow_repository }}")

    def test_gate_tolerates_empty_plan_output(self):
        steps = self.wf["jobs"]["gate"]["steps"]
        run = next(s["run"] for s in steps if s.get("name") == "Evaluate gate")
        self.assertIn('--enabled "${ENABLED:-[]}"', run)

    def test_gate_jobs_file_is_single_json_list(self):
        steps = self.wf["jobs"]["gate"]["steps"]
        run = next(s["run"] for s in steps if s.get("name") == "Fetch job results")
        self.assertIn("--paginate --jq '.jobs[]'", run)
        self.assertIn("> jobs.raw; then", run)
        self.assertIn("if jq -s . jobs.raw > jobs.tmp; then", run)
        self.assertIn("mv jobs.tmp jobs.json", run)
        self.assertNotIn("&& mv", run)
        self.assertNotRegex(run, r"gh api[^\n]*\|")

    def test_non_reusable_jobs_have_timeouts(self):
        for name, job in self.wf["jobs"].items():
            if "uses" not in job:
                self.assertIn("timeout-minutes", job, name)

    def test_checkout_and_python_pins(self):
        text = (ROOT / ".github/workflows/validate.yml").read_text()
        for line in re.findall(r"uses: (actions/\S+)", text):
            self.assertRegex(line, r"@[0-9a-f]{40}$")

    def test_java_job_shape(self):
        job = self.wf["jobs"]["java"]
        self.assertEqual(job["name"], "java (${{ matrix.component.name }})")
        self.assertEqual(job["uses"], "./.github/workflows/java-ci.yml")
        self.assertIs(job["strategy"]["fail-fast"], False)


if __name__ == "__main__":
    unittest.main()

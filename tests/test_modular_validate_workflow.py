import re
import unittest
from pathlib import Path

import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.inputs import input_name, module_fields


def load(rel):
    return yaml.safe_load((ROOT / rel).read_text())


class ModularValidateWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.wf = load(".github/workflows/validate.yml")

    def test_jobs_and_gate_contract(self):
        jobs = self.wf["jobs"]
        self.assertEqual(set(jobs), {"plan", "core-checks", "java", "python", "node", "docker", "gate"})
        self.assertEqual(jobs["gate"]["if"], "always()")
        self.assertEqual(set(jobs["gate"]["needs"]), {"plan", "core-checks", "java", "python", "node", "docker"})

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

    def test_plan_exposes_codes_and_commits_outputs(self):
        outputs = self.wf["jobs"]["plan"]["outputs"]
        self.assertEqual(outputs["codes"], "${{ steps.plan.outputs.codes }}")
        self.assertEqual(outputs["commits"], "${{ steps.plan.outputs.commits }}")

    def test_gate_passes_plan_codes_and_commits_via_env(self):
        step = next(s for s in self.wf["jobs"]["gate"]["steps"] if s.get("name") == "Evaluate gate")
        self.assertEqual(step["env"]["PLAN_CODES"], "${{ needs.plan.outputs.codes }}")
        self.assertEqual(step["env"]["TEMPLATE_COMMITS"], "${{ needs.plan.outputs.commits }}")
        self.assertIn('--plan-codes "${PLAN_CODES:-[]}"', step["run"])
        self.assertIn('--template-commits "${TEMPLATE_COMMITS:-$empty_object}"', step["run"])
        self.assertIn("empty_object='{}'", step["run"])
        self.assertNotIn("${{", step["run"])

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

    def test_language_jobs_pass_every_field_without_fallbacks(self):
        for job_id in ("java", "python", "node"):
            job = self.wf["jobs"][job_id]
            expected = {"working-directory": "${{ matrix.component.path }}"} | {
                input_name(f): f"${{{{ matrix.component.{f} }}}}" for f in module_fields(job_id)
            }
            self.assertEqual(job["with"], expected, job_id)
            self.assertEqual(job["name"], f"{job_id} (${{{{ matrix.component.name }}}})")
            self.assertEqual(job["uses"], f"./.github/workflows/{job_id}-ci.yml")
            self.assertEqual(job["if"], f"needs.plan.outputs.{job_id} != '[]'")
            self.assertEqual(job["strategy"]["matrix"]["component"], f"${{{{ fromJSON(needs.plan.outputs.{job_id}) }}}}")

    def test_docker_job_shape(self):
        job = self.wf["jobs"]["docker"]
        self.assertEqual(job["name"], "docker (${{ matrix.component.name }})")
        self.assertEqual(job["uses"], "./.github/workflows/docker-build.yml")
        self.assertEqual(job["if"], "needs.plan.outputs.docker != '[]'")
        self.assertIs(job["strategy"]["fail-fast"], False)
        self.assertEqual(job["with"], {
            "context": "${{ matrix.component.context }}",
            "dockerfile": "${{ matrix.component.dockerfile }}",
            "target": "${{ matrix.component.target }}",
            "push": False,
        })

    def test_no_or_fallback_in_with(self):
        for job in self.wf["jobs"].values():
            for value in (job.get("with") or {}).values():
                self.assertNotIn("||", str(value))

    def test_plan_exposes_every_module_output(self):
        outputs = self.wf["jobs"]["plan"]["outputs"]
        for name in ("java", "python", "node", "docker"):
            self.assertEqual(outputs[name], f"${{{{ steps.plan.outputs.{name} }}}}")


if __name__ == "__main__":
    unittest.main()

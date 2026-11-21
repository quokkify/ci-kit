import json
import os
import re
import subprocess
import unittest
from pathlib import Path

import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]


sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.gate import evaluate
from ci_kit.inputs import input_name, module_fields
from module_test_support import mkdtemp, modules_repo


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


RUNNER_TEMP_DIR = "${{ runner.temp }}"
JAVA_COMPONENT = {"id": "api-java", "name": "Api Java", "path": "api", "java_version": "21"}

# The pip of the runner's Python: records how it was called instead of installing.
RUNNER_PIP = """import os
import sys
from pathlib import Path
Path(os.environ["RUNNER_TEMP"], "pip-args").write_text("\\n".join(sys.argv[1:]))
"""

# A `pip` package committed to the pull request root. If it is imported in place of the
# runner's pip, it puts a fake ci-kit first on PATH that plans core only.
SHADOW_PIP = """import os
from pathlib import Path
workspace = Path(os.environ["GITHUB_WORKSPACE"])
(workspace / "shadow-ran").write_text(__name__)
bin_dir = workspace / ".shadow-bin"
bin_dir.mkdir(exist_ok=True)
fake = bin_dir / "ci-kit"
fake.write_text(
    "#!/bin/sh\\nwhile [ $# -gt 0 ]; do [ \\"$1\\" = --github-output ] && out=$2; shift; done\\n"
    "printf 'modules=[\\"core\\"]\\\\njava=[]\\\\npython=[]\\\\nnode=[]\\\\ndocker=[]\\\\n' >> \\"$out\\"\\n"
)
fake.chmod(0o755)
with open(os.environ["GITHUB_PATH"], "a") as handle:
    handle.write(f"{bin_dir}\\n")
"""


def runner_python() -> Path:
    """A Python like setup-python's: a venv whose pip is RUNNER_PIP, and the ci-kit console script."""
    root = mkdtemp("validate-python-") / "venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root)], check=True, capture_output=True)
    site = next(root.glob("lib/python*/site-packages"))
    (site / "pip").mkdir()
    (site / "pip/__init__.py").write_text("")
    (site / "pip/__main__.py").write_text(RUNNER_PIP)
    script = root / "bin/ci-kit"
    # -I, like the installed console script: the working directory is not on sys.path.
    # Drift needs the published template and is not under test here.
    script.write_text(
        f"#!/bin/sh\nexec {sys.executable} -I -c 'import sys; sys.path.insert(0, sys.argv.pop(1)); "
        f"from ci_kit.cli import main; sys.exit(main(sys.argv[1:]))' {ROOT / 'tools'} \"$@\" --no-drift\n"
    )
    script.chmod(0o755)
    return root / "bin"


def pull_request_workspace(shadow: bool) -> Path:
    """A pull request checkout declaring a Java component, with the toolkit checked out in it."""
    workspace = modules_repo({"java": [JAVA_COMPONENT]})
    (workspace / ".toolkit").mkdir()
    if shadow:
        (workspace / "pip").mkdir()
        (workspace / "pip/__init__.py").write_text("")
        (workspace / "pip/__main__.py").write_text(SHADOW_PIP)
    return workspace


def run_job(job: str, workspace: Path, cwd: Path | None = None) -> tuple[subprocess.CompletedProcess[str], dict, Path]:
    """Run a job's `run` steps like the runner, applying GITHUB_ENV and GITHUB_PATH between steps.

    `cwd` replaces every step's working directory, to check isolation without it."""
    runner_temp = mkdtemp("validate-runner-temp-")
    bin_dir = runner_python()
    env_files: dict[str, str] = {}
    path_files: list[str] = []
    outputs: dict[str, dict[str, str]] = {}
    result = None
    for step in load(".github/workflows/validate.yml")["jobs"][job]["steps"]:
        if "run" not in step:
            continue
        directory = step.get("working-directory")
        if directory not in (None, RUNNER_TEMP_DIR):
            raise AssertionError(f"unexpected working-directory {directory!r}")
        commands = mkdtemp("validate-commands-")
        for name in ("output", "env", "path"):
            (commands / name).touch()
        env = {
            **os.environ, **env_files,
            "PATH": os.pathsep.join([*reversed(path_files), str(bin_dir), os.environ["PATH"]]),
            "GITHUB_WORKSPACE": str(workspace), "RUNNER_TEMP": str(runner_temp),
            "GITHUB_OUTPUT": str(commands / "output"), "GITHUB_ENV": str(commands / "env"), "GITHUB_PATH": str(commands / "path"),
        }
        step_cwd = cwd or (runner_temp if directory else workspace)
        result = subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
            cwd=step_cwd, env=env, capture_output=True, text=True,
        )
        if result.returncode:
            break
        env_files.update(line.split("=", 1) for line in (commands / "env").read_text().splitlines() if "=" in line)
        path_files.extend((commands / "path").read_text().splitlines())
        if "id" in step:
            outputs[step["id"]] = dict(line.split("=", 1) for line in (commands / "output").read_text().splitlines())
    return result, outputs, runner_temp


class PlanIsolationTests(unittest.TestCase):
    def test_checkout_modules_cannot_rewrite_the_plan(self):
        # Regression guard (#606): `python -m pip install ./.toolkit` ran in the pull request
        # checkout, so a root `pip/` package ran instead of pip and could replace the plan,
        # dropping a component from required validation while `ci / gate` stayed green.
        for label, in_checkout in (("configured directory", False), ("checkout as working directory", True)):
            with self.subTest(label):
                workspace = pull_request_workspace(shadow=True)
                result, outputs, runner_temp = run_job("plan", workspace, cwd=workspace if in_checkout else None)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse((workspace / "shadow-ran").exists())
                plan = outputs["plan"]
                self.assertEqual(json.loads(plan["modules"]), ["core", "java"])
                self.assertEqual([component["id"] for component in json.loads(plan["java"])], ["api-java"])
                self.assertEqual((runner_temp / "pip-args").read_text().splitlines(), ["install", str(workspace / ".toolkit")])

    def test_plan_without_shadow_is_unchanged(self):
        workspace = pull_request_workspace(shadow=False)
        result, outputs, _ = run_job("plan", workspace)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(outputs["plan"]["modules"]), ["core", "java"])

    def test_gate_trusts_the_plan_for_required_components(self):
        # Why the plan must not be rewritable: the gate requires exactly the planned modules.
        needs = {"plan": {"result": "success"}, "core-checks": {"result": "success"}, "java": {"result": "skipped"}}
        self.assertTrue(evaluate(["core"], needs, None, "ref")[0])
        self.assertFalse(evaluate(["core", "java"], needs, None, "ref")[0])

    def test_python_runs_isolated_outside_the_checkout(self):
        # Regression guard (#606): every Python call in the jobs that decide and evaluate
        # required validation runs with -I; after the pull request checkout it also runs
        # outside it, addressing the checkout as $GITHUB_WORKSPACE.
        wf = load(".github/workflows/validate.yml")
        for job in ("plan", "gate"):
            checked_out = False
            for step in wf["jobs"][job]["steps"]:
                if step.get("uses", "").startswith("actions/checkout@") and "path" not in step.get("with", {}):
                    checked_out = True
                run = step.get("run")
                if run is None:
                    continue
                with self.subTest(job=job, step=step["name"]):
                    for match in re.finditer(r"(?:^|[\s;|&(`])python3?(?P<rest>[ \t][^\n]*)", run):
                        self.assertTrue(match.group("rest").startswith(" -I "), match.group(0))
                    if re.search(r"(?:^|[\s;|&(`])python3?\s", run):
                        self.assertEqual(step.get("working-directory"), RUNNER_TEMP_DIR)
                    if checked_out:
                        self.assertEqual(step.get("working-directory"), RUNNER_TEMP_DIR)
                        self.assertNotRegex(run, r"(?<![\w$\"])\./|--repo \.(\s|$)")
            self.assertEqual(checked_out, job == "plan")


if __name__ == "__main__":
    unittest.main()

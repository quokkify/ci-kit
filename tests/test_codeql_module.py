from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from module_test_support import ROOT, git, mkdtemp, modules_repo, render, render_tree, shared_template_repo

PIN = "v99.0.0"
REUSABLE = ROOT / ".github/workflows/codeql-analysis.yml"
CODEQL = ".github/workflows/codeql.yml"
GITLEAKS = ".github/workflows/gitleaks.yml"


def data_file(data: dict) -> Path:
    path = mkdtemp("codeql-data-") / "data.yml"
    path.write_text(yaml.safe_dump(data))
    return path


def security_tree(answers: dict) -> dict[str, bytes]:
    return render_tree(shared_template_repo(), data_file({**answers, "module": "security", "toolkit_version": PIN}))


def plan_steps() -> dict[str, dict]:
    steps = yaml.safe_load(REUSABLE.read_text())["jobs"]["plan"]["steps"]
    return {step.get("id", step["name"]): step for step in steps}


def ci_kit_shim() -> Path:
    """Put a ci-kit executable backed by this checkout's sources on PATH."""
    bin_dir = mkdtemp("codeql-bin-")
    shim = bin_dir / "ci-kit"
    shim.write_text(
        # -I, like the installed console script: the working directory is not on sys.path.
        f"#!/bin/sh\nexec {sys.executable} -I -c 'import sys; sys.path.insert(0, sys.argv.pop(1)); "
        f"from ci_kit.cli import main; sys.exit(main(sys.argv[1:]))' {ROOT / 'tools'} \"$@\"\n"
    )
    shim.chmod(0o755)
    return bin_dir


RUNNER_TEMP_DIR = "${{ runner.temp }}"


def run_step(
    step_id: str, env: dict[str, str], workspace: Path | None = None, cwd: Path | None = None, bin_dir: Path | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
    """Run one plan step like the runner: `workspace` is the checkout, `cwd` overrides the step's directory."""
    step = plan_steps()[step_id]
    runner_temp = mkdtemp("codeql-runner-temp-")
    target = mkdtemp("codeql-step-") / "out"
    target.touch()
    workspace = workspace or mkdtemp("codeql-workspace-")
    if cwd is None:
        directory = step.get("working-directory")
        if directory not in (None, RUNNER_TEMP_DIR):
            raise AssertionError(f"unexpected working-directory {directory!r}")
        cwd = runner_temp if directory else workspace
    path = f"{bin_dir or ci_kit_shim()}{os.pathsep}{os.environ['PATH']}"
    full_env = {
        **os.environ, **step.get("env", {}), **env, "PATH": path, "GITHUB_OUTPUT": str(target),
        "GITHUB_WORKSPACE": str(workspace), "RUNNER_TEMP": str(runner_temp),
    }
    result = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=cwd, env=full_env, capture_output=True, text=True)
    outputs = dict(line.split("=", 1) for line in target.read_text().splitlines())
    return result, outputs


def language_modules_repo(components: list[dict]) -> Path:
    declarations: dict[str, list[dict]] = {}
    for component in components:
        entry = {key: component[key] for key in ("id", "name", "path")}
        if component["type"] == "java":
            entry["java_version"] = "21"
        declarations.setdefault(component["type"], []).append(entry)
    return modules_repo(declarations)


def planned_output(repo: Path) -> tuple[subprocess.CompletedProcess[str], str]:
    result, outputs = run_step("plan", {}, workspace=repo)
    return result, outputs.get("codeql", "")


def select_languages(override: str, planned: str) -> list[str]:
    result, decision = run_step("override", {"OVERRIDE": override})
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    plan_needed = decision["plan"] == "true"
    result, outputs = run_step("languages", {
        "OVERRIDE": override, "PLAN_NEEDED": decision["plan"], "PLANNED": planned if plan_needed else "",
        "BASE_NEEDED": "false" if plan_needed else "", "BASE_PLANNED": "",
    })
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return json.loads(outputs["languages"])


def effective_languages(security: dict[str, bytes], components: list[dict]) -> list[str]:
    caller = yaml.safe_load(security[CODEQL])
    override = caller["jobs"]["codeql"]["with"]["languages-override"]
    result, planned = planned_output(language_modules_repo(components))
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return select_languages(override, planned)


def analyze_category(job: dict) -> str:
    return next(step["with"]["category"] for step in job["steps"] if "codeql-action/analyze@" in step.get("uses", ""))


def reusable_analyze() -> dict:
    return yaml.safe_load(REUSABLE.read_text())["jobs"]["analyze"]


DECLARED = {
    "python": {"id": "app-python", "name": "App Python", "path": "."},
    "node": {"id": "web-node", "name": "Web Node", "path": "web"},
    "java": {"id": "api-java", "name": "Api Java", "path": "api", "java_version": "21"},
}


def write_state(repo: Path, modules: list[str], files: dict[str, str]) -> None:
    """Replace the working tree with core plus `modules` and the given source files."""
    for path in repo.iterdir():
        if path.name != ".git":
            shutil.rmtree(path) if path.is_dir() else path.unlink()
    (repo / ".ci/modules").mkdir(parents=True)
    for module in ("core", *modules):
        (repo / f".copier-answers.{module}.yml").write_text(f"module: {module}\n")
        data: dict = {"schema_version": 1}
        if module != "core":
            data["components"] = [DECLARED[module]]
        (repo / f".ci/modules/{module}.yml").write_text(yaml.safe_dump(data))
    for name, content in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(content)


def pull_request_repo(base: list[str], base_files: dict[str, str], head: list[str], head_files: dict[str, str]) -> tuple[Path, str]:
    """A checkout of a pull request head whose base commit declares other modules."""
    repo = mkdtemp("codeql-pr-")
    git(repo, "init", "-q", "-b", "main")
    write_state(repo, base, base_files)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    base_sha = git(repo, "rev-parse", "HEAD")
    write_state(repo, head, head_files)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "head")
    return repo, base_sha


def planned_languages(
    repo: Path, event: str, base_sha: str, cwd: Path | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[str] | None]:
    """Run the plan job's scripts the way a `pull_request`/`push` run executes them."""
    head_result, head = run_step("plan", {}, workspace=repo, cwd=cwd)
    if head_result.returncode != 0:
        raise AssertionError(head_result.stdout + head_result.stderr)
    base_result, base = run_step("base-plan", {"EVENT_NAME": event, "BASE_SHA": base_sha}, workspace=repo, cwd=cwd)
    if base_result.returncode != 0:
        return base_result, None
    result, outputs = run_step("languages", {
        "OVERRIDE": "[]", "PLAN_NEEDED": "true", "PLANNED": head["codeql"],
        "BASE_NEEDED": base.get("needed", ""), "BASE_PLANNED": base.get("codeql", ""),
    }, workspace=repo, cwd=cwd)
    if result.returncode != 0:
        return result, None
    return result, json.loads(outputs["languages"])


class CodeqlLanguageTests(unittest.TestCase):
    def test_codeql_languages_follow_language_modules(self) -> None:
        security = security_tree({})
        python = {"type": "python", "id": "app-python", "name": "App Python", "path": "."}
        node = {"type": "node", "id": "web-node", "name": "Web Node", "path": "web"}
        java = {"type": "java", "id": "api-java", "name": "Api Java", "path": "api"}
        cases = (
            ([python], ["actions", "python"]),
            ([node], ["actions", "javascript-typescript"]),
            ([java], ["actions", "java-kotlin"]),
            ([python, node, java], ["actions", "python", "javascript-typescript", "java-kotlin"]),
        )
        for components, expected in cases:
            with self.subTest(components=[component["type"] for component in components]):
                self.assertEqual(effective_languages(security, components), expected)
        self.assertTrue(analyze_category(reusable_analyze()))

    def test_override_wins(self) -> None:
        override = ["actions", "go"]
        security = security_tree({"codeql_languages": override})
        components = [{"type": "python", "id": "app-python", "name": "App Python", "path": "."}]
        self.assertEqual(effective_languages(security, components), override)
        self.assertEqual(select_languages('["go"]', '["actions","python"]'), ["go"])
        self.assertEqual(select_languages("[]", '["actions","python"]'), ["actions", "python"])

    def test_override_renders_the_way_prettier_formats_it(self) -> None:
        # Regression guard: a double-quoted escaped JSON string failed `prettier --check` in consumers.
        for override, line in (
            ([], 'languages-override: "[]"'),
            (["actions", "java-kotlin"], "languages-override: '[\"actions\", \"java-kotlin\"]'"),
        ):
            with self.subTest(override=override):
                tree = security_tree({"codeql": True, "codeql_languages": override})
                text = tree[CODEQL].decode()
                self.assertIn(f"      {line}\n", text)
                value = yaml.safe_load(text)["jobs"]["codeql"]["with"]["languages-override"]
                self.assertEqual(value, json.dumps(override))
                if override:
                    if not (os.environ.get("CI_KIT_PRETTIER_CHECK") and shutil.which("npx")):
                        self.skipTest("set CI_KIT_PRETTIER_CHECK=1 (and have npx) to run prettier@3 --check, which needs the npm registry")
                    out = mkdtemp("codeql-prettier-")
                    (out / "codeql.yml").write_bytes(tree[CODEQL])
                    result = subprocess.run(
                        ["npx", "--yes", "prettier@3", "--check", str(out / "codeql.yml")],
                        capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_override_is_parsed_not_string_compared(self) -> None:
        self.assertEqual(select_languages("[ ]", '["actions","python"]'), ["actions", "python"])
        self.assertEqual(run_step("override", {"OVERRIDE": "[ ]"})[1], {"plan": "true"})
        self.assertEqual(run_step("override", {"OVERRIDE": '[ "go" ]'})[1], {"plan": "false"})
        for bad in ('"go"', "[1]", "not json"):
            with self.subTest(override=bad):
                self.assertNotEqual(run_step("override", {"OVERRIDE": bad})[0].returncode, 0)

    def test_override_skips_plan_and_toolchain(self) -> None:
        steps = plan_steps()
        self.assertEqual(list(steps)[0], "override")
        for step_id, step in steps.items():
            if step_id not in ("override", "languages"):
                with self.subTest(step=step_id):
                    self.assertEqual(step.get("if"), "steps.override.outputs.plan == 'true'")

    def test_plan_findings_keep_codeql_running(self) -> None:
        # Regression guard: a plan finding used to fail the plan job and skip every analysis.
        repo = modules_repo({
            "python": [{"id": "app", "name": "App Python", "path": "."}],
            "node": [{"id": "app", "name": "App Node", "path": "."}],
        })
        result, planned = planned_output(repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CIK-CORE-005", result.stdout + result.stderr)
        self.assertIn("::warning", result.stdout)
        self.assertEqual(json.loads(planned), ["actions", "python", "javascript-typescript"])
        self.assertEqual(select_languages("[]", planned), ["actions", "python", "javascript-typescript"])

    def test_invalid_declaration_keeps_its_codeql_language(self) -> None:
        # Regression guard: an unloaded language module used to drop out of the codeql output.
        repo = modules_repo({
            "java": [{"id": "app-java", "name": "App Java", "path": "."}],
            "python": [{"id": "app-python", "name": "App Python", "path": "."}],
        })
        result, planned = planned_output(repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("CIK-CORE-004", result.stdout + result.stderr)
        self.assertEqual(json.loads(planned), ["actions", "python", "java-kotlin"])
        self.assertEqual(select_languages("[]", planned), ["actions", "python", "java-kotlin"])

    def test_missing_plan_output_fails_selection(self) -> None:
        result, outputs = run_step("languages", {"OVERRIDE": "[]", "PLAN_NEEDED": "true", "PLANNED": ""})
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("languages", outputs)

    def test_caller_delegates_to_the_reusable_analysis(self) -> None:
        caller = yaml.safe_load(security_tree({})[CODEQL])
        self.assertEqual(caller["jobs"]["codeql"]["uses"], f"quokkify/ci-kit/.github/workflows/codeql-analysis.yml@{PIN}")
        self.assertEqual(caller["jobs"]["codeql"]["permissions"], reusable_analyze()["permissions"])
        self.assertEqual(caller["jobs"]["codeql"]["with"], {"languages-override": "[]"})


class CodeqlBaseLanguageTests(unittest.TestCase):
    def test_pull_request_deleting_answers_keeps_base_language(self) -> None:
        # Regression guard: a pull request that deleted .copier-answers.python.yml and kept
        # Python code was analyzed for `actions` only, and the CodeQL check still passed.
        repo, base_sha = pull_request_repo(["python"], {"app.py": "print(1)\n"}, [], {"app.py": "print(2)\n"})
        result, languages = planned_languages(repo, "pull_request", base_sha)
        self.assertEqual(languages, ["actions", "python"], result.stdout + result.stderr)

    def test_pull_request_adding_module_plans_it(self) -> None:
        # Regression guard: base-commit planning must not drop a language the head adds.
        repo, base_sha = pull_request_repo(["python"], {"app.py": "1\n"}, ["python", "node"], {"app.py": "1\n", "web/app.js": "1\n"})
        result, languages = planned_languages(repo, "pull_request", base_sha)
        self.assertEqual(languages, ["actions", "python", "javascript-typescript"], result.stdout + result.stderr)

    def test_push_plans_the_checkout_only(self) -> None:
        # Regression guard: only pull requests consult the base commit.
        repo, base_sha = pull_request_repo(["python"], {"app.py": "1\n"}, [], {"app.py": "1\n"})
        for event in ("push", "schedule", "workflow_dispatch"):
            with self.subTest(event=event):
                result, languages = planned_languages(repo, event, base_sha)
                self.assertEqual(languages, ["actions"], result.stdout + result.stderr)
                self.assertEqual(run_step("base-plan", {"EVENT_NAME": event, "BASE_SHA": ""}, workspace=repo)[1], {"needed": "false"})

    def test_unreadable_base_fails_closed(self) -> None:
        # Regression guard: without the base plan the job must fail, not analyze the head alone.
        repo, _ = pull_request_repo(["python"], {"app.py": "1\n"}, [], {"app.py": "1\n"})
        for base_sha in ("0123456789abcdef0123456789abcdef01234567", "", "not-a-sha"):
            with self.subTest(base_sha=base_sha):
                result, languages = planned_languages(repo, "pull_request", base_sha)
                self.assertIsNone(languages)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("base commit", result.stdout + result.stderr)

    def test_missing_base_plan_output_fails_selection(self) -> None:
        result, outputs = run_step("languages", {
            "OVERRIDE": "[]", "PLAN_NEEDED": "true", "PLANNED": '["actions"]', "BASE_NEEDED": "true", "BASE_PLANNED": "",
        })
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("languages", outputs)

    def test_shallow_checkout_fetches_the_base_commit(self) -> None:
        origin, base_sha = pull_request_repo(["java"], {"api/App.java": "class App {}\n"}, [], {"api/App.java": "class App {}\n"})
        git(origin, "config", "uploadpack.allowAnySHA1InWant", "true")
        clone = mkdtemp("codeql-shallow-") / "repo"
        git(origin.parent, "clone", "-q", "--depth=1", f"file://{origin}", str(clone))
        with self.assertRaises(subprocess.CalledProcessError):
            git(clone, "cat-file", "-e", f"{base_sha}^{{commit}}")
        result, languages = planned_languages(clone, "pull_request", base_sha)
        self.assertEqual(languages, ["actions", "java-kotlin"], result.stdout + result.stderr)

    def test_removed_module_without_source_is_not_analyzed(self) -> None:
        # Regression guard: a pull request removing a module and all of its code must stay
        # mergeable; CodeQL fails a language that has no source files.
        repo, base_sha = pull_request_repo(
            ["python", "node"], {"app.py": "1\n", "web/app.ts": "1\n"}, [], {"README.md": "docs\n", "web/data.json": "{}\n"},
        )
        result, languages = planned_languages(repo, "pull_request", base_sha)
        self.assertEqual(languages, ["actions"], result.stdout + result.stderr)
        self.assertIn("::notice", result.stdout)
        self.assertIn("python", result.stdout)
        self.assertIn("javascript-typescript", result.stdout)


SHADOW = """import os
from pathlib import Path
Path(os.environ["GITHUB_WORKSPACE"], "shadow-ran").write_text(__name__)
with open(os.environ["GITHUB_OUTPUT"], "a") as handle:
    handle.write('languages=["actions"]\\ncodeql=["actions"]\\nneeded=false\\n')
raise SystemExit(0)
"""


def fake_ci_kit(exit_code: int, codes: list[str]) -> Path:
    """A ci-kit stand-in that writes a plan and exits with `exit_code`."""
    bin_dir = mkdtemp("codeql-fake-bin-")
    script = bin_dir / "ci-kit"
    script.write_text(
        "#!/bin/sh\nwhile [ $# -gt 0 ]; do [ \"$1\" = --github-output ] && out=$2; shift; done\n"
        f"printf 'codeql=[\"actions\",\"python\"]\\ncodes={json.dumps(codes)}\\n' >> \"$out\"\nexit {exit_code}\n"
    )
    script.chmod(0o755)
    return bin_dir


class CodeqlPlanIsolationTests(unittest.TestCase):
    def test_checkout_modules_cannot_rewrite_the_plan(self) -> None:
        # Regression guard: the plan scripts ran with the pull request checkout first on
        # sys.path, so a root json.py (or a module ci-kit imports) could rewrite the plan.
        shadows = {f"{name}.py": SHADOW for name in ("json", "subprocess", "tempfile", "re", "yaml", "jsonschema")}
        repo, base_sha = pull_request_repo(["python"], {"app.py": "1\n"}, [], {"app.py": "1\n", **shadows})
        for label, cwd in (("configured directory", None), ("checkout as working directory", repo)):
            with self.subTest(label):
                result, languages = planned_languages(repo, "pull_request", base_sha, cwd=cwd)
                self.assertEqual(languages, ["actions", "python"], result.stdout + result.stderr)
                self.assertFalse((repo / "shadow-ran").exists())

    def test_python_runs_isolated_outside_the_checkout(self) -> None:
        steps = yaml.safe_load(REUSABLE.read_text())["jobs"]["plan"]["steps"]
        checked_out = False
        for step in steps:
            if step.get("uses", "").startswith("actions/checkout@") and "path" not in step.get("with", {}):
                checked_out = True
            run = step.get("run")
            if run is None:
                continue
            with self.subTest(step=step["name"]):
                for match in re.finditer(r"(?:^|[\s;|&(`])python3?(?P<rest>[ \t][^\n]*)", run):
                    self.assertTrue(match.group("rest").startswith(" -I "), match.group(0))
                if checked_out:
                    self.assertEqual(step.get("working-directory"), RUNNER_TEMP_DIR)
                    self.assertNotRegex(run, r"(?<![\w$\"])\./|--repo \.(\s|$)")
        self.assertTrue(checked_out)

    def test_base_plan_failure_is_not_ignored(self) -> None:
        # Regression guard: a failing base `ci-kit plan` must fail the job; plan findings must not.
        repo, base_sha = pull_request_repo(["python"], {"app.py": "1\n"}, [], {"app.py": "1\n"})
        env = {"EVENT_NAME": "pull_request", "BASE_SHA": base_sha}
        result, _ = run_step("base-plan", env, workspace=repo, bin_dir=fake_ci_kit(2, []))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ci-kit plan failed", result.stdout)
        result, _ = run_step("base-plan", env, workspace=repo, bin_dir=fake_ci_kit(1, []))
        self.assertNotEqual(result.returncode, 0)
        result, outputs = run_step("base-plan", env, workspace=repo, bin_dir=fake_ci_kit(1, ["CIK-CORE-005"]))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(outputs["codeql"], '["actions","python"]')
        self.assertIn("::warning", result.stdout)

    def test_base_plan_findings_keep_its_languages(self) -> None:
        repo = mkdtemp("codeql-pr-")
        git(repo, "init", "-q", "-b", "main")
        write_state(repo, ["python", "node"], {"app.py": "1\n", "app.js": "1\n"})
        (repo / ".ci/modules/node.yml").write_text(yaml.safe_dump({"schema_version": 1, "components": [{**DECLARED["node"], "path": "."}]}))
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "base")
        base_sha = git(repo, "rev-parse", "HEAD")
        write_state(repo, [], {"app.py": "1\n", "app.js": "1\n"})
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "head")
        result, languages = planned_languages(repo, "pull_request", base_sha)
        self.assertEqual(languages, ["actions", "python", "javascript-typescript"], result.stdout + result.stderr)


class CodeqlSourceDetectionTests(unittest.TestCase):
    def languages_for(self, base: list[str], files: dict[str, str]) -> list[str] | None:
        base_files = {"src.txt": "1\n"}
        repo, base_sha = pull_request_repo(base, base_files, [], files)
        result, languages = planned_languages(repo, "pull_request", base_sha)
        self.assertIsNotNone(languages, result.stdout + result.stderr)
        return languages

    def test_every_extracted_file_type_counts_as_source(self) -> None:
        # Regression guard: `.cjs` and other extractor file types were not counted, so renaming
        # a file let the pull request skip the language its base commit declares.
        cases = {
            "python": ["a.py", "a.pyw", "a.pyi", "A.PY"],
            "node": [
                "a.js", "a.jsx", "a.mjs", "a.cjs", "a.es6", "a.es", "a.xsjs", "a.xsjslib",
                "a.ts", "a.tsx", "a.mts", "a.cts",
                "a.htm", "a.html", "a.xhtm", "a.xhtml", "a.vue", "a.hbs", "a.ejs", "a.njk", "a.erb", "a.jsp", "a.dot",
                "A.CJS",
            ],
            "java": ["A.java"],
        }
        expected = {"python": "python", "node": "javascript-typescript", "java": "java-kotlin"}
        for module, names in cases.items():
            for name in names:
                with self.subTest(file=name):
                    self.assertEqual(self.languages_for([module], {f"src/{name}": "x\n"}), ["actions", expected[module]])

    def test_interpreter_scripts_without_extension_count_as_source(self) -> None:
        for module, line, language in (
            ("node", "#!/usr/bin/env node", "javascript-typescript"),
            ("node", "#!/usr/local/bin/bun", "javascript-typescript"),
            ("node", "#!/usr/bin/env -S tsx --watch", "javascript-typescript"),
            ("python", "#!/usr/bin/env python3", "python"),
            ("python", "#!/usr/bin/python3.12 -u", "python"),
        ):
            with self.subTest(shebang=line):
                self.assertEqual(self.languages_for([module], {"bin/tool": f"{line}\nx\n"}), ["actions", language])

    def test_files_codeql_does_not_extract_do_not_count(self) -> None:
        for module, files in (
            ("python", {"bin/tool": "#!/bin/sh\n", "notes": "python\n", "a.pyc": "x"}),
            ("node", {"package.json": "{}\n", "a.yml": "a: 1\n", "bin/tool": "#!/bin/bash\nnode x\n"}),
            ("java", {"A.kt": "class A\n", "build.gradle": "x\n"}),
        ):
            with self.subTest(module=module):
                self.assertEqual(self.languages_for([module], files), ["actions"])


class SecurityModuleTests(unittest.TestCase):
    def test_security_flags(self) -> None:
        base = {".copier-answers.security.yml", ".ci/modules/security.yml"}
        cases = (
            ({"codeql": False, "gitleaks": True}, base | {GITLEAKS}),
            ({"codeql": True, "gitleaks": False}, base | {CODEQL}),
            ({}, base | {CODEQL, GITLEAKS}),
        )
        for answers, expected in cases:
            with self.subTest(answers=answers):
                self.assertEqual(set(security_tree(answers)), expected)

    def test_reusable_codeql_actionlint(self) -> None:
        self.assertIsNotNone(shutil.which("actionlint"), "actionlint is required for workflow linting")
        dest = render(shared_template_repo(), data_file({"module": "security", "toolkit_version": PIN}))
        for path in (REUSABLE, dest / CODEQL, dest / GITLEAKS):
            with self.subTest(path=path.name):
                # Same ignore as validate-toolkit.yml: actionlint does not know job.workflow_*.
                result = subprocess.run(
                    ["actionlint", "-ignore", 'property "workflow_(repository|sha)" is not defined', str(path)],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

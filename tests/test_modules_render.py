from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import jsonschema
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from module_test_support import ROOT, make_template_repo, mkdtemp, render_tree, shared_template_repo
from ci_kit.plan import build_plan
from ci_kit.inputs import load_inputs, module_fields

MODULE_FILES = {
    "release": (
        ".github/workflows/release.yml", ".github/release-please/config.json",
        ".github/release-please/manifest.json", ".github/scripts/enrich_release_notes.py",
    ),
    "allure": (".github/workflows/allure-report.yml", ".github/allure/allurerc.mjs", ".github/allure/safe_extract.py"),
    "renovate": (".github/renovate.json",),
}
DEFAULT_COMPONENT = {"id": "app-java", "name": "Application Java", "path": ".", "java_version": "21"}


class ModuleRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="modules-render-")).resolve()
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.repo = make_template_repo(cls.tmp)
        cls.counter = 0

    def copy(self, module: str, *data: str) -> tuple[subprocess.CompletedProcess[str], Path]:
        type(self).counter += 1
        dest = self.tmp / f"dest-{self.counter}"
        dest.mkdir()
        args = ["copier", "copy", "--trust", "--defaults", "--vcs-ref", "HEAD", "-a", f".copier-answers.{module}.yml", "-d", f"module={module}"]
        for item in data:
            args += ["-d", item]
        result = subprocess.run([*args, str(self.repo), str(dest)], capture_output=True, text=True)
        return result, dest

    def render(self, module: str, *data: str) -> Path:
        result, dest = self.copy(module, *data)
        self.assertEqual(result.returncode, 0, result.stderr)
        return dest

    def rendered_files(self, module: str) -> set[str]:
        dest = self.render(module)
        return {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()}

    def decl(self, module: str, *data: str) -> str:
        return (self.render(module, *data) / f".ci/modules/{module}.yml").read_text()

    def caller(self, **answers: str) -> str:
        data = [f"{key}={value}" for key, value in answers.items()]
        return (self.render("core", *data) / ".github/workflows/validate.yml").read_text()

    def load_schema(self, module: str, version: int) -> dict:
        return json.loads((ROOT / f"tools/ci_kit/schemas/{module}.v{version}.json").read_text())

    def copy_java(self, component_override: dict) -> subprocess.CompletedProcess[str]:
        component = {**DEFAULT_COMPONENT, **component_override}
        result, _ = self.copy("java", f"java_components={json.dumps([component])}")
        return result

    def render_with_core(self, module: str, data: str) -> Path:
        dest = self.render(module, data)
        args = ["copier", "copy", "--trust", "--defaults", "--vcs-ref", "HEAD", "-a", ".copier-answers.core.yml", "-d", "module=core", str(self.repo), str(dest)]
        subprocess.run(args, check=True, capture_output=True, text=True)
        return dest

    def test_declarations_with_unsorted_keys_do_not_drift(self) -> None:
        cases = {
            "node": ("node_components", [{"id": "web", "name": "Web", "path": ".", "node_version": "22", "package_manager": "npm"}]),
            "docker": ("docker_images", [{"id": "img", "context": ".", "dockerfile": "Dockerfile"}]),
            "java": ("java_components", [{**DEFAULT_COMPONENT, "run_lint": False, "timeout_minutes": 30, "test_command": "gradle test"}]),
            "python": ("python_components", [{"id": "svc", "name": "Svc", "path": ".", "test_command": "pytest", "python_version": "3.12"}]),
        }
        for module, (key, components) in cases.items():
            with self.subTest(module=module):
                dest = self.render_with_core(module, f"{key}={json.dumps(components)}")
                self.assertEqual(build_plan(dest, drift=True, src_override=str(self.repo)).findings, [])

    def test_default_answers_pin_workflow_default_versions(self) -> None:
        table = load_inputs()
        python = yaml.safe_load(self.decl("python"))["components"][0]
        node = yaml.safe_load(self.decl("node"))["components"][0]
        self.assertEqual(python["python_version"], table["python-ci"]["python-version"]["default"])
        self.assertEqual(node["node_version"], table["node-ci"]["node-version"]["default"])

    def test_core_renders_exact_file_set(self) -> None:
        self.assertEqual(
            self.rendered_files("core"),
            {
                ".copier-answers.core.yml", ".ci/modules/core.yml", ".github/workflows/validate.yml",
                ".github/workflows/copier-update.yml", "docs/ci-kit.md", "README.md", ".github/pull_request_template.md",
            },
        )

    def test_java_renders_exact_file_set(self) -> None:
        self.assertEqual(self.rendered_files("java"), {".copier-answers.java.yml", ".ci/modules/java.yml"})

    def test_release_module_renders_area_files(self) -> None:
        self.assertEqual(self.rendered_files("release"), {
            ".copier-answers.release.yml", ".ci/modules/release.yml",
            *MODULE_FILES["release"],
        })
        dest = self.render("release", "release_mode=manifest", "toolkit_version=v99.0.0")
        answers = yaml.safe_load((dest / ".copier-answers.release.yml").read_text())
        self.assertEqual(
            {key: answers[key] for key in ("module", "release_mode", "toolkit_version")},
            {"module": "release", "release_mode": "manifest", "toolkit_version": "v99.0.0"},
        )
        self.assertNotIn("project_name", answers)
        workflow = (dest / ".github/workflows/release.yml").read_text()
        self.assertIn("mode: manifest", workflow)
        self.assertIn("release-please.yml@v99.0.0", workflow)

    def test_allure_and_renovate_modules_render_area_files(self) -> None:
        for module in ("allure", "renovate"):
            with self.subTest(module=module):
                self.assertEqual(self.rendered_files(module), {
                    f".copier-answers.{module}.yml", f".ci/modules/{module}.yml",
                    *MODULE_FILES[module],
                })

    def test_allure_module_rejects_auto_source(self) -> None:
        result, _ = self.copy("allure", "allure_source=auto", "toolkit_version=v99.0.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("'auto' is not in ['component', 'external']", result.stderr)

    def test_declarations_match_schemas(self) -> None:
        for module in ("core", "java", "security", "release", "allure", "renovate"):
            with self.subTest(module=module):
                jsonschema.validate(yaml.safe_load(self.decl(module)), self.load_schema(module, 1))

    def test_java_declaration_content(self) -> None:
        self.assertEqual(
            yaml.safe_load(self.decl("java")),
            {"schema_version": 1, "components": [DEFAULT_COMPONENT]},
        )

    def test_schemas_reject_unknown_fields(self) -> None:
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({"schema_version": 1, "extra": 1}, self.load_schema("core", 1))
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate({"schema_version": 2}, self.load_schema("core", 1))

    def test_caller_pins_toolkit_version(self) -> None:
        self.assertIn(
            "uses: quokkify/ci-kit/.github/workflows/validate.yml@v99.0.0",
            self.caller(toolkit_version="v99.0.0"),
        )

    def test_caller_keeps_workflow_name_validate(self) -> None:
        self.assertEqual(yaml.safe_load(self.caller(toolkit_version="v99.0.0"))["name"], "Validate")

    def test_caller_passes_actionlint(self) -> None:
        dest = self.render("core", "toolkit_version=v99.0.0")
        result = subprocess.run(["actionlint", ".github/workflows/validate.yml"], cwd=dest, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_java_component_rejects_bad_input(self) -> None:
        for bad in ({"path": "../x"}, {"path": "a/*"}, {"id": "gate"}, {"java_version": "8"}, {"bogus": 1}):
            with self.subTest(bad=bad):
                self.assertNotEqual(self.copy_java(bad).returncode, 0)

    def test_java_components_reject_duplicate_ids(self) -> None:
        result, _ = self.copy("java", f"java_components={json.dumps([DEFAULT_COMPONENT, DEFAULT_COMPONENT])}")
        self.assertNotEqual(result.returncode, 0)

    def test_python_renders_exact_file_set(self) -> None:
        self.assertEqual(self.rendered_files("python"), {".copier-answers.python.yml", ".ci/modules/python.yml"})

    def test_node_docker_renders_exact_file_set(self) -> None:
        self.assertEqual(self.rendered_files("node"), {".copier-answers.node.yml", ".ci/modules/node.yml"})
        self.assertEqual(self.rendered_files("docker"), {".copier-answers.docker.yml", ".ci/modules/docker.yml"})

    def render_scenario(self, name: str) -> Path:
        type(self).counter += 1
        dest = self.tmp / f"scenario-{self.counter}"
        dest.mkdir()
        for module, answers in yaml.safe_load((ROOT / f"tests/scenarios/modules/{name}.yml").read_text()).items():
            args = ["copier", "copy", "--trust", "--defaults", "--vcs-ref", "HEAD", "-a", f".copier-answers.{module}.yml", "-d", f"module={module}"]
            for key, value in answers.items():
                args += ["-d", f"{key}={json.dumps(value) if isinstance(value, (list, dict)) else value}"]
            subprocess.run([*args, str(self.repo), str(dest)], check=True, capture_output=True, text=True)
        return dest

    def test_polyglot_declarations_match_schemas(self) -> None:
        expected = {
            "core-python": {"core", "python"},
            "core-node-docker": {"core", "node", "docker"},
            "core-polyglot": {"core", "java", "python", "node"},
        }
        for name, modules in expected.items():
            with self.subTest(scenario=name):
                dest = self.render_scenario(name)
                declared = {p.stem for p in (dest / ".ci/modules").glob("*.yml")}
                self.assertEqual(declared, modules)
                for module in modules:
                    jsonschema.validate(yaml.safe_load((dest / f".ci/modules/{module}.yml").read_text()), self.load_schema(module, 1))
                self.assertEqual(build_plan(dest, drift=False).findings, [])

    def test_java_test_modules_render_and_plan_cleanly(self) -> None:
        plan = build_plan(self.render_scenario("core-java-modules"), drift=False)
        self.assertEqual(plan.findings, [])
        self.assertEqual([item["id"] for item in json.loads(plan.matrices["java"][0]["test_modules"])], ["core", "it"])

    def test_core_java_unchanged(self) -> None:
        text = self.decl("java")
        self.assertEqual(
            text,
            'schema_version: 1\ncomponents:\n  - id: "app-java"\n    name: "Application Java"\n    path: "."\n    java_version: "21"\n',
        )

    def test_java_extra_keys_render(self) -> None:
        component = {**DEFAULT_COMPONENT, "distribution": "temurin", "run_lint": False, "timeout_minutes": 30}
        result, dest = self.copy("java", f"java_components={json.dumps([component])}")
        self.assertEqual(result.returncode, 0, result.stderr)
        text = (dest / ".ci/modules/java.yml").read_text()
        self.assertIn('    "distribution": "temurin"\n', text)
        self.assertIn('    "run_lint": false\n', text)
        self.assertEqual(yaml.safe_load(text)["components"], [component])

    def test_python_component_renders_extra_keys(self) -> None:
        component = {"id": "svc", "name": "Svc", "path": "svc", "python_version": "3.12", "timeout_minutes": 20}
        result, dest = self.copy("python", f"python_components={json.dumps([component])}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(yaml.safe_load((dest / ".ci/modules/python.yml").read_text())["components"], [component])

    def test_python_component_rejects_numeric_version(self) -> None:
        component = {"id": "svc", "name": "Svc", "path": ".", "python_version": 3.12}
        result, _ = self.copy("python", f"python_components={json.dumps([component])}")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('python_version must be a non-empty string such as "24"', result.stdout + result.stderr)

    def test_component_versions_must_not_be_empty(self) -> None:
        # The module schema requires minLength 1; Copier must reject an empty version at copy time, not only ci-kit plan.
        cases = {"python": ("python_version", "pip_version", "poetry_version", "uv_version"), "node": ("node_version", "corepack_version")}
        for module, keys in cases.items():
            for key in keys:
                for value in ("", "  "):
                    with self.subTest(module=module, key=key, value=value):
                        component = {"id": "svc", "name": "Svc", "path": ".", key: value}
                        result, _ = self.copy(module, f"{module}_components={json.dumps([component])}")
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn(f'{key} must be a non-empty string such as "24"', result.stdout + result.stderr)

    def test_new_modules_reject_bad_input(self) -> None:
        base = {"python": {"id": "a", "name": "A", "path": "."}, "node": {"id": "a", "name": "A", "path": "."}, "docker": {"id": "a"}}
        bad_cases = {
            "python": ({"path": "../x"}, {"path": "a/*"}, {"id": "1x"}, {"name": ""}, {"bogus": 1}),
            "node": ({"path": ""}, {"node_version": 24}, {"bogus": 1}),
            "docker": ({"context": "../x"}, {"context": "a/*"}, {"bogus": 1}, {"id": "-x"}),
        }
        for module, cases in bad_cases.items():
            for bad in cases:
                with self.subTest(module=module, bad=bad):
                    key = "docker_images" if module == "docker" else f"{module}_components"
                    result, _ = self.copy(module, f"{key}={json.dumps([{**base[module], **bad}])}")
                    self.assertNotEqual(result.returncode, 0)
            with self.subTest(module=module, case="duplicate"):
                key = "docker_images" if module == "docker" else f"{module}_components"
                result, _ = self.copy(module, f"{key}={json.dumps([base[module], base[module]])}")
                self.assertNotEqual(result.returncode, 0)

    def test_components_reject_duplicate_names_within_a_module(self) -> None:
        cases = {
            "java": ("java_components", [{**DEFAULT_COMPONENT, "id": "a"}, {**DEFAULT_COMPONENT, "id": "b"}]),
            "python": ("python_components", [{"id": "a", "name": "Same", "path": "."}, {"id": "b", "name": "Same", "path": "."}]),
            "node": ("node_components", [{"id": "a", "name": "Same", "path": "."}, {"id": "b", "name": "Same", "path": "."}]),
            "docker": ("docker_images", [{"id": "a", "name": "Same"}, {"id": "b", "name": "Same"}]),
        }
        for module, (key, components) in cases.items():
            with self.subTest(module=module):
                result, _ = self.copy(module, f"{key}={json.dumps(components)}")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Component names must be unique", result.stdout + result.stderr)

    def test_validator_allowed_keys_match_schemas(self) -> None:
        config = yaml.safe_load((ROOT / "copier.yml").read_text())
        for module, question in (("java", "java_components"), ("python", "python_components"), ("node", "node_components"), ("docker", "docker_images")):
            with self.subTest(module=module):
                properties = set(self.load_schema(module, 1)["properties"]["components"]["items"]["properties"])
                self.assertEqual(properties, {"id", "name", "path", *module_fields(module)} - ({"path"} if module == "docker" else set()))
                match = re.search(r"\{% set allowed_keys = (\[.*?\]) %\}", config[question]["validator"])
                self.assertIsNotNone(match)
                self.assertEqual(set(ast.literal_eval(match.group(1))), properties)


PIN = "v99.0.0"
OPTIONS = {
    "gitleaks_job_name": "Scan current tree (legacy history pending baseline)",
    "gitleaks_scan": "tree",
    "gitleaks_aggregate": True,
    "codeql_aggregate": True,
}


def render_failure(data: dict) -> str:
    data_file = mkdtemp("options-") / "data.yml"
    data_file.write_text(yaml.safe_dump({"toolkit_version": PIN, **data}))
    try:
        render_tree(shared_template_repo(), data_file)
    except subprocess.CalledProcessError as error:
        return error.stdout + error.stderr
    raise AssertionError(f"copier accepted {data}")


class ModuleOptionsTests(unittest.TestCase):
    def test_gitleaks_options_render(self) -> None:
        template = shared_template_repo()
        default = yaml.safe_load(render_tree(template, None, {"module": "security", "toolkit_version": PIN})[".github/workflows/gitleaks.yml"])
        self.assertEqual(list(default["jobs"]), ["scan"])
        self.assertEqual(default["jobs"]["scan"]["name"], "gitleaks")
        data_file = mkdtemp("options-") / "data.yml"
        data_file.write_text(yaml.safe_dump({**OPTIONS, "module": "security", "toolkit_version": PIN}))
        jobs = yaml.safe_load(render_tree(template, data_file)[".github/workflows/gitleaks.yml"])["jobs"]
        self.assertEqual(jobs["scan"]["name"], OPTIONS["gitleaks_job_name"])
        scan = jobs["scan"]["steps"][-1]["run"]
        self.assertNotIn('gitleaks" git ', scan)
        self.assertEqual(
            scan.strip(),
            '"$RUNNER_TEMP/bin/gitleaks" dir --redact --no-banner --config "$RUNNER_TEMP/gitleaks.toml" '
            '--gitleaks-ignore-path "$RUNNER_TEMP/gitleaksignore" .',
        )
        self.assertEqual(jobs["complete"], {
            "name": "Gitleaks", "if": "always()", "needs": "scan", "runs-on": "ubuntu-latest", "timeout-minutes": 5,
            "steps": [{
                "name": "Require the secret scan to pass", "env": {"SCAN_RESULT": "${{ needs.scan.result }}"},
                "run": 'test "$SCAN_RESULT" = success',
            }],
        })

    def test_codeql_aggregate_renders(self) -> None:
        template = shared_template_repo()
        jobs = yaml.safe_load(render_tree(template, None, {
            "module": "security", "toolkit_version": PIN, "codeql_aggregate": True,
        })[".github/workflows/codeql.yml"])["jobs"]
        self.assertEqual(jobs["complete"], {
            "name": "CodeQL", "if": "always()", "needs": "codeql", "runs-on": "ubuntu-latest", "timeout-minutes": 5,
            "steps": [{
                "name": "Require every CodeQL analysis to pass",
                "env": {"ANALYZE_RESULT": "${{ needs.codeql.result }}"},
                "run": 'test "$ANALYZE_RESULT" = success',
            }],
        })
        default = render_tree(template, None, {"module": "security", "toolkit_version": PIN})
        self.assertEqual(list(yaml.safe_load(default[".github/workflows/codeql.yml"])["jobs"]), ["codeql"])

    def test_allure_group_by_renders(self) -> None:
        rendered = render_tree(shared_template_repo(), None, {
            "module": "allure", "toolkit_version": PIN, "allure_group_by": '["feature", "story"]',
        })[".github/allure/allurerc.mjs"].decode()
        self.assertIn('groupBy: ["feature", "story"],', rendered)

    def test_option_validators(self) -> None:
        security = {"module": "security"}
        allure = {"module": "allure"}
        cases = (
            ({**security, "gitleaks_job_name": ""}, "gitleaks_job_name"),
            ({**security, "gitleaks_job_name": "a: b"}, "gitleaks_job_name"),
            ({**security, "gitleaks_scan": "full"}, "full"),
            ({**allure, "allure_group_by": "feature"}, "allure_group_by"),
            ({**allure, "allure_group_by": ["feature", ""]}, "allure_group_by"),
            ({**allure, "allure_group_by": ["feature", 3]}, "allure_group_by"),
            ({**allure, "allure_group_by": []}, "allure_group_by"),
            *(({**security, "gitleaks_job_name": name}, "gitleaks_job_name")
              for name in ("true", "No", "OFF", "null", "y", "123", "1.5", "1e3", "0x1F", "0o17", "2024-01-01")),
        )
        for data, message in cases:
            with self.subTest(data=data):
                self.assertIn(message, render_failure(data))

    def test_copier_update_drops_or_refuses_legacy_answers_instead_of_updating_them(self) -> None:
        core = render_tree(shared_template_repo(), None, {"module": "core", "toolkit_version": PIN})
        workflow = core[".github/workflows/copier-update.yml"].decode()
        guard = workflow.index("if [[ -f .copier-answers.yml ]]; then")
        self.assertLess(guard, workflow.index("git rm -q .copier-answers.yml"))
        self.assertLess(workflow.index("git rm -q .copier-answers.yml"), workflow.index('ci-kit update --repo . --to "$ref"\n'))
        self.assertNotIn("copier update", workflow)
        self.assertNotIn("update_args", workflow)
        self.assertIn("FETCH_HEAD:modules/release/template/.github/scripts/enrich_release_notes.py.jinja", workflow)


if __name__ == "__main__":
    unittest.main()

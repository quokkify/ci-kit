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

from module_test_support import ROOT, make_template_repo
from ci_kit.plan import build_plan
from ci_kit.inputs import load_inputs, module_fields

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
            {".copier-answers.core.yml", ".ci/modules/core.yml", ".github/workflows/validate.yml"},
        )

    def test_java_renders_exact_file_set(self) -> None:
        self.assertEqual(self.rendered_files("java"), {".copier-answers.java.yml", ".ci/modules/java.yml"})

    def test_declarations_match_schemas(self) -> None:
        for module in ("core", "java"):
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
        self.assertIn('python_version must be a string such as "24"', result.stdout + result.stderr)

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


if __name__ == "__main__":
    unittest.main()

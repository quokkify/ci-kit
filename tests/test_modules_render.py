from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import jsonschema
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from module_test_support import ROOT, make_template_repo

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

    def test_caller_passes_actionlint(self) -> None:
        dest = self.render("core", "toolkit_version=v99.0.0")
        result = subprocess.run(["actionlint", ".github/workflows/validate.yml"], cwd=dest, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_java_component_rejects_bad_input(self) -> None:
        for bad in ({"path": "../x"}, {"path": "a/*"}, {"id": "gate"}, {"java_version": "8"}):
            with self.subTest(bad=bad):
                self.assertNotEqual(self.copy_java(bad).returncode, 0)

    def test_java_components_reject_duplicate_ids(self) -> None:
        result, _ = self.copy("java", f"java_components={json.dumps([DEFAULT_COMPONENT, DEFAULT_COMPONENT])}")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()

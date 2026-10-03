from __future__ import annotations

import importlib.util
import ast
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest import TestCase, main

import yaml

ROOT = Path(__file__).resolve().parents[1]


class SuiteRunnerTests(TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "tests").mkdir()
        (self.root / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts/run_test_suites.py", self.root / "scripts/run_test_suites.py")

    def run_runner(self, nested: bool = False) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.pop("PROJECT_TOOLKIT_NESTED_VALIDATION", None)
        if nested:
            env["PROJECT_TOOLKIT_NESTED_VALIDATION"] = "1"
        return subprocess.run(
            [sys.executable, "scripts/run_test_suites.py"], cwd=self.root,
            env=env, text=True, capture_output=True, timeout=20,
        )

    def test_new_suites_run_in_order_and_children_cannot_recurse(self) -> None:
        for name in ("test_z_future.py", "test_a_future.py"):
            (self.root / "tests" / name).write_text(
                "import os, subprocess, sys, unittest\n"
                "class FutureSuite(unittest.TestCase):\n"
                "    def test_without_main(self):\n"
                "        assert os.environ['PROJECT_TOOLKIT_NESTED_VALIDATION'] == '1'\n"
                "        assert subprocess.run([sys.executable, 'scripts/run_test_suites.py']).returncode == 0\n"
                f"        print('ran {name}')\n"
            )
        (self.root / "tests/helper.py").write_text("raise RuntimeError('not a suite')\n")
        result = self.run_runner()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(result.stdout.index("ran test_a_future.py"), result.stdout.index("ran test_z_future.py"))
        self.assertEqual(result.stdout.count("nested validation:"), 2)

    def test_failure_does_not_skip_later_suites(self) -> None:
        (self.root / "tests/test_a_failure.py").write_text("raise SystemExit(7)\n")
        (self.root / "tests/test_z_success.py").write_text(
            "import unittest\nclass Last(unittest.TestCase):\n"
            "    def test_success(self):\n        print('last suite ran')\n"
        )
        result = self.run_runner()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("test_a_failure.py", result.stderr)
        self.assertIn("last suite ran", result.stdout)

    def test_empty_suite_set_fails(self) -> None:
        self.assertNotEqual(self.run_runner().returncode, 0)

    def test_file_with_no_test_cases_fails(self) -> None:
        (self.root / "tests/test_empty.py").write_text("# no test cases\n")
        result = self.run_runner()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no tests discovered", result.stderr)

    def test_suite_symlinks_are_rejected(self) -> None:
        outside = self.root / "outside.py"
        outside.write_text("raise SystemExit(0)\n")
        (self.root / "tests/test_link.py").symlink_to(outside)
        result = self.run_runner()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlinks are not allowed", result.stderr)

    def test_nested_validation_skips_even_failing_suites(self) -> None:
        (self.root / "tests/test_failure.py").write_text("raise SystemExit(7)\n")
        result = self.run_runner(nested=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("nested validation:", result.stdout)


class RenderedWorkflowEvidenceTests(TestCase):
    def setUp(self) -> None:
        spec = importlib.util.spec_from_file_location("render_codeql_workflows", ROOT / "scripts/render_codeql_workflows.py")
        assert spec and spec.loader
        self.renderer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.renderer)
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copy2(ROOT / "copier.yml", self.root / "copier.yml")
        shutil.copytree(ROOT / "template", self.root / "template")
        (self.root / "tests/scenarios").mkdir(parents=True)
        shutil.copy2(ROOT / "tests/scenarios/python.yml", self.root / "tests/scenarios/python.yml")
        (self.root / ".github/workflows").mkdir(parents=True)

    def test_current_worktree_rendered_without_overwriting_native_workflows(self) -> None:
        # The Python scenario always renders Validate; optional workflows may
        # be excluded, and directory iteration order differs across hosts.
        template = self.root / "template/.github/workflows/validate.yml.jinja"
        template.write_text("# current worktree sentinel\n" + template.read_text())
        native = self.root / ".github/workflows/codeql.yml"
        native.write_text("native workflow sentinel\n")
        paths = self.renderer.render_workflows(self.root)
        self.assertTrue(paths)
        self.assertTrue(any(b"current worktree sentinel" in path.read_bytes() for path in paths))
        self.assertEqual(native.read_text(), "native workflow sentinel\n")
        self.assertTrue(all(path.name.startswith("codeql-generated-python-") for path in paths))
        with self.assertRaises(FileExistsError):
            self.renderer.render_workflows(self.root)

    def test_empty_scenario_set_fails(self) -> None:
        (self.root / "tests/scenarios/python.yml").unlink()
        with self.assertRaisesRegex(ValueError, "no template scenarios"):
            self.renderer.render_workflows(self.root)


class CodeQLCoveragePolicyTests(TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for directory in (".github/workflows", ".github/codeql"):
            (self.root / directory).mkdir(parents=True)
        self.workflow = self.root / ".github/workflows/codeql.yml"
        self.config = self.root / ".github/codeql/codeql-config.yml"
        shutil.copy2(ROOT / ".github/workflows/codeql.yml", self.workflow)
        shutil.copy2(ROOT / ".github/codeql/codeql-config.yml", self.config)
        # Extract this pure policy function without importing the executable
        # validator, which would launch all template probes and test suites.
        tree = ast.parse((ROOT / "scripts/validate.py").read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "codeql_runner_workflow_errors")
        namespace = {"ROOT": self.root, "Path": Path, "yaml": yaml}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "validate.py", "exec"), namespace)
        self.errors = namespace[function.name]

    def test_current_contract_passes(self) -> None:
        self.assertEqual(self.errors(self.workflow), [])

    def test_missing_actions_language_or_render_evidence_is_rejected(self) -> None:
        original = self.workflow.read_text()
        for fragment in ("          - language: actions\n            build-mode: none\n", "        run: python scripts/render_codeql_workflows.py\n"):
            with self.subTest(fragment=fragment):
                self.workflow.write_text(original.replace(fragment, ""))
                self.assertTrue(self.errors(self.workflow))

    def test_narrowing_paths_is_rejected(self) -> None:
        self.config.write_text("paths: [scripts, tests]\n")
        self.assertTrue(self.errors(self.workflow))


if __name__ == "__main__":
    main()

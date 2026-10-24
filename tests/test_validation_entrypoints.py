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
from unittest.mock import patch

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

    def run_runner(self, *arguments: str, nested: bool = False) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env.pop("PROJECT_TOOLKIT_NESTED_VALIDATION", None)
        if nested:
            env["PROJECT_TOOLKIT_NESTED_VALIDATION"] = "1"
        return subprocess.run(
            [sys.executable, "scripts/run_test_suites.py", *arguments], cwd=self.root,
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

    def test_failed_subprocess_output_is_reported(self) -> None:
        # Regression guard for #470: CalledProcessError hid copier's stderr, so a flaky copy failed blind.
        (self.root / "tests/test_child.py").write_text(
            "import subprocess, sys, unittest\n"
            "def child(tag):\n"
            "    code = f\"import sys; print('{tag}' + ' out'); sys.exit('{tag}' + ' err')\"\n"
            "    subprocess.run([sys.executable, '-c', code], check=True, capture_output=True, text=True)\n"
            "class Child(unittest.TestCase):\n"
            "    def test_direct(self):\n        child('direct')\n"
            "    def test_subtest(self):\n"
            "        with self.subTest(case='one'):\n            child('subtest')\n"
        )
        result = self.run_runner()
        self.assertNotEqual(result.returncode, 0)
        for tag in ("direct", "subtest"):
            self.assertIn(f"{tag} out", result.stderr)
            self.assertIn(f"{tag} err", result.stderr)

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

    def write_passing_suites(self, *names: str) -> None:
        for name in names:
            (self.root / "tests" / name).write_text(
                "import unittest\nclass Dummy(unittest.TestCase):\n    def test_ok(self):\n        pass\n"
            )

    def shard_layout(self, count: int) -> list[list[str]]:
        result = self.run_runner("--list-shards", str(count))
        self.assertEqual(result.returncode, 0, result.stderr)
        return [line.split(": ", 1)[1].split() for line in result.stdout.splitlines()]

    def test_shards_are_disjoint_complete_and_deterministic(self) -> None:
        names = [f"test_s{index}.py" for index in range(7)]
        self.write_passing_suites(*names)
        layout = self.shard_layout(3)
        self.assertEqual(sorted(name for shard in layout for name in shard), names)
        self.assertEqual(layout, self.shard_layout(3))
        for index in range(1, 4):
            result = self.run_runner("--shard", f"{index}/3")
            self.assertEqual(result.returncode, 0, result.stderr)
            for name in layout[index - 1]:
                self.assertIn(f"tests/{name}", result.stdout)
            self.assertEqual(result.stdout.count("+ unittest suite"), len(layout[index - 1]))

    def test_invalid_shard_arguments_fail(self) -> None:
        self.write_passing_suites("test_a.py", "test_b.py")
        for value in ("0/3", "4/3", "abc", "1/3", "1/0"):
            with self.subTest(value=value):
                self.assertNotEqual(self.run_runner("--shard", value).returncode, 0)
        self.assertNotEqual(self.run_runner("--list-shards", "3").returncode, 0)

    def test_nested_validation_fails_with_shard(self) -> None:
        (self.root / "tests/test_failure.py").write_text("raise SystemExit(7)\n")
        result = self.run_runner("--shard", "1/1", nested=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("silently skip", result.stderr)
        self.assertNotIn("nested validation:", result.stdout)

    def test_empty_shard_fails(self) -> None:
        self.write_passing_suites("test_a.py")
        module = importlib.util.spec_from_file_location("run_test_suites", self.root / "scripts/run_test_suites.py")
        runner = importlib.util.module_from_spec(module)
        module.loader.exec_module(runner)
        with patch.dict(os.environ), patch.object(runner, "shard_suites", return_value=[[], []]):
            os.environ.pop("PROJECT_TOOLKIT_NESTED_VALIDATION", None)
            self.assertEqual(runner.run_suites(self.root, (1, 2)), 1)

    def test_heaviest_weighted_suites_land_in_different_shards(self) -> None:
        self.write_passing_suites("test_ci_kit_drift.py", "test_migration_lifecycle.py", "test_unweighted.py")
        layout = self.shard_layout(2)
        self.assertNotEqual(
            [index for index, shard in enumerate(layout) if "test_ci_kit_drift.py" in shard],
            [index for index, shard in enumerate(layout) if "test_migration_lifecycle.py" in shard],
        )

    def test_unweighted_suite_appears_exactly_once(self) -> None:
        self.write_passing_suites("test_ci_kit_drift.py", "test_ci_kit_update.py", "test_brand_new.py")
        layout = self.shard_layout(2)
        self.assertEqual(sum(shard.count("test_brand_new.py") for shard in layout), 1)


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
        shutil.copytree(ROOT / "modules", self.root / "modules")
        (self.root / "tests/scenarios/modules").mkdir(parents=True)
        shutil.copy2(ROOT / "tests/scenarios/modules/core-python.yml", self.root / "tests/scenarios/modules/core-python.yml")
        (self.root / ".github/workflows").mkdir(parents=True)

    def test_current_worktree_rendered_without_overwriting_native_workflows(self) -> None:
        # core always renders the update workflow; directory iteration order differs across hosts.
        template = self.root / "modules/core/template/.github/workflows/copier-update.yml.jinja"
        template.write_text("# current worktree sentinel\n" + template.read_text())
        native = self.root / ".github/workflows/codeql.yml"
        native.write_text("native workflow sentinel\n")
        paths = self.renderer.render_workflows(self.root)
        self.assertTrue(paths)
        self.assertTrue(any(b"current worktree sentinel" in path.read_bytes() for path in paths))
        self.assertEqual(native.read_text(), "native workflow sentinel\n")
        self.assertTrue(all(path.name.startswith("codeql-generated-core-python-") for path in paths))
        with self.assertRaises(FileExistsError):
            self.renderer.render_workflows(self.root)

    def test_empty_scenario_set_fails(self) -> None:
        (self.root / "tests/scenarios/modules/core-python.yml").unlink()
        with self.assertRaisesRegex(ValueError, "no module scenarios"):
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

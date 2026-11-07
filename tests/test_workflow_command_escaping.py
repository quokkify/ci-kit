"""Regression guards: action output must not let untrusted text start workflow commands.

Kept separate from test_composite_actions.py so these guards do not collide with
other changes appending tests there; harness helpers are reused from that module.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_composite_actions as composite  # noqa: E402

action = composite.action
GRADLE_RETRY_SCRIPT = composite.ROOT / "actions/gradle-retry/gradle-retry.sh"


class JUnitStepSummaryCommandEscapingTests(unittest.TestCase):
    script = composite.JUnitStepSummaryTests.script
    run_summary = composite.JUnitStepSummaryTests.run_summary

    def test_untrusted_paths_cannot_inject_workflow_commands(self) -> None:
        # Regression guard: error lines embedded attacker-created file names unescaped.
        with tempfile.TemporaryDirectory(prefix="junit-summary-command-") as tmp:
            workspace = Path(tmp)
            hostile = workspace / "results" / "evil\n::error::pwned 100%"
            hostile.mkdir(parents=True)
            (hostile / "junit.xml").write_text("<testsuite")
            result, _, _ = self.run_summary(workspace)
            self.assertEqual(result.returncode, 2)
            output = result.stdout + result.stderr
            self.assertNotIn("\n::", "\n" + output)
            self.assertIn("evil%0A::error::pwned 100%25", result.stderr)


class SetupActionCommandEscapingTests(unittest.TestCase):
    def test_validation_errors_cannot_inject_workflow_commands(self) -> None:
        # Regression guard: invalid input values were echoed without workflow-command escaping.
        hostile = "x\n::error::pwned 100%"
        cases = {
            "setup-python": {"CACHE_DEPENDENCIES": "true", "INSTALL_DEPENDENCIES": hostile, "PACKAGE_MANAGER": "auto", "POETRY_VERSION": "2.1.4", "UV_VERSION": "0.8.15", "PIP_VERSION": "25.0"},
            "setup-node": {"CACHE_DEPENDENCIES": "true", "INSTALL_DEPENDENCIES": "true", "PACKAGE_MANAGER": hostile},
            "setup-java-gradle": {"CACHE_DEPENDENCIES": "true", "CACHE_JDK": hostile, "VALIDATE_WRAPPERS": "true"},
        }
        for name, values in cases.items():
            with self.subTest(name=name):
                first = action(name)["runs"]["steps"][0]
                result = subprocess.run(["bash", "-c", first["run"]], env={**os.environ, **values}, text=True, capture_output=True, check=False)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertNotIn("\n::", "\n" + result.stdout + result.stderr)
                self.assertIn("x%0A::error::pwned 100%25", result.stderr)

    def test_multiline_tool_versions_are_not_written_to_outputs(self) -> None:
        # Regression guard: a tool-reported version could append extra GITHUB_OUTPUT lines.
        injected = "1.2.3\nhas-injected=true"
        with tempfile.TemporaryDirectory(prefix="node-version-") as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            npm = bin_dir / "npm"
            npm.write_text(f"#!/bin/bash\nprintf '{injected}\\n'\n")
            npm.chmod(0o755)
            prepare = next(step for step in action("setup-node")["runs"]["steps"] if step.get("id") == "manager")
            env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin", "PACKAGE_MANAGER": "npm", "COREPACK_VERSION": "0.34.0",
                   "INSTALL_COMMAND": "", "WORKING_DIRECTORY": str(root), "RUNNER_TEMP": str(root), "CACHE_DEPENDENCIES": "false",
                   "GITHUB_OUTPUT": str(root / "output"), "GITHUB_PATH": str(root / "path")}
            result = subprocess.run(["bash", "-c", prepare["run"]], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertNotIn("has-injected", (root / "output").read_text() if (root / "output").exists() else "")
        with tempfile.TemporaryDirectory(prefix="python-version-") as tmp:
            root = Path(tmp)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            python = bin_dir / "python"
            python.write_text("#!/bin/bash\nexit 0\n")
            python.chmod(0o755)
            prepare = next(step for step in action("setup-python")["runs"]["steps"] if step.get("id") == "manager")
            env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin", "PACKAGE_MANAGER": "uv", "INSTALL_COMMAND": "",
                   "UV_VERSION": injected, "POETRY_VERSION": "2.1.4", "PIP_VERSION": "25.0",
                   "WORKING_DIRECTORY": str(root), "CACHE_DEPENDENCIES": "false", "GITHUB_OUTPUT": str(root / "output")}
            result = subprocess.run(["bash", "-c", prepare["run"]], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertNotIn("has-injected", (root / "output").read_text() if (root / "output").exists() else "")


class ComposeActionCommandEscapingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = action("compose-up")
        cls.steps = {step["name"]: step for step in cls.data["runs"]["steps"]}

    run_validation = composite.ComposeActionTests.run_validation

    def test_validation_errors_cannot_inject_workflow_commands(self) -> None:
        # Regression guard: invalid input values were echoed without workflow-command escaping.
        result, _ = self.run_validation(DOWN_ON_TIMEOUT="x\n::error::pwned 100%")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("\n::", "\n" + result.stdout + result.stderr)
        self.assertIn("x%0A::error::pwned 100%25", result.stderr)


class GradleRetryCommandEscapingTests(unittest.TestCase):
    def test_invalid_inputs_cannot_inject_workflow_commands(self) -> None:
        # Regression guard: invalid input values were echoed without workflow-command escaping.
        for name in ("GRADLE_RETRY_MAX_ATTEMPTS", "GRADLE_RETRY_INITIAL_DELAY_SECONDS"):
            with self.subTest(name=name):
                env = {**os.environ, "GRADLE_RETRY_COMMAND": "true", name: "x\n::error::pwned 100%"}
                result = subprocess.run(["bash", str(GRADLE_RETRY_SCRIPT)], env=env, text=True, capture_output=True, check=False)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("\n::", "\n" + result.stdout + result.stderr)
                self.assertIn("x%0A::error::pwned 100%25", result.stderr)


if __name__ == "__main__":
    unittest.main()

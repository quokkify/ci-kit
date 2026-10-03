from __future__ import annotations

import importlib.util
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, main

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "validate_helpers", ROOT / "scripts/validate_helpers.py"
)
assert SPEC and SPEC.loader
helpers = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = helpers
SPEC.loader.exec_module(helpers)


class LoadYamlOrErrorTests(TestCase):
    def test_missing_yaml_records_validation_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            errors: list[str] = []
            result = helpers.load_yaml_or_error(Path(temporary) / "copier.yml", errors, "copier.yml")
        self.assertIsNone(result)
        self.assertEqual(len(errors), 1)
        self.assertIn("copier.yml: YAML parse failed:", errors[0])

    def test_malformed_yaml_records_validation_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "copier.yml"
            path.write_text("foo: [bar\n", encoding="utf-8")
            errors: list[str] = []
            result = helpers.load_yaml_or_error(path, errors, "copier.yml")
        self.assertIsNone(result)
        self.assertEqual(len(errors), 1)
        self.assertIn("copier.yml: YAML parse failed:", errors[0])

    def test_non_utf8_yaml_records_validation_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "copier.yml"
            path.write_bytes(b"\xff\xfe\xfd")
            errors: list[str] = []
            result = helpers.load_yaml_or_error(path, errors, "copier.yml")
        self.assertIsNone(result)
        self.assertEqual(len(errors), 1)
        self.assertIn("copier.yml: YAML parse failed:", errors[0])


class ActionReferenceTests(TestCase):
    def test_examples_accept_release_updates_without_expected_version_constants(self) -> None:
        for version, digest in (("v2.6.0", "a" * 40), ("v3.17.42", "b" * 40)):
            for prefix in ("uses:", "- uses:"):
                with self.subTest(version=version, prefix=prefix):
                    text = f"  {prefix} quokkify/ci-kit/actions/setup-java-gradle@{digest} # {version}\n"
                    self.assertEqual(helpers.action_reference_errors(text, "example", require_toolkit_pin=True), [])

    def test_examples_reject_mutable_refs_short_digests_and_untracked_pins(self) -> None:
        for reference in ("main", "v2", "v2.6.0", "abc123 # v2.6.0", "a" * 40,
                          "a" * 40 + " # main", "a" * 40 + " # v2.6"):
            with self.subTest(reference=reference):
                text = f"- uses: quokkify/ci-kit/actions/setup-node@{reference}\n"
                self.assertTrue(helpers.action_reference_errors(text, "example", require_toolkit_pin=True))

    def test_legacy_consumers_and_local_actions_remain_supported(self) -> None:
        text = "uses: quokkify/ci-kit/.github/workflows/node-ci.yml@v2.6.0\n- uses: ./actions/setup-node\n"
        self.assertEqual(helpers.action_reference_errors(text, "consumer"), [])
        self.assertTrue(helpers.action_reference_errors("- uses: actions/checkout@v7\n", "consumer"))
        self.assertTrue(helpers.action_reference_errors("- uses: actions/checkout\n", "consumer"))

    def test_renovate_native_manager_covers_every_example_pin(self) -> None:
        config = json.loads((ROOT / ".github/renovate.json").read_text())
        patterns = config["github-actions"]["managerFilePatterns"]
        for path in sorted((ROOT / "examples").glob("*.yml")):
            relative = path.relative_to(ROOT).as_posix()
            self.assertTrue(any(re.search(pattern[1:-1], relative) for pattern in patterns), relative)
            self.assertEqual(helpers.action_reference_errors(path.read_text(), relative, require_toolkit_pin=True), [])
        self.assertTrue(any(rule.get("pinDigests") and "examples/**" in rule.get("matchFileNames", [])
                            for rule in config["packageRules"]))

    def test_future_dependency_bumps_preserve_behavior_tests(self) -> None:
        """Changing dependency digests/tags must not require changing behavior assertions."""
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary) / "checkout"
            shutil.copytree(ROOT, checkout, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            for relative in ("actions/allure-report/action.yml", "actions/compose-up/action.yml",
                             ".github/workflows/allure-publisher-core.yml"):
                path = checkout / relative
                text, count = re.subn(r"@[0-9a-f]{40} # v\d+\.\d+\.\d+", "@" + "b" * 40 + " # v99.12.34", path.read_text())
                self.assertGreater(count, 0, relative)
                path.write_text(text)
            result = subprocess.run(
                [sys.executable, "tests/test_composite_actions.py",
                 "ComposeActionTests.test_static_wrapper_is_pinned_and_has_one_standalone_call",
                 "AllureReportActionTests.test_wrapper_uses_sidecar_metadata_release_and_has_no_vendor_copy",
                 "AllureReportActionTests.test_renovate_manages_the_executable_release_pin",
                 "AllureTrustedCommentPropagationTests.test_generated_workflow_forwards_compact_comment_artifact_unchanged"],
                cwd=checkout, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class ValidatorIntegrationTests(TestCase):
    def run_validator(self, copier_content: bytes | None) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary) / "checkout"
            shutil.copytree(ROOT, checkout, ignore=shutil.ignore_patterns(".git", "__pycache__"))
            copier = checkout / "copier.yml"
            if copier_content is None:
                copier.unlink()
            else:
                copier.write_bytes(copier_content)
            return subprocess.run(
                [sys.executable, "scripts/validate.py", "--static"],
                cwd=checkout,
                env={**os.environ, "PYTHONPATH": str(checkout / "scripts")},
                text=True,
                capture_output=True,
                check=False,
            )

    def assert_yaml_error(self, content: bytes | None) -> None:
        result = self.run_validator(content)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        combined = result.stdout + result.stderr
        self.assertIn("copier.yml: YAML parse failed:", combined)
        self.assertNotIn("Traceback (most recent call last)", combined)

    def test_static_validator_reports_missing_copier_yaml(self) -> None:
        self.assert_yaml_error(None)

    def test_static_validator_reports_malformed_copier_yaml(self) -> None:
        self.assert_yaml_error(b"_min_copier_version: [broken\n")

    def test_static_validator_reports_non_utf8_copier_yaml(self) -> None:
        self.assert_yaml_error(b"\xff\xfe\xfd")


if __name__ == "__main__":
    main()

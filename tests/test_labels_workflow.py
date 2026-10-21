from __future__ import annotations

import os
import re
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
REUSABLE = ROOT / ".github/workflows/labels-sync.yml"
CALLER = ROOT / "template/.github/workflows/labels.yml.jinja"
MANIFEST_URL = "https://raw.githubusercontent.com/quokkify/.github/main/labels.yml"


def load(text: str) -> dict:
    workflow = yaml.safe_load(text)
    workflow["on"] = workflow.pop(True)
    return workflow


def render_caller(toolkit_version: str = "v9.8.7") -> str:
    return Environment(undefined=StrictUndefined, keep_trailing_newline=True).from_string(
        CALLER.read_text(encoding="utf-8")
    ).render(toolkit_version=toolkit_version)


class ReusableLabelSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = load(REUSABLE.read_text(encoding="utf-8"))
        self.steps = self.workflow["jobs"]["sync"]["steps"]

    def select_step(self) -> dict:
        return next(step for step in self.steps if step.get("id") == "config")

    def select(self, workspace: Path, manifest_url: str = MANIFEST_URL) -> tuple[subprocess.CompletedProcess[str], str, str]:
        output = workspace / "output"
        summary = workspace / "summary"
        output.touch()
        summary.touch()
        step = self.select_step()
        env = {
            **os.environ,
            "MANIFEST_URL": manifest_url,
            "PROJECT_LABELS": step["env"]["PROJECT_LABELS"],
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
        }
        completed = subprocess.run(
            ["bash", "-c", textwrap.dedent(step["run"])], cwd=workspace, env=env, text=True, capture_output=True
        )
        return completed, output.read_text(encoding="utf-8"), summary.read_text(encoding="utf-8")

    def test_permissions_are_limited_to_label_management(self) -> None:
        self.assertEqual(self.workflow["permissions"], {"contents": "read", "issues": "write"})

    def test_phase_one_never_deletes_unlisted_labels_by_default(self) -> None:
        inputs = self.workflow["on"]["workflow_call"]["inputs"]
        self.assertIs(inputs["delete-other-labels"]["default"], False)
        self.assertIs(inputs["dry-run"]["default"], False)
        self.assertEqual(inputs["manifest-url"]["default"], MANIFEST_URL)

    def test_label_sync_is_pinned_to_a_release_commit(self) -> None:
        text = REUSABLE.read_text(encoding="utf-8")
        self.assertRegex(text, r"uses: EndBug/label-sync@[0-9a-f]{40} # v\d+\.\d+\.\d+\n")
        sync = self.steps[-1]
        self.assertEqual(sync["with"]["config-file"], "${{ steps.config.outputs.files }}")
        self.assertEqual(sync["with"]["delete-other-labels"], "${{ inputs.delete-other-labels }}")

    def test_checkout_reads_only_the_project_label_file_without_credentials(self) -> None:
        checkout = self.steps[0]["with"]
        self.assertEqual(checkout["sparse-checkout"], self.select_step()["env"]["PROJECT_LABELS"])
        self.assertIs(checkout["sparse-checkout-cone-mode"], False)
        self.assertIs(checkout["persist-credentials"], False)

    def test_missing_project_file_syncs_the_manifest_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            completed, output, summary = self.select(Path(temporary))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(output, f"files<<EOF\n{MANIFEST_URL}\nEOF\n")
        self.assertIn("organization manifest only", summary)

    def test_project_file_is_synced_after_the_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            (workspace / ".github").mkdir()
            (workspace / ".github/labels.yml").write_text("- name: local\n  color: '000000'\n", encoding="utf-8")
            completed, output, summary = self.select(workspace)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(output, f"files<<EOF\n{MANIFEST_URL}\n.github/labels.yml\nEOF\n")
        self.assertEqual(summary, "")

    def test_manifest_url_must_be_an_https_yaml_file(self) -> None:
        for url in ("http://example.com/labels.yml", "https://example.com/labels.json", "https://example.com/a b.yml", ""):
            with self.subTest(url=url), tempfile.TemporaryDirectory() as temporary:
                completed, output, _ = self.select(Path(temporary), url)
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(output, "")


class LabelsCallerTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = load(render_caller())

    def test_triggers_cover_label_edits_dispatch_and_a_weekly_refresh(self) -> None:
        triggers = self.workflow["on"]
        self.assertEqual(triggers["push"], {"branches": ["main"], "paths": [".github/labels.yml", ".github/workflows/labels.yml"]})
        self.assertEqual(len(triggers["schedule"]), 1)
        self.assertRegex(triggers["schedule"][0]["cron"], r"^\d+ \d+ \* \* [0-6]$")
        self.assertIs(triggers["workflow_dispatch"]["inputs"]["dry-run"]["default"], False)
        self.assertNotIn("pull_request", triggers)
        self.assertNotIn("pull_request_target", triggers)

    def test_caller_grants_exactly_what_the_reusable_workflow_needs(self) -> None:
        reusable = load(REUSABLE.read_text(encoding="utf-8"))
        self.assertEqual(self.workflow["permissions"], reusable["permissions"])
        self.assertIs(self.workflow["concurrency"]["cancel-in-progress"], False)

    def test_caller_uses_the_selected_toolkit_release_and_keeps_deletion_off(self) -> None:
        job = self.workflow["jobs"]["sync"]
        self.assertEqual(job["uses"], "quokkify/ci-kit/.github/workflows/labels-sync.yml@v9.8.7")
        self.assertEqual(job["with"], {"dry-run": "${{ inputs.dry-run == true }}"})

    def test_rendered_caller_has_no_unrendered_jinja(self) -> None:
        text = render_caller()
        self.assertIsNone(re.search(r"{%|{{ *toolkit_version", text))
        self.assertTrue(text.endswith("}}\n") and not text.endswith("\n\n"))


if __name__ == "__main__":
    unittest.main()

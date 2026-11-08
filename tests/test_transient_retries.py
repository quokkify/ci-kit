"""Regression guards: network steps that hit GitHub must retry transient HTTP failures.

GitHub release downloads returned 504 and the REST API returned 503 in consumer CI
runs; un-retried curl and github-script calls turned those blips into red jobs.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_GLOBS = (".github/workflows/*.yml", ".github/workflows/*.yaml", "actions/*/action.yml")
CURL_SOURCES = sorted(
    {path for pattern in (*WORKFLOW_GLOBS, "modules/*/template/**/*.jinja") for path in ROOT.glob(pattern)}
)
LINE_CONTINUATION = re.compile(r"\\\n\s*")
RELEASE_DOWNLOAD_CURL = re.compile(r"^[^\n]*\bcurl\b[^\n]*releases/download/[^\n]*$", re.MULTILINE)
RETRY_COUNT = re.compile(r"--retry\s+(\d+)\b")
REQUIRED_CURL_FLAGS = ("--retry-all-errors", "--retry-max-time ")


def github_script_steps(path: Path) -> list[tuple[str, dict]]:
    data = yaml.safe_load(path.read_text()) or {}
    step_groups = [(job_name, job.get("steps") or []) for job_name, job in (data.get("jobs") or {}).items()]
    step_groups.append(("runs", (data.get("runs") or {}).get("steps") or []))
    return [
        (f"{path.relative_to(ROOT)}:{group}:{step.get('name', '<unnamed>')}", step)
        for group, steps in step_groups
        for step in steps
        if str(step.get("uses", "")).startswith("actions/github-script@")
    ]


class ReleaseDownloadRetryTests(unittest.TestCase):
    def test_every_release_download_retries_transient_failures(self) -> None:
        commands = [
            (path.relative_to(ROOT), match.group(0).strip())
            for path in CURL_SOURCES
            for match in RELEASE_DOWNLOAD_CURL.finditer(LINE_CONTINUATION.sub(" ", path.read_text()))
        ]
        self.assertTrue(commands, "expected at least one GitHub release download")
        for source, command in commands:
            with self.subTest(source=str(source), command=command):
                retry_count = RETRY_COUNT.search(command)
                self.assertIsNotNone(retry_count, "curl release download must pass --retry N")
                self.assertGreater(int(retry_count.group(1)), 0)
                for flag in REQUIRED_CURL_FLAGS:
                    self.assertIn(flag, command)

    def test_continued_curl_line_is_detected(self) -> None:
        sample = 'curl -fsSLo out \\\n    "https://github.com/o/r/releases/download/v1/x.tar.gz"\n'
        self.assertEqual(len(RELEASE_DOWNLOAD_CURL.findall(LINE_CONTINUATION.sub(" ", sample))), 1)


class GithubScriptRetryTests(unittest.TestCase):
    def test_every_github_script_step_retries(self) -> None:
        steps = [
            item
            for pattern in WORKFLOW_GLOBS
            for path in ROOT.glob(pattern)
            for item in github_script_steps(path)
        ]
        self.assertTrue(steps, "expected at least one actions/github-script step")
        for label, step in steps:
            with self.subTest(step=label):
                retries = (step.get("with") or {}).get("retries")
                self.assertIsInstance(retries, int)
                self.assertGreater(retries, 0)


if __name__ == "__main__":
    unittest.main()

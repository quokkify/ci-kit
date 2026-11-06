from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "actions/gradle-problems-summary/gradle_problems_summary.py"
FIXTURE = ROOT / "tests/fixtures/gradle-problems/problems-report.html"
FIXTURE_WORKSPACE = "/__w/demo/demo"
DEFAULT_REPORT = "build/reports/problems/problems-report.html"


def report_html(data: object) -> str:
    """Wrap report data in the Gradle 9 problems-report data block."""
    payload = data if isinstance(data, str) else json.dumps(data)
    return (
        "<!DOCTYPE html>\n<html><body>\n<script type=\"text/javascript\">\n"
        "function configurationCacheProblems() { return (\n"
        "// begin-report-data\n"
        f"{payload}\n"
        "// end-report-data\n"
        ");}\n</script>\n</body></html>\n"
    )


def diagnostic(name: str, label: str, *, severity: str = "WARNING", path: str = "build.gradle") -> dict:
    return {
        "problemId": [
            {"name": "deprecation", "displayName": "Deprecation"},
            {"name": name, "displayName": f"{name} display"},
        ],
        "severity": severity,
        "contextualLabel": label,
        "locations": [{"path": path, "line": 2}, {"taskPath": ":hello"}],
    }


def report_data(diagnostics: list, summaries: list | None = None) -> dict:
    return {
        "diagnostics": diagnostics,
        "problemsReport": {
            "totalProblemCount": len(diagnostics),
            "buildName": "build ':'",
            "requestedTasks": "build",
            "documentationLink": "https://docs.gradle.org/9.8.2/userguide/reporting_problems.html",
            "summaries": summaries or [],
        },
    }


def table_rows(summary: str) -> list[str]:
    return [line for line in summary.splitlines() if line.startswith("| ") and "---" not in line]


def unescaped_pipes(line: str) -> int:
    return len(re.findall(r"(?<!\\)\|", line))


class GradleProblemsSummaryTests(unittest.TestCase):
    def write(self, workspace: Path, relative: str, content: str | bytes) -> Path:
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def fixture(self, workspace: Path, relative: str = DEFAULT_REPORT) -> Path:
        content = FIXTURE.read_text(encoding="utf-8").replace(FIXTURE_WORKSPACE, str(workspace))
        return self.write(workspace, relative, content)

    def run_summary(
        self,
        workspace: Path,
        **inputs: str,
    ) -> tuple[subprocess.CompletedProcess[str], str, str]:
        summary = workspace / "github-summary.md"
        output = workspace / "github-output.txt"
        env = {
            **os.environ,
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
            "GITHUB_WORKSPACE": str(workspace),
            "FAIL_ON_MISSING": inputs.get("fail_on_missing", "false"),
            "MAX_DEPTH": inputs.get("max_depth", "64"),
            "MAX_FILE_BYTES": inputs.get("max_file_bytes", "10485760"),
            "MAX_FILES": inputs.get("max_files", "50"),
            "MAX_ROWS": inputs.get("max_rows", "50"),
            "MAX_SCAN_ENTRIES": inputs.get("max_scan_entries", "100000"),
            "MAX_SUMMARY_BYTES": inputs.get("max_summary_bytes", "65536"),
            "MAX_TOTAL_BYTES": inputs.get("max_total_bytes", "52428800"),
            "REPORT_PATHS": inputs.get("report_paths", DEFAULT_REPORT),
            "TITLE": inputs.get("title", "Gradle problems"),
            "WORKING_DIRECTORY": inputs.get("working_directory", "."),
        }
        result = subprocess.run(
            [sys.executable, "-I", str(SCRIPT)],
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
        return (
            result,
            summary.read_text(encoding="utf-8") if summary.exists() else "",
            output.read_text(encoding="utf-8") if output.exists() else "",
        )

    def test_real_gradle_report_is_grouped_by_problem_id_with_counts_and_locations(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gradle-problems-real-") as tmp:
            workspace = Path(tmp)
            self.fixture(workspace)
            result, summary, output = self.run_summary(workspace)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("### Gradle problems", summary)
            self.assertIn("44 problems in 1 report", summary)
            self.assertIn("Severity | Problem | Group | Message | Location | Count", summary)
            rows = table_rows(summary)
            self.assertEqual(len(rows), 5)  # header + four problem ids
            custom = next(row for row in rows if "Custom group" in row)
            self.assertTrue(custom.endswith("| 40 |"), custom)
            raw = next(row for row in rows if "found raw type" in row)
            self.assertIn("| src/main/java/demo/A\\.java\\:3\\:51 \\(\\+1 more\\) |", raw)
            self.assertIn("Compilation / Java compilation", raw)
            self.assertTrue(raw.endswith("| 2 |"), raw)
            self.assertNotIn(str(workspace), summary)
            self.assertIn("reports=1\n", output)
            self.assertIn("invalid-reports=0\n", output)
            self.assertIn("problems=44\n", output)
            self.assertIn("has-problems=true\n", output)

    def test_untrusted_fields_cannot_inject_html_markdown_or_table_cells(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gradle-problems-xss-") as tmp:
            workspace = Path(tmp)
            self.fixture(workspace)
            hostile = diagnostic(
                "x",
                "![img](https://evil.example/p.png) <script>alert(1)</script> | `tick` $x$ ‮\x1b[31m\nnext",
                severity="<b>ERROR</b>",
                path="a|b<iframe>.gradle",
            )
            hostile["problemId"][0]["displayName"] = "# Heading [link](javascript:alert(1))"
            data = report_data([hostile])
            data["problemsReport"]["buildName"] = "</details><img src=x onerror=alert(1)>"
            data["problemsReport"]["requestedTasks"] = "*emphasis* _x_"
            self.write(workspace, "other/" + DEFAULT_REPORT, report_html(data))
            result, summary, _ = self.run_summary(
                workspace,
                report_paths=f"{DEFAULT_REPORT}\nother/{DEFAULT_REPORT}",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            for raw in ("<script", "<img", "<iframe", "<b>", "</details>", "**bold**", "![", "](", "‮", "\x1b"):
                self.assertNotIn(raw, summary, raw)
            self.assertIsNone(re.search(r"(?<!\\)`", summary))
            self.assertIn("&lt;script&gt;", summary)
            self.assertIn("\\*\\*bold\\*\\*", summary)
            self.assertIn("\\$x\\$", summary)
            for row in table_rows(summary):
                self.assertEqual(unescaped_pipes(row), 7, row)

    def test_huge_fields_rows_and_summary_size_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gradle-problems-huge-") as tmp:
            workspace = Path(tmp)
            diagnostics = [diagnostic(f"id-{index}", "x" * 10000 + "|") for index in range(500)]
            self.write(workspace, DEFAULT_REPORT, report_html(report_data(diagnostics)))
            result, summary, output = self.run_summary(workspace, max_rows="20")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(table_rows(summary)), 21)
            self.assertIn("480 more problem types not shown", summary)
            self.assertTrue(all(len(row) < 2000 for row in table_rows(summary)))
            self.assertIn("problems=500\n", output)

            summary_path = workspace / "github-summary.md"
            summary_path.unlink()
            result, summary, _ = self.run_summary(workspace, max_summary_bytes="4096")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertLessEqual(len(summary.encode("utf-8")), 4096)
            self.assertIn("Summary truncated at max-summary-bytes=4096", summary)

    def test_missing_report_is_optional_or_fails_closed_by_input(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gradle-problems-missing-") as tmp:
            workspace = Path(tmp)
            result, summary, output = self.run_summary(workspace)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("No Gradle Problems Report matched", summary)
            self.assertIn("reports=0\n", output)
            self.assertIn("problems=0\n", output)
            self.assertIn("has-problems=false\n", output)
            result, _, _ = self.run_summary(workspace, fail_on_missing="true")
            self.assertEqual(result.returncode, 2)
            self.assertIn("no Gradle Problems Report matched report-paths", result.stderr)

    def test_empty_report_has_no_problem_rows(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gradle-problems-empty-") as tmp:
            workspace = Path(tmp)
            self.write(workspace, DEFAULT_REPORT, report_html(report_data([])))
            result, summary, output = self.run_summary(workspace)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("No problems reported", summary)
            self.assertEqual(table_rows(summary), [])
            self.assertIn("has-problems=false\n", output)

    def test_malformed_and_unsupported_reports_warn_without_failing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gradle-problems-malformed-") as tmp:
            workspace = Path(tmp)
            cases = {
                "no-markers": "<html><body>nothing here</body></html>",
                "bad-json": report_html("{not json"),
                "deep-json": report_html("[" * 100000 + "]" * 100000),
                "config-cache": report_html({"diagnostics": [], "totalProblemCount": 0}),
                "wrong-types": report_html({"diagnostics": {}, "problemsReport": []}),
            }
            for name, content in cases.items():
                self.write(workspace, f"{name}/{DEFAULT_REPORT}", content)
            self.write(workspace, f"binary/{DEFAULT_REPORT}", b"\xff\xfe\x00bad")
            self.fixture(workspace, f"good/{DEFAULT_REPORT}")
            result, summary, output = self.run_summary(
                workspace,
                report_paths=f"*/{DEFAULT_REPORT}",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.count("::warning"), 6, result.stdout)
            self.assertIn("unsupported", result.stdout)
            self.assertIn("reports=1\n", output)
            self.assertIn("invalid-reports=6\n", output)
            self.assertIn("problems=44\n", output)
            self.assertEqual(summary.count("could not be summarized"), 6)

    def test_multiple_reports_are_appended_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gradle-problems-multi-") as tmp:
            workspace = Path(tmp)
            self.fixture(workspace, f"app/{DEFAULT_REPORT}")
            self.write(
                workspace,
                f"lib/{DEFAULT_REPORT}",
                report_html(report_data([diagnostic("only-lib", "lib problem", severity="ERROR")])),
            )
            (workspace / "github-summary.md").write_text("previous step\n", encoding="utf-8")
            result, summary, output = self.run_summary(
                workspace,
                report_paths=f"app/{DEFAULT_REPORT}\n**/{DEFAULT_REPORT}",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(summary.startswith("previous step\n"))
            self.assertIn("45 problems in 2 reports", summary)
            self.assertEqual(summary.count("#### "), 2)
            self.assertIn("app/build/reports/problems/problems\\-report\\.html", summary)
            self.assertIn("lib/build/reports/problems/problems\\-report\\.html", summary)
            self.assertLess(summary.index("app/build"), summary.index("lib/build"))
            self.assertIn("| ERROR | only\\-lib display |", summary)
            self.assertIn("reports=2\n", output)
            self.assertIn("problems=45\n", output)

    def test_paths_cannot_escape_workspace_or_inject_workflow_commands(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gradle-problems-guards-") as tmp:
            workspace = Path(tmp)
            for pattern in ("../build/x.html", "/etc/passwd", "a/**b/x.html", "a/[x].html"):
                result, _, _ = self.run_summary(workspace, report_paths=pattern)
                self.assertEqual(result.returncode, 2, pattern)
            outside = Path(tmp) / "outside.html"
            outside.write_text(report_html(report_data([diagnostic("leak", "leak")])), encoding="utf-8")
            (workspace / "build/reports/problems").mkdir(parents=True)
            (workspace / DEFAULT_REPORT).symlink_to(outside)
            result, summary, output = self.run_summary(workspace)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("leak", summary)
            self.assertIn("reports=0\n", output)

            injected = "evil\n::error::pwned"
            self.write(workspace, f"{injected}/{DEFAULT_REPORT}", "no markers")
            result, _, output = self.run_summary(workspace, report_paths=f"*/{DEFAULT_REPORT}")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("\n::error::", "\n" + result.stdout)
            self.assertIn("evil%0A::error::pwned", result.stdout)

    def test_input_bounds_are_validated(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gradle-problems-inputs-") as tmp:
            workspace = Path(tmp)
            for name, value, message in (
                ("fail_on_missing", "yes", "fail-on-missing"),
                ("max_rows", "0", "max-rows"),
                ("max_summary_bytes", "100", "max-summary-bytes"),
                ("max_file_bytes", "-1", "max-file-bytes"),
                ("title", "two\nlines", "title"),
            ):
                result, _, _ = self.run_summary(workspace, **{name: value})
                self.assertEqual(result.returncode, 2, name)
                self.assertIn(message, result.stderr)
            self.fixture(workspace)
            result, _, _ = self.run_summary(workspace, max_file_bytes="1024")
            self.assertEqual(result.returncode, 2)
            self.assertIn("max-file-bytes=1024", result.stderr)

    def test_action_metadata_is_self_contained(self) -> None:
        data = yaml.safe_load((ROOT / "actions/gradle-problems-summary/action.yml").read_text())
        step = data["runs"]["steps"][0]
        self.assertEqual(step["id"], "summary")
        self.assertIn("${GITHUB_ACTION_PATH}/gradle_problems_summary.py", step["run"])
        self.assertNotIn("${{", step["run"])
        self.assertEqual(data["inputs"]["report-paths"]["default"], DEFAULT_REPORT)
        self.assertEqual(data["inputs"]["fail-on-missing"]["default"], "false")
        self.assertEqual(
            set(data["outputs"]),
            {"reports", "invalid-reports", "problems", "has-problems"},
        )
        env_names = set(step["env"])
        self.assertEqual(
            env_names,
            {name.upper().replace("-", "_") for name in data["inputs"]},
        )


if __name__ == "__main__":
    unittest.main()

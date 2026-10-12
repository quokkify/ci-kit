from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.cli import main
from ci_kit.gate import evaluate, render_summary

REF = "0123456789abcdef0123"


def needs(plan="success", core="success", java="success", all=None):
    if all:
        plan = core = java = all
    return {name: {"result": result, "outputs": {}} for name, result in (("plan", plan), ("core-checks", core), ("java", java))}


def row(rows, module):
    return next(item for item in rows if item.module == module)


def jobs_with_failed_step(step, number=4):
    return [
        {"name": "ci / core-checks / core-checks", "conclusion": "success", "steps": []},
        {"name": "ci / java (Application Java) / ci", "conclusion": "failure", "steps": [
            {"name": "Set up job", "conclusion": "success", "number": 1},
            {"name": step, "conclusion": "failure", "number": number},
            {"name": "Post", "conclusion": "failure", "number": 9},
        ]},
    ]


def failed_java(step):
    return evaluate(["core", "java"], needs(java="failure"), jobs_with_failed_step(step), REF)[1]


class GateTests(unittest.TestCase):
    def test_all_success_passes(self):
        ok, rows = evaluate(["core", "java"], needs(all="success"), [], REF)
        self.assertTrue(ok)
        self.assertEqual([r.result for r in rows], ["ok", "ok"])

    def test_cancelled_fails(self):
        ok, rows = evaluate(["core", "java"], needs(java="cancelled"), [], REF)
        self.assertFalse(ok)
        self.assertEqual(row(rows, "java").result, "cancelled")

    def test_disabled_module_skipped_is_fine(self):
        self.assertTrue(evaluate(["core"], needs(java="skipped"), [], REF)[0])

    def test_enabled_module_skipped_is_cik_core_020(self):
        ok, rows = evaluate(["core", "java"], needs(java="skipped"), [], REF)
        self.assertFalse(ok)
        self.assertEqual(row(rows, "java").code, "CIK-CORE-020")
        self.assertEqual(row(rows, "java").result, "skipped")

    def test_failed_setup_java_is_toolkit(self):
        found = row(failed_java("Set up Java"), "java")
        self.assertEqual((found.category, found.failing_step), ("toolkit", "Set up Java"))

    def test_failed_gradle_test_is_project(self):
        self.assertEqual(row(failed_java("Gradle test with bounded recovery"), "java").category, "project")

    def test_first_failed_step_by_number_decides(self):
        jobs = jobs_with_failed_step("Gradle test with bounded recovery", number=7)
        jobs[1]["steps"].append({"name": "Set up Java", "conclusion": "failure", "number": 3})
        found = row(evaluate(["java"], needs(java="failure"), jobs, REF)[1], "java")
        self.assertEqual(found.failing_step, "Set up Java")

    def test_plan_failure_is_template(self):
        ok, rows = evaluate([], needs(plan="failure", core="skipped", java="skipped"), [], REF)
        self.assertFalse(ok)
        self.assertEqual((row(rows, "plan").category, row(rows, "plan").code), ("template", ""))

    def test_no_jobs_api_still_decides_and_marks_unknown(self):
        ok, rows = evaluate(["core", "java"], needs(java="failure"), None, REF)
        self.assertFalse(ok)
        self.assertEqual(row(rows, "java").category, "unknown")

    def test_summary_table_header_and_runbook_link(self):
        _, rows = evaluate(["core", "java"], needs(java="skipped"), [], REF)
        table = render_summary(rows)
        self.assertTrue(table.startswith("| module | template @ | ci-kit @ | result | category | failing step | code |\n"))
        self.assertIn(f"[CIK-CORE-020](https://github.com/quokkify/ci-kit/blob/{REF}/docs/errors/CIK-CORE-020.md)", table)
        self.assertIn("| java | - | 0123456789ab | skipped |", table)

    def test_failed_non_enabled_job_fails_gate(self):
        ok, rows = evaluate([], needs(core="skipped", java="failure"), [], REF)
        self.assertFalse(ok)
        self.assertEqual((row(rows, "java").result, row(rows, "java").category), ("fail", "unknown"))

    def test_cancelled_non_enabled_job_fails_gate(self):
        ok, rows = evaluate([], needs(core="skipped", java="cancelled"), [], REF)
        self.assertFalse(ok)
        self.assertEqual(row(rows, "java").result, "cancelled")

    def test_unknown_result_on_non_enabled_job_fails_gate(self):
        ok, rows = evaluate([], needs(core="skipped", java="neutral"), [], REF)
        self.assertFalse(ok)
        self.assertEqual(row(rows, "java").result, "neutral")

    def test_skipped_non_enabled_job_adds_no_row(self):
        ok, rows = evaluate([], needs(core="skipped", java="skipped"), [], REF)
        self.assertTrue(ok)
        self.assertEqual(rows, [])

    def test_pipe_in_step_name_is_escaped(self):
        found = failed_java("a | b")
        self.assertIn("a \\| b", render_summary(found))


class GateCliTests(unittest.TestCase):
    def run_gate(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["gate", *args])
        return code, out.getvalue(), err.getvalue()

    def test_malformed_json_fails_closed(self):
        code, _, err = self.run_gate("--enabled", "[", "--needs", "{}", "--ref", REF)
        self.assertEqual(code, 1)
        self.assertIn("--enabled is not valid JSON", err)
        code, _, err = self.run_gate("--enabled", "[]", "--needs", "nope", "--ref", REF)
        self.assertEqual(code, 1)

    def test_jobs_wrapper_summary_append_and_exit(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw).resolve()
            (tmp / "jobs.json").write_text(json.dumps({"jobs": jobs_with_failed_step("Set up Java")}))
            summary = tmp / "summary.md"
            summary.write_text("existing\n")
            code, out, _ = self.run_gate(
                "--enabled", '["core","java"]', "--needs", json.dumps(needs(java="failure")),
                "--jobs-file", str(tmp / "jobs.json"), "--ref", REF, "--summary", str(summary),
            )
            self.assertEqual(code, 1)
            self.assertIn("toolkit", out)
            self.assertTrue(summary.read_text().startswith("existing\n| module"))

    def test_malformed_jobs_file_means_unknown(self):
        with tempfile.TemporaryDirectory() as raw:
            bad = Path(raw).resolve() / "jobs.json"
            bad.write_text("{")
            code, out, _ = self.run_gate(
                "--enabled", '["java"]', "--needs", json.dumps(needs(java="failure")), "--jobs-file", str(bad), "--ref", REF,
            )
            self.assertEqual(code, 1)
            self.assertIn("unknown", out)

    def test_unwritable_summary_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            missing = Path(raw).resolve() / "no-dir" / "summary.md"
            code, _, err = self.run_gate(
                "--enabled", '["core"]', "--needs", json.dumps(needs(all="success")), "--ref", REF, "--summary", str(missing),
            )
            self.assertEqual(code, 1)
            self.assertIn("--summary", err)

    def test_success_exits_zero(self):
        code, _, _ = self.run_gate("--enabled", '["core"]', "--needs", json.dumps(needs(all="success")), "--ref", REF)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.cli import main
from ci_kit.gate import _failing_step, evaluate, render_summary

REF = "0123456789abcdef0123"


def needs(plan="success", core="success", java="success", all=None, **extra):
    if all:
        plan = core = java = all
    results = {"plan": plan, "core-checks": core, "java": java, **extra}
    return {name: {"result": result, "outputs": {}} for name, result in results.items()}


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

    def test_language_module_failures_are_categorized(self):
        cases = (
            ("python", "Set up Python", "toolkit"), ("python", "Prepare Python package manager", "toolkit"),
            ("python", "Cache Python dependencies", "toolkit"), ("python", "Test", "project"),
            ("python", "Validate inputs", "project"),
            ("node", "Set up Node.js", "toolkit"), ("node", "Prepare Node.js package manager", "toolkit"),
            ("node", "Cache Node.js dependencies", "toolkit"), ("node", "Lint", "project"),
            ("node", "Validate inputs", "project"),
            ("docker", "Set up Docker Buildx", "toolkit"), ("docker", "Build image", "project"),
        )
        for module, step, category in cases:
            with self.subTest(module=module, step=step):
                jobs = [{"name": f"ci / {module} (Component) / ci", "conclusion": "failure", "steps": [{"name": step, "conclusion": "failure", "number": 2}]}]
                _, rows = evaluate(["core", module], needs(**{module: "failure"}), jobs, REF)
                found = row(rows, module)
                self.assertEqual((found.category, found.failing_step), (category, step))

    def test_first_failed_step_by_number_decides(self):
        jobs = jobs_with_failed_step("Gradle test with bounded recovery", number=7)
        jobs[1]["steps"].append({"name": "Set up Java", "conclusion": "failure", "number": 3})
        found = row(evaluate(["java"], needs(java="failure"), jobs, REF)[1], "java")
        self.assertEqual(found.failing_step, "Set up Java")

    def test_plan_failure_is_template(self):
        ok, rows = evaluate([], needs(plan="failure", core="skipped", java="skipped"), [], REF)
        self.assertFalse(ok)
        self.assertEqual((row(rows, "plan").category, row(rows, "plan").code), ("template", ""))

    def test_plan_failure_lists_codes_and_modules_not_blamed(self):
        ok, rows = evaluate(["core", "java"], needs(plan="failure", core="skipped", java="skipped"), [], REF, plan_codes=["CIK-CORE-010", "CIK-CORE-003"])
        self.assertFalse(ok)
        self.assertEqual(row(rows, "plan").code, "CIK-CORE-010,CIK-CORE-003")
        self.assertEqual(row(rows, "plan").category, "template")
        for module in ("core", "java"):
            found = row(rows, module)
            self.assertEqual((found.result, found.category, found.failing_step, found.code), ("skipped", "", "plan failed", ""))
        table = render_summary(rows)
        self.assertIn(f"[CIK-CORE-010](https://github.com/quokkify/ci-kit/blob/{REF}/docs/errors/CIK-CORE-010.md), [CIK-CORE-003](", table)
        self.assertNotIn("CIK-CORE-020", table)
        self.assertIn("| core | - | 0123456789ab | skipped |  | plan failed |  |", table)

    def test_plan_failure_without_codes_has_empty_code_cell(self):
        _, rows = evaluate(["core"], needs(plan="failure", core="skipped"), [], REF)
        self.assertEqual(row(rows, "plan").code, "")

    def test_plan_failure_still_reports_failed_module(self):
        _, rows = evaluate(["core"], needs(plan="cancelled", core="failure"), [], REF)
        self.assertEqual(row(rows, "core").result, "fail")

    def test_skipped_with_plan_success_keeps_cik_core_020_and_ignores_plan_codes(self):
        _, rows = evaluate(["java"], needs(java="skipped"), [], REF, plan_codes=["CIK-CORE-010"])
        self.assertEqual(row(rows, "java").code, "CIK-CORE-020")

    def test_template_commits_rendered_for_modules_only(self):
        _, rows = evaluate(["core", "java"], needs(plan="failure", core="skipped", java="success"), [], REF, template_commits={"core": "abcdef1234567890", "java": "fedcba"})
        table = render_summary(rows)
        self.assertIn("| plan | - | 0123456789ab |", table)
        self.assertIn("| core | abcdef1234567890 | 0123456789ab |", table)
        self.assertIn("| java | fedcba | 0123456789ab |", table)
        _, rows = evaluate(["core"], needs(all="success"), [], REF, template_commits={"core": "abc"})
        self.assertIn("| core | abc |", render_summary(rows))
        _, rows = evaluate(["core"], needs(all="success"), [], REF)
        self.assertIn("| core | - |", render_summary(rows))

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


class GateJobNameTests(unittest.TestCase):
    def test_gate_matches_language_job_name(self):
        jobs = [{"name": "ci / python (App Python) / ci", "conclusion": "failure", "steps": [{"name": "Run tests", "conclusion": "failure"}]}]
        self.assertEqual(_failing_step(jobs, "python"), "Run tests")


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

    def test_malformed_plan_codes_and_template_commits_fail_closed(self):
        base = ("--enabled", "[]", "--needs", "{}", "--ref", REF)
        for flag, value, message in (
            ("--plan-codes", "[", "--plan-codes is not valid JSON"),
            ("--plan-codes", '{"a":1}', "--plan-codes must be a JSON array of strings"),
            ("--plan-codes", "[1]", "--plan-codes must be a JSON array of strings"),
            ("--template-commits", "{", "--template-commits is not valid JSON"),
            ("--template-commits", "[]", "--template-commits must be a JSON object of strings"),
            ("--template-commits", '{"core":1}', "--template-commits must be a JSON object of strings"),
        ):
            code, _, err = self.run_gate(*base, flag, value)
            self.assertEqual(code, 1, (flag, value))
            self.assertIn(message, err)

    def test_plan_failure_cli_output_and_annotations(self):
        args = ("--enabled", '["core","java"]', "--needs", json.dumps(needs(plan="failure", core="skipped", java="skipped")), "--ref", REF,
                "--plan-codes", '["CIK-CORE-010"]', "--template-commits", '{"core":"c0ffee"}')
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "false"}):
            code, out, err = self.run_gate(*args)
        self.assertEqual(code, 1)
        self.assertIn("| core | c0ffee |", out)
        self.assertIn("CIK-CORE-010 plan: fail", err)
        self.assertNotIn("CIK-CORE-020", out + err)
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            code, out, _ = self.run_gate(*args)
        self.assertNotIn("CIK-CORE-020", out)
        self.assertIn("::error title=CIK-CORE-010::plan: fail", out)
        self.assertIn("::error::core: skipped (step 'plan failed')", out)

    def test_success_exits_zero(self):
        code, _, _ = self.run_gate("--enabled", '["core"]', "--needs", json.dumps(needs(all="success")), "--ref", REF)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()

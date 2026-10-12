from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
from pathlib import Path
from unittest import TestCase, main
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("migration_checks", ROOT / "scripts/migration_checks.py")
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

HEAD = "a" * 40
MANAGED_ID = 7
MARKER_BODY = "intro\n<!-- ci-kit-migrate -->\n```json\n" + json.dumps({
    "module": "core", "area": "ci", "ref": "v1.0.0", "commits": [], "findings": [],
    "checks_before": ["Shared template contract"], "checks_after": ["ci / gate"],
}) + "\n```\n"


def entry(context, source="active-ruleset", ruleset_id=MANAGED_ID):
    item = {"context": context, "integration_id": None, "source": source}
    if source == "active-ruleset":
        item["ruleset_id"] = ruleset_id
    return item


def policy(*entries, status="active"):
    return {"status": status, "checks": list(entries)}


def codeql_rule(threshold):
    return {"type": "code_scanning", "parameters": {"code_scanning_tools": [{"tool": "CodeQL", "alerts_threshold": threshold, "security_alerts_threshold": "all"}]}}


class FakeClient:
    managed = {"id": MANAGED_ID, "name": "ci-kit/security/o/r"}
    detail = {"id": MANAGED_ID, "name": "ci-kit/security/o/r", "rules": []}
    listing = None

    def __init__(self, token, *args, **kwargs):
        self.token = token

    def request(self, method, path, payload=None):
        if path.startswith("/repos/o/r/rulesets?"):
            return type(self).listing if type(self).listing is not None else [type(self).managed]
        if path == f"/repos/o/r/rulesets/{MANAGED_ID}":
            return type(self).detail
        raise AssertionError(path)


class NextChecksTests(TestCase):
    def test_forward_keeps_other_checks(self):
        self.assertEqual(module.next_checks(["Gitleaks", "Shared template contract", "CodeQL"], ["Shared template contract"], ["ci / gate"], revert=False), ["Gitleaks", "CodeQL", "ci / gate"])

    def test_idempotent_when_already_applied(self):
        self.assertEqual(module.next_checks(["Gitleaks", "ci / gate"], ["Shared template contract"], ["ci / gate"], revert=False), ["Gitleaks", "ci / gate"])

    def test_revert(self):
        self.assertEqual(module.next_checks(["Gitleaks", "ci / gate"], ["Shared template contract"], ["ci / gate"], revert=True), ["Gitleaks", "Shared template contract"])

    def test_duplicates_removed_order_preserved(self):
        self.assertEqual(module.next_checks(["b", "a", "b"], [], ["a", "c", "c"], revert=False), ["b", "a", "c"])

    def test_empty_result_rejected(self):
        with self.assertRaises(ValueError):
            module.next_checks(["Shared template contract"], ["Shared template contract"], [], revert=False)

    def test_missing_marker_rejected(self):
        with self.assertRaises(ValueError):
            module.parse_marker("no marker here")


class MainTests(TestCase):
    def setUp(self):
        FakeClient.managed = {"id": MANAGED_ID, "name": "ci-kit/security/o/r"}
        FakeClient.detail = {"id": MANAGED_ID, "name": "ci-kit/security/o/r", "rules": []}
        FakeClient.listing = None
        self.reconcile_calls = []
        self.policy = policy(entry("Gitleaks"), entry("Shared template contract"))
        self.body = MARKER_BODY
        self.pr_calls = []

    def run_main(self, argv, **env):
        def fake_pr_view(repo, number):
            self.pr_calls.append((repo, number))
            return {"body": self.body, "headRefOid": HEAD, "baseRefName": "develop"}

        def fake_required(request, repository, branch):
            self.required_args = (repository, branch)
            return self.policy

        def fake_reconcile(client, repo, branch, checks, revision, mode, dry_run, codeql_threshold=None):
            self.reconcile_calls.append(dict(repo=repo, branch=branch, checks=checks, revision=revision, mode=mode, dry_run=dry_run, codeql_threshold=codeql_threshold))
            return {"action": "update"}

        out, err = io.StringIO(), io.StringIO()
        with patch.object(module, "pr_view", fake_pr_view), patch.object(module, "required_checks", fake_required), \
                patch.object(module, "GitHubClient", FakeClient), patch.object(module, "reconcile", fake_reconcile), \
                patch.dict(os.environ, {"GH_TOKEN": "t", **env}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = module.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_main_passes_head_sha_as_revision(self):
        code, out, _ = self.run_main(["--pr", "o/r#5"])
        self.assertEqual(code, 0)
        self.assertEqual(self.pr_calls, [("o/r", 5)])
        self.assertEqual(self.required_args, ("o/r", "develop"))
        call = self.reconcile_calls[0]
        self.assertEqual((call["revision"], call["mode"], call["dry_run"], call["branch"]), (HEAD, "active", False, "develop"))
        self.assertEqual(call["checks"], ["Gitleaks", "ci / gate"])
        self.assertIn("Shared template contract", out)
        self.assertIn("ci / gate", out)

    def test_dry_run_passes_flag_and_prints_plan(self):
        code, out, _ = self.run_main(["--pr", "o/r#5", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertTrue(self.reconcile_calls[0]["dry_run"])
        self.assertIn("before", out)
        self.assertIn("after", out)

    def test_revert_swaps(self):
        self.policy = policy(entry("Gitleaks"), entry("ci / gate"))
        code, _, _ = self.run_main(["--pr", "o/r#5", "--revert"])
        self.assertEqual(code, 0)
        self.assertEqual(self.reconcile_calls[0]["checks"], ["Gitleaks", "Shared template contract"])

    def test_only_managed_ruleset_contexts_are_operated_on(self):
        self.policy = policy(entry("Gitleaks"), entry("Shared template contract"), entry("Other", ruleset_id=99), entry("Classic", source="classic-protection"))
        code, _, _ = self.run_main(["--pr", "o/r#5"])
        self.assertEqual(code, 0)
        self.assertEqual(self.reconcile_calls[0]["checks"], ["Gitleaks", "ci / gate"])

    def test_classic_protection_owned_before_refused(self):
        self.policy = policy(entry("Gitleaks"), entry("Shared template contract"), entry("Shared template contract", source="classic-protection"))
        code, _, err = self.run_main(["--pr", "o/r#5"])
        self.assertEqual(code, 1)
        self.assertIn("classic-protection", err)
        self.assertEqual(self.reconcile_calls, [])

    def test_other_ruleset_owned_before_refused(self):
        self.policy = policy(entry("Gitleaks"), entry("Shared template contract"), entry("Shared template contract", ruleset_id=99))
        code, _, err = self.run_main(["--pr", "o/r#5"])
        self.assertEqual(code, 1)
        self.assertIn("ruleset 99", err)
        self.assertEqual(self.reconcile_calls, [])

    def test_revert_checks_after_owned_elsewhere_refused(self):
        self.policy = policy(entry("ci / gate"), entry("ci / gate", source="classic-protection"))
        code, _, err = self.run_main(["--pr", "o/r#5", "--revert"])
        self.assertEqual(code, 1)
        self.assertIn("classic-protection", err)
        self.assertEqual(self.reconcile_calls, [])

    def test_unknown_status_refused(self):
        self.policy = policy(entry("Gitleaks"), status="unknown")
        code, _, err = self.run_main(["--pr", "o/r#5"])
        self.assertEqual(code, 1)
        self.assertIn("unknown", err)
        self.assertEqual(self.reconcile_calls, [])

    def test_managed_ruleset_missing_refused(self):
        FakeClient.listing = []
        code, _, err = self.run_main(["--pr", "o/r#5"])
        self.assertEqual(code, 1)
        self.assertIn("ci-kit/security/o/r", err)
        self.assertEqual(self.reconcile_calls, [])

    def test_codeql_threshold_preserved(self):
        FakeClient.detail = {"id": MANAGED_ID, "rules": [codeql_rule("errors")]}
        self.run_main(["--pr", "o/r#5"])
        self.assertEqual(self.reconcile_calls[0]["codeql_threshold"], "errors")

    def test_no_codeql_rule_passes_none(self):
        self.run_main(["--pr", "o/r#5"])
        self.assertIsNone(self.reconcile_calls[0]["codeql_threshold"])

    def test_missing_marker_exit_1(self):
        self.body = "nothing"
        code, _, err = self.run_main(["--pr", "o/r#5"])
        self.assertEqual(code, 1)
        self.assertIn("marker", err)

    def test_malformed_pr_argument(self):
        with self.assertRaises(SystemExit):
            self.run_main(["--pr", "o/r"])

    def test_missing_token_exit_2(self):
        with patch.dict(os.environ, {}, clear=True):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = module.main(["--pr", "o/r#5"])
        self.assertEqual(code, 2)


class IdempotencyTests(TestCase):
    def test_rerun_after_success_is_reconcile_noop(self):
        desired = module.build_desired("ci-kit/security/o/r", "develop", ["Gitleaks", "ci / gate"], "active", "errors")
        existing = {**desired, "id": MANAGED_ID}

        class Client:
            def request(self, method, path, payload=None):
                assert method == "GET", (method, path)
                if "check-runs" in path:
                    return {"check_runs": [{"name": "Gitleaks"}, {"name": "ci / gate"}], "total_count": 2}
                if "/statuses" in path:
                    return []
                if path.startswith("/repos/o/r/rulesets?"):
                    return [{"id": MANAGED_ID, "name": "ci-kit/security/o/r"}]
                return existing

        result = module.reconcile(Client(), "o/r", "develop", ["Gitleaks", "ci / gate"], HEAD, "active", False, "errors")
        self.assertEqual(result["action"], "noop")


if __name__ == "__main__":
    main()

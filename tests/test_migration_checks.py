from __future__ import annotations

import base64
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
BASE = "b" * 40
REV = "c" * 40
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


def codeql_rule(threshold, security="all"):
    return {"type": "code_scanning", "parameters": {"code_scanning_tools": [{"tool": "CodeQL", "alerts_threshold": threshold, "security_alerts_threshold": security}]}}


def status_rule(*contexts, integration_id=None):
    return {"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": c, "integration_id": integration_id} for c in contexts]}}


def make_detail(*rules, enforcement="active", include=("refs/heads/develop",)):
    return {"id": MANAGED_ID, "name": "ci-kit/security/o/r", "enforcement": enforcement,
            "conditions": {"ref_name": {"include": list(include), "exclude": []}}, "rules": list(rules)}


DEFAULT_RULES = (status_rule("Gitleaks", "Shared template contract"),)


class FakeClient:
    managed = {"id": MANAGED_ID, "name": "ci-kit/security/o/r"}
    detail = make_detail(*DEFAULT_RULES)
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

    def test_component_checks_replaced_and_foreign_context_kept(self):
        self.assertEqual(
            module.next_checks(["Lint / docker", "Shared template contract", "App Python / ci"], ["Shared template contract", "App Python / ci"], ["ci / gate"], revert=False),
            ["Lint / docker", "ci / gate"],
        )

    def test_revert(self):
        self.assertEqual(
            module.next_checks(["Gitleaks", "ci / gate"], ["Shared template contract"], ["ci / gate"], revert=True, restore=["Shared template contract"]),
            ["Gitleaks", "Shared template contract"],
        )

    def test_revert_restores_only_what_the_switch_removed(self):
        # Regression guard for #459: skills required App Python / ci but never Shared template contract.
        self.assertEqual(
            module.next_checks(["CodeQL", "gitleaks", "ci / gate"], ["Shared template contract", "App Python / ci"], ["ci / gate"],
                               revert=True, restore=["App Python / ci"]),
            ["CodeQL", "gitleaks", "App Python / ci"],
        )

    def test_revert_with_nothing_to_restore_removes_checks_after(self):
        self.assertEqual(
            module.next_checks(["CodeQL", "ci / gate"], ["Shared template contract"], ["ci / gate"], revert=True, restore=[]),
            ["CodeQL"],
        )

    def test_revert_without_restore_rejected(self):
        with self.assertRaisesRegex(ValueError, "--restore"):
            module.next_checks(["Gitleaks", "ci / gate"], ["Shared template contract"], ["ci / gate"], revert=True)

    def test_restore_outside_checks_before_rejected(self):
        with self.assertRaisesRegex(ValueError, "CodeQL"):
            module.next_checks(["ci / gate"], ["Shared template contract"], ["ci / gate"], revert=True, restore=["CodeQL"])

    def test_duplicates_removed_order_preserved(self):
        self.assertEqual(module.next_checks(["b", "a", "b"], [], ["a", "c", "c"], revert=False), ["b", "a", "c"])

    def test_empty_result_rejected(self):
        with self.assertRaises(ValueError):
            module.next_checks(["Shared template contract"], ["Shared template contract"], [], revert=False)

    def test_missing_marker_rejected(self):
        with self.assertRaises(ValueError):
            module.parse_marker("no marker here")



def encoded(text, **extra):
    return {"type": "file", "encoding": "base64", "content": base64.b64encode(text.encode()).decode(), **extra}


class LegacyChecksTests(TestCase):
    def read(self, payload):
        with patch.object(module, "api_request", lambda endpoint: payload):
            return module.legacy_checks_at("o/r", BASE)

    def test_encoding_none_refused(self):
        with self.assertRaises(module.Refusal):
            self.read({"type": "file", "encoding": "none", "content": ""})

    def test_no_jobs_refused(self):
        with self.assertRaises(module.Refusal):
            self.read(encoded("on: push\n"))

    def test_missing_file_refused(self):
        with self.assertRaises(module.Refusal):
            self.read(None)

    def test_job_without_mapping_refused(self):
        with self.assertRaisesRegex(module.Refusal, "job a has no ci-kit"):
            self.read(encoded("jobs:\n  a:\n    name: Alpha\n  docker:\n    uses: quokkify/ci-kit/.github/workflows/docker-build.yml@v1.0.0\n"))

    def test_component_jobs_give_exact_contexts(self):
        text = "jobs:\n  template-contract:\n    name: Shared template contract\n  app:\n    name: App Python\n    uses: quokkify/ci-kit/.github/workflows/python-ci.yml@v1.0.0\n  docker:\n    uses: quokkify/ci-kit/.github/workflows/docker-build.yml@v1.0.0\n"
        self.assertEqual(self.read(encoded(text)), ["Shared template contract", "App Python / ci", "docker / build"])

    def test_job_names_and_endpoint(self):
        seen = []

        def fake(endpoint):
            seen.append(endpoint)
            return encoded("jobs:\n  a:\n    name: Alpha\n    uses: quokkify/ci-kit/.github/workflows/java-ci.yml@v1.0.0\n")

        with patch.object(module, "api_request", fake):
            self.assertEqual(module.legacy_checks_at("o/r", BASE), ["Alpha / ci"])
        self.assertIn(".github/workflows/validate.yml", seen[0])
        self.assertIn(f"?ref={BASE}", seen[0])


class MainTests(TestCase):
    def setUp(self):
        FakeClient.managed = {"id": MANAGED_ID, "name": "ci-kit/security/o/r"}
        FakeClient.detail = make_detail(*DEFAULT_RULES)
        FakeClient.listing = None
        self.legacy = ["Shared template contract"]
        self.legacy_refs = []
        self.reconcile_calls = []
        self.policy = policy(entry("Gitleaks"), entry("Shared template contract"))
        self.body = MARKER_BODY
        self.pr_calls = []

    def run_main(self, argv, **env):
        def fake_pr_view(repo, number):
            self.pr_calls.append((repo, number))
            return {"body": self.body, "headRefOid": HEAD, "baseRefName": "develop", "baseRefOid": BASE}

        def fake_legacy(repo, ref):
            self.legacy_refs.append(ref)
            if isinstance(self.legacy, Exception):
                raise self.legacy
            return self.legacy

        def fake_required(request, repository, branch):
            self.required_args = (repository, branch)
            return self.policy

        def fake_reconcile(client, repo, branch, checks, revision, mode, dry_run, codeql_threshold=None):
            self.reconcile_calls.append(dict(repo=repo, branch=branch, checks=checks, revision=revision, mode=mode, dry_run=dry_run, codeql_threshold=codeql_threshold))
            return {"action": "update"}

        out, err = io.StringIO(), io.StringIO()
        with patch.object(module, "pr_view", fake_pr_view), patch.object(module, "required_checks", fake_required), patch.object(module, "legacy_checks_at", fake_legacy), \
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
        FakeClient.detail = make_detail(status_rule("Gitleaks", "ci / gate"))
        code, _, _ = self.run_main(["--pr", "o/r#5", "--revert", "--revision", REV, "--restore", "Shared template contract"])
        self.assertEqual(code, 0)
        self.assertEqual(self.legacy_refs, [REV])
        self.assertEqual(self.reconcile_calls[0]["revision"], REV)
        self.assertEqual(self.reconcile_calls[0]["checks"], ["Gitleaks", "Shared template contract"])

    def test_revert_adds_back_only_restored_checks(self):
        # Regression guard for #459: the skills switch removed App Python / ci; Shared template contract was never required.
        legacy = ["Shared template contract", "App Python / ci"]
        self.body = MARKER_BODY.replace('["Shared template contract"]', json.dumps(legacy))
        self.legacy = legacy
        self.policy = policy(entry("CodeQL"), entry("gitleaks"), entry("ci / gate"))
        FakeClient.detail = make_detail(status_rule("CodeQL", "gitleaks", "ci / gate"))
        code, out, err = self.run_main(["--pr", "o/r#5", "--revert", "--revision", REV, "--restore", "App Python / ci"])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.reconcile_calls[0]["checks"], ["CodeQL", "gitleaks", "App Python / ci"])
        report = json.loads(out)
        self.assertEqual((report["removed"], report["added"]), (["ci / gate"], ["App Python / ci"]))

    def test_repeated_restore_flags_collect_every_context(self):
        legacy = ["Shared template contract", "App Python / ci"]
        self.body = MARKER_BODY.replace('["Shared template contract"]', json.dumps(legacy))
        self.legacy = legacy
        self.policy = policy(entry("Gitleaks"), entry("ci / gate"))
        FakeClient.detail = make_detail(status_rule("Gitleaks", "ci / gate"))
        code, _, err = self.run_main(["--pr", "o/r#5", "--revert", "--restore", "App Python / ci", "--revision", REV, "--restore", "Shared template contract"])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.reconcile_calls[0]["checks"], ["Gitleaks", "App Python / ci", "Shared template contract"])

    def test_forward_report_names_removed_checks_for_the_revert(self):
        code, out, err = self.run_main(["--pr", "o/r#5", "--dry-run"])
        self.assertEqual(code, 0, err)
        report = json.loads(out)
        self.assertEqual((report["removed"], report["added"]), (["Shared template contract"], ["ci / gate"]))

    def test_restore_outside_checks_before_refused(self):
        self.policy = policy(entry("Gitleaks"), entry("ci / gate"))
        FakeClient.detail = make_detail(status_rule("Gitleaks", "ci / gate"))
        self.refused(("--pr", "o/r#5", "--revert", "--revision", REV, "--restore", "Gitleaks"), needle="--restore")

    def test_only_managed_ruleset_contexts_are_operated_on(self):
        self.policy = policy(entry("Gitleaks"), entry("Shared template contract"), entry("Other", ruleset_id=99), entry("Classic", source="classic-protection"))
        code, _, _ = self.run_main(["--pr", "o/r#5"])
        self.assertEqual(code, 0)
        self.assertEqual(self.legacy_refs, [BASE])
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
        FakeClient.detail = make_detail(status_rule("ci / gate"))
        code, _, err = self.run_main(["--pr", "o/r#5", "--revert", "--revision", REV, "--restore", "Shared template contract"])
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
        self.policy = policy(status="unprotected")
        code, _, err = self.run_main(["--pr", "o/r#5"])
        self.assertEqual(code, 1)
        self.assertIn("ci-kit/security/o/r", err)
        self.assertEqual(self.reconcile_calls, [])

    def test_codeql_threshold_preserved(self):
        FakeClient.detail = make_detail(*DEFAULT_RULES, codeql_rule("errors", "high_or_higher"))
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

    def refused(self, argv=("--pr", "o/r#5"), needle=None):
        code, _, err = self.run_main(list(argv))
        self.assertEqual(code, 1)
        self.assertEqual(self.reconcile_calls, [])
        if needle:
            self.assertIn(needle, err)

    def test_evaluate_mode_refused(self):
        FakeClient.detail = make_detail(*DEFAULT_RULES, enforcement="evaluate")
        self.refused(needle="enforcement")

    def test_other_branch_target_refused(self):
        FakeClient.detail = make_detail(*DEFAULT_RULES, include=("refs/heads/main",))
        self.refused(needle="refs/heads/develop")

    def test_no_required_checks_rule_refused(self):
        FakeClient.detail = make_detail(codeql_rule("errors", "high_or_higher"))
        self.refused(needle="required_status_checks")

    def test_current_comes_from_detail_not_effective_rules(self):
        self.policy = policy(entry("Elsewhere", ruleset_id=99))
        code, _, _ = self.run_main(["--pr", "o/r#5"])
        self.assertEqual(code, 0)
        self.assertEqual(self.reconcile_calls[0]["checks"], ["Gitleaks", "ci / gate"])

    def test_crafted_checks_before_refused(self):
        self.body = MARKER_BODY.replace('["Shared template contract"]', '["Gitleaks"]')
        self.refused(needle="differs")

    def test_checks_after_must_match_module(self):
        self.body = MARKER_BODY.replace('["ci / gate"]', '["Other"]')
        self.refused(needle="CHECKS_AFTER")

    def test_unknown_module_refused(self):
        self.body = MARKER_BODY.replace('"core"', '"nope"')
        self.refused(needle="CHECKS_AFTER")

    def test_revert_requires_revision(self):
        with self.assertRaises(SystemExit):
            self.run_main(["--pr", "o/r#5", "--revert", "--restore"])

    def test_revert_requires_restore(self):
        with self.assertRaises(SystemExit):
            self.run_main(["--pr", "o/r#5", "--revert", "--revision", REV])

    def test_restore_requires_revert(self):
        with self.assertRaises(SystemExit):
            self.run_main(["--pr", "o/r#5", "--restore", "Shared template contract"])

    def test_integration_bound_check_refused(self):
        FakeClient.detail = make_detail(status_rule("Gitleaks", "Shared template contract", integration_id=15368))
        self.refused(needle="integration")

    def test_security_threshold_mismatch_refused(self):
        FakeClient.detail = make_detail(*DEFAULT_RULES, codeql_rule("errors", "all"))
        self.refused(needle="security_alerts_threshold")

    def test_malformed_codeql_rule_refused_not_crash(self):
        FakeClient.detail = make_detail(*DEFAULT_RULES, {"type": "code_scanning", "parameters": None})
        self.refused()
        FakeClient.detail = make_detail(*DEFAULT_RULES, {"type": "code_scanning", "parameters": {"code_scanning_tools": ["x"]}})
        self.refused()

    def test_validate_yml_read_failure_refused(self):
        self.legacy = RuntimeError("gh api failed")
        self.refused(needle="validate.yml")

    def test_missing_gh_refused(self):
        self.legacy = FileNotFoundError("gh")
        self.refused(needle="validate.yml")

    def test_api_request_non_404_failure_raises_runtime_error(self):
        class Result:
            returncode = 1
            stderr = "HTTP 500"
            stdout = ""
        with patch.object(module.subprocess, "run", return_value=Result()):
            with self.assertRaises(RuntimeError):
                module.api_request("repos/o/r/x")

    def test_required_checks_runtime_error_refused(self):
        def boom(request, repository, branch):
            raise RuntimeError("gh down")
        with patch.object(module, "pr_view", lambda r, n: {"body": self.body, "headRefOid": HEAD, "baseRefName": "develop", "baseRefOid": BASE}), \
                patch.object(module, "legacy_checks_at", lambda r, ref: self.legacy), patch.object(module, "required_checks", boom), \
                patch.object(module, "GitHubClient", FakeClient), patch.dict(os.environ, {"GH_TOKEN": "t"}), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(module.main(["--pr", "o/r#5"]), 1)

    def test_missing_gh_on_pr_view_refused(self):
        def boom(repo, number):
            raise FileNotFoundError("gh")
        with patch.object(module, "pr_view", boom), patch.dict(os.environ, {"GH_TOKEN": "t"}), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(module.main(["--pr", "o/r#5"]), 1)

    def test_revert_empty_checks_before_refused(self):
        self.body = MARKER_BODY.replace('["Shared template contract"]', '[]')
        self.legacy = []
        FakeClient.detail = make_detail(status_rule("Gitleaks", "ci / gate"))
        self.refused(("--pr", "o/r#5", "--revert", "--revision", REV, "--restore"))

    def test_yaml_error_refused(self):
        import yaml
        self.legacy = yaml.YAMLError("bad")
        self.refused(needle="validate.yml")

    def test_timeout_refused(self):
        self.legacy = module.subprocess.TimeoutExpired("gh", 45)
        self.refused(needle="validate.yml")

    def test_required_checks_timeout_refused(self):
        def boom(request, repository, branch):
            raise module.subprocess.TimeoutExpired("gh", 45)
        with patch.object(module, "pr_view", lambda r, n: {"body": self.body, "headRefOid": HEAD, "baseRefName": "develop", "baseRefOid": BASE}), \
                patch.object(module, "legacy_checks_at", lambda r, ref: self.legacy), patch.object(module, "required_checks", boom), \
                patch.object(module, "GitHubClient", FakeClient), patch.dict(os.environ, {"GH_TOKEN": "t"}), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(module.main(["--pr", "o/r#5"]), 1)

    def test_non_string_module_refused(self):
        self.body = MARKER_BODY.replace('"core"', '["core"]')
        self.refused(needle="CHECKS_AFTER")

    def test_missing_token_exit_2(self):
        with patch.dict(os.environ, {}, clear=True):
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = module.main(["--pr", "o/r#5"])
        self.assertEqual(code, 2)


UNMANAGED_ID = 21
BYPASS = [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always"}]


def unmanaged_detail(*contexts, integration_id=None, **overrides):
    detail = {"id": UNMANAGED_ID, "node_id": "RRS_1", "name": "main", "target": "branch", "source_type": "Repository", "source": "o/r",
              "enforcement": "active", "updated_at": "2026-10-01T00:00:00Z",
              "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}}, "bypass_actors": list(BYPASS),
              "rules": [{"type": "deletion"}, {"type": "pull_request", "parameters": {"required_approving_review_count": 0}},
                        status_rule(*contexts, integration_id=integration_id)]}
    detail.update(overrides)
    return detail


class UnmanagedClient:
    def __init__(self, detail, published=("Gitleaks", "Shared template contract", "ci / gate"), changed=None, readback=None):
        self.detail, self.published, self.changed, self.readback = detail, published, changed, readback
        self.reads = 0
        self.put = None

    def request(self, method, path, payload=None):
        if path.startswith("/repos/o/r/rulesets?"):
            return [{"id": UNMANAGED_ID, "name": "main"}]
        if "/check-runs?" in path:
            return {"check_runs": [{"name": name} for name in self.published], "total_count": len(self.published)}
        if "/statuses?" in path:
            return []
        if path == f"/repos/o/r/rulesets/{UNMANAGED_ID}":
            if method == "PUT":
                self.put = payload
                return {"id": UNMANAGED_ID}
            assert method == "GET", method
            if self.put is not None:
                return self.readback(self.put) if self.readback else {**self.detail, **self.put, "updated_at": "2026-10-02T00:00:00Z"}
            self.reads += 1
            return self.changed if self.changed is not None and self.reads > 1 else self.detail
        raise AssertionError(path)


class UnmanagedRulesetTests(TestCase):
    def setUp(self):
        self.policy = policy(entry("Gitleaks", ruleset_id=UNMANAGED_ID), entry("Shared template contract", ruleset_id=UNMANAGED_ID))
        self.client = UnmanagedClient(unmanaged_detail("Gitleaks", "Shared template contract"))
        self.legacy_refs = []

    def run_main(self, argv):
        def fake_legacy(repo, ref):
            self.legacy_refs.append(ref)
            return ["Shared template contract"]

        def no_reconcile(*args, **kwargs):
            raise AssertionError("reconcile must not run for an unmanaged ruleset")

        out, err = io.StringIO(), io.StringIO()
        with patch.object(module, "pr_view", lambda r, n: {"body": MARKER_BODY, "headRefOid": HEAD, "baseRefName": "main", "baseRefOid": BASE}), \
                patch.object(module, "legacy_checks_at", fake_legacy), patch.object(module, "required_checks", lambda request, repository, branch: self.policy), \
                patch.object(module, "GitHubClient", lambda token: self.client), patch.object(module, "reconcile", no_reconcile), \
                patch.dict(os.environ, {"GH_TOKEN": "t"}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = module.main(argv)
        return code, out.getvalue(), err.getvalue()

    def put_contexts(self):
        rule = next(rule for rule in self.client.put["rules"] if rule["type"] == "required_status_checks")
        return rule["parameters"]["required_status_checks"]

    def refused(self, argv=("--pr", "o/r#5"), needle=None):
        code, _, err = self.run_main(list(argv))
        self.assertEqual(code, 1, err)
        self.assertIsNone(self.client.put)
        if needle:
            self.assertIn(needle, err)

    def test_forward_patches_only_required_checks(self):
        code, out, err = self.run_main(["--pr", "o/r#5"])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.put_contexts(), [{"context": "Gitleaks", "integration_id": None}, {"context": "ci / gate"}])
        detail = unmanaged_detail("Gitleaks", "Shared template contract")
        self.assertEqual(set(self.client.put), {"name", "target", "enforcement", "conditions", "bypass_actors", "rules"})
        for key in ("name", "target", "enforcement", "conditions", "bypass_actors"):
            self.assertEqual(self.client.put[key], detail[key])
        self.assertEqual(self.client.put["rules"][:2], detail["rules"][:2])
        report = json.loads(out)
        self.assertEqual(report["ruleset"], {"id": UNMANAGED_ID, "name": "main", "managed": False})
        self.assertEqual(report["after"], ["Gitleaks", "ci / gate"])
        self.assertEqual(report["action"], "update")
        self.assertEqual(self.legacy_refs, [BASE])

    def test_integration_binding_of_kept_checks_preserved(self):
        self.client = UnmanagedClient(unmanaged_detail("Gitleaks", "Shared template contract", integration_id=15368))
        code, _, err = self.run_main(["--pr", "o/r#5"])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.put_contexts(), [{"context": "Gitleaks", "integration_id": 15368}, {"context": "ci / gate"}])

    def test_dry_run_writes_nothing(self):
        code, out, err = self.run_main(["--pr", "o/r#5", "--dry-run"])
        self.assertEqual(code, 0, err)
        self.assertIsNone(self.client.put)
        self.assertEqual(json.loads(out)["after"], ["Gitleaks", "ci / gate"])

    def test_revert_restores_legacy_check(self):
        self.policy = policy(entry("Gitleaks", ruleset_id=UNMANAGED_ID), entry("ci / gate", ruleset_id=UNMANAGED_ID))
        self.client = UnmanagedClient(unmanaged_detail("Gitleaks", "ci / gate"))
        code, _, err = self.run_main(["--pr", "o/r#5", "--revert", "--revision", REV, "--restore", "Shared template contract"])
        self.assertEqual(code, 0, err)
        self.assertEqual(self.legacy_refs, [REV])
        self.assertEqual(self.put_contexts(), [{"context": "Gitleaks", "integration_id": None}, {"context": "Shared template contract"}])

    def test_already_applied_is_noop(self):
        self.policy = policy(entry("Gitleaks", ruleset_id=UNMANAGED_ID), entry("ci / gate", ruleset_id=UNMANAGED_ID))
        self.client = UnmanagedClient(unmanaged_detail("Gitleaks", "ci / gate"))
        code, out, err = self.run_main(["--pr", "o/r#5"])
        self.assertEqual(code, 0, err)
        self.assertIsNone(self.client.put)
        self.assertEqual(json.loads(out)["action"], "noop")

    def test_no_ruleset_requires_checks_refused(self):
        self.policy = policy(status="unprotected")
        self.refused(needle="ci-kit/security/o/r")

    def test_several_rulesets_refused(self):
        self.policy = policy(entry("Gitleaks", ruleset_id=UNMANAGED_ID), entry("Shared template contract", ruleset_id=22))
        self.refused(needle="exactly one")

    def test_classic_protection_refused(self):
        self.policy = policy(entry("Gitleaks", ruleset_id=UNMANAGED_ID), entry("Shared template contract", ruleset_id=UNMANAGED_ID),
                             entry("Other", source="classic-protection"))
        self.refused(needle="classic")

    def test_unknown_status_refused(self):
        self.policy = policy(entry("Gitleaks", ruleset_id=UNMANAGED_ID), status="unknown")
        self.refused(needle="unknown")

    def test_organization_ruleset_refused(self):
        self.client = UnmanagedClient(unmanaged_detail("Gitleaks", "Shared template contract", source_type="Organization"))
        self.refused(needle="Organization")

    def test_inactive_ruleset_refused(self):
        self.client = UnmanagedClient(unmanaged_detail("Gitleaks", "Shared template contract", enforcement="evaluate"))
        self.refused(needle="enforcement")

    def test_hidden_bypass_actors_refused(self):
        detail = unmanaged_detail("Gitleaks", "Shared template contract")
        del detail["bypass_actors"]
        self.client = UnmanagedClient(detail)
        self.refused(needle="bypass_actors")

    def test_duplicated_required_checks_rule_refused(self):
        detail = unmanaged_detail("Gitleaks", "Shared template contract")
        detail["rules"].append(status_rule("Extra"))
        self.client = UnmanagedClient(detail)
        self.refused(needle="required_status_checks")

    def test_concurrent_edit_refused(self):
        self.client = UnmanagedClient(unmanaged_detail("Gitleaks", "Shared template contract"),
                                      changed=unmanaged_detail("Gitleaks", "Shared template contract", updated_at="2026-10-01T00:00:01Z"))
        self.refused(needle="changed")

    def test_readback_mismatch_exit_2(self):
        self.client = UnmanagedClient(unmanaged_detail("Gitleaks", "Shared template contract"),
                                      readback=lambda put: {**put, "id": UNMANAGED_ID, "bypass_actors": []})
        code, _, err = self.run_main(["--pr", "o/r#5"])
        self.assertEqual(code, 2)
        self.assertIn("readback", err)

    def test_only_added_checks_must_be_published(self):
        self.client = UnmanagedClient(unmanaged_detail("Gitleaks", "Shared template contract"), published=("ci / gate",))
        code, _, err = self.run_main(["--pr", "o/r#5"])
        self.assertEqual(code, 0, err)
        self.assertIsNotNone(self.client.put)

    def test_missing_ruleset_id_refused(self):
        self.policy = policy(entry("Gitleaks", ruleset_id=None))
        self.refused(needle="no ruleset id")

    def test_duplicated_context_refused(self):
        self.client = UnmanagedClient(unmanaged_detail("Gitleaks", "Gitleaks", "Shared template contract"))
        self.refused(needle="duplicated")

    def test_malformed_readback_exit_2_names_applied_put(self):
        self.client = UnmanagedClient(unmanaged_detail("Gitleaks", "Shared template contract"), readback=lambda put: ["not", "a", "ruleset"])
        code, _, err = self.run_main(["--pr", "o/r#5"])
        self.assertEqual(code, 2)
        self.assertIn("PUT applied", err)

    def test_readback_mismatch_names_applied_put(self):
        self.client = UnmanagedClient(unmanaged_detail("Gitleaks", "Shared template contract"),
                                      readback=lambda put: {**put, "id": UNMANAGED_ID, "bypass_actors": []})
        code, _, err = self.run_main(["--pr", "o/r#5"])
        self.assertEqual(code, 2)
        self.assertIn("PUT applied", err)

    def test_unpublished_new_check_exit_2_without_write(self):
        self.client = UnmanagedClient(unmanaged_detail("Gitleaks", "Shared template contract"), published=("Gitleaks",))
        code, _, err = self.run_main(["--pr", "o/r#5"])
        self.assertEqual(code, 2)
        self.assertIn("ci / gate", err)
        self.assertIsNone(self.client.put)


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

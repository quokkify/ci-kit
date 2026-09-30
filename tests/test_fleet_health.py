from __future__ import annotations

import base64
import copy
from datetime import datetime, timezone
from contextlib import redirect_stdout
from io import StringIO
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from unittest import TestCase, main, mock

ROOT = Path(__file__).resolve().parents[1]

def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

health = load("fleet_health")
fleet = load("update_copier_fleet")
NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)
DEFAULT_SHA = "a" * 40
PR_SHA = "b" * 40


class API:
    def __init__(self):
        self.applied = "v2.0.0"
        self.rule_checks = [{"context": "Validate", "integration_id": 7}]
        self.classic = {"checks": []}
        self.pulls = []
        self.runs = {DEFAULT_SHA: [self.check(DEFAULT_SHA)], PR_SHA: [self.check(PR_SHA)]}
        self.statuses = []
        self.denied = set()
        self.calls = []
        self.pr_stale = False
        self.pr_base_stale = False
        self.default_stale = False
        self.commit_calls = 0

    def check(self, sha, conclusion="success", status="completed", app=7):
        return {"id": 1, "head_sha": sha, "name": "Validate", "status": status, "conclusion": conclusion, "app": {"id": app}, "html_url": "https://github.com/acme/demo/actions/runs/1", "pull_requests": [{"number": 42, "head": {"sha": PR_SHA}, "base": {"sha": DEFAULT_SHA}}]}

    def add_pr(self, state="open", merged_at=None):
        self.pulls = [{"number": 42, "state": state, "merged_at": merged_at, "created_at": "2026-09-20T00:00:00Z", "head": {"ref": "automation/copier-template-update", "sha": PR_SHA, "repo": {"full_name": "acme/demo"}}, "base": {"ref": "main", "sha": DEFAULT_SHA}, "html_url": "untrusted url"}]

    def __call__(self, endpoint):
        self.calls.append(endpoint)
        if any(fragment in endpoint for fragment in self.denied):
            raise RuntimeError("token secret may appear in raw error")
        if endpoint == "repos/acme/demo":
            return {"default_branch": "main"}
        if endpoint == "repos/acme/demo/commits/main":
            self.commit_calls += 1
            return {"sha": PR_SHA if self.default_stale and self.commit_calls > 1 else DEFAULT_SHA}
        if "/contents/.copier-answers.yml?ref=" in endpoint:
            assert f"ref={DEFAULT_SHA}" in endpoint
            return {"encoding": "base64", "content": base64.b64encode(f"_commit: {self.applied}\n".encode()).decode()}
        if "/rules/branches/" in endpoint:
            return [{"type": "required_status_checks", "ruleset_id": 10, "parameters": {"required_status_checks": self.rule_checks}}] if self.rule_checks else []
        if "/protection/required_status_checks" in endpoint:
            return self.classic
        if "/check-runs?" in endpoint:
            sha = endpoint.split("/commits/", 1)[1].split("/", 1)[0]
            return {"check_runs": self.runs[sha]}
        if "/statuses?" in endpoint:
            return self.statuses
        if "/pulls?" in endpoint:
            return self.pulls
        if endpoint == "repos/acme/demo/pulls/42":
            item = copy.deepcopy(self.pulls[0])
            item["head"]["sha"] = DEFAULT_SHA if self.pr_stale else PR_SHA
            if self.pr_base_stale:
                item["base"]["sha"] = PR_SHA
            return item
        raise AssertionError(endpoint)

    def collect(self, exception=None):
        return health.collect_health("acme/demo", "obsolete-branch", "v2.0.0", "automation/copier-template-update", self, exception, NOW)


class HealthTests(TestCase):
    def test_no_pr_current_version_observes_default_exact_revision(self):
        api = API()
        result = api.collect()
        self.assertEqual(result["adoption"], "current")
        self.assertEqual(result["default_sha"], DEFAULT_SHA)
        self.assertEqual(result["default_branch"], "main")
        self.assertEqual(result["default_ci"]["status"], "passed")
        self.assertEqual(result["default_ci"]["checks"][0]["app_id"], 7)
        self.assertIsNone(result["pull_request"])
        self.assertEqual(result["pull_request_observation"]["status"], "observed")

    def test_open_pr_is_not_adoption(self):
        api = API()
        api.applied = "v1.0.0"
        api.add_pr()
        result = api.collect()
        self.assertEqual(result["adoption"], "different")
        self.assertEqual(result["pull_request"]["ci"]["status"], "passed")
        self.assertEqual(result["pull_request"]["head_sha"], PR_SHA)
        self.assertEqual(result["pull_request"]["age_days"], 10)
        self.assertEqual(result["pull_request"]["url"], "https://github.com/acme/demo/pull/42")

    def test_pending_failed_and_passed_pr_heads(self):
        for conclusion, status, expected in ((None, "in_progress", "pending"), ("failure", "completed", "failed"), ("success", "completed", "passed"), ("skipped", "completed", "unknown")):
            with self.subTest(expected=expected):
                api = API()
                api.add_pr()
                api.runs[PR_SHA] = [api.check(PR_SHA, conclusion, status)]
                self.assertEqual(api.collect()["pull_request"]["ci"]["status"], expected)

    def test_wrong_revision_or_app_is_not_green(self):
        for sha, app in ((PR_SHA, 7), (DEFAULT_SHA, 8)):
            api = API()
            api.runs[DEFAULT_SHA] = [api.check(sha, app=app)]
            self.assertEqual(api.collect()["default_ci"]["status"], "unknown")

    def test_stale_default_revision_and_pr_revision_unknown(self):
        api = API()
        api.default_stale = True
        result = api.collect()
        self.assertEqual(result["adoption"], "unknown")
        self.assertEqual(result["default_ci"]["status"], "unknown")
        api = API()
        api.add_pr()
        api.pr_stale = True
        self.assertEqual(api.collect()["pull_request"]["ci"]["status"], "unknown")

    def test_unchanged_pr_head_with_advanced_base_is_unknown(self):
        api = API()
        api.add_pr()
        api.pr_base_stale = True
        self.assertEqual(api.collect()["pull_request"]["ci"]["status"], "unknown")
        api = API()
        api.add_pr()
        api.runs[PR_SHA][0]["pull_requests"][0]["base"]["sha"] = "c" * 40
        self.assertEqual(api.collect()["pull_request"]["ci"]["status"], "unknown")

    def test_pr_with_already_historical_base_cannot_be_green(self):
        api = API()
        api.add_pr()
        historical_base = "c" * 40
        api.pulls[0]["base"]["sha"] = historical_base
        api.runs[PR_SHA][0]["pull_requests"][0]["base"]["sha"] = historical_base
        result = api.collect()
        self.assertEqual(result["default_ci"]["status"], "passed")
        self.assertEqual(result["pull_request"]["base_sha"], historical_base)
        self.assertEqual(result["pull_request"]["ci"]["status"], "unknown")
        self.assertIn("current default revision", result["pull_request"]["ci"]["reason"])

    def test_default_advancing_during_observation_invalidates_pr_health(self):
        api = API()
        api.add_pr()
        api.default_stale = True
        self.assertEqual(api.collect()["pull_request"]["ci"]["status"], "unknown")

    def test_pr_check_without_matching_association_is_unknown(self):
        api = API()
        api.add_pr()
        api.runs[PR_SHA][0]["pull_requests"] = []
        self.assertEqual(api.collect()["pull_request"]["ci"]["status"], "unknown")

    def test_latest_unbound_retry_does_not_reuse_old_green_pr_check(self):
        api = API()
        api.add_pr()
        latest = api.check(PR_SHA)
        latest["id"] = 2
        latest["pull_requests"] = []
        api.runs[PR_SHA].append(latest)
        self.assertEqual(api.collect()["pull_request"]["ci"]["status"], "unknown")

    def test_api_denials_generic_and_never_green(self):
        for endpoint in ("contents", "rules/branches", "check-runs", "protection/required", "pulls?"):
            api = API()
            api.denied.add(endpoint)
            result = api.collect()
            self.assertNotIn("token secret", json.dumps(result))
            if endpoint == "pulls?":
                self.assertEqual(result["pull_request_observation"]["status"], "unknown")
            else:
                self.assertEqual(result["default_ci"]["status"], "unknown")

    def test_classic_denial_preserves_known_active_rules(self):
        api = API()
        api.denied.add("protection/required")
        result = api.collect()
        self.assertEqual(result["required_checks"]["status"], "unknown")
        self.assertEqual(result["required_checks"]["checks"][0]["context"], "Validate")
        self.assertEqual(result["default_ci"]["checks"][0]["status"], "passed")
        self.assertEqual(result["default_ci"]["status"], "unknown")

    def test_evaluate_disabled_and_empty_rules_are_not_active(self):
        api = API()
        # GitHub's effective branch endpoint excludes evaluate/disabled rules.
        # Do not query raw repository rulesets or infer active from workflow files.
        api.rule_checks = []
        result = api.collect()
        self.assertEqual(result["required_checks"]["status"], "unprotected")
        self.assertEqual(result["default_ci"]["status"], "unknown")
        self.assertFalse(any("/rulesets" in endpoint for endpoint in api.calls))

    def test_classic_checks_and_commit_status_provenance(self):
        api = API()
        api.rule_checks = []
        api.classic = {"checks": [{"context": "Legacy", "app_id": -1}]}
        api.statuses = [{"context": "Legacy", "state": "success", "id": 99, "creator": {"login": "trusted-user"}, "target_url": "https://github.com/acme/demo"}]
        result = api.collect()
        self.assertEqual(result["default_ci"]["status"], "passed")
        self.assertEqual(result["default_ci"]["checks"][0]["commit_status_id"], 99)

    def test_expired_exception_does_not_change_health_or_adoption(self):
        api = API()
        api.runs[DEFAULT_SHA] = []
        result = api.collect({"reason": "temporary maintenance", "expires_on": "2026-09-29"})
        self.assertEqual(result["exception"]["status"], "expired")
        self.assertEqual(result["default_ci"]["status"], "unknown")
        self.assertEqual(result["adoption"], "current")

    def test_latest_check_retry_is_authoritative(self):
        api = API()
        passed = api.check(DEFAULT_SHA)
        failed = api.check(DEFAULT_SHA, "failure")
        failed["id"] = 2
        api.runs[DEFAULT_SHA] = [passed, failed]
        self.assertEqual(api.collect()["default_ci"]["status"], "failed")

    def test_missing_external_codeql_check_is_unknown(self):
        api = API()
        api.rule_checks.append({"context": "github-advanced-security", "integration_id": 12})
        self.assertEqual(api.collect()["default_ci"]["status"], "unknown")

    def test_exceptions_are_strict_and_public_config_empty(self):
        self.assertEqual(health.load_exceptions(), {})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "exceptions.json"
            path.write_text(json.dumps({"schema_version": 1, "repositories": {"acme/demo": {"reason": "", "expires_on": "2026-09-31"}}}))
            with self.assertRaises(ValueError):
                health.load_exceptions(path)

    def test_health_is_opt_in_and_cli_adapter_only_reads(self):
        api = API()
        inventory = fleet.TemplateInventory("v2.0.0", None, (), "2/2", (), "disabled", "disabled", "disabled", "disabled", "disabled")
        result = fleet.Result("acme/demo", "up-to-date", inventory=inventory)
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "report.json"
            def read_api(arguments, *, env):
                self.assertEqual(arguments[:3], ["api", "--method", "GET"])
                return api(arguments[3])
            with mock.patch.dict("os.environ", {"GH_TOKEN": "test-token"}), mock.patch.object(fleet.shutil, "which", return_value="tool"), mock.patch.object(fleet, "discover_repositories", return_value=[fleet.Repository("acme/demo", "main")]), mock.patch.object(fleet, "resolve_template_ref", return_value="v2.0.0"), mock.patch.object(fleet, "process_repository", return_value=result), mock.patch.object(fleet, "gh_json", side_effect=read_api) as calls, redirect_stdout(StringIO()):
                self.assertEqual(fleet.main(["--dry-run", "--json-report", str(report)]), 0)
                calls.assert_not_called()
                self.assertNotIn("health", json.loads(report.read_text())["repositories"][0])
                self.assertEqual(fleet.main(["--dry-run", "--include-health", "--json-report", str(report)]), 0)
                self.assertTrue(calls.called)
                self.assertEqual(json.loads(report.read_text())["repositories"][0]["health"]["adoption"], "current")

    def test_reports_preserve_old_fields_and_sanitize_health_markdown(self):
        api = API()
        api.add_pr()
        result = api.collect({"reason": "| injected\n# <script>`[evil](url)", "expires_on": "2026-10-01"})
        item = fleet.Result("acme/demo", "pull-request", "PR created", health=result)
        parsed = json.loads(fleet.json_report([item], {"pull-request": 1}))
        self.assertEqual(parsed["schema_version"], 4)
        self.assertEqual(parsed["repositories"][0]["status"], "pull-request")
        self.assertEqual(parsed["repositories"][0]["health"], result)
        markdown = fleet.markdown_report([item], {"pull-request": 1})
        self.assertIn("Copier fleet audit", markdown)
        self.assertIn("Adoption and branch health", markdown)
        self.assertNotIn("<script>", markdown)
        self.assertNotIn("\n# <script>", markdown)
        self.assertIn("\\| injected", markdown)
        self.assertIn("&lt;script&gt;", markdown)


if __name__ == "__main__":
    main()

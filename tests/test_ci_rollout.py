from __future__ import annotations

import base64
from contextlib import ExitStack
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rollout_copier_fleet as rollout
import verify_ci_gate as gate

SHA = "a" * 40
GITHUB_RUN_VARIABLES = ("GITHUB_EVENT_NAME", "GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")
OTHER = "b" * 40
REPO = "quokkify/skills"


def workflow(sha=SHA, outcome="success", status="completed", identifier=1):
    return {"id": identifier, "head_sha": sha, "head_branch": "main", "event": "push",
            "status": status, "conclusion": outcome}


def check(name, sha=SHA, outcome="success", status="completed", app="github-actions"):
    return {"id": 1, "head_sha": sha, "name": name, "status": status,
            "conclusion": outcome, "app": {"slug": app}}


LEGACY_VALIDATE = (b"jobs:\n  template-contract:\n    name: Shared template contract\n"
                   b"  app:\n    name: App Python\n    uses: quokkify/ci-kit/.github/workflows/python-ci.yml@v1.2.3\n")


def module_only_api(security=b"codeql: true\ngitleaks: true\n"):
    def read(endpoint, *, missing_ok=False):
        if endpoint.endswith(f".copier-answers.yml?ref={SHA}"):
            assert missing_ok
            return None
        if "/.copier-answers.security.yml?" in endpoint:
            content = security
        elif "/.copier-answers.core.yml?" in endpoint:
            content = b"_commit: v1.2.3\n_src_path: gh:quokkify/ci-kit\n"
        else:
            raise AssertionError(endpoint)
        return {"content": base64.b64encode(content).decode()}
    return read


def pilot_api(answers=b"", validate=LEGACY_VALIDATE):
    def read(endpoint, *, missing_ok=False):
        content = validate if "/validate.yml?" in endpoint else b"_commit: v1.2.3\n_src_path: gh:quokkify/ci-kit\n" + answers
        return {"content": base64.b64encode(content).decode()}
    return read


class ReleaseGateTests(unittest.TestCase):
    def test_success_is_exact_sha_and_both_workflows(self):
        with patch.object(gate, "pages", return_value=[workflow()]) as reads:
            self.assertTrue(gate.release_ready(REPO, SHA))
            self.assertEqual(reads.call_count, 2)
            self.assertIn("head_sha=" + SHA, reads.call_args.args[0])

    def test_absent_wrong_sha_and_pending_do_not_pass(self):
        for runs in ([], [workflow(OTHER)], [workflow(status="in_progress")]):
            with self.subTest(runs=runs), patch.object(gate, "pages", return_value=runs):
                self.assertFalse(gate.release_ready(REPO, SHA))

    def test_latest_pending_run_supersedes_older_success(self):
        with patch.object(gate, "pages", return_value=[workflow(), workflow(status="queued", identifier=2)]):
            self.assertFalse(gate.release_ready(REPO, SHA))

    def test_failure_and_action_required_fail_closed(self):
        for outcome in ("failure", "cancelled", "skipped", "action_required"):
            with self.subTest(outcome=outcome), patch.object(gate, "pages", return_value=[workflow(outcome=outcome)]):
                with self.assertRaises(gate.GateError):
                    gate.release_ready(REPO, SHA)

    def test_api_error_and_empty_checks_timeout(self):
        with patch.object(gate.subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["gh"])):
            with self.assertRaises(gate.GateError):
                gate.api("repos/example")
        with self.assertRaises(gate.GateError):
            gate.wait_until(lambda: False, timeout=0)

    def test_preflight_verifies_merged_pending_candidate(self):
        candidate = {"merged": True, "base": {"ref": "main"}, "merge_commit_sha": OTHER}
        with patch.object(gate, "api", side_effect=[{"sha": SHA}, [], candidate, {"sha": SHA}]), \
             patch.object(gate, "pages", return_value=[{"number": 9, "pull_request": {}}]) as pages, \
             patch.object(gate, "release_ready", return_value=True) as ready:
            self.assertTrue(gate.preflight_ready(REPO, SHA))
            self.assertEqual([call.args for call in ready.call_args_list], [(REPO, OTHER)])
            self.assertIn("labels=autorelease%3A%20pending", pages.call_args.args[0])

    def test_bad_candidate_and_advanced_main_stop(self):
        with patch.object(gate, "api", return_value={"sha": OTHER}):
            with self.assertRaises(gate.GateError):
                gate.preflight_ready(REPO, SHA)

    def test_candidate_ci_failure_and_late_main_advance_stop(self):
        candidate = {"merged": True, "base": {"ref": "main"}, "merge_commit_sha": OTHER}
        with patch.object(gate, "api", side_effect=[{"sha": SHA}, [], candidate]), \
             patch.object(gate, "pages", return_value=[{"number": 9, "pull_request": {}}]), \
             patch.object(gate, "release_ready", return_value=False):
            self.assertFalse(gate.preflight_ready(REPO, SHA))
        with patch.object(gate, "api", side_effect=[{"sha": SHA}, [], {"sha": OTHER}]), \
             patch.object(gate, "pages", return_value=[]), \
             patch.object(gate, "release_ready", return_value=True):
            with self.assertRaises(gate.GateError):
                gate.preflight_ready(REPO, SHA)

    def test_ordinary_push_without_candidates_does_not_wait(self):
        with patch.object(gate, "api", side_effect=[{"sha": SHA}, [], {"sha": SHA}]), \
             patch.object(gate, "pages", return_value=[]), \
             patch.object(gate, "release_ready") as ready:
            self.assertTrue(gate.preflight_ready(REPO, SHA))
            ready.assert_not_called()
        ordinary = [{"merged_at": "2026-01-01T00:00:00Z", "head": {"ref": "feature/x"}}]
        with patch.object(gate, "api", side_effect=[{"sha": SHA}, ordinary, {"sha": SHA}]), \
             patch.object(gate, "pages", return_value=[]), \
             patch.object(gate, "release_ready") as ready:
            self.assertTrue(gate.preflight_ready(REPO, SHA))
            ready.assert_not_called()

    def test_release_merge_trigger_without_candidates_is_waited(self):
        release = [{"merged_at": "2026-01-01T00:00:00Z", "head": {"ref": "release-please--branches--main"}}]
        for ready_result in (True, False):
            with self.subTest(ready=ready_result), \
                 patch.object(gate, "api", side_effect=[{"sha": SHA}, release, {"sha": SHA}]), \
                 patch.object(gate, "pages", return_value=[]), \
                 patch.object(gate, "release_ready", return_value=ready_result) as ready:
                self.assertEqual(gate.preflight_ready(REPO, SHA), ready_result)
                ready.assert_called_once_with(REPO, SHA)

    def test_unmerged_release_pull_on_trigger_does_not_wait(self):
        release = [{"merged_at": None, "head": {"ref": "release-please--branches--main"}}]
        with patch.object(gate, "api", side_effect=[{"sha": SHA}, release, {"sha": SHA}]), \
             patch.object(gate, "pages", return_value=[]), \
             patch.object(gate, "release_ready") as ready:
            self.assertTrue(gate.preflight_ready(REPO, SHA))
            ready.assert_not_called()

    def test_trigger_that_is_the_candidate_is_waited_once(self):
        candidate = {"merged": True, "base": {"ref": "main"}, "merge_commit_sha": SHA}
        release = [{"merged_at": "2026-01-01T00:00:00Z", "head": {"ref": "release-please--branches--main"}}]
        for trigger_pulls in ([], release):
            with self.subTest(trigger_pulls=trigger_pulls), \
                 patch.object(gate, "api", side_effect=[{"sha": SHA}, trigger_pulls, candidate, {"sha": SHA}]), \
                 patch.object(gate, "pages", return_value=[{"number": 9, "pull_request": {}}]), \
                 patch.object(gate, "release_ready", return_value=True) as ready:
                self.assertTrue(gate.preflight_ready(REPO, SHA))
                ready.assert_called_once_with(REPO, SHA)

    def test_post_tag_checks_the_resolved_commit(self):
        with patch.object(sys, "argv", ["gate", "--repository", REPO, "--ref", "v1.2.3"]), \
             patch.object(gate, "api", return_value={"sha": OTHER}), \
             patch.object(gate, "release_ready", return_value=True) as ready:
            self.assertEqual(gate.main(), 0)
            self.assertEqual(ready.call_args.args, (REPO, OTHER))
        with patch.object(gate, "api", side_effect=[{"sha": SHA}, [], {"merged": True, "base": {"ref": "main"}, "merge_commit_sha": "bad"}]), \
             patch.object(gate, "pages", return_value=[{"number": 9, "pull_request": {}}]), \
             patch.object(gate, "release_ready", return_value=True):
            with self.assertRaises(gate.GateError):
                gate.preflight_ready(REPO, SHA)


class PilotGateTests(unittest.TestCase):
    def test_absent_wrong_sha_spoofed_and_pending_checks_block(self):
        for checks in ([], [check("Tests", OTHER)], [check("Tests", app="third-party")], [check("Tests", status="in_progress")]):
            with self.subTest(checks=checks), patch.object(gate, "pages", side_effect=[checks, []]), \
                 patch.object(gate, "workflows_ready", return_value=True):
                self.assertFalse(gate.pilot_ready(REPO, SHA, ("Tests",)))

    def test_failure_or_required_skipped_stops(self):
        for outcome in ("failure", "skipped", "action_required"):
            with self.subTest(outcome=outcome), patch.object(gate, "pages", side_effect=[[check("Tests", outcome=outcome)], []]):
                with self.assertRaises(gate.GateError):
                    gate.pilot_ready(REPO, SHA, ("Tests",))

    def test_status_cannot_spoof_actions_context(self):
        with patch.object(gate, "pages", side_effect=[[], [{"id": 1, "context": "Tests", "state": "success"}]]), \
             patch.object(gate, "workflows_ready", return_value=True):
            self.assertFalse(gate.pilot_ready(REPO, SHA, ("Tests",)))

    def test_codeql_aggregate_failure_blocks(self):
        with patch.object(gate, "pages", side_effect=[[check("Tests"), check("CodeQL", app="github-advanced-security", outcome="failure")], []]):
            with self.assertRaises(gate.GateError):
                gate.pilot_ready(REPO, SHA, ("Tests",))

    def test_no_change_verifies_version_and_freshness(self):
        with patch.object(gate, "current_snapshot", side_effect=[(SHA, "", ""), (OTHER, "", "")]), \
             patch.object(gate, "api", side_effect=pilot_api()), patch.object(gate, "wait_until"):
            with self.assertRaisesRegex(gate.GateError, "changed while checking"):
                gate.verify_pilot(REPO, None, "v1.2.3", 0)
        with patch.object(gate, "current_snapshot", return_value=(SHA, "", "")), patch.object(gate, "api", side_effect=pilot_api()):
            with self.assertRaisesRegex(gate.GateError, "is not on v2.0.0"):
                gate.verify_pilot(REPO, None, "v2.0.0", 0)

    def test_default_branch_pilot_skips_pull_request_only_checks(self):
        for pr, expected in ((None, False), (7, True)):
            with self.subTest(pr=pr), patch.object(gate, "current_snapshot", return_value=(SHA, OTHER, SHA)), \
                 patch.object(gate, "api", side_effect=pilot_api()), \
                 patch.object(gate, "wait_until", side_effect=lambda check, timeout: check()), \
                 patch.object(gate, "pilot_ready", return_value=True) as ready:
                gate.verify_pilot("quokkify/q4j", pr, "v1.2.3", 0)
                contexts = ready.call_args.args[2]
                self.assertEqual("Tests pipeline complete" in contexts, expected)
                self.assertIn("Shared template contract", contexts)

    def test_pilot_contexts_join_own_and_template_checks(self):
        self.assertEqual(gate.pilot_contexts("quokkify/skills", 7, ("ci / gate",)), ("gitleaks", "ci / gate"))
        self.assertEqual(gate.pilot_contexts("quokkify/q4j", 7, ("ci / gate",)),
                         ("Scan current tree (legacy history pending baseline)", "Tests pipeline complete", "ci / gate"))
        self.assertEqual(gate.pilot_contexts("quokkify/q4j", None, ("ci / gate",)),
                         ("Scan current tree (legacy history pending baseline)", "ci / gate"))

    def test_handed_off_validate_expects_checks_after_without_reading_validate(self):
        with patch.object(gate, "api", side_effect=AssertionError("validate.yml must not be read")):
            self.assertEqual(gate.template_contexts(REPO, SHA, ("validate",)), ("ci / gate",))

    def test_legacy_template_contexts_come_from_validate_jobs(self):
        # A pilot with components needs no per-context mapping: the contexts are read from its validate.yml.
        for handed_off in ((), ("other",)):
            with self.subTest(handed_off=handed_off), patch.object(gate, "api", side_effect=pilot_api()) as read:
                self.assertEqual(gate.template_contexts(REPO, SHA, handed_off), ("Shared template contract", "App Python / ci"))
                self.assertIn(f"/contents/.github/workflows/validate.yml?ref={SHA}", read.call_args.args[0])

    def test_template_contexts_for_module_repo(self):
        for security, expected in ((b"codeql: true\ngitleaks: true\n", {"ci / gate", "CodeQL", "gitleaks"}),
                                   (b"codeql: false\ngitleaks: true\n", {"ci / gate", "gitleaks"}),
                                   (b"codeql: true\ngitleaks: false\n", {"ci / gate", "CodeQL"})):
            with self.subTest(security=security), patch.object(gate, "api", side_effect=module_only_api(security)):
                self.assertEqual(set(gate.template_contexts(REPO, SHA, (), modules_only=True)), expected)

    def test_pilot_without_legacy_uses_core_answers(self):
        with patch.object(gate, "current_snapshot", return_value=(SHA, OTHER, SHA)), \
             patch.object(gate, "api", side_effect=module_only_api()), \
             patch.object(gate, "wait_until", side_effect=lambda check, timeout: check()), \
             patch.object(gate, "pilot_ready", return_value=True) as ready:
            gate.verify_pilot("quokkify/skills", 7, "v1.2.3", 0)
            self.assertEqual(ready.call_args.args[2], ("gitleaks", "ci / gate", "CodeQL"))
        with patch.object(gate, "current_snapshot", return_value=(SHA, OTHER, SHA)), \
             patch.object(gate, "api", side_effect=module_only_api()):
            with self.assertRaisesRegex(gate.GateError, "is not on v2.0.0"):
                gate.verify_pilot("quokkify/skills", 7, "v2.0.0", 0)

    def test_api_returns_none_only_for_a_confirmed_404(self):
        def failing(stderr):
            return subprocess.CalledProcessError(1, ["gh"], stderr=stderr)
        with patch.object(gate.subprocess, "run", side_effect=failing("gh: Not Found (HTTP 404)")):
            self.assertIsNone(gate.api("repos/a/b/contents/x", missing_ok=True))
            with self.assertRaises(gate.GateError):
                gate.api("repos/a/b/contents/x")
        for error in (failing("gh: Bad Gateway (HTTP 502)"), subprocess.TimeoutExpired(["gh"], 45)):
            with self.subTest(error=error), patch.object(gate.subprocess, "run", side_effect=error):
                with self.assertRaises(gate.GateError):
                    gate.api("repos/a/b/contents/x", missing_ok=True)

    def test_transient_error_on_legacy_or_security_read_fails_closed(self):
        def broken(path):
            def read(endpoint, *, missing_ok=False):
                if path in endpoint:
                    raise gate.GateError("HTTP 502")
                return module_only_api()(endpoint, missing_ok=missing_ok)
            return read
        with patch.object(gate, "current_snapshot", return_value=(SHA, OTHER, SHA)), \
             patch.object(gate, "api", side_effect=broken(".copier-answers.yml?")):
            with self.assertRaises(gate.GateError):
                gate.verify_pilot("quokkify/skills", 7, "v1.2.3", 0)
        with patch.object(gate, "api", side_effect=broken(".copier-answers.security.yml?")):
            with self.assertRaises(gate.GateError):
                gate.template_contexts(REPO, SHA, (), modules_only=True)
        with patch.object(gate, "api", side_effect=module_only_api(b"- not\n- a mapping\n")):
            with self.assertRaisesRegex(gate.GateError, "security answers"):
                gate.template_contexts(REPO, SHA, (), modules_only=True)

    def test_explicit_contexts_skip_template_derivation(self):
        with patch.object(gate, "current_snapshot", return_value=(SHA, OTHER, SHA)), \
             patch.object(gate, "api", side_effect=pilot_api(validate=b"not: [valid")), \
             patch.object(gate, "template_contexts", side_effect=AssertionError("must not derive")), \
             patch.object(gate, "wait_until", side_effect=lambda check, timeout: check()), \
             patch.object(gate, "pilot_ready", return_value=True) as ready:
            gate.verify_pilot("quokkify/skills", 7, "v1.2.3", 0, contexts=("ci / gate",))
            self.assertEqual(ready.call_args.args[2], ("ci / gate",))

    def test_unusable_legacy_validate_stops_the_gate(self):
        for validate in (b"jobs:\n  a:\n    name: Alpha\n", b"on: push\n", b"jobs: [\n", b"\xff"):
            with self.subTest(validate=validate), patch.object(gate, "api", side_effect=pilot_api(validate=validate)):
                with self.assertRaisesRegex(gate.GateError, "legacy check contexts"):
                    gate.template_contexts(REPO, SHA, ())

    def test_verify_pilot_expects_gate_after_handoff(self):
        for answers, expected in ((b"handed_off: [validate]\n", "ci / gate"), (b"handed_off: oops\n", "Shared template contract"),
                                  (b"", "Shared template contract")):
            with self.subTest(answers=answers), patch.object(gate, "current_snapshot", return_value=(SHA, OTHER, SHA)), \
                 patch.object(gate, "api", side_effect=pilot_api(answers)), \
                 patch.object(gate, "wait_until", side_effect=lambda check, timeout: check()), \
                 patch.object(gate, "pilot_ready", return_value=True) as ready:
                gate.verify_pilot("quokkify/gh-pages-subdir-action", 7, "v1.2.3", 0)
                self.assertIn(expected, ready.call_args.args[2])
                self.assertEqual("Shared template contract" in ready.call_args.args[2], expected != "ci / gate")

    def test_private_repository_stops(self):
        with patch.object(gate, "api", return_value={"private": True, "visibility": "private"}):
            with self.assertRaises(gate.GateError):
                gate.current_revision(REPO, None)

    def test_unchanged_head_with_advanced_base_blocks(self):
        snapshot = (SHA, OTHER, "c" * 40)
        with patch.object(gate, "current_snapshot", return_value=(SHA, "d" * 40, "e" * 40)):
            with self.assertRaises(gate.GateError):
                gate.pilot_ready(REPO, SHA, ("Tests",), pr=9, snapshot=snapshot)

    def test_old_base_check_and_codeql_association_block(self):
        snapshot = (SHA, OTHER, "c" * 40)
        association = {"number": 9, "head": {"sha": SHA}, "base": {"sha": "d" * 40}}
        stale_check = {**check("Tests"), "pull_requests": [association]}
        with patch.object(gate, "current_snapshot", return_value=snapshot), \
             patch.object(gate, "pages", side_effect=[[stale_check], []]):
            with self.assertRaises(gate.GateError):
                gate.pilot_ready(REPO, SHA, ("Tests",), pr=9, snapshot=snapshot)
        stale_workflow = {**workflow(), "pull_requests": [association]}
        with patch.object(gate, "pages", return_value=[stale_workflow]):
            with self.assertRaises(gate.GateError):
                gate.workflows_ready(REPO, SHA, ("codeql.yml",), pr=9, snapshot=snapshot)

    def test_null_merge_revision_cannot_pass(self):
        metadata = {"private": False, "visibility": "public", "default_branch": "main"}
        pull = {"state": "open", "base": {"ref": "main", "sha": OTHER},
                "head": {"ref": "automation/copier-template-update", "sha": SHA,
                         "repo": {"full_name": REPO}}, "merge_commit_sha": None}
        with patch.object(gate, "api", side_effect=[metadata, pull]):
            with self.assertRaises(gate.GateError):
                gate.current_snapshot(REPO, 9)

    def test_initially_stale_base_cannot_pass_historical_ci(self):
        metadata = {"private": False, "visibility": "public", "default_branch": "main"}
        pull = {"state": "open", "base": {"ref": "main", "sha": OTHER},
                "head": {"ref": "automation/copier-template-update", "sha": SHA,
                         "repo": {"full_name": REPO}}, "merge_commit_sha": "c" * 40}
        with patch.object(gate, "api", side_effect=[metadata, pull, {"sha": "d" * 40}]):
            with self.assertRaisesRegex(gate.GateError, "behind the current target branch"):
                gate.current_snapshot(REPO, 9)
        with patch.object(gate, "api", side_effect=[metadata, pull, {"sha": OTHER}]):
            self.assertEqual(gate.current_snapshot(REPO, 9), (SHA, OTHER, "c" * 40))

    def test_current_head_base_check_and_codeql_association_pass(self):
        snapshot = (SHA, OTHER, "c" * 40)
        association = {"number": 9, "head": {"sha": SHA}, "base": {"sha": OTHER}}
        checks = [{**check("Tests"), "pull_requests": [association]}]
        with patch.object(gate, "current_snapshot", return_value=snapshot), \
             patch.object(gate, "pages", side_effect=[checks, [], [{**workflow(), "pull_requests": [association]}]]):
            self.assertTrue(gate.pilot_ready(REPO, SHA, ("Tests",), pr=9, snapshot=snapshot))

    def test_latest_current_base_rerun_supersedes_old_base_check(self):
        snapshot = (SHA, OTHER, "c" * 40)
        old = {"number": 9, "head": {"sha": SHA}, "base": {"sha": "d" * 40}}
        current = {"number": 9, "head": {"sha": SHA}, "base": {"sha": OTHER}}
        checks = [{**check("Tests"), "pull_requests": [old]},
                  {**check("Tests"), "id": 2, "pull_requests": [current]}]
        with patch.object(gate, "current_snapshot", return_value=snapshot), \
             patch.object(gate, "pages", side_effect=[checks, [], [{**workflow(), "pull_requests": [current]}]]):
            self.assertTrue(gate.pilot_ready(REPO, SHA, ("Tests",), pr=9, snapshot=snapshot))


class RolloutTests(unittest.TestCase):
    def test_audit_and_targeted_updates_never_touch_pilots(self):
        for arguments in (["--dry-run"], ["--write", "--repo", "quokkify/q4j"]):
            with self.subTest(arguments=arguments), patch.object(rollout.subprocess, "run") as run, \
                 patch.object(rollout, "verify_pilot") as verify:
                run.return_value.returncode = 0
                rollout.rollout(arguments)
                self.assertFalse(verify.called)
                self.assertEqual(run.call_args.args[0][-len(arguments):], arguments)

    def test_auto_merge_is_forwarded_to_every_stage(self):
        calls = []
        events = []

        def update(arguments, report):
            calls.append(arguments)
            events.append("update")
            repository = arguments[arguments.index("--repo") + 1] if "--repo" in arguments else "remaining"
            return {"schema_version": 4, "configuration_gaps": 1,
                    "repositories": [{"repository": repository, "status": "up-to-date", "detail": "", "health": {"adopted": True}}]}

        def verify(repository, pr, target, timeout, expected_snapshot=None):
            events.append("verify")
            return SHA

        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(rollout.fleet, "resolve_template_ref", return_value="v1.2.3"), \
             patch.object(rollout.fleet, "resolve_template_commit", return_value=SHA), \
             patch.object(rollout, "wait_until"), \
             patch.object(rollout, "run_update", side_effect=update), \
             patch.object(rollout, "verify_pilot", side_effect=verify), \
             patch.object(rollout, "current_snapshot", return_value=(SHA, "", "")), \
             patch.dict(rollout.os.environ, dict.fromkeys(GITHUB_RUN_VARIABLES, "")):
            rollout.rollout(["--write", "--public-only", "--auto-merge",
                             "--json-report", str(Path(temporary) / "report.json"),
                             "--markdown-report", str(Path(temporary) / "report.md")])
        self.assertGreater(len(calls), 1)
        for arguments in calls:
            self.assertIn("--auto-merge", arguments)
        self.assertIn("verify", events)
        self.assertLess(events.index("verify"), len(events) - 1)
        self.assertEqual(events[-1], "update")

    def test_merge_ready_bypasses_the_pilot_gate(self):
        arguments = ["--write", "--public-only", "--auto-merge", "--merge-ready"]
        with patch.object(rollout.subprocess, "run") as run, \
             patch.object(rollout, "verify_pilot") as verify, \
             patch.object(rollout.fleet, "resolve_template_ref") as resolve:
            run.return_value.returncode = 0
            rollout.rollout(arguments)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][-len(arguments):], arguments)
        self.assertFalse(verify.called)
        self.assertFalse(resolve.called)

    def test_pilots_precede_fanout_and_recheck_before_fanout(self):
        events = []

        def update(arguments, report):
            repository = arguments[arguments.index("--repo") + 1] if "--repo" in arguments else "remaining"
            events.append(("update", repository))
            return {"schema_version": 4, "configuration_gaps": 1,
                    "repositories": [{"repository": repository, "status": "up-to-date", "detail": "", "health": {"adopted": True}}]}

        timeouts = {}

        def verify(repository, pr, target, timeout, expected_snapshot=None):
            events.append(("verify", repository))
            timeouts.setdefault(repository, timeout)
            return SHA

        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(rollout.fleet, "resolve_template_ref", return_value="v1.2.3"), \
             patch.object(rollout.fleet, "resolve_template_commit", return_value=SHA), \
             patch.object(rollout, "wait_until"), \
             patch.object(rollout, "run_update", side_effect=update), \
             patch.object(rollout, "verify_pilot", side_effect=verify), \
             patch.object(rollout, "current_snapshot", return_value=(SHA, "", "")), \
             patch.dict(rollout.os.environ, dict.fromkeys(GITHUB_RUN_VARIABLES, "")):
            destination = Path(temporary) / "report.json"
            markdown = Path(temporary) / "report.md"
            rollout.rollout(["--write", "--public-only", "--json-report", str(destination), "--markdown-report", str(markdown)])
            self.assertEqual(events[-1], ("update", "remaining"))
            summary = markdown.read_text()
            self.assertEqual(summary.count("## Copier fleet:"), 1)
            self.assertTrue(summary.startswith("## Copier fleet: ✅ nothing to do\n"))
            self.assertIn("Write mode · ", summary)
            self.assertIn("target v1.2.3 · pilots verified: ", summary)
            self.assertEqual(sum(event[0] == "verify" for event in events), 6)
            self.assertEqual(timeouts, {repository: 2700 if repository == "quokkify/q4j" else 900
                                        for repository in gate.PILOT_CHECKS})
            self.assertTrue(json.loads(destination.read_text())["rollout"]["fanout_started"])
            combined = json.loads(destination.read_text())
            self.assertEqual(combined["schema_version"], 6)
            self.assertEqual(combined["configuration_gaps"], 4)
            self.assertEqual(combined["repositories"][0]["health"], {"adopted": True})

    def rollout_patches(self, temporary, update):
        stack = ExitStack()
        stack.enter_context(patch.object(rollout.fleet, "resolve_template_ref", return_value="v1.2.3"))
        stack.enter_context(patch.object(rollout.fleet, "resolve_template_commit", return_value=SHA))
        stack.enter_context(patch.object(rollout, "wait_until"))
        stack.enter_context(patch.object(rollout, "run_update", side_effect=update))
        stack.enter_context(patch.object(rollout, "verify_pilot", return_value=SHA))
        stack.enter_context(patch.object(rollout, "current_snapshot", return_value=(SHA, "", "")))
        stack.enter_context(patch.dict(rollout.os.environ, dict.fromkeys(GITHUB_RUN_VARIABLES, "")))
        return stack

    def rollout_arguments(self, temporary):
        return ["--write", "--public-only", "--json-report", str(Path(temporary) / "report.json"),
                "--markdown-report", str(Path(temporary) / "report.md")]

    def test_fanout_failure_is_a_failure_row_not_a_stopped_rollout(self):
        def update(arguments, report):
            if "--repo" in arguments:
                return {"repositories": [{"repository": arguments[arguments.index("--repo") + 1], "status": "up-to-date", "detail": ""}]}
            return {"_exit_code": 1, "repositories": [{"repository": "quokkify/other", "status": "failed", "detail": "HTTP 502"}]}

        with tempfile.TemporaryDirectory() as temporary, self.rollout_patches(temporary, update):
            with self.assertRaises(gate.GateError):
                rollout.rollout(self.rollout_arguments(temporary))
            combined = json.loads((Path(temporary) / "report.json").read_text())
            self.assertIsNone(combined["rollout"]["stopped"])
            self.assertTrue(combined["rollout"]["fanout_started"])
            summary = (Path(temporary) / "report.md").read_text()
            self.assertTrue(summary.startswith("## Copier fleet: ❌ action required — 1 failed\n"))
            self.assertNotIn("Rollout stopped", summary)

    def test_fanout_exclusion_of_pilots_does_not_duplicate_them(self):
        def update(arguments, report):
            if "--repo" in arguments:
                repository = arguments[arguments.index("--repo") + 1]
                return {"summary": {"pull-request": 1}, "repositories": [
                    {"repository": repository, "status": "pull-request", "detail": f"https://github.com/{repository}/pull/5",
                     "health": {"pull_request": {"url": f"https://github.com/{repository}/pull/5", "number": 5, "state": "open",
                                                 "head_sha": SHA, "age_days": 0, "ci": {"status": "unknown"}}}}]}
            return {"summary": {"excluded": len(gate.PILOT_CHECKS) + 1, "up-to-date": 1}, "repositories": [
                *({"repository": pilot, "status": "excluded", "detail": ""} for pilot in gate.PILOT_CHECKS),
                {"repository": "quokkify/ci-kit", "status": "excluded", "detail": ""},
                {"repository": "quokkify/other", "status": "up-to-date", "detail": ""}]}

        with tempfile.TemporaryDirectory() as temporary, self.rollout_patches(temporary, update):
            rollout.rollout(self.rollout_arguments(temporary))
            combined = json.loads((Path(temporary) / "report.json").read_text())
            self.assertEqual([(item["repository"], item["status"]) for item in combined["repositories"]],
                             [*((pilot, "pull-request") for pilot in gate.PILOT_CHECKS),
                              ("quokkify/ci-kit", "excluded"), ("quokkify/other", "up-to-date")])
            self.assertEqual(combined["summary"], {"pull-request": len(gate.PILOT_CHECKS), "excluded": 1, "up-to-date": 1})
            summary = (Path(temporary) / "report.md").read_text()
            self.assertIn(" · 1 excluded · ", summary)
            self.assertIn(f"{len(gate.PILOT_CHECKS)} ready to merge (", summary)
            self.assertIn("· CI ✅ · pilot gate |", summary)
            excluded = summary.split("| Excluded | Why |", 1)[1]
            self.assertIn("quokkify/ci-kit", excluded)
            self.assertFalse(any(pilot in excluded for pilot in gate.PILOT_CHECKS))

    def test_render_failure_writes_fallback_and_keeps_the_original_error(self):
        with tempfile.TemporaryDirectory() as temporary, \
             self.rollout_patches(temporary, gate.GateError("pilot exploded")), \
             patch.object(rollout.fleet, "markdown_report", side_effect=RuntimeError("render")):
            with self.assertRaisesRegex(gate.GateError, "pilot exploded"):
                rollout.rollout(self.rollout_arguments(temporary))
            summary = (Path(temporary) / "report.md").read_text()
            self.assertTrue(summary.startswith("## Copier fleet: ❌ action required — rollout stopped\n"))
            self.assertIn("(RuntimeError)", summary)

    def test_keyboard_interrupt_is_recorded_and_reraised(self):
        with tempfile.TemporaryDirectory() as temporary, self.rollout_patches(temporary, KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                rollout.rollout(self.rollout_arguments(temporary))
            self.assertEqual(json.loads((Path(temporary) / "report.json").read_text())["rollout"]["stopped"], "KeyboardInterrupt")

    def test_stopped_rollout_without_stage_report_still_renders_one_verdict(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(rollout.fleet, "resolve_template_ref", return_value="v1.2.3"), \
             patch.object(rollout.fleet, "resolve_template_commit", return_value=SHA), \
             patch.object(rollout, "wait_until"), \
             patch.object(rollout, "run_update", side_effect=gate.GateError(f"Fleet updater stopped with exit code 2; see {temporary}/toolkit-canary-x/pilot-0.json")):
            destination = Path(temporary) / "report.json"
            markdown = Path(temporary) / "report.md"
            with self.assertRaises(gate.GateError):
                rollout.rollout(["--write", "--public-only", "--json-report", str(destination), "--markdown-report", str(markdown)])
            combined = json.loads(destination.read_text())
            self.assertEqual((combined["schema_version"], combined["repositories"]), (6, []))
            self.assertEqual(combined["rollout"]["stopped"], "Fleet updater stopped with exit code 2; see the stage report")
            summary = markdown.read_text()
            self.assertEqual(summary.count("## Copier fleet:"), 1)
            self.assertTrue(summary.startswith("## Copier fleet: ❌ action required — rollout stopped\n\nNo managed repositories were processed. **Rollout stopped:**"))

    def test_pilot_failure_stops_before_fanout_and_writes_partial_report(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(rollout.fleet, "resolve_template_ref", return_value="v1.2.3"), \
             patch.object(rollout.fleet, "resolve_template_commit", return_value=SHA), \
             patch.object(rollout, "wait_until"), \
             patch.object(rollout, "current_snapshot", return_value=(SHA, "", "")), \
             patch.object(rollout, "run_update", return_value={"repositories": [{"repository": next(iter(gate.PILOT_CHECKS)), "status": "up-to-date", "detail": ""}]}) as update, \
             patch.object(rollout, "verify_pilot", side_effect=gate.GateError("failed")):
            destination = Path(temporary) / "report.json"
            markdown = Path(temporary) / "report.md"
            with self.assertRaises(gate.GateError):
                rollout.rollout(["--write", "--public-only", "--json-report", str(destination), "--markdown-report", str(markdown)])
            self.assertEqual(update.call_count, 1)
            combined = json.loads(destination.read_text())
            self.assertFalse(combined["rollout"]["fanout_started"])
            self.assertEqual((combined["rollout"]["stopped"], combined["run"]["mode"]), ("failed", "write"))
            summary = markdown.read_text()
            self.assertTrue(summary.startswith("## Copier fleet: ❌ action required — rollout stopped\n"))
            self.assertIn("**Rollout stopped:** failed.", summary)
            self.assertIn("fanout not started", summary)


class MigrateLegacyRolloutTests(unittest.TestCase):
    ARGUMENTS = ["--write", "--public-only", "--migrate-legacy"]

    def run_rollout(self, statuses, migration):
        events = []

        def update(arguments, report):
            repository = arguments[arguments.index("--repo") + 1] if "--repo" in arguments else "remaining"
            events.append(("update", repository, arguments))
            status = statuses.get(repository, "pull-request")
            detail = f"https://github.com/{repository}/pull/5" if status == "pull-request" else ""
            return {"repositories": [{"repository": repository, "status": status, "detail": detail}]}

        calls = []

        def verify(repository, pr, target, timeout, expected_snapshot=None, **kwargs):
            calls.append((repository, pr, kwargs))
            return SHA

        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(rollout.fleet, "resolve_template_ref", return_value="v1.2.3"), \
             patch.object(rollout.fleet, "resolve_template_commit", return_value=SHA), \
             patch.object(rollout, "wait_until"), \
             patch.object(rollout, "run_update", side_effect=update), \
             patch.object(rollout, "pilot_migration", side_effect=migration), \
             patch.object(rollout, "verify_pilot", side_effect=verify), \
             patch.object(rollout, "current_snapshot", return_value=(SHA, "", "")):
            try:
                rollout.rollout([*self.ARGUMENTS, "--json-report", str(Path(temporary) / "report.json")])
            finally:
                self.events, self.calls = events, calls

    def test_rollout_migrate_legacy_pilots(self):
        self.assertEqual(rollout.MIGRATE_LEGACY_PILOTS,
                         ("quokkify/gh-pages-subdir-action", "quokkify/skills", "quokkify/q4j"))
        self.run_rollout({}, lambda repository, pr: ((f"{repository}: ci / gate", "CodeQL"), SHA))
        self.assertEqual([event[1] for event in self.events[:-1]], list(rollout.MIGRATE_LEGACY_PILOTS))
        self.assertEqual(self.events[-1][1], "remaining")
        self.assertEqual({repository for repository, _, _ in self.calls}, set(rollout.MIGRATE_LEGACY_PILOTS))
        for repository, pr, kwargs in self.calls:
            self.assertEqual(pr, 5)
            self.assertEqual(kwargs["contexts"], (f"{repository}: ci / gate", "CodeQL"))
            self.assertEqual(kwargs["branch"], "ci-kit/migrate-legacy")
        for event in self.events:
            self.assertIn("--migrate-legacy", event[2])

    def test_non_pull_request_pilot_status_stops_with_repository_and_status(self):
        target = rollout.MIGRATE_LEGACY_PILOTS[1]
        for status in ("not-applicable", "needs-update-first", "migrate-rejected", "would-migrate"):
            with self.subTest(status=status):
                with self.assertRaisesRegex(gate.GateError, f"{target}.*{status}"):
                    self.run_rollout({target: status}, lambda repository, pr: (("ci / gate",), SHA))
                self.assertFalse(any(event[1] == "remaining" for event in self.events))

    def test_moved_head_or_unusable_marker_stops_before_fanout(self):
        def unusable(repository, pr):
            raise gate.GateError(f"migrate marker unusable: {repository}")

        with self.assertRaises(gate.GateError):
            self.run_rollout({}, unusable)
        self.assertEqual(self.calls, [])
        with self.assertRaisesRegex(gate.GateError, "head moved"):
            self.run_rollout({}, lambda repository, pr: (("ci / gate",), "b" * 40))
        self.assertEqual(self.calls, [])

    def test_pilot_migration_parses_checks_after_and_head(self):
        body = "text\n<!-- ci-kit-migrate -->\n```json\n" + json.dumps({"checks_after": ["ci / gate", "CodeQL"]}) + "\n```"
        completed = type("Done", (), {"stdout": json.dumps({"body": body, "headRefOid": SHA})})()
        with patch.object(rollout.subprocess, "run", return_value=completed):
            self.assertEqual(rollout.pilot_migration(REPO, 5), (("ci / gate", "CodeQL"), SHA))
        for payload in ({"body": "no marker", "headRefOid": SHA},
                        {"body": body.replace("checks_after", "other"), "headRefOid": SHA},
                        {"body": body, "headRefOid": "x"}):
            completed = type("Done", (), {"stdout": json.dumps(payload)})()
            with self.subTest(payload=payload), patch.object(rollout.subprocess, "run", return_value=completed):
                with self.assertRaises(gate.GateError):
                    rollout.pilot_migration(REPO, 5)


if __name__ == "__main__":
    unittest.main()

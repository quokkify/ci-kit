from __future__ import annotations

import base64
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
OTHER = "b" * 40
REPO = "quokkify/skills"


def workflow(sha=SHA, outcome="success", status="completed", identifier=1):
    return {"id": identifier, "head_sha": sha, "head_branch": "main", "event": "push",
            "status": status, "conclusion": outcome}


def check(name, sha=SHA, outcome="success", status="completed", app="github-actions"):
    return {"id": 1, "head_sha": sha, "name": name, "status": status,
            "conclusion": outcome, "app": {"slug": app}}


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
        with patch.object(gate, "api", side_effect=[{"sha": SHA}, candidate, {"sha": SHA}]), \
             patch.object(gate, "pages", return_value=[{"number": 9, "pull_request": {}}]) as pages, \
             patch.object(gate, "release_ready", return_value=True) as ready:
            self.assertTrue(gate.preflight_ready(REPO, SHA))
            self.assertEqual(ready.call_args_list[1].args, (REPO, OTHER))
            self.assertIn("labels=autorelease%3A%20pending", pages.call_args.args[0])

    def test_bad_candidate_and_advanced_main_stop(self):
        with patch.object(gate, "api", return_value={"sha": OTHER}):
            with self.assertRaises(gate.GateError):
                gate.preflight_ready(REPO, SHA)

    def test_candidate_ci_failure_and_late_main_advance_stop(self):
        candidate = {"merged": True, "base": {"ref": "main"}, "merge_commit_sha": OTHER}
        with patch.object(gate, "api", side_effect=[{"sha": SHA}, candidate]), \
             patch.object(gate, "pages", return_value=[{"number": 9, "pull_request": {}}]), \
             patch.object(gate, "release_ready", side_effect=[True, False]):
            self.assertFalse(gate.preflight_ready(REPO, SHA))
        with patch.object(gate, "api", side_effect=[{"sha": SHA}, {"sha": OTHER}]), \
             patch.object(gate, "pages", return_value=[]), \
             patch.object(gate, "release_ready", return_value=True):
            with self.assertRaises(gate.GateError):
                gate.preflight_ready(REPO, SHA)

    def test_post_tag_checks_the_resolved_commit(self):
        with patch.object(sys, "argv", ["gate", "--repository", REPO, "--ref", "v1.2.3"]), \
             patch.object(gate, "api", return_value={"sha": OTHER}), \
             patch.object(gate, "release_ready", return_value=True) as ready:
            self.assertEqual(gate.main(), 0)
            self.assertEqual(ready.call_args.args, (REPO, OTHER))
        with patch.object(gate, "api", side_effect=[{"sha": SHA}, {"merged": True, "base": {"ref": "main"}, "merge_commit_sha": "bad"}]), \
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
        payload = {"content": base64.b64encode(b"_commit: v1.2.3\n_src_path: gh:quokkify/ci-kit\n").decode()}
        with patch.object(gate, "current_snapshot", side_effect=[(SHA, "", ""), (OTHER, "", "")]), \
             patch.object(gate, "api", return_value=payload), patch.object(gate, "wait_until"):
            with self.assertRaises(gate.GateError):
                gate.verify_pilot(REPO, None, "v1.2.3", 0)
        with patch.object(gate, "current_snapshot", return_value=(SHA, "", "")), patch.object(gate, "api", return_value=payload):
            with self.assertRaises(gate.GateError):
                gate.verify_pilot(REPO, None, "v2.0.0", 0)

    def test_default_branch_pilot_skips_pull_request_only_checks(self):
        payload = {"content": base64.b64encode(b"_commit: v1.2.3\n_src_path: gh:quokkify/ci-kit\n").decode()}
        for pr, expected in ((None, False), (7, True)):
            with self.subTest(pr=pr), patch.object(gate, "current_snapshot", return_value=(SHA, OTHER, SHA)), \
                 patch.object(gate, "api", return_value=payload), \
                 patch.object(gate, "wait_until", side_effect=lambda check, timeout: check()), \
                 patch.object(gate, "pilot_ready", return_value=True) as ready:
                gate.verify_pilot("quokkify/q4j", pr, "v1.2.3", 0)
                contexts = ready.call_args.args[2]
                self.assertEqual("Tests pipeline complete" in contexts, expected)
                self.assertIn("Shared template contract", contexts)

    def test_handed_off_validate_replaces_legacy_context(self):
        pilot = "quokkify/gh-pages-subdir-action"
        legacy = gate.PILOT_CHECKS[pilot]
        handed = gate.pilot_contexts(pilot, 7, handed_off=("validate",))
        self.assertEqual(handed, tuple("ci / gate" if c == "Shared template contract" else c for c in legacy))
        self.assertEqual(gate.pilot_contexts(pilot, 7, handed_off=("validate", "validate")), handed)
        self.assertEqual(gate.pilot_contexts(pilot, 7, handed_off=("other",)), legacy)

    def test_legacy_pilot_contexts_are_unchanged(self):
        self.assertEqual(gate.pilot_contexts("quokkify/skills", None),
                         ("Gitleaks", "App Python / ci", "Shared template contract"))
        self.assertEqual(gate.pilot_contexts("quokkify/q4j", 7),
                         ("Scan current tree (legacy history pending baseline)", "Tests pipeline complete",
                          "Shared template contract"))
        self.assertEqual(gate.pilot_contexts("quokkify/q4j", None),
                         ("Scan current tree (legacy history pending baseline)", "Shared template contract"))

    def test_verify_pilot_expects_gate_after_handoff(self):
        for answers, expected in ((b"handed_off: [validate]\n", "ci / gate"), (b"handed_off: oops\n", "Shared template contract"),
                                  (b"", "Shared template contract")):
            payload = {"content": base64.b64encode(b"_commit: v1.2.3\n_src_path: gh:quokkify/ci-kit\n" + answers).decode()}
            with self.subTest(answers=answers), patch.object(gate, "current_snapshot", return_value=(SHA, OTHER, SHA)), \
                 patch.object(gate, "api", return_value=payload), \
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
             patch.object(rollout, "current_snapshot", return_value=(SHA, "", "")):
            destination = Path(temporary) / "report.json"
            rollout.rollout(["--write", "--public-only", "--json-report", str(destination)])
            self.assertEqual(events[-1], ("update", "remaining"))
            self.assertEqual(sum(event[0] == "verify" for event in events), 6)
            self.assertEqual(timeouts, {repository: 2700 if repository == "quokkify/q4j" else 900
                                        for repository in gate.PILOT_CHECKS})
            self.assertTrue(json.loads(destination.read_text())["rollout"]["fanout_started"])
            combined = json.loads(destination.read_text())
            self.assertEqual(combined["schema_version"], 4)
            self.assertEqual(combined["configuration_gaps"], 4)
            self.assertEqual(combined["repositories"][0]["health"], {"adopted": True})

    def test_pilot_failure_stops_before_fanout_and_writes_partial_report(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(rollout.fleet, "resolve_template_ref", return_value="v1.2.3"), \
             patch.object(rollout.fleet, "resolve_template_commit", return_value=SHA), \
             patch.object(rollout, "wait_until"), \
             patch.object(rollout, "current_snapshot", return_value=(SHA, "", "")), \
             patch.object(rollout, "run_update", return_value={"repositories": [{"repository": next(iter(gate.PILOT_CHECKS)), "status": "up-to-date", "detail": ""}]}) as update, \
             patch.object(rollout, "verify_pilot", side_effect=gate.GateError("failed")):
            destination = Path(temporary) / "report.json"
            with self.assertRaises(gate.GateError):
                rollout.rollout(["--write", "--public-only", "--json-report", str(destination)])
            self.assertEqual(update.call_count, 1)
            self.assertFalse(json.loads(destination.read_text())["rollout"]["fanout_started"])


class MigrateRolloutTests(unittest.TestCase):
    ARGUMENTS = ["--write", "--public-only", "--migrate", "core"]

    def run_rollout(self, statuses, migration, remaining=True):
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

    def test_expected_contexts_come_from_pull_request_marker(self):
        def migration(repository, pr):
            return (f"{repository}: ci / gate", "Scan"), SHA

        self.run_rollout({}, migration)
        self.assertEqual([event[1] for event in self.events[:-1]], list(rollout.MIGRATE_PILOTS))
        self.assertEqual(self.events[-1][1], "remaining")
        self.assertEqual({repository for repository, _, _ in self.calls}, set(rollout.MIGRATE_PILOTS))
        for repository, pr, kwargs in self.calls:
            self.assertEqual(pr, 5)
            self.assertEqual(kwargs["contexts"], (f"{repository}: ci / gate", "Scan"))
            self.assertEqual(kwargs["branch"], "ci-kit/migrate-core")
        for event in self.events:
            self.assertIn("--migrate", event[2])

    def test_non_pull_request_pilot_status_stops_with_repository_and_status(self):
        for status in ("not-applicable", "needs-update-first", "migrate-rejected", "would-migrate"):
            target = rollout.MIGRATE_PILOTS[1]
            with self.subTest(status=status):
                with self.assertRaisesRegex(gate.GateError, f"{target}.*{status}"):
                    self.run_rollout({target: status}, lambda repository, pr: (("ci / gate",), SHA))
                self.assertFalse(any(event[1] == "remaining" for event in self.events))

    def test_missing_or_invalid_marker_stops(self):
        def migration(repository, pr):
            raise gate.GateError(f"migrate marker unusable: {repository}")

        with self.assertRaises(gate.GateError):
            self.run_rollout({}, migration)
        self.assertEqual(self.calls, [])

    def test_pilot_migration_parses_checks_after_and_head(self):
        body = "text\n<!-- ci-kit-migrate -->\n```json\n" + json.dumps({"checks_after": ["ci / gate", "Scan"]}) + "\n```"
        completed = type("Done", (), {"stdout": json.dumps({"body": body, "headRefOid": SHA})})()
        with patch.object(rollout.subprocess, "run", return_value=completed):
            self.assertEqual(rollout.pilot_migration(REPO, 5), (("ci / gate", "Scan"), SHA))
        for payload in ({"body": "no marker", "headRefOid": SHA},
                        {"body": body.replace("checks_after", "other"), "headRefOid": SHA},
                        {"body": body, "headRefOid": "x"}):
            completed = type("Done", (), {"stdout": json.dumps(payload)})()
            with self.subTest(payload=payload), patch.object(rollout.subprocess, "run", return_value=completed):
                with self.assertRaises(gate.GateError):
                    rollout.pilot_migration(REPO, 5)


if __name__ == "__main__":
    unittest.main()

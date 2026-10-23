#!/usr/bin/env python3
"""Update public fleet pilots, verify their revision, then create remaining PRs."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import update_copier_fleet as fleet
from verify_ci_gate import (DEFAULT_PILOT_TIMEOUT, GateError, PILOT_CHECKS, PILOT_TIMEOUTS, current_snapshot,
                            release_ready, verify_pilot, wait_until)

def merged_repositories(repositories: list[dict]) -> list[dict]:
    """Drop a stage's `excluded` entry for a repository another stage processed."""
    processed = {item["repository"] for item in repositories if item["status"] != "excluded"}
    return [item for item in repositories if item["status"] != "excluded" or item["repository"] not in processed]


def run_update(arguments: list[str], report: Path) -> dict:
    command = [sys.executable, str(Path(__file__).with_name("update_copier_fleet.py")),
               *arguments, "--json-report", str(report)]
    result = subprocess.run(command, check=False)
    if not report.is_file():
        raise GateError(f"Fleet updater stopped with exit code {result.returncode}; see {report}")
    payload = json.loads(report.read_text())
    payload["_exit_code"] = result.returncode
    return payload


def rollout(arguments: list[str]) -> None:
    args = fleet.build_parser().parse_args(arguments)
    # A targeted request must never mutate unrelated pilots. The audit is also
    # passed through unchanged, without requiring a write token or CI evidence.
    if args.dry_run or args.repo or args.merge_ready:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("update_copier_fleet.py")), *arguments],
            check=False,
        )
        if result.returncode:
            raise GateError(f"Fleet updater stopped with exit code {result.returncode}")
        return
    if (args.org != "quokkify" or not args.public_only
            or args.template_repository != "quokkify/ci-kit"
            or args.branch != "automation/copier-template-update"):
        raise GateError("Full rollout requires the public quokkify fleet and standard automation branch")
    pilots = tuple(PILOT_CHECKS)
    pilot_branch = {"branch": fleet.add_module_branch(args.add_module)} if args.add_module else {}
    if any(pilot in args.exclude for pilot in pilots):
        raise GateError("Full rollout cannot exclude a pilot")
    target = fleet.resolve_template_ref(args.template_repository, args.template_ref, env=os.environ.copy())
    if not fleet.RELEASE_TAG_PATTERN.fullmatch(target):
        raise GateError("Full rollout requires an exact released vMAJOR.MINOR.PATCH tag")
    template_sha = fleet.resolve_template_commit(args.template_repository, target, env=os.environ.copy())
    wait_until(lambda: release_ready(args.template_repository, template_sha), timeout=300)
    # Remove report flags before splitting work; each stage receives its own
    # private report, then the original destinations get the combined evidence.
    base = []
    index = 0
    while index < len(arguments):
        arg = arguments[index]
        if arg in {"--json-report", "--markdown-report", "--template-ref"}:
            index += 2
        elif any(arg.startswith(flag + "=") for flag in ("--json-report", "--markdown-report", "--template-ref")):
            index += 1
        else:
            base.append(arg)
            index += 1
    base.extend(["--template-ref", target])
    repositories = []
    stage_reports = []
    gates = []
    verified = []
    fanout_started = False
    stopped = None
    fanout_failed = False
    try:
        with tempfile.TemporaryDirectory(prefix="toolkit-canary-") as temporary:
            directory = Path(temporary)
            for index, repository in enumerate(pilots):
                report = run_update([*base, "--repo", repository], directory / f"pilot-{index}.json")
                stage_reports.append(report)
                repositories.extend(report["repositories"])
                if report.get("_exit_code", 0):
                    raise GateError(f"Pilot updater failed: {repository}")
                if len(report["repositories"]) != 1:
                    raise GateError(f"Pilot update did not produce one result: {repository}")
                item = report["repositories"][0]
                if item["repository"] != repository or item["status"] not in {"up-to-date", "pull-request"}:
                    raise GateError(f"Pilot was not updated or verified: {repository}")
                pr = None
                if item["status"] == "pull-request":
                    match = re.fullmatch(rf"https://github\.com/{re.escape(repository)}/pull/(\d+)", item["detail"])
                    if not match:
                        raise GateError(f"Unrecognized pilot PR: {repository}")
                    pr = int(match[1])
                snapshot = current_snapshot(repository, pr, **pilot_branch)
                timeout = PILOT_TIMEOUTS.get(repository, DEFAULT_PILOT_TIMEOUT)
                sha = verify_pilot(repository, pr, target, timeout=timeout, expected_snapshot=snapshot, **pilot_branch)
                verified.append((repository, pr, snapshot))
                gates.append({"repository": repository, "revision": sha, "base_revision": snapshot[1],
                              "merge_revision": snapshot[2], "pull_request": pr, "result": "success"})
            # Recheck earlier pilots immediately before fanout: waiting on a later
            # pilot must not make stale heads count as current evidence.
            if fleet.resolve_template_commit(args.template_repository, target, env=os.environ.copy()) != template_sha:
                raise GateError("Template tag changed before fanout")
            for repository, pr, snapshot in verified:
                if current_snapshot(repository, pr, **pilot_branch) != snapshot:
                    raise GateError(f"Pilot changed before fanout: {repository}")
                if verify_pilot(repository, pr, target, timeout=0, expected_snapshot=snapshot, **pilot_branch) != snapshot[0]:
                    raise GateError(f"Pilot changed before fanout: {repository}")
            remaining = [*base]
            for repository in pilots:
                remaining.extend(["--exclude", repository])
            fanout_started = True
            report = run_update(remaining, directory / "remaining.json")
            stage_reports.append(report)
            repositories.extend(report["repositories"])
            if report.get("_exit_code", 0):
                fanout_failed = True
                raise GateError("Remaining fleet updater failed; inspect the partial report")
    except BaseException as exc:
        if not fanout_failed:
            stopped = re.sub(r"\S*toolkit-canary-\S*", "the stage report", str(exc)) or type(exc).__name__
        raise
    finally:
        repositories = merged_repositories(repositories)
        counts = {}
        for item in repositories:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        report = {"schema_version": max([6, *(stage.get("schema_version", 3) for stage in stage_reports)]),
                  "summary": counts, "repositories": repositories,
                  "rollout": {"target": target, "pilots": gates, "fanout_started": fanout_started, "stopped": stopped},
                  "run": fleet.run_metadata("write", target, os.environ.copy(), args.template_ref)}
        for key in ("configuration_gaps", "configuration_mismatches"):
            report[key] = sum(stage.get(key, 0) for stage in stage_reports)
        # Retain additive top-level report metadata (for example health-policy
        # descriptions) rather than resetting the report to an older schema.
        for stage in stage_reports:
            for key, value in stage.items():
                if not key.startswith("_") and key not in report:
                    report[key] = value
        if args.json_report:
            args.json_report.parent.mkdir(parents=True, exist_ok=True)
            args.json_report.write_text(json.dumps(report, indent=2) + "\n")
        if args.markdown_report:
            args.markdown_report.parent.mkdir(parents=True, exist_ok=True)
            try:
                markdown = fleet.markdown_report(fleet.results_from_report(report), counts, report["run"], report["rollout"])
            except Exception as exc:
                markdown = (f"## Copier fleet: ❌ action required — {'rollout stopped' if stopped else 'report rendering failed'}\n\n"
                            f"The summary could not be rendered ({type(exc).__name__}); see the job log and JSON report.\n")
            args.markdown_report.write_text(markdown)


def main() -> int:
    try:
        rollout(sys.argv[1:])
    except (GateError, fleet.FleetUpdateError, OSError, ValueError, KeyError) as exc:
        print(f"Rollout stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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

import update_copier_fleet as fleet
from verify_ci_gate import GateError, PILOT_CHECKS, current_snapshot, release_ready, verify_pilot, wait_until


def run_update(arguments: list[str], report: Path) -> dict:
    command = [sys.executable, str(Path(__file__).with_name("update_copier_fleet.py")),
               *arguments, "--json-report", str(report), "--markdown-report", str(report.with_suffix(".md"))]
    result = subprocess.run(command, check=False)
    if not report.is_file():
        raise GateError(f"Fleet updater stopped with exit code {result.returncode}; see {report}")
    payload = json.loads(report.read_text())
    payload["_exit_code"] = result.returncode
    markdown = report.with_suffix(".md")
    payload["_markdown"] = markdown.read_text() if markdown.is_file() else ""
    return payload


def rollout(arguments: list[str]) -> None:
    args = fleet.build_parser().parse_args(arguments)
    # A targeted request must never mutate unrelated pilots. The audit is also
    # passed through unchanged, without requiring a write token or CI evidence.
    if args.dry_run or args.repo:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("update_copier_fleet.py")), *arguments],
            check=False,
        )
        if result.returncode:
            raise GateError(f"Fleet updater stopped with exit code {result.returncode}")
        return
    if (args.org != "quokkify" or not args.public_only
            or args.template_repository != "quokkify/project-toolkit"
            or args.branch != "automation/copier-template-update"):
        raise GateError("Full rollout requires the public quokkify fleet and standard automation branch")
    if any(pilot in args.exclude for pilot in PILOT_CHECKS):
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
    try:
        with tempfile.TemporaryDirectory(prefix="toolkit-canary-") as temporary:
            directory = Path(temporary)
            for index, repository in enumerate(PILOT_CHECKS):
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
                snapshot = current_snapshot(repository, pr)
                sha = verify_pilot(repository, pr, target, timeout=900, expected_snapshot=snapshot)
                verified.append((repository, pr, snapshot))
                gates.append({"repository": repository, "revision": sha, "base_revision": snapshot[1],
                              "merge_revision": snapshot[2], "pull_request": pr, "result": "success"})
            # Recheck earlier pilots immediately before fanout: waiting on a later
            # pilot must not make stale heads count as current evidence.
            if fleet.resolve_template_commit(args.template_repository, target, env=os.environ.copy()) != template_sha:
                raise GateError("Template tag changed before fanout")
            for repository, pr, snapshot in verified:
                if current_snapshot(repository, pr) != snapshot:
                    raise GateError(f"Pilot changed before fanout: {repository}")
                if verify_pilot(repository, pr, target, timeout=0, expected_snapshot=snapshot) != snapshot[0]:
                    raise GateError(f"Pilot changed before fanout: {repository}")
            remaining = [*base]
            for repository in PILOT_CHECKS:
                remaining.extend(["--exclude", repository])
            fanout_started = True
            report = run_update(remaining, directory / "remaining.json")
            stage_reports.append(report)
            repositories.extend(report["repositories"])
            if report.get("_exit_code", 0):
                raise GateError("Remaining fleet updater failed; inspect the partial report")
    finally:
        counts = {}
        for item in repositories:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        report = {"schema_version": max((stage.get("schema_version", 3) for stage in stage_reports), default=3),
                  "summary": counts, "repositories": repositories,
                  "rollout": {"target": target, "pilots": gates, "fanout_started": fanout_started}}
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
            lines = ["## Copier pilot rollout", "", f"Target: `{target}`", ""]
            lines.extend(f"- {gate['repository']}: verified `{gate['revision']}`" for gate in gates)
            lines.extend(["", f"Repositories processed: {len(repositories)}. See the JSON artifact and logs for detail."])
            lines.extend(stage["_markdown"] for stage in stage_reports if stage.get("_markdown"))
            args.markdown_report.write_text("\n".join(lines) + "\n")


def main() -> int:
    try:
        rollout(sys.argv[1:])
    except (GateError, fleet.FleetUpdateError, OSError, ValueError, KeyError) as exc:
        print(f"Rollout stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

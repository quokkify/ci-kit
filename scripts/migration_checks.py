#!/usr/bin/env python3
"""Switch a consumer's required checks between legacy jobs and ``ci / gate``.

Explicit, human-run step: reads the migrate marker from a PR body, computes the
managed ruleset's new required checks and reconciles it at the PR head SHA.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.migrate import parse_marker
from fleet_health import required_checks
from reconcile_ruleset import GitHubClient, ReconcileError, _find_existing, build_desired, list_rulesets, managed_name, reconcile

PR_ARGUMENT = re.compile(r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#([1-9][0-9]*)$")
CODEQL_THRESHOLDS = {"none", "errors", "all"}


class Refusal(RuntimeError):
    """The switch cannot be proven safe; nothing was changed."""


def next_checks(current: list[str], before: list[str], after: list[str], *, revert: bool) -> list[str]:
    remove, add = (after, before) if revert else (before, after)
    result = list(dict.fromkeys([*(item for item in current if item not in set(remove)), *add]))
    if not result:
        raise ValueError("required checks cannot become empty")
    return result


def pr_view(repo: str, number: int) -> dict[str, Any]:
    result = subprocess.run(
        ["gh", "pr", "view", str(number), "--repo", repo, "--json", "body,headRefOid,baseRefName"],
        check=True, capture_output=True, text=True, timeout=45,
    )
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise ValueError("malformed gh pr view output")
    return payload


def api_request(endpoint: str) -> Any:
    result = subprocess.run(["gh", "api", endpoint], capture_output=True, text=True, timeout=45)
    if result.returncode:
        if "HTTP 404" in result.stderr:
            return None
        raise RuntimeError(f"gh api {endpoint} failed")
    return json.loads(result.stdout)


def string_list(marker: dict[str, Any], key: str) -> list[str]:
    value = marker.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"migrate marker {key} must be a list of check names")
    return value


def codeql_threshold(detail: Any) -> str | None:
    rules = detail.get("rules") if isinstance(detail, dict) else None
    if not isinstance(rules, list):
        raise Refusal("managed ruleset detail has no rules; refusing")
    thresholds = []
    for rule in rules:
        if isinstance(rule, dict) and rule.get("type") == "code_scanning":
            tools = rule.get("parameters", {}).get("code_scanning_tools")
            if not isinstance(tools, list) or len(tools) != 1 or tools[0].get("tool") != "CodeQL" or tools[0].get("alerts_threshold") not in CODEQL_THRESHOLDS:
                raise Refusal("managed ruleset code-scanning rule is not a single CodeQL tool with a known threshold; refusing")
            thresholds.append(tools[0]["alerts_threshold"])
    if len(thresholds) > 1:
        raise Refusal("managed ruleset has several code-scanning rules; refusing")
    return thresholds[0] if thresholds else None


def foreign_owner(entries: list[dict[str, Any]], context: str, managed_id: int) -> str | None:
    for item in entries:
        if item["context"] != context:
            continue
        if item["source"] == "classic-protection":
            return "classic-protection"
        if item.get("ruleset_id") != managed_id:
            return f"ruleset {item.get('ruleset_id')}"
    return None


def plan(policy: dict[str, Any], managed_id: int, before: list[str], after: list[str], revert: bool) -> tuple[list[str], list[str]]:
    if policy.get("status") == "unknown":
        raise Refusal(f"required checks status is unknown ({policy.get('reason', 'no reason given')}); refusing")
    entries = policy.get("checks", [])
    for context in (after if revert else before):
        owner = foreign_owner(entries, context, managed_id)
        if owner:
            raise Refusal(f"{context!r} is required by {owner}, not the managed ruleset; refusing")
    current = [item["context"] for item in entries if item["source"] == "active-ruleset" and item.get("ruleset_id") == managed_id]
    return current, next_checks(current, before, after, revert=revert)


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--pr", required=True, help="OWNER/REPO#N")
    command.add_argument("--revert", action="store_true")
    command.add_argument("--dry-run", action="store_true")
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    match = PR_ARGUMENT.fullmatch(args.pr)
    if not match:
        parser().error("--pr must be OWNER/REPO#N")
    repo, number = match.group(1), int(match.group(2))
    try:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            raise ReconcileError("set GH_TOKEN or GITHUB_TOKEN; credentials are never accepted as CLI arguments")
        try:
            view = pr_view(repo, number)
            head, branch = view["headRefOid"], view["baseRefName"]
            marker = parse_marker(view["body"])
            before, after = string_list(marker, "checks_before"), string_list(marker, "checks_after")
        except (subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
            raise Refusal(f"migration marker unavailable for {repo}#{number}: {exc}") from exc
        if not isinstance(head, str) or not re.fullmatch(r"[0-9a-f]{40}", head) or not isinstance(branch, str) or not branch:
            raise Refusal(f"PR head or base unavailable for {repo}#{number}")
        client = GitHubClient(token)
        name = managed_name(*repo.split("/"))
        existing = _find_existing(list_rulesets(client, repo), name)
        if existing is None or not isinstance(existing.get("id"), int):
            raise Refusal(f"managed ruleset {name} does not exist; refusing")
        managed_id = existing["id"]
        threshold = codeql_threshold(client.request("GET", f"/repos/{repo}/rulesets/{managed_id}"))
        try:
            policy = required_checks(api_request, repo, branch)
        except ValueError as exc:
            raise Refusal(f"required checks unavailable: {exc}") from exc
        try:
            current, new = plan(policy, managed_id, before, after, args.revert)
        except ValueError as exc:
            raise Refusal(str(exc)) from exc
        output = reconcile(client, repo, branch, new, head, "active", args.dry_run, threshold)
    except Refusal as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except ReconcileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"repo": repo, "branch": branch, "revision": head, "dry_run": args.dry_run, "before": current, "after": new, "action": output.get("action")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

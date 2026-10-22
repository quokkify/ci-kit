#!/usr/bin/env python3
"""Switch a consumer's required checks between legacy jobs and ``ci / gate``.

Explicit, human-run step: reads the migrate marker from a PR body, computes the
new required checks and reconciles the managed ruleset at the PR head SHA. Without
a managed ruleset it patches only the required checks of the single repository
ruleset that requires checks on the base branch.

--revert removes checks_after and re-adds only the --restore contexts: the "removed"
list the forward switch printed. Each must be in checks_before.

The PR body is never trusted for check lists: checks_after must equal the module's
CHECKS_AFTER and checks_before must equal the legacy jobs of validate.yml at the PR
base SHA (at --revision for --revert). A managed-ruleset PUT without bypass_actors is
assumed to leave existing bypass actors unchanged; verify with --dry-run before relying on it.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import tempfile
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.migration_marker import CHECKS_AFTER, legacy_check_names, parse_marker
from fleet_health import required_checks
from reconcile_ruleset import (GitHubClient, ReconcileError, _find_existing, _normalize, build_desired, check_contexts, list_rulesets,
                               managed_name, reconcile)

PR_ARGUMENT = re.compile(r"^([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)#([1-9][0-9]*)$")
CODEQL_THRESHOLDS = {"none", "errors", "all"}
PATCHED_KEYS = ("name", "target", "enforcement", "conditions", "bypass_actors", "rules")


class Refusal(RuntimeError):
    """The switch cannot be proven safe; nothing was changed."""


def next_checks(current: list[str], before: list[str], after: list[str], *, revert: bool, restore: list[str] | None = None) -> list[str]:
    if revert:
        if restore is None:
            raise ValueError("--revert needs --restore: the checks the forward switch removed")
        unknown = [item for item in restore if item not in before]
        if unknown:
            raise ValueError(f"--restore {unknown} not in checks_before {before}; refusing")
        remove, add = after, restore
    else:
        remove, add = before, after
    result = list(dict.fromkeys([*(item for item in current if item not in set(remove)), *add]))
    if not result:
        raise ValueError("required checks cannot become empty")
    return result


def pr_view(repo: str, number: int) -> dict[str, Any]:
    result = subprocess.run(
        ["gh", "pr", "view", str(number), "--repo", repo, "--json", "body,headRefOid,baseRefName,baseRefOid"],
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


def legacy_checks_at(repo: str, ref: str) -> list[str]:
    payload = api_request(f"repos/{repo}/contents/.github/workflows/validate.yml?ref={ref}")
    if not isinstance(payload, dict) or payload.get("type") != "file" or payload.get("encoding") != "base64" or not isinstance(payload.get("content"), str):
        raise Refusal(f"validate.yml missing or not readable as base64 at {ref}; refusing")
    content = payload["content"]
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "validate.yml"
        path.write_bytes(base64.b64decode(content))
        try:
            names = legacy_check_names(path)
        except ValueError as error:
            raise Refusal(f"validate.yml at {ref}: {error}; refusing") from error
    if not names:
        raise Refusal(f"validate.yml at {ref} defines no jobs; refusing")
    return names


def string_list(marker: dict[str, Any], key: str) -> list[str]:
    value = marker.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"migrate marker {key} must be a list of check names")
    return value


def managed_state(detail: Any, branch: str) -> tuple[list[str], str | None]:
    if not isinstance(detail, dict) or not isinstance(detail.get("rules"), list):
        raise Refusal("managed ruleset detail has no rules; refusing")
    if detail.get("enforcement") != "active":
        raise Refusal(f"managed ruleset enforcement is {detail.get('enforcement')!r}, not active; refusing")
    conditions = detail.get("conditions")
    ref_name = conditions.get("ref_name") if isinstance(conditions, dict) else None
    include = ref_name.get("include") if isinstance(ref_name, dict) else None
    if not isinstance(include, list) or f"refs/heads/{branch}" not in include:
        raise Refusal(f"managed ruleset does not target refs/heads/{branch}; refusing")
    contexts: list[str] | None = None
    thresholds = []
    for rule in detail["rules"]:
        if not isinstance(rule, dict):
            raise Refusal("managed ruleset has a malformed rule; refusing")
        parameters = rule.get("parameters")
        if rule.get("type") == "required_status_checks":
            statuses = parameters.get("required_status_checks") if isinstance(parameters, dict) else None
            if contexts is not None or not isinstance(statuses, list):
                raise Refusal("managed ruleset required_status_checks rule is malformed or duplicated; refusing")
            contexts = []
            for status in statuses:
                if not isinstance(status, dict) or not isinstance(status.get("context"), str):
                    raise Refusal("managed ruleset has a malformed required check; refusing")
                if status.get("integration_id") is not None:
                    raise Refusal(f"required check {status['context']!r} is bound to an integration; re-sending it would unbind it; refusing")
                contexts.append(status["context"])
        elif rule.get("type") == "code_scanning":
            tools = parameters.get("code_scanning_tools") if isinstance(parameters, dict) else None
            if not isinstance(tools, list) or len(tools) != 1 or not isinstance(tools[0], dict) or tools[0].get("tool") != "CodeQL" or tools[0].get("alerts_threshold") not in CODEQL_THRESHOLDS:
                raise Refusal("managed ruleset code-scanning rule is not a single CodeQL tool with a known threshold; refusing")
            expected = build_desired("n", "b", ["c"], "active", tools[0]["alerts_threshold"])["rules"][-1]["parameters"]["code_scanning_tools"][0]["security_alerts_threshold"]
            if tools[0].get("security_alerts_threshold") != expected:
                raise Refusal("managed ruleset security_alerts_threshold differs from what reconcile would write; refusing")
            thresholds.append(tools[0]["alerts_threshold"])
    if contexts is None:
        raise Refusal("managed ruleset has no required_status_checks rule; refusing")
    if len(thresholds) > 1:
        raise Refusal("managed ruleset has several code-scanning rules; refusing")
    return contexts, thresholds[0] if thresholds else None


def foreign_owner(entries: list[dict[str, Any]], context: str, managed_id: int) -> str | None:
    for item in entries:
        if item["context"] != context:
            continue
        if item["source"] == "classic-protection":
            return "classic-protection"
        if item.get("ruleset_id") != managed_id:
            return f"ruleset {item.get('ruleset_id')}"
    return None


def plan(policy: dict[str, Any], managed_id: int, current: list[str], before: list[str], after: list[str], revert: bool,
         restore: list[str] | None = None) -> list[str]:
    if policy.get("status") == "unknown":
        raise Refusal(f"required checks status is unknown ({policy.get('reason', 'no reason given')}); refusing")
    for context in (after if revert else before):
        owner = foreign_owner(policy.get("checks", []), context, managed_id)
        if owner:
            raise Refusal(f"{context!r} is required by {owner}, not the managed ruleset; refusing")
    return next_checks(current, before, after, revert=revert, restore=restore)


def unmanaged_ruleset(client: GitHubClient, repo: str, policy: dict[str, Any], name: str) -> tuple[dict[str, Any], list[str]]:
    if policy.get("status") == "unknown":
        raise Refusal(f"required checks status is unknown ({policy.get('reason', 'no reason given')}); refusing")
    entries = policy.get("checks", [])
    if any(item["source"] == "classic-protection" for item in entries):
        raise Refusal(f"managed ruleset {name} does not exist and classic branch protection requires checks; refusing")
    owners = {item.get("ruleset_id") for item in entries}
    if not all(isinstance(owner, int) for owner in owners):
        raise Refusal("a required check on the base branch has no ruleset id; refusing")
    if len(owners) != 1:
        raise Refusal(f"managed ruleset {name} does not exist and {len(owners)} rulesets require checks on the base branch, not exactly one; refusing")
    identifier = owners.pop()
    detail = client.request("GET", f"/repos/{repo}/rulesets/{identifier}")
    if not isinstance(detail, dict) or detail.get("id") != identifier or not isinstance(detail.get("rules"), list):
        raise Refusal(f"ruleset {identifier} detail is malformed; refusing")
    if detail.get("source_type") != "Repository":
        raise Refusal(f"ruleset {identifier} is owned by {detail.get('source_type')!r}, not the repository; refusing")
    if detail.get("enforcement") != "active":
        raise Refusal(f"ruleset {identifier} enforcement is {detail.get('enforcement')!r}, not active; refusing")
    if not isinstance(detail.get("bypass_actors"), list):
        raise Refusal(f"ruleset {identifier} bypass_actors are not readable with this token; refusing")
    if not isinstance(detail.get("updated_at"), str) or not all(key in detail for key in PATCHED_KEYS):
        raise Refusal(f"ruleset {identifier} detail is incomplete; refusing")
    rules = [rule for rule in detail["rules"] if isinstance(rule, dict) and rule.get("type") == "required_status_checks"]
    statuses = rules[0].get("parameters", {}).get("required_status_checks") if len(rules) == 1 and isinstance(rules[0].get("parameters"), dict) else None
    if not isinstance(statuses, list) or not all(isinstance(status, dict) and isinstance(status.get("context"), str) for status in statuses):
        raise Refusal(f"ruleset {identifier} required_status_checks rule is missing, malformed or duplicated; refusing")
    contexts = [status["context"] for status in statuses]
    if len(set(contexts)) != len(contexts):
        raise Refusal(f"ruleset {identifier} has duplicated required check contexts; refusing")
    return detail, contexts


def patched_view(ruleset: dict[str, Any]) -> Any:
    view = _normalize({key: ruleset.get(key) for key in PATCHED_KEYS})
    for rule in view["rules"] or []:
        if isinstance(rule, dict) and rule.get("type") == "required_status_checks":
            rule["parameters"]["required_status_checks"] = [
                {key: value for key, value in status.items() if value is not None} for status in rule["parameters"]["required_status_checks"]
            ]
    return view


def patch_required_checks(client: GitHubClient, repo: str, detail: dict[str, Any], current: list[str], checks: list[str], revision: str, dry_run: bool) -> dict[str, Any]:
    added = set(checks) - set(current)
    missing = sorted(added - check_contexts(client, repo, revision)) if added else []
    if missing:
        raise ReconcileError(f"required check context(s) not published for {revision}: {', '.join(missing)}; refusing mutation")
    rules = []
    for rule in detail["rules"]:
        if isinstance(rule, dict) and rule.get("type") == "required_status_checks":
            kept = {status["context"]: status for status in rule["parameters"]["required_status_checks"]}
            rule = {**rule, "parameters": {**rule["parameters"], "required_status_checks": [kept.get(check, {"context": check}) for check in checks]}}
        rules.append(rule)
    desired = {**{key: detail[key] for key in PATCHED_KEYS}, "rules": rules}
    if patched_view(desired) == patched_view(detail):
        return {"action": "noop"}
    if dry_run:
        return {"action": "update"}
    path = f"/repos/{repo}/rulesets/{detail['id']}"
    fresh = client.request("GET", path)
    if not isinstance(fresh, dict) or fresh.get("updated_at") != detail["updated_at"]:
        raise Refusal(f"ruleset {detail['id']} changed since it was read; refusing")
    client.request("PUT", path, desired)
    try:
        matches = patched_view(client.request("GET", path)) == patched_view(desired)
    except (AttributeError, KeyError, TypeError) as exc:
        raise ReconcileError(f"ruleset {detail['id']} PUT applied; readback is malformed: {exc}") from exc
    if not matches:
        raise ReconcileError(f"ruleset {detail['id']} PUT applied; readback differs from what was sent")
    return {"action": "update"}


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--pr", required=True, help="OWNER/REPO#N")
    command.add_argument("--revert", action="store_true")
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--revision", help="head SHA of the git-revert PR; required with --revert")
    command.add_argument("--restore", nargs="*", action="extend", metavar="CONTEXT",
                         help="with --revert: the checks the forward switch removed (its 'removed' output); no value restores none")
    return command


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    match = PR_ARGUMENT.fullmatch(args.pr)
    if not match:
        parser().error("--pr must be OWNER/REPO#N")
    if args.revert and not (args.revision and re.fullmatch(r"[0-9a-f]{40}", args.revision)):
        parser().error("--revert requires --revision <40-hex head SHA of the revert PR>")
    if args.revert != (args.restore is not None):
        parser().error("--revert requires --restore [CONTEXT ...] (the forward switch's 'removed'), and --restore requires --revert")
    repo, number = match.group(1), int(match.group(2))
    try:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            raise ReconcileError("set GH_TOKEN or GITHUB_TOKEN; credentials are never accepted as CLI arguments")
        try:
            view = pr_view(repo, number)
            head, branch, base = view["headRefOid"], view["baseRefName"], view["baseRefOid"]
            marker = parse_marker(view["body"])
            module_name = marker.get("module")
            marker_before = string_list(marker, "checks_before")
            after = string_list(marker, "checks_after")
        except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError) as exc:
            raise Refusal(f"migration marker unavailable for {repo}#{number}: {exc}") from exc
        sha = re.compile(r"[0-9a-f]{40}")
        if not all(isinstance(item, str) and sha.fullmatch(item) for item in (head, base)) or not isinstance(branch, str) or not branch:
            raise Refusal(f"PR head or base unavailable for {repo}#{number}")
        if not isinstance(module_name, str) or module_name not in CHECKS_AFTER or after != CHECKS_AFTER[module_name]:
            raise Refusal("migration marker checks_after does not match the module's CHECKS_AFTER; refusing")
        revision = args.revision if args.revert else head
        try:
            before = legacy_checks_at(repo, args.revision if args.revert else base)
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError, subprocess.SubprocessError, yaml.YAMLError) as exc:
            raise Refusal(f"cannot read legacy validate.yml for {repo}: {exc}") from exc
        if before != marker_before:
            raise Refusal(f"marker checks_before {marker_before} differs from validate.yml jobs {before}; refusing")
        client = GitHubClient(token)
        name = managed_name(*repo.split("/"))
        existing = _find_existing(list_rulesets(client, repo), name)
        if existing is not None and not isinstance(existing.get("id"), int):
            raise Refusal(f"managed ruleset {name} has no numeric id; refusing")
        if existing is not None:
            current, threshold = managed_state(client.request("GET", f"/repos/{repo}/rulesets/{existing['id']}"), branch)
        try:
            policy = required_checks(api_request, repo, branch)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            raise Refusal(f"required checks unavailable: {exc}") from exc
        if existing is None:
            detail, current = unmanaged_ruleset(client, repo, policy, name)
            ruleset = {"id": detail["id"], "name": detail["name"], "managed": False}
        else:
            ruleset = {"id": existing["id"], "name": name, "managed": True}
        try:
            new = plan(policy, ruleset["id"], current, before, after, args.revert, args.restore)
        except ValueError as exc:
            raise Refusal(str(exc)) from exc
        if existing is None:
            output = patch_required_checks(client, repo, detail, current, new, revision, args.dry_run)
        else:
            output = reconcile(client, repo, branch, new, revision, "active", args.dry_run, threshold)
    except Refusal as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except ReconcileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"repo": repo, "branch": branch, "revision": revision, "dry_run": args.dry_run, "ruleset": ruleset, "before": current, "after": new,
                      "removed": [item for item in current if item not in new], "added": [item for item in new if item not in current],
                      "action": output.get("action")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

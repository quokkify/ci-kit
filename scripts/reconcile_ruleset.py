#!/usr/bin/env python3
"""Safely reconcile one managed GitHub repository ruleset.

The command is deliberately separate from Copier generation. It defaults to
``evaluate``: that mode writes an evaluate-enforced ruleset and verifies its
readback; use ``--dry-run`` for a strictly non-mutating preview and ``--active``
for the explicit promotion to active enforcement.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class ReconcileError(RuntimeError):
    """A safe, actionable reconciliation failure."""


@dataclass
class GitHubClient:
    token: str
    api_base: str = "https://api.github.com"

    def request(self, method: str, path: str, payload: Mapping[str, Any] | None = None) -> Any:
        body = json.dumps(payload).encode() if payload is not None else None
        request = Request(
            self.api_base.rstrip("/") + path,
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                **({"Content-Type": "application/json"} if body else {}),
            },
        )
        try:
            with urlopen(request, timeout=30) as response:
                raw = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            suffix = f": {detail}" if detail else ""
            raise ReconcileError(f"GitHub API {method} {path} failed with HTTP {exc.code}{suffix}") from exc
        except (URLError, TimeoutError) as exc:
            raise ReconcileError(f"GitHub API {method} {path} failed: {exc}") from exc
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ReconcileError(f"GitHub API {method} {path} returned malformed JSON") from exc


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReconcileError(f"GitHub API returned malformed {label}: expected an object")
    return value


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ReconcileError(f"GitHub API returned malformed {label}: expected a list")
    return value


def managed_name(owner: str, repo: str) -> str:
    """Return the deterministic identity used to find this repository's managed ruleset."""
    return f"ci-kit/security/{owner}/{repo}"


def build_desired(name: str, branch: str, checks: list[str], mode: str, codeql_threshold: str | None) -> dict[str, Any]:
    """Build the complete desired ruleset request without performing I/O."""
    if not name or not branch or not checks or any(not item.strip() for item in checks):
        raise ReconcileError("ruleset name, branch, and at least one non-empty check are required")
    rules: list[dict[str, Any]] = [
        {"type": "deletion"},
        {"type": "non_fast_forward"},
        {
            "type": "pull_request",
            "parameters": {
                "required_approving_review_count": 1,
                "dismiss_stale_reviews_on_push": True,
                "require_code_owner_review": False,
                "require_last_push_approval": True,
                "required_review_thread_resolution": False,
            },
        },
        {
            "type": "required_status_checks",
            "parameters": {
                # integration_id is optional and must be omitted unless the
                # caller explicitly binds a check to a GitHub App.  GitHub's
                # 2022-11-28 schema accepts an integer here, not JSON null.
                "required_status_checks": [{"context": check} for check in sorted(set(checks))],
                "strict_required_status_checks_policy": True,
            },
        },
    ]
    if codeql_threshold is not None:
        if codeql_threshold not in {"none", "errors", "all"}:
            raise ReconcileError("--codeql-alert-threshold must be one of: none, errors, all")
        security_threshold = {"none": "none", "errors": "high_or_higher", "all": "all"}[codeql_threshold]
        rules.append({
            "type": "code_scanning",
            "parameters": {
                "code_scanning_tools": [{
                    "tool": "CodeQL",
                    "alerts_threshold": codeql_threshold,
                    "security_alerts_threshold": security_threshold,
                }]
            },
        })
    return {
        "name": name,
        "target": "branch",
        "enforcement": mode,
        "conditions": {"ref_name": {"include": [f"refs/heads/{branch}"], "exclude": []}},
        "rules": rules,
    }


def _normalize(value: Any) -> Any:
    """Normalize API values for order-independent exact policy comparison."""
    if isinstance(value, Mapping):
        return {key: _normalize(value[key]) for key in sorted(value) if key not in {"id", "node_id", "created_at", "updated_at", "etag"}}
    if isinstance(value, list):
        return sorted((_normalize(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True))
    return value


def _policy_view(value: Mapping[str, Any]) -> dict[str, Any]:
    """Compare managed fields and ignore GitHub response metadata/defaults."""
    conditions = _require_mapping(value.get("conditions"), "ruleset conditions")
    ref_name = _require_mapping(conditions.get("ref_name"), "ruleset ref_name condition")
    raw_rules = _require_list(value.get("rules"), "ruleset rules")
    rules: list[dict[str, Any]] = []
    for raw_rule in raw_rules:
        rule = _require_mapping(raw_rule, "ruleset rule")
        rule_type = rule.get("type")
        if not isinstance(rule_type, str):
            raise ReconcileError("GitHub API returned a ruleset rule without a string type")
        if rule_type == "pull_request":
            item: dict[str, Any] = {"type": rule_type}
            params = _require_mapping(rule.get("parameters"), "pull_request parameters")
            item["parameters"] = {key: params.get(key) for key in (
                "required_approving_review_count", "dismiss_stale_reviews_on_push",
                "require_code_owner_review", "require_last_push_approval",
                "required_review_thread_resolution")}
        elif rule_type == "required_status_checks":
            item = {"type": rule_type}
            params = _require_mapping(rule.get("parameters"), "status-check parameters")
            statuses = _require_list(params.get("required_status_checks"), "required status checks")
            item["parameters"] = {
                "required_status_checks": [{
                    "context": _require_mapping(status, "status check").get("context"),
                    "integration_id": _require_mapping(status, "status check").get("integration_id"),
                } for status in statuses],
                "strict_required_status_checks_policy": params.get("strict_required_status_checks_policy"),
            }
        elif rule_type == "code_scanning":
            item = {"type": rule_type}
            params = _require_mapping(rule.get("parameters"), "code-scanning parameters")
            tools = _require_list(params.get("code_scanning_tools"), "code-scanning tools")
            item["parameters"] = {"code_scanning_tools": [
                {key: _require_mapping(tool, "code-scanning tool").get(key) for key in (
                    "tool", "alerts_threshold", "security_alerts_threshold")}
                for tool in tools
            ]}
        else:
            # Unknown rules are operator-owned. Preserve and compare their
            # complete meaningful payload rather than silently reducing them
            # to just their type.
            item = _normalize(rule)
        rules.append(item)
    rules.sort(key=lambda item: json.dumps(item, sort_keys=True))
    return {
        "name": value.get("name"),
        "target": value.get("target"),
        "enforcement": value.get("enforcement"),
        "conditions": {"ref_name": {"include": ref_name.get("include"), "exclude": ref_name.get("exclude")}},
        "rules": rules,
    }


def _rule_types(rules: list[Any]) -> set[str]:
    """Validate rules and return their declared types."""
    types: set[str] = set()
    for rule in rules:
        mapping = _require_mapping(rule, "ruleset rule")
        rule_type = mapping.get("type")
        if not isinstance(rule_type, str):
            raise ReconcileError("GitHub API returned a ruleset rule without a string type")
        types.add(rule_type)
    return types


def _find_existing(rulesets: list[Any], name: str) -> Mapping[str, Any] | None:
    """Find the unique matching managed ruleset, rejecting duplicates or malformed entries."""
    matches = []
    for item in rulesets:
        mapping = _require_mapping(item, "ruleset")
        if mapping.get("name") == name:
            matches.append(mapping)
    if len(matches) > 1:
        raise ReconcileError(f"multiple managed rulesets named {name!r}; refusing to choose one")
    return matches[0] if matches else None


def _paged_check_runs(client: GitHubClient, repo: str, revision: str) -> list[Any]:
    """Fetch all check-run pages and fail closed when pagination is incomplete."""
    result: list[Any] = []
    page = 1
    while True:
        data = _require_mapping(client.request(
            "GET", f"/repos/{repo}/commits/{revision}/check-runs?per_page=100&page={page}"
        ), "check-runs response")
        runs = _require_list(data.get("check_runs"), "check_runs")
        result.extend(runs)
        total = data.get("total_count")
        if total is not None and (not isinstance(total, int) or total < 0):
            raise ReconcileError("GitHub API returned malformed check-runs total_count; refusing mutation")
        if total is not None and len(result) < total:
            if len(runs) == 0:
                raise ReconcileError("GitHub API returned incomplete paginated check-runs; refusing mutation")
            page += 1
            continue
        if total is None and len(runs) == 100:
            page += 1
            continue
        return result


def _paged_statuses(client: GitHubClient, repo: str, revision: str) -> list[Any]:
    """Fetch all commit-status pages."""
    result: list[Any] = []
    page = 1
    while True:
        statuses = _require_list(client.request(
            "GET", f"/repos/{repo}/commits/{revision}/statuses?per_page=100&page={page}"
        ), "statuses response")
        result.extend(statuses)
        if len(statuses) < 100:
            return result
        page += 1


def check_contexts(client: GitHubClient, repo: str, revision: str) -> set[str]:
    """Collect exact published check names for a commit revision."""
    runs = _paged_check_runs(client, repo, revision)
    contexts: set[str] = set()
    for run in runs:
        mapping = _require_mapping(run, "check run")
        if not isinstance(mapping.get("name"), str) or not mapping["name"].strip():
            raise ReconcileError("GitHub API returned malformed check_runs entry; refusing mutation")
        contexts.add(mapping["name"])
    statuses = _paged_statuses(client, repo, revision)
    for status in statuses:
        mapping = _require_mapping(status, "commit status")
        if not isinstance(mapping.get("context"), str) or not mapping["context"].strip():
            raise ReconcileError("GitHub API returned malformed statuses entry; refusing mutation")
        contexts.add(mapping["context"])
    return contexts


def list_rulesets(client: GitHubClient, repo: str) -> list[Any]:
    """Read every ruleset page; list entries intentionally omit rules."""
    result: list[Any] = []
    page = 1
    while True:
        page_items = _require_list(client.request(
            "GET", f"/repos/{repo}/rulesets?includes_parents=true&per_page=100&page={page}"
        ), "rulesets response")
        result.extend(page_items)
        if len(page_items) < 100:
            return result
        page += 1


def reconcile(client: GitHubClient, repo: str, branch: str, checks: list[str], revision: str | None, mode: str, dry_run: bool, codeql_threshold: str | None = None) -> dict[str, Any]:
    """Evaluate or safely reconcile the managed ruleset and verify its readback."""
    owner, _, repository = repo.partition("/")
    if not owner or not repository or repo.count("/") != 1:
        raise ReconcileError("--repo must be OWNER/REPOSITORY")
    name = managed_name(owner, repository)
    desired = build_desired(name, branch, checks, mode, codeql_threshold)
    if not dry_run and not revision:
        raise ReconcileError("--revision is required for evaluate/active mutation; refusing mutation")
    if revision:
        published = check_contexts(client, repo, revision)
        missing = sorted(set(checks) - published)
        if missing:
            raise ReconcileError(f"required check context(s) not published for {revision}: {', '.join(missing)}; refusing mutation")
    rulesets = list_rulesets(client, repo)
    existing = _find_existing(rulesets, name)
    action = "create" if existing is None else "update"
    if existing is not None:
        identifier = existing.get("id")
        if not isinstance(identifier, int):
            raise ReconcileError("managed ruleset response has no numeric id; refusing update")
        existing = _require_mapping(client.request("GET", f"/repos/{repo}/rulesets/{identifier}"), "managed ruleset detail")
        existing_rules = _require_list(existing.get("rules"), "managed rules")
        desired_types = _rule_types(desired["rules"])
        # Preserve policy owned by an operator inside the managed ruleset, while
        # replacing only the rule types controlled by this command.
        unmanaged: list[Mapping[str, Any]] = []
        for raw_rule in existing_rules:
            rule = _require_mapping(raw_rule, "managed ruleset rule")
            rule_type = rule.get("type")
            if not isinstance(rule_type, str) or not rule_type.strip():
                raise ReconcileError("GitHub API returned malformed managed ruleset rule; refusing mutation")
            if rule_type not in desired_types:
                if "parameters" in rule and not isinstance(rule["parameters"], Mapping):
                    raise ReconcileError("GitHub API returned malformed unmanaged rule parameters; refusing mutation")
                unmanaged.append(rule)
        desired["rules"] = desired["rules"] + unmanaged
    result: dict[str, Any] = {"action": action, "mode": mode, "dry_run": dry_run, "name": name, "desired": desired}
    if existing is not None and _policy_view(existing) == _policy_view(desired):
        result["action"] = "noop"
        result["actual"] = existing
        result["matches"] = True
        return result
    if dry_run:
        result["actual"] = existing
        result["matches"] = existing is not None and _policy_view(existing) == _policy_view(desired)
        return result
    identifier = existing.get("id") if existing else None
    if existing is not None and not isinstance(identifier, int):
        raise ReconcileError("managed ruleset response has no numeric id; refusing update")
    path = f"/repos/{repo}/rulesets/{identifier}" if identifier is not None else f"/repos/{repo}/rulesets"
    actual = client.request("PUT" if identifier is not None else "POST", path, desired)
    actual_mapping = _require_mapping(actual, "ruleset mutation response")
    readback_id = actual_mapping.get("id")
    if not isinstance(readback_id, int):
        raise ReconcileError("ruleset mutation response has no numeric id")
    readback = _require_mapping(client.request("GET", f"/repos/{repo}/rulesets/{readback_id}"), "ruleset readback")
    try:
        matches = _policy_view(readback) == _policy_view(desired)
    except ReconcileError as exc:
        raise ReconcileError("ruleset readback mismatch; refusing to report success") from exc
    if not matches:
        raise ReconcileError("ruleset readback mismatch; refusing to report success")
    result["actual"] = readback
    result["matches"] = True
    return result


def parser() -> argparse.ArgumentParser:
    """Create the command-line interface for ruleset reconciliation."""
    command = argparse.ArgumentParser(description=__doc__)
    command.add_argument("--repo", required=True, help="GitHub repository OWNER/REPOSITORY")
    command.add_argument("--branch", default="main")
    command.add_argument("--check", action="append", required=True, dest="checks", help="Exact published check context; repeatable")
    command.add_argument("--revision", help="Commit SHA used to preflight published check contexts")
    command.add_argument("--mode", choices=("evaluate", "active"), default="evaluate")
    command.add_argument("--active", action="store_true", help="Explicitly promote the managed ruleset to active")
    command.add_argument("--dry-run", action="store_true")
    command.add_argument("--codeql-alert-threshold", choices=("none", "errors", "all"))
    return command


def main(argv: list[str] | None = None) -> int:
    """Run the CLI, translating safe operational errors to exit status 2."""
    args = parser().parse_args(argv)
    if args.mode == "active" and not args.active:
        parser().error("--mode active requires the explicit --active flag")
    mode = "active" if args.active else args.mode
    try:
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if not token:
            raise ReconcileError("set GH_TOKEN or GITHUB_TOKEN; credentials are never accepted as CLI arguments")
        output = reconcile(GitHubClient(token), args.repo, args.branch, args.checks, args.revision, mode, args.dry_run, args.codeql_alert_threshold)
    except ReconcileError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

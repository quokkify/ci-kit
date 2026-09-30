"""Read-only, exact-revision GitHub evidence for fleet adoption reports."""
from __future__ import annotations

import base64
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
from typing import Any, Callable
from urllib.parse import quote

import yaml

SHA = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_EXCEPTIONS = Path(__file__).resolve().parents[1] / ".github/fleet-health-exceptions.json"


class HealthUnavailable(ValueError):
    pass


def load_exceptions(path: Path = DEFAULT_EXCEPTIONS) -> dict[str, dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "repositories"} or payload["schema_version"] != 1:
        raise ValueError("invalid fleet health exception configuration")
    exceptions = payload["repositories"]
    if not isinstance(exceptions, dict):
        raise ValueError("exceptions must be a repository mapping")
    for repository, item in exceptions.items():
        if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
            raise ValueError("invalid exception repository")
        if not isinstance(item, dict) or set(item) != {"reason", "expires_on"}:
            raise ValueError("exceptions require reason and expires_on only")
        if not isinstance(item["reason"], str) or not item["reason"].strip():
            raise ValueError("exception reason must not be empty")
        if not isinstance(item["expires_on"], str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", item["expires_on"]):
            raise ValueError("exception expiry must be YYYY-MM-DD")
        date.fromisoformat(item["expires_on"])
    return exceptions


def unknown(reason: str) -> dict[str, Any]:
    return {"status": "unknown", "reason": reason}


def pages(request: Callable[[str], Any], endpoint: str, key: str | None = None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for page in range(1, 101):
        separator = "&" if "?" in endpoint else "?"
        payload = request(f"{endpoint}{separator}per_page=100&page={page}")
        chunk = payload.get(key) if key and isinstance(payload, dict) else payload
        if not isinstance(chunk, list) or any(not isinstance(item, dict) for item in chunk):
            raise HealthUnavailable("malformed paginated evidence")
        items.extend(chunk)
        if len(chunk) < 100:
            return items
    raise HealthUnavailable("evidence pagination limit exceeded")


def revision(request: Callable[[str], Any], repository: str, ref: str) -> str:
    item = request(f"repos/{repository}/commits/{quote(ref, safe='')}")
    sha = item.get("sha") if isinstance(item, dict) else None
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise HealthUnavailable("revision unavailable")
    return sha


def required_checks(request: Callable[[str], Any], repository: str, branch: str) -> dict[str, Any]:
    rules = pages(request, f"repos/{repository}/rules/branches/{quote(branch, safe='')}")
    required: list[dict[str, Any]] = []
    for rule in rules:
        if rule.get("type") != "required_status_checks":
            continue
        checks = rule.get("parameters", {}).get("required_status_checks")
        if not isinstance(checks, list):
            raise HealthUnavailable("required checks unavailable")
        for check in checks:
            if not isinstance(check, dict) or not isinstance(check.get("context"), str) or not check["context"]:
                raise HealthUnavailable("required check identity unavailable")
            integration = check.get("integration_id")
            if integration is not None and (type(integration) is not int or integration < 0):
                raise HealthUnavailable("required check integration unavailable")
            required.append({"context": check["context"], "integration_id": integration, "source": "active-ruleset", "ruleset_id": rule.get("ruleset_id")})
    # Classic protection may coexist with rulesets. Distinguish a documented 404
    # from denied/malformed responses through the request adapter, not stderr.
    try:
        protection = request(f"repos/{repository}/branches/{quote(branch, safe='')}/protection/required_status_checks")
    except Exception:
        return {"status": "unknown", "reason": "classic branch protection evidence unavailable", "checks": required, "source": "active-branch-rules"}
    if protection is not None:
        checks = protection.get("checks") if isinstance(protection, dict) else None
        if not isinstance(checks, list):
            raise HealthUnavailable("classic branch protection unavailable")
        for check in checks:
            if not isinstance(check, dict) or not isinstance(check.get("context"), str) or not check["context"]:
                raise HealthUnavailable("classic required check identity unavailable")
            app_id = check.get("app_id")
            if app_id is not None and (type(app_id) is not int or app_id < -1):
                raise HealthUnavailable("classic required check integration unavailable")
            required.append({"context": check["context"], "integration_id": None if app_id == -1 else app_id, "source": "classic-protection"})
    return {"status": "active" if required else "unprotected", "checks": required, "source": "active-branch-rules-and-classic-protection"}


def check_evidence(request: Callable[[str], Any], repository: str, sha: str, policy: dict[str, Any], pull_request: dict[str, Any] | None = None) -> dict[str, Any]:
    if not policy.get("checks"):
        return {"sha": sha, **unknown("active required checks unavailable" if policy.get("status") == "unknown" else "no active required checks")}
    runs = pages(request, f"repos/{repository}/commits/{sha}/check-runs?filter=latest", "check_runs")
    statuses = pages(request, f"repos/{repository}/commits/{sha}/statuses")
    observed: list[dict[str, Any]] = []
    for required in policy["checks"]:
        context, integration = required["context"], required["integration_id"]
        matching = [run for run in runs if run.get("name") == context and run.get("head_sha") == sha and (integration is None or run.get("app", {}).get("id") == integration)]
        matching.sort(key=lambda run: run.get("id", 0), reverse=True)
        if matching and pull_request is not None:
            latest = matching[0]
            associated = any(
                item.get("number") == pull_request["number"]
                and item.get("head", {}).get("sha") == sha
                and item.get("base", {}).get("sha") == pull_request["base_sha"]
                for item in latest.get("pull_requests", []) if isinstance(item, dict)
            )
            if not associated:
                matching = []
        if matching:
            run = matching[0]
            state = "pending" if run.get("status") != "completed" else "passed" if run.get("conclusion") == "success" else "failed" if run.get("conclusion") in {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"} else "unknown"
            observed.append({"context": context, "status": state, "sha": sha, "check_run_id": run.get("id"), "app_id": run.get("app", {}).get("id"), "conclusion": run.get("conclusion"), "url": run.get("html_url")})
        else:
            matching_statuses = [status for status in statuses if status.get("context") == context] if integration is None and pull_request is None else []
            matching_statuses.sort(key=lambda item: item.get("id", 0), reverse=True)
            if matching_statuses:
                item = matching_statuses[0]
                state = {"success": "passed", "failure": "failed", "error": "failed", "pending": "pending"}.get(item.get("state"), "unknown")
                observed.append({"context": context, "status": state, "sha": sha, "commit_status_id": item.get("id"), "creator": item.get("creator", {}).get("login"), "url": item.get("target_url")})
            else:
                observed.append({"context": context, **unknown("no matching exact-revision check evidence")})
    states = {item["status"] for item in observed}
    state = "failed" if "failed" in states else "unknown" if "unknown" in states else "pending" if "pending" in states else "passed"
    if policy.get("status") == "unknown":
        state = "unknown"
    return {"sha": sha, "status": state, "checks": observed, "policy_coverage": policy.get("status", "unknown")}


def collect_health(repository: str, default_branch: str, target: str | None, automation_branch: str, request: Callable[[str], Any], exception: dict[str, str] | None = None, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    result: dict[str, Any] = {"observed_at": now.isoformat(), "target_template_version": target, "default_branch": default_branch, "applied_template_version": None, "default_sha": None, "adoption": "unknown", "pull_request": None}
    if exception:
        result["exception"] = {**exception, "status": "expired" if date.fromisoformat(exception["expires_on"]) < now.date() else "active"}
    else:
        result["exception"] = None
    try:
        metadata = request(f"repos/{repository}")
        branch = metadata.get("default_branch") if isinstance(metadata, dict) else None
        if not isinstance(branch, str) or not branch:
            raise HealthUnavailable("default branch unavailable")
        result["default_branch"] = branch
        sha = revision(request, repository, branch)
        result["default_sha"] = sha
        content = request(f"repos/{repository}/contents/.copier-answers.yml?ref={sha}")
        if not isinstance(content, dict) or content.get("encoding") != "base64" or not isinstance(content.get("content"), str):
            raise HealthUnavailable("applied template answers unavailable")
        answers = yaml.safe_load(base64.b64decode("".join(content["content"].split()), validate=True).decode("utf-8"))
        applied = answers.get("_commit") if isinstance(answers, dict) else None
        if not isinstance(applied, str) or not applied:
            raise HealthUnavailable("applied template version unavailable")
        result["applied_template_version"] = applied
        result["adoption"] = "current" if target and applied == target else "different" if target else "unknown"
    except Exception:
        result["default_ci"] = unknown("default revision or applied template evidence unavailable")
        result["required_checks"] = unknown("branch policy unavailable")
        return result
    try:
        policy = required_checks(request, repository, branch)
    except Exception:
        policy = unknown("active branch protection evidence unavailable")
    result["required_checks"] = policy
    try:
        result["default_ci"] = check_evidence(request, repository, sha, policy)
    except Exception:
        result["default_ci"] = {"sha": sha, **unknown("exact-revision CI evidence unavailable")}
    try:
        endpoint = f"repos/{repository}/pulls?state=all&head={quote(repository.split('/')[0] + ':' + automation_branch, safe='')}&base={quote(branch, safe='')}&sort=updated&direction=desc"
        pulls = pages(request, endpoint)
        valid = [pull for pull in pulls if pull.get("head", {}).get("ref") == automation_branch and pull.get("head", {}).get("repo", {}).get("full_name", "").casefold() == repository.casefold() and pull.get("base", {}).get("ref") == branch]
        if valid:
            pull = max(valid, key=lambda item: item.get("number", 0))
            head = pull.get("head", {}).get("sha")
            if not isinstance(head, str) or not SHA.fullmatch(head):
                raise HealthUnavailable("PR revision unavailable")
            base_sha = pull.get("base", {}).get("sha")
            if not isinstance(base_sha, str) or not SHA.fullmatch(base_sha):
                raise HealthUnavailable("PR base revision unavailable")
            created = datetime.fromisoformat(pull["created_at"].replace("Z", "+00:00"))
            pr = {"url": f"https://github.com/{repository}/pull/{pull['number']}", "number": pull["number"], "state": "merged" if pull.get("merged_at") else pull.get("state"), "age_days": max(0, (now - created).days), "head_sha": head, "base_branch": branch, "base_sha": base_sha, "merge_sha": pull.get("merge_commit_sha")}
            result["pull_request"] = pr
            try:
                pr["ci"] = check_evidence(request, repository, head, policy, pr)
                if base_sha != sha:
                    pr["ci"] = {"sha": head, **unknown("pull request base differs from the current default revision")}
                current = request(f"repos/{repository}/pulls/{pull['number']}")
                if (
                    current.get("head", {}).get("sha") != head
                    or current.get("base", {}).get("sha") != base_sha
                    or current.get("merge_commit_sha") != pr["merge_sha"]
                    or current.get("head", {}).get("ref") != automation_branch
                    or current.get("base", {}).get("ref") != branch
                    or current.get("head", {}).get("repo", {}).get("full_name", "").casefold() != repository.casefold()
                ):
                    pr["ci"] = {"sha": head, **unknown("pull request head or base identity changed during observation")}
            except Exception:
                pr["ci"] = {"sha": head, **unknown("pull request CI evidence unavailable")}
        result["pull_request_observation"] = {"status": "observed"}
    except Exception:
        result["pull_request_observation"] = unknown("automation pull request evidence unavailable")
    try:
        current_metadata = request(f"repos/{repository}")
        if current_metadata.get("default_branch") != branch or revision(request, repository, branch) != sha:
            result["adoption"] = "unknown"
            result["default_ci"] = {"sha": sha, **unknown("default revision changed during observation")}
            if result["pull_request"] is not None:
                result["pull_request"]["ci"] = {"sha": result["pull_request"]["head_sha"], **unknown("current default revision changed during observation")}
    except Exception:
        result["adoption"] = "unknown"
        result["default_ci"] = {"sha": sha, **unknown("default revision could not be rechecked")}
        if result["pull_request"] is not None:
            result["pull_request"]["ci"] = {"sha": result["pull_request"]["head_sha"], **unknown("current default revision could not be rechecked")}
    return result

#!/usr/bin/env python3
"""Read-only, bounded GitHub CI gates for releases and public fleet pilots."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from collections.abc import Callable
from urllib.parse import quote

API_DEADLINE: float | None = None


class GateError(RuntimeError):
    """A gate cannot establish that the exact revision is healthy."""


def api(endpoint: str) -> object:
    """Use the current gh credential; never write or silently ignore API errors."""
    try:
        timeout = 45.0 if API_DEADLINE is None else min(45.0, API_DEADLINE - time.monotonic())
        if timeout <= 0:
            raise GateError("CI read deadline expired")
        result = subprocess.run(
            ["gh", "api", endpoint], check=True, capture_output=True, text=True, timeout=timeout
        )
        return json.loads(result.stdout)
    except (subprocess.SubprocessError, ValueError) as exc:
        raise GateError(f"GitHub read failed for {endpoint}") from exc


def pages(endpoint: str, key: str | None = None) -> list[dict]:
    """Read all pages, with a conservative upper bound instead of truncation."""
    items = []
    separator = "&" if "?" in endpoint else "?"
    for page in range(1, 11):
        payload = api(f"{endpoint}{separator}per_page=100&page={page}")
        batch = payload.get(key) if key and isinstance(payload, dict) else payload
        if not isinstance(batch, list) or not all(isinstance(item, dict) for item in batch):
            raise GateError(f"Malformed GitHub response for {endpoint}")
        items.extend(batch)
        if len(batch) < 100:
            return items
    raise GateError(f"GitHub response exceeded the bounded pagination limit: {endpoint}")


def wait_until(check: Callable[[], bool], timeout: int, interval: int = 15) -> None:
    global API_DEADLINE
    deadline = time.monotonic() + timeout
    previous = API_DEADLINE
    # A zero-timeout freshness check performs one bounded read, without polling.
    API_DEADLINE = deadline if timeout else time.monotonic() + 60
    try:
        while True:
            if check():
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GateError("Timed out waiting for current-revision CI evidence")
            time.sleep(min(interval, remaining))
    finally:
        API_DEADLINE = previous


def association_matches(item: dict, pr: int, snapshot: tuple[str, str, str]) -> bool:
    return any(isinstance(pull, dict) and pull.get("number") == pr
               and pull.get("head", {}).get("sha") == snapshot[0]
               and pull.get("base", {}).get("sha") == snapshot[1]
               for pull in item.get("pull_requests", []))


def workflows_ready(repository: str, sha: str, workflows: tuple[str, ...], release: bool = False,
                    pr: int | None = None, snapshot: tuple[str, str, str] | None = None) -> bool:
    for workflow in workflows:
        filters = "&event=push&branch=main" if release else ""
        runs = pages(
            f"repos/{repository}/actions/workflows/{workflow}/runs?head_sha={sha}{filters}",
            "workflow_runs",
        )
        matching = [r for r in runs if r.get("head_sha") == sha
                    and (not release or (r.get("event") == "push" and r.get("head_branch") == "main"))]
        if not matching:
            return False
        latest = max(matching, key=lambda r: (r["id"], r.get("run_attempt", 1)))
        if pr is not None and (snapshot is None or not association_matches(latest, pr, snapshot)):
            raise GateError(f"{workflow} lacks CI association with the current pilot head/base; update or rerun the PR")
        if latest.get("status") != "completed":
            return False
        if latest.get("conclusion") != "success":
            raise GateError(f"{workflow} failed for {sha}: {latest.get('conclusion')}")
    return True


def release_ready(repository: str, sha: str) -> bool:
    return workflows_ready(repository, sha, ("validate-toolkit.yml", "codeql.yml"), release=True)


def preflight_ready(repository: str, sha: str) -> bool:
    current = api(f"repos/{repository}/commits/main")
    if not isinstance(current, dict) or current.get("sha") != sha:
        raise GateError("Main advanced since this release run started; rerun on the current revision")
    if not release_ready(repository, sha):
        return False
    candidates = pages(f"repos/{repository}/issues?state=closed&labels=autorelease%3A%20pending")
    for issue in candidates:
        if "pull_request" not in issue:
            continue
        pull = api(f"repos/{repository}/pulls/{issue['number']}")
        if not isinstance(pull, dict):
            raise GateError("Malformed pending release PR response")
        if not pull.get("merged") or pull.get("base", {}).get("ref") != "main":
            continue
        candidate_sha = pull.get("merge_commit_sha")
        if not isinstance(candidate_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", candidate_sha):
            raise GateError("Pending release PR has no exact merged revision")
        if not release_ready(repository, candidate_sha):
            return False
    # API reads above can take time. An advanced branch must not pass preflight.
    current = api(f"repos/{repository}/commits/main")
    if not isinstance(current, dict) or current.get("sha") != sha:
        raise GateError("Main advanced during release preflight")
    return True


# Explicit public-pilot policy is usable by a public-scoped token without branch-
# administration access. CodeQL is separately verified by workflow identity;
# its external code-scanning rule need not appear in the check-runs API.
PILOT_CHECKS = {
    "quokkify/gh-pages-subdir-action": ("Scan Git history and current tree", "Validation pipeline complete", "Shared template contract"),
    "quokkify/skills": ("Gitleaks", "App Python / ci", "Shared template contract"),
    "quokkify/q4j": ("Scan current tree (legacy history pending baseline)", "Tests pipeline complete", "Shared template contract"),
}
# Contexts produced only by pull_request workflows. A pilot that is already up to date is verified on its
# default branch, where these checks never run, so waiting for them could only time out.
PILOT_PULL_REQUEST_ONLY_CHECKS = {
    "quokkify/q4j": ("Tests pipeline complete",),
}
DEFAULT_PILOT_TIMEOUT = 900
# Pilot CI that outlasts the default wait: the q4j module tests take 16 to 34 minutes.
PILOT_TIMEOUTS = {
    "quokkify/q4j": 2700,
}


def pilot_contexts(repository: str, pr: int | None) -> tuple[str, ...]:
    contexts = PILOT_CHECKS[repository]
    if pr is None:
        pull_request_only = PILOT_PULL_REQUEST_ONLY_CHECKS.get(repository, ())
        contexts = tuple(context for context in contexts if context not in pull_request_only)
    return contexts


def current_snapshot(repository: str, pr: int | None) -> tuple[str, str, str]:
    metadata = api(f"repos/{repository}")
    if not isinstance(metadata, dict) or metadata.get("visibility") != "public" or metadata.get("private") is not False:
        raise GateError(f"Pilot must be public: {repository}")
    if pr is None:
        commit = api(f"repos/{repository}/commits/{quote(metadata['default_branch'], safe='')}")
        sha = commit.get("sha") if isinstance(commit, dict) else None
        base_sha = merge_sha = ""
    else:
        pull = api(f"repos/{repository}/pulls/{pr}")
        if (not isinstance(pull, dict) or pull.get("state") != "open"
                or pull.get("base", {}).get("ref") != metadata["default_branch"]
                or pull.get("head", {}).get("repo", {}).get("full_name") != repository
                or pull.get("head", {}).get("ref") != "automation/copier-template-update"):
            raise GateError(f"Unexpected pilot PR identity: {repository}#{pr}")
        sha = pull.get("head", {}).get("sha")
        base_sha = pull.get("base", {}).get("sha")
        merge_sha = pull.get("merge_commit_sha")
        if not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) for value in (base_sha, merge_sha)):
            raise GateError(f"Pilot merge/base revision unavailable: {repository}#{pr}; update or rerun the PR")
        target = api(f"repos/{repository}/commits/{quote(metadata['default_branch'], safe='')}")
        if not isinstance(target, dict) or target.get("sha") != base_sha:
            raise GateError(f"Pilot base is behind the current target branch: {repository}#{pr}; update or rerun the PR")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise GateError(f"Missing pilot revision: {repository}")
    return sha, base_sha, merge_sha


def current_revision(repository: str, pr: int | None) -> str:
    return current_snapshot(repository, pr)[0]


def pilot_ready(repository: str, sha: str, contexts: tuple[str, ...], pr: int | None = None,
                snapshot: tuple[str, str, str] | None = None) -> bool:
    if pr is not None and current_snapshot(repository, pr) != snapshot:
        raise GateError(f"Pilot head/base/merge changed during CI wait: {repository}; update or rerun the PR")
    runs = pages(f"repos/{repository}/commits/{sha}/check-runs?filter=latest", "check_runs")
    statuses = pages(f"repos/{repository}/commits/{sha}/statuses")
    evidence = {}
    latest_runs = {}
    for run in sorted(runs, key=lambda r: r["id"]):
        if run.get("head_sha") != sha:
            continue
        # Only checks produced by GitHub Actions are trusted for this policy.
        if run.get("app", {}).get("slug") not in {"github-actions", "github-advanced-security"}:
            continue
        latest_runs[run["name"]] = run
    for run in latest_runs.values():
        if (pr is not None and run.get("name") in contexts
                and (snapshot is None or not association_matches(run, pr, snapshot))):
            raise GateError(f"Pilot check {run.get('name')} lacks current head/base association; update or rerun the PR")
        evidence[run["name"]] = (run.get("status") == "completed", run.get("conclusion"))
    # Legacy CodeQL status is published separately from matrix analysis checks.
    # GitHub statuses are newest first; preserve the newest status per context.
    latest_statuses = {}
    for status in sorted(statuses, key=lambda s: s["id"], reverse=True):
        latest_statuses.setdefault(status["context"], status)
    # Statuses can block the gate, but cannot satisfy a required Actions check:
    # an arbitrary token must not spoof a required context by display name.
    for context, status in latest_statuses.items():
        if status.get("state") == "pending":
            evidence[f"status:{context}"] = (False, None)
        elif status.get("state") != "success":
            raise GateError(f"Pilot {repository}@{sha}: status {context} is {status.get('state')}")
    ready = True
    for context in contexts:
        completed, outcome = evidence.get(context, (False, None))
        if not completed:
            ready = False
        elif outcome != "success":
            raise GateError(f"Pilot {repository}@{sha}: {context} is {outcome}")
    # Extra failing or unfinished checks also stop propagation. Skip/neutral is
    # allowed only for non-required optional checks, never for pilot contexts.
    for context, (completed, outcome) in evidence.items():
        if not completed:
            ready = False
        elif outcome not in {"success", "skipped", "neutral"}:
            raise GateError(f"Pilot {repository}@{sha}: {context} is {outcome}")
    return workflows_ready(repository, sha, ("codeql.yml",), pr=pr, snapshot=snapshot) and ready


def verify_pilot(repository: str, pr: int | None, target: str, timeout: int,
                 expected_snapshot: tuple[str, str, str] | None = None) -> str:
    import base64
    import yaml
    from update_copier_fleet import normalize_template_source

    snapshot = current_snapshot(repository, pr)
    if expected_snapshot is not None and snapshot != expected_snapshot:
        raise GateError(f"Pilot changed before fanout: {repository}; update or rerun the PR")
    sha = snapshot[0]
    contents = api(f"repos/{repository}/contents/.copier-answers.yml?ref={sha}")
    try:
        answers = yaml.safe_load(base64.b64decode(contents["content"]).decode())
    except (KeyError, TypeError, ValueError, yaml.YAMLError) as exc:
        raise GateError(f"Cannot read pilot answers: {repository}@{sha}") from exc
    if not isinstance(answers, dict) or answers.get("_commit") != target:
        raise GateError(f"Pilot {repository}@{sha} is not on {target}")
    source = answers.get("_src_path")
    if not isinstance(source, str) or normalize_template_source(source) != "quokkify/ci-kit":
        raise GateError(f"Pilot {repository}@{sha} uses a different template source")
    contexts = pilot_contexts(repository, pr)
    wait_until(lambda: pilot_ready(repository, sha, contexts, pr=pr, snapshot=snapshot), timeout)
    if current_snapshot(repository, pr) != snapshot:
        raise GateError(f"Pilot head/base/merge changed while checking: {repository}; update or rerun the PR")
    return sha


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    revision = parser.add_mutually_exclusive_group(required=True)
    revision.add_argument("--sha")
    revision.add_argument("--ref", help="Verify the actual released tag revision")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", args.repository) or (args.sha and not re.fullmatch(r"[0-9a-f]{40}", args.sha)):
        parser.error("repository and exact SHA must be valid")
    if not 0 <= args.timeout <= 3600:
        parser.error("timeout must be between 0 and 3600 seconds")
    try:
        if args.ref:
            if args.preflight or not re.fullmatch(r"v\d+\.\d+\.\d+", args.ref):
                raise GateError("Post-release gate requires an exact release tag")
            commit = api(f"repos/{args.repository}/commits/{quote(args.ref, safe='')}")
            args.sha = commit.get("sha") if isinstance(commit, dict) else None
            if not isinstance(args.sha, str) or not re.fullmatch(r"[0-9a-f]{40}", args.sha):
                raise GateError("Release tag did not resolve to an exact SHA")
        check = preflight_ready if args.preflight else release_ready
        wait_until(lambda: check(args.repository, args.sha), args.timeout)
    except GateError as exc:
        print(exc)
        return 1
    print(f"Release CI verified for {args.repository}@{args.sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

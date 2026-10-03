#!/usr/bin/env python3
"""Validate toolkit policy, templates, and executable language fixtures."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import yaml

from validate_helpers import action_reference_errors, load_yaml_or_error

from validate_python_fixture import validate_python_fixture

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []
PARSER = argparse.ArgumentParser(description=__doc__)
PARSER.add_argument(
    "--static",
    action="store_true",
    help="run shared policy, template, and static checks without language fixtures",
)
ARGS = PARSER.parse_args() if __name__ == "__main__" else PARSER.parse_args([])


def check(condition: bool, message: str) -> None:
    """Record a validation error when a condition is false."""
    if not condition:
        ERRORS.append(message)


def run(cmd: list[str], cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    """Run a command with visible, repository-relative provenance."""
    print(
        "+",
        " ".join(cmd),
        f"(cwd={cwd.relative_to(ROOT) if cwd.is_relative_to(ROOT) else cwd})",
    )
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def release_fleet_follow_up_errors(job: object, rel: Path) -> list[str]:
    """Validate the optional job that propagates a fresh tag to the Copier fleet.

    Release Please tags with GITHUB_TOKEN, and GITHUB_TOKEN events never start a new
    workflow run, so the fleet update has to be chained inside the release run. That
    chaining must stay gated on an actual release, or every push to main would run the
    write-capable fleet updater.
    """
    if job is None:
        return []
    errors: list[str] = []

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(f"{rel}: fleet-update job {message}")

    if not isinstance(job, dict):
        return [f"{rel}: fleet-update job must be a mapping"]

    require(
        job.get("uses") == "./.github/workflows/copier-fleet-auto-update.yml",
        "must use ./.github/workflows/copier-fleet-auto-update.yml",
    )
    require("steps" not in job and "runs-on" not in job, "must not define steps or runs-on")
    require(job.get("needs") == ["release", "verify-tag"], "must depend on release and the actual-tag CI gate")
    require(
        "needs.release.outputs.releases-created == 'true'" in str(job.get("if", "")),
        "must only run when Release Please tagged a release",
    )
    require(
        job.get("with") == {"template-ref": "${{ needs.release.outputs.tag-name }}"},
        "must pass exactly the tag Release Please created as template-ref",
    )
    require(job.get("secrets") == "inherit", "must inherit secrets for the write-mode token")
    return errors


def release_workflow_errors(path: Path) -> list[str]:
    """Validate the repository-level Release Please driver workflow contract."""
    errors: list[str] = []
    rel = path.relative_to(ROOT)

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(f"{rel}: {message}")

    try:
        workflow = yaml.safe_load(path.read_text())
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return [f"{rel}: YAML parse failed: {exc}"]
    if not isinstance(workflow, dict):
        return [f"{rel}: workflow root must be a mapping"]

    triggers = workflow.get("on", workflow.get(True))
    require(isinstance(triggers, dict), "must define push and workflow_dispatch triggers")
    if isinstance(triggers, dict):
        require(
            set(triggers) == {"push", "workflow_dispatch"},
            "must define only push and workflow_dispatch triggers",
        )
        require(
            triggers.get("push") == {"branches": ["main"]},
            "push trigger must be limited to branches: [main]",
        )
        require("workflow_dispatch" in triggers, "must include workflow_dispatch")

    require(
        workflow.get("permissions")
        == {"actions": "read", "contents": "write", "pull-requests": "write"},
        "permissions must be actions:read, contents:write and pull-requests:write",
    )
    require(
        workflow.get("concurrency")
        == {
            "group": "release-${{ github.workflow }}-${{ github.repository }}",
            "cancel-in-progress": False,
        },
        "concurrency must use the repository-scoped release group and must not cancel in-progress runs",
    )

    jobs = workflow.get("jobs")
    require(isinstance(jobs, dict), "jobs block must be a mapping")
    require(
        isinstance(jobs, dict)
        and "release" in jobs
        and "verify-release" in jobs
        and "verify-tag" in jobs
        and set(jobs) <= {"verify-release", "release", "verify-tag", "fleet-update"},
        "must define the release caller and its exact-revision CI gate",
    )
    caller_jobs = (
        [
            job
            for name, job in jobs.items()
            if name == "release" and isinstance(job, dict) and "uses" in job
        ]
        if isinstance(jobs, dict)
        else []
    )
    require(len(caller_jobs) == 1, "must define exactly one reusable workflow caller job")
    if not caller_jobs:
        return errors

    errors.extend(release_fleet_follow_up_errors(jobs.get("fleet-update"), rel))

    job = caller_jobs[0]
    require(job.get("needs") == "verify-release", "release must depend on its CI gate")
    gate = jobs.get("verify-release", {})
    require(gate.get("permissions") == {"actions": "read", "contents": "read", "pull-requests": "read"}, "release gate must be read-only")
    require(gate.get("timeout-minutes") == 35, "release gate must have a bounded job timeout")
    require("github.ref == 'refs/heads/main'" in str(gate.get("if", "")), "release preflight must be restricted to main")
    gate_steps = gate.get("steps", [])
    require(any(isinstance(step, dict) and step.get("with", {}).get("ref") == "${{ github.sha }}" for step in gate_steps), "release gate must check out the exact triggering commit")
    require(any(isinstance(step, dict) and "scripts/verify_ci_gate.py" in str(step.get("run", ""))
                and "--preflight" in str(step.get("run", ""))
                and step.get("env", {}).get("RELEASE_SHA") == "${{ github.sha }}" for step in gate_steps), "release gate must verify CI for the triggering SHA")
    tag_gate = jobs.get("verify-tag", {})
    require(tag_gate.get("needs") == "release", "actual-tag gate must depend on release")
    require(tag_gate.get("permissions") == {"actions": "read", "contents": "read"}, "actual-tag gate must be read-only")
    require(tag_gate.get("timeout-minutes") == 35, "actual-tag gate must have a bounded timeout")
    require("needs.release.outputs.releases-created == 'true'" in str(tag_gate.get("if", "")), "actual-tag gate must run only for a new release")
    require(any(isinstance(step, dict) and "--ref" in str(step.get("run", ""))
                and step.get("env", {}).get("RELEASE_TAG") == "${{ needs.release.outputs.tag-name }}"
                for step in tag_gate.get("steps", [])), "actual-tag gate must verify the tag Release Please created")
    require(
        job.get("uses") == "./.github/workflows/release-please.yml",
        "caller job must use ./.github/workflows/release-please.yml",
    )
    require(
        "steps" not in job and "runs-on" not in job,
        "caller job must not define steps or runs-on",
    )
    require(
        job.get("with")
        == {
            "mode": "manifest",
            "config-file": ".github/release-please/config.json",
            "manifest-file": ".github/release-please/manifest.json",
            "enrich-release-notes": True,
        },
        "caller job must pass manifest mode and current config/manifest paths",
    )
    return errors


def _version_tuple(value: str) -> tuple[int, int, int] | None:
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        return None
    major, minor, patch = (int(part) for part in value.split("."))
    return (major, minor, patch)


def copier_fleet_workflow_errors(path: Path, job_name: str = "audit") -> list[str]:
    """Validate a Copier fleet workflow and its pin."""

    errors: list[str] = []
    rel = path.relative_to(ROOT)

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(f"{rel}: {message}")

    try:
        workflow = yaml.safe_load(path.read_text())
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return [f"{rel}: YAML parse failed: {exc}"]
    if not isinstance(workflow, dict):
        return [f"{rel}: workflow root must be a mapping"]

    jobs = workflow.get("jobs")
    require(isinstance(jobs, dict), "jobs block must be a mapping")
    if not isinstance(jobs, dict):
        return errors

    audit = jobs.get(job_name)
    require(isinstance(audit, dict), f"{job_name} job must be present")
    if not isinstance(audit, dict):
        return errors

    steps = audit.get("steps")
    require(isinstance(steps, list), f"{job_name} job must define steps")
    if not isinstance(steps, list):
        return errors

    setup_step = next(
        (
            step
            for step in steps
            if isinstance(step, dict)
            and step.get("name") == "Set up Python and Copier"
            and step.get("uses") == "./actions/setup-python"
        ),
        None,
    )
    require(setup_step is not None, "missing Set up Python and Copier step")
    if not isinstance(setup_step, dict):
        return errors

    with_block = setup_step.get("with")
    require(isinstance(with_block, dict), "setup step must define with:")
    if not isinstance(with_block, dict):
        return errors

    install_command = with_block.get("install-command")
    require(isinstance(install_command, str), "setup step must pin Copier via install-command")
    if not isinstance(install_command, str):
        return errors

    match = re.fullmatch(r"python -m pip install copier==(\d+\.\d+\.\d+)", install_command)
    require(bool(match), "install-command must be a direct copier==X.Y.Z pin")
    if not match:
        return errors

    pin = match.group(1)
    pin_tuple = _version_tuple(pin)
    require(pin_tuple is not None, "Copier pin must be an exact semantic version")
    copier_config = load_yaml_or_error(ROOT / "copier.yml", ERRORS, "copier.yml")
    min_version = copier_config.get("_min_copier_version") if isinstance(copier_config, dict) else None
    min_tuple = _version_tuple(min_version) if isinstance(min_version, str) else None
    require(min_tuple is not None, "copier.yml:_min_copier_version must be an exact semantic version")
    if pin_tuple is not None and min_tuple is not None:
        require(
            pin_tuple >= min_tuple,
            "Copier fleet audit pin is lower than copier.yml:_min_copier_version",
        )

    return errors


def copier_fleet_auto_update_workflow_errors(path: Path) -> list[str]:
    """Guard the invariants that keep the public update front away from private repositories.

    The security argument for storing a write-capable token in this public repository rests on
    three properties of this file. Deleting any of them leaves every test and every other check
    green while the token starts running unbounded, so assert them here.
    """

    errors = copier_fleet_workflow_errors(path, job_name="update")
    rel = path.relative_to(ROOT)

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(f"{rel}: {message}")

    workflow = load_yaml_or_error(path, errors, str(rel))
    if not isinstance(workflow, dict):
        return errors

    permissions = workflow.get("permissions")
    require(
        permissions == {"contents": "read"},
        f"top-level permissions must be exactly contents: read, found {permissions!r}",
    )
    require(
        workflow.get("concurrency")
        == {
            "group": "copier-fleet-auto-update-${{ inputs.repository || 'fleet' }}",
            "cancel-in-progress": False,
        },
        "concurrency must isolate targeted repositories while serializing full-fleet runs",
    )

    jobs = workflow.get("jobs")
    update = jobs.get("update") if isinstance(jobs, dict) else None
    if not isinstance(update, dict):
        return errors

    require(
        "github.repository == 'quokkify/ci-kit'" in str(update.get("if", "")),
        "update job must be guarded against forks",
    )

    steps = update.get("steps")
    update_step = next(
        (
            step
            for step in steps
            if isinstance(step, dict) and step.get("name") == "Update Copier fleet"
        ),
        None,
    ) if isinstance(steps, list) else None
    require(isinstance(update_step, dict), "missing Update Copier fleet step")
    if not isinstance(update_step, dict):
        return errors

    script = str(update_step.get("run", ""))
    require("--public-only" in script, "update step must pass --public-only")
    require("--write" in script, "update step must be able to run in write mode")
    require("python scripts/rollout_copier_fleet.py" in script, "write front must use the pilot rollout coordinator")

    env = update_step.get("env")
    require(isinstance(env, dict), "update step must define env:")
    if not isinstance(env, dict):
        return errors

    write_token = str(env.get("GH_TOKEN", ""))
    require(
        "secrets." in write_token and "github.token" not in write_token,
        "GH_TOKEN must come from a secret with no github.token fallback",
    )

    return errors


def is_semver(value: object) -> bool:
    """Return whether value is SemVer 2.0.0, using only linear-time parsing."""
    if not isinstance(value, str) or not value:
        return False

    version_and_prerelease, separator, build = value.partition("+")
    if separator and (
        not build
        or "+" in build
        or any(
            not identifier
            or any(character not in ALPHANUMERIC_HYPHEN for character in identifier)
            for identifier in build.split(".")
        )
    ):
        return False

    core, separator, prerelease = version_and_prerelease.partition("-")
    if separator:
        if not prerelease:
            return False
        for identifier in prerelease.split("."):
            if not identifier or any(
                character not in ALPHANUMERIC_HYPHEN for character in identifier
            ):
                return False
            if is_ascii_digits(identifier) and len(identifier) > 1 and identifier[0] == "0":
                return False

    core_parts = core.split(".")
    return len(core_parts) == 3 and all(
        is_ascii_digits(part) and (part == "0" or part[0] != "0")
        for part in core_parts
    )


ALPHANUMERIC_HYPHEN = frozenset(
    "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-"
)


def is_ascii_digits(value: str) -> bool:
    return bool(value) and all("0" <= character <= "9" for character in value)


def release_manifest_errors(manifest: object) -> list[str]:
    """Return errors for the single-package Release Please manifest contract."""
    if not isinstance(manifest, dict) or set(manifest) != {"."}:
        return ["manifest must contain exactly the root package key '.'"]
    version = manifest["."]
    if not is_semver(version):
        return ["root package version must be a well-formed SemVer string"]
    return []


def validate_toolkit_workflow_errors(
    path: Path, workflow_override: object | None = None
) -> list[str]:
    """Validate the runner-selection DAG and isolated repository micro-jobs."""
    errors: list[str] = []
    rel = path.relative_to(ROOT)

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(f"{rel}: {message}")

    if workflow_override is None:
        try:
            workflow = yaml.safe_load(path.read_text())
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            return [f"{rel}: YAML parse failed: {exc}"]
    else:
        workflow = workflow_override
    if not isinstance(workflow, dict):
        return [f"{rel}: workflow root must be a mapping"]
    jobs = workflow.get("jobs")
    require(isinstance(jobs, dict), "jobs block must be a mapping")
    if not isinstance(jobs, dict):
        return errors
    require(
        set(jobs)
        == {
            "detect-runner",
            "lint",
            "gitleaks",
            "python",
            "node",
            "java",
            "validation-complete",
        },
        "must define detect-runner, lint, gitleaks, language, and aggregate jobs",
    )
    selector = jobs.get("detect-runner")
    require(isinstance(selector, dict), "detect-runner job must be a mapping")
    if isinstance(selector, dict):
        require(
            selector.get("uses") == "./.github/workflows/reusable-detect-runner.yml",
            "detect-runner must call the local reusable selector",
        )
        require(
            selector.get("secrets")
            == {"RUNNER_DISCOVERY_TOKEN": "${{ secrets.RUNNER_DISCOVERY_TOKEN }}"},
            "detect-runner must receive only the runner discovery token",
        )
        selector_inputs = selector.get("with", {})
        require(
            isinstance(selector_inputs, dict)
            and set(selector_inputs)
            == {"strategy", "same_repo", "trusted_author", "is_renovate_bot"},
            "detect-runner must pass the complete trust context",
        )
    expected = {
        "lint": {
            "setups": {"actions/setup-python", "actions/setup-node"},
            "command": "python scripts/validate.py --static",
            "needs": {"detect-runner"},
        },
        "gitleaks": {
            "setups": set(),
            "command": None,
            "needs": {"detect-runner"},
        },
        "python": {
            "setups": {"actions/setup-python"},
            "command": "python scripts/validate_python_fixture.py",
            "needs": {"detect-runner", "lint"},
        },
        "node": {
            "setups": {"actions/setup-node"},
            "command": "node scripts/validate_node_fixture.mjs",
            "needs": {"detect-runner", "lint"},
        },
        "java": {
            "setups": {"actions/setup-java", "actions/setup-python"},
            "command": "bash scripts/validate_java_fixture.sh",
            "needs": {"detect-runner", "lint"},
        },
    }
    for name, contract in expected.items():
        job = jobs.get(name)
        if not isinstance(job, dict):
            require(False, f"{name} job must be a mapping")
            continue
        steps = job.get("steps")
        if not isinstance(steps, list):
            require(False, f"{name} job must define steps")
            continue
        needs = job.get("needs")
        needs_set = (
            {needs}
            if isinstance(needs, str)
            else set(needs)
            if isinstance(needs, list)
            else set()
        )
        require(needs_set == contract["needs"], f"{name} job has incorrect dependencies")
        require(
            job.get("runs-on") == "${{ fromJson(needs.detect-runner.outputs.runs_on) }}",
            f"{name} job must use the detected runner",
        )
        require(
            "github.event_name != 'pull_request_target'" in str(job.get("if", "")),
            f"{name} must reject pull_request_target",
        )
        require("secrets" not in job, f"{name} job must not inherit secrets")
        setup_actions: set[str] = set()
        commands: list[str] = []
        checkout_found = False
        for step in steps:
            if not isinstance(step, dict):
                continue
            use = step.get("uses")
            if isinstance(use, str):
                action = use.rpartition("@")[0]
                if action == "actions/checkout":
                    checkout_found = True
                    action_inputs = step.get("with")
                    require(
                        isinstance(action_inputs, dict)
                        and action_inputs.get("persist-credentials") is False,
                        f"{name} checkout must set persist-credentials: false",
                    )
                if action.startswith("actions/setup-"):
                    setup_actions.add(action)
            command = step.get("run")
            if isinstance(command, str):
                commands.append(command)
        require(checkout_found, f"{name} job must check out the repository")
        require(
            setup_actions == contract["setups"],
            f"{name} job has unexpected toolchain setup actions: {sorted(setup_actions)}",
        )
        if contract["command"] is not None:
            require(
                contract["command"] in commands,
                f"{name} job must run only its focused validation entrypoint",
            )
        if name == "java":
            java_steps = [
                step
                for step in steps
                if isinstance(step, dict)
                and step.get("run") == "bash scripts/validate_java_fixture.sh"
            ]
            require(
                len(java_steps) == 1
                and java_steps[0].get("env") == {"REQUIRE_MAVEN": "1"},
                "java CI validation must require Maven",
            )
        other_commands = {
            value["command"]
            for key, value in expected.items()
            if key != name and value["command"] is not None
        }
        require(
            not other_commands.intersection(commands),
            f"{name} job invokes another job's validation entrypoint",
        )
    gitleaks = jobs.get("gitleaks")
    if isinstance(gitleaks, dict):
        steps = gitleaks.get("steps", [])
        checkout_steps = [
            step
            for step in steps
            if isinstance(step, dict)
            and str(step.get("uses", "")).startswith("actions/checkout@")
        ]
        require(len(checkout_steps) == 1, "gitleaks must define exactly one checkout step")
        if checkout_steps:
            checkout_inputs = checkout_steps[0].get("with", {})
            require(
                isinstance(checkout_inputs, dict) and checkout_inputs.get("fetch-depth") == 0,
                "gitleaks checkout must fetch full history",
            )
        install_steps = [
            step
            for step in steps
            if isinstance(step, dict) and step.get("name") == "Install checksum-verified Gitleaks"
        ]
        require(len(install_steps) == 1, "gitleaks must define one checksum-verified install")
        if install_steps:
            install = install_steps[0]
            install_env = install.get("env", {})
            version = install_env.get("GITLEAKS_VERSION") if isinstance(install_env, dict) else None
            checksum = install_env.get("GITLEAKS_SHA256") if isinstance(install_env, dict) else None
            require(
                is_semver(version),
                "gitleaks release version must be pinned to exact SemVer",
            )
            require(
                isinstance(checksum, str) and re.fullmatch(r"[0-9a-f]{64}", checksum) is not None,
                "gitleaks release archive must use a pinned SHA-256 checksum",
            )
            install_script = str(install.get("run", ""))
            for marker in (
                "gitleaks/gitleaks/releases/download/v${GITLEAKS_VERSION}/${archive}",
                'printf \'%s  %s\\n\' "$GITLEAKS_SHA256" "$archive" | sha256sum -c -',
                'install -m 0755 gitleaks "$HOME/.local/bin/gitleaks"',
                'echo "$HOME/.local/bin" >> "$GITHUB_PATH"',
            ):
                require(marker in install_script, f"gitleaks install is missing marker: {marker}")
        scan_steps = [
            step
            for step in steps
            if isinstance(step, dict) and step.get("name") in {"Scan Git history and current tree", "gitleaks"}
        ]
        require(len(scan_steps) == 1, "gitleaks must define one history and tree scan")
        if scan_steps:
            scan_script = str(scan_steps[0].get("run", ""))
            for command in (
                "gitleaks git --redact --no-banner .",
                "gitleaks dir --redact --no-banner .",
            ):
                require(command in scan_script, f"gitleaks scan must run redacted command: {command}")
            require("--report-path" not in scan_script, "gitleaks must not persist a findings report")
        require(
            not any(
                isinstance(step, dict)
                and str(step.get("uses", "")).startswith("actions/upload-artifact@")
                for step in steps
            ),
            "gitleaks must not upload potentially secret-bearing artifacts",
        )
    aggregate = jobs.get("validation-complete")
    require(
        isinstance(aggregate, dict),
        "validation-complete aggregate must be a mapping",
    )
    if isinstance(aggregate, dict):
        needs = aggregate.get("needs")
        require(
            isinstance(needs, list)
            and set(needs) == {"detect-runner", "lint", "gitleaks", "python", "node", "java"},
            "validate aggregate must depend on every required stage",
        )
        require(
            aggregate.get("if")
            == "${{ always() && github.event_name != 'pull_request_target' }}",
            "validate aggregate must always evaluate and reject pull_request_target",
        )
        require(
            aggregate.get("runs-on") == "ubuntu-latest",
            "validate aggregate must use a fixed fallback runner",
        )
        aggregate_text = json.dumps(aggregate)
        for stage in ("detect-runner", "lint", "gitleaks", "python", "node", "java"):
            require(
                f"needs.{stage}.result" in aggregate_text,
                f"validate aggregate must fail closed on {stage}",
            )
        require("exit 1" in aggregate_text, "validate aggregate must fail with a non-zero status")
    return errors


def runner_selection_allows_self_hosted(
    strategy: str,
    *,
    same_repo: bool = False,
    trusted_author: bool = False,
    is_renovate_bot: bool = False,
) -> bool:
    """Model the reusable selector's trust gate for negative policy probes."""
    if strategy == "push_any":
        return True
    if strategy == "pr_trusted":
        return same_repo and (trusted_author or is_renovate_bot)
    return False


def validation_aggregate_succeeds(results: dict[str, str]) -> bool:
    """Model the aggregate job's requirement that every stage succeeds."""
    required = {"detect-runner", "lint", "gitleaks", "python", "node", "java"}
    return set(results) == required and all(result == "success" for result in results.values())


def validate_toolkit_workflow_negative_probes(path: Path) -> list[str]:
    """Prove the workflow contract rejects Gitleaks bypass mutations."""
    try:
        workflow = yaml.safe_load(path.read_text())
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return [f"{path.relative_to(ROOT)}: negative probe YAML parse failed: {exc}"]
    if not isinstance(workflow, dict):
        return [f"{path.relative_to(ROOT)}: negative probes require a mapping"]
    errors: list[str] = []

    def clone() -> dict:
        return json.loads(json.dumps(workflow))

    def step_by_name(mutated: dict, job_name: str, step_name: str) -> dict:
        jobs = mutated.get("jobs")
        job = jobs.get(job_name) if isinstance(jobs, dict) else None
        steps = job.get("steps") if isinstance(job, dict) else None
        if isinstance(steps, list):
            for step in steps:
                if isinstance(step, dict) and step.get("name") == step_name:
                    return step
        return {}

    def require_rejection(mutated: object, marker: str, label: str) -> None:
        probe_errors = validate_toolkit_workflow_errors(path, mutated)
        if not any(marker in error for error in probe_errors):
            errors.append(f"{path.relative_to(ROOT)}: negative probe accepted {label}")

    mutated = clone()
    del mutated["jobs"]["gitleaks"]
    require_rejection(mutated, "must define detect-runner, lint, gitleaks", "missing gitleaks job")

    mutated = clone()
    mutated["jobs"]["gitleaks"]["needs"] = []
    require_rejection(mutated, "gitleaks job has incorrect dependencies", "detector bypass")

    mutated = clone()
    mutated["jobs"]["gitleaks"]["runs-on"] = "ubuntu-latest"
    require_rejection(mutated, "gitleaks job must use the detected runner", "runner wiring bypass")

    mutated = clone()
    checkout = step_by_name(mutated, "gitleaks", "Check out full repository history")
    checkout.setdefault("with", {})["fetch-depth"] = 1
    require_rejection(mutated, "gitleaks checkout must fetch full history", "shallow checkout")

    mutated = clone()
    install = step_by_name(mutated, "gitleaks", "Install checksum-verified Gitleaks")
    install.setdefault("env", {})["GITLEAKS_VERSION"] = "latest"
    require_rejection(mutated, "version must be pinned", "unpinned release")

    mutated = clone()
    step_by_name(mutated, "gitleaks", "Install checksum-verified Gitleaks")["run"] = (
        "install gitleaks"
    )
    require_rejection(mutated, "sha256sum -c", "checksum bypass")

    mutated = clone()
    scan_step = step_by_name(mutated, "gitleaks", "Scan Git history and current tree") or step_by_name(mutated, "gitleaks", "gitleaks")
    scan_step["run"] = (
        "gitleaks git .\ngitleaks dir ."
    )
    require_rejection(mutated, "redacted command", "redaction bypass")

    mutated = clone()
    aggregate_needs = mutated["jobs"]["validation-complete"].get("needs")
    mutated["jobs"]["validation-complete"]["needs"] = [
        need for need in aggregate_needs if need != "gitleaks"
    ] if isinstance(aggregate_needs, list) else []
    require_rejection(mutated, "depend on every required stage", "aggregate dependency bypass")

    mutated = clone()
    aggregate_step = step_by_name(mutated, "validation-complete", "Validate stage results")
    aggregate_script = str(aggregate_step.get("run", ""))
    aggregate_step["run"] = "\n".join(
        line for line in aggregate_script.splitlines() if "needs.gitleaks.result" not in line
    )
    require_rejection(mutated, "fail closed on gitleaks", "aggregate result bypass")

    successful_results = {
        stage: "success"
        for stage in ("detect-runner", "lint", "gitleaks", "python", "node", "java")
    }
    if not validation_aggregate_succeeds(successful_results):
        errors.append(f"{path.relative_to(ROOT)}: aggregate positive probe rejected success")
    for failed_stage in successful_results:
        failed_results = dict(successful_results)
        failed_results[failed_stage] = "failure"
        if validation_aggregate_succeeds(failed_results):
            errors.append(
                f"{path.relative_to(ROOT)}: aggregate negative probe accepted {failed_stage}"
            )
    return errors


def reusable_runner_workflow_errors(path: Path) -> list[str]:
    """Validate fail-closed runner discovery and its reusable outputs."""
    errors: list[str] = []
    rel = path.relative_to(ROOT)

    def require(condition: bool, message: str) -> None:
        if not condition:
            errors.append(f"{rel}: {message}")

    try:
        workflow = yaml.safe_load(path.read_text())
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return [f"{rel}: YAML parse failed: {exc}"]
    if not isinstance(workflow, dict):
        return [f"{rel}: workflow root must be a mapping"]
    triggers = workflow.get("on", workflow.get(True))
    call = triggers.get("workflow_call") if isinstance(triggers, dict) else None
    require(isinstance(call, dict), "must expose workflow_call")
    if isinstance(call, dict):
        call_inputs = call.get("inputs")
        require(
            isinstance(call_inputs, dict)
            and set(call_inputs)
            == {"strategy", "same_repo", "trusted_author", "is_renovate_bot"},
            "workflow_call inputs must expose the complete trust context",
        )
        require(
            call.get("secrets")
            == {
                "RUNNER_DISCOVERY_TOKEN": {
                    "description": "Token used only to list repository self-hosted runners",
                    "required": False,
                }
            },
            "workflow_call must declare only the optional runner discovery token",
        )
        require(
            set(call.get("outputs", {})) == {"runs_on", "is_self_hosted"},
            "workflow_call outputs must expose runner labels and hosting mode",
        )
    require(
        workflow.get("permissions") == {"contents": "read", "actions": "none"},
        "permissions must be exactly contents:read and actions:none",
    )
    jobs = workflow.get("jobs", {})
    require(
        isinstance(jobs, dict) and set(jobs) == {"detect-runner"},
        "must define one selector job",
    )
    job = jobs.get("detect-runner", {}) if isinstance(jobs, dict) else {}
    if not isinstance(job, dict):
        job = {}
    require(job.get("runs-on") == "ubuntu-latest", "selector must bootstrap on ubuntu-latest")
    steps = job.get("steps", []) if isinstance(job, dict) else []
    script_steps = [step for step in steps if isinstance(step, dict) and step.get("id") == "select"]
    require(len(script_steps) == 1, "must define exactly one select step")
    if script_steps:
        step = script_steps[0]
        use = step.get("uses", "")
        require(
            re.fullmatch(r"actions/github-script@[0-9a-f]{40}", use) is not None,
            "github-script must be pinned to a full SHA",
        )
        require(
            step.get("env")
            == {
                "DISCOVERY_TOKEN_AVAILABLE": "${{ secrets.RUNNER_DISCOVERY_TOKEN != '' }}",
                "STRATEGY": "${{ inputs.strategy }}",
                "SAME_REPO": "${{ inputs.same_repo }}",
                "TRUSTED_AUTHOR": "${{ inputs.trusted_author }}",
                "IS_RENOVATE_BOT": "${{ inputs.is_renovate_bot }}",
            },
            "selector must pass trust inputs through the environment",
        )
        script = str(step.get("with", {}).get("script", ""))
        for marker in (
            "strategy === 'push_any'",
            "strategy === 'pr_trusted'",
            "process.env.STRATEGY",
            "process.env.SAME_REPO",
            "process.env.TRUSTED_AUTHOR",
            "process.env.IS_RENOVATE_BOT",
            "listSelfHostedRunnersForRepo",
            "runner.status === 'online'",
            "process.env.DISCOVERY_TOKEN_AVAILABLE !== 'true'",
            "Runner discovery token is not configured",
            "setHosted();",
            "catch (error)",
        ):
            require(marker in script, f"selector script is missing fail-closed marker: {marker}")
    probes = {
        "fork trusted author": runner_selection_allows_self_hosted(
            "pr_trusted", same_repo=False, trusted_author=True
        ),
        "same-repo untrusted author": runner_selection_allows_self_hosted(
            "pr_trusted", same_repo=True
        ),
        "unknown strategy": runner_selection_allows_self_hosted("unknown"),
    }
    for label, selected in probes.items():
        require(not selected, f"negative runner-selection probe allowed {label}")
    require(
        runner_selection_allows_self_hosted(
            "pr_trusted", same_repo=True, trusted_author=True
        ),
        "trusted same-repository PR probe must allow discovery",
    )
    require(
        runner_selection_allows_self_hosted(
            "pr_trusted", same_repo=True, is_renovate_bot=True
        ),
        "same-repository Renovate probe must allow discovery",
    )
    require(
        runner_selection_allows_self_hosted("push_any"),
        "push strategy probe must allow discovery",
    )
    return errors


def codeql_runner_workflow_errors(path: Path) -> list[str]:
    """Validate that CodeQL remains separate and uses the trusted runner selector."""
    rel = path.relative_to(ROOT)
    try:
        workflow = yaml.safe_load(path.read_text())
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return [f"{rel}: YAML parse failed: {exc}"]
    if not isinstance(workflow, dict):
        return [f"{rel}: workflow root must be a mapping"]
    jobs = workflow.get("jobs", {})
    if not isinstance(jobs, dict) or set(jobs) != {"detect-runner", "analyze"}:
        return [f"{rel}: must define exactly detect-runner and analyze jobs"]
    errors: list[str] = []
    selector = jobs["detect-runner"]
    analyze = jobs["analyze"]
    if not isinstance(selector, dict) or not isinstance(analyze, dict):
        return [f"{rel}: detect-runner and analyze jobs must be mappings"]
    if selector.get("uses") != "./.github/workflows/reusable-detect-runner.yml":
        errors.append(f"{rel}: detect-runner must call the local reusable selector")
    if selector.get("secrets") != {
        "RUNNER_DISCOVERY_TOKEN": "${{ secrets.RUNNER_DISCOVERY_TOKEN }}"
    }:
        errors.append(f"{rel}: detect-runner must receive only the runner discovery token")
    if analyze.get("needs") != "detect-runner":
        errors.append(f"{rel}: analyze must depend on detect-runner")
    if analyze.get("runs-on") != "${{ fromJson(needs.detect-runner.outputs.runs_on) }}":
        errors.append(f"{rel}: analyze must use the detected runner")
    analyze_strategy = analyze.get("strategy")
    matrix = analyze_strategy.get("matrix", {}) if isinstance(analyze_strategy, dict) else {}
    include = matrix.get("include", []) if isinstance(matrix, dict) else []
    expected_languages = {
        ("actions", "none"),
        ("python", "none"),
        ("javascript-typescript", "none"),
        ("java-kotlin", "none"),
    }
    configured_languages = {
        (entry.get("language"), entry.get("build-mode"))
        for entry in include
        if isinstance(entry, dict)
    }
    if configured_languages != expected_languages:
        errors.append(f"{rel}: every CodeQL language must use explicit build-mode none")
    steps = analyze.get("steps", [])
    init_steps = [
        step
        for step in steps
        if isinstance(step, dict)
        and str(step.get("uses", "")).startswith("github/codeql-action/init@")
    ]
    if len(init_steps) != 1 or init_steps[0].get("with", {}).get("build-mode") != (
        "${{ matrix.build-mode }}"
    ):
        errors.append(f"{rel}: CodeQL init must receive the explicit matrix build mode")
    render_steps = [
        step for step in steps if isinstance(step, dict)
        and step.get("run") == "python scripts/render_codeql_workflows.py"
    ]
    if (
        len(render_steps) != 1 or len(init_steps) != 1
        or render_steps[0].get("if") != "matrix.language == 'actions'"
        or steps.index(render_steps[0]) >= steps.index(init_steps[0])
    ):
        errors.append(f"{rel}: Actions template evidence must be rendered before CodeQL init")
    config_path = ROOT / ".github/codeql/codeql-config.yml"
    try:
        config = yaml.safe_load(config_path.read_text())
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        errors.append(f"{config_path.relative_to(ROOT)}: YAML parse failed: {exc}")
    else:
        if not isinstance(config, dict) or config.get("paths") != [
            "scripts", "tests", "actions", ".github/workflows"
        ]:
            errors.append(f"{rel}: CodeQL must cover scripts, tests, shipped actions and workflows")
    return errors


RENOVATE_SCHEMA = "https://docs.renovatebot.com/renovate-schema.json"
TEMPLATE_WORKFLOW_SOURCES = ROOT / "template/.github/workflows"
# The API endpoint rather than raw.githubusercontent.com: raw is CDN-cached for
# several minutes, which would fail this check against a preset that was already
# updated. Authorization is attached when a token is present so CI is not subject
# to the unauthenticated rate limit.
SHARED_PRESET_BASE_URL = (
    "https://api.github.com/repos/quokkify/renovate-presets/contents/presets/base.json"
)


def template_owned_workflow_paths() -> list[str]:
    """Return the workflow paths whose versions ci-kit owns."""
    return sorted(
        f".github/workflows/{source.name.removesuffix('.jinja')}"
        for source in TEMPLATE_WORKFLOW_SOURCES.glob("*.yml.jinja")
    )


def assert_shared_preset_covers_template_workflows() -> None:
    """The shared preset must disable Renovate for every template-owned workflow.

    Those files carry pins that this repository updates and ships through `copier update`.
    A template workflow missing from the preset would be updated from both sides at once,
    so adding one here without updating the preset must fail rather than drift silently.
    The rule lives in the preset because the consumer's renovate.json is owned by the
    project and cannot be rewritten.
    """
    request = urllib.request.Request(
        SHARED_PRESET_BASE_URL,
        headers={"Accept": "application/vnd.github.raw+json"},
    )
    token = (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            preset = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        print(f"shared preset unreachable ({exc}): skipping template-owned coverage check")
        return
    rules = preset.get("packageRules") if isinstance(preset, dict) else None
    if not isinstance(rules, list):
        ERRORS.append("shared preset has no packageRules array")
        return
    covered = [
        rule.get("matchFileNames")
        for rule in rules
        if isinstance(rule, dict) and rule.get("enabled") is False and "matchFileNames" in rule
    ]
    if len(covered) != 1:
        ERRORS.append(
            f"shared preset must carry exactly one disabled template-owned rule, found {len(covered)}"
        )
        return
    expected = template_owned_workflow_paths()
    check(
        sorted(covered[0] or []) == expected,
        "shared preset template-owned coverage mismatch: "
        f"preset={covered[0]!r} template={expected!r}; update "
        "quokkify/renovate-presets//presets/base before adding a template workflow",
    )


_copier_config = load_yaml_or_error(ROOT / "copier.yml", ERRORS, "copier.yml")
skip_if_exists = _copier_config.get("_skip_if_exists") or [] if isinstance(_copier_config, dict) else []
RENOVATE_PRESET_PATHS = {
    "default": "presets/base",
    "python": "presets/python/default",
    "javascript": "presets/npm/default",
    "java": "presets/gradle/default",
    "docker": "presets/docker/default",
    "github-actions": "presets/github-actions/default",
}


def renovate_extends(repository: str, presets: list[str]) -> list[str]:
    """Return exact Renovate extends entries for selected preset names."""
    return [f"github>{repository}//{RENOVATE_PRESET_PATHS[preset]}" for preset in presets]


GENERATED_RENOVATE_CONFIGS: list[tuple[str, str]] = []


def assert_generated_renovate_config(path: Path, expected_extends: list[str], label: str) -> None:
    """Validate the generated Renovate file shape and exact shared preset refs."""
    if path.is_file():
        GENERATED_RENOVATE_CONFIGS.append((label, path.read_text()))
    try:
        data = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        ERRORS.append(f"{label}: renovate.json is not valid JSON: {exc}")
        return
    check(data.get("$schema") == RENOVATE_SCHEMA, f"{label}: renovate schema mismatch")
    check(
        data.get("extends") == expected_extends,
        f"{label}: renovate extends mismatch: {data.get('extends')!r}",
    )


for path in sorted([*ROOT.rglob("*.yml"), *ROOT.rglob("*.yaml")]):
    if ".git" in path.parts or path.is_relative_to(ROOT / "template"):
        continue
    try:
        yaml.safe_load(path.read_text())
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        ERRORS.append(f"{path.relative_to(ROOT)}: YAML: {exc}")


BOOLEAN_INPUTS = {
    "build",
    "cache-dependencies",
    "cache-jdk",
    "down-on-timeout",
    "fail-on-missing",
    "fork-pr",
    "install-dependencies",
    "publish-pages",
    "pyramid-enabled",
    "show-logs-on-failure",
    "wait-for-health",
}
EXPECTED_ACTIONS = {
    "checkout",
    "allure-report",
    "compose-up",
    "deploy-gh-pages-subdir",
    "gradle-retry",
    "junit-step-summary",
    "setup-java-gradle",
    "setup-node",
    "setup-python",
}


def validate_action_metadata(data: object, label: str) -> list[str]:
    """Return fail-closed metadata errors for one composite action."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return [f"{label}: action metadata must be a mapping"]
    allowed_top = {"name", "description", "author", "branding", "inputs", "outputs", "runs"}
    for key in sorted(set(data) - allowed_top):
        errors.append(f"{label}: unknown top-level key {key}")
    if not data.get("name") or not data.get("description"):
        errors.append(f"{label}: action requires name and description")
    for forbidden in ("permissions", "secrets"):
        if forbidden in data:
            errors.append(f"{label}: composite action must not declare {forbidden}")

    inputs = data.get("inputs", {})
    if not isinstance(inputs, dict):
        errors.append(f"{label}: inputs must be a mapping")
        inputs = {}
    for name, spec in inputs.items():
        if not isinstance(spec, dict) or not spec.get("description"):
            errors.append(f"{label}: input {name} requires a description")
            continue
        unknown = set(spec) - {"description", "required", "default", "deprecationMessage"}
        if unknown:
            errors.append(f"{label}: input {name} has unknown keys: {sorted(unknown)}")
        if "required" in spec and not isinstance(spec["required"], bool):
            errors.append(f"{label}: input {name} required must be boolean")
        if name in BOOLEAN_INPUTS and spec.get("default") not in ("true", "false"):
            errors.append(f"{label}: boolean input {name} requires a true/false string default")

    outputs = data.get("outputs", {})
    if not isinstance(outputs, dict):
        errors.append(f"{label}: outputs must be a mapping")
    else:
        for name, spec in outputs.items():
            if not isinstance(spec, dict) or not spec.get("description") or not spec.get("value"):
                errors.append(f"{label}: output {name} requires description and value")
                continue
            unknown = set(spec) - {"description", "value"}
            if unknown:
                errors.append(f"{label}: output {name} has unknown keys: {sorted(unknown)}")

    runs = data.get("runs", {})
    if not isinstance(runs, dict) or runs.get("using") != "composite":
        errors.append(f"{label}: action must use composite runner")
        return errors
    for key in sorted(set(runs) - {"using", "steps"}):
        errors.append(f"{label}: runs has unknown key {key}")
    steps = runs.get("steps")
    if not isinstance(steps, list) or not steps:
        errors.append(f"{label}: action requires non-empty runs.steps")
        return errors
    for index, step in enumerate(steps, 1):
        if not isinstance(step, dict) or ("uses" in step) == ("run" in step):
            errors.append(f"{label}: step {index} must declare exactly one of uses or run")
        elif "run" in step and not step.get("shell"):
            errors.append(f"{label}: run step {index} requires an explicit shell")
        if isinstance(step, dict):
            allowed_step = {"name", "id", "if", "uses", "with", "run", "shell", "env", "working-directory"}
            unknown = set(step) - allowed_step
            if unknown:
                errors.append(f"{label}: step {index} has unknown keys: {sorted(unknown)}")
    return errors


action_paths = sorted((ROOT / "actions").glob("*/action.yml"))
check(
    {path.parent.name for path in action_paths} == EXPECTED_ACTIONS,
    "actions/: expected exactly the documented composite actions",
)
for action_path in action_paths:
    data = yaml.safe_load(action_path.read_text())
    label = str(action_path.relative_to(ROOT))
    ERRORS.extend(validate_action_metadata(data, label))
    if isinstance(data, dict) and isinstance(data.get("runs"), dict):
        steps = data["runs"].get("steps", [])
        if isinstance(steps, list):
            for index, step in enumerate(steps, 1):
                if isinstance(step, dict) and isinstance(step.get("run"), str):
                    syntax = subprocess.run(
                        ["bash", "-n"],
                        input=step["run"],
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    check(
                        syntax.returncode == 0,
                        f"{label}: run step {index} has invalid bash syntax: {syntax.stderr.strip()}",
                    )


def validate_gh_pages_subdir_manager_regex() -> list[str]:
    """Regression probes for the gh-pages-subdir-action regex matcher."""
    errors: list[str] = []

    try:
        renovate = json.loads((ROOT / ".github/renovate.json").read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f".github/renovate.json regex probe requires valid JSON: {exc}"]

    manager = next(
        (
            m
            for m in renovate.get("customManagers", [])
            if m.get("depNameTemplate") == "quokkify/gh-pages-subdir-action"
        ),
        None,
    )
    if manager is None:
        return ["renovate.json missing quokkify/gh-pages-subdir-action custom manager"]

    match_strings = manager.get("matchStrings")
    if not isinstance(match_strings, list) or not match_strings:
        return ["renovate.json missing matchStrings for gh-pages-subdir-action manager"]
    pattern = match_strings[0]

    try:
        # Renovate regex uses JavaScript-style named groups (?<currentValue>).
        # Python supports (?P<name>), so normalize before compiling probe fixtures.
        matcher = re.compile(pattern.replace("(?<currentValue>", "(?P<currentValue>"))
    except re.error as exc:
        return [f"renovate.json invalid gh-pages-subdir-action regex: {exc}"]

    positive = {
        "plain-text": "`gh-pages-subdir-action` `v2.6.0`",
        "markdown-link": "[quokkify/gh-pages-subdir-action](https://github.com/quokkify/gh-pages-subdir-action) `v2.6.0`",
        "comment-context": "delegates to gh-pages-subdir-action v2.6.0",
    }
    for label, text in positive.items():
        match = matcher.search(text)
        if not match or match.group("currentValue") != "v2.6.0":
            errors.append(f"gh-pages-subdir regex probe failed to match {label}: {text}")

    for label, text in {
        "missing-separator": "gh-pages-subdir-actionv2.6.0",
        "version-suffix": "gh-pages-subdir-action v2.6.0-beta",
        "ownerless-prefix": "myquokkify/gh-pages-subdir-action v2.6.0",
        "ownerful-prefix": "foo/gh-pages-subdir-action v2.6.0",
        "suffix-no-space": "gh-pages-subdir-action-extra v2.6.0",
        "inline-use": "random quokkify/ci-kit/actions/deploy-gh-pages-subdir@v2.6.0",
    }.items():
        if matcher.search(text):
            errors.append(f"gh-pages-subdir regex probe over-matched {label}: {text}")

    return errors


# Validate gh-pages-subdir-action Renovate regex against fixtures to avoid drift.
ERRORS.extend(validate_gh_pages_subdir_manager_regex())

# Negative contract probes prove that permission/secret leakage, malformed booleans,
# and ambiguous run steps are rejected by the validator itself.
negative_probe = {
    "name": "invalid",
    "description": "invalid",
    "permissions": {"contents": "write"},
    "secrets": {"token": {}},
    "inputs": {"build": {"description": "bad", "default": "yes"}},
    "runs": {"using": "composite", "steps": [{"run": "true"}]},
}
negative_errors = validate_action_metadata(negative_probe, "negative-probe")
for expected in ("permissions", "secrets", "true/false", "explicit shell"):
    check(
        any(expected in error for error in negative_errors),
        f"action validator negative probe did not reject {expected}",
    )

def setup_policy_errors(example: object, texts: dict[str, str]) -> list[str]:
    errors: list[str] = []
    if not isinstance(example, dict) or example.get("permissions") != {"contents": "read"}:
        errors.append("examples/setup-actions.yml permissions must be exactly contents: read")
    for label, text in texts.items():
        if re.search(r"\$\{\{\s*secrets\.", text, re.IGNORECASE):
            errors.append(f"{label}: setup actions must not read secret contexts")
    return errors


setup_example = yaml.safe_load((ROOT / "examples/setup-actions.yml").read_text())
action_texts = {str(path.relative_to(ROOT)): path.read_text() for path in action_paths}
ERRORS.extend(setup_policy_errors(setup_example, action_texts))
mutated_example = dict(setup_example)
mutated_example["permissions"] = "write-all"
check(
    bool(setup_policy_errors(mutated_example, action_texts)),
    "setup policy negative probe did not reject widened permissions",
)
mutated_actions = dict(action_texts)
mutated_actions["actions/setup-python/action.yml"] += "\n${{ secrets.GITHUB_TOKEN }}\n"
check(
    any("secret contexts" in error for error in setup_policy_errors(setup_example, mutated_actions)),
    "setup policy negative probe did not reject secret context",
)

for path in sorted(ROOT.rglob("*.json")):
    if path.is_relative_to(ROOT / "template"):
        continue
    try:
        json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        ERRORS.append(f"{path.relative_to(ROOT)}: JSON: {exc}")
for path in sorted(ROOT.rglob("*.json5")):
    try:
        json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        ERRORS.append(f"{path.relative_to(ROOT)}: JSON5 subset: {exc}")

for path in sorted([*ROOT.rglob("*.yml"), *ROOT.rglob("*.yaml")]):
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        ERRORS.append(f"{path.relative_to(ROOT)}: action scan failed: {exc}")
        continue
    ERRORS.extend(action_reference_errors(
        text, str(path.relative_to(ROOT)), require_toolkit_pin=path.is_relative_to(ROOT / "examples")
    ))

link_re = re.compile(r"(?<!!)\[[^]]+\]\(([^)]+)\)")
for path in sorted(ROOT.rglob("*.md")):
    for link in link_re.findall(path.read_text()):
        if re.match(r"^(https?://|mailto:|#)", link):
            continue
        target = (path.parent / link.split("#", 1)[0]).resolve()
        check(
            target.exists() and (target == ROOT or ROOT in target.parents),
            f"{path.relative_to(ROOT)}: broken/escaping local link: {link}",
        )

poly = (ROOT / "examples/polyglot-ci.yml").read_text()
for job in ("python:", "node:", "java:", "integration:"):
    check(
        re.search(rf"^  {re.escape(job)}$", poly, re.MULTILINE) is not None,
        f"polyglot example missing independent {job[:-1]} job",
    )
check(
    "needs: [changes, python, node, java]" in poly,
    "polyglot integration job must depend on component checks",
)

docker = yaml.safe_load((ROOT / ".github/workflows/docker-build.yml").read_text())
on_key = next((k for k in docker if str(k).lower() in ("on", "true")), None)
call = docker[on_key]["workflow_call"]
check(call["inputs"]["push"]["default"] is False, "Docker push default must be false")
release = yaml.safe_load((ROOT / ".github/workflows/release-please.yml").read_text())
check(
    release.get("permissions") == {"contents": "write", "pull-requests": "write"},
    "Release workflow permissions must be exactly contents:write and pull-requests:write",
)
release_manifest = json.loads((ROOT / ".github/release-please/manifest.json").read_text())
check(
    not release_manifest_errors(release_manifest),
    "Release Please manifest must contain exactly one root package with a SemVer version",
)
for valid_manifest in ({".": "0.0.0"}, {".": "0.1.0"}, {".": "1.2.3-rc.1+build.5"}):
    check(
        not release_manifest_errors(valid_manifest),
        f"valid release manifest must be accepted: {valid_manifest}",
    )
for invalid_manifest in (
    {".": "01.0.0"},
    {".": "0.1"},
    {".": "1.0.0-01"},
    {".": 1},
    {".": "0.1.0", "extra": "0.1.0"},
):
    check(
        bool(release_manifest_errors(invalid_manifest)),
        f"invalid release manifest must be rejected: {invalid_manifest}",
    )
check(
    bool(release_manifest_errors({".": "0.0.0-0." + "--." * 10_000})),
    "pathological SemVer input must be rejected without regex backtracking",
)
release_config = json.loads((ROOT / ".github/release-please/config.json").read_text())
check(
    release_config.get("release-type") == "simple"
    and release_config.get("include-component-in-tag") is False
    and release_config.get("packages", {}).get(".", {}).get("package-name")
    == "ci-kit",
    "Release Please config must preserve the simple ci-kit SemVer contract",
)
assert_shared_preset_covers_template_workflows()
ERRORS.extend(release_workflow_errors(ROOT / ".github/workflows/release.yml"))
ERRORS.extend(
    copier_fleet_auto_update_workflow_errors(
        ROOT / ".github/workflows/copier-fleet-auto-update.yml"
    )
)
ERRORS.extend(
    validate_toolkit_workflow_errors(ROOT / ".github/workflows/validate-toolkit.yml")
)
ERRORS.extend(
    validate_toolkit_workflow_negative_probes(ROOT / ".github/workflows/validate-toolkit.yml")
)
ERRORS.extend(
    reusable_runner_workflow_errors(ROOT / ".github/workflows/reusable-detect-runner.yml")
)
ERRORS.extend(codeql_runner_workflow_errors(ROOT / ".github/workflows/codeql.yml"))
for fixture in sorted((ROOT / "tests/fixtures/release-workflows").glob("invalid-*.yml")):
    check(
        bool(release_workflow_errors(fixture)),
        f"{fixture.relative_to(ROOT)} must fail release driver validation",
    )

RESOLVE_SCRIPT_ACTION = "actions/github-script"
GENERATE_REPORT_ACTION = "quokkify/allure-report-action"


def allure_publisher_workflow_errors(path: Path) -> list[str]:
    """Validate the reusable Allure publisher's trust boundaries."""
    errors: list[str] = []
    label = str(path.relative_to(ROOT))
    try:
        workflow = yaml.safe_load(path.read_text())
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        return [f"{label}: YAML parse failed: {exc}"]
    if not isinstance(workflow, dict):
        return [f"{label}: workflow root must be a mapping"]
    triggers = workflow.get("on", workflow.get(True))
    if not isinstance(triggers, dict) or "workflow_call" not in triggers:
        errors.append(f"{label}: must expose workflow_call")
    if workflow.get("permissions") != {}:
        errors.append(f"{label}: workflow-level permissions must be empty")
    jobs = workflow.get("jobs", {})
    expected_jobs = {"resolve", "download", "generate", "comment"}
    if not isinstance(jobs, dict) or set(jobs) != expected_jobs:
        errors.append(f"{label}: must define resolve/download/generate/comment jobs")
        return errors
    required_permissions = {
        "resolve": {"actions": "read", "contents": "read", "pull-requests": "read"},
        "download": {"actions": "read", "contents": "read"},
        "generate": {"actions": "read", "contents": "read"},
        "comment": {"actions": "read", "pull-requests": "write"},
    }
    valid_jobs: dict[str, dict] = {}
    for name, permissions in required_permissions.items():
        job = jobs[name]
        if not isinstance(job, dict):
            errors.append(f"{label}: {name} job must be a mapping")
            continue
        valid_jobs[name] = job
        if job.get("permissions") != permissions:
            errors.append(f"{label}: {name} permissions are not narrowly scoped")
    uses: list[str] = []
    for name, job in valid_jobs.items():
        steps = job.get("steps")
        if not isinstance(steps, list):
            errors.append(f"{label}: {name} steps must be a list")
            continue
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                errors.append(f"{label}: {name} step {index} must be a mapping")
                continue
            if step.get("uses"):
                uses.append(step["uses"])
    if any(re.search(r"@[0-9a-f]{1,39}$|@(v|main|master)", use) for use in uses):
        errors.append(f"{label}: every external action must use a full SHA")
    generate_job = valid_jobs.get("generate")
    generate_steps = generate_job.get("steps", []) if generate_job else []
    if not isinstance(generate_steps, list):
        generate_steps = []
    # Match the action by name, not by its pinned SHA. The SHA moves with every
    # release bump, while the trust boundary being asserted here is "this step runs
    # the report builder without write privileges". SHA pinning itself stays enforced
    # by the full-SHA check above.
    if not any(
        isinstance(step, dict)
        and step.get("name") == "Build report without write privileges"
        and str(step.get("uses", "")).split("@", 1)[0] == GENERATE_REPORT_ACTION
        for step in generate_steps
    ):
        errors.append(f"{label}: missing trust-boundary assertion {GENERATE_REPORT_ACTION}")

    # These patterns deliberately describe the complete guard, rather than
    # searching for a marker.  This prevents comments, dead branches, and an
    # unrelated github-script step from satisfying the contract.
    resolve_guards = {
        "No external Allure artifacts found": re.compile(
            r'if\s*\(\s*externalRun\s*&&\s*actualNames\.length\s*===\s*0.*?'
            r'minimumArtifacts\s*===\s*0.*?process\.env\.SKIP_EMPTY_ARTIFACTS.*?\)\s*\{.*?'
            r'skip\(\s*"No external Allure artifacts found; report generation skipped\."\s*\)\s*;.*?return\s*;', re.DOTALL),
        "External Allure artifact contract mismatch": re.compile(
            r'if\s*\(\s*externalRun\s*&&.*?actualNames\.length\s*<\s*minimumArtifacts.*?'
            r'actualNames\.length\s*>\s*maximumArtifacts.*?new Set\(actualNames\).*?\)\s*\{.*?'
            r'core\.setFailed\(\s*`External Allure artifact contract mismatch.*?`\s*\)\s*;.*?return\s*;', re.DOTALL),
        'pull.user?.login === "dependabot[bot]"': re.compile(
            r'const pagesUnsafe =.*?pull\.head\.repo\.full_name !== pull\.base\.repo\.full_name.*?'
            r'pull\.user\?\.login === "dependabot\[bot\]";\s*'
            r'core\.setOutput\("fork-pr", String\(pagesUnsafe\)\)', re.DOTALL),
        "A newer source workflow run exists": re.compile(
            r"if\s*\(\s*!Number\.isSafeInteger\(newestRunId\).*?Number\(run\.id\)\s*!==\s*newestRunId.*?\)\s*\{.*?"
            r"skip\(\s*['\"]A newer source workflow run exists for this PR head; stale report suppressed\.['\"]\s*\)\s*;"
            r".*?return\s*;\s*\}", re.DOTALL),
        "Ignore unexpected source workflow identity": re.compile(
            r'if \(run\.name !== process\.env\.SOURCE_WORKFLOW \|\| run\.path !== process\.env\.SOURCE_WORKFLOW_PATH\) \{'
            r'.*?skip\(`Ignore unexpected source workflow identity:.*?return;', re.DOTALL),
        "Allure artifact contract v": re.compile(
            r'if \(componentRun &&.*?componentArtifactPattern\.test\(name\).*?new Set\(actualNames\).*?'
            r'core\.setFailed\(`Allure artifact contract v.*?return;', re.DOTALL),
    }
    resolve_job = valid_jobs.get("resolve")
    resolve_steps = resolve_job.get("steps", []) if resolve_job else []
    if not isinstance(resolve_steps, list):
        resolve_steps = []
    # Match the action by name, not by pinned SHA: the SHA is owned by the
    # dependency updater, and every ``uses`` above is already required to be a
    # full SHA.  Pinning it here too made a routine github-script bump report
    # all four guards as missing.
    resolve_step = next(
        (
            step for step in resolve_steps
            if isinstance(step, dict)
            and step.get("id") == "resolve"
            and str(step.get("uses", "")).split("@", 1)[0] == RESOLVE_SCRIPT_ACTION
        ),
        None,
    )
    resolve_script = ((resolve_step or {}).get("with") or {}).get("script", "")
    for marker, guard in resolve_guards.items():
        if not guard.search(str(resolve_script)):
            errors.append(f"{label}: missing trust-boundary assertion {marker}")
    call = (triggers or {}).get("workflow_call", {})
    secrets = call.get("secrets", {}) if isinstance(call, dict) else {}
    if secrets.get("github-token", {}).get("required") is not False:
        errors.append(f"{label}: legacy token must be optional for scoped per-job tokens")
    build_step = next((step for step in generate_steps if isinstance(step, dict)
                       and str(step.get("uses", "")).split("@", 1)[0] == GENERATE_REPORT_ACTION), {})
    build_inputs = build_step.get("with", {})
    if build_inputs.get("github-token") != "${{ secrets.github-token || github.token }}":
        errors.append(f"{label}: builder must default to the scoped job token")
    if build_inputs.get("comment-marker") != "<!-- project-toolkit-allure-report -->":
        errors.append(f"{label}: builder and trusted poster markers differ")
    download_steps = valid_jobs.get("download", {}).get("steps", [])
    helper = next((step for step in download_steps if isinstance(step, dict)
                   and step.get("name") == "Check out trusted toolkit extractor"), {})
    if helper.get("with", {}) != {"repository": "${{ job.workflow_repository }}", "ref": "${{ job.workflow_sha }}",
                                  "path": ".toolkit", "persist-credentials": False}:
        errors.append(f"{label}: extractor checkout must follow the called workflow commit")
    extractor = next((step for step in download_steps if isinstance(step, dict) and "run" in step), {})
    if (extractor.get("run") != "python .toolkit/template/.github/allure/safe_extract.py.jinja"
            or extractor.get("env", {}).get("ARTIFACT_MANIFEST") != "${{ needs.resolve.outputs.artifact_manifest }}"
            or any("actions/download-artifact@" in str(step.get("uses", "")) for step in download_steps)):
        errors.append(f"{label}: source artifacts must pass bounded extraction before materialization")
    env = (resolve_step or {}).get("env", {})
    for key, value in {"SOURCE_WORKFLOW": "source-workflow", "SOURCE_WORKFLOW_PATH": "source-workflow-path",
                       "ARTIFACT_MODE": "artifact-mode", "ARTIFACT_PREFIX": "artifact-prefix",
                       "MINIMUM_ARTIFACTS": "minimum-artifacts", "MAXIMUM_ARTIFACTS": "maximum-artifacts",
                       "SKIP_EMPTY_ARTIFACTS": "skip-empty-artifacts"}.items():
        if env.get(key) != "${{ inputs." + value + " }}":
            errors.append(f"{label}: resolver input binding is missing for {value}")
    return errors


def allure_pages_workflow_errors(path: Path) -> list[str]:
    """Validate separate Pages privileges, source freshness, and bounded report extraction."""
    workflow = yaml.safe_load(path.read_text())
    errors: list[str] = []
    if workflow.get("permissions") != {} or set(workflow.get("jobs", {})) != {"pages"}:
        return ["Allure Pages must have one explicitly scoped job"]
    pages = workflow["jobs"]["pages"]
    if pages.get("permissions") != {"actions": "read", "contents": "write", "pull-requests": "read"}:
        errors.append("Allure Pages permissions are not narrowly scoped")
    freshness = next((step for step in pages.get("steps", []) if step.get("id") == "freshness"), {})
    script = freshness.get("with", {}).get("script", "")
    for guard in ('run.name !== process.env.SOURCE_WORKFLOW || run.path !== process.env.SOURCE_WORKFLOW_PATH',
                  'pull.state === "open" && pull.head?.sha === run.head_sha',
                  'pull.base?.repo?.full_name === `${context.repo.owner}/${context.repo.repo}`',
                  'pull.user?.login === "dependabot[bot]"',
                  'if (!sameHead || !latest)', 'matches.length !== 1 || matches[0].expired',
                  'const expectedName = `allure-report-${context.runId}`'):
        if guard not in script:
            errors.append(f"Allure Pages executable freshness guard missing: {guard}")
    for key, value in {"PR_NUMBER": "pr-number", "SOURCE_WORKFLOW": "source-workflow",
                       "SOURCE_WORKFLOW_PATH": "source-workflow-path"}.items():
        if freshness.get("env", {}).get(key) != "${{ inputs." + value + " }}":
            errors.append(f"Allure Pages input binding missing: {value}")
    steps = pages.get("steps", [])
    for step in steps[1:]:
        if step.get("if") not in ("${{ steps.freshness.outputs.fresh == 'true' }}",
                                  "${{ steps.freshness.outputs.fresh == 'true' && steps.publication.outputs.fresh == 'true' }}"):

            errors.append("Allure Pages must gate every publication step on source freshness")
        if "actions/download-artifact@" in str(step.get("uses", "")):
            errors.append("Allure Pages ZIPs must pass bounded extraction")
    helper = next((step for step in steps if step.get("name") == "Check out trusted toolkit extractor"), {})
    if helper.get("with", {}).get("repository") != "${{ job.workflow_repository }}" or helper.get("with", {}).get("ref") != "${{ job.workflow_sha }}":
        errors.append("Allure Pages extractor must follow the called workflow commit")
    return errors


def allure_legacy_wrapper_errors(path: Path) -> list[str]:
    """Keep the old public API while delegating to the same-commit core and Pages workflows."""
    workflow = yaml.safe_load(path.read_text())
    errors: list[str] = []
    call = workflow.get("on", workflow.get(True))["workflow_call"]
    inputs = call["inputs"]
    for name in ("source-workflow", "source-workflow-path", "artifact-prefix", "minimum-artifacts", "maximum-artifacts",
                 "config-file", "categories-file", "pages-url", "publish-pages", "pages-destination-directory"):
        if name not in inputs:
            errors.append(f"Allure legacy wrapper removed input: {name}")
    if call["secrets"]["github-token"].get("required") is not False:
        errors.append("Allure legacy override token must be optional")
    jobs = workflow.get("jobs", {})
    if set(jobs) != {"report", "pages"}:
        return errors + ["Allure legacy wrapper must delegate report and optional Pages"]
    if jobs["report"].get("uses") != "./.github/workflows/allure-publisher-core.yml" or jobs["pages"].get("uses") != "./.github/workflows/allure-pages.yml":
        errors.append("Allure legacy wrapper must use same-commit reusable workflows")
    if "inputs.publish-pages" not in jobs["pages"].get("if", ""):
        errors.append("Allure legacy Pages must be opt-in")
    for name in jobs["report"].get("with", {}):
        if jobs["report"]["with"][name] != "${{ inputs." + name + " }}":
            errors.append(f"Allure legacy input must pass unchanged: {name}")
    return errors



def allure_publisher_negative_probes(path: Path) -> list[str]:
    """Prove trust checks fail when executable guards are removed or malformed."""
    errors: list[str] = []
    source = path.read_text()
    parsed = yaml.safe_load(source)
    # The canonical validator reports malformed shapes before this negative
    # probe driver runs.  Do not dereference those shapes while probing guards.
    jobs = parsed.get("jobs") if isinstance(parsed, dict) else None
    resolve = jobs.get("resolve") if isinstance(jobs, dict) else None
    steps = resolve.get("steps") if isinstance(resolve, dict) else None
    if not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps):
        return errors
    generate_pin = next(
        (
            str(step.get("uses"))
            for job in (jobs or {}).values()
            if isinstance(job, dict)
            for step in (job.get("steps") or [])
            if isinstance(step, dict)
            and str(step.get("uses", "")).startswith(f"{GENERATE_REPORT_ACTION}@")
        ),
        f"{GENERATE_REPORT_ACTION}@" + "0" * 40,
    )
    required_by_job = {
        "generate": generate_pin,
        "resolve": (
            "No external Allure artifacts found",
            "External Allure artifact contract mismatch",
            'pull.user?.login === "dependabot[bot]"',
            "A newer source workflow run exists",
        ),
    }
    with tempfile.TemporaryDirectory(prefix="allure-validator-probes-", dir=ROOT) as tmp:
        probe_root = Path(tmp)
        for job_name, literals in required_by_job.items():
            for literal in (literals,) if isinstance(literals, str) else literals:
                mutated = source.replace(literal, "removed-trust-guard", 1)
                fixture = probe_root / f"missing-{job_name}-{len(errors)}.yml"
                fixture.write_text(mutated, encoding="utf-8")
                # The generate guard is reported by action name, since the pin it
                # carries is expected to move with every release bump.
                expected = GENERATE_REPORT_ACTION if literal == generate_pin else literal
                if not any(expected in error for error in allure_publisher_workflow_errors(fixture)):
                    errors.append(f"missing executable guard probe did not fail: {job_name}/{literal}")

        comment_only = source.replace(
            "A newer source workflow run exists",
            "removed-trust-guard",
            1,
        ) + "\n# A newer source workflow run exists\n"
        fixture = probe_root / "comment-only.yml"
        fixture.write_text(comment_only, encoding="utf-8")
        if not any(
            "A newer source workflow run exists" in error
            for error in allure_publisher_workflow_errors(fixture)
        ):
            errors.append("comment-only trust guard probe did not fail")

        # Each assertion must remain tied to the executable resolve step.  For
        # every marker, retain it only as a comment, in an unrelated step, or
        # below an unreachable branch; all three mutations must fail.
        resolve_markers = (
            "No external Allure artifacts found",
            "External Allure artifact contract mismatch",
            'pull.user?.login === "dependabot[bot]"',
            "A newer source workflow run exists",
        )
        for marker in resolve_markers:
            removed = source.replace(marker, "removed-trust-guard", 1)
            comment_fixture = probe_root / f"comment-{len(errors)}.yml"
            comment_fixture.write_text(removed + f"\n// {marker}\n", encoding="utf-8")
            if not allure_publisher_workflow_errors(comment_fixture):
                errors.append(f"comment-only trust guard probe did not fail: {marker}")

            unrelated = yaml.safe_load(removed)
            resolve = unrelated["jobs"]["resolve"]
            resolve["steps"].append(
                {
                    "name": "Unrelated marker text",
                    "uses": resolve["steps"][0]["uses"],
                    "with": {"script": f"core.info({marker!r});"},
                }
            )
            unrelated_fixture = probe_root / f"unrelated-{len(errors)}.yml"
            unrelated_fixture.write_text(yaml.safe_dump(unrelated, sort_keys=False), encoding="utf-8")
            if not allure_publisher_workflow_errors(unrelated_fixture):
                errors.append(f"unrelated-step trust guard probe did not fail: {marker}")

            dead = yaml.safe_load(removed)
            dead_script = dead["jobs"]["resolve"]["steps"][0]["with"]["script"]
            dead["jobs"]["resolve"]["steps"][0]["with"]["script"] = (
                dead_script + f"\nif (false) {{ core.info({marker!r}); }}\n"
            )
            dead_fixture = probe_root / f"dead-{len(errors)}.yml"
            dead_fixture.write_text(yaml.safe_dump(dead, sort_keys=False), encoding="utf-8")
            if not allure_publisher_workflow_errors(dead_fixture):
                errors.append(f"dead-branch trust guard probe did not fail: {marker}")

        # Regression guard: a dependency bump of the github-script pin must not
        # make the trust guards look absent (surfaced by PR #176).
        bumped = re.sub(
            r"(uses: actions/github-script@)[0-9a-f]{40}",
            r"\g<1>" + "0" * 40,
            source,
        )
        bumped_fixture = probe_root / "bumped-github-script.yml"
        bumped_fixture.write_text(bumped, encoding="utf-8")
        bumped_errors = [
            error for error in allure_publisher_workflow_errors(bumped_fixture)
            if "trust-boundary assertion" in error
        ]
        if bumped_errors:
            errors.append(
                "github-script pin bump probe lost trust guards: " + "; ".join(bumped_errors)
            )

        # Regression guard: the same must hold for the report builder pin, whose
        # bump broke this validator in PR #174.
        bumped_generate = re.sub(
            rf"(uses: {re.escape(GENERATE_REPORT_ACTION)}@)[0-9a-f]{{40}}",
            r"\g<1>" + "0" * 40,
            source,
        )
        bumped_generate_fixture = probe_root / "bumped-report-action.yml"
        bumped_generate_fixture.write_text(bumped_generate, encoding="utf-8")
        bumped_generate_errors = [
            error for error in allure_publisher_workflow_errors(bumped_generate_fixture)
            if "trust-boundary assertion" in error
        ]
        if bumped_generate_errors:
            errors.append(
                "report action pin bump probe lost trust guards: "
                + "; ".join(bumped_generate_errors)
            )

        parsed = yaml.safe_load(source)
        malformed_mutations = (
            ("null job", lambda workflow: workflow["jobs"].__setitem__("resolve", None), "resolve job must be a mapping"),
            ("scalar job", lambda workflow: workflow["jobs"].__setitem__("resolve", "invalid"), "resolve job must be a mapping"),
            ("null steps", lambda workflow: workflow["jobs"]["resolve"].__setitem__("steps", None), "resolve steps must be a list"),
            ("scalar steps", lambda workflow: workflow["jobs"]["resolve"].__setitem__("steps", "invalid"), "resolve steps must be a list"),
            ("null step item", lambda workflow: workflow["jobs"]["resolve"]["steps"].__setitem__(0, None), "resolve step 0 must be a mapping"),
            ("scalar step item", lambda workflow: workflow["jobs"]["resolve"]["steps"].__setitem__(0, "invalid"), "resolve step 0 must be a mapping"),
        )
        for mutation, apply_mutation, expected in malformed_mutations:
            malformed = json.loads(json.dumps(parsed))
            apply_mutation(malformed)
            fixture = probe_root / f"malformed-{len(errors)}.yml"
            fixture.write_text(yaml.safe_dump(malformed, sort_keys=False), encoding="utf-8")
            fixture_errors = allure_publisher_workflow_errors(fixture)
            if not any(expected in error for error in fixture_errors):
                errors.append(f"malformed workflow probe did not report {expected} ({mutation})")
    return errors


ALLURE_WORKFLOW = ROOT / ".github/workflows/allure-publisher-core.yml"
ALLURE_PAGES_WORKFLOW = ROOT / ".github/workflows/allure-pages.yml"
ERRORS.extend(allure_publisher_workflow_errors(ALLURE_WORKFLOW))
ERRORS.extend(allure_pages_workflow_errors(ALLURE_PAGES_WORKFLOW))
ERRORS.extend(allure_legacy_wrapper_errors(ROOT / ".github/workflows/allure-publisher.yml"))
ERRORS.extend(allure_publisher_negative_probes(ALLURE_WORKFLOW))

secret_patterns = [
    r"ghp_[A-Za-z0-9]{20,}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    "/ho" + r"me/[^/\s]+",
    r"BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY",
]
for path in sorted(p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts):
    try:
        text = path.read_text()
    except UnicodeDecodeError:
        continue
    for pattern in secret_patterns:
        if re.search(pattern, text):
            ERRORS.append(f"{path.relative_to(ROOT)}: prohibited secret/personal-path pattern")

if ERRORS:
    print("\n".join("ERROR: " + e for e in ERRORS), file=sys.stderr)
    raise SystemExit(1)

check(
    not any("ci-kit.md" in str(entry) for entry in skip_if_exists),
    "docs/ci-kit.md must stay toolkit-owned so copier update refreshes it",
)
copier = shutil.which("copier")
check(copier is not None, "copier executable is required")
actionlint = shutil.which("actionlint")
check(actionlint is not None, "actionlint executable is required")
if ERRORS:
    print("\n".join("ERROR: " + e for e in ERRORS), file=sys.stderr)
    raise SystemExit(1)
assert copier is not None and actionlint is not None
with tempfile.TemporaryDirectory(prefix="project-toolkit-validation-") as tmp:
    tmp_path = Path(tmp)
    template_source = shutil.copytree(
        ROOT,
        tmp_path / "template-source",
        ignore=shutil.ignore_patterns(
            ".git", ".worktrees", ".ruff_cache", ".venv", "__pycache__", "*.pyc", "build", "dist", "node_modules"
        ),
    )
    run(["git", "init", "-q"], template_source)
    run(["git", "config", "user.email", "fixture@example.invalid"], template_source)
    run(["git", "config", "user.name", "Fixture"], template_source)
    run(["git", "config", "commit.gpgsign", "false"], template_source)
    run(["git", "config", "core.hooksPath", "/dev/null"], template_source)
    run(["git", "add", "."], template_source)
    run(["git", "commit", "--no-verify", "-qm", "candidate template"], template_source)
    expected_scenario_presets = {
        "python": ["default", "github-actions", "python"],
        "node": ["default", "github-actions", "javascript"],
        "java": ["default", "github-actions", "java"],
        "polyglot": ["default", "github-actions", "python", "javascript", "java"],
        "same-type": ["default", "github-actions", "python"],
        "allure-polyglot": ["default", "github-actions", "python", "javascript", "java"],
        "allure-pages": ["default", "github-actions", "python"],
        "allure-external": ["default", "github-actions"],
        "docker": ["default", "github-actions", "python", "docker"],
    }
    for scenario, expected_presets in expected_scenario_presets.items():
        dest = tmp_path / scenario
        run(
            [
                copier,
                "copy",
                "--trust",
                "--defaults",
                "--vcs-ref",
                "HEAD",
                "--data-file",
                str(template_source / f"tests/scenarios/{scenario}.yml"),
                str(template_source),
                str(dest),
            ]
        )
        if scenario == "allure-external":
            external_workflow = dest / ".github/workflows/test.yml"
            external_workflow.write_text(
                "name: Run tests\n"
                "on: [pull_request]\n"
                "permissions:\n"
                "  contents: read\n"
                "jobs:\n"
                "  test:\n"
                "    runs-on: ubuntu-latest\n"
                "    steps:\n"
                "      - run: echo fixture\n",
                encoding="utf-8",
            )
            categories = dest / ".github/allure/categories.json"
            categories.parent.mkdir(parents=True, exist_ok=True)
            categories.write_text("[]\n", encoding="utf-8")
        for generated_workflow in sorted((dest / ".github/workflows").glob("*.yml")):
            if generated_workflow.name == "allure-report.yml":
                continue
            run([actionlint, str(generated_workflow)])
        allure_workflow_path = dest / ".github/workflows/allure-report.yml"
        allure_config_path = dest / ".github/allure/allurerc.mjs"
        allure_extractor_path = dest / ".github/allure/safe_extract.py"
        if scenario.startswith("allure-"):
            run([actionlint, str(allure_workflow_path)])
            check(allure_config_path.is_file(), f"{scenario}: missing Allure 3 config")
            if allure_config_path.is_file():
                run(["node", "--check", str(allure_config_path)])
            resolver_script = yaml.safe_load(ALLURE_WORKFLOW.read_text())["jobs"]["resolve"]["steps"][0]["with"]["script"]
            resolver_script_path = tmp_path / f"{scenario}-resolver.js"
            resolver_script_path.write_text(f"async function main() {{\n{resolver_script}\n}}\n", encoding="utf-8")
            run(["node", "--check", str(resolver_script_path)])
            check(allure_extractor_path.is_file(), f"{scenario}: missing bounded ZIP extractor")
            if allure_extractor_path.is_file():
                run([sys.executable, "-m", "py_compile", str(allure_extractor_path)])
            allure_workflow = yaml.safe_load(allure_workflow_path.read_text())
            allure_triggers = allure_workflow.get("on", allure_workflow.get(True))
            expected_source_workflows = ["Validate", "Run tests"] if scenario == "allure-external" else ["Validate"]
            check(
                allure_triggers
                == {"workflow_run": {"workflows": expected_source_workflows, "types": ["completed"]}},
                f"{scenario}: report workflow has the wrong source workflow trigger",
            )
            check(
                allure_workflow.get("permissions") == {},
                f"{scenario}: workflow-level permissions must remain empty",
            )
            caller_jobs = allure_workflow.get("jobs", {})
            caller = caller_jobs.get("report", {})
            check(set(caller_jobs) == ({"report", "pages"} if scenario == "allure-pages" else {"report"})
                  and caller.get("uses") == "quokkify/ci-kit/.github/workflows/allure-publisher-core.yml@v1.0.0"
                  and caller.get("permissions") == {"actions": "read", "contents": "read", "pull-requests": "write"}
                  and all("steps" not in job and "secrets" not in job for job in caller_jobs.values()),
                  f"{scenario}: generated caller must delegate without broad token secrets")
            jobs = yaml.safe_load(ALLURE_WORKFLOW.read_text())["jobs"]
            if scenario == "allure-pages":
                jobs["pages"] = yaml.safe_load(ALLURE_PAGES_WORKFLOW.read_text())["jobs"]["pages"]
                check(caller_jobs["pages"].get("uses") == "quokkify/ci-kit/.github/workflows/allure-pages.yml@v1.0.0"
                      and caller_jobs["pages"].get("permissions") == {"actions": "read", "contents": "write", "pull-requests": "read"},
                      "allure-pages: only the separate Pages call may grant contents:write")
            check(
                jobs.get("resolve", {}).get("permissions")
                == {"actions": "read", "contents": "read", "pull-requests": "read"},
                f"{scenario}: resolver permissions are not read-only",
            )
            resolve_condition = jobs.get("resolve", {}).get("if", "")
            check(
                "github.event.workflow_run.event == 'pull_request'" in resolve_condition
                and 'fromJSON(\'["success","failure","neutral","timed_out"]\')'
                in resolve_condition
                and "github.event.workflow_run.conclusion" in resolve_condition,
                f"{scenario}: resolver does not allowlist reportable source conclusions",
            )
            check(
                jobs.get("generate", {}).get("permissions")
                == {"actions": "read", "contents": "read"},
                f"{scenario}: untrusted report generation has write permissions",
            )
            check(
                jobs.get("comment", {}).get("permissions")
                == {"actions": "read", "pull-requests": "write"},
                f"{scenario}: comment job permissions are not narrowly scoped",
            )
            write_content_jobs = [
                name
                for name, job in jobs.items()
                if isinstance(job, dict)
                and job.get("permissions", {}).get("contents") == "write"
            ]
            check(
                write_content_jobs == (["pages"] if scenario == "allure-pages" else []),
                f"{scenario}: only the opt-in Pages job may receive contents:write",
            )
            caller_text = allure_workflow_path.read_text()
            report_text = ALLURE_WORKFLOW.read_text() + (ALLURE_PAGES_WORKFLOW.read_text() if scenario == "allure-pages" else "")
            pr_number_script_steps = []
            for job_name in ("comment", "pages"):
                for step in jobs.get(job_name, {}).get("steps", []):
                    script = step.get("with", {}).get("script", "")
                    if "const prNumber" in script:
                        pr_number_script_steps.append(step)
            check(
                len(pr_number_script_steps) == (3 if scenario == "allure-pages" else 1)
                and all(
                    step.get("env", {}).get("PR_NUMBER")
                    in ("${{ needs.resolve.outputs.pr-number }}", "${{ inputs.pr-number }}")
                    and "const prNumber = Number(process.env.PR_NUMBER);"
                    in step.get("with", {}).get("script", "")
                    and "${{ needs.resolve.outputs.pr-number }}"
                    not in step.get("with", {}).get("script", "")
                    for step in pr_number_script_steps
                ),
                f"{scenario}: PR number is interpolated directly into a github-script body",
            )
            check(
                "ref: ${{ github.sha }}" in report_text
                and "github.event.repository.default_branch" not in report_text,
                f"{scenario}: report config is not pinned to the downstream default-branch SHA",
            )
            extractor_text = allure_extractor_path.read_text()
            external_allure = scenario == "allure-external"
            preflight_jobs = (jobs["download"], jobs.get("pages", {"steps": []}))
            check(
                all(
                    "actions/download-artifact@" not in str(step.get("uses", ""))
                    for job in preflight_jobs
                    for step in job.get("steps", [])
                )
                and "artifact_manifest" in report_text
                and "${{ runner.temp }}/allure-archives" in report_text
                and "${{ runner.temp }}/allure-expanded" in report_text
                and "MATERIALIZE_ROOT: ${{ github.workspace }}/${{ needs.resolve.outputs.materialize-root }}"
                in report_text
                and "python .toolkit/template/.github/allure/safe_extract.py.jinja" in report_text
                and "repository: ${{ job.workflow_repository }}" in report_text
                and "ref: ${{ job.workflow_sha }}" in report_text,
                f"{scenario}: source or Pages ZIPs are extracted before bounded preflight",
            )
            check(
                "workflow_run.pull_requests[0]" not in report_text
                and "Expected exactly one current open PR" in report_text
                and "A newer source workflow run exists" in report_text,
                f"{scenario}: PR resolution or stale-run suppression is incomplete",
            )
            check(
                "preflight_eocd" in extractor_text
                and "MAX_CENTRAL_DIRECTORY" in extractor_text
                and "MAX_UNCOMPRESSED" in extractor_text
                and "duplicate artifact path" in extractor_text
                and 'pull.user?.login === "dependabot[bot]"' in report_text,
                f"{scenario}: pre-extraction collision and resource bounds are missing",
            )
            validate_text = (dest / ".github/workflows/validate.yml").read_text()
            check(
                'answers.get("allure_report")' in validate_text
                and 'Path(".github/allure/allurerc.mjs")' in validate_text
                and 'Path(".github/allure/safe_extract.py")' in validate_text,
                f"{scenario}: generated contract does not verify Allure outputs",
            )
            artifact_names = ["allure-results-app-python"]
            if scenario == "allure-polyglot":
                artifact_names = [
                    "allure-results-backend-python",
                    "allure-results-frontend-node",
                    "allure-results-worker-java",
                ]
            if scenario == "allure-external":
                artifact_names = ["external-allure-one", "external-allure-two"]
                configured = caller["with"]
                check('workflows: [Validate, "Run tests"]' in caller_text
                      and configured.get("source-workflow") == "Run tests"
                      and configured.get("source-workflow-path") == ".github/workflows/test.yml"
                      and configured.get("artifact-mode") == "external"
                      and configured.get("artifact-prefix") == "external-allure-"
                      and configured.get("minimum-artifacts") == 2
                      and configured.get("maximum-artifacts") == 7
                      and configured.get("skip-empty-artifacts") is True
                      and configured.get("categories-file") == ".github/allure/categories.json",
                      "allure-external: rendered source workflow or bounded artifact contract is incomplete")
            else:
                for artifact_name in artifact_names:
                    check(artifact_name in validate_text, f"{scenario}: missing source workflow artifact for {artifact_name}")
                check(caller["with"].get("artifact-mode") == "component"
                      and caller["with"].get("source-workflow") == "Validate"
                      and caller["with"].get("source-workflow-path") == ".github/workflows/validate.yml"
                      and "const sourceArtifactContractVersion = 1" in report_text
                      and "const componentArtifactPattern = /^allure-results-[A-Za-z_][A-Za-z0-9_-]*$/" in report_text,
                      f"{scenario}: migration-safe source artifact contract is missing")
            check('source-artifacts-directory: ${{ needs.resolve.outputs.source-artifacts-directory }}' in report_text
                  and 'const materializeRoot = componentMode ? ".allure-input/results" : ".allure-input/source-artifacts";' in report_text
                  and "allureArtifacts.map((artifact) => ({" in report_text,
                  f"{scenario}: stable Allure source materialization contract is missing")
            if scenario == "allure-polyglot":
                check(
                    'test-artifact-path: "reports/allure-results"' in validate_text
                    and 'test-artifact-path: "build/allure-results"' in validate_text,
                    "allure-polyglot: custom per-component result paths were not rendered",
                )
                probe_root = tmp_path / "allure-zip-probe"
                fixture_archives = probe_root / "fixtures"
                archive_root = probe_root / "downloaded"
                output_root = probe_root / "output"
                materialized_root = dest / ".allure-input" / "results"
                fixture_archives.mkdir(parents=True)
                manifest = [
                    {"name": artifact_name, "id": index}
                    for index, artifact_name in enumerate(artifact_names, 1)
                ]

                def write_archives(files_by_artifact: dict[str, dict[str, str]]) -> None:
                    shutil.rmtree(fixture_archives, ignore_errors=True)
                    fixture_archives.mkdir(parents=True)
                    for artifact_name, files in files_by_artifact.items():
                        with zipfile.ZipFile(
                            fixture_archives / f"{artifact_name}.zip",
                            "w",
                            compression=zipfile.ZIP_DEFLATED,
                        ) as bundle:
                            for relative, content in files.items():
                                bundle.writestr(relative, content)

                extract_env = dict(
                    os.environ,
                    ARTIFACT_MANIFEST=json.dumps(manifest),
                    ARTIFACT_ARCHIVE_DIR=str(fixture_archives),
                    ARCHIVE_ROOT=str(archive_root),
                    OUTPUT_ROOT=str(output_root),
                    MATERIALIZE_ROOT=str(materialized_root),
                    GITHUB_WORKSPACE=str(dest),
                )
                valid_files = {
                    artifact_name: {f"{artifact_name}.json": "{}"}
                    for artifact_name in artifact_names
                }
                write_archives(valid_files)
                run([sys.executable, str(allure_extractor_path)], dest, extract_env)
                check(
                    {path.name for path in materialized_root.iterdir()}
                    == {f"{artifact_name}.json" for artifact_name in artifact_names},
                    "allure-polyglot: bounded ZIP merge omitted expected files",
                )

                shutil.rmtree(materialized_root.parent)
                external_target = probe_root / "must-stay-empty"
                external_target.mkdir()
                materialized_root.parent.symlink_to(external_target, target_is_directory=True)
                materialization_probe = subprocess.run(
                    [sys.executable, str(allure_extractor_path)],
                    cwd=dest,
                    env=extract_env,
                    text=True,
                    capture_output=True,
                )
                check(
                    materialization_probe.returncode != 0
                    and "unsafe workspace ancestor" in materialization_probe.stderr
                    and not any(external_target.iterdir()),
                    "allure-polyglot: validated materialization followed a workspace symlink",
                )
                materialized_root.parent.unlink()

                collision_files = dict(valid_files)
                collision_files[artifact_names[0]] = {"collision.json": "{}"}
                collision_files[artifact_names[1]] = {"collision.json": "{}"}
                write_archives(collision_files)
                collision_probe = subprocess.run(
                    [sys.executable, str(allure_extractor_path)],
                    cwd=dest,
                    env=extract_env,
                    text=True,
                    capture_output=True,
                )
                check(
                    collision_probe.returncode != 0
                    and "duplicate artifact path: collision.json" in collision_probe.stderr,
                    "allure-polyglot: duplicate ZIP paths were not rejected before extraction",
                )

                traversal_files = dict(valid_files)
                traversal_files[artifact_names[0]] = {"../escape.json": "{}"}
                write_archives(traversal_files)
                traversal_probe = subprocess.run(
                    [sys.executable, str(allure_extractor_path)],
                    cwd=dest,
                    env=extract_env,
                    text=True,
                    capture_output=True,
                )
                check(
                    traversal_probe.returncode != 0
                    and "unsafe path in artifact ZIP" in traversal_probe.stderr,
                    "allure-polyglot: ZIP path traversal was not rejected",
                )

                write_archives(valid_files)
                symlink_archive = fixture_archives / f"{artifact_names[0]}.zip"
                with zipfile.ZipFile(symlink_archive, "w") as bundle:
                    symlink = zipfile.ZipInfo("link.json")
                    symlink.create_system = 3
                    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
                    bundle.writestr(symlink, "target.json")
                symlink_probe = subprocess.run(
                    [sys.executable, str(allure_extractor_path)],
                    cwd=dest,
                    env=extract_env,
                    text=True,
                    capture_output=True,
                )
                check(
                    symlink_probe.returncode != 0
                    and "non-regular ZIP entry rejected" in symlink_probe.stderr,
                    "allure-polyglot: ZIP symlink was not rejected before extraction",
                )

                write_archives(valid_files)
                oversized_archive = fixture_archives / f"{artifact_names[0]}.zip"
                archive_bytes = bytearray(oversized_archive.read_bytes())
                central_header = archive_bytes.index(b"PK\x01\x02")
                archive_bytes[central_header + 24 : central_header + 28] = (1073741825).to_bytes(
                    4, "little"
                )
                oversized_archive.write_bytes(archive_bytes)
                shutil.rmtree(output_root, ignore_errors=True)
                shutil.rmtree(materialized_root, ignore_errors=True)
                oversized_probe = subprocess.run(
                    [sys.executable, str(allure_extractor_path)],
                    cwd=dest,
                    env=extract_env,
                    text=True,
                    capture_output=True,
                )
                check(
                    oversized_probe.returncode != 0
                    and (
                        "artifact entry exceeds 256 MiB" in oversized_probe.stderr
                        or "pre-extraction file-count or 1 GiB limits" in oversized_probe.stderr
                    )
                    and not output_root.exists()
                    and not materialized_root.exists(),
                    "allure-polyglot: declared ZIP bomb was not rejected before extraction",
                )
            check(caller["with"].get("publish-pages") == (scenario == "allure-pages")
                  and caller["with"].get("managed-pages-layout") is True
                  and ("pages" in caller_jobs) == (scenario == "allure-pages"),
                  f"{scenario}: Pages publishing does not match the Copier answers")
            if scenario == "allure-pages":
                check(caller["with"].get("pages-url") == "https://quokkify.github.io/fixture-allure-pages"
                      and caller_jobs["pages"]["with"].get("pages-destination-directory")
                      == "${{ format('allure/pr-{0}', needs.report.outputs.pr-number) }}"
                      and "format('{0}/allure/pr-{1}/allure-report/'" in report_text,
                      "allure-pages: commented report URL does not address the published report directory")

        else:
            check(not allure_workflow_path.exists(), f"{scenario}: Allure workflow generated while disabled")
            check(not allure_config_path.exists(), f"{scenario}: Allure config generated while disabled")
            check(not allure_extractor_path.exists(), f"{scenario}: Allure extractor generated while disabled")
        generated_validate = (dest / ".github/workflows/validate.yml").read_text()
        check(
            not generated_validate.endswith("\n\n"),
            f"{scenario}: validate workflow has a trailing blank line",
        )
        generated_readme = (dest / "README.md").read_text()
        check(
            not generated_readme.endswith("\n\n"),
            f"{scenario}: README has a trailing blank line that blocks Copier rollout",
        )
        onboarding_path = dest / "docs/ci-kit.md"
        check(onboarding_path.is_file(), f"{scenario}: missing docs/ci-kit.md onboarding guide")
        if onboarding_path.is_file():
            onboarding = onboarding_path.read_text()
            check(
                not onboarding.endswith("\n\n") and "{%" not in onboarding and "{{" not in onboarding,
                f"{scenario}: onboarding guide has unrendered Jinja or a trailing blank line",
            )
            check(
                ("allure-report.yml" in onboarding) == scenario.startswith("allure-"),
                f"{scenario}: onboarding guide Allure row does not match the Copier answers",
            )
        for owned in ("/README.md", ".github/renovate.json"):
            check(
                owned in skip_if_exists,
                f"copier.yml must keep {owned} in _skip_if_exists so an update never "
                "overwrites a file the project owns",
            )
        check(
            (dest / ".copier-answers.yml").exists(),
            f"{scenario}: missing .copier-answers.yml",
        )
        assert_generated_renovate_config(
            dest / ".github/renovate.json",
            renovate_extends("quokkify/renovate-presets", expected_presets),
            scenario,
        )
        if scenario == "polyglot":
            generated = (dest / ".github/workflows/validate.yml").read_text()
            for name in (
                "python-ci.yml",
                "node-ci.yml",
                "java-ci.yml",
            ):
                check(name in generated, f"polyglot generated workflow missing {name}")
            check(
                "docker-build.yml" not in generated,
                "polyglot generated workflow unexpectedly includes Docker",
            )
        if scenario == "same-type":
            generated = (dest / ".github/workflows/validate.yml").read_text()
            for job_id, display_name, path in (
                ("backend-python", "Backend Python", "backend"),
                ("tools-python", "Tools Python", "tools"),
            ):
                check(f"  {job_id}:" in generated, f"same-type missing job {job_id}")
                check(f'name: "{display_name}"' in generated, f"same-type missing display name for {job_id}")
                check(f'working-directory: "{path}"' in generated, f"same-type missing path for {job_id}")
        if scenario == "python":
            run(["git", "init", "-q"], dest)
            run(["git", "config", "user.email", "fixture@example.invalid"], dest)
            run(["git", "config", "user.name", "Fixture"], dest)
            run(["git", "config", "commit.gpgsign", "false"], dest)
            run(["git", "config", "core.hooksPath", "/dev/null"], dest)
            run(["git", "add", "."], dest)
            run(["git", "commit", "--no-verify", "-qm", "fixture"], dest)
            run([copier, "update", "--trust", "--defaults"], dest)
            status = subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=dest, text=True
            )
            check(status == "", f"copier update was not idempotent: {status}")
            answers = yaml.safe_load((dest / ".copier-answers.yml").read_text())
            check(
                answers.get("renovate_config_repository")
                == "quokkify/renovate-presets",
                "copier update did not persist renovate_config_repository",
            )
            check(
                answers.get("renovate_presets") == ["default", "github-actions", "python"],
                "copier update did not persist inferred renovate_presets",
            )

    invalid_component_cases = {
        "duplicate-id": [{"type": "python", "path": ".", "id": "same", "name": "One"}, {"type": "node", "path": ".", "id": "same", "name": "Two"}],
        "invalid-id": [{"type": "python", "path": ".", "id": "bad.id", "name": "Application"}],
        "empty-name": [{"type": "python", "path": ".", "id": "app-python", "name": "  "}],
        "reserved-id": [{"type": "python", "path": ".", "id": "docker", "name": "Application"}],
        "missing-required-fields": [{"type": "python", "path": "."}],
    }
    for reserved_id in (
        "template-contract", "docker", "release", "update", "scan", "analyze",
        "resolve", "generate", "comment", "pages", "changes", "integration",
    ):
        invalid_component_cases[f"reserved-{reserved_id}"] = [
            {"type": "python", "path": ".", "id": reserved_id, "name": "Application"}
        ]
    for case_name, invalid_components in invalid_component_cases.items():
        invalid_data = tmp_path / f"{case_name}.yml"
        invalid_data.write_text(yaml.safe_dump({"components": invalid_components}))
        invalid_result = subprocess.run(
            [
                copier,
                "copy",
                "--trust",
                "--defaults",
                "--vcs-ref",
                "HEAD",
                "--data-file",
                str(invalid_data),
                str(template_source),
                str(tmp_path / case_name),
            ],
            text=True,
            capture_output=True,
        )
        check(
            invalid_result.returncode != 0,
            f"{case_name}: invalid component configuration was accepted",
        )

    config_only_data = tmp_path / "config-only.yml"
    config_only_data.write_text(
        yaml.safe_dump(
            {
                "project_name": "fixture-config-only",
                "toolkit_version": "v1.0.0",
                "components": [],
                "docker": False,
                "release_please": True,
                "renovate": True,
                "renovate_config_repository": "quokkify/renovate-presets",
                "renovate_presets": ["default", "github-actions"],
            }
        )
    )
    config_only_dest = tmp_path / "config-only"
    run(
        [
            copier,
            "copy",
            "--trust",
            "--defaults",
            "--vcs-ref",
            "HEAD",
            "--data-file",
            str(config_only_data),
            str(template_source),
            str(config_only_dest),
        ]
    )
    check(
        (config_only_dest / ".github/workflows/validate.yml").exists(),
        "config-only project lost shared template-contract validation",
    )
    config_only_validate = (config_only_dest / ".github/workflows/validate.yml").read_text()
    check(
        not config_only_validate.endswith("\n\n"),
        "config-only validate workflow has a trailing blank line",
    )
    for workflow_name in ("python-ci.yml", "node-ci.yml", "java-ci.yml", "docker-build.yml"):
        check(
            workflow_name not in config_only_validate,
            f"config-only project unexpectedly generated {workflow_name}",
        )
    for generated_workflow in sorted((config_only_dest / ".github/workflows").glob("*.yml")):
        run([actionlint, str(generated_workflow)])
    config_only_gitleaks = (config_only_dest / ".github/workflows/gitleaks.yml").read_text()
    check(
        'install -m 0755 gitleaks "$RUNNER_TEMP/bin/gitleaks"' in config_only_gitleaks,
        "generated Gitleaks workflow installs outside its extraction directory",
    )
    check(
        'git show "$TRUSTED_REF:.gitleaks.toml"' in config_only_gitleaks
        and '--config "$RUNNER_TEMP/gitleaks.toml"' in config_only_gitleaks
        and '--gitleaks-ignore-path "$RUNNER_TEMP/gitleaksignore"' in config_only_gitleaks,
        "generated Gitleaks workflow does not protect policy from pull request changes",
    )
    check(
        'if ! git cat-file -e "$TRUSTED_REF^{commit}" 2>/dev/null; then' in config_only_gitleaks,
        "generated Gitleaks workflow fetches a ref the credential-less checkout already contains",
    )
    check(
        'Path(".github/renovate.json")' in config_only_validate
        and 'git diff --check "$BASE_SHA..$HEAD_SHA"' in config_only_validate
        and '"$RUNNER_TEMP/bin/actionlint"' in config_only_validate,
        "generated validation workflow is missing contract gates",
    )
    check(
        (config_only_dest / ".github/workflows/release.yml").exists(),
        "config-only project lost Release Please workflow",
    )
    assert_generated_renovate_config(
        config_only_dest / ".github/renovate.json",
        renovate_extends("quokkify/renovate-presets", ["default", "github-actions"]),
        "config-only",
    )

    no_renovate_data = tmp_path / "no-renovate.yml"
    no_renovate_data.write_text(
        yaml.safe_dump(
            {
                "project_name": "fixture-no-renovate",
                "toolkit_version": "v1.0.0",
                "components": [{"type": "node", "path": ".", "id": "app-node", "name": "Application Node"}],
                "docker": False,
                "release_please": False,
                "renovate": False,
            }
        )
    )
    no_renovate_dest = tmp_path / "no-renovate"
    run(
        [
            copier,
            "copy",
            "--trust",
            "--defaults",
            "--vcs-ref",
            "HEAD",
            "--data-file",
            str(no_renovate_data),
            str(template_source),
            str(no_renovate_dest),
        ]
    )
    check(
        not (no_renovate_dest / ".github/renovate.json").exists(),
        "renovate=false generated .github/renovate.json",
    )

    no_codeql_data = tmp_path / "no-codeql.yml"
    no_codeql_data.write_text(
        yaml.safe_dump(
            {
                "project_name": "fixture-no-codeql",
                "toolkit_version": "v1.0.0",
                "components": [],
                "docker": False,
                "codeql": False,
                "release_please": False,
                "renovate": False,
            }
        )
    )
    no_codeql_dest = tmp_path / "no-codeql"
    run(
        [
            copier,
            "copy",
            "--trust",
            "--defaults",
            "--vcs-ref",
            "HEAD",
            "--data-file",
            str(no_codeql_data),
            str(template_source),
            str(no_codeql_dest),
        ]
    )
    check(
        not (no_codeql_dest / ".github/workflows/codeql.yml").exists(),
        "codeql=false generated .github/workflows/codeql.yml",
    )
    for workflow_name in ("validate.yml", "gitleaks.yml"):
        check(
            (no_codeql_dest / f".github/workflows/{workflow_name}").is_file(),
            f"codeql=false dropped the {workflow_name} baseline workflow",
        )
    check(
        'if answers.get("codeql") and not codeql_path.is_file():'
        in (no_codeql_dest / ".github/workflows/validate.yml").read_text(),
        "generated contract does not gate the CodeQL workflow on its answer",
    )

    no_gitleaks_data = tmp_path / "no-gitleaks.yml"
    no_gitleaks_data.write_text(
        yaml.safe_dump({"components": [], "codeql": True, "gitleaks": False, "release_please": False, "renovate": False})
    )
    no_gitleaks_dest = tmp_path / "no-gitleaks"
    run([
        copier, "copy", "--trust", "--defaults", "--vcs-ref", "HEAD",
        "--data-file", str(no_gitleaks_data), str(template_source), str(no_gitleaks_dest),
    ])
    check(
        not (no_gitleaks_dest / ".github/workflows/gitleaks.yml").exists(),
        "gitleaks=false generated .github/workflows/gitleaks.yml",
    )
    check(
        (no_gitleaks_dest / ".github/workflows/codeql.yml").exists(),
        "gitleaks=false scenario unexpectedly changed CodeQL output",
    )

    custom_all_data = tmp_path / "custom-renovate-all.yml"
    custom_all_data.write_text(
        yaml.safe_dump(
            {
                "renovate_config_repository": "acme/shared-renovate",
                "renovate_presets": list(RENOVATE_PRESET_PATHS),
            }
        )
    )
    custom_dest = tmp_path / "custom-renovate-all"
    run(
        [
            copier,
            "copy",
            "--trust",
            "--defaults",
            "--vcs-ref",
            "HEAD",
            "--data-file",
            str(custom_all_data),
            str(template_source),
            str(custom_dest),
        ]
    )
    assert_generated_renovate_config(
        custom_dest / ".github/renovate.json",
        renovate_extends("acme/shared-renovate", list(RENOVATE_PRESET_PATHS)),
        "custom-renovate-all",
    )

    dotted_repo_data = tmp_path / "custom-renovate-dotted-repo.yml"
    dotted_repo_data.write_text(
        yaml.safe_dump(
            {
                "renovate_config_repository": "octocat/octocat.github.io",
                "renovate_presets": ["default"],
            }
        )
    )
    dotted_repo_dest = tmp_path / "custom-renovate-dotted-repo"
    run(
        [
            copier,
            "copy",
            "--trust",
            "--defaults",
            "--vcs-ref",
            "HEAD",
            "--data-file",
            str(dotted_repo_data),
            str(template_source),
            str(dotted_repo_dest),
        ]
    )
    assert_generated_renovate_config(
        dotted_repo_dest / ".github/renovate.json",
        renovate_extends("octocat/octocat.github.io", ["default"]),
        "custom-renovate-dotted-repo",
    )

    custom_selected_data = tmp_path / "custom-renovate-selected.yml"
    custom_selected_data.write_text(
        yaml.safe_dump(
            {
                "renovate_config_repository": "acme/shared-renovate",
                "renovate_presets": ["default", "javascript", "github-actions"],
            }
        )
    )
    custom_selected_dest = tmp_path / "custom-renovate-selected"
    run(
        [
            copier,
            "copy",
            "--trust",
            "--defaults",
            "--vcs-ref",
            "HEAD",
            "--data-file",
            str(custom_selected_data),
            str(template_source),
            str(custom_selected_dest),
        ]
    )
    assert_generated_renovate_config(
        custom_selected_dest / ".github/renovate.json",
        renovate_extends("acme/shared-renovate", ["default", "javascript", "github-actions"]),
        "custom-renovate-selected",
    )

    for invalid_value in (
        "https://github.com/acme/shared-renovate",
        "github>acme/shared-renovate",
        "ac.me/shared-renovate",
        "acme/shared-renovate//presets/base",
        "acme/shared-renovate#main",
        "acme /shared-renovate",
        "acme/../shared-renovate",
        "{{ acme }}/shared-renovate",
    ):
        invalid_label = re.sub(r"[^A-Za-z0-9]+", "-", invalid_value).strip("-")
        bad_dest = tmp_path / ("invalid-renovate-" + invalid_label)
        result = subprocess.run(
            [
                copier,
                "copy",
                "--trust",
                "--defaults",
                "--vcs-ref",
                "HEAD",
                "--data",
                f"renovate_config_repository={invalid_value}",
                str(template_source),
                str(bad_dest),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        check(
            result.returncode != 0,
            f"invalid renovate_config_repository accepted: {invalid_value}",
        )

    invalid_preset_values: tuple[tuple[str, object], ...] = (
        ("unknown", ["default", "ruby"]),
        ("duplicate", ["default", "python", "python"]),
        ("empty", []),
        ("scalar-string", "default"),
        ("scalar-number", 1),
        ("map", {"default": True}),
    )
    for invalid_label, invalid_value in invalid_preset_values:
        invalid_data = tmp_path / f"invalid-renovate-presets-{invalid_label}.yml"
        invalid_data.write_text(yaml.safe_dump({"renovate_presets": invalid_value}))
        bad_dest = tmp_path / f"invalid-renovate-presets-{invalid_label}"
        result = subprocess.run(
            [
                copier,
                "copy",
                "--trust",
                "--defaults",
                "--vcs-ref",
                "HEAD",
                "--data-file",
                str(invalid_data),
                str(template_source),
                str(bad_dest),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        check(
            result.returncode != 0,
            f"invalid renovate_presets accepted: {invalid_label}={invalid_value!r}",
        )

    # A repository whose source is not described by components must still be able to
    # declare what CodeQL scans; deriving the list from components alone silently
    # dropped Java and Python coverage across the fleet.
    explicit_languages_data = tmp_path / "codeql-languages.yml"
    explicit_languages_data.write_text(
        yaml.safe_dump({"components": [], "codeql": True, "codeql_languages": ["actions", "java-kotlin"]})
    )
    explicit_languages_dest = tmp_path / "codeql-languages"
    run(
        [
            copier, "copy", "--trust", "--defaults", "--vcs-ref", "HEAD",
            "--data-file", str(explicit_languages_data),
            str(template_source), str(explicit_languages_dest),
        ]
    )
    explicit_codeql = (explicit_languages_dest / ".github/workflows/codeql.yml").read_text()
    check(
        'language: ["actions", "java-kotlin"]' in explicit_codeql,
        "codeql_languages must override the language matrix derived from components",
    )
    run([actionlint, str(explicit_languages_dest / ".github/workflows/codeql.yml")])

    derived_languages_data = tmp_path / "codeql-derived.yml"
    derived_languages_data.write_text(
        yaml.safe_dump({"components": [{"type": "java", "path": ".", "id": "app-java", "name": "Application Java"}], "codeql": True})
    )
    derived_languages_dest = tmp_path / "codeql-derived"
    run(
        [
            copier, "copy", "--trust", "--defaults", "--vcs-ref", "HEAD",
            "--data-file", str(derived_languages_data),
            str(template_source), str(derived_languages_dest),
        ]
    )
    check(
        'language: ["actions", "java-kotlin"]'
        in (derived_languages_dest / ".github/workflows/codeql.yml").read_text(),
        "an empty codeql_languages must still derive the matrix from components",
    )

    # The reusable release workflow has always supported manifest mode; the template
    # hardcoded single, which a repository releasing independently versioned components
    # cannot express and must therefore diverge from the template forever.
    manifest_release_data = tmp_path / "release-manifest.yml"
    manifest_release_data.write_text(
        yaml.safe_dump({"release_please": True, "release_mode": "manifest"})
    )
    manifest_release_dest = tmp_path / "release-manifest"
    run(
        [
            copier, "copy", "--trust", "--defaults", "--vcs-ref", "HEAD",
            "--data-file", str(manifest_release_data),
            str(template_source), str(manifest_release_dest),
        ]
    )
    manifest_release = (manifest_release_dest / ".github/workflows/release.yml").read_text()
    for expected in (
        "mode: manifest",
        "config-file: .github/release-please/config.json",
        "manifest-file: .github/release-please/manifest.json",
    ):
        check(
            expected in manifest_release,
            f"manifest release mode must emit {expected!r}",
        )
    run([actionlint, str(manifest_release_dest / ".github/workflows/release.yml")])

    single_release_data = tmp_path / "release-single.yml"
    single_release_data.write_text(yaml.safe_dump({"release_please": True}))
    single_release_dest = tmp_path / "release-single"
    run(
        [
            copier, "copy", "--trust", "--defaults", "--vcs-ref", "HEAD",
            "--data-file", str(single_release_data),
            str(template_source), str(single_release_dest),
        ]
    )
    single_release = (single_release_dest / ".github/workflows/release.yml").read_text()
    check("mode: single" in single_release, "the default release mode must stay single")
    check(
        "config-file: .github/release-please/config.json" in single_release
        and "manifest-file: .github/release-please/manifest.json" in single_release,
        "single release mode must use the config-backed root manifest",
    )
    check(
        "config-backed-single: true" in single_release,
        "single release mode must opt into config-backed workflow inputs",
    )

    invalid_release_data = tmp_path / "invalid-release-mode.yml"
    invalid_release_data.write_text(
        yaml.safe_dump({"release_please": True, "release_mode": "components"})
    )
    invalid_release_result = subprocess.run(
        [
            copier, "copy", "--trust", "--defaults", "--vcs-ref", "HEAD",
            "--data-file", str(invalid_release_data),
            str(template_source), str(tmp_path / "invalid-release-mode"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    check(
        invalid_release_result.returncode != 0,
        "invalid release_mode accepted",
    )

    invalid_language_values: tuple[tuple[str, object], ...] = (
        ("unsupported", ["actions", "cobol"]),
        ("duplicate", ["actions", "actions"]),
        ("scalar-string", "actions"),
        ("map", {"actions": True}),
        ("non-string-entry", ["actions", 1]),
    )
    for invalid_label, invalid_value in invalid_language_values:
        invalid_data = tmp_path / f"invalid-codeql-languages-{invalid_label}.yml"
        invalid_data.write_text(
            yaml.safe_dump({"codeql": True, "codeql_languages": invalid_value})
        )
        bad_dest = tmp_path / f"invalid-codeql-languages-{invalid_label}"
        result = subprocess.run(
            [
                copier, "copy", "--trust", "--defaults", "--vcs-ref", "HEAD",
                "--data-file", str(invalid_data),
                str(template_source), str(bad_dest),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        check(
            result.returncode != 0,
            f"invalid codeql_languages accepted: {invalid_label}={invalid_value!r}",
        )

    for invalid_label, invalid_path in (
        ("traversal", "../allure-results"),
        ("absolute", "/tmp/allure-results"),
        ("glob", "reports/*"),
        ("backslash", "reports\\allure-results"),
        ("empty-segment", "reports//allure-results"),
    ):
        invalid_data = tmp_path / f"invalid-allure-path-{invalid_label}.yml"
        invalid_data.write_text(
            yaml.safe_dump(
                {
                    "allure_report": True,
                    "components": [
                        {
                            "type": "python",
                            "path": ".",
                            "id": "app-python",
                            "name": "Application Python",
                            "allure_results_path": invalid_path,
                        }
                    ],
                }
            )
        )
        result = subprocess.run(
            [
                copier,
                "copy",
                "--trust",
                "--defaults",
                "--vcs-ref",
                "HEAD",
                "--data-file",
                str(invalid_data),
                str(template_source),
                str(tmp_path / f"invalid-allure-path-{invalid_label}"),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        check(result.returncode != 0, f"unsafe allure_results_path accepted: {invalid_path}")

    for invalid_label, invalid_url in (
        ("http", "http://owner.github.io/repository"),
        ("trailing-slash", "https://owner.github.io/repository/"),
        ("query", "https://owner.github.io/repository?ref=main"),
        ("template", "https://{{ owner }}.github.io/repository"),
    ):
        result = subprocess.run(
            [
                copier,
                "copy",
                "--trust",
                "--defaults",
                "--vcs-ref",
                "HEAD",
                "--data",
                "allure_report=true",
                "--data",
                "allure_publish_pages=true",
                "--data",
                f"allure_pages_url={invalid_url}",
                str(template_source),
                str(tmp_path / f"invalid-allure-url-{invalid_label}"),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        check(result.returncode != 0, f"unsafe allure_pages_url accepted: {invalid_url}")

    invalid_external_values: tuple[tuple[str, str, object], ...] = (
        ("workflow-path-traversal", "allure_external_workflow_path", "../test.yml"),
        ("artifact-prefix-glob", "allure_external_artifact_prefix", "allure-*"),
        ("minimum-zero", "allure_external_artifact_min_count", 0),
        ("minimum-over-limit", "allure_external_artifact_min_count", 51),
        ("maximum-over-limit", "allure_external_artifact_max_count", 51),
        ("maximum-below-minimum", "allure_external_artifact_max_count", 1),
        ("categories-traversal", "allure_categories_file", "../categories.json"),
    )
    for invalid_label, field, invalid_value in invalid_external_values:
        external_data: dict[str, object] = {
            "components": [],
            "allure_report": True,
            "allure_external_workflow_name": "Run tests",
            "allure_external_workflow_path": ".github/workflows/test.yml",
            "allure_external_artifact_prefix": "allure-results-",
            "allure_external_artifact_min_count": 2,
            "allure_external_artifact_max_count": 7,
        }
        external_data[field] = invalid_value
        invalid_data = tmp_path / f"invalid-allure-external-{invalid_label}.yml"
        invalid_data.write_text(yaml.safe_dump(external_data), encoding="utf-8")
        result = subprocess.run(
            [
                copier,
                "copy",
                "--trust",
                "--defaults",
                "--vcs-ref",
                "HEAD",
                "--data-file",
                str(invalid_data),
                str(template_source),
                str(tmp_path / f"invalid-allure-external-{invalid_label}"),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        check(result.returncode != 0, f"unsafe external Allure value accepted: {field}={invalid_value!r}")

# Consumers run formatters over generated files. A config the template emits in a
# shape no formatter agrees with fails their checks on every template update, so
# hold the generated Renovate config to Prettier's default profile.
if GENERATED_RENOVATE_CONFIGS:
    if shutil.which("npx") is None:
        print("npx unavailable: skipping Prettier check for generated Renovate configs")
    else:
        with tempfile.TemporaryDirectory(prefix="renovate-prettier-") as prettier_dir:
            samples = []
            for label, content in GENERATED_RENOVATE_CONFIGS:
                sample = Path(prettier_dir) / f"{label}.json"
                sample.write_text(content, encoding="utf-8")
                samples.append(str(sample))
            prettier = subprocess.run(
                ["npx", "--yes", "prettier@3.9.6", "--check", *samples],
                text=True,
                capture_output=True,
                check=False,
            )
            check(
                prettier.returncode == 0,
                "generated renovate.json is not Prettier-formatted: "
                + (prettier.stderr or prettier.stdout).strip(),
            )

# The runner discovers suites and marks their child environment so tests that
# invoke this validator do not recursively start the suite runner again.
run([sys.executable, "scripts/run_test_suites.py"])
run(["bash", "-n", "scripts/rollout_ci_kit.sh"])

if not ARGS.static:
    fixture_commands = (
        ("Python", validate_python_fixture),
        ("Node.js", lambda: run(["node", "scripts/validate_node_fixture.mjs"])),
        ("Java", lambda: run(["bash", "scripts/validate_java_fixture.sh"])),
    )
    for language, validate_fixture in fixture_commands:
        try:
            validate_fixture()
        except (OSError, subprocess.CalledProcessError) as exc:
            ERRORS.append(f"{language} fixture validation failed: {exc}")
if ERRORS:
    print("\n".join("ERROR: " + e for e in ERRORS), file=sys.stderr)
    raise SystemExit(1)
print("static validation: OK" if ARGS.static else "validation: OK")

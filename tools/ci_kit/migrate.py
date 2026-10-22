from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .drift import DIFF_LINES, RENDER_TIMEOUT, TRUSTED_SRC_RE, RenderError, _copier, _diff, _normalize, render_module
from .equivalence import VALIDATE_PATH, equivalence_findings
from .handoff import AREAS, area_for_module
from .legacy import ANSWERS, load_answers
from .legacy_jobs import LegacyJob, MappingError, check_contexts, component_job_ids, legacy_to_modules, parse_jobs
from .plan import Finding, build_plan, discover
from .update import _git, _rejects, commit_all, copier_update_command

MARKER = "<!-- ci-kit-migrate -->"
CHECKS_AFTER = {"core": ["ci / gate"]}
PAYLOAD_KEYS = ("module", "area", "ref", "checks_before", "checks_after", "commits", "findings", "modules")


@dataclass
class MigrateResult:
    module: str
    area: str
    ref: str
    checks_before: list[str] = field(default_factory=list)
    checks_after: list[str] = field(default_factory=list)
    commits: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)


def result_payload(result: MigrateResult) -> dict:
    return {
        "module": result.module,
        "area": result.area,
        "ref": result.ref,
        "checks_before": result.checks_before,
        "checks_after": result.checks_after,
        "commits": result.commits,
        "findings": [
            {"code": f.code, "module": f.module, "message": f.message, "path": f.path}
            for f in result.findings
        ],
        "modules": result.modules,
    }


def render_marker(result: MigrateResult) -> str:
    return f"{MARKER}\n```json\n{json.dumps(result_payload(result), indent=2)}\n```"


def parse_marker(body: str) -> dict:
    lines = body.splitlines()
    try:
        start = lines.index(MARKER)
    except ValueError:
        raise ValueError("migrate marker not found") from None
    if start + 1 >= len(lines) or lines[start + 1].strip() != "```json":
        raise ValueError("migrate marker is not followed by a json block")
    block: list[str] = []
    for line in lines[start + 2:]:
        if line.strip() == "```":
            break
        block.append(line)
    else:
        raise ValueError("migrate json block is not closed")
    try:
        data = json.loads("\n".join(block))
    except json.JSONDecodeError as error:
        raise ValueError(f"migrate json block is invalid: {error}") from error
    if not isinstance(data, dict):
        raise ValueError("migrate json block must be an object")
    return data


def legacy_check_names(validate_yml: Path) -> list[str]:
    return check_contexts(yaml.safe_load(Path(validate_yml).read_text()))


def copier_copy_command(module: str, to: str, src: str, data: dict[str, object] | None = None) -> list[str]:
    values = {
        key: value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        for key, value in (data or {}).items()
    }
    for item in (to, src, *values, *values.values()):
        if item.startswith("-"):
            raise ValueError(f"option-like ref, source or data: {item}")
    return [
        *_copier(), "copy", "--defaults", "--quiet",
        "-a", f".copier-answers.{module}.yml",
        "-d", f"module={module}", *(("-d", f"toolkit_version={to}") if module == "core" else ()),
        *[arg for key, value in values.items() for arg in ("-d", f"{key}={value}")],
        "--vcs-ref", to, "--", src, ".",
    ]


def _run(command: list[str], repo: Path) -> str | None:
    try:
        result = subprocess.run(command, cwd=repo, capture_output=True, text=True, timeout=RENDER_TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"copier timed out after {RENDER_TIMEOUT}s"
    except OSError as error:
        return f"cannot run {command[0]}: {error.strerror or error}"
    if result.returncode:
        lines = (result.stderr or result.stdout).strip().splitlines()
        return lines[-1] if lines else "no output"
    return None


def _without_components(text: str, toolkit_version: str) -> dict:
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("not a mapping")
    jobs = data.get("jobs")
    if isinstance(jobs, dict):
        skip = component_job_ids(data, toolkit_version)
        data = {**data, "jobs": {key: value for key, value in jobs.items() if str(key) not in skip}}
    return data


def _validate_difference(path: str, wanted: bytes, actual: bytes, toolkit_version: str) -> str | None:
    try:
        expected = _without_components(_normalize(wanted), toolkit_version)
        current = _without_components(_normalize(actual), toolkit_version)
    except (yaml.YAMLError, ValueError) as error:
        return f"{path} cannot be compared with the legacy render: {error}"
    if expected == current:
        return None
    dump = lambda data: json.dumps(data, indent=1, default=str)
    return f"{path} differs from the legacy render\n{_diff(path, dump(expected), dump(current))}"


def _toolkit_version(legacy: dict | None) -> str:
    legacy = legacy or {}
    return str(legacy.get("toolkit_version") or legacy.get("_commit") or "")


def _contract_findings(repo: Path, module: str, area_paths: tuple[str, ...], src_override: str | None) -> list[Finding]:
    answers_path = repo / ANSWERS
    with tempfile.TemporaryDirectory() as tmp:
        try:
            files = render_module(answers_path, Path(tmp) / "render", src_override)
        except RenderError as error:
            return [Finding("CIK-MIGRATE-003", module, str(error), ANSWERS)]
    toolkit_version = _toolkit_version(load_answers(repo)[0])
    findings = []
    for path in area_paths:
        target = repo / path
        if path not in files:
            findings.append(Finding("CIK-MIGRATE-003", module, f"{path} is not rendered by the legacy template", path))
        elif not target.is_file():
            findings.append(Finding("CIK-MIGRATE-003", module, f"{path} is missing from the repo", path))
        elif path == VALIDATE_PATH:
            message = _validate_difference(path, files[path], target.read_bytes(), toolkit_version)
            if message:
                findings.append(Finding("CIK-MIGRATE-003", module, message, path))
        else:
            actual, wanted = _normalize(target.read_bytes()), _normalize(files[path])
            if actual != wanted:
                findings.append(Finding("CIK-MIGRATE-003", module, f"{path} differs from the legacy render\n{_diff(path, wanted, actual)}", path))
    return findings


def _ignored(repo: Path) -> set[str]:
    # Per file, not --directory: a collapsed directory entry changes once git clean removes an empty sibling.
    # Read raw bytes: _git strips its text output, which would eat leading spaces of the first -z entry,
    # and text mode cannot decode every file name.
    result = subprocess.run(
        ["git", "ls-files", "-z", "--others", "--ignored", "--exclude-standard"],
        cwd=repo, capture_output=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"git ls-files failed: {os.fsdecode(result.stderr).strip()}")
    return {os.fsdecode(path) for path in result.stdout.split(b"\0") if path}


def _rollback(repo: Path, head: str, ignored: set[str]) -> None:
    """Restore head and remove what the migration created, keeping ignored files that existed before it."""
    _git(repo, "reset", "-q", "--hard", head, check=False)
    _git(repo, "clean", "-qfd", check=False)
    for path in sorted(_ignored(repo) - ignored):
        (repo / path).unlink(missing_ok=True)
    # Directories that held only the removed files are now empty and untracked.
    _git(repo, "clean", "-qfd", check=False)


def migrate(repo: Path, module: str, to: str, *, src_override: str | None = None) -> MigrateResult:
    repo = Path(repo).resolve()
    area_name = area_for_module(module)
    result = MigrateResult(module=module, area=area_name or "", ref=to, checks_after=CHECKS_AFTER.get(module, []))

    def fail(code: str, message: str, path: str | None = None) -> MigrateResult:
        result.findings = [Finding(code, module, message, path)]
        return result

    if area_name is None:
        return fail("CIK-MIGRATE-004", f"module {module} owns no hand-off area")
    area = AREAS[area_name]
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo, capture_output=True, text=True, check=False,
        )
    except OSError as error:
        return fail("CIK-MIGRATE-001", f"cannot run git: {error.strerror or error}")
    if status.returncode:
        return fail("CIK-MIGRATE-001", "not a git repository or git status failed")
    if status.stdout.strip():
        return fail("CIK-MIGRATE-001", "working tree is not clean")
    stale = _rejects(repo)
    if stale:
        return fail("CIK-MIGRATE-001", f"reject files from an earlier run: {', '.join(stale)}; remove them first")
    legacy, error = load_answers(repo)
    if legacy is None:
        return fail("CIK-MIGRATE-002", error or f"cannot read {ANSWERS}", ANSWERS)
    if str(legacy.get("_commit") or "") != to:
        return fail("CIK-MIGRATE-002", f"legacy _commit is {legacy.get('_commit')}, not {to}; run ci-kit update --module legacy --to {to} first", ANSWERS)
    handed = legacy.get("handed_off", [])
    if not isinstance(handed, list) or any(not isinstance(item, str) for item in handed):
        return fail("CIK-MIGRATE-004", f"handed_off is malformed: {handed!r}", ANSWERS)
    if area_name in handed or module in discover(repo)[0]:
        return fail("CIK-MIGRATE-004", f"module {module} is already installed or area {area_name} is already handed off", ANSWERS)
    src = str(legacy.get("_src_path") or "")
    if src_override is None and not TRUSTED_SRC_RE.fullmatch(src):
        return fail("CIK-MIGRATE-002", f"untrusted template source: {src}", ANSWERS)
    jobs: list[LegacyJob] = []
    checks: list[str] = []
    validate = repo / area.legacy_paths[0]
    try:
        data = yaml.safe_load(validate.read_text()) if validate.is_file() else None
    except yaml.YAMLError:
        data = None
    if isinstance(data, dict):
        try:
            jobs = parse_jobs(data, _toolkit_version(legacy))
            checks = check_contexts(data)
        except MappingError as error:
            return fail(
                "CIK-MIGRATE-005",
                f"{error}; it cannot be mapped to a module, move it to a project-owned workflow in a preparatory pull request",
                area.legacy_paths[0],
            )
    findings = _contract_findings(repo, module, area.legacy_paths, src_override)
    if findings:
        result.findings = findings
        return result

    declarations = legacy_to_modules(jobs)
    head = _git(repo, "rev-parse", "HEAD")
    ignored = _ignored(repo)
    commits: list[str] = []

    def abort(message: str) -> MigrateResult:
        _rollback(repo, head, ignored)
        return fail("CIK-UPDATE-001", f"{message}; rolled back to {head[:7]}")

    try:
        flow = "[" + ", ".join([*handed, area_name]) + "]"
        command = copier_update_command(
            ANSWERS, to, trust=True, data={"toolkit_version": to, "handed_off": flow},
        )
        problem = _run(command, repo)
        if problem:
            return abort(f"copier update failed: {problem}")
        for path in area.legacy_paths:
            (repo / f"{path}.rej").unlink(missing_ok=True)
        rejects = _rejects(repo)
        if rejects:
            return abort(f"conflicts in {', '.join(rejects)}")
        _git(repo, "rm", "-q", "-f", "--ignore-unmatch", "--", *area.legacy_paths)
        sha = commit_all(repo, f"chore(ci-kit): hand off {area_name} to {module}")
        if sha:
            commits.append(sha)
        for name, answers in declarations:
            problem = _run(copier_copy_command(name, to, src_override or src, answers), repo)
            if problem:
                return abort(f"copier copy of {name} failed: {problem}")
            sha = commit_all(repo, f"chore(ci-kit): add {name} module")
            if sha:
                commits.append(sha)
    except (ValueError, RuntimeError) as error:
        return abort(str(error))
    plan = build_plan(repo, src_override=src_override)
    mismatches = equivalence_findings(jobs, plan)
    if mismatches:
        _rollback(repo, head, ignored)
        result.findings = mismatches
        return result
    result.checks_before = checks
    result.modules = [name for name, _ in declarations]
    result.commits = commits
    result.findings = plan.findings
    return result

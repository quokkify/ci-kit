from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .drift import DIFF_LINES, RENDER_TIMEOUT, TRUSTED_SRC_RE, RenderError, _copier, _diff, _normalize, render_module
from .handoff import AREAS, area_for_module
from .legacy import ANSWERS, load_answers
from .plan import Finding, build_plan, discover
from .update import _git, _rejects, commit_all, copier_update_command

MARKER = "<!-- ci-kit-migrate -->"
CHECKS_AFTER = {"core": ["ci / gate"]}
PAYLOAD_KEYS = ("module", "area", "ref", "checks_before", "checks_after", "commits", "findings")


@dataclass
class MigrateResult:
    module: str
    area: str
    ref: str
    checks_before: list[str] = field(default_factory=list)
    checks_after: list[str] = field(default_factory=list)
    commits: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


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
    data = yaml.safe_load(Path(validate_yml).read_text())
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, dict):
        return []
    names = []
    for job_id, job in jobs.items():
        name = job.get("name") if isinstance(job, dict) else None
        names.append(str(name) if name else str(job_id))
    return names


def copier_copy_command(module: str, to: str, src: str) -> list[str]:
    if to.startswith("-") or src.startswith("-"):
        raise ValueError(f"option-like ref or source: {to if to.startswith('-') else src}")
    return [
        *_copier(), "copy", "--defaults", "--quiet",
        "-a", f".copier-answers.{module}.yml",
        "-d", f"module={module}", "-d", f"toolkit_version={to}",
        "--vcs-ref", to, "--", src, ".",
    ]


def _run(command: list[str], repo: Path) -> str | None:
    try:
        result = subprocess.run(command, cwd=repo, capture_output=True, text=True, timeout=RENDER_TIMEOUT)
    except subprocess.TimeoutExpired:
        return f"copier timed out after {RENDER_TIMEOUT}s"
    if result.returncode:
        lines = (result.stderr or result.stdout).strip().splitlines()
        return lines[-1] if lines else "no output"
    return None


def _contract_findings(repo: Path, module: str, area_paths: tuple[str, ...], src_override: str | None) -> list[Finding]:
    answers_path = repo / ANSWERS
    with tempfile.TemporaryDirectory() as tmp:
        try:
            files = render_module(answers_path, Path(tmp) / "render", src_override)
        except RenderError as error:
            return [Finding("CIK-MIGRATE-003", module, str(error), ANSWERS)]
    findings = []
    for path in area_paths:
        target = repo / path
        if path not in files:
            findings.append(Finding("CIK-MIGRATE-003", module, f"{path} is not rendered by the legacy template", path))
        elif not target.is_file():
            findings.append(Finding("CIK-MIGRATE-003", module, f"{path} is missing from the repo", path))
        else:
            actual, wanted = _normalize(target.read_bytes()), _normalize(files[path])
            if actual != wanted:
                findings.append(Finding("CIK-MIGRATE-003", module, f"{path} differs from the legacy render\n{_diff(path, wanted, actual)}", path))
    return findings


def _rollback(repo: Path, head: str) -> None:
    _git(repo, "reset", "-q", "--hard", head, check=False)
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
    if _git(repo, "status", "--porcelain", check=False):
        return fail("CIK-MIGRATE-001", "working tree is not clean")
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
    findings = _contract_findings(repo, module, area.legacy_paths, src_override)
    if findings:
        result.findings = findings
        return result

    validate = repo / area.legacy_paths[0]
    result.checks_before = legacy_check_names(validate) if validate.is_file() else []
    head = _git(repo, "rev-parse", "HEAD")
    commits: list[str] = []

    def abort(message: str) -> MigrateResult:
        _rollback(repo, head)
        return fail("CIK-UPDATE-001", message)

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
        problem = _run(copier_copy_command(module, to, src_override or src), repo)
        if problem:
            return abort(f"copier copy failed: {problem}")
        sha = commit_all(repo, f"chore(ci-kit): add {module} module")
        if sha:
            commits.append(sha)
    except (ValueError, RuntimeError) as error:
        return abort(str(error))
    result.commits = commits
    result.findings = build_plan(repo, src_override=src_override).findings
    return result

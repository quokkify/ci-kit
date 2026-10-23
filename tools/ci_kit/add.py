from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .drift import RENDER_TIMEOUT, TRUSTED_SRC_RE, _copier
from .modules import MODULES
from .plan import Finding, build_plan
from .update import LEGACY, _answers_files, _git, _read, commit_all


@dataclass
class AddResult:
    module: str
    ref: str
    installed: bool = False
    commits: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


class _AddFailed(Exception):
    pass


def _copy(src: str, ref: str, module: str, dest: Path) -> None:
    command = [
        *_copier(), "copy", "--trust", "--defaults", "--vcs-ref", ref,
        "-d", f"module={module}", "-d", f"toolkit_version={ref}",
        "--answers-file", f".copier-answers.{module}.yml", "--", src, str(dest),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=RENDER_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise _AddFailed(f"copier timed out after {RENDER_TIMEOUT}s") from None
    if result.returncode:
        lines = (result.stderr or result.stdout).strip().splitlines()
        raise _AddFailed(lines[0] if lines else "copier failed")


def _rendered_paths(src: str, ref: str, module: str) -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "render"
        _copy(src, ref, module, dest)
        answers = f".copier-answers.{module}.yml"
        return sorted(
            p.relative_to(dest).as_posix() for p in dest.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(dest).parts and p.relative_to(dest).as_posix() != answers
        )


def _others(repo: Path) -> set[str]:
    return set(_git(repo, "ls-files", "--others", "--directory", "-z").split("\0")) - {""}


def _rollback(repo: Path, head: str, before: set[str]) -> None:
    _git(repo, "reset", "-q", "--hard", head)
    for rel in sorted(_others(repo) - before):
        path = repo / rel.rstrip("/")
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)


def _source(repo: Path, module: str, to: str, src_override: str | None) -> tuple[str | None, Finding | None]:
    answers = _answers_files(repo)
    core_src: str | None = None
    for required in MODULES[module].requires:
        path = answers.get(required)
        if path is None:
            return None, Finding("CIK-ADD-003", module, f"required module {required} is not installed")
        data = _read(path)
        commit = str(data.get("_commit") or "")
        if commit != to:
            return None, Finding("CIK-ADD-003", module, f"required module {required} is at {commit or '(unknown)'}, not {to}", path.name)
        if required == "core":
            core_src = str(data.get("_src_path") or "")
    if src_override is not None:
        src = src_override
    elif core_src is not None and TRUSTED_SRC_RE.fullmatch(core_src):
        src = core_src
    else:
        return None, Finding("CIK-ADD-003", module, f"untrusted template source: {core_src or '(none)'}", ".copier-answers.core.yml")
    if src.startswith("-") or to.startswith("-"):
        return None, Finding("CIK-ADD-003", module, "option-like ref or source")
    return src, None


def add_module(repo: Path, module: str, to: str, *, src_override: str | None = None) -> AddResult:
    repo = Path(repo).resolve()
    result = AddResult(module, to)
    if module not in MODULES or module == LEGACY:
        result.findings.append(Finding("CIK-ADD-002", module, f"unknown module {module}"))
        return result
    answers_name = f".copier-answers.{module}.yml"
    if (repo / answers_name).exists():
        return result
    try:
        head = _git(repo, "rev-parse", "--verify", "HEAD")
        dirty = _git(repo, "status", "--porcelain")
    except RuntimeError as error:
        result.findings.append(Finding("CIK-ADD-001", module, f"not a git repository with commits: {error}"))
        return result
    if dirty:
        result.findings.append(Finding("CIK-ADD-001", module, "working tree is not clean"))
        return result
    src, finding = _source(repo, module, to, src_override)
    if finding is not None:
        result.findings.append(finding)
        return result
    try:
        rendered = _rendered_paths(src, to, module)
    except _AddFailed as error:
        result.findings.append(Finding("CIK-ADD-005", module, f"cannot render module at {to}: {error}"))
        return result
    existing = [path for path in rendered if (repo / path).exists() or (repo / path).is_symlink()]
    if existing:
        result.findings.append(Finding("CIK-ADD-004", module, f"{existing[0]} already exists", existing[0]))
        return result

    before = _others(repo)
    try:
        _copy(src, to, module, repo)
        commit = commit_all(repo, f"chore(ci-kit): add {module} module")
        findings = build_plan(repo, src_override=src_override).findings
        if findings:
            first = findings[0]
            raise _AddFailed(f"plan check failed after install: {first.code} {first.module}: {first.message.splitlines()[0]}")
    except (_AddFailed, RuntimeError) as error:
        _rollback(repo, head, before)
        result.findings.append(Finding("CIK-ADD-005", module, str(error), answers_name))
        return result
    result.installed = True
    result.commits = [commit] if commit else []
    return result

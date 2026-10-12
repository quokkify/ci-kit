from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from .drift import RENDER_TIMEOUT, TRUSTED_SRC_RE, _copier
from .plan import Finding, build_plan, discover

LEGACY = "legacy"
LEGACY_ANSWERS = ".copier-answers.yml"
LANGUAGE_MODULES = ("java", "node", "python", "go", "rust", "dotnet", "kotlin")
FALLBACK_IDENTITY = ["-c", "user.name=ci-kit", "-c", "user.email=ci-kit@users.noreply.github.com"]


@dataclass(frozen=True)
class ModuleUpdate:
    module: str
    from_ref: str
    to_ref: str
    commit: str | None


def update_order(answers: dict[str, Path]) -> list[str]:
    names = sorted(answers)
    rest = [name for name in names if name not in (LEGACY, "core")]
    languages = [name for name in rest if name in LANGUAGE_MODULES]
    others = [name for name in rest if name not in LANGUAGE_MODULES]
    head = [name for name in (LEGACY, "core") if name in answers]
    return head + languages + others


def copier_update_command(answers_file: str, ref: str, *, trust: bool, data: dict[str, str]) -> list[str]:
    if ref.startswith("-"):
        raise ValueError(f"option-like ref: {ref}")
    command = [
        *_copier(), "update", "--defaults", "--conflict=rej", "--skip-tasks",
        "-a", answers_file, "--vcs-ref", ref,
    ]
    if trust:
        command.append("--trust")
    for key, value in data.items():
        command += ["-d", f"{key}={value}"]
    return [*command, "--", "."]


def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    if check and result.returncode:
        lines = (result.stderr or result.stdout).strip().splitlines()
        raise RuntimeError(lines[0] if lines else f"git {args[0]} failed")
    return result.stdout.strip()


def _answers_files(repo: Path) -> dict[str, Path]:
    answers = dict(discover(repo)[0])
    legacy = repo / LEGACY_ANSWERS
    if legacy.is_file():
        answers[LEGACY] = legacy
    return answers


def _read(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _rejects(repo: Path) -> list[str]:
    return sorted(
        p.relative_to(repo).as_posix() for p in repo.rglob("*.rej")
        if ".git" not in p.relative_to(repo).parts
    )


def _commit(repo: Path, module: str, to: str) -> str | None:
    _git(repo, "add", "-A")
    if not _git(repo, "status", "--porcelain"):
        return None
    configured = bool(_git(repo, "config", "user.email", check=False))
    identity = [] if configured else FALLBACK_IDENTITY
    _git(repo, *identity, "commit", "-q", "-m", f"chore(ci-kit): update {module} to {to}")
    return _git(repo, "rev-parse", "HEAD")


def run_update(
    repo: Path, to: str, *, modules: list[str] | None = None,
    skip_legacy: bool = False, src_override: str | None = None,
) -> tuple[list[ModuleUpdate], list[Finding]]:
    repo = Path(repo).resolve()
    answers = _answers_files(repo)
    if skip_legacy:
        answers.pop(LEGACY, None)
    if modules is not None:
        answers = {name: path for name, path in answers.items() if name in modules}
    order = update_order(answers)
    updates: list[ModuleUpdate] = []
    for module in order:
        path = answers[module]
        current = _read(path)
        src = str(current.get("_src_path") or "")
        if src_override is None and not TRUSTED_SRC_RE.fullmatch(src):
            return updates, [Finding("CIK-UPDATE-001", module, f"untrusted template source: {src}", path.name)]
        data = {"toolkit_version": to} if module == "core" else {}
        try:
            command = copier_update_command(path.name, to, trust=module == LEGACY, data=data)
            result = subprocess.run(command, cwd=repo, capture_output=True, text=True, timeout=RENDER_TIMEOUT)
        except ValueError as error:
            return updates, [Finding("CIK-UPDATE-001", module, str(error), path.name)]
        except subprocess.TimeoutExpired:
            return updates, [Finding("CIK-UPDATE-001", module, f"copier timed out after {RENDER_TIMEOUT}s", path.name)]
        if result.returncode:
            lines = (result.stderr or result.stdout).strip().splitlines()
            return updates, [Finding("CIK-UPDATE-001", module, f"copier update failed: {lines[-1] if lines else 'no output'}", path.name)]
        rejects = _rejects(repo)
        if rejects:
            return updates, [Finding("CIK-UPDATE-001", module, f"conflicts in {', '.join(rejects)}", rejects[0][: -len('.rej')])]
        try:
            commit = _commit(repo, module, to)
        except RuntimeError as error:
            return updates, [Finding("CIK-UPDATE-001", module, f"cannot commit: {error}", path.name)]
        updates.append(ModuleUpdate(module, str(current.get("_commit") or ""), to, commit))
    return updates, build_plan(repo, src_override=src_override).findings

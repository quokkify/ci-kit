from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import yaml

from .drift import RENDER_TIMEOUT, TRUSTED_SRC_RE, _copier
from .handoff import AREAS, HANDOFF_GATED
from .modules import MODULES
from .plan import Finding, build_plan, discover

LEGACY = "legacy"
LEGACY_ANSWERS = ".copier-answers.yml"
FALLBACK_IDENTITY = ["-c", "user.name=ci-kit", "-c", "user.email=ci-kit@users.noreply.github.com"]


@dataclass(frozen=True)
class ModuleUpdate:
    module: str
    from_ref: str
    to_ref: str
    commit: str | None


# Feature modules, then languages. labels joins after renovate once it exists.
MODULE_ORDER = ("security", "release", "allure", "renovate", "labels", "java", "python", "node", "docker")


def update_order(modules: Iterable[str]) -> list[str]:
    names = set(modules)
    fixed = [LEGACY, "core", *(name for name in MODULE_ORDER if name in MODULES)]
    return [name for name in fixed if name in names] + sorted(names - set(fixed))


def copier_update_command(
    answers_file: str, ref: str, *, trust: bool, data: dict[str, str], exclude: tuple[str, ...] = (),
) -> list[str]:
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
    for pattern in exclude:
        command += ["-x", pattern]
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


def _legacy_owned(repo: Path, module: str) -> tuple[str, ...]:
    """Paths of `module`'s gated areas that legacy still renders.

    The template already skips them in the destination, but Copier also renders the new
    template into an empty scratch directory without the legacy answers; excluding them on
    the command line keeps that render from turning legacy files into conflicts.
    """
    path = repo / LEGACY_ANSWERS
    if not path.is_file():
        return ()
    handed = _read(path).get("handed_off")
    handed = handed if isinstance(handed, list) else []
    return tuple(
        f"/{rel}" for name, area in AREAS.items()
        if area.module == module and name in HANDOFF_GATED and name not in handed for rel in area.legacy_paths
    )


def _rejects(repo: Path) -> list[str]:
    return sorted(
        p.relative_to(repo).as_posix() for p in repo.rglob("*.rej")
        if ".git" not in p.relative_to(repo).parts
    )


def _commit(repo: Path, module: str, to: str) -> str | None:
    return commit_all(repo, f"chore(ci-kit): update {module} to {to}")


def commit_all(repo: Path, message: str) -> str | None:
    _git(repo, "add", "-A")
    if not _git(repo, "status", "--porcelain"):
        return None
    configured = bool(_git(repo, "config", "user.email", check=False))
    identity = [] if configured else FALLBACK_IDENTITY
    _git(repo, *identity, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def run_update(
    repo: Path, to: str, *, modules: list[str] | None = None,
    skip_legacy: bool = False, src_override: str | None = None,
) -> tuple[list[ModuleUpdate], list[Finding]]:
    repo = Path(repo).resolve()
    answers = _answers_files(repo)
    if skip_legacy:
        answers.pop(LEGACY, None)
    for name in modules or []:
        if name not in answers:
            return [], [Finding("CIK-UPDATE-001", name, f"no answers file for module {name}")]
    if modules is not None:
        answers = {name: path for name, path in answers.items() if name in modules}
    order = update_order(answers)
    if order and _git(repo, "status", "--porcelain", check=False):
        return [], [Finding("CIK-UPDATE-001", order[0], "working tree is not clean")]
    updates: list[ModuleUpdate] = []
    for module in order:
        path = answers[module]
        current = _read(path)
        src = str(current.get("_src_path") or "")
        if src_override is None and not TRUSTED_SRC_RE.fullmatch(src):
            return updates, [Finding("CIK-UPDATE-001", module, f"untrusted template source: {src}", path.name)]
        data = {"toolkit_version": to}
        try:
            command = copier_update_command(path.name, to, trust=module == LEGACY, data=data, exclude=_legacy_owned(repo, module))
            # Resolved: Copier renders the old and new copies in $TMPDIR and, without --trust, rejects reading the
            # legacy answers through a symlinked temporary directory (macOS /var) as a path outside the destination.
            env = {**os.environ, "TMPDIR": str(Path(tempfile.gettempdir()).resolve())}
            result = subprocess.run(command, cwd=repo, capture_output=True, text=True, timeout=RENDER_TIMEOUT, env=env)
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

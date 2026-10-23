"""Temporary (v3.10.x) migration that hands every remaining legacy area to modules."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .drift import RENDER_TIMEOUT, STARTER_PATHS, TRUSTED_SRC_RE, RenderError, _copier, _diff, _normalize, render_module
from .handoff import AREAS
from .legacy import ANSWERS, load_answers
from .legacy_answers import module_answers
from .migration_marker import CHECKS_AFTER, MARKER
from .plan import ANSWERS_RE, Finding, build_plan, discover
from .update import _git, _read, _rejects, commit_all, copier_update_command, update_order

MODULE = "legacy-exit"
MIGRATED = tuple(name for name in AREAS if name != "validate")
CODEQL_PATH = ".github/workflows/codeql.yml"
# core's own text replaces the legacy one (docs D6; copier-update.yml updates every answers file);
# build_plan's drift check pins both to the core render.
CORE_REWRITTEN = (".github/workflows/copier-update.yml", "docs/ci-kit.md")
CODEQL_WORKFLOW = "quokkify/ci-kit/.github/workflows/codeql-analysis.yml@"
# The analyze category of legacy codeql.yml and the reusable codeql-analysis.yml;
# a different category would start new code scanning analyses instead of continuing the old ones.
CODEQL_CATEGORY = "/language:${{ matrix.language }}"
SECURITY_CHECKS = (("codeql", "CodeQL"), ("gitleaks", "gitleaks"))


@dataclass
class MigrateLegacyResult:
    ref: str
    modules: list[str] = field(default_factory=list)
    commits: list[str] = field(default_factory=list)
    checks_before: list[str] = field(default_factory=list)
    checks_after: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)


def result_payload(result: MigrateLegacyResult) -> dict:
    return {
        "module": MODULE,
        "area": ",".join(MIGRATED),
        "ref": result.ref,
        "checks_before": result.checks_before,
        "checks_after": result.checks_after,
        "commits": result.commits,
        "findings": [{"code": f.code, "module": f.module, "message": f.message, "path": f.path} for f in result.findings],
        "modules": result.modules,
    }


def render_marker(result: MigrateLegacyResult) -> str:
    return f"{MARKER}\n```json\n{json.dumps(result_payload(result), indent=2)}\n```"


def _security_checks(answers: dict) -> list[str]:
    return [*CHECKS_AFTER["core"], *(context for key, context in SECURITY_CHECKS if answers.get(key) is True)]


def checks_before(legacy: dict) -> list[str]:
    return _security_checks(legacy)


def checks_after(repo: Path) -> list[str]:
    path = Path(repo) / ".copier-answers.security.yml"
    return _security_checks(_read(path) if path.is_file() else {})


def _status(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"], cwd=repo, capture_output=True, text=True, check=False,
        )
    except OSError as error:
        return f"cannot run git: {error.strerror or error}"
    if result.returncode:
        return "not a git repository or git status failed"
    return "working tree is not clean" if result.stdout.strip() else None


def _ignored(repo: Path) -> set[str]:
    # Per file, not --directory: a collapsed directory entry changes once git clean removes an empty sibling.
    # Raw bytes: _git strips its output, and text mode cannot decode every file name.
    result = subprocess.run(
        ["git", "ls-files", "-z", "--others", "--ignored", "--exclude-standard"], cwd=repo, capture_output=True, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"git ls-files failed: {os.fsdecode(result.stderr).strip()}")
    return {os.fsdecode(path) for path in result.stdout.split(b"\0") if path}


def _porcelain(repo: Path) -> str:
    return _git(repo, "status", "--porcelain", "--untracked-files=all", "--ignored")


def _rollback(repo: Path, head: str, ignored: set[str], porcelain: str) -> str | None:
    """Restore head and remove what the migration created, keeping ignored files that existed before it.

    Returns what could not be restored, or None once HEAD and the tree (ignored files included) match the snapshot.
    """
    _git(repo, "reset", "-q", "--hard", head, check=False)
    _git(repo, "clean", "-qfd", check=False)
    for path in sorted(_ignored(repo) - ignored):
        (repo / path).unlink(missing_ok=True)
    # Directories that held only the removed files are now empty and untracked.
    _git(repo, "clean", "-qfd", check=False)
    problems = []
    if _git(repo, "rev-parse", "HEAD", check=False) != head:
        problems.append(f"HEAD is not {head[:7]}")
    if _porcelain(repo) != porcelain:
        problems.append("the working tree differs from before the migration")
    return "; ".join(problems) or None


def _tracked(repo: Path) -> dict[str, bytes]:
    result = subprocess.run(["git", "ls-files", "-z"], cwd=repo, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(f"git ls-files failed: {os.fsdecode(result.stderr).strip()}")
    names = [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]
    return {name: (repo / name).read_bytes() for name in names if (repo / name).is_file()}


def _starters(repo: Path) -> dict[str, bytes | None]:
    return {path: (repo / path).read_bytes() if (repo / path).is_file() else None for path in sorted(STARTER_PATHS)}


def _restore(repo: Path, starters: dict[str, bytes | None]) -> None:
    """Starter files belong to the project: every Copier run of the migration must leave them as they were."""
    for path, content in starters.items():
        target = repo / path
        if content is None:
            target.unlink(missing_ok=True)
        elif not target.is_file() or target.read_bytes() != content:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)


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


def _copy(repo: Path, module: str, to: str, src: str, data: dict) -> str | None:
    """Render `module` at `to` over the repository; existing files are overwritten, _skip_if_exists starters are not."""
    with tempfile.TemporaryDirectory() as tmp:
        data_file = Path(tmp) / "data.yml"
        data_file.write_text(yaml.safe_dump({**data, "module": module, "toolkit_version": to}))
        return _run([
            *_copier(), "copy", "--defaults", "--quiet", "--trust", "--overwrite", "--skip-tasks",
            "--vcs-ref", to, "--data-file", str(data_file), "-a", f".copier-answers.{module}.yml", "--", src, ".",
        ], repo)


def _pin_source(repo: Path, module: str, src: str) -> None:
    """The override only selects where to render from: the answers keep the source the legacy answers name."""
    path = repo / f".copier-answers.{module}.yml"
    pinned = yaml.safe_dump({"_src_path": src}, default_flow_style=False, width=float("inf")).rstrip("\n")
    path.write_text(re.sub(r"^_src_path:.*$", lambda _: pinned, path.read_text(), count=1, flags=re.MULTILINE))


def _render_legacy(repo: Path, src_override: str | None) -> dict[str, bytes]:
    with tempfile.TemporaryDirectory() as tmp:
        return render_module(repo / ANSWERS, Path(tmp) / "render", src_override)


def _codeql_before(data: object) -> tuple[set[str], str]:
    job = data["jobs"]["analyze"]
    category = next(s["with"]["category"] for s in job["steps"] if "codeql-action/analyze@" in s.get("uses", ""))
    return set(job["strategy"]["matrix"]["language"]), category


def _codeql_after(repo: Path, data: object, to: str) -> set[str]:
    from .cli import CODEQL_LANGUAGES

    job = data["jobs"]["codeql"]
    if job["uses"] != f"{CODEQL_WORKFLOW}{to}":
        raise ValueError(f"calls {job['uses']}, not {CODEQL_WORKFLOW}{to}")
    override = json.loads(job["with"]["languages-override"])
    if override:
        return set(override)
    discovered = set().union(*discover(repo))
    return {"actions", *(language for module, language in CODEQL_LANGUAGES.items() if module in discovered)}


def _codeql_finding(repo: Path, before: bytes | None, after: bytes | None, to: str) -> Finding | None:
    def fail(message: str) -> Finding:
        return Finding("CIK-MIGRATE-005", "security", message, CODEQL_PATH)

    if before is None or after is None:
        return None if before == after else fail(f"{CODEQL_PATH} is {'removed' if after is None else 'added'} by the migration")
    try:
        languages, category = _codeql_before(yaml.safe_load(before))
        planned = _codeql_after(repo, yaml.safe_load(after), to)
    except (KeyError, TypeError, StopIteration, ValueError, yaml.YAMLError) as error:
        return fail(f"{CODEQL_PATH} cannot be compared with the legacy workflow: {error}")
    if category != CODEQL_CATEGORY:
        return fail(f"legacy CodeQL category {category} differs from {CODEQL_CATEGORY}")
    if languages != planned:
        return fail(f"CodeQL languages change from {sorted(languages)} to {sorted(planned)}")
    return None


def equivalence_findings(
    repo: Path, before: dict[str, bytes], modules: list[str], to: str, checks: tuple[list[str], list[str]],
    src_override: str | None,
) -> list[Finding]:
    after = _tracked(repo)
    added = {f".ci/modules/{module}.yml" for module in modules}
    findings: list[Finding] = []
    for path in sorted(set(before) | set(after)):
        if path == ANSWERS or ANSWERS_RE.match(path) or (path in added and path not in before):
            continue
        if path in CORE_REWRITTEN:
            if path not in after:
                findings.append(Finding("CIK-MIGRATE-005", "core", f"{path} is missing after the migration", path))
            continue
        if path == CODEQL_PATH:
            found = _codeql_finding(repo, before.get(path), after.get(path), to)
            if found:
                findings.append(found)
            continue
        old, new = before.get(path), after.get(path)
        if old == new:
            continue
        if old is None or new is None:
            message = f"{path} is {'removed' if new is None else 'added'} by the migration"
        else:
            message = f"{path} differs from the legacy render\n{_diff(path, _normalize(old), _normalize(new))}"
        findings.append(Finding("CIK-MIGRATE-005", MODULE, message, path))
    try:
        leftover = sorted(_render_legacy(repo, src_override))
    except RenderError as error:
        leftover = [f"(render failed: {error})"]
    if leftover:
        findings.append(Finding("CIK-MIGRATE-005", "legacy", f"legacy still renders {', '.join(leftover)}", ANSWERS))
    findings.extend(
        Finding("CIK-MIGRATE-005", f.module, f"{f.code}: {f.message}", f.path)
        for f in build_plan(repo, src_override=src_override).findings
    )
    if checks[0] != checks[1]:
        findings.append(Finding(
            "CIK-MIGRATE-005", MODULE, f"required checks change from {checks[0]} to {checks[1]}", ".copier-answers.security.yml",
        ))
    return findings


def _preconditions(repo: Path, to: str, src_override: str | None) -> tuple[dict | None, list[Finding]]:
    def fail(code: str, message: str, path: str | None = ANSWERS) -> tuple[None, list[Finding]]:
        return None, [Finding(code, MODULE, message, path)]

    problem = _status(repo)
    if problem:
        return fail("CIK-MIGRATE-001", problem, None)
    stale = _rejects(repo)
    if stale:
        return fail("CIK-MIGRATE-001", f"reject files from an earlier run: {', '.join(stale)}; remove them first", None)
    if not (repo / ANSWERS).is_file():
        return fail("CIK-MIGRATE-004", f"{ANSWERS} is absent: every area is already owned by modules")
    legacy, error = load_answers(repo)
    if legacy is None:
        return fail("CIK-MIGRATE-002", error or f"cannot read {ANSWERS}")
    if to.startswith("-"):
        return fail("CIK-MIGRATE-002", f"option-like ref: {to}", None)
    if str(legacy.get("_commit") or "") != to:
        return fail("CIK-MIGRATE-002", f"legacy _commit is {legacy.get('_commit')}, not {to}; run ci-kit update --to {to} first")
    src = str(legacy.get("_src_path") or "")
    if src_override is None and not TRUSTED_SRC_RE.fullmatch(src):
        return fail("CIK-MIGRATE-002", f"untrusted template source: {src}")
    core = repo / ".copier-answers.core.yml"
    if str(_read(core).get("_commit") or "") != to:
        return fail("CIK-MIGRATE-002", f"core module is not installed at {to}; run ci-kit update --to {to} first", core.name)
    handed = legacy.get("handed_off", [])
    if not isinstance(handed, list) or any(not isinstance(item, str) for item in handed):
        return fail("CIK-MIGRATE-004", f"handed_off is malformed: {handed!r}")
    if "validate" not in handed:
        return fail("CIK-MIGRATE-002", "validate is not handed off to core; see docs/errors/CIK-LEGACY-006.md")
    present = set().union(*discover(repo))
    done = [name for name in MIGRATED if name in handed or (AREAS[name].module != "core" and AREAS[name].module in present)]
    if done:
        return fail("CIK-MIGRATE-004", f"area(s) {', '.join(done)} already handed off or their module already installed")
    return legacy, []


def _drift(repo: Path, rendered: dict[str, bytes]) -> list[Finding]:
    findings: list[Finding] = []
    for name in MIGRATED:
        for path in AREAS[name].legacy_paths:
            if path in STARTER_PATHS or path not in rendered:
                continue
            target = repo / path
            if not target.is_file():
                findings.append(Finding("CIK-MIGRATE-003", AREAS[name].module, f"{path} is missing from the repo", path))
                continue
            actual, wanted = _normalize(target.read_bytes()), _normalize(rendered[path])
            if actual != wanted:
                findings.append(Finding(
                    "CIK-MIGRATE-003", AREAS[name].module, f"{path} differs from the legacy render\n{_diff(path, wanted, actual)}", path,
                ))
    return findings


def migrate_legacy(repo: Path, to: str, *, src_override: str | None = None) -> MigrateLegacyResult:
    repo = Path(repo).resolve()
    result = MigrateLegacyResult(ref=to)
    legacy, result.findings = _preconditions(repo, to, src_override)
    if legacy is None:
        return result
    try:
        rendered = _render_legacy(repo, src_override)
    except RenderError as error:
        result.findings = [Finding("CIK-MIGRATE-003", "legacy", str(error), ANSWERS)]
        return result
    result.findings = _drift(repo, rendered)
    if result.findings:
        return result

    translated = module_answers(legacy)
    modules = update_order(translated)
    legacy_owned = [
        path for name in MIGRATED for path in AREAS[name].legacy_paths if path in rendered and path not in STARTER_PATHS
    ]
    src = src_override or str(legacy["_src_path"])
    before_checks = checks_before(legacy)
    head = _git(repo, "rev-parse", "HEAD")
    ignored = _ignored(repo)
    porcelain = _porcelain(repo)
    before = _tracked(repo)
    starters = _starters(repo)
    commits: list[str] = []

    def abort(findings: list[Finding]) -> MigrateLegacyResult:
        problem = _rollback(repo, head, ignored, porcelain)
        if problem:
            findings = [*findings, Finding("CIK-MIGRATE-005", MODULE, f"rollback incomplete: {problem}; inspect the repository", None)]
        result.findings = findings
        return result

    def failed(message: str) -> MigrateLegacyResult:
        problem = _rollback(repo, head, ignored, porcelain)
        outcome = f"rolled back to {head[:7]}" if problem is None else f"rollback incomplete: {problem}; inspect the repository"
        result.findings = [Finding("CIK-MIGRATE-005", MODULE, f"{message}; {outcome}", None)]
        return result

    def commit(message: str) -> None:
        rejects = _rejects(repo)
        if rejects:
            raise RuntimeError(f"conflicts in {', '.join(rejects)}")
        _restore(repo, starters)
        sha = commit_all(repo, message)
        if sha:
            commits.append(sha)

    def copy(module: str, data: dict) -> str | None:
        problem = _copy(repo, module, to, src, data)
        if not problem:
            _pin_source(repo, module, str(legacy["_src_path"]))
        return problem

    try:
        flow = "[" + ", ".join(AREAS) + "]"
        problem = _run(copier_update_command(ANSWERS, to, trust=True, data={"toolkit_version": to, "handed_off": flow}), repo)
        if problem:
            return failed(f"copier update of legacy failed: {problem}")
        for path in legacy_owned:
            (repo / f"{path}.rej").unlink(missing_ok=True)
        _git(repo, "rm", "-q", "-f", "--ignore-unmatch", "--", *legacy_owned)
        commit(f"chore(ci-kit): hand off {', '.join(MIGRATED)} to modules")
        # copy, not update: Copier would treat the base files legacy just dropped as deletions by the project.
        core = {key: value for key, value in _read(repo / ".copier-answers.core.yml").items() if not str(key).startswith("_")}
        problem = copy("core", core)
        if problem:
            return failed(f"copier copy of core failed: {problem}")
        commit("chore(ci-kit): render core base files")
        for module in modules:
            problem = copy(module, translated[module])
            if problem:
                return failed(f"copier copy of {module} failed: {problem}")
            commit(f"chore(ci-kit): add {module} module")
        after_checks = checks_after(repo)
        findings = equivalence_findings(repo, before, modules, to, (before_checks, after_checks), src_override)
    except (OSError, ValueError, RuntimeError) as error:
        return failed(str(error))
    except BaseException as error:
        # Includes KeyboardInterrupt: the repository must never keep a half-applied migration.
        problem = _rollback(repo, head, ignored, porcelain)
        if problem:
            error.add_note(f"rollback incomplete: {problem}; inspect the repository")
        raise
    if findings:
        return abort(findings)
    result.modules = modules
    result.commits = commits
    result.checks_before = before_checks
    result.checks_after = after_checks
    return result

from __future__ import annotations

import difflib
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from .plan import Finding

REF_RE = re.compile(r"(quokkify/ci-kit/[^@\s]+)@[\w.\-/]+")
TRUSTED_SRC_RE = re.compile(r"^(https://github\.com/|gh:|git@github\.com:)quokkify/ci-kit(\.git)?$")
RENDER_TIMEOUT = 300
DIFF_LINES = 40


class RenderError(Exception):
    pass


def _copier() -> list[str]:
    binary = shutil.which("copier")
    return [binary] if binary else [sys.executable, "-m", "copier"]


def render_module(answers_path: Path, dest: Path, src_override: str | None = None) -> dict[str, bytes]:
    answers_path = Path(answers_path)
    dest = Path(dest)
    try:
        answers = yaml.safe_load(answers_path.read_text())
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
        reason = str(getattr(error, "strerror", None) or error).splitlines()
        raise RenderError(f"cannot read answers file: {reason[0] if reason else type(error).__name__}") from error
    if not isinstance(answers, dict):
        raise RenderError("cannot read answers file: answers file must be a YAML mapping")
    commit = answers.get("_commit")
    src = src_override or answers.get("_src_path")
    if not commit or not src:
        raise RenderError(f"cannot render module at {commit}: answers lack _commit or _src_path")
    commit, src = str(commit), str(src)
    if src_override is None and not TRUSTED_SRC_RE.fullmatch(src):
        raise RenderError(f"untrusted template source: {src}")
    if commit.startswith("-") or src.startswith("-"):
        raise RenderError(f"cannot render module at {commit}: option-like commit or source")
    data = {key: value for key, value in answers.items() if not str(key).startswith("_")}
    dest.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        data_file = Path(tmp) / "data.yml"
        data_file.write_text(yaml.safe_dump(data))
        try:
            result = subprocess.run(
                [
                    *_copier(), "copy", "--defaults", "--quiet",
                    "--vcs-ref", commit, "--data-file", str(data_file),
                    "-a", answers_path.name, "--", src, str(dest),
                ],
                capture_output=True, text=True, timeout=RENDER_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            raise RenderError(f"cannot render module at {commit}: copier timed out after {RENDER_TIMEOUT}s") from None
    if result.returncode:
        lines = (result.stderr or result.stdout).strip().splitlines()
        raise RenderError(f"cannot render module at {commit}: {lines[0] if lines else 'copier failed'}")
    rendered: dict[str, bytes] = {}
    for path in sorted(dest.rglob("*")):
        rel = path.relative_to(dest)
        if path.is_file() and ".git" not in rel.parts and rel.as_posix() != answers_path.name:
            rendered[rel.as_posix()] = path.read_bytes()
    return rendered


def _normalize(data: bytes) -> str:
    return REF_RE.sub(r"\1@<ref>", data.decode("utf-8", errors="replace"))


def _diff(path: str, expected: str, actual: str) -> str:
    lines = list(difflib.unified_diff(
        expected.splitlines(), actual.splitlines(),
        fromfile=f"template/{path}", tofile=f"repo/{path}", lineterm="",
    ))
    return "\n".join(lines[:DIFF_LINES])


def drift_findings(repo: Path, answers: dict[str, Path], src_override: str | None = None, owned: set[str] | None = None) -> list[Finding]:
    repo = Path(repo)
    findings: list[Finding] = []
    renders: dict[str, dict[str, bytes]] = {}
    for module in sorted(answers):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                renders[module] = render_module(answers[module], Path(tmp) / "render", src_override)
            except RenderError as error:
                findings.append(Finding("CIK-CORE-010", module, str(error), answers[module].name))

    owners: dict[str, list[str]] = {}
    for module, files in renders.items():
        for path in files:
            owners.setdefault(path, []).append(module)
    if owned is not None:
        owned.update(owners)
    contested = {path for path, mods in owners.items() if len(mods) > 1}
    for path in sorted(contested):
        mods = owners[path]
        findings.append(Finding("CIK-CORE-011", ",".join(mods), f"{path} is rendered by modules {', '.join(mods)}", path))

    for module, files in renders.items():
        for path, expected in files.items():
            if path in contested:
                continue
            target = repo / path
            if not target.is_file():
                findings.append(Finding("CIK-CORE-010", module, f"template-owned file {path} is missing from the repo", path))
                continue
            actual = _normalize(target.read_bytes())
            wanted = _normalize(expected)
            if actual != wanted:
                findings.append(Finding("CIK-CORE-010", module, f"{path} differs from the template render\n{_diff(path, wanted, actual)}", path))
    return findings

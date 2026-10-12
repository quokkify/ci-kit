from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}
VOLATILE_ANSWER_KEYS = ("_commit", "_src_path")


def git(cwd: Path, *args: str) -> str:
    import os

    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        env={**os.environ, **GIT_ENV},
    )
    return result.stdout.strip()


def make_template_repo(tmp: Path) -> Path:
    repo = Path(tmp).resolve() / "ci-kit"
    shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(".git", ".omc", ".superpowers"))
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "snapshot")
    git(repo, "tag", "v99.0.0")
    return repo


def run_copier(repo: Path, dest: Path, *args: str) -> Path:
    dest = Path(dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    copier = shutil.which("copier")
    if copier is None:
        raise AssertionError("copier is required to render the template")
    subprocess.run(
        [copier, "copy", "--trust", "--defaults", "--vcs-ref", "HEAD", *args, str(repo), str(dest)],
        check=True, capture_output=True, text=True,
    )
    return dest


def render(repo: Path, scenario: Path | None = None, data: dict | None = None) -> Path:
    dest = Path(tempfile.mkdtemp(prefix="render-")).resolve()
    args: list[str] = []
    if scenario is not None:
        args += ["--data-file", str(scenario)]
    for key, value in (data or {}).items():
        args += ["--data", f"{key}={value}"]
    return run_copier(repo, dest, *args)


def render_tree(repo: Path, scenario: Path | None = None, data: dict | None = None) -> dict[str, bytes]:
    dest = render(repo, scenario, data)
    try:
        tree: dict[str, bytes] = {}
        for path in sorted(dest.rglob("*")):
            if path.is_file() and ".git" not in path.relative_to(dest).parts:
                tree[path.relative_to(dest).as_posix()] = path.read_bytes()
        return tree
    finally:
        shutil.rmtree(dest, ignore_errors=True)


def normalize_answers(tree: dict[str, bytes], expect_module: str | None = None) -> dict[str, bytes]:
    out = dict(tree)
    raw = out.get(".copier-answers.yml")
    if raw is not None:
        kept = []
        found_module = False
        for line in raw.decode().splitlines(keepends=True):
            if line.startswith(("_commit:", "_src_path:")):
                continue
            if line.startswith("module:"):
                found_module = True
                if expect_module is not None and line.strip() != f"module: {expect_module}":
                    raise AssertionError(f"unexpected answers line: {line!r}")
                continue
            kept.append(line)
        if expect_module is not None and not found_module:
            raise AssertionError("module answer missing from answers file")
        out[".copier-answers.yml"] = "".join(kept).encode()
    return out


_TEMPLATE_REPO: Path | None = None


def shared_template_repo() -> Path:
    global _TEMPLATE_REPO
    if _TEMPLATE_REPO is None:
        tmp = Path(tempfile.mkdtemp(prefix="ci-kit-template-")).resolve()
        _TEMPLATE_REPO = make_template_repo(tmp)
    return _TEMPLATE_REPO


def rendered_repo(*modules: str) -> Path:
    dest = Path(tempfile.mkdtemp(prefix="rendered-")).resolve()
    template = shared_template_repo()
    for module in modules:
        run_copier(template, dest, "-a", f".copier-answers.{module}.yml", "-d", f"module={module}")
    return dest

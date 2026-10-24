from __future__ import annotations

import atexit
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
_TEMP_DIRS: list[Path] = []


def mkdtemp(prefix: str) -> Path:
    path = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    _TEMP_DIRS.append(path)
    return path


@atexit.register
def _cleanup_temp_dirs() -> None:
    for path in _TEMP_DIRS:
        shutil.rmtree(path, ignore_errors=True)


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
    dest = mkdtemp("render-")
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


_TEMPLATE_REPO: Path | None = None


def shared_template_repo() -> Path:
    global _TEMPLATE_REPO
    if _TEMPLATE_REPO is None:
        tmp = mkdtemp("ci-kit-template-")
        _TEMPLATE_REPO = make_template_repo(tmp)
    return _TEMPLATE_REPO


def rendered_repo(*modules: str) -> Path:
    dest = mkdtemp("rendered-")
    template = shared_template_repo()
    for module in modules:
        run_copier(template, dest, "-a", f".copier-answers.{module}.yml", "-d", f"module={module}")
    return dest


def _commit_all(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


def modules_repo(declarations: dict[str, list[dict]]) -> Path:
    import yaml

    repo = mkdtemp("modules-repo-")
    (repo / ".ci/modules").mkdir(parents=True)
    for module in ("core", *declarations):
        (repo / f".copier-answers.{module}.yml").write_text(f"module: {module}\n")
        data = {"schema_version": 1}
        if module != "core":
            data["components"] = declarations[module]
        (repo / f".ci/modules/{module}.yml").write_text(yaml.safe_dump(data))
    return repo


def lifecycle_template_repo() -> Path:
    repo = make_template_repo(mkdtemp("ci-kit-lifecycle-"))
    decl = repo / "modules/java/template/.ci/modules/java.yml.jinja"
    decl.write_text(decl.read_text() + "\n# rendered by ci-kit v99.1.0\n")
    _commit_all(repo, "java: annotate declaration")
    git(repo, "tag", "v99.1.0")
    return repo


def overlap_template_repo() -> Path:
    repo = make_template_repo(mkdtemp("ci-kit-overlap-"))
    core = repo / "modules/core/template/.ci/modules/core.yml.jinja"
    (repo / "modules/java/template/.ci/modules/core.yml.jinja").write_text(core.read_text())
    _commit_all(repo, "java: also render core declaration")
    git(repo, "tag", "-f", "v99.0.0")
    return repo


def rendered_from(template: Path, *modules: str, ref: str = "HEAD") -> Path:
    dest = mkdtemp("rendered-")
    for module in modules:
        subprocess.run(
            [
                shutil.which("copier"), "copy", "--trust", "--defaults", "--vcs-ref", ref,
                "-a", f".copier-answers.{module}.yml", "-d", f"module={module}", str(template), str(dest),
            ],
            check=True, capture_output=True, text=True,
        )
    return dest

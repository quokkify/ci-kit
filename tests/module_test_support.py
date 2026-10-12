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
VOLATILE_ANSWER_KEYS = ("_commit", "_src_path")
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


def normalize_answers(tree: dict[str, bytes], expect_module: str | None = None) -> dict[str, bytes]:
    out = dict(tree)
    raw = out.get(".copier-answers.yml")
    if raw is not None:
        kept = []
        found_module = False
        for line in raw.decode().splitlines(keepends=True):
            if line.startswith(("_commit:", "_src_path:")):
                continue
            if line.rstrip("\n") == "handed_off: []":
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


def component_less_scenarios() -> list[str]:
    import yaml

    names = []
    for path in sorted((ROOT / "tests/scenarios").glob("*.yml")):
        data = yaml.safe_load(path.read_text())
        if isinstance(data, dict) and not data.get("components"):
            names.append(path.stem)
    return names


def legacy_scenario_repo(template: Path, scenario: str, ref: str = "v99.0.0") -> Path:
    import yaml

    data = yaml.safe_load((ROOT / "tests/scenarios" / f"{scenario}.yml").read_text())
    data["toolkit_version"] = ref
    data_file = mkdtemp("scenario-") / "data.yml"
    data_file.write_text(yaml.safe_dump(data))
    dest = mkdtemp("legacy-")
    subprocess.run(
        [shutil.which("copier"), "copy", "--trust", "--defaults", "--vcs-ref", ref,
         "--data-file", str(data_file), str(template), str(dest)],
        check=True, capture_output=True, text=True,
    )
    for key in ("allure_external_workflow_path", "allure_categories_file"):
        rel = data.get(key)
        if rel and not (dest / rel).exists():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            (dest / rel).write_text("[]\n" if rel.endswith(".json") else "name: Run tests\non: push\njobs: {}\n")
    git(dest, "init", "-q")
    _commit_all(dest, "initial")
    return dest


MODULE_UPDATE_OPENER = "if compgen -G '.copier-answers.*.yml'"


def strip_module_update_block(workflow: str) -> str:
    lines = workflow.splitlines(keepends=True)
    openers = [i for i, line in enumerate(lines) if line.lstrip().startswith(MODULE_UPDATE_OPENER)]
    assert len(openers) == 1, f"expected exactly one module-update block, found {len(openers)}"
    start = openers[0]
    indent = lines[start][: len(lines[start]) - len(lines[start].lstrip())]
    end = next(i for i in range(start + 1, len(lines)) if lines[i].rstrip("\n") == f"{indent}fi")
    if end + 1 < len(lines) and not lines[end + 1].strip():
        end += 1
    return "".join(lines[:start] + lines[end + 1 :])

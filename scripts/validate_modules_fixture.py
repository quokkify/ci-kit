#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "tests/scenarios/modules/core-java.yml"
LEGACY_SCENARIO = ROOT / "tests/scenarios/allure-external.yml"
TAG = "v99.0.0"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "ci-kit",
    "GIT_AUTHOR_EMAIL": "ci-kit@example.com",
    "GIT_COMMITTER_NAME": "ci-kit",
    "GIT_COMMITTER_EMAIL": "ci-kit@example.com",
}
EXCLUDED = (".git", ".venv", ".superpowers", ".omc", "__pycache__")


def run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, env={**os.environ, **GIT_ENV})


def require(args: list[str], cwd: Path | None = None) -> None:
    result = run(args, cwd)
    if result.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(args)}\n{result.stdout}{result.stderr}")


def make_template_repo(tmp: Path) -> Path:
    repo = tmp / "ci-kit"
    shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(*EXCLUDED))
    require(["git", "init", "-q"], repo)
    require(["git", "add", "-A"], repo)
    require(["git", "commit", "-q", "-m", "snapshot"], repo)
    require(["git", "tag", TAG], repo)
    return repo


def render(template: Path, dest: Path, module: str, answers: dict) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    args = [
        "copier", "copy", "--trust", "--defaults", "--vcs-ref", TAG,
        "-a", f".copier-answers.{module}.yml", "-d", f"module={module}",
    ]
    for key, value in answers.items():
        args += ["-d", f"{key}={json.dumps(value) if isinstance(value, (list, dict)) else value}"]
    require([*args, str(template), str(dest)])


def doctor(template: Path, repo: Path) -> subprocess.CompletedProcess[str]:
    return run([sys.executable, "-m", "ci_kit.cli", "doctor", "--repo", str(repo), "--template-src", str(template)])


def legacy_repo(template: Path, dest: Path) -> None:
    data = yaml.safe_load(LEGACY_SCENARIO.read_text())
    data["toolkit_version"] = TAG
    data_file = dest.parent / "legacy-data.yml"
    data_file.write_text(yaml.safe_dump(data))
    dest.mkdir(parents=True)
    require(["copier", "copy", "--trust", "--defaults", "--vcs-ref", TAG, "--data-file", str(data_file), str(template), str(dest)])
    for key in ("allure_external_workflow_path", "allure_categories_file"):
        rel = data.get(key)
        if rel and not (dest / rel).exists():
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            (dest / rel).write_text("[]\n" if rel.endswith(".json") else "name: Run tests\non: push\njobs: {}\n")
    require(["git", "init", "-q"], dest)
    require(["git", "add", "-A"], dest)
    require(["git", "commit", "-q", "-m", "initial"], dest)


def main() -> int:
    if not SCENARIO.is_file():
        print(f"FAIL scenario file missing: {SCENARIO.relative_to(ROOT)}")
        return 1
    scenario = yaml.safe_load(SCENARIO.read_text())
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"{'PASS' if ok else 'FAIL'} {name}")
        if not ok:
            failures.append(name)
            if detail:
                print(detail.rstrip())

    with tempfile.TemporaryDirectory(prefix="modules-fixture-") as raw:
        tmp = Path(raw).resolve()
        template = make_template_repo(tmp)

        def full_render(name: str) -> Path:
            dest = tmp / name
            render(template, dest, "core", scenario["core"])
            render(template, dest, "java", scenario["java"])
            return dest

        clean = full_render("clean")
        result = doctor(template, clean)
        check("clean core+java render passes doctor", result.returncode == 0, result.stdout + result.stderr)

        languages = tmp / "languages"
        render(template, languages, "core", scenario["core"])
        render(template, languages, "python", {"python_components": [{"id": "app-python", "name": "Application Python", "path": ".", "test_command": "pytest", "python_version": "3.12"}]})
        render(template, languages, "node", {"node_components": [{"id": "app-node", "name": "Application Node", "path": ".", "node_version": "22", "package_manager": "npm"}]})
        render(template, languages, "docker", {"docker_images": [{"id": "app-docker", "context": ".", "dockerfile": "Dockerfile"}]})
        result = doctor(template, languages)
        check("core+python+node+docker render passes doctor", result.returncode == 0, result.stdout + result.stderr)

        workflows = sorted((clean / ".github/workflows").glob("*.yml"))
        lint = run(["actionlint", *map(str, workflows)])
        check("rendered caller passes actionlint", bool(workflows) and lint.returncode == 0, lint.stdout + lint.stderr)

        edited = full_render("edited")
        declaration = edited / ".ci/modules/java.yml"
        declaration.write_text(declaration.read_text() + "\n# local edit\n")

        deleted = full_render("deleted")
        (deleted / ".ci/modules/java.yml").unlink()

        orphan = tmp / "orphan"
        render(template, orphan, "java", scenario["java"])

        for name, repo, code in (
            ("edited java declaration reports CIK-CORE-010", edited, "CIK-CORE-010"),
            ("deleted java declaration reports CIK-CORE-002", deleted, "CIK-CORE-002"),
            ("java without core reports CIK-CORE-003", orphan, "CIK-CORE-003"),
        ):
            result = doctor(template, repo)
            output = result.stdout + result.stderr
            check(name, result.returncode == 1 and code in output, f"exit {result.returncode}\n{output}")

        legacy = tmp / "legacy"
        legacy_repo(template, legacy)
        migrated = run([sys.executable, "-m", "ci_kit.cli", "migrate", "--module", "core", "--repo", str(legacy), "--to", TAG, "--template-src", str(template)])
        check("legacy allure-external migrates to core", migrated.returncode == 0, migrated.stdout + migrated.stderr)
        result = doctor(template, legacy)
        check("migrated legacy repo passes doctor", result.returncode == 0, result.stdout + result.stderr)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

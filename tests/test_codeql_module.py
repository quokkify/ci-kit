from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.cli import main
from ci_kit.handoff import AREAS
from ci_kit.legacy_answers import module_answers
from module_test_support import ROOT, mkdtemp, modules_repo, render, render_tree, shared_template_repo
from test_legacy_module_parity import assert_parity

PIN = "v99.0.0"
SCENARIOS = ROOT / "tests/scenarios"
REUSABLE = ROOT / ".github/workflows/codeql-analysis.yml"
CODEQL = ".github/workflows/codeql.yml"
GITLEAKS = ".github/workflows/gitleaks.yml"


def data_file(data: dict) -> Path:
    path = mkdtemp("codeql-data-") / "data.yml"
    path.write_text(yaml.safe_dump(data))
    return path


def legacy_tree(scenario: str, overrides: dict | None = None) -> dict[str, bytes]:
    data = yaml.safe_load((SCENARIOS / f"{scenario}.yml").read_text())
    data.update({"toolkit_version": PIN, **(overrides or {})})
    return render_tree(shared_template_repo(), data_file(data))


def security_tree(answers: dict) -> dict[str, bytes]:
    return render_tree(shared_template_repo(), data_file({**answers, "module": "security", "toolkit_version": PIN}))


def planned_languages(components: list[dict]) -> list[str]:
    declarations: dict[str, list[dict]] = {}
    for component in components:
        entry = {key: component[key] for key in ("id", "name", "path")}
        if component["type"] == "java":
            entry["java_version"] = "21"
        declarations.setdefault(component["type"], []).append(entry)
    repo = modules_repo(declarations)
    target = mkdtemp("codeql-plan-") / "out"
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        main(["plan", "--repo", str(repo), "--github-output", str(target)])
    return json.loads(dict(line.split("=", 1) for line in target.read_text().splitlines())["codeql"])


def select_languages(override: str, planned: str) -> list[str]:
    plan_job = yaml.safe_load(REUSABLE.read_text())["jobs"]["plan"]
    step = next(step for step in plan_job["steps"] if step.get("id") == "languages")
    target = mkdtemp("codeql-select-") / "out"
    target.touch()
    env = {**os.environ, **step["env"], "OVERRIDE": override, "PLANNED": planned, "GITHUB_OUTPUT": str(target)}
    subprocess.run(["bash", "-e", "-c", step["run"]], env=env, check=True, capture_output=True, text=True)
    lines = dict(line.split("=", 1) for line in target.read_text().splitlines())
    return json.loads(lines["languages"])


def effective_languages(security: dict[str, bytes], components: list[dict]) -> list[str]:
    caller = yaml.safe_load(security[CODEQL])
    override = caller["jobs"]["codeql"]["with"]["languages-override"]
    return select_languages(override, json.dumps(planned_languages(components)))


def analyze_category(job: dict) -> str:
    return next(step["with"]["category"] for step in job["steps"] if "codeql-action/analyze@" in step.get("uses", ""))


def legacy_analyze(tree: dict[str, bytes]) -> dict:
    return yaml.safe_load(tree[CODEQL])["jobs"]["analyze"]


def reusable_analyze() -> dict:
    return yaml.safe_load(REUSABLE.read_text())["jobs"]["analyze"]


class CodeqlLanguageTests(unittest.TestCase):
    def test_codeql_language_parity(self) -> None:
        checked = 0
        for path in sorted(SCENARIOS.glob("*.yml")):
            with self.subTest(scenario=path.stem):
                legacy = legacy_tree(path.stem)
                if CODEQL not in legacy:
                    continue
                checked += 1
                answers = yaml.safe_load(legacy[".copier-answers.yml"])
                security = security_tree(module_answers(answers)["security"])
                legacy_job = legacy_analyze(legacy)
                languages = effective_languages(security, answers.get("components") or [])
                self.assertEqual(len(languages), len(set(languages)))
                self.assertEqual(set(languages), set(legacy_job["strategy"]["matrix"]["language"]))
                self.assertEqual(analyze_category(reusable_analyze()), analyze_category(legacy_job))
        self.assertTrue(checked)

    def test_override_wins(self) -> None:
        override = ["actions", "go"]
        legacy = legacy_tree("polyglot", {"codeql_languages": override})
        answers = yaml.safe_load(legacy[".copier-answers.yml"])
        security = security_tree(module_answers(answers)["security"])
        self.assertEqual(effective_languages(security, answers["components"]), override)
        self.assertEqual(legacy_analyze(legacy)["strategy"]["matrix"]["language"], override)
        self.assertEqual(select_languages('["go"]', '["actions","python"]'), ["go"])
        self.assertEqual(select_languages("[]", '["actions","python"]'), ["actions", "python"])

    def test_reusable_analyze_matches_legacy(self) -> None:
        legacy_job = legacy_analyze(legacy_tree("python"))
        job = reusable_analyze()
        self.assertEqual(job["name"], legacy_job["name"])
        self.assertEqual(job["permissions"], legacy_job["permissions"])
        self.assertEqual(job["timeout-minutes"], legacy_job["timeout-minutes"])
        self.assertEqual(job["strategy"]["fail-fast"], legacy_job["strategy"]["fail-fast"])
        self.assertEqual(job["strategy"]["matrix"]["language"], "${{ fromJSON(needs.plan.outputs.languages) }}")
        codeql_steps = lambda steps: [step for step in steps if "github/codeql-action/" in step.get("uses", "")]
        self.assertEqual(codeql_steps(job["steps"]), codeql_steps(legacy_job["steps"]))
        self.assertEqual(job["steps"][0]["with"], legacy_job["steps"][0]["with"])

    def test_caller_keeps_legacy_triggers(self) -> None:
        legacy = yaml.safe_load(legacy_tree("python")[CODEQL])
        caller = yaml.safe_load(security_tree({})[CODEQL])
        for key in ("name", True, "permissions", "concurrency"):
            self.assertEqual(caller[key], legacy[key])
        self.assertEqual(caller["jobs"]["codeql"]["uses"], f"quokkify/ci-kit/.github/workflows/codeql-analysis.yml@{PIN}")
        self.assertEqual(caller["jobs"]["codeql"]["permissions"], legacy["jobs"]["analyze"]["permissions"])
        self.assertEqual(caller["jobs"]["codeql"]["with"], {"languages-override": "[]"})


class SecurityModuleTests(unittest.TestCase):
    def test_gitleaks_parity(self) -> None:
        scenarios = [path.stem for path in sorted(SCENARIOS.glob("*.yml")) if yaml.safe_load(path.read_text()).get("gitleaks", True)]
        self.assertTrue(scenarios)
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                assert_parity("security", "security", scenario, exclude={CODEQL})

    def test_security_flags(self) -> None:
        base = {".copier-answers.yml", ".ci/modules/security.yml"}
        cases = (
            ({"codeql": False, "gitleaks": True}, base | {GITLEAKS}),
            ({"codeql": True, "gitleaks": False}, base | {CODEQL}),
            ({}, base | set(AREAS["security"].legacy_paths)),
        )
        for answers, expected in cases:
            with self.subTest(answers=answers):
                self.assertEqual(set(security_tree(answers)), expected)

    def test_module_answers_security(self) -> None:
        legacy = {"codeql": True, "gitleaks": False, "codeql_languages": ["go"], "components": []}
        self.assertEqual(module_answers(legacy)["security"], {"codeql": True, "gitleaks": False, "codeql_languages": ["go"]})
        self.assertEqual(module_answers({"codeql": False, "gitleaks": True})["security"], {"codeql": False, "gitleaks": True})
        self.assertNotIn("security", module_answers({"codeql": False, "gitleaks": False}))

    def test_reusable_codeql_actionlint(self) -> None:
        self.assertIsNotNone(shutil.which("actionlint"), "actionlint is required for workflow linting")
        dest = render(shared_template_repo(), data_file({"module": "security", "toolkit_version": PIN}))
        for path in (REUSABLE, dest / CODEQL, dest / GITLEAKS):
            with self.subTest(path=path.name):
                # Same ignore as validate-toolkit.yml: actionlint does not know job.workflow_*.
                result = subprocess.run(
                    ["actionlint", "-ignore", 'property "workflow_(repository|sha)" is not defined', str(path)],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

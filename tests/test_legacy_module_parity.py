from __future__ import annotations

import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.handoff import AREAS
from ci_kit.legacy_answers import module_answers
from ci_kit.plan import build_plan
from module_test_support import ROOT, git, legacy_scenario_repo, mkdtemp, render_tree, run_copier, shared_template_repo

PIN = "v99.0.0"
SCENARIOS = ROOT / "tests/scenarios"


def scenarios_with(flag: str) -> list[str]:
    return [path.stem for path in sorted(SCENARIOS.glob("*.yml")) if yaml.safe_load(path.read_text()).get(flag)]


def assert_parity(area: str, module: str, scenario: str, exclude: set[str] = set()) -> None:
    template = shared_template_repo()
    legacy = render_tree(template, SCENARIOS / f"{scenario}.yml", {"toolkit_version": PIN})
    answers = yaml.safe_load(legacy[".copier-answers.yml"])
    translated = module_answers(answers)
    if module not in translated:
        raise AssertionError(f"scenario {scenario} does not install module {module}")
    data_file = mkdtemp("parity-") / "data.yml"
    data_file.write_text(yaml.safe_dump({**translated[module], "module": module, "toolkit_version": PIN}))
    rendered = render_tree(template, data_file)
    paths = [path for path in AREAS[area].legacy_paths if path not in exclude]
    legacy_files = {path: legacy[path] for path in paths if path in legacy}
    if not legacy_files:
        raise AssertionError(f"legacy scenario {scenario} renders no files of area {area}")
    module_files = {path: rendered[path] for path in paths if path in rendered}
    if module_files != legacy_files:
        differing = sorted(set(legacy_files) ^ set(module_files) | {p for p in legacy_files if module_files.get(p) != legacy_files[p]})
        raise AssertionError(f"{module} module differs from legacy area {area} on {scenario}: {', '.join(differing)}")


class ReleaseParityTests(unittest.TestCase):
    def test_release_parity(self) -> None:
        scenarios = scenarios_with("release_please")
        self.assertTrue(scenarios)
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                assert_parity("release", "release", scenario)

    def test_module_answers_translate_release_mode(self) -> None:
        legacy = {"release_please": True, "release_mode": "manifest"}
        self.assertEqual(module_answers(legacy), {"release": {"release_mode": "manifest"}})

    def test_release_not_installed_without_release_please(self) -> None:
        self.assertNotIn("release", module_answers({"release_please": False, "release_mode": "single"}))

    def test_double_owner_reported(self) -> None:
        template = shared_template_repo()
        repo = legacy_scenario_repo(template, "wave1-release")
        for module in ("core", "release"):
            run_copier(template, repo, "-a", f".copier-answers.{module}.yml", "-d", f"module={module}")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "modules")
        findings = build_plan(repo, drift=False).findings
        self.assertTrue(
            any(f.code in ("CIK-CORE-011", "CIK-HANDOFF-002") and "release" in f.module.split(",") for f in findings),
            [(f.code, f.module, f.message) for f in findings],
        )


if __name__ == "__main__":
    unittest.main()

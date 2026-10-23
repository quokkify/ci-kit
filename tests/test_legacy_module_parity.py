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

    def test_enrich_release_notes_copies_identical(self) -> None:
        name = "enrich_release_notes.py.jinja"
        self.assertEqual(
            (ROOT / "template/.github/scripts" / name).read_bytes(),
            (ROOT / "modules/release/template/.github/scripts" / name).read_bytes(),
        )

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


class AllureParityTests(unittest.TestCase):
    def test_allure_parity(self) -> None:
        scenarios = scenarios_with("allure_report")
        self.assertTrue(scenarios)
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                assert_parity("allure", "allure", scenario)

    def test_safe_extract_copies_identical(self) -> None:
        name = "safe_extract.py.jinja"
        self.assertEqual(
            (ROOT / "template/.github/allure" / name).read_bytes(),
            (ROOT / "modules/allure/template/.github/allure" / name).read_bytes(),
        )

    def test_module_answers_allure_auto_resolves(self) -> None:
        base = {"allure_report": True, "allure_source": "auto", "project_name": "p"}
        component = module_answers({**base, "components": [{"type": "python"}]})
        self.assertEqual(component["allure"]["allure_source"], "component")
        external = module_answers({**base, "components": []})
        self.assertEqual(external["allure"]["allure_source"], "external")
        self.assertEqual(external["allure"]["project_name"], "p")

    def test_allure_not_installed_without_allure_report(self) -> None:
        self.assertNotIn("allure", module_answers({"allure_report": False}))


class RenovateParityTests(unittest.TestCase):
    def test_renovate_parity(self) -> None:
        scenarios = scenarios_with("renovate")
        self.assertTrue(scenarios)
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                assert_parity("renovate", "renovate", scenario)

    def test_module_answers_renovate_explicit_presets(self) -> None:
        legacy = {"renovate": True, "renovate_presets": ["default"], "renovate_config_repository": "o/r", "components": [{"type": "python"}]}
        self.assertEqual(
            module_answers(legacy)["renovate"],
            {"renovate_presets": ["default"], "renovate_config_repository": "o/r"},
        )

    def test_module_answers_renovate_derives_default_presets(self) -> None:
        legacy = {"renovate": True, "components": [{"type": "python"}, {"type": "node"}], "docker": True}
        self.assertEqual(
            module_answers(legacy)["renovate"]["renovate_presets"],
            ["default", "github-actions", "python", "javascript", "docker"],
        )

    def test_renovate_not_installed_without_renovate(self) -> None:
        self.assertNotIn("renovate", module_answers({"renovate": False}))


class BaseParityTests(unittest.TestCase):
    STARTERS = ("README.md", ".github/pull_request_template.md")

    def test_base_parity(self) -> None:
        template = shared_template_repo()
        core = render_tree(template, None, {"module": "core", "toolkit_version": PIN})
        self.assertTrue(set(AREAS["base"].legacy_paths) <= set(core))
        scenarios = [name for name in (p.stem for p in sorted(SCENARIOS.glob("*.yml"))) if name not in scenarios_with("allure_report")]
        self.assertTrue(scenarios)
        for scenario in scenarios:
            with self.subTest(scenario=scenario):
                legacy = render_tree(template, SCENARIOS / f"{scenario}.yml", {"toolkit_version": PIN})
                project = yaml.safe_load(legacy[".copier-answers.yml"])["project_name"]
                expected = render_tree(template, None, {"module": "core", "toolkit_version": PIN, "project_name": project})
                for path in self.STARTERS:
                    self.assertEqual(expected[path], legacy[path], path)

    def test_copier_update_keeps_legacy_step_behind_answers_file(self) -> None:
        template = shared_template_repo()
        core = render_tree(template, None, {"module": "core", "toolkit_version": PIN})[".github/workflows/copier-update.yml"].decode()
        legacy = render_tree(template, SCENARIOS / "python.yml", {"toolkit_version": PIN})[".github/workflows/copier-update.yml"].decode()
        guard = core.index("if [[ -f .copier-answers.yml ]]; then")
        self.assertLess(guard, core.index('"${command[@]}" .'))
        self.assertIn('ci-kit update "${update_args[@]}"', core)
        self.assertIn("FETCH_HEAD:modules/release/template/.github/scripts/enrich_release_notes.py.jinja", core)
        self.assertNotIn("FETCH_HEAD:template/", core)
        # Everything up to the legacy step is the legacy workflow verbatim.
        prefix = legacy[: legacy.index("          python - <<'PY'")]
        self.assertTrue(core.startswith(prefix))


if __name__ == "__main__":
    unittest.main()

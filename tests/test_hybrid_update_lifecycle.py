from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.plan import build_plan
from ci_kit.update import run_update
from module_test_support import (
    ROOT, component_less_scenarios, component_scenarios, git, legacy_scenario_repo, lifecycle_template_repo,
)

REF = "v99.0.0"
NEXT_REF = "v99.1.0"
EDITED_COMMAND = "make hand-edited-test"
QUESTIONS = {"java": "java_components", "python": "python_components", "node": "node_components"}
MODULE_ORDER = ("java", "python", "node")


def _copy_module(template: Path, repo: Path, module: str, data: dict[str, object], ref: str = REF) -> None:
    values = {key: json.dumps(value, separators=(",", ":")) for key, value in data.items()}
    command = [
        shutil.which("copier"), "copy", "--trust", "--defaults", "--quiet", "--vcs-ref", ref,
        "-a", f".copier-answers.{module}.yml", "-d", f"module={module}",
        *(("-d", f"toolkit_version={ref}") if module == "core" else ()),
        *[arg for key, value in values.items() for arg in ("-d", f"{key}={value}")],
        str(template), str(repo),
    ]
    subprocess.run(command, check=True, capture_output=True, text=True)


def _entries(components: list[dict], language: str, test_command: str | None, allure: bool) -> list[dict]:
    entries = []
    for component in components:
        if component["type"] != language:
            continue
        entry = {"id": component["id"], "name": component["name"], "path": component["path"]}
        if allure:
            if language == "python":
                entry["install_command"] = "python -m pip install pytest allure-pytest"
            entry["upload_test_artifacts"] = True
            entry["test_artifact_path"] = component.get("allure_results_path", "allure-results")
            entry["test_artifact_name"] = f"allure-results-{component['id']}"
        if language == "java":
            entry["java_version"] = "17"
        if test_command:
            entry["test_command"] = test_command
        entries.append(entry)
    return entries


def hybrid_validate_repo(template: Path, scenario: str, test_command: str | None = None, ref: str = REF) -> Path:
    """A repository where legacy hands `validate` off to `core`, built without any migration code."""
    data = yaml.safe_load((ROOT / "tests/scenarios" / f"{scenario}.yml").read_text())
    repo = legacy_scenario_repo(template, scenario, ref, overrides={"handed_off": ["validate"]})
    _copy_module(template, repo, "core", {}, ref)
    components = data.get("components") or []
    for language in MODULE_ORDER:
        entries = _entries(components, language, test_command, bool(data.get("allure_report")))
        if entries:
            _copy_module(template, repo, language, {QUESTIONS[language]: entries}, ref)
    if data.get("docker"):
        _copy_module(template, repo, "docker", {"docker_images": [{"id": "docker"}]}, ref)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "hybrid validate fixture")
    legacy = yaml.safe_load((repo / ".copier-answers.yml").read_text())
    assert legacy["handed_off"] == ["validate"], legacy
    assert (repo / ".copier-answers.core.yml").is_file()
    assert not [p for p in repo.rglob("*.rej")]
    findings = build_plan(repo, src_override=str(template)).findings
    assert findings == [], findings
    return repo


def declared_components(repo: Path) -> list[dict]:
    return [
        component
        for module in MODULE_ORDER
        if (repo / f".ci/modules/{module}.yml").is_file()
        for component in yaml.safe_load((repo / f".ci/modules/{module}.yml").read_text())["components"]
    ]


class HybridUpdateLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = lifecycle_template_repo()
        cls.src = str(cls.template)

    def assert_clean_update(self, repo: Path, ref: str):
        updates, findings = run_update(repo, ref, src_override=self.src)
        self.assertEqual(findings, [])
        self.assertEqual(list(repo.rglob("*.rej")), [])
        self.assertEqual(build_plan(repo, src_override=self.src).findings, [])
        return updates


class ComponentLessUpdateTests(HybridUpdateLifecycleTests):
    def test_scenarios_are_discovered(self):
        scenarios = component_less_scenarios()
        for name in ("allure-external", "wave1-release", "allure-component-less-default"):
            self.assertIn(name, scenarios)
        self.assertNotIn("java", scenarios)

    def test_update_is_clean(self):
        for scenario in component_less_scenarios():
            with self.subTest(scenario=scenario):
                repo = hybrid_validate_repo(self.template, scenario)
                self.assertIn("name: Validate", (repo / ".github/workflows/validate.yml").read_text())
                updates = self.assert_clean_update(repo, NEXT_REF)
                self.assertEqual([u.module for u in updates], ["legacy", "core"])


class ComponentUpdateTests(HybridUpdateLifecycleTests):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.scenarios = component_scenarios()

    def assert_every_component_keeps_the_edit(self, repo: Path) -> None:
        components = declared_components(repo)
        self.assertTrue(components)
        for component in components:
            self.assertEqual(component.get("test_command"), EDITED_COMMAND, component["id"])

    def test_scenarios_are_discovered(self):
        for name in ("polyglot", "docker", "java", "node"):
            self.assertIn(name, self.scenarios)
        self.assertNotIn("allure-external", self.scenarios)

    def test_hand_edited_components_survive_an_update_to_the_same_ref(self):
        for scenario in self.scenarios:
            with self.subTest(scenario=scenario):
                repo = hybrid_validate_repo(self.template, scenario, EDITED_COMMAND)
                self.assert_every_component_keeps_the_edit(repo)
                head = git(repo, "rev-parse", "HEAD")
                self.assert_clean_update(repo, REF)
                self.assertEqual(git(repo, "status", "--porcelain"), "")
                self.assertEqual(git(repo, "rev-parse", "HEAD"), head)

    def test_components_update_to_the_next_release(self):
        # v99.1.0 changes the java declaration render; the hand edits live in module answers and must survive.
        for scenario in ("polyglot", "docker"):
            with self.subTest(scenario=scenario):
                repo = hybrid_validate_repo(self.template, scenario, EDITED_COMMAND)
                modules = [m for m in (*MODULE_ORDER, "docker") if (repo / f".copier-answers.{m}.yml").is_file()]
                updates = self.assert_clean_update(repo, NEXT_REF)
                self.assertEqual(sorted(u.module for u in updates), sorted(["legacy", "core", *modules]))
                for answers in repo.glob(".copier-answers*.yml"):
                    self.assertEqual(yaml.safe_load(answers.read_text())["_commit"], NEXT_REF, answers.name)
                self.assertEqual(git(repo, "status", "--porcelain"), "")
                self.assert_every_component_keeps_the_edit(repo)
                if "java" in modules:
                    self.assertIn(f"# rendered by ci-kit {NEXT_REF}", (repo / ".ci/modules/java.yml").read_text())

    def test_allure_artifact_names_use_external_prefix(self):
        repo = hybrid_validate_repo(self.template, "allure-polyglot")
        uploading = [
            entry
            for matrix in build_plan(repo, src_override=self.src).matrices.values()
            for entry in matrix
            if entry.get("upload_test_artifacts")
        ]
        self.assertTrue(uploading)
        for entry in uploading:
            self.assertTrue(entry["test_artifact_name"].startswith("allure-results-"), entry)


class FleetHybridUpdateTests(HybridUpdateLifecycleTests):
    @unittest.skipUnless(shutil.which("copier"), "Copier is required for the hybrid update integration")
    def test_hybrid_repository_updates_only_core_without_conflicts(self):
        spec = importlib.util.spec_from_file_location("update_copier_fleet", ROOT / "scripts/update_copier_fleet.py")
        fleet = importlib.util.module_from_spec(spec)
        sys.modules.setdefault(spec.name, fleet)
        spec.loader.exec_module(fleet)
        template = self.template
        repo = hybrid_validate_repo(template, "wave1-release")
        canonical = "https://github.com/quokkify/ci-kit.git"
        for name in (".copier-answers.yml", ".copier-answers.core.yml"):
            path = repo / name
            path.write_text(re.sub(r"^_src_path:.*$", f"_src_path: {canonical}", path.read_text(), flags=re.MULTILINE))
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "canonical source")
        self.assertIn(f"validate.yml@{REF}", (repo / ".github/workflows/validate.yml").read_text())
        with tempfile.TemporaryDirectory() as temporary:
            env = os.environ.copy()
            env.update({
                "HOME": str(Path(temporary).resolve()),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": f"url.file://{template}/.insteadOf",
                "GIT_CONFIG_VALUE_0": canonical,
            })
            fleet.update_template(repo, template_source=canonical, template_ref=NEXT_REF, env=env)
            rows = fleet.apply_module_updates(repo, NEXT_REF, env=env)
        self.assertEqual([(row["module"], row["to"]) for row in rows], [("core", NEXT_REF)])
        self.assertEqual(list(repo.rglob("*.rej")), [])
        self.assertIn(f"validate.yml@{NEXT_REF}", (repo / ".github/workflows/validate.yml").read_text())
        for name in (".copier-answers.yml", ".copier-answers.core.yml"):
            self.assertEqual(yaml.safe_load((repo / name).read_text())["_commit"], NEXT_REF)
        legacy_refs = [
            path.name for path in (repo / ".github/workflows").glob("*.yml")
            if path.name != "validate.yml" and f"@{REF}" in path.read_text()
        ]
        self.assertEqual(legacy_refs, [])
        self.assertEqual(build_plan(repo, src_override=str(template)).findings, [])


class FleetModuleOnlyUpdateTests(HybridUpdateLifecycleTests):
    @unittest.skipUnless(shutil.which("copier"), "Copier is required for the module-only update integration")
    def test_fleet_update_without_legacy_answers(self):
        spec = importlib.util.spec_from_file_location("update_copier_fleet", ROOT / "scripts/update_copier_fleet.py")
        fleet = importlib.util.module_from_spec(spec)
        sys.modules.setdefault(spec.name, fleet)
        spec.loader.exec_module(fleet)
        template = self.template
        repo = Path(tempfile.mkdtemp(prefix="ci-kit-module-only-"))
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        git(repo, "init", "-q", "-b", "main")
        git(repo, "config", "user.name", "test")
        git(repo, "config", "user.email", "test@example.com")
        entries = [{"id": "app", "name": "App", "path": "."}]
        _copy_module(template, repo, "core", {})
        _copy_module(template, repo, "security", {"codeql": True, "gitleaks": True})
        _copy_module(template, repo, "release", {})
        _copy_module(template, repo, "python", {QUESTIONS["python"]: entries})
        canonical = "https://github.com/quokkify/ci-kit.git"
        names = sorted(path.name for path in repo.glob(".copier-answers*.yml"))
        self.assertEqual(names, [f".copier-answers.{m}.yml" for m in ("core", "python", "release", "security")])
        for name in names:
            path = repo / name
            path.write_text(re.sub(r"^_src_path:.*$", f"_src_path: {canonical}", path.read_text(), flags=re.MULTILINE))
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "module-only fixture")
        self.assertEqual(build_plan(repo, src_override=str(template)).findings, [])
        with tempfile.TemporaryDirectory() as temporary:
            env = os.environ.copy()
            env.update({
                "HOME": str(Path(temporary).resolve()),
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": f"url.file://{template}/.insteadOf",
                "GIT_CONFIG_VALUE_0": canonical,
            })
            rows = fleet.apply_module_updates(repo, NEXT_REF, env=env)
        self.assertEqual(sorted(row["module"] for row in rows), ["core", "python", "release", "security"])
        self.assertEqual(list(repo.rglob("*.rej")), [])
        self.assertFalse((repo / ".copier-answers.yml").exists())
        for name in names:
            self.assertEqual(yaml.safe_load((repo / name).read_text())["_commit"], NEXT_REF, name)
        self.assertEqual(build_plan(repo, src_override=str(template)).findings, [])
        inventory = fleet.modules_inventory(NEXT_REF, ("core", "python", "release", "security"), repo)
        self.assertEqual((inventory.codeql, inventory.release_please, inventory.docker), ("enabled", "enabled", "disabled"))


if __name__ == "__main__":
    unittest.main()

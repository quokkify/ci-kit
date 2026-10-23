from __future__ import annotations

import shutil
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.legacy import handoff_findings
from ci_kit.plan import build_plan
from module_test_support import ROOT, mkdtemp, render, run_copier, shared_template_repo

_CACHE: dict[tuple, Path] = {}
LEGACY_WORKFLOW = ".github/workflows/gitleaks.yml"


def legacy_repo(scenario: str = "java.yml", handed_off: list | None = None, core: bool = True) -> Path:
    handed_off = ["validate"] if handed_off is None else handed_off
    key = (scenario, repr(handed_off), core)
    if key not in _CACHE:
        data = yaml.safe_load((ROOT / "tests/scenarios" / scenario).read_text())
        data["toolkit_version"] = "v99.0.0"
        data["handed_off"] = ["validate"]
        data_file = mkdtemp("scenario-") / "data.yml"
        data_file.write_text(yaml.safe_dump(data))
        dest = render(shared_template_repo(), data_file)
        if handed_off != ["validate"]:
            # The template refuses to render legacy without validate handed off; older answers are written afterwards.
            edit_answers(dest, handed_off=handed_off)
        if core:
            run_copier(shared_template_repo(), dest, "-a", ".copier-answers.core.yml", "-d", "module=core")
        _CACHE[key] = dest
        supplied = {
            "allure_external_workflow_path": "name: Run tests\non: push\njobs: {}\n",
            "allure_categories_file": "[]\n",
        }
        for answer, content in supplied.items():
            target = data.get(answer)
            if data.get("allure_report") and target and not (dest / target).exists():
                (dest / target).parent.mkdir(parents=True, exist_ok=True)
                (dest / target).write_text(content)
    copy = mkdtemp("legacy-copy-") / "repo"
    shutil.copytree(_CACHE[key], copy)
    return copy


def codes(repo: Path, drift: bool = False) -> list[str]:
    override = str(shared_template_repo()) if drift else None
    return [finding.code for finding in build_plan(repo, drift=drift, src_override=override).findings]


def edit_answers(repo: Path, **changes) -> None:
    path = repo / ".copier-answers.yml"
    data = yaml.safe_load(path.read_text())
    for key, value in changes.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    path.write_text(yaml.safe_dump(data))


class HandoffTests(unittest.TestCase):
    def test_handed_off_with_core_is_clean(self):
        repo = legacy_repo(handed_off=["validate"], core=True)
        self.assertEqual(build_plan(repo, drift=False).findings, [])
        self.assertEqual(codes(repo, drift=True), [])

    def test_handoff_001_area_without_owner_module(self):
        repo = legacy_repo(handed_off=["validate"], core=False)
        self.assertEqual(codes(repo), ["CIK-HANDOFF-001"])

    def test_handoff_002_module_without_handoff_points_to_legacy_006_runbook(self):
        repo = legacy_repo(handed_off=["validate"], core=True)
        edit_answers(repo, handed_off=[])
        findings = build_plan(repo, drift=False).findings
        self.assertEqual([f.code for f in findings], ["CIK-HANDOFF-002", "CIK-LEGACY-006"])
        self.assertIn("docs/errors/CIK-LEGACY-006.md", findings[0].message)

    def test_validate_not_handed_off_reports_legacy_006(self):
        for answers in ({"handed_off": []}, {"handed_off": None}):
            with self.subTest(answers=answers):
                repo = legacy_repo(handed_off=[], core=False)
                if answers["handed_off"] is None:
                    edit_answers(repo, **answers)
                findings = build_plan(repo, drift=False).findings
                self.assertEqual([f.code for f in findings], ["CIK-LEGACY-006"])
                self.assertEqual(findings[0].module, "legacy")
                self.assertEqual(findings[0].path, ".copier-answers.yml")
                self.assertIn("docs/errors/CIK-LEGACY-006.md", findings[0].message)

    def test_malformed_handed_off_keeps_handoff_003(self):
        for value in ("oops", ["bogus"], [1]):
            with self.subTest(value=value):
                repo = legacy_repo(core=False)
                edit_answers(repo, handed_off=value)
                self.assertEqual(codes(repo), ["CIK-HANDOFF-003"])

    def test_handoff_003_unknown_or_malformed(self):
        for value in (["bogus"], "validate", [1], {"a": 1}):
            with self.subTest(value=value):
                repo = legacy_repo(handed_off=["validate"], core=True)
                edit_answers(repo, handed_off=value)
                self.assertEqual(codes(repo), ["CIK-HANDOFF-003"])

    def test_handoff_003_does_not_double_report_legacy_003_or_005(self):
        for value in ("validate", ["bogus"]):
            with self.subTest(value=value):
                repo = legacy_repo(handed_off=["validate"], core=True)
                edit_answers(repo, handed_off=value)
                wf = repo / ".github/workflows/validate.yml"
                wf.write_text(wf.read_text().replace("@v99.0.0", "@v1.2.3"))
                self.assertEqual(codes(repo), ["CIK-HANDOFF-003"])
                wf.write_text("a: [")
                self.assertEqual(codes(repo), ["CIK-HANDOFF-003"])

    def test_broken_module_declaration_still_counts_as_installed_for_handoff_001(self):
        repo = legacy_repo(handed_off=["validate"], core=True)
        (repo / ".ci/modules/core.yml").write_text("a: [")
        self.assertEqual(codes(repo), ["CIK-CORE-004"])

    def test_unrenderable_module_does_not_make_its_files_legacy_owned(self):
        repo = legacy_repo(handed_off=["validate"], core=True)
        answers = repo / ".copier-answers.core.yml"
        answers.write_text(answers.read_text().replace("_commit: v99.0.0", "_commit: v0.0.0-missing"))
        wf = repo / ".github/workflows/validate.yml"
        wf.write_text(wf.read_text().replace("@v99.0.0", "@v1.2.3"))
        # The edited _commit also disagrees with the legacy answers.
        self.assertEqual(codes(repo, drift=True), ["CIK-CORE-012", "CIK-CORE-010"])
        wf.write_text("a: [")
        self.assertEqual(codes(repo, drift=True), ["CIK-CORE-012", "CIK-CORE-010"])

    def test_module_only_repo_runs_no_legacy_checks(self):
        from module_test_support import rendered_repo

        self.assertEqual(codes(rendered_repo("core")), [])


class HandoffFindingsTests(unittest.TestCase):
    def findings(self, handed_off, modules, rendered=None):
        return [(f.code, f.module, f.message) for f in handoff_findings(Path("."), {"handed_off": handed_off}, set(modules), rendered)]

    def test_core_and_validate_only_is_clean(self):
        self.assertEqual(self.findings(["validate"], {"core"}), [])
        self.assertEqual(self.findings(["validate"], {"core", "java"}, {".github/workflows/validate.yml"}), [])

    def test_installed_security_without_security_handoff_is_002(self):
        found = self.findings(["validate"], {"core", "security"})
        self.assertEqual([(code, module) for code, module, _ in found], [("CIK-HANDOFF-002", "security")])
        self.assertIn("(security)", found[0][2])

    def test_base_conflicts_only_when_core_renders_its_files(self):
        self.assertEqual(self.findings(["validate"], {"core"}, {".github/workflows/validate.yml"}), [])
        found = self.findings(["validate"], {"core"}, {".github/workflows/validate.yml", "docs/ci-kit.md"})
        self.assertEqual([(code, module) for code, module, _ in found], [("CIK-HANDOFF-002", "core")])
        self.assertIn("(base)", found[0][2])
        self.assertEqual(self.findings(["validate", "base"], {"core"}, {"docs/ci-kit.md"}), [])

    def test_one_finding_per_module_across_its_areas(self):
        found = self.findings([], {"core", "release"}, {".github/workflows/validate.yml", "README.md", ".github/workflows/release.yml"})
        self.assertEqual([(code, module) for code, module, _ in found], [("CIK-HANDOFF-002", "core"), ("CIK-HANDOFF-002", "release")])
        self.assertIn("(base, validate)", found[0][2])

    def test_handed_off_area_without_module_is_001_not_002(self):
        found = self.findings(["validate", "security"], {"core"})
        self.assertEqual([(code, module) for code, module, _ in found], [("CIK-HANDOFF-001", "security")])

    def test_handed_off_disabled_feature_needs_no_module(self):
        def found(answers):
            return [(f.code, f.module) for f in handoff_findings(Path("."), {"handed_off": ["validate", "security", "renovate"], **answers}, {"core"})]

        self.assertEqual(found({"codeql": False, "gitleaks": False, "renovate": False}), [])
        self.assertEqual(found({"codeql": False, "gitleaks": True, "renovate": False}), [("CIK-HANDOFF-001", "security")])

    def test_hybrid_validate_with_core_render_is_clean(self):
        repo = legacy_repo(handed_off=["validate"], core=True)
        for path in ("README.md", "docs/ci-kit.md", ".github/workflows/copier-update.yml", ".github/pull_request_template.md"):
            self.assertTrue((repo / path).is_file(), path)
        # core was copied after legacy and left the legacy-owned base files alone.
        self.assertIn("## What this project received", (repo / "docs/ci-kit.md").read_text())
        self.assertEqual(codes(repo, drift=True), [])


class LegacyTests(unittest.TestCase):
    def test_clean_legacy_render(self):
        self.assertEqual(codes(legacy_repo()), [])

    def test_legacy_001_missing_keys(self):
        repo = legacy_repo()
        edit_answers(repo, toolkit_version=None)
        self.assertEqual(codes(repo), ["CIK-LEGACY-001"])

    def test_legacy_001_unparseable_only(self):
        for text in ("a: [", "- 1\n"):
            with self.subTest(text=text):
                repo = legacy_repo()
                (repo / ".copier-answers.yml").write_text(text)
                self.assertEqual(codes(repo), ["CIK-LEGACY-001"])

    def test_legacy_002_commit_mismatch(self):
        repo = legacy_repo()
        edit_answers(repo, _commit="v98.0.0")
        # The legacy _commit also disagrees with the core answers.
        self.assertEqual(codes(repo), ["CIK-CORE-012", "CIK-LEGACY-002"])

    def test_legacy_003_ref_mismatch(self):
        repo = legacy_repo()
        wf = repo / LEGACY_WORKFLOW
        wf.write_text(wf.read_text().replace("@v99.0.0", "@v1.2.3"))
        self.assertEqual(set(codes(repo)), {"CIK-LEGACY-003"})

    def test_legacy_003_skips_module_owned_and_pinned_sha(self):
        repo = legacy_repo(handed_off=["validate"], core=True)
        wf = repo / ".github/workflows/validate.yml"
        wf.write_text(wf.read_text().replace("@v99.0.0", "@v1.2.3"))
        self.assertEqual(codes(repo, drift=False), [])
        repo2 = legacy_repo()
        wf2 = repo2 / LEGACY_WORKFLOW
        sha = "a" * 40
        wf2.write_text(wf2.read_text().replace("@v99.0.0", f"@{sha} # v99.0.0"))
        self.assertEqual(codes(repo2), [])

    def test_caller_ref_differing_from_toolkit_version_is_not_legacy_003_when_module_owned(self):
        repo = legacy_repo(handed_off=["validate"], core=True)
        wf = repo / ".github/workflows/validate.yml"
        wf.write_text(wf.read_text().replace("@v99.0.0", "@v77.0.0"))
        self.assertNotIn("CIK-LEGACY-003", codes(repo, drift=True))

    def test_legacy_004_feature_without_files(self):
        repo = legacy_repo()
        (repo / ".github/renovate.json").unlink()
        self.assertEqual(codes(repo), ["CIK-LEGACY-004"])
        repo = legacy_repo()
        (repo / ".github/workflows/codeql.yml").unlink()
        self.assertEqual(codes(repo), ["CIK-LEGACY-004"])

    def test_legacy_004_external_artifact_bounds(self):
        for low, high in ((5, 2), (0, 3), (1, 51), ("a", 3)):
            with self.subTest(low=low, high=high):
                repo = legacy_repo("allure-external.yml")
                self.assertEqual(codes(repo), [])
                edit_answers(repo, allure_external_artifact_min_count=low, allure_external_artifact_max_count=high)
                self.assertEqual(codes(repo), ["CIK-LEGACY-004"])

    def test_legacy_004_missing_categories_file(self):
        repo = legacy_repo("allure-external.yml")
        (repo / ".github/allure/categories.json").unlink()
        self.assertEqual(codes(repo), ["CIK-LEGACY-004"])

    def test_legacy_004_component_mode_without_components(self):
        repo = legacy_repo("allure-external.yml")
        edit_answers(repo, allure_source="component", components=[])
        self.assertEqual(codes(repo), ["CIK-LEGACY-004"])

    def test_every_legacy_scenario_render_is_clean(self):
        scenarios = sorted(p.name for p in (ROOT / "tests/scenarios").glob("*.yml"))
        self.assertTrue(scenarios)
        for name in scenarios:
            with self.subTest(scenario=name):
                repo = legacy_repo(name)
                override = str(shared_template_repo())
                self.assertEqual(build_plan(repo, drift=True, src_override=override).findings, [])

    def test_legacy_004_allure_rules(self):
        repo = legacy_repo("allure-polyglot.yml")
        self.assertEqual(codes(repo), [])
        (repo / ".github/allure/safe_extract.py").unlink()
        self.assertEqual(codes(repo), ["CIK-LEGACY-004"])

    def test_legacy_004_publish_pages_requires_report(self):
        repo = legacy_repo()
        edit_answers(repo, allure_publish_pages=True)
        self.assertEqual(codes(repo), ["CIK-LEGACY-004"])

    def test_legacy_005_unparseable_files(self):
        repo = legacy_repo()
        (repo / ".github/renovate.json").write_text("{")
        (repo / LEGACY_WORKFLOW).write_text("a: [")
        self.assertEqual(codes(repo), ["CIK-LEGACY-005", "CIK-LEGACY-005"])

    def test_legacy_005_skips_module_owned(self):
        repo = legacy_repo(handed_off=["validate"], core=True)
        (repo / ".github/workflows/validate.yml").write_text("a: [")
        self.assertEqual(codes(repo, drift=False), [])


if __name__ == "__main__":
    unittest.main()

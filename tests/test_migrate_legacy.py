from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit import migrate_legacy as migration
from ci_kit.cli import main
from ci_kit.drift import render_module
from ci_kit.handoff import AREAS
from ci_kit.migrate_legacy import CODEQL_CATEGORY, migrate_legacy, render_marker
from ci_kit.migration_marker import MARKER, parse_marker
from ci_kit.plan import build_plan
from ci_kit.update import commit_all, run_update
from module_test_support import ROOT, git, hide_legacy_exit_finding, lifecycle_template_repo, mkdtemp
from test_hybrid_update_lifecycle import NEXT_REF, REF, hybrid_validate_repo
from test_legacy_module_parity import OPTIONS

SCENARIOS = sorted(path.stem for path in (ROOT / "tests/scenarios").glob("*.yml"))
CODEQL = ".github/workflows/codeql.yml"
CHANGED = {CODEQL, "docs/ci-kit.md", ".github/workflows/copier-update.yml"}
MIGRATED_AREAS = ("base", "security", "release", "allure", "renovate")
STARTERS = {"README.md": "# Project README\n", ".github/renovate.json": '{"extends": ["local>owner/presets"]}\n'}


def tree(repo: Path, ref: str = "HEAD") -> dict[str, bytes]:
    names = git(repo, "ls-tree", "-r", "--name-only", ref).splitlines()
    return {name: subprocess.run(["git", "show", f"{ref}:{name}"], cwd=repo, capture_output=True, check=True).stdout for name in names}


def is_answers(path: str) -> bool:
    return path.startswith(".copier-answers") or path.startswith(".ci/modules/")


def setUpModule() -> None:
    hide_legacy_exit_finding()


class MigrateLegacyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # v99.0.0 core already renders the base area once legacy hands it off (the fleet state at v3.10).
        cls.template = lifecycle_template_repo()
        cls.src = str(cls.template)

    def repo(self, scenario: str = "wave1-release") -> Path:
        return hybrid_validate_repo(self.template, scenario)

    def migrate(self, repo: Path, to: str = REF):
        return migrate_legacy(repo, to, src_override=self.src)

    def assert_untouched(self, repo: Path, head: str) -> None:
        self.assertEqual(git(repo, "rev-parse", "HEAD"), head)
        self.assertEqual(git(repo, "status", "--porcelain", "--untracked-files=all", "--ignored"), "")

    def assert_migrated(self, repo: Path, before: dict[str, bytes], result) -> None:
        self.assertEqual(result.findings, [])
        legacy = yaml.safe_load((repo / ".copier-answers.yml").read_text())
        self.assertEqual(legacy["handed_off"], list(AREAS))
        with tempfile.TemporaryDirectory() as tmp:
            rendered = render_module(repo / ".copier-answers.yml", Path(tmp) / "render", self.src)
        self.assertEqual(rendered, {})
        after = tree(repo)
        self.assertEqual(git(repo, "status", "--porcelain"), "")
        for path in sorted(set(before) | set(after)):
            if is_answers(path) or path in CHANGED:
                continue
            with self.subTest(path=path):
                self.assertEqual(after.get(path), before.get(path))
        self.assertIn("ci-kit doctor", (repo / "docs/ci-kit.md").read_text())
        self.assertIn("if [[ -f .copier-answers.yml ]]; then", (repo / ".github/workflows/copier-update.yml").read_text())
        self.assertEqual(build_plan(repo, src_override=self.src).findings, [])
        self.assertEqual(result.checks_before, result.checks_after)
        self.assertIn("ci / gate", result.checks_after)
        self.assertEqual(len(result.commits), 2 + len(result.modules))

    def test_every_legacy_scenario_migrates(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                repo = self.repo(scenario)
                before = tree(repo)
                result = self.migrate(repo)
                self.assert_migrated(repo, before, result)
                legacy = yaml.safe_load(before[".copier-answers.yml"])
                expected = [m for m, flag in (
                    ("security", legacy.get("codeql") or legacy.get("gitleaks")), ("release", legacy.get("release_please")),
                    ("allure", legacy.get("allure_report")), ("renovate", legacy.get("renovate")),
                ) if flag]
                self.assertEqual(result.modules, expected)
                if legacy.get("codeql"):
                    self.assertIn("CodeQL", result.checks_after)

    def test_starter_files_kept(self):
        repo = self.repo("python")
        for path, text in STARTERS.items():
            (repo / path).write_text(text)
        (repo / ".github/pull_request_template.md").unlink()
        commit_all(repo, "own starter files")
        before = tree(repo)
        result = self.migrate(repo)
        self.assert_migrated(repo, before, result)
        self.assertFalse((repo / ".github/pull_request_template.md").exists())
        # The next update must neither drop the edited starters nor the base files core now owns.
        updates, findings = run_update(repo, NEXT_REF, src_override=self.src)
        self.assertEqual(findings, [])
        self.assertEqual(list(repo.rglob("*.rej")), [])
        for path, text in STARTERS.items():
            self.assertEqual((repo / path).read_text(), text)
        self.assertIn(f"`{NEXT_REF}`", (repo / "docs/ci-kit.md").read_text())
        self.assertTrue((repo / ".github/workflows/copier-update.yml").is_file())

    def test_hand_edit_refuses_before_commits(self):
        repo = self.repo()
        release = repo / ".github/workflows/release.yml"
        release.write_text(release.read_text() + "# hand edit\n")
        commit_all(repo, "edit release")
        head = git(repo, "rev-parse", "HEAD")
        result = self.migrate(repo)
        self.assertEqual([(f.code, f.path) for f in result.findings], [("CIK-MIGRATE-003", ".github/workflows/release.yml")])
        self.assertEqual(result.commits, [])
        self.assert_untouched(repo, head)

    def test_dirty_tree_001(self):
        repo = self.repo()
        (repo / "notes.txt").write_text("wip\n")
        result = self.migrate(repo)
        self.assertEqual([f.code for f in result.findings], ["CIK-MIGRATE-001"])

    def test_wrong_ref_002(self):
        repo = self.repo()
        head = git(repo, "rev-parse", "HEAD")
        result = self.migrate(repo, NEXT_REF)
        self.assertEqual([f.code for f in result.findings], ["CIK-MIGRATE-002"])
        self.assert_untouched(repo, head)

    def test_already_handed_off_004(self):
        repo = self.repo()
        (repo / ".copier-answers.release.yml").write_text(f"_commit: {REF}\nmodule: release\n")
        commit_all(repo, "release module")
        result = self.migrate(repo)
        self.assertEqual([f.code for f in result.findings], ["CIK-MIGRATE-004"])
        answers = repo / ".copier-answers.yml"
        data = yaml.safe_load(answers.read_text())
        answers.write_text(yaml.safe_dump({**data, "handed_off": ["validate", "release"]}))
        git(repo, "rm", "-q", ".copier-answers.release.yml")
        commit_all(repo, "hand release off")
        self.assertEqual([f.code for f in self.migrate(repo).findings], ["CIK-MIGRATE-004"])

    def test_equivalence_failure_rolls_back_005(self):
        repo = self.repo()
        (repo / ".gitignore").write_text("*.log\n")
        commit_all(repo, "ignore logs")
        (repo / "kept.log").write_text("existed before\n")
        head = git(repo, "rev-parse", "HEAD")
        real = migration._copy

        def tampered(repo_path, module, *args, **kwargs):
            problem = real(repo_path, module, *args, **kwargs)
            if module == "security":
                (repo_path / ".github/workflows/gitleaks.yml").write_text("name: gitleaks\n")
                (repo_path / "created.log").write_text("made by the migration\n")
                (repo_path / "cache").mkdir()
                (repo_path / "cache/run.log").write_text("made by the migration\n")
            return problem

        with mock.patch.object(migration, "_copy", side_effect=tampered):
            result = self.migrate(repo)
        self.assertEqual({f.code for f in result.findings}, {"CIK-MIGRATE-005"})
        self.assertTrue(any(f.path == ".github/workflows/gitleaks.yml" for f in result.findings), result.findings)
        self.assertEqual(result.commits, [])
        self.assertEqual(git(repo, "rev-parse", "HEAD"), head)
        self.assertEqual(git(repo, "status", "--porcelain", "--untracked-files=all"), "")
        self.assertFalse((repo / "created.log").exists())
        self.assertFalse((repo / "cache").exists())
        self.assertEqual((repo / "kept.log").read_text(), "existed before\n")

    def failing_stage(self, stage: str, error: BaseException):
        repo = self.repo()
        (repo / ".gitignore").write_text("*.log\n")
        commit_all(repo, "ignore logs")
        (repo / "kept.log").write_text("existed before\n")
        head = git(repo, "rev-parse", "HEAD")
        status = git(repo, "status", "--porcelain", "--untracked-files=all", "--ignored")
        real_run, real_copy = migration._run, migration._copy

        def update(command, repo_path):
            problem = real_run(command, repo_path)
            (repo_path / "made.log").write_text("made by the migration\n")
            raise error

        def copy(repo_path, module, *args):
            problem = real_copy(repo_path, module, *args)
            (repo_path / "made.log").write_text("made by the migration\n")
            if module == "security":
                raise error
            return problem

        patch = {"legacy-update": ("_run", update), "module-copy": ("_copy", copy)}[stage]
        with mock.patch.object(migration, patch[0], side_effect=patch[1]), self.assertRaises(type(error)):
            self.migrate(repo)
        self.assertEqual(git(repo, "rev-parse", "HEAD"), head)
        self.assertEqual(git(repo, "status", "--porcelain", "--untracked-files=all", "--ignored"), status)
        self.assertFalse((repo / "made.log").exists())
        self.assertEqual((repo / "kept.log").read_text(), "existed before\n")

    def test_any_exception_at_legacy_update_rolls_back(self):
        self.failing_stage("legacy-update", KeyboardInterrupt())

    def test_any_exception_at_module_copy_rolls_back(self):
        self.failing_stage("module-copy", KeyError("boom"))

    def test_rollback_that_raises_keeps_the_original_exception(self):
        repo = self.repo()
        real_run = migration._run

        def update(command, repo_path):
            real_run(command, repo_path)
            raise KeyError("boom")

        with mock.patch.object(migration, "_run", side_effect=update), \
                mock.patch.object(migration, "_rollback", side_effect=OSError("disk")), \
                self.assertRaises(KeyError) as raised:
            self.migrate(repo)
        self.assertTrue(any("rollback incomplete" in note for note in raised.exception.__notes__))

    def test_unrestorable_rollback_reports_a_distinct_finding(self):
        repo = self.repo()
        real_copy, real_git = migration._copy, migration._git

        def tampered(repo_path, module, *args):
            problem = real_copy(repo_path, module, *args)
            if module == "security":
                (repo_path / ".github/workflows/gitleaks.yml").write_text("name: gitleaks\n")
            return problem

        def no_restore(repo_path, *args, **kwargs):
            return "" if args[0] in ("reset", "clean") else real_git(repo_path, *args, **kwargs)

        with mock.patch.object(migration, "_copy", side_effect=tampered), mock.patch.object(migration, "_git", side_effect=no_restore):
            result = self.migrate(repo)
        messages = [f.message for f in result.findings]
        self.assertTrue(any(message.startswith("rollback incomplete") for message in messages), messages)
        self.assertFalse(any("rolled back" in message for message in messages), messages)

    def test_new_answers_keep_the_legacy_template_source(self):
        repo = self.repo()
        with tempfile.TemporaryDirectory() as tmp:
            alias = Path(tmp) / "template-alias"
            alias.symlink_to(self.template)
            result = migrate_legacy(repo, REF, src_override=str(alias))
        self.assertEqual(result.findings, [])
        legacy = yaml.safe_load((repo / ".copier-answers.yml").read_text())["_src_path"]
        for path in sorted(repo.glob(".copier-answers.*.yml")):
            with self.subTest(path=path.name):
                self.assertEqual(yaml.safe_load(path.read_text())["_src_path"], legacy)

    def test_codeql_language_change_is_005(self):
        repo = self.repo("python")
        head = git(repo, "rev-parse", "HEAD")
        real = migration._copy

        def override(repo_path, module, to, src, data):
            if module == "security":
                data = {**data, "codeql_languages": ["actions", "go"]}
            return real(repo_path, module, to, src, data)

        with mock.patch.object(migration, "_copy", side_effect=override):
            result = self.migrate(repo)
        self.assertEqual([(f.code, f.path) for f in result.findings], [("CIK-MIGRATE-005", CODEQL)])
        self.assertEqual(git(repo, "rev-parse", "HEAD"), head)

    def test_required_checks_change_is_005(self):
        repo = self.repo("python")
        head = git(repo, "rev-parse", "HEAD")
        with mock.patch.object(migration, "checks_after", return_value=["ci / gate"]):
            result = self.migrate(repo)
        self.assertEqual([f.code for f in result.findings], ["CIK-MIGRATE-005"])
        self.assertIn("required checks", result.findings[0].message)
        self.assertEqual(git(repo, "rev-parse", "HEAD"), head)

    def test_every_module_option_migrates(self):
        repo = hybrid_validate_repo(self.template, "allure-external", overrides=OPTIONS)
        before = tree(repo)
        result = self.migrate(repo)
        self.assert_migrated(repo, before, result)
        self.assertEqual(result.checks_after, ["ci / gate", "CodeQL", OPTIONS["gitleaks_job_name"], "Gitleaks"])
        security = yaml.safe_load((repo / ".copier-answers.security.yml").read_text())
        allure = yaml.safe_load((repo / ".copier-answers.allure.yml").read_text())
        for key in ("gitleaks_job_name", "gitleaks_scan", "gitleaks_aggregate", "codeql_aggregate"):
            self.assertEqual(security[key], OPTIONS[key], key)
        self.assertEqual(allure["allure_group_by"], OPTIONS["allure_group_by"])
        self.assertEqual(yaml.safe_load((repo / CODEQL).read_text())["jobs"]["complete"]["name"], "CodeQL")

    def test_codeql_aggregate_loss_is_005(self):
        repo = hybrid_validate_repo(self.template, "python", overrides={"codeql_aggregate": True})
        head = git(repo, "rev-parse", "HEAD")
        real = migration._copy

        def drop(repo_path, module, to, src, data):
            if module == "security":
                data = {**data, "codeql_aggregate": False}
            return real(repo_path, module, to, src, data)

        with mock.patch.object(migration, "_copy", side_effect=drop):
            result = self.migrate(repo)
        self.assertEqual([(f.code, f.path) for f in result.findings], [("CIK-MIGRATE-005", CODEQL)])
        self.assertIn("complete", result.findings[0].message)
        self.assertEqual(git(repo, "rev-parse", "HEAD"), head)

    def test_security_checks_follow_gitleaks_options(self):
        self.assertEqual(migration.checks_before({"codeql": True, "gitleaks": True}), ["ci / gate", "CodeQL", "gitleaks"])
        self.assertEqual(
            migration.checks_before({"codeql": True, "gitleaks": True, "gitleaks_job_name": "Secrets", "gitleaks_aggregate": True,
                                     "codeql_aggregate": True}),
            ["ci / gate", "CodeQL", "Secrets", "Gitleaks"],
        )
        self.assertEqual(migration.checks_before({"codeql": False, "gitleaks": False, "gitleaks_aggregate": True}), ["ci / gate"])
        for name in (None, "", 7):
            with self.subTest(name=name):
                self.assertEqual(migration.checks_before({"gitleaks": True, "gitleaks_job_name": name}), ["ci / gate", "gitleaks"])

    def test_codeql_category_matches_reusable_workflow(self):
        reusable = yaml.safe_load((ROOT / ".github/workflows/codeql-analysis.yml").read_text())
        steps = reusable["jobs"]["analyze"]["steps"]
        category = next(s["with"]["category"] for s in steps if "codeql-action/analyze@" in s.get("uses", ""))
        self.assertEqual(category, CODEQL_CATEGORY)

    def test_lifecycle_migrate_update_revert(self):
        repo = self.repo("allure-polyglot")
        start = git(repo, "rev-parse", "HEAD")
        before = tree(repo)
        result = self.migrate(repo)
        self.assert_migrated(repo, before, result)
        migrated = git(repo, "rev-parse", "HEAD")
        updates, findings = run_update(repo, NEXT_REF, src_override=self.src)
        self.assertEqual(findings, [])
        self.assertEqual(list(repo.rglob("*.rej")), [])
        self.assertEqual([u.module for u in updates], ["legacy", "core", "security", "allure", "renovate", "java", "python", "node"])
        self.assertIn(f"@{NEXT_REF}", (repo / ".github/workflows/allure-report.yml").read_text())
        self.assertIn("docs/ci-kit.md", git(repo, "diff", "--name-only", migrated, "HEAD").splitlines())
        self.assertEqual(build_plan(repo, src_override=self.src).findings, [])
        git(repo, "reset", "-q", "--hard", migrated)
        git(repo, "revert", "--no-edit", *reversed(result.commits))
        self.assertEqual(git(repo, "diff", start, "HEAD"), "")
        self.assertEqual(build_plan(repo, src_override=self.src).findings, [])

    def test_cli_json_and_marker(self):
        repo = self.repo()
        out = mkdtemp("migrate-json-") / "out.json"
        code = main(["migrate-legacy", "--repo", str(repo), "--to", REF, "--template-src", self.src, "--json", str(out)])
        self.assertEqual(code, 0)
        payload = json.loads(out.read_text())
        self.assertEqual(payload["module"], "legacy-exit")
        self.assertEqual(payload["area"], ",".join(MIGRATED_AREAS))
        self.assertEqual(payload["ref"], REF)
        self.assertEqual(payload["modules"], ["security", "release", "renovate"])
        self.assertEqual(len(payload["commits"]), 5)
        self.assertEqual(payload["findings"], [])
        self.assertEqual(payload["checks_before"], payload["checks_after"])
        self.assertEqual(payload["checks_after"], ["ci / gate", "CodeQL", "gitleaks"])
        result = migration.MigrateLegacyResult(ref=REF, modules=payload["modules"], commits=payload["commits"])
        marker = render_marker(result)
        self.assertTrue(marker.startswith(MARKER))
        self.assertEqual(parse_marker(f"Body\n\n{marker}\n"), {**payload, "checks_before": [], "checks_after": []})

    def test_cli_failure_exits_one(self):
        repo = self.repo()
        (repo / "notes.txt").write_text("wip\n")
        out = mkdtemp("migrate-json-") / "out.json"
        code = main(["migrate-legacy", "--repo", str(repo), "--to", REF, "--template-src", self.src, "--json", str(out)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out.read_text())["findings"][0]["code"], "CIK-MIGRATE-001")


RELEASED = "v3.9.0"


def released_template_repo() -> Path | None:
    """A clone of ci-kit where the real v3.9.0 tag precedes the working tree as v3.10.0 and a trivial v3.10.1."""
    repo = mkdtemp("ci-kit-released-") / "ci-kit"
    git(ROOT.parent, "clone", "-q", "--no-checkout", str(ROOT), str(repo))
    if subprocess.run(["git", "rev-parse", "-q", "--verify", f"refs/tags/{RELEASED}^{{commit}}"], cwd=repo, capture_output=True).returncode:
        return None
    git(repo, "checkout", "-q", "-B", "release", RELEASED)
    git(repo, "rm", "-rq", ".")
    shutil.copytree(ROOT, repo, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git", ".omc", ".superpowers", ".venv"))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "snapshot")
    git(repo, "tag", "-f", "v3.10.0")
    (repo / "RELEASE-NOTE.txt").write_text("v3.10.1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "trivial")
    git(repo, "tag", "-f", "v3.10.1")
    return repo


class ReleasedUpgradeTests(unittest.TestCase):
    """Regression guard: a hybrid repository built from the real v3.9.0 release updates, migrates and updates again."""

    def test_v3_9_0_update_migrate_update(self):
        template = released_template_repo()
        if template is None:
            self.skipTest(f"the ci-kit clone has no {RELEASED} tag (shallow or tagless checkout)")
        src = str(template)
        repo = hybrid_validate_repo(template, "python", ref=RELEASED)
        (repo / "README.md").write_text("# Customized README\n")
        commit_all(repo, "docs: customize README")
        base = {path: (repo / path).read_text() for path in ("docs/ci-kit.md", ".github/workflows/copier-update.yml")}

        updates, findings = run_update(repo, "v3.10.0", src_override=src)
        self.assertEqual(findings, [])
        self.assertEqual([u.module for u in updates], ["legacy", "core", "python"])
        self.assertEqual(list(repo.rglob("*.rej")), [])
        for path, text in base.items():
            self.assertEqual((repo / path).read_text(), text.replace(RELEASED, "v3.10.0"), path)
        self.assertEqual((repo / "README.md").read_text(), "# Customized README\n")
        self.assertEqual(build_plan(repo, src_override=src).findings, [])

        result = migrate_legacy(repo, "v3.10.0", src_override=src)
        self.assertEqual(result.findings, [])
        self.assertEqual(result.checks_before, result.checks_after)
        self.assertEqual(yaml.safe_load((repo / ".copier-answers.yml").read_text())["handed_off"], list(AREAS))
        self.assertEqual((repo / "README.md").read_text(), "# Customized README\n")
        self.assertEqual(git(repo, "status", "--porcelain"), "")
        self.assertEqual(build_plan(repo, src_override=src).findings, [])

        updates, findings = run_update(repo, "v3.10.1", src_override=src)
        self.assertEqual(findings, [])
        self.assertEqual(list(repo.rglob("*.rej")), [])
        for answers in repo.glob(".copier-answers*.yml"):
            self.assertEqual(yaml.safe_load(answers.read_text())["_commit"], "v3.10.1", answers.name)
        self.assertEqual(build_plan(repo, src_override=src).findings, [])


if __name__ == "__main__":
    unittest.main()

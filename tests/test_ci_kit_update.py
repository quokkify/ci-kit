from __future__ import annotations

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

from unittest import mock

from ci_kit.cli import main
from ci_kit.modules import MODULES, ModuleInfo
from ci_kit.plan import build_plan
from ci_kit.update import ModuleUpdate, copier_update_command, run_update, update_order
from module_test_support import (
    BASE_PATHS, ROOT, base_handoff_template_repo, git, hide_legacy_exit_finding, lifecycle_template_repo, mkdtemp,
    rendered_from,
)

_TEMPLATE: Path | None = None


def template() -> Path:
    global _TEMPLATE
    if _TEMPLATE is None:
        _TEMPLATE = lifecycle_template_repo()
    return _TEMPLATE


def commit_all(repo: Path, message: str = "initial") -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


def modules_repo(*modules: str) -> Path:
    repo = rendered_from(template(), *modules, ref="v99.0.0")
    git(repo, "init", "-q")
    commit_all(repo)
    return repo


def legacy_repo() -> Path:
    data = yaml.safe_load((ROOT / "tests/scenarios/java.yml").read_text())
    data["toolkit_version"] = "v99.0.0"
    data_file = mkdtemp("scenario-") / "data.yml"
    data_file.write_text(yaml.safe_dump(data))
    dest = mkdtemp("legacy-")
    subprocess.run(
        [shutil.which("copier"), "copy", "--trust", "--defaults", "--vcs-ref", "v99.0.0",
         "--data-file", str(data_file), str(template()), str(dest)],
        check=True, capture_output=True, text=True,
    )
    git(dest, "init", "-q")
    commit_all(dest)
    return dest


def subjects(repo: Path) -> list[str]:
    return git(repo, "log", "--format=%s").splitlines()


def setUpModule() -> None:
    hide_legacy_exit_finding()


class OrderTests(unittest.TestCase):
    def test_update_order(self):
        modules = {"zeta", "docker", "node", "python", "java", "renovate", "allure", "release", "security", "core", "legacy"}
        self.assertEqual(
            update_order(modules),
            ["legacy", "core", "security", "release", "allure", "renovate", "java", "python", "node", "docker", "zeta"],
        )
        self.assertEqual(update_order({"java", "core"}), ["core", "java"])

    def test_update_order_places_labels_after_renovate_when_supported(self):
        self.assertNotIn("labels", update_order({"core", "renovate"}))
        if "labels" not in MODULES:
            with mock.patch.dict(MODULES, {"labels": ModuleInfo(("core",))}):
                self.assertEqual(update_order({"labels", "java", "renovate", "core"}), ["core", "renovate", "labels", "java"])


class CommandTests(unittest.TestCase):
    def test_command_shape(self):
        cmd = copier_update_command(".copier-answers.core.yml", "v1", trust=False, data={"toolkit_version": "v1"})
        self.assertIn("update", cmd)
        self.assertNotIn("--trust", cmd)
        self.assertEqual(cmd[cmd.index("--vcs-ref") + 1], "v1")
        self.assertEqual(cmd[cmd.index("-d") + 1], "toolkit_version=v1")
        self.assertEqual(cmd[-2:], ["--", "."])
        legacy = copier_update_command(".copier-answers.yml", "v1", trust=True, data={"toolkit_version": "v1"})
        self.assertIn("--trust", legacy)
        self.assertEqual(legacy[legacy.index("-d") + 1], "toolkit_version=v1")

    def test_option_like_ref_refused(self):
        with self.assertRaises(ValueError):
            copier_update_command("a.yml", "--evil", trust=False, data={})


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.src = str(template())

    def test_one_commit_per_changed_module(self):
        repo = modules_repo("core", "java")
        updates, findings = run_update(repo, "v99.1.0", src_override=self.src)
        self.assertEqual(findings, [])
        self.assertEqual([u.module for u in updates], ["core", "java"])
        self.assertTrue(all(isinstance(u, ModuleUpdate) and u.from_ref == "v99.0.0" and u.to_ref == "v99.1.0" for u in updates))
        self.assertEqual(subjects(repo)[:2], ["chore(ci-kit): update java to v99.1.0", "chore(ci-kit): update core to v99.1.0"])
        self.assertEqual([u.commit for u in updates], [git(repo, "rev-parse", "HEAD~1"), git(repo, "rev-parse", "HEAD")])

    def test_no_commit_when_nothing_changes(self):
        repo = modules_repo("core")
        run_update(repo, "v99.1.0", src_override=self.src)
        head = git(repo, "rev-parse", "HEAD")
        updates, findings = run_update(repo, "v99.1.0", src_override=self.src)
        self.assertEqual(findings, [])
        self.assertEqual([u.commit for u in updates], [None])
        self.assertEqual(git(repo, "rev-parse", "HEAD"), head)

    def test_core_caller_follows_to_ref(self):
        repo = modules_repo("core", "java")
        run_update(repo, "v99.1.0", src_override=self.src)
        text = (repo / ".github/workflows/validate.yml").read_text()
        self.assertIn("@v99.1.0", text)
        self.assertNotIn("@v99.0.0", text)

    def test_module_filter_keeps_order(self):
        repo = modules_repo("core", "java")
        updates, _ = run_update(repo, "v99.1.0", modules=["java"], src_override=self.src)
        self.assertEqual([u.module for u in updates], ["java"])

    def test_rej_stops_with_cik_update_001(self):
        repo = modules_repo("core", "java")
        path = repo / ".ci/modules/java.yml"
        path.write_text(path.read_text().rstrip("\n") + "\n# local edit\n")
        commit_all(repo, "local edit")
        updates, findings = run_update(repo, "v99.1.0", src_override=self.src)
        self.assertEqual([f.code for f in findings], ["CIK-UPDATE-001"])
        self.assertEqual(findings[0].module, "java")
        self.assertIn(".rej", findings[0].message)
        self.assertEqual([u.module for u in updates], ["core"])
        self.assertEqual(subjects(repo)[0], "chore(ci-kit): update core to v99.1.0")
        self.assertIn("?? .ci/modules/java.yml.rej", git(repo, "status", "--porcelain", "-uall").splitlines())
        self.assertEqual(git(repo, "diff", "--cached", "--name-only"), "")

    def test_dirty_tree_refused(self):
        repo = modules_repo("core")
        (repo / "stray.txt").write_text("x")
        updates, findings = run_update(repo, "v99.1.0", src_override=self.src)
        self.assertEqual([f.code for f in findings], ["CIK-UPDATE-001"])
        self.assertIn("working tree is not clean", findings[0].message)
        self.assertEqual(updates, [])

    def test_unknown_module_is_cik_update_001(self):
        repo = modules_repo("core")
        updates, findings = run_update(repo, "v99.1.0", modules=["nope"], src_override=self.src)
        self.assertEqual([f.code for f in findings], ["CIK-UPDATE-001"])
        self.assertIn("no answers file for module nope", findings[0].message)

    def test_untrusted_source_is_cik_update_001(self):
        repo = modules_repo("core")
        updates, findings = run_update(repo, "v99.1.0")
        self.assertEqual([f.code for f in findings], ["CIK-UPDATE-001"])
        self.assertIn(f"untrusted template source: {self.src}", findings[0].message)
        self.assertEqual(updates, [])

    def test_skip_legacy_leaves_legacy_answers_untouched(self):
        repo = legacy_repo()
        legacy = (repo / ".copier-answers.yml").read_bytes()
        tree = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file() and ".git" not in p.relative_to(repo).parts}
        updates, _ = run_update(repo, "v99.1.0", skip_legacy=True, src_override=self.src)
        self.assertEqual(updates, [])
        self.assertEqual((repo / ".copier-answers.yml").read_bytes(), legacy)
        self.assertEqual({p: p.read_bytes() for p in repo.rglob("*") if p.is_file() and ".git" not in p.relative_to(repo).parts}, tree)

    def test_legacy_updates_first(self):
        repo = legacy_repo()
        updates, _ = run_update(repo, "v99.1.0", modules=["legacy"], src_override=self.src)
        self.assertEqual([u.module for u in updates], ["legacy"])
        self.assertEqual(subjects(repo)[0], "chore(ci-kit): update legacy to v99.1.0")
        self.assertEqual(yaml.safe_load((repo / ".copier-answers.yml").read_text())["toolkit_version"], "v99.1.0")
        self.assertNotIn("CIK-LEGACY-002", [f.code for f in build_plan(repo, src_override=self.src).findings])

    def test_commit_identity_fallback(self):
        repo = modules_repo("core")
        for key in ("user.name", "user.email"):
            subprocess.run(["git", "config", "--local", "--unset", key], cwd=repo, capture_output=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env["HOME"] = str(mkdtemp("home-"))
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        with mock.patch.dict(os.environ, env, clear=True):
            run_update(repo, "v99.1.0", src_override=self.src)
        self.assertEqual(git(repo, "log", "-1", "--format=%ae"), "ci-kit@users.noreply.github.com")


    def test_module_updates_are_not_trusted(self):
        repo = modules_repo("core", "java")
        calls = []
        real = subprocess.run

        def spy(command, *args, **kwargs):
            if "update" in command:
                calls.append(command)
            return real(command, *args, **kwargs)

        with mock.patch("ci_kit.update.subprocess.run", side_effect=spy):
            run_update(repo, "v99.1.0", src_override=self.src)
        self.assertEqual(len(calls), 2)
        self.assertFalse(any("--trust" in call for call in calls))

    def test_core_owns_base_files_without_legacy(self):
        repo = modules_repo("core")
        for path in BASE_PATHS:
            self.assertTrue((repo / path).is_file(), path)
        run_update(repo, "v99.1.0", src_override=self.src)
        self.assertIn("`v99.1.0`", (repo / "docs/ci-kit.md").read_text())
        self.assertIn("ci-kit doctor", (repo / "docs/ci-kit.md").read_text())

    def test_starter_files_not_overwritten(self):
        repo = modules_repo("core")
        edited = {"README.md": "# Project README\n", ".github/pull_request_template.md": "## What changed\n"}
        for path, text in edited.items():
            (repo / path).write_text(text)
        commit_all(repo, "own starter files")
        updates, findings = run_update(repo, "v99.1.0", src_override=self.src)
        self.assertEqual(findings, [])
        self.assertEqual([u.module for u in updates], ["core"])
        for path, text in edited.items():
            self.assertEqual((repo / path).read_text(), text)
        self.assertEqual(build_plan(repo, src_override=self.src).findings, [])


class BaseHandoffUpdateTests(unittest.TestCase):
    """core takes the base area over only once legacy hands it off (real Copier updates)."""

    @classmethod
    def setUpClass(cls):
        from test_hybrid_update_lifecycle import hybrid_validate_repo

        cls.template = base_handoff_template_repo()
        cls.src = str(cls.template)
        cls.hybrid = staticmethod(hybrid_validate_repo)

    def unmigrated(self) -> Path:
        repo = self.hybrid(self.template, "python")
        (repo / "README.md").write_text("# Project README\n")
        commit_all(repo, "own README")
        return repo

    def test_unmigrated_hybrid_core_update_leaves_legacy_base_files(self):
        repo = self.unmigrated()
        before = {path: (repo / path).read_bytes() for path in BASE_PATHS}
        updates, findings = run_update(repo, "v99.1.0", modules=["core"], src_override=self.src)
        # Only the partial update itself is reported: legacy and python stay on v99.0.0.
        self.assertEqual({f.code for f in findings}, {"CIK-CORE-012"})
        self.assertEqual([u.module for u in updates], ["core"])
        self.assertEqual(list(repo.rglob("*.rej")), [])
        self.assertEqual({path: (repo / path).read_bytes() for path in BASE_PATHS}, before)
        self.assertNotIn("docs/ci-kit.md", git(repo, "show", "--name-only", "--format=", "HEAD").splitlines())

    def test_unmigrated_hybrid_full_update_is_clean(self):
        repo = self.unmigrated()
        updates, findings = run_update(repo, "v99.1.0", src_override=self.src)
        self.assertEqual(findings, [])
        self.assertEqual([u.module for u in updates], ["legacy", "core", "python"])
        self.assertEqual(list(repo.rglob("*.rej")), [])
        self.assertEqual((repo / "README.md").read_text(), "# Project README\n")
        self.assertIn("## What this project received", (repo / "docs/ci-kit.md").read_text())
        self.assertIn("--skip-legacy", (repo / ".github/workflows/copier-update.yml").read_text())

    def test_core_update_from_release_without_base_renders_base_after_handoff(self):
        """Only core moving from a release without base files (v99.0.0 here) to one with them.

        A core release that already renders base files needs migrate-legacy instead
        (tests/test_migrate_legacy.py); a bare legacy handoff plus update loses them there.
        """
        repo = self.unmigrated()
        subprocess.run(
            [shutil.which("copier"), "update", "--trust", "--defaults", "--skip-tasks", "--vcs-ref", "v99.1.0",
             "-d", "toolkit_version=v99.1.0", "-d", "handed_off=[validate, base]", "."],
            cwd=repo, check=True, capture_output=True, text=True,
        )
        commit_all(repo, "hand base off")
        updates, findings = run_update(repo, "v99.1.0", src_override=self.src)
        self.assertEqual(findings, [])
        self.assertEqual([(u.module, u.commit is None) for u in updates], [("legacy", True), ("core", False), ("python", False)])
        self.assertEqual(list(repo.rglob("*.rej")), [])
        for path in BASE_PATHS:
            self.assertTrue((repo / path).is_file(), path)
        self.assertIn("ci-kit doctor", (repo / "docs/ci-kit.md").read_text())
        self.assertIn("ci-kit update", (repo / ".github/workflows/copier-update.yml").read_text())
        self.assertEqual(build_plan(repo, src_override=self.src).findings, [])


class CliTests(unittest.TestCase):
    def test_json_output_and_exit(self):
        repo = modules_repo("core", "java")
        out = mkdtemp("json-") / "out.json"
        code = main(["update", "--repo", str(repo), "--to", "v99.1.0", "--template-src", str(template()), "--json", str(out)])
        self.assertEqual(code, 0)
        data = json.loads(out.read_text())
        self.assertEqual(data["ref"], "v99.1.0")
        self.assertEqual([m["module"] for m in data["modules"]], ["core", "java"])
        self.assertEqual(set(data["modules"][0]), {"module", "from", "to", "commit"})
        self.assertEqual(data["findings"], [])

    def test_findings_exit_one_and_json_written(self):
        repo = modules_repo("core")
        out = mkdtemp("json-") / "out.json"
        code = main(["update", "--repo", str(repo), "--to", "v99.1.0", "--json", str(out)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out.read_text())["findings"][0]["code"], "CIK-UPDATE-001")


if __name__ == "__main__":
    unittest.main()

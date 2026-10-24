from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from unittest import mock

from ci_kit.cli import main
from ci_kit.modules import MODULES, ModuleInfo
from ci_kit.plan import build_plan
from ci_kit.update import ModuleUpdate, copier_update_command, run_update, update_order
from module_test_support import git, lifecycle_template_repo, mkdtemp, rendered_from

BASE_PATHS = (".github/workflows/copier-update.yml", "docs/ci-kit.md", "README.md", ".github/pull_request_template.md")

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


def subjects(repo: Path) -> list[str]:
    return git(repo, "log", "--format=%s").splitlines()


class OrderTests(unittest.TestCase):
    def test_update_order(self):
        modules = {"zeta", "docker", "node", "python", "java", "renovate", "allure", "release", "security", "core"}
        self.assertEqual(
            update_order(modules),
            ["core", "security", "release", "allure", "renovate", "java", "python", "node", "docker", "zeta"],
        )
        self.assertEqual(update_order({"java", "core"}), ["core", "java"])

    def test_update_order_places_labels_after_renovate_when_supported(self):
        self.assertNotIn("labels", update_order({"core", "renovate"}))
        if "labels" not in MODULES:
            with mock.patch.dict(MODULES, {"labels": ModuleInfo(("core",))}):
                self.assertEqual(update_order({"labels", "java", "renovate", "core"}), ["core", "renovate", "labels", "java"])


class CommandTests(unittest.TestCase):
    def test_command_shape(self):
        cmd = copier_update_command(".copier-answers.core.yml", "v1", data={"toolkit_version": "v1"})
        self.assertIn("update", cmd)
        self.assertNotIn("--trust", cmd)
        self.assertEqual(cmd[cmd.index("--vcs-ref") + 1], "v1")
        self.assertEqual(cmd[cmd.index("-d") + 1], "toolkit_version=v1")
        self.assertEqual(cmd[-2:], ["--", "."])
        self.assertNotIn("-x", cmd)

    def test_option_like_ref_refused(self):
        with self.assertRaises(ValueError):
            copier_update_command("a.yml", "--evil", data={})


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

    def test_empty_legacy_answers_do_not_hold_modules_back(self):
        # Regression guard: v4 ignores legacy answers that hand every area off; the fleet drops them.
        repo = modules_repo("core", "java")
        legacy = (
            "_commit: v99.0.0\n_src_path: https://github.com/quokkify/ci-kit.git\nmodule: legacy\n"
            "toolkit_version: v99.0.0\nhanded_off: [validate, base, security, release, allure, renovate]\n"
        )
        (repo / ".copier-answers.yml").write_text(legacy)
        commit_all(repo, "empty legacy answers")
        updates, findings = run_update(repo, "v99.1.0", src_override=self.src)
        self.assertEqual(findings, [])
        self.assertEqual([u.module for u in updates], ["core", "java"])
        self.assertEqual((repo / ".copier-answers.yml").read_text(), legacy)
        self.assertEqual(list(repo.rglob("*.rej")), [])

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

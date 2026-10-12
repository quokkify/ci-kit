from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.cli import main
from ci_kit.update import ModuleUpdate, copier_update_command, run_update, update_order
from module_test_support import ROOT, git, lifecycle_template_repo, mkdtemp, rendered_from

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


class OrderTests(unittest.TestCase):
    def test_order_legacy_core_languages_others(self):
        p = Path("x")
        self.assertEqual(
            update_order({"java": p, "legacy": p, "core": p, "zeta": p, "node": p}),
            ["legacy", "core", "java", "node", "zeta"],
        )


class CommandTests(unittest.TestCase):
    def test_command_shape(self):
        cmd = copier_update_command(".copier-answers.core.yml", "v1", trust=False, data={"toolkit_version": "v1"})
        self.assertIn("update", cmd)
        self.assertNotIn("--trust", cmd)
        self.assertEqual(cmd[cmd.index("--vcs-ref") + 1], "v1")
        self.assertEqual(cmd[cmd.index("-d") + 1], "toolkit_version=v1")
        self.assertLess(cmd.index("--"), len(cmd))
        self.assertIn("--trust", copier_update_command(".copier-answers.yml", "v1", trust=True, data={}))

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

    def test_commit_identity_fallback(self):
        repo = modules_repo("core")
        for key in ("user.name", "user.email"):
            subprocess.run(["git", "config", "--local", "--unset", key], cwd=repo, capture_output=True)
        env_home = mkdtemp("home-")
        import os

        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env["HOME"] = str(env_home)
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        old = os.environ.copy()
        try:
            os.environ.clear()
            os.environ.update(env)
            run_update(repo, "v99.1.0", src_override=self.src)
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertEqual(git(repo, "log", "-1", "--format=%ae"), "ci-kit@users.noreply.github.com")


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

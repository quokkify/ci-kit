from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.plan import build_plan
from module_test_support import GIT_ENV, git, lifecycle_template_repo, rendered_from

ANSWERS = {"core": ".copier-answers.core.yml", "java": ".copier-answers.java.yml"}


def update(repo: Path, module: str, ref: str) -> None:
    copier = shutil.which("copier")
    result = subprocess.run(
        [copier, "update", "--trust", "--defaults", "--quiet", "-a", ANSWERS[module], "--vcs-ref", ref],
        cwd=repo, capture_output=True, text=True, env={**os.environ, **GIT_ENV},
    )
    if result.returncode:
        raise AssertionError(result.stderr or result.stdout)


def changed_paths(repo: Path) -> set[str]:
    tracked = git(repo, "diff", "--name-only", "HEAD").splitlines()
    untracked = git(repo, "ls-files", "--others", "--exclude-standard").splitlines()
    return set(tracked + untracked)


def snapshot(repo: Path) -> dict[str, bytes]:
    return {
        p.relative_to(repo).as_posix(): p.read_bytes()
        for p in sorted(repo.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(repo).parts
    }


class LifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = lifecycle_template_repo()

    def setUp(self):
        self.repo = rendered_from(self.template, "core", "java", ref="v99.0.0")
        git(self.repo, "init", "-q")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "render")

    def commit(self):
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "update")

    def plan(self):
        return build_plan(self.repo, src_override=str(self.template))

    def test_fresh_render_has_clean_plan(self):
        self.assertEqual(self.plan().findings, [])

    def test_update_java_only_changes_java_paths(self):
        update(self.repo, "java", "v99.1.0")
        self.assertEqual(changed_paths(self.repo), {".copier-answers.java.yml", ".ci/modules/java.yml"})
        # core stays on v99.0.0, so the plan reports the split release.
        self.assertEqual([f.code for f in self.plan().findings], ["CIK-CORE-012"])

    def test_joint_update_changes_both_answers(self):
        update(self.repo, "core", "v99.1.0")
        self.commit()
        update(self.repo, "java", "v99.1.0")
        self.commit()
        changed = set(git(self.repo, "diff", "--name-only", "HEAD~2", "HEAD").splitlines())
        self.assertEqual(changed, {".copier-answers.core.yml", ".copier-answers.java.yml", ".ci/modules/java.yml"})
        self.assertEqual(self.plan().findings, [])

    def test_second_update_is_empty_diff(self):
        update(self.repo, "java", "v99.1.0")
        self.commit()
        update(self.repo, "java", "v99.1.0")
        self.assertEqual(changed_paths(self.repo), set())

    def test_manual_edit_then_update_reports_drift_not_conflict(self):
        path = self.repo / ".ci/modules/java.yml"
        path.write_text(path.read_text().replace('"21"', '"17"'))
        self.assertEqual([f.code for f in self.plan().findings], ["CIK-CORE-010"])
        git(self.repo, "checkout", "--", ".ci/modules/java.yml")
        update(self.repo, "java", "v99.1.0")
        self.assertNotIn("<<<<<<<", path.read_text())
        self.assertEqual(list(self.repo.rglob("*.rej")), [])
        self.assertEqual([f.code for f in self.plan().findings], ["CIK-CORE-012"])

    def test_rollback_java_commit_restores_previous_render(self):
        before = snapshot(self.repo)
        update(self.repo, "java", "v99.1.0")
        self.assertNotEqual(snapshot(self.repo), before)
        self.commit()
        git(self.repo, "revert", "--no-edit", "HEAD")
        self.assertEqual(snapshot(self.repo), before)
        self.assertEqual(self.plan().findings, [])

    def test_removing_java_module_leaves_clean_plan(self):
        (self.repo / ".copier-answers.java.yml").unlink()
        (self.repo / ".ci/modules/java.yml").unlink()
        plan = self.plan()
        self.assertEqual(plan.findings, [])
        self.assertEqual(plan.modules, ["core"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from module_test_support import ROOT, make_template_repo, render


class ModuleSelectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="module-selector-")).resolve()
        cls.addClassCleanup(shutil.rmtree, cls.tmp, ignore_errors=True)
        cls.candidate = make_template_repo(cls.tmp)

    def test_module_defaults_to_core(self) -> None:
        dest = render(self.candidate)
        try:
            answers = yaml.safe_load((dest / ".copier-answers.core.yml").read_text())
            self.assertFalse((dest / ".copier-answers.yml").exists())
            self.assertTrue((dest / ".ci/modules/core.yml").is_file())
        finally:
            shutil.rmtree(dest, ignore_errors=True)
        self.assertEqual(answers.get("module"), "core")

    def test_no_legacy_choice(self) -> None:
        dest = self.tmp / "legacy"
        result = subprocess.run(
            [shutil.which("copier"), "copy", "--trust", "--defaults", "--vcs-ref", "HEAD",
             "-d", "module=legacy", str(self.candidate), str(dest)],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("legacy", result.stderr + result.stdout)
        self.assertFalse((dest / ".copier-answers.yml").exists())


class NoLegacyTemplateTests(unittest.TestCase):
    def test_no_template_dir_references(self) -> None:
        self.assertFalse((ROOT / "template").exists())
        tracked = subprocess.run(
            ["git", "ls-files", "--", "scripts", "tools", ".github"],
            cwd=ROOT, check=True, capture_output=True, text=True,
        ).stdout.split()
        hits = []
        for name in tracked:
            path = ROOT / name
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                for match in re.finditer(r"template/", line):
                    if line[match.end():].startswith("{"):
                        continue  # a diff label such as f"template/{path}", not a path in this repository
                    prefix = line[: match.start()]
                    # Every remaining template path is a module's: modules/<name>/template/.
                    if not re.search(r"modules/\S*/$", prefix) and not re.search(r"[A-Za-z0-9_-]$", prefix):
                        hits.append(f"{name}:{number}: {line.strip()}")
                        break
        self.assertEqual(hits, [])


# The last release that still shipped the legacy template; every fleet repository was on it before v4.
LAST_LEGACY_RELEASE = "v3.11.2"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout


def tree(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file() and ".git" not in path.relative_to(root).parts
    }


class BareLegacyUpdateTests(unittest.TestCase):
    def test_bare_copier_update_of_legacy_answers_is_refused_before_writing(self) -> None:
        # Regression guard: a bare `copier update` on a v3.11.2 legacy consumer replaced `module: legacy`
        # with the default core, rendered .copier-answers.core.yml and deleted .copier-answers.yml and
        # legacy-owned files, with no CIK-CORE-030 check on that path.
        copier = shutil.which("copier")
        self.assertIsNotNone(copier, "copier is required")
        with tempfile.TemporaryDirectory(prefix="bare-legacy-update-") as raw:
            root = Path(raw).resolve()
            template = root / "ci-kit"
            template.mkdir()
            archive = subprocess.run(["git", "archive", LAST_LEGACY_RELEASE], cwd=ROOT, check=True, capture_output=True).stdout
            subprocess.run(["tar", "-x", "-C", str(template)], input=archive, check=True)
            git(template, "init", "-q")
            git(template, "add", "-A")
            git(template, "commit", "-qm", "last legacy release")
            git(template, "tag", "v99.0.0")
            consumer = root / "consumer"
            subprocess.run([copier, "copy", "--trust", "--defaults", "--vcs-ref", "v99.0.0", str(template), str(consumer)],
                           check=True, capture_output=True, text=True)
            self.assertEqual(yaml.safe_load((consumer / ".copier-answers.yml").read_text())["module"], "legacy")
            git(consumer, "init", "-q")
            git(consumer, "add", "-A")
            git(consumer, "commit", "-qm", "legacy consumer")
            # v99.1.0 is this working tree, tracked files and new untracked ones alike.
            git(template, "rm", "-rq", ".")
            listed = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                                    cwd=ROOT, check=True, capture_output=True).stdout.decode().split("\0")
            for name in filter(None, listed):
                source = ROOT / name
                if source.is_file() and not source.is_symlink():
                    (template / name).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, template / name)
            git(template, "add", "-A")
            git(template, "commit", "-qm", "v4")
            git(template, "tag", "v99.1.0")
            before = tree(consumer)
            result = subprocess.run([copier, "update", "--trust", "--defaults", "--vcs-ref", "v99.1.0"],
                                    cwd=consumer, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("CIK-CORE-030", result.stdout + result.stderr)
            self.assertIn("docs/errors/CIK-CORE-030.md", result.stdout + result.stderr)
            self.assertEqual(tree(consumer), before)
            self.assertTrue((consumer / ".copier-answers.yml").is_file())
            self.assertEqual(git(consumer, "status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()

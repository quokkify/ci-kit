from __future__ import annotations

import ast
import json
import os
import shutil
import re
import subprocess
import tempfile
import textwrap
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "modules/core/template/.github/workflows/copier-update.yml.jinja"


def production_script(workflow: Path = WORKFLOW) -> str:
    content = workflow.read_text(encoding="utf-8")
    run_block = content.split("        run: |\n", 1)[1]
    script = textwrap.dedent(run_block)
    return script.replace("{% raw %}", "").replace("{% endraw %}", "")


def run_git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, text=True, capture_output=True
    )
    return result.stdout.strip()


LEGACY_AREAS = ("validate", "base", "security", "release", "allure", "renovate")
STABLE = {"tagName": "v2.21.5", "publishedAt": "2026-09-01T00:00:00Z", "isDraft": False, "isPrerelease": False}


class CopierUpdateWorkflowTests(unittest.TestCase):
    """The core module's workflow: drop or refuse legacy answers, then update the modules with ci-kit."""

    def setUp(self) -> None:
        self.script = production_script()

    def make_checkout(self, temporary: Path) -> tuple[Path, Path]:
        bare = temporary / "remote.git"
        checkout = temporary / "checkout"
        run_git("init", "--bare", str(bare), cwd=temporary)
        run_git("clone", str(bare), str(checkout), cwd=temporary)
        run_git("config", "user.name", "fixture", cwd=checkout)
        run_git("config", "user.email", "fixture@example.com", cwd=checkout)
        (checkout / "README.md").write_text("fixture\n", encoding="utf-8")
        (checkout / ".copier-answers.core.yml").write_text("module: core\n", encoding="utf-8")
        run_git("add", ".", cwd=checkout)
        run_git("commit", "-m", "initial", cwd=checkout)
        run_git("push", "origin", "HEAD:refs/heads/main", cwd=checkout)
        return checkout, bare

    def fake_tools(self, temporary: Path) -> Path:
        bin_dir = temporary / "bin"
        bin_dir.mkdir()
        (bin_dir / "gh").write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "if [[ \" $* \" == *\" --jq .tagName \"* ]]; then printf '%s\\n' \"${LATEST_REF:-v2.21.5}\"; else cat \"$GH_RELEASE_JSON\"; fi\n",
            encoding="utf-8",
        )
        # core never runs Copier directly; any call is recorded so tests can assert it never happens.
        (bin_dir / "copier").write_text(
            "#!/usr/bin/env bash\nprintf 'copier-called\\n' >> \"$COPIER_LOG\"\n",
            encoding="utf-8",
        )
        update = "" if getattr(self, "ci_kit_noop", False) else (
            "printf 'module update\\n' > .ci-kit-updated\n"
            "git add --all && git -c user.name=t -c user.email=t@example.com commit -q -m 'chore(ci-kit): update core'\n"
        )
        (bin_dir / "ci-kit").write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "printf '%s\\n' \"$*\" >> \"$CI_KIT_LOG\"\n" + update,
            encoding="utf-8",
        )
        (bin_dir / "python").write_text(
            "#!/usr/bin/env bash\n"
            "if [[ \"$1 $2 $3\" == '-m pip install' ]]; then printf '%s\\n' \"$4\" >> \"$PIP_LOG\"; exit 0; fi\n"
            f"exec {shutil.which('python3') or sys.executable} \"$@\"\n",
            encoding="utf-8",
        )
        for tool in (bin_dir / "gh", bin_dir / "copier", bin_dir / "ci-kit", bin_dir / "python"):
            tool.chmod(0o755)
        return bin_dir

    def execute(self, checkout: Path, temporary: Path, release: dict, template_ref: str = "v2.21.5") -> subprocess.CompletedProcess[str]:
        release_file = temporary / "release.json"
        release_file.write_text(json.dumps(release), encoding="utf-8")
        env = {
            **os.environ,
            "PATH": f"{self.fake_tools(temporary)}:{os.environ['PATH']}",
            "TEMPLATE_REF": template_ref,
            "GH_RELEASE_JSON": str(release_file),
            "COPIER_LOG": str(temporary / "copier.log"),
            "CI_KIT_LOG": str(temporary / "ci-kit.log"),
            "PIP_LOG": str(temporary / "pip.log"),
            "GITHUB_STEP_SUMMARY": str(temporary / "summary.md"),
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "fixture/project",
            "DEFAULT_BRANCH": "main",
            "BRANCH": "automation/copier-template-update",
        }
        return subprocess.run(
            ["bash", "-c", self.script], cwd=checkout, env=env, text=True, capture_output=True
        )

    def test_yaml_dependency_is_in_test_setup_not_generated_update_workflow(self) -> None:
        self.assertNotIn("pip install --disable-pip-version-check --quiet PyYAML==6.0.3", self.script)
        validation_workflow = (ROOT / ".github/workflows/validate-toolkit.yml").read_text(encoding="utf-8")
        pins = {re.split(r"==", pin)[0]: pin for pin in tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]}
        self.assertIn(f"python -m pip install {pins['PyYAML']} {pins['copier']}", validation_workflow)


    def test_unreleased_or_mismatched_release_ref_stops_before_copier(self) -> None:
        cases = [
            ("v2.21.5", {"tagName": "v2.21.4", "publishedAt": "2026-09-01T00:00:00Z", "isDraft": False, "isPrerelease": False}),
            ("v2.21.5", {"tagName": "v2.21.5", "publishedAt": None, "isDraft": False, "isPrerelease": False}),
            ("v2.21.5", {"tagName": "v2.21.5", "publishedAt": "2026-09-01T00:00:00Z", "isDraft": True, "isPrerelease": False}),
            ("v2.21.5", {"tagName": "v2.21.5", "publishedAt": "2026-09-01T00:00:00Z", "isDraft": False, "isPrerelease": True}),
            ("main", {"tagName": "main", "publishedAt": "2026-09-01T00:00:00Z", "isDraft": False, "isPrerelease": False}),
            ("b13b2d1604dbde783d0ee23ac01f41c1853fd665", {"tagName": "b13b2d1604dbde783d0ee23ac01f41c1853fd665", "publishedAt": "2026-09-01T00:00:00Z", "isDraft": False, "isPrerelease": False}),
        ]
        for template_ref, release in cases:
            with self.subTest(template_ref=template_ref, release=release), tempfile.TemporaryDirectory() as raw:
                temporary = Path(raw)
                checkout, _ = self.make_checkout(temporary)
                result = self.execute(checkout, temporary, release, template_ref)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Refused template ref", result.stderr)
                self.assertFalse((temporary / "copier.log").exists())
                self.assertFalse((temporary / "ci-kit.log").exists())

    def test_copier_update_without_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            temporary = Path(raw)
            checkout, bare = self.make_checkout(temporary)
            result = self.execute(checkout, temporary, STABLE)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((temporary / "copier.log").exists())
            self.assertEqual((temporary / "ci-kit.log").read_text(encoding="utf-8"), "update --repo . --to v2.21.5\n")
            self.assertEqual((temporary / "pip.log").read_text(encoding="utf-8"), "git+https://github.com/quokkify/ci-kit@v2.21.5\n")
            branch = "automation/copier-template-update"
            self.assertIn(".ci-kit-updated", run_git("--git-dir", str(bare), "ls-tree", "-r", "--name-only", branch, cwd=temporary).splitlines())
            self.assertEqual(run_git("--git-dir", str(bare), "rev-list", "--count", branch, cwd=temporary), "2")

    def legacy_checkout(self, temporary: Path, answers: str) -> tuple[Path, Path]:
        checkout, bare = self.make_checkout(temporary)
        (checkout / ".copier-answers.yml").write_text(answers, encoding="utf-8")
        run_git("add", ".", cwd=checkout)
        run_git("commit", "-m", "legacy answers", cwd=checkout)
        run_git("push", "origin", "HEAD:refs/heads/main", cwd=checkout)
        return checkout, bare

    @staticmethod
    def handed_off(areas) -> str:
        return "_src_path: gh:quokkify/ci-kit\nhanded_off:\n" + "".join(f"  - {area}\n" for area in areas)

    def test_copier_update_drops_empty_legacy(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            temporary = Path(raw)
            checkout, bare = self.legacy_checkout(temporary, self.handed_off(LEGACY_AREAS))
            result = self.execute(checkout, temporary, STABLE)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((temporary / "copier.log").exists())
            self.assertEqual((temporary / "ci-kit.log").read_text(encoding="utf-8"), "update --repo . --to v2.21.5\n")
            branch = "automation/copier-template-update"
            git = ("--git-dir", str(bare))
            drop, update = run_git(*git, "rev-list", "--reverse", f"main..{branch}", cwd=temporary).split()
            self.assertEqual(run_git(*git, "log", "-1", "--format=%s", drop, cwd=temporary), "chore(ci-kit): drop empty legacy answers")
            self.assertEqual(run_git(*git, "diff", "--name-status", "main", drop, cwd=temporary), "D\t.copier-answers.yml")
            self.assertEqual(run_git(*git, "log", "-1", "--format=%s", update, cwd=temporary), "chore(template): update shared project template")
            self.assertEqual(run_git(*git, "diff", "--name-status", drop, update, cwd=temporary), "A\t.ci-kit-updated")

    def test_copier_update_pushes_the_drop_alone_when_modules_are_current(self) -> None:
        self.ci_kit_noop = True
        with tempfile.TemporaryDirectory() as raw:
            temporary = Path(raw)
            checkout, bare = self.legacy_checkout(temporary, self.handed_off(LEGACY_AREAS))
            result = self.execute(checkout, temporary, STABLE)
            self.assertEqual(result.returncode, 0, result.stderr)
            branch = "automation/copier-template-update"
            commits = run_git("--git-dir", str(bare), "log", "--format=%s", f"main..{branch}", cwd=temporary).splitlines()
            self.assertEqual(commits, ["chore(ci-kit): drop empty legacy answers"])

    def test_copier_update_refuses_partial_legacy(self) -> None:
        cases = (
            self.handed_off(["validate", "base"]),
            self.handed_off([]),
            "handed_off: [validate, base, security, release, allure, renovate, 1]\n",
            "handed_off: validate\n",
            "handed_off: [validate\n",
            "- not a mapping\n",
        )
        for answers in cases:
            with self.subTest(answers=answers), tempfile.TemporaryDirectory() as raw:
                temporary = Path(raw)
                checkout, bare = self.legacy_checkout(temporary, answers)
                head = run_git("rev-parse", "HEAD", cwd=checkout)
                result = self.execute(checkout, temporary, STABLE)
                self.assertEqual(result.returncode, 1)
                self.assertIn("CIK-CORE-030 legacy answers still render files", result.stderr)
                self.assertIn("security, release, allure, renovate", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse((temporary / "copier.log").exists())
                self.assertFalse((temporary / "ci-kit.log").exists())
                self.assertEqual(run_git("status", "--porcelain", cwd=checkout), "")
                self.assertEqual(run_git("rev-parse", "HEAD", cwd=checkout), head)
                self.assertEqual(run_git("--git-dir", str(bare), "branch", "--list", "automation/*", cwd=temporary), "")

    def test_workflow_areas_match_the_plan(self) -> None:
        sys.path.insert(0, str(ROOT / "tools"))
        from ci_kit.plan import LEGACY_AREAS as PLAN_AREAS

        match = re.search(r"^areas = (\(.*\))$", self.script, flags=re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertEqual(ast.literal_eval(match.group(1)), PLAN_AREAS)
        self.assertEqual(LEGACY_AREAS, PLAN_AREAS)

    def test_release_module_requires_helper(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            temporary = Path(raw)
            checkout, _ = self.make_checkout(temporary)
            (checkout / ".copier-answers.release.yml").write_text("module: release\n", encoding="utf-8")
            run_git("add", ".", cwd=checkout)
            run_git("commit", "-m", "release module", cwd=checkout)
            result = self.execute(checkout, temporary, STABLE)
            self.assertEqual(result.returncode, 1)
            self.assertIn("missing while the release module is installed", result.stderr)

    def test_generated_workflow_checks_helper_drift_even_for_same_ref(self) -> None:
        check = self.script.index("Verify the executable release helper")
        update = self.script.index('ci-kit update --repo . --to "$ref"')
        no_op = self.script.index("git diff --cached --quiet\nthen") if "git diff --cached --quiet\nthen" in self.script else self.script.rindex("git diff --cached --quiet")
        self.assertLess(update, check)
        self.assertLess(check, no_op)
        self.assertIn('cmp -s "$template_helper" "$helper"', self.script)
        self.assertIn("FETCH_HEAD:modules/release/template/.github/scripts/enrich_release_notes.py.jinja", self.script)

    def test_same_ref_stale_helper_fails_before_noop_but_healthy_helper_is_noop(self) -> None:
        canonical = "canonical helper from template\\n"
        for helper_content, expected_status in (("stale helper\\n", 1), (canonical, 0)):
            with self.subTest(helper_content=helper_content), tempfile.TemporaryDirectory() as raw:
                temporary = Path(raw)
                checkout, template_bare = self.make_checkout(temporary)
                template_work = temporary / "template"
                run_git("clone", str(template_bare), str(template_work), cwd=temporary)
                run_git("config", "user.name", "fixture", cwd=template_work)
                run_git("config", "user.email", "fixture@example.com", cwd=template_work)
                template_helper = template_work / "modules/release/template/.github/scripts/enrich_release_notes.py.jinja"
                template_helper.parent.mkdir(parents=True)
                template_helper.write_text(canonical, encoding="utf-8")
                run_git("add", ".", cwd=template_work)
                run_git("commit", "-m", "template helper", cwd=template_work)
                run_git("tag", "v2.21.5", cwd=template_work)
                run_git("push", "origin", "refs/tags/v2.21.5:refs/tags/v2.21.5", cwd=template_work)
                run_git("config", "url." + str(template_bare) + ".insteadOf", "https://github.com/quokkify/ci-kit.git", cwd=checkout)
                (checkout / ".copier-answers.release.yml").write_text("module: release\n", encoding="utf-8")
                helper = checkout / ".github/scripts/enrich_release_notes.py"
                helper.parent.mkdir(parents=True)
                helper.write_text(helper_content, encoding="utf-8")
                run_git("add", ".", cwd=checkout)
                run_git("commit", "-m", "release helper", cwd=checkout)
                run_git("push", "origin", "HEAD:refs/heads/main", cwd=checkout)
                result = self.execute(checkout, temporary, STABLE)
                self.assertEqual(result.returncode, expected_status, result.stderr)
                if expected_status:
                    self.assertIn("differs from ci-kit", result.stderr)

    def test_module_update_step_is_guarded_and_ordered(self) -> None:
        script = self.script
        guard = script.index("if [[ -f .copier-answers.yml ]]; then")
        drop = script.index('git commit -q -m "chore(ci-kit): drop empty legacy answers"')
        install = script.index('python -m pip install "git+https://github.com/quokkify/ci-kit@${ref}"')
        update = script.index('ci-kit update --repo . --to "$ref"')
        reset = script.index('git reset --soft "$base"')
        self.assertLess(guard, drop)
        self.assertLess(drop, install)
        self.assertLess(install, update)
        self.assertLess(update, reset)
        self.assertNotIn("update_args", script)
        self.assertNotIn(" -x ", script)

    def test_module_update_uses_temp_commit_identity_and_no_swallowed_failure(self) -> None:
        # No legacy update precedes the module step any more, so no temporary commit (and identity) is needed.
        self.assertIn('base="$(git rev-parse HEAD)"', self.script)
        self.assertNotIn("pending legacy update", self.script)
        section = self.script[self.script.index('base="$(git rev-parse HEAD)"'):self.script.index('git reset --soft "$base"')]
        self.assertNotIn("|| true", section)

    def test_module_update_has_no_expression_interpolation_in_run(self) -> None:
        content = WORKFLOW.read_text(encoding="utf-8")
        run_block = content.split("        run: |\n", 1)[1]
        stripped = run_block.replace("{% raw %}${#rejected[@]}{% endraw %}", "")
        self.assertNotIn("${{", stripped)
        self.assertNotIn("{{", stripped)

    def test_module_update_runs_ci_kit_not_copier(self) -> None:
        # core renders this workflow, so .copier-answers.core.yml always exists; the module
        # step runs whenever there are no legacy answers to drop or refuse.
        self.assertNotIn("compgen", self.script)
        self.assertNotIn("copier update", self.script)
        self.assertNotIn("--skip-legacy", self.script)
        self.assertIn("\nci-kit update", self.script)


if __name__ == "__main__":
    unittest.main()

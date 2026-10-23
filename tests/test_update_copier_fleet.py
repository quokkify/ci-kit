from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO
from unittest import TestCase, main, mock, skipUnless

import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "update_copier_fleet", ROOT / "scripts/update_copier_fleet.py"
)
assert SPEC and SPEC.loader
fleet = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fleet
SPEC.loader.exec_module(fleet)


def cloned_answers(raw_answers: str):
    def materialize(_: object, destination: Path, *, env: dict[str, str]) -> None:
        del env
        destination.mkdir(parents=True, exist_ok=True)
        (destination / fleet.ANSWERS_FILE).write_text(raw_answers, encoding="utf-8")

    return materialize


class DiscoveryTests(TestCase):
    @mock.patch.object(fleet, "gh_json", return_value=[])
    def test_public_only_discovery_is_explicit(self, gh_json_mock: mock.Mock) -> None:
        self.assertEqual(fleet.discover_repositories("quokkify", env={}, public_only=True), [])
        arguments = gh_json_mock.call_args.args[0]
        self.assertIn("--visibility", arguments)
        self.assertEqual(arguments[arguments.index("--visibility") + 1], "public")
        self.assertIn("visibility", arguments[arguments.index("--json") + 1].split(","))

    @mock.patch.object(
        fleet,
        "gh_json",
        return_value=[
            {
                "nameWithOwner": "quokkify/leaked-private",
                "defaultBranchRef": {"name": "main"},
                "isArchived": False,
                "isFork": False,
                "visibility": "PRIVATE",
            },
            {
                "nameWithOwner": "quokkify/public-example",
                "defaultBranchRef": {"name": "main"},
                "isArchived": False,
                "isFork": False,
                "visibility": "PUBLIC",
            },
        ],
    )
    def test_public_only_discovery_rechecks_visibility_client_side(self, _: mock.Mock) -> None:
        discovered = fleet.discover_repositories("quokkify", env={}, public_only=True)
        self.assertEqual([repo.name_with_owner for repo in discovered], ["quokkify/public-example"])


class SingleManifestSeedingTests(TestCase):
    def test_tag_fallback_requests_json_lines(self) -> None:
        with mock.patch.object(fleet, "run") as run_mock:
            run_mock.return_value = type("Result", (), {"stdout": '"v1.2.3"\n', "stderr": "", "returncode": 0})()
            self.assertEqual(
                fleet.gh_json_lines(
                    ["api", "repos/x/tags", "--paginate", "--jq", ".[].name | @json"],
                    env={},
                ),
                ["v1.2.3"],
            )
            self.assertEqual(
                run_mock.call_args.args[0],
                ["gh", "api", "repos/x/tags", "--paginate", "--jq", ".[].name | @json"],
            )

    def test_seed_uses_unambiguous_latest_stable_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / fleet.ANSWERS_FILE).write_text(
                "release_please: true\nrelease_mode: single\n", encoding="utf-8"
            )
            (root / ".github/workflows").mkdir(parents=True)
            (root / ".github/workflows/release.yml").write_text("name: release\n")
            releases = [
                {"tagName": "v4.5.6", "isLatest": True},
                {"tagName": "v4.5.5", "isLatest": False},
            ]
            with mock.patch.object(fleet, "gh_json", return_value=releases), mock.patch.object(
                fleet, "gh_json_lines"
            ) as tags_mock:
                fleet.seed_single_release_manifest(
                    root, fleet.Repository("quokkify/example", "main"), env={}
                )
            self.assertEqual(
                json.loads((root / ".github/release-please/manifest.json").read_text()),
                {".": "4.5.6"},
            )
            tags_mock.assert_not_called()

    def test_seed_repairs_a_released_single_consumer_with_missing_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / fleet.ANSWERS_FILE).write_text(
                "release_please: true\nrelease_mode: single\n", encoding="utf-8"
            )
            with mock.patch.object(
                fleet,
                "gh_json",
                return_value=[{"tagName": "v4.5.6", "isLatest": True}],
            ), mock.patch.object(fleet, "gh_json_lines") as tags_mock:
                fleet.seed_single_release_manifest(
                    root, fleet.Repository("quokkify/example", "main"), env={}
                )
            self.assertEqual(
                json.loads((root / ".github/release-please/manifest.json").read_text()),
                {".": "4.5.6"},
            )
            tags_mock.assert_not_called()

    def test_seed_uses_highest_exact_semver_tag_when_releases_are_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / fleet.ANSWERS_FILE).write_text("release_please: true\n", encoding="utf-8")
            (root / ".github/workflows").mkdir(parents=True)
            (root / ".github/workflows/release.yml").write_text("name: release\n", encoding="utf-8")
            repository = fleet.Repository("quokkify/example", "main")
            with mock.patch.object(fleet, "gh_json", return_value=[]), mock.patch.object(
                fleet, "gh_json_lines", return_value=["v1.2.3", "v1.10.0", "not-a-version"]
            ):
                fleet.seed_single_release_manifest(root, repository, env={})
            self.assertEqual(
                json.loads((root / ".github/release-please/manifest.json").read_text()),
                {".": "1.10.0"},
            )

    def test_seed_fails_closed_on_ambiguous_stable_releases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / fleet.ANSWERS_FILE).write_text("release_please: true\n", encoding="utf-8")
            (root / ".github/workflows").mkdir(parents=True)
            (root / ".github/workflows/release.yml").write_text("name: release\n", encoding="utf-8")
            releases = [{"tagName": "v1.0.0", "isLatest": True}, {"tagName": "v2.0.0", "isLatest": True}]
            with mock.patch.object(fleet, "gh_json", return_value=releases):
                with self.assertRaisesRegex(fleet.FleetUpdateError, "ambiguous"):
                    fleet.seed_single_release_manifest(root, fleet.Repository("quokkify/example", "main"), env={})

    def test_seed_fails_closed_when_release_and_exact_tags_are_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / fleet.ANSWERS_FILE).write_text("release_please: true\n", encoding="utf-8")
            (root / ".github/workflows").mkdir(parents=True)
            (root / ".github/workflows/release.yml").write_text("name: release\n")
            with mock.patch.object(fleet, "gh_json", return_value=[]), mock.patch.object(
                fleet, "gh_json_lines", return_value=["release-1", "v1.0.0-rc.1"]
            ):
                with self.assertRaisesRegex(fleet.FleetUpdateError, "ambiguous or missing"):
                    fleet.seed_single_release_manifest(
                        root, fleet.Repository("quokkify/example", "main"), env={}
                    )

    def test_manifest_mode_and_existing_version_are_never_reseeded(self) -> None:
        for answers, existing in (
            ("release_please: true\nrelease_mode: manifest\n", None),
            ("release_please: true\nrelease_mode: single\n", {".": "9.8.7"}),
        ):
            with self.subTest(answers=answers), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / fleet.ANSWERS_FILE).write_text(answers, encoding="utf-8")
                (root / ".github/workflows").mkdir(parents=True)
                (root / ".github/workflows/release.yml").write_text("name: release\n")
                manifest = root / ".github/release-please/manifest.json"
                if existing is not None:
                    manifest.parent.mkdir(parents=True, exist_ok=True)
                    manifest.write_text(json.dumps(existing), encoding="utf-8")
                with mock.patch.object(fleet, "gh_json") as releases_mock:
                    fleet.seed_single_release_manifest(
                        root, fleet.Repository("quokkify/example", "main"), env={}
                    )
                releases_mock.assert_not_called()
                if existing is None:
                    self.assertFalse(manifest.exists())
                else:
                    self.assertEqual(json.loads(manifest.read_text()), existing)


class ReleaseDependencySectionTests(TestCase):
    def repair(self, config: dict, answers: str = "release_please: true\n") -> tuple[bool, dict]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / fleet.ANSWERS_FILE).write_text(answers, encoding="utf-8")
            path = root / ".github/release-please/config.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(config), encoding="utf-8")
            changed = fleet.ensure_release_dependency_sections(root)
            return changed, json.loads(path.read_text(encoding="utf-8"))

    def template_sections(self) -> list[dict]:
        return json.loads(fleet.TEMPLATE_RELEASE_CONFIG.read_text(encoding="utf-8"))["packages"]["."]["changelog-sections"]

    def test_adds_template_sections_when_release_please_defaults_apply(self) -> None:
        changed, config = self.repair({"release-type": "simple", "packages": {".": {"package-name": "skills"}}})
        self.assertTrue(changed)
        self.assertEqual(config["packages"]["."]["changelog-sections"], self.template_sections())
        self.assertEqual(config["packages"]["."]["package-name"], "skills")

    def test_appends_or_unhides_deps_and_keeps_custom_sections(self) -> None:
        changed, config = self.repair({"packages": {
            "a": {"changelog-sections": [{"type": "feat", "section": "Features"}]},
            "b": {"changelog-sections": [{"type": "deps", "section": "Deps", "hidden": True}]},
        }})
        self.assertTrue(changed)
        self.assertEqual(config["packages"]["a"]["changelog-sections"], [
            {"type": "feat", "section": "Features"},
            {"type": "deps", "section": "📦 Dependencies"},
        ])
        self.assertEqual(config["packages"]["b"]["changelog-sections"], [{"type": "deps", "section": "Deps"}])

    def test_overrides_only_the_package_whose_inherited_sections_lack_deps(self) -> None:
        top = [{"type": "fix", "section": "Fixes"}]
        changed, config = self.repair({"changelog-sections": top, "packages": {
            ".": {}, "web": {"changelog-sections": [{"type": "deps", "section": "Deps"}]},
        }})
        self.assertTrue(changed)
        self.assertEqual(config["changelog-sections"], top)
        self.assertEqual(config["packages"]["."]["changelog-sections"], [*top, {"type": "deps", "section": "📦 Dependencies"}])
        self.assertEqual(config["packages"]["web"], {"changelog-sections": [{"type": "deps", "section": "Deps"}]})

    def test_leaves_valid_or_disabled_configs_untouched(self) -> None:
        valid = {"packages": {".": {"changelog-sections": self.template_sections()}}}
        self.assertEqual(self.repair(valid), (False, valid))
        broken = {"packages": {".": {}}}
        self.assertEqual(self.repair(broken, "release_please: false\n"), (False, broken))

    def test_fails_closed_on_config_without_packages(self) -> None:
        with self.assertRaises(fleet.FleetUpdateError):
            self.repair({"release-type": "simple"})

    def test_repaired_config_passes_the_release_workflow_guard(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/release-please.yml").read_text())
        guard = next(
            step["run"] for step in workflow["jobs"]["validate"]["steps"]
            if step.get("name") == "Require a visible dependencies changelog section"
        )
        _, config = self.repair({"packages": {".": {}, "web": {"changelog-sections": []}}})
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "config.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-I", "-c", guard], env={"CONFIG_FILE": str(path)},
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class ProjectOwnedToolkitRefTests(TestCase):
    """The generated contract requires every toolkit reference to match toolkit_version."""

    def write_workflows(self, root: Path) -> None:
        workflows = root / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text(
            "jobs:\n"
            "  build:\n"
            "    uses: quokkify/ci-kit/.github/workflows/node-ci.yml@v2.14.0\n"
            "    steps:\n"
            "      - uses: quokkify/ci-kit/actions/setup-node@v2.14.0\n",
            encoding="utf-8",
        )
        (workflows / "pinned.yml").write_text(
            "jobs:\n"
            "  build:\n"
            "    uses: quokkify/ci-kit/actions/setup-node@"
            + "a" * 40
            + " # v2.14.0\n",
            encoding="utf-8",
        )
        (workflows / "foreign.yml").write_text(
            "jobs:\n  build:\n    uses: other/repo/.github/workflows/ci.yml@v2.14.0\n",
            encoding="utf-8",
        )

    def test_tag_references_move_to_the_new_template_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_workflows(root)
            changed = fleet.bump_project_owned_toolkit_refs(root, "v2.19.0")
            ci = (root / ".github/workflows/ci.yml").read_text()
        self.assertEqual(changed, [".github/workflows/ci.yml"])
        self.assertIn("node-ci.yml@v2.19.0", ci)
        self.assertIn("setup-node@v2.19.0", ci)
        self.assertNotIn("v2.14.0", ci)

    def test_digest_pins_and_other_repositories_are_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_workflows(root)
            fleet.bump_project_owned_toolkit_refs(root, "v2.19.0")
            pinned = (root / ".github/workflows/pinned.yml").read_text()
            foreign = (root / ".github/workflows/foreign.yml").read_text()
        self.assertIn("a" * 40, pinned)
        self.assertIn("# v2.14.0", pinned)
        self.assertIn("other/repo/.github/workflows/ci.yml@v2.14.0", foreign)


ANSWERS_TEMPLATE = ROOT / "template/.copier-answers.yml.jinja"
ANSWERS_CORPUS = [
    {
        "_commit": "v2.25.0",
        "_src_path": "https://github.com/quokkify/ci-kit.git",
        "allure_categories_file": "",
        "project_name": "App & Co",
        "flag": "on",
        "Yes": "No",
        "count_text": "123",
        "colon": "a: b",
        "comment": "tag #1",
        "unicode": "Привет мир",
        "infinity": ".inf",
        "dotted": ".github/workflows/test.yml",
        "here": ".",
        "newline": "first\nsecond",
        "quote": 'say "hi"',
        "nothing": None,
        "enabled": True,
        "disabled": False,
        "count": 7,
        "languages": [],
        "settings": {},
        "renovate_presets": ["default", "github-actions", "123"],
        "components": [
            {"id": "app-java", "name": "App Java", "path": ".", "type": "java"},
            {"id": "web", "name": "", "path": "frontend", "type": "node", "tags": ["a", "on"], "extra": {"k": "v"}},
        ],
        "nested": {"inner": {"list": [1, "x"]}, "empty": []},
    },
    {"_commit": "v1.0.0", "only": "value"},
]


def render_answers_template(answers: dict) -> str:
    import jinja2

    environment = jinja2.Environment(
        extensions=["jinja2_ansible_filters.AnsibleCoreFiltersExtension"], keep_trailing_newline=True
    )
    return environment.from_string(ANSWERS_TEMPLATE.read_text(encoding="utf-8")).render(
        _copier_answers=answers
    )


class AnswersFormatTests(TestCase):
    """The template and the updater must write one Prettier-compatible answers format."""

    def test_updater_and_template_write_identical_round_tripping_answers(self) -> None:
        for answers in ANSWERS_CORPUS:
            rendered = fleet.render_answers(answers)
            with self.subTest(keys=list(answers)[:3]):
                self.assertEqual(yaml.safe_load(rendered), answers)
                self.assertEqual(render_answers_template(answers), rendered)

    def test_format_matches_prettier_conventions(self) -> None:
        rendered = fleet.render_answers(ANSWERS_CORPUS[0])
        for line in [
            "_src_path: https://github.com/quokkify/ci-kit.git",
            'allure_categories_file: ""',
            'flag: "on"',
            '"Yes": "No"',
            'count_text: "123"',
            'colon: "a: b"',
            'infinity: ".inf"',
            "dotted: .github/workflows/test.yml",
            "here: .",
            'newline: "first\\nsecond"',
            "languages: []",
            "settings: {}",
            "renovate_presets:\n  - default\n  - github-actions\n  - \"123\"",
            "components:\n  - id: app-java\n    name: App Java\n    path: .\n    type: java\n  - id: web\n    name: \"\"",
            "    tags:\n      - a\n      - \"on\"\n    extra:\n      k: v",
        ]:
            self.assertIn(line, rendered)
        self.assertNotIn("'", rendered)
        self.assertTrue(rendered.endswith("\n") and not rendered.endswith("\n\n"))

    def test_changed_answers_are_rewritten_without_single_quotes(self) -> None:
        original = '_commit: v2.24.0\nallure_categories_file: ""\n'
        copier_serialized = "_commit: v2.25.0\nallure_categories_file: ''\ngitleaks: false\n"
        with tempfile.TemporaryDirectory() as temporary:
            answers = Path(temporary) / fleet.ANSWERS_FILE
            answers.write_text(copier_serialized, encoding="utf-8")
            fleet.restore_answers_format_if_semantically_equal(answers, original)
            self.assertEqual(
                answers.read_text(encoding="utf-8"),
                '_commit: v2.25.0\nallure_categories_file: ""\ngitleaks: false\n',
            )


class RenamedToolkitRefTests(TestCase):
    """Project-owned workflows that still use the old repository name follow the rename."""

    def test_renamed_tag_and_digest_references_move_to_the_current_name_and_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            (workflows / "build.yml").write_text(
                "jobs:\n"
                "  build:\n"
                "    uses: quokkify/project-toolkit/.github/workflows/java-ci.yml@v2.25.0\n"
                "    steps:\n"
                "      - uses: quokkify/project-toolkit/actions/setup-java-gradle@" + "a" * 40 + " # v2.25.0\n"
                "      - run: echo quokkify/project-toolkit/actions/not-a-reference\n"
                "      - uses: someone/quokkify/project-toolkit/actions/x@v1.0.0\n",
                encoding="utf-8",
            )
            changed = fleet.bump_project_owned_toolkit_refs(root, "v3.0.0", resolve_commit=lambda: "b" * 40)
            text = (workflows / "build.yml").read_text()
        self.assertEqual(changed, [".github/workflows/build.yml"])
        self.assertIn("uses: quokkify/ci-kit/.github/workflows/java-ci.yml@v3.0.0", text)
        self.assertIn("uses: quokkify/ci-kit/actions/setup-java-gradle@" + "b" * 40 + " # v3.0.0", text)
        self.assertIn("run: echo quokkify/project-toolkit/actions/not-a-reference", text)
        self.assertIn("uses: someone/quokkify/project-toolkit/actions/x@v1.0.0", text)


class AlreadyAppliedRejectTests(TestCase):
    """A rejected hunk the project already carries is not a conflict."""

    REJECTED = (
        "diff a/.github/workflows/report.yml b/.github/workflows/report.yml\t(rejected hunks)\n"
        "@@ -1,3 +1,2 @@ jobs:\n"
        " jobs:\n"
        "   report: {}\n"
        "-\n"
    )

    def check(self, content: str) -> tuple[bool, bool]:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workflows = root / ".github" / "workflows"
            workflows.mkdir(parents=True)
            (workflows / "report.yml").write_text(content, encoding="utf-8")
            rejected = workflows / "report.yml.rej"
            rejected.write_text(self.REJECTED, encoding="utf-8")
            return fleet.discard_if_already_applied(root, rejected), rejected.exists()

    def test_reject_for_a_change_the_project_already_made_is_discarded(self) -> None:
        self.assertEqual(self.check("jobs:\n  report: {}\n"), (True, False))

    def test_reject_for_a_change_that_is_still_missing_is_kept(self) -> None:
        self.assertEqual(self.check("jobs:\n  report: {name: local}\n"), (False, True))


class AuthenticatedGitTests(TestCase):
    """git does not read GH_TOKEN; a hosted runner has no credential helper at all."""

    def test_push_and_ls_remote_authenticate_through_gh(self) -> None:
        repository = fleet.Repository("quokkify/example", "main")
        with (
            mock.patch.object(fleet, "run") as run_mock,
            tempfile.TemporaryDirectory() as temporary,
        ):
            run_mock.return_value.stdout = ""
            fleet.push_automation_branch(
                repository,
                Path(temporary),
                branch="automation/copier-template-update",
                env={"GH_TOKEN": "token"},
            )
        remote_commands = [
            call.args[0]
            for call in run_mock.call_args_list
            if call.args[0][0] == "git" and any(arg in ("push", "ls-remote") for arg in call.args[0])
        ]
        self.assertEqual(len(remote_commands), 2)
        for command in remote_commands:
            self.assertIn("credential.helper=!gh auth git-credential", command)
            # The empty helper must come first so an ambient one cannot win.
            self.assertLess(
                command.index("credential.helper="),
                command.index("credential.helper=!gh auth git-credential"),
            )

    def test_local_git_operations_are_not_given_credentials(self) -> None:
        repository = fleet.Repository("quokkify/example", "main")
        with (
            mock.patch.object(fleet, "run") as run_mock,
            tempfile.TemporaryDirectory() as temporary,
        ):
            run_mock.return_value.stdout = ""
            fleet.push_automation_branch(
                repository,
                Path(temporary),
                branch="automation/copier-template-update",
                env={"GH_TOKEN": "token"},
            )
        for call in run_mock.call_args_list:
            command = call.args[0]
            if command[0] == "git" and command[1] in ("config", "add", "commit", "switch"):
                self.assertNotIn("credential.helper=!gh auth git-credential", command)


class IdenticalBranchPushTests(TestCase):
    """A rerun must not restart pilot CI by pushing the same change again."""

    def push(self, remote_tree: str, remote_parent: str) -> list[list[str]]:
        repository = fleet.Repository("quokkify/example", "main")
        outputs = {"ls-remote": "c" * 40 + "\trefs/heads/branch\n", "HEAD^{tree}": "t" * 40, "HEAD^": "p" * 40}

        def fake_run(command, **_):
            key = next((arg for arg in command if arg in outputs), None)
            return subprocess.CompletedProcess(command, 0, outputs.get(key, ""), "")

        remote = {"commit": {"tree": {"sha": remote_tree}}, "parents": [{"sha": remote_parent}]}
        with (
            mock.patch.object(fleet, "run", side_effect=fake_run) as run_mock,
            mock.patch.object(fleet, "gh_json", return_value=remote),
            tempfile.TemporaryDirectory() as temporary,
        ):
            fleet.push_automation_branch(repository, Path(temporary), branch="branch", env={})
        return [call.args[0] for call in run_mock.call_args_list if "push" in call.args[0]]

    def test_same_tree_on_same_base_is_not_pushed(self) -> None:
        self.assertEqual(self.push("t" * 40, "p" * 40), [])

    def test_changed_tree_is_pushed(self) -> None:
        self.assertEqual(len(self.push("x" * 40, "p" * 40)), 1)

    def test_same_tree_on_a_moved_base_is_pushed(self) -> None:
        self.assertEqual(len(self.push("t" * 40, "x" * 40)), 1)


class ExplicitRepositoryVisibilityTests(TestCase):
    """--public-only is an invariant of the public fleet front, not only a discovery filter."""

    def run_main(self, metadata: dict[str, object], arguments: list[str]) -> tuple[int, str]:
        buffer = StringIO()
        with (
            mock.patch.object(fleet, "gh_json", return_value=metadata),
            mock.patch.object(
                fleet,
                "process_repository",
                return_value=fleet.Result("quokkify/example", "up-to-date"),
            ) as process_mock,
            mock.patch.object(fleet.shutil, "which", return_value="/usr/bin/tool"),
            mock.patch.object(fleet, "resolve_template_ref", return_value="v2.16.0"),
            mock.patch.dict(
                "os.environ",
                {"GITHUB_TOKEN": "repository-scoped-token"},
                clear=False,
            ),
            redirect_stdout(buffer),
        ):
            status = fleet.main(arguments)
            self.processed = process_mock.call_count
        return status, buffer.getvalue()

    def test_public_only_rejects_an_explicitly_requested_private_repository(self) -> None:
        status, output = self.run_main(
            {
                "nameWithOwner": "quokkify/private-example",
                "defaultBranchRef": {"name": "main"},
                "isArchived": False,
                "isFork": False,
                "visibility": "PRIVATE",
            },
            ["--dry-run", "--public-only", "--repo", "quokkify/private-example"],
        )
        self.assertEqual(status, 1)
        self.assertIn("--public-only rejects non-public repositories", output)
        self.assertIn("without --public-only", output)
        self.assertEqual(self.processed, 0)

    def test_public_only_rejects_an_internal_repository(self) -> None:
        status, output = self.run_main(
            {
                "nameWithOwner": "quokkify/internal-example",
                "defaultBranchRef": {"name": "main"},
                "isArchived": False,
                "isFork": False,
                "visibility": "INTERNAL",
            },
            ["--dry-run", "--public-only", "--repo", "quokkify/internal-example"],
        )
        self.assertEqual(status, 1)
        self.assertIn("--public-only rejects non-public repositories", output)
        self.assertEqual(self.processed, 0)

    def test_public_only_rejects_metadata_without_a_visibility_field(self) -> None:
        status, output = self.run_main(
            {
                "nameWithOwner": "quokkify/unknown-example",
                "defaultBranchRef": {"name": "main"},
                "isArchived": False,
                "isFork": False,
            },
            ["--dry-run", "--public-only", "--repo", "quokkify/unknown-example"],
        )
        self.assertEqual(status, 1)
        self.assertIn("--public-only rejects non-public repositories", output)
        self.assertEqual(self.processed, 0)

    def test_public_only_rejects_a_null_visibility_field(self) -> None:
        status, _ = self.run_main(
            {
                "nameWithOwner": "quokkify/null-example",
                "defaultBranchRef": {"name": "main"},
                "isArchived": False,
                "isFork": False,
                "visibility": None,
            },
            ["--dry-run", "--public-only", "--repo", "quokkify/null-example"],
        )
        self.assertEqual(status, 1)
        self.assertEqual(self.processed, 0)

    def test_public_only_rejects_a_private_repository_in_write_mode(self) -> None:
        with mock.patch.dict("os.environ", {"GH_TOKEN": "codeowner-token"}, clear=False):
            status, output = self.run_main(
                {
                    "nameWithOwner": "quokkify/private-example",
                    "defaultBranchRef": {"name": "main"},
                    "isArchived": False,
                    "isFork": False,
                    "visibility": "PRIVATE",
                },
                ["--write", "--public-only", "--repo", "quokkify/private-example"],
            )
        self.assertEqual(status, 1)
        self.assertIn("--public-only rejects non-public repositories", output)
        self.assertEqual(self.processed, 0)

    def test_public_only_rejects_a_private_archived_repository_loudly(self) -> None:
        status, _ = self.run_main(
            {
                "nameWithOwner": "quokkify/private-archived",
                "defaultBranchRef": {"name": "main"},
                "isArchived": True,
                "isFork": False,
                "visibility": "PRIVATE",
            },
            ["--dry-run", "--public-only", "--repo", "quokkify/private-archived"],
        )
        self.assertEqual(status, 1)
        self.assertEqual(self.processed, 0)

    def test_public_only_accepts_an_explicitly_requested_public_repository(self) -> None:
        status, _ = self.run_main(
            {
                "nameWithOwner": "quokkify/public-example",
                "defaultBranchRef": {"name": "main"},
                "isArchived": False,
                "isFork": False,
                "visibility": "PUBLIC",
            },
            ["--dry-run", "--public-only", "--repo", "quokkify/public-example"],
        )
        self.assertEqual(status, 0)
        self.assertEqual(self.processed, 1)

    def test_without_public_only_visibility_does_not_gate_an_explicit_repository(self) -> None:
        status, _ = self.run_main(
            {
                "nameWithOwner": "quokkify/private-example",
                "defaultBranchRef": {"name": "main"},
                "isArchived": False,
                "isFork": False,
                "visibility": "PRIVATE",
            },
            ["--dry-run", "--repo", "quokkify/private-example"],
        )
        self.assertEqual(status, 0)
        self.assertEqual(self.processed, 1)

    def test_explicit_repository_metadata_requests_visibility(self) -> None:
        with (
            mock.patch.object(
                fleet,
                "gh_json",
                return_value={
                    "nameWithOwner": "quokkify/public-example",
                    "defaultBranchRef": {"name": "main"},
                    "isArchived": False,
                    "isFork": False,
                    "visibility": "PUBLIC",
                },
            ) as gh_json_mock,
            mock.patch.object(
                fleet,
                "process_repository",
                return_value=fleet.Result("quokkify/public-example", "up-to-date"),
            ),
            mock.patch.object(fleet.shutil, "which", return_value="/usr/bin/tool"),
            mock.patch.object(fleet, "resolve_template_ref", return_value="v2.16.0"),
            mock.patch.dict(
                "os.environ",
                {"GITHUB_TOKEN": "repository-scoped-token", "GH_TOKEN": ""},
                clear=False,
            ),
            redirect_stdout(StringIO()),
        ):
            fleet.main(["--dry-run", "--public-only", "--repo", "quokkify/public-example"])
        arguments = gh_json_mock.call_args.args[0]
        self.assertIn("visibility", arguments[arguments.index("--json") + 1])


class TemplateSourceTests(TestCase):
    def test_accepts_supported_github_source_forms(self) -> None:
        expected = "quokkify/ci-kit"
        for source in (
            "gh:quokkify/ci-kit",
            "https://github.com/quokkify/ci-kit.git",
            "git@github.com:quokkify/ci-kit.git",
            "ssh://git@github.com/quokkify/ci-kit/",
        ):
            with self.subTest(source=source):
                self.assertEqual(fleet.normalize_template_source(source), expected)

    def test_renamed_repository_sources_resolve_to_ci_kit(self) -> None:
        for source in ("https://github.com/quokkify/project-toolkit.git", "gh:quokkify/project-toolkit"):
            with self.subTest(source=source):
                self.assertEqual(fleet.normalize_template_source(source), "quokkify/ci-kit")

    def test_renamed_source_is_rewritten_to_canonical_url(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            answers = repository / fleet.ANSWERS_FILE
            answers.write_text("_commit: v2.25.0\n_src_path: https://github.com/quokkify/project-toolkit.git\n", encoding="utf-8")
            self.assertTrue(fleet.canonicalize_answers_source(repository, "quokkify/ci-kit"))
            self.assertEqual(
                answers.read_text(encoding="utf-8"),
                "_commit: v2.25.0\n_src_path: https://github.com/quokkify/ci-kit.git\n",
            )

    def test_rejects_local_or_ambiguous_sources(self) -> None:
        for source in ("../project-toolkit", "/tmp/template", "github.com/quokkify/ci-kit"):
            with self.subTest(source=source):
                self.assertIsNone(fleet.normalize_template_source(source))

    def test_requires_answers_source(self) -> None:
        self.assertEqual(
            fleet.parse_template_source("_src_path: gh:quokkify/ci-kit\n"),
            "gh:quokkify/ci-kit",
        )
        with self.assertRaises(fleet.FleetUpdateError):
            fleet.parse_template_source("project_name: example\n")

    def test_rewrites_copier_shorthand_to_renovate_compatible_url(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            answers = repository / fleet.ANSWERS_FILE
            answers.write_text(
                "_commit: v2.8.2\n_src_path: gh:quokkify/ci-kit\nproject_name: example\n",
                encoding="utf-8",
            )
            self.assertTrue(
                fleet.canonicalize_answers_source(repository, "quokkify/ci-kit")
            )
            self.assertEqual(
                answers.read_text(encoding="utf-8"),
                "_commit: v2.8.2\n"
                "_src_path: https://github.com/quokkify/ci-kit.git\n"
                "project_name: example\n",
            )

    def test_leaves_canonical_copier_source_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            answers = repository / fleet.ANSWERS_FILE
            content = "_commit: v2.8.2\n_src_path: https://github.com/quokkify/ci-kit.git\n"
            answers.write_text(content, encoding="utf-8")
            self.assertFalse(
                fleet.canonicalize_answers_source(repository, "quokkify/ci-kit")
            )
            self.assertEqual(answers.read_text(encoding="utf-8"), content)

    def test_rejects_duplicate_copier_source_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            answers = repository / fleet.ANSWERS_FILE
            answers.write_text(
                "_src_path: gh:quokkify/ci-kit\n"
                "_src_path: gh:quokkify/ci-kit\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(fleet.FleetUpdateError, "exactly one"):
                fleet.canonicalize_answers_source(repository, "quokkify/ci-kit")

    def test_rejects_symlinked_answers_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            target = repository / "target.yml"
            target.write_text(
                "_src_path: gh:quokkify/ci-kit\n",
                encoding="utf-8",
            )
            (repository / fleet.ANSWERS_FILE).symlink_to(target)
            with self.assertRaisesRegex(fleet.FleetUpdateError, "must not be a symlink"):
                fleet.canonicalize_answers_source(repository, "quokkify/ci-kit")
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "_src_path: gh:quokkify/ci-kit\n",
            )


class TemplateUpdateTests(TestCase):
    @skipUnless(shutil.which("copier"), "Copier is required for the legacy migration integration")
    def test_real_legacy_copier_update_without_validate_handoff_is_refused(self) -> None:
        """Exercise the actual old-template update, not only command construction.

        A legacy repository from before handed_off still relies on the legacy validate.yml, which the
        current template no longer renders, so the fleet update must stop before any push (CIK-LEGACY-006).
        """
        legacy_revision = "a8217fa983347a3dd10133ec51117f873485b138"
        components = (
            [{"type": "java", "path": "worker"},
             {"type": "python", "path": "123-worker"},
             {"type": "python", "path": "api/v1"},
             {"type": "python", "path": "api-v1"}]
            + [{"type": "python", "path": "api"} for _ in range(17)]
        )
        repository = fleet.Repository("quokkify/legacy-fixture", "main")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            data = root / "components.yml"
            generated = root / "generated"
            data.write_text(yaml.safe_dump({"components": components}), encoding="utf-8")
            legacy_template = root / "legacy-template"
            subprocess.run(["git", "clone", "-q", str(ROOT), str(legacy_template)], check=True)
            subprocess.run(
                ["git", "checkout", "--quiet", "--detach", legacy_revision],
                cwd=legacy_template,
                check=True,
            )
            legacy_env = os.environ.copy()
            cache_home = root / "home"
            cache_home.mkdir()
            legacy_env.update(
                {
                    "HOME": str(cache_home),
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": f"url.file://{legacy_template}/.insteadOf",
                    "GIT_CONFIG_VALUE_0": "https://github.com/quokkify/ci-kit.git",
                }
            )
            subprocess.run(
                [
                    "copier", "copy", "--trust", "--defaults", "--vcs-ref", legacy_revision,
                    "--data-file", str(data),
                    "https://github.com/quokkify/ci-kit.git", str(source),
                ], check=True, text=True, capture_output=True, env=legacy_env,
            )
            subprocess.run(["git", "init", "-q"], cwd=source, check=True)
            subprocess.run(["git", "add", "--all"], cwd=source, check=True)
            subprocess.run(
                ["git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "legacy fixture"], cwd=source, check=True,
            )
            answers = (source / fleet.ANSWERS_FILE).read_text(encoding="utf-8")
            # Keep the destination's canonical Copier source identifier so the
            # production audit is exercised, but route that source to a local
            # clone containing this exact candidate revision.  The public remote
            # cannot be expected to contain an unpushed merge commit.
            candidate_source = root / "candidate-source"
            subprocess.run(["git", "clone", "-q", str(ROOT), str(candidate_source)], check=True)
            current_revision = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=candidate_source, check=True,
                text=True, capture_output=True,
            ).stdout.strip()
            integration_env = os.environ.copy()
            update_home = root / "update-home"
            update_home.mkdir()
            integration_env.update(
                {
                    "HOME": str(update_home),
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": f"url.file://{candidate_source}/.insteadOf",
                    "GIT_CONFIG_VALUE_0": "https://github.com/quokkify/ci-kit.git",
                }
            )

            def clone_fixture(_: fleet.Repository, destination: Path, *, env: dict[str, str]) -> None:
                del env
                shutil.copytree(source, destination)

            with mock.patch.object(fleet, "fetch_answers_files", return_value={"legacy": answers}), \
                 mock.patch.object(fleet, "clone_repository", side_effect=clone_fixture), \
                 mock.patch.object(fleet, "push_automation_branch") as push_mock, \
                 mock.patch.object(fleet, "ensure_pull_request", return_value="https://example.invalid/pr") as pr_mock:
                with self.assertRaises(fleet.RepositoryProcessError) as raised:
                    fleet.process_repository(
                        repository, expected_template="quokkify/ci-kit",
                        branch="automation/copier-template-update", dry_run=False,
                        template_ref=current_revision, env=integration_env, workspace=generated,
                    )

            self.assertIn("CIK-LEGACY-006", str(raised.exception))
            push_mock.assert_not_called()
            pr_mock.assert_not_called()

    @mock.patch.object(fleet, "changed_paths", return_value=[])
    @mock.patch.object(fleet, "canonicalize_answers_source")
    @mock.patch.object(fleet, "run")
    def test_migrates_legacy_components_without_replacing_topology(
        self,
        run_mock: mock.Mock,
        _: mock.Mock,
        __: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            answers = repository / fleet.ANSWERS_FILE
            original = (
                "components:\n"
                "  - type: java\n"
                "    path: worker\n"
                "_src_path: https://github.com/quokkify/ci-kit.git\n"
            )
            answers.write_text(original, encoding="utf-8")
            fleet.update_template(
                repository,
                template_source="quokkify/ci-kit",
                template_ref="v2.21.5",
                env={},
            )
            # The migration payload is passed to Copier without dirtying the
            # checkout; Copier writes the migrated answers after a successful
            # update.  A mocked runner must therefore leave the source intact.
            self.assertEqual(answers.read_text(encoding="utf-8"), original)
            run_mock.assert_called_once()
            command = run_mock.call_args.args[0]
            self.assertIn("--vcs-ref", command)
            self.assertIn("gitleaks=true", command)
            data_index = command.index("--data")
            self.assertEqual(
                yaml.safe_load(command[data_index + 1].removeprefix("components=")),
                [{"type": "java", "path": "worker", "id": "worker-java", "name": "Worker Java"}],
            )

    @mock.patch.object(fleet, "changed_paths", return_value=[])
    @mock.patch.object(fleet, "canonicalize_answers_source")
    @mock.patch.object(fleet, "run")
    def test_migrates_numeric_leading_legacy_path_to_valid_identity(
        self,
        run_mock: mock.Mock,
        _: mock.Mock,
        __: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / fleet.ANSWERS_FILE).write_text(
                "components:\n  - type: python\n    path: 123-worker\n"
                "_src_path: https://github.com/quokkify/ci-kit.git\n",
                encoding="utf-8",
            )
            fleet.update_template(
                repository,
                template_source="quokkify/ci-kit",
                template_ref="v2.21.5",
                env={},
            )

        command = run_mock.call_args.args[0]
        data_index = command.index("--data")
        self.assertEqual(
            yaml.safe_load(command[data_index + 1].removeprefix("components=")),
            [{
                "type": "python",
                "path": "123-worker",
                "id": "component-123-worker-python",
                "name": "123 Worker Python",
            }],
        )

    @mock.patch.object(fleet, "changed_paths", return_value=[])
    @mock.patch.object(fleet, "canonicalize_answers_source")
    @mock.patch.object(fleet, "run")
    def test_migrates_normalization_collisions_to_distinct_stable_identities(
        self,
        run_mock: mock.Mock,
        _: mock.Mock,
        __: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / fleet.ANSWERS_FILE).write_text(
                "components:\n"
                "  - type: python\n"
                "    path: api/v1\n"
                "  - type: python\n"
                "    path: api-v1\n"
                "_src_path: https://github.com/quokkify/ci-kit.git\n",
                encoding="utf-8",
            )
            fleet.update_template(
                repository,
                template_source="quokkify/ci-kit",
                template_ref="v2.21.5",
                env={},
            )

        command = run_mock.call_args.args[0]
        data_index = command.index("--data")
        migrated = yaml.safe_load(command[data_index + 1].removeprefix("components="))
        self.assertEqual(migrated[0]["id"], "api-v1-python")
        self.assertRegex(migrated[1]["id"], r"^api-v1-python-[0-9a-f]{8}$")
        self.assertNotEqual(migrated[0]["id"], migrated[1]["id"])
        self.assertEqual([component["path"] for component in migrated], ["api/v1", "api-v1"])

    @mock.patch.object(fleet, "changed_paths", return_value=[])
    @mock.patch.object(fleet, "canonicalize_answers_source")
    @mock.patch.object(fleet, "run")
    def test_migrates_repeated_legacy_components_with_bounded_unique_ids(
        self,
        run_mock: mock.Mock,
        _: mock.Mock,
        __: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            components = [{"type": "python", "path": "api"} for _ in range(17)]
            (repository / fleet.ANSWERS_FILE).write_text(
                yaml.safe_dump(
                    {
                        "components": components,
                        "_src_path": "https://github.com/quokkify/ci-kit.git",
                    }
                ),
                encoding="utf-8",
            )
            fleet.update_template(
                repository,
                template_source="quokkify/ci-kit",
                template_ref="v2.21.5",
                env={},
            )

        command = run_mock.call_args.args[0]
        data_index = command.index("--data")
        migrated = yaml.safe_load(command[data_index + 1].removeprefix("components="))
        ids = [component["id"] for component in migrated]
        self.assertEqual(len(ids), 17)
        self.assertEqual(len(set(ids)), 17)
        self.assertTrue(all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", value) for value in ids))
        self.assertEqual([component["path"] for component in migrated], ["api"] * 17)

    @mock.patch.object(fleet, "changed_paths", return_value=[])
    @mock.patch.object(fleet, "canonicalize_answers_source")
    @mock.patch.object(fleet, "run")
    def test_canonicalizes_source_after_clean_copier_update(
        self,
        run_mock: mock.Mock,
        canonicalize_mock: mock.Mock,
        _: mock.Mock,
    ) -> None:
        events: list[str] = []
        run_mock.side_effect = lambda *args, **kwargs: events.append("copier-update")
        canonicalize_mock.side_effect = lambda *args, **kwargs: events.append("canonicalize")

        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / fleet.ANSWERS_FILE).write_text(
                "_src_path: https://github.com/quokkify/ci-kit.git\n",
                encoding="utf-8",
            )
            fleet.update_template(
                repository,
                template_source="quokkify/ci-kit",
                template_ref="v2.8.1",
                env={},
            )

        self.assertEqual(events, ["copier-update", "canonicalize"])
        copier_command = run_mock.call_args.args[0]
        self.assertIn("--vcs-ref", copier_command)
        self.assertIn("v2.8.1", copier_command)
        self.assertIn("--data", copier_command)
        self.assertIn("toolkit_version=v2.8.1", copier_command)

    def test_seeds_single_manifest_only_after_clean_copier_update(self) -> None:
        events: list[str] = []
        with tempfile.TemporaryDirectory() as temporary:
            repository_path = Path(temporary)
            (repository_path / fleet.ANSWERS_FILE).write_text(
                "_src_path: https://github.com/quokkify/ci-kit.git\n",
                encoding="utf-8",
            )
            with mock.patch.object(
                fleet, "run", side_effect=lambda *args, **kwargs: events.append("copier-update")
            ), mock.patch.object(
                fleet, "seed_single_release_manifest",
                side_effect=lambda *args, **kwargs: events.append("seed-manifest"),
            ), mock.patch.object(
                fleet, "ensure_release_dependency_sections",
                side_effect=lambda *args, **kwargs: events.append("release-sections"),
            ), mock.patch.object(fleet, "restore_answers_format_if_semantically_equal"), mock.patch.object(
                fleet, "canonicalize_answers_source"
            ), mock.patch.object(fleet, "changed_paths", return_value=[]):
                fleet.update_template(
                    repository_path,
                    template_source="quokkify/ci-kit",
                    template_ref="v2.8.1",
                    env={},
                    repository=fleet.Repository("quokkify/example", "main"),
                )
        self.assertEqual(events, ["copier-update", "seed-manifest", "release-sections"])

        original = (
            "_commit: v2.8.2\n"
            "_src_path: https://github.com/quokkify/ci-kit.git\n"
            "renovate_presets:\n"
            "  - default\n"
            "  - github-actions\n"
        )
        copier_serialized = original.replace("  - ", "- ")
        with tempfile.TemporaryDirectory() as temporary:
            answers = Path(temporary) / fleet.ANSWERS_FILE
            answers.write_text(copier_serialized, encoding="utf-8")

            fleet.restore_answers_format_if_semantically_equal(answers, original)

            self.assertEqual(answers.read_text(encoding="utf-8"), original)

    def test_keeps_copier_answers_when_semantics_changed(self) -> None:
        original = "_commit: v2.8.1\nrenovate_presets:\n  - default\n"
        updated = "_commit: v2.8.2\nrenovate_presets:\n- default\n- github-actions\n"
        with tempfile.TemporaryDirectory() as temporary:
            answers = Path(temporary) / fleet.ANSWERS_FILE
            answers.write_text(updated, encoding="utf-8")

            fleet.restore_answers_format_if_semantically_equal(answers, original)

            self.assertEqual(
                answers.read_text(encoding="utf-8"),
                "_commit: v2.8.2\nrenovate_presets:\n  - default\n  - github-actions\n",
            )

    @mock.patch.object(fleet, "gh_json", return_value={"tagName": "v2.10.1"})
    def test_resolves_latest_release_when_template_ref_is_omitted(
        self,
        gh_json_mock: mock.Mock,
    ) -> None:
        resolved = fleet.resolve_template_ref("quokkify/ci-kit", None, env={})

        self.assertEqual(resolved, "v2.10.1")
        gh_json_mock.assert_called_once_with(
            [
                "release",
                "view",
                "--repo",
                "quokkify/ci-kit",
                "--json",
                "tagName",
            ],
            env={},
        )

    @mock.patch.object(fleet, "gh_json", return_value={"tagName": "v02.10.1"})
    def test_rejects_latest_release_with_leading_zero(
        self,
        _: mock.Mock,
    ) -> None:
        with self.assertRaisesRegex(fleet.FleetUpdateError, "exact vMAJOR.MINOR.PATCH"):
            fleet.resolve_template_ref("quokkify/ci-kit", None, env={})

    def test_keeps_explicit_non_release_template_ref_for_preview(self) -> None:
        self.assertEqual(
            fleet.resolve_template_ref("quokkify/ci-kit", "feature/allure", env={}),
            "feature/allure",
        )

    @mock.patch.object(fleet, "gh_json", return_value={"sha": "a" * 40})
    def test_resolves_release_tag_to_its_commit(self, gh_json_mock: mock.Mock) -> None:
        resolved = fleet.resolve_template_commit(
            "quokkify/ci-kit", "v2.19.2", env={}
        )

        self.assertEqual(resolved, "a" * 40)
        gh_json_mock.assert_called_once_with(
            [
                "api",
                "repos/quokkify/ci-kit/commits/v2.19.2",
                "--jq",
                "{sha: .sha}",
            ],
            env={},
        )

    @mock.patch.object(fleet, "gh_json", return_value={"sha": "not-a-commit"})
    def test_rejects_a_tag_that_does_not_resolve_to_a_commit(self, _: mock.Mock) -> None:
        with self.assertRaisesRegex(fleet.FleetUpdateError, "did not resolve to a commit"):
            fleet.resolve_template_commit("quokkify/ci-kit", "v2.19.2", env={})


class ProjectOwnedToolkitRefTests(TestCase):
    """The consumer contract accepts a tag, or a digest whose comment names that tag."""

    NEW_COMMIT = "b" * 40
    OLD_COMMIT = "fd12c1b07b08c7f873942abd5e8fedbfa26b4583"

    def workflows(self, root: Path) -> Path:
        directory = root / ".github" / "workflows"
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def test_rewrites_a_digest_pin_with_its_release_comment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = self.workflows(repository) / "publish-maven-central.yml"
            workflow.write_text(
                "        uses: quokkify/ci-kit/actions/setup-java-gradle@"
                f"{self.OLD_COMMIT} # v2.19.1\n",
                encoding="utf-8",
            )

            changed = fleet.bump_project_owned_toolkit_refs(
                repository, "v2.19.2", resolve_commit=lambda: self.NEW_COMMIT
            )

            self.assertEqual(changed, [".github/workflows/publish-maven-central.yml"])
            self.assertEqual(
                workflow.read_text(encoding="utf-8"),
                "        uses: quokkify/ci-kit/actions/setup-java-gradle@"
                f"{self.NEW_COMMIT} # v2.19.2\n",
            )

    def test_rewrites_a_digest_pin_that_renovate_moved_ahead_of_the_answers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = self.workflows(repository) / "ci.yml"
            workflow.write_text(
                "      uses: quokkify/ci-kit/.github/workflows/java-ci.yml@"
                f"{self.OLD_COMMIT} # v2.20.0\n",
                encoding="utf-8",
            )

            fleet.bump_project_owned_toolkit_refs(
                repository, "v2.19.2", resolve_commit=lambda: self.NEW_COMMIT
            )

            self.assertEqual(
                workflow.read_text(encoding="utf-8"),
                "      uses: quokkify/ci-kit/.github/workflows/java-ci.yml@"
                f"{self.NEW_COMMIT} # v2.19.2\n",
            )

    def test_still_rewrites_plain_tag_references(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = self.workflows(repository) / "ci.yaml"
            workflow.write_text(
                "      uses: quokkify/ci-kit/.github/workflows/node-ci.yml@v2.19.1\n",
                encoding="utf-8",
            )

            changed = fleet.bump_project_owned_toolkit_refs(
                repository, "v2.19.2", resolve_commit=lambda: self.NEW_COMMIT
            )

            self.assertEqual(changed, [".github/workflows/ci.yaml"])
            self.assertEqual(
                workflow.read_text(encoding="utf-8"),
                "      uses: quokkify/ci-kit/.github/workflows/node-ci.yml@v2.19.2\n",
            )

    def test_leaves_handed_off_module_owned_workflows_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            directory = self.workflows(repository)
            caller = "      uses: quokkify/ci-kit/.github/workflows/validate.yml@v2.19.1\n"
            (directory / "validate.yml").write_text(caller, encoding="utf-8")
            (directory / "ci.yml").write_text(caller, encoding="utf-8")
            (repository / fleet.ANSWERS_FILE).write_text(
                "_commit: v2.19.1\n_src_path: gh:quokkify/ci-kit\nhanded_off:\n  - validate\n", encoding="utf-8"
            )

            changed = fleet.bump_project_owned_toolkit_refs(repository, "v2.19.2")

            self.assertEqual(changed, [".github/workflows/ci.yml"])
            self.assertEqual((directory / "validate.yml").read_text(encoding="utf-8"), caller)

    def test_leaves_a_digest_pin_without_a_release_comment_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = self.workflows(repository) / "ci.yml"
            original = (
                "      uses: quokkify/ci-kit/actions/setup-node@"
                f"{self.OLD_COMMIT}\n"
            )
            workflow.write_text(original, encoding="utf-8")

            changed = fleet.bump_project_owned_toolkit_refs(
                repository, "v2.19.2", resolve_commit=lambda: self.NEW_COMMIT
            )

            self.assertEqual(changed, [])
            self.assertEqual(workflow.read_text(encoding="utf-8"), original)

    def test_leaves_a_foreign_action_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = self.workflows(repository) / "ci.yml"
            original = (
                "      uses: actions/checkout@"
                f"{self.OLD_COMMIT} # v2.19.1\n"
            )
            workflow.write_text(original, encoding="utf-8")

            changed = fleet.bump_project_owned_toolkit_refs(
                repository, "v2.19.2", resolve_commit=lambda: self.NEW_COMMIT
            )

            self.assertEqual(changed, [])
            self.assertEqual(workflow.read_text(encoding="utf-8"), original)

    def test_is_idempotent_when_every_reference_already_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = self.workflows(repository) / "ci.yml"
            original = (
                "      uses: quokkify/ci-kit/actions/setup-java-gradle@"
                f"{self.NEW_COMMIT} # v2.19.2\n"
            )
            workflow.write_text(original, encoding="utf-8")

            changed = fleet.bump_project_owned_toolkit_refs(
                repository, "v2.19.2", resolve_commit=lambda: self.NEW_COMMIT
            )

            self.assertEqual(changed, [])
            self.assertEqual(workflow.read_text(encoding="utf-8"), original)

    def test_keeps_digest_pins_untouched_without_a_resolved_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = self.workflows(repository) / "ci.yml"
            original = (
                "      uses: quokkify/ci-kit/actions/setup-python@"
                f"{self.OLD_COMMIT} # v2.19.1\n"
            )
            workflow.write_text(original, encoding="utf-8")

            changed = fleet.bump_project_owned_toolkit_refs(repository, "v2.19.2")

            self.assertEqual(changed, [])
            self.assertEqual(workflow.read_text(encoding="utf-8"), original)


class TemplateInventoryTests(TestCase):
    def write_baseline(self, repository: Path) -> None:
        for relative_path in fleet.BASELINE_PATHS:
            path = repository / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("generated\n", encoding="utf-8")

    def test_infers_java_component_from_workflow_when_answers_are_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / ".github/workflows/java.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  build:\n    uses: quokkify/ci-kit/.github/workflows/java-ci.yml@v2.21.2\n"
                "    with:\n      working-directory: backend\n",
                encoding="utf-8",
            )
            inventory = fleet.inventory_from_answers("components: []\n", repository)
        self.assertEqual(inventory.components, ("java:backend",))

    def test_infers_components_when_answers_key_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / ".github/workflows/ci.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  test:\n    uses: quokkify/ci-kit/.github/workflows/python-ci.yml@v2\n"
                "    with:\n      working-directory: backend\n",
                encoding="utf-8",
            )
            inventory = fleet.inventory_from_answers("docker: false\n", repository)
        self.assertEqual(inventory.components, ("python:backend",))

    def test_keeps_mixed_job_paths_paired_to_their_language(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / ".github/workflows/ci.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  backend:\n    steps:\n      - uses: actions/setup-python@v5\n"
                "        with:\n          working-directory: backend\n"
                "  frontend:\n    steps:\n      - uses: actions/setup-node@v4\n"
                "        with:\n          working-directory: frontend\n",
                encoding="utf-8",
            )
            inventory = fleet.inventory_from_answers("components: []\n", repository)
        self.assertEqual(inventory.components, ("node:frontend", "python:backend"))

    def test_custom_release_please_workflow_is_not_reported_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / ".github/workflows/custom-release.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  release:\n    steps:\n      - uses: googleapis/release-please-action@v4\n",
                encoding="utf-8",
            )
            self.write_baseline(repository)
            inventory = fleet.inventory_from_answers("release_please: true\n", repository)
        self.assertEqual(inventory.release_please, "custom")
        self.assertFalse(fleet.inventory_has_mismatch(inventory))

    def test_comment_only_release_please_action_is_reported_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / ".github/workflows/notes.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "name: notes\n# uses: googleapis/release-please-action@v4\n",
                encoding="utf-8",
            )
            self.write_baseline(repository)
            inventory = fleet.inventory_from_answers("release_please: true\n", repository)
        self.assertEqual(inventory.release_please, "missing")
        self.assertTrue(fleet.inventory_has_mismatch(inventory))

    def test_quoted_release_please_text_is_not_executable_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / ".github/workflows/notes.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                'jobs:\n  notes:\n    steps:\n      - run: "uses: googleapis/release-please-action@v4"\n',
                encoding="utf-8",
            )
            self.write_baseline(repository)
            inventory = fleet.inventory_from_answers("release_please: true\n", repository)
        self.assertEqual(inventory.release_please, "missing")

    def test_reports_configured_features_and_materialized_outputs(self) -> None:
        raw_answers = (
            "_commit: v2.8.2\n"
            "components:\n"
            "  - type: python\n"
            "    path: backend\n"
            "  - type: node\n"
            "    path: frontend\n"
            "docker: true\n"
            "release_please: true\n"
            "renovate: false\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            self.write_baseline(repository)
            release = repository / fleet.FEATURE_PATHS["release_please"]
            release.parent.mkdir(parents=True, exist_ok=True)
            release.write_text("generated\n", encoding="utf-8")
            renovate = repository / fleet.FEATURE_PATHS["renovate"]
            renovate.parent.mkdir(parents=True, exist_ok=True)
            renovate.write_text("custom\n", encoding="utf-8")

            inventory = fleet.inventory_from_answers(raw_answers, repository)

        self.assertEqual(inventory.commit, "v2.8.2")
        self.assertIsNone(inventory.target_commit)
        self.assertEqual(inventory.components, ("python:backend", "node:frontend"))
        self.assertEqual(inventory.baseline, "2/2")
        self.assertEqual(inventory.missing_baseline, ())
        self.assertEqual(inventory.docker, "enabled")
        self.assertEqual(inventory.codeql, "unknown")
        self.assertEqual(inventory.allure_report, "unknown")
        self.assertEqual(inventory.release_please, "enabled")
        self.assertEqual(inventory.renovate, "custom")
        self.assertFalse(fleet.inventory_has_mismatch(inventory))

    def test_codeql_is_an_optional_output_rather_than_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            self.write_baseline(repository)

            disabled = fleet.inventory_from_answers(
                "components: []\ncodeql: false\n", repository
            )

            codeql = repository / fleet.FEATURE_PATHS["codeql"]
            codeql.parent.mkdir(parents=True, exist_ok=True)
            codeql.write_text("name: CodeQL\n", encoding="utf-8")
            enabled = fleet.inventory_from_answers(
                "components: []\ncodeql: true\n", repository
            )
            kept_after_opting_out = fleet.inventory_from_answers(
                "components: []\ncodeql: false\n", repository
            )

            codeql.unlink()
            missing = fleet.inventory_from_answers(
                "components: []\ncodeql: true\n", repository
            )

        self.assertEqual(disabled.codeql, "disabled")
        self.assertEqual(disabled.baseline, "2/2")
        self.assertNotIn(fleet.FEATURE_PATHS["codeql"], disabled.missing_baseline)
        self.assertFalse(fleet.inventory_has_mismatch(disabled))

        self.assertEqual(enabled.codeql, "enabled")
        self.assertFalse(fleet.inventory_has_mismatch(enabled))

        self.assertEqual(kept_after_opting_out.codeql, "custom")

        self.assertEqual(missing.codeql, "missing")
        self.assertTrue(fleet.inventory_has_mismatch(missing))

    def test_distinguishes_missing_disabled_and_unknown_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            inventory = fleet.inventory_from_answers(
                "components: []\nrelease_please: true\nrenovate: false\n",
                repository,
            )

        self.assertEqual(inventory.commit, "unknown")
        self.assertEqual(inventory.components, ("none",))
        self.assertEqual(inventory.docker, "unknown")
        self.assertEqual(inventory.allure_report, "unknown")
        self.assertEqual(inventory.release_please, "missing")
        self.assertEqual(inventory.renovate, "disabled")
        self.assertTrue(fleet.inventory_has_mismatch(inventory))

    def test_allure_requires_generated_workflow_and_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / fleet.FEATURE_PATHS["allure_report"]
            config = repository / fleet.ALLURE_CONFIG_PATH
            workflow.parent.mkdir(parents=True)
            workflow.write_text("name: Allure report\n", encoding="utf-8")

            incomplete = fleet.inventory_from_answers(
                "components: []\nallure_report: true\n",
                repository,
            )
            custom = fleet.inventory_from_answers(
                "components: []\nallure_report: false\n",
                repository,
            )
            config.parent.mkdir(parents=True)
            config.write_text("export default {};\n", encoding="utf-8")
            still_incomplete = fleet.inventory_from_answers(
                "components: []\nallure_report: true\n",
                repository,
            )
            extractor = repository / fleet.ALLURE_EXTRACTOR_PATH
            extractor.write_text("# trusted extractor\n", encoding="utf-8")
            enabled = fleet.inventory_from_answers(
                "components: []\nallure_report: true\n",
                repository,
            )

        self.assertEqual(incomplete.allure_report, "missing")
        self.assertTrue(fleet.inventory_has_mismatch(incomplete))
        self.assertEqual(custom.allure_report, "custom")
        self.assertEqual(still_incomplete.allure_report, "missing")
        self.assertEqual(enabled.allure_report, "enabled")

    def test_allure_accepts_complete_custom_helper_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / fleet.FEATURE_PATHS["allure_report"]
            config = repository / "tools/allure/allurerc.mjs"
            extractor = repository / "tools/allure/safe_extract.py"
            workflow.parent.mkdir(parents=True)
            config.parent.mkdir(parents=True)
            workflow.write_text(
                "name: Allure report\n\n"
                "jobs:\n  report:\n    steps:\n"
                "      - run: python tools/allure/safe_extract.py\n"
                "        with:\n"
                "          config-file: tools/allure/allurerc.mjs\n",
                encoding="utf-8",
            )
            config.write_text("export default {};\n", encoding="utf-8")
            extractor.write_text("# trusted extractor\n", encoding="utf-8")
            for path in fleet.BASELINE_PATHS:
                baseline = repository / path
                baseline.parent.mkdir(parents=True, exist_ok=True)
                baseline.write_text("name: baseline\n", encoding="utf-8")

            inventory = fleet.inventory_from_answers(
                "components: []\nallure_report: true\n",
                repository,
            )

        self.assertEqual(inventory.allure_report, "custom")
        self.assertFalse(fleet.inventory_has_mismatch(inventory))

    def test_shell_extractor_preserves_comments_and_operator_continuations(self) -> None:
        expected = {"tools/allure/safe_extract.py"}
        for run in (
            "# comment\npython tools/allure/safe_extract.py",
            "echo setup # comment\npython tools/allure/safe_extract.py",
            "echo setup &&\npython tools/allure/safe_extract.py",
            "printf x |\npython tools/allure/safe_extract.py",
            "echo setup;# comment\npython tools/allure/safe_extract.py",
            ":;# cat <<EOF\npython tools/allure/safe_extract.py",
            "echo $((1 << 2))\npython tools/allure/safe_extract.py",
        ):
            with self.subTest(run=run):
                self.assertEqual(fleet._shell_extractor_paths(run), expected)

    def test_shell_extractor_ignores_heredoc_marker_in_comments(self) -> None:
        for run in (
            "# cat <<EOF\npython tools/allure/safe_extract.py",
            ":;# cat <<EOF\npython tools/allure/safe_extract.py",
        ):
            with self.subTest(run=run):
                self.assertEqual(
                    fleet._shell_extractor_paths(run),
                    {"tools/allure/safe_extract.py"},
                )

    def test_allure_accepts_complete_helpers_in_nonstandard_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / ".github/workflows/report.yml"
            config = repository / "tools/allure/allurerc.mjs"
            extractor = repository / "tools/allure/safe_extract.py"
            workflow.parent.mkdir(parents=True)
            config.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  report:\n    steps:\n      - run: python tools/allure/safe_extract.py\n"
                "        with:\n          config-file: tools/allure/allurerc.mjs\n",
                encoding="utf-8",
            )
            config.write_text("export default {};\n", encoding="utf-8")
            extractor.write_text("# extractor\n", encoding="utf-8")
            inventory = fleet.inventory_from_answers(
                "components: []\nallure_report: true\n", repository
            )
        self.assertEqual(inventory.allure_report, "custom")

    def test_allure_accepts_attached_shell_separator_before_extractor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / ".github/workflows/report.yml"
            config = repository / "tools/allure/allurerc.mjs"
            extractor = repository / "tools/allure/safe_extract.py"
            workflow.parent.mkdir(parents=True)
            config.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  report:\n    steps:\n"
                "      - run: cd tools; python ../tools/allure/safe_extract.py\n"
                "        with:\n          config-file: tools/allure/allurerc.mjs\n",
                encoding="utf-8",
            )
            config.write_text("export default {};\n", encoding="utf-8")
            extractor.write_text("# extractor\n", encoding="utf-8")
            self.assertTrue(fleet.has_custom_allure_outputs(repository))

    def test_allure_ignores_extractor_in_heredoc_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / ".github/workflows/report.yml"
            config = repository / "tools/allure/allurerc.mjs"
            extractor = repository / "tools/allure/safe_extract.py"
            workflow.parent.mkdir(parents=True)
            config.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  report:\n    steps:\n"
                "      - run: |\n"
                "          cat <<'EOF'\n"
                "          python tools/allure/safe_extract.py\n"
                "          EOF\n"
                "        with:\n          config-file: tools/allure/allurerc.mjs\n",
                encoding="utf-8",
            )
            config.write_text("export default {};\n", encoding="utf-8")
            extractor.write_text("# extractor\n", encoding="utf-8")
            self.assertFalse(fleet.has_custom_allure_outputs(repository))

    def test_allure_finds_extractor_in_multiline_python_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / ".github/workflows/report.yml"
            config = repository / "tools/allure/allurerc.mjs"
            extractor = repository / "tools/allure/safe_extract.py"
            workflow.parent.mkdir(parents=True)
            config.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  report:\n    steps:\n"
                "      - run: |\n"
                "          python \\\n"
                "            tools/allure/safe_extract.py\n"
                "        with:\n          config-file: tools/allure/allurerc.mjs\n",
                encoding="utf-8",
            )
            config.write_text("export default {};\n", encoding="utf-8")
            extractor.write_text("# extractor\n", encoding="utf-8")
            self.assertTrue(fleet.has_custom_allure_outputs(repository))

    def test_allure_finds_extractor_after_newline_separated_command(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / ".github/workflows/report.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  report:\n    steps:\n"
                "      - run: |\n"
                "          echo setup\n"
                "          python tools/allure/safe_extract.py\n"
                "        with:\n          config-file: tools/allure/allurerc.mjs\n",
                encoding="utf-8",
            )
            (repository / "tools/allure").mkdir(parents=True)
            (repository / "tools/allure/allurerc.mjs").write_text("{}\n", encoding="utf-8")
            (repository / "tools/allure/safe_extract.py").write_text("# helper\n", encoding="utf-8")
            self.assertTrue(fleet.has_custom_allure_outputs(repository))

    def test_allure_ignores_quoted_semicolons_and_echo_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / ".github/workflows/report.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  report:\n    steps:\n"
                "      - run: echo \"python tools/allure/safe_extract.py; echo text\"\n"
                "        with:\n          config-file: tools/allure/allurerc.mjs\n",
                encoding="utf-8",
            )
            (repository / "tools/allure").mkdir(parents=True)
            (repository / "tools/allure/allurerc.mjs").write_text("{}\n", encoding="utf-8")
            (repository / "tools/allure/safe_extract.py").write_text("# helper\n", encoding="utf-8")
            self.assertFalse(fleet.has_custom_allure_outputs(repository))

    def test_allure_ignores_heredoc_body_but_finds_command_after_delimiter(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / ".github/workflows/report.yml"
            config = repository / "tools/allure/allurerc.mjs"
            extractor = repository / "tools/allure/safe_extract.py"
            workflow.parent.mkdir(parents=True)
            config.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  report:\n    steps:\n"
                "      - run: |\n"
                "          cat <<'EOF'\n"
                "          python tools/allure/safe_extract.py\n"
                "          EOF\n"
                "          python tools/allure/safe_extract.py\n"
                "        with:\n          config-file: tools/allure/allurerc.mjs\n",
                encoding="utf-8",
            )
            config.write_text("export default {};\n", encoding="utf-8")
            extractor.write_text("# extractor\n", encoding="utf-8")
            self.assertTrue(fleet.has_custom_allure_outputs(repository))

    def test_allure_finds_extractor_after_here_string(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / ".github/workflows/report.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  report:\n    steps:\n"
                "      - run: |\n"
                "          cat <<< text\n"
                "          python tools/allure/safe_extract.py\n"
                "        with:\n          config-file: tools/allure/allurerc.mjs\n",
                encoding="utf-8",
            )
            (repository / "tools/allure").mkdir(parents=True)
            (repository / "tools/allure/allurerc.mjs").write_text("{}\n", encoding="utf-8")
            (repository / "tools/allure/safe_extract.py").write_text("# helper\n", encoding="utf-8")
            self.assertTrue(fleet.has_custom_allure_outputs(repository))

    def test_allure_ignores_extractor_in_each_of_multiple_heredocs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / ".github/workflows/report.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  report:\n    steps:\n"
                "      - run: |\n"
                "          cat <<FIRST <<SECOND\n"
                "          FIRST body python tools/allure/safe_extract.py\n"
                "          FIRST\n"
                "          SECOND body python tools/allure/safe_extract.py\n"
                "          SECOND\n"
                "        with:\n          config-file: tools/allure/allurerc.mjs\n",
                encoding="utf-8",
            )
            (repository / "tools/allure").mkdir(parents=True)
            (repository / "tools/allure/allurerc.mjs").write_text("{}\n", encoding="utf-8")
            (repository / "tools/allure/safe_extract.py").write_text("# helper\n", encoding="utf-8")
            self.assertFalse(fleet.has_custom_allure_outputs(repository))

    def test_allure_handles_shell_quoted_delimiters_arithmetic_and_multiline_quotes(self) -> None:
        runs = (
            "cat <<\\EOF\n"
            "body\n"
            "EOF\n"
            "python tools/allure/safe_extract.py\n",
            "((1 << 2))\n"
            "python tools/allure/safe_extract.py\n",
            "printf %s 'literal\n"
            "<<EOF\n"
            "still literal'\n"
            "python tools/allure/safe_extract.py\n",
        )
        for run in runs:
            with self.subTest(run=run):
                with tempfile.TemporaryDirectory() as temporary:
                    repository = Path(temporary)
                    workflow = repository / ".github/workflows/report.yml"
                    workflow.parent.mkdir(parents=True)
                    workflow.write_text(
                        "jobs:\n  report:\n    steps:\n"
                        "      - run: |\n"
                        + "".join(f"          {line}" for line in run.splitlines(keepends=True))
                        + "        with:\n          config-file: tools/allure/allurerc.mjs\n",
                        encoding="utf-8",
                    )
                    (repository / "tools/allure").mkdir(parents=True)
                    (repository / "tools/allure/allurerc.mjs").write_text("{}\n", encoding="utf-8")
                    (repository / "tools/allure/safe_extract.py").write_text("# helper\n", encoding="utf-8")
                    self.assertTrue(fleet.has_custom_allure_outputs(repository))

    def test_allure_finds_command_after_mixed_quoted_heredoc_delimiters(self) -> None:
        runs = (
            'cat <<E"OF"\nbody\nEOF\n',
            "cat <<'E'O'F'\nbody\nEOF\n",
            "cat <<E\\ OF\nbody\nE OF\n",
        )
        for run in runs:
            with self.subTest(run=run):
                with tempfile.TemporaryDirectory() as temporary:
                    repository = Path(temporary)
                    workflow = repository / ".github/workflows/report.yml"
                    workflow.parent.mkdir(parents=True)
                    workflow.write_text(
                        "jobs:\n  report:\n    steps:\n"
                        "      - run: |\n"
                        + "".join(f"          {line}" for line in (run + "python tools/allure/safe_extract.py\n").splitlines(keepends=True))
                        + "        with:\n          config-file: tools/allure/allurerc.mjs\n",
                        encoding="utf-8",
                    )
                    (repository / "tools/allure").mkdir(parents=True)
                    (repository / "tools/allure/allurerc.mjs").write_text("{}\n", encoding="utf-8")
                    (repository / "tools/allure/safe_extract.py").write_text("# helper\n", encoding="utf-8")
                    self.assertTrue(fleet.has_custom_allure_outputs(repository))

    def test_allure_ignores_extractor_in_quoted_backslash_heredoc_data(self) -> None:
        runs = (
            "cat <<'E\\OF'\n"
            "python tools/allure/safe_extract.py\n"
            "EOF\n"
            "E\\OF\n",
            'cat <<"E\\qOF"\n'
            "python tools/allure/safe_extract.py\n"
            "EqOF\n"
            "E\\qOF\n",
        )
        for run in runs:
            with self.subTest(run=run):
                with tempfile.TemporaryDirectory() as temporary:
                    repository = Path(temporary)
                    workflow = repository / ".github/workflows/report.yml"
                    workflow.parent.mkdir(parents=True)
                    workflow.write_text(
                        "jobs:\n  report:\n    steps:\n"
                        "      - run: |\n"
                        + "".join(f"          {line}" for line in run.splitlines(keepends=True))
                        + "        with:\n          config-file: tools/allure/allurerc.mjs\n",
                        encoding="utf-8",
                    )
                    (repository / "tools/allure").mkdir(parents=True)
                    (repository / "tools/allure/allurerc.mjs").write_text("{}\n", encoding="utf-8")
                    (repository / "tools/allure/safe_extract.py").write_text("# helper\n", encoding="utf-8")
                    self.assertFalse(fleet.has_custom_allure_outputs(repository))

    def test_allure_handles_heredoc_delimiter_line_continuations(self) -> None:
        runs = (
            ("cat <<E\\\nOF\nbody\nEOF\npython tools/allure/safe_extract.py\n", True),
            ('cat <<"E\\\nOF"\npython tools/allure/safe_extract.py\nEOF\n', False),
        )
        for run, expected in runs:
            with self.subTest(run=run):
                with tempfile.TemporaryDirectory() as temporary:
                    repository = Path(temporary)
                    workflow = repository / ".github/workflows/report.yml"
                    workflow.parent.mkdir(parents=True)
                    workflow.write_text(
                        "jobs:\n  report:\n    steps:\n"
                        "      - run: |\n"
                        + "".join(f"          {line}" for line in run.splitlines(keepends=True))
                        + "        with:\n          config-file: tools/allure/allurerc.mjs\n",
                        encoding="utf-8",
                    )
                    (repository / "tools/allure").mkdir(parents=True)
                    (repository / "tools/allure/allurerc.mjs").write_text("{}\n", encoding="utf-8")
                    (repository / "tools/allure/safe_extract.py").write_text("# helper\n", encoding="utf-8")
                    self.assertEqual(fleet.has_custom_allure_outputs(repository), expected)

    def test_allure_ignores_extractor_in_unquoted_heredoc_body_continuation(self) -> None:
        run = (
            "cat <<EOF\n"
            "ignored\\\n"
            "EOF\n"
            "python tools/allure/safe_extract.py\n"
            "EOF\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / ".github/workflows/report.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  report:\n    steps:\n"
                "      - run: |\n"
                + "".join(f"          {line}" for line in run.splitlines(keepends=True))
                + "        with:\n          config-file: tools/allure/allurerc.mjs\n",
                encoding="utf-8",
            )
            (repository / "tools/allure").mkdir(parents=True)
            (repository / "tools/allure/allurerc.mjs").write_text("{}\n", encoding="utf-8")
            (repository / "tools/allure/safe_extract.py").write_text("# helper\n", encoding="utf-8")
            self.assertFalse(fleet.has_custom_allure_outputs(repository))

    def test_allure_finds_command_after_even_backslash_heredoc_body_line(self) -> None:
        run = "cat <<EOF\nignored\\\\\nEOF\npython tools/allure/safe_extract.py\n"
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / ".github/workflows/report.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  report:\n    steps:\n"
                "      - run: |\n"
                + "".join(f"          {line}" for line in run.splitlines(keepends=True))
                + "        with:\n          config-file: tools/allure/allurerc.mjs\n",
                encoding="utf-8",
            )
            (repository / "tools/allure").mkdir(parents=True)
            (repository / "tools/allure/allurerc.mjs").write_text("{}\n", encoding="utf-8")
            (repository / "tools/allure/safe_extract.py").write_text("# helper\n", encoding="utf-8")
            self.assertTrue(fleet.has_custom_allure_outputs(repository))

    def test_allure_finds_command_after_backslash_quoted_heredoc_delimiter(self) -> None:
        run = "cat <<\\EOF\nignored\\\nEOF\npython tools/allure/safe_extract.py\n"
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / ".github/workflows/report.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  report:\n    steps:\n"
                "      - run: |\n"
                + "".join(f"          {line}" for line in run.splitlines(keepends=True))
                + "        with:\n          config-file: tools/allure/allurerc.mjs\n",
                encoding="utf-8",
            )
            (repository / "tools/allure").mkdir(parents=True)
            (repository / "tools/allure/allurerc.mjs").write_text("{}\n", encoding="utf-8")
            (repository / "tools/allure/safe_extract.py").write_text("# helper\n", encoding="utf-8")
            self.assertTrue(fleet.has_custom_allure_outputs(repository))

    def test_workflow_defaults_run_directory_is_inherited_by_components(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / ".github/workflows/ci.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "defaults:\n  run:\n    working-directory: backend\n"
                "jobs:\n  test:\n    steps:\n"
                "      - uses: actions/setup-python@v5\n"
                "      - run: python -m pytest\n",
                encoding="utf-8",
            )
            components = fleet.inferred_components(repository)
        self.assertEqual(components, ("python:backend",))

    def test_allure_ignores_comments_and_quoted_run_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / ".github/workflows/report.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  report:\n    steps:\n"
                "      # run: python tools/safe_extract.py\n"
                "      - run: \"echo 'python tools/safe_extract.py config-file: tools/allure/allurerc.mjs'\"\n"
                "        with:\n          config-file: tools/allure/allurerc.mjs\n",
                encoding="utf-8",
            )
            (repository / "tools/allure").mkdir(parents=True)
            (repository / "tools/allure/allurerc.mjs").write_text("{}\n", encoding="utf-8")
            (repository / "tools/safe_extract.py").write_text("# helper\n", encoding="utf-8")
            inventory = fleet.inventory_from_answers("allure_report: true\n", repository)
        self.assertEqual(inventory.allure_report, "missing")

    def test_component_inference_uses_only_step_uses_and_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / ".github/workflows/ci.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  build:\n    steps:\n"
                "      - name: setup\n"
                "        env: {FAKE: setup-java}\n"
                "        with: {command: ./mvnw}\n"
                "      - uses: actions/setup-java@v4\n"
                "      - run: ./mvnw test\n"
                "        working-directory: server\n",
                encoding="utf-8",
            )
            components = fleet.inferred_components(repository)
        self.assertEqual(components, ("java:.", "java:server"))

    def test_markdown_reports_configuration_gap_count_once_and_json_aliases(self) -> None:
        inventory = fleet.TemplateInventory(
            commit="v1.0.0", target_commit=None, components=("none",), baseline="1/2",
            missing_baseline=(fleet.BASELINE_PATHS[0],), docker="unknown", codeql="unknown",
            allure_report="unknown", release_please="unknown", renovate="unknown",
        )
        result = fleet.Result("quokkify/example", "up-to-date", inventory=inventory)
        markdown = fleet.markdown_report([result], {"up-to-date": 1})
        report = fleet.json.loads(fleet.json_report([result], {"up-to-date": 1}))
        self.assertEqual(markdown.count("configuration gap"), 1)
        self.assertEqual(report["configuration_gaps"], report["configuration_mismatches"])

    def test_release_please_partial_evidence_is_missing(self) -> None:
        cases = ("# release-please\n", "release-please-config.json\n")
        for marker in cases:
            with self.subTest(marker=marker):
                with tempfile.TemporaryDirectory() as temporary:
                    repository = Path(temporary)
                    workflow = repository / ".github/workflows/custom.yml"
                    workflow.parent.mkdir(parents=True)
                    workflow.write_text(marker, encoding="utf-8")
                    if marker.startswith("release"):
                        (repository / "release-please-config.json").write_text(
                            "{}\n", encoding="utf-8"
                        )
                    inventory = fleet.inventory_from_answers(
                        "release_please: true\n", repository
                    )
                self.assertEqual(inventory.release_please, "missing")

    def test_release_please_custom_action_is_custom(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            workflow = repository / ".github/workflows/custom-release.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  release:\n    steps:\n      - uses: acme/release-please-action@v1\n",
                encoding="utf-8",
            )
            inventory = fleet.inventory_from_answers(
                "release_please: true\n", repository
            )
        self.assertEqual(inventory.release_please, "custom")

    def test_symlinked_template_outputs_are_reported_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            target = repository / "custom.yml"
            target.write_text("custom\n", encoding="utf-8")
            baseline = repository / fleet.BASELINE_PATHS[0]
            baseline.parent.mkdir(parents=True, exist_ok=True)
            baseline.symlink_to(target)
            release = repository / fleet.FEATURE_PATHS["release_please"]
            release.parent.mkdir(parents=True, exist_ok=True)
            release.symlink_to(target)

            inventory = fleet.inventory_from_answers(
                "components: []\nrelease_please: true\nrenovate: false\n",
                repository,
            )

        self.assertEqual(inventory.release_please, "missing")
        self.assertIn(".github/workflows/validate.yml", inventory.missing_baseline)

    def test_symlinked_parent_directory_does_not_materialize_outputs(self) -> None:
        raw_answers = (
            "_commit: v2.8.2\n"
            "_src_path: gh:quokkify/ci-kit\n"
            "release_please: true\n"
            "renovate: true\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "repository"
            outside = Path(temporary) / "outside"
            (outside / "workflows").mkdir(parents=True)
            (outside / "workflows" / "validate.yml").write_text("name: Validate\n")
            (outside / "workflows" / "gitleaks.yml").write_text("name: Gitleaks\n")
            (outside / "workflows" / "codeql.yml").write_text("name: CodeQL\n")
            (outside / "workflows" / "release.yml").write_text("name: Release\n")
            (outside / "renovate.json").write_text("{}\n")
            repository.mkdir()
            (repository / ".github").symlink_to(outside, target_is_directory=True)

            inventory = fleet.inventory_from_answers(raw_answers, repository)

        self.assertEqual(inventory.baseline, "0/2")
        self.assertEqual(inventory.release_please, "missing")
        self.assertEqual(inventory.renovate, "missing")
        self.assertEqual(set(inventory.missing_baseline), set(fleet.BASELINE_PATHS))

    def test_console_markdown_and_json_reports_share_the_inventory(self) -> None:
        inventory = fleet.TemplateInventory(
            commit="v2.8.2",
            target_commit="v2.9.0",
            components=("python:.",),
            baseline="2/2",
            missing_baseline=(),
            docker="disabled",
            codeql="enabled",
            allure_report="enabled",
            release_please="enabled",
            renovate="missing",
        )
        result = fleet.Result(
            "quokkify/example",
            "would-update",
            ".github/renovate.json",
            inventory,
        )
        counts = {"would-update": 1}

        console = "\n".join(fleet.console_lines(result))
        markdown = fleet.markdown_report([result], counts)
        report = fleet.json.loads(fleet.json_report([result], counts))

        self.assertIn("template=v2.8.2->v2.9.0 components=python:.", console)
        self.assertIn("renovate=missing", console)
        self.assertIn("| ⚠️ | [example](https://github.com/quokkify/example) | Missing template outputs: `.github/renovate.json`. Template drift v2.8.2 → v2.9.0: would change `.github/renovate.json`.", markdown)
        self.assertIn("| ⚠️ | [example](https://github.com/quokkify/example) | v2.8.2 → v2.9.0 | ? | ? |", markdown)
        self.assertEqual(report["schema_version"], 6)
        self.assertEqual(report["configuration_mismatches"], 1)
        self.assertEqual(report["repositories"][0]["template"]["renovate"], "missing")
        self.assertEqual(report["repositories"][0]["template"]["allure_report"], "enabled")
        self.assertEqual(report["repositories"][0]["template"]["target_commit"], "v2.9.0")

    def test_report_renderers_escape_control_characters_and_backslash_pipes(self) -> None:
        inventory = fleet.TemplateInventory(
            commit="v2.8.2\r\x1b\u0085\u009b|row",
            target_commit=None,
            components=("python:C:\\repo\\|row\r\x1b\u0085\u009b",),
            baseline="2/2",
            missing_baseline=(),
            docker="disabled",
            codeql="disabled",
            allure_report="disabled",
            release_please="disabled",
            renovate="enabled",
        )
        result = fleet.Result("quokkify/example", "up-to-date", inventory=inventory)

        console = "\n".join(fleet.console_lines(result))
        markdown = fleet.markdown_report([result], {"up-to-date": 1})

        for rendered, backslash in ((console, "\\"), (markdown, "&#92;")):
            self.assertNotIn("\r", rendered)
            self.assertNotIn("\x1b", rendered)
            self.assertIn(backslash + "x0d", rendered)
            self.assertIn(backslash + "x1b", rendered)
            self.assertNotIn("\u0085", rendered)
            self.assertNotIn("\u009b", rendered)
            self.assertIn(backslash + "x85", rendered)
            self.assertIn(backslash + "x9b", rendered)
        self.assertIn("&#124;row", markdown)
        self.assertNotIn("|row", markdown)
        self.assertEqual(len([line for line in markdown.splitlines() if line.startswith("|")]), 3)


class GitStatusTests(TestCase):
    @mock.patch.object(fleet, "run")
    def test_parses_modified_untracked_and_renamed_paths(self, run_mock: mock.Mock) -> None:
        run_mock.return_value.stdout = (
            " M README.md\0?? .github/workflows/codeql.yml\0"
            "R  new-name.yml\0old-name.yml\0"
        )
        paths = fleet.changed_paths(Path("/tmp/repository"))
        self.assertEqual(
            paths,
            [".github/workflows/codeql.yml", "README.md", "new-name.yml"],
        )


class RepositoryProcessingTests(TestCase):
    @mock.patch.object(fleet, "fetch_answers_files", return_value={})
    def test_skips_repository_without_answers(self, _: mock.Mock) -> None:
        result = fleet.process_repository(
            fleet.Repository("quokkify/plain", "main"),
            expected_template="quokkify/ci-kit",
            branch=fleet.DEFAULT_BRANCH,
            dry_run=True,
            template_ref=None,
            env={},
            workspace=Path("/tmp"),
        )
        self.assertEqual(result.status, "not-managed")

    @mock.patch.object(
        fleet,
        "fetch_answers_files",
        return_value={"legacy": "_src_path: gh:someone/other-template\n"},
    )
    def test_skips_foreign_template(self, _: mock.Mock) -> None:
        result = fleet.process_repository(
            fleet.Repository("quokkify/foreign", "main"),
            expected_template="quokkify/ci-kit",
            branch=fleet.DEFAULT_BRANCH,
            dry_run=True,
            template_ref=None,
            env={},
            workspace=Path("/tmp"),
        )
        self.assertEqual(result.status, "foreign-template")

    @mock.patch.object(fleet, "update_template")
    @mock.patch.object(
        fleet,
        "clone_repository",
        side_effect=cloned_answers("_src_path: gh:someone/other-template\n"),
    )
    @mock.patch.object(
        fleet,
        "fetch_answers_files",
        return_value={"legacy": "_src_path: gh:quokkify/ci-kit\n"},
    )
    def test_revalidates_template_source_from_cloned_revision(
        self,
        _: mock.Mock,
        __: mock.Mock,
        update_mock: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(fleet.FleetUpdateError, "changed to a different template"):
                fleet.process_repository(
                    fleet.Repository("quokkify/example", "main"),
                    expected_template="quokkify/ci-kit",
                    branch=fleet.DEFAULT_BRANCH,
                    dry_run=True,
                    template_ref=None,
                    env={},
                    workspace=Path(temporary),
                )
        update_mock.assert_not_called()

    @mock.patch.object(fleet, "update_template", side_effect=fleet.FleetUpdateError("conflict"))
    @mock.patch.object(
        fleet,
        "clone_repository",
        side_effect=cloned_answers(
            "_commit: v2.8.2\n"
            "_src_path: gh:quokkify/ci-kit\n"
            "components: []\n"
        ),
    )
    @mock.patch.object(
        fleet,
        "fetch_answers_files",
        return_value={"legacy": "_src_path: gh:quokkify/ci-kit\n"},
    )
    def test_preserves_inventory_when_copier_update_fails(
        self,
        _: mock.Mock,
        __: mock.Mock,
        ___: mock.Mock,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(fleet.RepositoryProcessError, "conflict") as raised:
                fleet.process_repository(
                    fleet.Repository("quokkify/example", "main"),
                    expected_template="quokkify/ci-kit",
                    branch=fleet.DEFAULT_BRANCH,
                    dry_run=True,
                    template_ref=None,
                    env={},
                    workspace=Path(temporary),
                )
        self.assertEqual(raised.exception.inventory.commit, "v2.8.2")
        self.assertEqual(raised.exception.inventory.components, ("none",))

    @mock.patch.object(fleet, "update_template", return_value=["renovate.json", "validate.yml"])
    @mock.patch.object(fleet, "clone_repository")
    @mock.patch.object(
        fleet,
        "fetch_answers_files",
        return_value={"legacy": "_src_path: gh:quokkify/ci-kit\n"},
    )
    def test_dry_run_reports_changes_without_push(
        self,
        _: mock.Mock,
        clone_mock: mock.Mock,
        update_mock: mock.Mock,
    ) -> None:
        clone_mock.side_effect = cloned_answers(
            "_commit: v2.8.2\n_src_path: gh:quokkify/ci-kit\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = fleet.process_repository(
                fleet.Repository("quokkify/example", "main"),
                expected_template="quokkify/ci-kit",
                branch=fleet.DEFAULT_BRANCH,
                dry_run=True,
                template_ref="v3.0.0",
                env={},
                workspace=Path(temporary),
            )
        self.assertEqual(result.status, "would-update")
        self.assertIn("renovate.json", result.detail)
        clone_mock.assert_called_once()
        self.assertEqual(update_mock.call_args.kwargs["template_ref"], "v3.0.0")
        self.assertEqual(
            update_mock.call_args.kwargs["template_source"], "quokkify/ci-kit"
        )

    @mock.patch.object(fleet, "ensure_pull_request", return_value="https://github.com/quokkify/example/pull/1")
    @mock.patch.object(fleet, "push_automation_branch")
    @mock.patch.object(fleet, "update_template", return_value=[".github/workflows/validate.yml"])
    @mock.patch.object(fleet, "clone_repository")
    @mock.patch.object(
        fleet,
        "fetch_answers_files",
        return_value={"legacy": "_src_path: gh:quokkify/ci-kit\n"},
    )
    def test_write_mode_pushes_branch_and_returns_pull_request(
        self,
        _: mock.Mock,
        clone_mock: mock.Mock,
        update_mock: mock.Mock,
        push_mock: mock.Mock,
        pull_request_mock: mock.Mock,
    ) -> None:
        clone_mock.side_effect = cloned_answers(
            "_commit: v2.8.2\n_src_path: gh:quokkify/ci-kit\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = fleet.process_repository(
                fleet.Repository("quokkify/example", "main"),
                expected_template="gh:quokkify/ci-kit.git",
                branch=fleet.DEFAULT_BRANCH,
                dry_run=False,
                template_ref=None,
                env={},
                workspace=Path(temporary),
            )
        self.assertEqual(result.status, "pull-request")
        self.assertEqual(result.detail, "https://github.com/quokkify/example/pull/1")
        clone_mock.assert_called_once()
        update_mock.assert_called_once()
        push_mock.assert_called_once()
        pull_request_mock.assert_called_once()

    @mock.patch.object(fleet, "ensure_pull_request")
    @mock.patch.object(fleet, "push_automation_branch")
    @mock.patch.object(fleet, "update_template", return_value=[])
    @mock.patch.object(fleet, "clone_repository")
    @mock.patch.object(
        fleet,
        "fetch_answers_files",
        return_value={"legacy": "_src_path: gh:quokkify/ci-kit\n"},
    )
    def test_up_to_date_repository_does_not_push(
        self,
        _: mock.Mock,
        clone_mock: mock.Mock,
        update_mock: mock.Mock,
        push_mock: mock.Mock,
        pull_request_mock: mock.Mock,
    ) -> None:
        clone_mock.side_effect = cloned_answers(
            "_commit: v2.8.2\n_src_path: gh:quokkify/ci-kit\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = fleet.process_repository(
                fleet.Repository("quokkify/example", "main"),
                expected_template="quokkify/ci-kit",
                branch=fleet.DEFAULT_BRANCH,
                dry_run=False,
                template_ref=None,
                env={},
                workspace=Path(temporary),
            )
        self.assertEqual(result.status, "up-to-date")
        clone_mock.assert_called_once()
        update_mock.assert_called_once()
        push_mock.assert_not_called()
        pull_request_mock.assert_not_called()

LEGACY_ANSWERS_TEXT = "_commit: v2.8.2\n_src_path: gh:quokkify/ci-kit\n"
CORE_ANSWERS_TEXT = "_commit: v3.0.0\n_src_path: gh:quokkify/ci-kit\n"


def fake_contents(listing, files, *, listing_error: str | None = None):
    def fake_run(command, **_):
        endpoint = next(arg for arg in command if arg.startswith("repos/"))
        if endpoint.endswith("/contents/"):
            if listing_error:
                return subprocess.CompletedProcess(command, 1, "", listing_error)
            return subprocess.CompletedProcess(command, 0, json.dumps(listing), "")
        name = endpoint.rsplit("/", 1)[1]
        return subprocess.CompletedProcess(command, 0, files[name], "")

    return fake_run


def entry(name: str, kind: str = "file") -> dict:
    return {"name": name, "type": kind}


class FetchAnswersFilesTests(TestCase):
    def fetch(self, listing, files=None, **kwargs):
        with mock.patch.object(fleet, "run", side_effect=fake_contents(listing, files or {}, **kwargs)):
            return fleet.fetch_answers_files("quokkify/example", env={})

    def test_collects_legacy_and_module_answers_from_the_root_listing(self) -> None:
        files = {
            ".copier-answers.yml": LEGACY_ANSWERS_TEXT,
            ".copier-answers.core.yml": CORE_ANSWERS_TEXT,
        }
        listing = [
            entry(".copier-answers.yml"),
            entry(".copier-answers.core.yml"),
            entry(".copier-answers.yaml"),
            entry(".copier-answers.d", "dir"),
            entry("README.md"),
        ]
        self.assertEqual(
            self.fetch(listing, files),
            {"legacy": LEGACY_ANSWERS_TEXT, "core": CORE_ANSWERS_TEXT},
        )

    def test_module_answers_names_match_the_ci_kit_discovery(self) -> None:
        from ci_kit.plan import discover

        names = [
            ".copier-answers.yml", ".copier-answers.java.yml", ".copier-answers.Core.yml",
            ".copier-answers.my_mod.yml", ".copier-answers.a.b.yml", ".copier-answers..yml",
        ]
        files = {name: CORE_ANSWERS_TEXT for name in names}
        fetched = self.fetch([entry(name) for name in names], files)
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary).resolve()
            for name in names:
                (repo / name).write_text(CORE_ANSWERS_TEXT, encoding="utf-8")
            discovered = set(discover(repo)[0])
        self.assertEqual(set(fetched) - {fleet.LEGACY_MODULE}, discovered)
        self.assertEqual(discovered, {"java", "Core", "my_mod"})

    def test_near_miss_module_answers_are_reported_as_unknown_modules(self) -> None:
        listing = [entry(".copier-answers.yml"), entry(".copier-answers.Core.yml")]
        files = {".copier-answers.yml": LEGACY_ANSWERS_TEXT, ".copier-answers.Core.yml": CORE_ANSWERS_TEXT}
        result, clone_mock = process(self.fetch(listing, files))
        self.assertEqual((result.status, result.detail), ("unknown-module", "Core"))
        clone_mock.assert_not_called()

    def test_symlinked_module_answers_are_unsafe(self) -> None:
        listing = [entry(".copier-answers.yml"), entry(".copier-answers.java.yml", "symlink")]
        with self.assertRaises(fleet.UnsafeAnswersFileError):
            self.fetch(listing, {".copier-answers.yml": LEGACY_ANSWERS_TEXT})

    def test_directory_named_like_answers_is_unsafe(self) -> None:
        with self.assertRaises(fleet.UnsafeAnswersFileError):
            self.fetch([entry(".copier-answers.core.yml", "dir")])

    def test_empty_or_missing_root_has_no_answers(self) -> None:
        self.assertEqual(self.fetch([]), {})
        self.assertEqual(self.fetch([], listing_error="HTTP 404: Not Found"), {})

    def test_other_listing_errors_are_not_swallowed(self) -> None:
        with self.assertRaises(fleet.FleetUpdateError):
            self.fetch([], listing_error="HTTP 500: boom")


def process(files, *, dry_run=True, template_ref="v3.1.0", workspace=None, **patches):
    with mock.patch.object(fleet, "fetch_answers_files", return_value=files), \
         mock.patch.object(fleet, "clone_repository", side_effect=patches.pop("clone", None)) as clone_mock, \
         tempfile.TemporaryDirectory() as temporary:
        result = fleet.process_repository(
            fleet.Repository("quokkify/example", "main"),
            expected_template="quokkify/ci-kit",
            branch=fleet.DEFAULT_BRANCH,
            dry_run=dry_run,
            template_ref=template_ref,
            env={},
            workspace=Path(workspace or temporary),
        )
    return result, clone_mock


class ModuleAnswersGuardTests(TestCase):
    def test_foreign_module_template_is_reported_without_cloning(self) -> None:
        for source in ("gh:someone/else", "gh:quokkify/ci-kit-evil", "https://github.com/quokkify/ci-kit/x", "gh:quokkify/ci-kit\n"):
            with self.subTest(source=source):
                files = {"core": f"_commit: v3.0.0\n_src_path: {json.dumps(source)}\n"}
                result, clone_mock = process(files)
                self.assertEqual(result.status, "foreign-module-template")
                clone_mock.assert_not_called()

    def test_missing_module_source_is_foreign(self) -> None:
        result, _ = process({"core": "_commit: v3.0.0\n"})
        self.assertEqual(result.status, "foreign-module-template")

    def test_unknown_module_is_reported_without_cloning(self) -> None:
        result, clone_mock = process({"legacy": LEGACY_ANSWERS_TEXT, "rust": CORE_ANSWERS_TEXT})
        self.assertEqual(result.status, "unknown-module")
        self.assertIn("rust", result.detail)
        clone_mock.assert_not_called()

    def test_unsafe_answers_file_is_reported_without_cloning(self) -> None:
        with mock.patch.object(
            fleet, "fetch_answers_files", side_effect=fleet.UnsafeAnswersFileError(".copier-answers.java.yml")
        ), mock.patch.object(fleet, "clone_repository") as clone_mock:
            result = fleet.process_repository(
                fleet.Repository("quokkify/example", "main"),
                expected_template="quokkify/ci-kit",
                branch=fleet.DEFAULT_BRANCH,
                dry_run=True,
                template_ref="v3.1.0",
                env={},
                workspace=Path("/tmp"),
            )
        self.assertEqual(result.status, "unsafe-answers-file")
        self.assertIn(".copier-answers.java.yml", result.detail)
        clone_mock.assert_not_called()


def materialize_files(files: dict[str, str]):
    def materialize(_: object, destination: Path, *, env: dict[str, str]) -> None:
        del env
        destination.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (destination / name).parent.mkdir(parents=True, exist_ok=True)
            (destination / name).write_text(text, encoding="utf-8")

    return materialize


ROWS = [{"module": "core", "from": "v3.0.0", "to": "v3.1.0", "doctor": "ok"}]


class ModuleProcessingTests(TestCase):
    def test_hybrid_repository_updates_legacy_then_modules(self) -> None:
        files = {"legacy": LEGACY_ANSWERS_TEXT + "handed_off:\n  - ci\n", "core": CORE_ANSWERS_TEXT}
        cloned = {".copier-answers.yml": files["legacy"], ".copier-answers.core.yml": CORE_ANSWERS_TEXT}
        order: list[str] = []
        with mock.patch.object(fleet, "update_template", side_effect=lambda *a, **k: order.append("legacy") or ["a.yml"]) as update_mock, \
             mock.patch.object(fleet, "apply_module_updates", side_effect=lambda *a, **k: order.append("modules") or ROWS) as modules_mock, \
             mock.patch.object(fleet, "changed_paths", return_value=["a.yml", "b.yml"]):
            result, _ = process(files, clone=materialize_files(cloned))
        self.assertEqual(order, ["legacy", "modules"])
        self.assertEqual(modules_mock.call_args.args[1], "v3.1.0")
        update_mock.assert_called_once()
        self.assertEqual(result.status, "would-update")
        self.assertEqual(result.detail, "a.yml, b.yml")
        self.assertEqual(result.inventory.modules, ("core",))
        self.assertEqual(result.inventory.state, "hybrid(ci)")
        self.assertEqual(result.inventory.commit, "v2.8.2")

    def test_modules_only_repository_skips_the_legacy_update(self) -> None:
        cloned = {".copier-answers.core.yml": CORE_ANSWERS_TEXT, ".copier-answers.java.yml": CORE_ANSWERS_TEXT}
        files = {"core": CORE_ANSWERS_TEXT, "java": CORE_ANSWERS_TEXT}
        with mock.patch.object(fleet, "update_template") as update_mock, \
             mock.patch.object(fleet, "apply_module_updates", return_value=ROWS), \
             mock.patch.object(fleet, "changed_paths", return_value=[]):
            result, _ = process(files, clone=materialize_files(cloned))
        update_mock.assert_not_called()
        self.assertEqual(result.status, "up-to-date")
        self.assertEqual(result.inventory.state, "modules")
        self.assertEqual(result.inventory.modules, ("core", "java"))
        self.assertEqual(result.inventory.commit, "v3.0.0")

    def test_pull_request_carries_the_module_table(self) -> None:
        cloned = {".copier-answers.core.yml": CORE_ANSWERS_TEXT}
        with mock.patch.object(fleet, "apply_module_updates", return_value=ROWS), \
             mock.patch.object(fleet, "changed_paths", return_value=["x.yml"]), \
             mock.patch.object(fleet, "push_automation_branch"), \
             mock.patch.object(fleet, "ensure_pull_request", return_value="https://example.invalid/pr") as pr_mock:
            result, _ = process({"core": CORE_ANSWERS_TEXT}, dry_run=False, clone=materialize_files(cloned))
        self.assertEqual(result.status, "pull-request")
        self.assertEqual(pr_mock.call_args.kwargs["modules"], ROWS)

    def test_module_update_failure_opens_no_pull_request(self) -> None:
        cloned = {".copier-answers.core.yml": CORE_ANSWERS_TEXT}
        with mock.patch.object(fleet, "apply_module_updates", side_effect=fleet.FleetUpdateError("ci-kit update failed: CIK-UPDATE-001")), \
             mock.patch.object(fleet, "push_automation_branch") as push_mock, \
             mock.patch.object(fleet, "ensure_pull_request") as pr_mock:
            with self.assertRaisesRegex(fleet.RepositoryProcessError, "CIK-UPDATE-001") as raised:
                process({"core": CORE_ANSWERS_TEXT}, dry_run=False, clone=materialize_files(cloned))
        push_mock.assert_not_called()
        pr_mock.assert_not_called()
        self.assertEqual(raised.exception.inventory.state, "modules")

    def test_missing_template_ref_is_an_error_for_module_repositories(self) -> None:
        cloned = {".copier-answers.core.yml": CORE_ANSWERS_TEXT}
        with self.assertRaisesRegex(fleet.RepositoryProcessError, "resolved template ref"):
            process({"core": CORE_ANSWERS_TEXT}, template_ref=None, clone=materialize_files(cloned))

    def test_cloned_symlink_module_answers_are_unsafe(self) -> None:
        def clone(_: object, destination: Path, *, env: dict[str, str]) -> None:
            destination.mkdir(parents=True)
            (destination / "elsewhere.yml").write_text(CORE_ANSWERS_TEXT, encoding="utf-8")
            (destination / ".copier-answers.core.yml").symlink_to("elsewhere.yml")

        result, _ = process({"core": CORE_ANSWERS_TEXT}, clone=clone)
        self.assertEqual(result.status, "unsafe-answers-file")

    def test_pure_legacy_repository_is_unchanged(self) -> None:
        cloned = {".copier-answers.yml": LEGACY_ANSWERS_TEXT}
        with mock.patch.object(fleet, "update_template", return_value=["a.yml"]), \
             mock.patch.object(fleet, "apply_module_updates") as modules_mock:
            result, _ = process({"legacy": LEGACY_ANSWERS_TEXT}, clone=materialize_files(cloned))
        modules_mock.assert_not_called()
        self.assertEqual((result.status, result.detail), ("would-update", "a.yml"))
        self.assertEqual(result.inventory.modules, ())
        self.assertEqual(result.inventory.state, "legacy")


LEGACY_PULL_REQUEST_BODY = (
    "## Summary\n\n"
    "Automated `copier update` from the shared "
    "[`quokkify/ci-kit`](https://github.com/quokkify/ci-kit) template.\n\n"
    "This keeps the organization baseline (validation, CodeQL, Gitleaks, "
    "Release Please, and Renovate) synchronized while leaving merge approval to maintainers.\n\n"
    "## Generated changes\n\n"
    "- `a.yml`\n"
    "- `b.yml`"
    "\n\n## Verification\n\n"
    "- `copier update --trust --defaults --conflict=rej --skip-tasks`\n"
    "- Legacy components keep their `type`/`path`; the updater supplies stable `id`/`name` migration data.\n"
    "- `git diff --cached --check`\n\n"
    "Do not edit the automation branch directly; make project-specific changes after merging "
    "or adjust the Copier answers/template.\n"
)


class PullRequestBodyTests(TestCase):
    def create(self, **kwargs) -> str:
        def fake_run(command, **_):
            if command[:3] == ["gh", "pr", "create"]:
                fake_run.body = command[command.index("--body") + 1]
                return subprocess.CompletedProcess(command, 0, "https://example.invalid/pr\n", "")
            return subprocess.CompletedProcess(command, 0, "[]", "")

        with mock.patch.object(fleet, "run", side_effect=fake_run):
            fleet.ensure_pull_request(
                fleet.Repository("quokkify/example", "main"),
                branch="b", changed=["a.yml", "b.yml"], env={}, **kwargs,
            )
        return fake_run.body

    def test_legacy_body_is_byte_identical(self) -> None:
        self.assertEqual(self.create(), LEGACY_PULL_REQUEST_BODY)

    def test_module_rows_are_rendered_as_a_table(self) -> None:
        body = self.create(modules=ROWS)
        self.assertIn("| module | old → new | doctor |\n| --- | --- | --- |\n| core | v3.0.0 → v3.1.0 | ok |", body)
        self.assertTrue(body.startswith(LEGACY_PULL_REQUEST_BODY.split("## Verification")[0]))


class ModuleUpdateRunnerTests(TestCase):
    def fake(self, returncode: int, payload: dict):
        def fake_run(command, **kwargs):
            fake_run.command = command
            fake_run.env = kwargs.get("env")
            Path(command[command.index("--json") + 1]).write_text(json.dumps(payload), encoding="utf-8")
            return subprocess.CompletedProcess(command, returncode, "", "")

        return fake_run

    def test_runs_the_cli_from_this_checkout_without_template_src(self) -> None:
        payload = {"ref": "v3.1.0", "modules": [{"module": "core", "from": "v3.0.0", "to": "v3.1.0", "commit": "c" * 40}], "findings": []}
        fake_run = self.fake(0, payload)
        with mock.patch.object(fleet, "run", side_effect=fake_run):
            rows = fleet.run_ci_kit_update(Path("/repo"), "v3.1.0", env={"PYTHONPATH": "/other", "X": "1"})
        self.assertEqual(rows, [{"module": "core", "from": "v3.0.0", "to": "v3.1.0", "doctor": "ok"}])
        command = fake_run.command
        self.assertEqual(command[:4], [sys.executable, "-m", "ci_kit.cli", "update"])
        self.assertEqual(command[command.index("--repo") + 1], "/repo")
        self.assertEqual(command[command.index("--to") + 1], "v3.1.0")
        self.assertIn("--skip-legacy", command)
        self.assertNotIn("--template-src", command)
        tools = str(ROOT / "tools")
        self.assertEqual(fake_run.env["PYTHONPATH"], tools + os.pathsep + "/other")
        self.assertEqual(fake_run.env["X"], "1")

    def test_python_path_is_only_the_checkout_when_unset(self) -> None:
        fake_run = self.fake(0, {"modules": [], "findings": []})
        with mock.patch.object(fleet, "run", side_effect=fake_run):
            fleet.run_ci_kit_update(Path("/repo"), "v3.1.0", env={})
        self.assertEqual(fake_run.env["PYTHONPATH"], str(ROOT / "tools"))

    def test_nonzero_exit_reports_finding_codes(self) -> None:
        payload = {"ref": "v3.1.0", "modules": [], "findings": [{"code": "CIK-UPDATE-001", "module": "core", "message": "x", "path": None}, {"code": "CIK-DRIFT-001", "module": "java", "message": "y", "path": None}]}
        with mock.patch.object(fleet, "run", side_effect=self.fake(1, payload)):
            with self.assertRaisesRegex(fleet.FleetUpdateError, "CIK-UPDATE-001, CIK-DRIFT-001"):
                fleet.run_ci_kit_update(Path("/repo"), "v3.1.0", env={})

    def test_nonzero_exit_without_report_is_still_an_error(self) -> None:
        completed = subprocess.CompletedProcess([], 2, "", "boom")
        with mock.patch.object(fleet, "run", return_value=completed):
            with self.assertRaisesRegex(fleet.FleetUpdateError, "ci-kit update failed"):
                fleet.run_ci_kit_update(Path("/repo"), "v3.1.0", env={})


class ApplyModuleUpdatesTests(TestCase):
    def test_pending_changes_are_committed_first_then_squashed_back_to_the_working_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary).resolve()
            git = lambda *args: subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
            git("init", "-q", "-b", "main")
            git("config", "user.email", "t@example.invalid")
            git("config", "user.name", "t")
            (repo / "base.txt").write_text("1")
            git("add", "-A")
            git("commit", "-q", "-m", "base")
            base = git("rev-parse", "HEAD")
            (repo / "legacy.txt").write_text("legacy")

            def fake_update(path: Path, ref: str, *, env: dict[str, str]):
                self.assertEqual(git("status", "--porcelain"), "")
                (path / "module.txt").write_text("module")
                git("add", "-A")
                git("commit", "-q", "-m", "module")
                return ROWS

            with mock.patch.object(fleet, "run_ci_kit_update", side_effect=fake_update):
                rows = fleet.apply_module_updates(repo, "v3.1.0", env={})
            self.assertEqual(rows, ROWS)
            self.assertEqual(git("rev-parse", "HEAD"), base)
            self.assertEqual(fleet.changed_paths(repo), ["legacy.txt", "module.txt"])

    def test_failure_leaves_no_pull_request_state_behind(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary).resolve()
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
            subprocess.run(["git", "-c", "user.email=t@e.i", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "b"], cwd=repo, check=True)
            with mock.patch.object(fleet, "run_ci_kit_update", side_effect=fleet.FleetUpdateError("ci-kit update failed: CIK-UPDATE-001")):
                with self.assertRaises(fleet.FleetUpdateError):
                    fleet.apply_module_updates(repo, "v3.1.0", env={})


class ModuleReportTests(TestCase):
    def inventory(self, **kwargs):
        return fleet.TemplateInventory(
            commit="v3.0.0", target_commit="v3.1.0", components=("none",), baseline="2/2",
            missing_baseline=(), docker="unknown", codeql="unknown", allure_report="unknown",
            release_please="unknown", renovate="unknown", **kwargs,
        )

    def test_reports_carry_modules_and_state(self) -> None:
        result = fleet.Result("quokkify/example", "would-update", "a.yml", self.inventory(modules=("core", "java"), state="hybrid(ci)"))
        counts = {"would-update": 1}
        report = json.loads(fleet.json_report([result], counts))
        self.assertEqual(report["repositories"][0]["template"]["modules"], ["core", "java"])
        self.assertEqual(report["repositories"][0]["template"]["state"], "hybrid(ci)")
        self.assertIn("state=hybrid(ci) modules=core,java", "\n".join(fleet.console_lines(result)))

    def test_new_statuses_have_markdown_labels_and_modules_state_has_no_baseline_gap(self) -> None:
        result = fleet.Result("quokkify/example", "up-to-date", "", self.inventory(modules=("core",), state="modules"))
        self.assertEqual(fleet.configuration_gap_count([result]), 0)
        labels = {
            "foreign-module-template": "Foreign module template",
            "unknown-module": "Unknown module",
            "unsafe-answers-file": "Unsafe answers file",
        }
        for status, label in labels.items():
            self.assertIn(label, fleet.markdown_report([fleet.Result("quokkify/x", status)], {status: 1}))

    def test_legacy_inventory_defaults(self) -> None:
        inventory = fleet.inventory_from_answers(LEGACY_ANSWERS_TEXT, Path("/nonexistent"))
        self.assertEqual((inventory.modules, inventory.state), ((), "legacy"))


class SquashToBaseTests(TestCase):
    def test_migration_commits_become_one_pending_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary).resolve()
            git = lambda *args: subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
            git("init", "-q", "-b", "main")
            git("config", "user.email", "t@example.invalid")
            git("config", "user.name", "t")
            (repo / "a").write_text("1")
            git("add", "-A")
            git("commit", "-q", "-m", "base")
            base = fleet.git_head(repo)
            for name in ("b", "c"):
                (repo / name).write_text(name)
                git("add", "-A")
                git("commit", "-q", "-m", name)
            fleet.squash_to_base(repo, base)
            self.assertEqual(fleet.git_head(repo), base)
            self.assertEqual(fleet.changed_paths(repo), ["b", "c"])


class FleetReportTests(TestCase):
    def inventory(self, commit="v3.2.3", target="v3.2.3", **kwargs):
        values = dict(commit=commit, target_commit=target, components=("python:.",), baseline="2/2",
                      missing_baseline=(), docker="disabled", codeql="enabled", allure_report="disabled",
                      release_please="enabled", renovate="enabled")
        values.update(kwargs)
        return fleet.TemplateInventory(**values)

    def pull_request(self, repository, number, ci, state="open"):
        return {"pull_request": {"url": f"https://github.com/{repository}/pull/{number}", "number": number,
                                 "state": state, "age_days": 0, "ci": {"status": ci}},
                "pull_request_observation": {"status": "observed"}}

    def attention_rows(self, markdown):
        section = markdown.split("### Needs your attention", 1)[1].split("<details>", 1)[0]
        return [line for line in section.splitlines() if line.startswith("| ") and "Repository" not in line and not line.startswith("| :-:")]

    def test_green_run_has_one_verdict_and_four_column_fleet_table(self) -> None:
        results = [fleet.Result("quokkify/a", "up-to-date", inventory=self.inventory()),
                   fleet.Result("quokkify/b", "up-to-date", inventory=self.inventory()),
                   fleet.Result("quokkify/ci-kit", "excluded")]
        markdown = fleet.markdown_report(results, {"up-to-date": 2, "excluded": 1})
        self.assertTrue(markdown.startswith("## Copier fleet: ✅ nothing to do\n"))
        self.assertEqual(markdown.count("## Copier fleet:"), 1)
        self.assertIn("All 2 managed repositories are on **v3.2.3**.", markdown)
        self.assertNotIn("Needs your attention", markdown)
        self.assertIn("| Repository | Template | CI on `main` | Last automation PR |\n| --- | --- | --- | --- |", markdown)
        self.assertIn("| [a](https://github.com/quokkify/a) | v3.2.3 | ? | ? |", markdown)
        self.assertIn("| quokkify/ci-kit | `--exclude` |", markdown)

    def test_attention_rows_follow_human_cost_and_keep_unknown_statuses(self) -> None:
        gap = self.inventory(baseline="1/2", missing_baseline=(fleet.BASELINE_PATHS[1],))
        results = [
            fleet.Result("quokkify/drift", "would-update", ".github/x.yml", self.inventory(target="v3.3.0")),
            fleet.Result("quokkify/mystery", "brand-new-status", "raw detail"),
            fleet.Result("quokkify/gap", "up-to-date", inventory=gap),
            fleet.Result("quokkify/red-pr", "pull-request", "https://github.com/quokkify/red-pr/pull/7",
                         self.inventory(target="v3.3.0"), self.pull_request("quokkify/red-pr", 7, "failed")),
            fleet.Result("quokkify/broken", "failed", "HTTP 502"),
            fleet.Result("quokkify/conflict", "failed", "Copier produced conflict files: a.yml.rej, b.json.rej"),
        ]
        markdown = fleet.markdown_report(results, {})
        rows = self.attention_rows(markdown)
        self.assertIn("### Needs your attention — 6\n", markdown)
        self.assertEqual(len(rows), 6)
        self.assertEqual([row.split("](https://github.com/quokkify/", 1)[1].split(")", 1)[0] for row in rows],
                         ["conflict", "broken", "red-pr", "gap", "drift", "mystery"])
        self.assertIn("Copier conflict in a.yml, b.json; nothing pushed.", rows[0])
        self.assertIn("**Re-run**", rows[1])
        self.assertIn("`.github/workflows/gitleaks.yml`", rows[3])
        self.assertIn("Unrecognized status brand-new-status: raw detail.", rows[5])
        self.assertTrue(all("| **" in row for row in rows))
        self.assertIn("## Copier fleet: ❌ action required — 1 conflict · 1 failed · 1 PR with failed CI · "
                      "1 configuration gap · 1 drift · 1 unrecognized status\n", markdown)
        details = markdown.split("<summary>Commands and raw errors</summary>", 1)[1]
        self.assertIn("gh workflow run copier-fleet-auto-update.yml -f repository=quokkify/conflict\n", details)
        self.assertIn("<pre>HTTP 502</pre>", details)

    def test_drift_only_dry_run_verdict(self) -> None:
        result = fleet.Result("quokkify/a", "would-update", "x.yml", self.inventory(target="v3.3.0"))
        run = {"mode": "dry-run", "template_ref": "v3.3.0", "trigger": None, "run_url": None, "generated_at": "2026-10-21T09:27:00+00:00"}
        markdown = fleet.markdown_report([result], {"would-update": 1}, run)
        self.assertTrue(markdown.startswith("## Copier fleet: 🟡 drift in 1\n"))
        self.assertIn("Dry run · 2026-10-21 09:27 UTC · target v3.3.0\n", markdown)

    def test_ready_and_pending_pull_requests_are_context_not_attention(self) -> None:
        results = [
            fleet.Result("quokkify/ready", "pull-request", "https://github.com/quokkify/ready/pull/1",
                         self.inventory(target="v3.3.0"), self.pull_request("quokkify/ready", 1, "passed")),
            fleet.Result("quokkify/pending", "pull-request", "https://github.com/quokkify/pending/pull/2",
                         self.inventory(target="v3.3.0"), self.pull_request("quokkify/pending", 2, "pending")),
            fleet.Result("quokkify/calm", "up-to-date", inventory=self.inventory()),
        ]
        markdown = fleet.markdown_report(results, {"pull-request": 2, "up-to-date": 1})
        self.assertTrue(markdown.startswith("## Copier fleet: 🔀 2 PRs open\n"))
        self.assertNotIn("Needs your attention", markdown)
        self.assertIn("1 waiting on CI ([pending#2](https://github.com/quokkify/pending/pull/2))", markdown)
        self.assertIn("1 ready to merge ([ready#1](https://github.com/quokkify/ready/pull/1))", markdown)
        self.assertIn("| ⏳ | [pending](https://github.com/quokkify/pending) | v3.2.3 → v3.3.0 | ? | [#2](https://github.com/quokkify/pending/pull/2) open today · CI ⏳ |", markdown)

    def test_context_lists_pull_request_links_only_up_to_three(self) -> None:
        results = [fleet.Result(f"quokkify/r{number}", "pull-request", f"https://github.com/quokkify/r{number}/pull/{number}",
                                self.inventory(target="v3.3.0"), self.pull_request(f"quokkify/r{number}", number, ci))
                   for number, ci in enumerate(["pending"] * 3 + ["unknown"] * 4, 1)]
        markdown = fleet.markdown_report(results)
        self.assertIn("3 waiting on CI ([r1#1](https://github.com/quokkify/r1/pull/1), [r2#2](https://github.com/quokkify/r2/pull/2), "
                      "[r3#3](https://github.com/quokkify/r3/pull/3)) · 4 open, CI unknown\n", markdown)
        self.assertNotIn("[r4#4]", markdown.split("<details>", 1)[0])

    def test_summary_never_counts_zero_calm_repositories(self) -> None:
        drift = fleet.Result("quokkify/drift", "would-update", "x.yml", self.inventory(target="v3.3.0"))
        open_pr = fleet.Result("quokkify/open", "pull-request", "https://github.com/quokkify/open/pull/1",
                               self.inventory(target="v3.3.0"), self.pull_request("quokkify/open", 1, "pending"))
        other_pr = fleet.Result("quokkify/other", "pull-request", "https://github.com/quokkify/other/pull/2",
                                self.inventory(target="v3.3.0"), self.pull_request("quokkify/other", 2, "pending"))
        self.assertIn("\nAll 2 managed repositories have update PRs open.\n", fleet.markdown_report([open_pr, other_pr]))
        self.assertIn("\nAll 1 managed repositories need attention.\n", fleet.markdown_report([drift]))
        self.assertIn("\n1 of 2 managed repositories need attention; the rest have PRs in flight.\n", fleet.markdown_report([drift, open_pr]))

    def test_successful_pilot_gate_marks_its_pull_request_ready(self) -> None:
        health = self.pull_request("quokkify/pilot", 5, "unknown")
        health["pull_request"]["head_sha"] = "a" * 40
        pilot = fleet.Result("quokkify/pilot", "pull-request", "https://github.com/quokkify/pilot/pull/5", self.inventory(target="v3.3.0"), health)
        other = fleet.Result("quokkify/other", "pull-request", "https://github.com/quokkify/other/pull/6",
                             self.inventory(target="v3.3.0"), self.pull_request("quokkify/other", 6, "unknown"))
        gate = {"repository": "quokkify/pilot", "pull_request": 5, "revision": "a" * 40, "result": "success"}
        rollout = {"target": "v3.3.0", "pilots": [gate], "fanout_started": True, "stopped": None}
        markdown = fleet.markdown_report([pilot, other], None, None, rollout)
        self.assertIn("| [#5](https://github.com/quokkify/pilot/pull/5) open today · CI ✅ · pilot gate |", markdown)
        self.assertIn("| [#6](https://github.com/quokkify/other/pull/6) open today · CI ? |", markdown)
        self.assertIn("1 ready to merge ([pilot#5](https://github.com/quokkify/pilot/pull/5)) · 1 open, CI unknown ([other#6]", markdown)
        for stale in ({**gate, "pull_request": 4}, {**gate, "revision": "b" * 40}, {**gate, "result": "failure"}):
            markdown = fleet.markdown_report([pilot], None, None, {**rollout, "pilots": [stale]})
            self.assertNotIn("pilot gate", markdown)
            self.assertIn("1 open, CI unknown", markdown)
        headless = self.pull_request("quokkify/pilot", 5, "unknown")
        closed = self.pull_request("quokkify/pilot", 5, "unknown", state="closed")
        closed["pull_request"]["head_sha"] = "a" * 40
        for health in (headless, closed):
            result = fleet.Result("quokkify/pilot", "pull-request", "https://github.com/quokkify/pilot/pull/5", self.inventory(target="v3.3.0"), health)
            self.assertNotIn("pilot gate", fleet.markdown_report([result], None, None, rollout))

    def test_drift_rows_name_changed_files_and_offer_the_write_command(self) -> None:
        run = {"mode": "dry-run", "template_ref": "v3.3.0", "trigger": "workflow_dispatch",
               "run_url": "https://github.com/quokkify/ci-kit/actions/runs/7", "generated_at": None}
        few = fleet.Result("quokkify/few", "would-update", ".github/a.yml", self.inventory())
        many = fleet.Result("quokkify/many", "would-update", "a, b, c, d, e", self.inventory())
        markdown = fleet.markdown_report([few, many], None, run)
        rows = self.attention_rows(markdown)
        self.assertIn("| Template drift: would change `.github/a.yml`. | **Open** the update PR: run write mode for this repository (command below). |", rows[0])
        self.assertIn("Template drift: would change `a`, `b`, `c` and 2 more.", rows[1])
        self.assertIn("<summary>Commands</summary>", markdown)
        self.assertNotIn("<pre>", markdown)
        self.assertNotIn("dry-run=true", markdown)
        self.assertIn("gh workflow run copier-fleet-auto-update.yml -R quokkify/ci-kit -f repository=quokkify/many\n", markdown)
        two = fleet.markdown_report([fleet.Result("quokkify/two", "would-update", "a, b", self.inventory())], None, run)
        self.assertIn("would change `a` and `b`.", two)
        write = fleet.markdown_report([few], None, {**run, "mode": "write"})
        self.assertNotIn("gh workflow run", write)
        self.assertNotIn("<summary>Commands", write)
        failed = fleet.markdown_report([few, fleet.Result("quokkify/f", "failed", "boom")], None, run)
        self.assertIn("<summary>Commands and raw errors</summary>", failed)
        self.assertIn("-f repository=quokkify/f -f dry-run=true\n", failed)

    def test_run_block_is_written_to_json_and_context_without_guessing(self) -> None:
        env = {"GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "quokkify/ci-kit",
               "GITHUB_RUN_ID": "42", "GITHUB_EVENT_NAME": "schedule"}
        run = fleet.run_metadata("write", "v3.3.0", env)
        self.assertEqual({key: run[key] for key in ("mode", "template_ref", "trigger", "run_url")},
                         {"mode": "write", "template_ref": "v3.3.0", "trigger": "schedule",
                          "run_url": "https://github.com/quokkify/ci-kit/actions/runs/42"})
        self.assertEqual(datetime.fromisoformat(run["generated_at"]).utcoffset().total_seconds(), 0)
        bare = fleet.run_metadata("dry-run", None, {})
        self.assertEqual((bare["trigger"], bare["run_url"]), (None, None))
        result = fleet.Result("quokkify/a", "failed", "boom")
        report = json.loads(fleet.json_report([result], {"failed": 1}, run))
        self.assertEqual(report["schema_version"], 6)
        self.assertEqual(report["run"], run)
        self.assertNotIn("run", json.loads(fleet.json_report([result], {"failed": 1})))
        markdown = fleet.markdown_report([result], {"failed": 1}, run)
        self.assertIn("Write mode (schedule) · ", markdown)
        self.assertIn("[job log](https://github.com/quokkify/ci-kit/actions/runs/42) · target v3.3.0", markdown)
        self.assertIn("gh workflow run copier-fleet-auto-update.yml -R quokkify/ci-kit -f repository=quokkify/a\n", markdown)
        unknown = fleet.markdown_report([result], {"failed": 1})
        for guessed in ("Write mode", "Dry run", "target ", "job log", " -R "):
            self.assertNotIn(guessed, unknown)

    def test_untrusted_detail_is_escaped_without_visible_backslashes(self) -> None:
        detail = "a|b <img src=x> `c` [l](https://evil.example) \\( *x*\n</pre># h"
        results = [fleet.Result("quokkify/odd", "odd|status", detail), fleet.Result("quokkify/f", "failed", detail)]
        markdown = fleet.markdown_report(results, {})
        rows = self.attention_rows(markdown)
        self.assertEqual([row.count("|") for row in rows], [5, 5])
        self.assertIn("odd&#124;status: a&#124;b &#60;img src=x&#62; &#96;c&#96; &#91;l&#93;&#40;https&#58;//evil.example&#41; &#92;&#40; &#42;x&#42;", rows[1])
        self.assertNotIn("<img", markdown)
        self.assertNotIn("</pre>#", markdown)
        self.assertNotIn("\\|", markdown)
        self.assertNotIn("\\", "".join(rows))
        self.assertIn("<pre>a|b &lt;img src=x&gt; `c` [l](https://evil.example) \\( *x*\n&lt;/pre&gt;# h</pre>", markdown)

    def test_unmanaged_repositories_do_not_break_a_green_fleet(self) -> None:
        results = [fleet.Result("quokkify/a", "up-to-date", inventory=self.inventory()),
                   fleet.Result("quokkify/docs", "not-managed"), fleet.Result("quokkify/other", "foreign-template", "gh:acme/t"),
                   fleet.Result("quokkify/skip", "not-applicable")]
        markdown = fleet.markdown_report(results)
        self.assertTrue(markdown.startswith("## Copier fleet: ✅ nothing to do\n\nAll 1 managed repositories are on **v3.2.3**.\n"))
        self.assertIn("| — | [docs](https://github.com/quokkify/docs) | ? | ? | ? |", markdown)
        self.assertIn("| ↗️ | [other](https://github.com/quokkify/other) | ? | ? | ? |", markdown)
        self.assertIn("Legend: ✅ ok · — not managed or not applicable · ↗️ foreign template · `?` no evidence.", markdown)

    def test_pull_request_without_a_parseable_url_stays_in_flight(self) -> None:
        result = fleet.Result("quokkify/a", "pull-request", "created", self.inventory(target="v3.3.0"))
        markdown = fleet.markdown_report([result])
        self.assertTrue(markdown.startswith("## Copier fleet: 🔀 1 PR open\n"))
        self.assertIn("All 1 managed repositories have update PRs open.", markdown)
        self.assertNotIn("need nothing", markdown)
        self.assertIn("1 open, CI unknown (a PR (link unavailable))", markdown)
        self.assertIn("| 🔀 | [a](https://github.com/quokkify/a) | v3.2.3 → v3.3.0 | ? | PR (link unavailable) open ? · CI ? |", markdown)

    def test_failing_ci_on_main_and_expired_exception_need_attention(self) -> None:
        health = {"default_ci": {"checks": [{"context": "Tests", "status": "failed"}, {"context": "Lint", "status": "passed"}]},
                  "required_checks": {"checks": [{"context": "Tests"}, {"context": "Lint"}]},
                  "exception": {"status": "expired", "reason": "waiting on vendor", "expires_on": "2026-09-01"}}
        results = [fleet.Result("quokkify/a", "up-to-date", inventory=self.inventory(), health=health)]
        markdown = fleet.markdown_report(results)
        rows = self.attention_rows(markdown)
        self.assertTrue(markdown.startswith("## Copier fleet: ❌ action required — 1 failing CI on main\n"))
        self.assertEqual(len(rows), 1)
        self.assertIn("CI on `main` failing: Tests. Health exception expired on 2026-09-01: waiting on vendor.", rows[0])
        self.assertIn("**Fix** the failing checks on the default branch. **Review** the exception: renew or remove it.", rows[0])
        self.assertIn("| ❌ | [a](https://github.com/quokkify/a) | v3.2.3 | 1/2 ✅ · ❌ Tests | ? |", markdown)
        expired_only = {"exception": health["exception"]}
        markdown = fleet.markdown_report([fleet.Result("quokkify/a", "up-to-date", inventory=self.inventory(), health=expired_only)])
        self.assertTrue(markdown.startswith("## Copier fleet: ❌ action required — 1 configuration gap\n"))

    def test_rerun_command_only_for_failed_and_carries_requested_template_ref(self) -> None:
        run = fleet.run_metadata("write", "v3.1.0", {}, "v3.1.0")
        self.assertEqual(run["requested_template_ref"], "v3.1.0")
        current = fleet.Result("quokkify/r", "up-to-date")
        failed = fleet.Result("quokkify/f", "failed", "boom")
        markdown = fleet.markdown_report([current, failed], None, run)
        self.assertIn("gh workflow run copier-fleet-auto-update.yml -f repository=quokkify/f -f template-ref=v3.1.0\n", markdown)
        self.assertNotIn("repository=quokkify/r", markdown)
        latest = fleet.markdown_report([failed], None, fleet.run_metadata("write", "v3.3.0", {}))
        self.assertNotIn("template-ref", latest)

    @mock.patch.object(fleet, "discard_if_already_applied", return_value=False)
    @mock.patch.object(fleet, "canonicalize_answers_source")
    @mock.patch.object(fleet, "run")
    def test_real_conflict_error_classifies_as_conflict(self, run_mock: mock.Mock, _: mock.Mock, __: mock.Mock) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            (repository / fleet.ANSWERS_FILE).write_text("_src_path: https://github.com/quokkify/ci-kit.git\n", encoding="utf-8")
            run_mock.side_effect = lambda *args, **kwargs: (repository / "validate.yml.rej").write_text("-x\n", encoding="utf-8")
            with self.assertRaises(fleet.FleetUpdateError) as raised:
                fleet.update_template(repository, template_source="quokkify/ci-kit", template_ref="v2.21.5", env={})
        self.assertTrue(str(raised.exception).startswith(fleet.CONFLICT_PREFIX))
        facts = fleet.attention_facts(fleet.Result("quokkify/a", "failed", str(raised.exception)), None)
        self.assertEqual([fact.bucket for fact in facts], ["conflict"])
        self.assertIn("Copier conflict in validate.yml;", facts[0].happened)

    def test_null_health_sections_render(self) -> None:
        health = {"required_checks": None, "default_ci": None, "pull_request": None}
        markdown = fleet.markdown_report([fleet.Result("quokkify/a", "up-to-date", inventory=self.inventory(), health=health)])
        self.assertTrue(markdown.startswith("## Copier fleet: ✅ nothing to do\n"))
        self.assertIn("| [a](https://github.com/quokkify/a) | v3.2.3 | ? | ? |", markdown)

    def test_legend_lists_only_present_glyphs_and_text_encodes_mentions_and_math(self) -> None:
        results = [fleet.Result("quokkify/ok", "up-to-date", inventory=self.inventory()),
                   fleet.Result("quokkify/bad", "failed", "boom"),
                   fleet.Result("quokkify/drift", "would-update", "x.yml", self.inventory(target="v3.3.0"))]
        markdown = fleet.markdown_report(results)
        self.assertTrue(markdown.endswith("\nLegend: ✅ ok · ❌ failed · 🟡 drift (dry run) · `?` no evidence.\n"))
        self.assertEqual(fleet.markdown_text("@team $x$"), "&#64;team &#36;x&#36;")

    def test_report_round_trips_through_json(self) -> None:
        result = fleet.Result("quokkify/a", "pull-request", "https://github.com/quokkify/a/pull/1",
                              self.inventory(modules=("core",), state="hybrid(validate)"), {"adoption": "different"}, {"findings": []})
        payload = json.loads(fleet.json_report([result], {"pull-request": 1}))
        self.assertEqual(fleet.results_from_report(payload), [result])


class ChecksVerdictTests(TestCase):
    NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)

    @staticmethod
    def check(name="ci / gate", status="COMPLETED", conclusion="SUCCESS", completed="2026-10-22T11:30:00Z"):
        return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion,
                "startedAt": "2026-10-22T11:20:00Z", "completedAt": completed}

    @staticmethod
    def context(name="CodeRabbit", state="SUCCESS"):
        return {"__typename": "StatusContext", "context": name, "state": state, "startedAt": "2026-10-22T11:30:00Z"}

    def test_green_and_settled_is_ready(self) -> None:
        rollup = [self.check(), self.check("x", conclusion="SKIPPED"), self.context()]
        self.assertEqual(fleet.checks_verdict(rollup, self.NOW), ("ready", "all checks green"))

    def test_empty_or_unreadable_rollup_waits(self) -> None:
        for rollup in ([], None, "x"):
            self.assertEqual(fleet.checks_verdict(rollup, self.NOW), ("waiting", "no checks reported yet"))

    def test_running_check_waits(self) -> None:
        rollup = [self.check(status="IN_PROGRESS", conclusion=None, completed=None)]
        self.assertEqual(fleet.checks_verdict(rollup, self.NOW), ("waiting", "checks still running"))

    def test_pending_status_context_waits(self) -> None:
        self.assertEqual(fleet.checks_verdict([self.context(state="PENDING")], self.NOW)[0], "waiting")

    def test_recent_completion_waits(self) -> None:
        rollup = [self.check(completed="2026-10-22T11:55:00Z")]
        self.assertEqual(fleet.checks_verdict(rollup, self.NOW), ("waiting", "checks finished less than 10 minutes ago"))

    def test_missing_or_unparseable_times_wait_instead_of_merging(self) -> None:
        for completed in (None, "", "not-a-date"):
            with self.subTest(completed=completed):
                rollup = [self.check(completed=completed)]
                self.assertEqual(fleet.checks_verdict(rollup, self.NOW), ("waiting", "check times unavailable"))
        context = {"__typename": "StatusContext", "context": "x", "state": "SUCCESS"}
        self.assertEqual(fleet.checks_verdict([context], self.NOW), ("waiting", "check times unavailable"))

    def test_exactly_ten_minutes_old_is_ready(self) -> None:
        rollup = [self.check(completed="2026-10-22T11:50:00Z")]
        self.assertEqual(fleet.checks_verdict(rollup, self.NOW), ("ready", "all checks green"))

    def test_status_context_with_recent_start_waits(self) -> None:
        context = {"__typename": "StatusContext", "context": "x", "state": "SUCCESS", "startedAt": "2026-10-22T11:55:00Z"}
        self.assertEqual(fleet.checks_verdict([context], self.NOW), ("waiting", "checks finished less than 10 minutes ago"))

    def test_failed_checks_named(self) -> None:
        rollup = [self.check("a", conclusion="FAILURE"), self.context("b", "ERROR"), self.check("c", conclusion="CANCELLED")]
        self.assertEqual(fleet.checks_verdict(rollup, self.NOW), ("failed", "failed checks: a, b, c"))

    def test_failure_wins_over_pending(self) -> None:
        rollup = [self.check("a", conclusion="FAILURE"), self.check("b", status="IN_PROGRESS", conclusion=None, completed=None)]
        self.assertEqual(fleet.checks_verdict(rollup, self.NOW), ("failed", "failed checks: a"))


class AutoMergeTests(TestCase):
    URL = "https://github.com/quokkify/example/pull/7"
    HEAD = "c" * 40
    OLD_HEAD = "d" * 40
    REPOSITORY = fleet.Repository("quokkify/example", "main")
    VIEW = ["pr", "view", "7", "--repo", "quokkify/example", "--json",
            "state,baseRefName,mergeable,headRefOid,autoMergeRequest,isCrossRepository,headRefName,statusCheckRollup"]
    NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    DIRECT = ["gh", "pr", "merge", "7", "--repo", "quokkify/example", "--squash", "--match-head-commit", HEAD]
    MERGE = ["gh", "pr", "merge", "7", "--repo", "quokkify/example", "--auto", "--squash", "--match-head-commit", HEAD]
    DISABLE = ["gh", "pr", "merge", "7", "--repo", "quokkify/example", "--disable-auto"]
    HERMETIC_ENV = {"GH_TOKEN": "t", "GITHUB_TOKEN": "", "GITHUB_EVENT_NAME": "", "GITHUB_SERVER_URL": "",
                    "GITHUB_REPOSITORY": "", "GITHUB_RUN_ID": "", "GITHUB_ACTIONS": ""}

    def settings(self, **overrides):
        return {"allow_auto_merge": True, "allow_squash_merge": True, **overrides}

    def pull(self, **overrides):
        return {"state": "OPEN", "baseRefName": "main", "mergeable": "MERGEABLE", "headRefOid": self.HEAD,
                "autoMergeRequest": None, "isCrossRepository": False, "headRefName": fleet.DEFAULT_BRANCH,
                "statusCheckRollup": [self.check()], **overrides}

    def check(self, name="ci / gate", *, status="COMPLETED", conclusion="SUCCESS", completed="2026-10-22T11:00:00Z"):
        return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion, "completedAt": completed}

    def enable(self, *, settings=None, pulls=None, merge=(0, ""), disable=(0, ""), url=None, **overrides):
        arguments = {"branch": fleet.DEFAULT_BRANCH, "head": self.HEAD, "applied": "v3.2.0", "target": "v3.3.0",
                     "requested_template_ref": None, **overrides}
        views = list(pulls or [self.pull()])

        def fake_gh_json(arguments, **_):
            if arguments[0] == "pr":
                if isinstance(views[0], Exception):
                    raise views[0]
                return views.pop(0) if len(views) > 1 else views[0]
            if isinstance(settings, Exception):
                raise settings
            return self.settings() if settings is None else settings

        def fake_run(command, **_):
            returncode, stderr = disable if "--disable-auto" in command else merge
            return subprocess.CompletedProcess(command, returncode, "", stderr)

        with mock.patch.object(fleet, "gh_json", side_effect=fake_gh_json) as gh_mock, \
             mock.patch.object(fleet, "run", side_effect=fake_run) as run_mock, \
             mock.patch.object(fleet.time, "sleep") as sleep_mock:
            outcome = fleet.enable_auto_merge(self.REPOSITORY, url or self.URL, env={}, now=lambda: self.NOW, **arguments)
        self.sleep_mock = sleep_mock
        return outcome, [call.args[0] for call in gh_mock.call_args_list], [call.args[0] for call in run_mock.call_args_list]

    def test_enables_squash_auto_merge_bound_to_the_pushed_head(self) -> None:
        outcome, reads, commands = self.enable()
        self.assertEqual(outcome, {"state": "enabled", "reason": f"auto-merge enabled for {self.HEAD}"})
        self.assertEqual(reads, [self.VIEW, ["api", "repos/quokkify/example"]])
        self.assertEqual(commands, [self.MERGE])

    def test_without_a_push_binds_to_the_current_pull_request_head(self) -> None:
        _, _, commands = self.enable(head=None, pulls=[self.pull(headRefOid=self.OLD_HEAD)])
        self.assertEqual(commands[-1][-1], self.OLD_HEAD)

    def test_ineligible_pull_requests_are_skipped_with_an_exact_reason(self) -> None:
        cases = (
            ({"branch": "feature/other"}, "not the automation update branch"),
            ({"pulls": [self.pull(isCrossRepository=True)]}, "pull request is not the updater's own branch"),
            ({"pulls": [self.pull(headRefName="feature")]}, "pull request is not the updater's own branch"),
            ({"requested_template_ref": "v3.3.0"}, "target is not a release tag resolved by the run"),
            ({"target": "main"}, "target is not a release tag resolved by the run"),
            ({"applied": "0f1e2d3c"}, "applied version is not a release tag"),
            ({"applied": "v3.4.0"}, "target v3.3.0 is older than v3.4.0"),
            ({"applied": "v3.10.0", "target": "v3.9.0"}, "target v3.9.0 is older than v3.10.0"),
            ({"applied": "v2.9.0", "target": "v3.0.0"}, "major version bump v2.9.0 → v3.0.0 needs a human"),
            ({"applied": "v0.3.5", "target": "v0.4.0"}, "major version bump v0.3.5 → v0.4.0 needs a human"),
            ({"modules": [{"module": "core", "from": "v2.9.0"}]}, "major version bump core v2.9.0 → v3.3.0 needs a human"),
            ({"modules": [{"module": "core", "from": ""}]}, "module core applied version is not a release tag"),
            ({"settings": self.settings(allow_squash_merge=False)}, "repository does not allow squash merge"),
            ({"pulls": [self.pull(mergeable="CONFLICTING")]}, "pull request has merge conflicts"),
            ({"pulls": [self.pull(baseRefName="develop")]}, "pull request base is not the default branch"),
            ({"pulls": [self.pull(state="CLOSED")]}, "pull request is not open"),
        )
        for overrides, reason in cases:
            with self.subTest(reason=reason, overrides=overrides):
                outcome, _, commands = self.enable(**overrides)
                self.assertEqual(outcome, {"state": "skipped", "reason": reason})
                self.assertEqual(commands, [])

    def test_auto_merge_disabled_merges_green_pull_request_directly(self) -> None:
        outcome, _, commands = self.enable(settings=self.settings(allow_auto_merge=False))
        self.assertEqual(outcome, {"state": "merged", "reason": f"merged {self.HEAD}"})
        self.assertEqual(commands, [self.DIRECT])

    def test_direct_path_waits_for_running_checks(self) -> None:
        running = self.check(status="IN_PROGRESS", conclusion=None, completed=None)
        outcome, _, commands = self.enable(settings=self.settings(allow_auto_merge=False), pulls=[self.pull(statusCheckRollup=[running])])
        self.assertEqual(outcome["state"], "waiting")
        self.assertEqual(commands, [])

    def test_direct_path_skips_failed_checks(self) -> None:
        failing = self.check(conclusion="FAILURE")
        outcome, _, commands = self.enable(settings=self.settings(allow_auto_merge=False), pulls=[self.pull(statusCheckRollup=[failing])])
        self.assertEqual(outcome, {"state": "skipped", "reason": "failed checks: ci / gate"})
        self.assertEqual(commands, [])

    def test_direct_path_waits_while_mergeability_is_not_computed(self) -> None:
        for mergeable in ("UNKNOWN", None):
            with self.subTest(mergeable=mergeable):
                outcome, _, commands = self.enable(settings=self.settings(allow_auto_merge=False), pulls=[self.pull(mergeable=mergeable)])
                self.assertEqual(outcome, {"state": "waiting", "reason": "mergeability not yet computed"})
                self.assertEqual(commands, [])

    def test_direct_merge_rejected_head_is_failed(self) -> None:
        outcome, _, _ = self.enable(settings=self.settings(allow_auto_merge=False), merge=(1, "head moved"))
        self.assertEqual(outcome, {"state": "failed", "reason": "head moved"})

    def test_direct_path_never_uses_auto_flag(self) -> None:
        armed = self.pull(autoMergeRequest={"mergeMethod": "SQUASH"})
        for pulls in ([self.pull()], [armed], [self.pull(statusCheckRollup=[self.check(conclusion="FAILURE")])]):
            _, _, commands = self.enable(settings=self.settings(allow_auto_merge=False), pulls=pulls)
            for command in commands:
                self.assertNotIn("--auto", command)

    def test_minor_patch_and_repair_only_updates_are_eligible(self) -> None:
        for applied, target, modules in (("v3.2.9", "v3.3.0", []), ("v3.3.0", "v3.3.1", []), ("v0.4.0", "v0.4.1", []),
                                         ("v3.3.0", "v3.3.0", []), ("v0.4.1", "v0.4.1", [{"module": "core", "from": "v0.4.1"}]),
                                         ("v3.2.0", "v3.3.0", [{"module": "core", "from": "v3.1.0"}])):
            with self.subTest(applied=applied, target=target):
                self.assertEqual(self.enable(applied=applied, target=target, modules=modules)[0]["state"], "enabled")

    def test_missing_or_unreadable_settings_fail_rather_than_skip(self) -> None:
        for settings in ({"allow_squash_merge": True}, {"allow_auto_merge": True}, [], "nope"):
            with self.subTest(settings=settings):
                outcome, _, commands = self.enable(settings=settings)
                self.assertEqual(outcome, {"state": "failed", "reason": "repository auto-merge setting unreadable (token permissions?)"})
                self.assertEqual(commands, [])

    def test_failing_reads_and_unexpected_errors_fail_the_auto_merge_only(self) -> None:
        cases = (
            ({"pulls": [fleet.FleetUpdateError("gh pr view failed (1): no pull requests found")]}, "gh pr view failed (1): no pull requests found"),
            ({"settings": fleet.FleetUpdateError("gh api failed (1): HTTP 403")}, "gh api failed (1): HTTP 403"),
            ({"settings": KeyError("boom")}, "'boom'"),
        )
        for overrides, reason in cases:
            with self.subTest(reason=reason):
                outcome, _, commands = self.enable(**overrides)
                self.assertEqual(outcome, {"state": "failed", "reason": reason})
                self.assertEqual(commands, [])

    def test_pull_request_url_of_another_repository_fails_before_any_call(self) -> None:
        outcome, reads, commands = self.enable(url="https://github.com/someone/fork/pull/7")
        self.assertEqual(outcome, {"state": "failed", "reason": "pull request URL does not belong to the repository"})
        self.assertEqual((reads, commands), ([], []))

    def test_rejected_merge_request_fails_with_the_sanitized_github_message(self) -> None:
        outcome, _, _ = self.enable(merge=(1, "Head branch was modified\x1b[0m\n"))
        self.assertEqual(outcome, {"state": "failed", "reason": "Head branch was modified\\x1b[0m"})

    def test_already_merged_pull_request_is_reported_without_a_request(self) -> None:
        outcome, reads, commands = self.enable(pulls=[self.pull(state="MERGED")])
        self.assertEqual(outcome["state"], "merged")
        self.assertEqual((reads, commands), ([self.VIEW], []))

    def test_auto_merge_already_enabled_on_the_same_head_is_not_requested_again(self) -> None:
        outcome, _, commands = self.enable(pulls=[self.pull(autoMergeRequest={"mergeMethod": "SQUASH"})])
        self.assertEqual(outcome, {"state": "enabled", "reason": "auto-merge already enabled"})
        self.assertEqual(commands, [])

    def test_after_this_run_disarmed_a_stale_read_still_gets_a_fresh_request(self) -> None:
        armed = self.pull(autoMergeRequest={"mergeMethod": "SQUASH"})
        outcome, _, commands = self.enable(pulls=[armed], disarmed=True)
        self.assertEqual(outcome, {"state": "enabled", "reason": f"auto-merge enabled for {self.HEAD}"})
        self.assertEqual(commands, [self.MERGE])
        outcome, _, commands = self.enable(pulls=[armed], disarmed=True, applied="v2.9.0")
        self.assertEqual(outcome, {"state": "skipped", "reason": "major version bump v2.9.0 → v3.3.0 needs a human"})
        self.assertEqual(commands, [])

    def test_ineligible_pull_request_with_armed_auto_merge_is_disarmed(self) -> None:
        armed = self.pull(autoMergeRequest={"mergeMethod": "SQUASH"})
        outcome, _, commands = self.enable(pulls=[armed], applied="v2.9.0")
        self.assertEqual(outcome, {"state": "skipped", "reason": "major version bump v2.9.0 → v3.3.0 needs a human; existing auto-merge disabled"})
        self.assertEqual(commands, [self.DISABLE])
        outcome, _, _ = self.enable(pulls=[armed], applied="v2.9.0", disable=(1, "HTTP 403"))
        self.assertEqual(outcome, {"state": "failed", "reason": "major version bump v2.9.0 → v3.3.0 needs a human; could not disable existing auto-merge: HTTP 403"})
        outcome, _, commands = self.enable(pulls=[armed], merge=(1, "x"), settings={"allow_auto_merge": True})
        self.assertEqual(outcome["reason"], "repository auto-merge setting unreadable (token permissions?); existing auto-merge disabled")
        self.assertEqual(commands, [self.DISABLE])

    def test_lagging_pull_request_head_is_reread_then_skipped(self) -> None:
        stale = self.pull(headRefOid=self.OLD_HEAD)
        outcome, reads, commands = self.enable(pulls=[stale, stale, self.pull()])
        self.assertEqual(outcome["state"], "enabled")
        self.assertEqual(reads.count(self.VIEW), 3)
        self.assertEqual(commands, [self.MERGE])
        outcome, reads, commands = self.enable(pulls=[stale])
        self.assertEqual(outcome, {"state": "skipped", "reason": "pull request head not yet updated"})
        self.assertEqual((reads.count(self.VIEW), self.sleep_mock.call_count, commands), (4, 3, []))

    def test_any_failed_disable_request_is_an_error(self) -> None:
        for returncode, stderr, expected in ((0, "", None), (1, "GraphQL: Auto merge is not enabled", "GraphQL: Auto merge is not enabled"),
                                             (1, "HTTP 403: Resource not accessible", "HTTP 403: Resource not accessible"),
                                             (1, "", "gh pr merge --disable-auto exited 1")):
            with self.subTest(stderr=stderr), \
                 mock.patch.object(fleet, "run", return_value=subprocess.CompletedProcess([], returncode, "", stderr)) as run_mock:
                self.assertEqual(fleet.disable_auto_merge(self.REPOSITORY, "7", env={}), expected)
                run_mock.assert_called_once_with(self.DISABLE, env={}, check=False)

    def test_force_push_reports_the_replaced_remote_head(self) -> None:
        outputs = {"ls-remote": self.OLD_HEAD + "\trefs/heads/branch\n", "HEAD^{tree}": "t" * 40, "HEAD^": "p" * 40, "HEAD": self.HEAD}

        def fake_run(command, **_):
            key = next((arg for arg in reversed(command) if arg in outputs), None)
            return subprocess.CompletedProcess(command, 0, outputs.get(key, ""), "")

        for remote_tree, expected in (("t" * 40, []), ("x" * 40, [self.OLD_HEAD])):
            remote = {"commit": {"tree": {"sha": remote_tree}}, "parents": [{"sha": "p" * 40}]}
            replaced: list[str] = []
            with self.subTest(remote_tree=remote_tree), mock.patch.object(fleet, "run", side_effect=fake_run), \
                 mock.patch.object(fleet, "gh_json", return_value=remote), tempfile.TemporaryDirectory() as temporary:
                fleet.push_automation_branch(self.REPOSITORY, Path(temporary), branch="branch", env={}, replaced=replaced)
            self.assertEqual(replaced, expected)

    def test_only_the_repositorys_own_pull_request_is_reused(self) -> None:
        fork = {"url": "https://github.com/someone/example/pull/9", "number": 9, "isCrossRepository": True,
                "headRepositoryOwner": {"login": "someone"}, "headRefName": fleet.DEFAULT_BRANCH}
        other = {**fork, "url": "https://github.com/quokkify/example/pull/8", "isCrossRepository": False,
                 "headRepositoryOwner": {"login": "Quokkify"}, "headRefName": "other"}
        own = {**other, "url": self.URL, "headRefName": fleet.DEFAULT_BRANCH}
        for listed, expected in (([fork, other, own], self.URL), ([fork, other], "https://github.com/quokkify/example/pull/10")):
            with self.subTest(expected=expected), mock.patch.object(fleet, "gh_json", return_value=listed) as gh_mock, \
                 mock.patch.object(fleet, "run", return_value=subprocess.CompletedProcess([], 0, "https://github.com/quokkify/example/pull/10\n", "")) as run_mock:
                url = fleet.ensure_pull_request(self.REPOSITORY, branch=fleet.DEFAULT_BRANCH, changed=["a.yml"], env={})
            self.assertEqual(url, expected)
            self.assertEqual(gh_mock.call_args.args[0][-1], "url,number,isCrossRepository,headRepositoryOwner,headRefName")
            self.assertEqual(run_mock.called, expected != self.URL)

    def test_pull_request_body_mentions_auto_merge_only_with_the_flag(self) -> None:
        bodies = {}
        for auto_merge in (False, True):
            with mock.patch.object(fleet, "gh_json", return_value=[]), \
                 mock.patch.object(fleet, "run", return_value=subprocess.CompletedProcess([], 0, self.URL, "")) as run_mock:
                fleet.ensure_pull_request(self.REPOSITORY, branch=fleet.DEFAULT_BRANCH, changed=["a.yml"], env={}, auto_merge=auto_merge)
            command = run_mock.call_args.args[0]
            bodies[auto_merge] = command[command.index("--body") + 1]
        self.assertIn("while leaving merge approval to maintainers.", bodies[False])
        self.assertNotIn("leaving merge approval", bodies[True])
        self.assertIn("may enable auto-merge when the repository allows it; major version bumps always wait for a maintainer.", bodies[True])

    def write_update(self, *, replace_head=False, disable=(0, ""), armed=True, **kwargs):
        def push(*_, replaced=None, **__):
            if replace_head:
                replaced.append(self.OLD_HEAD)
            return self.HEAD

        with mock.patch.object(fleet, "fetch_answers_files", return_value={"legacy": "_src_path: gh:quokkify/ci-kit\n"}), \
             mock.patch.object(fleet, "clone_repository", side_effect=cloned_answers("_commit: v3.2.0\n_src_path: gh:quokkify/ci-kit\n")), \
             mock.patch.object(fleet, "update_template", return_value=[".github/workflows/validate.yml"]), \
             mock.patch.object(fleet, "push_automation_branch", side_effect=push), \
             mock.patch.object(fleet, "ensure_pull_request", return_value=self.URL) as pr_mock, \
             mock.patch.object(fleet, "gh_json", side_effect=armed if isinstance(armed, Exception) else None,
                               return_value={"autoMergeRequest": {"mergeMethod": "SQUASH"} if armed else None}) as gh_mock, \
             mock.patch.object(fleet, "run", return_value=subprocess.CompletedProcess([], *disable[:1], "", disable[1])) as run_mock, \
             mock.patch.object(fleet, "enable_auto_merge", return_value={"state": "enabled", "reason": "r"}) as merge_mock, \
             tempfile.TemporaryDirectory() as temporary:
            result = fleet.process_repository(self.REPOSITORY, expected_template="quokkify/ci-kit", branch=fleet.DEFAULT_BRANCH,
                                              dry_run=False, template_ref="v3.3.0", env={}, workspace=Path(temporary), **kwargs)
        self.reads = [call.args[0] for call in gh_mock.call_args_list]
        return result, merge_mock, pr_mock, [call.args[0] for call in run_mock.call_args_list]

    def test_write_update_evaluates_the_pull_request_it_refreshed(self) -> None:
        result, merge_mock, pr_mock, commands = self.write_update(auto_merge=True, requested_template_ref=None)
        self.assertEqual(result.auto_merge, {"state": "enabled", "reason": "r"})
        self.assertTrue(pr_mock.call_args.kwargs["auto_merge"])
        self.assertEqual(merge_mock.call_args.args, (self.REPOSITORY, self.URL))
        self.assertEqual(merge_mock.call_args.kwargs, {"branch": fleet.DEFAULT_BRANCH, "head": self.HEAD, "applied": "v3.2.0",
                                                       "target": "v3.3.0", "requested_template_ref": None, "env": {}, "modules": [],
                                                       "disarmed": False})
        self.assertEqual((commands, self.reads), ([], []))

    def test_write_update_without_the_flag_never_enables_auto_merge(self) -> None:
        result, merge_mock, pr_mock, commands = self.write_update()
        self.assertIsNone(result.auto_merge)
        merge_mock.assert_not_called()
        self.assertNotIn("auto_merge", pr_mock.call_args.kwargs)
        self.assertEqual(commands, [])

    def test_new_head_on_an_existing_pull_request_disarms_auto_merge_even_without_the_flag(self) -> None:
        result, merge_mock, _, commands = self.write_update(replace_head=True)
        self.assertEqual(self.reads, [["pr", "view", "7", "--repo", "quokkify/example", "--json", "autoMergeRequest"]])
        self.assertEqual(commands, [self.DISABLE])
        self.assertIsNone(result.auto_merge)
        merge_mock.assert_not_called()

    def test_new_head_without_armed_auto_merge_sends_no_disable_request(self) -> None:
        result, merge_mock, _, commands = self.write_update(replace_head=True, armed=False, auto_merge=True)
        self.assertEqual(commands, [])
        self.assertFalse(merge_mock.call_args.kwargs["disarmed"])

    def test_failed_disarm_after_a_new_head_is_recorded_on_the_pull_request(self) -> None:
        for overrides, reason in (({"disable": (1, "HTTP 403")}, "HTTP 403"),
                                  ({"armed": fleet.FleetUpdateError("gh pr view failed (1): HTTP 502")}, "gh pr view failed (1): HTTP 502")):
            with self.subTest(reason=reason):
                result, merge_mock, _, _ = self.write_update(replace_head=True, auto_merge=True, **overrides)
                self.assertEqual(result.status, "pull-request")
                self.assertEqual(result.auto_merge, {"state": "failed", "reason": f"could not disable auto-merge after pushing a new head: {reason}"})
                merge_mock.assert_not_called()

    def test_disarmed_new_head_is_passed_to_the_fresh_auto_merge_request(self) -> None:
        _, merge_mock, _, commands = self.write_update(replace_head=True, auto_merge=True)
        self.assertEqual(commands, [self.DISABLE])
        self.assertTrue(merge_mock.call_args.kwargs["disarmed"])

    def test_flag_is_rejected_with_dry_run(self) -> None:
        with mock.patch("sys.stderr", new_callable=StringIO) as stderr, self.assertRaises(SystemExit) as exit_context:
            fleet.main(["--dry-run", "--auto-merge"])
        self.assertEqual(exit_context.exception.code, 2)
        self.assertIn("--auto-merge cannot be combined", stderr.getvalue())

    def test_run_block_records_the_flag_and_forwards_it(self) -> None:
        outcome = fleet.Result("quokkify/example", "pull-request", self.URL, auto_merge={"state": "failed", "reason": "r"})
        with mock.patch.dict("os.environ", self.HERMETIC_ENV), \
             mock.patch.object(fleet.shutil, "which", return_value="/usr/bin/tool"), \
             mock.patch.object(fleet, "discover_repositories", return_value=[self.REPOSITORY]), \
             mock.patch.object(fleet, "resolve_template_ref", return_value="v3.3.0"), \
             mock.patch.object(fleet, "process_repository", return_value=outcome) as process_mock, \
             redirect_stdout(StringIO()), tempfile.TemporaryDirectory() as temporary:
            report_path = Path(temporary) / "report.json"
            self.assertEqual(fleet.main(["--write", "--auto-merge", "--json-report", str(report_path)]), 0)
            report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual((process_mock.call_args.kwargs["auto_merge"], process_mock.call_args.kwargs["requested_template_ref"]), (True, None))
        self.assertEqual(report["schema_version"], 6)
        self.assertEqual((report["run"]["auto_merge"], report["run"]["trigger"], report["run"]["run_url"]), (True, None, None))
        self.assertEqual(report["repositories"][0]["auto_merge"], {"state": "failed", "reason": "r"})
        self.assertFalse(fleet.run_metadata("write", "v3.3.0", {})["auto_merge"])

    def test_json_report_round_trips_and_stays_additive(self) -> None:
        merged = fleet.Result("quokkify/a", "pull-request", "https://github.com/quokkify/a/pull/1", auto_merge={"state": "merged", "reason": "r"})
        plain = fleet.Result("quokkify/b", "up-to-date")
        payload = json.loads(fleet.json_report([merged, plain], {}))
        self.assertNotIn("auto_merge", payload["repositories"][1])
        self.assertEqual(fleet.results_from_report(payload), [merged, plain])

    def test_failed_auto_merge_is_an_attention_row_and_progress_is_context(self) -> None:
        def result(name, number, state):
            return fleet.Result(f"quokkify/{name}", "pull-request", f"https://github.com/quokkify/{name}/pull/{number}",
                                auto_merge={"state": state, "reason": "Head branch was modified" if state == "failed" else "r"})
        markdown = fleet.markdown_report([result("a", 1, "failed"), result("b", 2, "enabled"), result("c", 3, "merged"), result("d", 4, "skipped"), result("e", 5, "waiting")])
        self.assertTrue(markdown.startswith("## Copier fleet: ❌ action required — 1 auto-merge failed\n"))
        self.assertIn("### Needs your attention — 1", markdown)
        self.assertIn("Auto-merge could not be enabled on PR [#1](https://github.com/quokkify/a/pull/1): Head branch was modified.", markdown)
        self.assertIn("**Fix** the cause", markdown)
        self.assertIn(" · 1 auto-merging · 1 merged · 1 auto-merge waiting on CI", markdown)
        self.assertNotIn("[d](https://github.com/quokkify/d) | Auto-merge", markdown)


class CommandLineTests(TestCase):
    def test_migrate_flag_is_gone(self) -> None:
        stderr = StringIO()
        with mock.patch.object(sys, "stderr", stderr), self.assertRaises(SystemExit) as raised:
            fleet.main(["--dry-run", "--migrate", "core"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--migrate", stderr.getvalue())

    @mock.patch.object(fleet, "discover_repositories", return_value=[])
    @mock.patch.object(fleet.shutil, "which", return_value="/usr/bin/tool")
    @mock.patch.dict(
        "os.environ",
        {
            "GITHUB_TOKEN": "repository-scoped-token",
            "GH_TOKEN": "ambient-codeowner-token",
        },
        clear=False,
    )
    def test_dry_run_prefers_repository_scoped_token(
        self,
        _: mock.Mock,
        discover_mock: mock.Mock,
    ) -> None:
        self.assertEqual(fleet.main(["--dry-run"]), 0)
        self.assertEqual(discover_mock.call_args.kwargs["env"]["GH_TOKEN"], "repository-scoped-token")

    @mock.patch.object(fleet, "discover_repositories", return_value=[])
    @mock.patch.object(fleet.shutil, "which", return_value="/usr/bin/tool")
    @mock.patch.dict(
        "os.environ",
        {
            "GITHUB_TOKEN": "repository-scoped-token",
            "GH_TOKEN": "ambient-codeowner-token",
            "PATH": "/opt/toolchain/bin",
        },
        clear=False,
    )
    def test_command_lookup_uses_process_path_only(
        self,
        which_mock: mock.Mock,
        _: mock.Mock,
    ) -> None:
        self.assertEqual(fleet.main(["--dry-run"]), 0)
        self.assertEqual(
            [call.args[0] for call in which_mock.call_args_list],
            ["copier", "gh", "git"],
        )
        self.assertTrue(
            all(call.kwargs.get("path") == "/opt/toolchain/bin" for call in which_mock.call_args_list)
        )

    @mock.patch.object(
        fleet,
        "process_repository",
        return_value=fleet.Result("quokkify/example", "would-update", "README.md"),
    )
    @mock.patch.object(
        fleet,
        "discover_repositories",
        return_value=[fleet.Repository("quokkify/example", "main")],
    )
    @mock.patch.object(fleet.shutil, "which", return_value="/usr/bin/tool")
    @mock.patch.dict(
        "os.environ",
        {"GITHUB_TOKEN": "repository-scoped-token", "GH_TOKEN": ""},
        clear=False,
    )
    def test_dry_run_returns_distinct_status_when_drift_exists(
        self,
        _: mock.Mock,
        __: mock.Mock,
        process_mock: mock.Mock,
    ) -> None:
        self.assertEqual(fleet.main(["--dry-run", "--template-ref", "v2.10.1"]), 3)
        process_mock.assert_called_once()

    @mock.patch.object(fleet, "process_repository")
    @mock.patch.object(
        fleet,
        "discover_repositories",
        return_value=[fleet.Repository("quokkify/example", "main")],
    )
    @mock.patch.object(fleet.shutil, "which", return_value="/usr/bin/tool")
    @mock.patch.dict(
        "os.environ",
        {"GITHUB_TOKEN": "repository-scoped-token", "GH_TOKEN": ""},
        clear=False,
    )
    def test_failed_repository_keeps_inventory_in_json_report(
        self,
        _: mock.Mock,
        __: mock.Mock,
        process_mock: mock.Mock,
    ) -> None:
        inventory = fleet.TemplateInventory(
            commit="v2.8.2",
            target_commit=None,
            components=("none",),
            baseline="2/2",
            missing_baseline=(),
            docker="disabled",
            codeql="disabled",
            allure_report="disabled",
            release_please="disabled",
            renovate="enabled",
        )
        process_mock.side_effect = fleet.RepositoryProcessError("conflict", inventory)
        with tempfile.TemporaryDirectory() as temporary:
            report_path = Path(temporary) / "audit.json"
            status = fleet.main(
                [
                    "--dry-run",
                    "--template-ref",
                    "v2.10.1",
                    "--json-report",
                    str(report_path),
                ]
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(status, 1)
        self.assertEqual(report["repositories"][0]["status"], "failed")
        self.assertEqual(report["repositories"][0]["template"]["commit"], "v2.8.2")

    @mock.patch.dict(
        "os.environ",
        {"GH_TOKEN": "", "GITHUB_TOKEN": ""},
        clear=False,
    )
    def test_write_mode_requires_codeowner_session_token(self) -> None:
        self.assertEqual(fleet.main(["--write", "--repo", "quokkify/example"]), 2)

    def test_failed_checks_skip_is_a_pr_ci_attention_row(self) -> None:
        url = "https://github.com/quokkify/a/pull/1"
        result = fleet.Result("quokkify/a", "pull-request", url, auto_merge={"state": "skipped", "reason": "failed checks: ci / gate"})
        markdown = fleet.markdown_report([result])
        self.assertIn("### Needs your attention — 1", markdown)
        self.assertIn("PR [#1](https://github.com/quokkify/a/pull/1) open", markdown)
        self.assertIn("failed checks&#58; ci / gate", markdown)
        quiet = fleet.Result("quokkify/b", "pull-request", "https://github.com/quokkify/b/pull/2", auto_merge={"state": "skipped", "reason": "major version bump"})
        self.assertNotIn("Needs your attention", fleet.markdown_report([quiet]))

    def test_merged_pull_request_is_not_treated_as_open(self) -> None:
        url = "https://github.com/quokkify/a/pull/1"
        merged = fleet.Result("quokkify/a", "pull-request", url, auto_merge={"state": "merged", "reason": "r"})
        self.assertEqual(fleet.automation_pull_request(merged)["state"], "merged")
        waiting = fleet.Result("quokkify/a", "pull-request", url, auto_merge={"state": "waiting", "reason": "r"})
        self.assertEqual(fleet.automation_pull_request(waiting)["state"], "open")
        self.assertEqual(fleet.automation_pull_request(fleet.Result("quokkify/a", "pull-request", url))["state"], "open")


class MergeReadyTests(TestCase):
    URL = "https://github.com/quokkify/example/pull/7"
    HEAD = "c" * 40
    REPOSITORY = fleet.Repository("quokkify/example", "main")
    BASE = {".copier-answers.yml": "v3.2.0", ".copier-answers.core.yml": "v3.2.0"}
    UPDATED = {".copier-answers.yml": "v3.3.0", ".copier-answers.core.yml": "v3.3.0"}

    TRUSTED = "gh:quokkify/ci-kit"

    def answers(self, versions, sources=None):
        sources = sources or {}
        return {file: {"_commit": commit, "_src_path": sources.get(file, self.TRUSTED)} for file, commit in versions.items()}

    def merge_ready(self, *, pulls=None, base=None, head=None, base_sources=None, head_sources=None):
        pulls = [{"url": self.URL, "headRefOid": self.HEAD}] if pulls is None else pulls
        parsed = {
            "main": self.answers(self.BASE if base is None else base, base_sources),
            self.HEAD: self.answers(self.UPDATED if head is None else head, head_sources),
        }
        outcome = {"state": "merged", "reason": f"merged {self.HEAD}"}
        with mock.patch.object(fleet, "gh_json", return_value=pulls) as gh_mock, \
             mock.patch.object(fleet, "answers_at", side_effect=lambda _, ref, **__: parsed[ref]), \
             mock.patch.object(fleet, "enable_auto_merge", return_value=outcome) as merge_mock, \
             mock.patch.object(fleet, "run") as run_mock:
            result = fleet.merge_ready_repository(
                self.REPOSITORY, branch=fleet.DEFAULT_BRANCH, env={}, expected_template=fleet.DEFAULT_TEMPLATE_REPOSITORY,
            )
        self.gh_mock, self.merge_mock, self.run_mock = gh_mock, merge_mock, run_mock
        return result

    def parse_error(self, argv, message):
        stderr = StringIO()
        with mock.patch.object(sys, "stderr", stderr), self.assertRaises(SystemExit):
            fleet.main(argv)
        self.assertIn(message, stderr.getvalue())

    def test_parser_requires_write_and_auto_merge(self) -> None:
        required = "--merge-ready requires --write and --auto-merge"
        self.parse_error(["--merge-ready", "--write"], required)
        self.parse_error(["--merge-ready", "--dry-run"], required)
        self.parse_error(["--merge-ready", "--dry-run", "--auto-merge"], "--auto-merge cannot be combined with --dry-run")

    def test_parser_rejects_template_ref(self) -> None:
        self.parse_error(["--merge-ready", "--write", "--auto-merge", "--template-ref", "v3.3.0"], "--merge-ready cannot be combined with --template-ref")

    def test_no_open_pull_request_is_up_to_date_without_writes(self) -> None:
        result = self.merge_ready(pulls=[])
        self.assertEqual(result, fleet.Result("quokkify/example", "up-to-date"))
        self.assertEqual(self.gh_mock.call_args.args[0], [
            "pr", "list", "--repo", "quokkify/example", "--head", fleet.DEFAULT_BRANCH,
            "--state", "open", "--json", "url,headRefOid",
        ])
        self.merge_mock.assert_not_called()
        self.assertFalse(any("merge" in call.args[0] for call in self.run_mock.call_args_list))

    def test_versions_come_from_base_and_head(self) -> None:
        result = self.merge_ready()
        self.assertEqual((result.status, result.detail), ("pull-request", self.URL))
        self.assertEqual(result.auto_merge, {"state": "merged", "reason": f"merged {self.HEAD}"})
        args, kwargs = self.merge_mock.call_args
        self.assertEqual(args, (self.REPOSITORY, self.URL))
        self.assertEqual(kwargs["branch"], fleet.DEFAULT_BRANCH)
        self.assertEqual((kwargs["applied"], kwargs["target"]), ("v3.2.0", "v3.3.0"))
        self.assertEqual(kwargs["modules"], [{"module": "core", "from": "v3.2.0"}])
        self.assertEqual(kwargs["head"], self.HEAD)
        self.assertIsNone(kwargs["requested_template_ref"])

    def test_merge_binds_to_the_inspected_head(self) -> None:
        moved = "e" * 40
        pull = {"state": "OPEN", "baseRefName": "main", "mergeable": "MERGEABLE", "headRefOid": moved,
                "autoMergeRequest": None, "isCrossRepository": False, "headRefName": fleet.DEFAULT_BRANCH,
                "statusCheckRollup": []}

        def fake_gh_json(arguments, **_):
            if arguments[:2] == ["pr", "list"]:
                return [{"url": self.URL, "headRefOid": self.HEAD}]
            if arguments[:2] == ["pr", "view"]:
                return pull
            return {"allow_auto_merge": True, "allow_squash_merge": True}

        parsed = {"main": self.answers(self.BASE), self.HEAD: self.answers(self.UPDATED)}
        with mock.patch.object(fleet, "gh_json", side_effect=fake_gh_json), \
             mock.patch.object(fleet, "answers_at", side_effect=lambda _, ref, **__: parsed[ref]), \
             mock.patch.object(fleet, "run") as run_mock, \
             mock.patch.object(fleet.time, "sleep"):
            result = fleet.merge_ready_repository(
                self.REPOSITORY, branch=fleet.DEFAULT_BRANCH, env={}, expected_template=fleet.DEFAULT_TEMPLATE_REPOSITORY,
            )
        self.assertEqual(result.auto_merge, {"state": "skipped", "reason": "pull request head not yet updated"})
        self.assertFalse(any("merge" in call.args[0] for call in run_mock.call_args_list))

    def real_merge_ready(self, rollup, *, mergeable="MERGEABLE"):
        pull = {"state": "OPEN", "baseRefName": "main", "mergeable": mergeable, "headRefOid": self.HEAD,
                "autoMergeRequest": None, "isCrossRepository": False, "headRefName": fleet.DEFAULT_BRANCH,
                "statusCheckRollup": rollup}

        def fake_gh_json(arguments, **_):
            if arguments[:2] == ["pr", "list"]:
                return [{"url": self.URL, "headRefOid": self.HEAD}]
            if arguments[:2] == ["pr", "view"]:
                return pull
            return {"allow_auto_merge": False, "allow_squash_merge": True}

        parsed = {"main": self.answers(self.BASE), self.HEAD: self.answers(self.UPDATED)}
        with mock.patch.object(fleet, "gh_json", side_effect=fake_gh_json), \
             mock.patch.object(fleet, "answers_at", side_effect=lambda _, ref, **__: parsed[ref]), \
             mock.patch.object(fleet, "run", return_value=subprocess.CompletedProcess([], 0, "", "")) as run_mock, \
             mock.patch.object(fleet.time, "sleep"):
            result = fleet.merge_ready_repository(
                self.REPOSITORY, branch=fleet.DEFAULT_BRANCH, env={}, expected_template=fleet.DEFAULT_TEMPLATE_REPOSITORY,
            )
        return result, [call.args[0] for call in run_mock.call_args_list]

    def test_direct_path_merges_green_pull_request_with_pinned_head(self) -> None:
        green = {"__typename": "CheckRun", "name": "ci / gate", "status": "COMPLETED", "conclusion": "SUCCESS", "completedAt": "2020-01-01T00:00:00Z"}
        result, commands = self.real_merge_ready([green])
        self.assertEqual(result.auto_merge, {"state": "merged", "reason": f"merged {self.HEAD}"})
        self.assertEqual(commands, [["gh", "pr", "merge", "7", "--repo", "quokkify/example", "--squash", "--match-head-commit", self.HEAD]])
        self.assertNotIn("--auto", commands[0])

    def test_direct_path_waits_for_running_check_without_merging(self) -> None:
        running = {"__typename": "CheckRun", "name": "ci / gate", "status": "IN_PROGRESS", "conclusion": None, "completedAt": None}
        result, commands = self.real_merge_ready([running])
        self.assertEqual(result.auto_merge, {"state": "waiting", "reason": "checks still running"})
        self.assertEqual(commands, [])

    def test_module_head_version_must_match_the_target(self) -> None:
        result = self.merge_ready(
            base={".copier-answers.core.yml": "v3.2.0", ".copier-answers.java.yml": "v3.1.0"},
            head={".copier-answers.core.yml": "v3.3.0", ".copier-answers.java.yml": "v4.0.0"},
        )
        self.assertEqual(result.auto_merge, {"state": "skipped", "reason": "module java head v4.0.0 does not match target v3.3.0"})
        self.merge_mock.assert_not_called()

    def test_untrusted_template_sources_are_skipped(self) -> None:
        core, legacy = ".copier-answers.core.yml", ".copier-answers.yml"
        cases = (
            ({"head_sources": {core: "gh:evil/ci-kit"}}, core),
            ({"base_sources": {legacy: "gh:evil/template"}, "head_sources": {legacy: "gh:evil/template"}}, legacy),
            ({"head_sources": {core: "https://github.com/quokkify/ci-kit"}}, core),
        )
        for overrides, file in cases:
            with self.subTest(overrides=overrides):
                result = self.merge_ready(**overrides)
                self.assertEqual(result.auto_merge, {"state": "skipped", "reason": f"untrusted template source in {file}"})
                self.merge_mock.assert_not_called()

    def test_module_only_repository_uses_the_primary_module_versions(self) -> None:
        self.merge_ready(
            base={".copier-answers.core.yml": "v3.2.0", ".copier-answers.java.yml": "v3.1.0"},
            head={".copier-answers.core.yml": "v3.3.0", ".copier-answers.java.yml": "v3.3.0"},
        )
        kwargs = self.merge_mock.call_args.kwargs
        self.assertEqual((kwargs["applied"], kwargs["target"]), ("v3.2.0", "v3.3.0"))
        self.assertEqual(kwargs["modules"], [{"module": "java", "from": "v3.1.0"}])

    def test_added_or_removed_answers_file_is_skipped(self) -> None:
        for base, head in ((
            {".copier-answers.yml": "v3.2.0"}, self.UPDATED,
        ), (
            self.BASE, {".copier-answers.yml": "v3.3.0"},
        )):
            with self.subTest(base=base, head=head):
                result = self.merge_ready(base=base, head=head)
                self.assertEqual((result.status, result.detail), ("pull-request", self.URL))
                self.assertEqual(result.auto_merge, {"state": "skipped", "reason": "answers files differ: .copier-answers.core.yml"})
                self.merge_mock.assert_not_called()

    def test_answers_versions_reads_each_root_answers_file_at_the_ref(self) -> None:
        listing = [entry(".copier-answers.yml"), entry(".copier-answers.core.yml"), entry("README.md")]
        texts = {".copier-answers.yml": "_commit: v3.3.0\n", ".copier-answers.core.yml": "_src_path: gh:quokkify/ci-kit\n"}

        def fake_run(command, **_):
            endpoint = next(arg for arg in command if arg.startswith("repos/"))
            path, _, query = endpoint.partition("?")
            self.assertEqual(query, f"ref={self.HEAD}")
            if path.endswith("/contents/"):
                return subprocess.CompletedProcess(command, 0, json.dumps(listing), "")
            return subprocess.CompletedProcess(command, 0, texts[path.rsplit("/", 1)[1]], "")

        with mock.patch.object(fleet, "run", side_effect=fake_run):
            versions = fleet.answers_versions(self.REPOSITORY, self.HEAD, env={})
        self.assertEqual(versions, {".copier-answers.yml": "v3.3.0", ".copier-answers.core.yml": None})

    @mock.patch.dict("os.environ", {"GH_TOKEN": "t"}, clear=False)
    def test_main_routes_merge_ready_and_never_clones(self) -> None:
        outcome = fleet.Result("quokkify/example", "pull-request", self.URL, auto_merge={"state": "merged", "reason": "merged"})
        with mock.patch.object(fleet.shutil, "which", return_value="/usr/bin/tool"), \
             mock.patch.object(fleet, "discover_repositories", return_value=[self.REPOSITORY, fleet.Repository("quokkify/skip", "main")]), \
             mock.patch.object(fleet, "resolve_template_ref") as resolve_mock, \
             mock.patch.object(fleet, "merge_ready_repository", return_value=outcome) as merge_ready_mock, \
             mock.patch.object(fleet, "process_repository") as process_mock, \
             mock.patch.object(fleet, "clone_repository") as clone_mock, \
             redirect_stdout(StringIO()):
            status = fleet.main(["--write", "--auto-merge", "--merge-ready", "--exclude", "quokkify/skip"])
        self.assertEqual(status, 0)
        merge_ready_mock.assert_called_once()
        self.assertEqual(merge_ready_mock.call_args.args, (self.REPOSITORY,))
        self.assertEqual(merge_ready_mock.call_args.kwargs["branch"], fleet.DEFAULT_BRANCH)
        self.assertEqual(merge_ready_mock.call_args.kwargs["expected_template"], fleet.DEFAULT_TEMPLATE_REPOSITORY)
        process_mock.assert_not_called()
        clone_mock.assert_not_called()
        resolve_mock.assert_not_called()


class ReleaseHelperDriftTests(TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repository = Path(self.temporary.name)
        (self.repository / fleet.ANSWERS_FILE).write_text(
            "release_please: true\n", encoding="utf-8"
        )

    @mock.patch.object(fleet, "run")
    def test_matching_template_helper_is_healthy(self, run_mock: mock.Mock) -> None:
        helper = self.repository / ".github/scripts/enrich_release_notes.py"
        helper.parent.mkdir(parents=True)
        helper.write_text("canonical helper\n", encoding="utf-8")
        run_mock.return_value = type("Result", (), {"stdout": "canonical helper\n"})()
        fleet.verify_release_helper(self.repository, "quokkify/ci-kit", "v2.23.0", env={})

    @mock.patch.object(fleet, "run")
    def test_stale_helper_fails_closed_with_reconciliation_guidance(self, run_mock: mock.Mock) -> None:
        helper = self.repository / ".github/scripts/enrich_release_notes.py"
        helper.parent.mkdir(parents=True)
        helper.write_text("stale helper\n", encoding="utf-8")
        run_mock.return_value = type("Result", (), {"stdout": "canonical helper\n"})()
        with self.assertRaisesRegex(fleet.FleetUpdateError, "customized changes"):
            fleet.verify_release_helper(self.repository, "quokkify/ci-kit", "v2.23.0", env={})

    @mock.patch.object(fleet, "run")
    def test_helper_is_read_from_the_release_module(self, run_mock: mock.Mock) -> None:
        helper = self.repository / ".github/scripts/enrich_release_notes.py"
        helper.parent.mkdir(parents=True)
        helper.write_text("canonical helper\n", encoding="utf-8")
        run_mock.return_value = type("Result", (), {"stdout": "canonical helper\n"})()
        fleet.verify_release_helper(self.repository, "quokkify/ci-kit", "v2.23.0", env={})
        self.assertIn(
            "FETCH_HEAD:modules/release/template/.github/scripts/enrich_release_notes.py.jinja",
            run_mock.call_args.args[0],
        )

    def test_missing_release_helper_fails_when_feature_is_enabled(self) -> None:
        with self.assertRaisesRegex(fleet.FleetUpdateError, "release_please is enabled"):
            fleet.verify_release_helper(self.repository, "quokkify/ci-kit", "v2.23.0", env={})


if __name__ == "__main__":
    main()

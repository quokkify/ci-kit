"""Regression guards for the workflows that hold write-capable credentials.

Each class pins one hardening decision: the fleet updater installs only hash-locked
packages, the release driver hands the fleet updater only its own secret, and the
reusable Release Please workflow gives each job the permissions it needs and no
persisted Git credential.
"""

from __future__ import annotations

import json
import re
import shlex
import tomllib
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
FLEET_WORKFLOW = WORKFLOWS / "copier-fleet-auto-update.yml"
RELEASE_WORKFLOW = WORKFLOWS / "release.yml"
RELEASE_PLEASE_WORKFLOW = WORKFLOWS / "release-please.yml"
RENOVATE_CONFIG = ROOT / ".github/renovate.json"
FLEET_LOCKS = {
    "requirements/fleet-build.lock": "requirements/fleet-build.in",
    "requirements/fleet.lock": "requirements/fleet.in",
}
FLEET_INSTALL_COMMANDS = [
    "python -m pip install --require-hashes --no-deps --only-binary=:all: -r requirements/fleet-build.lock",
    "python -m pip install --require-hashes --no-deps --no-build-isolation -r requirements/fleet.lock",
]
# Options Renovate's pip-compile manager can re-run from a uv header; anything else makes it skip the lock.
RENOVATE_UV_OPTIONS = {"--universal", "--generate-hashes", "--python-version", "--output-file"}
CHECKOUT = "actions/checkout@"


def load(path: Path) -> dict:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict), path
    return payload


def triggers(workflow: dict) -> dict:
    return workflow.get("on", workflow.get(True))


def step_named(job: dict, name: str) -> dict:
    return next(step for step in job["steps"] if step.get("name") == name)


def lock_header_command(text: str) -> list[str]:
    match = re.match(r"^(?:#.*\n)*?#    (uv pip compile .*)\n", text)
    assert match, "lock header must record the uv pip compile command"
    return shlex.split(match.group(1))


def locked_requirements(text: str) -> dict[str, list[str]]:
    """Map each `name==version` requirement to the hashes listed under it."""
    requirements: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            current = line.split(" ", 1)[0].split(";", 1)[0]
            requirements[current] = []
        stripped = line.strip().rstrip("\\").strip()
        if current and stripped.startswith("--hash=sha256:"):
            requirements[current].append(stripped)
    return requirements


def regex(pattern: str) -> re.Pattern[str]:
    assert pattern.startswith("/") and pattern.endswith("/"), pattern
    return re.compile(pattern[1:-1])


class FleetLockedInstallTests(unittest.TestCase):
    """Regression guard: the step holding FLEET_UPDATE_TOKEN imports only hash-locked code."""

    def setUp(self) -> None:
        self.workflow = load(FLEET_WORKFLOW)
        self.setup = step_named(self.workflow["jobs"]["update"], "Set up Python")
        self.install = step_named(self.workflow["jobs"]["update"], "Install hash-locked fleet requirements")

    def test_fleet_installs_only_from_hash_locked_requirements(self) -> None:
        commands = [line.strip() for line in self.install["run"].splitlines() if line.strip().startswith("python ")]
        self.assertEqual(commands, FLEET_INSTALL_COMMANDS)
        self.assertNotRegex(self.install["run"], r"[A-Za-z0-9_.-]+==")
        self.assertNotRegex(FLEET_WORKFLOW.read_text(encoding="utf-8"), r"(?i)\b(?:pyyaml|copier|jsonschema|pip)==")

    def test_setup_python_wrapper_installs_nothing_unhashed(self) -> None:
        """The wrapper's bootstrap runs `pip install pip==X` without a hash; the fleet job must skip it."""
        self.assertEqual(self.setup["uses"], "./actions/setup-python")
        self.assertEqual(self.setup["with"].get("install-dependencies"), "false")
        self.assertEqual(self.setup["with"].get("cache-dependencies"), "false")
        self.assertNotIn("install-command", self.setup["with"])
        action = load(ROOT / "actions/setup-python/action.yml")
        for step in action["runs"]["steps"]:
            if "pip install" in str(step.get("run", "")):
                with self.subTest(step=step["name"]):
                    # Both inputs are "false" above, so a step gated only on them cannot run.
                    gates = set(re.findall(r"inputs\.([a-z-]+) == 'true'", step["if"]))
                    self.assertTrue(gates)
                    self.assertLessEqual(gates, {"install-dependencies", "cache-dependencies"})
                    self.assertNotRegex(step["if"], r"&&|!|false")

    def test_pip_itself_comes_from_the_hash_lock_first(self) -> None:
        build = locked_requirements((ROOT / "requirements/fleet-build.lock").read_text(encoding="utf-8"))
        self.assertTrue(any(name.startswith("pip==") for name in build))
        steps = self.workflow["jobs"]["update"]["steps"]
        self.assertLess(steps.index(self.setup), steps.index(self.install))
        self.assertLess(steps.index(self.install), steps.index(step_named(self.workflow["jobs"]["update"], "Update Copier fleet")))

    def test_every_locked_requirement_is_exact_and_hashed(self) -> None:
        for lock in FLEET_LOCKS:
            with self.subTest(lock=lock):
                requirements = locked_requirements((ROOT / lock).read_text(encoding="utf-8"))
                self.assertTrue(requirements)
                for requirement, hashes in requirements.items():
                    self.assertRegex(requirement, r"^[a-z0-9][a-z0-9._-]*==[^=\s]+$")
                    self.assertTrue(hashes, f"{requirement} has no hash")

    def test_lock_headers_record_a_command_renovate_can_rerun(self) -> None:
        python_version = self.setup["with"]["python-version"]
        for lock, source in FLEET_LOCKS.items():
            with self.subTest(lock=lock):
                argv = lock_header_command((ROOT / lock).read_text(encoding="utf-8"))
                self.assertEqual(argv[:3], ["uv", "pip", "compile"])
                options = [arg for arg in argv[3:] if arg.startswith("-")]
                self.assertEqual({option.split("=", 1)[0] for option in options}, RENOVATE_UV_OPTIONS)
                self.assertIn("--generate-hashes", options)
                self.assertIn(f"--output-file={lock}", options)
                self.assertIn(f"--python-version={python_version}", options)
                self.assertEqual([arg for arg in argv[3:] if not arg.startswith("-")], [source])

    def test_fleet_inputs_match_the_ci_kit_runtime_dependencies(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        fleet_in = (ROOT / "requirements/fleet.in").read_text(encoding="utf-8").split()
        self.assertEqual(sorted(fleet_in), sorted(project["project"]["dependencies"]))

    def test_renovate_regenerates_the_locks_and_tracks_each_pin_once(self) -> None:
        config = json.loads(RENOVATE_CONFIG.read_text(encoding="utf-8"))
        self.assertIn("pip-compile", config["enabledManagers"])
        lock_patterns = [regex(item) for item in config["pip-compile"]["managerFilePatterns"]]
        for lock in FLEET_LOCKS:
            self.assertTrue(any(pattern.search(lock) for pattern in lock_patterns), lock)
        for source in FLEET_LOCKS.values():
            self.assertFalse(any(pattern.search(source) for pattern in lock_patterns), source)

        inline = next(
            manager
            for manager in config["customManagers"]
            if "(?<depName>PyYAML|copier|jsonschema)" in "".join(manager["matchStrings"])
        )
        inline_patterns = [regex(item) for item in inline["managerFilePatterns"]]
        for path in (*FLEET_LOCKS, *FLEET_LOCKS.values()):
            self.assertFalse(any(pattern.search(path) for pattern in inline_patterns), path)

        group = next(
            rule for rule in config["packageRules"] if set(rule.get("matchManagers", [])) == {"custom.regex", "pip-compile"}
        )
        for name in ("PyYAML", "pyyaml", "copier", "jsonschema"):
            self.assertTrue(any(regex(item).search(name) for item in group["matchPackageNames"]), name)
        self.assertTrue(group.get("groupName"))

    def test_only_pip_compile_owns_the_lock_files(self) -> None:
        """A regex edit of a lock header would not regenerate the lock; pip-compile must own them alone."""
        config = json.loads(RENOVATE_CONFIG.read_text(encoding="utf-8"))
        for manager in config["customManagers"]:
            patterns = [regex(item) for item in manager["managerFilePatterns"]]
            for lock in FLEET_LOCKS:
                with self.subTest(manager=manager["description"], lock=lock):
                    self.assertFalse(any(pattern.search(lock) for pattern in patterns))

    def test_fleet_updates_never_automerge_and_wait_a_week(self) -> None:
        config = json.loads(RENOVATE_CONFIG.read_text(encoding="utf-8"))
        rules = config["packageRules"]
        rule = next(item for item in rules if item.get("matchFileNames") == ["requirements/fleet*"])
        self.assertEqual(set(rule) - {"description"}, {"matchFileNames", "automerge", "minimumReleaseAge"})
        self.assertIs(rule["automerge"], False)
        self.assertEqual(rule["minimumReleaseAge"], "7 days")
        # Later rules win: nothing after this rule may turn automerge back on.
        for later in rules[rules.index(rule) + 1 :]:
            self.assertNotIn("automerge", later)
            self.assertNotIn("minimumReleaseAge", later)


class ReleaseFleetSecretTests(unittest.TestCase):
    """Regression guard: the release driver hands the fleet updater only FLEET_UPDATE_TOKEN."""

    def test_release_passes_only_the_fleet_token(self) -> None:
        job = load(RELEASE_WORKFLOW)["jobs"]["fleet-update"]
        self.assertEqual(job["secrets"], {"FLEET_UPDATE_TOKEN": "${{ secrets.FLEET_UPDATE_TOKEN }}"})

    def test_no_toolkit_workflow_inherits_all_secrets(self) -> None:
        for path in sorted(WORKFLOWS.glob("*.yml")):
            with self.subTest(path=path.name):
                for job in (load(path).get("jobs") or {}).values():
                    self.assertNotEqual(job.get("secrets"), "inherit")

    def test_fleet_workflow_declares_its_optional_call_secret(self) -> None:
        workflow = load(FLEET_WORKFLOW)
        secrets = triggers(workflow)["workflow_call"]["secrets"]
        self.assertEqual(set(secrets), {"FLEET_UPDATE_TOKEN"})
        # Optional: dry runs never need it, and write mode fails fast with an explicit message.
        self.assertIs(secrets["FLEET_UPDATE_TOKEN"]["required"], False)
        update = workflow["jobs"]["update"]
        for name in ("Update Copier fleet", "Merge ready template updates"):
            self.assertEqual(step_named(update, name)["env"]["GH_TOKEN"], "${{ secrets.FLEET_UPDATE_TOKEN }}")


class ReleasePleaseLeastPrivilegeTests(unittest.TestCase):
    """Regression guard: every job of the reusable workflow declares only what it uses."""

    EXPECTED = {
        "validate": {"contents": "read"},
        # release-please-action creates release PRs, labels, tags and releases through the API.
        "single": {"contents": "write", "pull-requests": "write"},
        "manifest": {"contents": "write", "pull-requests": "write"},
        # `gh release edit` rewrites component release notes; no pull request is touched.
        "sync-component-releases": {"contents": "write"},
        # Pushes the enriched CHANGELOG commit and PATCHes the release PR body.
        "enrich": {"contents": "write", "pull-requests": "write"},
    }

    def setUp(self) -> None:
        self.workflow = load(RELEASE_PLEASE_WORKFLOW)

    def test_default_token_is_read_only(self) -> None:
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})

    def test_each_job_declares_its_minimal_permissions(self) -> None:
        jobs = self.workflow["jobs"]
        self.assertEqual(set(jobs), set(self.EXPECTED))
        for name, permissions in self.EXPECTED.items():
            with self.subTest(job=name):
                self.assertEqual(jobs[name].get("permissions"), permissions)

    def test_callers_grant_everything_the_jobs_request(self) -> None:
        template = (ROOT / "modules/release/template/.github/workflows/release.yml.jinja").read_text(encoding="utf-8")
        caller = yaml.safe_load(re.sub(r"\{\{.*?\}\}", "x", re.sub(r"\{%-?.*?%\}", "", template)))
        granted = caller["permissions"]
        ranks = {"none": 0, "read": 1, "write": 2}
        for name, permissions in self.EXPECTED.items():
            for scope, level in permissions.items():
                with self.subTest(job=name, scope=scope):
                    self.assertGreaterEqual(ranks[granted.get(scope, "none")], ranks[level])
        self.assertLessEqual(
            {"contents", "pull-requests"}, set(load(RELEASE_WORKFLOW)["permissions"])
        )

    def test_no_checkout_persists_the_job_token(self) -> None:
        for name, job in self.workflow["jobs"].items():
            for step in job["steps"]:
                if str(step.get("uses", "")).startswith(CHECKOUT):
                    with self.subTest(job=name, step=step.get("name")):
                        self.assertIs(step.get("with", {}).get("persist-credentials"), False)

    def test_enrich_supplies_a_git_credential_only_to_the_push(self) -> None:
        enrich = self.workflow["jobs"]["enrich"]
        prepare = step_named(enrich, "Prepare enriched release notes")
        update = step_named(enrich, "Update the existing Release Please pull request")
        self.assertNotIn("credential", prepare["run"])
        pushes = [line.strip() for line in update["run"].splitlines() if " push " in line]
        self.assertEqual(
            pushes, ["git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push origin HEAD"]
        )
        self.assertEqual(update["env"]["GH_TOKEN"], "${{ github.token }}")


if __name__ == "__main__":
    unittest.main()

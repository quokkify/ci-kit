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
FLEET_INSTALL_COMMAND = (
    "python -m pip install --require-hashes --no-deps --only-binary=:all: -r requirements/fleet-build.lock"
    " && python -m pip install --require-hashes --no-deps --no-build-isolation -r requirements/fleet.lock"
)
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
        self.setup = step_named(self.workflow["jobs"]["update"], "Set up Python and Copier")

    def test_fleet_installs_only_from_hash_locked_requirements(self) -> None:
        command = self.setup["with"]["install-command"]
        self.assertEqual(command, FLEET_INSTALL_COMMAND)
        for part in command.split(" && "):
            self.assertIn("--require-hashes", part)
            self.assertIn("--no-deps", part)
        self.assertNotRegex(command, r"[A-Za-z0-9_.-]+==")
        self.assertNotRegex(FLEET_WORKFLOW.read_text(encoding="utf-8"), r"(?i)\b(?:pyyaml|copier|jsonschema)==")

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

    def test_python_runtime_bumps_move_the_lock_target_too(self) -> None:
        config = json.loads(RENOVATE_CONFIG.read_text(encoding="utf-8"))
        manager = next(item for item in config["customManagers"] if item.get("depNameTemplate") == "python")
        patterns = [regex(item) for item in manager["managerFilePatterns"]]
        matchers = [re.compile(item.replace("(?<", "(?P<")) for item in manager["matchStrings"]]
        for lock in FLEET_LOCKS:
            with self.subTest(lock=lock):
                self.assertTrue(any(pattern.search(lock) for pattern in patterns))
                text = (ROOT / lock).read_text(encoding="utf-8")
                self.assertTrue(any(matcher.search(text) for matcher in matchers))


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


if __name__ == "__main__":
    unittest.main()

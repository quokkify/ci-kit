#!/usr/bin/env python3
"""Render and compile the opt-in q4j starter without running tests."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

import yaml
from copier import run_copy, run_update

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], cwd: Path) -> None:
    """Run a fixture command in its isolated checkout."""
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def commit(directory: Path, message: str) -> None:
    """Commit fixture state with hooks and signing disabled."""
    run(["git", "add", "."], directory)
    run(["git", "commit", "--no-verify", "-qm", message], directory)


def init_git(directory: Path) -> None:
    """Create a disposable repository for the real Copier update contract."""
    run(["git", "init", "-q"], directory)
    for key, value in (
        ("user.name", "Fixture"),
        ("user.email", "fixture@example.invalid"),
        ("commit.gpgsign", "false"),
        ("core.hooksPath", "/dev/null"),
    ):
        run(["git", "config", key, value], directory)


def validate_q4j_fixture(static: bool = False) -> None:
    """Check default omission and update ownership, then optionally compile."""
    data = yaml.safe_load((ROOT / "tests/scenarios/q4j-tests.yml").read_text())
    with tempfile.TemporaryDirectory(prefix="project-toolkit-q4j-fixture-") as tmp:
        temporary = Path(tmp).resolve()
        source = temporary / "template"
        source.mkdir()
        shutil.copy2(ROOT / "copier.yml", source / "copier.yml")
        shutil.copytree(ROOT / "templates/project/template", source / "templates/project/template")
        init_git(source)
        commit(source, "Current working template")
        run(["git", "tag", "v1.0.0"], source)

        disabled = temporary / "disabled"
        default_data = {
            key: value
            for key, value in data.items()
            if key not in {"q4j_tests", "q4j_tests_path"}
        }
        run_copy(str(source), disabled, data=default_data, defaults=True, vcs_ref="v1.0.0")
        assert not (disabled / "test-automation").exists(), "default must omit test-automation"
        disabled_answers = yaml.safe_load((disabled / ".copier-answers.yml").read_text())
        assert "q4j_installed_path" not in disabled_answers, "disabled must omit ownership metadata"

        init_git(disabled)
        commit(disabled, "Consumer before q4j activation")
        run_update(disabled, defaults=True, overwrite=True, vcs_ref="v1.0.0")
        assert not (disabled / "test-automation").exists(), "disabled update must stay disabled"
        run_update(
            disabled, data={"q4j_tests": True}, defaults=True,
            overwrite=True, vcs_ref="v1.0.0",
        )
        assert (disabled / "test-automation/build.gradle").exists(), "first activation must remain allowed"
        activated_answers = yaml.safe_load((disabled / ".copier-answers.yml").read_text())
        assert activated_answers["q4j_installed_path"] == "test-automation"

        custom = temporary / "custom"
        run_copy(
            str(source), custom,
            data={**data, "q4j_tests_path": "qa", "renovate": True, "codeql": True},
            defaults=True, vcs_ref="v1.0.0",
        )
        assert (custom / "qa/build.gradle").exists(), "custom directory must render"
        assert not (custom / "test-automation").exists(), "custom directory must replace default"
        custom_answers = yaml.safe_load((custom / ".copier-answers.yml").read_text())
        assert custom_answers["q4j_installed_path"] == "qa"
        assert "java" in custom_answers["renovate_presets"], "q4j must infer the Java preset"
        codeql = yaml.safe_load((custom / ".github/workflows/codeql.yml").read_text())
        assert "java-kotlin" in codeql["jobs"]["analyze"]["strategy"]["matrix"]["language"]

        destination = temporary / "enabled"
        run_copy(str(source), destination, data=data, defaults=True, vcs_ref="v1.0.0")
        starter = destination / "test-automation"
        assert (starter / "gradlew").stat().st_mode & 0o111, "wrapper must be executable"
        template_starter = (
            source / "templates/project/template"
            / "{% if q4j_tests %}{{ q4j_tests_path }}{% endif %}"
        )
        for name in (
            ".gitattributes", "build.gradle", "gradle/compilation.gradle", "gradle/dependencies.gradle",
            "gradle/tests.gradle", "gradle/libs.versions.toml", "gradle/code-analysis.gradle",
            "tools/checkstyle/checkstyle.xml", "tools/checkstyle/suppressions.xml",
        ):
            assert (starter / name).read_bytes() == (template_starter / name).read_bytes(), (
                f"generation changed project-owned Gradle configuration: {name}"
            )
        catalog = tomllib.loads((starter / "gradle/libs.versions.toml").read_text())
        assert catalog["libraries"]["q4j-config"] == {
            "module": "dev.quokkify:config", "version": {"ref": "q4j"},
        }, "catalog must use the published q4j config coordinate and shared version"
        assert catalog["libraries"]["testng"] == {
            "module": "org.testng:testng", "version": {"ref": "testng"},
        }, "catalog must use plain TestNG"
        assert catalog["libraries"]["checkstyle"] == {
            "module": "com.puppycrawl.tools:checkstyle", "version": {"ref": "checkstyle"},
        }, "catalog must pin the Checkstyle tool used by code analysis"
        for dependency in ("q4j", "testng", "checkstyle"):
            assert re.fullmatch(
                r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)",
                catalog["versions"][dependency],
            ), f"{dependency} must use an exact released SemVer pin"
        generated_answers = yaml.safe_load((destination / ".copier-answers.yml").read_text())
        assert "q4j_version" not in generated_answers, "dependency versions must not become Copier answers"
        if not static:
            run([
                "./gradlew", "--no-daemon", "assemble", "testClasses",
                "checkstyleMain", "checkstyleTest",
            ], starter)

        init_git(destination)
        # Exercise every project-owned starter file, including the binary wrapper.
        owned = yaml.safe_load((source / "copier.yml").read_text())["_skip_if_exists"]
        markers = {}
        for name in owned:
            if name.startswith("{{ q4j_tests_path }}/"):
                name = name.replace("{{ q4j_tests_path }}", "test-automation")
                path = destination / name
                marker = b"project-owned fixture content\n"
                path.write_bytes(marker)
                markers[name] = marker
        commit(destination, "Consumer customization")
        template_starter = (
            source / "templates/project/template"
            / "{% if q4j_tests %}{{ q4j_tests_path }}{% endif %}"
        )
        for path in template_starter.rglob("*"):
            if path.is_file():
                with path.open("ab") as stream:
                    stream.write(b"Updated toolkit fixture content\n")
        commit(source, "Updated template")
        run(["git", "tag", "v1.0.1"], source)
        for changed_data, expected_reason in (
            ({"q4j_tests": False}, "cannot be disabled"),
            ({"q4j_tests_path": "qa"}, "cannot be renamed"),
        ):
            before = {
                path.relative_to(destination): path.read_bytes()
                for path in destination.rglob("*")
                if path.is_file() and ".git" not in path.relative_to(destination).parts
            }
            try:
                run_update(
                    destination, data=changed_data, defaults=True,
                    overwrite=True, vcs_ref="v1.0.1",
                )
            except ValueError as exc:
                assert expected_reason in str(exc), str(exc)
            else:
                raise AssertionError(f"unsafe update accepted: {changed_data}")
            after = {
                path.relative_to(destination): path.read_bytes()
                for path in destination.rglob("*")
                if path.is_file() and ".git" not in path.relative_to(destination).parts
            }
            assert before == after, "rejected update changed consumer files"
        run_update(destination, defaults=True, overwrite=True, vcs_ref="v1.0.1")
        for name, marker in markers.items():
            assert (destination / name).read_bytes() == marker, f"update replaced {name}"
        assert "Updated toolkit fixture content" in (starter / "README.md").read_text(), "README must update"
        updated_answers = yaml.safe_load((destination / ".copier-answers.yml").read_text())
        assert updated_answers["q4j_installed_path"] == "test-automation", "update must retain ownership metadata"
    print("q4j fixture: OK" + (" (static)" if static else " (compile and Checkstyle only)"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--static", action="store_true", help="render and check ownership without compiling")
    args = parser.parse_args()
    validate_q4j_fixture(static=args.static)

from __future__ import annotations

import json
import os
from pathlib import Path

ARTIFACT_PREFIX = "allure-results-"
MAX_ARTIFACT_NAME = 128
MAX_ALLURE_ARTIFACTS = 50


def artifact_name(component_id: str, module_id: str) -> str:
    return f"{ARTIFACT_PREFIX}{component_id}-{module_id}"


def _java_build_tool(component_dir: Path) -> str:
    # Mirrors the build-tool auto detection of java-ci.yml.
    if os.access(component_dir / "gradlew", os.X_OK) or (component_dir / "build.gradle").is_file() or (component_dir / "build.gradle.kts").is_file():
        return "gradle"
    if (component_dir / "pom.xml").is_file():
        return "maven"
    return "gradle"


def allure_component_defaults(repo: Path, module: str, component: dict) -> dict:
    """Upload allure-results-<id> when the allure module runs in component mode.

    Applies only when the declaration leaves upload_test_artifacts unset and test_artifact_name is unset
    or an allure-results- name; explicit settings win.
    """
    if "upload_test_artifacts" in component or component.get("test_modules") or component.get("run_tests") is False:
        return component
    if not str(component.get("test_artifact_name", ARTIFACT_PREFIX)).startswith(ARTIFACT_PREFIX):
        return component
    path = "allure-results"
    if module == "java":
        tool = component.get("build_tool", "auto")
        if tool == "auto":
            tool = _java_build_tool(Path(repo) / component["path"])
        path = "target/allure-results" if tool == "maven" else "build/allure-results"
    return {
        **component,
        "upload_test_artifacts": True,
        "test_artifact_name": component.get("test_artifact_name", f"{ARTIFACT_PREFIX}{component['id']}"),
        "test_artifact_path": component.get("test_artifact_path", path),
    }


def encode_test_modules(component: dict) -> str:
    modules = component.get("test_modules")
    if not modules:
        return ""
    return json.dumps([
        {
            "id": module["id"],
            "module": module["module"],
            "test_command": module.get("test_command", ""),
            "results": module.get("results", ""),
            "artifact": artifact_name(component["id"], module["id"]) if module.get("results") else "",
        }
        for module in modules
    ], separators=(",", ":"))


def component_artifacts(component: dict, module: str = "java") -> list[str]:
    if component.get("test_modules"):
        return [artifact_name(component["id"], module["id"]) for module in component["test_modules"] if module.get("results")]
    if not component.get("upload_test_artifacts"):
        return []
    if component.get("test_artifact_name"):
        return [component["test_artifact_name"]]
    from .inputs import WORKFLOWS, load_inputs

    return [load_inputs()[WORKFLOWS[module]]["test-artifact-name"]["default"]]

from __future__ import annotations

import json

ARTIFACT_PREFIX = "allure-results-"
MAX_ARTIFACT_NAME = 128
MAX_ALLURE_ARTIFACTS = 50


def artifact_name(component_id: str, module_id: str) -> str:
    return f"{ARTIFACT_PREFIX}{component_id}-{module_id}"


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


def component_artifacts(component: dict) -> list[str]:
    if component.get("test_modules"):
        return [artifact_name(component["id"], module["id"]) for module in component["test_modules"] if module.get("results")]
    if not component.get("upload_test_artifacts"):
        return []
    if component.get("test_artifact_name"):
        return [component["test_artifact_name"]]
    from .inputs import load_inputs

    return [load_inputs()["java-ci"]["test-artifact-name"]["default"]]

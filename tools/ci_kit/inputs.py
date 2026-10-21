from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import yaml

WORKFLOWS: dict[str, str] = {"java": "java-ci", "python": "python-ci", "node": "node-ci", "docker": "docker-build"}

_EMPTY_DEFAULTS = {"string": "", "boolean": False}


def extract_inputs(workflow_yml: Path) -> dict[str, dict]:
    data = yaml.safe_load(Path(workflow_yml).read_text())
    triggers = data.get("on", data.get(True)) or {}
    inputs = (triggers.get("workflow_call") or {}).get("inputs") or {}
    result: dict[str, dict] = {}
    for name, spec in inputs.items():
        kind = spec["type"]
        default = spec["default"] if "default" in spec else _EMPTY_DEFAULTS[kind]
        result[name] = {"type": kind, "default": default}
    return result


def load_inputs() -> dict[str, dict[str, dict]]:
    return json.loads(resources.files("ci_kit").joinpath("workflow_inputs.json").read_text())


def field_name(input_name: str) -> str:
    return input_name.replace("-", "_")


def input_name(field: str) -> str:
    return field.replace("_", "-")


def module_fields(module: str) -> tuple[str, ...]:
    from .modules import MODULES

    declared = MODULES[module].fields
    if declared is not None:
        return declared
    table = load_inputs()[WORKFLOWS[module]]
    return tuple(field_name(name) for name in table if name != "working-directory")


def fill(module: str, component: dict) -> dict:
    table = load_inputs()[WORKFLOWS[module]]
    entry = {"id": component["id"], "name": component.get("name", component["id"])}
    if module != "docker":
        entry["path"] = component["path"]
    for name in module_fields(module):
        entry[name] = component[name] if name in component else table[input_name(name)]["default"]
    return entry

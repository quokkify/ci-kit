from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import jsonschema
import yaml

from .inputs import WORKFLOWS, fill
from .modules import MODULES

ANSWERS_RE = re.compile(r"^\.copier-answers\.([A-Za-z0-9_-]+)\.yml$")


@dataclass(frozen=True)
class Finding:
    code: str
    module: str
    message: str
    path: str | None = None


@dataclass
class Plan:
    modules: list[str] = field(default_factory=list)
    matrices: dict[str, list[dict]] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    module_owned: set[str] = field(default_factory=set)


def discover(repo: Path) -> tuple[dict[str, Path], dict[str, Path]]:
    repo = Path(repo)
    answers: dict[str, Path] = {}
    for path in sorted(repo.glob(".copier-answers.*.yml")):
        match = ANSWERS_RE.match(path.name)
        if match:
            answers[match.group(1)] = path
    declarations: dict[str, Path] = {}
    for path in sorted((repo / ".ci" / "modules").glob("*.yml")):
        declarations[path.stem] = path
    return answers, declarations


def _schema(module: str, version: int) -> dict | None:
    resource = resources.files("ci_kit").joinpath("schemas", f"{module}.v{version}.json")
    if not resource.is_file():
        return None
    return json.loads(resource.read_text())


def _rel(repo: Path, path: Path) -> str:
    return path.relative_to(repo).as_posix()


def _version_findings(repo: Path, answers: dict[str, Path]) -> list[Finding]:
    # One toolkit release for every answers file (legacy included) until modules release independently.
    files = dict(answers)
    legacy = repo / ".copier-answers.yml"
    if legacy.is_file():
        files["legacy"] = legacy
    values: dict[str, dict] = {}
    for module, path in files.items():
        try:
            data = yaml.safe_load(path.read_text())
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        if isinstance(data, dict):
            values[module] = data
    reference = next((module for module in ("core", "legacy") if module in values), None)
    if reference is None:
        return []
    findings: list[Finding] = []
    for key in ("toolkit_version", "_commit"):
        expected = values[reference].get(key)
        if not expected:
            continue
        for module in sorted(values):
            actual = values[module].get(key)
            if module != reference and actual and actual != expected:
                findings.append(Finding(
                    "CIK-CORE-012", module,
                    f"{key} {actual} in {files[module].name} differs from {expected} in {files[reference].name}",
                    files[module].name,
                ))
    return findings


def build_plan(repo: Path, *, drift: bool = True, src_override: str | None = None) -> Plan:
    repo = Path(repo)
    answers, declarations = discover(repo)
    findings: list[Finding] = []
    loaded: dict[str, dict] = {}

    for module in sorted(set(answers) | set(declarations)):
        if module not in MODULES:
            path = answers.get(module) or declarations[module]
            findings.append(Finding("CIK-CORE-001", module, f"module {module} is not supported by this ci-kit", _rel(repo, path)))
            continue
        if module not in declarations:
            findings.append(Finding("CIK-CORE-002", module, f".ci/modules/{module}.yml is missing for {answers[module].name}", f".ci/modules/{module}.yml"))
            continue
        if module not in answers:
            findings.append(Finding("CIK-CORE-002", module, f".copier-answers.{module}.yml is missing for .ci/modules/{module}.yml", f".copier-answers.{module}.yml"))
            continue
        decl_path = declarations[module]
        rel = _rel(repo, decl_path)
        try:
            data = yaml.safe_load(decl_path.read_text())
        except OSError as error:
            findings.append(Finding("CIK-CORE-004", module, f"cannot read declaration: {error.strerror or error}", rel))
            continue
        except (yaml.YAMLError, UnicodeDecodeError) as error:
            findings.append(Finding("CIK-CORE-004", module, f"cannot parse declaration: {error}".splitlines()[0], rel))
            continue
        if not isinstance(data, dict):
            findings.append(Finding("CIK-CORE-004", module, "declaration must be a YAML mapping", rel))
            continue
        version = data.get("schema_version")
        schema = _schema(module, version) if isinstance(version, int) and not isinstance(version, bool) else None
        if schema is None:
            if "schema_version" not in data or not isinstance(version, int) or isinstance(version, bool):
                findings.append(Finding("CIK-CORE-004", module, "schema_version must be an integer", rel))
            else:
                findings.append(Finding("CIK-CORE-001", module, f"schema_version {version} is not supported for module {module}", rel))
            continue
        errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path))
        if errors:
            where = "/".join(str(p) for p in errors[0].path) or "<root>"
            findings.append(Finding("CIK-CORE-004", module, f"schema violation at {where}: {errors[0].message}", rel))
            continue
        loaded[module] = data

    for module in sorted(loaded):
        for required in MODULES[module].requires:
            if required not in loaded:
                findings.append(Finding("CIK-CORE-003", module, f"module {module} requires module {required}", f".ci/modules/{module}.yml"))

    seen: dict[str, str] = {}
    for module in sorted(loaded):
        module_ids: set[str] = set()
        module_names: set[str] = set()
        for component in loaded[module].get("components", []):
            owner = seen.setdefault(component["id"], module)
            if owner != module:
                findings.append(Finding("CIK-CORE-005", module, f"component id {component['id']} is already declared by module {owner}", f".ci/modules/{module}.yml"))
            elif component["id"] in module_ids:
                findings.append(Finding("CIK-CORE-005", module, f"component id {component['id']} is declared more than once in module {module}", f".ci/modules/{module}.yml"))
            module_ids.add(component["id"])
            name = component.get("name", component["id"])
            if name in module_names:
                findings.append(Finding("CIK-CORE-005", module, f"component name {name} is used by more than one component in module {module}", f".ci/modules/{module}.yml"))
            module_names.add(name)

    findings.extend(_version_findings(repo, answers))

    modules = sorted(loaded)
    module_owned: set[str] = set()
    rendered: set[str] | None = None
    if drift and modules:
        from .drift import drift_findings

        findings.extend(drift_findings(repo, {module: answers[module] for module in modules}, src_override, module_owned))
        rendered = set(module_owned)
    module_owned.update(f".ci/modules/{module}.yml" for module in modules)
    legacy_path = repo / ".copier-answers.yml"
    if legacy_path.exists():
        from .handoff import AREAS
        from .legacy import handoff_findings, legacy_findings, load_answers

        legacy_answers, _ = load_answers(repo)
        handed = (legacy_answers or {}).get("handed_off", [])
        if isinstance(handed, list) and all(isinstance(name, str) and name in AREAS for name in handed):
            owned_areas = [AREAS[name] for name in handed]
        else:
            owned_areas = [area for area in AREAS.values() if area.module in loaded]
        for area in owned_areas:
            module_owned.update(area.legacy_paths)
        findings.extend(handoff_findings(repo, legacy_answers, set(answers), rendered))
        findings.extend(legacy_findings(repo, module_owned))
    matrices = {module: [fill(module, component) for component in loaded[module].get("components", [])] for module in modules if module in WORKFLOWS}
    return Plan(modules=modules, matrices=matrices, findings=findings, module_owned=module_owned)

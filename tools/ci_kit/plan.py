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
from .test_modules import ARTIFACT_PREFIX, MAX_ALLURE_ARTIFACTS, MAX_ARTIFACT_NAME, component_artifacts

ANSWERS_RE = re.compile(r"^\.copier-answers\.([A-Za-z0-9_-]+)\.yml$")
# ci-kit v4 has no legacy template. Legacy answers are dropped once every area the legacy
# template rendered is handed off, and refused (CIK-CORE-030) while any is left.
LEGACY_ANSWERS = ".copier-answers.yml"
LEGACY_AREAS = ("validate", "base", "security", "release", "allure", "renovate")
LEGACY_ANSWERS_MESSAGE = "legacy answers still render files; migrate with ci-kit v3.11.x"


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
    # One toolkit release for every answers file until modules release independently.
    files = dict(answers)
    values: dict[str, dict] = {}
    for module, path in files.items():
        try:
            data = yaml.safe_load(path.read_text())
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        if isinstance(data, dict):
            values[module] = data
    reference = "core"
    if reference not in values:
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


def legacy_areas_left(raw: str) -> list[str]:
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        data = None
    handed = data.get("handed_off") if isinstance(data, dict) else None
    handed = set(handed) if isinstance(handed, list) and all(isinstance(name, str) for name in handed) else set()
    return [area for area in LEGACY_AREAS if area not in handed]


def legacy_answers_findings(repo: Path) -> list[Finding]:
    path = repo / LEGACY_ANSWERS
    if not path.exists():
        return []
    try:
        left = legacy_areas_left(path.read_text())
    except (OSError, UnicodeDecodeError):
        left = list(LEGACY_AREAS)
    if not left:
        return []
    return [Finding(
        "CIK-CORE-030", "legacy",
        f"{LEGACY_ANSWERS_MESSAGE} (not handed off: {', '.join(left)})",
        LEGACY_ANSWERS,
    )]


def _test_module_findings(loaded: dict[str, dict]) -> list[Finding]:
    findings: list[Finding] = []
    owners: dict[str, str] = {}
    names: list[str] = []
    for module in sorted(loaded):
        if not MODULES[module].language:
            continue
        path = f".ci/modules/{module}.yml"
        for component in loaded[module].get("components", []):
            module_ids: set[str] = set()
            for item in component.get("test_modules", []):
                if item["id"] in module_ids:
                    findings.append(Finding("CIK-CORE-005", module, f"test module id {item['id']} is declared more than once in component {component['id']}", path))
                module_ids.add(item["id"])
            for name in component_artifacts(component):
                names.append(name)
                if len(name) > MAX_ARTIFACT_NAME:
                    findings.append(Finding("CIK-CORE-013", module, f"artifact name {name} is {len(name)} characters, over the {MAX_ARTIFACT_NAME} limit", path))
                owner = owners.setdefault(name, component["id"])
                if owner != component["id"]:
                    findings.append(Finding("CIK-CORE-005", module, f"artifact name {name} of component {component['id']} is already used by component {owner}", path))
    if "allure" in loaded:
        count = sum(name.startswith(ARTIFACT_PREFIX) for name in names)
        if count > MAX_ALLURE_ARTIFACTS:
            findings.append(Finding("CIK-CORE-013", "allure", f"{count} allure result artifacts exceed the {MAX_ALLURE_ARTIFACTS} limit", ".ci/modules/allure.yml"))
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

    findings.extend(_test_module_findings(loaded))
    findings.extend(_version_findings(repo, answers))
    findings.extend(legacy_answers_findings(repo))

    modules = sorted(loaded)
    module_owned: set[str] = set()
    if drift and modules:
        from .drift import drift_findings

        findings.extend(drift_findings(repo, {module: answers[module] for module in modules}, src_override, module_owned))
    module_owned.update(f".ci/modules/{module}.yml" for module in modules)
    matrices = {module: [fill(module, component) for component in loaded[module].get("components", [])] for module in modules if module in WORKFLOWS}
    return Plan(modules=modules, matrices=matrices, findings=findings, module_owned=module_owned)

from __future__ import annotations

import re
from dataclasses import dataclass

from .inputs import WORKFLOWS, field_name, load_inputs
from .modules import MODULES

CONTRACT_JOB = "template-contract"
CONTRACT_CHECK = "Shared template contract"
JOB_KEYS = ("name", "uses", "with")
DOCKER_INPUTS = MODULES["docker"].fields
MODULE_ORDER = ("core", "java", "python", "node", "docker")
QUESTIONS = {
    "java": "java_components",
    "python": "python_components",
    "node": "node_components",
    "docker": "docker_images",
}
JAVA_DEFAULT_VERSION = "17"
USES_RE = re.compile(r"^quokkify/ci-kit/\.github/workflows/([a-z-]+)\.yml@(\S+)$")
MODULE_BY_WORKFLOW = {workflow: module for module, workflow in WORKFLOWS.items()}


class MappingError(ValueError):
    def __init__(self, message: str, job_id: str):
        super().__init__(message)
        self.job_id = job_id


@dataclass(frozen=True)
class LegacyJob:
    job_id: str
    name: str
    module: str
    with_: dict[str, object]


def _target(job: object) -> tuple[str, str] | None:
    uses = job.get("uses") if isinstance(job, dict) else None
    match = USES_RE.match(uses) if isinstance(uses, str) else None
    if match and match.group(1) in MODULE_BY_WORKFLOW:
        return MODULE_BY_WORKFLOW[match.group(1)], match.group(2)
    return None


def _jobs(data: object) -> dict:
    jobs = data.get("jobs") if isinstance(data, dict) else None
    return jobs if isinstance(jobs, dict) else {}


def component_job_ids(data: dict, toolkit_version: str) -> set[str]:
    return {str(job_id) for job_id, job in _jobs(data).items() if _target(job)}


def _value(job_id: str, key: str, value: object, kind: str) -> object:
    if isinstance(value, str) and "${{" in value:
        raise MappingError(f"job {job_id}: input {key} uses a GitHub expression", job_id)
    if kind == "string" and isinstance(value, (str, int)) and not isinstance(value, bool):
        return str(value)
    if kind == "boolean" and isinstance(value, bool):
        return value
    if kind == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    raise MappingError(f"job {job_id}: input {key} has value {value!r} that is not a {kind}", job_id)


def _parse_with(job_id: str, module: str, raw: object) -> dict[str, object]:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise MappingError(f"job {job_id}: with must be a mapping", job_id)
    table = load_inputs()[WORKFLOWS[module]]
    result: dict[str, object] = {}
    for key, value in raw.items():
        if module == "docker" and key == "push":
            if value is not False:
                raise MappingError(f"job {job_id}: input push must be false for docker", job_id)
            continue
        if key not in table or (module == "docker" and key not in DOCKER_INPUTS):
            raise MappingError(f"job {job_id}: input {key} is not supported", job_id)
        result[key] = _value(job_id, key, value, table[key]["type"])
    return result


def _parse_job(job_id: str, job: object, toolkit_version: str) -> LegacyJob:
    if not isinstance(job, dict):
        raise MappingError(f"job {job_id} is not a mapping", job_id)
    for key in job:
        if key not in JOB_KEYS:
            raise MappingError(f"job {job_id}: key {key} cannot be mapped", job_id)
    if "uses" not in job:
        raise MappingError(f"job {job_id} does not call a reusable workflow", job_id)
    target = _target(job)
    if target is None:
        raise MappingError(f"job {job_id}: uses {job['uses']!r} is not a known ci-kit workflow", job_id)
    module, ref = target
    if ref != toolkit_version:
        raise MappingError(f"job {job_id}: uses ref {ref} differs from toolkit_version {toolkit_version}", job_id)
    name = job.get("name")
    if name is not None and not isinstance(name, str):
        raise MappingError(f"job {job_id}: key name must be a string", job_id)
    if name and "${{" in name:
        raise MappingError(f"job {job_id}: name uses a GitHub expression", job_id)
    return LegacyJob(str(job_id), name or str(job_id), module, _parse_with(str(job_id), module, job.get("with")))


def parse_jobs(data: dict, toolkit_version: str) -> list[LegacyJob]:
    return [
        _parse_job(str(job_id), job, toolkit_version)
        for job_id, job in _jobs(data).items()
        if job_id != CONTRACT_JOB
    ]


def _entry(job: LegacyJob) -> dict:
    entry: dict[str, object] = {"id": job.job_id, "name": job.name}
    for key, value in job.with_.items():
        entry["path" if key == "working-directory" else field_name(key)] = value
    if job.module != "docker":
        entry.setdefault("path", ".")
    if job.module == "java":
        entry.setdefault("java_version", JAVA_DEFAULT_VERSION)
    return entry


def legacy_to_modules(jobs: list[LegacyJob]) -> list[tuple[str, dict]]:
    declarations: list[tuple[str, dict]] = [("core", {})]
    for module in MODULE_ORDER[1:]:
        entries = [_entry(job) for job in jobs if job.module == module]
        if entries:
            declarations.append((module, {QUESTIONS[module]: entries}))
    return declarations


def check_contexts(data: dict) -> list[str]:
    contexts = []
    for job_id, job in _jobs(data).items():
        if job_id == CONTRACT_JOB:
            contexts.append(CONTRACT_CHECK)
            continue
        target = _target(job)
        if target is None:
            raise MappingError(f"job {job_id} has no ci-kit workflow to derive a check name from", str(job_id))
        name = job.get("name") or str(job_id)
        contexts.append(f"{name} / {'build' if target[0] == 'docker' else 'ci'}")
    return contexts

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from .inputs import WORKFLOWS

MARKER = "<!-- ci-kit-migrate -->"
CHECKS_AFTER = {"core": ["ci / gate"]}
CONTRACT_JOB = "template-contract"
CONTRACT_CHECK = "Shared template contract"
USES_RE = re.compile(r"^quokkify/ci-kit/\.github/workflows/([a-z-]+)\.yml@(\S+)$")
MODULE_BY_WORKFLOW = {workflow: module for module, workflow in WORKFLOWS.items()}


class MappingError(ValueError):
    def __init__(self, message: str, job_id: str):
        super().__init__(message)
        self.job_id = job_id


def _target(job: object) -> tuple[str, str] | None:
    uses = job.get("uses") if isinstance(job, dict) else None
    match = USES_RE.match(uses) if isinstance(uses, str) else None
    if match and match.group(1) in MODULE_BY_WORKFLOW:
        return MODULE_BY_WORKFLOW[match.group(1)], match.group(2)
    return None


def _jobs(data: object) -> dict:
    jobs = data.get("jobs") if isinstance(data, dict) else None
    return jobs if isinstance(jobs, dict) else {}


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


def legacy_check_names(validate_yml: Path) -> list[str]:
    return check_contexts(yaml.safe_load(Path(validate_yml).read_text()))


def parse_marker(body: str) -> dict:
    lines = body.splitlines()
    try:
        start = lines.index(MARKER)
    except ValueError:
        raise ValueError("migrate marker not found") from None
    if start + 1 >= len(lines) or lines[start + 1].strip() != "```json":
        raise ValueError("migrate marker is not followed by a json block")
    block: list[str] = []
    for line in lines[start + 2:]:
        if line.strip() == "```":
            break
        block.append(line)
    else:
        raise ValueError("migrate json block is not closed")
    try:
        data = json.loads("\n".join(block))
    except json.JSONDecodeError as error:
        raise ValueError(f"migrate json block is invalid: {error}") from error
    if not isinstance(data, dict):
        raise ValueError("migrate json block must be an object")
    return data

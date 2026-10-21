from __future__ import annotations

from .inputs import WORKFLOWS, input_name, load_inputs
from .legacy_jobs import DOCKER_INPUTS, LegacyJob
from .plan import Finding, Plan

CODE = "CIK-MIGRATE-005"
VALIDATE_PATH = ".github/workflows/validate.yml"
WORKING_DIRECTORY = "working-directory"
ENTRY_KEYS = ("id", "name", "path")
MISSING = object()


def effective(module: str, values: dict) -> dict[str, object]:
    table = load_inputs()[WORKFLOWS[module]]
    names = DOCKER_INPUTS if module == "docker" else tuple(name for name in table if name != WORKING_DIRECTORY)
    result: dict[str, object] = {name: table[name]["default"] for name in names}
    result.update({key: value for key, value in values.items() if key != WORKING_DIRECTORY})
    if module == "docker":
        result["push"] = False
    return result


def _shown(value: object) -> str:
    return "unset" if value is MISSING else repr(value)


def equivalence_findings(jobs: list[LegacyJob], plan: Plan) -> list[Finding]:
    legacy = {(job.module, job.job_id): job for job in jobs}
    planned = {(module, entry["id"]): entry for module, entries in plan.matrices.items() for entry in entries}
    findings = []
    for module, job_id in sorted(legacy.keys() ^ planned.keys()):
        where = "legacy validate.yml" if (module, job_id) in legacy else f"the {module} module plan"
        findings.append(Finding(CODE, module, f"job {job_id} exists only in {where}", VALIDATE_PATH))
    for module, job_id in sorted(legacy.keys() & planned.keys()):
        job, entry = legacy[(module, job_id)], planned[(module, job_id)]
        before = effective(module, job.with_)
        after = effective(module, {input_name(key): value for key, value in entry.items() if key not in ENTRY_KEYS})
        if module != "docker":
            default = load_inputs()[WORKFLOWS[module]][WORKING_DIRECTORY]["default"]
            before[WORKING_DIRECTORY] = job.with_.get(WORKING_DIRECTORY, default)
            after[WORKING_DIRECTORY] = entry.get("path", default)
        for name in sorted(before.keys() | after.keys()):
            old, new = before.get(name, MISSING), after.get(name, MISSING)
            if old != new:
                findings.append(Finding(
                    CODE, module,
                    f"job {job_id}: input {name} is {_shown(old)} in legacy validate.yml but {_shown(new)} in the {module} module plan",
                    VALIDATE_PATH,
                ))
                break
    return findings

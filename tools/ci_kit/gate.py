from __future__ import annotations

from dataclasses import dataclass

from .modules import MODULES

TOOLKIT_STEPS = frozenset({
    "Check out repository",
    "Check out toolkit actions",
    "Resolve build tool for dependency cache",
    "Set up Java",
    "Resolve build tool and commands",
    "Upload test artifacts",
    "Set up Python",
    "Prepare Python package manager",
    "Cache Python dependencies",
    "Set up Node.js",
    "Prepare Node.js package manager",
    "Cache Node.js dependencies",
    "Set up Docker Buildx",
})

RESULT_LABELS = {"success": "ok", "failure": "fail", "cancelled": "cancelled", "skipped": "skipped"}
DOCS_BASE = "https://github.com/quokkify/ci-kit/blob"
SKIPPED_CODE = "CIK-CORE-020"
PLAN_FAILED_STEP = "plan failed"


@dataclass
class Row:
    module: str
    ref: str
    result: str
    category: str
    failing_step: str
    code: str
    template: str = "-"


def _matches(job_name: str, job_id: str) -> bool:
    return any(part == job_id or part.startswith(f"{job_id} (") for part in job_name.split(" / "))


def _failing_step(jobs: list[dict], job_id: str) -> str | None:
    for job in jobs:
        if job.get("conclusion") != "failure" or not _matches(str(job.get("name", "")), job_id):
            continue
        failed = [step for step in job.get("steps") or [] if step.get("conclusion") == "failure"]
        if failed:
            return str(min(failed, key=lambda step: step.get("number", 0)).get("name", ""))
        return ""
    return None


def _categorize(jobs: list[dict] | None, job_id: str) -> tuple[str, str]:
    if jobs is None:
        return "unknown", ""
    step = _failing_step(jobs, job_id)
    if not step:
        return "unknown", ""
    return ("toolkit" if step in TOOLKIT_STEPS else "project"), step


def _result(needs: dict, job_id: str) -> str:
    entry = needs.get(job_id)
    result = entry.get("result") if isinstance(entry, dict) else None
    return result if isinstance(result, str) else "skipped"


def evaluate(
    enabled: list[str], needs: dict, jobs: list[dict] | None, ref: str,
    *, plan_codes: list[str] = (), template_commits: dict[str, str] | None = None,
) -> tuple[bool, list[Row]]:
    commits = template_commits or {}
    rows: list[Row] = []
    ok = True
    handled = {"plan"}
    plan_result = _result(needs, "plan")
    if plan_result != "success":
        ok = False
        rows.append(Row("plan", ref, RESULT_LABELS.get(plan_result, plan_result), "template", "", ",".join(plan_codes)))
    for module in enabled:
        info = MODULES.get(module)
        job_id = info.job if info else module
        handled.add(job_id)
        result = _result(needs, job_id)
        label = RESULT_LABELS.get(result, result)
        if result == "success":
            rows.append(Row(module, ref, label, "", "", "", commits.get(module, "-")))
            continue
        ok = False
        if result == "skipped" and plan_result != "success":
            rows.append(Row(module, ref, label, "", PLAN_FAILED_STEP, "", commits.get(module, "-")))
        elif result == "skipped":
            rows.append(Row(module, ref, label, "template", "", SKIPPED_CODE, commits.get(module, "-")))
        elif result == "failure":
            category, step = _categorize(jobs, job_id)
            rows.append(Row(module, ref, label, category, step, "", commits.get(module, "-")))
        else:
            rows.append(Row(module, ref, label, "", "", "", commits.get(module, "-")))
    for job_id in needs:
        if job_id in handled:
            continue
        result = _result(needs, job_id)
        if result not in ("success", "skipped"):
            ok = False
            rows.append(Row(job_id, ref, RESULT_LABELS.get(result, result), "unknown", "", ""))
    return ok, rows


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def render_summary(rows: list[Row]) -> str:
    lines = [
        "| module | template @ | ci-kit @ | result | category | failing step | code |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        code = ", ".join(f"[{item}]({DOCS_BASE}/{row.ref}/docs/errors/{item}.md)" for item in row.code.split(",") if item)
        cells = [_cell(row.module), _cell(row.template), _cell(row.ref[:12]), row.result, _cell(row.category), _cell(row.failing_step), code]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"

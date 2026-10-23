from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .gate import evaluate, render_summary
from .inputs import WORKFLOWS
from .plan import Finding, build_plan, discover

# Ordered as the legacy CodeQL workflow iterated python, node, then java components.
CODEQL_LANGUAGES = {"python": "python", "node": "javascript-typescript", "java": "java-kotlin"}


def _compact(value: object) -> str:
    return json.dumps(value, separators=(",", ":"))


def _escape_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(value: str) -> str:
    return _escape_data(value).replace(":", "%3A").replace(",", "%2C")


def _annotations() -> bool:
    return os.environ.get("GITHUB_ACTIONS") == "true"


def format_finding(finding: Finding) -> str:
    if _annotations():
        return f"::error title={_escape_property(finding.code)}::{_escape_data(f'{finding.module}: {finding.message}')}"
    return f"{finding.code} {finding.module} {finding.path or '-'}: {finding.message} (docs/errors/{finding.code}.md)"


def _template_commits(repo: Path) -> dict[str, str]:
    import yaml

    commits: dict[str, str] = {}
    for module, path in discover(repo)[0].items():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            continue
        commit = data.get("_commit") if isinstance(data, dict) else None
        if isinstance(commit, str) and commit:
            commits[module] = commit
    return commits


def _plan(args: argparse.Namespace) -> int:
    plan = build_plan(Path(args.repo), drift=not args.no_drift, src_override=args.template_src)
    outputs = {"modules": _compact(plan.modules)}
    outputs.update({module: _compact(plan.matrices.get(module, [])) for module in WORKFLOWS})
    # Every discovered module counts, loaded or not: a broken declaration must
    # not silently drop its language from security scanning.
    discovered = set().union(*discover(Path(args.repo)))
    outputs["codeql"] = _compact(["actions", *(language for module, language in CODEQL_LANGUAGES.items() if module in discovered)])
    if args.github_output:
        outputs["codes"] = _compact(list(dict.fromkeys(finding.code for finding in plan.findings)))
        outputs["commits"] = _compact(_template_commits(Path(args.repo)))
        with open(args.github_output, "a", encoding="utf-8") as handle:
            for key, value in outputs.items():
                handle.write(f"{key}={value}\n")
    else:
        for key, value in outputs.items():
            print(f"{key}={value}")
    for finding in plan.findings:
        print(format_finding(finding), file=None if _annotations() else sys.stderr)
    return 1 if plan.findings else 0


def _doctor(args: argparse.Namespace) -> int:
    plan = build_plan(Path(args.repo), src_override=args.template_src)
    for finding in plan.findings:
        print(format_finding(finding))
    if not plan.findings:
        print(f"ok: modules {', '.join(plan.modules) or '(none)'}")
    return 1 if plan.findings else 0


def _update(args: argparse.Namespace) -> int:
    from .update import run_update

    updates, findings = run_update(
        Path(args.repo), args.to, modules=args.module or None,
        skip_legacy=args.skip_legacy, src_override=args.template_src,
    )
    for update in updates:
        print(f"{update.module}: {update.from_ref} -> {update.to_ref} {update.commit or '(unchanged)'}")
    for finding in findings:
        print(format_finding(finding))
    if args.json:
        payload = {
            "ref": args.to,
            "modules": [{"module": u.module, "from": u.from_ref, "to": u.to_ref, "commit": u.commit} for u in updates],
            "findings": [{"code": f.code, "module": f.module, "message": f.message, "path": f.path} for f in findings],
        }
        Path(args.json).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 1 if findings else 0


def _load_json(text: str, label: str) -> object:
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} is not valid JSON: {error}") from error


def _load_jobs(path: str | None) -> list[dict] | None:
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict):
        data = data.get("jobs")
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        return None
    return data


def _gate(args: argparse.Namespace) -> int:
    try:
        enabled = _load_json(args.enabled, "--enabled")
        needs = _load_json(args.needs, "--needs")
    except ValueError as error:
        print(f"ci-kit gate: {error}", file=sys.stderr)
        return 1
    if not isinstance(enabled, list) or not all(isinstance(item, str) for item in enabled):
        print("ci-kit gate: --enabled must be a JSON array of strings", file=sys.stderr)
        return 1
    if not isinstance(needs, dict):
        print("ci-kit gate: --needs must be a JSON object", file=sys.stderr)
        return 1
    try:
        plan_codes = _load_json(args.plan_codes, "--plan-codes")
        commits = _load_json(args.template_commits, "--template-commits")
    except ValueError as error:
        print(f"ci-kit gate: {error}", file=sys.stderr)
        return 1
    if not isinstance(plan_codes, list) or not all(isinstance(item, str) for item in plan_codes):
        print("ci-kit gate: --plan-codes must be a JSON array of strings", file=sys.stderr)
        return 1
    if not isinstance(commits, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in commits.items()):
        print("ci-kit gate: --template-commits must be a JSON object of strings", file=sys.stderr)
        return 1
    ok, rows = evaluate(enabled, needs, _load_jobs(args.jobs_file), args.ref, plan_codes=plan_codes, template_commits=commits)
    table = render_summary(rows)
    print(table, end="")
    if args.summary:
        try:
            with open(args.summary, "a", encoding="utf-8") as handle:
                handle.write(table)
        except OSError as error:
            print(f"ci-kit gate: cannot write --summary {args.summary}: {error}", file=sys.stderr)
            return 1
    for row in rows:
        if row.result != "ok":
            detail = f"{row.module}: {row.result}"
            parts = [row.category] if row.category else []
            if row.failing_step:
                parts.append(f"step {row.failing_step!r}")
            if parts:
                detail += f" ({', '.join(parts)})"
            if _annotations():
                title = f" title={_escape_property(row.code)}" if row.code else ""
                print(f"::error{title}::{_escape_data(detail)}")
            else:
                print(f"{row.code or 'gate'} {detail}", file=sys.stderr)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ci-kit")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--repo", required=True)
    plan.add_argument("--github-output")
    plan.add_argument("--template-src")
    plan.add_argument("--no-drift", action="store_true")
    plan.set_defaults(func=_plan)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--repo", required=True)
    doctor.add_argument("--template-src")
    doctor.set_defaults(func=_doctor)
    update = sub.add_parser("update")
    update.add_argument("--repo", required=True)
    update.add_argument("--to", required=True)
    update.add_argument("--module", action="append")
    update.add_argument("--skip-legacy", action="store_true")
    update.add_argument("--template-src")
    update.add_argument("--json")
    update.set_defaults(func=_update)
    gate = sub.add_parser("gate")
    gate.add_argument("--enabled", required=True)
    gate.add_argument("--needs", required=True)
    gate.add_argument("--jobs-file")
    gate.add_argument("--ref", required=True)
    gate.add_argument("--summary")
    gate.add_argument("--plan-codes", default="[]")
    gate.add_argument("--template-commits", default="{}")
    gate.set_defaults(func=_gate)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

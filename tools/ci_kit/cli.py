from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .plan import Finding, build_plan


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


def _plan(args: argparse.Namespace) -> int:
    plan = build_plan(Path(args.repo), src_override=args.template_src)
    outputs = {"modules": _compact(plan.modules), "java": _compact(plan.matrices.get("java", []))}
    if args.github_output:
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ci-kit")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--repo", required=True)
    plan.add_argument("--github-output")
    plan.add_argument("--template-src")
    plan.set_defaults(func=_plan)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--repo", required=True)
    doctor.add_argument("--template-src")
    doctor.set_defaults(func=_doctor)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

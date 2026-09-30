#!/usr/bin/env python3
"""Discover root unittest suites with validator recursion protection."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
NESTED_MARKER = "PROJECT_TOOLKIT_NESTED_VALIDATION"
# Intentional exclusions must be documented here; none are currently needed.
EXCLUDED_SUITES: frozenset[str] = frozenset()


def run_suites(root: Path = ROOT) -> int:
    if os.environ.get(NESTED_MARKER) == "1":
        print("nested validation: skipping suites that re-enter scripts/validate.py")
        return 0
    candidates = sorted((root / "tests").glob("test_*.py"))
    if any(path.is_symlink() for path in candidates):
        print("ERROR: test suite symlinks are not allowed", file=sys.stderr)
        return 1
    suites = [path for path in candidates if path.is_file() and path.name not in EXCLUDED_SUITES]
    if not suites:
        print("ERROR: no test suites discovered in tests/test_*.py", file=sys.stderr)
        return 1
    env = {**os.environ, NESTED_MARKER: "1"}
    failures = []
    for path in suites:
        print(f"+ unittest suite {path.relative_to(root)}", flush=True)
        if subprocess.run(
            [sys.executable, str(root / "scripts/run_test_suites.py"), "--suite", path.name],
            cwd=root, env=env,
        ).returncode:
            failures.append(path.name)
    if failures:
        print("ERROR: test suites failed: " + ", ".join(failures), file=sys.stderr)
    return int(bool(failures))


def run_suite(root: Path, filename: str) -> int:
    if Path(filename).name != filename or not filename.startswith("test_") or not filename.endswith(".py"):
        raise ValueError("suite must be a root test_*.py filename")
    path = root / "tests" / filename
    if not path.is_file() or path.is_symlink():
        raise ValueError("suite must be a regular test file")
    suite = unittest.defaultTestLoader.discover(str(root / "tests"), pattern=filename)
    if suite.countTestCases() == 0:
        print(f"ERROR: no tests discovered in {filename}", file=sys.stderr)
        return 1
    return int(not unittest.TextTestRunner().run(suite).wasSuccessful())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", help="internal isolated-suite entrypoint")
    arguments = parser.parse_args()
    raise SystemExit(run_suite(ROOT, arguments.suite) if arguments.suite else run_suites())

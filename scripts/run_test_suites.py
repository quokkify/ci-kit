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


SUITE_WEIGHTS: dict[str, int] = {
    "test_ci_kit_update.py": 42,
    "test_migration_lifecycle.py": 40,
    "test_ci_kit_migrate.py": 38,
    "test_module_selector.py": 30,
    "test_modules_lifecycle.py": 30,
    "test_ci_kit_drift.py": 28,
    "test_ci_kit_plan.py": 27,
    "test_ci_kit_legacy.py": 15,
    "test_allure_publisher.py": 14,
    "test_update_copier_fleet.py": 13,
    "test_composite_actions.py": 12,
    "test_modules_render.py": 9,
    "test_release_notes_config.py": 7,
}
DEFAULT_WEIGHT = 3


def discover_suites(root: Path) -> list[Path]:
    candidates = sorted((root / "tests").glob("test_*.py"))
    if any(path.is_symlink() for path in candidates):
        raise ValueError("test suite symlinks are not allowed")
    suites = [path for path in candidates if path.is_file() and path.name not in EXCLUDED_SUITES]
    if not suites:
        raise ValueError("no test suites discovered in tests/test_*.py")
    return suites


def shard_suites(suites: list[Path], count: int) -> list[list[Path]]:
    if count < 1 or count > len(suites):
        raise ValueError(f"shard count must be between 1 and {len(suites)}")
    shards: list[list[Path]] = [[] for _ in range(count)]
    loads = [0] * count
    for path in sorted(suites, key=lambda item: (-SUITE_WEIGHTS.get(item.name, DEFAULT_WEIGHT), item.name)):
        index = loads.index(min(loads))
        shards[index].append(path)
        loads[index] += SUITE_WEIGHTS.get(path.name, DEFAULT_WEIGHT)
    return [sorted(shard, key=lambda item: item.name) for shard in shards]


def parse_shard(value: str) -> tuple[int, int]:
    try:
        index, count = (int(part) for part in value.split("/"))
    except ValueError:
        raise ValueError("shard must look like I/N") from None
    if not 1 <= index <= count:
        raise ValueError("shard must satisfy 1 <= I <= N")
    return index, count


def list_shards(root: Path, count: int) -> int:
    try:
        shards = shard_suites(discover_suites(root), count)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    for index, shard in enumerate(shards, 1):
        weight = sum(SUITE_WEIGHTS.get(path.name, DEFAULT_WEIGHT) for path in shard)
        print(f"shard {index}/{count} (weight {weight}): {' '.join(path.name for path in shard)}")
    return 0


def run_suites(root: Path = ROOT, shard: tuple[int, int] | None = None) -> int:
    nested = os.environ.get(NESTED_MARKER) == "1"
    if nested and shard:
        print(f"ERROR: {NESTED_MARKER}=1 would silently skip the sharded suites", file=sys.stderr)
        return 1
    if nested:
        print("nested validation: skipping suites that re-enter scripts/validate.py")
        return 0
    try:
        suites = discover_suites(root)
        if shard:
            suites = shard_suites(suites, shard[1])[shard[0] - 1]
            if not suites:
                raise ValueError(f"shard {shard[0]}/{shard[1]} is empty")
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
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
    parser.add_argument("--shard", help="run one weight-balanced shard, formatted I/N")
    parser.add_argument("--list-shards", type=int, metavar="N", help="print the N-shard layout")
    arguments = parser.parse_args()
    if arguments.suite:
        raise SystemExit(run_suite(ROOT, arguments.suite))
    if arguments.list_shards is not None:
        raise SystemExit(list_shards(ROOT, arguments.list_shards))
    try:
        selected = parse_shard(arguments.shard) if arguments.shard else None
    except ValueError as error:
        parser.error(str(error))
    raise SystemExit(run_suites(ROOT, selected))

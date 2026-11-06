#!/usr/bin/env python3
"""Aggregate bounded, workspace-contained JUnit XML into a GitHub step summary."""

from __future__ import annotations

import os
import re
import stat
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Pattern, Sequence, Tuple


class SummaryError(ValueError):
    """Raised for invalid action inputs or JUnit documents."""


class RejectingTreeBuilder(ET.TreeBuilder):
    """Reject DTDs before entity declarations can be processed, regardless of encoding."""

    def doctype(self, name: str, public_id: Optional[str], system_id: Optional[str]) -> None:
        del name, public_id, system_id
        raise SummaryError("DTD and entity declarations are not accepted")


MAX_DURATION_SECONDS = Decimal("315576000")  # Ten years per report is already nonsensical.
MAX_GLOB_PATTERNS = 64
MAX_GLOB_LENGTH = 1024
MAX_SUITE_DEPTH = 128
DEFAULT_JUNIT_PATHS = "test-results/**/*.xml"
LEGACY_VARIANTS = {
    "frontend-single": "test-results/junit.xml",
    "backend-pytest": "test-results/*.xml",
    "e2e-playwright": "test-results/e2e-junit.xml",
}


@dataclass
class Stats:
    """Aggregated JUnit counters and duration."""
    tests: int = 0
    failures: int = 0
    errors: int = 0
    skipped: int = 0
    duration: Decimal = Decimal(0)

    @property
    def passed(self) -> int:
        """Return tests without failure, error, or skipped status."""
        return self.tests - self.failures - self.errors - self.skipped

    def add(self, other: "Stats") -> None:
        """Add another report's counters in place."""
        self.tests += other.tests
        self.failures += other.failures
        self.errors += other.errors
        self.skipped += other.skipped
        self.duration += other.duration


def local_name(tag: object) -> str:
    """Return an XML tag without its namespace."""
    return str(tag).rsplit("}", 1)[-1]


def parse_bounded_int(raw: str, name: str, minimum: int, maximum: int) -> int:
    """Parse an integer action input constrained to an inclusive range."""
    if not raw.isascii() or not raw.isdecimal() or len(raw) > len(str(maximum)):
        raise SummaryError(f"{name} must be an integer from {minimum} through {maximum}")
    value = int(raw)
    if not minimum <= value <= maximum:
        raise SummaryError(f"{name} must be an integer from {minimum} through {maximum}")
    return value


def parse_count(raw: Optional[str], name: str, source: str) -> int:
    """Parse a bounded non-negative JUnit count attribute."""
    value = "0" if raw in (None, "") else raw
    if not value.isascii() or not value.isdecimal() or len(value) > 12:
        raise SummaryError(f"{source}: {name} must be a non-negative integer")
    return int(value)


def parse_duration(raw: Optional[str], name: str, source: str) -> Decimal:
    """Parse a finite bounded duration without exponent expansion."""
    value = "0" if raw in (None, "") else raw
    if len(value) > 64:
        raise SummaryError(f"{source}: {name} is too long")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise SummaryError(f"{source}: {name} must be a finite non-negative number") from exc
    if not parsed.is_finite() or parsed < 0 or parsed > MAX_DURATION_SECONDS:
        raise SummaryError(
            f"{source}: {name} must be finite, non-negative, and at most {MAX_DURATION_SECONDS}"
        )
    exponent = parsed.as_tuple().exponent
    if not isinstance(exponent, int) or exponent < -18 or exponent > 9:
        raise SummaryError(
            f"{source}: {name} must use no more than 18 fractional digits"
        )
    return parsed


def stats_from_attributes(element: ET.Element, source: str) -> Stats:
    """Build statistics from canonical testsuite attributes."""
    skipped = parse_count(element.get("skipped"), "skipped", source)
    skipped += parse_count(element.get("disabled"), "disabled", source)
    result = Stats(
        tests=parse_count(element.get("tests"), "tests", source),
        failures=parse_count(element.get("failures"), "failures", source),
        errors=parse_count(element.get("errors"), "errors", source),
        skipped=skipped,
        duration=parse_duration(element.get("time"), "time", source),
    )
    if result.passed < 0:
        raise SummaryError(f"{source}: failures + errors + skipped exceeds tests")
    return result


def stats_from_testcases(cases: Sequence[ET.Element], source: str) -> Stats:
    """Derive statistics from testcase elements when totals are absent."""
    result = Stats(tests=len(cases))
    for case in cases:
        children = {local_name(child.tag) for child in case}
        if "error" in children:
            result.errors += 1
        elif "failure" in children:
            result.failures += 1
        elif "skipped" in children or case.get("status", "").lower() in {
            "disabled",
            "notrun",
            "skipped",
        }:
            result.skipped += 1
        result.duration += parse_duration(case.get("time"), "testcase time", source)
    return result


def stats_from_element(element: ET.Element, source: str, depth: int = 0) -> Stats:
    """Prefer canonical suite totals; fall back recursively only when totals are absent."""
    if depth > MAX_SUITE_DEPTH:
        raise SummaryError(f"{source}: testsuite nesting exceeds {MAX_SUITE_DEPTH}")
    if element.get("tests") is not None:
        return stats_from_attributes(element, source)

    child_suites = [child for child in element if local_name(child.tag) == "testsuite"]
    if child_suites:
        result = Stats()
        for child in child_suites:
            result.add(stats_from_element(child, source, depth + 1))
        return result

    cases = [child for child in element.iter() if local_name(child.tag) == "testcase"]
    if cases:
        return stats_from_testcases(cases, source)
    raise SummaryError(f"{source}: no testcase elements or testsuite summary attributes found")


def parse_junit(raw: bytes, source: str) -> Stats:
    """Parse one JUnit document while rejecting DTDs and entities."""
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise SummaryError(f"{source}: DTD and entity declarations are not accepted")
    try:
        parser = ET.XMLParser(target=RejectingTreeBuilder())
        root = ET.fromstring(raw, parser=parser)
    except SummaryError as exc:
        raise SummaryError(f"{source}: {exc}") from exc
    except ET.ParseError as exc:
        raise SummaryError(f"{source}: invalid XML: {exc}") from exc
    if local_name(root.tag) not in {"testsuite", "testsuites"}:
        raise SummaryError(f"{source}: root element must be testsuite or testsuites")
    return stats_from_element(root, source)


def validate_relative_path(value: str, name: str) -> Tuple[str, ...]:
    """Normalize a non-empty workspace-relative POSIX path."""
    pure = PurePosixPath(value)
    if (
        not value
        or pure.is_absolute()
        or ".." in pure.parts
        or "\\" in value
        or "\x00" in value
    ):
        raise SummaryError(f"{name} must be a relative POSIX path without traversal")
    return tuple(part for part in pure.parts if part != ".")


def compile_glob(pattern: str) -> Pattern[str]:
    """Compile a normalized bounded glob into a linear regular expression."""
    if len(pattern) > MAX_GLOB_LENGTH:
        raise SummaryError(f"junit-paths entries must not exceed {MAX_GLOB_LENGTH} characters")
    parts = validate_relative_path(pattern, "junit-paths entry")
    if any("**" in part and part != "**" for part in parts):
        raise SummaryError("junit-paths supports ** only as a complete path segment")
    pattern = "/".join(parts)
    if any(character in pattern for character in "[]{}"):
        raise SummaryError("junit-paths supports only literal characters, *, ?, and ** wildcards")
    expression: List[str] = ["^"]
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                index += 2
                if index < len(pattern) and pattern[index] == "/":
                    expression.append("(?:[^/]+/)*")
                    index += 1
                else:
                    expression.append(".*")
                continue
            expression.append("[^/]*")
        elif character == "?":
            expression.append("[^/]")
        else:
            expression.append(re.escape(character))
        index += 1
    expression.append("$")
    return re.compile("".join(expression))


def open_relative_directory(root_fd: int, parts: Sequence[str]) -> int:
    """Open a directory beneath root_fd without following symlinks."""
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
    current = os.dup(root_fd)
    try:
        for part in parts:
            next_fd = os.open(part, flags, dir_fd=current)
            os.close(current)
            current = next_fd
        return current
    except OSError:
        os.close(current)
        raise


def scan_root(pattern: str) -> Tuple[str, ...]:
    """Return the literal directory prefix that bounds a glob scan."""
    parts = validate_relative_path(pattern, "junit-paths entry")
    wildcard_index = next(
        (index for index, part in enumerate(parts) if "*" in part or "?" in part),
        len(parts),
    )
    if wildcard_index == len(parts):
        return parts[:-1]
    return parts[:wildcard_index]


def scan_plans(
    raw_patterns: Sequence[str],
) -> List[Tuple[Tuple[str, ...], Tuple[Optional[int], ...]]]:
    """Group depth policies and drop roots fully covered by a parent plan."""
    grouped: Dict[Tuple[str, ...], List[Optional[int]]] = {}
    for pattern in raw_patterns:
        root = scan_root(pattern)
        depth = None if "**" in PurePosixPath(pattern).parts else len(PurePosixPath(pattern).parts)
        grouped.setdefault(root, []).append(depth)
    plans: List[Tuple[Tuple[str, ...], Tuple[Optional[int], ...]]] = []
    for root in sorted(grouped, key=lambda item: (len(item), item)):
        depths = tuple(grouped[root])
        covered = False
        for parent, parent_depths in plans:
            if root[: len(parent)] != parent:
                continue
            if None in parent_depths:
                covered = True
                break
            parent_finite = [depth for depth in parent_depths if depth is not None]
            child_finite = [depth for depth in depths if depth is not None]
            if len(child_finite) == len(depths) and max(parent_finite) >= max(child_finite):
                covered = True
                break
        if not covered:
            plans.append((root, depths))
    return plans


def scan_files(
    root_fd: int,
    patterns: Sequence[Pattern[str]],
    plans: Sequence[Tuple[Tuple[str, ...], Tuple[Optional[int], ...]]],
    max_files: int,
    max_scan_entries: int,
    max_depth: int,
) -> List[Tuple[str, ...]]:
    """Discover matching regular files through bounded directory-fd walks."""
    found: Dict[str, Tuple[str, ...]] = {}
    scanned = 0

    def visit(
        directory_fd: int,
        relative_parts: List[str],
        allowed_depths: Sequence[Optional[int]],
    ) -> None:
        nonlocal scanned
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                scanned += 1
                if scanned > max_scan_entries:
                    raise SummaryError(
                        f"scanned more than max-scan-entries={max_scan_entries} filesystem entries"
                    )
                if entry.is_symlink():
                    continue
                relative_parts.append(entry.name)
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if not any(
                            pattern_depth is None or len(relative_parts) < pattern_depth
                            for pattern_depth in allowed_depths
                        ):
                            continue
                        if len(relative_parts) > max_depth:
                            raise SummaryError(
                                f"directory depth exceeds max-depth={max_depth}"
                            )
                        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW
                        child_fd = os.open(entry.name, flags, dir_fd=directory_fd)
                        try:
                            visit(child_fd, relative_parts, allowed_depths)
                        finally:
                            os.close(child_fd)
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    relative = "/".join(relative_parts)
                    if any(pattern.fullmatch(relative) for pattern in patterns):
                        found[relative] = tuple(relative_parts)
                        if len(found) > max_files:
                            raise SummaryError(
                                f"matched more than max-files={max_files} unique files"
                            )
                finally:
                    relative_parts.pop()

    for root, allowed_depths in plans:
        if len(root) > max_depth:
            raise SummaryError(f"junit-paths static prefix exceeds max-depth={max_depth}")
        try:
            scan_fd = open_relative_directory(root_fd, root)
        except FileNotFoundError:
            continue
        try:
            visit(scan_fd, list(root), allowed_depths)
        finally:
            os.close(scan_fd)
    return [found[key] for key in sorted(found)]


def read_regular_file(root_fd: int, parts: Sequence[str], max_file_bytes: int) -> bytes:
    """Read a bounded regular file beneath root_fd without following symlinks."""
    source = "/".join(parts)
    parent_fd = open_relative_directory(root_fd, parts[:-1])
    file_fd = -1
    try:
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
        file_fd = os.open(parts[-1], flags, dir_fd=parent_fd)
        metadata = os.fstat(file_fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise SummaryError(f"{source}: matched path is no longer a regular file")
        if metadata.st_size > max_file_bytes:
            raise SummaryError(
                f"{source}: file size {metadata.st_size} exceeds max-file-bytes={max_file_bytes}"
            )
        chunks: List[bytes] = []
        remaining = max_file_bytes + 1
        while remaining > 0:
            chunk = os.read(file_fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > max_file_bytes:
            raise SummaryError(f"{source}: content exceeds max-file-bytes={max_file_bytes}")
        return raw
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        os.close(parent_fd)


def command_text(value: str) -> str:
    """Escape untrusted text so it cannot start or alter a GitHub workflow command."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def format_decimal(value: Decimal) -> str:
    """Render a bounded Decimal without unnecessary trailing zeros."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def write_outputs(path: Optional[str], files: int, stats: Stats) -> None:
    """Append aggregate values to the GitHub Actions output file."""
    if not path:
        return
    values = {
        "files": str(files),
        "tests": str(stats.tests),
        "passed": str(stats.passed),
        "failures": str(stats.failures),
        "errors": str(stats.errors),
        "skipped": str(stats.skipped),
        "duration": format_decimal(stats.duration),
        "has-failures": "true" if stats.failures or stats.errors else "false",
    }
    with Path(path).open("a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


def write_summary(path: Optional[str], title: str, files: int, stats: Stats) -> None:
    """Append an aggregate Markdown table to the job summary."""
    if not path:
        print("GITHUB_STEP_SUMMARY is not set; numeric outputs were still computed.")
        return
    suffix = " (failures)" if stats.failures or stats.errors else ""
    lines = [
        f"### {title}{suffix}",
        "",
        "| Files | Tests | Passed | Failed | Errors | Skipped | Duration |",
        "|------:|------:|-------:|-------:|-------:|--------:|---------:|",
        f"| {files} | {stats.tests} | {stats.passed} | {stats.failures} | {stats.errors} | {stats.skipped} | {format_decimal(stats.duration)}s |",
        "",
    ]
    with Path(path).open("a", encoding="utf-8") as summary:
        summary.write("\n".join(lines))


def resolve_contract() -> Tuple[str, str]:
    """Resolve generic inputs or the deprecated CSP migration contract."""
    working_directory = os.environ.get("WORKING_DIRECTORY", ".")
    junit_paths = os.environ.get("JUNIT_PATHS", DEFAULT_JUNIT_PATHS)
    legacy_cwd = os.environ.get("LEGACY_CWD", "")
    legacy_variant = os.environ.get("LEGACY_VARIANT", "")
    if legacy_cwd or legacy_variant:
        if not legacy_cwd or not legacy_variant:
            raise SummaryError("deprecated cwd and variant inputs must be supplied together")
        if working_directory != "." or junit_paths != DEFAULT_JUNIT_PATHS:
            raise SummaryError(
                "deprecated cwd/variant inputs cannot be combined with working-directory/junit-paths"
            )
        if legacy_variant not in LEGACY_VARIANTS:
            raise SummaryError(f"unsupported deprecated variant: {legacy_variant}")
        return legacy_cwd, LEGACY_VARIANTS[legacy_variant]
    return working_directory, junit_paths


def main() -> int:
    """Run the action and return its process exit code."""
    workspace_fd = -1
    workdir_fd = -1
    try:
        workspace_raw = os.environ.get("GITHUB_WORKSPACE", "")
        if not workspace_raw:
            raise SummaryError("GITHUB_WORKSPACE is required")
        workspace = Path(workspace_raw).resolve()
        workspace_fd = os.open(
            workspace,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
        )

        fail_on_missing = os.environ.get("FAIL_ON_MISSING", "false")
        if fail_on_missing not in {"true", "false"}:
            raise SummaryError("fail-on-missing must be 'true' or 'false'")
        title = os.environ.get("TITLE", "Test results")
        if not title or len(title) > 200 or any(ord(character) < 32 for character in title):
            raise SummaryError("title must be a non-empty single line of at most 200 characters")

        max_files = parse_bounded_int(os.environ.get("MAX_FILES", "200"), "max-files", 1, 10000)
        max_file_bytes = parse_bounded_int(
            os.environ.get("MAX_FILE_BYTES", "10485760"),
            "max-file-bytes",
            1,
            104857600,
        )
        max_total_bytes = parse_bounded_int(
            os.environ.get("MAX_TOTAL_BYTES", "52428800"),
            "max-total-bytes",
            1,
            1073741824,
        )
        max_scan_entries = parse_bounded_int(
            os.environ.get("MAX_SCAN_ENTRIES", "100000"),
            "max-scan-entries",
            1,
            1000000,
        )
        max_depth = parse_bounded_int(
            os.environ.get("MAX_DEPTH", "64"),
            "max-depth",
            1,
            256,
        )

        working_directory, junit_paths = resolve_contract()
        workdir_parts = validate_relative_path(working_directory, "working-directory")
        raw_patterns = [line.strip() for line in junit_paths.splitlines() if line.strip()]
        if len(raw_patterns) > MAX_GLOB_PATTERNS:
            raise SummaryError(f"junit-paths must not contain more than {MAX_GLOB_PATTERNS} entries")
        normalized_patterns = [
            "/".join(validate_relative_path(pattern, "junit-paths entry"))
            for pattern in raw_patterns
        ]
        patterns = [compile_glob(pattern) for pattern in raw_patterns]
        if not patterns:
            raise SummaryError("junit-paths must contain at least one non-empty path or glob")
        plans = scan_plans(normalized_patterns)
        workdir_fd = open_relative_directory(workspace_fd, workdir_parts)
        files = scan_files(
            workdir_fd,
            patterns,
            plans,
            max_files,
            max_scan_entries,
            max_depth,
        )
        if not files and fail_on_missing == "true":
            raise SummaryError("no regular JUnit XML files matched junit-paths")

        total = Stats()
        total_bytes = 0
        for parts in files:
            raw = read_regular_file(workdir_fd, parts, max_file_bytes)
            total_bytes += len(raw)
            if total_bytes > max_total_bytes:
                raise SummaryError(
                    f"matched content exceeds max-total-bytes={max_total_bytes}"
                )
            total.add(parse_junit(raw, "/".join(parts)))

        write_outputs(os.environ.get("GITHUB_OUTPUT"), len(files), total)
        write_summary(os.environ.get("GITHUB_STEP_SUMMARY"), title, len(files), total)
        if not files:
            print("::warning::No regular JUnit XML files matched junit-paths")
        else:
            print(
                f"Aggregated {len(files)} JUnit file(s): tests={total.tests} "
                f"passed={total.passed} failures={total.failures} errors={total.errors} "
                f"skipped={total.skipped}"
            )
        return 0
    except (OSError, SummaryError) as exc:
        print(f"junit-step-summary: {command_text(str(exc))}", file=sys.stderr)
        return 2
    finally:
        if workdir_fd >= 0:
            os.close(workdir_fd)
        if workspace_fd >= 0:
            os.close(workspace_fd)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Summarize bounded, workspace-contained Gradle Problems Reports in a GitHub step summary.

Report content is untrusted: the embedded JSON is parsed as data only, and every rendered
field is normalized, truncated, and escaped before it reaches Markdown.
"""

from __future__ import annotations

import html
import json
import os
import re
import stat
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Pattern, Sequence, Tuple


class SummaryError(ValueError):
    """Raised for invalid action inputs or limit violations."""


class ReportError(ValueError):
    """Raised for a malformed or unsupported report; summarized as a warning."""


MAX_GLOB_PATTERNS = 64
MAX_GLOB_LENGTH = 1024
MAX_GLOBSTARS = 4
MAX_FIELD_CHARS = 200
MAX_LOCATION_CHARS = 160
MAX_COUNT = 1_000_000_000
DEFAULT_REPORT_PATHS = "build/reports/problems/problems-report.html"
MAX_ENTRIES = 100_000
BEGIN_MARKER = "// begin-report-data\n"
END_MARKER = "\n// end-report-data"
# Every ASCII punctuation character that can start Markdown, GFM, or math syntax.
MARKDOWN_SPECIALS = re.compile(r"([\\`*_{}\[\]()#+\-.!|~$^=:])")
# Zero-width spaces keep GFM from turning untrusted URLs and email addresses into autolinks.
AUTOLINK_BREAKS = (
    (re.compile(re.escape("://")), ":\u200b//"),
    (re.compile(re.escape("www."), re.IGNORECASE), "www\u200b."),
    (re.compile(re.escape("@")), "@\u200b"),
)
STRIPPED_CATEGORIES = {"Cc", "Cf", "Co", "Cs", "Zl", "Zp"}
SEVERITY_ORDER = {"ERROR": 0, "WARNING": 1, "ADVICE": 2}
TRUNCATION_RESERVE = 256


def parse_bounded_int(raw: str, name: str, minimum: int, maximum: int) -> int:
    """Parse an integer action input constrained to an inclusive range."""
    if not raw.isascii() or not raw.isdecimal() or len(raw) > len(str(maximum)):
        raise SummaryError(f"{name} must be an integer from {minimum} through {maximum}")
    value = int(raw)
    if not minimum <= value <= maximum:
        raise SummaryError(f"{name} must be an integer from {minimum} through {maximum}")
    return value


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
        raise SummaryError(f"report-paths entries must not exceed {MAX_GLOB_LENGTH} characters")
    parts = validate_relative_path(pattern, "report-paths entry")
    if any("**" in part and part != "**" for part in parts):
        raise SummaryError("report-paths supports ** only as a complete path segment")
    parts = tuple(
        part
        for index, part in enumerate(parts)
        if part != "**" or index == 0 or parts[index - 1] != "**"
    )
    if parts.count("**") > MAX_GLOBSTARS:
        raise SummaryError(
            f"report-paths entries must not contain more than {MAX_GLOBSTARS} ** segments"
        )
    pattern = "/".join(parts)
    if any(character in pattern for character in "[]{}"):
        raise SummaryError("report-paths supports only literal characters, *, ?, and ** wildcards")
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
    parts = validate_relative_path(pattern, "report-paths entry")
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
            raise SummaryError(f"report-paths static prefix exceeds max-depth={max_depth}")
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


def markdown_text(value: object, limit: int = MAX_FIELD_CHARS) -> str:
    """Render untrusted text as inert single-line Markdown."""
    text = value if isinstance(value, str) else ""
    text = "".join(
        " " if unicodedata.category(character) in STRIPPED_CATEGORIES else character
        for character in text[: limit * 4]
    )
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    for unsafe, broken in AUTOLINK_BREAKS:
        text = unsafe.sub(broken, text)
    return html.escape(MARKDOWN_SPECIALS.sub(r"\\\1", text), quote=True)


def command_text(value: str) -> str:
    """Escape untrusted text for a GitHub workflow command message."""
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def bounded_count(value: object) -> int:
    """Return a non-negative integer count, or zero for anything else."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return min(value, MAX_COUNT)


def problem_key(problem_id: object) -> Tuple[Tuple[str, str], ...]:
    """Normalize a Gradle problemId chain into (name, displayName) pairs."""
    if not isinstance(problem_id, list):
        return ()
    key: List[Tuple[str, str]] = []
    for part in problem_id[:16]:
        if isinstance(part, dict):
            name = part.get("name")
            display = part.get("displayName")
            key.append(
                (
                    name[:MAX_FIELD_CHARS] if isinstance(name, str) else "",
                    display[:MAX_FIELD_CHARS] if isinstance(display, str) else "",
                )
            )
    return tuple(key)


def format_location(locations: object, workspaces: Sequence[str]) -> str:
    """Return the first file location, or else the first task path, as plain text."""
    if not isinstance(locations, list):
        return ""
    task_path = ""
    for location in locations[:32]:
        if not isinstance(location, dict):
            continue
        path = location.get("path")
        if isinstance(path, str) and path:
            for workspace in workspaces:
                if path.startswith(workspace + "/"):
                    path = path[len(workspace) + 1 :]
                    break
            text = path
            for key in ("line", "column"):
                number = location.get(key)
                if isinstance(number, int) and not isinstance(number, bool) and number > 0:
                    text += f":{min(number, MAX_COUNT)}"
                else:
                    break
            return text
        candidate = location.get("taskPath")
        if not task_path and isinstance(candidate, str):
            task_path = candidate
    return task_path


@dataclass
class ProblemGroup:
    """Occurrences of one problem id and severity within a report."""

    key: Tuple[Tuple[str, str], ...]
    severity: str
    message: str = ""
    locations: Dict[str, None] = field(default_factory=dict)
    count: int = 0


@dataclass
class Report:
    """Parsed problem groups and metadata for one report file."""

    source: str
    build_name: str
    requested_tasks: str
    groups: List[ProblemGroup]
    problems: int
    ignored_entries: int


def find_data_block(text: str) -> Optional[str]:
    """Return the text between the first begin-marker line and the next end-marker line.

    Only forward str.find scans are used, so repeated hostile markers stay linear.
    """
    if text.startswith(BEGIN_MARKER):
        start = len(BEGIN_MARKER)
    else:
        begin = text.find("\n" + BEGIN_MARKER)
        if begin < 0:
            return None
        start = begin + 1 + len(BEGIN_MARKER)
    end = text.find(END_MARKER, start - 1)
    while end >= 0:
        after = end + len(END_MARKER)
        if after == len(text) or text[after] == "\n":
            return text[start:end] if end >= start else ""
        end = text.find(END_MARKER, after)
    return None


def extract_data(raw: bytes) -> Dict[str, object]:
    """Locate and decode the JSON data block embedded in the report HTML."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReportError("report is not valid UTF-8") from exc
    data_text = find_data_block(text.replace("\r\n", "\n"))
    if data_text is None:
        raise ReportError("unsupported report format: no embedded report-data block")
    try:
        data = json.loads(data_text)
    except RecursionError as exc:
        raise ReportError("report data is nested too deeply") from exc
    except ValueError as exc:
        raise ReportError(f"report data is not valid JSON: {exc.__class__.__name__}") from exc
    if not isinstance(data, dict):
        raise ReportError("unsupported report format: report data is not an object")
    if not isinstance(data.get("problemsReport"), dict):
        raise ReportError(
            "unsupported report format: no problemsReport section (not a Gradle Problems Report)"
        )
    if not isinstance(data.get("diagnostics"), list):
        raise ReportError("unsupported report format: diagnostics is not a list")
    return data


def parse_report(raw: bytes, source: str, workspaces: Sequence[str]) -> Report:
    """Group diagnostics by problem id and severity, adding Gradle's skipped counts."""
    data = extract_data(raw)
    metadata = data["problemsReport"]
    assert isinstance(metadata, dict)
    diagnostics = data["diagnostics"]
    assert isinstance(diagnostics, list)
    groups: Dict[Tuple[Tuple[str, ...], str], ProblemGroup] = {}
    first_by_names: Dict[Tuple[str, ...], ProblemGroup] = {}
    ignored = max(len(diagnostics) - MAX_ENTRIES, 0)
    problems = 0
    for entry in diagnostics[:MAX_ENTRIES]:
        if not isinstance(entry, dict):
            ignored += 1
            continue
        key = problem_key(entry.get("problemId"))
        severity = entry.get("severity")
        severity = severity.upper()[:16] if isinstance(severity, str) and severity else "UNKNOWN"
        names = tuple(name for name, _ in key)
        group = groups.setdefault((names, severity), ProblemGroup(key=key, severity=severity))
        first_by_names.setdefault(names, group)
        group.count = min(group.count + 1, MAX_COUNT)
        problems += 1
        label = entry.get("contextualLabel")
        if not group.message and isinstance(label, str):
            group.message = label[: MAX_FIELD_CHARS * 4]
        location = format_location(entry.get("locations"), workspaces)
        if location and len(group.locations) <= 1024:
            group.locations.setdefault(location[: MAX_LOCATION_CHARS * 4], None)

    summaries = metadata.get("summaries")
    summaries = summaries if isinstance(summaries, list) else []
    ignored += max(len(summaries) - MAX_ENTRIES, 0)
    for entry in summaries[:MAX_ENTRIES]:
        if not isinstance(entry, dict):
            ignored += 1
            continue
        skipped = bounded_count(entry.get("count"))
        key = problem_key(entry.get("problemId"))
        names = tuple(name for name, _ in key)
        group = first_by_names.get(names)
        if group is None:
            group = groups.setdefault((names, "UNKNOWN"), ProblemGroup(key=key, severity="UNKNOWN"))
            first_by_names[names] = group
        group.count = min(group.count + skipped, MAX_COUNT)
        problems += skipped

    build_name = metadata.get("buildName")
    requested = metadata.get("requestedTasks")
    ordered = sorted(
        groups.values(),
        key=lambda group: (SEVERITY_ORDER.get(group.severity, 3), -group.count),
    )
    return Report(
        source=source,
        build_name=build_name if isinstance(build_name, str) else "",
        requested_tasks=requested if isinstance(requested, str) else "",
        groups=ordered,
        problems=min(problems, MAX_COUNT),
        ignored_entries=ignored,
    )


def group_row(group: ProblemGroup) -> str:
    """Render one escaped Markdown table row."""
    names = [display or name for name, display in group.key]
    problem = names[-1] if names else "unknown problem"
    category = " / ".join(names[:-1])
    locations = list(group.locations)
    location = markdown_text(locations[0], MAX_LOCATION_CHARS) if locations else ""
    if len(locations) > 1:
        more = f"{len(locations) - 1}+" if len(locations) > 1024 else str(len(locations) - 1)
        location += " " + markdown_text(f"(+{more} more)")
    cells = [
        markdown_text(group.severity),
        markdown_text(problem),
        markdown_text(category),
        markdown_text(group.message or problem),
        location,
        str(group.count),
    ]
    return "| " + " | ".join(cells) + " |"


def plural(count: int, word: str) -> str:
    """Return a counted English noun."""
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def render_summary(
    title: str,
    reports: Sequence[Report],
    invalid: Sequence[Tuple[str, str]],
    max_rows: int,
    max_bytes: int,
) -> str:
    """Render a bounded Markdown summary for parsed and invalid reports."""
    total = sum(report.problems for report in reports)
    lines: List[str] = [f"### {markdown_text(title)}", ""]
    if not reports and not invalid:
        lines += [
            "No Gradle Problems Report matched `report-paths`; Gradle writes one only when it reports problems.",
            "",
        ]
    else:
        lines += [f"{plural(total, 'problem')} in {plural(len(reports), 'report')}.", ""]

    body: List[str] = []
    rows_left = max_rows
    for report in reports:
        body += [f"#### {markdown_text(report.source, MAX_LOCATION_CHARS)}", ""]
        details = []
        if report.build_name:
            details.append(f"Build {markdown_text(report.build_name)}")
        if report.requested_tasks:
            details.append(f"tasks {markdown_text(report.requested_tasks)}")
        details.append(plural(report.problems, "problem"))
        body += [", ".join(details) + ".", ""]
        if report.ignored_entries:
            body += [f"{report.ignored_entries} malformed report entries were ignored.", ""]
        if not report.groups:
            body += ["No problems reported.", ""]
            continue
        shown = report.groups[:rows_left]
        if shown:
            body += [
                "| Severity | Problem | Group | Message | Location | Count |",
                "|---|---|---|---|---|---:|",
            ]
            body += [group_row(group) for group in shown]
            body.append("")
        rows_left -= len(shown)
        hidden = len(report.groups) - len(shown)
        if hidden:
            body += [f"{hidden} more problem types not shown (max-rows={max_rows}).", ""]
    for source, reason in invalid:
        body += [
            f"Report {markdown_text(source, MAX_LOCATION_CHARS)} could not be summarized: "
            f"{markdown_text(reason)}.",
            "",
        ]

    output = "\n".join(lines) + "\n"
    notice = f"Summary truncated at max-summary-bytes={max_bytes}.\n"
    used = len(output.encode("utf-8"))
    for line in body:
        size = len(line.encode("utf-8")) + 1
        if used + size + TRUNCATION_RESERVE > max_bytes:
            return output + ("\n" if not output.endswith("\n\n") else "") + notice
        output += line + "\n"
        used += size
    return output


def write_outputs(path: Optional[str], reports: Sequence[Report], invalid: int) -> None:
    """Append aggregate values to the GitHub Actions output file."""
    if not path:
        return
    problems = sum(report.problems for report in reports)
    values = {
        "reports": str(len(reports)),
        "invalid-reports": str(invalid),
        "problems": str(problems),
        "has-problems": "true" if problems else "false",
    }
    with Path(path).open("a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={value}\n")


def main() -> int:
    """Run the action and return its process exit code."""
    workspace_fd = -1
    workdir_fd = -1
    try:
        workspace_raw = os.environ.get("GITHUB_WORKSPACE", "")
        if not workspace_raw:
            raise SummaryError("GITHUB_WORKSPACE is required")
        workspace = Path(workspace_raw).resolve()
        workspaces = [str(workspace), workspace_raw.rstrip("/")]
        workspace_fd = os.open(
            workspace,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
        )

        fail_on_missing = os.environ.get("FAIL_ON_MISSING", "false")
        if fail_on_missing not in {"true", "false"}:
            raise SummaryError("fail-on-missing must be 'true' or 'false'")
        title = os.environ.get("TITLE", "Gradle problems")
        if not title or len(title) > 200 or any(ord(character) < 32 for character in title):
            raise SummaryError("title must be a non-empty single line of at most 200 characters")

        max_files = parse_bounded_int(os.environ.get("MAX_FILES", "50"), "max-files", 1, 1000)
        max_file_bytes = parse_bounded_int(
            os.environ.get("MAX_FILE_BYTES", "10485760"), "max-file-bytes", 1, 104857600
        )
        max_total_bytes = parse_bounded_int(
            os.environ.get("MAX_TOTAL_BYTES", "52428800"), "max-total-bytes", 1, 1073741824
        )
        max_scan_entries = parse_bounded_int(
            os.environ.get("MAX_SCAN_ENTRIES", "100000"), "max-scan-entries", 1, 1000000
        )
        max_depth = parse_bounded_int(os.environ.get("MAX_DEPTH", "64"), "max-depth", 1, 256)
        max_rows = parse_bounded_int(os.environ.get("MAX_ROWS", "50"), "max-rows", 1, 1000)
        max_summary_bytes = parse_bounded_int(
            os.environ.get("MAX_SUMMARY_BYTES", "65536"), "max-summary-bytes", 1024, 1048576
        )

        working_directory = os.environ.get("WORKING_DIRECTORY", ".")
        workdir_parts = validate_relative_path(working_directory, "working-directory")
        report_paths = os.environ.get("REPORT_PATHS", DEFAULT_REPORT_PATHS)
        raw_patterns = [line.strip() for line in report_paths.splitlines() if line.strip()]
        if len(raw_patterns) > MAX_GLOB_PATTERNS:
            raise SummaryError(f"report-paths must not contain more than {MAX_GLOB_PATTERNS} entries")
        normalized_patterns = [
            "/".join(validate_relative_path(pattern, "report-paths entry"))
            for pattern in raw_patterns
        ]
        patterns = [compile_glob(pattern) for pattern in raw_patterns]
        if not patterns:
            raise SummaryError("report-paths must contain at least one non-empty path or glob")
        plans = scan_plans(normalized_patterns)
        try:
            workdir_fd = open_relative_directory(workspace_fd, workdir_parts)
            files = scan_files(
                workdir_fd, patterns, plans, max_files, max_scan_entries, max_depth
            )
        except FileNotFoundError:
            files = []
        if not files and fail_on_missing == "true":
            raise SummaryError("no Gradle Problems Report matched report-paths")

        prefix = "/".join(workdir_parts)
        reports: List[Report] = []
        invalid: List[Tuple[str, str]] = []
        total_bytes = 0
        for parts in files:
            source = "/".join((prefix, *parts)) if prefix else "/".join(parts)
            raw = read_regular_file(workdir_fd, parts, max_file_bytes)
            total_bytes += len(raw)
            if total_bytes > max_total_bytes:
                raise SummaryError(f"matched content exceeds max-total-bytes={max_total_bytes}")
            try:
                reports.append(parse_report(raw, source, workspaces))
            except ReportError as exc:
                invalid.append((source, str(exc)))
                print(f"::warning::{command_text(f'{source}: {exc}')}")

        write_outputs(os.environ.get("GITHUB_OUTPUT"), reports, len(invalid))
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            summary = render_summary(title, reports, invalid, max_rows, max_summary_bytes)
            with Path(summary_path).open("a", encoding="utf-8") as handle:
                handle.write(summary)
        else:
            print("GITHUB_STEP_SUMMARY is not set; outputs were still computed.")
        if not files:
            print("No Gradle Problems Report matched report-paths.")
        else:
            problems = sum(report.problems for report in reports)
            print(
                command_text(
                    f"Summarized {len(reports)} Gradle Problems Report(s) with {problems} "
                    f"problem(s); {len(invalid)} report(s) could not be summarized."
                )
            )
        return 0
    except (OSError, SummaryError) as exc:
        print(f"gradle-problems-summary: {command_text(str(exc))}", file=sys.stderr)
        return 2
    finally:
        if workdir_fd >= 0:
            os.close(workdir_fd)
        if workspace_fd >= 0:
            os.close(workspace_fd)


if __name__ == "__main__":
    raise SystemExit(main())

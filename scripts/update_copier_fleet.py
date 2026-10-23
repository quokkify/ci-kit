#!/usr/bin/env python3
"""Update every Copier-managed repository in a GitHub organization.

The updater intentionally accepts only ci-kit template sources. In
write mode it owns one deterministic branch per repository and creates or
refreshes a pull request. Dry-run mode performs the same Copier rendering
without pushing anything.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.parse
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.drift import TRUSTED_SRC_RE
from ci_kit.handoff import AREAS
from ci_kit.modules import MODULES
from ci_kit.plan import ANSWERS_RE

ANSWERS_FILE = ".copier-answers.yml"
LEGACY_MODULE = "legacy"
CI_KIT_TOOLS = Path(__file__).resolve().parents[1] / "tools"
TEMPLATE_RELEASE_CONFIG = Path(__file__).resolve().parents[1] / "template/.github/release-please/config.json.jinja"
DEFAULT_BRANCH = "automation/copier-template-update"
DEFAULT_TEMPLATE_REPOSITORY = "quokkify/ci-kit"
RENAMED_TEMPLATE_REPOSITORIES = {"quokkify/project-toolkit": "quokkify/ci-kit"}
PR_TITLE = "chore(template): update shared project template"
PR_LABEL = "area: template"
PRIVATE_REPOSITORY_ERROR = (
    "--public-only rejects non-public repositories; private consumers are not served by the "
    "public fleet automation and are updated by a maintainer running this script without "
    "--public-only from a short-lived local gh session"
)
RELEASE_TAG_PATTERN = re.compile(
    r"^v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$"
)
COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
# Answers files are written in the style Prettier emits for YAML, by both the
# template and this updater: plain scalars where YAML reads them back as the same
# string, double-quoted JSON strings otherwise, and sequences indented under their
# key. The template's .copier-answers.yml.jinja repeats these two patterns.
ANSWERS_PLAIN_PATTERN = re.compile(r"^[A-Za-z_./][A-Za-z0-9_./@+-]*(?::[A-Za-z0-9_./@+-]+)*(?: [A-Za-z0-9_./@+-]+(?::[A-Za-z0-9_./@+-]+)*)*$")
ANSWERS_RESERVED_PATTERN = re.compile(r"^(?:y|n|yes|no|true|false|on|off|null|\.inf|\.nan|\.[0-9].*)$", re.IGNORECASE)


class FleetUpdateError(RuntimeError):
    """A repository or fleet operation could not be completed safely."""


class RepositoryProcessError(FleetUpdateError):
    """A managed repository failed after its inventory was collected."""

    def __init__(self, message: str, inventory: "TemplateInventory") -> None:
        super().__init__(message)
        self.inventory = inventory


class UnsafeAnswersFileError(FleetUpdateError):
    """An answers file is not a regular file."""


@dataclass(frozen=True)
class Repository:
    name_with_owner: str
    default_branch: str


@dataclass(frozen=True)
class Result:
    repository: str
    status: str
    detail: str = ""
    inventory: "TemplateInventory | None" = None
    health: dict[str, Any] | None = None
    auto_merge: dict[str, str] | None = None


@dataclass(frozen=True)
class TemplateInventory:
    commit: str
    target_commit: str | None
    components: tuple[str, ...]
    baseline: str
    missing_baseline: tuple[str, ...]
    docker: str
    codeql: str
    allure_report: str
    release_please: str
    renovate: str
    modules: tuple[str, ...] = ()
    state: str = "legacy"


BASELINE_PATHS = (
    ".github/workflows/validate.yml",
    ".github/workflows/gitleaks.yml",
)
FEATURE_PATHS = {
    "codeql": ".github/workflows/codeql.yml",
    "allure_report": ".github/workflows/allure-report.yml",
    "release_please": ".github/workflows/release.yml",
    "renovate": ".github/renovate.json",
}
ALLURE_CONFIG_PATH = ".github/allure/allurerc.mjs"
ALLURE_EXTRACTOR_PATH = ".github/allure/safe_extract.py"
ALLURE_EXPECTED_PATHS = (
    FEATURE_PATHS["allure_report"],
    ALLURE_CONFIG_PATH,
    ALLURE_EXTRACTOR_PATH,
)
FEATURE_LABELS = {
    "enabled": "on",
    "disabled": "off",
    "missing": "missing",
    "custom": "custom",
    "unknown": "unknown",
}


def is_regular_file(path: Path, *, repository_root: Path) -> bool:
    try:
        relative_path = path.relative_to(repository_root)
    except ValueError:
        return False
    current = repository_root
    if current.is_symlink():
        return False
    for part in relative_path.parts:
        current /= part
        if current.is_symlink():
            return False
    return path.is_file()


def sanitize_text(value: str) -> str:
    sanitized: list[str] = []
    for character in value:
        codepoint = ord(character)
        if not unicodedata.category(character).startswith("C"):
            sanitized.append(character)
        elif codepoint <= 0xFF:
            sanitized.append(f"\\x{codepoint:02x}")
        elif codepoint <= 0xFFFF:
            sanitized.append(f"\\u{codepoint:04x}")
        else:
            sanitized.append(f"\\U{codepoint:08x}")
    return "".join(sanitized)


def run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        list(command),
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and completed.returncode:
        rendered = " ".join(command)
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise FleetUpdateError(f"{rendered} failed ({completed.returncode}): {detail}")
    return completed


def gh_json(arguments: Sequence[str], *, env: dict[str, str]) -> Any:
    completed = run(["gh", *arguments], env=env)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise FleetUpdateError(f"gh returned invalid JSON for {' '.join(arguments)}") from exc


def gh_json_lines(arguments: Sequence[str], *, env: dict[str, str]) -> list[Any]:
    """Read one JSON value per line, including paginated gh API output."""
    completed = run(["gh", *arguments], env=env)
    values: list[Any] = []
    for line in completed.stdout.splitlines():
        try:
            values.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise FleetUpdateError(f"gh returned invalid JSON for {' '.join(arguments)}") from exc
    return values


def resolve_template_ref(
    template_repository: str,
    requested_ref: str | None,
    *,
    env: dict[str, str],
) -> str:
    """Resolve the implicit Copier target to the latest stable release tag."""
    if requested_ref:
        return requested_ref
    release = gh_json(
        ["release", "view", "--repo", template_repository, "--json", "tagName"],
        env=env,
    )
    tag_name = release.get("tagName") if isinstance(release, dict) else None
    if not isinstance(tag_name, str) or not RELEASE_TAG_PATTERN.fullmatch(tag_name):
        raise FleetUpdateError("latest template release must use an exact vMAJOR.MINOR.PATCH tag")
    return tag_name


def resolve_template_commit(
    template_repository: str,
    template_ref: str,
    *,
    env: dict[str, str],
) -> str:
    """Resolve a template release tag to the commit a digest pin has to name."""
    payload = gh_json(
        ["api", f"repos/{template_repository}/commits/{template_ref}", "--jq", "{sha: .sha}"],
        env=env,
    )
    sha = payload.get("sha") if isinstance(payload, dict) else None
    if not isinstance(sha, str) or not COMMIT_SHA_PATTERN.fullmatch(sha):
        raise FleetUpdateError(f"{template_repository}@{template_ref} did not resolve to a commit")
    return sha


def is_public(metadata: object) -> bool:
    """Return whether GitHub reports this repository as public."""
    if not isinstance(metadata, dict):
        return False
    visibility = metadata.get("visibility")
    return isinstance(visibility, str) and visibility.casefold() == "public"


def discover_repositories(
    org: str,
    *,
    env: dict[str, str],
    public_only: bool = False,
) -> list[Repository]:
    arguments = [
        "repo",
        "list",
        org,
        "--limit",
        "1000",
        "--json",
        "nameWithOwner,defaultBranchRef,isArchived,isFork,visibility",
    ]
    if public_only:
        arguments.extend(["--visibility", "public"])
    items = gh_json(arguments, env=env)
    if len(items) >= 1000:
        raise FleetUpdateError(
            f"repository discovery reached the 1000-repository safety limit for {org}; "
            "refuse to run with a potentially truncated fleet"
        )
    repositories: list[Repository] = []
    for item in items:
        if public_only and not is_public(item):
            continue
        if item.get("isArchived") or item.get("isFork"):
            continue
        default_ref = item.get("defaultBranchRef")
        full_name = item.get("nameWithOwner")
        if isinstance(default_ref, dict) and isinstance(default_ref.get("name"), str) and isinstance(full_name, str):
            repositories.append(Repository(full_name, default_ref["name"]))
    return sorted(repositories, key=lambda repository: repository.name_with_owner.casefold())


def fetch_answers_files(repository: str, *, env: dict[str, str], ref: str | None = None) -> dict[str, str]:
    query = f"?ref={urllib.parse.quote(ref, safe='')}" if ref else ""
    listing = run(["gh", "api", f"repos/{repository}/contents/{query}"], env=env, check=False)
    if listing.returncode:
        if "404" in listing.stderr or "Not Found" in listing.stderr:
            return {}
        detail = listing.stderr.strip() or listing.stdout.strip()
        raise FleetUpdateError(f"cannot list {repository}: {detail}")
    try:
        entries = json.loads(listing.stdout)
    except json.JSONDecodeError as exc:
        raise FleetUpdateError(f"gh returned invalid JSON listing {repository}") from exc
    if not isinstance(entries, list):
        raise FleetUpdateError(f"unexpected contents listing for {repository}")
    files: dict[str, str] = {}
    for entry in entries:
        name = entry.get("name") if isinstance(entry, dict) else None
        if not isinstance(name, str):
            continue
        match = ANSWERS_RE.fullmatch(name)
        if name != ANSWERS_FILE and not match:
            continue
        module = match.group(1) if match else LEGACY_MODULE
        if entry.get("type") != "file" or (match and module == LEGACY_MODULE):
            raise UnsafeAnswersFileError(name)
        completed = run(
            [
                "gh",
                "api",
                f"repos/{repository}/contents/{name}{query}",
                "-H",
                "Accept: application/vnd.github.raw+json",
            ],
            env=env,
            check=False,
        )
        if completed.returncode:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise FleetUpdateError(f"cannot read {repository}/{name}: {detail}")
        files[module] = completed.stdout
    return files


def normalize_template_source(source: str) -> str | None:
    value = source.strip().removesuffix("/").removesuffix(".git")
    prefixes = (
        "gh:",
        "https://github.com/",
        "http://github.com/",
        "git@github.com:",
        "ssh://git@github.com/",
    )
    for prefix in prefixes:
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        owner, repository = value.split("/", 1)
        if owner not in {".", ".."} and repository not in {".", ".."}:
            return RENAMED_TEMPLATE_REPOSITORIES.get(value.casefold(), value.casefold())
    return None


def parse_answers(raw_answers: str) -> dict[str, Any]:
    try:
        answers = yaml.safe_load(raw_answers)
    except yaml.YAMLError as exc:
        raise FleetUpdateError(f"invalid {ANSWERS_FILE}: {exc}") from exc
    if not isinstance(answers, dict):
        raise FleetUpdateError(f"{ANSWERS_FILE} must contain a YAML mapping")
    return answers


def parse_template_source(raw_answers: str) -> str:
    answers = parse_answers(raw_answers)
    if not isinstance(answers.get("_src_path"), str):
        raise FleetUpdateError(f"{ANSWERS_FILE} does not contain a string _src_path")
    return answers["_src_path"]


def _shell_heredoc_free(run: str) -> str:
    """Remove heredoc bodies before inspecting shell command tokens."""
    lines = run.splitlines(keepends=True)
    kept: list[str] = []
    delimiters: list[tuple[str, bool, bool]] = []
    quote: str | None = None
    arithmetic_depth = 0
    paren_arithmetic_depth = 0
    line_index = 0
    while line_index < len(lines):
        line = lines[line_index]
        if delimiters:
            delimiter, strip_tabs, quoted = delimiters.pop(0)
            body_lines = [line]
            consumed_body_lines = 1
            # For an unquoted heredoc, Bash removes backslash-newline pairs
            # from the body before checking the closing delimiter.  Fold the
            # physical lines here so a delimiter-like line joined to prior
            # body text cannot be mistaken for the closing delimiter.
            if not quoted:
                while True:
                    body_text = body_lines[-1].rstrip("\r\n")
                    trailing_slashes = len(body_text) - len(body_text.rstrip("\\"))
                    if trailing_slashes % 2 == 0:
                        break
                    next_index = line_index + consumed_body_lines
                    if next_index >= len(lines):
                        break
                    body_lines[-1] = body_text[:-1]
                    body_lines.append(lines[next_index])
                    consumed_body_lines += 1
            candidate = "".join(body_lines).rstrip("\r\n")
            if (candidate.lstrip("\t") if strip_tabs else candidate) != delimiter:
                delimiters.insert(0, (delimiter, strip_tabs, quoted))
            else:
                # The command line already retained its terminating newline,
                # which separates it from commands after the heredoc.
                pass
            line_index += consumed_body_lines
            continue
        # A backslash-newline is removed by the shell before parsing.  Fold
        # such continuations for delimiter recognition, while retaining the
        # original physical lines in the output and advancing past them.
        logical_line = line
        consumed_lines = 1
        while logical_line.rstrip("\r\n").endswith("\\"):
            continuation = logical_line.rstrip("\r\n")
            trailing_slashes = len(continuation) - len(continuation.rstrip("\\"))
            if trailing_slashes % 2 == 0 or line_index + consumed_lines >= len(lines):
                break
            logical_line = continuation[:-1] + lines[line_index + consumed_lines]
            consumed_lines += 1
        kept.extend(lines[line_index : line_index + consumed_lines])
        line = logical_line
        comment = False
        index = 0
        while index < len(line):
            character = line[index]
            if character == "\n":
                comment = False
                index += 1
                continue
            if comment:
                index += 1
                continue
            if quote:
                if character == quote and (index == 0 or line[index - 1] != "\\"):
                    quote = None
                index += 1
                continue
            if character in "'\"":
                quote = character
                index += 1
                continue
            # A heredoc marker in a shell comment is just comment text. A
            # comment may begin directly after a shell control operator.
            previous = line[:index].rstrip()
            if character == "#" and (
                not previous
                or line[index - 1].isspace()
                or previous.endswith((";", "&&", "||", "|", "&"))
            ):
                comment = True
                break
            if line.startswith("$((", index):
                arithmetic_depth += 1
                index += 3
                continue
            if arithmetic_depth and line.startswith("))", index):
                arithmetic_depth -= 1
                index += 2
                continue
            if not arithmetic_depth and line.startswith("((", index):
                paren_arithmetic_depth += 1
                index += 2
                continue
            if paren_arithmetic_depth and line.startswith("))", index):
                paren_arithmetic_depth -= 1
                index += 2
                continue
            # A here-string (<<<) consumes one word, not following lines.
            if line[index : index + 2] != "<<":
                index += 1
                continue
            if arithmetic_depth or paren_arithmetic_depth:
                index += 2
                continue
            if line[index : index + 3] == "<<<":
                index += 3
                continue
            index += 2
            strip_tabs = index < len(line) and line[index] == "-"
            if strip_tabs:
                index += 1
            while index < len(line) and line[index] in " \t":
                index += 1
            if index >= len(line):
                break
            # Read the complete shell word and apply quote removal. Delimiter
            # words may mix quoted and unquoted segments, and an escaped
            # whitespace remains part of the word (e.g. <<E\ OF).
            delimiter_chars: list[str] = []
            delimiter_quote: str | None = None
            delimiter_was_quoted = False
            escaped_delimiter = False
            while index < len(line):
                delimiter_character = line[index]
                if escaped_delimiter:
                    # In an unquoted word, a backslash quotes any following
                    # character. Within double quotes it only quotes the
                    # shell's escapable characters; otherwise it is literal.
                    if delimiter_quote == '"' and delimiter_character not in '$`"\\\n':
                        delimiter_chars.append("\\")
                    delimiter_chars.append(delimiter_character)
                    escaped_delimiter = False
                elif delimiter_character == "\\" and delimiter_quote != "'":
                    escaped_delimiter = True
                    delimiter_was_quoted = True
                elif delimiter_quote:
                    if delimiter_character == delimiter_quote:
                        delimiter_quote = None
                    else:
                        delimiter_chars.append(delimiter_character)
                elif delimiter_character in "'\"":
                    delimiter_quote = delimiter_character
                    delimiter_was_quoted = True
                elif delimiter_character in " \t;&|\r\n":
                    break
                else:
                    delimiter_chars.append(delimiter_character)
                index += 1
            if escaped_delimiter:
                delimiter_chars.append("\\")
            if delimiter_quote:
                break
            delimiter = "".join(delimiter_chars)
            if delimiter:
                delimiters.append((delimiter, strip_tabs, delimiter_was_quoted))
            # A command can contain multiple heredoc redirects. Find all of
            # them so every body is suppressed before tokenization.
            index += 1
        line_index += consumed_lines
    return "".join(kept)


def _shell_newline_separated(run: str) -> str:
    """Make unescaped shell newlines visible as command separators."""
    result: list[str] = []
    quote: str | None = None
    escaped = False
    comment = False
    for character in run:
        if character == "\n":
            if escaped:
                if result and result[-1] == "\\":
                    result.pop()
                result.append(" ")
                escaped = False
                continue
            # Keep a physical newline after a comment so shlex can terminate
            # the comment.  Newlines after a shell continuation operator are
            # whitespace, not an additional command separator.
            if quote is None and not comment:
                stripped = "".join(result).rstrip()
                if stripped.endswith(("&&", "||", "|")):
                    result.append(character)
                else:
                    result.append(";")
            elif comment:
                result.append(";")
            else:
                result.append(character)
            comment = False
            escaped = False
            continue
        if quote is None and not comment and character == "#":
            previous = "".join(result).rstrip()
            if (
                not previous
                or (result and result[-1].isspace())
                or previous.endswith((";", "&&", "||", "|", "&"))
            ):
                comment = True
        if comment:
            # Do not let shlex's comment mode consume following physical
            # lines; retain only whitespace until the newline above.
            result.append(" ")
            escaped = False
            continue
        if character == "\n" and escaped:
            if result and result[-1] == "\\":
                result.pop()
            result.append(" ")
            escaped = False
            continue
        if character == "\\" and not escaped:
            result.append(character)
            escaped = True
            continue
        if character in "'\"" and not escaped:
            quote = None if quote == character else character if quote is None else quote
        result.append(character)
        escaped = False
    return "".join(result)


def _shell_extractor_paths(run: str) -> set[str]:
    """Find extractor paths used as shell commands, not embedded text."""
    try:
        source = _shell_newline_separated(_shell_heredoc_free(run))
        lexer = shlex.shlex(source, posix=True, punctuation_chars=";&|")
        lexer.commenters = "#"
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return set()

    extractors: set[str] = set()
    command_start = True
    working_directory = "."
    pending_cd = False
    for index, token in enumerate(tokens):
        if token in {";", "&&", "||", "|", "&"}:
            command_start = True
            pending_cd = False
            continue
        if command_start and token == "cd":
            pending_cd = True
        elif pending_cd:
            working_directory = posixpath.normpath(
                posixpath.join(working_directory, token)
            )
            pending_cd = False
        elif command_start and token in {"python", "python3"}:
            extractor_index = index + 1
            while extractor_index < len(tokens) and tokens[extractor_index] in {"\\", "\n"}:
                extractor_index += 1
            if extractor_index >= len(tokens):
                command_start = False
                continue
            extractor = tokens[extractor_index]
            if extractor.endswith("safe_extract.py"):
                extractors.add(posixpath.normpath(posixpath.join(working_directory, extractor)))
        command_start = False
    return extractors


def has_custom_allure_outputs(repository_path: Path) -> bool:
    """Recognize complete custom Allure output in executable workflow steps."""
    for workflow_path in workflow_files(repository_path):
        try:
            document = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if not isinstance(document, dict) or not isinstance(document.get("jobs"), dict):
            continue
        config_paths: set[str] = set()
        extractor_paths: set[str] = set()
        for job in document["jobs"].values():
            if not isinstance(job, dict):
                continue
            executable_units = [job]
            steps = job.get("steps")
            if isinstance(steps, list):
                executable_units.extend(step for step in steps if isinstance(step, dict))
            for unit in executable_units:
                options = unit.get("with")
                config_path = options.get("config-file") if isinstance(options, dict) else None
                if isinstance(config_path, str):
                    config_paths.add(config_path)
                run = unit.get("run")
                if isinstance(run, str):
                    extractor_paths.update(_shell_extractor_paths(run))
        if any(
            is_regular_file(repository_path / config, repository_root=repository_path)
            and is_regular_file(repository_path / extractor, repository_root=repository_path)
            for config in config_paths
            for extractor in extractor_paths
        ):
            return True
    return False


def workflow_files(repository_path: Path) -> list[Path]:
    """Return regular workflow files without following repository symlinks."""
    workflows = repository_path / ".github" / "workflows"
    if not workflows.is_dir() or workflows.is_symlink():
        return []
    return [
        path
        for path in sorted(workflows.glob("*.yml")) + sorted(workflows.glob("*.yaml"))
        if is_regular_file(path, repository_root=repository_path)
    ]


def inferred_components(repository_path: Path) -> tuple[str, ...]:
    """Infer language/path pairs within each workflow job or step."""
    language_patterns = {
        "python": r"python-ci|setup-python|python\s+-m\s+pytest|pytest",
        "node": r"node-ci|setup-node|npm\s+(?:ci|test|run)|yarn\s+",
        "java": r"java-ci|(?:actions/)?setup-java(?:-[A-Za-z0-9_-]+)?@|gradlew|gradle\s+(?:build|test)|(?:^|\s)\./mvnw(?:\s|$)|maven",
    }
    found: set[str] = set()
    for workflow in workflow_files(repository_path):
        try:
            document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if not isinstance(document, dict):
            continue
        jobs = document.get("jobs", {})
        if not isinstance(jobs, dict):
            continue
        workflow_defaults = document.get("defaults", {})
        workflow_path = "."
        if isinstance(workflow_defaults, dict):
            workflow_run_defaults = workflow_defaults.get("run", {})
            if isinstance(workflow_run_defaults, dict):
                workflow_path = workflow_run_defaults.get("working-directory", workflow_path)
        if not isinstance(workflow_path, str):
            workflow_path = "."
        for job in jobs.values():
            if not isinstance(job, dict):
                continue
            job_path = job.get("working-directory", workflow_path)
            defaults = job.get("defaults", {})
            if isinstance(defaults, dict):
                run_defaults = defaults.get("run", {})
                if isinstance(run_defaults, dict):
                    job_path = run_defaults.get("working-directory", job_path)
            if not isinstance(job_path, str):
                job_path = "."
            job_with = job.get("with", {})
            if isinstance(job_with, dict) and isinstance(job_with.get("working-directory"), str):
                job_path = job_with["working-directory"]
            units: list[tuple[Any, str]] = []
            if isinstance(job.get("uses"), str):
                units.append((job["uses"], job_path))
            if isinstance(job.get("run"), str):
                units.append((job["run"], job_path))
            steps = job.get("steps", [])
            if isinstance(steps, list):
                for step in steps:
                    if isinstance(step, dict):
                        step_path = step.get("working-directory", job_path)
                        step_with = step.get("with", {})
                        if isinstance(step_with, dict):
                            step_path = step_with.get("working-directory", step_path)
                        if isinstance(step.get("uses"), str):
                            units.append((step["uses"], step_path))
                        if isinstance(step.get("run"), str):
                            units.append((step["run"], step_path))
            for unit, path in units:
                text = unit
                if not isinstance(path, str):
                    path = "."
                for component, pattern in language_patterns.items():
                    if re.search(pattern, text, re.IGNORECASE):
                        found.add(f"{component}:{path}")
    return tuple(sorted(found))


def has_custom_release_please_outputs(repository_path: Path) -> bool:
    """Recognize an executable custom Release Please workflow.

    Parse the workflow document before inspecting jobs and steps.  Searching
    raw YAML text would treat comments and quoted values as executable action
    evidence, hiding a real configuration gap.
    """
    standard = repository_path / FEATURE_PATHS["release_please"]
    action_pattern = re.compile(r"(?:^|/)release[-_]please-action@", re.IGNORECASE)
    for workflow in workflow_files(repository_path):
        if workflow == standard:
            continue
        try:
            document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            continue
        if not isinstance(document, dict):
            continue
        jobs = document.get("jobs")
        if not isinstance(jobs, dict):
            continue
        for job in jobs.values():
            if not isinstance(job, dict):
                continue
            executable_uses = [job.get("uses")]
            steps = job.get("steps")
            if isinstance(steps, list):
                executable_uses.extend(
                    step.get("uses") for step in steps if isinstance(step, dict)
                )
            if any(isinstance(uses, str) and action_pattern.search(uses) for uses in executable_uses):
                return True
    return False


def feature_state(answers: dict[str, Any], key: str, repository_path: Path) -> str:
    configured = answers.get(key)
    if not isinstance(configured, bool):
        return "unknown"
    expected_paths = ALLURE_EXPECTED_PATHS if key == "allure_report" else (FEATURE_PATHS[key],)
    materialized = tuple(
        is_regular_file(repository_path / path, repository_root=repository_path)
        for path in expected_paths
    )
    if configured:
        if all(materialized):
            return "enabled"
        if key == "allure_report" and has_custom_allure_outputs(repository_path):
            return "custom"
        if key == "release_please" and has_custom_release_please_outputs(repository_path):
            return "custom"
        return "missing"
    if key == "release_please" and has_custom_release_please_outputs(repository_path):
        return "custom"
    return "custom" if any(materialized) else "disabled"


def inventory_from_answers(raw_answers: str, repository_path: Path) -> TemplateInventory:
    answers = parse_answers(raw_answers)
    commit = answers.get("_commit")
    if not isinstance(commit, str) or not commit.strip():
        commit = "unknown"

    raw_components = answers.get("components")
    components: list[str] = []
    components_valid = True
    if isinstance(raw_components, list):
        for component in raw_components:
            if not isinstance(component, dict):
                components_valid = False
                break
            component_type = component.get("type")
            component_path = component.get("path")
            if not isinstance(component_type, str) or not isinstance(component_path, str):
                components_valid = False
                break
            components.append(f"{component_type}:{component_path}")
    elif raw_components is not None:
        components_valid = False
    if not components_valid:
        components = ["unknown"]
    else:
        components = list(inferred_components(repository_path)) if not components else components
        if not components:
            components = ["none"]

    missing_baseline = tuple(
        path
        for path in BASELINE_PATHS
        if not is_regular_file(repository_path / path, repository_root=repository_path)
    )
    present_baseline = len(BASELINE_PATHS) - len(missing_baseline)
    docker = answers.get("docker")
    docker_state = "enabled" if docker is True else "disabled" if docker is False else "unknown"
    return TemplateInventory(
        commit=commit.strip(),
        target_commit=None,
        components=tuple(components),
        baseline=f"{present_baseline}/{len(BASELINE_PATHS)}",
        missing_baseline=missing_baseline,
        docker=docker_state,
        codeql=feature_state(answers, "codeql", repository_path),
        allure_report=feature_state(answers, "allure_report", repository_path),
        release_please=feature_state(answers, "release_please", repository_path),
        renovate=feature_state(answers, "renovate", repository_path),
    )


def legacy_handed_off(raw_answers: str) -> list[str]:
    handed = parse_answers(raw_answers).get("handed_off")
    return [area for area in handed if isinstance(area, str)] if isinstance(handed, list) else []


def modules_inventory(commit: str, modules: Sequence[str], repository_path: Path) -> TemplateInventory:
    missing_baseline = tuple(
        path
        for path in BASELINE_PATHS
        if not is_regular_file(repository_path / path, repository_root=repository_path)
    )
    components = inferred_components(repository_path) or ("none",)
    return TemplateInventory(
        commit=commit,
        target_commit=None,
        components=tuple(components),
        baseline=f"{len(BASELINE_PATHS) - len(missing_baseline)}/{len(BASELINE_PATHS)}",
        missing_baseline=missing_baseline,
        docker="unknown",
        codeql="unknown",
        allure_report="unknown",
        release_please="unknown",
        renovate="unknown",
        modules=tuple(modules),
        state="modules",
    )


def inventory_has_mismatch(inventory: TemplateInventory | None) -> bool:
    if inventory is None or inventory.state == "modules":
        return False
    return (
        inventory.baseline != f"{len(BASELINE_PATHS)}/{len(BASELINE_PATHS)}"
        or inventory.codeql == "missing"
        or inventory.allure_report == "missing"
        or inventory.release_please == "missing"
        or inventory.renovate == "missing"
    )


def configuration_gap_count(results: Sequence[Result]) -> int:
    """Count inventory gaps independently from synchronization drift."""
    return sum(inventory_has_mismatch(result.inventory) for result in results)


def console_lines(result: Result) -> list[str]:
    suffix = f": {sanitize_text(result.detail)}" if result.detail else ""
    lines = [f"[{result.status}] {sanitize_text(result.repository)}{suffix}"]
    if result.inventory:
        inventory = result.inventory
        template_version = inventory.commit
        if inventory.target_commit and inventory.target_commit != inventory.commit:
            template_version += f"->{inventory.target_commit}"
        lines.append(
            "  "
            f"template={sanitize_text(template_version)} "
            f"components={sanitize_text(','.join(inventory.components))} "
            f"baseline={inventory.baseline} "
            f"docker={FEATURE_LABELS[inventory.docker]} "
            f"codeql={FEATURE_LABELS[inventory.codeql]} "
            f"allure={FEATURE_LABELS[inventory.allure_report]} "
            f"release-please={FEATURE_LABELS[inventory.release_please]} "
            f"renovate={FEATURE_LABELS[inventory.renovate]} "
            f"state={sanitize_text(inventory.state)} "
            f"modules={sanitize_text(','.join(inventory.modules))}"
        )
    return lines


def feature_summary(results: Sequence[Result]) -> tuple[str, str]:
    inventories = [result.inventory for result in results if result.inventory]
    denominator = len(inventories)
    enabled = {
        "docker": sum(inventory.docker == "enabled" for inventory in inventories),
        "codeql": sum(inventory.codeql == "enabled" for inventory in inventories),
        "allure": sum(inventory.allure_report == "enabled" for inventory in inventories),
        "release-please": sum(inventory.release_please == "enabled" for inventory in inventories),
        "renovate": sum(inventory.renovate == "enabled" for inventory in inventories),
    }
    component_counts: dict[str, int] = {}
    for inventory in inventories:
        seen = {
            sanitize_text(component.split(":", 1)[0])
            for component in inventory.components
            if component not in {"none", "unknown"}
        }
        for component_type in seen:
            component_counts[component_type] = component_counts.get(component_type, 0) + 1
    features = "features: " + ", ".join(
        f"{name}={count}/{denominator}" for name, count in enabled.items()
    )
    components = "components: " + (
        ", ".join(f"{name}={count}" for name, count in sorted(component_counts.items()))
        if component_counts
        else "none"
    )
    return features, components


def markdown_escape(value: str) -> str:
    return sanitize_text(value).replace("\\", "\\\\").replace("|", "\\|")


REPOSITORY_SLUG_PATTERN = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
PULL_REQUEST_URL_PATTERN = re.compile(r"https://github\.com/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/pull/([0-9]+)")
RUN_URL_PATTERN = re.compile(r"https://[A-Za-z0-9.-]+/([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)/actions/runs/[0-9]+")
GIT_REF_PATTERN = re.compile(r"[A-Za-z0-9._/-]+")
MARKDOWN_ENTITIES = {ord(character): f"&#{ord(character)};" for character in "\\`*_[]()#!|~<>&\"'@:$"}
UNMANAGED_STATUSES = {"excluded", "not-managed", "not-applicable", "foreign-template"}
QUIET_STATUSES = UNMANAGED_STATUSES | {"up-to-date", "pull-request"}
STATUS_GLYPHS = {"up-to-date": "✅", "pull-request": "🔀", "not-managed": "—", "not-applicable": "—", "foreign-template": "↗️"}
CONFLICT_PREFIX = "Copier produced conflict files:"


@dataclass(frozen=True)
class AttentionBucket:
    key: str
    singular: str
    plural: str
    glyph: str

    def count(self, number: int) -> str:
        return f"{number} {self.singular if number == 1 else self.plural}"


ATTENTION_BUCKETS = (
    AttentionBucket("conflict", "conflict", "conflicts", "❌"),
    AttentionBucket("failed", "failed", "failed", "❌"),
    AttentionBucket("pr-ci", "PR with failed CI", "PRs with failed CI", "❌"),
    AttentionBucket("auto-merge", "auto-merge failed", "auto-merge failed", "❌"),
    AttentionBucket("main-ci", "failing CI on main", "failing CI on main", "❌"),
    AttentionBucket("gap", "configuration gap", "configuration gaps", "⚠️"),
    AttentionBucket("drift", "drift", "drift", "🟡"),
    AttentionBucket("unknown", "unrecognized status", "unrecognized statuses", "❔"),
)
BUCKETS = {bucket.key: bucket for bucket in ATTENTION_BUCKETS}
BUCKET_ORDER = {bucket.key: index for index, bucket in enumerate(ATTENTION_BUCKETS)}
LEGEND = (
    ("✅", "ok"), ("❌", "failed"), ("⚠️", "gap"), ("🔀", "PR open"), ("⏳", "CI pending"),
    ("🟡", "drift (dry run)"), ("❔", "unrecognized status"), ("—", "not managed or not applicable"), ("↗️", "foreign template"),
)


@dataclass(frozen=True)
class Fact:
    bucket: str
    happened: str
    step: str


@dataclass(frozen=True)
class ReportRow:
    result: Result
    pull_request: dict[str, Any] | None
    facts: tuple[Fact, ...]
    in_flight: bool

    @property
    def bucket(self) -> AttentionBucket | None:
        return BUCKETS[self.facts[0].bucket] if self.facts else None

    @property
    def glyph(self) -> str:
        if self.bucket:
            return self.bucket.glyph
        if self.in_flight:
            return "⏳" if self.pull_request and pull_request_ci(self.pull_request) == "pending" else "🔀"
        return STATUS_GLYPHS.get(self.result.status, "❔")


def markdown_text(value: str) -> str:
    """Neutralize untrusted text for Markdown prose and table cells without visible backslashes."""
    return sanitize_text(value).translate(MARKDOWN_ENTITIES)


def preformatted(value: str) -> str:
    return "<pre>" + "\n".join(html.escape(sanitize_text(line)) for line in value.splitlines()) + "</pre>"


def repository_link(repository: str) -> str:
    if REPOSITORY_SLUG_PATTERN.fullmatch(repository):
        return f"[{repository.split('/', 1)[1]}](https://github.com/{repository})"
    return markdown_text(repository)


def automation_pull_request(result: Result) -> dict[str, Any] | None:
    pull_request = (result.health or {}).get("pull_request")
    if isinstance(pull_request, dict) and PULL_REQUEST_URL_PATTERN.fullmatch(str(pull_request.get("url", ""))):
        return pull_request
    if result.status != "pull-request":
        return None
    match = PULL_REQUEST_URL_PATTERN.fullmatch(result.detail)
    return {"url": match[0] if match else None, "state": "merged" if (result.auto_merge or {}).get("state") == "merged" else "open", "ci": {"status": "unknown"}, "repository": result.repository}


def pull_request_link(pull_request: dict[str, Any], *, with_repository: bool = False) -> str:
    match = PULL_REQUEST_URL_PATTERN.fullmatch(str(pull_request.get("url") or ""))
    if not match:
        name = str(pull_request.get("repository") or "").rsplit("/", 1)[-1]
        return (f"{markdown_text(name)} PR" if with_repository else "PR") + " (link unavailable)"
    label = f"{match[1].split('/', 1)[1]}#{match[2]}" if with_repository else f"#{match[2]}"
    return f"[{label}]({match[0]})"


def pull_request_ci(pull_request: dict[str, Any]) -> str:
    ci = pull_request.get("ci")
    return str(ci.get("status", "unknown")) if isinstance(ci, dict) else "unknown"


def age_text(days: Any) -> str:
    return "today" if days == 0 else f"{days}d" if isinstance(days, int) else "?"


def missing_outputs(inventory: TemplateInventory) -> list[str]:
    missing = list(inventory.missing_baseline)
    missing.extend(FEATURE_PATHS[key] for key in ("codeql", "release_please", "renovate") if getattr(inventory, key) == "missing")
    if inventory.allure_report == "missing":
        missing.extend(ALLURE_EXPECTED_PATHS)
    return missing


def attention_facts(result: Result, pull_request: dict[str, Any] | None) -> tuple[Fact, ...]:
    facts: list[Fact] = []
    status = result.status
    health = result.health or {}
    if status == "failed" and result.detail.startswith(CONFLICT_PREFIX):
        files = ", ".join(name.strip().removesuffix(".rej") for name in result.detail[len(CONFLICT_PREFIX):].split(","))
        facts.append(Fact("conflict", f"Copier conflict in {markdown_text(files)}; nothing pushed.", "**Resolve** the conflicting files on the default branch, then re-run for this repository."))
    elif status == "failed":
        facts.append(Fact("failed", "Update failed; raw error below.", "**Re-run** for this repository; investigate if it fails again."))
    elif status in {"unsafe-answers-file", "unknown-module", "foreign-module-template"}:
        facts.append(Fact("gap", f"{markdown_text(status.replace('-', ' ').capitalize())}: {markdown_text(result.detail)}.", "**Fix** the module answers file in the repository."))
    elif status in {"needs-update-first", "add-rejected"}:
        step = "**Update** the repository to the target template release first, then re-run." if status == "needs-update-first" else "**Fix** the cause named by the code (see docs/errors), then re-run."
        facts.append(Fact("gap", f"{markdown_text(status.replace('-', ' ').capitalize())}: {markdown_text(result.detail)}.", step))
    elif status == "would-add":
        facts.append(Fact("drift", f"Module {markdown_text(result.detail)} would be added.", "**Open** the add-module PR: run write mode for this repository."))
    elif status == "would-update":
        inventory = result.inventory
        version = f" {markdown_text(inventory.commit)} → {markdown_text(inventory.target_commit)}" if inventory and inventory.target_commit and inventory.target_commit != inventory.commit else ""
        paths = [path.strip() for path in result.detail.split(",") if path.strip()]
        names = [f"`{path}`" if GIT_REF_PATTERN.fullmatch(path) else markdown_text(path) for path in paths[:3]]
        if len(paths) > 3:
            names.append(f"{len(paths) - 3} more")
        files = f": would change {', '.join(names[:-1])} and {names[-1]}" if len(names) > 1 else f": would change {names[0]}" if names else ""
        facts.append(Fact("drift", f"Template drift{version}{files}.", "**Open** the update PR: run write mode for this repository (command below)."))
    elif status not in QUIET_STATUSES:
        detail = f": {markdown_text(result.detail)}" if result.detail else ""
        facts.append(Fact("unknown", f"Unrecognized status {markdown_text(status)}{detail}.", "**Review** the raw status and detail below."))
    if inventory_has_mismatch(result.inventory):
        assert result.inventory
        files = ", ".join(f"`{path}`" for path in missing_outputs(result.inventory)) or f"baseline {markdown_text(result.inventory.baseline)}"
        facts.append(Fact("gap", f"Missing template outputs: {files}.", "**Fix** the missing outputs or the Copier answers."))
    exception = health.get("exception")
    if isinstance(exception, dict) and exception.get("status") == "expired":
        facts.append(Fact("gap", f"Health exception expired on {markdown_text(str(exception.get('expires_on')))}: {markdown_text(str(exception.get('reason')))}.", "**Review** the exception: renew or remove it."))
    if pull_request and pull_request.get("state") == "open" and pull_request_ci(pull_request) == "failed":
        facts.append(Fact("pr-ci", f"PR {pull_request_link(pull_request)} open {age_text(pull_request.get('age_days'))}, CI ❌.", f"**Fix** the [failing checks]({pull_request['url']}/checks); the next run refreshes the PR."))
    auto_merge = result.auto_merge or {}
    if pull_request and auto_merge.get("state") == "skipped" and str(auto_merge.get("reason", "")).startswith("failed checks:"):
        facts.append(Fact("pr-ci", f"PR {pull_request_link(pull_request)} open {age_text(pull_request.get('age_days'))}, CI ❌ ({markdown_text(str(auto_merge['reason']))}).", f"**Fix** the [failing checks]({pull_request['url']}/checks); the next run refreshes the PR."))
    if (result.auto_merge or {}).get("state") == "failed":
        target = f" on PR {pull_request_link(pull_request)}" if pull_request else ""
        facts.append(Fact("auto-merge", f"Auto-merge could not be enabled{target}: {markdown_text(str(result.auto_merge.get('reason')))}.", "**Fix** the cause, then re-run with `--auto-merge` or merge the PR manually."))
    failing = [str(check.get("context")) for check in (health.get("default_ci") or {}).get("checks", []) if isinstance(check, dict) and check.get("status") == "failed"]
    if failing:
        facts.append(Fact("main-ci", "CI on `main` failing: " + ", ".join(markdown_text(context) for context in failing) + ".", "**Fix** the failing checks on the default branch."))
    return tuple(sorted(facts, key=lambda fact: BUCKET_ORDER[fact.bucket]))


def ci_on_default_branch(health: dict[str, Any] | None) -> str:
    if not health:
        return "?"
    policy = health.get("required_checks") or {}
    required = [str(check.get("context")) for check in policy.get("checks", []) if isinstance(check, dict)]
    if not required:
        return "no required checks" if policy.get("status") == "unprotected" else "?"
    observed = {str(check.get("context")): check.get("status") for check in (health.get("default_ci") or {}).get("checks", []) if isinstance(check, dict)}
    passed = sum(observed.get(context) == "passed" for context in required)
    parts = [f"{passed}/{len(required)} ✅"]
    failed = [context for context in required if observed.get(context) == "failed"]
    if failed:
        parts.append("❌ " + ", ".join(markdown_text(context) for context in failed))
    unknown = [context for context in required if observed.get(context) not in {"passed", "failed"}]
    if unknown:
        parts.append(", ".join(f"{markdown_text(context)} ?" for context in unknown))
    return " · ".join(parts)


def last_pull_request(pull_request: dict[str, Any] | None, health: dict[str, Any] | None) -> str:
    if not pull_request:
        return "none" if (health or {}).get("pull_request_observation", {}).get("status") == "observed" else "?"
    link = pull_request_link(pull_request)
    if pull_request.get("state") != "open":
        return f"{link} {markdown_text(str(pull_request.get('state')))}"
    ci = {"passed": "✅", "failed": "❌", "pending": "⏳"}.get(pull_request_ci(pull_request), "?")
    evidence = pull_request.get("ci")
    gate = " · pilot gate" if isinstance(evidence, dict) and evidence.get("source") == "pilot gate" else ""
    return f"{link} open {age_text(pull_request.get('age_days'))} · CI {ci}{gate}"


def rerun_command(result: Result, run: dict[str, Any]) -> str | None:
    if not (result.status == "failed" or result.status == "would-update" and run.get("mode") == "dry-run") or not REPOSITORY_SLUG_PATTERN.fullmatch(result.repository):
        return None
    match = RUN_URL_PATTERN.fullmatch(str(run.get("run_url") or ""))
    target = f" -R {match[1]}" if match else ""
    reference = run.get("requested_template_ref")
    template_ref = f" -f template-ref={reference}" if isinstance(reference, str) and GIT_REF_PATTERN.fullmatch(reference) else ""
    dry_run = " -f dry-run=true" if result.status == "failed" and run.get("mode") == "dry-run" else ""
    return f"gh workflow run copier-fleet-auto-update.yml{target} -f repository={result.repository}{template_ref}{dry_run}"


def raw_evidence(result: Result) -> list[str]:
    evidence = [result.detail] if result.detail and result.status not in {"pull-request", "would-update"} else []
    return evidence


def generated_text(value: Any) -> str | None:
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def report_rows(results: Sequence[Result], rollout: dict[str, Any] | None = None) -> list[ReportRow]:
    gates = {gate.get("repository"): gate for gate in (rollout or {}).get("pilots") or [] if isinstance(gate, dict) and gate.get("result") == "success"}
    rows = []
    for result in results:
        if result.status == "excluded":
            continue
        pull_request = automation_pull_request(result)
        gate = gates.get(result.repository)
        match = PULL_REQUEST_URL_PATTERN.fullmatch(str((pull_request or {}).get("url") or ""))
        if (gate and pull_request and match and match[1] == result.repository and int(match[2]) == gate.get("pull_request")
                and pull_request.get("state") == "open" and pull_request_ci(pull_request) == "unknown"
                and pull_request.get("head_sha") and pull_request.get("head_sha") == gate.get("revision")):
            pull_request = {**pull_request, "ci": {"status": "passed", "source": "pilot gate"}}
        in_flight = bool(pull_request and pull_request.get("state") == "open" and pull_request_ci(pull_request) != "failed")
        rows.append(ReportRow(result, pull_request, attention_facts(result, pull_request), in_flight))
    return sorted(rows, key=lambda row: (0, BUCKET_ORDER[row.facts[0].bucket]) if row.facts else (1, 0) if row.in_flight else (2, 0))


def _verdict(rows: Sequence[ReportRow], stopped: Any) -> str:
    attention = [row.bucket for row in rows if row.bucket]
    if stopped or any(bucket.key != "drift" for bucket in attention):
        counts = [bucket.count(attention.count(bucket)) for bucket in ATTENTION_BUCKETS if bucket in attention]
        return "❌ action required — " + " · ".join((["rollout stopped"] if stopped else []) + counts)
    if attention:
        return f"🟡 drift in {len(attention)}"
    open_count = sum(row.in_flight for row in rows)
    if open_count:
        return f"🔀 {open_count} PR{'s' if open_count != 1 else ''} open"
    return "✅ nothing to do"


def _summary(rows: Sequence[ReportRow], stopped: Any) -> str:
    managed = [row for row in rows if row.result.status not in UNMANAGED_STATUSES]
    calm = [row for row in managed if not row.facts and not row.in_flight]
    inventories = [row.result.inventory for row in managed]
    versions = {inventory.commit for inventory in inventories if inventory}
    if not managed:
        sentence = "No managed repositories were processed."
    elif len(calm) == len(managed) and len(versions) == 1 and all(inventory and inventory.target_commit in {None, inventory.commit} for inventory in inventories):
        sentence = f"All {len(managed)} managed repositories are on **{markdown_text(next(iter(versions)))}**."
    elif len(calm) == len(managed):
        sentence = f"All {len(managed)} managed repositories need nothing."
    elif not any(row.facts for row in managed):
        sentence = (f"{len(calm)} of {len(managed)} managed repositories need nothing; the rest have PRs in flight." if calm
                    else f"All {len(managed)} managed repositories have update PRs open.")
    elif calm:
        sentence = f"{len(calm)} of {len(managed)} managed repositories need nothing."
    else:
        attention = sum(bool(row.facts) for row in managed)
        sentence = (f"All {len(managed)} managed repositories need attention." if attention == len(managed)
                    else f"{attention} of {len(managed)} managed repositories need attention; the rest have PRs in flight.")
    if stopped:
        sentence += f" **Rollout stopped:** {markdown_text(str(stopped))}."
    return sentence


def _context(rows: Sequence[ReportRow], excluded: int, run: dict[str, Any], rollout: dict[str, Any] | None) -> str:
    context = []
    mode = {"write": "Write mode", "dry-run": "Dry run"}.get(str(run.get("mode")))
    if mode:
        context.append(mode + (f" ({markdown_text(str(run['trigger']))})" if run.get("trigger") else ""))
    generated = generated_text(run.get("generated_at")) if run.get("generated_at") else None
    if generated:
        context.append(generated)
    if RUN_URL_PATTERN.fullmatch(str(run.get("run_url") or "")):
        context.append(f"[job log]({run['run_url']})")
    if run.get("template_ref"):
        context.append(f"target {markdown_text(str(run['template_ref']))}")
    if rollout and rollout.get("pilots"):
        context.append("pilots verified: " + ", ".join(repository_link(str(gate.get("repository"))) for gate in rollout["pilots"]))
    if rollout and not rollout.get("fanout_started"):
        context.append("fanout not started")
    if excluded:
        context.append(f"{excluded} excluded")
    in_flight = [row.pull_request for row in rows if row.in_flight and row.pull_request]
    for ci, label in (("pending", "waiting on CI"), ("passed", "ready to merge"), (None, "open, CI unknown")):
        pulls = [pull for pull in in_flight if (pull_request_ci(pull) == ci if ci else pull_request_ci(pull) not in {"pending", "passed"})]
        if pulls:
            links = " (" + ", ".join(pull_request_link(pull, with_repository=True) for pull in pulls) + ")" if len(pulls) <= 3 else ""
            context.append(f"{len(pulls)} {label}{links}")
    states = [(row.result.auto_merge or {}).get("state") for row in rows]
    for state, label in (("enabled", "auto-merging"), ("merged", "merged"), ("waiting", "auto-merge waiting on CI")):
        if states.count(state):
            context.append(f"{states.count(state)} {label}")
    return " · ".join(context)


def _attention_section(rows: Sequence[ReportRow], run: dict[str, Any]) -> list[str]:
    attention = [row for row in rows if row.bucket]
    if not attention:
        return []
    lines = [f"### Needs your attention — {len(attention)}", "", "| | Repository | What happened | Next step |", "| :-: | --- | --- | --- |"]
    for row in attention:
        happened = " ".join(dict.fromkeys(fact.happened for fact in row.facts))
        steps = " ".join(dict.fromkeys(fact.step for fact in row.facts))
        lines.append(f"| {row.glyph} | {repository_link(row.result.repository)} | {happened} | {steps} |")
    sections = []
    raw = False
    for number, row in enumerate(attention, 1):
        command = rerun_command(row.result, run)
        evidence = raw_evidence(row.result)
        if not command and not evidence:
            continue
        assert row.bucket
        sections.extend([f"#### {number}. {markdown_text(row.result.repository)} — {row.bucket.singular}", ""])
        if command:
            sections.extend(["```", command, "```", ""])
        if evidence:
            raw = True
            sections.extend([*(preformatted(item) for item in evidence), ""])
    if sections:
        lines.extend(["", "<details>", f"<summary>{'Commands and raw errors' if raw else 'Commands'}</summary>", "", *sections, "</details>"])
    return [*lines, ""]


def _fleet_section(rows: Sequence[ReportRow], results: Sequence[Result]) -> list[str]:
    statuses: dict[str, int] = {}
    for result in results:
        statuses[result.status] = statuses.get(result.status, 0) + 1
    status_counts = " · ".join(
        f"{count} {markdown_text({'pull-request': 'PR open' if count == 1 else 'PRs open', 'would-update': 'drift'}.get(status, status.replace('-', ' ')))}"
        for status, count in sorted(statuses.items(), key=lambda item: (item[0] == "excluded", item[0] != "up-to-date", item[0]))
    )
    lines = ["<details>", f"<summary>Fleet — {status_counts or 'no repositories'}</summary>", ""]
    if rows:
        marked = any(row.glyph != "✅" for row in rows)
        lines.append(("| | " if marked else "| ") + "Repository | Template | CI on `main` | Last automation PR |")
        lines.append(("| :-: | " if marked else "| ") + "--- | --- | --- | --- |")
        for row in rows:
            inventory = row.result.inventory
            template = "?"
            if inventory:
                template = markdown_text(inventory.commit)
                if inventory.target_commit and inventory.target_commit != inventory.commit:
                    template += f" → {markdown_text(inventory.target_commit)}"
            cells = [repository_link(row.result.repository), template, ci_on_default_branch(row.result.health), last_pull_request(row.pull_request, row.result.health)]
            lines.append("| " + (f"{row.glyph} | " if marked else "") + " | ".join(cells) + " |")
        explanation = (
            "**Template** is the version recorded in `.copier-answers.yml`; `→` names this run's target. "
            "**CI on `main`** counts required checks from active rulesets that passed at the current default-branch commit; "
            "❌ names failed checks and `?` names checks with no run for that exact revision (often scheduled workflows such as CodeQL): "
            "missing evidence, not a failure."
        )
        if not any(row.result.health for row in rows):
            explanation += " CI evidence is collected only with `--include-health`."
        elif any(((row.result.health or {}).get("required_checks") or {}).get("reason") == "classic branch protection evidence unavailable" for row in rows):
            explanation += " Classic branch protection cannot be read with this token, so policy coverage beyond rulesets is unknown (expected)."
        lines.extend(["", explanation + " Full SHAs, check-run IDs and per-check reasons are in the JSON report."])
    exceptions = [(row.result, (row.result.health or {}).get("exception")) for row in rows if isinstance((row.result.health or {}).get("exception"), dict)]
    if exceptions:
        lines.extend(["", "| Exception | Status | Reason | Expires |", "| --- | --- | --- | --- |"])
        lines.extend(
            "| " + " | ".join([repository_link(result.repository), *(markdown_text(str(exception.get(key))) for key in ("status", "reason", "expires_on"))]) + " |"
            for result, exception in exceptions
        )
    excluded = [result for result in results if result.status == "excluded"]
    if excluded:
        lines.extend(["", "| Excluded | Why |", "| --- | --- |"])
        lines.extend(f"| {markdown_text(result.repository)} | {markdown_text(result.detail) if result.detail else '`--exclude`'} |" for result in excluded)
    return [*lines, "", "</details>"]


def _legend(lines: Sequence[str]) -> str:
    cells = "\n".join(line for line in lines if line.startswith("|"))
    entries = [f"{glyph} {meaning}" for glyph, meaning in LEGEND if glyph in cells]
    if " ?" in cells:
        entries.append("`?` no evidence")
    return "Legend: " + " · ".join(entries) + "." if entries else ""


def markdown_report(
    results: Sequence[Result],
    counts: dict[str, int] | None = None,
    run: dict[str, Any] | None = None,
    rollout: dict[str, Any] | None = None,
) -> str:
    """Render one verdict-first report; `counts` is accepted for compatibility and recomputed from results."""
    run = run or {}
    stopped = (rollout or {}).get("stopped")
    rows = report_rows(results, rollout)
    lines = [f"## Copier fleet: {_verdict(rows, stopped)}", "", _summary(rows, stopped), ""]
    context = _context(rows, sum(result.status == "excluded" for result in results), run, rollout)
    if context:
        lines.extend([context, ""])
    lines.extend(_attention_section(rows, run))
    lines.extend(_fleet_section(rows, results))
    legend = _legend(lines)
    if legend:
        lines.extend(["", legend])
    return "\n".join(lines) + "\n"


def run_metadata(
    mode: str,
    template_ref: str | None,
    env: dict[str, str],
    requested_template_ref: str | None = None,
    auto_merge: bool = False,
) -> dict[str, Any]:
    server, repository, run_id = (env.get(name, "").strip() for name in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"))
    return {
        "mode": mode,
        "template_ref": template_ref,
        "requested_template_ref": requested_template_ref,
        "auto_merge": auto_merge,
        "trigger": env.get("GITHUB_EVENT_NAME") or None,
        "run_url": f"{server}/{repository}/actions/runs/{run_id}" if server and repository and run_id else None,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def results_from_report(payload: dict[str, Any]) -> list[Result]:
    results = []
    for item in payload.get("repositories", []):
        template = item.get("template")
        inventory = None
        if template:
            inventory = TemplateInventory(
                commit=template["commit"],
                target_commit=template.get("target_commit"),
                components=tuple(template["components"]),
                baseline=f"{template['baseline']['present']}/{template['baseline']['expected']}",
                missing_baseline=tuple(template["baseline"]["missing"]),
                docker=template["docker"],
                codeql=template["codeql"],
                allure_report=template["allure_report"],
                release_please=template["release_please"],
                renovate=template["renovate"],
                modules=tuple(template.get("modules", ())),
                state=template.get("state", "legacy"),
            )
        results.append(Result(item["repository"], item["status"], item.get("detail", ""), inventory, item.get("health"), item.get("auto_merge")))
    return results


def json_report(results: Sequence[Result], counts: dict[str, int], run: dict[str, Any] | None = None) -> str:
    repositories = []
    for result in results:
        item: dict[str, Any] = {
            "repository": result.repository,
            "status": result.status,
            "detail": result.detail,
        }
        if result.inventory:
            item["template"] = {
                "commit": result.inventory.commit,
                "target_commit": result.inventory.target_commit,
                "components": list(result.inventory.components),
                "baseline": {
                    "present": int(result.inventory.baseline.split("/", 1)[0]),
                    "expected": len(BASELINE_PATHS),
                    "missing": list(result.inventory.missing_baseline),
                },
                "docker": result.inventory.docker,
                "codeql": result.inventory.codeql,
                "allure_report": result.inventory.allure_report,
                "release_please": result.inventory.release_please,
                "renovate": result.inventory.renovate,
                "modules": list(result.inventory.modules),
                "state": result.inventory.state,
            }
        if result.health is not None:
            item["health"] = result.health
        if result.auto_merge is not None:
            item["auto_merge"] = result.auto_merge
        repositories.append(item)
    gaps = configuration_gap_count(results)
    payload = {
        "schema_version": 6,
        "summary": counts,
        "configuration_gaps": gaps,
        "configuration_mismatches": gaps,
        "repositories": repositories,
    }
    if run is not None:
        payload["run"] = run
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def canonical_template_source(source: str) -> str:
    """Return a Copier and Renovate compatible HTTPS Git URL."""
    normalized = normalize_template_source(source)
    if normalized is None:
        raise FleetUpdateError(f"unsupported template source: {source}")
    return f"https://github.com/{normalized}.git"


def canonicalize_answers_source(repository_path: Path, expected_template: str) -> bool:
    """Replace Copier's GitHub shorthand without reserializing the answers file."""
    answers_path = repository_path / ANSWERS_FILE
    if answers_path.is_symlink():
        raise FleetUpdateError(f"{ANSWERS_FILE} must not be a symlink")
    content = answers_path.read_text(encoding="utf-8")
    current = parse_template_source(content)
    if normalize_template_source(current) != normalize_template_source(expected_template):
        raise FleetUpdateError(f"{ANSWERS_FILE} is managed by a different template")
    canonical = canonical_template_source(expected_template)
    if current == canonical:
        return False
    updated, substitutions = re.subn(
        r"^_src_path:.*$",
        f"_src_path: {canonical}",
        content,
        flags=re.MULTILINE,
    )
    if substitutions != 1:
        raise FleetUpdateError(f"{ANSWERS_FILE} must contain exactly one _src_path entry")
    answers_path.write_text(updated, encoding="utf-8")
    return True


def clone_repository(repository: Repository, destination: Path, *, env: dict[str, str]) -> None:
    run(
        [
            "gh",
            "repo",
            "clone",
            repository.name_with_owner,
            str(destination),
            "--",
            "--depth=1",
            f"--branch={repository.default_branch}",
            "--single-branch",
        ],
        env=env,
    )


def changed_paths(repository_path: Path) -> list[str]:
    output = run(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=repository_path,
    ).stdout
    paths: list[str] = []
    entries = output.split("\0")
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if not entry:
            continue
        status = entry[:2]
        path = entry[3:]
        if status[0] in {"R", "C"}:
            if index >= len(entries) or not entries[index]:
                raise FleetUpdateError("git returned an incomplete rename/copy status entry")
            # In porcelain v1 -z output the destination is the first path and
            # the source follows as the extra NUL-delimited field.
            index += 1
        paths.append(path)
    return sorted(set(paths))


def restore_answers_format_if_semantically_equal(
    answers_path: Path,
    original_text: str,
) -> None:
    if answers_path.is_symlink():
        raise FleetUpdateError(f"{ANSWERS_FILE} must not be a symlink")
    if not answers_path.is_file():
        raise FleetUpdateError(f"{ANSWERS_FILE} must be a regular file")

    updated_text = answers_path.read_text(encoding="utf-8")
    original_answers = parse_answers(original_text)
    updated_answers = parse_answers(updated_text)
    if original_answers == updated_answers and original_text != updated_text:
        answers_path.write_text(original_text, encoding="utf-8")
    elif original_answers != updated_answers:
        answers_path.write_text(render_answers(updated_answers), encoding="utf-8")


def render_answers_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    text = str(value)
    if ANSWERS_PLAIN_PATTERN.fullmatch(text) and not ANSWERS_RESERVED_PATTERN.fullmatch(text):
        return text
    return json.dumps(text, ensure_ascii=False)


def _render_answers_lines(value: Any, indent: int) -> list[str]:
    pad = " " * indent
    lines: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                lines.append(f"{pad}{render_answers_scalar(key)}:")
                lines.extend(_render_answers_lines(item, indent + 2))
            else:
                inline = ("{}" if isinstance(item, dict) else "[]") if isinstance(item, (dict, list)) else render_answers_scalar(item)
                lines.append(f"{pad}{render_answers_scalar(key)}: {inline}")
        return lines
    for item in value:
        if isinstance(item, (dict, list)) and item:
            nested = _render_answers_lines(item, indent + 2)
            lines.append(f"{pad}- {nested[0].lstrip()}")
            lines.extend(nested[1:])
        else:
            inline = ("{}" if isinstance(item, dict) else "[]") if isinstance(item, (dict, list)) else render_answers_scalar(item)
            lines.append(f"{pad}- {inline}")
    return lines


def render_answers(answers: dict[str, Any]) -> str:
    """Serialize Copier answers exactly as the template's answers file does."""
    return "\n".join(_render_answers_lines(answers, 0)) + "\n"


def verify_release_helper(repository_path: Path, template_source: str, template_ref: str, *, env: dict[str, str]) -> None:
    """Fail closed when Copier preserved a stale or customized executable helper."""
    helper = repository_path / ".github/scripts/enrich_release_notes.py"
    answers = parse_answers((repository_path / ANSWERS_FILE).read_text(encoding="utf-8"))
    if not helper.exists():
        if answers.get("release_please") is True:
            raise FleetUpdateError(
                f"{helper.relative_to(repository_path)} is missing although release_please is enabled; "
                "restore the template helper before retrying"
            )
        return
    with tempfile.TemporaryDirectory(prefix="copier-helper-") as temporary:
        fetched = Path(temporary) / "expected.py"
        source = canonical_template_source(template_source)
        run(["git", "fetch", "--quiet", "--depth=1", source, template_ref], cwd=repository_path, env=env)
        result = run(
            ["git", "show", "FETCH_HEAD:template/.github/scripts/enrich_release_notes.py.jinja"],
            cwd=repository_path,
            env=env,
        )
        fetched.write_text(result.stdout, encoding="utf-8")
        if fetched.read_bytes() != helper.read_bytes():
            raise FleetUpdateError(
                ".github/scripts/enrich_release_notes.py differs from the selected ci-kit ref; "
                "reconcile project-owned/customized changes with the template before retrying"
            )


def ensure_release_dependency_sections(repository_path: Path) -> bool:
    """Give every Release Please package a visible deps changelog section.

    The config is project-owned, so Copier never touches it after generation. A
    config without changelog-sections falls back to Release Please defaults, which
    drop every deps(...) commit, and the reusable release workflow then refuses it.
    """
    answers = parse_answers((repository_path / ANSWERS_FILE).read_text(encoding="utf-8"))
    path = repository_path / ".github/release-please/config.json"
    if answers.get("release_please") is not True or not path.is_file():
        return False
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(config.get("packages"), dict):
        raise FleetUpdateError(f"cannot repair {path.relative_to(repository_path)}: packages must be a mapping")
    template = json.loads(TEMPLATE_RELEASE_CONFIG.read_text(encoding="utf-8"))["packages"]["."]["changelog-sections"]
    changed = False
    for package in config["packages"].values():
        if not isinstance(package, dict):
            continue
        sections = package.get("changelog-sections", config.get("changelog-sections"))
        if not isinstance(sections, list):
            sections = [dict(section) for section in template]
        elif not any(isinstance(s, dict) and s.get("type") == "deps" and not s.get("hidden") for s in sections):
            sections = [
                {k: v for k, v in s.items() if k != "hidden"} if isinstance(s, dict) and s.get("type") == "deps" else s
                for s in sections
            ]
            if not any(isinstance(s, dict) and s.get("type") == "deps" for s in sections):
                sections.append(next(dict(s) for s in template if s["type"] == "deps"))
        else:
            continue
        package["changelog-sections"] = sections
        changed = True
    if changed:
        path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return changed


def seed_single_release_manifest(
    repository_path: Path,
    repository: Repository,
    *,
    env: dict[str, str],
) -> None:
    """Seed the root manifest from an existing stable release, never 0.1.0."""
    answers = parse_answers((repository_path / ANSWERS_FILE).read_text(encoding="utf-8"))
    manifest = repository_path / ".github/release-please/manifest.json"
    release_mode = answers.get("release_mode", "single")
    if (
        answers.get("release_please") is not True
        or release_mode != "single"
        or manifest.exists()
    ):
        return
    releases = gh_json(
        ["release", "list", "--repo", repository.name_with_owner,
         "--exclude-drafts", "--exclude-pre-releases", "--limit", "2",
         "--json", "tagName,isLatest"], env=env
    )
    stable = [
        item for item in releases
        if isinstance(item, dict) and isinstance(item.get("tagName"), str)
        and item.get("isLatest") is True
        and RELEASE_TAG_PATTERN.fullmatch(item["tagName"])
    ]
    if len(stable) > 1:
        raise FleetUpdateError("cannot seed release manifest: stable release is ambiguous")
    if len(stable) == 1:
        tag_name = stable[0]["tagName"]
    else:
        # GitHub Releases can be absent for a valid stable tag (for example
        # repositories that publish artifacts elsewhere).  Fall back to the
        # repository's exact SemVer tags, and never accept a template default.
        tags = gh_json_lines(
            ["api", f"repos/{repository.name_with_owner}/tags", "--paginate", "--jq", ".[].name | @json"],
            env=env,
        )
        candidates = sorted(
            {
                tag for tag in tags
                if isinstance(tag, str) and RELEASE_TAG_PATTERN.fullmatch(tag)
            },
            key=lambda tag: tuple(int(part) for part in tag[1:].split(".")),
            reverse=True,
        )
        if not candidates:
            raise FleetUpdateError("cannot seed release manifest: stable release is ambiguous or missing")
        tag_name = candidates[0]
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({".": tag_name[1:]}, indent=2) + "\n", encoding="utf-8")


def update_template(
    repository_path: Path,
    *,
    template_source: str,
    template_ref: str | None,
    env: dict[str, str],
    repository: Repository | None = None,
) -> list[str]:
    answers_path = repository_path / ANSWERS_FILE
    if answers_path.is_symlink():
        raise FleetUpdateError(f"{ANSWERS_FILE} must not be a symlink")
    if not answers_path.is_file():
        raise FleetUpdateError(f"{ANSWERS_FILE} must be a regular file")
    original_answers_text = answers_path.read_text(encoding="utf-8")

    # This is a breaking schema migration.  Copier's --defaults mode answers
    # the changed component question with its new default, which would replace
    # a legacy component's type/path.  Pass migrated answers as --data instead
    # of writing them before Copier starts: Copier requires a pristine
    # destination, and the resulting answers file is written by Copier itself.
    try:
        answers = yaml.safe_load(original_answers_text)
    except yaml.YAMLError as exc:
        raise FleetUpdateError(f"{ANSWERS_FILE} is not valid YAML: {exc}") from exc
    components = answers.get("components") if isinstance(answers, dict) else None
    if isinstance(components, list):
        migrated_components: list[dict[str, Any]] = []
        generated_ids: set[str] = {
            component["id"]
            for component in components
            if isinstance(component, dict) and isinstance(component.get("id"), str)
        }
        for index, component in enumerate(components):
            if not isinstance(component, dict):
                raise FleetUpdateError(f"components[{index}] must be a mapping")
            migrated = dict(component)
            if "id" not in migrated or "name" not in migrated:
                component_type = migrated.get("type")
                component_path = migrated.get("path")
                if not isinstance(component_type, str) or not isinstance(component_path, str):
                    raise FleetUpdateError(
                        f"cannot migrate components[{index}]: type and path are required"
                    )
                path_slug = re.sub(r"[^A-Za-z0-9_-]+", "-", component_path.strip("./")).strip("-")
                path_slug = path_slug or "app"
                candidate_id = f"{path_slug}-{component_type}"
                # Legacy paths may begin with a digit, while the breaking
                # schema deliberately uses the GitHub Actions identifier
                # grammar.  Prefix rather than dropping path information so
                # the generated identity remains stable and recognizable.
                if not re.match(r"^[A-Za-z_]", candidate_id):
                    candidate_id = f"component-{candidate_id}"
                if candidate_id in {
                    "template-contract", "docker", "release", "update", "scan",
                    "analyze", "resolve", "generate", "comment", "pages",
                    "changes", "integration",
                }:
                    candidate_id = f"component-{candidate_id}"
                # Distinct legacy paths can normalize to the same slug (for
                # example ``api/v1`` and ``api-v1``).  Keep the readable slug
                # where possible, and add a deterministic digest only when a
                # collision occurs.  This is a migration identity, not the
                # old type/index workflow fallback: every generated ID still
                # derives from the component's complete legacy identity.
                if candidate_id in generated_ids:
                    identity_key = f"{component_path}\0{component_type}"
                    digest = hashlib.sha256(identity_key.encode("utf-8")).hexdigest()
                    # A legacy template allowed repeated identical components.
                    # Bound the disambiguation by the input size: this keeps
                    # migration deterministic without an unbounded loop when
                    # every digest prefix is already occupied.
                    digest_candidate = f"{candidate_id}-{digest[:8]}"
                    candidate_id = digest_candidate
                    suffix = 2
                    while candidate_id in generated_ids and suffix <= len(components) + 1:
                        candidate_id = f"{digest_candidate}-{suffix}"
                        suffix += 1
                    if candidate_id in generated_ids:
                        raise FleetUpdateError(
                            f"cannot migrate components[{index}]: unable to allocate a unique stable identity"
                        )
                generated_ids.add(candidate_id)
                migrated.setdefault("id", candidate_id)
                migrated.setdefault("name", f"{path_slug.replace('-', ' ').title()} {component_type.title()}")
            migrated_components.append(migrated)
        if migrated_components != components:
            answers["components"] = migrated_components

    command = [
        "copier",
        "update",
        "--trust",
        "--defaults",
        "--conflict=rej",
        # Generated workflows often receive harmless local edits near template
        # changes. A one-line context keeps Copier's three-line default from
        # rejecting otherwise clean hunks while still surfacing overlapping edits.
        "--context-lines",
        "1",
        "--skip-tasks",
    ]
    if isinstance(components, list) and answers.get("components") != components:
        command.extend(
            [
                "--data",
                "components=" + json.dumps(answers["components"], separators=(",", ":")),
            ]
        )
    if "gitleaks" not in answers:
        command.extend(["--data", "gitleaks=true"])
    if template_ref:
        command.extend(["--vcs-ref", template_ref])
        if RELEASE_TAG_PATTERN.fullmatch(template_ref):
            command.extend(["--data", f"toolkit_version={template_ref}"])
    command.append(".")
    run(command, cwd=repository_path, env=env)
    if template_ref:
        verify_release_helper(repository_path, template_source, template_ref, env=env)
    if repository is not None:
        # Copier requires a pristine checkout. Seed a missing single-project
        # Release Please manifest only after the update has completed.
        seed_single_release_manifest(repository_path, repository, env=env)
    ensure_release_dependency_sections(repository_path)
    restore_answers_format_if_semantically_equal(answers_path, original_answers_text)
    canonicalize_answers_source(repository_path, template_source)
    if template_ref and RELEASE_TAG_PATTERN.fullmatch(template_ref):
        bump_project_owned_toolkit_refs(
            repository_path,
            template_ref,
            resolve_commit=lambda: resolve_template_commit(
                template_source, template_ref, env=env
            ),
        )
    rejected = [
        path
        for path in sorted(repository_path.rglob("*.rej"))
        if not discard_if_already_applied(repository_path, path)
    ]
    if rejected:
        names = ", ".join(str(path.relative_to(repository_path)) for path in rejected)
        raise FleetUpdateError(f"{CONFLICT_PREFIX} {names}")
    return changed_paths(repository_path)


def discard_if_already_applied(repository_path: Path, rejected: Path) -> bool:
    """Delete a Copier .rej whose hunks the project already carries.

    A project that already made the template's change locally (for example a lint fix
    that dropped a trailing blank line) rejects the same change from the template,
    although nothing is left to apply. When every rejected hunk applies in reverse, the
    file is already in the template's new state and the conflict is not real.
    """
    target = rejected.with_suffix("").relative_to(repository_path).as_posix()
    try:
        lines = rejected.read_text(encoding="utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return False
    if not lines or not lines[0].startswith("diff "):
        return False
    patch = f"--- a/{target}\n+++ b/{target}\n" + "".join(lines[1:])
    with tempfile.NamedTemporaryFile("w", suffix=".diff", encoding="utf-8", delete=False) as handle:
        handle.write(patch)
    try:
        completed = run(["git", "apply", "--check", "--reverse", handle.name], cwd=repository_path, check=False)
    finally:
        os.unlink(handle.name)
    if completed.returncode:
        return False
    rejected.unlink()
    return True


def authenticated_git() -> list[str]:
    """Return a git invocation that authenticates through gh rather than a prompt.

    git does not read GH_TOKEN. A local maintainer session usually has a credential
    helper configured already, which is why the write path worked there, but a hosted
    runner has none and the push fails with "could not read Username for
    https://github.com". Clearing inherited helpers first keeps an ambient one from
    taking precedence, and the token never reaches a remote URL or the reflog.
    """
    return [
        "git",
        "-c",
        "credential.helper=",
        "-c",
        "credential.helper=!gh auth git-credential",
    ]


TOOLKIT_REFERENCE_PREFIX = r"quokkify/ci-kit/(?:\.github/workflows|actions)/[^@\s]+@"
TOOLKIT_TAG_REFERENCE = re.compile(rf"({TOOLKIT_REFERENCE_PREFIX})v\d+\.\d+\.\d+")
TOOLKIT_DIGEST_REFERENCE = re.compile(
    rf"({TOOLKIT_REFERENCE_PREFIX})[0-9a-f]{{40}}([ \t]+#[ \t]*)v\d+\.\d+\.\d+"
)
RENAMED_TOOLKIT_REFERENCE = re.compile(
    r"(?<![\w./-])(" + "|".join(re.escape(name) for name in RENAMED_TEMPLATE_REPOSITORIES) + r")/(?=(?:\.github/workflows|actions)/[^@\s]+@)"
)


def module_owned_paths(repository_path: Path) -> set[str]:
    answers_path = repository_path / ANSWERS_FILE
    if not answers_path.is_file():
        return set()
    return {
        path
        for area in legacy_handed_off(answers_path.read_text(encoding="utf-8"))
        if area in AREAS
        for path in AREAS[area].legacy_paths
    }


def bump_project_owned_toolkit_refs(
    repository_path: Path,
    template_ref: str,
    *,
    resolve_commit: Callable[[], str] | None = None,
) -> list[str]:
    """Move toolkit references in project-owned workflows to the new template version.

    Copier rewrites the workflows it owns, but a project-owned workflow such as ci.yml
    also references this toolkit, and the generated template contract requires every such
    reference to match the recorded toolkit_version. Leaving those behind fails the
    consumer's own validation immediately after an otherwise correct update, so the bump
    has to be part of the same commit.

    Files of a handed-off area belong to their module, whose own update moves them;
    rewriting them here would make that update conflict.

    Exact vMAJOR.MINOR.PATCH tags are rewritten to the new tag. A digest pin carrying a
    release comment, which is what Renovate writes, is rewritten to the new tag's commit
    together with its comment: the contract accepts a digest only while the comment still
    names the recorded toolkit_version, so leaving the comment behind is the same failure
    as leaving a stale tag behind, and neither this update nor the Renovate bump can pass
    that check on its own. A digest pin without a release comment is left alone, since
    nothing identifies which release it was meant to track. References that still use a
    renamed toolkit repository name are moved to the current name first.

    ``resolve_commit`` runs at most once, and only when a digest pin is present, so a
    fleet of tag-only consumers costs no extra API call.
    """
    changed: list[str] = []
    commit: str | None = None
    module_owned = module_owned_paths(repository_path)
    workflows = repository_path / ".github" / "workflows"
    for workflow in sorted(workflows.glob("*.yml")) + sorted(workflows.glob("*.yaml")):
        if workflow.relative_to(repository_path).as_posix() in module_owned:
            continue
        original = workflow.read_text(encoding="utf-8")
        updated = RENAMED_TOOLKIT_REFERENCE.sub(lambda match: RENAMED_TEMPLATE_REPOSITORIES[match.group(1)] + "/", original)
        updated = TOOLKIT_TAG_REFERENCE.sub(rf"\g<1>{template_ref}", updated)
        if resolve_commit is not None and TOOLKIT_DIGEST_REFERENCE.search(updated):
            if commit is None:
                commit = resolve_commit()
            updated = TOOLKIT_DIGEST_REFERENCE.sub(
                rf"\g<1>{commit}\g<2>{template_ref}", updated
            )
        if updated != original:
            workflow.write_text(updated, encoding="utf-8")
            changed.append(workflow.relative_to(repository_path).as_posix())
    return changed


def push_automation_branch(
    repository: Repository,
    repository_path: Path,
    *,
    branch: str,
    env: dict[str, str],
    message: str = PR_TITLE,
    replaced: list[str] | None = None,
) -> str:
    run(["git", "switch", "-C", branch], cwd=repository_path, env=env)
    run(["git", "config", "user.name", "quokkify-copier[bot]"], cwd=repository_path)
    run(
        ["git", "config", "user.email", "quokkify-copier[bot]@users.noreply.github.com"],
        cwd=repository_path,
    )
    run(["git", "add", "--all"], cwd=repository_path)
    run(["git", "diff", "--cached", "--check"], cwd=repository_path)
    run(["git", "commit", "-m", message], cwd=repository_path)

    remote = run(
        [*authenticated_git(), "ls-remote", "--heads", "origin", f"refs/heads/{branch}"],
        cwd=repository_path,
        env=env,
    ).stdout.strip()
    push = [*authenticated_git(), "push"]
    if remote:
        remote_sha = remote.split()[0]
        if remote_has_same_change(repository, repository_path, remote_sha, env=env):
            return remote_sha
        push.append(f"--force-with-lease=refs/heads/{branch}:{remote_sha}")
        if replaced is not None:
            replaced.append(remote_sha)
    push.extend(["origin", f"HEAD:refs/heads/{branch}"])
    run(push, cwd=repository_path, env=env)
    return git_head(repository_path)


def remote_has_same_change(
    repository: Repository,
    repository_path: Path,
    remote_sha: str,
    *,
    env: dict[str, str],
) -> bool:
    """Whether the automation branch already carries this exact update.

    Every run makes a fresh commit, so pushing it unconditionally would restart CI
    on an identical tree and throw away pilot evidence a rerun is waiting for.
    """
    try:
        remote = gh_json(["api", f"repos/{repository.name_with_owner}/commits/{remote_sha}"], env=env)
    except FleetUpdateError:
        return False
    local_tree = run(["git", "rev-parse", "HEAD^{tree}"], cwd=repository_path).stdout.strip()
    local_parent = run(["git", "rev-parse", "HEAD^"], cwd=repository_path).stdout.strip()
    parents = [parent.get("sha") for parent in remote.get("parents", [])]
    return remote.get("commit", {}).get("tree", {}).get("sha") == local_tree and parents == [local_parent]


def module_table(rows: Sequence[dict[str, str]]) -> str:
    if not rows:
        return ""
    lines = ["| module | old → new | doctor |", "| --- | --- | --- |"]
    lines.extend(
        f"| {markdown_escape(row['module'])} | {markdown_escape(row['from'] or 'unknown')} → "
        f"{markdown_escape(row['to'])} | {markdown_escape(row['doctor'])} |"
        for row in rows
    )
    return "\n\n## Module updates\n\n" + "\n".join(lines)


def ci_kit_environment(env: dict[str, str]) -> dict[str, str]:
    merged = dict(env)
    existing = merged.get("PYTHONPATH")
    merged["PYTHONPATH"] = str(CI_KIT_TOOLS) + (os.pathsep + existing if existing else "")
    return merged


def run_ci_kit_update(repository_path: Path, template_ref: str, *, env: dict[str, str]) -> list[dict[str, str]]:
    with tempfile.TemporaryDirectory(prefix="ci-kit-update-") as temporary:
        report_path = Path(temporary) / "update.json"
        completed = run(
            [
                sys.executable,
                "-m",
                "ci_kit.cli",
                "update",
                "--repo",
                str(repository_path),
                "--to",
                template_ref,
                "--skip-legacy",
                "--json",
                str(report_path),
            ],
            env=ci_kit_environment(env),
            check=False,
        )
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = {}
    findings = report.get("findings") if isinstance(report, dict) else None
    codes = [
        finding["code"]
        for finding in findings or []
        if isinstance(finding, dict) and isinstance(finding.get("code"), str)
    ]
    if completed.returncode:
        reason = ", ".join(codes) or f"exit {completed.returncode}"
        raise FleetUpdateError(f"ci-kit update failed: {reason}")
    return [
        {
            "module": str(row.get("module")),
            "from": str(row.get("from") or ""),
            "to": str(row.get("to")),
            "doctor": "ok",
        }
        for row in report.get("modules", [])
        if isinstance(row, dict)
    ]


def apply_module_updates(repository_path: Path, template_ref: str, *, env: dict[str, str]) -> list[dict[str, str]]:
    identity = ["-c", "user.name=quokkify-copier[bot]", "-c", "user.email=quokkify-copier[bot]@users.noreply.github.com"]
    base = git_head(repository_path)
    run(["git", "add", "--all"], cwd=repository_path)
    if run(["git", "status", "--porcelain"], cwd=repository_path).stdout.strip():
        run(["git", *identity, "commit", "-q", "-m", "chore(template): pending legacy update"], cwd=repository_path)
    rows = run_ci_kit_update(repository_path, template_ref, env=env)
    squash_to_base(repository_path, base)
    return rows


def git_head(repository_path: Path) -> str:
    return run(["git", "rev-parse", "HEAD"], cwd=repository_path).stdout.strip()


def squash_to_base(repository_path: Path, base: str) -> None:
    run(["git", "reset", "--soft", base], cwd=repository_path)


def existing_pull_request(repository: Repository, branch: str, *, env: dict[str, str]) -> str | None:
    owner = repository.name_with_owner.split("/", 1)[0].casefold()
    listed = gh_json(
        [
            "pr",
            "list",
            "--repo",
            repository.name_with_owner,
            "--state",
            "open",
            "--head",
            branch,
            "--json",
            "url,number,isCrossRepository,headRepositoryOwner,headRefName",
        ],
        env=env,
    )
    existing = [
        pull
        for pull in listed
        if isinstance(pull, dict)
        and pull.get("isCrossRepository") is False
        and str((pull.get("headRepositoryOwner") or {}).get("login", "")).casefold() == owner
        and pull.get("headRefName") == branch
    ]
    return existing[0]["url"] if existing else None


def ensure_pull_request(
    repository: Repository,
    *,
    branch: str,
    changed: Sequence[str],
    env: dict[str, str],
    modules: Sequence[dict[str, str]] = (),
    auto_merge: bool = False,
) -> str:
    existing = existing_pull_request(repository, branch, env=env)
    if existing:
        return existing

    body = (
        "## Summary\n\n"
        "Automated `copier update` from the shared "
        "[`quokkify/ci-kit`](https://github.com/quokkify/ci-kit) template.\n\n"
        "This keeps the organization baseline (validation, CodeQL, Gitleaks, "
        "Release Please, and Renovate) synchronized "
        + (
            "and may enable auto-merge when the repository allows it; major version bumps always wait for a maintainer.\n\n"
            if auto_merge
            else "while leaving merge approval to maintainers.\n\n"
        )
        + "## Generated changes\n\n"
        + "\n".join(f"- `{path}`" for path in changed)
        + module_table(modules)
        + "\n\n## Verification\n\n"
        "- `copier update --trust --defaults --conflict=rej --skip-tasks`\n"
        "- Legacy components keep their `type`/`path`; the updater supplies stable `id`/`name` migration data.\n"
        "- `git diff --cached --check`\n\n"
        "Do not edit the automation branch directly; make project-specific changes after merging "
        "or adjust the Copier answers/template.\n"
    )
    return create_pull_request(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            repository.name_with_owner,
            "--base",
            repository.default_branch,
            "--head",
            branch,
            "--title",
            PR_TITLE,
            "--body",
            body,
        ],
        env=env,
    )


def create_pull_request(command: Sequence[str], *, env: dict[str, str]) -> str:
    """Create a pull request labelled PR_LABEL, unlabelled where the repository lacks it.

    gh resolves labels before it creates the pull request, so an unknown label fails
    the whole command without creating anything and the unlabelled retry is safe.
    """
    try:
        return run([*command, "--label", PR_LABEL], env=env).stdout.strip()
    except FleetUpdateError as exc:
        if f"could not add label: '{PR_LABEL}' not found" not in str(exc):
            raise
    return run(command, env=env).stdout.strip()


AUTO_MERGE_HEAD_POLL_SECONDS = 2
AUTO_MERGE_HEAD_POLLS = 3
AUTO_MERGE_PULL_FIELDS = "state,baseRefName,mergeable,headRefOid,autoMergeRequest,isCrossRepository,headRefName,statusCheckRollup"
AUTO_MERGE_SETTLE = timedelta(minutes=10)
CHECK_PASSING_CONCLUSIONS = frozenset({"SUCCESS", "SKIPPED", "NEUTRAL"})
CHECK_PENDING_STATES = frozenset({"PENDING", "EXPECTED"})


def parse_check_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def checks_verdict(rollup: object, now: datetime) -> tuple[str, str]:
    """Classify a statusCheckRollup as ready, waiting or failed, with a human reason."""
    entries = [entry for entry in rollup if isinstance(entry, dict)] if isinstance(rollup, list) else []
    if not entries:
        return "waiting", "no checks reported yet"
    failed: list[str] = []
    running = False
    latest: datetime | None = None
    for entry in entries:
        if entry.get("__typename") == "StatusContext":
            name, state = str(entry.get("context") or "?"), entry.get("state")
            if state in CHECK_PENDING_STATES:
                running = True
            elif state != "SUCCESS":
                failed.append(name)
            moment = parse_check_time(entry.get("startedAt"))
        else:
            name = str(entry.get("name") or "?")
            if entry.get("status") != "COMPLETED":
                running = True
            elif entry.get("conclusion") not in CHECK_PASSING_CONCLUSIONS:
                failed.append(name)
            moment = parse_check_time(entry.get("completedAt"))
        if moment is not None and (latest is None or moment > latest):
            latest = moment
    if failed:
        return "failed", f"failed checks: {', '.join(failed)}"
    if running:
        return "waiting", "checks still running"
    if latest is None:
        return "waiting", "check times unavailable"
    if now - latest < AUTO_MERGE_SETTLE:
        return "waiting", "checks finished less than 10 minutes ago"
    return "ready", "all checks green"


def pull_request_number(repository: Repository, url: str) -> str | None:
    match = PULL_REQUEST_URL_PATTERN.fullmatch(url)
    return match[2] if match and match[1].casefold() == repository.name_with_owner.casefold() else None


def release_version(tag: str | None) -> tuple[int, int, int] | None:
    if not tag or not RELEASE_TAG_PATTERN.fullmatch(tag):
        return None
    major, minor, patch = (int(part) for part in tag[1:].split("."))
    return major, minor, patch


def is_major_bump(applied: tuple[int, int, int], target: tuple[int, int, int]) -> bool:
    return applied[0] != target[0] or (target[0] == 0 and applied[1] != target[1])


def disable_auto_merge(repository: Repository, number: str, *, env: dict[str, str]) -> str | None:
    """Disarm armed auto-merge; return the sanitized error, or None on success."""
    completed = run(["gh", "pr", "merge", number, "--repo", repository.name_with_owner, "--disable-auto"], env=env, check=False)
    if not completed.returncode:
        return None
    return sanitize_text(completed.stderr.strip() or completed.stdout.strip() or f"gh pr merge --disable-auto exited {completed.returncode}")


def enable_auto_merge(
    repository: Repository,
    url: str,
    *,
    branch: str,
    head: str | None,
    applied: str,
    target: str | None,
    requested_template_ref: str | None,
    env: dict[str, str],
    modules: Sequence[dict[str, str]] = (),
    disarmed: bool = False,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, str]:
    """Squash-merge the automation PR: through GitHub auto-merge when the repository allows it, directly once checks are green otherwise."""
    number = pull_request_number(repository, url)
    if number is None:
        return {"state": "failed", "reason": "pull request URL does not belong to the repository"}
    name = repository.name_with_owner
    pull_request: dict[str, Any] = {}
    direct = False

    def view() -> dict[str, Any]:
        payload = gh_json(["pr", "view", number, "--repo", name, "--json", AUTO_MERGE_PULL_FIELDS], env=env)
        if not isinstance(payload, dict):
            raise FleetUpdateError("pull request metadata is unreadable")
        return payload

    def evaluate() -> tuple[str, str]:
        nonlocal pull_request, direct
        pull_request = view()
        if pull_request.get("state") == "MERGED":
            return "merged", "pull request already merged"
        if branch != DEFAULT_BRANCH:
            return "skipped", "not the automation update branch"
        if pull_request.get("isCrossRepository") is not False or pull_request.get("headRefName") != branch:
            return "skipped", "pull request is not the updater's own branch"
        target_version = release_version(target)
        if requested_template_ref or target_version is None:
            return "skipped", "target is not a release tag resolved by the run"
        applied_version = release_version(applied)
        if applied_version is None:
            return "skipped", "applied version is not a release tag"
        if target_version < applied_version:
            return "skipped", f"target {target} is older than {applied}"
        if is_major_bump(applied_version, target_version):
            return "skipped", f"major version bump {applied} → {target} needs a human"
        for row in modules:
            module_version = release_version(row.get("from"))
            if module_version is None:
                return "skipped", f"module {row.get('module')} applied version is not a release tag"
            if is_major_bump(module_version, target_version):
                return "skipped", f"major version bump {row.get('module')} {row.get('from')} → {target} needs a human"
        settings = gh_json(["api", f"repos/{name}"], env=env)
        if not isinstance(settings, dict) or "allow_auto_merge" not in settings or "allow_squash_merge" not in settings:
            return "failed", "repository auto-merge setting unreadable (token permissions?)"
        direct = not settings["allow_auto_merge"]
        if not settings["allow_squash_merge"]:
            return "skipped", "repository does not allow squash merge"
        if pull_request.get("state") != "OPEN":
            return "skipped", "pull request is not open"
        if pull_request.get("baseRefName") != repository.default_branch:
            return "skipped", "pull request base is not the default branch"
        if pull_request.get("mergeable") == "CONFLICTING":
            return "skipped", "pull request has merge conflicts"
        expected = head or pull_request.get("headRefOid")
        if not isinstance(expected, str) or not COMMIT_SHA_PATTERN.fullmatch(expected):
            return "failed", "pull request head commit is unknown"
        for _ in range(AUTO_MERGE_HEAD_POLLS):
            if pull_request.get("headRefOid") == expected:
                break
            time.sleep(AUTO_MERGE_HEAD_POLL_SECONDS)
            pull_request = view()
        if pull_request.get("headRefOid") != expected:
            return "skipped", "pull request head not yet updated"
        if direct:
            if pull_request.get("mergeable") != "MERGEABLE":
                return "waiting", "mergeability not yet computed"
            verdict, why = checks_verdict(pull_request.get("statusCheckRollup"), now())
            if verdict != "ready":
                return ("skipped" if verdict == "failed" else "waiting"), why
            # Never pass --auto here: where auto-merge cannot be armed, gh merges at once and ignores checks.
            completed = run(["gh", "pr", "merge", number, "--repo", name, "--squash", "--match-head-commit", expected], env=env, check=False)
            if completed.returncode:
                return "failed", sanitize_text(completed.stderr.strip() or completed.stdout.strip() or f"gh pr merge exited {completed.returncode}")
            return "merged", f"merged {expected}"
        if pull_request.get("autoMergeRequest") and not disarmed:
            return "enabled", "auto-merge already enabled"
        completed = run(["gh", "pr", "merge", number, "--repo", name, "--auto", "--squash", "--match-head-commit", expected], env=env, check=False)
        if completed.returncode:
            return "failed", sanitize_text(completed.stderr.strip() or completed.stdout.strip() or f"gh pr merge exited {completed.returncode}")
        return "enabled", f"auto-merge enabled for {expected}"

    try:
        state, reason = evaluate()
        if state in {"skipped", "failed"} and not direct and pull_request.get("autoMergeRequest") and not disarmed:
            error = disable_auto_merge(repository, number, env=env)
            if error is None:
                reason += "; existing auto-merge disabled"
            else:
                state, reason = "failed", f"{reason}; could not disable existing auto-merge: {error}"
    except Exception as exc:  # the PR exists; never fail the repository over auto-merge
        state, reason = "failed", sanitize_text(str(exc) or type(exc).__name__)
    return {"state": state, "reason": reason}


def answers_file_name(module: str) -> str:
    return ANSWERS_FILE if module == LEGACY_MODULE else f".copier-answers.{module}.yml"


def answers_at(repository: Repository, ref: str, *, env: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Parse each root answers file at ref, keyed by file name."""
    return {
        answers_file_name(module): parse_answers(text)
        for module, text in fetch_answers_files(repository.name_with_owner, env=env, ref=ref).items()
    }


def answers_commit(answers: dict[str, Any]) -> str | None:
    commit = answers.get("_commit")
    return commit.strip() if isinstance(commit, str) and commit.strip() else None


def answers_versions(repository: Repository, ref: str, *, env: dict[str, str]) -> dict[str, str | None]:
    """Map each root answers file at ref to its _commit."""
    return {file: answers_commit(answers) for file, answers in answers_at(repository, ref, env=env).items()}


def trusted_answers_source(file: str, answers: dict[str, Any], expected_template: str) -> bool:
    source = answers.get("_src_path")
    if not isinstance(source, str):
        return False
    if file == ANSWERS_FILE:
        expected = normalize_template_source(expected_template)
        return expected is not None and normalize_template_source(source) == expected
    return bool(TRUSTED_SRC_RE.fullmatch(source))


def merge_ready_repository(repository: Repository, *, branch: str, env: dict[str, str], expected_template: str) -> Result:
    """Evaluate and merge the open automation PR without running Copier or pushing."""
    name = repository.name_with_owner
    pulls = gh_json(["pr", "list", "--repo", name, "--head", branch, "--state", "open", "--json", "url,headRefOid"], env=env)
    if not isinstance(pulls, list):
        raise FleetUpdateError("pull request listing is unreadable")
    if not pulls:
        return Result(name, "up-to-date")
    pull = pulls[0]
    url = pull.get("url") if isinstance(pull, dict) else None
    head_oid = pull.get("headRefOid") if isinstance(pull, dict) else None
    if not isinstance(url, str) or not isinstance(head_oid, str) or not COMMIT_SHA_PATTERN.fullmatch(head_oid):
        raise FleetUpdateError("open pull request has no usable URL or head commit")

    def skipped(reason: str) -> Result:
        return Result(name, "pull-request", url, auto_merge={"state": "skipped", "reason": reason})

    try:
        base = answers_at(repository, repository.default_branch, env=env)
        head = answers_at(repository, head_oid, env=env)
    except UnsafeAnswersFileError as exc:
        return Result(name, "unsafe-answers-file", str(exc))
    differ = sorted(set(base) ^ set(head))
    if differ:
        return skipped(f"answers files differ: {', '.join(differ)}")
    if not base:
        return skipped("no answers files")
    for file in sorted(base):
        if (
            not trusted_answers_source(file, base[file], expected_template)
            or not trusted_answers_source(file, head[file], expected_template)
            or base[file].get("_src_path") != head[file].get("_src_path")
        ):
            return skipped(f"untrusted template source in {file}")
    modules = sorted(ANSWERS_RE.fullmatch(file)[1] for file in base if file != ANSWERS_FILE)
    if ANSWERS_FILE in base:
        primary = ANSWERS_FILE
    else:
        primary = answers_file_name("core" if "core" in modules else modules[0])
    target = answers_commit(head[primary])
    rows: list[dict[str, str]] = []
    for module in modules:
        file = answers_file_name(module)
        if file == primary:
            continue
        head_commit = answers_commit(head[file])
        if head_commit != target:
            return skipped(f"module {module} head {head_commit} does not match target {target}")
        rows.append({"module": module, "from": answers_commit(base[file]) or ""})
    merge = enable_auto_merge(
        repository,
        url,
        branch=branch,
        head=head_oid,
        applied=answers_commit(base[primary]) or "",
        target=target,
        requested_template_ref=None,
        env=env,
        modules=rows,
    )
    return Result(name, "pull-request", url, auto_merge=merge)


def process_repository(
    repository: Repository,
    *,
    expected_template: str,
    branch: str,
    dry_run: bool,
    template_ref: str | None,
    env: dict[str, str],
    workspace: Path,
    auto_merge: bool = False,
    requested_template_ref: str | None = None,
) -> Result:
    name = repository.name_with_owner
    try:
        files = fetch_answers_files(name, env=env)
    except UnsafeAnswersFileError as exc:
        return Result(name, "unsafe-answers-file", str(exc))
    if not files:
        return Result(name, "not-managed")

    module_files = {module: text for module, text in files.items() if module != LEGACY_MODULE}
    for module in sorted(module_files):
        if module not in MODULES:
            return Result(name, "unknown-module", module)
    for module, text in sorted(module_files.items()):
        module_source = parse_answers(text).get("_src_path")
        if not isinstance(module_source, str) or not TRUSTED_SRC_RE.fullmatch(module_source):
            return Result(name, "foreign-module-template", f"{module}: {module_source if isinstance(module_source, str) else 'missing _src_path'}")

    normalized_expected = normalize_template_source(expected_template)
    raw_answers = files.get(LEGACY_MODULE)
    if raw_answers is not None:
        source = normalize_template_source(parse_template_source(raw_answers))
        if normalized_expected is None or source != normalized_expected:
            return Result(name, "foreign-template", source or "unrecognized source")

    destination = workspace / name.replace("/", "--")
    clone_repository(repository, destination, env=env)
    for module in module_files:
        if not is_regular_file(destination / f".copier-answers.{module}.yml", repository_root=destination):
            return Result(name, "unsafe-answers-file", f".copier-answers.{module}.yml")
    modules = tuple(sorted(module_files))

    if raw_answers is not None:
        cloned_answers_path = destination / ANSWERS_FILE
        if not is_regular_file(cloned_answers_path, repository_root=destination):
            raise FleetUpdateError(f"cloned {ANSWERS_FILE} must be a regular file")
        cloned_answers = cloned_answers_path.read_text(encoding="utf-8")
        cloned_source = normalize_template_source(parse_template_source(cloned_answers))
        if cloned_source != normalized_expected:
            raise FleetUpdateError("cloned repository changed to a different template during audit")
        inventory = inventory_from_answers(cloned_answers, destination)
        if modules:
            areas = ",".join(legacy_handed_off(cloned_answers))
            inventory = replace(inventory, modules=modules, state=f"hybrid({areas})")
    else:
        primary = "core" if "core" in modules else modules[0]
        primary_answers = parse_answers((destination / f".copier-answers.{primary}.yml").read_text(encoding="utf-8"))
        primary_commit = primary_answers.get("_commit")
        inventory = modules_inventory(
            primary_commit.strip() if isinstance(primary_commit, str) and primary_commit.strip() else "unknown",
            modules,
            destination,
        )
    try:
        paths: list[str] = []
        if raw_answers is not None:
            paths = update_template(
                destination,
                template_source=expected_template,
                template_ref=template_ref,
                env=env,
                repository=repository,
            )
            updated_answers_path = destination / ANSWERS_FILE
            if is_regular_file(updated_answers_path, repository_root=destination):
                updated_answers = parse_answers(updated_answers_path.read_text(encoding="utf-8"))
                updated_commit = updated_answers.get("_commit")
                if isinstance(updated_commit, str) and updated_commit.strip():
                    inventory = replace(inventory, target_commit=updated_commit.strip())
        rows: list[dict[str, str]] = []
        if modules:
            if not template_ref:
                raise FleetUpdateError("module update requires a resolved template ref")
            rows = apply_module_updates(destination, template_ref, env=env)
            paths = changed_paths(destination)
            if raw_answers is None:
                target = parse_answers(
                    (destination / f".copier-answers.{primary}.yml").read_text(encoding="utf-8")
                ).get("_commit")
                if isinstance(target, str) and target.strip():
                    inventory = replace(inventory, target_commit=target.strip())
        if not paths:
            return Result(name, "up-to-date", inventory=inventory)
        if dry_run:
            return Result(name, "would-update", ", ".join(paths), inventory)

        replaced: list[str] = []
        head = push_automation_branch(repository, destination, branch=branch, env=env, replaced=replaced)
        url = ensure_pull_request(
            repository,
            branch=branch,
            changed=paths,
            env=env,
            **({"modules": rows} if rows else {}),
            **({"auto_merge": True} if auto_merge else {}),
        )
        disarmed = False
        if replaced:
            # GitHub keeps auto-merge armed across pushes, so a new head must be re-approved.
            number = pull_request_number(repository, url)
            error = None if number else "pull request URL does not belong to the repository"
            try:
                if number and gh_json(["pr", "view", number, "--repo", name, "--json", "autoMergeRequest"], env=env).get("autoMergeRequest"):
                    error = disable_auto_merge(repository, number, env=env)
                    disarmed = error is None
            except Exception as exc:  # the PR exists; record instead of failing the repository
                error = sanitize_text(str(exc) or type(exc).__name__)
            if error:
                return Result(name, "pull-request", url, inventory, auto_merge={"state": "failed", "reason": f"could not disable auto-merge after pushing a new head: {error}"})
        if not auto_merge:
            return Result(name, "pull-request", url, inventory)
        merge = enable_auto_merge(
            repository,
            url,
            branch=branch,
            head=head,
            applied=inventory.commit,
            target=template_ref,
            requested_template_ref=requested_template_ref,
            env=env,
            modules=rows,
            disarmed=disarmed,
        )
        return Result(name, "pull-request", url, inventory, auto_merge=merge)
    except Exception as exc:
        raise RepositoryProcessError(str(exc), inventory) from exc


def add_module_branch(module: str) -> str:
    return f"ci-kit/add-{module}"


def add_module_title(module: str) -> str:
    return f"chore(ci-kit): add {module} module"


def run_ci_kit_add(repository_path: Path, module: str, target_ref: str, *, env: dict[str, str]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="ci-kit-add-") as temporary:
        report_path = Path(temporary) / "add.json"
        completed = run(
            [
                sys.executable,
                "-m",
                "ci_kit.cli",
                "add",
                "--module",
                module,
                "--repo",
                str(repository_path),
                "--to",
                target_ref,
                "--json",
                str(report_path),
            ],
            env=ci_kit_environment(env),
            check=False,
        )
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = None
    if not isinstance(report, dict):
        raise FleetUpdateError(f"ci-kit add failed: exit {completed.returncode}")
    if completed.returncode and not report.get("findings"):
        raise FleetUpdateError(f"ci-kit add failed: exit {completed.returncode}")
    return report


def add_module_pull_request(repository: Repository, *, branch: str, module: str, target_ref: str, env: dict[str, str]) -> str:
    existing = existing_pull_request(repository, branch, env=env)
    if existing:
        return existing
    body = (
        "## Summary\n\n"
        f"Adds the `{module}` module from the shared [`quokkify/ci-kit`](https://github.com/quokkify/ci-kit) template "
        f"at `{target_ref}`.\n\n"
        f"Merging this pull request triggers the `{module}` module's workflow on `main`.\n"
    )
    return create_pull_request(
        [
            "gh",
            "pr",
            "create",
            "--repo",
            repository.name_with_owner,
            "--base",
            repository.default_branch,
            "--head",
            branch,
            "--title",
            add_module_title(module),
            "--body",
            body,
        ],
        env=env,
    )


def add_module_repository(
    repository: Repository,
    *,
    module: str,
    target_ref: str | None,
    dry_run: bool,
    env: dict[str, str],
    workspace: Path,
    expected_template: str,
) -> Result:
    name = repository.name_with_owner
    try:
        files = fetch_answers_files(name, env=env)
    except UnsafeAnswersFileError as exc:
        return Result(name, "unsafe-answers-file", str(exc))
    if not files:
        return Result(name, "not-managed")
    if module in files:
        return Result(name, "up-to-date")
    core = files.get("core")
    if core is None:
        return Result(name, "add-rejected", "CIK-ADD-003")
    core_answers = parse_answers(core)
    core_source = core_answers.get("_src_path")
    if not isinstance(core_source, str) or not TRUSTED_SRC_RE.fullmatch(core_source):
        return Result(name, "foreign-module-template", f"core: {core_source if isinstance(core_source, str) else 'missing _src_path'}")
    if not target_ref:
        raise FleetUpdateError("add-module requires a resolved template ref")
    core_commit = answers_commit(core_answers)
    if core_commit != target_ref:
        return Result(name, "needs-update-first", f"core {core_commit or 'unknown'}, target {target_ref}")

    destination = workspace / name.replace("/", "--")
    clone_repository(repository, destination, env=env)
    base = git_head(destination)
    report = run_ci_kit_add(destination, module, target_ref, env=env)
    codes = [
        finding["code"]
        for finding in report.get("findings") or []
        if isinstance(finding, dict) and isinstance(finding.get("code"), str)
    ]
    if codes:
        return Result(name, "add-rejected", ", ".join(codes))
    if not report.get("installed"):
        return Result(name, "up-to-date")
    if dry_run:
        return Result(name, "would-add", module)
    branch = add_module_branch(module)
    squash_to_base(destination, base)
    push_automation_branch(repository, destination, branch=branch, env=env, message=add_module_title(module))
    url = add_module_pull_request(repository, branch=branch, module=module, target_ref=target_ref, env=env)
    return Result(name, "pull-request", url)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--org", default="quokkify")
    parser.add_argument("--template-repository", default=DEFAULT_TEMPLATE_REPOSITORY)
    parser.add_argument("--template-ref", help="Optional Copier VCS ref; scheduled runs normally use the latest release tag.")
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--include-health", action="store_true", help="Add read-only adoption, exact-revision CI, branch policy and PR evidence.")
    parser.add_argument("--health-exceptions", type=Path, help="Maintainer-owned reporting-only exception JSON; defaults to checked-in fleet config.")
    parser.add_argument("--markdown-report", type=Path, help="Write a Markdown fleet report to this path.")
    parser.add_argument("--json-report", type=Path, help="Write a machine-readable JSON fleet report to this path.")
    parser.add_argument("--repo", action="append", default=[], help="Process one owner/repository; repeatable.")
    parser.add_argument("--exclude", action="append", default=[], help="Skip one owner/repository; repeatable.")
    parser.add_argument(
        "--public-only",
        action="store_true",
        help="Serve only public repositories; organization discovery is filtered and an explicit --repo that is not public fails.",
    )
    parser.add_argument("--auto-merge", action="store_true", help="Enable GitHub auto-merge (squash) on eligible template-update PRs; write mode only.")
    parser.add_argument(
        "--merge-ready",
        action="store_true",
        help="Skip Copier and pushes; only evaluate and merge open automation PRs. Requires --write --auto-merge.",
    )
    parser.add_argument(
        "--add-module",
        choices=sorted(module for module in MODULES if module != LEGACY_MODULE),
        help="Install this module in every managed repository through a ci-kit/add-<module> pull request; incompatible with --auto-merge and --merge-ready.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--write", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.add_module and (args.auto_merge or args.merge_ready):
        parser.error("--add-module cannot be combined with --auto-merge or --merge-ready")
    if args.auto_merge and args.dry_run:
        parser.error("--auto-merge cannot be combined with --dry-run")
    if args.merge_ready and not (args.write and args.auto_merge):
        parser.error("--merge-ready requires --write and --auto-merge")
    if args.merge_ready and args.template_ref:
        parser.error("--merge-ready cannot be combined with --template-ref")
    env = os.environ.copy()
    if args.write:
        if not env.get("GH_TOKEN", "").strip():
            print("GH_TOKEN is required in --write mode; use the CODEOWNER's short-lived local/API session", file=sys.stderr)
            return 2
        env["GH_TOKEN"] = env["GH_TOKEN"].strip()
    elif env.get("GITHUB_TOKEN", "").strip():
        env["GH_TOKEN"] = env["GITHUB_TOKEN"].strip()
    if not env.get("GH_TOKEN"):
        print("GH_TOKEN or GITHUB_TOKEN is required", file=sys.stderr)
        return 2
    for command in ("gh",) if args.merge_ready else ("copier", "gh", "git"):
        if shutil.which(command, path=env.get("PATH")) is None:
            print(f"required command is unavailable: {command}", file=sys.stderr)
            return 2

    branch = args.branch

    health_module = None
    health_exceptions: dict[str, dict[str, str]] = {}
    if args.include_health:
        import importlib.util
        spec = importlib.util.spec_from_file_location("fleet_health", Path(__file__).with_name("fleet_health.py"))
        assert spec and spec.loader
        health_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(health_module)
        try:
            health_exceptions = health_module.load_exceptions(args.health_exceptions) if args.health_exceptions else health_module.load_exceptions()
        except (OSError, ValueError):
            print("invalid or unreadable maintainer fleet-health exception configuration", file=sys.stderr)
            return 2
    elif args.health_exceptions:
        print("--health-exceptions requires --include-health", file=sys.stderr)
        return 2

    repositories: list[Repository] = []
    results: list[Result] = []
    failures = 0
    if args.repo:
        for requested_name in args.repo:
            normalized_name = normalize_template_source(requested_name)
            if normalized_name is None or requested_name.casefold() != normalized_name:
                result = Result(requested_name, "failed", "--repo must be exactly owner/repository")
                failures += 1
                results.append(result)
                continue
            try:
                metadata = gh_json(
                    [
                        "repo",
                        "view",
                        requested_name,
                        "--json",
                        "nameWithOwner,defaultBranchRef,isArchived,isFork,visibility",
                    ],
                    env=env,
                )
                default_ref = metadata.get("defaultBranchRef")
                full_name = metadata.get("nameWithOwner")
                if args.public_only and not is_public(metadata):
                    raise FleetUpdateError(PRIVATE_REPOSITORY_ERROR)
                if metadata.get("isArchived") or metadata.get("isFork"):
                    result = Result(requested_name, "excluded", "archived or fork")
                    results.append(result)
                    continue
                if (
                    not isinstance(full_name, str)
                    or not isinstance(default_ref, dict)
                    or not isinstance(default_ref.get("name"), str)
                ):
                    raise FleetUpdateError("repository metadata has no usable default branch")
                repositories.append(Repository(full_name, default_ref["name"]))
            except FleetUpdateError as exc:
                failures += 1
                result = Result(requested_name, "failed", str(exc))
                results.append(result)
    else:
        try:
            repositories = discover_repositories(
                args.org,
                env=env,
                public_only=args.public_only,
            )
        except FleetUpdateError as exc:
            print(f"fleet discovery failed: {exc}", file=sys.stderr)
            return 1

    excluded = {name.casefold() for name in args.exclude}
    template_ref = args.template_ref
    if not args.merge_ready and any(repository.name_with_owner.casefold() not in excluded for repository in repositories):
        try:
            template_ref = resolve_template_ref(
                args.template_repository,
                args.template_ref,
                env=env,
            )
        except FleetUpdateError as exc:
            print(f"template release resolution failed: {exc}", file=sys.stderr)
            return 1
    with tempfile.TemporaryDirectory(prefix="copier-fleet-") as temporary:
        workspace = Path(temporary)
        for repository in repositories:
            if repository.name_with_owner.casefold() in excluded:
                results.append(Result(repository.name_with_owner, "excluded"))
                continue
            try:
                if args.merge_ready:
                    result = merge_ready_repository(repository, branch=branch, env=env, expected_template=args.template_repository)
                elif args.add_module:
                    result = add_module_repository(
                        repository,
                        module=args.add_module,
                        target_ref=template_ref,
                        dry_run=args.dry_run,
                        env=env,
                        workspace=workspace,
                        expected_template=args.template_repository,
                    )
                else:
                    result = process_repository(
                        repository,
                        expected_template=args.template_repository,
                        branch=branch,
                        dry_run=args.dry_run,
                        template_ref=template_ref,
                        env=env,
                        workspace=workspace,
                        auto_merge=args.auto_merge,
                        requested_template_ref=args.template_ref,
                    )
            except RepositoryProcessError as exc:
                failures += 1
                result = Result(
                    repository.name_with_owner,
                    "failed",
                    str(exc),
                    exc.inventory,
                )
            except Exception as exc:  # continue the fleet, then fail the run loudly
                failures += 1
                result = Result(repository.name_with_owner, "failed", str(exc))
            if health_module is not None and result.inventory is not None:
                result = replace(result, health=health_module.collect_health(
                    repository.name_with_owner, repository.default_branch, template_ref,
                    branch, lambda endpoint: gh_json(["api", "--method", "GET", endpoint], env=env),
                    health_exceptions.get(repository.name_with_owner),
                ))
            results.append(result)

    for result in results:
        for line in console_lines(result):
            print(line, flush=True)
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    print("summary: " + ", ".join(f"{status}={count}" for status, count in sorted(counts.items())))
    for line in feature_summary(results):
        print(line)
    print(f"configuration-gaps: {configuration_gap_count(results)}")
    run = run_metadata("dry-run" if args.dry_run else "write", template_ref, env, args.template_ref, args.auto_merge)
    if args.markdown_report:
        args.markdown_report.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_report.write_text(markdown_report(results, counts, run), encoding="utf-8")
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(json_report(results, counts, run), encoding="utf-8")
    if failures:
        return 1
    if args.dry_run and counts.get("would-update", 0):
        print("template drift detected; run write mode from a CODEOWNER session", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

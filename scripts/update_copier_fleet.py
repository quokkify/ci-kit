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
import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Sequence

import yaml

ANSWERS_FILE = ".copier-answers.yml"
DEFAULT_BRANCH = "automation/copier-template-update"
DEFAULT_TEMPLATE_REPOSITORY = "quokkify/ci-kit"
RENAMED_TEMPLATE_REPOSITORIES = {"quokkify/project-toolkit": "quokkify/ci-kit"}
PR_TITLE = "chore(template): update shared project template"
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
MARKDOWN_FEATURE_LABELS = {
    "enabled": "✅ On",
    "disabled": "— Off",
    "missing": "⚠️ Missing",
    "custom": "🛠️ Custom",
    "unknown": "❔ Unknown",
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


def fetch_answers(repository: str, *, env: dict[str, str]) -> str | None:
    completed = run(
        [
            "gh",
            "api",
            f"repos/{repository}/contents/{ANSWERS_FILE}",
            "-H",
            "Accept: application/vnd.github.raw+json",
        ],
        env=env,
        check=False,
    )
    if completed.returncode == 0:
        return completed.stdout
    if "404" in completed.stderr or "Not Found" in completed.stderr:
        return None
    detail = completed.stderr.strip() or completed.stdout.strip()
    raise FleetUpdateError(f"cannot read {repository}/{ANSWERS_FILE}: {detail}")


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


def inventory_has_mismatch(inventory: TemplateInventory | None) -> bool:
    if inventory is None:
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
            f"renovate={FEATURE_LABELS[inventory.renovate]}"
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


def markdown_report(results: Sequence[Result], counts: dict[str, int]) -> str:
    current = counts.get("up-to-date", 0)
    drift = counts.get("would-update", 0)
    excluded = counts.get("excluded", 0)
    mismatches = configuration_gap_count(results)
    lines = [
        "## Copier fleet audit",
        "",
        f"Fleet: {len(results)} repositories",
        "",
        f"✅ {current} up-to-date · 🟡 {drift} drift · ⚠️ {mismatches} configuration gaps · ⏭️ {excluded} excluded",
        "",
        "| Repository | Sync | Template | Components | Baseline | Docker | CodeQL | Allure | Release Please | Renovate |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    status_labels = {
        "up-to-date": "✅ Current",
        "would-update": "🟡 Drift",
        "pull-request": "🔀 Pull request",
        "not-managed": "— Not managed",
        "foreign-template": "↗️ Foreign template",
        "excluded": "⏭️ Excluded",
        "failed": "❌ Failed",
    }
    for result in results:
        inventory = result.inventory
        if inventory:
            template_version = inventory.commit
            if inventory.target_commit and inventory.target_commit != inventory.commit:
                template_version += f" → {inventory.target_commit}"
            cells = (
                template_version,
                ", ".join(inventory.components),
                inventory.baseline,
                MARKDOWN_FEATURE_LABELS[inventory.docker],
                MARKDOWN_FEATURE_LABELS[inventory.codeql],
                MARKDOWN_FEATURE_LABELS[inventory.allure_report],
                MARKDOWN_FEATURE_LABELS[inventory.release_please],
                MARKDOWN_FEATURE_LABELS[inventory.renovate],
            )
        else:
            cells = ("—", "—", "—", "—", "—", "—", "—", "—")
        lines.append(
            "| "
            + " | ".join(
                markdown_escape(value)
                for value in (
                    result.repository,
                    status_labels.get(result.status, result.status),
                    *cells,
                )
            )
            + " |"
        )

    problem_results = [
        result
        for result in results
        if result.status in {"would-update", "failed"} or inventory_has_mismatch(result.inventory)
    ]
    if problem_results:
        lines.extend(["", "### Details", ""])
        for result in problem_results:
            lines.append(f"#### `{markdown_escape(result.repository)}`")
            if result.detail:
                lines.append(f"- {markdown_escape(result.detail)}")
            inventory = result.inventory
            if inventory and inventory.baseline != f"{len(BASELINE_PATHS)}/{len(BASELINE_PATHS)}":
                lines.append(f"- Baseline files present: {inventory.baseline}")
                lines.extend(f"  - Missing: `{path}`" for path in inventory.missing_baseline)
            if inventory and inventory.codeql == "missing":
                lines.append(f"- Missing enabled template output: `{FEATURE_PATHS['codeql']}`")
            if inventory and inventory.release_please == "missing":
                lines.append(f"- Missing enabled template output: `{FEATURE_PATHS['release_please']}`")
            if inventory and inventory.allure_report == "missing":
                lines.append(
                    "- Allure is enabled but its generated workflow/config/extractor set is incomplete: "
                    + ", ".join(f"`{path}`" for path in ALLURE_EXPECTED_PATHS)
                )
            if inventory and inventory.renovate == "missing":
                lines.append(f"- Missing enabled template output: `{FEATURE_PATHS['renovate']}`")
            lines.append("")
    health_results = [result for result in results if result.health is not None]
    if health_results:
        lines.extend(["", "### Adoption and branch health", "", "This read-only snapshot is independent of PR creation and template rendering.", "", "| Repository | Applied → target | Default revision / CI | Automation PR / CI | Active required checks | Exception |", "| --- | --- | --- | --- | --- | --- |"])
        for result in health_results:
            health = result.health or {}
            pr = health.get("pull_request")
            if pr:
                pr_text = f"{pr.get('url')} · {pr.get('state')} · {pr.get('age_days')}d · {pr.get('head_sha')} · {pr.get('ci', {}).get('status', 'unknown')}"
            else:
                pr_text = "none" if health.get("pull_request_observation", {}).get("status") == "observed" else "unknown"
            policy = health.get("required_checks", {})
            checks = ", ".join(str(item.get("context")) + (f" (app {item['integration_id']})" if item.get("integration_id") is not None else "") for item in policy.get("checks", []))
            exception = health.get("exception")
            exception_text = f"{exception.get('status')}: {exception.get('reason')} · expires {exception.get('expires_on')}" if exception else "none"
            cells = [result.repository, f"{health.get('applied_template_version')} → {health.get('target_template_version')} ({health.get('adoption')})", f"{health.get('default_sha')} · {health.get('default_ci', {}).get('status', 'unknown')}", pr_text, f"{policy.get('status', 'unknown')}: {checks or 'none observed'}", exception_text]
            lines.append("| " + " | ".join(health_markdown_escape(str(value)) for value in cells) + " |")
    return "\n".join(lines).rstrip() + "\n"


def health_markdown_escape(value: str) -> str:
    import html
    value = html.escape(sanitize_text(value)).replace("\\", "\\\\")
    for character in "|`*_[]()#!":
        value = value.replace(character, "\\" + character)
    return value


def json_report(results: Sequence[Result], counts: dict[str, int]) -> str:
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
            }
        if result.health is not None:
            item["health"] = result.health
        repositories.append(item)
    gaps = configuration_gap_count(results)
    payload = {
        "schema_version": 4,
        "summary": counts,
        "configuration_gaps": gaps,
        "configuration_mismatches": gaps,
        "repositories": repositories,
    }
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
    rejected = sorted(repository_path.rglob("*.rej"))
    if rejected:
        names = ", ".join(str(path.relative_to(repository_path)) for path in rejected)
        raise FleetUpdateError(f"Copier produced conflict files: {names}")
    return changed_paths(repository_path)


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

    Exact vMAJOR.MINOR.PATCH tags are rewritten to the new tag. A digest pin carrying a
    release comment, which is what Renovate writes, is rewritten to the new tag's commit
    together with its comment: the contract accepts a digest only while the comment still
    names the recorded toolkit_version, so leaving the comment behind is the same failure
    as leaving a stale tag behind, and neither this update nor the Renovate bump can pass
    that check on its own. A digest pin without a release comment is left alone, since
    nothing identifies which release it was meant to track.

    ``resolve_commit`` runs at most once, and only when a digest pin is present, so a
    fleet of tag-only consumers costs no extra API call.
    """
    changed: list[str] = []
    commit: str | None = None
    workflows = repository_path / ".github" / "workflows"
    for workflow in sorted(workflows.glob("*.yml")) + sorted(workflows.glob("*.yaml")):
        original = workflow.read_text(encoding="utf-8")
        updated = TOOLKIT_TAG_REFERENCE.sub(rf"\g<1>{template_ref}", original)
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
) -> None:
    run(["git", "switch", "-C", branch], cwd=repository_path, env=env)
    run(["git", "config", "user.name", "quokkify-copier[bot]"], cwd=repository_path)
    run(
        ["git", "config", "user.email", "quokkify-copier[bot]@users.noreply.github.com"],
        cwd=repository_path,
    )
    run(["git", "add", "--all"], cwd=repository_path)
    run(["git", "diff", "--cached", "--check"], cwd=repository_path)
    run(["git", "commit", "-m", PR_TITLE], cwd=repository_path)

    remote = run(
        [*authenticated_git(), "ls-remote", "--heads", "origin", f"refs/heads/{branch}"],
        cwd=repository_path,
        env=env,
    ).stdout.strip()
    push = [*authenticated_git(), "push"]
    if remote:
        remote_sha = remote.split()[0]
        push.append(f"--force-with-lease=refs/heads/{branch}:{remote_sha}")
    push.extend(["origin", f"HEAD:refs/heads/{branch}"])
    run(push, cwd=repository_path, env=env)


def ensure_pull_request(
    repository: Repository,
    *,
    branch: str,
    changed: Sequence[str],
    env: dict[str, str],
) -> str:
    existing = gh_json(
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
            "url",
        ],
        env=env,
    )
    if existing:
        return existing[0]["url"]

    body = (
        "## Summary\n\n"
        "Automated `copier update` from the shared "
        "[`quokkify/ci-kit`](https://github.com/quokkify/ci-kit) template.\n\n"
        "This keeps the organization baseline (validation, CodeQL, Gitleaks, "
        "Release Please, and Renovate) synchronized while leaving merge approval to maintainers.\n\n"
        "## Generated changes\n\n"
        + "\n".join(f"- `{path}`" for path in changed)
        + "\n\n## Verification\n\n"
        "- `copier update --trust --defaults --conflict=rej --skip-tasks`\n"
        "- Legacy components keep their `type`/`path`; the updater supplies stable `id`/`name` migration data.\n"
        "- `git diff --cached --check`\n\n"
        "Do not edit the automation branch directly; make project-specific changes after merging "
        "or adjust the Copier answers/template.\n"
    )
    completed = run(
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
    return completed.stdout.strip()


def process_repository(
    repository: Repository,
    *,
    expected_template: str,
    branch: str,
    dry_run: bool,
    template_ref: str | None,
    env: dict[str, str],
    workspace: Path,
) -> Result:
    raw_answers = fetch_answers(repository.name_with_owner, env=env)
    if raw_answers is None:
        return Result(repository.name_with_owner, "not-managed")

    source = normalize_template_source(parse_template_source(raw_answers))
    normalized_expected = normalize_template_source(expected_template)
    if normalized_expected is None or source != normalized_expected:
        return Result(repository.name_with_owner, "foreign-template", source or "unrecognized source")

    destination = workspace / repository.name_with_owner.replace("/", "--")
    clone_repository(repository, destination, env=env)
    cloned_answers_path = destination / ANSWERS_FILE
    if not is_regular_file(cloned_answers_path, repository_root=destination):
        raise FleetUpdateError(f"cloned {ANSWERS_FILE} must be a regular file")
    cloned_answers = cloned_answers_path.read_text(encoding="utf-8")
    cloned_source = normalize_template_source(parse_template_source(cloned_answers))
    if cloned_source != normalized_expected:
        raise FleetUpdateError("cloned repository changed to a different template during audit")
    inventory = inventory_from_answers(cloned_answers, destination)
    try:
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
        if not paths:
            return Result(repository.name_with_owner, "up-to-date", inventory=inventory)
        if dry_run:
            return Result(repository.name_with_owner, "would-update", ", ".join(paths), inventory)

        push_automation_branch(repository, destination, branch=branch, env=env)
        url = ensure_pull_request(repository, branch=branch, changed=paths, env=env)
        return Result(repository.name_with_owner, "pull-request", url, inventory)
    except Exception as exc:
        raise RepositoryProcessError(str(exc), inventory) from exc


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
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--write", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
    for command in ("copier", "gh", "git"):
        if shutil.which(command, path=env.get("PATH")) is None:
            print(f"required command is unavailable: {command}", file=sys.stderr)
            return 2

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
    if any(repository.name_with_owner.casefold() not in excluded for repository in repositories):
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
                result = process_repository(
                    repository,
                    expected_template=args.template_repository,
                    branch=args.branch,
                    dry_run=args.dry_run,
                    template_ref=template_ref,
                    env=env,
                    workspace=workspace,
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
                    args.branch, lambda endpoint: gh_json(["api", "--method", "GET", endpoint], env=env),
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
    if args.markdown_report:
        args.markdown_report.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_report.write_text(markdown_report(results, counts), encoding="utf-8")
    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(json_report(results, counts), encoding="utf-8")
    if failures:
        return 1
    if args.dry_run and counts.get("would-update", 0):
        print("template drift detected; run write mode from a CODEOWNER session", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

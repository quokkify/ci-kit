from __future__ import annotations

import re
from pathlib import Path

import yaml


def load_yaml_or_error(path: Path, errors: list[str], label: str) -> object | None:
    """Load YAML and record a validation error instead of raising."""
    try:
        return yaml.safe_load(path.read_text())
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        errors.append(f"{label}: YAML parse failed: {exc}")
        return None


def action_reference_errors(text: str, label: str, *, require_toolkit_pin: bool = False) -> list[str]:
    """Check immutable action refs while accepting legacy toolkit release tags."""
    errors: list[str] = []
    uses = re.compile(r"^\s*(?:-\s+)?uses:\s*([^\s#]+)(?:[ \t]+#([^\n]*))?", re.MULTILINE)
    for match in uses.finditer(text):
        use = match[1].strip("\"'")
        if use.startswith("./"):
            continue
        target, sep, ref = use.rpartition("@")
        if not sep:
            errors.append(f"{label}: action without ref: {use}")
            continue
        toolkit = target.startswith(("quokkify/ci-kit/.github/workflows/", "quokkify/ci-kit/actions/"))
        digest = bool(re.fullmatch(r"[0-9a-f]{40}", ref))
        release = bool(re.fullmatch(r"v\d+\.\d+\.\d+", (match[2] or "").strip()))
        if toolkit:
            legacy_tag = bool(re.fullmatch(r"v\d+\.\d+\.\d+", ref))
            if not ((digest and release) or (legacy_tag and not require_toolkit_pin)):
                errors.append(f"{label}: toolkit reference requires a full SHA with # vMAJOR.MINOR.PATCH" +
                              ("" if require_toolkit_pin else " or an exact release tag") + f": {use}")
        elif not digest:
            errors.append(f"{label}: external action is not SHA-pinned: {use}")
    return errors

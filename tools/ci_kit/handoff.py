from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Area:
    module: str
    legacy_paths: tuple[str, ...]


AREAS: dict[str, Area] = {
    "validate": Area(module="core", legacy_paths=(".github/workflows/validate.yml",)),
    "base": Area(module="core", legacy_paths=(
        ".github/workflows/copier-update.yml", "docs/ci-kit.md", "README.md", ".github/pull_request_template.md",
    )),
    "security": Area(module="security", legacy_paths=(".github/workflows/codeql.yml", ".github/workflows/gitleaks.yml")),
    "release": Area(module="release", legacy_paths=(
        ".github/workflows/release.yml", ".github/release-please/config.json",
        ".github/release-please/manifest.json", ".github/scripts/enrich_release_notes.py",
    )),
    "allure": Area(module="allure", legacy_paths=(
        ".github/workflows/allure-report.yml", ".github/allure/allurerc.mjs", ".github/allure/safe_extract.py",
    )),
    "renovate": Area(module="renovate", legacy_paths=(".github/renovate.json",)),
}


def area_for_module(module: str) -> str | None:
    for name, area in AREAS.items():
        if area.module == module:
            return name
    return None

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Area:
    module: str
    legacy_paths: tuple[str, ...]


AREAS: dict[str, Area] = {
    "validate": Area(module="core", legacy_paths=(".github/workflows/validate.yml",)),
}


def area_for_module(module: str) -> str | None:
    for name, area in AREAS.items():
        if area.module == module:
            return name
    return None

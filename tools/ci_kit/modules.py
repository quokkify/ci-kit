from __future__ import annotations

from typing import NamedTuple


class ModuleInfo(NamedTuple):
    requires: tuple[str, ...]
    job: str | None = None
    language: bool = False
    fields: tuple[str, ...] | None = None


MODULES: dict[str, ModuleInfo] = {
    "core": ModuleInfo((), "core-checks"),
    "java": ModuleInfo(("core",), "java", True),
    "python": ModuleInfo(("core",), "python", True),
    "node": ModuleInfo(("core",), "node", True),
    "docker": ModuleInfo(("core",), "docker", False, fields=("context", "dockerfile", "target")),
    "release": ModuleInfo(("core",)),
}

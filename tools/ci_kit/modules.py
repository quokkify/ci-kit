from __future__ import annotations

from typing import NamedTuple


class ModuleInfo(NamedTuple):
    requires: tuple[str, ...]
    job: str


MODULES: dict[str, ModuleInfo] = {
    "core": ModuleInfo((), "core-checks"),
    "java": ModuleInfo(("core",), "java"),
}

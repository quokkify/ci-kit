from __future__ import annotations


def module_answers(legacy: dict) -> dict[str, dict]:
    modules: dict[str, dict] = {}
    if legacy.get("release_please"):
        modules["release"] = {"release_mode": legacy["release_mode"]}
    return modules

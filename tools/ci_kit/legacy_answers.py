from __future__ import annotations

ALLURE_KEYS = (
    "allure_external_workflow_name", "allure_external_workflow_path", "allure_external_artifact_prefix",
    "allure_external_artifact_min_count", "allure_external_artifact_max_count", "allure_categories_file",
    "allure_publish_pages", "allure_pages_url", "allure_group_by", "project_name",
)
SECURITY_KEYS = (
    "codeql", "gitleaks", "codeql_languages", "codeql_aggregate", "gitleaks_job_name", "gitleaks_scan", "gitleaks_aggregate",
)
RENOVATE_LANGUAGE_PRESETS = {"python": "python", "node": "javascript", "java": "java"}


def legacy_renovate_presets(legacy: dict) -> list[str]:
    """Mirror the legacy renovate_presets default in copier.yml."""
    presets = ["default", "github-actions"]
    types = [component.get("type") for component in legacy.get("components") or []]
    presets += [preset for type_, preset in RENOVATE_LANGUAGE_PRESETS.items() if type_ in types]
    if legacy.get("docker"):
        presets.append("docker")
    return presets


def module_answers(legacy: dict) -> dict[str, dict]:
    modules: dict[str, dict] = {}
    if legacy.get("codeql") or legacy.get("gitleaks"):
        modules["security"] = {key: legacy[key] for key in SECURITY_KEYS if key in legacy}
    if legacy.get("release_please"):
        modules["release"] = {"release_mode": legacy["release_mode"]}
    if legacy.get("allure_report"):
        source = legacy.get("allure_source", "auto")
        if source == "auto":
            source = "component" if legacy.get("components") else "external"
        modules["allure"] = {"allure_source": source, **{key: legacy[key] for key in ALLURE_KEYS if key in legacy}}
    if legacy.get("renovate"):
        modules["renovate"] = {
            "renovate_presets": legacy.get("renovate_presets") or legacy_renovate_presets(legacy),
            **({"renovate_config_repository": legacy["renovate_config_repository"]} if "renovate_config_repository" in legacy else {}),
        }
    return modules

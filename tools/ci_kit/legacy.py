from __future__ import annotations

import json
from pathlib import Path

import yaml

from .handoff import AREAS, HANDOFF_GATED
from .plan import Finding

ANSWERS = ".copier-answers.yml"
REQUIRED_ANSWERS = ("_src_path", "_commit", "toolkit_version")
HEX = "0123456789abcdef"
LAST_LEGACY_VALIDATE = "v3.8.0"


def load_answers(repo: Path) -> tuple[dict | None, str | None]:
    path = Path(repo) / ANSWERS
    try:
        data = yaml.safe_load(path.read_text())
    except OSError as error:
        return None, f"cannot read {ANSWERS}: {error.strerror or error}"
    except (yaml.YAMLError, UnicodeDecodeError) as error:
        return None, f"cannot parse {ANSWERS}: {error}".splitlines()[0]
    if not isinstance(data, dict):
        return None, f"{ANSWERS} must be a YAML mapping"
    return data, None


def handoff_findings(
    repo: Path, legacy_answers: dict | None, modules: set[str], rendered: set[str] | None = None,
) -> list[Finding]:
    if legacy_answers is None:
        return []
    handed = legacy_answers.get("handed_off", [])
    known = sorted(AREAS)
    if not isinstance(handed, list) or any(not isinstance(a, str) or a not in AREAS for a in handed):
        return [Finding("CIK-HANDOFF-003", "legacy", f"handed_off must be a list of known areas ({', '.join(known)}); got {handed!r}", ANSWERS)]
    findings: list[Finding] = []
    contested: dict[str, list[str]] = {}
    for name in sorted(AREAS):
        area = AREAS[name]
        if name in handed:
            if area.module not in modules:
                findings.append(Finding("CIK-HANDOFF-001", area.module, f"area {name} is handed off but module {area.module} is not installed", ANSWERS))
            continue
        if area.module not in modules:
            continue
        # A gated area conflicts only when the module actually rendered one of its files.
        renders = rendered is not None and any(path in rendered for path in area.legacy_paths)
        if name not in HANDOFF_GATED or renders:
            contested.setdefault(area.module, []).append(name)
    for module in sorted(contested):
        areas = ", ".join(contested[module])
        findings.append(Finding(
            "CIK-HANDOFF-002", module,
            f"module {module} is installed but legacy still renders its area ({areas}); see docs/errors/CIK-LEGACY-006.md",
            ANSWERS,
        ))
    return findings


def _parse_json(repo: Path, rel: str) -> Finding | None:
    path = repo / rel
    if not path.is_file():
        return None
    try:
        json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, ValueError) as error:
        return Finding("CIK-LEGACY-005", "legacy", f"cannot parse {rel}: {error}".splitlines()[0], rel)
    return None


def _feature_findings(repo: Path, answers: dict) -> list[Finding]:
    def need(message: str, path: str) -> Finding:
        return Finding("CIK-LEGACY-004", "legacy", message, path)

    findings: list[Finding] = []
    if answers.get("renovate") and not (repo / ".github/renovate.json").is_file():
        findings.append(need("renovate=true requires .github/renovate.json", ".github/renovate.json"))
    if answers.get("codeql") and not (repo / ".github/workflows/codeql.yml").is_file():
        findings.append(need("codeql=true requires .github/workflows/codeql.yml", ".github/workflows/codeql.yml"))
    report = answers.get("allure_report")
    if report:
        allure_paths = (".github/workflows/allure-report.yml", ".github/allure/allurerc.mjs", ".github/allure/safe_extract.py")
        if any(not (repo / p).is_file() for p in allure_paths):
            findings.append(need("allure_report=true requires its generated workflow, config, and bounded extractor", ".github/workflows/allure-report.yml"))
        components = answers.get("components")
        source = answers.get("allure_source") or "auto"
        mode = source if source in ("component", "external") else ("component" if components else "external")
        if mode == "component" and not components:
            findings.append(need("allure_source=component requires at least one component", ANSWERS))
        if mode == "external":
            if not (repo / str(answers.get("allure_external_workflow_path", ""))).is_file():
                findings.append(need("external Allure reporting requires its configured source workflow", ANSWERS))
            low = answers.get("allure_external_artifact_min_count")
            high = answers.get("allure_external_artifact_max_count")
            if not isinstance(low, int) or not isinstance(high, int) or not 1 <= low <= high <= 50:
                findings.append(need("external Allure artifact counts must satisfy 1 <= minimum <= maximum <= 50", ANSWERS))
    categories = answers.get("allure_categories_file")
    if categories and not (repo / str(categories)).is_file():
        findings.append(need("configured Allure categories file does not exist", str(categories)))
    if answers.get("allure_publish_pages") and not report:
        findings.append(need("allure_publish_pages=true requires allure_report=true", ANSWERS))
    return findings


def _ref_findings(repo: Path, rel: str, text: str, toolkit: str) -> list[Finding]:
    findings: list[Finding] = []
    marker = "uses: quokkify/ci-kit/"
    for line in text.splitlines():
        if not line.lstrip().startswith(marker) or "@" not in line:
            continue
        parts = line.split("@", 1)[1].split()
        reference = parts[0] if parts else ""
        comment = line.partition("#")[2].strip()
        pinned = len(reference) == 40 and all(c in HEX for c in reference) and comment == toolkit
        if reference != toolkit and not pinned:
            findings.append(Finding("CIK-LEGACY-003", "legacy", f"{rel} uses toolkit {reference}, expected {toolkit}", rel))
    return findings


def legacy_findings(repo: Path, module_owned: set[str]) -> list[Finding]:
    repo = Path(repo)
    answers, problem = load_answers(repo)
    if answers is None:
        return [Finding("CIK-LEGACY-001", "legacy", problem or f"cannot read {ANSWERS}", ANSWERS)]
    findings: list[Finding] = []
    missing = [key for key in REQUIRED_ANSWERS if not answers.get(key)]
    if missing:
        findings.append(Finding("CIK-LEGACY-001", "legacy", f"{ANSWERS} lacks {', '.join(missing)}", ANSWERS))
    toolkit = answers.get("toolkit_version")
    if answers.get("_commit") and toolkit and answers["_commit"] != toolkit:
        findings.append(Finding("CIK-LEGACY-002", "legacy", f"Copier _commit {answers['_commit']} must match toolkit_version {toolkit}", ANSWERS))
    handed = answers.get("handed_off", [])
    if isinstance(handed, list) and all(isinstance(a, str) and a in AREAS for a in handed) and "validate" not in handed:
        findings.append(Finding(
            "CIK-LEGACY-006", "legacy",
            "validate is not handed off; the legacy template no longer renders validate.yml. "
            f"Migrate with ci-kit {LAST_LEGACY_VALIDATE}, see docs/errors/CIK-LEGACY-006.md",
            ANSWERS,
        ))
    findings.extend(_feature_findings(repo, answers))
    for rel in (".github/renovate.json", ".github/release-please/config.json", ".github/release-please/manifest.json"):
        if rel in module_owned:
            continue
        found = _parse_json(repo, rel)
        if found:
            findings.append(found)
    for path in sorted((repo / ".github/workflows").glob("*.yml")):
        rel = path.relative_to(repo).as_posix()
        if rel in module_owned:
            continue
        try:
            text = path.read_text()
            yaml.safe_load(text)
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
            findings.append(Finding("CIK-LEGACY-005", "legacy", f"cannot parse {rel}: {error}".splitlines()[0], rel))
            continue
        if toolkit:
            findings.extend(_ref_findings(repo, rel, text, str(toolkit)))
    return findings

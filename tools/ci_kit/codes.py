from __future__ import annotations

CODES: dict[str, str] = {
    "CIK-CORE-001": "Module declaration schema version is not supported",
    "CIK-CORE-002": "Module answers file and declaration are not paired",
    "CIK-CORE-003": "Module requires a module that is not installed",
    "CIK-CORE-004": "Module declaration is unparseable or violates its schema",
    "CIK-CORE-005": "Component id is declared more than once, or a component name is used more than once in a module",
    "CIK-CORE-010": "Module declaration drifted from its rendered template",
    "CIK-CORE-011": "Module-owned file drifted from its rendered template",
    "CIK-CORE-012": "toolkit_version or _commit differs between answers files",
    "CIK-CORE-020": "Gate found a failing or missing module job",
    "CIK-CORE-030": "Legacy answers still render files",
    "CIK-UPDATE-001": "Module update failed or left conflicts",
}

from __future__ import annotations

CODES: dict[str, str] = {
    "CIK-CORE-001": "Module declaration schema version is not supported",
    "CIK-CORE-002": "Module answers file and declaration are not paired",
    "CIK-CORE-003": "Module requires a module that is not installed",
    "CIK-CORE-004": "Module declaration is unparseable or violates its schema",
    "CIK-CORE-005": "Component id is declared more than once, or a component name is used more than once in a module",
    "CIK-CORE-010": "Module declaration drifted from its rendered template",
    "CIK-CORE-011": "Module-owned file drifted from its rendered template",
    "CIK-CORE-020": "Gate found a failing or missing module job",
    "CIK-HANDOFF-001": "Area is handed off but its owner module is not installed",
    "CIK-HANDOFF-002": "Module is installed but its area is not handed off",
    "CIK-HANDOFF-003": "handed_off is malformed or names an unknown area",
    "CIK-LEGACY-001": "Legacy answers file is unreadable or lacks required keys",
    "CIK-LEGACY-002": "Legacy Copier commit does not match toolkit_version",
    "CIK-LEGACY-003": "Legacy workflow references a different toolkit ref",
    "CIK-LEGACY-004": "Enabled legacy feature lacks its generated files",
    "CIK-LEGACY-005": "Legacy-owned JSON or YAML file does not parse",
    "CIK-UPDATE-001": "Module update failed or left conflicts",
}

from __future__ import annotations

CODES: dict[str, str] = {
    "CIK-CORE-001": "Module declaration schema version is not supported",
    "CIK-CORE-002": "Module answers file and declaration are not paired",
    "CIK-CORE-003": "Module requires a module that is not installed",
    "CIK-CORE-004": "Module declaration is unparseable or violates its schema",
    "CIK-CORE-010": "Module declaration drifted from its rendered template",
    "CIK-CORE-011": "Module-owned file drifted from its rendered template",
    "CIK-CORE-020": "Gate found a failing or missing module job",
}

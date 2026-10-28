from __future__ import annotations

CODES: dict[str, str] = {
    "CIK-ADD-001": "Module cannot be added: the working tree is not clean",
    "CIK-ADD-002": "Module cannot be added: it is unknown",
    "CIK-ADD-003": "Module cannot be added: a required module is missing, at another ref, or has an untrusted source",
    "CIK-ADD-004": "Module cannot be added: a file it renders already exists",
    "CIK-ADD-005": "Module add failed and was rolled back",
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

#!/usr/bin/env python3
"""Render current template scenarios into runtime-only Actions CodeQL evidence."""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile

from copier import run_copy
import yaml

ROOT = Path(__file__).resolve().parents[1]


def render_workflows(root: Path = ROOT) -> list[Path]:
    scenarios = sorted((root / "tests/scenarios").glob("*.yml"))
    if not scenarios:
        raise ValueError("no template scenarios found")
    target = root / ".github/workflows"
    evidence: dict[Path, bytes] = {}
    with tempfile.TemporaryDirectory(prefix="codeql-template-") as temporary:
        temporary_root = Path(temporary)
        # A non-VCS source preserves the exact checked-out files, including local
        # changes. Copier must not silently render a previous committed template.
        source = temporary_root / "source"
        source.mkdir()
        shutil.copy2(root / "copier.yml", source / "copier.yml")
        shutil.copytree(root / "templates", source / "templates")
        for scenario in scenarios:
            destination = temporary_root / scenario.stem
            run_copy(
                str(source), destination,
                data=yaml.safe_load(scenario.read_text()),
                defaults=True, quiet=True, skip_tasks=True,
            )
            workflows = sorted((destination / ".github/workflows").glob("*.yml"))
            if not workflows:
                raise ValueError(f"{scenario.name}: no workflows rendered")
            for workflow in workflows:
                content = workflow.read_bytes()
                parsed = yaml.safe_load(content)
                if not isinstance(parsed, dict) or not isinstance(parsed.get("jobs"), dict):
                    raise ValueError(f"{scenario.name}/{workflow.name}: invalid workflow YAML")
                path = target / f"codeql-generated-{scenario.stem}-{workflow.name}"
                if path.exists():
                    raise FileExistsError(f"refusing to overwrite workflow: {path}")
                evidence[path] = content
    for path, content in evidence.items():
        path.write_bytes(content)
    return sorted(evidence)


if __name__ == "__main__":
    print(f"Rendered {len(render_workflows())} workflows for CodeQL Actions analysis")

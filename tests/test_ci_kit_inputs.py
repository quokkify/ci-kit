from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.inputs import WORKFLOWS, extract_inputs, field_name, input_name, load_inputs


class WorkflowInputsTest(unittest.TestCase):
    def test_table_matches_workflows(self):
        root = Path(__file__).resolve().parents[1] / ".github" / "workflows"
        self.assertEqual(
            load_inputs(),
            {name: extract_inputs(root / f"{name}.yml") for name in WORKFLOWS.values()},
            msg="regenerate: python -c \"import json,sys; sys.path.insert(0,'tools'); from pathlib import Path; from ci_kit.inputs import WORKFLOWS, extract_inputs; print(json.dumps({n: extract_inputs(Path('.github/workflows')/f'{n}.yml') for n in WORKFLOWS.values()}, indent=2, sort_keys=True))\" > tools/ci_kit/workflow_inputs.json",
        )

    def test_python_defaults(self):
        table = load_inputs()["python-ci"]
        self.assertEqual(table["python-version"], {"type": "string", "default": "3.14"})
        self.assertEqual(table["run-build"], {"type": "boolean", "default": False})

    def test_names_round_trip(self):
        self.assertEqual(field_name("run-build"), "run_build")
        self.assertEqual(input_name("run_build"), "run-build")


if __name__ == "__main__":
    unittest.main()

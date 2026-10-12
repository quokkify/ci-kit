from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from ci_kit.codes import CODES


def source_text_of(directory: Path) -> str:
    return "\n".join(p.read_text() for p in sorted(directory.rglob("*.py")))


class CodeRegistryTests(unittest.TestCase):
    def test_every_code_has_runbook_and_every_runbook_has_code(self):
        docs = {p.stem for p in (ROOT / "docs/errors").glob("CIK-*.md")}
        self.assertEqual(set(CODES), docs)

    def test_every_code_used_in_sources_is_registered(self):
        used = set(re.findall(r"CIK-[A-Z]+-\d{3}", source_text_of(ROOT / "tools/ci_kit")))
        self.assertLessEqual(used, set(CODES))

    def test_runbooks_have_required_sections(self):
        for code in CODES:
            text = (ROOT / "docs/errors" / f"{code}.md").read_text()
            for heading in ("## Cause", "## Fix", "## Example"):
                with self.subTest(code=code, heading=heading):
                    self.assertIn(heading, text)


if __name__ == "__main__":
    unittest.main()

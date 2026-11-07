import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest import TestCase

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/pr-contract.yml"
CALLER = ROOT / "modules/core/template/.github/workflows/pr-contract.yml.jinja"
spec = importlib.util.spec_from_file_location("enrich_release_notes", ROOT / "scripts/enrich_release_notes.py")
assert spec and spec.loader
notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notes)

NOTE = "## Release note\n\nYou can now upload one results directory per component.\n"
MIGRATION = "## Migration\n\n1. Rename `x` to `y`.\n"


def event(title: str, body: str = "", *, labels: tuple[str, ...] = (), login: str = "octocat",
          user_type: str = "User", branch: str = "feat/x") -> dict[str, Any]:
    return {"pull_request": {
        "title": title,
        "body": body,
        "labels": [{"name": name} for name in labels],
        "user": {"login": login, "type": user_type},
        "head": {"ref": branch},
    }}


class PrContractTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        cls.workflow = workflow
        cls.step = workflow["jobs"]["contract"]["steps"][0]

    def run_check(self, payload: dict[str, Any], *, enforce: bool = False) -> tuple[int, str, str]:
        self.assertEqual(self.step["shell"], "python")
        with tempfile.TemporaryDirectory(prefix="pr-contract-") as temporary:
            event_path = Path(temporary) / "event.json"
            summary_path = Path(temporary) / "summary.md"
            event_path.write_text(json.dumps(payload), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-I", "-c", self.step["run"]],
                env={
                    "GITHUB_EVENT_PATH": str(event_path),
                    "GITHUB_STEP_SUMMARY": str(summary_path),
                    "ENFORCE": "true" if enforce else "false",
                },
                cwd=temporary, capture_output=True, text=True, check=False,
            )
            summary = summary_path.read_text(encoding="utf-8") if summary_path.exists() else ""
        return result.returncode, result.stdout + result.stderr, summary

    def assert_contract(self, payload: dict[str, Any], *, errors: tuple[str, ...] = (), warnings: tuple[str, ...] = (),
                        notices: tuple[str, ...] = ()) -> str:
        code, output, _ = self.run_check(payload, enforce=True)
        self.assertEqual(code, 1 if errors else 0, output)
        for kind, titles in (("error", errors), ("warning", warnings), ("notice", notices)):
            reported = {line.split("::", 2)[1].split("title=", 1)[1] for line in output.splitlines()
                        if line.startswith(f"::{kind} ")}
            self.assertEqual(reported, set(titles), output)
        return output

    def test_contract_cases(self):
        cases = {
            "feat with release note": (event("feat(allure): upload one directory per component", NOTE), {}),
            "feat without release note": (
                event("feat(allure): upload one directory per component", "## Summary\nlong"),
                {"errors": ("Release note missing",)},
            ),
            "legacy highlight counts": (event("feat(core): add x", "## Highlight\nUsers get x."), {}),
            "comment-only release note": (
                event("perf(core): faster x", "## Release note\n<!-- guidance -->\n"),
                {"errors": ("Release note missing",)},
            ),
            "fenced heading is not a section": (
                event("feat(core): add x", "## Usage example\n```md\n## Release note\ntext\n```\n"),
                {"errors": ("Release note missing",)},
            ),
            "fix without note is a notice": (event("fix(core): stop the crash on start"), {"notices": ("Release note suggested",)}),
            "chore needs nothing": (event("chore(ci-kit): update the shared template"), {}),
            "title-only label": (event("feat(core): add x", labels=("release-notes: title-only",)), {}),
            "internal label": (event("feat(core): add x", labels=("release-notes: internal",)), {}),
            "breaking needs both": (
                event("feat(api)!: drop v1 endpoints"),
                {"errors": ("Release note missing", "Migration missing")},
            ),
            "breaking with alias heading": (event("fix(api)!: drop v1 endpoints", NOTE + "## Breaking change\n\nUse v2.\n"), {}),
            "breaking label does not waive migration": (
                event("feat(api)!: drop v1", NOTE, labels=("release-notes: title-only",)),
                {"errors": ("Migration missing",)},
            ),
            "missing scope": (event("feat: add x", NOTE), {"errors": ("PR title",)}),
            "unknown type": (event("feature(core): add x", NOTE), {"errors": ("PR title",)}),
            "long technical title": (
                event("fix(core): handle the edge case in safe_extract.py when the parse_args() call gets odd input", NOTE),
                {"warnings": ("Long title", "Technical title")},
            ),
            "long note": (
                event("feat(core): add x", "## Release note\n\n" + "word " * 70 + "\n\n- list\n"),
                {"warnings": ("Long release note",)},
            ),
            "unsafe markup": (
                event("feat(core): add x", "## Release note\nSee <details><summary>x 1.2.3</summary>.\n"),
                {"warnings": ("Unsafe markup",)},
            ),
        }
        for name, (payload, expected) in cases.items():
            with self.subTest(name):
                self.assert_contract(payload, **expected)

    def test_bots_are_exempt(self):
        for payload in (
            event("update dependency x", login="renovate[bot]", user_type="Bot"),
            event("chore(main): release 1.2.0", branch="release-please--branches--main"),
            event("chore: bump", branch="ci-kit/update-v4.3.0"),
            event("chore: bump", login="dependabot[bot]"),
        ):
            with self.subTest(payload=payload["pull_request"]["head"]["ref"]):
                output = self.assert_contract(payload, notices=("PR contract",))
                self.assertIn("Skipped", output)

    def test_warn_mode_reports_errors_as_warnings_and_passes(self):
        code, output, summary = self.run_check(event("feat(api)!: drop v1"))
        self.assertEqual(code, 0, output)
        self.assertIn("::warning title=Release note missing::", output)
        self.assertIn("::warning title=Migration missing::", output)
        self.assertNotIn("::error", output)
        self.assertIn("Warn mode", summary)

    def test_failure_message_is_the_fill_in_instruction(self):
        _, output, summary = self.run_check(event("feat(core): add x"), enforce=True)
        line = next(line for line in output.splitlines() if line.startswith("::error title=Release note missing::"))
        self.assertIn("%0A%0A## Release note%0A%0A<one paragraph", line)
        self.assertNotIn("\r", line)
        self.assertIn("## Release note\n\n<one paragraph, at most 300 characters", summary)
        self.assertIn('label "release-notes: title-only"', summary)

    def test_section_parser_matches_the_release_notes_helper(self):
        namespace: dict[str, Any] = {"__name__": "contract"}
        script = self.step["run"].replace("sys.exit(main())", "")
        exec(compile(script, "pr-contract", "exec"), namespace)
        sections = namespace["sections"]
        corpus = [
            NOTE + MIGRATION,
            "## Release note\n<!-- only a comment -->\n## Migration\nStep.\n",
            "## Usage example\n````md\n```java\n## Migration\n```\n````\n## Migration\nApply.\n",
            "## Highlight\nLegacy.\n## Breaking changes\nOld alias.\n## Checklist\n- [ ] x\n",
            "## Release note\nfirst\n## Release note\nduplicate\n",
            "## Description\nReviewer only.\n",
        ]
        for body in corpus:
            with self.subTest(body=body[:30]):
                helper = notes.extract_rich_sections(body)
                published = {
                    "release note" if key == "highlight" else key: notes._neutralize(value)
                    for key, value in helper.items()
                    if notes._neutralize(value)
                }
                if "highlight" in helper and "release note" in helper:
                    published["release note"] = notes._neutralize(helper["release note"])
                self.assertEqual(set(sections(body)), set(published))

    def test_workflow_reads_only_the_event_payload(self):
        job = self.workflow["jobs"]["contract"]
        self.assertEqual(self.workflow["permissions"], {})
        self.assertEqual(job["permissions"], {})
        self.assertEqual(len(job["steps"]), 1)
        self.assertNotIn("uses", job["steps"][0])
        self.assertNotIn("${{", job["steps"][0]["run"])
        trigger = self.workflow.get("on", self.workflow.get(True))
        self.assertEqual(trigger["workflow_call"]["inputs"]["enforce"], {
            "description": "Fail the check on contract errors; false reports them as warnings.",
            "type": "boolean", "default": False,
        })

    def test_core_caller_runs_on_edits_and_label_changes_in_warn_mode(self):
        caller = yaml.safe_load(CALLER.read_text(encoding="utf-8").replace("{% raw %}", "").replace("{% endraw %}", "")
                                .replace("{{ toolkit_version }}", "v9.9.9"))
        trigger = caller.get("on", caller.get(True))
        self.assertEqual(
            set(trigger["pull_request"]["types"]),
            {"opened", "edited", "synchronize", "reopened", "ready_for_review", "labeled", "unlabeled"},
        )
        self.assertNotIn("pull_request_target", trigger)
        self.assertEqual(caller["permissions"], {})
        self.assertEqual(caller["jobs"]["contract"]["uses"], "quokkify/ci-kit/.github/workflows/pr-contract.yml@v9.9.9")
        self.assertEqual(caller["jobs"]["contract"]["with"], {"enforce": False})

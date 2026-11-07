import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from unittest import TestCase, mock

import yaml
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/release-notes"
SAMPLES = {
    "ci-kit-534": "quokkify/ci-kit",
    "q4j-755": "quokkify/q4j",
    "archgate-39": "quokkify/archgate",
    "java-test-automation-template-20": "quokkify/java-test-automation-template",
}
spec = importlib.util.spec_from_file_location("enrich_release_notes", ROOT / "scripts/enrich_release_notes.py")
assert spec and spec.loader
notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notes)

REPO = "acme/widget"
LINK = notes.pull_link(REPO)
VERSION_TRIPLE = re.compile(r"\d+\.\d+\.\d+")


def rp_line(number: int, text: str, *, commit: bool = True) -> str:
    """One Release Please changelog bullet with its canonical PR (and commit) links."""
    line = f"* {text} ([#{number}](https://github.com/{REPO}/issues/{number}))"
    if commit:
        sha = f"{number:x}".ljust(40, "a")[:40]
        line += f" ([{sha[:7]}](https://github.com/{REPO}/commit/{sha}))"
    return line


def changelog(*sections: tuple[str, list[str]], older: bool = True) -> str:
    text = f"# Changelog\n\n## [1.1.0](https://github.com/{REPO}/compare/v1.0.0...v1.1.0) (2026-11-07)\n\n"
    for title, lines in sections:
        text += f"\n### {title}\n\n" + "\n".join(lines) + "\n\n"
    if older:
        text += "## [1.0.0](https://github.com/acme/widget/compare/v0.9.0...v1.0.0) (2026-10-01)\n\n* old entry\n"
    return text


def body_for(changelog_text: str) -> str:
    _, _, top = notes.top_section(changelog_text)
    return (
        ":robot: I have created a release *beep* *boop*\n---\n\n\n" + top.rstrip() + "\n\n---\n"
        "This PR was generated with [Release Please](https://github.com/googleapis/release-please).\n"
    )


def render(text: str, sources: dict[int, dict[str, Any]], **kwargs: Any) -> str:
    return notes.render_changelog(text, REPO, sources, **kwargs)[1]


def load_sample(name: str) -> tuple[str, str, dict[int, dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    directory = FIXTURES / name
    config = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    sources = {
        int(number): value
        for number, value in json.loads((directory / "sources.json").read_text(encoding="utf-8")).items()
    }
    hidden = json.loads((directory / "hidden-commits.json").read_text(encoding="utf-8"))
    return (
        (directory / "CHANGELOG.md").read_text(encoding="utf-8"),
        (directory / "release-pr-body.md").read_text(encoding="utf-8"),
        sources,
        hidden,
        notes._section_types(config["changelog-sections"]),
    )


def render_sample(name: str) -> tuple[str, str, str]:
    """Return (changelog, release PR body, notes) the way --prepare renders one sample."""
    text, body, sources, hidden, types = load_sample(name)
    updated, rendered = notes.render_changelog(text, SAMPLES[name], sources, section_types=types, hidden=hidden)
    heading = notes.top_section(updated)[2].split("\n", 1)[0]
    return updated, notes.replace_body_notes(body, heading, rendered), rendered


class SectionTests(TestCase):
    def test_published_sections_and_aliases(self):
        sections = notes.extract_rich_sections(
            "## Summary\nreview only\n## Release note\nYou can now X.\n## Release notes\nignored\n"
            "## Breaking change\nRemove Y.\n## Usage example\n```yaml\na: 1\n```\n## Checklist\n- [ ] x\n"
        )
        self.assertEqual(
            sections,
            {"release note": "You can now X.", "migration": "Remove Y.", "usage example": "```yaml\na: 1\n```"},
        )

    def test_migration_and_breaking_change_headings_are_both_kept(self):
        sections = notes.extract_rich_sections("## Migration\nStep one.\n## Breaking changes\nStep two.\n")
        self.assertEqual(sections["migration"], "Step one.\n\nStep two.")

    def test_extracts_sections_and_preserves_java_fence(self):
        body = "## Description\r\ninternal\r\n## Usage example\r\n```java\r\nVerifier.verify();\r\n```\r\n## Migration\r\n<!-- guidance -->\r\n"
        sections = notes.extract_rich_sections(body)
        self.assertIn("```java\nVerifier.verify();\n```", sections["usage example"])
        self.assertNotIn("migration", sections)

    def test_trailing_whitespace_is_dropped_so_the_changelog_passes_diff_check(self):
        body = "## Release note\nBefore:  \n```text\nArchitectureRunner - \t\n```\n"
        self.assertEqual(notes.extract_rich_sections(body)["release note"], "Before:\n```text\nArchitectureRunner -\n```")
        text = changelog(("Features", [rp_line(7, "report")]))
        updated, _ = notes.render_changelog(text, REPO, {7: {"title": "feat(x): report", "body": body}})
        self.assertEqual([line for line in updated.splitlines() if line != line.rstrip()], [])

    def test_comments_and_unknown_or_duplicate_headings_are_ignored(self):
        body = "## Highlight\n<!-- only guidance -->\n## Highlight\nsecond\n## Other\nno\n"
        self.assertEqual(notes.extract_rich_sections(body), {})

    def test_headings_inside_fences_are_not_sections_or_versions(self):
        body = "## Usage example\n```java\n## Migration\n```\nAfter\n"
        self.assertIn("```java\n## Migration\n```\nAfter", notes.extract_rich_sections(body)["usage example"])
        self.assertNotIn("migration", notes.extract_rich_sections(body))
        text = "## 1.0.0\n```md\n## fake\n```\n### Features\n- x\n## 0.9.0\n"
        self.assertEqual(len(notes._version_ranges(text)), 2)

    def test_nested_fences_require_matching_outer_length(self):
        body = "## Usage example\n````markdown\n```java\n## Migration\n```\n````\n## Migration\nApply it.\n"
        sections = notes.extract_rich_sections(body)
        self.assertIn("usage example", sections)
        self.assertIn("migration", sections)
        text = "## 1.0.0\n````md\n## fake\n```\n````\n## 0.9.0\n"
        self.assertEqual(len(notes._version_ranges(text)), 2)

    def test_untouched_pull_request_template_contributes_no_release_notes(self):
        template = (ROOT / "modules/core/template/.github/pull_request_template.md.jinja").read_text(encoding="utf-8")
        body = Environment(undefined=StrictUndefined).from_string(template).render()
        for heading in ("Release note", "Usage example", "Migration", "Description"):
            self.assertIn(f"## {heading}\n", body)
        self.assertLess(body.index("## Release note"), body.index("## Description"))
        self.assertEqual(notes.extract_rich_sections(body), {})
        # Regression guard: the reviewer sections must never leak into release notes.
        filled = body.replace("## Migration\n", "## Migration\nUpgrade steps.\n")
        migration = notes.extract_rich_sections(filled)["migration"]
        self.assertTrue(migration.startswith("Upgrade steps."))
        for leaked in ("Checklist", "[ ]", "For reviewers"):
            self.assertNotIn(leaked, migration)
        self.assertEqual(notes._neutralize(migration), "Upgrade steps.")


class RenovateTests(TestCase):
    def test_linked_change_cells_and_extra_columns(self):
        body = (
            "| Package | Change | [Age](https://x) |\n|---|---|---|\n"
            "| [renovate](https://renovatebot.com) ([source](https://x)) "
            "| [`44.190.2` → `44.192.2`](https://octochangelog.com/x) | ![age](https://x) |\n"
        )
        self.assertEqual(notes.renovate_rows(body), [("renovate", "44.190.2", "44.192.2")])

    def test_grouped_table_keeps_digest_rows_and_ignores_later_tables(self):
        body = (
            "| Package | Update | Change |\n|---|---|---|\n"
            "| [selenium/hub](https://x) | minor | `4.50.0-20261010` → `4.51.0-20261010` |\n"
            "| [selenium/standalone-chromium](https://x) | digest | `2bc5a3e` → `ba9dde7` |\n"
            "| [selenium/hub](https://x) | minor | `4.50.0-20261010` → `4.51.0-20261010` |\n\n"
            "| Components | Package | Change |\n|---|---|---|\n| x | other | `1.0.0` → `2.0.0` |\n"
        )
        self.assertEqual(
            notes.renovate_rows(body),
            [
                ("selenium/hub", "4.50.0-20261010", "4.51.0-20261010"),
                ("selenium/standalone-chromium", "2bc5a3e", "ba9dde7"),
            ],
        )

    def test_hostile_cells_are_rejected_and_rows_are_capped(self):
        hostile = (
            "| Package | Change |\n|---|---|\n"
            "| <script>x</script> | `1.0.0` → `2.0.0` |\n"
            "| ok | `1.0.0` → `<img src=x>` |\n"
            "| [x](y) | not a change |\n"
        )
        self.assertEqual(notes.renovate_rows(hostile), [])
        many = "| Package | Change |\n|---|---|\n" + "".join(
            f"| pkg{index} | `1.0.{index}` → `1.0.{index + 1}` |\n" for index in range(80)
        )
        self.assertEqual(len(notes.renovate_rows(many)), notes.MAX_TABLE_ROWS)

    def _entry(self, number: int, title: str, body: str = "", merged_at: str = "") -> Any:
        return notes.make_entry(number, {"title": title, "body": body, "merged_at": merged_at})

    def test_from_is_the_earliest_and_to_the_latest_bump(self):
        table = "| Package | Change |\n|---|---|\n| [q4j](https://x) | `{old}` → `{new}` |\n"
        entries = [
            self._entry(26, "fix(deps): update q4j to v0.12.2", table.format(old="0.12.1", new="0.12.2"), "2026-11-03"),
            self._entry(18, "fix(deps): update q4j to v0.10.0", table.format(old="0.9.0", new="0.10.0"), "2026-11-01"),
            self._entry(21, "fix(deps): update q4j to v0.11.0", table.format(old="0.10.0", new="0.11.0"), "2026-11-02"),
        ]
        lines, rest = notes.dependency_lines(entries, lambda number: f"#{number}")
        self.assertEqual(rest, [])
        self.assertEqual(lines, ["- q4j 0.9.0 → 0.12.2 (#18, #21, #26)"])

    def test_digest_update_folds_into_the_version_bump_of_the_same_image(self):
        entries = [
            self._entry(
                767, "deps(docker-compose): update mongo:9.0.2 docker digest to f5c6240",
                "| Package | Update | Change |\n|---|---|---|\n| [mongo](https://x) | digest | `bac22ea` → `f5c6240` |\n",
            ),
            self._entry(
                768, "deps(docker-compose): update mongo docker tag to v9.0.4",
                "| Package | Update | Change |\n|---|---|---|\n| [mongo](https://x) | patch | `9.0.2` → `9.0.4` |\n",
            ),
        ]
        lines, _ = notes.dependency_lines(entries, lambda number: f"#{number}")
        self.assertEqual(lines, ["- mongo 9.0.2 → 9.0.4 (#767, #768)"])

    def test_digest_only_update_keeps_the_digests(self):
        entry = self._entry(
            9, "deps(github-actions): update actions/checkout digest to 33d7b85",
            "| Package | Change |\n|---|---|\n| actions/checkout | `9b32540` → `33d7b85` |\n",
        )
        self.assertEqual(
            notes.dependency_lines([entry], lambda number: f"#{number}")[0],
            ["- actions/checkout 9b32540 → 33d7b85 (#9)"],
        )

    def test_major_bumps_are_flagged(self):
        table = "| Package | Change |\n|---|---|\n| [{name}](https://x) | `{old}` → `{new}` |\n"
        entries = [
            self._entry(27, "deps(gradle): update io.github.classgraph:classgraph to v5",
                        table.format(name="io.github.classgraph:classgraph", old="4.8.199", new="5.0.2")),
            self._entry(28, "deps(github-actions): update actions/setup-java action to v6",
                        table.format(name="actions/setup-java", old="v5.2.0", new="v6.0.0")),
            self._entry(29, "deps(deps): update python to 3.15", table.format(name="python", old="3.14", new="3.15")),
            self._entry(30, "deps: update q4j to v0.12.0", table.format(name="q4j", old="0.9.0", new="0.12.0")),
        ]
        lines, _ = notes.dependency_lines(entries, lambda number: f"#{number}")
        self.assertEqual(lines, [
            "- actions/setup-java v5.2.0 → v6.0.0 · major (#28)",
            "- io.github.classgraph:classgraph 4.8.199 → 5.0.2 · major (#27)",
            "- python 3.14 → 3.15 (#29)",
            "- q4j 0.9.0 → 0.12.0 (#30)",
        ])

    def test_internal_list_joins_packages_with_a_shared_prefix_and_the_same_change(self):
        table = "| Package | Change |\n|---|---|\n" + "".join(
            f"| {name} | `{old}` → `{new}` |\n" for name, old, new in (
                ("selenium/hub", "4.50.0", "4.51.0"), ("selenium/node-docker", "4.50.0", "4.51.0"),
                ("selenium/standalone-chromium", "2bc5a3e", "ba9dde7"), ("gradle", "9.8.1", "9.8.2"),
                ("maven", "9.8.1", "9.8.2"),
            )
        )
        entry = self._entry(761, "deps(docker-compose): update selenium grid images", table)
        compact, _ = notes.dependency_lines([entry], lambda number: f"#{number}", compact=True)
        self.assertEqual(compact, [
            "- gradle 9.8.1 → 9.8.2 (#761)",
            "- maven 9.8.1 → 9.8.2 (#761)",
            "- selenium/hub, selenium/node-docker 4.50.0 → 4.51.0 (#761)",
            "- selenium/standalone-chromium 2bc5a3e → ba9dde7 (#761)",
        ])
        visible, _ = notes.dependency_lines([entry], lambda number: f"#{number}")
        self.assertEqual(len(visible), 5)

    def test_long_reference_lists_become_a_count_and_endpoints(self):
        link = lambda number: f"#{number}"
        self.assertEqual(notes.ref_summary([1, 2, 4, 5, 9], link), "#1, #2, #4, #5, #9")
        self.assertEqual(notes.ref_summary([9, 1, 2, 4, 5, 7], link), "6 PRs, #1–#9")

    def test_title_without_table_and_grouped_title_without_table(self):
        lines, rest = notes.dependency_lines(
            [
                self._entry(5, "deps(uv): update uv to v0.13.7"),
                self._entry(6, "deps(github-actions): update github actions non-major updates"),
            ],
            lambda number: f"#{number}",
        )
        self.assertEqual(lines, ["- uv → v0.13.7 (#5)"])
        self.assertEqual([entry.number for entry in rest], [6])


class RenderTests(TestCase):
    def test_old_prs_without_new_sections_render_from_their_titles(self):
        text = changelog(
            ("✨ Features", [rp_line(3, "**core:** add x")]),
            ("🐛 Bug Fixes", [rp_line(4, "fix y")]),
        )
        rendered = render(text, {
            3: {"title": "feat(core): add x", "body": "## Summary\nLong reviewer text.\n## Test plan\n- ran"},
            4: {"title": "fix: fix y", "body": ""},
        })
        self.assertIn(f"### ✨ New\n\n- **core:** add x ({LINK(3)})", rendered)
        self.assertIn(f"### 🐛 Fixed\n\n- fix y ({LINK(4)})", rendered)
        self.assertNotIn("reviewer", rendered)

    def test_release_note_then_highlight_then_title(self):
        text = changelog(("Features", [rp_line(1, "a"), rp_line(2, "b"), rp_line(3, "c")]))
        rendered = render(text, {
            1: {"title": "feat(a): a", "body": "## Release note\nNew text.\n## Highlight\nOld text."},
            2: {"title": "feat(b): b", "body": "## Highlight\n**Bold** legacy\nhighlight."},
            3: {"title": "feat(c): c", "body": ""},
        })
        self.assertIn(f"- **a:** a — New text. ({LINK(1)})", rendered)
        self.assertNotIn("Old text", rendered)
        self.assertIn(f"- **b:** b — **Bold** legacy highlight. ({LINK(2)})", rendered)
        self.assertIn(f"- **c:** c ({LINK(3)})", rendered)

    def test_usage_example_renders_as_an_indented_block_under_its_bullet(self):
        text = changelog(("Features", [rp_line(1, "a")]))
        rendered = render(text, {1: {"title": "feat(a): a", "body": "## Usage example\n```yaml\n- uses: x\n```"}})
        self.assertIn(f"- **a:** a ({LINK(1)})\n  ```yaml\n  - uses: x\n  ```", rendered)

    def test_glance_line_omits_zero_parts_and_follows_the_marker(self):
        text = changelog(
            ("Features", [rp_line(1, "a")]),
            ("📦 Dependencies", [rp_line(2, "update x to v2")]),
        )
        rendered = render(text, {1: {"title": "feat: a"}, 2: {"title": "deps(npm): update x to v2"}})
        self.assertTrue(rendered.startswith(notes.NOTES_MARKER + "\n**At a glance:** 1 new · 1 internal\n"))

    def test_internal_label_demotes_a_fix_and_security_updates_are_fixed(self):
        text = changelog(("Bug Fixes", [rp_line(1, "retry"), rp_line(2, "update lib to v1.2.4 [security]")]))
        rendered = render(text, {
            1: {"title": "fix(rollout): retry", "labels": [{"name": "release-notes: internal"}]},
            2: {"title": "fix(deps): update lib to v1.2.4 [security]",
                "body": "| Package | Change |\n|---|---|\n| lib | `1.2.3` → `1.2.4` |\n"},
        })
        fixed = rendered[rendered.index("### 🐛 Fixed"):rendered.index("<details>")]
        self.assertIn("- lib 1.2.3 → 1.2.4", fixed)
        self.assertNotIn("retry", fixed)
        self.assertIn("**rollout:** retry", rendered[rendered.index("<details>"):])

    def test_dependency_pr_with_an_authored_note_is_not_grouped_away(self):
        text = changelog(("Bug Fixes", [rp_line(1, "update lib to v2")]))
        rendered = render(text, {1: {
            "title": "fix(deps): update lib to v2",
            "body": "## Release note\nLib 2 fixes the crash on start.\n| Package | Change |\n|---|---|\n| lib | `1.0.0` → `2.0.0` |\n",
        }})
        self.assertIn("Lib 2 fixes the crash on start.", rendered)

    def test_breaking_pr_is_not_repeated_and_migration_replaces_the_footer(self):
        text = changelog(
            ("⚠ BREAKING CHANGES", [
                "* the old API is removed",
                rp_line(9, "**api:** drop v1 endpoints", commit=False),
            ]),
            ("Features", [rp_line(8, "replace the gate"), rp_line(9, "**api:** drop v1 endpoints")]),
        )
        rendered = render(text, {
            8: {"title": "feat!: replace the gate", "body": "## Migration\n1. Run the update.\n2. Apply the plugin."},
            9: {"title": "feat(api)!: drop v1 endpoints", "body": ""},
        })
        self.assertEqual(rendered.count(f"({LINK(8)})"), 1)
        self.assertEqual(rendered.count(f"({LINK(9)})"), 1)
        self.assertNotIn("### ✨ New", rendered)
        self.assertIn("> [!WARNING]\n> replace the gate", rendered)
        self.assertIn("> 1. Run the update.\n> 2. Apply the plugin.", rendered)
        self.assertNotIn("the old API is removed", rendered)
        self.assertIn("**At a glance:** ⚠️ 2 breaking", rendered)

    def test_breaking_footer_without_migration_is_kept(self):
        text = changelog(
            ("⚠ BREAKING CHANGES", ["* the old API is removed"]),
            ("Features", [rp_line(8, "replace the gate")]),
        )
        rendered = render(text, {8: {"title": "feat!: replace the gate", "body": ""}})
        self.assertIn("> replace the gate", rendered)
        self.assertIn("> the old API is removed", rendered)

    def test_unattributed_lines_stay_visible(self):
        text = changelog(
            ("Features", ["* direct push without a PR ([abcdef0](https://github.com/acme/widget/commit/" + "a" * 40 + "))"]),
            ("Mystery", ["* something release please printed"]),
            ("⚠ BREAKING CHANGES", ["* footer one", "* footer two"]),
        )
        rendered = render(text, {})
        self.assertIn("### ✨ New\n\n- direct push without a PR", rendered)
        self.assertNotIn("/commit/", rendered)
        self.assertIn("- something release please printed", rendered)
        self.assertIn("> footer one", rendered)
        self.assertIn("> footer two", rendered)

    def test_hidden_commits_are_listed_internally_only(self):
        text = changelog(("Features", [rp_line(1, "a")]))
        rendered = render(text, {1: {"title": "feat: a"}}, hidden=[
            {"number": 2, "title": "ci(validate): split jobs"},
            {"number": 3, "title": "feat: something release please did not list"},
            {"number": 1, "title": "feat: a"},
        ])
        internal = rendered[rendered.index("<details>"):]
        self.assertIn("**validate:** split jobs", internal)
        self.assertIn("something release please did not list", internal)
        self.assertEqual(rendered.count(LINK(1)), 1)
        self.assertIn("Internal changes (2 PRs)", rendered)

    def test_regression_hidden_type_prs_keep_their_sections_and_breaking_ones_are_shown(self):
        text = changelog(("Features", [rp_line(1, "a")]))
        rendered = render(text, {
            1: {"title": "feat: a"},
            2: {"title": "build(gradle): require JDK 21", "body": "## Migration\nInstall JDK 21."},
            3: {"title": "ci!: drop the old runner label", "body": "## Migration\nRelabel your runners.\n## Release note\nRunners move."},
        }, hidden=[{"number": 2, "title": "build(gradle): require JDK 21"}, {"number": 3, "title": "ci!: drop the old runner label"}])
        breaking = rendered[rendered.index("### ⚠️ Breaking changes"):rendered.index("### ✨ New")]
        self.assertIn("> drop the old runner label — Runners move.", breaking)
        self.assertIn("> Relabel your runners.", breaking)
        internal = rendered[rendered.index("<details>"):]
        self.assertIn("Install JDK 21.", internal)
        self.assertEqual(rendered.count(LINK(3)), 1)

    def test_identical_subjects_merge_and_refs_compress(self):
        text = changelog(("Chores", [rp_line(number, "**template:** update shared project template") for number in (4, 5, 6, 7, 9)]))
        rendered = render(text, {
            number: {"title": "chore(template): update shared project template"} for number in (4, 5, 6, 7, 9)
        })
        self.assertIn(f"- **template:** update shared project template ({LINK(4)}–{LINK(7)}, {LINK(9)})", rendered)
        self.assertEqual(notes.compress_refs([1, 2, 4, 5, 6, 9], str), "1, 2, 4–6, 9")

    def test_untrusted_titles_are_escaped(self):
        text = changelog(("Features", [rp_line(1, "a")]))
        rendered = render(text, {1: {"title": "feat: drop List<String>\n  support <details><summary>x: 9.9.9</summary>"}})
        self.assertIn("drop List&lt;String&gt; support &lt;details&gt;&lt;summary&gt;", rendered)
        self.assertNotIn("<details><summary>x", rendered)

    def test_regression_an_oversized_internal_list_is_cut_from_the_end_but_keeps_authored_text(self):
        entries = [notes.Entry(number, "chore", "area", f"maintenance task number {number:04d} " + "x" * 80)
                   for number in range(1, 1000, 2)]
        entries.append(notes.Entry(2000, "feat", "core", "the visible change"))
        authored = notes.Entry(3000, "ci", "build", "move to JDK 21", sections={"migration": "Install JDK 21 locally."})
        entries.append(authored)
        rendered = notes.render_notes(entries, [], LINK)
        self.assertLessEqual(len(rendered), notes.MAX_NOTES_LENGTH)
        self.assertIn("- **core:** the visible change", rendered)
        self.assertIn("Install JDK 21 locally.", rendered)
        self.assertIn("Internal changes (501 PRs)</summary>", rendered)
        self.assertIn("maintenance task number 0001", rendered)
        self.assertNotIn("maintenance task number 0999", rendered)
        kept = rendered.count("maintenance task number")
        self.assertRegex(rendered, rf"- … and {500 - kept} more; see the compare link in the heading\n\n</details>$")

    def test_internal_only_release_renders_one_collapsed_block(self):
        text = changelog(("📦 Dependencies", [rp_line(2, "**npm:** update x to v2")]))
        rendered = render(text, {2: {"title": "deps(npm): update x to v2"}})
        self.assertNotIn("###", rendered)
        self.assertIn("<details><summary>Internal changes (1 PR)</summary>", rendered)


class RegressionTests(TestCase):
    """Regression guards for the failure modes the release-notes redesign must not repeat."""

    def test_regression_unicode_line_separators_cannot_forge_a_version_heading(self):
        forged = "feat(api): add thing\u2028## 9.9.9 forged"
        text = changelog(("Features", [rp_line(5, "**api:** add thing\u2028## 9.9.9 forged"), rp_line(6, "other")]))
        updated, rendered = notes.render_changelog(text, REPO, {5: {"title": forged}, 6: {"title": "feat: other"}})
        self.assertIn("**At a glance:** 2 new", rendered)
        self.assertIn(LINK(6), rendered)
        self.assertEqual([line for line in updated.split("\n") if line.startswith("## ")],
                         [line for line in text.split("\n") if line.startswith("## ")])
        for separator in "\u2028\u2029\x85\x0b\x0c\x1c\x1d\x1e":
            self.assertNotIn(separator, rendered)
        self.assertEqual(len(notes._version_ranges(updated)), 2)
        footer = changelog(("⚠ BREAKING CHANGES", ["* removed\u2028## 8.8.8 forged"]), ("Features", [rp_line(7, "x")]))
        updated, _ = notes.render_changelog(footer, REPO, {7: {"title": "feat!: x"}})
        self.assertEqual(len(notes._version_ranges(updated)), 2)
        self.assertNotIn("\n## 8.8.8", updated)

    def test_regression_author_html_cannot_hide_the_rest_of_the_notes(self):
        text = changelog(("Features", [rp_line(1, "a"), rp_line(2, "b")]))
        rendered = render(text, {
            1: {"title": "feat(a): a\rcarriage", "body": (
                "## Release note\nUse <b>bold</b> and `List<String>` here.\r\n"
                "## Migration\n<!-- guidance that lost its end\nStep after the comment.\n"
                "```xml\n<!-- kept in code -->\n<!-- open in code\n```"
            )},
            2: {"title": "feat(b): the next entry"},
        })
        self.assertNotIn("\r", rendered)
        self.assertIn("a carriage", rendered)
        self.assertIn("Use &lt;b>bold&lt;/b> and `List<String>` here.", rendered)
        self.assertIn("&lt;!-- guidance that lost its end", rendered)
        self.assertIn("Step after the comment.", rendered)
        self.assertIn("<!-- kept in code -->", rendered)
        self.assertIn(LINK(2), rendered)
        comments = [line for line in rendered.split("\n") if "<!--" in line]
        self.assertEqual(comments, [notes.NOTES_MARKER, "  > <!-- kept in code -->"])
        self.assertIn("&lt;!-- open in code", rendered)

    def test_regression_an_unterminated_fence_in_a_footer_is_read_as_text(self):
        text = changelog(
            ("⚠ BREAKING CHANGES", ["* run this:", "```sh"]),
            ("Features", [rp_line(7, "x"), rp_line(8, "y")]),
        )
        updated, rendered = notes.render_changelog(text, REPO, {7: {"title": "feat!: x"}, 8: {"title": "feat: y"}})
        self.assertIn(LINK(8), rendered)
        self.assertIn("run this:", rendered)
        self.assertTrue(updated.endswith("## [1.0.0](https://github.com/acme/widget/compare/v0.9.0...v1.0.0) (2026-10-01)\n\n* old entry\n"))
        self.assertEqual(len(notes._version_ranges(updated)), 2)

    def test_regression_migration_and_release_note_text_is_never_lost(self):
        hostile = (
            "Step 1: run it.\n\n---\n\n<details><summary>widget: 9.9.9</summary>\nhidden?\n</details>\n\n"
            "```md\n## not a version\n<!-- kept in code -->\n```\n<!-- authoring comment -->\nLast line."
        )
        cases = {
            "feat with migration": ("feat(a): add", {"title": "feat(a): add", "body": "## Migration\n" + hostile}),
            "breaking": ("feat(a)!: remove", {"title": "feat(a)!: remove", "body": "## Breaking change\n" + hostile}),
            "internal with migration": ("chore(a): move", {"title": "chore(a): move", "body": "## Migration\n" + hostile}),
            "internal label": ("fix(a): fix", {
                "title": "fix(a): fix", "labels": ["release-notes: internal"], "body": "## Migration\n" + hostile,
            }),
            "release note": ("feat(a): add", {"title": "feat(a): add", "body": "## Release note\n" + hostile}),
        }
        for name, (subject, source) in cases.items():
            with self.subTest(name):
                text = changelog(("Features", [rp_line(1, subject)]))
                updated, rendered = notes.render_changelog(text, REPO, {1: source})
                for words in ("Step 1: run it.", "hidden?", "## not a version", "Last line.", "widget: 9.9.9"):
                    self.assertIn(words, rendered)
                self.assertNotIn("authoring comment", rendered)
                self.assertNotIn("<details><summary>widget", rendered)
                self.assertEqual(len(notes._version_ranges(updated)), 2)
                self.assertEqual(notes.render_changelog(updated, REPO, {1: source}), (updated, rendered))

    def test_regression_migration_survives_on_every_golden_sample(self):
        for name in SAMPLES:
            _, _, sources, _, _ = load_sample(name)
            _, body, rendered = render_sample(name)
            for number, source in sources.items():
                sections = notes.extract_rich_sections(source["body"])
                for key in ("migration", "release note", "highlight"):
                    for line in sections.get(key, "").splitlines():
                        if line.strip():
                            with self.subTest(sample=name, pr=number, line=line[:40]):
                                self.assertIn(line.strip(), rendered)

    def test_regression_summary_text_never_contains_a_version_triple(self):
        rendered_samples = [render_sample(name)[1] for name in SAMPLES]
        for body in rendered_samples:
            for summary in re.findall(r"<summary>(.*?)</summary>", body):
                self.assertIsNone(VERSION_TRIPLE.search(summary), summary)
        for count in (0, 1, 2, 3, 12, 123, 999_999):
            entries = [notes.Entry(number, "chore", None, "x.1.2.3 1.2.3") for number in range(1, count + 2)]
            output = notes.render_notes(entries, [], LINK)
            for summary in re.findall(r"<summary>(.*?)</summary>", output):
                self.assertIsNone(VERSION_TRIPLE.search(summary), summary)

    def test_regression_reruns_are_a_fixed_point(self):
        for name in SAMPLES:
            with self.subTest(name):
                text, body, sources, hidden, types = load_sample(name)
                first, rendered = notes.render_changelog(text, SAMPLES[name], sources, section_types=types, hidden=hidden)
                second, again = notes.render_changelog(first, SAMPLES[name], {}, section_types=types)
                self.assertEqual((first, rendered), (second, again))
                heading = notes.top_section(first)[2].split("\n", 1)[0]
                once = notes.replace_body_notes(body, heading, rendered)
                self.assertEqual(notes.replace_body_notes(once, heading, again), once)

    def test_regression_breaking_pr_is_listed_once(self):
        for name, number in (("q4j-755", 801), ("java-test-automation-template-20", 53)):
            rendered = render_sample(name)[2]
            self.assertEqual(rendered.count(f"[#{number}]("), 1, name)
            self.assertNotIn("### ✨ New", rendered)

    def test_regression_old_prs_render_from_titles(self):
        rendered = render_sample("ci-kit-534")[2]
        self.assertIn("- **allure:** fail component mode when results are not at the artifact root ([#546](", rendered)
        self.assertNotIn("Test plan", rendered)


class GoldenTests(TestCase):
    def test_golden_release_bodies_and_changelogs(self):
        for name in SAMPLES:
            with self.subTest(name):
                updated, body, rendered = render_sample(name)
                directory = FIXTURES / name
                self.assertEqual(body, (directory / "expected-release-pr-body.md").read_text(encoding="utf-8"))
                self.assertEqual(updated, (directory / "expected-CHANGELOG.md").read_text(encoding="utf-8"))

    def test_changelog_top_section_and_release_body_notes_are_byte_identical(self):
        for name in SAMPLES:
            with self.subTest(name):
                updated, body, rendered = render_sample(name)
                top = notes.top_section(updated)[2]
                lines = body.split("\n")
                content = "\n".join(lines[lines.index("---") + 1 : len(lines) - 1 - lines[::-1].index("---")]).strip()
                self.assertEqual(content, top.strip())
                self.assertEqual(notes.rendered_notes_of(top), rendered)

    def test_golden_structure_matches_the_spec(self):
        for name in SAMPLES:
            with self.subTest(name):
                original_changelog = (FIXTURES / name / "CHANGELOG.md").read_text(encoding="utf-8")
                updated, body, rendered = render_sample(name)
                self.assertNotIn("/commit/", rendered)
                self.assertNotIn("/issues/", rendered)
                self.assertTrue(rendered.startswith(notes.NOTES_MARKER + "\n**At a glance:** "))
                self.assertTrue(rendered.rstrip().endswith("</details>"))
                self.assertEqual(rendered.count("<details>"), 1)
                self.assertNotIn(notes.BLOCK_START, body)
                self.assertNotIn("rich-release-notes", body)
                older = original_changelog[notes.top_section(original_changelog)[1]:]
                self.assertTrue(updated.endswith(older))
                self.assertLess(len(body.encode()), len((FIXTURES / name / "release-pr-body.md").read_bytes()))

    def test_template_sample_covers_every_listed_and_hidden_pr(self):
        for name in SAMPLES:
            _, _, sources, hidden, _ = load_sample(name)
            rendered = render_sample(name)[2]
            referenced = set()
            for start, end in re.findall(r"\[#(\d+)\]\([^)]*\)(?:–\[#(\d+)\])?", rendered):
                referenced.update(range(int(start), int(end or start) + 1))
            expected = set(sources) | {commit["number"] for commit in hidden}
            self.assertLessEqual(expected, referenced, name)


class BodyTests(TestCase):
    def test_single_body_notes_are_replaced_between_heading_and_footer(self):
        text = changelog(("Features", [rp_line(1, "a")]))
        updated, rendered = notes.render_changelog(text, REPO, {1: {"title": "feat: a"}})
        heading = notes.top_section(updated)[2].split("\n", 1)[0]
        body = notes.replace_body_notes(body_for(text), heading, rendered)
        self.assertTrue(body.startswith(":robot: I have created a release *beep* *boop*\n---\n\n\n## [1.1.0]"))
        self.assertTrue(body.endswith("</details>\n\n---\nThis PR was generated with [Release Please](https://github.com/googleapis/release-please).\n")
                        or body.endswith(f"({LINK(1)})\n\n---\nThis PR was generated with [Release Please](https://github.com/googleapis/release-please).\n"))
        self.assertNotIn("/commit/", body)

    def test_body_heading_must_match_the_changelog(self):
        text = changelog(("Features", [rp_line(1, "a")]))
        with self.assertRaisesRegex(notes.EnrichmentError, "disagree"):
            notes.replace_body_notes(body_for(text).replace("1.1.0", "1.2.0"), notes.top_section(text)[2].split("\n")[0], "x")

    def test_component_bodies_keep_nested_internal_details_inside_their_component(self):
        body = (
            ":robot: header\n---\n\n\n"
            "<details><summary>backend: 1.1.0</summary>\n\n## [1.1.0](https://x) (2026-11-07)\n\n### Features\n\n* a\n</details>\n\n"
            "<details><summary>frontend: 2.0.0</summary>\n\n## [2.0.0](https://y) (2026-11-07)\n\n* b\n</details>\n\n"
            "---\nfooter\n"
        )
        rendered = notes.NOTES_MARKER + "\n\n<details><summary>Internal changes (1 PR)</summary>\n\n- x\n\n</details>"
        updated = notes.replace_component_notes(body, {
            "backend": ("## [1.1.0](https://x) (2026-11-07)", rendered),
            "frontend": ("## [2.0.0](https://y) (2026-11-07)", rendered.replace("- x", "- y")),
        })
        self.assertEqual(notes.release_body_components(updated), {"backend", "frontend"})
        again = notes.replace_component_notes(updated, {
            "backend": ("## [1.1.0](https://x) (2026-11-07)", rendered),
            "frontend": ("## [2.0.0](https://y) (2026-11-07)", rendered.replace("- x", "- y")),
        })
        self.assertEqual(again, updated)
        backend = updated[: updated.index("frontend: 2.0.0")]
        self.assertIn("- x", backend)
        self.assertNotIn("- y", backend)
        with self.assertRaisesRegex(notes.EnrichmentError, "missing configured component"):
            notes.replace_component_notes(body, {"worker": ("## 1", "x")})

    def test_v1_rich_block_is_migrated_out_of_the_top_version(self):
        text = changelog(("Features", [rp_line(1, "a")])).replace(
            "(2026-11-07)\n\n",
            "(2026-11-07)\n\n" + notes.BLOCK_START + "\n### 🔄 Migration\n<!-- project-toolkit:rich-release-notes pr=1 -->\nold\n"
            + notes.BLOCK_END + "\n\n",
            1,
        )
        rendered = render(text, {1: {"title": "feat: a", "body": "## Migration\nnew steps"}})
        self.assertNotIn("old", rendered)
        self.assertIn("new steps", rendered)

    def test_legacy_marker_cleanup_preserves_fenced_headings(self):
        top = "## 1.0.0\n\n<!-- project-toolkit:rich-release-notes pr=1 -->\n### Highlights\nold\n```md\n### Keep this\n```\n### Features\n- normal\n"
        stripped = notes._strip_legacy(top)
        self.assertNotIn("old", stripped)
        self.assertIn("### Features", stripped)


class DiscoveryTests(TestCase):
    def test_standard_attribution_uses_only_the_trailing_link(self):
        commit = "a" * 40
        text = (
            "## 1.2.3\n\n### Features\n"
            "* title [#99](https://github.com/acme/widget/pull/99) "
            "([#7](https://github.com/acme/widget/issues/7)) "
            f"([abcdef0](https://github.com/acme/widget/commit/{commit}))\n"
        )
        self.assertEqual(notes.source_pr_numbers(text, REPO), [7])

    def test_source_discovery_ignores_fences_machine_blocks_and_older_versions(self):
        commit = "b" * 40
        canonical = (
            "* valid ([#8](https://github.com/acme/widget/pull/8)) "
            f"([abcdef1](https://github.com/acme/widget/commit/{commit}))\n"
        )
        text = (
            "## 2.0.0\n````md\n```java\n## fake\n```\n````\n"
            + notes.BLOCK_START + "\n" + canonical.replace("#8", "#9").replace("/8", "/9") + notes.BLOCK_END + "\n"
            + canonical + "## 1.0.0\n" + canonical.replace("#8", "#10").replace("/8", "/10")
        )
        self.assertEqual(notes.source_pr_numbers(text, REPO), [8])

    def test_previous_tag_from_compare_link(self):
        heading = f"## [1.1.0](https://github.com/{REPO}/compare/v1.0.0...v1.1.0) (2026-11-07)"
        self.assertEqual(notes.previous_tag(heading, REPO), "v1.0.0")
        self.assertEqual(
            notes.previous_tag(f"## [1.1.0](https://github.com/{REPO}/compare/backend-v1.0.0...backend-v1.1.0) (x)", REPO),
            "backend-v1.0.0",
        )
        self.assertIsNone(notes.previous_tag("## 1.0.0 (2026-11-07)", REPO))
        self.assertIsNone(notes.previous_tag(heading.replace(REPO, "evil/repo"), REPO))
        with self.assertRaises(notes.EnrichmentError):
            notes.previous_tag(f"## [1](https://github.com/{REPO}/compare/-x;rm...v1)", REPO)

    def test_manifest_discovers_package_local_and_root_relative_changelogs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            manifest = root / "manifest.json"
            config.write_text(json.dumps({
                "changelog-path": "CHANGELOG.md",
                "packages": {
                    "backend": {"package-name": "backend"},
                    "frontend": {"package-name": "frontend", "changelog-path": "docs/NEWS.md"},
                    "worker": {"package-name": "worker", "changelog-path": "/WORKER.md"},
                },
            }), encoding="utf-8")
            manifest.write_text(json.dumps({"backend": "1.0.0", "frontend": "2.0.0", "worker": "3.0.0"}), encoding="utf-8")
            targets = notes.discover_targets(
                mode="manifest", package_path=".", config_file=config, manifest_file=manifest, config_backed_single=False,
            )
        self.assertEqual(
            [(target.changelog.as_posix(), target.package.as_posix()) for target in targets],
            [("backend/CHANGELOG.md", "backend"), ("frontend/docs/NEWS.md", "frontend"), ("WORKER.md", "worker")],
        )

    def test_legacy_single_path_does_not_require_config(self):
        paths = notes.discover_changelog_paths(
            mode="single", package_path="package", config_file=Path("missing-config.json"),
            manifest_file=Path("missing-manifest.json"), config_backed_single=False,
        )
        self.assertEqual(paths, [Path("package/CHANGELOG.md")])

    def test_unsafe_paths_are_rejected(self):
        for value in ("../x", "/etc", "a b", "a/../b", ""):
            with self.subTest(value), self.assertRaises(notes.EnrichmentError):
                notes._safe_relative_path(value, label="path")


class HiddenCommitTests(TestCase):
    def _git(self, root: Path, *arguments: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *arguments],
            cwd=root, check=True, capture_output=True, text=True,
        ).stdout.strip()

    def test_regression_hidden_commits_are_anchored_at_the_release_pr_base_sha(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._git(root, "init", "-q", "-b", "main")
            (root / "pkg").mkdir()

            def commit(message: str, path: str = "file") -> str:
                (root / path).write_text(message, encoding="utf-8")
                self._git(root, "add", ".")
                self._git(root, "commit", "-qm", message)
                return self._git(root, "rev-parse", "HEAD")

            commit("chore(main): release 1.0.0 (#10)")
            self._git(root, "tag", "v1.0.0")
            commit("ci(validate): split jobs (#11)")
            commit("feat: listed by release please (#12)")
            commit("docs: outside the package (#13)")
            commit("test(pkg): cover the package (#14)", "pkg/file")
            commit("chore(main): release 1.1.0 (#15)")
            commit("direct push without a PR number")
            base = commit("docs: merged after the release PR base (#16)")
            later = self._git(root, "rev-parse", "HEAD~1")
            previous = Path.cwd()
            os.chdir(root)
            try:
                everything = notes.hidden_commits(previous="v1.0.0", base_sha=later, package=Path("."), listed=[12])
                package = notes.hidden_commits(previous="v1.0.0", base_sha=later, package=Path("pkg"), listed=[])
                root_only = notes.hidden_commits(
                    previous="v1.0.0", base_sha=later, package=Path("."), listed=[12], exclude=[Path("pkg")]
                )
                with self.assertRaises(notes.EnrichmentError):
                    notes.hidden_commits(previous="v1.0.0", base_sha="HEAD", package=Path("."), listed=[])
                with self.assertRaises(notes.EnrichmentError):
                    notes.hidden_commits(previous="--output=/tmp/x", base_sha=base, package=Path("."), listed=[])
                with self.assertRaises(notes.EnrichmentError):
                    notes.hidden_commits(previous="v9.9.9", base_sha=base, package=Path("."), listed=[])
            finally:
                os.chdir(previous)
        self.assertEqual(
            everything,
            [
                {"number": 11, "title": "ci(validate): split jobs"},
                {"number": 13, "title": "docs: outside the package"},
                {"number": 14, "title": "test(pkg): cover the package"},
            ],
        )
        self.assertEqual(package, [{"number": 14, "title": "test(pkg): cover the package"}])
        self.assertEqual([commit["number"] for commit in root_only], [11, 13])


class PrepareTests(TestCase):
    @staticmethod
    def _pull(number: int, *, body: str = "", title: str | None = None, **extra: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "number": number,
            "state": "closed",
            "merged_at": "2026-01-01T00:00:00Z",
            "title": title or f"feat: PR {number}",
            "body": body,
            "labels": [],
            "head": {"repo": {"full_name": REPO}, "ref": f"feature/{number}"},
            "base": {"repo": {"full_name": REPO}, "sha": "c" * 40, "ref": "main"},
        }
        payload.update(extra)
        return payload

    def _release(self, number: int, body: str) -> dict[str, Any]:
        return self._pull(
            number, body=body, state="open", merged_at=None,
            head={"repo": {"full_name": REPO}, "ref": "release-please--branches--main"},
        )

    def _prepare(self, root: Path, **overrides: Any) -> dict[str, Any]:
        arguments: dict[str, Any] = {
            "repository": REPO,
            "release_prs_file": root / "prs.json",
            "mode": "single",
            "package_path": ".",
            "config_file": root / "config.json",
            "manifest_file": root / "manifest.json",
            "config_backed_single": False,
            "output_directory": root / "output",
        }
        arguments.update(overrides)
        previous = Path.cwd()
        os.chdir(root)
        try:
            return notes.prepare_release_enrichment(**arguments)
        finally:
            os.chdir(previous)

    def test_prepare_fails_closed_for_multiple_release_prs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "prs.json").write_text(json.dumps([{"number": 1}, {"number": 2}]), encoding="utf-8")
            with self.assertRaisesRegex(notes.EnrichmentError, "exactly one"):
                self._prepare(root)

    def test_regression_pending_release_pr_fallback(self):
        text = changelog(("Features", [rp_line(7, "a")]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "CHANGELOG.md").write_text(text, encoding="utf-8")
            for output in ("", "[]"):
                (root / "prs.json").write_text(output, encoding="utf-8")
                gh_json = mock.Mock(side_effect=[
                    [
                        {"number": 50, "headRefName": "release-please--branches--main", "baseRefName": "main",
                         "isCrossRepository": False},
                        {"number": 51, "headRefName": "release-please--decoy", "baseRefName": "main",
                         "isCrossRepository": True},
                    ],
                    self._release(50, body_for(text)),
                    self._pull(7),
                ])
                with mock.patch.object(notes, "_run_gh") as run_gh, mock.patch.object(notes, "_gh_json", gh_json), \
                        mock.patch.object(notes, "hidden_commits", return_value=[]) as hidden:
                    metadata = self._prepare(root, target_branch="main")
                self.assertEqual(metadata["release_pr"], 50)
                self.assertEqual(gh_json.call_args_list[0].args[0][-2:], ["--base", "main"])
                self.assertTrue(metadata["body_changed"])
                self.assertIn("autorelease: pending", gh_json.call_args_list[0].args[0])
                run_gh.assert_called_once_with(["pr", "checkout", "50", "--repo", REPO, "--force"])
                self.assertEqual(hidden.call_args.kwargs["base_sha"], "c" * 40)
                (root / "CHANGELOG.md").write_text(text, encoding="utf-8")

    def test_fallback_skips_when_there_is_not_exactly_one_pending_release_pr(self):
        def pr(number: int, head: str, base: str = "main", cross: bool = False) -> dict[str, Any]:
            return {"number": number, "headRefName": head, "baseRefName": base, "isCrossRepository": cross}

        for listing in ([], [pr(1, "release-please--a"), pr(2, "release-please--b")],
                        [pr(3, "feature/labelled-by-hand")], [pr(4, "release-please--fork", cross=True)],
                        [pr(5, "release-please--branches--next", base="next")]):
            with self.subTest(listing=listing), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "prs.json").write_text("[]", encoding="utf-8")
                with mock.patch.object(notes, "_run_gh") as run_gh, mock.patch.object(notes, "_gh_json", return_value=listing):
                    metadata = self._prepare(root, target_branch="main")
                self.assertTrue(metadata["skipped"])
                run_gh.assert_not_called()
                self.assertTrue(json.loads((root / "output/metadata.json").read_text())["skipped"])

    def test_prepare_rejects_a_release_pr_that_is_not_a_release_please_branch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "prs.json").write_text('[{"number": 50}]', encoding="utf-8")
            with mock.patch.object(notes, "_run_gh") as run_gh, \
                    mock.patch.object(notes, "_gh_json", return_value=self._pull(50, state="open")):
                with self.assertRaisesRegex(notes.EnrichmentError, "not an open Release Please branch"):
                    self._prepare(root)
            run_gh.assert_not_called()

    def test_regression_fork_contributed_prs_render_from_their_title(self):
        text = changelog(("Features", [rp_line(7, "a")]))
        for head in ({"repo": {"full_name": "fork/widget"}, "ref": "main"}, {"repo": None, "ref": "x"}):
            with self.subTest(head=head), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "CHANGELOG.md").write_text(text, encoding="utf-8")
                (root / "prs.json").write_text('[{"number": 50}]', encoding="utf-8")
                source = self._pull(7, title="feat(ui): contributed by a fork", head=head)
                with mock.patch.object(notes, "_run_gh"), mock.patch.object(notes, "hidden_commits", return_value=[]), \
                        mock.patch.object(notes, "_gh_json", side_effect=[self._release(50, body_for(text)), source]):
                    metadata = self._prepare(root)
                self.assertEqual(metadata["source_prs"], [7])
                self.assertIn("contributed by a fork", (root / "CHANGELOG.md").read_text(encoding="utf-8"))

    def test_prepare_rejects_a_release_pr_on_another_base_branch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "prs.json").write_text('[{"number": 50}]', encoding="utf-8")
            release = self._release(50, "x")
            release["base"]["ref"] = "next"
            with mock.patch.object(notes, "_run_gh") as run_gh, mock.patch.object(notes, "_gh_json", return_value=release):
                with self.assertRaisesRegex(notes.EnrichmentError, "does not target main"):
                    self._prepare(root, target_branch="main")
            run_gh.assert_not_called()

    def test_regression_a_listed_reference_that_is_not_a_pull_request_keeps_release_pleases_text(self):
        text = changelog(("Features", [rp_line(7, "**api:** add search"), rp_line(8, "b")]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "CHANGELOG.md").write_text(text, encoding="utf-8")
            (root / "prs.json").write_text('[{"number": 50}]', encoding="utf-8")
            responses = [self._release(50, body_for(text)), notes.EnrichmentError("gh api failed: HTTP 404"), self._pull(8)]
            with mock.patch.object(notes, "_run_gh"), mock.patch.object(notes, "hidden_commits", return_value=[]), \
                    mock.patch.object(notes, "_gh_json", side_effect=responses):
                metadata = self._prepare(root)
            rendered = (root / "CHANGELOG.md").read_text(encoding="utf-8")
            self.assertEqual(metadata["source_prs"], [8])
            self.assertIn(f"- **api:** add search ({LINK(7)})", rendered)

    def test_prepare_fetches_hidden_type_prs_leniently(self):
        text = changelog(("Features", [rp_line(7, "a")]))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "CHANGELOG.md").write_text(text, encoding="utf-8")
            (root / "prs.json").write_text('[{"number": 50}]', encoding="utf-8")
            hidden = [{"number": 8, "title": "ci: move runners"}, {"number": 9, "title": "docs: an issue number"}]
            responses = [
                self._release(50, body_for(text)), self._pull(7),
                self._pull(8, title="ci: move runners", body="## Migration\nRelabel runners."),
                self._pull(9, state="open", merged_at=None),
            ]
            with mock.patch.object(notes, "_run_gh"), mock.patch.object(notes, "hidden_commits", return_value=hidden), \
                    mock.patch.object(notes, "_gh_json", side_effect=responses):
                metadata = self._prepare(root)
            rendered = (root / "CHANGELOG.md").read_text(encoding="utf-8")
            self.assertEqual(metadata["hidden_prs"], [8, 9])
            self.assertIn("Relabel runners.", rendered)
            self.assertIn("an issue number", rendered)

    def test_prepare_rejects_unmerged_or_foreign_base_source_pr(self):
        text = changelog(("Features", [rp_line(7, "a")]))
        cases = {
            "not merged": self._pull(7, state="open", merged_at=None),
            "repository validation failed": self._pull(7, base={"repo": {"full_name": "other/widget"}}),
        }
        for message, source in cases.items():
            with self.subTest(message), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root / "CHANGELOG.md").write_text(text, encoding="utf-8")
                (root / "prs.json").write_text('[{"number": 50}]', encoding="utf-8")
                with mock.patch.object(notes, "_run_gh"), \
                        mock.patch.object(notes, "_gh_json", side_effect=[self._release(50, body_for(text)), source]):
                    with self.assertRaisesRegex(notes.EnrichmentError, message):
                        self._prepare(root)

    def test_end_to_end_manifest_cli_renders_components_and_is_a_fixed_point(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            backend = changelog(("Features", [rp_line(1, "backend thing")])).replace(
                f"https://github.com/{REPO}/compare/v1.0.0...v1.1.0", f"https://github.com/{REPO}/compare/backend-v1.0.0...backend-v1.1.0"
            )
            (root / "backend").mkdir()
            (root / "backend/CHANGELOG.md").write_text(backend, encoding="utf-8")
            (root / "config.json").write_text(json.dumps(
                {"packages": {name: {"package-name": name} for name in ("backend", "frontend", "worker")}}
            ), encoding="utf-8")
            (root / "manifest.json").write_text(
                json.dumps({name: "1.0.0" for name in ("backend", "frontend", "worker")}), encoding="utf-8"
            )
            (root / "release-prs.json").write_text('[{"number": 50}]', encoding="utf-8")
            top = notes.top_section(backend)[2].rstrip()
            fake_data = {
                "50": self._release(50, (
                    ":robot: header\n---\n\n\n"
                    f"<details><summary>backend: 1.1.0</summary>\n\n{top}\n</details>\n\n"
                    "---\nThis PR was generated with Release Please.\n"
                )),
                "1": self._pull(1, title="feat(api): backend thing", body="## Release note\nBackend users get X."),
            }
            data_path = root / "gh-data.json"
            data_path.write_text(json.dumps(fake_data), encoding="utf-8")
            log_path = root / "gh-log.jsonl"
            bin_dir = root / "bin"
            bin_dir.mkdir()
            fake_gh = bin_dir / "gh"
            fake_gh.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "args=sys.argv[1:]\n"
                "with open(os.environ['FAKE_GH_LOG'],'a') as log: log.write(json.dumps(args)+'\\n')\n"
                "if args[:1]==['api'] and '/pulls/' in args[1]:\n"
                " print(json.dumps(json.load(open(os.environ['FAKE_GH_DATA']))[args[1].rsplit('/',1)[1]]))\n"
                "elif args[:2]==['pr','checkout']: pass\n"
                "else: print('unsupported fake gh command',file=sys.stderr); sys.exit(2)\n",
                encoding="utf-8",
            )
            fake_gh.chmod(0o755)
            fake_git = bin_dir / "git"
            fake_git.write_text(
                "#!/usr/bin/env python3\nimport sys\n"
                "args=sys.argv[1:]\n"
                "if args[:1]==['rev-parse']: print('d'*40)\n"
                "elif args[:1]==['log']:\n"
                " assert args[-2:]==['--','backend'], args\n"
                " assert args[2]=='d'*40+'..'+'c'*40, args\n"
                " print('e'*40+'\\x00ci(backend): split jobs (#3)')\n"
                "else: sys.exit(2)\n",
                encoding="utf-8",
            )
            fake_git.chmod(0o755)
            env = os.environ.copy()
            env.update({"PATH": f"{bin_dir}:{env['PATH']}", "FAKE_GH_DATA": str(data_path), "FAKE_GH_LOG": str(log_path)})
            command = [
                sys.executable, str(ROOT / "scripts/enrich_release_notes.py"), "--prepare",
                "--repository", REPO, "--release-prs", "release-prs.json", "--mode", "manifest",
                "--config-file", "config.json", "--manifest-file", "manifest.json", "--output-directory", "output",
            ]
            first = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            snapshot = (root / "backend/CHANGELOG.md").read_text(encoding="utf-8")
            release_body = json.loads((root / "output/release-body.json").read_text(encoding="utf-8"))["body"]
            metadata = json.loads((root / "output/metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["source_prs"], [1])
            self.assertEqual(metadata["hidden_prs"], [3])
            self.assertEqual(metadata["changelogs"], ["backend/CHANGELOG.md"])
            self.assertIn("Backend users get X.", snapshot)
            self.assertIn("**backend:** split jobs", snapshot)
            backend_block = release_body[: release_body.index("\n</details>\n\n---")]
            self.assertIn("Backend users get X.", backend_block)
            self.assertIn("<details><summary>Internal changes (1 PR)</summary>", backend_block)
            fake_data["50"]["body"] = release_body
            data_path.write_text(json.dumps(fake_data), encoding="utf-8")
            second = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual((root / "backend/CHANGELOG.md").read_text(encoding="utf-8"), snapshot)
            rerun = json.loads((root / "output/metadata.json").read_text(encoding="utf-8"))
            self.assertFalse(rerun["body_changed"])
            self.assertEqual(rerun["source_prs"], [])
            calls = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            self.assertIn(["pr", "checkout", "50", "--repo", REPO, "--force"], calls)


class ComponentReleaseSyncTests(TestCase):
    def _setup(self, root: Path, packages: dict[str, str]) -> None:
        (root / "config.json").write_text(json.dumps(
            {"packages": {path: {"package-name": name} for path, name in packages.items()}}
        ), encoding="utf-8")
        (root / "manifest.json").write_text(json.dumps({path: "1.1.0" for path in packages}), encoding="utf-8")
        for path in packages:
            (root / path).mkdir(parents=True, exist_ok=True)
            text = changelog(("Features", [rp_line(1, "a")]))
            rendered, _ = notes.render_changelog(text, REPO, {1: {"title": "feat: a"}})
            (root / path / "CHANGELOG.md").write_text(rendered, encoding="utf-8")

    def _sync(self, root: Path, outputs: dict[str, Any], mode: str = "manifest") -> tuple[list[str], mock.Mock]:
        previous = Path.cwd()
        os.chdir(root)
        try:
            with mock.patch.object(notes, "_run_gh") as run_gh:
                synced = notes.sync_component_releases(
                    repository=REPO, outputs=outputs, mode=mode, config_file=Path("config.json"),
                    manifest_file=Path("manifest.json"), notes_directory=root / "notes",
                )
            return synced, run_gh
        finally:
            os.chdir(previous)

    def test_multi_component_releases_are_reset_to_their_changelog_sections(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._setup(root, {"src/backend": "backend", "src/frontend": "frontend", "public": "public"})
            outputs = {
                "releases_created": "true",
                "paths_released": json.dumps(["src/backend", "public"]),
                "src/backend--tag_name": "backend-v1.1.0", "src/backend--version": "1.1.0",
                "public--tag_name": "public-v1.1.0", "public--version": "1.1.0",
            }
            synced, run_gh = self._sync(root, outputs)
            self.assertEqual(synced, ["backend-v1.1.0", "public-v1.1.0"])
            self.assertEqual([call.args[0][:3] for call in run_gh.call_args_list],
                             [["release", "edit", "backend-v1.1.0"], ["release", "edit", "public-v1.1.0"]])
            notes_file = Path(run_gh.call_args_list[0].args[0][-1])
            text = notes_file.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("## [1.1.0]"))
            self.assertIn(notes.NOTES_MARKER, text)
            self.assertIn("### ✨ New", text)
            self.assertNotIn("## [1.0.0]", text)

    def test_single_package_releases_are_left_alone(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._setup(root, {".": "ci-kit"})
            outputs = {"releases_created": "true", "paths_released": '["."]', "--tag_name": "v1.1.0", ".--tag_name": "v1.1.0", ".--version": "1.1.0"}
            synced, run_gh = self._sync(root, outputs)
            self.assertEqual(synced, [])
            run_gh.assert_not_called()
            synced, run_gh = self._sync(root, outputs, mode="single")
            run_gh.assert_not_called()

    def test_sync_warns_and_skips_mismatched_versions_unsafe_tags_and_unknown_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._setup(root, {"a": "a", "b": "b"})
            for outputs in (
                {"paths_released": '["a"]', "a--tag_name": "a-v2.0.0", "a--version": "2.0.0"},
                {"paths_released": '["a"]', "a--tag_name": "--notes=x", "a--version": "1.1.0"},
                {"paths_released": '["../a"]', "../a--tag_name": "a-v1.1.0", "../a--version": "1.1.0"},
                {"paths_released": '["c"]', "c--tag_name": "c-v1.1.0", "c--version": "1.1.0"},
            ):
                with self.subTest(outputs=outputs):
                    synced, run_gh = self._sync(root, outputs)
                    self.assertEqual(synced, [])
                    run_gh.assert_not_called()
            outputs = {
                "paths_released": '["c", "b"]', "c--tag_name": "c-v1.1.0", "c--version": "1.1.0",
                "b--tag_name": "b-v1.1.0", "b--version": "1.1.0",
            }
            synced, _ = self._sync(root, outputs)
            self.assertEqual(synced, ["b-v1.1.0"])
            (root / "config.json").write_text(json.dumps({"packages": {"a": {}, "b": {}}}), encoding="utf-8")
            synced, run_gh = self._sync(root, outputs)
            self.assertEqual(synced, [])
            run_gh.assert_not_called()


class WorkflowAndCopyTests(TestCase):
    def setUp(self) -> None:
        self.workflow = yaml.safe_load((ROOT / ".github/workflows/release-please.yml").read_text(encoding="utf-8"))

    def test_generated_helper_is_byte_identical_and_renders_through_jinja(self):
        generated = ROOT / "modules/release/template/.github/scripts/enrich_release_notes.py.jinja"
        source = (ROOT / "scripts/enrich_release_notes.py").read_text(encoding="utf-8")
        self.assertEqual(generated.read_text(encoding="utf-8"), source)
        rendered = Environment(undefined=StrictUndefined).from_string(generated.read_text(encoding="utf-8")).render()
        self.assertEqual(rendered + "\n", source)

    def test_enrich_job_runs_on_the_pending_release_pr_fallback(self):
        condition = self.workflow["jobs"]["enrich"]["if"]
        for job in ("single", "manifest"):
            self.assertIn(f"needs.{job}.outputs.release-prs != ''", condition)
            self.assertIn(f"needs.{job}.outputs.releases-created != 'true'", condition)
        update = next(step for step in self.workflow["jobs"]["enrich"]["steps"]
                      if step["name"] == "Update the existing Release Please pull request")
        self.assertIn(".skipped", update["run"])
        self.assertIn(".body_changed", update["run"])
        prepare = next(step for step in self.workflow["jobs"]["enrich"]["steps"]
                       if step["name"] == "Prepare enriched release notes")
        self.assertIn('python "$ENRICHMENT_SCRIPT" "${arguments[@]}"', prepare["run"])
        self.assertIn("--release-prs", prepare["run"])

    def test_component_release_sync_is_a_separate_job_that_cannot_fail_the_release(self):
        job = self.workflow["jobs"]["sync-component-releases"]
        self.assertEqual(job["needs"], "manifest")
        self.assertIs(job["continue-on-error"], True)
        self.assertIn("needs.manifest.outputs.releases-created == 'true'", job["if"])
        self.assertIn("inputs.enrich-release-notes", job["if"])
        self.assertEqual(
            self.workflow["jobs"]["manifest"]["outputs"]["release-outputs"], "${{ toJSON(steps.release.outputs) }}"
        )
        sync = job["steps"][-1]
        self.assertEqual(sync["env"]["RELEASE_OUTPUTS"], "${{ needs.manifest.outputs.release-outputs }}")
        self.assertIn("--sync-releases", sync["run"])
        self.assertIn("*--sync-releases*", sync["run"])
        self.assertNotIn("${{", sync["run"])
        for name in ("manifest", "single"):
            self.assertNotIn("Reset component", str(self.workflow["jobs"][name]))
        outputs = self.workflow["on"]["workflow_call"]["outputs"] if "on" in self.workflow else self.workflow[True]["workflow_call"]["outputs"]
        self.assertNotIn("sync-component-releases", str(outputs))

    def test_enrich_passes_the_target_branch_for_the_fallback(self):
        prepare = next(step for step in self.workflow["jobs"]["enrich"]["steps"]
                       if step["name"] == "Prepare enriched release notes")
        self.assertEqual(prepare["env"]["TARGET_BRANCH"],
                         "${{ inputs.target-branch || github.event.repository.default_branch }}")
        self.assertIn('--target-branch "$TARGET_BRANCH"', prepare["run"])

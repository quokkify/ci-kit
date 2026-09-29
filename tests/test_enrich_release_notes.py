import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, cast
from unittest import TestCase, mock

from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("enrich_release_notes", ROOT / "scripts/enrich_release_notes.py")
assert spec and spec.loader
notes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notes)


class RichNotesTests(TestCase):
    def test_dependency_chores_move_to_dependencies_and_other_chores_remain(self):
        changelog = (
            "## 2.1.0\n\n"
            "### 🧹 Chores\n\n"
            "* **deps:** update alpha (#1)\n"
            "* **maintenance:** reorganize scripts (#2)\n"
            "* internal cleanup (#3)\n\n"
            "### ✨ Features\n\n* feature\n"
        )
        normalized = notes._normalize_dependency_changelog(changelog)
        self.assertIn("### 📦 Dependencies\n\n* **deps:** update alpha", normalized)
        self.assertIn("### 🧹 Chores\n\n* **maintenance:** reorganize scripts", normalized)
        self.assertIn("* internal cleanup", normalized)
        self.assertIn("* feature", normalized)
        self.assertEqual(notes._normalize_dependency_changelog(normalized), normalized)

    def test_bare_chore_deps_commit_subject_is_removed_after_normalized_entry(self):
        changelog = (
            "## 2.23.2\n\n"
            "### 🧹 Chores\n\n"
            "* **deps:** update gradle/actions action to v6.4.0 (#314)\n\n"
            "### 📦 Dependencies\n\n"
            "* update quokkify/project-toolkit to v2.23.1 (#313)\n\n"
            "chore(deps): update gradle/actions action to v6.4.0\n"
        )
        normalized = notes._normalize_dependency_changelog(changelog)
        self.assertIn("* **deps:** update gradle/actions action to v6.4.0 (#314)", normalized)
        self.assertEqual(normalized.count("update gradle/actions action to v6.4.0"), 1)
        self.assertNotIn("chore(deps): update gradle/actions action to v6.4.0", normalized)
        self.assertIn("* update quokkify/project-toolkit to v2.23.1 (#313)", normalized)

    def test_plain_deps_chore_moves_to_dependencies_and_self_update_is_hidden(self):
        changelog = (
            "## 2.23.2\n\n"
            "### 🧹 Chores\n\n"
            "* deps: update gradle/actions action to v6.4.0 (#314) (af58859)\n"
            "* deps: update quokkify/project-toolkit to v2.23.1 (#313) (1094327)\n"
        )
        normalized = notes.enrich_changelog(changelog, [], repository="quokkify/project-toolkit")
        self.assertIn("### 📦 Dependencies", normalized)
        self.assertIn("* deps: update gradle/actions action to v6.4.0 (#314) (af58859)", normalized)
        self.assertNotIn("quokkify/project-toolkit", normalized)
        self.assertNotIn("### 🧹 Chores", normalized)

    def test_release_please_body_moves_dependency_chore_and_hides_self_update(self):
        repository = "quokkify/project-toolkit"
        gradle_sha = "a" * 40
        toolkit_sha = "b" * 40
        release_notes = (
            ":robot: I have created a release *beep* *boop*\n---\n\n"
            "## [2.23.2](https://github.com/quokkify/project-toolkit/compare/v2.23.1...v2.23.2) (2026-09-28)\n\n"
            "### 🧹 Chores\n\n"
            "* **deps:** update gradle/actions action to v6.4.0 "
            f"([#314](https://github.com/{repository}/issues/314)) "
            f"([{gradle_sha[:7]}](https://github.com/{repository}/commit/{gradle_sha}))\n"
            "* **deps:** update quokkify/project-toolkit to v2.23.1 "
            f"([#313](https://github.com/{repository}/issues/313)) "
            f"([{toolkit_sha[:7]}](https://github.com/{repository}/commit/{toolkit_sha}))\n\n"
            "chore(deps): update gradle/actions action to v6.4.0\n\n"
            "---\nThis PR was generated with Release Please.\n"
        )
        prs = [
            {"number": 314, "title": "chore(deps): update gradle/actions action to v6.4.0", "body": ""},
            {"number": 313, "title": "chore(deps): update quokkify/project-toolkit to v2.23.1", "body": ""},
        ]
        changelog = notes.enrich_changelog(release_notes, prs, repository=repository)
        changelog = notes.enrich_changelog(changelog, prs, repository=repository)
        body = notes.enrich_release_body(release_notes, "", repository=repository)
        body = notes.enrich_release_body(body, "", repository=repository)
        for output in (changelog, body):
            self.assertIn("### 📦 Dependencies", output)
            self.assertIn("update gradle/actions action to v6.4.0", output)
            self.assertEqual(output.count("update gradle/actions action to v6.4.0"), 1)
            self.assertNotIn("update quokkify/project-toolkit", output)
            self.assertNotIn("chore(deps):", output)
            self.assertNotIn("### 🧹 Chores", output)

    def test_generated_helper_template_renders_without_changing_python_syntax(self):
        template = (ROOT / "templates/project/template/.github/scripts/enrich_release_notes.py.jinja").read_text(encoding="utf-8")
        rendered = Environment(undefined=StrictUndefined).from_string(template).render()
        self.assertEqual(rendered + "\n", (ROOT / "scripts/enrich_release_notes.py").read_text(encoding="utf-8"))

    def test_workflow_delegates_source_discovery_to_the_helper(self):
        workflow = (ROOT / ".github/workflows/release-please.yml").read_text(encoding="utf-8")
        self.assertIn("--prepare", workflow)
        self.assertIn("--release-prs", workflow)
        self.assertIn("--repository", workflow)
        self.assertNotIn("gh pr view", workflow)
        self.assertNotIn("headRepository,baseRepository", workflow)

    def test_extracts_sections_and_preserves_java_fence(self):
        body = "## Description\r\ninternal\r\n## Usage example\r\n```java\r\nVerifier.verify();\r\n```\r\n## Migration\r\n<!-- guidance -->\r\n"
        sections = notes.extract_rich_sections(body)
        self.assertIn("```java\nVerifier.verify();\n```", sections["usage example"])
        self.assertNotIn("migration", sections)

    def test_comments_and_unknown_or_duplicate_headings_are_ignored(self):
        body = "## Highlight\n<!-- only guidance -->\n## Highlight\nsecond\n## Other\nno\n"
        self.assertEqual(notes.extract_rich_sections(body), {})

    def test_enrichment_is_sorted_and_idempotent(self):
        changelog = "# Changelog\n\n## 2.1.0\n\n### ✨ Features\n\n- normal\n"
        prs = [
            {"number": 12, "title": "Second", "body": "## Migration\nUpgrade now."},
            {"number": 4, "title": "First", "body": "## Highlight\nImportant."},
            {"number": 99, "title": "Empty", "body": "## Usage example\n<!-- optional -->"},
        ]
        first = notes.enrich_changelog(changelog, prs)
        self.assertLess(first.index("#### First"), first.index("#### Second"))
        self.assertEqual(notes.enrich_changelog(first, prs), first)
        self.assertEqual(first.count("rich-release-notes"), 2)

    def test_shell_looking_text_is_data(self):
        changelog = "## 1.0.0\n"
        body = "## Release notes\n${{ github.token }}\n$(touch /tmp/pwned)\n"
        output = notes.enrich_changelog(changelog, [{"number": 7, "body": body}])
        self.assertIn("${{ github.token }}", output)
        self.assertIn("$(touch /tmp/pwned)", output)

    def test_cli_writes_generated_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            changelog = root / "CHANGELOG.md"
            prs = root / "prs.json"
            changelog.write_text("## 1.0.0\n", encoding="utf-8")
            prs.write_text('[{"number": 1, "body": "## Highlight\\nHello"}]', encoding="utf-8")
            output = notes.enrich_changelog(changelog.read_text(), [{"number": 1, "body": "## Highlight\nHello"}])
            changelog.write_text(output, encoding="utf-8")
            self.assertIn("### Highlights", changelog.read_text())

    def test_headings_inside_fences_are_not_sections_or_versions(self):
        body = "## Usage example\n```java\n## Migration\n```\nAfter\n"
        self.assertIn("```java\n## Migration\n```\nAfter", notes.extract_rich_sections(body)["usage example"])
        self.assertNotIn("migration", notes.extract_rich_sections(body))
        changelog = "## 1.0.0\n```md\n## fake\n```\n### Features\n- x\n## 0.9.0\n"
        self.assertEqual(len(notes._version_ranges(changelog)), 2)

    def test_release_body_block_is_replaced_once(self):
        body = "## Generated\n" + notes.BLOCK_START + "\nold\n" + notes.BLOCK_END + "\nFooter\n"
        updated = notes.enrich_release_body(body, "new\n```md\n## nested\n```")
        self.assertEqual(updated.count(notes.BLOCK_START), 1)
        self.assertIn("new", updated)
        self.assertNotIn("old", updated)

    def test_release_body_block_is_before_release_please_footer(self):
        body = "## Generated notes\n\n---\nThis PR was generated with Release Please.\n"
        updated = notes.enrich_release_body(body, "### Highlights\nNew")
        self.assertLess(updated.index(notes.BLOCK_START), updated.index("\n---\n"))
        self.assertIn("This PR was generated", updated)

    def test_release_body_without_rich_content_is_byte_preserving(self):
        body = "## 1.0.0\n\n---\nfooter\n\n"
        self.assertEqual(notes.enrich_release_body(body, ""), body)

    def test_component_blocks_are_inside_release_please_details(self):
        body = (
            ":robot: header\n---\n\n\n"
            "<details><summary>backend: 1.2.3</summary>\n\n## 1.2.3\n\n</details>\n\n"
            "<details><summary>frontend: 4.5.6</summary>\n\n## 4.5.6\n\n</details>\n\n"
            "---\nfooter"
        )
        updated = notes.enrich_component_release_body(
            body, {"backend": "Backend rich", "frontend": "Frontend rich"}
        )
        self.assertEqual(updated.count(notes.BLOCK_START), 2)
        self.assertLess(updated.index("Backend rich"), updated.index("</details>"))
        frontend_start = updated.index("<details><summary>frontend")
        self.assertLess(updated.index("Frontend rich", frontend_start), updated.index("</details>", frontend_start))
        self.assertEqual(
            notes.enrich_component_release_body(
                updated, {"backend": "Backend rich", "frontend": "Frontend rich"}
            ),
            updated,
        )

    def test_empty_rich_content_removes_stale_release_body_block(self):
        body = (
            ":robot: header\n---\nnotes\n\n"
            + notes.BLOCK_START
            + "\nstale\n"
            + notes.BLOCK_END
            + "\n---\nThis PR was generated with Release Please.\n"
        )
        updated = notes.enrich_release_body(body, "")
        self.assertNotIn("stale", updated)
        self.assertNotIn(notes.BLOCK_START, updated)
        self.assertEqual(notes.enrich_release_body(updated, ""), updated)

    def test_dependency_entries_share_one_heading_in_both_outputs(self):
        prs = [
            {
                "number": number,
                "title": f"chore(deps): update package {number}",
                "body": "",
                "legacy_dependency": True,
            }
            for number in range(219, 225)
        ]
        changelog = notes.enrich_changelog("## 1.0.0\n", prs)
        body = notes.enrich_release_body("## 1.0.0\n\n---\nfooter\n", notes._render_entries(prs, set()))
        for output in (changelog, body):
            self.assertEqual(output.count(notes.DEPENDENCIES_HEADING), 1)
            for number in range(219, 225):
                self.assertEqual(output.count(f"rich-release-notes pr={number}"), 1)
                self.assertEqual(output.count(f"- update package {number}"), 1)
                self.assertNotIn("chore(deps)", output)

    def test_dependency_heading_is_deduplicated_and_bare_chore_is_hidden(self):
        prs = [
            {"number": 1, "title": "chore(deps): update one", "body": "", "legacy_dependency": True},
            {"number": 2, "title": "deps(deps): update two", "body": "", "legacy_dependency": True},
            {"number": 3, "title": "chore: internal maintenance", "body": ""},
            {
                "number": 4,
                "title": "chore(deps): rich update",
                "body": "## Release notes\n### 📦 Dependencies\nlinked heading\n",
            },
        ]
        output = notes._render_entries(prs, set())
        self.assertEqual(output.count(notes.DEPENDENCIES_HEADING), 1)
        self.assertIn("linked heading", output)
        self.assertNotIn("chore: internal maintenance", output)
        self.assertEqual(output.count("rich-release-notes pr=4"), 1)

    def test_repeated_updates_compact_to_highest_version_with_all_sources(self):
        prs = [
            {"number": number, "title": f"chore(deps): update renovate to v{version}{tail}",
             "body": "", "pr_url": f"https://example.test/pull/{number}"}
            for number, version, tail in (
                (299, "44.115.13", ""), (285, "44.107.0", ""),
                (287, "44.108.2", ""), (288, "44.111.4", ""),
                (290, "44.112.3", ""), (291, "44.115.2", ""),
            )
        ]
        rendered = notes._render_entries(prs, set())
        self.assertEqual(rendered.count("update renovate to v44.115.13"), 1)
        for number in (285, 287, 288, 290, 291, 299):
            self.assertIn(f"[#{number}](https://example.test/pull/{number})", rendered)
        self.assertEqual(notes._rich_numbers(rendered), {"285", "287", "288", "290", "291", "299"})
        self.assertNotIn("v44.107.0", rendered)

    def test_compaction_preserves_rich_sections_and_each_source_marker_once(self):
        prs = [
            {"number": 1, "title": "chore(deps): update poetry to v2.3.4", "body": "## Migration\nKeep this note."},
            {"number": 2, "title": "chore(deps): update poetry to v2.5.1", "body": ""},
        ]
        rendered = notes._render_entries(prs, set())
        self.assertIn("Keep this note.", rendered)
        self.assertIn("- update poetry to v2.5.1", rendered)
        changelog = notes.enrich_changelog("## 2.0.0\n", prs)
        release_body = notes.enrich_release_body("Release notes\n", rendered)
        for output in (rendered, changelog, release_body):
            self.assertIn("Keep this note.", output)
            for number in (1, 2):
                self.assertEqual(output.count(f"rich-release-notes pr={number}"), 1)

    def test_dependency_groups_are_exact_name_and_keep_security(self):
        prs = [
            {"number": 1, "title": "chore(deps): update poetry to v2.3.4 [security]", "body": ""},
            {"number": 2, "title": "chore(deps): update poetry to v2.5.1", "body": ""},
            {"number": 5, "title": "chore(deps): update uv to v0.11.15 [security]", "body": ""},
            {"number": 6, "title": "chore(deps): update uv to v0.12.19", "body": ""},
            {"number": 7, "title": "chore(deps): update corepack to v0.36.0", "body": ""},
            {"number": 3, "title": "chore(deps): update github/codeql-action/init digest to abc1234", "body": ""},
            {"number": 4, "title": "chore(deps): update github/codeql-action/analyze digest to abc1234", "body": ""},
        ]
        rendered = notes._render_entries(prs, set())
        self.assertIn("update poetry to v2.5.1 (#1, #2) [security]", rendered)
        self.assertIn("update github/codeql-action/init digest to abc1234", rendered)
        self.assertIn("update github/codeql-action/analyze digest to abc1234", rendered)
        self.assertEqual(rendered.count("update poetry to"), 1)
        self.assertIn("update uv to v0.12.19 (#5, #6) [security]", rendered)
        self.assertIn("update corepack to v0.36.0 (#7)", rendered)
        self.assertEqual(rendered.count("update uv to"), 1)

    def test_ambiguous_dependency_titles_are_not_compacted(self):
        prs = [
            {"number": 1, "title": "chore(deps): update package to v1.2.3 for compatibility", "body": ""},
            {"number": 2, "title": "chore(deps): update package to v1.2.4-beta.1", "body": ""},
        ]
        rendered = notes._render_entries(prs, set())
        self.assertIn("update package to v1.2.3 for compatibility", rendered)
        self.assertIn("update package to v1.2.4-beta.1", rendered)

    def test_compacted_dependency_block_matches_changelog_and_body_on_rerun(self):
        prs = [
            {"number": 10, "title": "chore(deps): update poetry to v2.3.4 [security]", "body": "",
             "pr_url": "https://example.test/pull/10"},
            {"number": 11, "title": "chore(deps): update poetry to v2.5.1", "body": "",
             "pr_url": "https://example.test/pull/11"},
        ]
        source = "## 2.0.0\n\n### Features\n- keep\n\n## 1.9.0\n\n### Dependencies\n- historical\n"
        first = notes.enrich_changelog(source, prs)
        again = notes.enrich_changelog(first, prs)
        self.assertEqual(first, again)
        self.assertIn("- update poetry to v2.5.1 ([#10](https://example.test/pull/10), [#11](https://example.test/pull/11)) [security]", first)
        self.assertEqual(first.count("update poetry to"), 1)
        self.assertIn("## 1.9.0\n\n### Dependencies\n- historical", first)
        body = notes.enrich_release_body("## release\n\n---\nfooter\n", notes._render_entries(prs, set()))
        self.assertEqual(body.count("update poetry to"), 1)
        self.assertEqual(notes._rich_numbers(notes._render_entries(prs, set())), {"10", "11"})

    def test_duplicate_source_records_render_once(self):
        changelog = "## 1.0.0\n"
        prs = [{"number": 7, "body": "## Highlight\nOne"}] * 2
        output = notes.enrich_changelog(changelog, prs)
        self.assertEqual(output.count("rich-release-notes pr=7"), 1)

    def test_empty_sources_remove_existing_block(self):
        changelog = "## 1.0.0\n\n" + notes.BLOCK_START + "\nold\n" + notes.BLOCK_END + "\n\n### Features\n- x\n"
        updated = notes.enrich_changelog(changelog, [])
        self.assertNotIn(notes.BLOCK_START, updated)
        self.assertIn("### Features", updated)

    def test_legacy_marker_cleanup_preserves_fenced_headings(self):
        changelog = (
            "## 1.0.0\n\n<!-- project-toolkit:rich-release-notes pr=1 -->\n"
            "### Highlights\nold\n```md\n### Keep this\n```\n### Features\n- normal\n"
        )
        updated = notes.enrich_changelog(changelog, [])
        self.assertNotIn("old", updated)
        self.assertIn("### Features", updated)

    def test_legacy_marker_cleanup_in_generated_helper_matches_root(self):
        template = (ROOT / "templates/project/template/.github/scripts/enrich_release_notes.py.jinja").read_text(encoding="utf-8")
        self.assertEqual(template, (ROOT / "scripts/enrich_release_notes.py").read_text(encoding="utf-8"))

    def test_generated_helper_template_renders_without_changing_python_syntax(self):
        template = (ROOT / "templates/project/template/.github/scripts/enrich_release_notes.py.jinja").read_text(encoding="utf-8")
        rendered = Environment(undefined=StrictUndefined).from_string(template).render()
        self.assertEqual(rendered + "\n", (ROOT / "scripts/enrich_release_notes.py").read_text(encoding="utf-8"))

    def test_nested_fences_require_matching_outer_length(self):
        body = "## Usage example\n````markdown\n```java\n## Migration\n```\n````\n## Migration\nApply it.\n"
        sections = notes.extract_rich_sections(body)
        self.assertIn("usage example", sections)
        self.assertIn("migration", sections)
        changelog = "## 1.0.0\n````md\n## fake\n```\n````\n## 0.9.0\n"
        self.assertEqual(len(notes._version_ranges(changelog)), 2)

    def test_reserved_delimiters_in_pr_body_are_rejected(self):
        body = f"## Highlight\nattacker {notes.BLOCK_END} tail"
        output = notes.enrich_changelog("## 1.0.0\n", [{"number": 7, "body": body}])
        self.assertNotIn(notes.BLOCK_START, output)
        self.assertEqual(notes.enrich_changelog(output, [{"number": 7, "body": body}]), output)

    def test_reserved_delimiters_in_pr_title_are_rejected(self):
        title = f"unsafe {notes.BLOCK_END} title"
        prs = [{"number": 7, "title": title, "body": "## Highlight\ncontent"}]
        output = notes.enrich_changelog("## 1.0.0\n", prs)
        self.assertNotIn(notes.BLOCK_START, output)
        self.assertEqual(notes.enrich_changelog(output, prs), output)

    def test_legacy_marker_injection_in_title_or_body_is_rejected(self):
        injected = "<!-- project-toolkit:rich-release-notes pr=999 -->"
        for pr in (
            {"number": 7, "title": injected, "body": "## Highlight\ncontent"},
            {"number": 7, "title": "safe", "body": f"## Highlight\n{injected}"},
        ):
            with self.subTest(pr=pr):
                output = notes.enrich_changelog("## 1.0.0\n", [pr])
                self.assertNotIn(notes.BLOCK_START, output)

    def test_plain_marker_lookalike_in_rich_text_is_rejected(self):
        pr = {
            "number": 7,
            "title": "safe",
            "body": "## Highlight\nproject-toolkit:rich-release-notes pr=999",
        }
        output = notes.enrich_changelog("## 1.0.0\n", [pr])
        self.assertNotIn(notes.BLOCK_START, output)
        self.assertEqual(notes._rich_numbers(output), set())

    def test_workflow_parser_contract_tracks_nested_fences_and_rich_block(self):
        workflow = (ROOT / ".github/workflows/release-please.yml").read_text(encoding="utf-8")
        self.assertNotIn("python - <<'PY'", workflow)
        self.assertNotIn("baseRepository", workflow)
        self.assertNotIn("--slurp", workflow)
        self.assertEqual(workflow.count("uses: actions/checkout@"), 3)
        self.assertEqual(workflow.count("name: Prepare enriched release notes"), 1)
        self.assertIn("ENRICHMENT_SCRIPT: ${{ inputs.enrichment-script }}", workflow)
        self.assertIn('python "$ENRICHMENT_SCRIPT" "${arguments[@]}"', workflow)

    def test_standard_attribution_rejects_a_second_pr_like_title_link(self):
        commit = "a" * 40
        changelog = (
            "## 1.2.3\n\n### Features\n"
            "* title [#99](https://github.com/acme/widget/pull/99) "
            "([#7](https://github.com/acme/widget/issues/7)) "
            f"([abcdef0](https://github.com/acme/widget/commit/{commit}))\n"
        )
        self.assertEqual(notes.source_pr_numbers(changelog, "acme/widget"), [7])

    def test_source_discovery_ignores_fences_machine_blocks_and_older_versions(self):
        commit = "b" * 40
        canonical = (
            "* valid ([#8](https://github.com/acme/widget/pull/8)) "
            f"([abcdef1](https://github.com/acme/widget/commit/{commit}))\n"
        )
        changelog = (
            "## 2.0.0\n````md\n```java\n## fake\n```\n````\n"
            + notes.BLOCK_START
            + "\n"
            + canonical.replace("#8", "#9").replace("/8", "/9")
            + notes.BLOCK_END
            + "\n"
            + canonical
            + "## 1.0.0\n"
            + canonical.replace("#8", "#10").replace("/8", "/10")
        )
        self.assertEqual(notes.source_pr_numbers(changelog, "acme/widget"), [8])

    def test_legacy_dependency_entries_are_rendered_under_dependencies(self):
        rendered = notes._render_entries(
            [{
                "number": 219,
                "title": "chore(deps): update allure",
                "body": "",
                "legacy_dependency": True,
            }],
            set(),
        )
        self.assertIn("### 📦 Dependencies", rendered)
        self.assertIn("- update allure", rendered)
        self.assertNotIn("chore(deps)", rendered)

    def test_dependency_titles_render_as_bullets_without_conventional_prefix(self):
        rendered = notes._render_entries(
            [
                {"number": 1, "title": "chore(deps): update one", "legacy_dependency": True},
                {"number": 2, "title": "deps(deps): update two (#2)", "legacy_dependency": True},
            ],
            set(),
        )
        self.assertEqual(rendered.count("### 📦 Dependencies"), 1)
        self.assertIn("- update one", rendered)
        self.assertIn("- update two (#2)", rendered)
        self.assertNotIn("chore(deps)", rendered)
        self.assertNotIn("deps(deps)", rendered)

    def test_rich_dependency_content_is_preserved_for_legacy_mapping(self):
        rendered = notes._render_entries(
            [{
                "number": 1,
                "title": "chore(deps): update one",
                "body": "## Dependencies\n- update one with compatibility note",
                "legacy_dependency": True,
            }],
            set(),
        )
        self.assertIn("- update one with compatibility note", rendered)
        self.assertNotIn("chore(deps)", rendered)

    def test_rich_linked_dependency_entries_are_bulleted_and_prefix_free(self):
        rendered = notes._render_entries(
            [{
                "number": 12,
                "title": "dependency summary",
                "body": (
                    "## Dependencies\n"
                    "chore(deps): [update one](https://example.test/one)\n"
                    "deps(deps): [update two](https://example.test/two)\n"
                ),
            }],
            set(),
        )
        self.assertIn("- [update one](https://example.test/one)", rendered)
        self.assertIn("- [update two](https://example.test/two)", rendered)
        self.assertNotIn("chore(deps)", rendered)
        self.assertNotIn("deps(deps)", rendered)

    def test_dependency_markers_stay_inside_one_contiguous_markdown_list(self):
        rendered = notes._render_entries(
            [
                {
                    "number": 1,
                    "title": "chore(deps): update one",
                    "body": "",
                    "legacy_dependency": True,
                },
                {
                    "number": 2,
                    "title": "deps(deps): [update two](https://example.test/two)",
                    "body": "",
                    "legacy_dependency": True,
                },
                {"number": 3, "title": "feature", "body": "## Highlight\nUseful note"},
            ],
            set(),
        )
        self.assertEqual(
            rendered,
            "\n".join(
                [
                    "### 📦 Dependencies",
                    "- update one (#1) <!-- project-toolkit:rich-release-notes pr=1 -->",
                    "- [update two](https://example.test/two) (#2) <!-- project-toolkit:rich-release-notes pr=2 -->",
                    "<!-- project-toolkit:rich-release-notes pr=3 -->",
                    "#### feature",
                    "### Highlights",
                    "Useful note",
                ]
            ),
        )
        self.assertEqual(notes._rich_numbers(rendered), {"1", "2", "3"})
        self.assertNotIn("\n\n-", rendered)

    def test_interleaved_rich_entries_keep_dependency_list_contiguous(self):
        rendered = notes._render_entries(
            [
                {"number": 1, "title": "chore(deps): update one", "legacy_dependency": True},
                {"number": 2, "title": "Highlight", "body": "## Highlight\nUseful note"},
                {"number": 3, "title": "chore(deps): update three", "legacy_dependency": True},
            ],
            set(),
        )
        self.assertEqual(rendered.count(notes.DEPENDENCIES_HEADING), 1)
        dependency_start = rendered.index(notes.DEPENDENCIES_HEADING)
        highlight_start = rendered.index("<!-- project-toolkit:rich-release-notes pr=2 -->")
        dependency_end = rendered.index("<!-- project-toolkit:rich-release-notes pr=3 -->")
        self.assertLess(dependency_start, highlight_start)
        self.assertLess(dependency_end, highlight_start)
        self.assertEqual(
            rendered[dependency_start:highlight_start].count("\n- "),
            2,
        )
        self.assertNotIn("\n\n", rendered[dependency_start:highlight_start])

    def test_mixed_dependency_and_highlight_stays_before_later_dependency(self):
        rendered = notes._render_entries(
            [
                {"number": 1, "title": "chore(deps): update one", "legacy_dependency": True},
                {
                    "number": 2,
                    "title": "mixed update",
                    "body": "## Dependencies\n- update two\n## Highlight\nUseful note",
                },
                {"number": 3, "title": "chore(deps): update three", "legacy_dependency": True},
            ],
            set(),
        )
        dependency_start = rendered.index(notes.DEPENDENCIES_HEADING)
        highlight_start = rendered.index("### Highlights")
        dependency_text = rendered[dependency_start:highlight_start]
        self.assertEqual(dependency_text.count("\n- "), 3)
        self.assertEqual(dependency_text.count("rich-release-notes pr="), 3)
        self.assertLess(dependency_text.index("update one"), dependency_text.index("update two"))
        self.assertLess(dependency_text.index("update two"), dependency_text.index("update three"))
        self.assertEqual(rendered.count("### Highlights"), 1)
        self.assertEqual(rendered.count("Useful note"), 1)

    def test_rich_number_parser_accepts_only_machine_marker_shapes(self):
        marker = "<!-- project-toolkit:rich-release-notes pr=999 -->"
        text = "\n".join(
            [
                marker,
                f"- generated dependency {marker}",
                f"literal documentation example: {marker} trailing text",
                f"- malformed {marker} trailing text",
                f"- multiple {marker} {marker}",
                "```md",
                marker,
                f"- fenced dependency {marker}",
                "```",
            ]
        )
        self.assertEqual(notes._rich_numbers(text), {"999"})
        self.assertEqual(
            notes._rich_numbers(
                f"literal documentation example: {marker} trailing text\n"
                f"- malformed {marker} trailing text\n"
                f"- multiple {marker} {marker}"
            ),
            set(),
        )

    def test_documentation_marker_substring_does_not_suppress_current_pr(self):
        changelog = (
            "## 1.0.0\n\n"
            "literal documentation example: "
            "<!-- project-toolkit:rich-release-notes pr=999 --> trailing text\n"
        )
        prs = [{"number": 999, "title": "Current highlight", "body": "## Highlight\nCurrent note"}]
        output = notes.enrich_changelog(changelog, prs)
        self.assertIn("Current note", output)
        self.assertIn("rich-release-notes pr=999", output)

    def test_title_only_legacy_dependency_keeps_pr_and_commit_attribution(self):
        sha = "a" * 40
        rendered = notes._render_entries(
            [{
                "number": 219,
                "title": "chore(deps): update allure",
                "body": "",
                "legacy_dependency": True,
                "pr_url": "https://github.com/acme/widget/pull/219",
                "commit_sha": sha,
                "commit_url": f"https://github.com/acme/widget/commit/{sha}",
            }],
            set(),
        )
        self.assertIn("- update allure", rendered)
        self.assertIn("[#219](https://github.com/acme/widget/pull/219)", rendered)
        self.assertIn(f"[aaaaaaa](https://github.com/acme/widget/commit/{sha})", rendered)
        self.assertNotIn("chore(deps)", rendered)

    def test_legacy_dependency_entries_render_exactly_once_in_changelog_and_body(self):
        prs = [
            {
                "number": number,
                "title": f"chore(deps): update package-{number}",
                "body": "",
                "legacy_dependency": True,
            }
            for number in range(219, 225)
        ]
        changelog = notes.enrich_changelog("## 2.21.1\n", prs)
        top = changelog[: notes._version_ranges(changelog)[0][1]]
        rich = notes._render_entries(prs, set())
        release_body = notes.enrich_release_body("## 2.21.1\n\n---\nfooter\n", rich)
        for number in range(219, 225):
            title = f"chore(deps): update package-{number}"
            with self.subTest(number=number):
                self.assertEqual(top.count(title), 0)
                self.assertEqual(release_body.count(title), 0)
                self.assertEqual(top.count(f"- update package-{number}"), 1)
                self.assertEqual(release_body.count(f"- update package-{number}"), 1)

    def test_legacy_dependency_discovery_uses_previous_release_tag(self):
        changelog = "## 2.21.1\n\n## 2.21.0\n"
        completed = subprocess.CompletedProcess(
            ["git"],
            0,
            "chore(deps): update allure (#219)\nchore: cleanup (#999)\n",
            "",
        )
        with mock.patch.object(notes, "_run_git", return_value=completed) as run_git:
            self.assertEqual(notes.legacy_dependency_pr_numbers(changelog), [219])
        run_git.assert_called_once_with(["log", "v2.21.0..HEAD", "--format=%s"])

    def test_legacy_dependency_discovery_accepts_linked_release_headings(self):
        changelog = (
            "## [2.21.1](https://github.com/acme/widget/releases/tag/v2.21.1)\n\n"
            "## [2.21.0](https://github.com/acme/widget/releases/tag/v2.21.0)\n"
        )
        completed = subprocess.CompletedProcess(
            ["git"], 0, "chore(deps): update node (#224)\n", ""
        )
        with mock.patch.object(notes, "_run_git", return_value=completed) as run_git:
            self.assertEqual(notes.legacy_dependency_pr_numbers(changelog), [224])
        run_git.assert_called_once_with(["log", "v2.21.0..HEAD", "--format=%s"])

    def test_legacy_dependency_discovery_fails_closed_when_history_is_missing(self):
        changelog = "## 2.21.1\n\n## 2.21.0\n"
        with mock.patch.object(
            notes, "_run_git", side_effect=notes.EnrichmentError("missing tag")
        ):
            with self.assertRaisesRegex(notes.EnrichmentError, "missing tag"):
                notes.legacy_dependency_pr_numbers(changelog)

    def test_manifest_discovers_package_local_and_root_relative_changelogs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            manifest = root / "manifest.json"
            config.write_text(
                json.dumps(
                    {
                        "changelog-path": "CHANGELOG.md",
                        "packages": {
                            "backend": {"package-name": "backend"},
                            "frontend": {"package-name": "frontend", "changelog-path": "docs/NEWS.md"},
                            "worker": {"package-name": "worker", "changelog-path": "/WORKER.md"},
                        },
                    }
                ),
                encoding="utf-8",
            )
            manifest.write_text(
                json.dumps({"backend": "1.0.0", "frontend": "2.0.0", "worker": "3.0.0"}),
                encoding="utf-8",
            )
            paths = notes.discover_changelog_paths(
                mode="manifest",
                package_path=".",
                config_file=config,
                manifest_file=manifest,
                config_backed_single=False,
            )
        self.assertEqual(
            [path.as_posix() for path in paths],
            ["WORKER.md", "backend/CHANGELOG.md", "frontend/docs/NEWS.md"],
        )

    def test_legacy_single_path_does_not_require_config(self):
        paths = notes.discover_changelog_paths(
            mode="single",
            package_path="package",
            config_file=Path("missing-config.json"),
            manifest_file=Path("missing-manifest.json"),
            config_backed_single=False,
        )
        self.assertEqual(paths, [Path("package/CHANGELOG.md")])

    def test_generated_helper_is_the_same_behavioral_contract(self):
        generated = ROOT / "templates/project/template/.github/scripts/enrich_release_notes.py.jinja"
        self.assertEqual(generated.read_bytes(), (ROOT / "scripts/enrich_release_notes.py").read_bytes())
        compile(generated.read_text(encoding="utf-8"), str(generated), "exec")

    def test_generated_release_body_placement_and_rerun_match_root(self):
        namespace: dict[str, Any] = {"__name__": "generated_helper"}
        generated = ROOT / "templates/project/template/.github/scripts/enrich_release_notes.py.jinja"
        exec(compile(generated.read_text(encoding="utf-8"), str(generated), "exec"), namespace)
        body = ":robot: header\n---\nnotes\n---\nThis PR was generated with Release Please.\n"
        enrich_body = cast(Callable[[str, str], str], namespace["enrich_release_body"])
        first = enrich_body(body, "### Highlights\nNew")
        second = enrich_body(first, "### Highlights\nNew")
        self.assertEqual(first, second)
        self.assertLess(first.index(notes.BLOCK_START), first.rindex("\n---\n"))
        self.assertTrue(first.startswith(":robot: header\n---\nnotes"))
        self.assertTrue(first.endswith("This PR was generated with Release Please.\n"))

    def test_prepare_fails_closed_for_zero_or_multiple_release_prs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release_prs = root / "prs.json"
            for payload in ([], [{"number": 1}, {"number": 2}]):
                release_prs.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(notes.EnrichmentError, "exactly one"):
                    notes.prepare_release_enrichment(
                        repository="acme/widget",
                        release_prs_file=release_prs,
                        mode="single",
                        package_path=".",
                        config_file=root / "config.json",
                        manifest_file=root / "manifest.json",
                        config_backed_single=False,
                        output_directory=root / "output",
                    )

    def test_prepare_rejects_unmerged_or_cross_repository_source_pr(self):
        release = {
            "number": 50,
            "body": "notes",
            "head": {"repo": {"full_name": "acme/widget"}},
            "base": {"repo": {"full_name": "acme/widget"}},
        }
        source = {
            "number": 7,
            "state": "open",
            "merged_at": None,
            "title": "source",
            "body": "## Highlight\nnew",
            "head": {"repo": {"full_name": "acme/widget"}},
            "base": {"repo": {"full_name": "acme/widget"}},
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "CHANGELOG.md").write_text(self._changelog(7), encoding="utf-8")
            (root / "prs.json").write_text('[{"number": 50}]', encoding="utf-8")
            with (
                mock.patch.object(notes, "_run_gh"),
                mock.patch.object(notes, "_gh_json", side_effect=[release, source]),
            ):
                previous = Path.cwd()
                os.chdir(root)
                try:
                    with self.assertRaisesRegex(notes.EnrichmentError, "not merged"):
                        notes.prepare_release_enrichment(
                            repository="acme/widget",
                            release_prs_file=root / "prs.json",
                            mode="single",
                            package_path=".",
                            config_file=root / "config.json",
                            manifest_file=root / "manifest.json",
                            config_backed_single=False,
                            output_directory=root / "output",
                        )
                finally:
                    os.chdir(previous)

    @staticmethod
    def _changelog(number: int) -> str:
        commit = (hex(number)[2:] or "a") * 40
        commit = commit[:40].ljust(40, "a")
        return (
            f"## 1.0.{number}\n\n### Features\n* change "
            f"([#{number}](https://github.com/acme/widget/issues/{number})) "
            f"([abcdef0](https://github.com/acme/widget/commit/{commit}))\n"
        )

    def test_end_to_end_manifest_cli_uses_gh_245_shapes_and_places_body(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for package in ("backend", "frontend", "worker"):
                (root / package).mkdir()
            for number, package in enumerate(("backend", "frontend", "worker"), 1):
                (root / package / "CHANGELOG.md").write_text(
                    self._changelog(number), encoding="utf-8"
                )
            (root / "config.json").write_text(
                json.dumps(
                    {
                        "packages": {
                            name: {"package-name": name}
                            for name in ("backend", "frontend", "worker")
                        }
                    }
                ),
                encoding="utf-8",
            )
            (root / "manifest.json").write_text(
                json.dumps({name: "1.0.0" for name in ("backend", "frontend", "worker")}),
                encoding="utf-8",
            )
            (root / "release-prs.json").write_text('[{"number": 50}]', encoding="utf-8")
            fake_data = {
                "50": self._pull_payload(
                    50,
                    body=(
                        ":robot: header\n---\n\n\n"
                        "<details><summary>backend: 1.0.1</summary>\n\n## 1.0.1\n\n</details>\n\n"
                        "<details><summary>frontend: 1.0.2</summary>\n\n## 1.0.2\n\n</details>\n\n"
                        "<details><summary>worker: 1.0.3</summary>\n\n## 1.0.3\n\n</details>\n\n"
                        "---\nThis PR was generated with Release Please.\n"
                    ),
                ),
                "1": self._pull_payload(1, body="## Highlight\nBackend"),
                "2": self._pull_payload(2, body="## Usage example\n```js\nrun();\n```"),
                "3": self._pull_payload(3, body="## Migration\nUpgrade worker"),
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
            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{bin_dir}:{env['PATH']}",
                    "FAKE_GH_DATA": str(data_path),
                    "FAKE_GH_LOG": str(log_path),
                }
            )
            command = [
                sys.executable,
                str(ROOT / "scripts/enrich_release_notes.py"),
                "--prepare",
                "--repository",
                "acme/widget",
                "--release-prs",
                "release-prs.json",
                "--mode",
                "manifest",
                "--config-file",
                "config.json",
                "--manifest-file",
                "manifest.json",
                "--output-directory",
                "output",
            ]
            first = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            snapshot = {
                path: (root / path).read_text(encoding="utf-8")
                for path in ("backend/CHANGELOG.md", "frontend/CHANGELOG.md", "worker/CHANGELOG.md")
            }
            second = subprocess.run(command, cwd=root, env=env, text=True, capture_output=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(
                snapshot,
                {path: (root / path).read_text(encoding="utf-8") for path in snapshot},
            )
            metadata = json.loads((root / "output/metadata.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["source_prs"], [1, 2, 3])
            self.assertEqual(metadata["rendered_prs"], [1, 2, 3])
            for number, path in enumerate(snapshot, 1):
                self.assertIn(f"rich-release-notes pr={number}", snapshot[path])
                self.assertEqual(snapshot[path].count("rich-release-notes"), 1)
            release_body = json.loads(
                (root / "output/release-body.json").read_text(encoding="utf-8")
            )["body"]
            self.assertEqual(release_body.count(notes.BLOCK_START), 3)
            backend_end = release_body.index("</details>")
            frontend_start = release_body.index("<details><summary>frontend")
            frontend_end = release_body.index("</details>", frontend_start)
            worker_start = release_body.index("<details><summary>worker")
            worker_end = release_body.index("</details>", worker_start)
            self.assertLess(release_body.index("Backend"), backend_end)
            self.assertLess(release_body.index("```js\nrun();\n```"), frontend_end)
            self.assertGreater(release_body.index("```js\nrun();\n```"), frontend_start)
            self.assertLess(release_body.index("Upgrade worker"), worker_end)
            self.assertGreater(release_body.index("Upgrade worker"), worker_start)
            calls = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            self.assertIn(["pr", "checkout", "50", "--repo", "acme/widget", "--force"], calls)
            self.assertTrue(all("--slurp" not in call for call in calls))
            self.assertTrue(all("--json" not in call for call in calls))

    @staticmethod
    def _pull_payload(number: int, *, body: str) -> dict[str, object]:
        return {
            "number": number,
            "state": "closed",
            "merged_at": "2026-01-01T00:00:00Z" if number != 50 else None,
            "title": f"PR {number}",
            "body": body,
            "head": {"repo": {"full_name": "acme/widget"}},
            "base": {"repo": {"full_name": "acme/widget"}},
        }

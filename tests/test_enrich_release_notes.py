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
    def test_enrich_handles_only_its_custom_release_sections(self):
        self.assertEqual(notes.RICH_HEADINGS, {
            "highlight": "✨ Highlights",
            "usage example": "💡 Usage Examples",
            "migration": "🔄 Migration",
        })
        sections = notes.extract_rich_sections(
            "## Release notes\nignore\n## Breaking change\nignore\n## Migration\nUse v2."
        )
        self.assertEqual(sections, {"migration": "Use v2."})

    def test_release_please_chore_and_dependency_content_is_untouched(self):
        changelog = (
            "## 2.23.2\n\n### 🧹 Chores\n\n"
            "- deps: update example package (#314)\n"
        )
        prs = [{"number": 314, "title": "chore(deps): update example package", "body": ""}]
        self.assertEqual(notes.enrich_changelog(changelog, prs), changelog)
        self.assertEqual(
            notes.enrich_release_body(changelog, notes._render_entries(prs, set())),
            changelog,
        )

    def test_release_please_owns_breaking_changes(self):
        release_body = (
            "## 3.0.0\n\n### ⚠ BREAKING CHANGES\n\n"
            "- remove the deprecated API\n\n---\nRelease Please footer\n"
        )
        prs = [{
            "number": 8,
            "title": "feat!: remove the deprecated API",
            "body": "## Breaking change\nRemove the deprecated API.",
        }]
        updated = notes.enrich_release_body(release_body, notes._render_entries(prs, set()))
        self.assertIn("### ⚠ BREAKING CHANGES", updated)
        self.assertNotIn("### ⚠️ Breaking Changes", updated)
        self.assertNotIn("Remove the deprecated API.", updated)
        self.assertEqual(updated.count("deprecated API"), 1)

    def test_dependency_pr_can_contribute_only_its_migration_note(self):
        prs = [{
            "number": 5,
            "title": "chore(deps): update one",
            "body": "## Migration\nChange the import path.\n## Dependencies\nDo not render this.",
        }]
        rendered = notes._render_entries(prs, set())
        self.assertIn("### 🔄 Migration", rendered)
        self.assertIn("Change the import path.", rendered)
        self.assertNotIn("Dependencies", rendered)
        self.assertNotIn("chore(deps)", rendered)

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
        self.assertLess(first.index("Important."), first.index("Upgrade now."))
        self.assertEqual(notes.enrich_changelog(first, prs), first)
        self.assertEqual(first.count("rich-release-notes"), 2)

    def test_release_notes_section_is_ignored(self):
        changelog = "## 1.0.0\n"
        body = "## Release notes\n${{ github.token }}\n$(touch /tmp/pwned)\n"
        output = notes.enrich_changelog(changelog, [{"number": 7, "body": body}])
        self.assertNotIn("${{ github.token }}", output)
        self.assertNotIn("$(touch /tmp/pwned)", output)
        self.assertNotIn(notes.BLOCK_START, output)

    def test_cli_writes_generated_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            changelog = root / "CHANGELOG.md"
            prs = root / "prs.json"
            changelog.write_text("## 1.0.0\n", encoding="utf-8")
            prs.write_text('[{"number": 1, "body": "## Highlight\\nHello"}]', encoding="utf-8")
            output = notes.enrich_changelog(changelog.read_text(), [{"number": 1, "body": "## Highlight\nHello"}])
            changelog.write_text(output, encoding="utf-8")
            self.assertIn("### ✨ Highlights", changelog.read_text())

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

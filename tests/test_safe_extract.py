"""Execute the shipped Allure extractor against local ZIP fixtures."""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import warnings
import zipfile
from pathlib import Path
from unittest import TestCase, main

import yaml

ROOT = Path(__file__).resolve().parents[1]
EXTRACTOR = ROOT / "template/.github/allure/safe_extract.py.jinja"
CORE = yaml.safe_load((ROOT / ".github/workflows/allure-publisher-core.yml").read_text())
FRAGMENT = "ci-env-fragment.properties"


class SafeExtractTest(TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="safe-extract-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.archives = self.root / "archives"
        self.archives.mkdir()
        self.materialized = self.workspace / ".allure-input/source-artifacts"

    def write_archive(self, name: str, members: list[tuple[str, str]], *, symlink: str | None = None) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(self.archives / f"{name}.zip", "w") as archive:
                for relative, content in members:
                    archive.writestr(relative, content)
                if symlink is not None:
                    link = zipfile.ZipInfo(symlink)
                    link.external_attr = (stat.S_IFLNK | 0o777) << 16
                    archive.writestr(link, "/etc/passwd")

    def mark_first_member_encrypted(self, name: str) -> None:
        path = self.archives / f"{name}.zip"
        data = bytearray(path.read_bytes())
        central = data.index(b"PK\x01\x02")
        data[central + 8] |= 0x1
        path.write_bytes(bytes(data))

    def extract(self, names: list[str], layout: str | None) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "ARTIFACT_MANIFEST": json.dumps([{"name": name, "id": index} for index, name in enumerate(names, 1)]),
            "ARTIFACT_ARCHIVE_DIR": str(self.archives),
            "ARCHIVE_ROOT": str(self.root / "downloaded"),
            "OUTPUT_ROOT": str(self.root / "expanded"),
            "MATERIALIZE_ROOT": str(self.materialized),
            "GITHUB_WORKSPACE": str(self.workspace),
        }
        env.pop("MATERIALIZE_LAYOUT", None)
        if layout is not None:
            env["MATERIALIZE_LAYOUT"] = layout
        return subprocess.run(
            [sys.executable, "-I", str(EXTRACTOR)],
            cwd=self.workspace,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )

    def materialized_files(self) -> set[str]:
        return {path.relative_to(self.materialized).as_posix() for path in self.materialized.rglob("*") if path.is_file()}

    def test_flat_bundles_with_fragments_get_one_results_directory_per_artifact(self) -> None:
        self.write_archive(
            "allure-results-backend",
            [(FRAGMENT, "Backend.Module=backend\n"), ("b-result.json", "{}"), ("b-container.json", "{}")],
        )
        self.write_archive("allure-results-frontend", [(FRAGMENT, "Frontend.Module=frontend\n"), ("f-result.json", "{}")])

        result = self.extract(["allure-results-backend", "allure-results-frontend"], "per-artifact")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.materialized_files(),
            {
                f"allure-results-backend/allure-results/{FRAGMENT}",
                "allure-results-backend/allure-results/b-result.json",
                "allure-results-backend/allure-results/b-container.json",
                f"allure-results-frontend/allure-results/{FRAGMENT}",
                "allure-results-frontend/allure-results/f-result.json",
            },
        )
        self.assertEqual(
            (self.materialized / f"allure-results-frontend/allure-results/{FRAGMENT}").read_text(),
            "Frontend.Module=frontend\n",
        )

    def test_bundles_uploaded_with_an_allure_results_root_keep_it_without_doubling(self) -> None:
        names = ["allure-results-tests_v1_healthy_bats-1", "allure-results-tests_v2_healthy_bats-1"]
        for name in names:
            module = name.removeprefix("allure-results-").removesuffix("-1")
            self.write_archive(
                name,
                [(f"allure-results/{FRAGMENT}", f"{module}.Module={module}\n"), (f"allure-results/{module}-result.json", "{}")],
            )

        result = self.extract(names, "per-artifact")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.materialized_files(),
            {
                f"allure-results-tests_v1_healthy_bats-1/allure-results/{FRAGMENT}",
                "allure-results-tests_v1_healthy_bats-1/allure-results/tests_v1_healthy_bats-result.json",
                f"allure-results-tests_v2_healthy_bats-1/allure-results/{FRAGMENT}",
                "allure-results-tests_v2_healthy_bats-1/allure-results/tests_v2_healthy_bats-result.json",
            },
        )

    def test_flat_bundle_without_a_fragment_becomes_one_results_directory(self) -> None:
        self.write_archive("allure-results-java", [("a-result.json", "{}"), ("a-container.json", "{}")])

        result = self.extract(["allure-results-java"], "per-artifact")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.materialized_files(),
            {"allure-results-java/allure-results/a-result.json", "allure-results-java/allure-results/a-container.json"},
        )

    def test_per_artifact_layout_preserves_deeper_results_directories(self) -> None:
        self.write_archive("external-one", [("service/build/allure-results/x-result.json", "{}"), ("README.txt", "x")])

        result = self.extract(["external-one"], "per-artifact")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.materialized_files(),
            {"external-one/service/build/allure-results/x-result.json", "external-one/README.txt"},
        )

    def test_per_artifact_layout_still_rejects_duplicates_inside_one_artifact(self) -> None:
        self.write_archive("allure-results-backend", [(FRAGMENT, "a\n"), (FRAGMENT, "b\n")])
        self.write_archive("allure-results-frontend", [(FRAGMENT, "c\n")])

        result = self.extract(["allure-results-backend", "allure-results-frontend"], "per-artifact")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(f"duplicate artifact path: allure-results-backend/allure-results/{FRAGMENT}", result.stderr)
        self.assertFalse(self.materialized.exists())

    def test_per_artifact_layout_still_rejects_unsafe_member_paths(self) -> None:
        self.write_archive("allure-results-backend", [("b-result.json", "{}")])
        self.write_archive("allure-results-frontend", [("../escape-result.json", "{}")])

        result = self.extract(["allure-results-backend", "allure-results-frontend"], "per-artifact")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe path in artifact ZIP: allure-results-frontend/../escape-result.json", result.stderr)
        self.assertFalse(self.materialized.exists())

    def test_per_artifact_layout_rejects_artifact_names_the_report_action_would_not_scan(self) -> None:
        for name in ("allure-results", "node_modules"):
            with self.subTest(name=name):
                self.write_archive(name, [("x-result.json", "{}")])

                result = self.extract([name], "per-artifact")

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"artifact names conflict with the per-artifact layout: {name}", result.stderr)
                self.assertFalse(self.materialized.exists())

    def test_per_artifact_layout_rejects_results_hidden_below_skipped_directories(self) -> None:
        self.write_archive("external-one", [("node_modules/pkg/allure-results/x-result.json", "{}")])

        result = self.extract(["external-one"], "per-artifact")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("artifact external-one has no scannable allure-results directory", result.stderr)
        self.assertFalse(self.materialized.exists())

    def test_per_artifact_layout_rejects_results_outside_the_scanned_directory(self) -> None:
        cases = {
            "loose": [("allure-results/a-result.json", "{}"), ("b-result.json", "{}")],
            "skipped": [("allure-results/a-result.json", "{}"), (".gradle/allure-results/b-container.json", "{}")],
        }
        for case, members in cases.items():
            with self.subTest(case=case):
                self.write_archive("external-one", members)

                result = self.extract(["external-one"], "per-artifact")

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "artifact external-one has Allure results outside a scannable allure-results directory", result.stderr
                )
                self.assertFalse(self.materialized.exists())

    def test_per_artifact_layout_rejects_members_nested_below_the_results_root(self) -> None:
        cases = {
            "flat": ([("a-result.json", "{}"), ("attachments/a.png", "x")], "attachments/a.png"),
            "rooted": ([("allure-results/a-result.json", "{}"), ("allure-results/data/a.png", "x")], "allure-results/data/a.png"),
        }
        for case, (members, member) in cases.items():
            with self.subTest(case=case):
                self.write_archive("external-one", members)

                result = self.extract(["external-one"], "per-artifact")

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    f"artifact external-one: only regular files directly under allure-results are read: {member}",
                    result.stderr,
                )
                self.assertFalse(self.materialized.exists())

    def test_per_artifact_layout_still_rejects_symlink_and_encrypted_members(self) -> None:
        self.write_archive("allure-results-backend", [("b-result.json", "{}")], symlink="link-result.json")
        result = self.extract(["allure-results-backend"], "per-artifact")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("non-regular ZIP entry rejected: allure-results-backend/link-result.json", result.stderr)
        self.assertFalse(self.materialized.exists())

        self.write_archive("allure-results-backend", [("b-result.json", "{}")])
        self.mark_first_member_encrypted("allure-results-backend")
        result = self.extract(["allure-results-backend"], "per-artifact")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("encrypted ZIP entry rejected: allure-results-backend/b-result.json", result.stderr)
        self.assertFalse(self.materialized.exists())

    def test_file_and_directory_conflicts_fail_cleanly_in_both_layouts(self) -> None:
        cases = {
            None: [("notes", "x"), ("notes/a.txt", "y")],
            "per-artifact": [("allure-results/a-result.json", "{}"), ("notes", "x"), ("notes/a.txt", "y")],
        }
        for layout, members in cases.items():
            with self.subTest(layout=layout):
                self.write_archive("external-one", members)

                result = self.extract(["external-one"], layout)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("artifact entry conflicts with another extracted path: external-one/notes", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(self.materialized.exists())

    def test_merged_layout_is_the_default_and_still_rejects_cross_artifact_duplicates(self) -> None:
        self.write_archive("allure-results-backend", [(FRAGMENT, "a\n")])
        self.write_archive("allure-results-frontend", [(FRAGMENT, "a\n")])

        for layout in (None, "merged"):
            with self.subTest(layout=layout):
                result = self.extract(["allure-results-backend", "allure-results-frontend"], layout)

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"duplicate artifact path: {FRAGMENT}", result.stderr)

    def test_merged_layout_keeps_member_paths_unprefixed(self) -> None:
        self.write_archive("allure-results-app-java", [("a-result.json", "{}")])

        result = self.extract(["allure-results-app-java"], None)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.materialized_files(), {"a-result.json"})

    def test_unknown_layout_fails_closed(self) -> None:
        self.write_archive("allure-results-backend", [("b-result.json", "{}")])

        result = self.extract(["allure-results-backend"], "flat")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported MATERIALIZE_LAYOUT: flat", result.stderr)
        self.assertFalse(self.materialized.exists())

    def test_core_download_selects_per_artifact_layout_only_for_source_artifacts(self) -> None:
        step = next(
            step for step in CORE["jobs"]["download"]["steps"]
            if step.get("run") == "python .toolkit/modules/allure/template/.github/allure/safe_extract.py.jinja"
        )
        self.assertEqual(
            step["env"]["MATERIALIZE_LAYOUT"],
            "${{ needs.resolve.outputs.source-artifacts-directory != '' && 'per-artifact' || 'merged' }}",
        )
        for other in CORE["jobs"]["generate"]["steps"]:
            self.assertNotIn("MATERIALIZE_LAYOUT", other.get("env", {}))


if __name__ == "__main__":
    main()

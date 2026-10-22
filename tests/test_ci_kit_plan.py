from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from ci_kit.cli import main
from ci_kit.plan import build_plan, discover
from ci_kit.inputs import module_fields
from module_test_support import modules_repo, rendered_repo, shared_template_repo

SCHEMAS = Path(__file__).resolve().parents[1] / "tools" / "ci_kit" / "schemas"


def codes(plan) -> list[str]:
    return [finding.code for finding in plan.findings]


def repo_with_java_decl(text: str) -> Path:
    repo = rendered_repo("core", "java")
    (repo / ".ci/modules/java.yml").write_text(text)
    return repo


class PlanTests(unittest.TestCase):
    def test_schema_properties_match_workflow_inputs(self):
        for module in ("java", "python", "node", "docker"):
            schema = json.loads((SCHEMAS / f"{module}.v1.json").read_text())
            props = set(schema["properties"]["components"]["items"]["properties"])
            base = {"id", "name"} | (set() if module == "docker" else {"path"})
            self.assertEqual(props, base | set(module_fields(module)), module)

    def test_plan_fills_defaults_and_keeps_explicit_false_and_empty(self):
        repo = modules_repo({"python": [{"id": "app", "name": "App", "path": ".", "run_build": False, "build_command": ""}]})
        entry = build_plan(repo, drift=False).matrices["python"][0]
        self.assertIs(entry["run_build"], False)
        self.assertEqual(entry["build_command"], "")
        self.assertEqual(entry["python_version"], "3.14")
        self.assertEqual(set(entry), {"id", "name", "path", *module_fields("python")})

    def test_docker_entry_defaults_name_to_id_and_has_no_path(self):
        repo = modules_repo({"docker": [{"id": "img"}]})
        entry = build_plan(repo, drift=False).matrices["docker"][0]
        self.assertEqual(entry, {"id": "img", "name": "img", "context": ".", "dockerfile": "Dockerfile", "target": ""})

    def test_core_has_no_matrix(self):
        plan = build_plan(modules_repo({"node": [{"id": "web", "name": "Web", "path": "."}]}), drift=False)
        self.assertNotIn("core", plan.matrices)

    def test_duplicate_component_id_across_modules_is_core_005(self):
        repo = modules_repo({"python": [{"id": "app", "name": "A", "path": "."}], "node": [{"id": "app", "name": "B", "path": "."}]})
        found = [f for f in build_plan(repo, drift=False).findings if f.code == "CIK-CORE-005"]
        self.assertEqual([(f.module, f.path) for f in found], [("python", ".ci/modules/python.yml")])

    def test_duplicate_component_id_within_a_module_is_core_005(self):
        repo = modules_repo({"python": [{"id": "a", "name": "A", "path": "."}, {"id": "a", "name": "B", "path": "."}]})
        found = [f for f in build_plan(repo, drift=False).findings if f.code == "CIK-CORE-005"]
        self.assertEqual([(f.module, f.path) for f in found], [("python", ".ci/modules/python.yml")])
        self.assertIn("declared more than once", found[0].message)

    def test_duplicate_component_name_within_a_module_is_core_005(self):
        repo = modules_repo({"node": [{"id": "a", "name": "Same", "path": "."}, {"id": "b", "name": "Same", "path": "."}]})
        found = [f for f in build_plan(repo, drift=False).findings if f.code == "CIK-CORE-005"]
        self.assertEqual([(f.module, f.path) for f in found], [("node", ".ci/modules/node.yml")])
        self.assertIn("name Same is used by more than one component", found[0].message)

    def test_schema_rejects_unsafe_paths_and_empty_versions(self):
        cases = (
            ("python", {"path": "../x"}), ("python", {"path": "a/../b"}), ("python", {"path": "a/*"}), ("python", {"python_version": ""}),
            ("node", {"path": ".."}), ("node", {"node_version": ""}),
            ("java", {"path": "../x"}),
            ("docker", {"context": "../x"}), ("docker", {"context": "a?"}),
        )
        for module, bad in cases:
            with self.subTest(module=module, bad=bad):
                base = {"id": "a"} if module == "docker" else {"id": "a", "name": "A", "path": "."}
                if module == "java":
                    base["java_version"] = "21"
                repo = modules_repo({module: [{**base, **bad}]})
                self.assertEqual(codes(build_plan(repo, drift=False)), ["CIK-CORE-004"])

    def test_numeric_version_is_core_004(self):
        repo = modules_repo({"node": [{"id": "web", "name": "Web", "path": ".", "node_version": 24}]})
        self.assertEqual(codes(build_plan(repo, drift=False)), ["CIK-CORE-004"])

    def test_valid_core_java_repo_has_no_findings(self):
        plan = build_plan(rendered_repo("core", "java"), drift=False)
        self.assertEqual(plan.findings, [])
        self.assertEqual(plan.modules, ["core", "java"])
        self.assertEqual(plan.matrices["java"][0]["id"], "app-java")
        self.assertNotIn("core", plan.matrices)

    def test_discover_ignores_legacy_answers(self):
        repo = rendered_repo("core")
        (repo / ".copier-answers.yml").write_text("module: legacy\n")
        answers, declarations = discover(repo)
        self.assertEqual(list(answers), ["core"])
        self.assertEqual(list(declarations), ["core"])

    def test_missing_declaration_is_cik_core_002(self):
        repo = rendered_repo("core", "java")
        (repo / ".ci/modules/java.yml").unlink()
        self.assertEqual(codes(build_plan(repo, drift=False)), ["CIK-CORE-002"])

    def test_missing_answers_is_cik_core_002(self):
        repo = rendered_repo("core", "java")
        (repo / ".copier-answers.java.yml").unlink()
        self.assertEqual(codes(build_plan(repo, drift=False)), ["CIK-CORE-002"])

    def test_unparseable_or_non_mapping_declaration_is_cik_core_004(self):
        for text in ("a: [", "- 1\n- 2\n"):
            with self.subTest(text=text):
                self.assertEqual(codes(build_plan(repo_with_java_decl(text), drift=False)), ["CIK-CORE-004"])

    def test_directory_or_broken_symlink_declaration_is_cik_core_004(self):
        for kind in ("dir", "symlink"):
            with self.subTest(kind=kind):
                repo = rendered_repo("core", "java")
                target = repo / ".ci/modules/java.yml"
                target.unlink()
                if kind == "dir":
                    target.mkdir()
                else:
                    target.symlink_to(repo / "missing.yml")
                plan = build_plan(repo, drift=False)
                self.assertEqual(codes(plan), ["CIK-CORE-004"])
                self.assertIn("cannot read declaration", plan.findings[0].message)

    def test_schema_violation_is_cik_core_004(self):
        self.assertEqual(codes(build_plan(repo_with_java_decl("schema_version: 1\ncomponents: []\n"), drift=False)), ["CIK-CORE-004"])

    def test_unknown_schema_version_is_cik_core_001(self):
        self.assertEqual(codes(build_plan(repo_with_java_decl("schema_version: 99\ncomponents: []\n"), drift=False)), ["CIK-CORE-001"])

    def test_unknown_module_is_cik_core_001(self):
        repo = rendered_repo("core")
        (repo / ".copier-answers.foo.yml").write_text("module: foo\n")
        plan = build_plan(repo, drift=False)
        self.assertEqual(codes(plan), ["CIK-CORE-001"])
        self.assertIn("module foo is not supported by this ci-kit", plan.findings[0].message)

    def test_java_without_core_is_cik_core_003(self):
        self.assertEqual(codes(build_plan(rendered_repo("java"), drift=False)), ["CIK-CORE-003"])

    def test_broken_core_declaration_also_fails_java_requires(self):
        repo = rendered_repo("core", "java")
        (repo / ".ci/modules/core.yml").write_text("a: [")
        plan = build_plan(repo, drift=False)
        self.assertEqual(codes(plan), ["CIK-CORE-004", "CIK-CORE-003"])


class CliTests(unittest.TestCase):
    def run_cli(self, *argv: str, env: dict | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, env or {}), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_doctor_exit_code_and_annotation_format(self):
        repo = rendered_repo("core", "java")
        self.assertEqual(self.run_cli("doctor", "--repo", str(repo), "--template-src", str(shared_template_repo()))[0], 0)
        (repo / ".ci/modules/java.yml").unlink()
        code, out, _ = self.run_cli("doctor", "--repo", str(repo), "--template-src", str(shared_template_repo()), env={"GITHUB_ACTIONS": ""})
        self.assertEqual(code, 1)
        self.assertRegex(out, r"^CIK-CORE-002 java \.ci/modules/java\.yml: .+ \(docs/errors/CIK-CORE-002\.md\)$")
        code, out, _ = self.run_cli("doctor", "--repo", str(repo), "--template-src", str(shared_template_repo()), env={"GITHUB_ACTIONS": "true"})
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("::error title=CIK-CORE-002::java: "))

    def test_annotation_escapes_percent_and_newlines(self):
        from ci_kit.plan import Finding
        from ci_kit.cli import format_finding

        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            line = format_finding(Finding("CIK-CORE-004", "ja,va", "100%\nbad\r::error::x"))
        self.assertEqual(line, "::error title=CIK-CORE-004::ja,va: 100%25%0Abad%0D::error::x")
        self.assertNotIn("\n", line)

    def test_plan_annotations_go_to_stdout(self):
        repo = rendered_repo("java")
        _, out, err = self.run_cli("plan", "--repo", str(repo), env={"GITHUB_ACTIONS": "true"})
        self.assertIn("::error title=CIK-CORE-003::", out)
        self.assertEqual(err, "")

    def test_plan_writes_compact_github_output(self):
        repo = rendered_repo("core", "java")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out"
            code, _, _ = self.run_cli("plan", "--repo", str(repo), "--template-src", str(shared_template_repo()), "--github-output", str(target))
            lines = target.read_text().splitlines()
        self.assertEqual(code, 0)
        self.assertEqual(lines[0], 'modules=["core","java"]')
        entry = json.loads(lines[1].removeprefix("java="))[0]
        self.assertEqual({key: entry[key] for key in ("id", "name", "path", "java_version")}, {"id": "app-java", "name": "Application Java", "path": ".", "java_version": "21"})
        self.assertEqual(set(entry), {"id", "name", "path", *module_fields("java")})

    def test_plan_outputs_every_matrix(self):
        repo = rendered_repo("core", "python")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out"
            code, _, _ = self.run_cli("plan", "--repo", str(repo), "--template-src", str(shared_template_repo()), "--github-output", str(target))
            pairs = [line.split("=", 1) for line in target.read_text().splitlines()]
        self.assertEqual(code, 0)
        self.assertEqual([key for key, _ in pairs], ["modules", "java", "python", "node", "docker", "codes", "commits"])
        lines = dict(pairs)
        self.assertEqual([entry["id"] for entry in json.loads(lines["python"])], ["app-python"])
        self.assertEqual(json.loads(lines["node"]), [])
        self.assertEqual(json.loads(lines["docker"]), [])
        self.assertEqual(json.loads(lines["java"]), [])
        self.assertEqual(lines["codes"], "[]")

    def test_plan_writes_codes_and_commits_outputs(self):
        repo = rendered_repo("core", "java")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out"
            self.run_cli("plan", "--repo", str(repo), "--template-src", str(shared_template_repo()), "--github-output", str(target))
            lines = dict(line.split("=", 1) for line in target.read_text().splitlines())
        self.assertEqual(lines["codes"], "[]")
        commits = json.loads(lines["commits"])
        self.assertEqual(sorted(commits), ["core", "java"])
        self.assertTrue(all(isinstance(value, str) and value for value in commits.values()))
        self.assertEqual(lines["commits"], json.dumps(commits, separators=(",", ":")))

    def test_plan_codes_unique_in_first_seen_order_and_unreadable_answers_skipped(self):
        repo = rendered_repo("java")
        (repo / ".copier-answers.core.yml").write_text(":\n  - [")
        (repo / ".copier-answers.extra.yml").write_text("_commit: abc\n")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out"
            code, _, _ = self.run_cli("plan", "--repo", str(repo), "--github-output", str(target))
            lines = dict(line.split("=", 1) for line in target.read_text().splitlines())
        self.assertEqual(code, 1)
        found = json.loads(lines["codes"])
        self.assertEqual(found, list(dict.fromkeys(found)))
        self.assertIn("CIK-CORE-003", found)
        commits = json.loads(lines["commits"])
        self.assertNotIn("core", commits)
        self.assertEqual(commits.get("extra"), "abc")
        self.assertIn("java", commits)

    def test_plan_fails_on_findings(self):
        repo = rendered_repo("java")
        code, _, _ = self.run_cli("plan", "--repo", str(repo))
        self.assertEqual(code, 1)

    def test_cli_has_no_migrate(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as raised:
            main(["migrate", "--help"])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("invalid choice: 'migrate'", err.getvalue())


if __name__ == "__main__":
    unittest.main()

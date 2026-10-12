# Modular CI prototype (core + java) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `core` + `java` prototype of modular Copier CI templates with a runtime-assembled reusable `validate.yml`, drift detection, `CIK-*` diagnostics and a gate, then prove it on GitHub against the spec's go/no-go criteria.

**Architecture:** The root `copier.yml` gains a `module` question that selects `modules/<module>/template` (default `legacy` keeps today's `template/`). Modules render only declarations (`.ci/modules/<module>.yml`) and the thin caller. A Python package `ci_kit` (CLI `ci-kit`: `plan`, `doctor`, `gate`) validates declarations, detects drift by re-rendering each module at its `_commit`, and classifies failures. The reusable `.github/workflows/validate.yml` runs `plan → core-checks → java (matrix over ./.github/workflows/java-ci.yml) → gate`.

**Tech Stack:** Copier 9.18.2, Python ≥ 3.12 (`unittest`, PyYAML 6.0.3, jsonschema 4.26.0), GitHub Actions reusable workflows, actionlint 1.7.12.

**Spec:** `docs/superpowers/specs/2026-10-12-modular-ci-templates-design.md`

## Global Constraints

- Copier pin `copier==9.18.2`; `_min_copier_version` stays `"9.18.2"`.
- Python deps pinned exactly: `PyYAML==6.0.3`, `jsonschema==4.26.0`.
- Tests are `unittest` suites at `tests/test_*.py`, discovered by `scripts/run_test_suites.py`; temp paths are passed through `Path(...).resolve()` (macOS `/var` → `/private/var`).
- `python scripts/validate.py --static` must stay green after every task; if it enforces a contract on a new file (docs index, workflow inventory), satisfy the contract instead of weakening the check.
- Legacy consumers (single `.copier-answers.yml`, no `module` answer) must render byte-identically to `origin/main`.
- Error codes: format `CIK-<MODULE>-<NNN>`; every code has `docs/errors/<code>.md`; emitted as `::error title=<code>::<message>` when `GITHUB_ACTIONS=true`.
- Required check name: `ci / gate`. Reusable workflows call only fixed `uses:` paths; no `uses:` built from inputs or outputs.
- Toolkit refs: `quokkify/ci-kit/...@<ref>`; drift comparison normalizes `<ref>`.
- No comments in code unless needed for regex clarity.
- Branch: `feat/393-modular-ci-prototype` from `origin/main` in its own worktree; Conventional Commits.

### Deliberate deviations from the spec (update the spec in Task 9)

- `modules/<name>/module.yml` is replaced by `tools/ci_kit/modules.py` (`requires`), and module ownership is derived from what each module actually renders. A hand-maintained path list would duplicate the template.
- Schemas live in `tools/ci_kit/schemas/` so `uvx ... ci-kit doctor` ships them.
- New code `CIK-CORE-004` (declaration unparseable or schema-invalid); `CIK-CORE-001` stays "unsupported `schema_version`".
- `.ci/modules/core.yml` carries only `schema_version`; the toolkit ref lives solely in the caller's `uses:` line, which Renovate bumps.

## Review Focus

1. **Legacy consumer runs `copier update --defaults`** with no `module` answer → renders exactly what `origin/main` renders (Task 1 regression test).
2. **Renovate bumps the caller's `@ref`** → not drift; `plan` stays green (Task 4 test `test_bumped_caller_ref_is_not_drift`).
3. **Declaration deleted, unparseable YAML, or a list instead of a mapping** → `CIK-CORE-002` / `CIK-CORE-004` with the file path, never a traceback (Task 3 tests).
4. **Gate without `actions: read` or with an API error** (fork PR, restricted token) → pass/fail still computed from `needs`; category shows `unknown` instead of crashing (Task 5 test).
5. **Java component path with traversal or globs** (`../x`, `a/*`) or duplicate/reserved `id` → rejected at `copier copy` and by schema (Task 2 test).

---

### Task 1: Spike + `module` selector in root `copier.yml`

Proves spec question T1 before anything depends on it, then lands the selector without changing legacy output.

**Files:**
- Create: `tests/test_module_selector.py`
- Modify: `copier.yml` (top-level `_subdirectory`, new `module` question, `when:` on every existing question)
- Create: `modules/.gitkeep` (removed in Task 2)

**Interfaces:**
- Produces: answer `module` (str, choices `legacy|core|java`, default `legacy`); `_subdirectory: "{% if module == 'legacy' %}template{% else %}modules/{{ module }}/template{% endif %}"`. Every pre-existing question carries `when: "{{ module == 'legacy' }}"` (combined with its existing `when` via `and`), except `project_name` and `toolkit_version`, which use `when: "{{ module in ['legacy', 'core'] }}"`.
- Produces test helper in `tests/test_module_selector.py`, reused by Tasks 2 and 4 via import: `make_template_repo(tmp: Path) -> Path`, which copies the working tree (`shutil.copytree`, excluding `.git`) into `tmp/ci-kit`, runs `git init` + one commit, and tags it `v99.0.0`, so tests see uncommitted changes.

- [ ] **Step 1: Spike (throwaway, scratchpad only).** In a scratch git repo, create a root `copier.yml` with `module` and templated `_subdirectory`, two modules each shipping `{{ _copier_conf.answers_file }}.jinja` and one file. Tag `v1`, change module `b`, tag `v2`. Run `copier copy --trust --vcs-ref v1 -a .copier-answers.a.yml -d module=a`, the same for `b`, then `copier update --trust --defaults -a .copier-answers.b.yml --vcs-ref v2`.
  Expected: only module `b`'s file changes; `.copier-answers.b.yml` has `_commit: v2`; `.copier-answers.a.yml` still `_commit: v1`. If not, stop and report: D1 needs revisiting (spec §11 row 1).

- [ ] **Step 2: Write the failing legacy-compat test**

```python
class LegacyCompatTests(TestCase):
    def test_legacy_scenarios_render_identically_to_main(self) -> None:
        for scenario in sorted((ROOT / "tests/scenarios").glob("*.yml")):
            with self.subTest(scenario=scenario.name):
                self.assertEqual(render_tree(BASELINE_REPO, scenario), render_tree(make_template_repo(...), scenario))

    def test_module_answer_defaults_to_legacy(self) -> None:
        answers = yaml.safe_load((render(..., data={}) / ".copier-answers.yml").read_text())
        self.assertEqual(answers.get("module", "legacy"), "legacy")
```

`BASELINE_REPO` is a `git worktree add --detach <tmp> origin/main` created in `setUpClass`; `render_tree` runs `copier copy --trust --defaults --data-file <scenario> --vcs-ref HEAD` and returns `{relative_path: bytes}`.

- [ ] **Step 3: Run it and confirm it fails**

Run: `python tests/test_module_selector.py -v`
Expected: FAIL in `test_module_answer_defaults_to_legacy` (`module` unknown); the compat test passes or errors on the missing helper.

- [ ] **Step 4: Edit `copier.yml`** as the Interfaces block says. `modules/.gitkeep` keeps the directory valid.

- [ ] **Step 5: Run tests and the repo gate**

Run: `python tests/test_module_selector.py -v && python scripts/validate.py --static`
Expected: PASS; validate prints no `ERROR`.

- [ ] **Step 6: Commit**

```bash
git add copier.yml modules/.gitkeep tests/test_module_selector.py
git commit -m "feat(modules): add module selector with legacy default"
```

### Task 2: `core` and `java` module templates + schemas

**Files:**
- Create: `modules/core/template/{{ _copier_conf.answers_file }}.jinja`, `modules/core/template/.ci/modules/core.yml.jinja`, `modules/core/template/.github/workflows/validate.yml.jinja`
- Create: `modules/java/template/{{ _copier_conf.answers_file }}.jinja`, `modules/java/template/.ci/modules/java.yml.jinja`
- Create: `tools/ci_kit/__init__.py`, `tools/ci_kit/schemas/core.v1.json`, `tools/ci_kit/schemas/java.v1.json`
- Modify: `copier.yml` (question `java_components`), delete `modules/.gitkeep`
- Test: `tests/test_modules_render.py`

**Interfaces:**
- Consumes: `module` answer, `make_template_repo` (Task 1).
- Produces question `java_components` (yaml list, `when: "{{ module == 'java' }}"`, default `[{id: app-java, name: Application Java, path: ., java_version: "21"}]`). Validator mirrors the legacy `components` rules: `id` matches `^[A-Za-z_][A-Za-z0-9_-]*$`, unique, not in `['plan', 'core-checks', 'java', 'gate']`; `path` non-empty, no `..` segment, no `*?[`; `java_version` in `['17', '21', '25']`.
- Produces rendered files:
  - `.ci/modules/core.yml` = `schema_version: 1`
  - `.ci/modules/java.yml` = `schema_version: 1` + `components` (keys `id, name, path, java_version`)
  - `.github/workflows/validate.yml` = the caller from spec §7, with `uses: quokkify/ci-kit/.github/workflows/validate.yml@{{ toolkit_version }}` and `permissions: {contents: read, actions: read}`
- Produces JSON Schemas (draft 2020-12, `additionalProperties: false`) matching exactly those shapes; `schema_version` is `const: 1`.

- [ ] **Step 1: Write the failing tests**

```python
def test_core_renders_exact_file_set(self):
    self.assertEqual(rendered_files(module="core"), {".copier-answers.core.yml", ".ci/modules/core.yml", ".github/workflows/validate.yml"})

def test_java_renders_exact_file_set(self):
    self.assertEqual(rendered_files(module="java"), {".copier-answers.java.yml", ".ci/modules/java.yml"})

def test_declarations_match_schemas(self):
    for module in ("core", "java"):
        jsonschema.validate(yaml.safe_load(decl(module)), load_schema(module, 1))

def test_caller_pins_toolkit_version(self):
    self.assertIn("uses: quokkify/ci-kit/.github/workflows/validate.yml@v99.0.0", caller(toolkit_version="v99.0.0"))

def test_java_component_rejects_bad_input(self):
    for bad in ({"path": "../x"}, {"path": "a/*"}, {"id": "gate"}, {"java_version": "8"}):
        with self.subTest(bad=bad):
            self.assertNotEqual(copy_java(component_override=bad).returncode, 0)
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `python tests/test_modules_render.py -v`
Expected: FAIL (`modules/core/template` missing).

- [ ] **Step 3: Create the templates, schemas and the `java_components` question.**

- [ ] **Step 4: Run tests, actionlint on the rendered caller, and the repo gate**

Run: `python tests/test_modules_render.py -v && python tests/test_module_selector.py -v && python scripts/validate.py --static`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add modules tools/ci_kit copier.yml tests/test_modules_render.py
git commit -m "feat(modules): add core and java module templates with schemas"
```

### Task 3: `ci_kit` package — discovery, schema, compatibility, `requires`, codes, runbooks, CLI

**Files:**
- Create: `pyproject.toml`
- Create: `tools/ci_kit/codes.py`, `tools/ci_kit/modules.py`, `tools/ci_kit/plan.py`, `tools/ci_kit/cli.py`
- Create: `docs/errors/CIK-CORE-001.md`, `-002.md`, `-003.md`, `-004.md`, `-010.md`, `-011.md`, `-020.md`
- Test: `tests/test_ci_kit_plan.py`, `tests/test_ci_kit_codes.py`

**Interfaces:**
- `pyproject.toml`: `name = "ci-kit"`, `requires-python = ">=3.12"`, deps `PyYAML==6.0.3`, `jsonschema==4.26.0`, `copier==9.18.2`; `[project.scripts] ci-kit = "ci_kit.cli:main"`; package `ci_kit` from `tools/` with `schemas/*.json` as package data.
- `codes.py`: `CODES: dict[str, str]` (code → one-line title) for the seven codes above.
- `modules.py`: `MODULES: dict[str, ModuleInfo]`; `ModuleInfo(requires: tuple[str, ...], job: str)`; `core → ((), "core-checks")`, `java → (("core",), "java")`.
- `plan.py`:
  - `@dataclass(frozen=True) Finding(code: str, module: str, message: str, path: str | None = None)`
  - `@dataclass Plan(modules: list[str], matrices: dict[str, list[dict]], findings: list[Finding])`
  - `discover(repo: Path) -> tuple[dict[str, Path], dict[str, Path]]` returns answers files and declarations keyed by module, from `.copier-answers.<module>.yml` and `.ci/modules/<module>.yml`; ignores legacy `.copier-answers.yml`.
  - `build_plan(repo: Path, *, drift: bool = True) -> Plan`. Checks run in order: pairing (002), parse + schema (004), `schema_version` supported (001: a schema file `<module>.v<N>.json` exists), `requires` (003), then drift and ownership (Task 4, behind `drift`). `matrices["java"]` = the declaration's `components`; modules without components get `[]`.
- `cli.py`: `main(argv: list[str] | None = None) -> int` with subcommands:
  - `plan --repo PATH [--github-output PATH]`: writes `modules=<json>` and `java=<json>`; exit 1 if any finding.
  - `doctor --repo PATH`: human report, one line per finding: `<code> <module> <path>: <message> (docs/errors/<code>.md)`; exit 1 if any finding.
  - `gate` is added in Task 5.
  - Findings print as `::error title=<code>::<module>: <message>` when `GITHUB_ACTIONS == "true"`.
- Each runbook has headings `## Cause`, `## Fix` (exact command), `## Example`.

- [ ] **Step 1: Write the failing tests**

```python
def test_valid_core_java_repo_has_no_findings(self):
    plan = build_plan(rendered_repo("core", "java"), drift=False)
    self.assertEqual(plan.findings, [])
    self.assertEqual(plan.modules, ["core", "java"])
    self.assertEqual(plan.matrices["java"][0]["id"], "app-java")

def test_missing_declaration_is_cik_core_002(self):
    repo = rendered_repo("core", "java"); (repo / ".ci/modules/java.yml").unlink()
    self.assertEqual(codes(build_plan(repo, drift=False)), ["CIK-CORE-002"])

def test_unparseable_or_non_mapping_declaration_is_cik_core_004(self):
    for text in ("a: [", "- 1\n- 2\n"):
        with self.subTest(text=text):
            self.assertEqual(codes(build_plan(repo_with_java_decl(text), drift=False)), ["CIK-CORE-004"])

def test_unknown_schema_version_is_cik_core_001(self):
    self.assertEqual(codes(build_plan(repo_with_java_decl("schema_version: 99\ncomponents: []\n"), drift=False)), ["CIK-CORE-001"])

def test_java_without_core_is_cik_core_003(self):
    self.assertEqual(codes(build_plan(rendered_repo("java"), drift=False)), ["CIK-CORE-003"])

def test_doctor_exit_code_and_annotation_format(self): ...
```

`test_ci_kit_codes.py`:

```python
def test_every_code_has_runbook_and_every_runbook_has_code(self):
    docs = {p.stem for p in (ROOT / "docs/errors").glob("CIK-*.md")}
    self.assertEqual(set(CODES), docs)

def test_every_code_used_in_sources_is_registered(self):
    used = set(re.findall(r"CIK-[A-Z]+-\d{3}", source_text_of(ROOT / "tools/ci_kit")))
    self.assertLessEqual(used, set(CODES))
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `python tests/test_ci_kit_plan.py -v; python tests/test_ci_kit_codes.py -v`
Expected: FAIL (`ci_kit` not importable).

- [ ] **Step 3: Implement the files listed above.** Tests import via `sys.path.insert(0, str(ROOT / "tools"))`, as existing suites load `scripts/`.

- [ ] **Step 4: Run tests, the installed CLI, and the repo gate**

Run: `python tests/test_ci_kit_plan.py -v && python tests/test_ci_kit_codes.py -v && uv run --with . ci-kit doctor --repo <rendered core+java tmp repo> && python scripts/validate.py --static`
Expected: PASS; `doctor` exits 0 with no findings.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tools/ci_kit docs/errors tests/test_ci_kit_plan.py tests/test_ci_kit_codes.py
git commit -m "feat(ci-kit): add plan and doctor with CIK error codes"
```

### Task 4: Drift and ownership + template lifecycle tests

**Files:**
- Create: `tools/ci_kit/drift.py`
- Modify: `tools/ci_kit/plan.py` (call drift when `drift=True`)
- Test: `tests/test_ci_kit_drift.py`, `tests/test_modules_lifecycle.py`

**Interfaces:**
- Consumes: `Finding`, `build_plan`, `discover` (Task 3); `make_template_repo` (Task 1).
- `drift.py`:
  - `REF_RE = re.compile(r"(quokkify/ci-kit/[^@\s]+)@\S+")`, which normalizes the text after `@` in toolkit `uses:` references.
  - `render_module(answers_path: Path, dest: Path, src_override: str | None = None) -> dict[str, bytes]` runs `copier copy --trust --defaults --quiet --vcs-ref <_commit> --data-file <non-underscore answers> <src> <dest>` with `-a <answers file name>`. `src` is `src_override` or the answers' `_src_path`. It returns rendered files except the answers file.
  - `drift_findings(repo: Path, answers: dict[str, Path], src_override: str | None = None) -> list[Finding]`:
    - `CIK-CORE-010` per rendered file that is missing or differs after `REF_RE` normalization; the message holds a unified diff of up to 40 lines.
    - `CIK-CORE-011` per path rendered by two modules.
- `build_plan(..., src_override: str | None = None)` and the CLI flag `--template-src PATH` reach `src_override` (tests and fixtures point at a local repo).

- [ ] **Step 1: Write the failing tests**

`test_ci_kit_drift.py`:

```python
def test_clean_repo_has_no_drift(self): self.assertEqual(drift_findings(repo, answers, src), [])
def test_edited_declaration_is_cik_core_010_with_diff(self):
    f = drift_findings(edited(repo, ".ci/modules/java.yml"), answers, src)
    self.assertEqual([x.code for x in f], ["CIK-CORE-010"]); self.assertIn("+++", f[0].message)
def test_bumped_caller_ref_is_not_drift(self):
    bump_ref(repo, "v99.0.0", "v99.0.7"); self.assertEqual(drift_findings(repo, answers, src), [])
def test_deleted_owned_file_is_cik_core_010(self): ...
def test_two_modules_rendering_same_path_is_cik_core_011(self): ...
```

`test_modules_lifecycle.py` (template repo tagged `v99.0.0`, then a java template change committed and tagged `v99.1.0`):

```python
def test_update_java_only_changes_java_paths(self):
    self.assertEqual(changed_paths(update("java", "v99.1.0")), {".copier-answers.java.yml", ".ci/modules/java.yml"})
def test_joint_update_changes_both_answers(self): ...
def test_second_update_is_empty_diff(self): ...
def test_manual_edit_then_update_reports_drift_not_conflict(self):
    # plan reports CIK-CORE-010 before update; no '<<<<<<<' or '.rej' after update with the edit reverted
def test_rollback_java_commit_restores_previous_render(self): ...
def test_removing_java_module_leaves_clean_plan(self):
    # delete .copier-answers.java.yml and .ci/modules/java.yml → build_plan(...).findings == []
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `python tests/test_ci_kit_drift.py -v; python tests/test_modules_lifecycle.py -v`
Expected: FAIL (`ci_kit.drift` missing).

- [ ] **Step 3: Implement `drift.py`; wire it into `build_plan`.**

- [ ] **Step 4: Run tests, measure drift time, and run the repo gate**

Run: `python tests/test_ci_kit_drift.py -v && python tests/test_modules_lifecycle.py -v && time uv run --with . ci-kit doctor --repo <tmp> --template-src <tmp template repo> && python scripts/validate.py --static`
Expected: PASS. Record the `doctor` wall time for spec §11 row 5 (target ≲ 15 s).

- [ ] **Step 5: Commit**

```bash
git add tools/ci_kit tests/test_ci_kit_drift.py tests/test_modules_lifecycle.py
git commit -m "feat(ci-kit): detect template drift and ownership conflicts"
```

### Task 5: Gate evaluation and summary

**Files:**
- Create: `tools/ci_kit/gate.py`
- Modify: `tools/ci_kit/cli.py` (subcommand `gate`), `tools/ci_kit/codes.py` already holds `CIK-CORE-020`
- Test: `tests/test_ci_kit_gate.py`

**Interfaces:**
- Consumes: `MODULES[*].job` (Task 3).
- `gate.py`:
  - `TOOLKIT_STEPS = frozenset({"Check out repository", "Check out toolkit actions", "Resolve build tool for dependency cache", "Set up Java", "Resolve build tool and commands", "Upload test artifacts"})`; any other failing step is `project`.
  - `@dataclass Row(module: str, ref: str, result: str, category: str, failing_step: str, code: str)`
  - `evaluate(enabled: list[str], needs: dict, jobs: list[dict] | None, ref: str) -> tuple[bool, list[Row]]`. `needs` is `toJSON(needs)`; `jobs` comes from the run-jobs API, or `None` when unavailable.
    - Gate failure → `plan` result ≠ `success`, any `failure`/`cancelled`, or an enabled module whose job is `skipped` (row code `CIK-CORE-020`).
    - Category: `plan` failure → `template`. A module job failure → `toolkit` or `project` by the first failed step whose job name starts with the module job id; `unknown` when `jobs is None`.
  - `render_summary(rows: list[Row]) -> str` produces the spec §9.3 Markdown table; a code cell links `docs/errors/<code>.md` on `https://github.com/quokkify/ci-kit/blob/<ref>/`.
- CLI: `gate --enabled JSON --needs JSON [--jobs-file PATH] --ref REF [--summary PATH]`. Exit 0 or 1; a missing or malformed jobs file means `jobs=None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_all_success_passes(self): self.assertTrue(evaluate(["core","java"], needs(all="success"), jobs=[], ref="x")[0])
def test_cancelled_fails(self): ...
def test_disabled_module_skipped_is_fine(self): self.assertTrue(evaluate(["core"], needs(java="skipped"), [], "x")[0])
def test_enabled_module_skipped_is_cik_core_020(self):
    ok, rows = evaluate(["core","java"], needs(java="skipped"), [], "x")
    self.assertFalse(ok); self.assertEqual(row(rows, "java").code, "CIK-CORE-020")
def test_failed_setup_java_is_toolkit(self): self.assertEqual(row(eval_with_failed_step("Set up Java"), "java").category, "toolkit")
def test_failed_gradle_test_is_project(self): self.assertEqual(row(eval_with_failed_step("Gradle test with bounded recovery"), "java").category, "project")
def test_plan_failure_is_template(self): ...
def test_no_jobs_api_still_decides_and_marks_unknown(self):
    ok, rows = evaluate(["core","java"], needs(java="failure"), None, "x")
    self.assertFalse(ok); self.assertEqual(row(rows, "java").category, "unknown")
def test_summary_table_header_and_runbook_link(self): ...
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `python tests/test_ci_kit_gate.py -v`
Expected: FAIL (`ci_kit.gate` missing).

- [ ] **Step 3: Implement `gate.py` and the `gate` subcommand.**

- [ ] **Step 4: Run tests and the repo gate**

Run: `python tests/test_ci_kit_gate.py -v && python scripts/validate.py --static`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/ci_kit tests/test_ci_kit_gate.py
git commit -m "feat(ci-kit): add gate evaluation with failure categories"
```

### Task 6: Reusable `validate.yml` in ci-kit

**Files:**
- Create: `.github/workflows/validate.yml`
- Test: `tests/test_modular_validate_workflow.py`
- Modify: whatever `scripts/validate.py --static` requires for a new reusable workflow (README/docs inventory). Do not loosen checks.

**Interfaces:**
- Consumes: CLI `ci-kit plan` / `ci-kit gate` (Tasks 3–5); `java-ci.yml` inputs `working-directory`, `java-version`.
- Produces `on: workflow_call` (no inputs), top-level `permissions: {contents: read, actions: read}`, jobs:
  - `plan`: checks out the caller repo (`fetch-depth: 0`, `persist-credentials: false`), then repository `quokkify/ci-kit` into `.toolkit` at `ref: ${{ job.workflow_sha }}` (same pattern as `java-ci.yml:89-94`). Set up Python `3.14`, `pip install ./.toolkit`, run `ci-kit plan --repo . --github-output "$GITHUB_OUTPUT"`. Outputs `modules`, `java`.
  - `core-checks`: `needs: plan`, `if: contains(fromJSON(needs.plan.outputs.modules), 'core')`. Runs actionlint 1.7.12 with checksum and the diff whitespace check, both moved verbatim from `template/.github/workflows/validate.yml.jinja` (the `Install actionlint`, `Lint workflows`, `Check committed diff whitespace` steps).
  - `java`: `needs: plan`, `if: needs.plan.outputs.java != '[]'`, `strategy: {fail-fast: false, matrix: {component: ${{ fromJSON(needs.plan.outputs.java) }}}}`, `name: java (${{ matrix.component.name }})`, `uses: ./.github/workflows/java-ci.yml`, `with: {working-directory: ${{ matrix.component.path }}, java-version: ${{ matrix.component.java_version }}}`.
  - `gate`: `needs: [plan, core-checks, java]`, `if: always()`. Fetches `gh api repos/$GITHUB_REPOSITORY/actions/runs/$GITHUB_RUN_ID/attempts/$GITHUB_RUN_ATTEMPT/jobs --paginate` into a file, tolerating failure. Runs `ci-kit gate --enabled '${{ needs.plan.outputs.modules }}' --needs '${{ toJSON(needs) }}' --jobs-file jobs.json --ref ${{ job.workflow_sha }} --summary "$GITHUB_STEP_SUMMARY"`. Expressions go through `env:`, not inline in `run:`.

- [ ] **Step 1: Write the failing structural test**

```python
def test_jobs_and_gate_contract(self):
    wf = load(".github/workflows/validate.yml")
    self.assertEqual(set(wf["jobs"]), {"plan", "core-checks", "java", "gate"})
    self.assertEqual(wf["jobs"]["gate"]["if"], "always()")
    self.assertEqual(set(wf["jobs"]["gate"]["needs"]), {"plan", "core-checks", "java"})
def test_uses_are_static(self):
    for job in wf["jobs"].values():
        self.assertNotIn("${{", job.get("uses", ""))
def test_no_expression_interpolated_in_run(self): ...
def test_toolkit_checkout_uses_job_workflow_sha(self): ...
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `python tests/test_modular_validate_workflow.py -v`
Expected: FAIL (file missing).

- [ ] **Step 3: Write the workflow.**

- [ ] **Step 4: Run test, actionlint and the repo gate**

Run: `python tests/test_modular_validate_workflow.py -v && actionlint .github/workflows/validate.yml && python scripts/validate.py --static`
Expected: PASS, actionlint silent.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/validate.yml tests/test_modular_validate_workflow.py <inventory files>
git commit -m "feat(ci): add runtime-assembled reusable validate workflow"
```

### Task 7: Ring 0 — modular fixture in `validate-toolkit.yml`

**Files:**
- Create: `tests/scenarios/modules-core-java.yml` (answers for `core` and `java`), `scripts/validate_modules_fixture.py`
- Modify: `.github/workflows/validate-toolkit.yml` (new job `modules`, added to `validation-complete.needs`)

**Interfaces:**
- Consumes: `make_template_repo`-equivalent logic (local template source), CLI `ci-kit doctor`.
- `scripts/validate_modules_fixture.py` exits 0 only if all of these hold:
  1. Rendering `core` + `java` from the checkout into a temp repo gives `doctor` exit 0.
  2. Three broken variants each exit 1 with the expected code: edited `.ci/modules/java.yml` → `CIK-CORE-010`; deleted `.ci/modules/java.yml` → `CIK-CORE-002`; `java` without `core` → `CIK-CORE-003`.
  3. The rendered caller passes actionlint.
- New job `modules` mirrors the existing `java` job's runner, `needs` and pip install, and runs `python scripts/validate_modules_fixture.py`.

- [ ] **Step 1: Write the script with the three negative cases first; run it before the scenario file exists**

Run: `python scripts/validate_modules_fixture.py`
Expected: FAIL (scenario missing).

- [ ] **Step 2: Add the scenario and the workflow job.**

- [ ] **Step 3: Run the script, actionlint and the repo gate**

Run: `python scripts/validate_modules_fixture.py && actionlint .github/workflows/validate-toolkit.yml && python scripts/validate.py --static`
Expected: PASS.

- [ ] **Step 4: Commit and push; open a draft PR referencing #393**

```bash
git add tests/scenarios/modules-core-java.yml scripts/validate_modules_fixture.py .github/workflows/validate-toolkit.yml
git commit -m "ci: validate modular core+java fixture in toolkit CI"
git push -u origin feat/393-modular-ci-prototype
gh pr create --draft --base main --title "feat: modular CI prototype (core + java)" --body "Refs #393 ..."
```

Expected: `Validate toolkit` green on the draft PR, including `modules`.

### Task 8: Runtime trial on GitHub (requires explicit user approval before each outward step)

Spec decision 8 means a reusable workflow on a PR branch cannot be proven locally; this task proves it by calling the pushed branch ref.

**Files:** none in ci-kit unless a fix is needed. Results are recorded in the spec (Task 9).

- [ ] **Step 1: Ask the user to approve creating a sandbox repo** `quokkify/ci-kit-sandbox-java` (private is fine if Actions access policy allows calling the public ci-kit). Contents: minimal Gradle Java project (copy `tests/fixtures/java`), then `copier copy` of `core` and `java` from `gh:quokkify/ci-kit --vcs-ref feat/393-modular-ci-prototype`. Change the caller ref to the branch's head SHA.

- [ ] **Step 2: Open a PR in the sandbox and record answers to spec §11 rows 2–5:**
  - does nested `./.github/workflows/java-ci.yml` resolve in ci-kit at the same SHA;
  - the actual check names; `ci / gate` must exist;
  - no nesting-limit error;
  - `plan` job duration.

  Expected: `ci / gate` green.

- [ ] **Step 3: Go/no-go 4 — three induced failures, one sandbox commit each:**
  - edit `.ci/modules/java.yml` → gate row `template`, `CIK-CORE-010`;
  - set the `java` declaration's `java_version` to `"17"` while the build requires 21 → `project`;
  - point the caller at a ci-kit commit where `java-ci.yml`'s `Set up Java` uses a nonexistent `distribution` → `toolkit`.

  Expected: each cause is readable from the job summary alone.

- [ ] **Step 4: Go/no-go 3 — fix without Copier.** Push a visible change to `java-ci.yml` on the ci-kit branch. Bump only the caller SHA in the sandbox. Confirm the change is live, `copier` was not run, and `plan` shows no drift.

- [ ] **Step 5: Go/no-go 1–2 on the sandbox:** `copier update -a .copier-answers.java.yml` touches only java paths; a manual edit yields `CIK-CORE-010`, not a Copier conflict.

### Task 9: Record results, update spec and docs, mark PR ready

**Files:**
- Modify: `docs/superpowers/specs/2026-10-12-modular-ci-templates-design.md` (§6 structure per "Deliberate deviations", §9.2 add `CIK-CORE-004`, §11 table gains a "Result" column with Task 4/8 evidence, §12 verdict go or no-go)
- Create: `docs/modules.md`. It covers the user procedure for adding, updating, removing and rolling back a module; that drift and ownership mean template-owned files must not be edited; and running `ci-kit doctor`. Link it from `README.md`.

- [ ] **Step 1: Write the updates.**
- [ ] **Step 2: Run** `python scripts/run_test_suites.py && python scripts/validate.py --static`. Expected: PASS; local macOS-only failures must be listed in the PR body with their cause.
- [ ] **Step 3: Commit, push, request review** (`code-reviewer` agent on the whole branch), then `gh pr ready`.

```bash
git add docs README.md
git commit -m "docs: record modular CI prototype results and module guide"
git push
```

- [ ] **Step 4: Canary (only on go, separate PR, user approval):** migrate `java-test-automation-template` to `core` + `java` modules per `docs/modules.md`. This is the first real input to spec #2.

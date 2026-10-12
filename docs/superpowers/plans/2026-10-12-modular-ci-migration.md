# Modular CI migration (spec #2, wave 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the hybrid handoff, `ci-kit update` / `ci-kit migrate`, module-aware fleet with a migrate mode, state-aware pilot gate and `migration_checks.py`. Then migrate the 8 component-less consumers from legacy `validate.yml` to the `core` module.

**Architecture:** A legacy question `handed_off` excludes area files that modules now own. `tools/ci_kit/handoff.py` is the area registry. `ci-kit plan` absorbs the legacy `template-contract` checks. `ci-kit update` orchestrates every answers file. `ci-kit migrate` performs one atomic handoff. The fleet keeps its legacy pipeline and delegates the module parts to the CLI. Rulesets change only through `scripts/migration_checks.py`, which runs on top of `reconcile_ruleset.py`.

**Tech Stack:** Copier 9.18.2, Python ≥ 3.12 (`unittest`, PyYAML 6.0.3, jsonschema 4.26.0), `gh` CLI, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-12-modular-ci-migration-design.md` (builds on `docs/superpowers/specs/2026-10-12-modular-ci-templates-design.md`).

## Global Constraints

- Base: branch `feat/393-migration` from `feat/393-modular-ci-prototype` (PR #407), in its own worktree. Conventional Commits; trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Pins: `copier==9.18.2`, `PyYAML==6.0.3`, `jsonschema==4.26.0`. Run Python via `uv run --no-project`; never commit `uv.lock`.
- Tests are `unittest` suites at `tests/test_*.py`. Shared helpers live in `tests/module_test_support.py`. Temp paths go through `Path(...).resolve()`.
- `python scripts/validate.py --static` stays green. Extend its contracts; never loosen them.
- Legacy render with `handed_off: []` (and with no `handed_off` answer) is byte-identical to the legacy render before this branch.
- Codes `CIK-HANDOFF-001..003`, `CIK-LEGACY-001..005`, `CIK-MIGRATE-001..004`, `CIK-UPDATE-001`: each is registered in `tools/ci_kit/codes.py`, has `docs/errors/<code>.md` with `## Cause` / `## Fix` / `## Example`, and is emitted with the existing escaping.
- Area registry: `AREAS = {"validate": Area(module="core", legacy_paths=(".github/workflows/validate.yml",))}`.
- `checks_after` for `core` is `["ci / gate"]`. The `core` caller keeps `name: Validate`.
- Update order: legacy → `core` → language modules (by name) → other modules (by name). Legacy runs with `--trust`; modules run without it.
- Migrate-JSON in PR bodies: the first fenced ```json block directly after the line `<!-- ci-kit-migrate -->`.
- Fleet never merges PRs and never changes rulesets.
- `_src_path` of every answers file must `fullmatch` the existing `TRUSTED_SRC_RE`.
- No code comments unless needed for regex clarity.

## Review Focus

1. **Hand-edited legacy `validate.yml`.** `ci-kit migrate` aborts with `CIK-MIGRATE-003` and a diff, and the fleet opens no PR (Task 4, Task 6).
2. **Pilot already in `hybrid(validate)` during a normal `update` rollout.** The gate waits for `ci / gate`, not `Shared template contract`, so it does not time out (Task 7).
3. **`migration_checks.py` re-run or partially applied ruleset** (`checks_before` already absent, `checks_after` already present). The call is idempotent, keeps non-validate checks and never yields an empty set (Task 8).
4. **Private repo (`knowledge`) excluded by `--public-only`.** Migrating it with an explicit `--repo` works (Task 6).
5. **`.copier-answers.<m>.yml` that is a symlink, has a foreign `_src_path`, or names an unknown module.** The fleet reports it and does not process it (Task 5).

---

### Task 1: Legacy `handed_off` + area registry

**Files:**
- Modify: `copier.yml` (question `handed_off`, `_exclude` entry)
- Create: `tools/ci_kit/handoff.py`
- Test: `tests/test_handoff_registry.py`; extend `tests/test_module_selector.py`

**Interfaces:**
- Produces `handoff.py`:
  - `@dataclass(frozen=True) Area(module: str, legacy_paths: tuple[str, ...])`
  - `AREAS: dict[str, Area]`
  - `area_for_module(module: str) -> str | None`
- Question exactly as in spec §5.1, with validator: a YAML list of strings, each in `['validate']`, no duplicates. `_exclude` entry exactly as in spec §5.1.

- [ ] **Step 1: Write the failing tests**

```python
def test_registry_matches_copier_yml(self):
    cfg = yaml.safe_load((ROOT / "copier.yml").read_text())
    self.assertEqual(set(AREAS), set(areas_in_validator(cfg["handed_off"]["validator"])))
    for area, info in AREAS.items():
        for path in info.legacy_paths:
            self.assertTrue(any(f"'{area}' in" in e and path in e for e in cfg["_exclude"]))

def test_handed_off_validate_excludes_validate_yml(self):
    self.assertNotIn(".github/workflows/validate.yml", render_tree(repo, data={"handed_off": ["validate"], "toolkit_version": PIN}))

def test_unknown_area_rejected(self): ...  # copier copy exits non-zero for handed_off=[foo]

def test_empty_handed_off_is_byte_identical(self):  # added in test_module_selector.py
    self.assertEqual(render_tree(repo, s, data={"handed_off": []}), render_tree(repo, s))
```

- [ ] **Step 2: Run them and confirm they fail** — `python tests/test_handoff_registry.py -v`. Expected: FAIL (`ci_kit.handoff` missing).
- [ ] **Step 3: Implement the question, the `_exclude` entry and `handoff.py`.**
- [ ] **Step 4: Run** `python tests/test_handoff_registry.py -v && python tests/test_module_selector.py -v && python scripts/validate.py --static`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(modules): add legacy handed_off and area registry`.

### Task 2: `ci-kit plan` — handoff and legacy contract checks

**Files:**
- Create: `tools/ci_kit/legacy.py`
- Modify: `tools/ci_kit/plan.py`, `tools/ci_kit/codes.py`
- Create: `docs/errors/CIK-HANDOFF-00{1,2,3}.md`, `docs/errors/CIK-LEGACY-00{1..5}.md`
- Test: `tests/test_ci_kit_legacy.py`

**Interfaces:**
- Consumes: `AREAS` (Task 1); `Finding`, `build_plan` (prototype).
- Produces `legacy.py`:
  - `legacy_findings(repo: Path, module_owned: set[str]) -> list[Finding]` implements `CIK-LEGACY-001..005` per spec §5.3. It ports the Python in `template/.github/workflows/validate.yml.jinja` (job `template-contract`). Paths in `module_owned` are skipped for the ref check.
  - `handoff_findings(repo: Path, legacy_answers: dict | None, modules: set[str]) -> list[Finding]` implements `CIK-HANDOFF-001..003`.
- `build_plan` calls both functions when `.copier-answers.yml` exists. `module_owned` is the union of files rendered by modules (drift already computes it; expose it as `Plan.module_owned: set[str]`).

- [ ] **Step 1: Write the failing tests.** Write one negative test per code with the exact code assertion. Add one positive test: a legacy scenario render plus `core` with `handed_off: [validate]` gives no findings. Add one more: a caller `@ref` that differs from legacy `toolkit_version` is not `CIK-LEGACY-003`.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement; add codes and runbooks.**
- [ ] **Step 4: Run** `python tests/test_ci_kit_legacy.py -v && python tests/test_ci_kit_codes.py -v && python tests/test_ci_kit_plan.py -v && python scripts/validate.py --static`.
- [ ] **Step 5: Commit** `feat(ci-kit): check handoff and legacy template contract in plan`.

### Task 3: `ci-kit update`

**Files:**
- Create: `tools/ci_kit/update.py`
- Modify: `tools/ci_kit/cli.py` (subcommand `update`), `tools/ci_kit/codes.py`, `docs/errors/CIK-UPDATE-001.md`
- Test: `tests/test_ci_kit_update.py`

**Interfaces:**
- `@dataclass ModuleUpdate(module: str, from_ref: str, to_ref: str, commit: str | None)`
- `update_order(answers: dict[str, Path]) -> list[str]` returns the order from the Global Constraints. The legacy key is `"legacy"`.
- `run_update(repo: Path, to: str, *, modules: list[str] | None = None, skip_legacy: bool = False, src_override: str | None = None) -> tuple[list[ModuleUpdate], list[Finding]]`. For each file it runs `copier update --defaults --conflict=rej --skip-tasks -a <file> --vcs-ref <to>` (`--trust` only for legacy, `-d toolkit_version=<to>` for `core`, `--` before positionals). It then commits `chore(ci-kit): update <module> to <to>`. A `.rej` file or a Copier failure gives `CIK-UPDATE-001` and stops. At the end it calls `build_plan`.
- CLI: `ci-kit update --repo PATH --to REF [--module M]... [--skip-legacy] [--template-src PATH] [--json PATH]`. JSON shape is as in spec §6. Exit 1 on any finding.

- [ ] **Step 1: Write the failing tests** against a lifecycle template repo (`v99.0.0` → `v99.1.0`):

```python
def test_order_legacy_core_languages_others(self):
    self.assertEqual(update_order({"java": p, "legacy": p, "core": p, "zeta": p, "node": p}), ["legacy", "core", "java", "node", "zeta"])
def test_one_commit_per_changed_module(self): ...
def test_core_caller_follows_to_ref(self): ...   # uses: ...validate.yml@v99.1.0
def test_rej_stops_with_cik_update_001(self): ...
def test_skip_legacy_leaves_legacy_answers_untouched(self): ...
```

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `python tests/test_ci_kit_update.py -v && python scripts/validate.py --static`.
- [ ] **Step 5: Commit** `feat(ci-kit): add update command for all answers files`.

### Task 4: `ci-kit migrate` + lifecycle

**Files:**
- Create: `tools/ci_kit/migrate.py`
- Modify: `tools/ci_kit/cli.py`, `tools/ci_kit/codes.py`, `docs/errors/CIK-MIGRATE-00{1..4}.md`
- Test: `tests/test_ci_kit_migrate.py`, `tests/test_migration_lifecycle.py`; add a `name: Validate` assertion to `tests/test_modules_render.py`

**Interfaces:**
- Consumes: `AREAS`, `area_for_module` (Task 1); `run_update` internals for Copier invocation (Task 3); `REF_RE` normalization and `render_module` (prototype drift).
- `@dataclass MigrateResult(module: str, area: str, ref: str, checks_before: list[str], checks_after: list[str], commits: list[str], findings: list[Finding])`
- `CHECKS_AFTER = {"core": ["ci / gate"]}`
- `legacy_check_names(validate_yml: Path) -> list[str]` returns the `name:` of every job in the legacy validate workflow, or the job id when it has no name, in file order.
- `migrate(repo: Path, module: str, to: str, *, src_override: str | None = None) -> MigrateResult` runs the preconditions and steps from spec §7.1–7.2. Precondition failures return early with no commits.
- CLI: `ci-kit migrate --module M --repo PATH --to REF [--template-src PATH] [--json PATH]`. Exit 1 on findings.

- [ ] **Step 1: Write the failing tests.**
  - Unit: one test per `CIK-MIGRATE-00x`; a hand-edited `validate.yml` gives `CIK-MIGRATE-003` with `+++` in the message; `legacy_check_names` on a rendered legacy validate gives `["Shared template contract"]` for a component-less scenario.
  - Lifecycle, for each component-less scenario in `tests/scenarios/*.yml` (all except `java`, `node`, `python`, `polyglot`, `same-type`, `docker`, `allure-polyglot`):
    - render legacy at `v99.0.0`;
    - `migrate core` gives no findings, 2 commits, and `validate.yml` now owned by `core`;
    - `update` to `v99.1.0` gives no `.rej` and a clean plan;
    - `git revert` of the migrate commits restores the pre-migration tree byte-for-byte.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `python tests/test_ci_kit_migrate.py -v && python tests/test_migration_lifecycle.py -v && python scripts/validate.py --static`. Record the lifecycle suite wall time.
- [ ] **Step 5: Commit** `feat(ci-kit): add migrate command for area handoff`.

### Task 5: Fleet — module inventory and module-aware `update`

**Files:**
- Modify: `scripts/update_copier_fleet.py`, `scripts/fleet_health.py` (report columns only)
- Test: `tests/test_update_copier_fleet.py`, `tests/test_fleet_health.py`

**Interfaces:**
- `fetch_answers_files(repository: str, *, env) -> dict[str, str]` maps a module (`"legacy"` for `.copier-answers.yml`) to raw text. It uses `gh api repos/{r}/contents/` (root listing), selects regular files matching `^\.copier-answers(\.[a-z][a-z0-9-]*)?\.yml$`, and skips symlinks.
- `TemplateInventory` gains `modules: tuple[str, ...]` and `state: str` (`legacy` | `hybrid(<areas comma-joined>)` | `modules`). Areas come from the legacy `handed_off`.
- `Result.status` gains `foreign-module-template`, `unknown-module` and `unsafe-answers-file` for the Review Focus 5 cases. These are reported and not processed.
- After the existing `update_template(...)`, `process_repository` runs `python -m ci_kit.cli update --repo <dest> --to <ref> --skip-legacy --json <tmp>` when any module answers exist. Its module rows go into the PR body as `| module | old → new | doctor |`.
- JSON report: schema version +1. It adds `repositories[].template.modules` and `.state`; existing keys are unchanged. Markdown and health reports gain a "Modules / state" column.

- [ ] **Step 1: Write failing tests** in the existing fake-`gh` style: listing with legacy + core; symlinked module answers; foreign `_src_path`; `unknown-module`; PR body module table; JSON schema version bump.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `python tests/test_update_copier_fleet.py -v && python tests/test_fleet_health.py -v && python scripts/validate.py --static`.
- [ ] **Step 5: Commit** `feat(fleet): inventory module answers and update modules`.

### Task 6: Fleet — `migrate` mode

**Files:**
- Modify: `scripts/update_copier_fleet.py`
- Test: `tests/test_update_copier_fleet.py`

**Interfaces:**
- New flag `--migrate MODULE`, mutually exclusive with normal update behaviour. The default branch name becomes `ci-kit/migrate-<module>` unless `--branch` is given.
- `migrate_repository(repository, *, module, target_ref, dry_run, env, workspace) -> Result` with these statuses:
  - `not-applicable` (no legacy answers, or area already handed off);
  - `needs-update-first` (legacy `_commit` ≠ target);
  - `migrate-rejected` (detail = codes; the JSON report carries the diff);
  - `would-migrate` (dry run);
  - `pull-request`.
- `migration_pr_body(result: MigrateResult, repository: str) -> str` contains a summary, commits, `doctor` output, `checks_before → checks_after`, the exact command `python scripts/migration_checks.py --pr <repo>#<n>` (filled after creation by editing the body), rollback (`git revert` + `--revert`), then the line `<!-- ci-kit-migrate -->` and the ```json block (spec §7.3).
- Title: `chore(ci-kit): hand off <area> to <module> module`.

- [ ] **Step 1: Write failing tests:** each status; body contains the marker + parsable JSON; `--repo quokkify/knowledge --migrate core` processes a private repo (no `--public-only` filtering when `--repo` is given); rejected → no `push_automation_branch` call.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `python tests/test_update_copier_fleet.py -v && python scripts/validate.py --static`.
- [ ] **Step 5: Commit** `feat(fleet): add migrate mode that opens handoff PRs`.

### Task 7: State-aware pilot gate

**Files:**
- Modify: `scripts/verify_ci_gate.py`, `scripts/rollout_copier_fleet.py`
- Test: `tests/test_ci_rollout.py`

**Interfaces:**
- `pilot_contexts(repository: str, pr: int | None, *, handed_off: tuple[str, ...] = ()) -> tuple[str, ...]`. When `"validate"` is in `handed_off`, it replaces `"Shared template contract"` with `"ci / gate"`. `verify_pilot` reads `handed_off` from the pilot's legacy answers at the verified SHA (it already reads the answers there).
- `MIGRATE_PILOTS = ("quokkify/gh-pages-subdir-action", "quokkify/java-test-automation-template", "quokkify/renovate-presets")`.
- `rollout(...)` with `--migrate`: the pilots are `MIGRATE_PILOTS`, and their expected contexts are `checks_after` parsed from each pilot PR body (marker block). Pilots with status `not-applicable` / `needs-update-first` stop the rollout with a clear `GateError`.

- [ ] **Step 1: Write failing tests:** hybrid pilot in update rollout expects `ci / gate`; legacy pilot unchanged; migrate rollout uses PR-body `checks_after`; missing marker → `GateError`.
- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `python tests/test_ci_rollout.py -v && python scripts/validate.py --static`.
- [ ] **Step 5: Commit** `feat(fleet): make pilot gates follow handed-off areas`.

### Task 8: `scripts/migration_checks.py`

**Files:**
- Create: `scripts/migration_checks.py`
- Test: `tests/test_migration_checks.py`

**Interfaces:**
- `parse_marker(body: str) -> dict` raises `ValueError` without the marker block.
- `next_checks(current: list[str], before: list[str], after: list[str], *, revert: bool) -> list[str]`:
  - forward = `current − before + after`;
  - revert = `current − after + before`;
  - order preserved, duplicates removed;
  - raises `ValueError` on an empty result.
- `main(argv) -> int` takes `--pr OWNER/REPO#N`, `[--revert]` and `[--dry-run]`. It reads the PR body and head SHA via `gh`, reads current required checks via `fleet_health.required_checks` on the PR base branch, then calls `reconcile_ruleset.reconcile(..., revision=<head>, mode="active", dry_run=...)`. It prints the before and after lists.

- [ ] **Step 1: Write failing tests:**

```python
def test_forward_keeps_other_checks(self):
    self.assertEqual(next_checks(["Gitleaks", "Shared template contract", "CodeQL"], ["Shared template contract"], ["ci / gate"], revert=False), ["Gitleaks", "CodeQL", "ci / gate"])
def test_idempotent_when_already_applied(self):
    self.assertEqual(next_checks(["Gitleaks", "ci / gate"], ["Shared template contract"], ["ci / gate"], revert=False), ["Gitleaks", "ci / gate"])
def test_revert(self): ...
def test_empty_result_rejected(self): ...
def test_missing_marker_rejected(self): ...
def test_main_passes_head_sha_as_revision(self): ...  # fake gh + fake reconcile
```

- [ ] **Step 2: Run them and confirm they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `python tests/test_migration_checks.py -v && python scripts/validate.py --static`.
- [ ] **Step 5: Commit** `feat: add migration_checks helper for required check handoff`.

### Task 9: Consumer `copier-update.yml` + ring 0

**Files:**
- Modify: `template/.github/workflows/copier-update.yml.jinja`, `scripts/validate_modules_fixture.py`
- Test: `tests/test_copier_update_workflow.py`

**Interfaces:**
- `copier-update.yml.jinja` adds a step after the legacy update. When any `.copier-answers.*.yml` exists, it installs ci-kit at `"$ref"` (`python -m pip install "git+https://github.com/quokkify/ci-kit@$ref"`) and runs `ci-kit update --repo . --to "$ref" --skip-legacy`. Expressions go through `env:`.
- `validate_modules_fixture.py` adds a case: render the legacy scenario `tests/scenarios/allure-external.yml` at `v99.0.0`, then `ci-kit migrate --module core`, then `doctor`, which must exit 0.

- [ ] **Step 1: Write the failing workflow test and the new fixture case; run them and confirm they fail.**
- [ ] **Step 2: Implement.**
- [ ] **Step 3: Run** `python tests/test_copier_update_workflow.py -v && python scripts/validate_modules_fixture.py && python scripts/validate.py --static`.
- [ ] **Step 4: Commit** `feat(template): update module answers from consumer copier-update`.

### Task 10: Docs, push, draft PR

**Files:**
- Create: `docs/modules.md`. It covers: migrate procedure per repo (fleet `--migrate` → green PR → `migration_checks.py` → merge), rollback (`git revert` + `migration_checks.py --revert`), the `CIK-MIGRATE-003` blocker, and states (`legacy` / `hybrid` / `modules`).
- Modify: `docs/architecture.md` (new decision: hybrid handoff, fleet migrate mode, rules unchanged by fleet), `README.md` (link).

- [ ] **Step 1: Write docs; run** `python scripts/validate.py --static` (link checks).
- [ ] **Step 2: Commit** `docs: document module migration`.
- [ ] **Step 3: Controller only:** push `feat/393-migration` and open a draft PR with base `feat/393-modular-ci-prototype`, referencing #393. Wait for CI to go green.

### Task 11: Wave 1 execution (outward; explicit user approval before each numbered step)

Preconditions:
- prototype Task 8 recorded as go;
- #406, #407 and this PR merged;
- ci-kit release `vX` published by release-please.

1. Dry run: `python scripts/update_copier_fleet.py --org quokkify --public-only --dry-run --migrate core --markdown-report <tmp>`. Review the statuses with the user.
2. Normal fleet `update` to `vX` (existing rollout). Wait until every target repo's update PR is merged.
3. `python scripts/rollout_copier_fleet.py --migrate core --write ...` for the pilots. For each pilot PR: CI green → `python scripts/migration_checks.py --pr <repo>#<n> --dry-run` → user approval → run without `--dry-run` → merge (user or maintainer).
4. Ship the next ci-kit release through the normal `update` rollout. The hybrid pilots must pass the state-aware gate without conflicts.
5. Remaining public repos with `--migrate core --write`. Then `knowledge` with `--repo quokkify/knowledge --migrate core --write`. Repeat the per-PR procedure from step 3.
6. Record the readiness criteria from spec §11 in a comment on #393.

# Fleet Direct Merge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every automatic fleet run merges an already open, eligible template-update PR once its checks are green, without `allow_auto_merge`.

**Architecture:** `enable_auto_merge` gains a direct-merge branch for repos with auto-merge disabled, gated by a pure `checks_verdict`. A new `--merge-ready` mode skips Copier and only evaluates and merges existing automation PRs, reading versions from the GitHub API. The rollout passes `--auto-merge`, and the workflow adds a daily merge-ready step.

**Tech Stack:** Python 3.15 stdlib, `gh` CLI, unittest (`scripts/run_test_suites.py`), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-22-fleet-direct-merge-design.md`

## Global Constraints

- Direct merge command: `gh pr merge <n> --repo <owner/name> --squash --match-head-commit <sha>`; never `--auto` when `allow_auto_merge` is false.
- Ready = rollup non-empty; every CheckRun `COMPLETED` with conclusion in `SUCCESS`, `NEUTRAL`, `SKIPPED`; every StatusContext `SUCCESS`; newest `completedAt`/`startedAt` at least 10 minutes old; `mergeable == "MERGEABLE"`.
- New `auto_merge.state` values: `merged`, `waiting`; failed checks are `skipped` with reason `failed checks: <names>`.
- Eligibility rules from #455 do not change.
- `--merge-ready` requires `--write --auto-merge`; rejected with `--dry-run`, `--migrate`, `--template-ref`.
- Daily merge-ready runs only on schedule `17 4 * * 0,2-6`, with `FLEET_UPDATE_TOKEN`.
- Run one suite: `python scripts/run_test_suites.py --suite <file>`.

## Review Focus

- Repo with `allow_auto_merge: true` must behave exactly as before (native `--auto`), including the disarm paths. Task 2 keeps the existing `AutoMergeTests` green unchanged except the one case that used to expect `repository has auto-merge disabled`.
- PR whose head moved after the checks were read: `--match-head-commit` makes `gh` fail; the outcome must be `failed`, not a crash of the repository. Task 2 test.
- `--merge-ready` on a repo with no open automation PR must write nothing and report `up-to-date`. Task 3 test.
- Answers file added or removed between base and PR head: not eligible. Task 3 test.
- Rollout full mode with `--auto-merge` must still gate pilots before fanout. Task 4 test.

---

### Task 1: `checks_verdict`

**Files:**
- Modify: `scripts/update_copier_fleet.py` (next to `AUTO_MERGE_*` constants, ~line 2210)
- Test: `tests/test_update_copier_fleet.py` (new `ChecksVerdictTests`)

**Interfaces:**
- Produces: `AUTO_MERGE_SETTLE = timedelta(minutes=10)`; `checks_verdict(rollup: object, now: datetime) -> tuple[str, str]` returning `("ready", "all checks green")`, `("waiting", <reason>)` or `("failed", "failed checks: A, B")`.

- [ ] **Step 1: Write failing tests**

```python
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
def check(name="ci / gate", status="COMPLETED", conclusion="SUCCESS", completed="2026-10-22T11:30:00Z"): ...
def context(name="CodeRabbit", state="SUCCESS"): ...  # __typename StatusContext, startedAt 11:30

test_green_and_settled_is_ready: [check(), check("x", conclusion="SKIPPED"), context()] -> ("ready", "all checks green")
test_empty_or_unreadable_rollup_waits: [], None, "x" -> state "waiting", reason "no checks reported yet"
test_running_check_waits: check(status="IN_PROGRESS", conclusion=None) -> ("waiting", "checks still running")
test_pending_status_context_waits: context(state="PENDING") -> "waiting"
test_recent_completion_waits: check(completed="2026-10-22T11:55:00Z") -> ("waiting", "checks finished less than 10 minutes ago")
test_failed_checks_named: [check("a", conclusion="FAILURE"), context("b", "ERROR"), check("c", conclusion="CANCELLED")] -> ("failed", "failed checks: a, b, c")
test_failure_wins_over_pending
```

- [ ] **Step 2: Run** `python scripts/run_test_suites.py --suite test_update_copier_fleet.py` — Expected: FAIL, `checks_verdict` undefined.
- [ ] **Step 3: Implement** `checks_verdict`; a CheckRun name is `name`, a StatusContext name is `context`.
- [ ] **Step 4: Run** the suite — Expected: PASS.
- [ ] **Step 5: Commit** `feat(fleet): checks verdict for direct merge`

### Task 2: Direct merge in `enable_auto_merge`

**Files:**
- Modify: `scripts/update_copier_fleet.py:2212` (`AUTO_MERGE_PULL_FIELDS`), `:2239-2322` (`enable_auto_merge`)
- Test: `tests/test_update_copier_fleet.py` (`AutoMergeTests`)

**Interfaces:**
- Consumes: `checks_verdict` (Task 1).
- Produces: `enable_auto_merge(..., now: Callable[[], datetime] = lambda: datetime.now(timezone.utc))`; outcomes `{"state": "merged", "reason": f"merged {sha}"}`, `{"state": "waiting", ...}`, `{"state": "skipped", "reason": "failed checks: ..."}`.

- [ ] **Step 1: Write failing tests** in `AutoMergeTests` (`VIEW` gains `statusCheckRollup`; `pull()` gains a green settled rollup by default; pass a fixed `now`):

```python
DIRECT = ["gh", "pr", "merge", "7", "--repo", "quokkify/example", "--squash", "--match-head-commit", HEAD]
test_auto_merge_disabled_merges_green_pull_request_directly:
    enable(settings=settings(allow_auto_merge=False)) -> {"state": "merged", "reason": f"merged {HEAD}"}; commands == [DIRECT]
test_direct_path_waits_for_running_checks: rollup IN_PROGRESS -> state "waiting", commands == []
test_direct_path_skips_failed_checks: rollup FAILURE "ci / gate" -> {"state": "skipped", "reason": "failed checks: ci / gate"}
test_direct_merge_rejected_head_is_failed: merge=(1, "head moved") -> {"state": "failed", "reason": "head moved"}
test_direct_path_never_uses_auto_flag: no command contains "--auto"
```

Remove the `repository has auto-merge disabled` row from `test_ineligible_pull_requests_are_skipped_with_an_exact_reason`.

- [ ] **Step 2: Run** the suite — Expected: new tests FAIL.
- [ ] **Step 3: Implement**: in `evaluate()`, keep every existing check in order; where `not settings["allow_auto_merge"]` returned `skipped`, set a `direct` flag instead. Run the squash, state, base, conflict and head checks. Then, if `direct`: `checks_verdict(pull_request.get("statusCheckRollup"), now())`, map `ready` → `run([... DIRECT ...])`, `waiting` → `("waiting", reason)`, `failed` → `("skipped", reason)`. The armed-auto-merge disarm block applies only when not `direct`.
- [ ] **Step 4: Run** the suite — Expected: PASS, all previous `AutoMergeTests` included.
- [ ] **Step 5: Commit** `feat(fleet): merge eligible template PRs directly when auto-merge is off`

### Task 3: `--merge-ready` mode

**Files:**
- Modify: `scripts/update_copier_fleet.py` (`build_parser` ~2691, `main` ~2715 and repository loop ~2836)
- Test: `tests/test_update_copier_fleet.py` (new `MergeReadyTests`)

**Interfaces:**
- Consumes: `enable_auto_merge` (Task 2).
- Produces: flag `--merge-ready`; `answers_versions(repository: Repository, ref: str, *, env) -> dict[str, str | None]` mapping each root `.copier-answers*.yml` to `_commit`; `merge_ready_repository(repository: Repository, *, branch: str, env: dict[str, str]) -> Result`.

- [ ] **Step 1: Write failing tests**

```python
test_parser_requires_write_and_auto_merge: ["--merge-ready", "--write"] and ["--merge-ready", "--dry-run"] -> SystemExit
test_parser_rejects_migrate_and_template_ref
test_no_open_pull_request_is_up_to_date_without_writes: pr list -> [] ; Result.status == "up-to-date"; no "merge" command
test_versions_come_from_base_and_head: base {".copier-answers.yml": "v3.2.0", ".copier-answers.core.yml": "v3.2.0"}, head both "v3.3.0"
    -> enable_auto_merge called with applied="v3.2.0", target="v3.3.0", modules=[{"module": "core", "from": "v3.2.0"}], head=None, requested_template_ref=None
test_added_or_removed_answers_file_is_skipped: Result.auto_merge == {"state": "skipped", "reason": "answers files differ: .copier-answers.core.yml"}
test_main_routes_merge_ready_and_never_clones: clone_repository not called
```

- [ ] **Step 2: Run** the suite — Expected: FAIL.
- [ ] **Step 3: Implement**: in `merge_ready_repository`, find the PR with `gh pr list --repo <r> --head <branch> --state open --json url,headRefOid`, read `answers_versions` at the default branch and at `headRefOid` (via `gh api repos/<r>/contents?ref=` plus a per-file content read; parse `_commit` with `parse_answers`). The legacy file gives `applied`/`target`; module files become `modules` rows. Return `Result(name, "pull-request", url, auto_merge=...)`. In `main`, use the `args.merge_ready` branch instead of `process_repository`; `template_ref` resolution is not needed.
- [ ] **Step 4: Run** the suite — Expected: PASS.
- [ ] **Step 5: Commit** `feat(fleet): --merge-ready mode for daily merges`

### Task 4: Rollout accepts `--auto-merge` and passes `--merge-ready` through

**Files:**
- Modify: `scripts/rollout_copier_fleet.py:60-72`
- Test: `tests/test_ci_rollout.py:293`

- [ ] **Step 1: Replace** `test_auto_merge_is_refused_until_the_pilot_gate_is_wired` with:
  - `test_auto_merge_is_forwarded_to_every_stage`: every `run_update` argument list contains `--auto-merge`, and pilots are still verified before fanout;
  - `test_merge_ready_bypasses_the_pilot_gate`: one subprocess call with the original arguments, and no `verify_pilot`.
- [ ] **Step 2: Run** `python scripts/run_test_suites.py --suite test_ci_rollout.py` — Expected: FAIL.
- [ ] **Step 3: Implement**: delete the `GateError`; the pass-through condition becomes `args.dry_run or args.repo or args.merge_ready`.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `feat(rollout): wire auto-merge and merge-ready into the fleet rollout`

### Task 5: Workflow, report, docs

**Files:**
- Modify: `.github/workflows/copier-fleet-auto-update.yml`, `scripts/update_copier_fleet.py:1273-1276` (context line), `docs/fleet-health.md` §Auto-merge
- Test: `tests/test_update_copier_fleet.py` (report test), actionlint

- [ ] **Step 1: Failing test**: the report context line lists `1 merged` and `1 auto-merge waiting on CI` for results with those `auto_merge.state` values.
- [ ] **Step 2: Implement**: add `("waiting", "auto-merge waiting on CI")` to the state loop. In the workflow's write branch, run `args+=(--write --auto-merge)`. Add the step `Merge ready template updates` with `if: github.event.schedule == '17 4 * * 0,2-6'`. It runs `rollout_copier_fleet.py --org quokkify --public-only --exclude quokkify/.github --exclude quokkify/ci-kit --branch automation/copier-template-update --write --auto-merge --merge-ready` with `GH_TOKEN: ${{ secrets.FLEET_UPDATE_TOKEN }}` and appends its markdown report to `$GITHUB_STEP_SUMMARY`.
- [ ] **Step 3: Docs**: in `fleet-health.md` §Auto-merge, describe the direct path (D1/D2), `--merge-ready`, the daily schedule, and the canary finding (never `--auto` without `allow_auto_merge`).
- [ ] **Step 4: Verify**: the full suite (`python scripts/run_test_suites.py`), `actionlint .github/workflows/copier-fleet-auto-update.yml`, `python scripts/validate.py --static`.
- [ ] **Step 5: Commit** `feat(ci): daily merge of ready fleet template updates`

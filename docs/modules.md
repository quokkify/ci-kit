# Modules and migration

ci-kit consumers move from the single legacy Copier template to modules (`core` first) without a flag day. Ownership of files is handed over one area at a time, so a repository can run both for a while.

## States

The fleet inventory and the health report classify every repository by its answers files:

| State | Meaning |
|---|---|
| `legacy` | Only `.copier-answers.yml`; `handed_off` is empty. |
| `hybrid(<areas>)` | Legacy answers plus module answers (`.copier-answers.<module>.yml`); `<areas>` lists `handed_off`. `hybrid()` means modules are installed but nothing is handed off, which `ci-kit plan` reports as `CIK-HANDOFF-002`. |
| `modules` | Module answers only; the legacy template is gone. |

`handed_off` is a legacy answer: a YAML list of area names (currently `validate`, owned by `core`) whose files legacy stops rendering through `_exclude`. Duplicates and unknown areas are rejected by the Copier validator. Every file has exactly one owner at any moment: `ci-kit plan` reports `CIK-HANDOFF-001` when an area is handed off but its module is missing, `CIK-HANDOFF-002` when a module is installed but legacy still owns its area, and `CIK-HANDOFF-003` for a malformed `handed_off`. Legacy contract checks (`CIK-LEGACY-*`) apply only to legacy-owned files.

## Migrating one repository

Update and migrate never share a PR. The repository must already be on the target ref (`needs-update-first` otherwise). Repositories with language `components` cannot migrate yet (see [Blockers](#blockers)).

1. Dry run, then the real run:

   ```console
   python scripts/update_copier_fleet.py --dry-run --repo OWNER/REPO --migrate core
   python scripts/update_copier_fleet.py --repo OWNER/REPO --migrate core
   ```

   The dry run reports `would-migrate` for a repository it would migrate, without pushing. The real run opens a PR on `ci-kit/migrate-core` that removes legacy `validate.yml` and adds the `core` module. For pilot rings use `rollout_copier_fleet.py --migrate core` (pilots: `MIGRATE_PILOTS` in that script).
2. Wait for the PR check `ci / gate` to be green.
3. Preview the required-checks switch, then apply it:

   ```console
   python scripts/migration_checks.py --pr OWNER/REPO#N --dry-run
   python scripts/migration_checks.py --pr OWNER/REPO#N
   ```

4. Merge the PR.

The fleet never merges PRs and never edits rulesets. Required checks change only through `migration_checks.py`, run by a human with `GH_TOKEN` or `GITHUB_TOKEN` (never a CLI argument).

### What `migration_checks.py` changes

It edits only the managed ruleset `ci-kit/security/<owner>/<repo>`. It reads the migrate marker (`<!-- ci-kit-migrate -->` followed by a json block) from the PR body, computes `current - checks_before + checks_after` (reversed with `--revert`), and calls the `reconcile_ruleset.py` logic at the PR head SHA. Reconcile rewrites the whole managed ruleset, including `strict_required_status_checks_policy` and the pull_request parameters, to the values it would write for a normal reconcile. Run it only when nobody else is editing that ruleset: it reads and then writes, so a concurrent edit can be lost.

The PR body is not trusted for check names: `checks_after` must equal the module's `CHECKS_AFTER` (`core`: `ci / gate`) and `checks_before` must equal the job names of `validate.yml` at the PR base SHA (at `--revision` for `--revert`). Reconcile runs at the PR head SHA, or at `--revision` for `--revert`. The command refuses (exit 1, nothing changed) when:

- the marker is missing or malformed, or the PR head/base cannot be read;
- `checks_after` differs from `CHECKS_AFTER`, or `checks_before` differs from the legacy `validate.yml` jobs;
- `validate.yml` is missing, unreadable or defines no jobs, or has a job that calls a reusable workflow (`uses:`; its check context `<job name> / <called job>` cannot be derived);
- the managed ruleset does not exist, is not `active`, does not target `refs/heads/<base branch>`, has no or a duplicated `required_status_checks` rule, a malformed rule, several code-scanning rules, or a code-scanning rule that is not a single CodeQL tool with a known threshold and the `security_alerts_threshold` reconcile would write;
- a required check is bound to an integration (re-sending it would unbind it);
- the effective required checks cannot be read ("required checks unavailable"), or their status is unknown;
- a check being replaced is required by classic branch protection or another ruleset;
- the new set would be empty.

Exit 2 is a reconcile/API error, a missing `GH_TOKEN`/`GITHUB_TOKEN`, or an argument error (a malformed `--pr`, or `--revert` without `--revision <40-hex SHA>`). A PUT without `bypass_actors` is assumed to keep existing bypass actors; confirm with `--dry-run`.

## Rollback

Copier does not support downgrades, so rollback is a revert:

1. `git revert` the migration PR commit and open a revert PR.
2. Switch the checks back, using the head SHA of the revert PR:

   ```console
   python scripts/migration_checks.py --pr OWNER/REPO#N --revert --revision <revert PR head SHA>
   ```

3. Merge the revert PR.

## Blockers

Fleet statuses in migrate mode:

- `would-migrate`: dry run only; the migration succeeded in a scratch clone and nothing was pushed.
- `not-applicable`: no legacy answers, module already installed, or area already handed off. Also repositories with language `components` (detail `language components`, checked before cloning) and repositories whose `validate.yml` has another reusable-workflow job such as `docker` (`CIK-MIGRATE-005`, detail is its message): the `core` module would drop those jobs. They stay on the legacy template until the language-module waves; these do not fail the run.
- `needs-update-first`: legacy `_commit` differs from the target ref. Run the normal fleet update first, or `ci-kit update --repo . --module legacy --to <ref>`, then migrate.
- `migrate-rejected`: `ci-kit doctor` findings or a `CIK-MIGRATE-*` refusal other than `CIK-MIGRATE-005`. No branch, commit or PR is created and the run exits 1. The `detail` lists the codes; the JSON report holds `repositories[].migration` with findings and doctor output.

Run directly, `ci-kit migrate` runs the `ci-kit plan` checks on the result after its two commits. If they report findings, the command exits 1 but keeps both commits in the repository; inspect them and undo with `git reset --hard HEAD~2` if needed. The fleet migrates in a throwaway clone, so a rejected migration leaves nothing behind there.

`CIK-MIGRATE-003` means a file the module takes over (`.github/workflows/validate.yml`) was hand-edited and differs from the legacy render at the recorded `_commit`; the finding message holds the diff. Revert the edit to the rendered content, or move the custom logic into a project-owned workflow, then migrate again. See [CIK-MIGRATE-003](errors/CIK-MIGRATE-003.md); related runbooks: [001](errors/CIK-MIGRATE-001.md) (dirty tree), [002](errors/CIK-MIGRATE-002.md) (legacy `_commit` != target), [004](errors/CIK-MIGRATE-004.md) (nothing to hand off), [005](errors/CIK-MIGRATE-005.md) (language components or reusable-workflow jobs).

## The `Validate` name

The `core` caller keeps `name: Validate`. Allure external-mode `workflow_run` triggers subscribe to that workflow name, and a test in the module suite pins it. Do not rename it.

## Updating hybrid repositories

`ci-kit update` processes answers files in the order legacy, `core`, language modules, others, with one commit per answers file. The fleet `update` mode runs its own legacy pipeline first and then `ci-kit update --skip-legacy --to <ref>`, so `ci-kit` updates only the modules; files of handed-off areas are left to their module. The consumer's `copier-update.yml` runs the same `ci-kit update --repo . --to "$ref" --skip-legacy` after the legacy update. A `.rej` file or Copier failure stops the run with `CIK-UPDATE-001` ([runbook](errors/CIK-UPDATE-001.md)).

## Adding a handoff area

1. Register the area in `tools/ci_kit/handoff.py` (`AREAS`: owner module and legacy paths).
2. Add the legacy paths to `_exclude` in `copier.yml` guarded by `'<area>' in (handed_off | default([]))`.
3. Add the area to the `handed_off` validator's `allowed_handed_off` list in `copier.yml`.
4. Add the module's required checks to `CHECKS_AFTER` in `tools/ci_kit/migrate.py`.
5. Add the legacy-to-module check mapping to `HANDOFF_CONTEXTS` in `scripts/verify_ci_gate.py`.
6. Keep the registry, validator, and `_exclude` consistent; a test compares them.

# Modules and migration

ci-kit consumers move from the single legacy Copier template to modules (`core` first) without a flag day. Ownership of files is handed over one area at a time, so a repository can run both for a while.

## Available modules

| Module | Declaration | Copier question |
|---|---|---|
| `core` | none | `project_name`, `toolkit_version` |
| `java` | `.ci/modules/java.yml` | `java_components` |
| `python` | `.ci/modules/python.yml` | `python_components` |
| `node` | `.ci/modules/node.yml` | `node_components` |
| `docker` | `.ci/modules/docker.yml` | `docker_images` |
| `labels` | `.ci/modules/labels.yml` | `toolkit_version` |

A language declaration is `schema_version: 1` plus `components`, one mapping per component. Required fields: `id` (stable job identifier), `name` (display name), `path` (component directory, no `..` segment or glob characters), and the language version (`java_version` for `java`). Every other field is optional and mirrors an input of the module's reusable workflow in snake_case (for example `run_lint`, `test_command`, `timeout_minutes`); every `*_version` is a string such as `"3.12"`, and unknown keys are rejected. `docker` components need only `id`; `name`, `context`, `dockerfile` and `target` are optional.

The language version is optional in a declaration, but new projects pin it: the default answers of `python` and `node` set `python_version` and `node_version` to the workflow default. If a declaration omits it, the workflow default applies and can change with a ci-kit release. A docker `dockerfile` is resolved from the repository root, not from `context`.

`test_artifact_name` defaults to a fixed per-language name, so set a unique `test_artifact_name` per component when more than one component uploads test artifacts. For a legacy Allure report in component mode, every component must upload its results as `allure-results-<id>`: set `upload_test_artifacts: true`, `test_artifact_name: allure-results-<id>` and `test_artifact_path` to the Allure results directory. The legacy template used to add these uploads to its own `validate.yml`; modules do not add them automatically.

```yaml
schema_version: 1
components:
  - {"id": "backend-python", "name": "Backend Python", "path": "backend", "python_version": "3.12", "run_lint": false}
```

## States

The fleet inventory and the health report classify every repository by its answers files:

| State | Meaning |
|---|---|
| `legacy` | Only `.copier-answers.yml`; `handed_off` is empty. The current template refuses it with `CIK-LEGACY-006`. |
| `hybrid(<areas>)` | Legacy answers plus module answers (`.copier-answers.<module>.yml`); `<areas>` lists `handed_off`. `hybrid()` means modules are installed but nothing is handed off, which `ci-kit plan` reports as `CIK-HANDOFF-002`. |
| `modules` | Module answers only; the legacy template is gone. |

`handed_off` is a legacy answer: a YAML list of area names (currently `validate`, owned by `core`) whose files legacy stops rendering. `validate` is required and is the default for a new copy: the legacy template no longer ships `validate.yml`, so a list without it, or stored answers without the key, are refused by Copier and reported by `ci-kit plan` as `CIK-LEGACY-006` ([runbook](errors/CIK-LEGACY-006.md)). Duplicates and unknown areas are rejected by the Copier validator. Every file has exactly one owner at any moment: `ci-kit plan` reports `CIK-HANDOFF-001` when an area is handed off but its module is missing, `CIK-HANDOFF-002` when a module is installed but legacy still owns its area, and `CIK-HANDOFF-003` for a malformed `handed_off`. Legacy contract checks (`CIK-LEGACY-*`) apply only to legacy-owned files.

## Migrating one repository

The legacy-validate migration (`ci-kit migrate`, `update_copier_fleet.py --migrate`, `rollout_copier_fleet.py --migrate`) is retired: every consumer is already on the `core` module. Migration PRs that were already opened stay revertible; see [CIK-LEGACY-006](errors/CIK-LEGACY-006.md) for the path from a legacy `validate.yml` to modules and for the retirement details.

The fleet never merges migration PRs and never edits rulesets. Required checks change only through `migration_checks.py`, run by a human with `GH_TOKEN` or `GITHUB_TOKEN` (never a CLI argument). Today that is only used to roll a recorded migration back.

### What `migration_checks.py` changes

It edits one ruleset: the managed ruleset `ci-kit/security/<owner>/<repo>` when it exists, otherwise the repository ruleset that requires checks on the base branch (see below). It reads the migrate marker (`<!-- ci-kit-migrate -->` followed by a json block) from the PR body, computes `current - checks_before + checks_after` (with `--revert`: `current - checks_after + --restore`), and calls the `reconcile_ruleset.py` logic at the PR head SHA. Reconcile rewrites the whole managed ruleset, including `strict_required_status_checks_policy` and the pull_request parameters, to the values it would write for a normal reconcile. Run it only when nobody else is editing that ruleset: it reads and then writes, so a concurrent edit can be lost.

Without a managed ruleset (every consumer today has only a hand-made ruleset such as `main`), the target is the one ruleset that owns the effective required checks on the base branch, as reported by GitHub's branch rules. It is patched, not reconciled: the PUT resends its `name`, `target`, `enforcement`, `conditions`, `bypass_actors` and `rules` exactly as read and changes only the `required_status_checks` list. Kept checks keep their `integration_id`; added checks are sent without one. Only added checks must be published at the PR head SHA (at `--revision` for `--revert`). Right before the PUT the ruleset is read again and the command refuses if `updated_at` changed; after the PUT the readback must equal what was sent. An already applied switch is a `noop` with no write.

The PR body is not trusted for check names: `checks_after` must equal the module's `CHECKS_AFTER` (`core`: `ci / gate`) and `checks_before` must equal the job names of `validate.yml` at the PR base SHA (at `--revision` for `--revert`). Reconcile runs at the PR head SHA, or at `--revision` for `--revert`. The command refuses (exit 1, nothing changed) when:

- the marker is missing or malformed, or the PR head/base cannot be read;
- `checks_after` differs from `CHECKS_AFTER`, or `checks_before` differs from the legacy `validate.yml` jobs;
- a `--restore` context is not in `checks_before`;
- `validate.yml` is missing, unreadable or defines no jobs, or has a job that calls a reusable workflow (`uses:`; its check context `<job name> / <called job>` cannot be derived);
- there is no managed ruleset and the base branch has zero or several rulesets requiring checks, classic branch protection, a required check without a ruleset id, or the one ruleset is not a repository ruleset, has duplicated check contexts, not `active`, hides `bypass_actors` from the token, has no or a duplicated `required_status_checks` rule, or changed between the read and the PUT;
- the managed ruleset is not `active`, does not target `refs/heads/<base branch>`, has no or a duplicated `required_status_checks` rule, a malformed rule, several code-scanning rules, or a code-scanning rule that is not a single CodeQL tool with a known threshold and the `security_alerts_threshold` reconcile would write;
- a required check in the managed ruleset is bound to an integration (re-sending it would unbind it);
- the effective required checks cannot be read ("required checks unavailable"), or their status is unknown;
- a check being replaced is required by classic branch protection or another ruleset;
- the new set would be empty.

Exit 2 is a reconcile/API error (a message starting `PUT applied` means the ruleset was written but its readback did not match: inspect it before re-running), a missing `GH_TOKEN`/`GITHUB_TOKEN`, or an argument error (a malformed `--pr`, `--revert` without `--revision <40-hex SHA>` or `--restore`, or `--restore` without `--revert`). For the managed ruleset, a PUT without `bypass_actors` is assumed to keep existing bypass actors; confirm with `--dry-run`. The output names the edited ruleset (`ruleset.id`, `ruleset.name`, `ruleset.managed`) and lists the checks it `removed` and `added`.

## Rollback

Copier does not support downgrades, so rollback is a revert:

1. `git revert` the migration PR commit and open a revert PR.
2. Switch the checks back, using the head SHA of the revert PR. `--restore` takes the `removed` list of the forward switch, one argument per context. A bare `--restore` restores none, for a switch that removed nothing. The revert always removes `checks_after`, because the reverted repository no longer publishes it:

   ```console
   python scripts/migration_checks.py --pr OWNER/REPO#N --revert --revision <revert PR head SHA> --restore "App Python / ci"
   ```

3. Merge the revert PR.

## The `Validate` name

The `core` caller keeps `name: Validate`. Allure external-mode `workflow_run` triggers subscribe to that workflow name, and a test in the module suite pins it. Do not rename it.

## Updating hybrid repositories

`ci-kit update` processes answers files in the order legacy, `core`, language modules, others, with one commit per answers file. The fleet `update` mode runs its own legacy pipeline first and then `ci-kit update --skip-legacy --to <ref>`, so `ci-kit` updates only the modules; files of handed-off areas are left to their module. The consumer's `copier-update.yml` runs the same `ci-kit update --repo . --to "$ref" --skip-legacy` after the legacy update. A `.rej` file or Copier failure stops the run with `CIK-UPDATE-001` ([runbook](errors/CIK-UPDATE-001.md)).

## Adding a handoff area

1. Register the area in `tools/ci_kit/handoff.py` (`AREAS`: owner module and legacy paths).
2. Add the legacy paths to `_exclude` in `copier.yml` guarded by `'<area>' in (handed_off | default([]))`.
3. Add the area to the `handed_off` validator's `allowed_handed_off` list in `copier.yml`.
4. Add the module's required checks to `CHECKS_AFTER` in `tools/ci_kit/migration_marker.py`.
5. If handing off the area changes which checks a pilot publishes, extend `template_contexts` in `scripts/verify_ci_gate.py`. Today it handles `validate`: `CHECKS_AFTER["core"]` once handed off, otherwise the contexts of the pilot's legacy `validate.yml` jobs.
6. Keep the registry, validator, and `_exclude` consistent; a test compares them. An area whose legacy files are removed from the template (as `validate` was) needs no `_exclude` entry.

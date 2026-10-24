# Modules

ci-kit is a set of Copier modules. Each module is a Copier template under `modules/<module>/template` selected with `-d module=<module>` (default `core`), and each records its answers in `.copier-answers.<module>.yml`. Since v4.0.0 there is no legacy template: `module: legacy`, `template/` and the root `.copier-answers.yml` are gone. A repository that still has legacy answers leaves them as described in [leaving the legacy template](#leaving-the-legacy-template).

## Available modules

| Module | Declaration | Copier question |
|---|---|---|
| `core` | none | `project_name`, `toolkit_version` |
| `java` | `.ci/modules/java.yml` | `java_components` |
| `python` | `.ci/modules/python.yml` | `python_components` |
| `node` | `.ci/modules/node.yml` | `node_components` |
| `docker` | `.ci/modules/docker.yml` | `docker_images` |
| `security` | `.ci/modules/security.yml` | `codeql`, `gitleaks`, `codeql_languages`, `codeql_aggregate`, `gitleaks_job_name`, `gitleaks_scan`, `gitleaks_aggregate` |
| `release` | `.ci/modules/release.yml` | `release_mode` |
| `allure` | `.ci/modules/allure.yml` | `allure_*` |
| `renovate` | `.ci/modules/renovate.yml` | `renovate_config_repository`, `renovate_presets` |

The repository options `gitleaks_job_name`, `gitleaks_scan`, `gitleaks_aggregate`, `codeql_aggregate` and `allure_group_by` (an `allure_*` answer) are described in [usage](usage.md#repository-options).

A new repository is `core` plus any of `security`, `release`, `allure` and `renovate` plus one language module per stack.

A language declaration is `schema_version: 1` plus `components`, one mapping per component. Required fields: `id` (stable job identifier), `name` (display name), `path` (component directory, no `..` segment or glob characters), and the language version (`java_version` for `java`). Every other field is optional and mirrors an input of the module's reusable workflow in snake_case (for example `run_lint`, `test_command`, `timeout_minutes`); every `*_version` is a string such as `"3.12"`, and unknown keys are rejected. `docker` components need only `id`; `name`, `context`, `dockerfile` and `target` are optional.

The language version is optional in a declaration, but new projects pin it: the default answers of `python` and `node` set `python_version` and `node_version` to the workflow default. If a declaration omits it, the workflow default applies and can change with a ci-kit release. A docker `dockerfile` is resolved from the repository root, not from `context`.

`test_artifact_name` defaults to a fixed per-language name, so set a unique `test_artifact_name` per component when more than one component uploads test artifacts. For an Allure report in component mode, every component must upload its results as `allure-results-<id>`: set `upload_test_artifacts: true`, `test_artifact_name: allure-results-<id>` and `test_artifact_path` to the Allure results directory. Modules do not add these uploads automatically.

```yaml
schema_version: 1
components:
  - {"id": "backend-python", "name": "Backend Python", "path": "backend", "python_version": "3.12", "run_lint": false}
```

## States

The fleet inventory classifies every repository by its answers files:

| State | Meaning |
|---|---|
| `modules` | Module answers (`.copier-answers.<module>.yml`) only. |
| `legacy` | A root `.copier-answers.yml` whose `handed_off` does not list every area; the fleet refuses the repository with `CIK-CORE-030`. |

## Leaving the legacy template

ci-kit v4.0.0 cannot update a repository whose legacy `.copier-answers.yml` still renders files, and it has no `migrate-legacy` command. The legacy answers name the areas already owned by modules in `handed_off`; there are six areas: `validate`, `base`, `security`, `release`, `allure` and `renovate`.

- **Every area handed off.** The legacy answers render nothing. The fleet update and the consumer's `copier-update.yml` delete `.copier-answers.yml` in one `chore(ci-kit): drop empty legacy answers` commit and update the modules in the same run, on the same branch. To do it by hand, delete the file in its own commit (`git rm .copier-answers.yml`), then run `ci-kit update --repo . --to <v4 release>`. `ci-kit update` leaves such a file alone; it does not hold the module update back.
- **Any area left.** `ci-kit plan`, `ci-kit update`, the fleet update and `copier-update.yml` refuse the repository with [CIK-CORE-030](errors/CIK-CORE-030.md) and change nothing. Migrate it with ci-kit v3.11.x first: `ci-kit migrate-legacy` from that release hands the remaining areas to modules in one pull request. Merge it, delete the now empty `.copier-answers.yml` as above, then update to v4.

Required checks do not change when the legacy answers are dropped, so no ruleset switch is needed. The fleet never merges these PRs and never edits rulesets. Required checks change only through `migration_checks.py`, run by a human with `GH_TOKEN` or `GITHUB_TOKEN` (never a CLI argument). Today that is only used to roll a recorded validate migration back.

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

## Updating modules

`ci-kit update` refuses a repository with legacy answers that still render files (`CIK-CORE-030`) before it changes anything, then updates `core` first and the other modules in a fixed order, with one commit per answers file. The fleet `update` mode and the consumer's `copier-update.yml` first drop empty legacy answers (see above), then run `ci-kit update --repo . --to <ref>`. A `.rej` file or Copier failure stops the run with `CIK-UPDATE-001` ([runbook](errors/CIK-UPDATE-001.md)).

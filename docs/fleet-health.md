# Fleet adoption and health

The updater's optional `--include-health` adds read-only GitHub observations to
its existing console-independent JSON and Markdown reports:

```console
python scripts/update_copier_fleet.py --org quokkify --dry-run --include-health \
  --markdown-report /tmp/fleet.md --json-report /tmp/fleet.json
```

This flag does not change updates, branch protection, PR creation or exit-code
policy. The default invocation makes no additional health API calls. Health
is reporting evidence, not rollout approval. JSON schema version 5 preserves
all previous inventory and summary keys, adds `repositories[].health` when
requested, and adds `repositories[].template.modules` (module names from the
repository's `.copier-answers.<module>.yml` files) and `.state` (`legacy`,
`hybrid(<handed-off areas>)` or `modules`). Version 6 adds a top-level `run`
object: `mode` (`write` or `dry-run`), `template_ref` (resolved),
`requested_template_ref` (the `--template-ref` input), `trigger`
(`GITHUB_EVENT_NAME`), `run_url` (built from `GITHUB_SERVER_URL`,
`GITHUB_REPOSITORY` and `GITHUB_RUN_ID`) and `generated_at`; unset values are
`null`. A full rollout also records `rollout.stopped`, the reason the gate
stopped, or `null`. Repositories whose module answers are untrusted get
the statuses `foreign-module-template`, `unknown-module` or
`unsafe-answers-file` and are not processed. The recorded `template.commit` remains the updater's inventory;
`health.applied_template_version` comes from the actual default-branch answers
file read at `health.default_sha`. PR creation or a successful PR check does not
mean a template version has been adopted.

Each health snapshot records observation time, the actual default branch and
commit, applied and target versions, and default-commit CI. The automation PR
includes URL, state, age in whole days, head/base/merge revisions and CI evidence.
The newest matching automation PR is included even when closed or merged;
absence and inability to read PRs are separate states. `current` adoption means
the observed answers version equals the target; `different` deliberately makes
no claim about version ordering. Head, base and default identities are rechecked
before reporting. A concurrent change makes the affected observations unknown.

Required checks are read from GitHub's [effective branch rules endpoint](https://docs.github.com/en/rest/repos/rules#get-rules-for-a-branch),
which includes active repository and organization rules and excludes evaluate
and disabled rules. Classic required-status-check protection is read separately.
Discovered ruleset contexts and their integration IDs remain visible if classic
protection cannot be read. In that case complete policy coverage stays unknown.
A denied, missing or malformed protection response is not proof that protection
is absent. No active required checks yields `unprotected`, with unknown CI health.

CI observations use check runs and commit statuses requested for the exact SHA,
retain run/status IDs and app or creator provenance, and select the newest
matching check. Required integration IDs must match. PR check runs must also
identify that PR and its exact head/base pair; commit statuses alone cannot prove
PR-base association. Missing external security checks, skipped or neutral checks,
unbound check runs, stale revisions and unavailable API evidence remain unknown.
Known checks may individually pass while complete policy coverage is unknown.
No missing permission or missing evidence becomes a green aggregate.

The maintainer-owned `.github/fleet-health-exceptions.json` is empty by default.
To document an exception, add a repository with a non-empty `reason` and an
`expires_on` date in `YYYY-MM-DD` form. The date is inclusive and interpreted in
UTC. `--health-exceptions` selects another maintainer-controlled file; consumer
repositories are not consulted for exception policy. Expired exceptions are
flagged. Exceptions never change adoption, checks, branch rules or exit codes.
Invalid exception configuration fails before any update begins. Reports escape
untrusted Markdown text, and API errors use generic reasons rather than copying
raw CLI errors or credentials.

## Markdown report

The Markdown report (the job summary) opens with one verdict:
`❌ action required` for failures, configuration gaps (including expired
health exceptions), automation PRs with failed CI, failing required checks on
the default branch, migrations that need an update first, unrecognized statuses
or a stopped rollout; `🟡 drift in N` for
dry-run drift only; `🔀 N PRs open` when PRs are only in flight; otherwise
`✅ nothing to do`. A full rollout renders it once from the merged pilot and
fanout results. The context line shows mode, time, job log and target only
from the `run` block, never guessed, plus excluded repositories and the PRs
waiting on CI or ready to merge, and the PRs auto-merging or merged by
`--auto-merge`. Those PRs are not attention rows. An open pilot PR whose head
matches a successful pilot gate shows `CI ✅ · pilot gate`; this is render-only,
the JSON keeps the raw PR evidence and the gate result in `rollout.pilots`.

`Needs your attention` has one row per repository, ordered conflict, failure,
PR with failed CI, auto-merge failed, failing CI on `main`, configuration gap, needs update first,
drift, unrecognized status; any status the report does not know is shown with
its raw value. Re-run commands (for failed repositories and, in dry runs, the
write-mode command for drift, repeating an explicit template ref) and raw errors follow in a collapsed block. `not-managed`,
`not-applicable` and `foreign-template` repositories appear only in the fleet
table and do not count as managed. The collapsed fleet table lists Template,
CI on `main` (passed/required checks, with `?` for checks without exact-revision
evidence) and the last automation PR; inventory columns stay in the JSON report.
Untrusted text is neutralized with HTML entities in prose and table cells and
with `<pre>` blocks for raw errors, so no backslash escapes show.

Reading checks on private repositories may require Checks/Contents read access;
reading classic protection may require additional administration read access.
The reporter never asks for write permissions or changes protection to obtain
evidence. A narrower token simply produces an unknown observation.

## Auto-merge

```console
python scripts/update_copier_fleet.py --write --repo quokkify/skills --auto-merge
```

`--auto-merge` (off by default; rejected with `--dry-run` and `--migrate`)
merges each template-update PR the run created or refreshed, once the PR is
eligible. Write-mode workflow runs (release-triggered and the Monday run) pass
it. There are two paths, chosen by the repository's `allow_auto_merge` setting:

- **Native path** (`allow_auto_merge` on): `gh pr merge <number> --repo <repo>
  --auto --squash --match-head-commit <sha>`. The sha is the commit this run
  pushed, or the PR's current head when the branch already carried the same
  change. GitHub checks `--match-head-commit` only when auto-merge is enabled
  and keeps it armed across later pushes, so whenever the updater pushes a new
  head onto an existing automation PR that has auto-merge armed, it disables it
  (`--disable-auto`), with or without the flag, and then, with the flag, sends
  a fresh request bound to the new head. GitHub enforces the repository's
  rulesets and required checks at merge time.
- **Direct path** (`allow_auto_merge` off; no repository setting needed): the
  updater merges the PR itself with `gh pr merge <number> --squash
  --match-head-commit <sha>`, but only when the PR is ready (D2):
  every `statusCheckRollup` entry on the current head is completed with
  `SUCCESS`, `NEUTRAL` or `SKIPPED`; at least one check exists; the last one
  finished at least 10 minutes ago (late-registering workflows get time to
  appear); and `mergeable` is `MERGEABLE`. Otherwise the PR waits
  (`waiting`, merged by a later run) or is skipped with a reason such as
  `failed checks: ...` or `mergeability not yet computed`. GitHub still
  enforces rulesets on a direct merge.

Never run `gh pr merge --auto` on a repository where `allow_auto_merge` cannot
be armed: canary testing showed it then merges immediately and ignores
checks. The updater therefore uses the direct path, with its own readiness
check, instead.

The updater only reuses an open PR whose head branch lives in the repository
itself, never a fork's.

A PR is eligible only when all of these hold; otherwise it is skipped with
the reason in `repositories[].auto_merge.reason`:

1. the branch is the automation update branch, not `ci-kit/migrate-*`, and
   the PR's head is that branch in the repository itself;
2. the target is the release tag the run resolved, not an explicit `--template-ref`;
3. the applied version (`_commit` before the update) and every updated
   module's previous version are release tags with the same SemVer major as
   the target (for `v0`, the same minor), and the target is not older than the
   applied version; major bumps stay manual, while repair-only PRs on the same
   version (such as configuration-gap fixes) are eligible;
4. the repository's merge settings are readable and allow squash merge; a
   token that cannot read them makes the result `failed`, not `skipped`;
5. the PR is open, targets the default branch, is not `CONFLICTING`, and its
   head already shows the pushed commit (re-read up to three times).

If a skipped or failed PR still has auto-merge armed, the updater disables it
and says so in the reason. A rerun does not request auto-merge again when it
is already enabled.

### `--merge-ready`

```console
python scripts/update_copier_fleet.py --write --auto-merge --merge-ready --org quokkify --public-only
```

`--merge-ready` requires `--write --auto-merge` and is rejected with
`--dry-run`, `--migrate` and `--template-ref`. Copier does not run and nothing
is pushed: for each repository it takes the open PR from the automation
branch, checks that the template source is trusted and that the module
versions satisfy the same rules as above, and merges it by the direct path
when it is ready. `rollout_copier_fleet.py` accepts `--auto-merge` (forwarded
to every stage) and passes `--merge-ready` straight to the updater without the
pilot gate.

### Daily schedule

The daily run (`17 4 * * 0,2-6`) first does the read-only drift audit, then a
second step, `Merge ready template updates`, runs `--write --auto-merge
--merge-ready` with `FLEET_UPDATE_TOKEN`. The step runs even if the audit
step failed, because merging ready PRs does not depend on other repositories
being up to date. A PR that was not ready on one day is merged on a later one.

To stop one PR, close it, or disarm a native auto-merge with
`gh pr merge <url> --disable-auto`. A repository is excluded from fleet runs
with `--exclude`.

The JSON report adds `run.auto_merge` (`true`/`false`) and, for PRs evaluated
with the flag or disarmed after a push, `repositories[].auto_merge` with
`state` (`enabled`, `merged`, `waiting`, `skipped` or `failed`) and `reason`.
These fields are additive and the schema version is unchanged. A `failed`
state is an attention row but does not change the exit code; `enabled`,
`merged` and `waiting` are counted in the context line; `skipped` stays quiet.

## Migrate mode

```console
python scripts/update_copier_fleet.py --dry-run --repo quokkify/knowledge --migrate core
```

`--migrate MODULE` replaces the update flow: per repository it runs `ci-kit doctor`,
then `ci-kit migrate` on a clone and opens one squashed commit on
`ci-kit/migrate-MODULE` (override with `--branch`). Statuses: `not-applicable`
(no legacy answers, module installed, or area already handed off),
`needs-update-first` (legacy `_commit` differs from the resolved target ref),
`migrate-rejected`, `would-migrate` (dry run) and `pull-request`. An explicit
`--repo` is processed even when private; organization runs keep their filtering.

`migrate-rejected` opens no branch, commit or PR and makes the run exit 1. Its
`detail` lists the `CIK-*` codes; the JSON report adds
`repositories[].migration` with `findings` (`code`, `module`, `message`, `path`;
the `message` of `CIK-MIGRATE-003` carries the diff) and, for a failed doctor
run, `doctor` (its output). The schema version is unchanged.

The PR body ends with the `<!-- ci-kit-migrate -->` marker and a json block whose
`commits` is the pushed head SHA; the `migration_checks.py --pr` command is filled
with the real PR number by editing the body after creation.

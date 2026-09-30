# Fleet adoption and health

The updater's optional `--include-health` adds read-only GitHub observations to
its existing console-independent JSON and Markdown reports:

```console
python scripts/update_copier_fleet.py --org quokkify --dry-run --include-health \
  --markdown-report /tmp/fleet.md --json-report /tmp/fleet.json
```

This flag does not change updates, branch protection, PR creation or exit-code
policy. The default invocation makes no additional health API calls. Health
is reporting evidence, not rollout approval. JSON schema version 4 preserves
all previous inventory and summary keys and adds `repositories[].health` when
requested. The recorded `template.commit` remains the updater's inventory;
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

Reading checks on private repositories may require Checks/Contents read access;
reading classic protection may require additional administration read access.
The reporter never asks for write permissions or changes protection to obtain
evidence. A narrower token simply produces an unknown observation.

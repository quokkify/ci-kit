# Security

## Action pinning

Every third-party Action used by the toolkit is pinned to a full commit SHA with a nearby human-readable release comment. Renovate tracks and updates those pins. Consumer examples pin this toolkit to exact immutable SemVer release tags; `@main` is mutable and unsafe for production because an unrelated push changes executed code.

An exact release tag is immutable by project policy but Git itself permits tag deletion/recreation; a full commit SHA is cryptographically stronger. A mutable major alias such as `v1` intentionally moves and trades stronger immutability for easier updates.

## Permissions and secrets

Language and Docker workflows request only `contents: read`. Release Please needs `contents: write` and `pull-requests: write`. Docker login is skipped unless `push: true`; a caller pushing to GitHub Packages must explicitly grant `packages: write`, because the called workflow cannot elevate the caller token.

Only Docker push accepts secrets (`registry-username` and `registry-password`). They are passed directly to the pinned login Action and are never echoed. Build arguments are not secret-safe. Reusable workflows cannot elevate permissions granted by their caller, and secrets are not automatically forwarded through nested workflows unless explicitly passed or inherited.

## Cross-repository fleet automation

Full-fleet writes go through `scripts/rollout_copier_fleet.py`; direct use of the updater is reserved for targeted/manual maintenance. The coordinator verifies the target release commit, then checks an explicit public pilot policy before wider PR creation. Checks are read from exact commit endpoints and required check names must come from GitHub Actions/Advanced Security apps; a legacy commit status with a matching display name cannot satisfy them. Failed or pending additional statuses also block rollout. CodeQL workflow identity is verified separately because GitHub's external code-scanning rules are not consistently exposed as ordinary checks on default-branch commits.

The pilot policy is an explicit CI floor, not a claim to discover all effective organization rulesets: public-scoped credentials may lack administration access. Changes to required workflows/check names must update `PILOT_CHECKS` in `scripts/verify_ci_gate.py`. API failures and missing evidence stop rollout. The fleet token therefore additionally needs read access to Actions/checks/statuses on the public pilot repositories; no administration write or rule changes are required. Release gates use repository-scoped `GITHUB_TOKEN` with read-only Actions/contents permissions (preflight also reads pull requests).

The scheduled `Auto-update Copier-managed repositories` workflow writes to other repositories, which the repository-scoped `GITHUB_TOKEN` cannot do. It therefore uses a stored `FLEET_UPDATE_TOKEN` secret, a deliberate reversal of the earlier position that cross-repository writes never happen inside Actions. The reversal is bounded by three properties. The credential must be a GitHub App installation token or fine-grained token installed on public Quokkify repositories only, so it structurally cannot reach a private repository even if the script is misused. The script enforces the same boundary independently: `--public-only` filters organization discovery and rejects an explicitly requested non-public repository. Write mode fails fast when the secret is absent instead of silently falling back to a read-only token and failing deep inside the run.

The credential needs `Contents` read and write to clone and push the branch, `Pull requests` read and write to open and refresh the pull request, `Workflows` read and write because the template consists of workflow files and GitHub refuses a push that touches `.github/workflows` without it, and mandatory `Metadata` read. Pilot gates additionally require `Actions`, `Checks`, and `Commit statuses` read. Omitting `Workflows` fails late and confusingly, at the push, with `refusing to allow a Personal Access Token to create or update workflow ... without workflow scope`.

The workflow itself needs no elevated rights on this repository, so it requests `contents: read`; every write travels through the separate token. A repository guard keeps the schedule from running on forks. Private consumers are outside this automation entirely and are updated by a maintainer from a short-lived local `gh` session, so no credential with private access is stored in this public repository.

## Fork pull requests

GitHub normally withholds repository secrets from workflows triggered by untrusted fork pull requests. Keep PR validation read-only, do not use `pull_request_target` to execute fork code with write tokens, and keep push/release paths on trusted branch events.

## Security templates and repository rulesets

Copier answers `gitleaks: true` (the safe default) and `codeql: true` generate the
checksum-verified Gitleaks workflow and the SHA-pinned CodeQL workflow. Set either
answer to `false` to omit that workflow; `codeql_languages` is an optional validated
list, and otherwise languages are derived from `components`. Gitleaks publishes the
stable `gitleaks` check context. CodeQL's native code-scanning rule is preferred over
requiring a matrix job name.

Rulesets are external policy and are never applied by Copier. Use
`python scripts/reconcile_ruleset.py --repo OWNER/REPOSITORY --branch main
--check gitleaks --check CodeQL --revision SHA` with `GH_TOKEN` or `GITHUB_TOKEN`.
The default `evaluate` mode performs context preflight, writes an evaluate ruleset,
and verifies readback; `--dry-run` guarantees no POST/PUT. Only an explicit `--active` promotes the
managed ruleset. The fine-grained token or GitHub App needs `Administration: write` to create or
update rulesets, `Checks: read` to inspect check runs, `Commit statuses: read`
to inspect legacy status contexts, and `Metadata: read`; do not put it in
arguments, files, workflow logs, or Copier answers.

The command manages one namespaced ruleset, preserves unrelated rulesets and unknown
rules in the managed ruleset, and refuses duplicate managed identities. It requires
exact published check contexts for the target revision, blocks deletion and
non-fast-forward updates, and requires pull requests rather than direct commits.
After an active mutation it fetches the exact ruleset and compares normalized state;
a mismatch is a non-zero failure, not a success report. To recover a wrong check name,
run evaluate against a revision containing the real check, then rerun with the exact
context. Rollback is an explicit operator update to `--mode evaluate` or removal of
the managed ruleset through a separately reviewed GitHub administration action.

A ruleset only requires checks that already exist; it does not create workflows or
publish Gitleaks/CodeQL checks. Roll out with evaluate/readback first, then active
only after confirming the generated workflows are present on the target branch.

## Private workflow repositories

A private reusable workflow works only when the owner/organization grants the caller repository Actions access. The caller receives a scoped token to download workflow code, and outside collaborators may indirectly view logs containing workflow output. Cross-owner and organization policy restrictions apply; public toolkit workflows avoid most access-policy friction but remain supply-chain code and must be pinned.

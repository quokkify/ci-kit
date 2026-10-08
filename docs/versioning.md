# Versioning

- The toolkit publishes SemVer releases; the repository manifest starts at `0.0.0` until Release Please opens the first release PR.
- Production examples and generated callers use exact immutable tags such as `@v3.1.1`; release tags are never rewritten.
- Breaking workflow/input/template changes require a new major release.
- Release flow is a stable two-step process: pushes to `main` or manual dispatch run `.github/workflows/release.yml`, which calls `.github/workflows/release-please.yml` in `manifest` mode with `.github/release-please/config.json` and `.github/release-please/manifest.json`; maintainers then review and merge the Release Please PR. No manual tag or release command is part of the flow.
- Before Release Please runs, a read-only gate waits up to 30 minutes for successful `Validate toolkit` and `CodeQL` push runs on the exact triggering SHA and every merged `autorelease: pending` release candidate. The newest run/attempt must succeed; absent, queued, skipped, failed, or action-required evidence does not pass. If `main` has advanced, the stale release run stops; dispatch again on current `main`.
- After a release is created, an independent gate resolves its actual tag to a commit and verifies both workflows on that exact commit before fleet propagation. Release Please remains the version/tag authority. There is no atomic lock preventing `main` from advancing between preflight and Release Please; the post-tag guard prevents propagating an unchecked commit if that race occurs. Failed post-tag validation does not delete an already published release.
- Full write-mode fleet runs first create/update PRs for `gh-pages-subdir-action`, `skills`, and `q4j`. Each pilot must contain the target version in `.copier-answers.yml`, pass its configured test/security/template-contract checks and its exact-head CodeQL workflow, and retain the same head, base, and merge revision throughout the wait and immediately before remaining PRs are created. Its PR base must also equal the current target-branch commit, read independently from GitHub. PR checks and CodeQL runs must explicitly identify the current PR number/head/base combination. Missing merge identity or stale-base evidence stops rollout with instructions to update or rerun the PR. A pilot with no changes is checked on its exact default-branch SHA. Each pilot wait is limited to 15 minutes. A failure leaves pilot PRs available for inspection and prevents further fanout; nothing merges automatically.
- Nightly dry-run audits stay read-only and bypass pilot rollout. Explicit `repository` requests update only that repository. Weekly and release-triggered full writes use the same pilot gates; all use one resolved release tag throughout. After a pilot failure, inspect the run's partial report, repair the failure, and rerun the full workflow. These cross-repository CI paths require publication and a correctly scoped token to exercise; local mocked tests do not prove that live consumers pass.
- Renovate proposes toolkit upgrades through reviewable PRs.
- Before `1.0.0`, breaking changes are called out prominently in release notes even when SemVer permits a minor bump.
- Mutable major aliases such as `v1` may be convenient but are weaker supply-chain pins; this toolkit's production examples prefer exact release tags, while a full commit SHA is the strongest immutable reference.

- Template versioning and reusable workflow versioning are related but separate: `copier update` changes physical generated files, while Renovate changes referenced workflow tags.

Release notes

Release Please remains authoritative for versions and Conventional Commit
sections, including breaking changes from the `BREAKING CHANGE:` footer. Enrich
adds only the optional PR template sections `Highlight`, `Usage example`, and
`Migration`. Leave these fields empty for ordinary changes; a section that
holds only the template comment is skipped, while placeholder text such as
"None" is published verbatim. Each section is rendered once per release with
every contributing PR listed under it, so write entries that stand on their own
and avoid Markdown headings inside them. Usage examples may contain fenced
Markdown, including language identifiers.

Renovate dependency updates use `deps(<manager>)` Conventional Commits (for example `deps(github-actions)`) and appear
in the `📦 Dependencies` section; legacy `chore(deps)` commits stay in
`🧹 Chores`. Enrich does not move,
normalize, or synthesize chore/dependency entries.

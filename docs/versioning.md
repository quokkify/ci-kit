# Versioning

- The toolkit publishes SemVer releases; the repository manifest starts at `0.0.0` until Release Please opens the first release PR.
- Production examples and generated callers use exact immutable tags such as `@v4.3.4`; release tags are never rewritten.
- Breaking workflow/input/template changes require a new major release.
- Release flow is a stable two-step process: pushes to `main` or manual dispatch run `.github/workflows/release.yml`, which calls `.github/workflows/release-please.yml` in `manifest` mode with `.github/release-please/config.json` and `.github/release-please/manifest.json`; maintainers then review and merge the Release Please PR. No manual tag or release command is part of the flow.
- Before Release Please runs, a read-only gate waits up to 30 minutes for successful `Validate toolkit` and `CodeQL` push runs on the exact merge revision of every merged `autorelease: pending` release candidate, the only revisions Release Please tags, and on the triggering SHA itself when that commit merges a Release Please PR; ordinary pushes do not wait. The newest run/attempt must succeed; absent, queued, skipped, failed, or action-required evidence does not pass. If `main` has advanced, the stale release run stops; dispatch again on current `main`.
- After a release is created, an independent gate resolves its actual tag to a commit and verifies both workflows on that exact commit before fleet propagation. Release Please remains the version/tag authority. There is no atomic lock preventing `main` from advancing between preflight and Release Please; the post-tag guard prevents propagating an unchecked commit if that race occurs. Failed post-tag validation does not delete an already published release.
- Full write-mode fleet runs first create/update PRs for `gh-pages-subdir-action`, `skills`, and `q4j`. Each pilot must contain the target version in `.copier-answers.core.yml`, pass its configured test/security checks and its exact-head CodeQL workflow, and retain the same head, base, and merge revision throughout the wait and immediately before remaining PRs are created. Its PR base must also equal the current target-branch commit, read independently from GitHub. PR checks and CodeQL runs must explicitly identify the current PR number/head/base combination. Missing merge identity or stale-base evidence stops rollout with instructions to update or rerun the PR. A pilot with no changes is checked on its exact default-branch SHA. Each pilot wait is limited to 15 minutes. A failure leaves pilot PRs available for inspection and prevents further fanout; nothing merges automatically.
- Nightly dry-run audits stay read-only and bypass pilot rollout. Explicit `repository` requests update only that repository. Weekly and release-triggered full writes use the same pilot gates; all use one resolved release tag throughout. After a pilot failure, inspect the run's partial report, repair the failure, and rerun the full workflow. These cross-repository CI paths require publication and a correctly scoped token to exercise; local mocked tests do not prove that live consumers pass.
- Renovate proposes toolkit upgrades through reviewable PRs.
- Before `1.0.0`, breaking changes are called out prominently in release notes even when SemVer permits a minor bump.
- Mutable major aliases such as `v1` may be convenient but are weaker supply-chain pins; this toolkit's production examples prefer exact release tags, while a full commit SHA is the strongest immutable reference.

- Template versioning and reusable workflow versioning are related but separate: `copier update` changes physical generated files, while Renovate changes referenced workflow tags.

Release notes

Release Please stays authoritative for versions, tags and which commits belong to a
release. With `enrich-release-notes: true` the helper `enrich_release_notes.py` rebuilds
the notes of the top version from that list, the merged PRs' titles, labels and
`## Release note` / `## Usage example` / `## Migration` sections, and writes the same
text to the release PR body and the top section of `CHANGELOG.md`. The GitHub release is
created from the merged PR body, so all three surfaces match.

The rendered notes are: the Release Please version heading, a marker comment, an
`At a glance` count line, `⚠️ Breaking changes` (each in a warning box with its
migration steps, never repeated below), `✨ New`, `🐛 Fixed`, `🚀 Faster`, `⬆️ Updated`
(dependency updates in a user-facing type) and `↩️ Reverted`, then a collapsed
`Internal changes (N PRs)` list with one line per dependency (`from → to`, `· major` on a
major bump; packages with a shared prefix and the same change share a line) and one line
per maintenance subject. PR runs are compressed as `#535–#541`; more than five references
become a count and the first and last PR, such as `(15 PRs, #772–#799)`.
Commit hashes are not shown; the heading's compare link covers the range. Types that
the config hides (`docs`, `ci`, `test`, ...) are recovered from `git log` between the
previous tag and the release PR's base commit and listed only inside the collapsed list,
so they still never cause a release. A PR without the new sections renders from its
title. A version marker makes reruns a no-op; lines the helper cannot attribute are kept
visible.

Release Please updates an open release PR only when its own generated body differs from
the PR's current body. A rendered body always differs, so every push to the default
branch, including one with only hidden-type commits, regenerates and re-renders the PR
through the normal path. The fallback matters only for an open release PR that was never
rendered (for example right after a toolkit update): when Release Please reports no PR,
the workflow renders the single open `autorelease: pending` PR anyway. In a
multi-component repository Release Please flattens the nested list in each component's
GitHub release; the manifest job resets every new component release to its `CHANGELOG.md`
section afterwards.

Renovate dependency updates use `deps(<manager>)` Conventional Commits (for example `deps(github-actions)`); Release Please
lists them in the `📦 Dependencies` section and the helper groups them per package inside
the collapsed list.
The reusable Release Please workflow fails before releasing when a config-backed
package has no visible `deps` changelog section: without one, Release Please
falls back to its default sections and silently drops every `deps(...)` commit.
The check reads only the config, so a release without dependency updates passes.
The config is project-owned, so the fleet update repairs it after `copier update`:
a package without sections gets the template sections, and a missing or hidden
`deps` section is added or unhidden.

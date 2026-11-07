# Contributing

Use Conventional Commits, create one focused branch/PR, and keep workflows atomic. Before opening a PR run:

```console
python scripts/validate.py --static
python scripts/validate_python_fixture.py
node scripts/validate_node_fixture.mjs
bash scripts/validate_java_fixture.sh
# Or run the complete canonical suite:
python scripts/validate.py
actionlint .github/workflows/*.yml examples/*.yml
git diff --check
```

## Branch and PR title rules

Never commit to `main`; open a PR from a dedicated branch named `<type>/<short-kebab-slug>`,
for example `feat/gradle-problems-summary`, `fix/allure-component-defaults` or
`chore/bump-pinned-actions`. Bots use their own prefixes: `renovate/`, `release-please--`,
`automation/` and `ci-kit/`.

PR titles must match `type(scope): summary`, with a required scope and `!` before the colon
for a breaking change (plus a `BREAKING CHANGE:` footer). Allowed types: `feat`, `fix`,
`perf`, `refactor`, `docs`, `test`, `ci`, `chore`, `build`, `style`, `revert`, and `deps`
(dependency updates). Branch types use the same list. Repositories with a PR-title check
enforce this format; the title becomes the changelog entry either way.

New third-party Actions must be pinned to a full commit SHA and covered by Renovate. Add a composite action only with evidence of repeated step-level logic. Do not create or move release tags from feature work.

## Pull request titles and release notes

Release notes are rendered from merged pull requests by `scripts/enrich_release_notes.py`
(the same helper every consumer gets through the `release` module). Only the title, the
labels and the `## Release note`, `## Usage example` and `## Migration` sections are
published; everything else in the body is for reviewers.

- `feat`, `fix`, `perf` and `revert` are shown to users; every other type is listed under a
  collapsed "Internal changes". The label `release-notes: internal` moves a `feat` or `fix`
  that only maintainers notice there too.
- The title is the headline: what the user notices, at most 72 characters, without file or
  function names.
- `## Release note`: one paragraph, at most 300 characters, for users. Required for `feat`,
  `perf` and breaking PRs, encouraged for `fix`. The label `release-notes: title-only`
  waives it when the title says everything.
- A `!` title needs `## Migration` with numbered upgrade steps (`## Breaking change` is
  accepted as an alias).
- Fix a published note by editing the merged PR; the release PR is regenerated.

The core module's `PR contract` check (`.github/workflows/pr-contract.yml`) enforces these
rules from the PR event alone. Bots (`renovate/`, `release-please--`, `ci-kit/`,
`automation/` branches) are exempt.

The canonical validator automatically runs every root `tests/test_*.py` suite in
filename order through `scripts/run_test_suites.py`. Add new suites there without
editing a list in the validator. Intentional exclusions belong in the runner's
`EXCLUDED_SUITES` with a reason. Child suites inherit
`PROJECT_TOOLKIT_NESTED_VALIDATION=1` so validators launched by tests do not recurse.
Each file is loaded through unittest in an isolated process, including files without
a `unittest.main()` entrypoint. You can run the runner directly for focused
verification. Missing suites and files without test cases fail closed.

CI runs the suites as four parallel `unit-tests` matrix shards, each invoked as
`python scripts/run_test_suites.py --shard I/4`; `--list-shards 4` prints the layout.
Shards are balanced by the `SUITE_WEIGHTS` table in the runner (approximate seconds
per suite); update it when suite durations shift noticeably. New suites need no edit
and land in exactly one shard at the default weight. `validate.py` fails when the
shards do not cover every suite once or when a weight names a missing suite, and
`python scripts/validate.py --skip-suites` skips the runner for jobs that shard it.

CodeQL scans toolkit scripts, tests, shipped actions, and executable workflows.
The Actions analysis also renders all `tests/scenarios/modules/*.yml` from the current
working tree before initialization. Generated YAML is temporarily placed under
`.github/workflows/codeql-generated-*` for extraction; raw Jinja files are not
workflow evidence. This rendering does not execute template tasks or extensions.
Run it only in a disposable checkout: generated files are for analysis, not commits.

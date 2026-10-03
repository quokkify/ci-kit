# Usage

Examples pin an exact immutable project-toolkit release. All toolkit references in this page are managed by Renovate and move together when a new release is published.

Copy a small caller workflow from [`examples/`](../examples/) and replace commands/paths. Production callers use an exact released tag:

```yaml
jobs:
  backend:
    uses: quokkify/project-toolkit/.github/workflows/python-ci.yml@v2.24.0
    with:
      working-directory: backend
      install-command: python -m pip install -e .[test]
      lint-command: ruff check .
      test-command: pytest
```

A polyglot repository calls Python, Node.js, and Java workflows as separate jobs. See [`examples/polyglot-ci.yml`](../examples/polyglot-ci.yml) for path filtering and an integration job that treats unrelated skipped checks as acceptable but requires every selected component check to succeed.

## Reusable composite actions

Toolkit consumers can call composite actions directly for repeated setup and compose orchestration:

- `actions/setup-python/action.yml`
- `actions/setup-node/action.yml`
- `actions/setup-java-gradle/action.yml`
- `actions/compose-up/action.yml`
- `actions/deploy-gh-pages-subdir/action.yml`
- `actions/junit-step-summary/action.yml`
- `actions/allure-report/action.yml`

Use examples in [`examples/setup-actions.yml`](../examples/setup-actions.yml). The action inputs are versioned and can be called with their defaults where repository conventions already match the toolkit assumptions. Custom install commands are trusted caller configuration and must never interpolate untrusted pull-request data.

The Python and Node actions install dependencies by default; set `install-dependencies: "false"` when only the language runtime and cache are needed. The Java/Gradle action uses setup-java v6 for dependency and JDK caching, includes Gradle properties in cache inputs, and prepares a workspace-relative wrapper command; set `cache-dependencies: "false"` or `cache-jdk: "false"` to disable either cache independently. Callers run their own Gradle dependency/build command. `compose-up` is Linux-only, delegates exactly once to the immutable standalone health action, accepts newline-separated `compose-files` and `wait-urls`, normalizes `profiles`, and validates and prefixes `working-directory`-relative Compose and lifecycle-hook paths. `before-compose-hook` is sourced before startup so it can export Compose interpolation values; `after-health-hook` runs project-owned readiness checks after container health succeeds. With explicit `services`, the wrapper passes a normalized union with `completed-services`; without explicit services, standalone default coverage remains active and includes successful one-shot containers. `additional-compose-args` is forwarded to the standalone startup command and defaults to empty; callers can explicitly pass options such as `--quiet-pull`. `build` appends `--build`. `wait-for-health: "false"` and `show-logs-on-failure: "false"` fail closed; migrate to the standalone-owned defaults instead. It requires no write permissions by itself. Callers remain responsible for granting only the permissions required by their surrounding job and for running normal `docker compose down` cleanup when needed. `down-on-timeout: "true"` enables only failure cleanup scoped to the validated Compose files, without `-v`.

`deploy-gh-pages-subdir` in toolkit `v2.6.0` delegates exactly once to the immutable standalone [`quokkify/gh-pages-subdir-action`](https://github.com/quokkify/gh-pages-subdir-action) `v0.1.1` release (commit `816d85aa756f480457befb42168633cb6ccf09c7`) with unchanged inputs; this keeps existing input consumers stable. The standalone action validates workspace and destination paths, preserves sibling directories, keeps the token out of the remote URL and command arguments, and uses `--force-with-lease` with one concurrent-update retry. The caller must grant `contents: write`; normal branch protection and deployment policy remain the caller's responsibility. Use it for isolated paths such as `allure/pr-42`, not for replacing an entire Pages branch. New consumers can call the standalone root action directly.

`allure-report` delegates exactly once to the immutable standalone [`quokkify/allure-report-action`](https://github.com/quokkify/allure-report-action) `v0.5.2` release (commit `00a2788fd72dce6727a3232104f770f659aeaccb`) while preserving the caller input contract. The wrapper defaults `source-artifacts-directory` to `auto`: complete colocated `ci-env-fragment.properties` provenance activates atomic per-job result merging and module attribution, while repositories without that contract keep their already-merged results. The standalone implementation creates module-scoped environments from the configurable `module-environment-label`, scopes each environment's variables to its authoritative provenance, writes badges and the PR summary, optionally exports the existing epic-based test-pyramid files, optionally publishes the HTML report, and creates or updates one marked PR comment when `pr-number` is set. Tests without `epic` metadata remain in overall totals: Playwright is classified as `E2E`, while otherwise unclassified results appear under `No epic assigned`. Public and private repositories use the same explicit `github-token` input and repository context; callers grant `pull-requests: write`, plus `contents: write` only when Pages publishing is enabled. Pages publishing is off by default so private repositories can still generate and comment the summary without a public Pages dependency.

Call `junit-step-summary` with `if: always()` after a test step. It accepts one or more workspace-relative JUnit XML paths or `*`, `?`, and `**` globs (`**` must be a complete path segment), aggregates standard `testsuite`/`testsuites` documents, appends a Markdown table to `GITHUB_STEP_SUMMARY`, and exposes counts and duration as action outputs. It warns when no files match unless `fail-on-missing: "true"`. Literal prefixes prevent unrelated repository trees from being scanned. Directory depth, scanned entries, matched files, per-file bytes, and aggregate bytes are bounded; symlinks, traversal, DTD/entity declarations, malformed numbers, and unsafe file replacements are rejected. Python 3.9 or newer must be available on a POSIX runner. Deprecated CSP `cwd`/`variant` inputs are supported only as a migration bridge and cannot be mixed with the generic inputs.

## Docker secrets

Docker push is off by default. When `push: true`, pass both declared secrets explicitly:

```yaml
secrets:
  registry-username: ${{ github.actor }}
  registry-password: ${{ secrets.GITHUB_TOKEN }}
```

The caller must grant `packages: write` when its registry requires it. Never pass secrets via `build-args`; use BuildKit secret mounts in a project-specific workflow if secret material is genuinely needed during a build.

## Copier

```console
copier copy https://github.com/quokkify/project-toolkit.git my-project --vcs-ref v2.24.0 --trust
cd my-project
copier update --trust
```

Every generated repository receives the shared baseline workflows:

- `validate.yml` validates the committed Copier/configuration contract and runs the selected Python, Node.js, Java, and Docker jobs;
- `codeql.yml` analyzes GitHub Actions plus the languages inferred from `components` when `codeql` is enabled;
- `gitleaks.yml` scans both Git history and the current tree with a checksum-verified binary;
- `release.yml` uses the shared Release Please workflow when `release_please` is enabled;
- `allure-report.yml` securely consumes source-run `allure-results-*` artifacts and comments an Allure 3 report when `allure_report` is enabled;
- `.github/renovate.json` extends the selected organization presets when `renovate` is enabled.

The `Auto-update Copier-managed repositories` workflow runs in read-only mode on its daily audit schedule and can also be started by a CODEOWNER from the Actions UI or with `gh workflow run copier-fleet-auto-update.yml --repo quokkify/project-toolkit -f dry-run=true`; the API call is authenticated by the caller's current `gh` session. A separate audit workflow used to hold this role and was removed: the same script, arguments, and reports are reached through this workflow's `dry-run` mode, so one file now covers both fronts. Full-fleet runs share one concurrency lock, while explicit `repository` dispatches use repository-specific locks; targeted updates can therefore run in parallel without GitHub silently replacing older pending runs. Because the hosted job intentionally uses only the repository-scoped `GITHUB_TOKEN`, its supported scheduled fleet is explicitly limited to public Quokkify repositories; private consumers require an explicit CODEOWNER-run local audit and are never silently claimed as covered by the badge. The workflow discovers non-archived, non-fork public organization repositories containing `.copier-answers.yml`, accepts only answers whose `_src_path` resolves to `quokkify/project-toolkit`, and performs a `copier update` preview against the latest released template tag. The organization profile repository `quokkify/.github` and template source repository `quokkify/project-toolkit` are explicitly excluded before Copier metadata is inspected because neither is a generated consumer project. The audit fails with a distinct drift status when any supported repository would change, making the README badge red; a green badge means the most recent audit completed and found no drift in that public fleet. An explicitly requested repository that cannot be accessed fails the run. Console output includes the recorded template version, components (from Copier answers, with workflow-based language inference for older answers), baseline coverage, and Docker, CodeQL, Allure, Release Please, and Renovate states for each managed repository. Custom Allure and Release Please paths are reported as `custom`, not `missing`. Drift status and configuration-gap counts are reported independently in console, Markdown, and JSON. The Actions job summary renders the same inventory as a Markdown matrix with drift and missing-output details, and the workflow uploads a versioned `copier-fleet-audit.json` artifact for automation. JSON schema version 3 moves `codeql` out of the baseline set and reports it as a feature state; version 2 added `allure_report`. The baseline is therefore `validate.yml` and `gitleaks.yml`, and a repository that opted out of CodeQL no longer counts as missing a baseline file. Conditional outputs are classified as enabled, disabled, missing, custom, or unknown rather than treating absent legacy answers as false.

### Applying updates

Template updates reach a consumer through one of two paths, chosen by the consumer's visibility. Public consumers are served by scheduled automation in this repository; private consumers are not served by it at all and are updated by a maintainer instead.

**Public repositories, automatic.** The `Auto-update Copier-managed repositories` workflow runs immediately after Release Please tags a new toolkit version, weekly on Monday as a safety net, and on manual dispatch. The release path is a `workflow_call` from the `Release` workflow rather than a `release` or `push: tags` trigger, because Release Please tags with the repository-scoped `GITHUB_TOKEN` and events raised by that token never start a new workflow run; the follow-up job is gated on `releases-created` so an ordinary push to `main` does not run the write-capable updater, and it passes the tag Release Please just created as `template-ref`. It passes `--public-only --write` and creates or refreshes `chore(template): update shared project template` pull requests on the deterministic `automation/copier-template-update` branch. `--public-only` is an invariant of this front rather than a discovery detail: it filters organization discovery and additionally rejects an explicitly dispatched `repository` input that is not public, failing the run with an explicit message instead of silently updating it. Write mode requires the `FLEET_UPDATE_TOKEN` secret and fails fast when it is absent; dry-run mode never writes and uses the repository-scoped Actions token. The credential behind `FLEET_UPDATE_TOKEN` must be installed on public Quokkify repositories only, so the absence of private access is structural rather than a flag in a script. It needs `Contents`, `Pull requests`, and `Workflows` read and write, plus the mandatory `Metadata` read; `Workflows` is required because the template consists of workflow files, and without it the push is rejected. Reruns are idempotent per consumer: the `automation/copier-template-update` branch is rebuilt from the consumer's default branch and force-pushed with `--force-with-lease`, so it always carries exactly one commit for the current template version, and an already-open pull request for that branch is reused rather than closed and recreated. Anything committed onto that branch by hand is therefore discarded by the next run.

**Any repository, from a maintainer's machine.** GitHub does not transfer the dispatching user's credential into a hosted workflow, so a CODEOWNER's short-lived local session remains the general-purpose path and is the only supported way to update a private consumer from here:

```console
GH_TOKEN="$(gh auth token)" python scripts/update_copier_fleet.py --org quokkify --write
gh workflow run copier-fleet-auto-update.yml --repo quokkify/project-toolkit -f dry-run=true
```

The first command creates or refreshes pull requests using the caller's token without copying it into repository or organization secrets. The second command starts the read-only audit and refreshes the badge. Use `--repo owner/repository` to target one consumer and `--template-ref REF` to test an explicit released template tag such as `v2.21.5`; arbitrary branches and commit SHAs are rejected by the generated self-service workflow. Omit `--public-only` locally when a private consumer is the target.

Across both paths, existing project changes are preserved by Copier's update algorithm; `.rej` conflicts fail that repository loudly instead of opening a partial pull request. The deterministic `automation/copier-template-update` branch is automation-owned and may be force-updated with an exact lease, so maintainers should not add manual commits to it. Normal updates follow the latest release tag rather than unreleased `main`.

### Self-service template update

Generated projects ship an `Update project template` workflow with a single `workflow_dispatch` trigger and no schedule. It runs `copier update` against the latest released template tag (or an explicit `template-ref` input) and pushes the deterministic `automation/copier-template-update` branch using the project's own `GITHUB_TOKEN`, so this path needs no cross-repository credential anywhere. Before Copier runs, the supplied ref must match an exact published, non-draft, non-prerelease GitHub Release tag; this prevents a branch, SHA, or unreleased tag from selecting code while the job has `contents: write`.

It deliberately stops at pushing the branch and prints a compare link instead of opening the pull request. A pull request opened with `GITHUB_TOKEN` does not trigger `pull_request` workflows, so `Validate`, `Gitleaks`, and `CodeQL` would never run on it, and a repository with a required-status-check ruleset could never merge it. Opening it by hand costs one click and keeps every check honest.

`.rej` conflicts fail the run and nothing is pushed, so the branch never holds a half-applied template. A run that finds no changes exits cleanly without pushing.

This is the supported in-repository path for private consumers, which the public fleet automation does not serve at all, and an escape hatch for public ones that do not want to wait for the weekly schedule. Both fronts write the same branch, so on a public repository a self-service run can collide with the scheduled one; the push uses an exact lease and fails rather than discarding an unseen commit. Treat it on a public repository as the exceptional path.

### Ownership of pinned tool versions

Versions inside template-owned workflow files belong to this repository, not to Renovate in the generated project. Renovate here bumps the pinned `uses:` digests and `ACTIONLINT_VERSION` inside `templates/project/template`, and those bumps reach consumers through `copier update`.

The rule that stops a consumer's Renovate from editing those same files lives in the shared preset, `quokkify/renovate-presets//presets/base`, and reaches projects through `extends`. It disables Renovate for exactly the workflow paths this template owns. Without it the same pin is proposed twice — once here and once in every consumer — and a later `copier update` lands on top of a locally moved pin, producing a `.rej` conflict in a file the project never edited.

The generated `.github/renovate.json` deliberately does **not** carry that rule, and is written only when a project is first generated. Projects extend that file with their own custom managers and package rules, so an update that rewrote it would destroy their configuration; `_skip_if_exists` keeps it owned by the project.

The split is by file, not by manager: Renovate in the generated project keeps full ownership of everything else, including project-owned workflows such as `ci.yml` and all language and runtime dependencies.

`scripts/validate.py` fails when the shared preset and the set of template workflows disagree, so a new template workflow cannot ship without extending that rule. A project pointing at a different preset repository through `renovate_config_repository` does not receive the rule and must carry its own equivalent.

### Optional Q4J test automation

Set `q4j_tests: true` to add a standalone Java 21 / Gradle test project under
`test-automation/`, with Q4J configuration and a minimal TestNG example. Set
`q4j_tests_path` to choose another root folder. See
[Q4J test automation](q4j-tests.md) for the generated layout, extension points, and
the build, compilation, and Checkstyle checks used by the toolkit.

### CodeQL languages

The generated CodeQL workflow scans `actions` plus the languages implied by `components`. A repository that builds real source without describing it through `components` therefore gets `actions` only, and its application code is silently not scanned. Set `codeql_languages` to an explicit list to say what CodeQL should analyze:

```yaml
codeql_languages: [actions, java-kotlin]
```

Supported names are `actions`, `c-cpp`, `csharp`, `go`, `java-kotlin`, `javascript-typescript`, `python`, `ruby`, `rust`, and `swift`; unknown names, duplicates, and non-list values are rejected. Leave it empty to keep deriving the list from `components`.

The template accepts an optional YAML list of `python`, `node`, and `java` components plus Docker, CodeQL, Allure, Release Please, and Renovate booleans. Every component requires a stable `id` (a GitHub Actions job identifier) and non-empty display `name`; IDs must be unique and must not use generated IDs such as `docker`, `template-contract`, `update`, `scan`, `analyze`, `resolve`, `generate`, `comment`, or `pages`. Leave `components` empty when the repository owns custom language-specific validation; the shared template-contract validation and Gitleaks still remain present, and CodeQL Actions analysis does too unless it is switched off. Generated projects use Renovate presets from `quokkify/renovate-presets` by default. Set `renovate_config_repository` to another GitHub `owner/repo` slug when a project should consume a different shared preset repository; it names the preset repository, not the generated consumer repository. Set `renovate_presets` to a non-empty YAML list of selected preset names, preserving the desired extends order. Supported names map to paths as follows: `default` -> `presets/base`, `python` -> `presets/python/default`, `javascript` -> `presets/npm/default`, `java` -> `presets/gradle/default`, `docker` -> `presets/docker/default`, and `github-actions` -> `presets/github-actions/default`. New projects infer `default`, `github-actions`, and the language/Docker presets selected by `components` and `docker`. The generated extends entries follow that preset repository's default branch intentionally; pin them manually later if a stricter stability policy requires it. Set `codeql: false` when a repository cannot run code scanning: GitHub Advanced Security is required for private repositories, and without it the generated job fails on every pull request. Repositories generated before this answer existed report CodeQL as `unknown` rather than `missing`, so an audit does not flag them. Commit `.copier-answers.yml`; it is the update contract. Review generated pull requests before merging them.

This is a breaking component-schema migration. Existing consumers must add explicit `id` and `name` values to every entry while preserving its existing `type` and `path`, for example:

```yaml
components:
  - type: java
    path: worker
    id: worker-java
    name: Worker Java
```

The fleet updater preserves legacy `type` and `path` values and passes stable identities to Copier as migration data, so the cloned checkout remains pristine and the generated pull request contains the named jobs. Review that pull request and keep the resulting identity fields in the consumer answers file; do not rely on the old type/index job IDs.

## Copier Allure reporting

For consumers that own their test workflow, call the trusted publisher at job level from a thin `workflow_run` trigger. Use the report core for a caller that does not publish Pages. Pass the exact source workflow name/path, a dedicated artifact prefix, and caller-owned Allure config. Replace `RELEASE_TAG` with a toolkit release that contains the core/Pages split before migrating an existing caller:

```yaml
jobs:
  allure:
    uses: quokkify/project-toolkit/.github/workflows/allure-publisher-core.yml@RELEASE_TAG
    permissions:
      actions: read
      contents: read
      pull-requests: write
    with:
      source-workflow: Run tests
      source-workflow-path: .github/workflows/test.yml
      artifact-prefix: allure-results-
      minimum-artifacts: 1
      config-file: tools/allure/allurerc.mjs
      categories-file: tools/allure/categories.json
      history-path: allure-history/history.jsonl
      publish-pages: false
```

The report core keeps resolve, bounded download, read-only generation, and PR-comment write in separate jobs. Optional Pages publication is a separate `allure-pages.yml` reusable workflow with `contents: write`, called only by Pages-enabled generated workflows. A caller with Pages disabled requests no contents-write permission anywhere in its called graph. Zero matching artifacts warn and skip when `minimum-artifacts: 0`; a partial/expired/oversized contract fails closed. It resolves exactly one current open PR, suppresses stale source runs, and never publishes Pages for fork or Dependabot PRs. The workflow pins every action by full commit SHA and delegates report generation to the standalone `quokkify/allure-report-action`; callers remain responsible for project-specific config, categories, URLs, and the source artifact upload.

The caller permissions above are the maximum needed by the report core. Each child job uses its own scoped `github.token`: the generator receives only `actions: read` and `contents: read`, and the commenter receives `pull-requests: write`. New generated callers pass no token secret. The optional legacy `github-token` secret remains supported for existing callers, but a custom credential can retain broader permissions in every job; job permissions cannot attenuate that credential.

The original `allure-publisher.yml` API remains available as a wrapper around the same-commit core and Pages workflows, with its existing input names and optional token override. Its optional Pages path still requires a maximum `contents: write` grant because GitHub checks nested workflow permissions statically. Prefer the core for custom report-only callers. Keep legacy `pages-url` as a full report URL and `pages-destination-directory` as the HTML-root destination: `managed-pages-layout` defaults to false, preserving that layout. Generated callers opt into the managed layout and derive the `/allure/pr-N/allure-report/` link and destination from the validated PR number.

Set `allure_report: true` to generate `.github/allure/allurerc.mjs`, `.github/allure/safe_extract.py`, and `.github/workflows/allure-report.yml`. With generated `components`, each component job uploads one required, uniquely named `allure-results-<id>` artifact, where `<id>` is the component's stable job identifier. The default source is `<component path>/allure-results`; set an optional safe relative `allure_results_path` on a component for layouts such as Gradle's `build/allure-results` or Maven's `target/allure-results`. Configure the project's test adapter to write there—for example `pytest --alluredir=allure-results`, an Allure-enabled Java test task, or the selected Node test framework's Allure reporter. Missing required results fail the component job. The template deliberately does not modify dependency manifests or replace project-specific test commands.

Repositories with `components: []` can still use the managed report workflow while retaining project-owned test orchestration. Configure the exact `allure_external_workflow_name` and `.github/workflows/...` path, a dedicated artifact-name prefix, and a minimum/maximum artifact count from 1 through 50. The source workflow must upload only Allure result bundles under that prefix; keep broader JUnit, logs, and build reports in separately named artifacts. Every selected bundle is subject to the same aggregate compressed/expanded limits and collision checks. Use `allure_categories_file` when the project owns an optional categories JSON file; the generated workflow verifies that the configured source workflow and categories file exist. External artifacts are many separate per-job bundles rather than one merged result set, so the extractor materializes them into `.allure-input/source-artifacts` and the report action receives that path as `source-artifacts-directory` alongside `results-directory: .allure-input/results`. The two directories must stay distinct: with both pointed at `results-directory`, nothing flattens the per-job trees and Allure reads zero tests from a directory that does hold results. Generated `components` keep the single already-merged directory, because each component uploads exactly one bundle.

The follow-up resolves the current open PR through GitHub's API, requires its repository and head SHA to match the exact source run, validates either the versioned component artifact naming contract or the configured external prefix/count contract, rejects expired artifacts, and suppresses stale runs and superseded attempts of the same run. Component artifact identities come from the source run, allowing reports during a component-ID migration without consulting the current template component inventory. Artifact download, collision-safe bounded merging, and Allure generation happen in a read-only job. Compressed bytes are streamed into `runner.temp`; central-directory, path, type, count, per-file, and aggregate expanded-size limits are verified before any archive member is extracted. Extraction also stays under `runner.temp`; only fully validated regular files are then copied into a symlink-checked isolated workspace directory. The Pages job applies the same bounded pre-extraction gate to the generated report artifact before publication. A separate narrowly privileged job forwards the compact comment generated by the trusted report builder, requires exactly one final marker, and updates only the trusted bot comment. It rechecks the current head and newest source run before posting; input artifact files are never executed as comment-generation scripts. Fork and Dependabot PRs receive that safe comment but never Pages publication.

Set `allure_publish_pages: true` only when the repository intentionally publishes reports, and provide the real `allure_pages_url` served by the repository. Only the separate Pages call receives `contents: write`; it independently rechecks the source workflow identity, current same-repository PR, newest source run and attempt, and the exact generated report artifact. It repeats freshness checks after bounded extraction immediately before publishing below `allure/pr-N` on `gh-pages`. Configure GitHub Pages to serve that branch/root and ensure repository Actions policy permits write tokens. Pages remains disabled by default.

Allure decides which tests are new, flaky, regressed, or fixed only from the history it reads through its `historyPath`; with no history, Allure lists every test as new in each report and PR summary. Hosted runners start empty, so the report core carries that file between runs when a caller sets `history-path` to a workspace-relative `<directory>/<file>.jsonl` equal to the config's `historyPath`. Allure resolves `historyPath` against the working directory, which is the workspace root, not the config file's directory. Before generation, the read-only generator selects the newest artifact named `allure-history-<source workflow id>-pr-<N>` produced by a completed, successful earlier run of the same calling workflow, passes it through the bounded extractor outside the workspace, requires exactly one file containing JSON objects, and copies only that file to `history-path`. It never deletes checked-out files, and it fails rather than overwrite an existing file at that path. Allure then reads, updates, and trims the history itself, and the updated file is uploaded as a new artifact with the same name, retained for 30 days; no run replaces an earlier artifact. Missing or expired history starts a new history with a notice. API errors, an oversized artifact, unexpected files, or a corrupted line fail the job. Cancelled and failed runs are never selected, and neither are runs skipped as stale before generation. The per-PR stream never includes another pull request, a run ID, or a head SHA. The first report of a pull request therefore lists every test as new, and later reports compare with that pull request's previous accepted report, not with the default branch. Each source workflow keeps its own history stream; enable history for at most one report-core call per source workflow in a calling workflow. When Allure writes no file, for example with `appendHistory: false` and no earlier history, nothing is persisted and the job warns. When restored history comes back unchanged, it is persisted again and the job warns as well. Generated repositories set `historyPath: "./allure-history/history.jsonl"` and `historyLimit: 20` in `.github/allure/allurerc.mjs`, and their caller passes the same path; edit both together. Pages publication is unchanged, and history is not published to Pages. Leave `history-path` empty when the config uses an Allure Service or another history store you already operate.

Report runs are serialized per triggering source workflow, head repository, and branch: a newer commit cancels the older in-progress report of the same source, so the single marked PR comment is updated for every new head. Report runs triggered by different source workflows, and report-core calls for different sources in one caller, never cancel each other. A caller that listens to several source workflows therefore cannot lose the report of the workflow that owns the results.

Older `.copier-answers.yml` files have no Allure answer. Fleet audit reports them as `unknown`, not `disabled`; once configured, the workflow, config, and bounded extractor must all exist for the feature to be `enabled`. Both source modes use thin generated callers. These core/Pages endpoints must exist in the published toolkit release selected by `toolkit_version`. Fresh copies of a released template derive that default from the exact selected tag; non-release local sources retain the documented fallback. Updates preserve existing answers; for Allure-enabled projects Copier rejects an incompatible retained toolkit pin before writing files and gives an actionable retry command. Adopting the new endpoints manually requires `copier update --trust --defaults --vcs-ref <new-release> --data toolkit_version=<new-release>`. Fleet updates already set that value to the target release. Do not point a new thin caller at an earlier tag that lacks these endpoints. Copier owns their source bindings, permissions, triggers, config, and local extractor copy; the consumer owns its source test workflow and dedicated uploads. The reusable downloader and Pages job obtain the bounded extractor from `job.workflow_repository` at `job.workflow_sha`, the exact called toolkit workflow commit, and check out caller report configuration at `github.sha`. A caller therefore does not need a local extractor to use the reusable API, and no PR checkout supplies executable extraction code.

## Release Please

Use [`examples/release-single.yml`](../examples/release-single.yml) for one product version or [`examples/release-components.yml`](../examples/release-components.yml) with the example [component config](../examples/release-please-components-config.json) and [manifest](../examples/release-please-components-manifest.json) for independent versions. Both rely on Conventional Commits. A push to `main` runs Release Please and creates or updates a release PR for user-facing `feat`/`fix` commits; merging that release PR creates the tag and GitHub Release. Chore-only commits intentionally do not create a release.

## Renovate

New Copier-generated projects extend selected presets from `quokkify/renovate-presets` by default. The repository slug comes from the `renovate_config_repository` Copier answer, so project teams can point new projects at their own shared Renovate preset repository while keeping the selected preset paths. The `renovate_presets` answer uses user-facing names: `default` maps to `//presets/base`, `python` to `//presets/python/default`, `javascript` to `//presets/npm/default`, `java` to `//presets/gradle/default`, `docker` to `//presets/docker/default`, and `github-actions` to `//presets/github-actions/default`.

The bundled `github>quokkify/project-toolkit//renovate/default.json` preset remains available for toolkit-specific workflow reference updates. New generated projects intentionally follow the shared preset repository's default branch unless the generated `.github/renovate.json` is manually pinned to a tag or commit later.

The repository also manages versions that are easy to miss with Renovate's built-in managers:

- `_min_copier_version` in the root `copier.yml` uses the PyPI `copier` datasource.
- Every `copier==`/`PyYAML==` pin under `.github/workflows/` and in the project template's
  workflow templates uses the same PyPI datasource, so a Copier bump lands in all of them at
  once. `scripts/validate.py` rejects any fleet pin below `_min_copier_version`, so a partial
  bump would block its own update PR; `tests/test_composite_actions.py` guards the coverage.
- Toolkit release tags in Markdown and example YAML use the GitHub tags datasource.
- The documented Compose health action release follows the same GitHub tag as the pinned action implementation.

This keeps the version shown to readers consistent with the version executed by CI. Review the generated PR as usual; Renovate never automerges these changes.


## Action pins and project updates

Executable examples use `uses: owner/repo/path@<full commit SHA> # vX.Y.Z`. The SHA must be the commit of the release in the comment; a feature-branch commit must not be labeled as an already published version. Renovate's native GitHub Actions manager updates the digest and release comment together, including files in `examples/`. Behavior tests check the action contract and pin format without hard-coding a particular dependency release.

Existing consumers do not need a bulk migration: exact toolkit release tags remain supported, and generated validation already accepts full SHA pins with a matching release comment. A same-release digest pin does not require changing project commands or Copier answers. For a toolkit version upgrade, use the Copier/fleet update so `_commit`, `toolkit_version`, generated workflows, and project-owned workflow references move together. The fleet updater already resolves the release commit and updates both the SHA and its comment. A separate version bump that leaves Copier answers behind is still rejected intentionally; pinning alone cannot repair an incomplete template upgrade.

The new `gradle-project-command` output is optional. Released examples use the existing `gradle-command` output until their pinned release exposes the new API. Existing commands and the legacy output continue to work; adopt the new output when updating a project that needs explicit nested-project selection.

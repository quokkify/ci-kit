# Human guide: create, update, and roll out projects

This is the short, practical guide. You do not need to understand the toolkit internals first.

> **Important:** project-toolkit is a Copier template, not a GitHub “template repository.” Copier creates the small project-owned files, remembers the answers in `.copier-answers.yml`, and can update those files later.

## Before you start

Install these tools once:

- [GitHub CLI](https://cli.github.com/) as `gh`;
- [Copier](https://copier.readthedocs.io/) 9.17.0 or newer;
- Git.

Check access and capture the latest released toolkit tag:

```bash
gh auth status
copier --version
export TOOLKIT_REF="$(gh release view --repo quokkify/project-toolkit --json tagName --jq .tagName)"
echo "$TOOLKIT_REF"
```

Always use a released tag such as `v2.6.0`. Do not generate production files from `main` or `HEAD`.

## 1. Create a new project from the template

Run:

```bash
copier copy \
  https://github.com/quokkify/project-toolkit.git \
  my-project \
  --vcs-ref "$TOOLKIT_REF" \
  --data "toolkit_version=$TOOLKIT_REF" \
  --trust
```

Copier asks a few questions:

- project name;
- Python, Node.js, or Java components and their directories;
- whether Docker is used;
- whether Release Please is needed;
- whether Renovate is needed.

Then create the GitHub repository:

```bash
cd my-project
git init -b main
git add -A
git commit -m "chore: initialize project"
gh repo create quokkify/my-project --private --source=. --push
```

Commit `.copier-answers.yml`. It is not a secret. It is the receipt Copier needs for future updates.

Before merging generated CI, open `.github/workflows/ci.yml` and confirm that component directories match the real project.

## 2. Update a project created by Copier

The project must contain `.copier-answers.yml` and have a clean Git working tree.

```bash
cd my-project
git switch main
git pull --ff-only
git switch -c "chore/update-project-toolkit-${TOOLKIT_REF}"

copier update \
  --vcs-ref "$TOOLKIT_REF" \
  --data "toolkit_version=$TOOLKIT_REF" \
  --trust

git diff
git diff --check
git add -A
git commit -m "chore: update project-toolkit to ${TOOLKIT_REF}"
git push -u origin HEAD
gh pr create --fill
```

Review the PR before merging it. In particular, check:

- `.github/workflows/ci.yml`;
- `.github/workflows/release.yml`, if enabled;
- `.github/renovate.json`;
- `.copier-answers.yml`;
- any Copier conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`).

Copier updates generated files. Renovate has a different job: it updates released workflow/action references. Keep both enabled; they are complementary.

## 3. Add a new template capability

There is intentionally one configurable project template rather than many copied templates. Add a new option to it when projects share the same basic structure.

1. Create a branch in `project-toolkit`.
2. Add the new question or option to [`copier.yml`](../copier.yml).
3. Add or change a `.jinja` file under [`templates/project/template/`](../templates/project/template/).
4. Add or update a representative answer file under [`tests/scenarios/`](../tests/scenarios/). If it is a new scenario, register it in `scripts/validate.py` so static validation renders it.
5. Generate a disposable preview.
6. Run validation and open a PR.
7. After merge, wait for a released tag and use that tag in real projects.

Example preview:

```bash
PREVIEW_DIR="$(mktemp -d)"
trap 'rm -rf "$PREVIEW_DIR"' EXIT
copier copy \
  . \
  "$PREVIEW_DIR" \
  --vcs-ref HEAD \
  --trust \
  --data-file tests/scenarios/python.yml

python scripts/validate.py --static
git diff --check
```

`HEAD` is acceptable only for this local preview. Consumers must use a release tag.

Create a separate template repository only when the new project type has a genuinely different lifecycle and almost no shared generated files. Otherwise, another option in this template is easier to test and update.

## 4. Roll out the template across existing organization repositories

The safe organization-wide rollout is **one command that processes the selected repositories and opens reviewable PRs**. It never pushes directly to `main` and never merges automatically. Repositories with no changes or an already-open rollout PR are skipped, and the helper stops on the first error.

### Step 1: prepare one answers file per target repository

Create a private local directory outside any repository:

```bash
mkdir -p "$HOME/project-toolkit-rollout"
```

Create, for example, `$HOME/project-toolkit-rollout/example-service.yml`:

```yaml
project_name: example-service
components:
  - type: python
    path: .
    id: app-python
    name: Application Python
docker: false
release_please: true
renovate: true
renovate_config_repository: quokkify/renovate-presets
renovate_presets:
  - default
  - python
  - github-actions
```

The filename is the repository name: `example-service.yml` targets `quokkify/example-service`.

Add only repositories you really want to change. Different repositories can have different answers. Never put secrets in these files: Copier records the answers in the target repository.

### Step 2: run the rollout helper

Clone `project-toolkit` first (skip the first command if it is already cloned), then test with one answers file:

```bash
gh repo clone quokkify/project-toolkit
cd project-toolkit
git switch main
git pull --ff-only
export TOOLKIT_REF="v2.6.0" # use the exact tag you reviewed above
scripts/rollout_project_toolkit.sh "$HOME/project-toolkit-rollout"
```

After the first PR looks correct, add the remaining answer files and run the same command again. Already-open rollout PRs for the current toolkit release are detected and skipped.

1. clones the repository into a temporary directory;
2. updates it if `.copier-answers.yml` already exists;
3. adopts the template if this is the first Copier rollout;
4. preserves the repository's existing `README.md`;
5. stops if an existing answers file belongs to another Copier template;
6. stops before pushing if Copier leaves conflict markers;
7. opens a separate reviewable PR;
8. never merges the PR.

The first rollout uses `--overwrite` because existing CI or Renovate files may need replacement. That is why every repository with generated changes gets its own PR. Read the diff and let its normal CI/security checks run before merge.

To update the same fleet later, keep the answers directory and run the script again after a new toolkit release. Repositories already containing `.copier-answers.yml` will use `copier update` instead of first-time adoption. Keep `TOOLKIT_REF` fixed to the same reviewed tag for the whole rollout; choose a new tag only when starting a new rollout.

## Which command do I need?

- **Brand-new repository:** `copier copy` from section 1.
- **One managed repository:** `copier update` from section 2.
- **New shared generated behavior:** change the template and release it using section 3.
- **Many existing repositories:** prepare one answers file per repository and run section 4.

For an optional read-only adoption and exact-revision CI snapshot, use
`--include-health`; see [fleet health](fleet-health.md). JSON schema version 4
preserves the version 3 inventory and adds independent health observations.

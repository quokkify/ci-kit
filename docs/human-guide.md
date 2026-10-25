# Human guide: create, update, and roll out projects

This is the short, practical guide. You do not need to understand the toolkit internals first.

> **Important:** ci-kit is a Copier template, not a GitHub “template repository.” Copier creates the small project-owned files, remembers the answers of each module in `.copier-answers.<module>.yml`, and can update those files later.

## Before you start

Install these tools once:

- [GitHub CLI](https://cli.github.com/) as `gh`;
- [Copier](https://copier.readthedocs.io/) 9.18.2 or newer;
- Git.

Check access and capture the latest released toolkit tag:

```bash
gh auth status
copier --version
export TOOLKIT_REF="$(gh release view --repo quokkify/ci-kit --json tagName --jq .tagName)"
echo "$TOOLKIT_REF"
```

Always use a released tag such as `v4.0.1`. Do not generate production files from `main` or `HEAD`.

## 1. Create a new project from the template

A new repository is the `core` module, any of the `security`, `release`, `allure` and `renovate` modules you need, and one language module per stack (see [modules](modules.md)). Run Copier once per module; `module` defaults to `core`:

```bash
for module in core python security release renovate; do
  copier copy \
    https://github.com/quokkify/ci-kit.git \
    my-project \
    --vcs-ref "$TOOLKIT_REF" \
    --data "toolkit_version=$TOOLKIT_REF" \
    -d "module=$module"
done
```

Copier asks the questions of each module, such as the project name (`core`), the component directories (language modules), CodeQL and Gitleaks (`security`), the Release Please mode (`release`), or the Renovate presets (`renovate`).

Then create the GitHub repository:

```bash
cd my-project
git init -b main
git add -A
git commit -m "chore: initialize project"
gh repo create quokkify/my-project --private --source=. --push
```

Commit the module answers files (`.copier-answers.<module>.yml`). They are not secrets. They are the receipts Copier needs for future updates.

Before merging generated CI, open the module declarations in `.ci/modules/` and confirm that component directories match the real project. Then follow the installation check and update steps in the generated `docs/ci-kit.md`.

## 2. Update a project created by Copier

The project must contain its `.copier-answers.<module>.yml` files and have a clean Git working tree. `ci-kit update` updates every installed module, one commit per module; the generated `Update project template` workflow runs the same command.

```bash
cd my-project
git switch main
git pull --ff-only
git switch -c "chore/update-ci-kit-${TOOLKIT_REF}"

python -m pip install "git+https://github.com/quokkify/ci-kit@${TOOLKIT_REF}"
ci-kit update --repo . --to "$TOOLKIT_REF"
git reset --soft main

git diff --cached
git diff --cached --check
git commit -m "chore: update ci-kit to ${TOOLKIT_REF}"
git push -u origin HEAD
gh pr create --fill
```

Review the PR before merging it. In particular, check:

- `.github/workflows/validate.yml`;
- `.github/workflows/release.yml`, if the `release` module is installed;
- `.github/renovate.json`;
- the `.copier-answers.<module>.yml` files;
- any Copier conflict markers (`<<<<<<<`, `=======`, `>>>>>>>`).

Copier updates generated files. Renovate has a different job: it updates released workflow/action references. Keep both enabled; they are complementary.

A repository that still has a legacy `.copier-answers.yml` is refused with [CIK-CORE-030](errors/CIK-CORE-030.md) unless every area is handed off; see [leaving the legacy template](modules.md#leaving-the-legacy-template).

## 3. Add a new template capability

Each module is a Copier template under `modules/<module>/template`; all modules share the questions in the root `copier.yml`.

1. Create a branch in `ci-kit`.
2. Add the new question or option to [`copier.yml`](../copier.yml), gated by `when: "{{ module == '<module>' }}"`.
3. Add or change a `.jinja` file under `modules/<module>/template/`.
4. Add or update a representative scenario under [`tests/scenarios/modules/`](../tests/scenarios/modules/): a mapping from module name to its answers. `scripts/validate.py` renders every scenario.
5. Generate a disposable preview.
6. Run validation and open a PR.
7. After merge, wait for a released tag and use that tag in real projects.

Example preview:

```bash
PREVIEW_DIR="$(mktemp -d)"
trap 'rm -rf "$PREVIEW_DIR"' EXIT
for module in core python; do
  copier copy . "$PREVIEW_DIR" --vcs-ref HEAD --defaults -d "module=$module"
done

python scripts/validate.py --static
git diff --check
```

`HEAD` is acceptable only for this local preview. Consumers must use a release tag.

Create a separate template repository only when the new project type has a genuinely different lifecycle and almost no shared generated files. Otherwise, another option or module in this template is easier to test and update.

## 4. Roll out the template across existing organization repositories

Existing managed repositories are updated by the fleet automation: `scripts/update_copier_fleet.py` runs `ci-kit update` for every public consumer and opens one reviewable pull request per repository, and `scripts/rollout_copier_fleet.py` gates a full rollout on its pilot repositories. See [usage](usage.md#applying-updates). Neither pushes to `main` or merges automatically unless `--auto-merge` is requested.

A repository that has no ci-kit answers yet is adopted by hand with section 1.

## Which command do I need?

- **Brand-new repository:** `copier copy` from section 1.
- **One managed repository:** `ci-kit update` from section 2.
- **New shared generated behavior:** change the template and release it using section 3.
- **Many existing repositories:** the fleet update in section 4.

For an optional read-only adoption and exact-revision CI snapshot, use
`--include-health`; see [fleet health](fleet-health.md). JSON schema version 4
preserves the version 3 inventory and adds independent health observations.

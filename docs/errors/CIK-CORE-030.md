# CIK-CORE-030

Legacy answers still render files.

## Cause

`.copier-answers.yml` exists and its `handed_off` does not list every legacy area (`validate`, `base`, `security`, `release`, `allure`, `renovate`). ci-kit v4.0.0 has no legacy template, so it cannot update the files those areas still own. `ci-kit plan` reports it, and the fleet update and `copier-update.yml` refuse the repository without changing any file.

A `.copier-answers.yml` that hands off every area renders nothing; the fleet update and `copier-update.yml` delete it in one `chore(ci-kit): drop empty legacy answers` commit.

## Fix

ci-kit v4 has no legacy template and no `migrate-legacy`, so the way out depends on `handed_off`:

- **Every area handed off** (no `CIK-CORE-030`): the legacy answers render nothing. The fleet update and `copier-update.yml` drop them for you. By hand, delete the file in its own commit, then update the modules to v4:

  ```bash
  git rm .copier-answers.yml
  git commit -m "chore(ci-kit): drop empty legacy answers"
  python -m pip install "git+https://github.com/quokkify/ci-kit@v4.0.0"
  ci-kit update --repo . --to v4.0.0
  ```

- **Any area left** (this finding): migrate with the ci-kit v3.11.x CLI first, which still ships `migrate-legacy`. Legacy and `core` answers must name the same v3.11.x release (`_commit`); update the repository to it first if they do not:

  ```bash
  python -m pip install "git+https://github.com/quokkify/ci-kit@v3.11.2"
  ci-kit migrate-legacy --repo . --to v3.11.2
  ```

  If it reports `CIK-MIGRATE-003` ([v3.11.2 runbook](https://github.com/quokkify/ci-kit/blob/v3.11.2/docs/errors/CIK-MIGRATE-003.md)), first restore the reported file or move the custom logic into a project-owned workflow in a preparatory pull request, then re-run it. Merge the migration pull request. Every area is then handed off: delete the empty `.copier-answers.yml` as above and update to v4.

## Example

`handed_off: [validate]` in `.copier-answers.yml`: the `base`, `security`, `release`, `allure` and `renovate` files (such as `release.yml` and `renovate.json`) are still owned by the legacy template.

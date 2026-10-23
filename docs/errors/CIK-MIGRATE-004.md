# CIK-MIGRATE-004

Area is already handed off or its module is already installed.

## Cause

`ci-kit migrate-legacy` hands the `base`, `security`, `release`, `allure` and `renovate` areas off in one step. One of them is already in `handed_off`, its module (`.copier-answers.<module>.yml` or `.ci/modules/<module>.yml`) already exists, `handed_off` is malformed, or `.copier-answers.yml` is gone because the repository is already module-only.

## Fix

A module-only repository needs no migration. For a partial hand-off, revert the pull request that handed the area off or installed the module, then run the migration for all areas:

```bash
git revert <commit that installed the module>
ci-kit migrate-legacy --repo . --to v3.10.0
```

## Example

`.copier-answers.release.yml` exists while legacy still renders `release.yml`.

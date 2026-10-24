# CIK-CORE-012

toolkit_version or _commit differs between answers files.

## Cause

Every module answers file (`.copier-answers.<module>.yml`) must name the same ci-kit release in `toolkit_version` and `_commit`. A partial update, such as `ci-kit update --module` or a manual `copier update` of one module, left the files on different releases.

## Fix

Update every module to the same release:

```bash
ci-kit update --repo . --to <ref>
```

## Example

`.copier-answers.allure.yml` has `toolkit_version: v3.9.0` while `.copier-answers.core.yml` has `v3.10.0`, so the Allure workflow calls reusable workflows from an older release than `Validate`.

# CIK-LEGACY-006

Legacy validate is not handed off to the core module.

## Cause

The legacy template no longer renders `.github/workflows/validate.yml`; the `core` module owns it. A legacy `.copier-answers.yml` whose `handed_off` lacks `validate` (or has no `handed_off` key, as in answers last updated before v3.2.0) still relies on the legacy `validate.yml`. Copier refuses to copy or update such a repository instead of silently deleting the workflow, and `ci-kit plan` reports it.

The `ci-kit migrate` command that moved `validate.yml` to `core` is removed from the current release.

## Fix

Migrate with the last release whose legacy template still renders `validate.yml`: v3.7.0.

```bash
python -m pip install "git+https://github.com/quokkify/ci-kit@v3.7.0"
ci-kit update --repo . --module legacy --to v3.7.0
ci-kit migrate --module core --repo . --to v3.7.0
```

`ci-kit migrate` hands `validate` off, installs `core` and the language modules derived from the legacy `validate.yml` jobs, one commit each. Open a pull request with these commits and switch the base branch's required checks from the legacy Validate job names to `ci / gate` before you merge. Then update to the current release as usual (the generated `copier-update.yml` workflow, or `ci-kit update --repo . --to <current release>` with the current ci-kit CLI).

A new legacy copy hands `validate` off by default; add the `core` module next to it (`CIK-HANDOFF-001` until you do):

```bash
copier copy --trust -a .copier-answers.core.yml -d module=core https://github.com/quokkify/ci-kit.git .
```

## Example

`.copier-answers.yml` last updated by ci-kit v3.1.1 has no `handed_off`, and `copier update --defaults` fails with `CIK-LEGACY-006: validate must stay handed off`.

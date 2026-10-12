# CIK-MIGRATE-005

`ci-kit migrate` refused because the area still runs jobs that no module owns yet.

## Cause

The legacy answers list language `components`, or the legacy `.github/workflows/validate.yml` has a job that calls a reusable workflow (`uses:`, for example `docker`). The `core` module renders only the shared gate; migrating would drop those jobs, and the required check names of a reusable-workflow job (`<job name> / <called job>`) cannot be derived from the file. Nothing was changed.

The fleet updater reports such repositories as `not-applicable` (detail `language components`, or the refusal message) and does not fail the run.

## Fix

Keep the repository on the legacy template until the language modules of a later migration wave exist; the regular fleet update keeps it current meanwhile. No action is needed now.

```bash
ci-kit doctor --repo .
```

## Example

A repository with `components: [{type: python, path: .}]` in `.copier-answers.yml`: `ci-kit migrate --module core` reports `CIK-MIGRATE-005 repository has language components; migrate after language modules exist`.

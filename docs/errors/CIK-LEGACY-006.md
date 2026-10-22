# CIK-LEGACY-006

Legacy template no longer renders validate; validate is not handed off to core.

## Cause

The legacy template no longer renders `.github/workflows/validate.yml`; the `core` module owns it. A legacy `.copier-answers.yml` whose `handed_off` lacks `validate` (or has no `handed_off` key, as in answers last updated before v3.2.0) still relies on the legacy `validate.yml`. Copier refuses to copy or update such a repository instead of silently deleting the workflow, and `ci-kit plan` reports it. Two checks enforce this: a guard in the template exclusions refuses when updating from a template revision that still rendered `validate.yml`, and the `handed_off` validator refuses on copies and on later updates.

The `ci-kit migrate` command that moved `validate.yml` to `core` is removed from the current release.

## Fix

Migrate with the last release whose legacy template still renders `validate.yml`: v3.8.0.

```bash
python -m pip install "git+https://github.com/quokkify/ci-kit@v3.8.0"
ci-kit update --repo . --module legacy --to v3.8.0
ci-kit migrate --module core --repo . --to v3.8.0
```

`ci-kit migrate` hands `validate` off, installs `core` and the language modules derived from the legacy `validate.yml` jobs, one commit each. Open a pull request with these commits and switch the base branch's required checks from the legacy Validate job names to `ci / gate` before you merge. Then update to the current release as usual (the generated `copier-update.yml` workflow, or `ci-kit update --repo . --to <current release>` with the current ci-kit CLI).

A new legacy copy hands `validate` off by default; add the `core` module next to it (`CIK-HANDOFF-001` until you do):

```bash
copier copy https://github.com/quokkify/ci-kit.git . \
  --vcs-ref "$TOOLKIT_REF" --data "toolkit_version=$TOOLKIT_REF" --trust \
  -a .copier-answers.core.yml -d module=core
```

A fresh legacy copy into a directory that already holds another template's `.copier-answers.yml` is refused with this code too, because those stored answers have no `handed_off`. Use a different answers file (`-a`) or directory.

A fresh legacy copy prints a harmless Copier `MissingFileWarning` about `.copier-answers.yml`: the template looks for stored answers that do not exist yet. The commands above pass `--trust`; without it, a copy into a symlinked directory (such as a macOS temporary directory under `/var`) fails with `ForbiddenPathError`.

## Example

`.copier-answers.yml` last updated by ci-kit v3.1.1 has no `handed_off`, and `copier update --defaults` fails with a `CIK-LEGACY-006` message pointing to this runbook (`CIK-LEGACY-006: the legacy template no longer renders validate.yml. Hand validate off to the core module first; see docs/errors/CIK-LEGACY-006.md`).

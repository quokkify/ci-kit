# CIK-ADD-005

Installing the module failed and the repository was rolled back to its starting `HEAD`.

## Cause

Copier failed (the message carries its first error line), the commit could not be created, or `ci-kit plan` reported findings on the repository with the new module installed. The command resets to the original `HEAD` and deletes only files that did not exist before it ran, ignored ones included; files that were already present (for example `.venv/`) are kept.

## Fix

Read the message: a Copier error usually means the ref or template source is wrong; a plan finding names its own code, whose runbook in `docs/errors/` explains it. Fix the cause and re-run:

```bash
ci-kit doctor --repo .
ci-kit add --module labels --repo . --to v3.10.0
```

## Example

The tag passed as `--to` does not exist in the template repository: `CIK-ADD-005 labels -: cannot render module at v3.10.1: ...`.

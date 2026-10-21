# CIK-MIGRATE-003

`ci-kit migrate` refused to hand off an area whose files were edited by hand.

## Cause

A file the module will take over differs from the legacy render at the recorded `_commit` (toolkit refs are normalized). The message holds a unified diff, `template/<path>` against `repo/<path>`. Handing off would silently discard the edit.

For `.github/workflows/validate.yml` everything except the component jobs is compared: `template-contract`, triggers, `permissions` and `concurrency`. Component jobs (those calling a ci-kit language or docker workflow) are not compared here; hand-edited `with:` inputs are carried into the module declarations, and jobs that cannot be carried are [CIK-MIGRATE-005](CIK-MIGRATE-005.md). Other files of the area are compared as a whole.

The same code is reported when the file is missing from the repository, when the legacy template does not render it, or when the legacy render itself fails (the message holds the Copier error).

## Fix

Revert the edit to the rendered content, or move it into project-owned workflows, then re-run.

```bash
git checkout -- .github/workflows/validate.yml
```

## Example

`validate.yml` has a renamed job `My contract` where the template renders `Shared template contract`.

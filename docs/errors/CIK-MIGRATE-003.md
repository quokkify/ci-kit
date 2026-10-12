# CIK-MIGRATE-003

`ci-kit migrate` refused to hand off an area whose files were edited by hand.

## Cause

A file the module will take over differs from the legacy render at the recorded `_commit` (toolkit refs are normalized). The message holds a unified diff, `template/<path>` against `repo/<path>`. Handing off would silently discard the edit.

## Fix

Revert the edit to the rendered content, or move it into project-owned workflows, then re-run.

```bash
git checkout -- .github/workflows/validate.yml
```

## Example

`validate.yml` has a renamed job `My contract` where the template renders `Shared template contract`.

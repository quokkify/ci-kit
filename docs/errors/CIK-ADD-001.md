# CIK-ADD-001

`ci-kit add` refused to start because the working tree is not clean, or the path is not a Git repository with at least one commit.

## Cause

`ci-kit add` commits the new module in one commit and rolls back to the starting `HEAD` on failure. Uncommitted or untracked changes would be mixed into that commit or lost on rollback, so the command stops before touching anything.

## Fix

Commit or stash local changes, then re-run:

```bash
git status --porcelain   # must print nothing
ci-kit add --module labels --repo . --to v3.10.0
```

Ignored files (for example `.venv/`) do not count and survive a rollback.

## Example

`README.md` has an uncommitted edit; `ci-kit add --module labels` reports `CIK-ADD-001 labels -: working tree is not clean`.

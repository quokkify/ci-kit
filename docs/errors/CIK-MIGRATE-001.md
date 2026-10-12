# CIK-MIGRATE-001

`ci-kit migrate` refused to start because the working tree is not clean.

## Cause

Tracked changes or untracked files exist. Migration makes two commits and must start from a clean tree.

## Fix

Commit or stash the changes, then re-run the migration.

```bash
git status --short && ci-kit migrate --module core --repo . --to v3.2.0
```

## Example

A stray `notes.txt` in the repository root makes `git status --porcelain` non-empty.

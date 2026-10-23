# CIK-MIGRATE-001

`ci-kit migrate-legacy` needs a clean working tree.

## Cause

The repository has uncommitted changes, untracked files, or `.rej` files from an earlier Copier run. The migration commits every step and rolls back to the starting commit on failure, so it refuses to start on top of work it could not restore.

## Fix

Commit or stash the changes and delete stale `.rej` files, then re-run:

```bash
git status --porcelain --untracked-files=all
ci-kit migrate-legacy --repo . --to v3.10.0
```

## Example

`notes.txt` is untracked in the repository root.

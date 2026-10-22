# CIK-MIGRATE-001

`ci-kit migrate` refused to start because the working tree is not clean, `*.rej` files from an earlier run are present, or git is unusable.

## Cause

Tracked changes or untracked files exist. Migration makes the hand-off commit plus one commit per module and must start from a clean tree. Leftover `*.rej` files count too, even when `.gitignore` hides them from `git status`: they would be reported as new Copier conflicts. A failed migration removes the ignored files it created when it rolls back, so they come from an older run or another tool. The same code is reported when the directory is not a git repository, `git status` fails, or the `git` binary cannot be run (the message says which).

## Fix

Commit or stash the changes, delete leftover `*.rej` files named in the message, then re-run the migration.

```bash
git status --short && ci-kit migrate --module core --repo . --to v3.2.0
```

## Example

A stray `notes.txt` in the repository root makes `git status --porcelain` non-empty.

# CIK-MIGRATE-005

Migration result is not equivalent to legacy; rolled back.

## Cause

After the migration commits, every file legacy rendered must be byte for byte the same, except:

- `.github/workflows/codeql.yml`: the CodeQL language set and the analysis category must stay the same;
- `docs/ci-kit.md` and `.github/workflows/copier-update.yml`: `core` renders its own text;
- starter files: unchanged.

The required checks (`ci / gate`, `CodeQL` with `codeql`, `gitleaks` with `gitleaks`) must stay the same, legacy must render no files, and `ci-kit plan` must report nothing. The finding also covers a Copier failure or `.rej` conflict during the migration. The repository is reset to the starting commit, including ignored files the migration created.

## Fix

Read the reported path and difference. A module render that differs from legacy is a ci-kit bug: open an issue with the output. A workflow pinned to a commit SHA instead of the release tag differs byte for byte; pin the tag with `ci-kit update` first.

```bash
ci-kit migrate-legacy --repo . --to v3.10.0 --json migrate.json
```

## Example

`codeql_languages` lists `go`, but the security module would analyse `actions` and `python`.

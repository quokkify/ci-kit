# CIK-ADD-004

A file that the module renders already exists in the repository.

## Cause

`ci-kit add` renders the module into a temporary directory and refuses to install it when any of those paths (including `.ci/modules/<module>.yml`) already exists. Copier would otherwise overwrite a file the repository owns, or the repository would keep a hand-written copy the module now claims.

## Fix

Decide which copy wins. To adopt the module, delete or rename the existing file in its own commit, then re-run:

```bash
git rm .github/workflows/labels.yml
git commit -m "chore: hand the labels workflow to ci-kit"
ci-kit add --module labels --repo . --to v4.1.0
```

## Example

The repository already has its own `.github/workflows/labels.yml`: `CIK-ADD-004 labels .github/workflows/labels.yml: .github/workflows/labels.yml already exists`.

# CIK-ADD-003

A module that the requested module requires is missing, is at a different ref, or names an untrusted template source.

## Cause

Every module declares the modules it requires (`requires` in `MODULES`); all of them require `core`. `ci-kit add` checks that each required module has its `.copier-answers.<module>.yml` and that its `_commit` equals `--to`, so the new module is rendered from the same release as the rest of the repository. Without `--template-src`, the template source is the `_src_path` of `.copier-answers.core.yml`, which must be `https://github.com/quokkify/ci-kit` (or the `gh:`/SSH form). An option-like `--to` or source is refused as well.

## Fix

Bring the required modules to the target ref first, then add:

```bash
ci-kit update --repo . --to v4.1.0
ci-kit add --module labels --repo . --to v4.1.0
```

A repository that still has legacy answers must leave the legacy template first (see [docs/modules.md](../modules.md#leaving-the-legacy-template)). For an untrusted source, set `_src_path` in `.copier-answers.core.yml` to `https://github.com/quokkify/ci-kit`.

## Example

`.copier-answers.core.yml` has `_commit: v4.0.1` and the command runs with `--to v4.1.0`: `CIK-ADD-003 labels .copier-answers.core.yml: required module core is at v4.0.1, not v4.1.0`.

# CIK-ADD-003

A module that the requested module requires is missing, is at a different ref, or names an untrusted template source.

## Cause

Every module declares the modules it requires (`requires` in `MODULES`); all of them require `core`. `ci-kit add` checks that each required module has its `.copier-answers.<module>.yml` and that its `_commit` equals `--to`, so the new module is rendered from the same release as the rest of the repository. Without `--template-src`, the template source is the `_src_path` of `.copier-answers.core.yml`, which must be `https://github.com/quokkify/ci-kit` (or the `gh:`/SSH form). An option-like `--to` or source is refused as well.

## Fix

Bring the required modules to the target ref first, then add:

```bash
ci-kit update --repo . --to v3.10.0 --skip-legacy
ci-kit add --module labels --repo . --to v3.10.0
```

A legacy-only repository must migrate to the `core` module before adding any module (see [docs/modules.md](../modules.md#migrating-one-repository)). For an untrusted source, set `_src_path` in `.copier-answers.core.yml` to `https://github.com/quokkify/ci-kit`.

## Example

`.copier-answers.core.yml` has `_commit: v3.9.0` and the command runs with `--to v3.10.0`: `CIK-ADD-003 labels .copier-answers.core.yml: required module core is at v3.9.0, not v3.10.0`.

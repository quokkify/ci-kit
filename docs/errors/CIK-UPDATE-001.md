# CIK-UPDATE-001

`ci-kit update` could not update a module.

## Cause

The answers file points at an untrusted template source, Copier failed, Copier left `.rej` conflict files, or the update commit could not be created. Later modules are not touched.

## Fix

Resolve the listed `.rej` files by hand, delete them, commit, then re-run `ci-kit update`. For an untrusted source, set `_src_path` to `https://github.com/quokkify/ci-kit`.

```bash
ci-kit update --repo . --to v3.2.0
```

## Example

A local edit to `.ci/modules/java.yml` conflicts with the template change and Copier writes `.ci/modules/java.yml.rej`.

# CIK-CORE-001

Module declaration schema version is not supported.

## Cause

The declaration's `schema_version` has no matching schema in this ci-kit, or the module name is not supported.

## Fix

```bash
copier update --trust -a .copier-answers.<module>.yml
```

## Example

`.ci/modules/java.yml` contains `schema_version: 99`, or `.copier-answers.foo.yml` exists for the unknown module `foo`.

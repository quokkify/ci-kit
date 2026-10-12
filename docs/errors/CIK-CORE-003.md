# CIK-CORE-003

Module requires a module that is not installed.

## Cause

A module's `requires` list names a module with no valid answers file and declaration (java requires core).

## Fix

```bash
copier copy --trust -a .copier-answers.core.yml -d module=core <ci-kit> .
```

## Example

The repository has `java` installed but no `core` module.

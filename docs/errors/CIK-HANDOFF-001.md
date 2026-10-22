# CIK-HANDOFF-001

Area is handed off but its owner module is not installed.

## Cause

Legacy .copier-answers.yml lists an area in handed_off, but the module that owns that area has no answers file and declaration.

## Fix

Install the owner module; for validate that is core:

```bash
copier copy --trust -a .copier-answers.core.yml -d module=core https://github.com/quokkify/ci-kit.git .
```

validate cannot be removed from handed_off: the legacy template no longer renders validate.yml ([CIK-LEGACY-006](CIK-LEGACY-006.md)).

## Example

handed_off: [validate] while .ci/modules/core.yml does not exist.

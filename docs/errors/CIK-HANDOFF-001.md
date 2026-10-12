# CIK-HANDOFF-001

Area is handed off but its owner module is not installed.

## Cause

Legacy .copier-answers.yml lists an area in handed_off, but the module that owns that area has no answers file and declaration.

## Fix

```bash
ci-kit migrate --module core --repo . --to <REF>
```
Or remove the area from handed_off if the hand-off was not intended.

## Example

handed_off: [validate] while .ci/modules/core.yml does not exist.

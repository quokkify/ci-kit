# CIK-HANDOFF-001

Area is handed off but its owner module is not installed.

## Cause

Legacy .copier-answers.yml lists an area in handed_off, but the module that owns that area has no answers file and declaration.

## Fix

The `ci-kit migrate` command is retired; see [CIK-LEGACY-006](CIK-LEGACY-006.md) for the migration path.
Or remove the area from handed_off if the hand-off was not intended.

## Example

handed_off: [validate] while .ci/modules/core.yml does not exist.

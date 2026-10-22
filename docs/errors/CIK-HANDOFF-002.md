# CIK-HANDOFF-002

Module is installed but its area is not handed off.

## Cause

The module renders a file that the legacy template also owns, so both would write it.

## Fix

The `ci-kit migrate` command is retired; see [CIK-LEGACY-006](CIK-LEGACY-006.md).
Handing the area off means adding it to handed_off in .copier-answers.yml.

## Example

core is installed and handed_off is empty, so .github/workflows/validate.yml has two owners.

# CIK-HANDOFF-002

Module is installed but its area is not handed off.

## Cause

The module renders a file that the legacy template also owns, so both would write it.

## Fix

```bash
ci-kit migrate --module core --repo . --to <REF>
```
This adds the area to handed_off in .copier-answers.yml.

## Example

core is installed and handed_off is empty, so .github/workflows/validate.yml has two owners.

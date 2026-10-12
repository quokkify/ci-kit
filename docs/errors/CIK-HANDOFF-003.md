# CIK-HANDOFF-003

handed_off is malformed or names an unknown area.

## Cause

handed_off in .copier-answers.yml is not a list of strings, or contains an area ci-kit does not know.

## Fix

```bash
ci-kit plan --repo .
```
Edit .copier-answers.yml so handed_off is a list of known area names (for example validate), then re-run the plan.

## Example

handed_off: [bogus] or handed_off: validate.

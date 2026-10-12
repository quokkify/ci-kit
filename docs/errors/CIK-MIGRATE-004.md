# CIK-MIGRATE-004

`ci-kit migrate` refused because there is nothing to hand off.

## Cause

The module is already installed (`.copier-answers.<module>.yml` exists), its area is already in `handed_off`, `handed_off` is malformed, or the module owns no hand-off area.

## Fix

Check the current state; no migration is needed when the area is already handed off.

```bash
ci-kit doctor --repo .
```

## Example

Running `ci-kit migrate --module core` twice: the second run finds `validate` in `handed_off`.

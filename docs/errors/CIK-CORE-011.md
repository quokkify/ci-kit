# CIK-CORE-011

Module-owned file drifted from its rendered template.

## Cause

A file owned by a module was edited or is claimed by two modules.

## Fix

```bash
git checkout -- <path>
```

## Example

A hand edit changed `.github/workflows/validate.yml`, which the core module owns.

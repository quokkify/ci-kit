# CIK-ADD-002

`ci-kit add` does not know the requested module.

## Cause

The `--module` value is not in `MODULES` (`tools/ci_kit/modules.py`).

## Fix

Use one of the module names listed in [docs/modules.md](../modules.md#available-modules), spelled exactly:

```bash
ci-kit add --module labels --repo . --to v4.1.0
```

## Example

`ci-kit add --module label` (missing `s`) reports `CIK-ADD-002 label -: unknown module label`.

# CIK-CORE-020

Gate found a failing or missing module job.

## Cause

The gate job saw a module job that failed, was cancelled, or was skipped when it was expected to run.

## Fix

```bash
ci-kit doctor --repo .
```
Then fix the failing module job and re-run the workflow.

## Example

The `java` job failed while the `core-checks` job passed.

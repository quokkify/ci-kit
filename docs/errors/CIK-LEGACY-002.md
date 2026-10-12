# CIK-LEGACY-002

Legacy Copier commit does not match toolkit_version.

## Cause

_commit and toolkit_version in .copier-answers.yml differ, so the rendered refs and the template version disagree.

## Fix

```bash
ci-kit update --repo . --to <toolkit_version>
```
Or run copier update with -d toolkit_version=<REF> for the same REF.

## Example

_commit: v1.2.0 while toolkit_version: v1.1.0.

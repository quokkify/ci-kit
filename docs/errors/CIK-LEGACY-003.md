# CIK-LEGACY-003

Legacy workflow references a different toolkit ref.

## Cause

A legacy-owned workflow has uses: quokkify/ci-kit/...@<ref> where ref is not toolkit_version (a 40-hex SHA with a trailing comment equal to toolkit_version is accepted). Module-owned files are skipped.

## Fix

```bash
ci-kit update --repo . --to <toolkit_version>
```
Or edit the ref in the reported workflow to match toolkit_version.

## Example

validate.yml uses quokkify/ci-kit/actions/checkout@v1.2.3 while toolkit_version is v1.3.0.

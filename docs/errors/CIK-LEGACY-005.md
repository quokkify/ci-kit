# CIK-LEGACY-005

Legacy-owned JSON or YAML file does not parse.

## Cause

renovate.json, a release-please config or manifest, or a workflow owned by the legacy template is not valid JSON or YAML.

## Fix

```bash
git checkout -- <reported path>
```
Or fix the syntax by hand and re-run ci-kit plan.

## Example

.github/renovate.json contains a trailing comma.

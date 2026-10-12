# CIK-CORE-004

Module declaration is unparseable or violates its schema.

## Cause

`.ci/modules/<module>.yml` is not valid YAML, is not a mapping, or does not match its JSON schema.

## Fix

```bash
git checkout -- .ci/modules/<module>.yml
```

## Example

`.ci/modules/java.yml` contains `a: [`, which is not valid YAML.

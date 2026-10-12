# CIK-CORE-010

Module declaration drifted from its rendered template.

## Cause

The committed declaration differs from what the module template renders for the recorded answers.

## Fix

```bash
copier update --trust -a .copier-answers.<module>.yml
```

## Example

A hand edit changed `java_version` in `.ci/modules/java.yml` without updating the answers.

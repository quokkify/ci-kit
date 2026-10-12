# CIK-LEGACY-004

Enabled legacy feature lacks its generated files.

## Cause

A feature answer (renovate, codeql, allure_report and related Allure settings) is enabled but its generated file or valid setting is missing.

## Fix

```bash
copier recopy --trust --defaults -a .copier-answers.yml
```
Or disable the feature in .copier-answers.yml.

## Example

renovate: true while .github/renovate.json is deleted.

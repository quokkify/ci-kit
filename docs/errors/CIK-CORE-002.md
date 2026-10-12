# CIK-CORE-002

Module answers file and declaration are not paired.

## Cause

A module has only one of `.copier-answers.<module>.yml` and `.ci/modules/<module>.yml`.

## Fix

```bash
git checkout -- .ci/modules/<module>.yml
```
If the module was never installed, remove the orphan file; otherwise re-render with `copier copy --trust -a .copier-answers.<module>.yml -d module=<module> <ci-kit> .`.

## Example

`.ci/modules/java.yml` was deleted while `.copier-answers.java.yml` remains.

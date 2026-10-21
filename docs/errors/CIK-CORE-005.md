# CIK-CORE-005

Component id is declared by more than one module.

## Cause

Two module declarations under `.ci/modules/` list a component with the same `id`.

## Fix

Rename one of the components in its `.ci/modules/<module>.yml` so every id is unique across modules.

## Example

`.ci/modules/python.yml` and `.ci/modules/node.yml` both declare a component with `id: app`.

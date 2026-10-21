# CIK-CORE-005

Component id is declared more than once, or a component name is used more than once in a module.

## Cause

Two module declarations under `.ci/modules/` list a component with the same `id`, one declaration lists the same `id` twice, or two components of one module share a `name` (check names are `<type> (<name>)`, so they would collide).

## Fix

Rename one of the components in its `.ci/modules/<module>.yml` so every id is unique across modules and every name is unique within its module.

## Example

`.ci/modules/python.yml` and `.ci/modules/node.yml` both declare a component with `id: app`, or `.ci/modules/node.yml` declares two components named `Web`.

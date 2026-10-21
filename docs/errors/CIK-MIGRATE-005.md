# CIK-MIGRATE-005

`ci-kit migrate` refused because a legacy validate job or input cannot be mapped to a module.

## Cause

`ci-kit migrate --module core` maps every job of the legacy `.github/workflows/validate.yml` except `template-contract` to a module component (`java`, `python`, `node`, `docker`). A job cannot be mapped when:

- it has keys other than `name`, `uses` and `with` (`if`, `needs`, `secrets`, `permissions`, `strategy`, `steps`, `runs-on`, ...);
- it has no `uses:`, or `uses:` is not a known ci-kit workflow, or its ref differs from `toolkit_version`;
- a `with:` input is not a field of the module (for `docker`, any input except `context`, `dockerfile`, `target` and `push: false`), or a version is a floating-point number.

A module cannot reproduce such a job, and dropping it would silently lose CI behaviour. The message names the job and the key or input. Nothing was changed.

The same code is reported after the modules were copied when a migrated component does not run with the same effective workflow inputs as its legacy job (or the set of jobs differs); the changes are rolled back to the original `HEAD`, and the message names the job and the first differing input.

## Fix

Open a preparatory pull request in the repository: move the hand-written job or step into a project-owned workflow with its own required check, or drop the unsupported key or input. Merge it, then re-run the migration.

```bash
ci-kit migrate --module core --repo . --to <tag>
```

## Example

`validate.yml` has a job `complete` without `uses:`: `ci-kit migrate --module core` reports `CIK-MIGRATE-005 job complete does not call a reusable workflow; it cannot be mapped to a module, move it to a project-owned workflow in a preparatory pull request`.

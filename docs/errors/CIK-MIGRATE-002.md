# CIK-MIGRATE-002

`ci-kit migrate` refused to start because the legacy answers do not match the target ref.

## Cause

`.copier-answers.yml` is unreadable, has an untrusted `_src_path`, or its `_commit` differs from `--to`. Migration needs the legacy template and the module template at the same version.

## Fix

Bring legacy to the target ref first, then migrate.

```bash
ci-kit update --repo . --module legacy --to v3.2.0
```

## Example

Legacy `_commit` is `v3.1.0` and the command is run with `--to v3.2.0`.

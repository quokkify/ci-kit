# CIK-MIGRATE-002

Legacy or core answers are not on the target ref or not in hybrid(validate).

## Cause

`ci-kit migrate-legacy --to <ref>` compares the repository with the legacy render at `<ref>`, so it needs:

- `.copier-answers.yml` readable, with `_commit` equal to `<ref>` and a trusted `_src_path`;
- `.copier-answers.core.yml` with `_commit` equal to `<ref>`;
- `validate` in `handed_off` (the repository is in hybrid(validate), see [CIK-LEGACY-006](CIK-LEGACY-006.md)).

## Fix

Update every answers file to the target release first, merge that, then migrate:

```bash
ci-kit update --repo . --to v3.10.0
ci-kit migrate-legacy --repo . --to v3.10.0
```

## Example

`.copier-answers.yml` has `_commit: v3.9.0` and the command runs with `--to v3.10.0`.

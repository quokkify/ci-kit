# CIK-MIGRATE-003

Legacy-area file differs from the legacy render.

## Cause

A file of the `security`, `release`, `allure`, `renovate` or `base` area that legacy renders is missing or hand-edited. The modules render the same files as legacy; migrating would silently drop the edit. Nothing is committed. Starter files (`README.md`, `.github/pull_request_template.md`, `.github/renovate.json`, `.github/release-please/{config,manifest}.json`) belong to the project and are not compared.

## Fix

Restore the reported file from the legacy render, or move the custom logic into a project-owned workflow, in a preparatory pull request:

```bash
git checkout <commit that ci-kit update created> -- <reported path>
```

Then re-run `ci-kit migrate-legacy`.

## Example

`.github/workflows/release.yml` has an extra step added by hand.

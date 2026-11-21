# CIK-CORE-031

Template update changes workflow files that the Actions token cannot push.

## Cause

The `Update project template` workflow (`copier-update.yml`) pushes the `automation/copier-template-update` branch. GitHub rejects any push by the repository's Actions token (`GITHUB_TOKEN`) that creates or changes a file under `.github/workflows/`, and template updates usually do: every release moves the version pins in the generated workflows. Without a `TEMPLATE_UPDATE_TOKEN` repository secret the `Push the update branch` job stops before the push, lists the workflow files the update changes, and pushes nothing. Updates that change no workflow file are pushed with the Actions token as before.

Public Quokkify repositories are also updated by the central fleet automation, which pushes with its own credential. Private repositories rely on this workflow alone.

## Fix

Either:

- Add a `TEMPLATE_UPDATE_TOKEN` repository secret and rerun the workflow. Use a fine-grained personal access token with an expiry date, scoped to this repository only, with `Contents: Read and write` and `Workflows: Read and write` (`Metadata: Read` is added automatically). Only the workflow's `push` job receives it; the job that runs the template never does. A GitHub App installation token does not work as a stored secret: it expires after one hour.

  Setting the token has a trade-off. Unlike a push with the Actions token, a push with this token starts `on: push` workflows on the update branch at once, before review ([GitHub docs](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow#triggering-a-workflow-from-a-workflow)). A compromised ci-kit release could add a push-triggered workflow that runs with this repository's secrets. Keep secret-bearing workflows behind `branches: [<default branch>]` filters or protected environments, or prefer the manual path below.

  ```bash
  gh secret set TEMPLATE_UPDATE_TOKEN --repo <owner>/<repo>
  ```

- Or update by hand and open the pull request yourself:

  ```bash
  git switch main && git pull --ff-only
  export TOOLKIT_REF="$(gh release view --repo quokkify/ci-kit --json tagName --jq .tagName)"
  git switch -c "chore/update-ci-kit-${TOOLKIT_REF}"
  pip install "git+https://github.com/quokkify/ci-kit@${TOOLKIT_REF}"
  ci-kit update --repo . --to "$TOOLKIT_REF"
  git push -u origin HEAD
  gh pr create --fill
  ```

## Example

A private repository runs `Update project template` from v4.0.1 to v4.3.6. The update changes `.github/workflows/copier-update.yml` and `.github/workflows/validate.yml`, no `TEMPLATE_UPDATE_TOKEN` secret is set, and the run fails with `CIK-CORE-031` instead of GitHub's `refusing to allow a GitHub App to create or update workflow ... without workflows permission`.

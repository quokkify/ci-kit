# Shared checkout

`actions/checkout` is a composite action in project-toolkit. It checks out the
caller repository in the caller's job and delegates to one SHA-pinned upstream
`actions/checkout`. All upstream inputs and the `ref` and `commit` outputs are
forwarded. The upstream pin is maintained in `actions/checkout/action.yml`.

## Connect a project-owned workflow

After the first toolkit release containing this action, replace only the `uses`
line in custom workflows such as `.github/workflows/docs.yml`:

```yaml
- name: Checkout
  id: source
  uses: quokkify/project-toolkit/actions/checkout@vX.Y.Z
  with:
    persist-credentials: false
```

Replace `vX.Y.Z` with the exact released tag recorded as `toolkit_version` in
`.copier-answers.yml`. Releases published before this action was added do not
contain it. Preserve any existing `with`, `id`, `if`, and step-level settings.
`steps.source.outputs.commit` and `steps.source.outputs.ref` remain available.

Use the remote action as the first step: GitHub downloads remote actions before
running the job. A repository-local `./.github/actions/checkout` cannot bootstrap
its own checkout because its files are not yet present. A reusable workflow also
cannot provide a checkout to later jobs, which have separate workspaces.

The wrapper preserves upstream defaults, including `persist-credentials: true`.
Set it to `false` for jobs that only read the repository. Jobs that push changes
can retain the default and must have the appropriate token permissions. GitHub
uses the nested upstream action's post-job handler to clean up credentials.
The upstream runner requirements and the v7 privileged-event protection still
apply; `allow-unsafe-pr-checkout` defaults to `false`.

## Update and delivery

1. The existing global Renovate preset enables the built-in `github-actions`
   manager. It discovers `actions/checkout/action.yml` automatically and updates
   the upstream SHA and version comment in project-toolkit. No local custom
   manager or consumer Renovate override is required for this dependency.
2. Toolkit validation checks the forwarding contract and renders the Copier
   scenarios. The toolkit CI also performs a real checkout through the candidate
   action, checking outputs, full history, sparse checkout, and disabled
   credential persistence.
3. Release Please publishes the next toolkit version. The existing release
   follow-up runs the Copier fleet updater and opens template update PRs.
4. Copier-generated workflows use `quokkify/project-toolkit/actions/checkout@{{ toolkit_version }}`.
   The fleet updater also advances toolkit action references in project-owned
   workflows, including exact tags and SHA pins with a release comment. The
   answers and workflow references move in the same PR.

For example, after migrating `path-of-exile-starter`'s `docs.yml` once, subsequent
checkout upgrades arrive through its template update PR. Its Python and Pages
steps retain their existing independent dependency policy. Existing custom
workflows are not automatically rewritten from upstream checkout to this action;
that one-time migration must use a release which already contains the wrapper.

Use the fleet updater for coordinated updates of project-owned workflows; plain
`copier update` only renders template-owned files. Direct Renovate updates of
project-toolkit references in custom workflows are a separate global-preset
policy: if disabled for a Copier fleet, the rule belongs in that fleet's shared
preset. Do not disable upstream `actions/checkout` globally, since non-migrated
workflows still need its updates.

The toolkit's own bootstrap checkouts and self-contained reusable workflows keep
upstream checkout pins in this repository. They cannot use an unreleased remote
wrapper or a local wrapper before obtaining toolkit files. Renovate maintains
those pins centrally as well.

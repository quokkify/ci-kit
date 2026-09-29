# Versioning

- The toolkit publishes SemVer releases; the repository manifest starts at `0.0.0` until Release Please opens the first release PR.
- Production examples and generated callers use exact immutable tags such as `@v2.23.5`; release tags are never rewritten.
- Breaking workflow/input/template changes require a new major release.
- Release flow is a stable two-step process: pushes to `main` or manual dispatch run `.github/workflows/release.yml`, which calls `.github/workflows/release-please.yml` in `manifest` mode with `.github/release-please/config.json` and `.github/release-please/manifest.json`; maintainers then review and merge the Release Please PR. No manual tag or release command is part of the flow.
- Renovate proposes toolkit upgrades through reviewable PRs.
- Before `1.0.0`, breaking changes are called out prominently in release notes even when SemVer permits a minor bump.
- Mutable major aliases such as `v1` may be convenient but are weaker supply-chain pins; this toolkit's production examples prefer exact release tags, while a full commit SHA is the strongest immutable reference.

- Template versioning and reusable workflow versioning are related but separate: `copier update` changes physical generated files, while Renovate changes referenced workflow tags.

Release notes

Release Please remains authoritative for versions and Conventional Commit
sections, including breaking changes from the `BREAKING CHANGE:` footer. Enrich
adds only the optional PR template sections `Highlight`, `Usage example`, and
`Migration`. Leave these fields empty for ordinary changes. Usage examples may
contain fenced Markdown, including language identifiers.

Dependency updates use `chore(deps)` or `deps(deps)` Conventional Commits and
appear with other chores in the `🧹 Chores` section. Enrich does not move,
normalize, or synthesize chore/dependency entries.

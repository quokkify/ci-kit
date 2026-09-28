# Versioning

- The toolkit publishes SemVer releases; the repository manifest starts at `0.0.0` until Release Please opens the first release PR.
- Production examples and generated callers use exact immutable tags such as `@v2.23.1`; release tags are never rewritten.
- Breaking workflow/input/template changes require a new major release.
- Release flow is a stable two-step process: pushes to `main` or manual dispatch run `.github/workflows/release.yml`, which calls `.github/workflows/release-please.yml` in `manifest` mode with `.github/release-please/config.json` and `.github/release-please/manifest.json`; maintainers then review and merge the Release Please PR. No manual tag or release command is part of the flow.
- Renovate proposes toolkit upgrades through reviewable PRs.
- Before `1.0.0`, breaking changes are called out prominently in release notes even when SemVer permits a minor bump.
- Mutable major aliases such as `v1` may be convenient but are weaker supply-chain pins; this toolkit's production examples prefer exact release tags, while a full commit SHA is the strongest immutable reference.

- Template versioning and reusable workflow versioning are related but separate: `copier update` changes physical generated files, while Renovate changes referenced workflow tags.

Release notes

Release Please remains authoritative for versions and the normal Conventional
Commit sections. The optional PR template sections (`Release notes`, `Highlight`,
`Usage example`, `Migration`, and `Breaking change`) are user-facing context;
they do not replace `feat`/`fix` commits or the `BREAKING CHANGE:` versioning
signal. Leave them empty for ordinary changes. Usage examples may contain fenced
Markdown, including language identifiers.

Dependency release notes use Renovate's native `deps(deps)` Conventional Commit
type. Release Please 17.6.0 matches changelog sections by commit type, so the
legacy `chore(deps)` spelling remains intentionally hidden along with unrelated
`chore` commits; this producer contract is the supported replacement.

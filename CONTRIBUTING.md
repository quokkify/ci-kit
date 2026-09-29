# Contributing

Use Conventional Commits, create one focused branch/PR, and keep workflows atomic. Before opening a PR run:

```console
python scripts/validate.py --static
python scripts/validate_python_fixture.py
node scripts/validate_node_fixture.mjs
bash scripts/validate_java_fixture.sh
# Or run the complete canonical suite:
python scripts/validate.py
actionlint .github/workflows/*.yml examples/*.yml
git diff --check
```

New third-party Actions must be pinned to a full commit SHA and covered by Renovate. Add a composite action only with evidence of repeated step-level logic. Do not create or move release tags from feature work.

Optional release context

The centrally managed PR template includes optional `Release notes`, `Highlight`,
`Usage example`, and `Migration` sections. They are ignored when empty. Use
`Highlight` sparingly, keep examples runnable, and preserve fenced code blocks.
Release Please owns breaking-change notes through the Conventional Commit
`BREAKING CHANGE:` footer.

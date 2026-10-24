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

The canonical validator automatically runs every root `tests/test_*.py` suite in
filename order through `scripts/run_test_suites.py`. Add new suites there without
editing a list in the validator. Intentional exclusions belong in the runner's
`EXCLUDED_SUITES` with a reason. Child suites inherit
`PROJECT_TOOLKIT_NESTED_VALIDATION=1` so validators launched by tests do not recurse.
Each file is loaded through unittest in an isolated process, including files without
a `unittest.main()` entrypoint. You can run the runner directly for focused
verification. Missing suites and files without test cases fail closed.

CI runs the suites as four parallel `unit-tests` matrix shards, each invoked as
`python scripts/run_test_suites.py --shard I/4`; `--list-shards 4` prints the layout.
Shards are balanced by the `SUITE_WEIGHTS` table in the runner (approximate seconds
per suite); update it when suite durations shift noticeably. New suites need no edit
and land in exactly one shard at the default weight. `validate.py` fails when the
shards do not cover every suite once or when a weight names a missing suite, and
`python scripts/validate.py --skip-suites` skips the runner for jobs that shard it.

CodeQL scans toolkit scripts, tests, shipped actions, and executable workflows.
The Actions analysis also renders all `tests/scenarios/modules/*.yml` from the current
working tree before initialization. Generated YAML is temporarily placed under
`.github/workflows/codeql-generated-*` for extraction; raw Jinja files are not
workflow evidence. This rendering does not execute template tasks or extensions.
Run it only in a disposable checkout: generated files are for analysis, not commits.

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from update_copier_fleet import bump_project_owned_toolkit_refs


class CheckoutTests(unittest.TestCase):
    def test_ci_sparse_checkout_smoke_script_with_nested_readmes(self):
        workflow = yaml.safe_load((ROOT / '.github/workflows/validate-toolkit.yml').read_text())
        steps = workflow['jobs']['lint']['steps']
        checkout = next(step for step in steps if step.get('id') == 'checkout-smoke')
        verify = next(step for step in steps if step.get('name') == 'Verify shared checkout outputs and options')
        with tempfile.TemporaryDirectory(prefix='checkout-smoke-') as tmp:
            root = Path(tmp).resolve()
            repo = root / checkout['with']['path']
            repo.mkdir(parents=True)
            (repo / 'README.md').write_text('Root readme\n')
            (repo / 'actions').mkdir()
            (repo / 'actions/README.md').write_text('Nested readme\n')
            (repo / 'actions/action.yml').write_text('name: fixture\n')

            def git(*args):
                return subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

            git('init', '-q')
            git('config', 'commit.gpgsign', 'false')
            git('config', 'core.hooksPath', '/dev/null')
            git('add', '.')
            git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture')
            commit = git('rev-parse', 'HEAD')
            git('sparse-checkout', 'set', '--no-cone', '--', checkout['with']['sparse-checkout'])
            result = subprocess.run(['bash', '-c', verify['run']], cwd=root,
                env={**os.environ, 'CHECKOUT_COMMIT': commit, 'CHECKOUT_REF': '', 'EXPECTED_COMMIT': commit},
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_drop_in_contract_and_authentication_defaults(self):
        action = yaml.safe_load((ROOT / 'actions/checkout/action.yml').read_text())
        defaults = {
            'repository': '${{ github.repository }}', 'ref': '',
            'token': '${{ github.token }}', 'ssh-key': '', 'ssh-known-hosts': '',
            'ssh-strict': 'true', 'ssh-user': 'git', 'persist-credentials': 'true',
            'path': '', 'clean': 'true', 'filter': '', 'sparse-checkout': '',
            'sparse-checkout-cone-mode': 'true', 'fetch-depth': '1',
            'fetch-tags': 'false', 'show-progress': 'true', 'lfs': 'false',
            'submodules': 'false', 'set-safe-directory': 'true',
            'github-server-url': '', 'allow-unsafe-pr-checkout': 'false',
        }
        self.assertEqual({k: v['default'] for k, v in action['inputs'].items()}, defaults)
        self.assertEqual(action['runs']['using'], 'composite')
        self.assertEqual(len(action['runs']['steps']), 1)
        step = action['runs']['steps'][0]
        self.assertRegex(step['uses'], r'^actions/checkout@[0-9a-f]{40}$')
        self.assertEqual(step['with'], {key: '${{ inputs.' + key + ' }}' for key in defaults})
        self.assertEqual(set(action['outputs']), {'ref', 'commit'})
        for key in action['outputs']:
            self.assertEqual(action['outputs'][key]['value'], '${{ steps.' + step['id'] + '.outputs.' + key + ' }}')

    def test_renovate_discovers_the_upstream_pin(self):
        config = json.loads((ROOT / 'renovate/default.json').read_text())
        self.assertIn('github-actions', config['enabledManagers'])
        text = (ROOT / 'actions/checkout/action.yml').read_text()
        self.assertRegex(text, r'uses: actions/checkout@[0-9a-f]{40} # v\d+\.\d+\.\d+')

    def test_every_generated_checkout_tracks_the_copier_version(self):
        sources = ROOT / 'templates/project/template/.github/workflows'
        expected = {'validate.yml', 'codeql.yml', 'gitleaks.yml', 'copier-update.yml', 'allure-report.yml'}
        actual = set()
        for source in sources.glob('*.jinja'):
            text = source.read_text()
            self.assertNotIn('uses: actions/checkout@', text, source.name)
            for line in text.splitlines():
                if 'uses: quokkify/project-toolkit/actions/checkout@' in line:
                    actual.add(source.name.removesuffix('.jinja'))
                    rendered = Environment(undefined=StrictUndefined).from_string(line).render(toolkit_version='v9.8.7')
                    self.assertTrue(rendered.endswith('/actions/checkout@v9.8.7'))
        self.assertEqual(actual, expected)

    def test_fleet_updates_checkout_without_changing_docs_steps(self):
        # Model a caller-owned docs workflow after its one-time migration. Both
        # exact tags and Renovate's SHA + version comment must advance atomically.
        for ref in ('v2.22.0', 'a' * 40 + ' # v2.22.0'):
            with self.subTest(ref=ref), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                path = root / '.github/workflows/docs.yml'
                path.parent.mkdir(parents=True)
                original = '''name: Publish documentation
on: [push]
permissions:
  contents: write
jobs:
  docs:
    runs-on: ubuntu-latest
    steps:
      - id: source
        uses: quokkify/project-toolkit/actions/checkout@REF
        with:
          ref: main
          fetch-depth: 0
          persist-credentials: false
      - run: zensical build
'''.replace('@REF', '@' + ref)
                path.write_text(original)
                before = yaml.safe_load(original)
                changed = bump_project_owned_toolkit_refs(root, 'v9.8.7', resolve_commit=lambda: 'b' * 40)
                self.assertEqual(changed, ['.github/workflows/docs.yml'])
                after = yaml.safe_load(path.read_text())
                expected_ref = 'b' * 40 if ref.startswith('a' * 40) else 'v9.8.7'
                before['jobs']['docs']['steps'][0]['uses'] = 'quokkify/project-toolkit/actions/checkout@' + expected_ref
                self.assertEqual(after, before)
                self.assertIn('v9.8.7', path.read_text())
                self.assertEqual(bump_project_owned_toolkit_refs(root, 'v9.8.7', resolve_commit=lambda: 'b' * 40), [])


    def test_copier_upgrade_migrates_generated_checkout_and_keeps_custom_workflows(self):
        with tempfile.TemporaryDirectory(prefix='checkout-copier-') as tmp:
            root = Path(tmp).resolve()
            source, consumer = root / 'source', root / 'consumer'
            source.mkdir()
            shutil.copy2(ROOT / 'copier.yml', source / 'copier.yml')
            shutil.copytree(ROOT / 'templates', source / 'templates')
            current = {p: p.read_text() for p in source.rglob('*.yml.jinja')}
            upstream = 'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1'
            shared = 'quokkify/project-toolkit/actions/checkout@{{ toolkit_version }}'
            for path, text in current.items():
                path.write_text(text.replace(shared, upstream))

            def run(*args, cwd=source):
                result = subprocess.run(args, cwd=cwd, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            def init(path):
                run('git', 'init', '-q', cwd=path)
                run('git', 'config', 'user.name', 'Fixture', cwd=path)
                run('git', 'config', 'user.email', 'fixture@example.invalid', cwd=path)
                run('git', 'config', 'commit.gpgsign', 'false', cwd=path)
                run('git', 'config', 'core.hooksPath', '/dev/null', cwd=path)

            init(source)
            run('git', 'add', '.')
            run('git', 'commit', '-qm', 'baseline')
            run('git', 'tag', 'v8.0.0')
            copier = shutil.which('copier')
            self.assertIsNotNone(copier, 'Copier is required for upgrade coverage')
            run(copier, 'copy', '--trust', '--defaults', '--vcs-ref', 'v8.0.0',
                '--data', 'toolkit_version=v8.0.0', '--data', 'components=[]',
                '--data', 'codeql=false', '--data', 'renovate=false',
                str(source), str(consumer))
            custom = consumer / '.github/workflows/docs.yml'
            custom_text = 'name: Docs\non: [push]\njobs:\n  docs:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: ' + upstream + '\n      - run: zensical build\n'
            custom.write_text(custom_text)
            init(consumer)
            run('git', 'add', '.', cwd=consumer)
            run('git', 'commit', '-qm', 'consumer', cwd=consumer)
            for path, text in current.items():
                path.write_text(text)
            run('git', 'add', '.')
            run('git', 'commit', '-qm', 'shared checkout')
            run('git', 'tag', 'v8.1.0')
            run(copier, 'update', '--trust', '--defaults', '--conflict=rej',
                '--vcs-ref', 'v8.1.0', '--data', 'toolkit_version=v8.1.0', '.', cwd=consumer)
            answers = yaml.safe_load((consumer / '.copier-answers.yml').read_text())
            self.assertEqual(answers['_commit'], 'v8.1.0')
            self.assertEqual(answers['toolkit_version'], 'v8.1.0')
            self.assertFalse(list(consumer.rglob('*.rej')))
            self.assertEqual(custom.read_text(), custom_text)
            for name in ('validate', 'gitleaks', 'copier-update'):
                text = (consumer / f'.github/workflows/{name}.yml').read_text()
                self.assertIn('uses: quokkify/project-toolkit/actions/checkout@v8.1.0', text)
                self.assertNotIn('uses: actions/checkout@', text)


if __name__ == '__main__':
    unittest.main()

"""Execute the shipped GitHub scripts with API fixtures; no live publication."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, main

import jinja2
import yaml

ROOT = Path(__file__).resolve().parents[1]
CORE_PATH = ROOT / '.github/workflows/allure-publisher-core.yml'
PAGES_PATH = ROOT / '.github/workflows/allure-pages.yml'
CORE = yaml.safe_load(CORE_PATH.read_text())
PAGES = yaml.safe_load(PAGES_PATH.read_text())
MARKER = '<!-- project-toolkit-allure-report -->'


def script(job: dict, identifier: str) -> str:
    return next(step['with']['script'] for step in job['steps']
                if step.get('id') == identifier or step.get('name') == identifier)


def fixture() -> dict:
    return {
        'run': {'id': 41, 'run_attempt': 1, 'workflow_id': 7, 'name': 'Run tests',
                'path': '.github/workflows/test.yml', 'event': 'pull_request', 'conclusion': 'success',
                'head_sha': 'abc', 'head_repository': {'full_name': 'example/project'}},
        'pull': {'number': 42, 'state': 'open', 'head': {'sha': 'abc', 'repo': {'full_name': 'example/project'}},
                 'base': {'repo': {'full_name': 'example/project'}}, 'user': {'login': 'author'}},
        'runs': [{'id': 41, 'run_attempt': 1}],
        'artifacts': [{'name': 'allure-results-job', 'id': 1, 'size_in_bytes': 1, 'expired': False}],
        'report_artifacts': [{'name': 'allure-report-99', 'id': 9, 'size_in_bytes': 1, 'expired': False}],
        'body': 'Trusted compact report\n' + MARKER,
        'comments': [],
    }


def execute(scripts: str | list[str], data: dict, mode: str = 'external', minimum: int = 1,
            skip_empty: bool = False) -> dict:
    """Run actual YAML script bodies, binding only GitHub's external interfaces."""
    harness = r'''
const fixture = JSON.parse(process.argv[1]);
const scripts = JSON.parse(process.argv[2]);
const context = {payload: {workflow_run: fixture.run}, repo: {owner: 'example', repo: 'project'}, runId: 99};
const requests = [], failures = [], warnings = [], posted = [], phases = [];
let outputs = {}, pullReads = 0;
const github = {
  rest: {
    pulls: {list: 'pulls', get: async (params) => {
      requests.push({operation: 'getPull', params});
      const list = fixture.pull_sequence || [fixture.pull];
      return {data: list[Math.min(pullReads++, list.length - 1)]};
    }},
    actions: {listWorkflowRuns: 'runs', listWorkflowRunArtifacts: 'artifacts'},
    issues: {listComments: 'comments', updateComment: async (params) => posted.push({kind: 'update', ...params}),
             createComment: async (params) => posted.push({kind: 'create', ...params})},
  },
  paginate: async (operation, params) => {
    requests.push({operation, params});
    if (operation === 'pulls') return fixture.pulls || [fixture.pull];
    if (operation === 'runs') return fixture.runs;
    if (operation === 'comments') return fixture.comments;
    if (operation === 'artifacts') return params.run_id === context.runId ? fixture.report_artifacts : fixture.artifacts;
    throw Error(`Unexpected API operation: ${operation}`);
  },
};
const core = {setOutput: (name, value) => outputs[name] = value,
  warning: (message) => warnings.push(message), setFailed: (message) => failures.push(message)};
const fakeRequire = (name) => {
  if (name !== 'fs') throw Error(`Unexpected module: ${name}`);
  return {readFileSync: (name) => {
    if (name !== '.allure-generated/allure-pr-comment.md') throw Error(`Unexpected file: ${name}`);
    return fixture.body;
  }};
};
(async () => {
  const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
  for (const body of scripts) {
    outputs = {};
    await new AsyncFunction('github', 'context', 'core', 'require', 'process', body)(github, context, core, fakeRequire, process);
    phases.push({...outputs});
  }
  console.log(JSON.stringify({outputs, phases, requests, failures, warnings, posted}));
})().catch((error) => {console.error(error); process.exitCode = 1;});
'''
    env = dict(os.environ, SOURCE_WORKFLOW='Run tests', SOURCE_WORKFLOW_PATH='.github/workflows/test.yml',
               ARTIFACT_PREFIX='allure-results-', ARTIFACT_MODE=mode, MINIMUM_ARTIFACTS=str(minimum),
               MAXIMUM_ARTIFACTS='7', SKIP_EMPTY_ARTIFACTS=str(skip_empty).lower(), PR_NUMBER='42')
    result = subprocess.run(['node', '-e', harness, json.dumps(data), json.dumps([scripts] if isinstance(scripts, str) else scripts)],
                            env=env, text=True, capture_output=True, timeout=15, check=False)
    if result.returncode:
        raise AssertionError(result.stderr)
    return json.loads(result.stdout)


class AllureResolverRuntimeTests(TestCase):
    def resolve(self, data: dict, **options) -> dict:
        return execute(script(CORE['jobs']['resolve'], 'resolve'), data, **options)

    def test_modes_preserve_source_authoritative_provenance_and_exact_api_scope(self) -> None:
        for mode, materialize, source in [('component', '.allure-input/results', ''),
                                          ('external', '.allure-input/source-artifacts', '.allure-input/source-artifacts')]:
            with self.subTest(mode=mode):
                data = fixture()
                data['artifacts'][0]['name'] = 'allure-results-old-component'
                result = self.resolve(data, mode=mode)
                self.assertEqual(result['failures'], [])
                self.assertEqual(result['outputs']['ready'], 'true')
                self.assertEqual(result['outputs']['materialize-root'], materialize)
                self.assertEqual(result['outputs']['source-artifacts-directory'], source)
                self.assertEqual(json.loads(result['outputs']['artifact_manifest']), [{'name': 'allure-results-old-component', 'id': 1}])
                runs = next(item['params'] for item in result['requests'] if item['operation'] == 'runs')
                self.assertEqual(runs, {'owner': 'example', 'repo': 'project', 'workflow_id': 7,
                                        'event': 'pull_request', 'head_sha': 'abc', 'per_page': 100})
                artifacts = next(item['params'] for item in result['requests'] if item['operation'] == 'artifacts')
                self.assertEqual(artifacts['run_id'], 41)

    def test_identity_current_pr_and_newest_attempt_fail_closed_before_download(self) -> None:
        cases = []
        for field, value in [('name', 'Other workflow'), ('path', '.github/workflows/other.yml'), ('head_sha', '')]:
            data = fixture(); data['run'][field] = value; cases.append((field, data))
        data = fixture(); data['pull']['head']['sha'] = 'new'; cases.append(('changed head', data))
        data = fixture(); data['pull']['base']['repo']['full_name'] = 'other/base'; cases.append(('wrong base', data))
        data = fixture(); data['pulls'] = [data['pull'], copy.deepcopy(data['pull'])]; cases.append(('ambiguous PR', data))
        data = fixture(); data['runs'].append({'id': 42, 'run_attempt': 1}); cases.append(('newer run', data))
        data = fixture(); data['runs'][0]['run_attempt'] = 2; cases.append(('newer attempt', data))
        data = fixture(); data['runs'] = []; cases.append(('no authoritative run', data))
        for name, data in cases:
            with self.subTest(name=name):
                result = self.resolve(data)
                self.assertNotEqual(result['outputs'].get('ready'), 'true')
                self.assertFalse(any(item['operation'] == 'artifacts' for item in result['requests']))

    def test_external_empty_skip_and_partial_contract_failure(self) -> None:
        data = fixture(); data['artifacts'] = []
        self.assertEqual(self.resolve(data, minimum=2, skip_empty=True)['outputs']['ready'], 'false')
        self.assertEqual(self.resolve(data, minimum=0)['outputs']['ready'], 'false')
        self.assertTrue(self.resolve(data, minimum=1)['failures'])
        data = fixture()
        self.assertTrue(self.resolve(data, minimum=2, skip_empty=True)['failures'])
        data['artifacts'] *= 8
        self.assertTrue(self.resolve(data)['failures'])

    def test_artifact_metadata_and_component_contract_reject_unsafe_sets(self) -> None:
        for mutation in ['expired', 'too-big', 'duplicate-name', 'duplicate-id', 'traversal', 'invalid-id', 'invalid-size', 'invalid-component']:
            with self.subTest(mutation=mutation):
                data = fixture(); mode = 'external'
                if mutation == 'expired': data['artifacts'][0]['expired'] = True
                if mutation == 'too-big': data['artifacts'][0]['size_in_bytes'] = 262144001
                if mutation == 'duplicate-name': data['artifacts'].append({**data['artifacts'][0], 'id': 2})
                if mutation == 'duplicate-id': data['artifacts'].append({**data['artifacts'][0], 'name': 'allure-results-two'})
                if mutation == 'traversal': data['artifacts'][0]['name'] = 'allure-results-../escape'
                if mutation == 'invalid-id': data['artifacts'][0]['id'] = 1.5
                if mutation == 'invalid-size': data['artifacts'][0]['size_in_bytes'] = -1
                if mutation == 'invalid-component': data['artifacts'][0]['name'] = 'allure-results-bad.id'; mode = 'component'
                self.assertTrue(self.resolve(data, mode=mode)['failures'])
        data = fixture(); data['artifacts'] = []
        self.assertTrue(self.resolve(data, mode='component')['failures'])

    def test_fork_and_dependabot_sources_allow_safe_comment_but_mark_pages_unsafe(self) -> None:
        for fork, author in [(True, 'author'), (False, 'dependabot[bot]'), (False, 'author')]:
            data = fixture(); data['pull']['user']['login'] = author
            if fork:
                data['pull']['head']['repo']['full_name'] = 'example/fork'
                data['run']['head_repository']['full_name'] = 'example/fork'
            result = self.resolve(data)
            self.assertEqual(result['outputs']['ready'], 'true')
            self.assertEqual(result['outputs']['fork-pr'], str(fork or author == 'dependabot[bot]').lower())


class AllurePublicationRuntimeTests(TestCase):
    def pages(self, data: dict) -> dict:
        return execute(script(PAGES['jobs']['pages'], 'freshness'), data)

    def test_pages_revalidate_exact_report_identity_and_current_same_repository_pr(self) -> None:
        result = self.pages(fixture())
        self.assertEqual(result['outputs']['fresh'], 'true')
        self.assertEqual(json.loads(result['outputs']['artifact_manifest']), [{'name': 'allure-report-99', 'id': 9}])
        request = next(item['params'] for item in result['requests'] if item['operation'] == 'artifacts')
        self.assertEqual(request['run_id'], 99)
        for mutation in ['fork', 'dependabot', 'closed', 'head', 'base', 'rerun', 'attempt', 'missing', 'duplicate', 'expired', 'oversized', 'workflow']:
            with self.subTest(mutation=mutation):
                data = fixture()
                if mutation == 'fork':
                    data['pull']['head']['repo']['full_name'] = 'example/fork'; data['run']['head_repository']['full_name'] = 'example/fork'
                if mutation == 'dependabot': data['pull']['user']['login'] = 'dependabot[bot]'
                if mutation == 'closed': data['pull']['state'] = 'closed'
                if mutation == 'head': data['pull']['head']['sha'] = 'new'
                if mutation == 'base': data['pull']['base']['repo']['full_name'] = 'other/base'
                if mutation == 'rerun': data['runs'].append({'id': 42})
                if mutation == 'attempt': data['runs'][0]['run_attempt'] = 2
                if mutation == 'missing': data['report_artifacts'] = []
                if mutation == 'duplicate': data['report_artifacts'] *= 2
                if mutation == 'expired': data['report_artifacts'][0]['expired'] = True
                if mutation == 'oversized': data['report_artifacts'][0]['size_in_bytes'] = 262144001
                if mutation == 'workflow': data['run']['name'] = 'Wrong workflow'
                self.assertNotEqual(self.pages(data)['outputs'].get('fresh'), 'true')

    def test_pages_recheck_after_extraction_blocks_a_head_changed_during_download(self) -> None:
        data = fixture(); changed = copy.deepcopy(data['pull']); changed['head']['sha'] = 'new'
        data['pull_sequence'] = [data['pull'], changed]
        result = execute([script(PAGES['jobs']['pages'], 'freshness'), script(PAGES['jobs']['pages'], 'publication')], data)
        self.assertEqual(result['phases'][0]['fresh'], 'true')
        self.assertEqual(result['phases'][1]['fresh'], 'false')
        self.assertIn("steps.publication.outputs.fresh == 'true'", PAGES['jobs']['pages']['steps'][-1]['if'])

    def test_comment_forwards_builder_body_and_upserts_only_trusted_author_after_freshness(self) -> None:
        body = 'Trusted compact report\n' + MARKER
        poster = script(CORE['jobs']['comment'], 'Revalidate source freshness and update comment')
        for existing, expected in [([], 'create'), ([{'id': 73, 'user': {'login': 'github-actions[bot]'}, 'body': MARKER}], 'update'),
                                   ([{'id': 73, 'user': {'login': 'author'}, 'body': MARKER}], 'create')]:
            data = fixture(); data['comments'] = existing
            result = execute(poster, data)
            self.assertEqual(len(result['posted']), 1)
            self.assertEqual(result['posted'][0]['kind'], expected)
            self.assertEqual(result['posted'][0]['body'], body)
        for mutation in ['head', 'run', 'attempt', 'missing-marker', 'duplicate-marker', 'not-final-marker']:
            data = fixture()
            if mutation == 'head': data['pull']['head']['sha'] = 'new'
            if mutation == 'run': data['runs'].append({'id': 42})
            if mutation == 'attempt': data['runs'][0]['run_attempt'] = 2
            if mutation == 'missing-marker': data['body'] = 'unmarked'
            if mutation == 'duplicate-marker': data['body'] = MARKER + '\n' + MARKER
            if mutation == 'not-final-marker': data['body'] = MARKER + '\nextra'
            with self.subTest(mutation=mutation):
                self.assertEqual(execute(poster, data)['posted'], [])


HISTORY_SELECTOR_HARNESS = r"""
const fixture = JSON.parse(process.argv[1]);
const body = JSON.parse(process.argv[2]);
const context = {repo: {owner: 'example', repo: 'project'}, runId: 99};
const requests = [], failures = [], notices = [], infos = [];
const outputs = {};
const github = {
  rest: {actions: {
    listArtifactsForRepo: 'repo-artifacts',
    getWorkflowRun: async ({run_id}) => {
      requests.push({operation: 'getWorkflowRun', run_id});
      if (fixture.denied) throw Object.assign(new Error('Resource not accessible by integration'), {status: 403});
      const run = fixture.runs[String(run_id)];
      if (!run) throw Object.assign(new Error('Not Found'), {status: 404});
      return {data: run};
    },
  }},
  paginate: async (operation, params) => {
    requests.push({operation, params});
    if (operation !== 'repo-artifacts') throw Error(`Unexpected API operation: ${operation}`);
    return fixture.artifacts;
  },
};
const core = {setOutput: (name, value) => outputs[name] = value, setFailed: (message) => failures.push(message),
  notice: (message) => notices.push(message), info: (message) => infos.push(message)};
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
new AsyncFunction('github', 'context', 'core', 'process', body)(github, context, core, process)
  .then(() => console.log(JSON.stringify({outputs, failures, notices, infos, requests})),
        (error) => console.log(JSON.stringify({outputs, failures, notices, infos, requests, error: error.message})));
"""


def generate_step(name: str) -> dict:
    return next(step for step in CORE['jobs']['generate']['steps'] if step.get('name') == name)


def history_run(identifier: int, **overrides) -> dict:
    return {'id': identifier, 'workflow_id': 5, 'event': 'workflow_run', 'status': 'completed',
            'conclusion': 'success', **overrides}


def history_artifact(identifier: int, run_id: int, name: str = 'allure-history-pr-42', **overrides) -> dict:
    return {'id': identifier, 'name': name, 'size_in_bytes': 512, 'expired': False,
            'workflow_run': {'id': run_id, 'repository_id': 1, 'head_repository_id': 1}, **overrides}


class AllureHistoryTransportTests(TestCase):
    def select(self, fixture: dict, history_path: str = 'allure-history/history.jsonl', pr_number: str = '42') -> dict:
        fixture = {'runs': {'99': history_run(99, status='in_progress', conclusion=None)}, 'artifacts': [], **fixture}
        env = dict(os.environ, HISTORY_PATH=history_path, PR_NUMBER=pr_number)
        result = subprocess.run(['node', '-e', HISTORY_SELECTOR_HARNESS, json.dumps(fixture),
                                 json.dumps(script(CORE['jobs']['generate'], 'history'))],
                                env=env, text=True, capture_output=True, timeout=15, check=False)
        if result.returncode:
            raise AssertionError(result.stderr)
        return json.loads(result.stdout)

    def test_first_run_without_previous_history_is_an_explicit_new_history(self) -> None:
        outcome = self.select({})
        self.assertEqual(outcome['failures'], [])
        self.assertEqual(outcome['outputs']['found'], 'false')
        self.assertEqual(outcome['outputs']['artifact-name'], 'allure-history-pr-42')
        self.assertIn('this report starts a new history', outcome['notices'][0])
        listing = next(request for request in outcome['requests'] if request['operation'] == 'repo-artifacts')
        self.assertEqual(listing['params']['name'], 'allure-history-pr-42')

    def test_selects_newest_accepted_run_of_the_same_workflow_and_pull_request(self) -> None:
        runs = {'99': history_run(99, status='in_progress', conclusion=None),
                '98': history_run(98, conclusion='cancelled'), '97': history_run(97, workflow_id=6),
                '96': history_run(96, conclusion='failure'), '95': history_run(95, event='push'),
                '93': history_run(93), '90': history_run(90), '120': history_run(120)}
        artifacts = [history_artifact(1, 90), history_artifact(2, 93), history_artifact(3, 95), history_artifact(4, 96),
                     history_artifact(5, 97), history_artifact(6, 98), history_artifact(7, 99), history_artifact(8, 120),
                     history_artifact(9, 94, expired=True), history_artifact(10, 98, name='allure-history-pr-7'),
                     history_artifact(11, 98), history_artifact(12, 92, workflow_run={'id': 92, 'repository_id': 1,
                                                                                    'head_repository_id': 2})]
        outcome = self.select({'runs': runs, 'artifacts': artifacts})
        self.assertEqual(outcome['failures'], [])
        self.assertEqual(outcome['outputs']['found'], 'true')
        self.assertEqual(outcome['outputs']['source-run-id'], '93')
        self.assertEqual(json.loads(outcome['outputs']['artifact_manifest']), [{'name': 'allure-history-pr-42', 'id': 2}])
        inspected = [request['run_id'] for request in outcome['requests'] if request['operation'] == 'getWorkflowRun']
        self.assertEqual(inspected, [99, 98, 97, 96, 95, 93])

    def test_forged_artifact_floods_cost_one_lookup_per_run_and_fork_runs_none(self) -> None:
        runs = {'99': history_run(99, status='in_progress', conclusion=None), '98': history_run(98, event='pull_request')}
        forged = [history_artifact(index, 98) for index in range(100, 400)]
        forked = [history_artifact(index, 97, workflow_run={'id': 97, 'repository_id': 1, 'head_repository_id': 2})
                  for index in range(400, 700)]
        outcome = self.select({'runs': runs, 'artifacts': forged + forked})
        self.assertEqual(outcome['outputs']['found'], 'false')
        inspected = [request['run_id'] for request in outcome['requests'] if request['operation'] == 'getWorkflowRun']
        self.assertEqual(inspected, [99, 98])

    def test_access_errors_and_oversized_history_are_not_reported_as_missing_history(self) -> None:
        denied = self.select({'artifacts': [history_artifact(2, 93)], 'denied': True})
        self.assertIn('Resource not accessible', denied['error'])
        self.assertNotEqual(denied['outputs'].get('found'), 'true')
        self.assertEqual(denied['notices'], [])
        oversized = self.select({'runs': {'99': history_run(99), '93': history_run(93)},
                                 'artifacts': [history_artifact(2, 93, size_in_bytes=52428801)]})
        self.assertIn('exceeds 50 MiB', oversized['failures'][0])
        self.assertEqual(oversized['outputs']['found'], 'false')

    def test_history_path_must_be_a_dedicated_workspace_relative_jsonl(self) -> None:
        for unsafe in ['/tmp/history.jsonl', '../history.jsonl', 'history.jsonl', 'allure-history/../x.jsonl',
                       'allure-report/history.jsonl', '.allure-input/history.jsonl', 'allure-history/history.json',
                       'allure-history/-x.jsonl', 'allure history/history.jsonl']:
            outcome = self.select({}, history_path=unsafe)
            self.assertTrue(outcome['failures'], unsafe)
            self.assertEqual([request for request in outcome['requests'] if request['operation'] == 'repo-artifacts'], [])
        self.assertTrue(self.select({}, pr_number='')['failures'])

    def test_restore_steps_are_gated_bounded_and_read_only(self) -> None:
        names = [step.get('name') for step in CORE['jobs']['generate']['steps']]
        self.assertLess(names.index('Install restored Allure history'), names.index('Build report without write privileges'))
        self.assertLess(names.index('Build report without write privileges'), names.index('Upload updated Allure history'))
        self.assertEqual(CORE['jobs']['generate']['permissions'], {'actions': 'read', 'contents': 'read'})
        restore = generate_step('Restore previous Allure history')
        self.assertEqual(restore['run'], 'python .toolkit/templates/project/template/.github/allure/safe_extract.py.jinja')
        self.assertEqual(restore['env']['ARTIFACT_MANIFEST'], '${{ steps.history.outputs.artifact_manifest }}')
        self.assertNotIn('MATERIALIZE_ROOT', restore['env'])
        self.assertEqual(generate_step('Install restored Allure history')['env']['EXPANDED_ROOT'], restore['env']['OUTPUT_ROOT'])
        for name in ['Check out trusted toolkit extractor', 'Restore previous Allure history', 'Install restored Allure history']:
            self.assertEqual(next(step for step in CORE['jobs']['generate']['steps'] if step.get('name') == name)['if'],
                             "${{ steps.history.outputs.found == 'true' }}")
        upload = generate_step('Upload updated Allure history')
        self.assertEqual(upload['with']['name'], '${{ steps.history.outputs.artifact-name }}')
        self.assertEqual(upload['with']['path'], '${{ inputs.history-path }}')
        self.assertIn("steps.updated.outputs.present == 'true'", upload['if'])
        self.assertEqual(CORE[True]['workflow_call']['inputs']['history-path']['default'], '')

    def run_install(self, expanded: dict[str, str], checkout: dict[str, str | None] | None = None,
                    history_path: str = 'allure-history/history.jsonl') -> tuple[subprocess.CompletedProcess, Path]:
        step = generate_step('Install restored Allure history')
        root = Path(tempfile.mkdtemp(prefix='allure-history-install-'))
        self.addCleanup(shutil.rmtree, root)
        workspace, temporary = root / 'workspace', root / 'expanded'
        for base, files in ((temporary, expanded), (workspace, checkout or {})):
            base.mkdir(parents=True, exist_ok=True)
            for relative, content in files.items():
                target = base / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if content is None:
                    target.symlink_to(root)
                else:
                    target.write_text(content)
        env = dict(os.environ, HISTORY_PATH=history_path, EXPANDED_ROOT=str(temporary), SOURCE_RUN_ID='93',
                   GITHUB_OUTPUT=str(root / 'github-output'))
        result = subprocess.run(['python3', '-c', step['run']], cwd=workspace, env=env, text=True,
                                capture_output=True, timeout=15, check=False)
        return result, root

    def test_restored_history_must_be_one_regular_jsonl_file(self) -> None:
        valid, root = self.run_install({'history.jsonl': '{"uuid": "a"}\n\n{"uuid": "b"}\n'})
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertIn('from history artifact from run 93', valid.stdout)
        self.assertEqual((root / 'workspace/allure-history/history.jsonl').read_text(), '{"uuid": "a"}\n\n{"uuid": "b"}\n')
        self.assertRegex((root / 'github-output').read_text(), r'^sha256=[0-9a-f]{64}\n$')
        corrupted, _ = self.run_install({'history.jsonl': '{"uuid": "a"}\n{"uuid":\n'})
        self.assertIn('line 2', corrupted.stderr)
        self.assertNotEqual(corrupted.returncode, 0)
        not_object, _ = self.run_install({'history.jsonl': '[1, 2]\n'})
        self.assertIn('is not a JSON object', not_object.stderr)
        extra, root = self.run_install({'history.jsonl': '{}\n', 'run.sh': 'exit 0\n'})
        self.assertIn('expected only', extra.stderr)
        self.assertFalse((root / 'workspace/allure-history').exists())
        renamed, _ = self.run_install({'other.jsonl': '{}\n'})
        self.assertIn('expected only history.jsonl', renamed.stderr)

    def test_restore_never_deletes_or_overwrites_checked_out_files(self) -> None:
        sibling, root = self.run_install({'history.jsonl': '{}\n'},
                                         {'tools/allure/allurerc.mjs': 'export default {};\n'},
                                         history_path='tools/allure/history.jsonl')
        self.assertEqual(sibling.returncode, 0, sibling.stderr)
        self.assertEqual((root / 'workspace/tools/allure/allurerc.mjs').read_text(), 'export default {};\n')
        self.assertEqual((root / 'workspace/tools/allure/history.jsonl').read_text(), '{}\n')
        existing, root = self.run_install({'history.jsonl': '{}\n'}, {'allure-history/history.jsonl': 'committed\n'})
        self.assertIn('already exists', existing.stderr)
        self.assertEqual((root / 'workspace/allure-history/history.jsonl').read_text(), 'committed\n')
        linked, root = self.run_install({'history.jsonl': '{}\n'}, {'allure-history': None})
        self.assertIn('Unsafe history-path ancestor', linked.stderr)
        self.assertEqual(list(root.glob('history.jsonl')), [])

    def run_check(self, setup, restored_sha256: str = '') -> tuple[subprocess.CompletedProcess, str]:
        step = generate_step('Check updated Allure history')
        workspace = Path(tempfile.mkdtemp(prefix='allure-history-check-'))
        self.addCleanup(shutil.rmtree, workspace)
        setup(workspace)
        output = workspace / 'github-output'
        env = dict(os.environ, HISTORY_PATH='allure-history/history.jsonl', GITHUB_OUTPUT=str(output),
                   RESTORED_SHA256=restored_sha256)
        result = subprocess.run(['bash', '-c', step['run']], cwd=workspace, env=env, text=True,
                                capture_output=True, timeout=15, check=False)
        return result, output.read_text() if output.exists() else ''

    def test_persistence_respects_disabled_history_and_rejects_symlinks(self) -> None:
        def written(workspace: Path) -> None:
            (workspace / 'allure-history').mkdir()
            (workspace / 'allure-history/history.jsonl').write_text('{}\n')
        result, output = self.run_check(written)
        self.assertEqual((result.returncode, output), (0, 'present=true\n'))
        self.assertNotIn('unchanged', result.stdout)
        unchanged = hashlib.sha256(b'{}\n').hexdigest()
        result, output = self.run_check(written, restored_sha256=unchanged)
        self.assertEqual((result.returncode, output), (0, 'present=true\n'))
        self.assertIn('left the restored history at allure-history/history.jsonl unchanged', result.stdout)
        result, output = self.run_check(written, restored_sha256='0' * 64)
        self.assertNotIn('unchanged', result.stdout)
        result, output = self.run_check(lambda workspace: None)
        self.assertEqual((result.returncode, output), (0, 'present=false\n'))
        self.assertIn('Allure wrote no history', result.stdout)

        def linked(workspace: Path) -> None:
            (workspace / 'allure-history').mkdir()
            (workspace / 'allure-history/history.jsonl').symlink_to('/etc/hosts')
        result, output = self.run_check(linked)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output, '')

    def test_template_config_and_caller_share_one_history_path(self) -> None:
        config = (ROOT / 'templates/project/template/.github/allure/allurerc.mjs.jinja').read_text()
        self.assertIn('historyPath: "./allure-history/history.jsonl",', config)
        self.assertIn('historyLimit: 20,', config)
        self.assertNotIn('appendHistory', config)
        legacy = yaml.safe_load((ROOT / '.github/workflows/allure-publisher.yml').read_text())
        self.assertEqual(legacy[True]['workflow_call']['inputs']['history-path']['default'], '')
        self.assertEqual(legacy['jobs']['report']['with']['history-path'], '${{ inputs.history-path }}')


class AllureCallerContracts(TestCase):
    def test_forty_generated_variants_delegate_exact_inputs_and_no_broad_token(self) -> None:
        template = jinja2.Environment().from_string((ROOT / 'templates/project/template/.github/workflows/allure-report.yml.jinja').read_text())
        with tempfile.TemporaryDirectory(prefix='allure-callers-') as directory:
            for component in [False, True]:
                for pages in [False, True]:
                    for name in ['Validate', 'Run tests', 'CI: build', 'A "quoted" name', 'Workflow with spaces']:
                        for category in ['', '.github/allure/categories.json']:
                            text = template.render(toolkit_version='v99.0.0', components=[{'id': 'current-id'}] if component else [],
                                allure_external_workflow_name=name, allure_external_workflow_path='.github/workflows/test.yml',
                                allure_external_artifact_prefix='external-allure-', allure_external_artifact_min_count=2,
                                allure_external_artifact_max_count=7, allure_categories_file=category,
                                allure_publish_pages=pages, allure_pages_url='https://example.org/reports')
                            data = yaml.safe_load(text); jobs = data['jobs']; report = jobs['report']
                            self.assertEqual(set(jobs), {'report', 'pages'} if pages else {'report'})
                            self.assertTrue(all('steps' not in job and 'secrets' not in job for job in jobs.values()))
                            self.assertEqual(report['uses'], 'quokkify/project-toolkit/.github/workflows/allure-publisher-core.yml@v99.0.0')
                            self.assertEqual(report['permissions'], {'actions': 'read', 'contents': 'read', 'pull-requests': 'write'})
                            self.assertEqual(report['with'], {
                                'source-workflow': 'Validate' if component else name,
                                'source-workflow-path': '.github/workflows/validate.yml' if component else '.github/workflows/test.yml',
                                'artifact-prefix': 'allure-results-' if component else 'external-allure-',
                                'artifact-mode': 'component' if component else 'external', 'minimum-artifacts': 1 if component else 2,
                                'maximum-artifacts': 50 if component else 7, 'skip-empty-artifacts': not component,
                                'config-file': '.github/allure/allurerc.mjs', 'categories-file': category,
                                'publish-pages': pages, 'managed-pages-layout': True,
                                'history-path': 'allure-history/history.jsonl',
                                'pages-url': 'https://example.org/reports' if pages else ''})
                            if pages:
                                self.assertEqual(jobs['pages']['permissions'], {'actions': 'read', 'contents': 'write', 'pull-requests': 'read'})
                                self.assertTrue(jobs['pages']['with']['managed-pages-layout'])
                                self.assertEqual(jobs['pages']['with']['pr-number'], '${{ needs.report.outputs.pr-number }}')
                            path = Path(directory) / 'allure.yml'; path.write_text(text)
                            self.assertIsNotNone(shutil.which('actionlint'), 'actionlint is required for rendered callers')
                            subprocess.run(['actionlint', str(path)], capture_output=True, text=True, timeout=15, check=True)

    def test_core_permissions_token_and_legacy_api_pages_layout_are_preserved(self) -> None:
        self.assertTrue(all('write' not in job['permissions'].values() or job == CORE['jobs']['comment'] for job in CORE['jobs'].values()))
        self.assertTrue(all(job['permissions'].get('contents') != 'write' for job in CORE['jobs'].values()))
        for source in [CORE, PAGES]:
            self.assertFalse(source[True]['workflow_call']['secrets']['github-token']['required'])
            for job in source['jobs'].values():
                for step in job['steps']:
                    token = step.get('with', {}).get('github-token')
                    if token:
                        self.assertEqual(token, '${{ secrets.github-token || github.token }}')
                    if step.get('name') == 'Check out trusted toolkit extractor':
                        self.assertEqual(step['with']['repository'], '${{ job.workflow_repository }}')
                        self.assertEqual(step['with']['ref'], '${{ job.workflow_sha }}')
        legacy = yaml.safe_load((ROOT / '.github/workflows/allure-publisher.yml').read_text())
        api = legacy[True]['workflow_call']['inputs']
        for name in ['source-workflow', 'source-workflow-path', 'artifact-prefix', 'minimum-artifacts', 'maximum-artifacts',
                     'config-file', 'categories-file', 'pages-url', 'publish-pages', 'pages-destination-directory']:
            self.assertIn(name, api)
        self.assertEqual(api['artifact-mode']['default'], 'external')
        self.assertFalse(api['managed-pages-layout']['default'])
        self.assertEqual(legacy['jobs']['report']['uses'], './.github/workflows/allure-publisher-core.yml')
        self.assertEqual(legacy['jobs']['pages']['uses'], './.github/workflows/allure-pages.yml')
        self.assertEqual(PAGES['jobs']['pages']['steps'][-1]['with']['publish-dir'],
                         "${{ inputs.managed-pages-layout && 'allure-report' || 'allure-report/allure-report' }}")
        builder = next(step for step in CORE['jobs']['generate']['steps'] if step.get('name') == 'Build report without write privileges')
        self.assertEqual(builder['with']['comment-marker'], MARKER)

    def test_released_template_fresh_copies_bind_the_selected_tag_and_updates_preserve_answers(self) -> None:
        copier = shutil.which('copier')
        self.assertIsNotNone(copier, 'Copier is a required release migration test dependency')
        with tempfile.TemporaryDirectory(prefix='allure-release-default-') as directory:
            source = Path(directory) / 'source'
            shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns('.git', '__pycache__'))
            def git(args, cwd=source):
                subprocess.run(['git', '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid', *args],
                               cwd=cwd, capture_output=True, text=True, check=True, timeout=30)
            git(['init', '--quiet']); git(['add', '.']); git(['commit', '-qm', 'fixture']); git(['tag', 'v9.1.0'])
            for explicit in [False, True]:
                target = Path(directory) / ('explicit' if explicit else 'automatic')
                command = [copier, 'copy', '--trust', '--defaults', '--data', 'allure_report=true',
                           '--data', 'release_please=false', '--data', 'renovate=false']
                if explicit:
                    command += ['--vcs-ref', 'v9.1.0']
                subprocess.run([*command, str(source), str(target)], check=True, capture_output=True, text=True, timeout=30)
                answers = yaml.safe_load((target / '.copier-answers.yml').read_text())
                self.assertEqual(answers['_commit'], 'v9.1.0')
                self.assertEqual(answers['toolkit_version'], 'v9.1.0')
                self.assertIn('allure-publisher-core.yml@v9.1.0', (target / '.github/workflows/allure-report.yml').read_text())
            target = Path(directory) / 'explicit'
            git(['init', '--quiet'], cwd=target); git(['add', '.'], cwd=target); git(['commit', '-qm', 'consumer'], cwd=target)
            (source / 'README.md').write_text((source / 'README.md').read_text() + '\nFixture second release.\n')
            git(['add', '.']); git(['commit', '-qm', 'second release']); git(['tag', 'v9.2.0'])
            subprocess.run([copier, 'update', '--trust', '--defaults', '--vcs-ref', 'v9.2.0', '--data', 'toolkit_version=v9.2.0'],
                           cwd=target, check=True, capture_output=True, text=True, timeout=30)
            answers = yaml.safe_load((target / '.copier-answers.yml').read_text())
            self.assertEqual(answers['_commit'], 'v9.2.0')
            self.assertEqual(answers['toolkit_version'], 'v9.2.0')

    def test_legacy_allure_update_rejects_retained_pin_before_writes(self) -> None:
        copier = shutil.which('copier')
        self.assertIsNotNone(copier, 'Copier is a required release migration test dependency')
        with tempfile.TemporaryDirectory(prefix='allure-legacy-update-') as directory:
            source = Path(directory) / 'source'
            shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns('.git', '__pycache__'))
            def git(args, cwd=source):
                return subprocess.run(['git', '-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid', *args],
                                      cwd=cwd, capture_output=True, text=True, check=True, timeout=30)
            # Actual released questions and inline workflow, with no new endpoints.
            for path in ['copier.yml', 'templates/project/template/.github/workflows/allure-report.yml.jinja']:
                original = git(['show', 'v2.23.5:' + path], cwd=ROOT).stdout
                (source / path).write_text(original)
            for path in ['allure-publisher-core.yml', 'allure-pages.yml']:
                (source / '.github/workflows' / path).unlink()
            git(['init', '--quiet']); git(['add', '.']); git(['commit', '-qm', 'legacy release']); git(['tag', 'v2.23.5'])
            for enabled in [True, False]:
                target = Path(directory) / ('allure' if enabled else 'ordinary')
                subprocess.run([copier, 'copy', '--trust', '--defaults', '--vcs-ref', 'v2.23.5',
                                '--data', 'allure_report=' + str(enabled).lower(), '--data', 'toolkit_version=v2.23.5', '--data', 'release_please=false',
                                '--data', 'renovate=false', str(source), str(target)],
                               check=True, capture_output=True, text=True, timeout=30)
                git(['init', '--quiet'], cwd=target); git(['add', '.'], cwd=target); git(['commit', '-qm', 'consumer'], cwd=target)
            shutil.copytree(ROOT, source, dirs_exist_ok=True, ignore=shutil.ignore_patterns('.git', '__pycache__'))
            git(['add', '.']); git(['commit', '-qm', 'shared Allure release']); git(['tag', 'v9.3.0'])
            fresh = Path(directory) / 'invalid-fresh'
            fresh.mkdir()
            failed_copy = subprocess.run([copier, 'copy', '--trust', '--defaults', '--vcs-ref', 'v9.3.0',
                                          '--data', 'allure_report=true', '--data', 'toolkit_version=v2.23.5',
                                          str(source), str(fresh)], capture_output=True, text=True, timeout=30)
            self.assertNotEqual(failed_copy.returncode, 0)
            self.assertIn('--data toolkit_version=v9.3.0', failed_copy.stdout + failed_copy.stderr)
            self.assertEqual(list(fresh.iterdir()), [])
            target = Path(directory) / 'allure'
            before = {str(path.relative_to(target)): path.read_bytes() for path in target.rglob('*')
                      if path.is_file() and '.git' not in path.relative_to(target).parts}
            command = [copier, 'update', '--trust', '--defaults', '--vcs-ref', 'v9.3.0']
            failed = subprocess.run(command, cwd=target, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn('--data toolkit_version=v9.3.0', failed.stdout + failed.stderr)
            after = {str(path.relative_to(target)): path.read_bytes() for path in target.rglob('*')
                     if path.is_file() and '.git' not in path.relative_to(target).parts}
            self.assertEqual(after, before)
            subprocess.run([*command, '--data', 'toolkit_version=v9.3.0'], cwd=target,
                           check=True, capture_output=True, text=True, timeout=30)
            self.assertIn('allure-publisher-core.yml@v9.3.0', (target / '.github/workflows/allure-report.yml').read_text())
            ordinary = Path(directory) / 'ordinary'
            subprocess.run(command, cwd=ordinary, check=True, capture_output=True, text=True, timeout=30)
            answers = yaml.safe_load((ordinary / '.copier-answers.yml').read_text())
            self.assertEqual(answers['toolkit_version'], 'v2.23.5')
            self.assertFalse(answers['allure_report'])

    def test_validator_contracts_retain_executable_negative_probes(self) -> None:
        tree = ast.parse((ROOT / 'scripts/validate.py').read_text())
        names = {'allure_publisher_workflow_errors', 'allure_publisher_negative_probes', 'allure_pages_workflow_errors', 'allure_legacy_wrapper_errors'}
        selected = ast.Module(body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names], type_ignores=[])
        import re
        namespace = {'ROOT': ROOT, 'Path': Path, 'yaml': yaml, 're': re, 'tempfile': tempfile, 'json': json,
                     'RESOLVE_SCRIPT_ACTION': 'actions/github-script', 'GENERATE_REPORT_ACTION': 'quokkify/allure-report-action'}
        exec(compile(selected, '<allure-validators>', 'exec'), namespace)
        self.assertEqual(namespace['allure_publisher_workflow_errors'](CORE_PATH), [])
        self.assertEqual(namespace['allure_publisher_negative_probes'](CORE_PATH), [])
        self.assertEqual(namespace['allure_pages_workflow_errors'](PAGES_PATH), [])
        self.assertEqual(namespace['allure_legacy_wrapper_errors'](ROOT / '.github/workflows/allure-publisher.yml'), [])


if __name__ == '__main__':
    main()

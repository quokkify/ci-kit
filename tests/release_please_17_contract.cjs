#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

const releasePleaseRoot = process.argv[2];
const releaseBodyPath = process.argv[3];
const toolkitConfigPath = process.argv[4];
const generatedConfigPath = process.argv[5];
// Rendered single-package release PR bodies: each must still produce exactly one
// GitHub release whose notes keep the collapsed Internal list verbatim.
const singleBodyPaths = process.argv.slice(6);
if (!releasePleaseRoot || !releaseBodyPath || !toolkitConfigPath || !generatedConfigPath) {
  throw new Error('usage: release_please_17_contract.cjs RELEASE_PLEASE_ROOT BODY TOOLKIT_CONFIG GENERATED_CONFIG [SINGLE_BODY...]');
}

const load = relative => require(path.join(releasePleaseRoot, relative));
const packageJson = JSON.parse(fs.readFileSync(path.join(releasePleaseRoot, 'package.json'), 'utf8'));
if (packageJson.version !== '17.6.0') {
  throw new Error(`expected release-please 17.6.0, got ${packageJson.version}`);
}

const {PullRequestBody} = load('build/src/util/pull-request-body.js');
const {Version} = load('build/src/version.js');
const {TagName} = load('build/src/util/tag-name.js');
const {BranchName} = load('build/src/util/branch-name.js');
const {DefaultChangelogNotes} = load('build/src/changelog-notes/default.js');

(async () => {
  const body = fs.readFileSync(releaseBodyPath, 'utf8');
  const parsed = PullRequestBody.parse(body);
  if (!parsed || parsed.releaseData.length !== 3) {
    throw new Error('Release Please did not parse all three component releases');
  }
  const expected = {backend: 'Backend', frontend: 'run();', worker: 'Upgrade worker'};
  for (const release of parsed.releaseData) {
    if (!expected[release.component] || !release.notes.includes(expected[release.component])) {
      throw new Error(`rich notes missing from parsed ${release.component || 'unknown'} release`);
    }
    // textContent flattens the nested Internal list; the sync step restores it.
    if (!release.notes.includes('Internal changes')) {
      throw new Error(`internal changes missing from parsed ${release.component} release`);
    }
  }

  const quiet = {warn() {}, error() {}, info() {}, debug() {}, trace() {}};
  const singles = [];
  for (const singlePath of singleBodyPaths) {
    const singleBody = fs.readFileSync(singlePath, 'utf8');
    const single = PullRequestBody.parse(singleBody, quiet);
    if (!single || single.releaseData.length !== 1 || single.releaseData[0].component) {
      throw new Error(`${singlePath}: expected exactly one componentless release`);
    }
    const [release] = single.releaseData;
    const heading = singleBody.match(/^## \[?(\d+\.\d+\.\d+)/m);
    if (!heading || release.version.toString() !== heading[1]) {
      throw new Error(`${singlePath}: release version does not match the notes heading`);
    }
    const lines = singleBody.trim().split('\n');
    const content = lines.slice(lines.indexOf('---') + 1, lines.lastIndexOf('---')).join('\n').trim();
    if (release.notes !== content) {
      throw new Error(`${singlePath}: GitHub release notes differ from the release PR notes`);
    }
    if (!release.notes.includes('<details><summary>Internal changes') ||
        !release.notes.includes('<!-- project-toolkit:release-notes v2 -->')) {
      throw new Error(`${singlePath}: rendered notes lost the marker or the collapsed list`);
    }
    singles.push(release.version.toString());
  }

  // Negative control: a summary ending in a version triple is read as a
  // component release, which would leave the single-package release unpublished.
  const hijacked = PullRequestBody.parse([
    ':robot: I have created a release *beep* *boop*', '---', '',
    '## [1.2.0](https://github.com/acme/widget/compare/v1.1.0...v1.2.0) (2026-11-07)', '',
    '<details><summary>Dependencies (renovate 44.192.2 → 44.221.1)</summary>', '', '- x', '', '</details>', '',
    '---', 'footer',
  ].join('\n'), quiet);
  if (!hijacked || hijacked.releaseData.length !== 1 || !hijacked.releaseData[0].component) {
    throw new Error('expected a version-bearing summary to be parsed as a component release');
  }

  const generatedConfig = JSON.parse(fs.readFileSync(generatedConfigPath, 'utf8'));
  const generatedPackage = generatedConfig.packages['.'];
  // Importing strategies/simple.js directly triggers a circular dependency in
  // the 17.6.0 distribution.  The public config contract is equivalent here:
  // no package-name plus include-component-in-tag=false means componentless
  // tag and branch identities for the root package.
  const component = generatedPackage['package-name'];
  const branchComponent = component;
  const version = Version.parse('4.5.7');
  const tag = new TagName(version, component || undefined).toString();
  const branch = branchComponent
    ? BranchName.ofComponentTargetBranch(branchComponent, 'main').toString()
    : BranchName.ofTargetBranch('main').toString();
  if (tag !== 'v4.5.7' || branch !== 'release-please--branches--main') {
    throw new Error(`single identity changed: tag=${tag} branch=${branch}`);
  }

  const toolkitConfig = JSON.parse(fs.readFileSync(toolkitConfigPath, 'utf8'));
  const sections = toolkitConfig.packages['.']['changelog-sections'];
  const choreSection = sections.find(section => section.type === 'chore');
  if (!choreSection || choreSection.section !== '🧹 Chores' || choreSection.hidden) {
    throw new Error('chore commits must remain visible in the Chores section');
  }
  const commits = [
    {type: 'deps', scope: 'github-actions', bareMessage: 'update dependency alpha', message: 'deps(github-actions): update dependency alpha', sha: 'a'.repeat(40), notes: [], references: []},
    {type: 'chore', scope: 'deps', bareMessage: 'legacy dependency update', message: 'chore(deps): legacy dependency update', sha: 'b'.repeat(40), notes: [], references: []},
    {type: 'chore', scope: 'maintenance', bareMessage: 'reorganize scripts', message: 'chore(maintenance): reorganize scripts', sha: 'c'.repeat(40), notes: [], references: []},
    {type: 'chore', scope: null, bareMessage: 'internal cleanup', message: 'chore: internal cleanup', sha: 'd'.repeat(40), notes: [], references: []},
  ];
  const rendered = await new DefaultChangelogNotes().buildNotes(commits, {
    owner: 'acme', repository: 'widget', version: '1.2.3',
    currentTag: 'v1.2.3', changelogSections: sections,
  });
  if (!rendered.includes('🧹 Chores') || !rendered.includes('update dependency alpha')) {
    throw new Error('deps commit did not render in Chores');
  }
  for (const message of ['legacy dependency update', 'reorganize scripts', 'internal cleanup']) {
    if (!rendered.includes(message)) {
      throw new Error(`${message} did not enter the staging section`);
    }
  }

  process.stdout.write(JSON.stringify({
    releasePlease: packageJson.version,
    components: parsed.releaseData.map(release => release.component),
    singles,
    tag,
    branch,
    dependencies: true,
  }) + '\n');
})().catch(error => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});

#!/usr/bin/env node
'use strict';

const fs = require('fs');
const path = require('path');

const releasePleaseRoot = process.argv[2];
const configPath = process.argv[3];
if (!releasePleaseRoot || !configPath) {
  throw new Error('usage: release_please_17_notes.cjs RELEASE_PLEASE_ROOT CONFIG');
}

const packageJson = JSON.parse(
  fs.readFileSync(path.join(releasePleaseRoot, 'package.json'), 'utf8'),
);
if (packageJson.version !== '17.6.0') {
  throw new Error(`expected release-please 17.6.0, got ${packageJson.version}`);
}

const {DefaultChangelogNotes} = require(
  path.join(releasePleaseRoot, 'build/src/changelog-notes/default.js'),
);
const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
const sections = config.packages['.']['changelog-sections'];
const commits = [
  {
    type: 'refactor', scope: null,
    bareMessage: 'delegate PR summaries to upstream Allure CI',
    message: 'refactor: delegate PR summaries to upstream Allure CI',
    sha: 'd'.repeat(40), notes: [], references: [],
  },
  {
    type: 'chore',
    scope: 'deps',
    bareMessage: 'legacy dependency update',
    message: 'chore(deps): legacy dependency update',
    sha: 'a'.repeat(40),
    notes: [],
    references: [],
  },
  {
    type: 'chore',
    scope: null,
    bareMessage: 'internal cleanup',
    message: 'chore: internal cleanup',
    sha: 'b'.repeat(40),
    notes: [],
    references: [],
  },
  {
    type: 'chore',
    scope: 'maintenance',
    bareMessage: 'reorganize scripts',
    message: 'chore(maintenance): reorganize scripts',
    sha: 'd'.repeat(40),
    notes: [],
    references: [],
  },
  {
    type: 'deps',
    scope: 'deps',
    bareMessage: 'native dependency update',
    message: 'deps(deps): native dependency update',
    sha: 'c'.repeat(40),
    notes: [],
    references: [],
  },
];

(async () => {
  const rendered = await new DefaultChangelogNotes().buildNotes(commits, {
    owner: 'acme',
    repository: 'widget',
    version: '1.2.3',
    currentTag: 'v1.2.3',
    changelogSections: sections,
  });
  if (!rendered.includes('♻️ Refactoring') || !rendered.includes('delegate PR summaries')) {
    throw new Error('refactor must produce a user-facing release note');
  }
  if (!rendered.includes('🧹 Chores')) {
    throw new Error('chore commits must remain visible in Chores');
  }
  if (!rendered.includes('📦 Dependencies')) {
    throw new Error('deps(deps) did not render in Dependencies');
  }
  if (!rendered.includes('native dependency update')) {
    throw new Error('deps(deps) message did not render');
  }
  if (!rendered.includes('legacy dependency update')) {
    throw new Error('chore(deps) must remain visible for release enrichment');
  }
  if (!rendered.includes('internal cleanup') || !rendered.includes('reorganize scripts')) {
    throw new Error('all chore scopes must remain visible in the release notes');
  }
  process.stdout.write(JSON.stringify({releasePlease: packageJson.version, dependencies: true}) + '\n');
})().catch(error => {
  console.error(error.stack || error.message);
  process.exitCode = 1;
});

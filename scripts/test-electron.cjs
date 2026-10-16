'use strict';

// These regressions use fixtures/mocks and production geometry; no user apps,
// model credentials, live music client or user profile is required.
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const tests = [
  'v86-windows-encoding.cjs',
  'v86-partial-discovery.cjs',
  'v86-music-rows.cjs',
  'v87-music-resolver.cjs',
  'v87-layout-unit.cjs',
  'v85-submission-unit.cjs',
  'clean-character-ownership.cjs',
  'v84-audio-queue-unit.cjs',
  'v89-layout-unit.cjs',
  'url-policy-unit.cjs',
  'app-launcher-unit.cjs',
  'trace-unit.cjs',
  'evaluation-unit.cjs',
  'recovery-unit.cjs',
];

for (const test of tests) {
  const result = spawnSync(process.execPath, [path.join(__dirname, '../tests/ui', test)], {
    stdio: 'inherit',
    env: process.env,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status || 1);
}
console.log(JSON.stringify({ passed_groups: tests.length, real_gui_acceptance: false }));

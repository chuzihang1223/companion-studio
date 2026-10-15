'use strict';

// Pure launcher regressions: no Windows programs, user profiles or model services run.
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const path = require('node:path');
const test = require('node:test');
const { AppLauncher, AppLaunchVerifier } = require('../../desktop/electron/app-launcher.cjs');

const empty = () => ({ processes: [], windows: [] });
const ready = () => ({
  processes: [{ pid: 321, path: process.execPath }],
  windows: [
    {
      pid: 321,
      handle: 654,
      title: 'Launcher fixture',
      bounds: [10, 10, 640, 480],
      visible: true,
      responding: true,
    },
  ],
});

function harness({ observations = [empty()], spawnError } = {}) {
  const launches = [];
  const recorded = [];
  const focused = [];
  let observation = 0;
  let unrefCount = 0;
  const record = {
    id: 'fixture',
    app_id: 'fixture',
    name: 'Launcher fixture',
    executable: process.execPath,
    launch_candidates: [
      {
        id: 'primary',
        launch_type: 'exe',
        target: process.execPath,
        arguments: '"sample folder\\a&b.txt" "--label=$(literal)"',
        working_directory: path.dirname(process.execPath),
      },
    ],
  };
  const controller = {
    async observe_app(id) {
      assert.equal(id, record.id);
      return observations[Math.min(observation++, observations.length - 1)];
    },
    async focus_window(id, handle) {
      focused.push({ id, handle });
      return { focused: true };
    },
    async verify_window_state(id, handle) {
      assert.equal(id, record.id);
      assert.equal(handle, 654);
      return { visible: true, responding: true };
    },
  };
  const launcher = new AppLauncher({
    discovery: {
      find: () => record,
      recordLaunch: (...args) => recorded.push(args),
    },
    shell: {
      openPath: () => assert.fail('An executable must not use shell.openPath'),
      openExternal: () => assert.fail('An executable must not use shell.openExternal'),
    },
    controller,
    spawnProcess: (target, args, options) => {
      launches.push({ target, args, options });
      const child = new EventEmitter();
      child.pid = 321;
      child.unref = () => unrefCount++;
      queueMicrotask(() => child.emit(spawnError ? 'error' : 'spawn', spawnError));
      return child;
    },
  });
  return { launcher, record, launches, recorded, focused, unrefCount: () => unrefCount };
}

test('external GUI launch is visible and preserves shell-free argv and working directory', async () => {
  const fixture = harness();
  const target = process.execPath;
  const args = ['sample folder\\a&b.txt', '--label=$(literal)'];
  const cwd = path.dirname(target);
  assert.equal(await fixture.launcher.spawnEntry(target, args, cwd), 321);
  assert.deepEqual(fixture.launches, [
    {
      target,
      args,
      options: { cwd, windowsHide: false, detached: true, stdio: 'ignore', shell: false },
    },
  ]);
  assert.equal(fixture.unrefCount(), 1);
});

test('spawn errors reject without detaching an unstarted child', async () => {
  const error = Object.assign(new Error('Fixture executable unavailable'), { code: 'ENOENT' });
  const fixture = harness({ spawnError: error });
  await assert.rejects(fixture.launcher.spawnEntry(process.execPath, [], undefined), error);
  assert.equal(fixture.unrefCount(), 0);
});

test('the real launch path verifies and focuses an observed responsive application window', async () => {
  // Authorization remains the caller's responsibility; this exercises the launcher after approval.
  const fixture = harness({ observations: [empty(), ready()] });
  const stages = [];
  const result = await fixture.launcher.launch(fixture.record, {
    onProgress: async (stage) => stages.push(stage),
    targetText: 'fixture target',
  });
  assert.equal(result.verified, true);
  assert.equal(result.window_ready, true);
  assert.equal(result.focused, true);
  assert.equal(result.status, 'running');
  assert.equal(result.window.handle, 654);
  assert.equal(fixture.launches.length, 1);
  assert.deepEqual(fixture.launches[0].args, ['sample folder\\a&b.txt', '--label=$(literal)']);
  assert.deepEqual(fixture.focused, [{ id: 'fixture', handle: 654 }]);
  assert.equal(fixture.recorded.length, 1);
  assert.equal(fixture.recorded[0][2], true);
  assert.ok(stages.includes('window_ready'));
  assert.equal(fixture.launcher.pending.size, 0);
});

test('a submitted process without a ready window fails and does not reopen a fallback', async (t) => {
  const fixture = harness();
  fixture.record.launch_candidates.push({
    ...fixture.record.launch_candidates[0],
    id: 'fallback',
  });
  // Replace only the polling wait to avoid ten seconds of wall time. Its result preserves
  // production semantics: a process can be observed without a verified usable window.
  t.mock.method(AppLaunchVerifier.prototype, 'wait', async () => ({
    processes: [{ pid: 321, path: process.execPath }],
    windows: [],
    window: null,
    verified: true,
    window_ready: false,
  }));
  const result = await fixture.launcher.launch(fixture.record);
  assert.equal(result.status, 'launch_failed');
  assert.equal(result.code, 'APP_NOT_READY');
  assert.equal(result.verified, false);
  assert.equal(fixture.launches.length, 1);
  assert.equal(fixture.focused.length, 0);
  assert.equal(fixture.recorded.length, 1);
  assert.equal(fixture.recorded[0][2], false);
  assert.equal(fixture.launcher.pending.size, 0);
});

test('cancellation after submitting the launch propagates and releases the pending task', async () => {
  const fixture = harness();
  let checkpoints = 0;
  await assert.rejects(
    fixture.launcher.launch(fixture.record, {
      checkpoint: async () => {
        if (++checkpoints === 2) throw new Error('task_cancelled');
      },
    }),
    /task_cancelled/,
  );
  assert.equal(fixture.launches.length, 1);
  assert.equal(fixture.recorded.length, 0);
  assert.equal(fixture.launcher.pending.size, 0);
});

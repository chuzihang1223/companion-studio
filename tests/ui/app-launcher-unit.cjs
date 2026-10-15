'use strict';

// Pure launcher regressions: no Windows programs, user profiles or model services run.
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');
const test = require('node:test');
const { AppLauncher, AppLaunchVerifier } = require('../../desktop/electron/app-launcher.cjs');
const { normalizeRecord } = require('../../desktop/electron/application-record.cjs');

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

const notepadAumid = 'Microsoft.WindowsNotepad_8wekyb3d8bbwe!App';

function packagedNotepad(overrides = {}) {
  return normalizeRecord({
    app_id: 'fixture_packaged_notepad',
    name: '记事本',
    source: 'start_apps',
    app_user_model_id: notepadAumid,
    application_kind: 'registered_shell',
    ...overrides,
  });
}

function knownAppsFixture(rows, direct = {}) {
  const filename = require.resolve('../../desktop/electron/known-apps.cjs');
  const loaded = new Module(filename, module);
  loaded.paths = Module._nodeModulePaths(path.dirname(filename));
  const fromFile = Module.createRequire(filename);
  const fixtureFs = Object.create(fs);
  // Registry I/O and discovery are replaced; production detection and normalization run.
  fixtureFs.readFileSync = () => {
    throw Object.assign(new Error('No fixture registry'), { code: 'ENOENT' });
  };
  fixtureFs.existsSync = () => true;
  const discovery = {
    find: (id) => direct[id],
    list: () => {
      assert.equal(
        Object.keys(direct).length,
        0,
        'Direct ID lookup must not enumerate the catalog',
      );
      return rows;
    },
  };
  loaded.require = (id) => {
    if (id === 'node:fs') return fixtureFs;
    if (id === 'electron') return { shell: {} };
    if (id === './paths.cjs') return { userData: 'fixture-user-data' };
    if (id === './application-discovery.cjs')
      return {
        ApplicationDiscoveryService: class {
          constructor() {
            return discovery;
          }
        },
      };
    return fromFile(id);
  };
  loaded._compile(fs.readFileSync(filename, 'utf8'), filename);
  return loaded.exports;
}

test('known Notepad preserves an installed AppsFolder identity without inventing an EXE', () => {
  const packaged = packagedNotepad();
  const known = knownAppsFixture([packaged]);
  const found = known.detect('notepad');
  assert.equal(found.id, 'notepad');
  assert.equal(found.app_id, packaged.app_id);
  assert.equal(found.app_user_model_id, notepadAumid);
  assert.equal(found.path, null);
  assert.deepEqual(found.launch_candidates, packaged.launch_candidates);
  assert.equal(found.launch_type, 'shell_app');
  const forLaunch = normalizeRecord(found);
  assert.equal(forLaunch.app_user_model_id, notepadAumid);
  assert.equal(forLaunch.launch_candidates[0].target, notepadAumid);
  assert.equal(known.info('notepad').app_user_model_id, notepadAumid);
});

test('registered modern Notepad identity precedes a legacy EXE basename match', () => {
  const classic = normalizeRecord({
    app_id: 'fixture_classic',
    name: 'Classic Notepad fixture',
    executable: path.win32.join(process.env.WINDIR || 'C:\\Windows', 'System32', 'notepad.exe'),
    source: 'system',
  });
  const packaged = packagedNotepad();
  assert.equal(knownAppsFixture([classic, packaged]).detect('notepad').app_id, packaged.app_id);
  assert.equal(knownAppsFixture([classic]).detect('notepad').app_id, classic.app_id);
});

test('display-name lookalikes, other packages and uninstalled identities cannot become Notepad', () => {
  const identities = [
    'OtherPublisher.WindowsNotepad_8wekyb3d8bbwe!App',
    'prefix.' + notepadAumid,
    notepadAumid + '.suffix',
    'Microsoft.WindowsNotepad_wrongpublisher!App',
  ];
  for (const identity of identities) {
    const row = packagedNotepad({ app_user_model_id: identity });
    const found = knownAppsFixture([row]).detect('notepad');
    assert.equal(found.app_user_model_id, undefined, identity);
    assert.notEqual(found.app_id, row.app_id, identity);
  }
  const missing = packagedNotepad({ installed: false });
  assert.equal(knownAppsFixture([missing]).detect('notepad').app_user_model_id, undefined);
});

test('an explicitly resolved application ID keeps precedence over the builtin package alias', () => {
  const direct = normalizeRecord({
    app_id: 'notepad',
    name: 'Explicit fixture selection',
    executable: process.execPath,
    source: 'custom',
  });
  const found = knownAppsFixture([packagedNotepad(), direct], { notepad: direct }).detect(
    'notepad',
  );
  assert.equal(found.app_id, direct.app_id);
  assert.equal(found.path, direct.executable);
  assert.equal(found.app_user_model_id, null);
});

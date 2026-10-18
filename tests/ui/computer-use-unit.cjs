'use strict';
const assert = require('node:assert/strict'),
  path = require('node:path'),
  { randomUUID } = require('node:crypto');
const { ComputerUseCoordinator } = require('../../desktop/electron/computer-use.cjs');
require('../electron/visual-locator.test.cjs');
const owner = { conversation_id: randomUUID(), character_id: 'assistant' };
const window = {
  handle: 10,
  pid: 20,
  started_at: 'fixture-time',
  visible: true,
  minimized: false,
  responding: true,
  title: 'Synthetic window',
  bounds: [-500, 20, 400, 300],
};
const inputs = [];
const controller = {
  fixtureEntries: new Map(),
  list_windows: async () => [window],
  verify_window_state: async () => window,
  cancel_visual: () => {},
  call: async (_app, _action, args) => {
    inputs.push(args);
    return {
      observation_id: randomUUID(),
      created_ms: Date.now(),
      window,
      geometry: { monitors: [], window_dpi: 144, monitor_dpi: 144 },
      elements: [
        {
          identity: 'fresh',
          automation_id: 'box',
          name: 'Box',
          type: 'Edit',
          enabled: true,
          visible: true,
          bounds: [-400, 40, 100, 30],
        },
      ],
    };
  },
};
const registry = {
  detect: () => ({ installed: true, path: path.resolve('.cache/synthetic-fixture.exe') }),
};
(async () => {
  const computer = new ComputerUseCoordinator(controller, registry, async (scope) =>
    assert.deepEqual(scope, owner),
  );
  try {
    const windows = await computer.windows(owner, 'fixture');
    assert.ok(
      !JSON.stringify(windows).includes('started_at') &&
        !JSON.stringify(windows).includes('handle'),
    );
    const state = await computer.enable(owner, windows[0].choice_id, { preview: false });
    const id = state.targets[0].target_id;
    await assert.rejects(
      computer.execute('observe', { target_id: id }, { ...owner, conversation_id: randomUUID() }),
    );
    const found = await computer.execute(
      'find_element',
      { target_id: id, locator: { description: 'Box', automation_id: 'box', source: 'uia' } },
      owner,
    );
    assert.equal(found.candidates.length, 1);
    assert.equal(inputs.at(-1).visual_capture, false);
    assert.ok(!JSON.stringify(found).includes('fresh') && !JSON.stringify(found).includes('Box'));
    const frame = computer.get(owner, id).frames.get(found.observation_id);
    frame.created_ms -= 9000;
    const rejected = await computer.execute(
      'type_text',
      {
        target_id: id,
        observation_id: found.observation_id,
        candidate_id: found.candidates[0].candidate_id,
        text: 'never',
      },
      owner,
    );
    assert.equal(rejected.code, 'COMPUTER_OBSERVATION_EXPIRED');
    assert.equal(inputs.length, 1, 'expired action did not reach native controller');
    const ocr = await computer.execute(
      'find_element',
      { target_id: id, locator: { description: 'Box', source: 'ocr' } },
      owner,
    );
    assert.equal(ocr.code, 'COMPUTER_CAPTURE_PERMISSION_REQUIRED');
    computer.control(owner, id, 'pause');
    assert.equal(computer.state(owner).targets[0].observation, null);
    await assert.rejects(computer.execute('observe', { target_id: id }, owner), /PAUSED/);
    computer.control(owner, id, 'resume');
    assert.equal(computer.state(owner).targets[0].observation, null);
    computer.control(owner, id, 'takeover');
    assert.equal(computer.state(owner).targets.length, 0);
    const blocked = new ComputerUseCoordinator(
      controller,
      { detect: () => ({ installed: true, path: path.resolve('powershell.exe') }) },
      async () => {},
    );
    try {
      await assert.rejects(blocked.windows(owner, 'terminal'), /SENSITIVE/);
    } finally {
      blocked.close();
    }
  } finally {
    computer.close();
  }
  const fs = require('node:fs'),
    vm = require('node:vm');
  const { spawnSync } = require('node:child_process');
  const verification = spawnSync(
    'powershell.exe',
    [
      '-NoProfile',
      '-ExecutionPolicy',
      'Bypass',
      '-File',
      path.resolve('tests/windows/computer-verification.ps1'),
    ],
    { encoding: 'utf8', windowsHide: true },
  );
  assert.equal(verification.status, 0, verification.stderr || verification.stdout);
  const { VisualQA } = require('../../desktop/electron/visual-qa.cjs');
  const oldFlag = process.env.COMPANION_VISUAL_QA_PROFILE;
  process.env.COMPANION_VISUAL_QA_PROFILE = '1';
  let killed = 0;
  const qa = new VisualQA({ stopOwner: () => {}, controller: { fixtureEntries: new Map() } }, {});
  const consent = randomUUID(),
    fixture = randomUUID();
  qa.consents.set(consent, Date.now() - 1);
  qa.instances.set(fixture, {
    id: fixture,
    app: 'owned',
    scope: owner,
    consent,
    children: [{ kill: () => killed++ }],
  });
  try {
    await qa.perform('close_owner', { owner, consent_id: consent });
    assert.equal(killed, 1);
    assert.equal(qa.instances.size, 0, 'Expired permission never blocks owned fixture cleanup');
  } finally {
    if (oldFlag === undefined) delete process.env.COMPANION_VISUAL_QA_PROFILE;
    else process.env.COMPANION_VISUAL_QA_PROFILE = oldFlag;
  }
  const { ComputerController } = require('../../desktop/electron/computer-controller.cjs');
  const queued = new ComputerController(registry, path.resolve('.cache/visual-controller-unit'));
  let release,
    started = false;
  queued.serial = new Promise((resolve) => (release = resolve));
  queued.start = () => {
    started = true;
    throw Error('must not start');
  };
  const waiting = queued.call('fixture', 'visual', { visual_nonce: 'revoked-queued-input' });
  queued.cancel_visual('revoked-queued-input');
  release();
  await assert.rejects(waiting, /COMPUTER_PAUSED/);
  assert.equal(started, false, 'cancelled queued input never starts worker or sends native input');
  const handlers = {},
    calls = [];
  require('../../desktop/electron/computer-use-ipc.cjs')({
    ipcMain: { handle: (name, handler) => (handlers[name] = handler) },
    requireMainWindow: (event) => {
      if (event !== 'main') throw Error('DENIED');
    },
    request: async (...args) => {
      calls.push(args);
      return { valid: true };
    },
    dialog: { showMessageBox: async () => ({ response: 0 }) },
    getComputer: () => null,
    getQA: () => null,
  });
  await assert.rejects(handlers['computer-read']('web', 'state', owner), /DENIED/);
  await assert.rejects(
    handlers['computer-read']('main', 'state', { ...owner, url: 'https://unexpected.invalid' }),
    /ARGUMENT/,
  );
  const src = fs.readFileSync(path.resolve('desktop/electron/preload.cjs'), 'utf8');
  assert.ok(src.includes('computer-action'));
  assert.ok(!src.includes('COMPANION_CONTROL_TOKEN'));
  void vm;
  console.log(
    JSON.stringify({ computer_security_unit: true, raw_screenshots_sent: 0, gui: false }),
  );
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

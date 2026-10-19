'use strict';
// Original deterministic scheduler component/IPC regressions. No real backend, GUI, profile or credentials.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const { EventEmitter } = require('node:events');
const root = path.resolve(__dirname, '../..');
const ts = require(path.join(root, 'desktop/node_modules/typescript'));
const observations = { tests: [], residual: [] };
const reviewedFiles = [
  'desktop/electron/scheduler-ipc.cjs',
  'desktop/electron/scheduler-background.cjs',
  'desktop/electron/main.cjs',
  'desktop/electron/preload.cjs',
  'desktop/frontend/src/scheduling/ScheduledTasks.tsx',
  'desktop/frontend/src/scheduling/ScheduleHistory.tsx',
  'desktop/frontend/src/scheduling/ScheduleForm.tsx',
  'desktop/frontend/src/scheduling/TriggerFields.tsx',
  'desktop/frontend/src/scheduling/types.ts',
  'desktop/frontend/src/AppV3.tsx',
  'desktop/frontend/src/Workbench.tsx',
  'backend/app/api/schedules.py',
  'backend/app/services/scheduling/definitions.py',
  'backend/app/services/scheduling/recovery.py',
];
const hashFiles = () =>
  Object.fromEntries(
    reviewedFiles.map((file) => [
      file,
      crypto
        .createHash('sha256')
        .update(fs.readFileSync(path.join(root, file)))
        .digest('hex'),
    ]),
  );
const sourceBefore = hashFiles();
const ids = {
  ownerA: '11111111-1111-4111-8111-111111111111',
  ownerB: '22222222-2222-4222-8222-222222222222',
  scheduleA: '33333333-3333-4333-8333-333333333333',
  scheduleB: '44444444-4444-4444-8444-444444444444',
  occurrenceA: '55555555-5555-4555-8555-555555555555',
  runA: '66666666-6666-4666-8666-666666666666',
};
const tick = () => new Promise((r) => setImmediate(r));
function runtime() {
  const slots = [],
    stateIndices = [];
  let cursor = 0;
  const react = {
    useState(initial) {
      const i = cursor++;
      if (!(i in slots)) {
        slots[i] = typeof initial === 'function' ? initial() : initial;
        stateIndices.push(i);
      }
      return [
        slots[i],
        (value) => {
          slots[i] = typeof value === 'function' ? value(slots[i]) : value;
        },
      ];
    },
    useRef(initial) {
      const i = cursor++;
      if (!(i in slots)) slots[i] = { current: initial };
      return slots[i];
    },
    useCallback(fn) {
      cursor++;
      return fn;
    },
    useEffect() {
      cursor++;
    },
  };
  return {
    react,
    seed: (ordinal, value) => {
      slots[stateIndices[ordinal]] = value;
    },
    render(Component, props) {
      cursor = 0;
      return Component(props);
    },
    slots,
    stateIndices,
  };
}
function loadComponent(relative, hooks, api, extra = {}) {
  const exports = {};
  function ScheduleHistory() {}
  function ScheduleForm() {}
  function RunPanel() {}
  const localRequire = (name) =>
    name === 'react'
      ? hooks.react
      : name === 'react/jsx-runtime'
        ? {
            jsx: (type, props, key) => ({ type, props, key }),
            jsxs: (type, props, key) => ({ type, props, key }),
          }
        : name === './types'
          ? {
              labels: {},
              shownTime: (value) => value || '—',
              newDefinition: (owner) => ({ conversation_id: owner }),
            }
          : name === './ScheduleForm'
            ? { default: ScheduleForm }
            : name === './ScheduleList'
              ? {
                  default: loadComponent(
                    'desktop/frontend/src/scheduling/ScheduleList.tsx',
                    hooks,
                    api,
                  ),
                }
              : name === './ScheduleHistory'
                ? { default: ScheduleHistory }
                : name === '../workflows/RunPanel'
                  ? { default: RunPanel }
                  : name.endsWith('.css')
                    ? {}
                    : extra[name] || {};
  const filename = path.join(root, relative);
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
  vm.runInNewContext(
    code,
    { exports, module: { exports }, require: localRequire, window: { desktopAPI: api }, console },
    { filename },
  );
  return exports.default;
}
function nodes(tree, predicate) {
  const result = [];
  function visit(value) {
    if (Array.isArray(value)) value.forEach(visit);
    else if (value && typeof value === 'object') {
      if (typeof value.type === 'function' && value.type.name === 'ScheduleList') {
        visit(value.type(value.props));
        return;
      }
      if (predicate(value)) result.push(value);
      visit(value.props?.children);
    }
  }
  visit(tree);
  return result;
}
function textOf(node) {
  const value = node?.props?.children;
  return Array.isArray(value)
    ? value.map((v) => (typeof v === 'object' ? textOf(v) : String(v))).join('')
    : typeof value === 'object'
      ? textOf(value)
      : String(value ?? '');
}
async function ipcTests() {
  const handlers = {},
    calls = [],
    dialogs = [];
  let approved = true,
    valid = true,
    wrong = false;
  require(path.join(root, 'desktop/electron/scheduler-ipc.cjs'))({
    ipcMain: { handle: (name, fn) => (handlers[name] = fn) },
    requireMainWindow: (e) => {
      if (!e?.main) throw Error('Unauthorized window');
    },
    request: async (...args) => {
      calls.push(args);
      if (args[0].includes('/approval-overview?'))
        return {
          name: 'Synthetic Review',
          occurrence_id: wrong ? ids.scheduleA : ids.occurrenceA,
          scheduled_for: '2030-01-01T00:00:00Z',
          workflow_version_id: ids.runA,
          may_use_models: true,
          input_fields: 2,
          binding: valid ? 'a'.repeat(64) : 'invalid',
        };
      return { ok: true };
    },
    dialog: {
      showMessageBox: async (value) => {
        dialogs.push(value);
        return { response: approved ? 1 : 0 };
      },
    },
    background: () => ({}),
  });
  const options = { conversation_id: ids.ownerA, occurrence_id: ids.occurrenceA };
  await handlers['scheduler-control']({ main: true }, 'approve', options);
  assert.equal(
    calls[0][0],
    `/api/scheduler/occurrences/${ids.occurrenceA}/approval-overview?conversation_id=${ids.ownerA}`,
  );
  assert.deepEqual(calls[1], [
    `/api/scheduler/occurrences/${ids.occurrenceA}/approve`,
    { conversation_id: ids.ownerA, binding: 'a'.repeat(64) },
  ]);
  assert(dialogs[0].detail.includes(ids.occurrenceA));
  assert(dialogs[0].detail.includes(ids.runA));
  assert(dialogs[0].detail.includes('Synthetic Review'));
  calls.length = 0;
  await handlers['scheduler-control']({ main: true }, 'resume', { ...options, revision: 17 });
  assert.deepEqual(calls[1][1], {
    conversation_id: ids.ownerA,
    binding: 'a'.repeat(64),
    revision: 17,
  });
  for (const mode of ['rejected', 'bad_binding', 'wrong_occurrence', 'wrong_window']) {
    calls.length = 0;
    approved = mode !== 'rejected';
    valid = mode !== 'bad_binding';
    wrong = mode === 'wrong_occurrence';
    await assert.rejects(
      handlers['scheduler-control']({ main: mode !== 'wrong_window' }, 'approve', options),
    );
    assert(!calls.some(([url]) => url.endsWith('/approve')));
  }
  observations.tests.push(
    'IPC approval/resume exact endpoint and binding payload; cancelled, bad binding, wrong occurrence, wrong window all blocked',
  );
}
async function notificationTests() {
  const { SchedulerBackground } = require(
    path.join(root, 'desktop/electron/scheduler-background.cjs'),
  );
  let instances = [],
    sent = [],
    mode = 'show';
  class Native extends EventEmitter {
    static isSupported() {
      return true;
    }
    constructor(options) {
      super();
      this.options = options;
      instances.push(this);
    }
    show() {
      this.emit(mode);
    }
    close() {
      this.closed = true;
      this.emit('close');
    }
  }
  const window = {
    isDestroyed: () => false,
    isMinimized: () => false,
    show() {},
    focus() {},
    webContents: { send: (...args) => sent.push(args) },
  };
  const make = (request = async () => ({})) =>
    new SchedulerBackground({
      Notification: Native,
      userData: path.join(__dirname, 'profile-does-not-exist'),
      request,
      window: () => window,
      audit: () => {},
    });
  const bg = make();
  const row = {
    notification_id: 'synthetic',
    conversation_id: ids.ownerA,
    schedule_id: ids.scheduleA,
    occurrence_id: ids.occurrenceA,
    kind: 'COMPLETED',
    created_at: '2030-01-01T00:00:00Z',
  };
  await bg.show(row);
  instances.at(-1).emit('click');
  assert.deepEqual(sent.at(-1), [
    'scheduler-navigate',
    { conversation_id: ids.ownerA, schedule_id: ids.scheduleA, occurrence_id: ids.occurrenceA },
  ]);
  assert.deepEqual(bg.takeNavigation(), sent.at(-1)[1]);
  assert.equal(bg.takeNavigation(), null);
  for (let i = 0; i < 30; i++) await bg.show({ ...row, notification_id: String(i) });
  assert.equal(bg.activeNotifications.size, 16);
  assert.equal(instances[0].closed, true);
  mode = 'failed';
  for (let i = 0; i < 30; i++) await bg.show({ ...row, notification_id: 'failed-' + i });
  assert.equal(bg.activeNotifications.size, 15); // the first failure evicts one of the 16 shown before removing itself
  await bg.close();
  const previous = sent.length;
  instances[0].emit('click');
  assert.equal(sent.length, previous);
  assert.equal(bg.activeNotifications.size, 0);
  instances = [];
  let resolveClaim;
  const calls = [];
  const raced = make(async (url) => {
    calls.push(url);
    if (url.endsWith('/pending')) return { items: [row] };
    if (url.endsWith('/claim')) return new Promise((r) => (resolveClaim = r));
    return {};
  });
  const pending = raced.poll();
  await tick();
  await raced.close();
  resolveClaim(row);
  await pending;
  assert.equal(instances.length, 0);
  assert(!calls.some((url) => url.endsWith('/delivery')));
  observations.tests.push(
    'Native notification exact owner/schedule/occurrence navigation; consume-once; max16; failed cleanup; no navigation/postclaim show after quit',
  );
}
async function uiResidualTests() {
  const hooks = runtime(),
    calls = [];
  const api = {
    controlSchedules: async (...args) => {
      calls.push(args);
      return {};
    },
    readSchedules: async () => ({ items: [] }),
    schedulerBackground: async () => ({ enabled: false }),
  };
  const Component = loadComponent('desktop/frontend/src/scheduling/ScheduledTasks.tsx', hooks, api);
  const props = { conversationId: ids.ownerA, visible: false };
  hooks.render(Component, props);
  const owners = [
    { conversation_id: ids.ownerA, title: 'Owner A' },
    { conversation_id: ids.ownerB, title: 'Owner B' },
  ];
  hooks.seed(1, owners);
  hooks.seed(4, [{ source_id: ids.scheduleA, enabled: true, expires_at: '2030-01-01' }]);
  hooks.seed(13, [{ run_id: ids.runA, state: 'COMPLETED' }]);
  hooks.seed(14, ids.runA);
  hooks.seed(15, [
    {
      notification_id: 'notice-a',
      schedule_id: ids.scheduleA,
      occurrence_id: ids.occurrenceA,
      kind: 'COMPLETED',
      delivery: 'SHOWN',
    },
  ]);
  hooks.seed(16, ids.ownerA);
  let tree = hooks.render(Component, props);
  nodes(tree, (n) => n.props?.['aria-label'] === '调度工作会话')[0].props.onChange({
    target: { value: ids.ownerB },
  });
  tree = hooks.render(Component, props);
  const staleRun = nodes(tree, (n) => n.props?.['aria-label'] === '监控工作流运行')[0];
  const sourceShown = nodes(tree, (n) => n.type === 'small').some((n) =>
    textOf(n).includes(ids.scheduleA.slice(0, 8)),
  );
  const oldNoticeShown = nodes(tree, (n) => n.type === 'summary').some((n) =>
    textOf(n).includes('（1）'),
  );
  const button = nodes(tree, (n) => n.type === 'button' && textOf(n) === '授权状态监控')[0];
  if (button) button.props.onClick();
  await tick();
  const emittedWrongScope = calls.some(
    ([operation, value]) =>
      operation === 'source' &&
      value.conversation_id === ids.ownerB &&
      value.workflow_run_id === ids.runA,
  );
  assert.equal(staleRun, undefined);
  assert.equal(sourceShown, false);
  assert.equal(oldNoticeShown, false);
  assert.equal(emittedWrongScope, false);
  observations.tests.push(
    'Component actual owner selector clears watch/source/notices and hides controls until matching owner response',
  );
  hooks.seed(0, ids.ownerA);
  hooks.seed(16, ids.ownerA);
  hooks.seed(9, ids.scheduleA);
  const saved = (id) => ({
    definition: { schedule_id: id, name: id, trigger_type: 'ONCE', workflow_version_id: ids.runA },
    state: 'ENABLED',
    revision: 1,
    snapshot: { may_use_models: false },
    next_due: null,
  });
  hooks.seed(2, [saved(ids.scheduleA), saved(ids.scheduleB)]);
  hooks.seed(10, {
    items: [
      {
        occurrence_id: ids.occurrenceA,
        schedule_id: ids.scheduleA,
        owner_id: ids.ownerA,
        status: 'RUNNING',
      },
    ],
    events: [],
  });
  hooks.seed(18, ids.ownerA + '#' + ids.scheduleA);
  tree = hooks.render(Component, props);
  nodes(tree, (n) => n.type === 'button' && textOf(n) === '查看历史')[1].props.onClick();
  tree = hooks.render(Component, props);
  const child = nodes(
    tree,
    (n) => typeof n.type === 'function' && n.type.name === 'ScheduleHistory',
  )[0];
  assert.equal(child.key, ids.scheduleB);
  assert.equal(child.props.history.items.length, 0);
  observations.tests.push(
    'Component actual history selector never passes previous schedule history into newly selected schedule',
  );
}
async function detailTests() {
  const hooks = runtime();
  let resolvePreview, resolveTrace;
  const api = {
    readSchedules: async () => new Promise((r) => (resolvePreview = r)),
    readAgentTraces: async () => new Promise((r) => (resolveTrace = r)),
  };
  const Component = loadComponent(
    'desktop/frontend/src/scheduling/ScheduleHistory.tsx',
    hooks,
    api,
  );
  const a = {
    occurrence_id: ids.occurrenceA,
    workflow_run_id: ids.runA,
    schedule_id: ids.scheduleA,
    status: 'FAILED',
    summary: { trace_id: 'trace-a' },
  };
  const b = {
    occurrence_id: ids.scheduleB,
    workflow_run_id: '77777777-7777-4777-8777-777777777777',
    schedule_id: ids.scheduleA,
    status: 'FAILED',
    summary: { trace_id: 'trace-b' },
  };
  const props = {
    owner: ids.ownerA,
    history: { items: [a, b], events: [] },
    action: async () => {},
    refresh: () => {},
  };
  hooks.render(Component, props);
  hooks.seed(0, a);
  hooks.seed(1, { run_id: ids.runA, state: 'FAILED', nodes: [] });
  let tree = hooks.render(Component, props);
  const panel = nodes(tree, (n) => typeof n.type === 'function' && n.type.name === 'RunPanel')[0];
  panel.props.onPreview();
  panel.props.onTrace();
  hooks.seed(0, b);
  tree = hooks.render(Component, props);
  let switched = nodes(tree, (n) => typeof n.type === 'function' && n.type.name === 'RunPanel')[0];
  assert.equal(switched.props.run, null);
  resolvePreview({ run_id: ids.runA, revision: 2, can_resume: true });
  resolveTrace({ events: [{ kind: 'OLD_TRACE_A' }] });
  await tick();
  tree = hooks.render(Component, props);
  switched = nodes(tree, (n) => typeof n.type === 'function' && n.type.name === 'RunPanel')[0];
  assert.equal(switched.props.preview, null);
  assert(!nodes(tree, (n) => n.type === 'pre').some((n) => textOf(n).includes('OLD_TRACE_A')));
  observations.tests.push(
    'Actual ScheduleHistory component discards in-flight A recovery/Trace after selecting B; previous RunPanel is hidden',
  );
}
(async () => {
  await ipcTests();
  await notificationTests();
  await uiResidualTests();
  await detailTests();
  assert.deepEqual(hashFiles(), sourceBefore, 'Reviewed source changed during test');
  observations.status = 'PASS';
  observations.timestamp = new Date().toISOString();
  observations.source_hashes = sourceBefore;
  observations.limitations = [
    'Synthetic native APIs and transpiled TSX hook interleavings; not real React DOM or real Windows GUI',
    'No external requests, live profiles, credential access, installation or Git operations',
  ];
  const out = path.join(root, '.cache/scheduler-unit-report.json');
  fs.writeFileSync(out, JSON.stringify(observations, null, 2));
  console.log(JSON.stringify({ passed_groups: observations.tests.length, real_gui: false }));
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

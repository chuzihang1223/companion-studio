'use strict';
// Production IPC and pure SSR. This is not native desktop/restart acceptance.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('../../desktop/node_modules/typescript');
const React = require('../../desktop/node_modules/react');
const { renderToStaticMarkup: render } = require('../../desktop/node_modules/react-dom/server');
for (const extension of ['.ts', '.tsx'])
  require.extensions[extension] = (m, f) =>
    m._compile(
      ts.transpileModule(fs.readFileSync(f, 'utf8'), {
        compilerOptions: {
          module: ts.ModuleKind.CommonJS,
          target: ts.ScriptTarget.ES2022,
          jsx: ts.JsxEmit.ReactJSX,
        },
      }).outputText,
      f,
    );
require.extensions['.css'] = () => {};
global.window = {};
const Panel = require('../../desktop/frontend/src/recovery/RecoveryPanel.tsx').default;
assert.match(
  render(
    React.createElement(Panel, {
      conversationId: null,
      sessionId: null,
      visible: true,
      revision: 0,
    }),
  ),
  /选择工作会话/,
);
assert.match(
  render(
    React.createElement(Panel, {
      conversationId: 'fixture',
      sessionId: null,
      visible: true,
      revision: 0,
    }),
  ),
  /桌面应用/,
);
const handlers = {},
  calls = [];
const event = { sender: { id: 19 }, main: true };
const uuid = '11111111-1111-4111-8111-111111111111';
require('../../desktop/electron/agent-recovery.cjs')({
  ipcMain: { handle: (name, handler) => (handlers[name] = handler) },
  requireMainWindow: (e) => {
    if (!e?.main) throw Error('Unauthorized');
  },
  request: (...args) => {
    calls.push(args);
    return Promise.resolve({});
  },
});
const scope = { conversation_id: uuid, work_session_id: uuid, task_id: uuid };
const control = {
  conversation_id: uuid,
  work_session_id: uuid,
  request_id: uuid,
  revision: 1,
  approved_ids: [],
};
assert.throws(() => handlers['agent-recovery-read']({}, 'list', scope), /Unauthorized/);
assert.throws(() => handlers['agent-recovery-read'](event, 'execute', scope));
assert.throws(() =>
  handlers['agent-recovery-read'](event, 'detail', { ...scope, task_id: '../file' }),
);
assert.throws(() => handlers['agent-recovery-read'](event, 'list', { ...scope, path: 'private' }));
assert.throws(() => handlers['agent-recovery-control'](event, uuid, 'shell', control));
assert.throws(() =>
  handlers['agent-recovery-control'](event, uuid, 'resume', {
    ...control,
    approved_ids: ['../unsafe'],
  }),
);
assert.throws(() =>
  handlers['agent-recovery-control'](event, uuid, 'resume', { ...control, renderer_id: 'spoofed' }),
);
assert.throws(() =>
  handlers['agent-recovery-control'](event, uuid, 'resume', { ...control, revision: 0 }),
);
(async () => {
  await handlers['agent-recovery-read'](event, 'preview', scope);
  assert.match(calls.at(-1)[0], /\/api\/agent\/recovery\/tasks\/[^/]+\/preview\?/);
  await handlers['agent-recovery-control'](event, uuid, 'resume', control);
  assert.equal(calls.at(-1)[1].renderer_id, 'renderer-19');
  assert.equal(calls.at(-1)[1].confirm_model_requests, false);
  assert.deepEqual(calls.at(-1)[1].approved_ids, []);
  console.log(
    JSON.stringify({
      pass: true,
      checks: [
        'fixed_routes',
        'main_sender',
        'scope_uuid',
        'no_shell_or_path',
        'fresh_approval',
        'main_owned_renderer',
        'empty_ui',
      ],
      external_calls: 0,
      native_gui: false,
    }),
  );
})().catch((e) => {
  console.error(e);
  process.exitCode = 1;
});

'use strict';
// Real production IPC boundary with synthetic dialogs/storage; not native GUI acceptance.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
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
const Panel = require('../../desktop/frontend/src/extensions/ExtensionsSettings.tsx').default;
assert.match(render(React.createElement(Panel)), /MCP Servers/);
assert.match(render(React.createElement(Panel)), /Agent Skills/);
const handlers = {},
  calls = [];
const uuid = '11111111-1111-4111-8111-111111111111';
const other = '22222222-2222-4222-8222-222222222222';
const root = fs.mkdtempSync(path.resolve(__dirname, '../../.cache/extensions-unit-'));
let approve = false,
  binding = 'a'.repeat(64);
const event = { sender: { id: 5 }, main: true };
const state = () => ({
  server_id: uuid,
  config_fingerprint: binding,
  config: {
    transport: 'http',
    endpoint_or_command: 'https://example.com/mcp',
    args: [],
    environment_names: [],
  },
});
const host = require('../../desktop/electron/extensions.cjs')({
  ipcMain: { handle: (name, fn) => (handlers[name] = fn) },
  requireMainWindow: (e) => {
    if (!e?.main) throw Error('Unauthorized');
  },
  dialog: {
    showMessageBox: async () => ({ response: approve ? 1 : 0 }),
    showOpenDialog: async () => ({ canceled: false, filePaths: [path.join(root, 'skill-source')] }),
  },
  safeStorage: {
    isEncryptionAvailable: () => true,
    encryptString: (s) => Buffer.from('sealed:' + s),
    decryptString: (b) => b.toString().slice(7),
  },
  userData: root,
  exportsDirectory: path.join(root, 'Exports'),
  request: async (...args) => {
    calls.push(args);
    if (args[0] === '/api/mcp/servers') return { items: [state()] };
    if (args[0].startsWith('/api/mcp/plans/'))
      return {
        conversation_id: uuid,
        work_session_id: other,
        calls: [
          {
            id: 'call-1',
            server_id: uuid,
            original_tool_name: 'echo',
            side_effect_class: 'READ_ONLY',
            arguments: { text: 'synthetic' },
          },
        ],
      };
    if (args[0] === '/api/skills/preview')
      return { name: 'synthetic-skill', content_hash: 'b'.repeat(64), files: [] };
    return {};
  },
});
async function run() {
  const read = handlers['extension-read'],
    control = handlers['extension-control'];
  await assert.rejects(read({}, 'servers'), /Unauthorized/);
  await assert.rejects(control({}, 'connect', { server_id: uuid }), /Unauthorized/);
  await assert.rejects(read(event, 'shell'), /无效/);
  await assert.rejects(read(event, 'skill-file', { skill_id: uuid, file: '../secret' }));
  await assert.rejects(control(event, 'connect', { server_id: '../bad' }));
  await assert.rejects(control(event, 'server-save', { config: { command: 'unsafe' } }));
  await assert.rejects(control(event, 'connect', { server_id: uuid }), /用户取消/);
  assert(!calls.some((row) => row[0].endsWith('/connect')));
  approve = true;
  await control(event, 'credentials-save', {
    server_id: uuid,
    credential: 'isolated-test-key',
    environment: {},
  });
  assert(
    !fs
      .readFileSync(path.join(root, 'extension-credentials.json'), 'utf8')
      .includes('isolated-test-key'),
  );
  calls.length = 0;
  await control(event, 'connect', { server_id: uuid });
  assert(calls.some((row) => row[0].endsWith('/credentials')));
  assert(!JSON.stringify(await read(event, 'servers')).includes('isolated-test-key'));
  binding = 'c'.repeat(64);
  calls.length = 0;
  await control(event, 'connect', { server_id: uuid });
  assert(
    !calls.some((row) => row[0].endsWith('/credentials')),
    'credential cannot cross a changed endpoint/config',
  );
  calls.length = 0;
  await host.approvePlan(event, uuid, ['call-1']);
  assert.equal(calls.at(-1)[0], '/api/mcp/grants');
  assert.equal(calls.at(-1)[1].scope, 'once');
  approve = false;
  calls.length = 0;
  await assert.rejects(host.approvePlan(event, uuid, ['call-1']), /用户取消/);
  assert(!calls.some((row) => row[0] === '/api/mcp/grants'));
  const preview = await control(event, 'skill-select-folder');
  await assert.rejects(control(event, 'skill-import', { import_token: other }));
  await assert.rejects(
    control({ sender: { id: 8 }, main: true }, 'skill-import', {
      import_token: preview.import_token,
    }),
  );
  await control(event, 'skill-import', { import_token: preview.import_token });
  await assert.rejects(control(event, 'skill-import', { import_token: preview.import_token }));
  assert.equal(calls.at(-1)[1].expected_hash, 'b'.repeat(64));
  console.log(
    JSON.stringify({
      pass: true,
      checks: [
        'settings_ssr',
        'fixed_routes',
        'sender_boundary',
        'native_consent',
        'credential_sealing',
        'credential_scope',
        'no_credential_reads',
        'single_use_import_ticket',
        'no_script_execute',
      ],
      real_gui: false,
    }),
  );
}
run()
  .catch((e) => {
    console.error(e);
    process.exitCode = 1;
  })
  .finally(() => {
    if (root.startsWith(path.resolve(__dirname, '../../.cache') + path.sep))
      fs.rmSync(root, { recursive: true, force: true });
  });

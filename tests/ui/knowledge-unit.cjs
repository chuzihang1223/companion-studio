'use strict';
// Real IPC registration and UI rendering with synthetic dialogs/HTTP responses.
// This does not prove native GUI behavior or semantic model quality.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('../../desktop/node_modules/typescript');
for (const extension of ['.ts', '.tsx'])
  require.extensions[extension] = (module, filename) =>
    module._compile(
      ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
        compilerOptions: {
          module: ts.ModuleKind.CommonJS,
          target: ts.ScriptTarget.ES2022,
          jsx: ts.JsxEmit.ReactJSX,
        },
      }).outputText,
      filename,
    );
require.extensions['.css'] = () => {};
global.window = {};
const React = require('../../desktop/node_modules/react');
const { renderToStaticMarkup } = require('../../desktop/node_modules/react-dom/server');
const Panel = require('../../desktop/frontend/src/knowledge/KnowledgePanel.tsx').default;
const html = renderToStaticMarkup(
  React.createElement(Panel, { conversationId: null, characterId: 'assistant' }),
);
assert.match(html, /本地知识与记忆/);
assert.match(html, /不会下载模型或上传文档/);
const { defaultLayout } = require('../../desktop/frontend/src/WorkspaceLayoutEngine.ts');
assert.equal(defaultLayout().modules.knowledge.closed, true);
const handlers = {},
  calls = [],
  revealed = [];
const id = '11111111-1111-4111-8111-111111111111';
const event = { main: true };
let approved = false;
require('../../desktop/electron/knowledge.cjs')({
  ipcMain: {
    handle: (name, fn) => {
      handlers[name] = fn;
    },
  },
  requireMainWindow: (value) => {
    if (!value.main) throw Error('main only');
  },
  request: async (...args) => {
    calls.push(args);
    if (args[0] === '/api/knowledge/targets')
      return {
        targets: [
          {
            role: 'character_model',
            base_url: 'http://127.0.0.1:9',
            model: 'fixture',
            fingerprint: 'a'.repeat(64),
          },
        ],
      };
    if (args[0] === '/api/knowledge/resolve')
      return { open_path: 'synthetic-authorized-source.txt' };
    return { accepted: true };
  },
  dialog: {
    showMessageBox: async () => ({ response: approved ? 1 : 0 }),
    showOpenDialog: async () => ({ canceled: true, filePaths: [] }),
  },
  shell: { showItemInFolder: (value) => revealed.push(value) },
});
async function run() {
  assert.throws(() => handlers['knowledge-read']({}, 'collections'), /main only/);
  assert.throws(
    () => handlers['knowledge-read'](event, 'jobs', { collection_id: '../../escape' }),
    /编号无效/,
  );
  await assert.rejects(
    handlers['knowledge-control'](event, 'create', { api_key: 'isolated-test-key' }),
    /参数无效/,
  );
  const owner = { conversation_id: id, character_id: 'assistant' };
  await handlers['knowledge-control'](event, 'selection', {
    ...owner,
    collection_ids: [id],
    allow_model_transfer: false,
  });
  assert.deepEqual(calls.at(-1)[1].expected_targets, []);
  const before = calls.length;
  await assert.rejects(
    handlers['knowledge-control'](event, 'selection', {
      ...owner,
      collection_ids: [id],
      allow_model_transfer: true,
    }),
    /未更改授权/,
  );
  assert.equal(calls.length, before + 1); // Only read target metadata; no grant was sent.
  approved = true;
  await handlers['knowledge-control'](event, 'selection', {
    ...owner,
    collection_ids: [id],
    allow_model_transfer: true,
  });
  assert.deepEqual(calls.at(-1)[1].expected_targets, ['a'.repeat(64)]);
  await handlers['knowledge-control'](event, 'reveal-source', {
    ...owner,
    reference: { chunk_id: id },
  });
  assert.deepEqual(revealed, ['synthetic-authorized-source.txt']);
  await assert.rejects(
    handlers['knowledge-control'](event, 'reveal-source', { ...owner, open_path: 'arbitrary.exe' }),
    /参数无效/,
  );
  const result = await handlers['knowledge-control'](event, 'add-root', {
    collection_id: id,
    revision: 1,
  });
  assert.equal(result.cancelled, true);
  await handlers['knowledge-control'](event, 'embedding', {
    collection_id: id,
    revision: 1,
    embedding: { mode: 'DISABLED', endpoint: '', model: '', dimension: 384, revision_name: '' },
  });
  assert.equal(calls.at(-1)[1].consent, false);
  console.log('Knowledge IPC, consent, source reveal, UI and closed-default module checks passed.');
}
run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});

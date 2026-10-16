'use strict';
// Production protocol helpers and IPC; native dialogs are synthetic, not GUI acceptance.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('../../desktop/node_modules/typescript');
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
const {
  emptyWorkflow,
  makeNode,
  addEdge,
  duplicateNodes,
  removeNodes,
  fitView,
} = require('../../desktop/frontend/src/workflows/model.ts');
let graph = emptyWorkflow();
const a = makeNode('manual_trigger'),
  b = makeNode('output', { x: 400, y: 80 });
graph.nodes.push(a, b);
graph = addEdge(graph, a.node_id, 'out', b.node_id);
assert.equal(graph.edges.length, 1);
assert.throws(() => addEdge(graph, a.node_id, 'out', b.node_id));
assert.throws(() => addEdge(graph, a.node_id, 'out', a.node_id));
const c = makeNode('transform');
graph.nodes.push(c);
graph = addEdge(graph, b.node_id, 'out', c.node_id);
assert.throws(() => addEdge(graph, c.node_id, 'out', a.node_id), /循环/);
b.config = { value: { $ref: 'nodes.' + a.node_id + '.output' } };
const duplicate = duplicateNodes(graph, [a.node_id, b.node_id]);
assert.equal(duplicate.nodes.length, 5);
assert.equal(duplicate.edges.length, 3);
assert.equal(
  duplicate.nodes[4].config.value.$ref,
  'nodes.' + duplicate.nodes[3].node_id + '.output',
);
assert.equal(removeNodes(graph, [b.node_id]).edges.length, 0);
const view = fitView(graph.nodes, 600, 400);
assert(Number.isFinite(view.scale) && view.scale > 0);
const { nodeTraceEvents } = require('../../desktop/frontend/src/workflows/trace-links.ts');
const eventRows = [
  {
    sequence: 1,
    span_id: 'root',
    metadata: { node_execution_id: 'node-a' },
    span: { kind: 'task', span_id: 'root', parent_span_id: null, metadata: {} },
  },
  {
    sequence: 2,
    span_id: 'a',
    metadata: { node_execution_id: 'node-a' },
    span: {
      kind: 'workflow_node',
      span_id: 'a',
      parent_span_id: 'root',
      metadata: { node_execution_id: 'node-a' },
    },
  },
  {
    sequence: 3,
    span_id: 'tool-a',
    metadata: {},
    span: { kind: 'tool', span_id: 'tool-a', parent_span_id: 'a', metadata: {} },
  },
  {
    sequence: 4,
    span_id: 'b',
    metadata: {},
    span: {
      kind: 'workflow_node',
      span_id: 'b',
      parent_span_id: 'root',
      metadata: { node_execution_id: 'node-b' },
    },
  },
];
assert.deepEqual(
  nodeTraceEvents(eventRows, 'node-a').map((e) => e.sequence),
  [1, 2, 3],
);
assert.deepEqual(nodeTraceEvents(eventRows, 'not-executed'), []);
const handlers = {},
  calls = [],
  messages = [];
const uuid = '11111111-1111-4111-8111-111111111111';
const root = fs.mkdtempSync(path.resolve(__dirname, '../../.cache/workflow-unit-'));
const event = {
  main: true,
  sender: {
    id: 32,
    once() {},
    isDestroyed: () => false,
    send: (channel, data) => messages.push({ channel, data }),
  },
};
let allowed = false,
  mcp = false;
const definition = { ...emptyWorkflow(), workflow_id: uuid };
fs.writeFileSync(path.join(root, 'fixture.json'), JSON.stringify(definition));
const host = require('../../desktop/electron/workflows.cjs')({
  ipcMain: { handle: (name, fn) => (handlers[name] = fn) },
  requireMainWindow: (e) => {
    if (!e?.main) throw Error('Unauthorized');
  },
  request: async (...args) => {
    calls.push(args);
    const url = args[0];
    if (url.includes('/overview')) return { name: 'Synthetic', nodes: [], budget: {} };
    if (url.startsWith('/api/workflow-runs/'))
      return {
        state: 'WAITING_PERMISSION',
        nodes: [
          {
            pending: {
              approval_id: uuid,
              kind: 'tool',
              call: {
                name: mcp ? 'mcp.fixture' : 'workspace.create',
                id: 'call-1',
                arguments: { path: 'fixture.txt', content: 'fixture' },
              },
              plan_id: uuid,
              risk: 'medium',
            },
          },
        ],
      };
    if (url.startsWith('/api/workflows/')) return { definition, revision: 1 };
    return { accepted: true };
  },
  dialog: {
    showMessageBox: async () => ({ response: allowed ? 1 : 0 }),
    showOpenDialog: async () => ({ canceled: false, filePaths: [path.join(root, 'fixture.json')] }),
  },
  exportsDirectory: root,
  authorizeMCP: async (_e, id, ids) => {
    assert.equal(id, uuid);
    assert.deepEqual(ids, ['call-1']);
    calls.push(['MCP_EXACT_PLAN']);
  },
  backendURL: 'http://127.0.0.1:1',
  controlToken: 'synthetic-owned-control',
});
async function run() {
  const read = handlers['workflow-read'],
    control = handlers['workflow-control'];
  assert.throws(() => read({}, 'list'), /Unauthorized/);
  await assert.rejects(control({}, 'save', {}), /Unauthorized/);
  assert.throws(() => read(event, 'list', { shell: 'anything' }));
  assert.throws(() => read(event, 'run', { run_id: '../unsafe', conversation_id: uuid }));
  assert.throws(() => read(event, 'fetch_url', {}));
  await assert.rejects(
    control(event, 'start', {
      workflow_id: uuid,
      version_id: uuid,
      conversation_id: uuid,
      inputs: {},
    }),
    /已取消/,
  );
  assert(!calls.some((c) => c[0].endsWith('/runs')));
  allowed = true;
  calls.length = 0;
  await control(event, 'start', {
    workflow_id: uuid,
    version_id: uuid,
    conversation_id: uuid,
    inputs: {},
  });
  assert(calls.some((c) => c[0].endsWith('/runs')));
  calls.length = 0;
  allowed = false;
  await assert.rejects(
    control(event, 'approve', {
      run_id: uuid,
      conversation_id: uuid,
      approval_id: uuid,
      decision: true,
    }),
  );
  assert(!calls.some((c) => c[0].endsWith('/approve')));
  allowed = true;
  mcp = true;
  calls.length = 0;
  await control(event, 'approve', {
    run_id: uuid,
    conversation_id: uuid,
    approval_id: uuid,
    decision: true,
  });
  assert.equal(calls.at(-2)[0], 'MCP_EXACT_PLAN');
  assert(calls.at(-1)[0].endsWith('/approve'));
  calls.length = 0;
  await control(event, 'import');
  assert.notEqual(
    calls.at(-1)[1].definition.workflow_id,
    uuid,
    'import is a new draft, never an overwrite',
  );
  const output = await control(event, 'export', { workflow_id: uuid });
  assert(fs.existsSync(path.join(root, 'Workflows', output.filename)));
  await assert.rejects(
    control(event, 'save', { definition: { ...definition, shell: 'unsafe' }, revision: 0 }),
  );
  const originalFetch = global.fetch;
  global.fetch = async (url) => {
    assert.match(String(url), /after=1/);
    return new Response(
      'id: 2\nevent: workflow\ndata: {"cursor":2,"node_id":"n","state":"COMPLETED","kind":"WORKFLOW_NODE_COMPLETED"}\n\n',
      { status: 200 },
    );
  };
  try {
    await handlers['workflow-watch'](event, { run_id: uuid, conversation_id: uuid, after: 1 });
    await new Promise((resolve) => setTimeout(resolve, 30));
    assert.equal(messages[0].data.cursor, 2);
    assert.equal(messages[0].data.conversation_id, uuid);
  } finally {
    global.fetch = originalFetch;
    host();
  }
  fs.rmSync(root, { recursive: true, force: true });
  console.log(
    'workflow helpers, version copy, permissions, owner-only IPC and cursor subscription passed',
  );
}
run().catch((e) => {
  host();
  console.error(e);
  process.exitCode = 1;
});

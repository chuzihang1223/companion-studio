'use strict';
// Real candidate renderer/main/backend/SQLite/SDK. Model and native confirmation
// choices are controlled fixtures. This script never reads a normal user profile.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { StudioSmokeDriver, q, sleep } = require('./studio-smoke-driver.cjs');
const { exerciseCanvas } = require('./workflow-interactions.cjs');
const exerciseTemplates = require('./workflow-templates.cjs');
const exercisePerformance = require('./workflow-performance.cjs');
const exerciseRestart = require('./workflow-restart.cjs');
const exerciseEvaluation = require('./workflow-evaluation.cjs');
const exerciseComparison = require('./workflow-comparison.cjs');
let driver;
const read = (op, options = {}) =>
  driver.c.evaluate(`window.desktopAPI.readWorkflows(${q(op)},${q(options)})`);
const control = (op, options = {}) =>
  driver.c.evaluate(`window.desktopAPI.controlWorkflows(${q(op)},${q(options)})`);
const extRead = (op, options = {}) =>
  driver.c.evaluate(`window.desktopAPI.readExtensions(${q(op)},${q(options)})`);
const extControl = (op, options = {}) =>
  driver.c.evaluate(`window.desktopAPI.controlExtensions(${q(op)},${q(options)})`);
const scope = "document.querySelector('.workflow-builder')";

async function select(selector, value) {
  await driver.c.evaluate(
    `(()=>{const n=document.querySelector(${q(selector)});if(!n)throw Error('Select missing');n.value=${q(value)};n.dispatchEvent(new Event('change',{bubbles:true}));return true})()`,
  );
  await sleep(100);
}
async function idle() {
  await driver.c.until(
    "document.querySelector('.workflow-builder')?.getAttribute('aria-busy')==='false'",
  );
}
async function openBuilder() {
  if (!(await driver.c.evaluate("!!document.querySelector('.workflow-builder')"))) {
    await driver.click('模块');
    await driver.click(
      'Visual Workflow · 流程设计',
      "document.querySelector('.workspace-module-picker')",
    );
  }
  await driver.c.until("!!document.querySelector('.workflow-builder')");
  await driver.c.evaluate(
    "(()=>{const m=document.querySelector('[data-module=workflow_builder]');if(!m.classList.contains('module-maximized'))m.querySelector('[aria-label=\"最大化Visual Workflow · 流程设计\"]').click();return true})()",
  );
  await sleep(150);
  await driver.c.evaluate(
    "document.querySelector('[data-module=workflow_builder]').scrollIntoView({block:'start'});true",
  );
}
async function waitRun(id, owner, state) {
  await driver.c.until(
    `window.desktopAPI.readWorkflows('run',${q({ run_id: id, conversation_id: owner })}).then(r=>${q(state)}.includes(r.state))`,
    30000,
  );
  return read('run', { run_id: id, conversation_id: owner });
}

async function run() {
  driver = new StudioSmokeDriver();
  await driver.prepare();
  driver.mock.prependListener('request', (request, response) => {
    const end = response.end.bind(response);
    response.end = (chunk, ...args) => {
      if (request.url === '/v1/chat/completions') {
        try {
          const data = JSON.parse(String(chunk));
          if (data.choices) {
            data.usage = { prompt_tokens: 5, completion_tokens: 3, total_tokens: 8 };
            chunk = q(data);
          }
        } catch {
          /* Stream fixtures use SSE. */
        }
      }
      return end(chunk, ...args);
    };
  });
  await driver.launch();
  const info = await driver.c.evaluate('window.desktopAPI.appInfo()');
  const expected = JSON.parse(
    fs.readFileSync(path.resolve(__dirname, '../../desktop/package.json'), 'utf8'),
  ).version;
  driver.check(
    info.version === expected && info.packaged === !driver.source,
    'Candidate version and actual source/installed boundary match',
    { version: info.version, packaged: info.packaged },
  );
  await driver.c.evaluate(
    `window.desktopAPI.applyApiPreset({name:'Workflow local fixture',provider:'openai-compatible',base_url:'http://127.0.0.1:${driver.ports.model}/v1',api_key:'mock-local-only',model:'local-smoke',capabilities:{text:true,tool_calling:false},timeout_seconds:15})`,
  );
  await driver.c.evaluate(
    "window.desktopAPI.workPreferences({auto_work_mode:false,permission_mode:'ask'})",
  );
  const response = await fetch(driver.base + '/api/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: q({ character_id: 'assistant', conversation_type: 'work' }),
  });
  assert(response.ok);
  const owner = (await response.json()).id;
  driver.report.workflowConversation = owner;
  await driver.c.call('Page.reload');
  await driver.c.until("!!document.querySelector('[aria-label=输入消息]')");
  await driver.openConversation(owner);
  await openBuilder();
  await driver.m.evaluate(
    "(()=>{globalThis.__workflowDialogs=[];require('electron').dialog.showMessageBox=async(o)=>{if(!['工作流执行确认','扩展权限确认'].includes(o.title))throw Error('Unexpected fixture dialog');globalThis.__workflowDialogs.push(o.message);return {response:1}};return true})()",
  );
  driver.check(
    (await read('list')).items.length === 0,
    'New isolated profile has no imported workflows',
  );
  await driver.c.evaluate("document.querySelector('[data-add-node=manual_trigger]').click();true");
  await sleep(80);
  await driver.c.evaluate("document.querySelector('[data-add-node=output]').click();true");
  await sleep(100);
  const nodes = await driver.c.evaluate(
    "[...document.querySelectorAll('.wf-node')].map(n=>n.dataset.nodeId)",
  );
  driver.check(nodes.length === 2, 'Palette adds real trigger and output nodes');
  await driver.c.evaluate(
    `document.querySelector('[data-output-node="${nodes[0]}"][data-port=out]').click();true`,
  );
  await sleep(50);
  await driver.c.evaluate(`document.querySelector('[data-input-node="${nodes[1]}"]').click();true`);
  await driver.c.until("document.querySelectorAll('.wf-edge').length===1");
  driver.check(true, 'Keyboard/click accessible ports connect real nodes');
  await driver.click('撤销', scope);
  driver.check(
    await driver.c.evaluate("document.querySelectorAll('.wf-edge').length===0"),
    'Undo restores previous graph',
  );
  await driver.click('重做', scope);
  driver.check(
    await driver.c.evaluate("document.querySelectorAll('.wf-edge').length===1"),
    'Redo restores connection',
  );
  await driver.field('[aria-label="工作流名称"]', 'Workflow synthetic acceptance');
  await driver.c.evaluate("document.querySelector('.wf-edge-hit').focus();true");
  await driver.c.call('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Enter', code: 'Enter' });
  await driver.c.call('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Enter', code: 'Enter' });
  await driver.c.until("document.querySelectorAll('.wf-edge').length===0");
  driver.check(true, 'Keyboard activation deletes a selected connection');
  await exerciseCanvas(driver, nodes);
  await driver.click('整理节点', scope);
  await driver.c.evaluate(
    `document.querySelector('[data-node-id="${nodes[1]}"] .wf-node-title').click();true`,
  );
  await sleep(60);
  await driver.field(
    '[aria-label="节点配置 JSON"]',
    q({ value: '来自画布真实配置。', format: 'text' }),
  );
  await driver.click('应用节点配置', scope);
  await driver.click('保存草稿', scope);
  await idle();
  let saved = (await read('list')).items.find(
    (s) => s.definition.name === 'Workflow synthetic acceptance',
  );
  assert(saved);
  driver.check(
    saved.definition.edges.length === 1,
    'Save persists typed graph and revision in SQLite',
  );
  await driver.click('校验', scope);
  await idle();
  await driver.click('发布版本', scope);
  await idle();
  let versions = (await read('versions', { workflow_id: saved.definition.workflow_id })).items;
  driver.check(
    versions.length === 1 && versions[0].version === 1,
    'Publish creates immutable v1 snapshot',
  );
  await driver.click('运行所选版本', scope);
  await idle();
  const history = (await read('runs', { conversation_id: owner })).items;
  assert(history[0]);
  const first = await waitRun(history[0].run_id, owner, ['COMPLETED', 'FAILED']);
  driver.check(
    first.state === 'COMPLETED' && first.output,
    'Original backend coordinator executes a real workflow',
  );
  await driver.c.until("!!document.querySelector('[data-workflow-state=COMPLETED]')");
  driver.check(
    await driver.c.evaluate(
      "[...document.querySelectorAll('.wf-node')].every(n=>n.dataset.state==='COMPLETED')",
    ),
    'Durable event cursor updates actual canvas node highlights',
  );
  await driver.click('执行 Trace', scope);
  await idle();
  driver.check(
    await driver.c.evaluate(
      "document.querySelector('.wf-trace')?.textContent.includes('WORKFLOW_NODE_COMPLETED')",
    ),
    'Run opens original read-only Trace events',
  );
  await driver.screenshot('workflow-builder-run');
  await driver.click('编辑草稿', scope);
  await idle();
  const before = saved.definition;
  const fixture = path.join(driver.home, 'workspace');
  fs.mkdirSync(fixture);
  fs.writeFileSync(path.join(fixture, 'fixture.txt'), 'Public workflow source fixture.\n');
  fs.writeFileSync(path.join(fixture, 'check_fixture.py'), "print('fixture tests passed')\n");
  await driver.m.evaluate(
    `(()=>{require('electron').dialog.showOpenDialog=async()=>({canceled:false,filePaths:[${q(fixture)}]});return true})()`,
  );
  await driver.c.evaluate(`window.desktopAPI.selectWorkFolder(${q(owner)})`);
  await driver.check(true, 'Workspace is selected through the original native folder IPC');
  const readGraph = structuredClone(before);
  readGraph.workflow_id = require('node:crypto').randomUUID();
  readGraph.name = 'Real original tool node';
  readGraph.nodes.splice(1, 0, {
    ...before.nodes[1],
    node_id: 'read',
    type: 'tool',
    title: '读取合成文件',
    config: { name: 'workspace.read', arguments: { path: 'fixture.txt' } },
  });
  readGraph.edges = [
    {
      edge_id: 'r1',
      source_node_id: before.nodes[0].node_id,
      source_port: 'out',
      target_node_id: 'read',
      target_port: 'in',
      condition_label: '',
    },
    {
      edge_id: 'r2',
      source_node_id: 'read',
      source_port: 'out',
      target_node_id: before.nodes[1].node_id,
      target_port: 'in',
      condition_label: '',
    },
  ];
  readGraph.nodes.at(-1).config = { value: { $ref: 'nodes.read.output.content' }, format: 'text' };
  saved = await control('save', { definition: readGraph, revision: 0 });
  const pub = await control('publish', { definition: saved.definition, revision: saved.revision });
  const actual = await control('start', {
    workflow_id: readGraph.workflow_id,
    version_id: pub.version_id,
    conversation_id: owner,
    inputs: {},
  });
  const fileRun = await waitRun(actual.run_id, owner, ['COMPLETED', 'FAILED']);
  driver.check(
    fileRun.state === 'COMPLETED' &&
      Object.values(fileRun.output).includes('Public workflow source fixture.\n'),
    'Typed Tool node uses original scoped executor and actual file content',
  );
  const record = fileRun.nodes.find((n) => n.node_id === 'read');
  const checkpoint = await driver.c.evaluate(
    `window.desktopAPI.readAgentRecovery('detail',${q({ task_id: record.recovery_task_id, conversation_id: owner, work_session_id: record.work_session_id })})`,
  );
  driver.check(
    checkpoint.checkpoints[0].step_state === 'COMMITTED',
    'Workflow operation commits the original Skill 03 checkpoint',
  );
  const importsBefore = (await read('list')).items.length;
  const exported = await control('export', { workflow_id: readGraph.workflow_id });
  const exportPath = path.join(driver.home, 'Exports/Workflows', exported.filename);
  await driver.m.evaluate(
    `(()=>{require('electron').dialog.showOpenDialog=async()=>({canceled:false,filePaths:[${q(exportPath)}]});return true})()`,
  );
  const imported = await control('import');
  driver.check(
    imported.definition.workflow_id !== readGraph.workflow_id &&
      (await read('list')).items.length === importsBefore + 1,
    'Export/import creates an inert new draft without overwriting the original',
  );
  driver.report.firstWorkflowRun = first.run_id;
  await exerciseTemplates(driver, owner);
  await exercisePerformance(driver, before);
  await exerciseRestart(driver, owner, before, openBuilder);
  await driver.close();
  await driver.launch();
  await driver.openConversation(owner);
  await openBuilder();
  const reopened = await read('run', { run_id: first.run_id, conversation_id: owner });
  driver.check(
    reopened.state === 'COMPLETED' &&
      (await read('versions', { workflow_id: before.workflow_id })).items.length === 1,
    'Restart preserves immutable versions, output and workflow history without replay',
  );
  driver.check(
    (await extRead('servers')).items.every((s) => s.connection_status === 'DISCONNECTED'),
    'Restart never auto-connects extension servers',
  );
  await exerciseComparison(driver, owner, before.workflow_id, first.run_id);
  await exerciseEvaluation(driver);
  await driver.close();
  driver.report.result = 'pass';
  driver.persist();
}

run()
  .catch(async (error) => {
    if (driver) {
      driver.report.failure = String(error.stack || error);
      driver.persist();
      await driver.screenshot('workflow-failure').catch(() => {});
    }
    console.error(error);
    process.exitCode = 1;
  })
  .finally(async () => {
    await driver?.cleanup();
  });

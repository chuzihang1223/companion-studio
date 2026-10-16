'use strict';
const assert = require('node:assert/strict');
const { q, sleep } = require('./studio-smoke-driver.cjs');

module.exports = async function restartWorkflow(driver, owner, base, openBuilder) {
  const read = (op, options = {}) =>
    driver.c.evaluate(`window.desktopAPI.readWorkflows(${q(op)},${q(options)})`);
  const control = (op, options = {}) =>
    driver.c.evaluate(`window.desktopAPI.controlWorkflows(${q(op)},${q(options)})`);
  const definition = structuredClone(base);
  definition.workflow_id = require('node:crypto').randomUUID();
  definition.name = 'Durable workflow restart';
  const shape = structuredClone(base.nodes[0]);
  definition.nodes = [
    {
      ...shape,
      node_id: 'start',
      type: 'manual_trigger',
      title: '手动开始',
      config: {},
      position: { x: 40, y: 60 },
    },
    {
      ...shape,
      node_id: 'read',
      type: 'tool',
      title: '读取合成文件',
      config: { name: 'workspace.read', arguments: { path: 'fixture.txt' } },
      position: { x: 310, y: 60 },
    },
    {
      ...shape,
      node_id: 'wait',
      type: 'human_approval',
      title: '人工检查',
      config: { message: '请检查实际文件输出后继续。' },
      position: { x: 580, y: 60 },
    },
    {
      ...shape,
      node_id: 'finish',
      type: 'output',
      title: '输出已核验数据',
      config: { value: { $ref: 'nodes.read.output.content' }, format: 'text' },
      position: { x: 850, y: 60 },
    },
  ];
  definition.edges = definition.nodes
    .slice(1)
    .map((n, i) => ({
      edge_id: 'r' + i,
      source_node_id: definition.nodes[i].node_id,
      source_port: 'out',
      target_node_id: n.node_id,
      target_port: 'in',
      condition_label: '',
    }));
  const saved = await control('save', { definition, revision: 0 });
  const published = await control('publish', {
    definition: saved.definition,
    revision: saved.revision,
  });
  const started = await control('start', {
    workflow_id: definition.workflow_id,
    version_id: published.version_id,
    conversation_id: owner,
    inputs: {},
  });
  await driver.c.until(
    `window.desktopAPI.readWorkflows('run',${q({ run_id: started.run_id, conversation_id: owner })}).then(r=>r.state==='WAITING_PERMISSION')`,
  );
  const before = await read('run', { run_id: started.run_id, conversation_id: owner });
  const originalRead = before.nodes.find((n) => n.node_id === 'read');
  assert.equal(originalRead.state, 'COMPLETED');
  await driver.close();
  await driver.launch();
  await driver.openConversation(owner);
  await openBuilder();
  await driver.m.evaluate(
    "(()=>{require('electron').dialog.showMessageBox=async(o)=>{if(!['工作流执行确认','扩展权限确认'].includes(o.title))throw Error('Unexpected fixture dialog');return {response:1}};return true})()",
  );
  const interrupted = await read('run', { run_id: started.run_id, conversation_id: owner });
  driver.check(
    interrupted.state === 'INTERRUPTED' && interrupted.version_id === before.version_id,
    'Actual application/backend restart marks unfinished Workflow interrupted and keeps the immutable version',
  );
  const preview = await read('resume-preview', { run_id: started.run_id, conversation_id: owner });
  driver.check(
    preview.can_resume &&
      preview.items.find((i) => i.node_id === 'read').action === 'KEEP_VERIFIED',
    'Restart preview rechecks the original committed file checkpoint',
  );
  await control('resume', {
    run_id: started.run_id,
    conversation_id: owner,
    revision: preview.revision,
  });
  await driver.c.until(
    `window.desktopAPI.readWorkflows('run',${q({ run_id: started.run_id, conversation_id: owner })}).then(r=>r.state==='WAITING_PERMISSION')`,
  );
  await driver.click('刷新记录', "document.querySelector('.workflow-builder')");
  await driver.c.evaluate(
    `(()=>{const s=document.querySelector('[aria-label="工作流运行历史"]');s.value=${q(started.run_id)};s.dispatchEvent(new Event('change',{bubbles:true}));return true})()`,
  );
  await driver.c.until("!!document.querySelector('.wf-approval')");
  await driver.click('批准本次节点', "document.querySelector('.workflow-builder')");
  await driver.c.until(
    `window.desktopAPI.readWorkflows('run',${q({ run_id: started.run_id, conversation_id: owner })}).then(r=>r.state==='COMPLETED')`,
  );
  await sleep(100);
  const completed = await read('run', { run_id: started.run_id, conversation_id: owner });
  const retained = completed.nodes.find((n) => n.node_id === 'read');
  driver.check(
    completed.attempt === 2 &&
      retained.attempt === 1 &&
      retained.trace_id === originalRead.trace_id &&
      completed.trace_id !== retained.trace_id,
    'Safe Workflow resume keeps verified read output and its original Trace without repeating the tool',
  );
  await driver.c.evaluate(
    "document.querySelector('[data-node-id=read] .wf-node-title').click();true",
  );
  await sleep(70);
  await driver.click('查看节点 Trace', "document.querySelector('.workflow-builder')");
  await driver.c.until("document.querySelector('.wf-trace')?.textContent.includes('TOOL_STARTED')");
  driver.check(
    await driver.c.evaluate(
      `document.querySelector('.wf-trace').textContent.includes(${q(originalRead.trace_id)})`,
    ),
    'Clicking a retained node opens its real original tool subtree, not an unrelated resume Trace',
  );
  await driver.screenshot('workflow-recovery');
  driver.report.workflowRestart = {
    run_id: completed.run_id,
    same_version: true,
    attempts: completed.attempt,
    verified_read_executions: retained.attempt,
    original_trace_retained: true,
  };
};

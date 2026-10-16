'use strict';
const { q } = require('./studio-smoke-driver.cjs');

module.exports = async function exerciseComparison(driver, owner, workflowId, firstId) {
  const read = (op, options) =>
    driver.c.evaluate(`window.desktopAPI.readWorkflows(${q(op)},${q(options)})`);
  const control = (op, options) =>
    driver.c.evaluate(`window.desktopAPI.controlWorkflows(${q(op)},${q(options)})`);
  await driver.m.evaluate(
    "(()=>{require('electron').dialog.showMessageBox=async(o)=>{if(o.title!=='工作流执行确认')throw Error('Unexpected fixture dialog');return {response:1}};return true})()",
  );
  const draft = await read('detail', { workflow_id: workflowId });
  draft.definition.nodes.at(-1).config.value = 'Second immutable version output';
  const saved = await control('save', { definition: draft.definition, revision: draft.revision });
  const version = await control('publish', {
    definition: saved.definition,
    revision: saved.revision,
  });
  const started = await control('start', {
    workflow_id: workflowId,
    version_id: version.version_id,
    conversation_id: owner,
    inputs: {},
  });
  await driver.c.until(
    `window.desktopAPI.readWorkflows('run',${q({ run_id: started.run_id, conversation_id: owner })}).then(r=>r.state==='COMPLETED')`,
  );
  await driver.click('刷新记录', "document.querySelector('.workflow-builder')");
  await driver.c.until(
    `document.querySelector('[aria-label=工作流运行历史] option[value="${started.run_id}"]')!==null`,
  );
  await driver.c.evaluate(
    `(()=>{const n=document.querySelector('[aria-label=工作流运行历史]');n.value=${q(started.run_id)};n.dispatchEvent(new Event('change',{bubbles:true}));return true})()`,
  );
  await driver.c.until("!!document.querySelector('.wf-comparison')");
  await driver.c.evaluate("document.querySelector('.wf-comparison').open=true;true");
  await driver.c.until(
    `!!document.querySelector('[aria-label=对比工作流运行] option[value="${firstId}"]')`,
  );
  const calls = driver.report.localModelRequests;
  await driver.c.evaluate(
    `(()=>{const n=document.querySelector('[aria-label=对比工作流运行]');n.value=${q(firstId)};n.dispatchEvent(new Event('change',{bubbles:true}));return true})()`,
  );
  await driver.c.until(
    "document.querySelector('[aria-label=工作流版本运行对比]')?.textContent.includes('Second immutable version output')",
  );
  const original = await read('run', { run_id: firstId, conversation_id: owner });
  const current = await read('run', { run_id: started.run_id, conversation_id: owner });
  driver.check(
    original.version === 1 && current.version === 2 && q(original.output) !== q(current.output),
    'Two immutable versions retain their distinct actual outputs',
  );
  driver.check(
    driver.report.localModelRequests === calls && current.execution_snapshot.app_version,
    'Read-only version comparison renders saved execution conditions without model or tool replay',
  );
  driver.report.workflowComparison = {
    versions: [original.version, current.version],
    model_requests_while_reading: 0,
  };
};

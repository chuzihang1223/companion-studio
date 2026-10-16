'use strict';
const assert = require('node:assert/strict');
const { performanceGraph, rect, drag } = require('./workflow-interactions.cjs');
const { q, sleep } = require('./studio-smoke-driver.cjs');

module.exports = async function performance(driver, base) {
  const results = [];
  for (const count of [20, 50, 100]) {
    const definition = performanceGraph(base, count);
    const saved = await driver.c.evaluate(
      `window.desktopAPI.controlWorkflows('save',${q({ definition, revision: 0 })})`,
    );
    const validation = await driver.c.evaluate(
      `window.desktopAPI.controlWorkflows('validate',${q({ definition: saved.definition, revision: saved.revision })})`,
    );
    await driver.click('刷新工具 / Skills', "document.querySelector('.workflow-builder')");
    const started = Date.now();
    await driver.c.evaluate(
      `(()=>{const n=document.querySelector('[aria-label="选择已保存工作流"]');n.value=${q(definition.workflow_id)};n.dispatchEvent(new Event('change',{bubbles:true}));return true})()`,
    );
    await driver.c.until(`document.querySelectorAll('.wf-node').length===${count}`);
    const render_ms = Date.now() - started;
    await driver.click('适应画布', "document.querySelector('.workflow-builder')");
    await driver.c.evaluate(
      "(()=>{const n=document.querySelector('.wf-canvas');globalThis.__wfFrameDelays=[];globalThis.__wfMeasure=(e)=>{if(!e.buttons)return;const t=performance.now();requestAnimationFrame(()=>requestAnimationFrame(()=>globalThis.__wfFrameDelays.push(performance.now()-t)))};n.addEventListener('pointermove',globalThis.__wfMeasure,{capture:true});return true})()",
    );
    const target = await rect(driver, '[data-node-id=perf_0] .wf-node-title');
    assert(target.x >= 0 && target.y > 0);
    await drag(
      driver,
      { x: target.x + target.width / 2, y: target.y + target.height / 2 },
      { x: target.x + target.width / 2 + 18, y: target.y + target.height / 2 + 12 },
    );
    const moved = await rect(driver, '[data-node-id=perf_0] .wf-node-title');
    driver.check(
      Math.abs(moved.x - target.x - 18) < 3,
      `${count}-node canvas actually follows native pointer input`,
    );
    await sleep(60);
    const latencies = await driver.c.evaluate(
      "(()=>{const a=globalThis.__wfFrameDelays;document.querySelector('.wf-canvas').removeEventListener('pointermove',globalThis.__wfMeasure,{capture:true});return a})()",
    );
    const ordered = [...latencies].sort((a, b) => a - b);
    results.push({
      nodes: count,
      edges: definition.edges.length,
      validation_ms: validation.validation_ms,
      render_ms,
      native_pointer_moves: latencies.length,
      pointer_to_second_frame_p95_ms:
        ordered[Math.min(ordered.length - 1, Math.floor(ordered.length * 0.95))] ?? null,
      fps: null,
      note: 'Observed second-rAF latency, not a hardware FPS claim.',
    });
    driver.check(
      await driver.c.evaluate(
        `document.querySelectorAll('.wf-edge').length===${definition.edges.length}`,
      ),
      `${count}-node graph renders all ${definition.edges.length} arrows`,
    );
    await driver.click('保存草稿', "document.querySelector('.workflow-builder')");
    await driver.c.until(
      "document.querySelector('.workflow-builder')?.getAttribute('aria-busy')==='false'",
    );
  }
  const before = await rect(driver, '.wf-canvas');
  await driver.m.evaluate(
    "(()=>{const w=require('electron').BrowserWindow.getAllWindows().find(w=>w.webContents.getURL().includes('/app/'));w.setBounds({width:1280,height:900});return true})()",
  );
  await sleep(200);
  const after = await rect(driver, '.wf-canvas');
  driver.check(
    after.width > 0 &&
      after.width !== before.width &&
      (await driver.c.evaluate("document.querySelectorAll('.wf-node').length===100")),
    'Outer window Resize updates canvas viewport without losing the 100-node graph',
  );
  await driver.m.evaluate(
    "(()=>{const w=require('electron').BrowserWindow.getAllWindows().find(w=>w.webContents.getURL().includes('/app/'));w.setBounds({width:1440,height:940});return true})()",
  );
  driver.report.workflowPerformance = results;
  driver.persist();
};

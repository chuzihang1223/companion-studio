'use strict';
const assert = require('node:assert/strict');
const { q, sleep } = require('./studio-smoke-driver.cjs');

async function rect(driver, selector) {
  return driver.c.evaluate(
    `(()=>{const n=document.querySelector(${q(selector)});if(!n)throw Error('Target missing');const r=n.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}})()`,
  );
}
async function mouse(driver, type, x, y, extra = {}) {
  await driver.c.call('Input.dispatchMouseEvent', { type, x, y, ...extra });
}
async function drag(driver, from, to, modifiers = 0) {
  await mouse(driver, 'mouseMoved', from.x, from.y);
  await mouse(driver, 'mousePressed', from.x, from.y, { button: 'left', clickCount: 1, modifiers });
  for (let i = 1; i <= 6; i++) {
    await mouse(
      driver,
      'mouseMoved',
      from.x + ((to.x - from.x) * i) / 6,
      from.y + ((to.y - from.y) * i) / 6,
      { button: 'left', buttons: 1, modifiers },
    );
    await sleep(18);
  }
  await mouse(driver, 'mouseReleased', to.x, to.y, { button: 'left', clickCount: 1, modifiers });
  await sleep(80);
}
async function exerciseCanvas(driver, nodes) {
  const moduleBefore = await rect(driver, '[data-cell-module=workflow_builder]');
  const a = await rect(driver, `[data-node-id="${nodes[0]}"] .wf-node-title`);
  await drag(driver, { x: a.x + 60, y: a.y + 15 }, { x: a.x + 110, y: a.y + 35 });
  const moved = await rect(driver, `[data-node-id="${nodes[0]}"] .wf-node-title`);
  assert(
    Math.abs(moved.x - a.x - 50) < 3 && Math.abs(moved.y - a.y - 20) < 3,
    'Actual canvas pointer follows drag delta',
  );
  const moduleAfter = await rect(driver, '[data-cell-module=workflow_builder]');
  driver.check(
    Math.abs(moduleBefore.x - moduleAfter.x) < 1 && Math.abs(moduleBefore.y - moduleAfter.y) < 1,
    'Node pointer capture does not drag the outer Workbench module',
  );
  const output = await rect(driver, `[data-output-node="${nodes[0]}"][data-port=out]`);
  const input = await rect(driver, `[data-input-node="${nodes[1]}"]`);
  await drag(
    driver,
    { x: output.x + output.width / 2, y: output.y + output.height / 2 },
    { x: input.x + input.width / 2, y: input.y + input.height / 2 },
  );
  driver.check(
    await driver.c.evaluate("document.querySelectorAll('.wf-edge').length===1"),
    'Native Chromium pointer drag connects output to input',
  );
  const b = await rect(driver, `[data-node-id="${nodes[1]}"] .wf-node-title`);
  await mouse(driver, 'mousePressed', b.x + 45, b.y + 14, {
    button: 'left',
    clickCount: 1,
    modifiers: 8,
  });
  await mouse(driver, 'mouseReleased', b.x + 45, b.y + 14, {
    button: 'left',
    clickCount: 1,
    modifiers: 8,
  });
  driver.check(
    await driver.c.evaluate("document.querySelectorAll('.wf-node.is-selected').length===2"),
    'Shift selection keeps multiple canvas nodes selected',
  );
  const canvas = await rect(driver, '.wf-canvas');
  const oldZoom = await driver.c.evaluate(
    "document.querySelector('.wf-canvas-tools output').textContent",
  );
  await mouse(driver, 'mouseWheel', canvas.x + canvas.width / 2, canvas.y + 100, {
    deltaX: 0,
    deltaY: -180,
  });
  await sleep(120);
  driver.check(
    await driver.c.evaluate(
      `document.querySelector('.wf-canvas-tools output').textContent!==${q(oldZoom)}`,
    ),
    'Native wheel zoom updates canvas viewport',
  );
  await driver.click('适应画布', "document.querySelector('.workflow-builder')");
  const selected = await rect(driver, `[data-node-id="${nodes[0]}"] .wf-node-title`);
  const beforeAbort = await driver.c.evaluate(
    `document.querySelector('[data-node-id="${nodes[0]}"]').style.left`,
  );
  await mouse(driver, 'mousePressed', selected.x + 50, selected.y + 12, {
    button: 'left',
    clickCount: 1,
  });
  await mouse(driver, 'mouseMoved', selected.x + 90, selected.y + 40, {
    button: 'left',
    buttons: 1,
  });
  await driver.c.call('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Escape', code: 'Escape' });
  await mouse(driver, 'mouseReleased', selected.x + 90, selected.y + 40, { button: 'left' });
  await sleep(60);
  driver.check(
    await driver.c.evaluate(
      `document.querySelector('[data-node-id="${nodes[0]}"]').style.left===${q(beforeAbort)}`,
    ),
    'Escape cancels preview and releases pointer capture without committing',
  );
}

function performanceGraph(base, count) {
  const graph = structuredClone(base);
  graph.workflow_id = require('node:crypto').randomUUID();
  graph.name = `Canvas performance ${count}`;
  graph.nodes = Array.from({ length: count }, (_, i) => ({
    ...structuredClone(base.nodes[0]),
    node_id: 'perf_' + i,
    type: i === 0 ? 'manual_trigger' : i === count - 1 ? 'output' : 'transform',
    title: '节点 ' + (i + 1),
    config: i === 0 ? {} : i === count - 1 ? { value: 'done' } : { operation: 'length', value: [] },
    position: { x: (i % 10) * 250, y: Math.floor(i / 10) * 155 },
  }));
  graph.edges = [];
  const connect = (a, b) =>
    graph.edges.push({
      edge_id: 'p_' + graph.edges.length,
      source_node_id: 'perf_' + a,
      source_port: 'out',
      target_node_id: 'perf_' + b,
      target_port: 'in',
      condition_label: '',
    });
  for (let i = 0; i < count - 1; i++) connect(i, i + 1);
  if (count === 100) {
    for (let i = 0; i < 98; i++) connect(i, i + 2);
    connect(0, 3);
    connect(0, 4);
    connect(0, 5);
  }
  return graph;
}

module.exports = { exerciseCanvas, performanceGraph, rect, drag, mouse };

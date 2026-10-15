'use strict';

const assert = require('node:assert/strict');
const { q, sleep } = require('./studio-smoke-driver.cjs');

async function center(driver, selector) {
  return driver.c.evaluate(
    `(()=>{const n=document.querySelector(${q(selector)});if(!n||n.closest('[hidden]'))throw Error('Visible gesture target missing');n.scrollIntoView({block:'nearest'});const r=n.getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2,width:r.width,height:r.height}})()`,
  );
}

async function mouse(driver, type, point, held = false) {
  await driver.c.call('Input.dispatchMouseEvent', {
    type,
    x: point.x,
    y: point.y,
    button: type === 'mouseMoved' ? 'none' : 'left',
    buttons: held ? 1 : 0,
    clickCount: type === 'mouseMoved' ? 0 : 1,
  });
}

async function readLayout(driver) {
  const key = await driver.c.evaluate("document.querySelector('.workbench').dataset.layoutKey");
  const layout = await driver.c.evaluate(`window.desktopAPI.workbenchLayout(${q(key)})`);
  return { key, layout };
}

/** Genuine Chromium input drives production pointer handlers, not layout helper calls. */
async function exerciseWorkbench(driver) {
  await driver.click('恢复默认布局');
  await driver.c.until("!!document.querySelector('[data-cell-module=conversation]:not([hidden])')");
  const source = await center(driver, '[data-dock-module="code"]');
  await mouse(driver, 'mouseMoved', source);
  await mouse(driver, 'mousePressed', source, true);
  await sleep(230); // Production drag threshold is a long press, not immediate pointer movement.
  await mouse(driver, 'mouseMoved', { x: source.x + 12, y: source.y + 12 }, true);
  await driver.c.until(
    "!!document.querySelector('[data-drop-module=conversation][data-drop-edge=right]')",
  );
  const target = await center(driver, '[data-drop-module="conversation"][data-drop-edge="right"]');
  await mouse(driver, 'mouseMoved', target, true);
  await sleep(100);
  await mouse(driver, 'mouseReleased', target);
  await driver.c.until(
    "!!document.querySelector('[data-cell-module=code]:not([hidden])') && !document.querySelector('.module-drag-preview')",
  );
  await sleep(350);
  const dragged = await readLayout(driver);
  driver.check(
    dragged.layout && !dragged.layout.modules.code.closed && !dragged.layout.modules.code.minimized,
    'Real pointer drag opens code next to the conversation and saves the layout',
  );

  const dividerSelector = '.workspace-resize-divider[data-divider-axis="x"]';
  const before = await readLayout(driver);
  const handle = await center(driver, dividerSelector);
  await mouse(driver, 'mouseMoved', handle);
  await mouse(driver, 'mousePressed', handle, true);
  await driver.c.until("!!document.querySelector('.workspace-free.is-resizing')");
  const started = Date.now();
  for (let i = 1; i <= 30; i++)
    await mouse(driver, 'mouseMoved', { x: handle.x + i * 2, y: handle.y }, true);
  await mouse(driver, 'mouseReleased', { x: handle.x + 60, y: handle.y });
  await driver.c.until("!document.querySelector('.workspace-free.is-resizing')");
  driver.check(
    await driver.c.evaluate(
      "![...document.querySelectorAll('.module-resize-handle,.workspace-resize-divider')].some(node=>node.hasPointerCapture(1))",
    ),
    'Mouse pointer capture is released at the end of the real resize gesture',
  );
  await sleep(350);
  const after = await readLayout(driver);
  const changed = Object.keys(after.layout.modules).filter(
    (id) => Math.abs(after.layout.modules[id].width - before.layout.modules[id].width) > 1,
  );
  driver.check(
    changed.length >= 2,
    'Real shared divider resize changes adjacent modules and clears pointer capture',
    { modules: changed, mouseMoves: 30, gestureWallMs: Date.now() - started },
  );
  driver.check(
    after.key === before.key,
    'Resize persists under the original work session layout key',
  );
  await driver.screenshot('workbench-after-pointer-gestures');
  return after;
}

function geometry(layout) {
  return Object.fromEntries(
    Object.entries(layout.modules).map(([id, row]) => [
      id,
      {
        x: row.x,
        y: row.y,
        width: row.width,
        height: row.height,
        closed: row.closed,
        minimized: row.minimized,
      },
    ]),
  );
}

async function verifyRestoredWorkbench(driver, previous) {
  await driver.c.until(
    "!!document.querySelector('.workspace-grid') && document.querySelector('.workbench').dataset.layoutKey===" +
      q(previous.key),
  );
  const restored = await readLayout(driver);
  assert.deepEqual(
    geometry(restored.layout),
    geometry(previous.layout),
    'Stored geometry survives application restart',
  );
  driver.check(
    true,
    'Dragged/resized module geometry is restored after actual application restart',
  );
  driver.check(
    await driver.c.evaluate("!!document.querySelector('[data-cell-module=code]:not([hidden])')"),
    'Restored code module is actually visible in the renderer',
  );
}

module.exports = { exerciseWorkbench, verifyRestoredWorkbench };

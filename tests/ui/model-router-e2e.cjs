'use strict';
// Real renderer/IPC/backend/SQLite. Only the loopback model is synthetic.
const { StudioSmokeDriver, q, sleep } = require('./studio-smoke-driver.cjs');
let driver;
const pane = `document.querySelector('[aria-label="智能模型路由"]')`;
async function run() {
  driver = new StudioSmokeDriver();
  await driver.prepare();
  await driver.launch();
  await driver.c.evaluate(
    `window.desktopAPI.applyApiPreset({name:'Router local fixture',provider:'openai-compatible',base_url:'http://127.0.0.1:${driver.ports.model}/v1',api_key:'isolated-test-key',model:'local-smoke',capabilities:{text:true,streaming:true,tool_calling:false},timeout_seconds:20})`,
  );
  const config = await driver.c.evaluate('window.desktopAPI.listApiPresets()');
  driver.check(
    !q(config).includes('isolated-test-key'),
    'API list never returns plaintext credential',
  );
  await driver.click('设置');
  await driver.click('API', `document.querySelector('.settings-navigation')`);
  await driver.c.until('!!' + pane);
  driver.check(
    await driver.c.evaluate(`${pane}.querySelector('input[type=checkbox]').checked===false`),
    'Router starts disabled in a new profile',
  );
  const before = driver.report.localModelRequests;
  await driver.c.evaluate(`${pane}.querySelector('input[type=checkbox]').click();true`);
  await driver.c.evaluate(
    `(()=>{const n=${pane}.querySelector('[aria-label="路由模式"]');n.value='AUTO';n.dispatchEvent(new Event('change',{bubbles:true}));return true})()`,
  );
  await driver.c.evaluate(
    `${pane}.querySelector('details').open=true;${pane}.querySelector('details').querySelectorAll('input[type=checkbox]')[1].click();true`,
  );
  await driver.field('[aria-label="智能模型路由"] details input[type=number]', '4096');
  await driver.click('保存模型路由', pane);
  await driver.c.until(`${pane}.innerText.includes('路由设置已保存')`);
  let status = await driver.c.evaluate('window.desktopAPI.modelRoutingStatus()');
  driver.check(
    status.policy.enabled && status.policy.mode === 'AUTO',
    'UI saves enabled AUTO routing through guarded main IPC',
  );
  driver.check(
    status.models.some((m) => m.allow_auto && m.context_window === 4096),
    'Explicit model auto consent and capacity persist',
  );
  driver.check(
    driver.report.localModelRequests === before,
    'Saving routing emits no model probe or warmup request',
  );
  const response = await fetch(driver.base + '/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: q({ character_id: 'assistant', message: 'Synthetic routing UI acceptance' }),
  });
  driver.check(response.ok, 'Actual backend chat uses selected original model provider');
  await driver.click('刷新路由状态', pane);
  await sleep(150);
  status = await driver.c.evaluate('window.desktopAPI.modelRoutingStatus()');
  driver.check(
    status.recent_requests.some((r) => r.status === 'COMPLETED' && r.cost_status === 'UNKNOWN'),
    'Unknown model cost remains UNKNOWN after a real loopback request',
  );
  driver.check(
    status.models.some((m) => m.metrics.total.n > 0 && m.by_task.CHAT),
    'Measured latency has a task type and actual sample count',
  );
  await driver.c.evaluate(
    `(()=>{const s=[...${pane}.querySelectorAll('summary')].find(n=>n.textContent.includes('最近请求与 Usage'));s.parentElement.open=true;return true})()`,
  );
  driver.check(
    await driver.c.evaluate(`${pane}.innerText.includes('费用未知')`),
    'UI distinguishes unknown cost from free',
  );
  await driver.screenshot('model-router-auto-status');
  await driver.close();
  await driver.launch();
  status = await driver.c.evaluate('window.desktopAPI.modelRoutingStatus()');
  driver.check(
    status.policy.enabled && status.policy.mode === 'AUTO' && status.recent_requests.length > 0,
    'Routing policy and durable usage survive an actual application restart',
  );
  await driver.close();
  driver.report.result = 'PASS';
  driver.persist();
}
run()
  .catch((error) => {
    if (driver) {
      driver.report.result = 'FAIL';
      driver.report.error = String(error.stack || error);
    }
    process.exitCode = 1;
  })
  .finally(async () => {
    await driver?.cleanup();
  });

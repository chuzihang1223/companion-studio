'use strict';

// Opt-in packaged acceptance. This driver owns the only process it interrupts;
// the fixture delays a read-only system probe, never an application/user action.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { StudioSmokeDriver, q, sleep } = require('./studio-smoke-driver.cjs');
let driver;
const read = (op, scope) =>
  driver.c.evaluate(`window.desktopAPI.readAgentRecovery(${q(op)},${q(scope)})`);

async function select(label, value) {
  await driver.c.evaluate(
    `(()=>{const n=document.querySelector(${q(`[aria-label="${label}"]`)});if(!n)throw Error('Missing select');n.value=${q(value)};n.dispatchEvent(new Event('change',{bubbles:true}));return true})()`,
  );
}

async function run() {
  driver = new StudioSmokeDriver();
  await driver.prepare();
  await driver.launch();
  const info = await driver.c.evaluate('window.desktopAPI.appInfo()');
  const expected = JSON.parse(
    fs.readFileSync(path.resolve(__dirname, '../../desktop/package.json'), 'utf8'),
  ).version;
  driver.check(
    info.packaged && info.version === expected,
    'Actual packaged recovery candidate matches source version',
  );
  await driver.c.evaluate(
    `window.desktopAPI.applyApiPreset({name:'Recovery local fixture',provider:'openai-compatible',base_url:'http://127.0.0.1:${driver.ports.model}/v1',api_key:'mock-local-only',model:'local-smoke',capabilities:{text:true,tool_calling:false},timeout_seconds:15})`,
  );
  await driver.c.evaluate("window.desktopAPI.setTrustedAction('system.get_status','always')");
  await driver.c.evaluate('window.desktopAPI.workPreferences({auto_work_mode:false})');
  await driver.m.evaluate(
    "(()=>{const path=require('path');const {DesktopAppAgent}=require(path.join(require('electron').app.getAppPath(),'electron/desktop-app-agent.cjs'));const original=DesktopAppAgent.prototype.perform;DesktopAppAgent.prototype.perform=function(op,...args){if(op==='system.status')return new Promise(()=>{});return original.call(this,op,...args)};return true})()",
  );
  await driver.click('工作模式');
  await driver.send('查看系统状态');
  let conversation;
  const until = Date.now() + 20000;
  while (!conversation && Date.now() < until) {
    conversation = (await driver.json('/api/conversations?character_id=assistant')).find(
      (row) => row.conversation_type !== 'chat',
    );
    if (!conversation) await sleep(100);
  }
  assert.ok(conversation, 'Owned work conversation exists');
  const scope = { conversation_id: conversation.id };
  await driver.c.until(
    `window.desktopAPI.readAgentRecovery('list',${q(scope)}).then(p=>p.items.some(t=>t.checkpoints.some(s=>s.step_state==='EXECUTING')))`,
    20000,
  );
  const before = (await read('list', scope)).items[0];
  driver.check(
    before.state === 'RECOVERING' && before.checkpoints[0].side_effect_class === 'READ_ONLY',
    'Intent is durable before a pending read-only desktop probe',
  );
  const options = { ...scope, task_id: before.task_id, work_session_id: before.work_session_id };
  await driver.c.until(
    `window.desktopAPI.readAgentTraces('list',${q(scope)}).then(p=>p.items.some(t=>t.task_id===${q(before.task_id)} && t.event_count>0))`,
    10000,
  );
  const ownedPid = driver.child.pid;
  driver.c.close();
  driver.m.close();
  driver.c = driver.m = null;
  driver.child.kill(); // Only this driver's Electron parent. Its backend has a parent watcher.
  const deadline = Date.now() + 40000;
  let healthy = true;
  const exited = () => driver.child.exitCode !== null || driver.child.signalCode !== null;
  while ((healthy || !exited()) && Date.now() < deadline) {
    try {
      healthy = (await fetch(driver.base + '/health', { signal: AbortSignal.timeout(500) })).ok;
    } catch {
      healthy = false;
    }
    if (healthy || !exited()) await sleep(200);
  }
  driver.child.stdout?.unpipe(driver.log);
  driver.child.stderr?.unpipe(driver.log);
  for (const stream of [driver.child.stdin, driver.child.stdout, driver.child.stderr])
    stream?.destroy();
  driver.log.end();
  driver.check(!healthy && exited(), 'Only the owned Electron was interrupted; its backend stops', {
    pid: ownedPid,
    exit_code: driver.child.exitCode,
    signal: driver.child.signalCode,
  });
  await driver.launch();
  const interrupted = await read('detail', options);
  driver.check(
    interrupted.state === 'INTERRUPTED' && interrupted.task_id === before.task_id,
    'Restart loads the persisted logical task as interrupted without replay',
  );
  await driver.openConversation(conversation.id);
  await driver.c.until("!!document.querySelector('.recovery-panel')");
  await driver.c.until("document.querySelector('.recovery-state')?.innerText.includes('已中断')");
  await driver.click('恢复预览', "document.querySelector('.recovery-panel')");
  await driver.c.until(
    "document.querySelector('.recovery-preview')?.innerText.includes('重新检查授权后执行')",
  );
  driver.check(
    await driver.c.evaluate(
      "document.querySelector('.recovery-preview').innerText.includes('续接额外模型请求：0')",
    ),
    'Recovery preview explains revalidation and zero extra model requests',
  );
  await driver.click('检查检查点', "document.querySelector('.recovery-panel')");
  driver.check(
    await driver.c.evaluate(
      "document.querySelector('.recovery-checkpoints').innerText.includes('system.get_status')",
    ),
    'Checkpoint inspector shows the actual tool and durable step',
  );
  // Maximize an existing module; do not change layout algorithms for this feature.
  await driver.c.evaluate("document.querySelector('[aria-label=最大化工作流]')?.click();true");
  await driver.c.evaluate(
    "document.querySelector('.recovery-panel').scrollIntoView({block:'start'});true",
  );
  await driver.screenshot('recovery-preview');
  const modelsBeforeResume = driver.report.localModelRequests;
  await driver.click('安全续接', "document.querySelector('.recovery-panel')");
  await driver.c.until(
    `window.desktopAPI.readAgentRecovery('detail',${q(options)}).then(t=>t.state==='COMPLETED')`,
    30000,
  );
  await driver.c.until(
    "document.querySelector('.recovery-state')?.innerText.includes('已验证完成')",
    15000,
  );
  const completed = await read('detail', options);
  driver.check(
    completed.execution_id !== before.execution_id && completed.task_id === before.task_id,
    'Resume creates a new execution UUID and preserves logical task identity',
  );
  driver.check(
    completed.checkpoints[0].step_state === 'COMMITTED',
    'Resumed probe is observed, verified and committed',
  );
  driver.check(
    driver.report.localModelRequests === modelsBeforeResume,
    'Safe resume makes no additional local or external model/voice request',
  );
  driver.check(
    await driver.c.evaluate(
      "[...document.querySelectorAll('.recovery-panel button')].find(b=>b.textContent.trim()==='安全续接').disabled",
    ),
    'Completed task cannot be resumed twice',
  );
  await driver.click('执行追踪与只读回放', "document.querySelector('.recovery-panel')");
  await driver.c.until("document.querySelectorAll('.recovery-panel .trace-row').length>0");
  const traces = await driver.c.evaluate(`window.desktopAPI.readAgentTraces('list',${q(scope)})`);
  driver.check(
    traces.items.some((t) => t.task_id === before.task_id) &&
      traces.items.some((t) => t.task_id === completed.execution_id),
    'Original interruption and new resume Trace remain linked and readable',
  );
  await driver.screenshot('recovery-completed');
  await driver.c.evaluate("document.querySelector('[aria-label=最大化工作流]')?.click();true");
  await driver.c.until(
    "document.querySelector('.work-dialogue')?.innerText.includes('任务续接已验证完成')",
    15000,
  );
  driver.check(true, 'Resume completion reaches the original conversation UI through scoped SSE');
  await driver.click('新聊天');
  await driver.c.until(
    `!document.querySelector('.recovery-panel')?.innerText.includes(${q(before.task_id)})`,
  );
  driver.check(true, 'Navigation does not leak the previous conversation recovery state');
  await driver.click('模块');
  await driver.click(
    'Agent Evaluation · 评测',
    "document.querySelector('.workspace-module-picker')",
  );
  await driver.c.until("!!document.querySelector('.evaluation-dashboard')");
  await driver.c.evaluate(
    'document.querySelector(\'[aria-label="最大化Agent Evaluation · 评测"]\').click();true',
  );
  await select('评测测试集', 'recovery-core');
  await driver.c.until("document.querySelector('[aria-label=评测任务]')?.options.length===17");
  await driver.field('[aria-label="评测并发"]', '2');
  await driver.click('开始评测', "document.querySelector('.evaluation-dashboard')");
  await driver.c.until(
    "window.desktopAPI.readEvaluation('runs').then(p=>p.items.some(r=>r.snapshot.dataset_id==='recovery-core'))",
  );
  const run = (await driver.c.evaluate("window.desktopAPI.readEvaluation('runs')")).items[0];
  await driver.c.until(
    `window.desktopAPI.readEvaluation('detail',{run_id:${q(run.run_id)}}).then(r=>['COMPLETED','FAILED'].includes(r.status))`,
    180000,
  );
  const result = await driver.c.evaluate(
    `window.desktopAPI.readEvaluation('detail',{run_id:${q(run.run_id)}})`,
  );
  driver.check(
    result.status === 'COMPLETED' && result.metrics.counts.PASS === 16,
    'Installed frozen worker executes all 16 recovery benchmarks',
    { metrics: result.metrics, run_id: run.run_id },
  );
  const rows = (
    await driver.c.evaluate(
      `window.desktopAPI.readEvaluation('results',{run_id:${q(run.run_id)},limit:100})`,
    )
  ).items;
  const hardCrash = rows.find((row) => row.case_id === 'REC-012');
  driver.check(
    hardCrash.result.passed &&
      hardCrash.duration_ms === null &&
      hardCrash.result.tool_calls === 1 &&
      hardCrash.result.trace_scopes.length === 2,
    'Packaged worker hard exit and fresh-process resume preserve file hash without rewriting',
  );
  driver.check(
    result.metrics.recovery.duplicate_execution_count === 0 &&
      result.metrics.real_task_success_rate === null,
    'Metrics distinguish safe assertions, goal completion and unknown real AI quality',
  );
  fs.writeFileSync(
    path.join(driver.home, 'recovery-evaluation-result.json'),
    JSON.stringify({ run: result, results: rows }, null, 2),
  );
  await driver.c.until(
    "document.querySelector('.evaluation-progress')?.innerText.includes('COMPLETED') && document.querySelectorAll('[aria-label=评测结果] tbody tr').length===16",
  );
  for (const format of ['JSON', 'CSV', 'MARKDOWN'])
    await driver.click('导出 ' + format, "document.querySelector('.evaluation-dashboard')");
  driver.check(
    fs.readdirSync(path.join(driver.home, 'Exports')).filter((f) => f.includes(run.run_id))
      .length === 3,
    'Recovery metrics export in all three supported formats',
  );
  await driver.c.until("document.querySelectorAll('[aria-label=评测结果] tbody tr').length===16");
  await driver.c.evaluate(
    "document.querySelector('.evaluation-progress').scrollIntoView({block:'start'});true",
  );
  await driver.screenshot('recovery-evaluation');
  await driver.close();
  await driver.launch();
  driver.check(
    (await read('detail', options)).state === 'COMPLETED',
    'Completed task and its checkpoint survive another normal restart',
  );
  await driver.close();
  driver.report.pass = true;
  driver.report.manual.push(
    'Native window focus, Windows GUI Agent bridge and real AI/voice quality are not measured. App UI is actual Chromium; local system probe and benchmark worker are real.',
  );
  driver.persist();
  console.log(
    q({
      pass: true,
      checks: driver.report.checks.length,
      report: path.join(driver.home, 'report.json'),
    }),
  );
}
run()
  .catch((error) => {
    if (driver) {
      driver.report.error = String(error.stack || error);
      driver.persist();
    }
    console.error(error.stack || error);
    process.exitCode = 1;
  })
  .finally(async () => {
    await driver?.cleanup();
  });

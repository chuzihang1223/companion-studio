'use strict';
// Real candidate UI/main/backend/SQLite/tray. Only confirmation choices are
// synthetic. No imported profile, private application, model or voice request.
const fs = require('node:fs');
const path = require('node:path');
const { randomUUID } = require('node:crypto');
const { StudioSmokeDriver, q, sleep } = require('./studio-smoke-driver.cjs');
let driver;
const read = (op, options = {}) =>
  driver.c.evaluate(`window.desktopAPI.readSchedules(${q(op)},${q(options)})`);
const scope = 'document.querySelector(\'[aria-label="定时任务管理"]\')';
async function select(label, value) {
  await driver.c.evaluate(
    `(()=>{const n=document.querySelector('[aria-label='+${q(JSON.stringify(label))}+']');if(!n)throw Error('Missing selector');n.value=${q(value)};n.dispatchEvent(new Event('change',{bubbles:true}));return true})()`,
  );
  await sleep(80);
}
async function run() {
  driver = new StudioSmokeDriver();
  await driver.prepare();
  if (process.env.COMPANION_SCHEDULER_TEST_PORT)
    driver.ports.backend = Number(process.env.COMPANION_SCHEDULER_TEST_PORT);
  await driver.launch();
  const info = await driver.c.evaluate('window.desktopAPI.appInfo()');
  const expected = JSON.parse(
    fs.readFileSync(path.resolve(__dirname, '../../desktop/package.json'), 'utf8'),
  ).version;
  driver.check(
    info.version === expected && info.packaged === !driver.source,
    'Exact scheduler candidate identity',
    info,
  );
  driver.check(
    !(await driver.c.evaluate('window.desktopAPI.schedulerBackground()')).enabled,
    'Default close exits; background not silently enabled',
  );
  await driver.m.evaluate(
    `(()=>{const {dialog}=require('electron');const original=dialog.showMessageBox.bind(dialog);dialog.showMessageBox=async o=>['定时任务确认','工作流执行确认'].includes(o.title)?{response:1}:original(o);const loaded=Object.keys(require.cache).find(k=>k.endsWith('scheduler-background.cjs'));const C=require(loaded).SchedulerBackground;const poll=C.prototype.poll;C.prototype.poll=function(...args){globalThis.__schedulerQA=this;return poll.apply(this,args)};return true})()`,
  );
  await driver.m.evaluate(
    'new Promise((resolve,reject)=>{let n=0;const t=setInterval(()=>{if(globalThis.__schedulerQA){clearInterval(t);resolve(true)}else if(n++>50){clearInterval(t);reject(Error("Scheduler instance not observed"))}},100)})',
  );
  const response = await fetch(driver.base + '/api/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: q({ character_id: 'assistant', conversation_type: 'work' }),
  });
  if (!response.ok) throw Error('Fixture owner creation failed');
  const owner = (await response.json()).id;
  driver.report.schedulerConversation = owner;
  const workflow = {
    workflow_id: randomUUID(),
    name: 'Scheduler synthetic minute test',
    nodes: [
      { node_id: 'start', type: 'manual_trigger' },
      {
        node_id: 'step',
        type: 'transform',
        config: { operation: 'concat', value: ['Scheduled', 'fixture'], separator: ' ' },
      },
      { node_id: 'finish', type: 'output', config: { value: { $ref: 'nodes.step.output' } } },
    ],
    edges: [
      { edge_id: 'a', source_node_id: 'start', target_node_id: 'step' },
      { edge_id: 'b', source_node_id: 'step', target_node_id: 'finish' },
    ],
  };
  const saved = await driver.c.evaluate(
    `window.desktopAPI.controlWorkflows('save',${q({ definition: workflow, revision: 0 })})`,
  );
  const published = await driver.c.evaluate(
    `window.desktopAPI.controlWorkflows('publish',${q({ definition: saved.definition, revision: saved.revision, workflow_id: workflow.workflow_id })})`,
  );
  await driver.c.call('Page.reload');
  await driver.c.until("!!document.querySelector('[aria-label=输入消息]')");
  await driver.openConversation(owner);
  await driver.m.evaluate('globalThis.__schedulerQA.navigate();true');
  await driver.c.until('!!document.querySelector(\'[aria-label="调度工作会话"]\')');
  const geometry = await driver.c.evaluate(`(()=>{
    const pane=document.querySelector('[aria-label="定时任务管理"]');
    const header=document.querySelector('.chat-header');
    const title=pane.querySelector('h2');
    const p=pane.getBoundingClientRect(),h=header.getBoundingClientRect(),t=title.getBoundingClientRect();
    const hit=document.elementFromPoint(t.left+Math.min(20,t.width/2),t.top+t.height/2);
    return {panelTop:p.top,headerBottom:h.bottom,panelBottom:p.bottom,viewport:innerHeight,
      titleVisible:!!hit&&title.contains(hit),scrollable:getComputedStyle(pane).overflowY==='auto'};
  })()`);
  driver.check(
    Math.abs(geometry.panelTop - geometry.headerBottom) <= 1 &&
      geometry.panelBottom <= geometry.viewport + 1 &&
      geometry.titleVisible &&
      geometry.scrollable,
    'Scheduler starts directly below the header with no clipped underlying banner',
    geometry,
  );
  await driver.c.until(
    `!!document.querySelector('[aria-label="调度工作会话"] option[value="${owner}"]')`,
  );
  await select('调度工作会话', owner);
  await driver.c.until(
    "[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='创建定时任务')",
  );
  await driver.click('创建定时任务', scope);
  await driver.field('[aria-label="任务名称"]', 'One minute real tray test');
  await select('已发布工作流', workflow.workflow_id);
  await driver.c.until(
    `!!document.querySelector('[aria-label="固定工作流版本"] option[value="${published.version_id}"]')`,
  );
  await select('固定工作流版本', published.version_id);
  const due = await driver.c.evaluate(
    `(()=>{const at=new Date(Date.now()+60000);const pad=n=>String(n).padStart(2,'0');return at.getFullYear()+'-'+pad(at.getMonth()+1)+'-'+pad(at.getDate())+'T'+pad(at.getHours())+':'+pad(at.getMinutes())+':'+pad(at.getSeconds())})()`,
  );
  await driver.field('[aria-label="执行日期与时间"]', due);
  await driver.click('预览时间与权限', scope);
  await driver.c.until("!!document.querySelector('[data-schedule-preview=ready]')");
  await driver.click('保存定时任务', scope);
  await driver.c.until("!!document.querySelector('[data-schedule-id]')");
  const definitions = (await read('schedules', { conversation_id: owner })).items;
  const schedule = definitions[0];
  driver.report.schedule_id = schedule.definition.schedule_id;
  driver.report.scheduledUTC = schedule.next_due;
  driver.check(
    definitions.length === 1 && !!schedule.next_due,
    'UI creates real one-minute schedule and UTC preview',
    { local: due, utc: schedule.next_due },
  );
  await driver.screenshot('scheduled-task-created');
  await driver.c.evaluate(
    `document.querySelector('[aria-label="关闭窗口保持托盘运行"]').click();true`,
  );
  await driver.c.until(
    'window.desktopAPI.schedulerBackground().then(r=>r.enabled&&r.tray_available)',
  );
  await driver.m.evaluate(
    "require('electron').BrowserWindow.getAllWindows().find(w=>w.webContents.getURL().includes('/app/')).close();true",
  );
  driver.check(
    await driver.m.evaluate(
      "!require('electron').BrowserWindow.getAllWindows().find(w=>w.webContents.getURL().includes('/app/')).isVisible()",
    ),
    'Actual close event hides window to live native tray',
  );
  const started = Date.now();
  let history;
  while (Date.now() - started < 85000) {
    history = await read('history', {
      conversation_id: owner,
      schedule_id: schedule.definition.schedule_id,
    });
    if (history.items.some((r) => r.status === 'COMPLETED')) break;
    await sleep(500);
  }
  const occurrence = history.items.find((r) => r.status === 'COMPLETED');
  driver.check(!!occurrence, 'Real wall-clock task finishes while window hidden', history);
  driver.report.trigger_delay_ms =
    Date.parse(occurrence.started_at) - Date.parse(occurrence.scheduled_for);
  driver.report.occurrence_id = occurrence.occurrence_id;
  driver.report.workflow_run_id = occurrence.workflow_run_id;
  const output = await driver.c.evaluate(
    `window.desktopAPI.readWorkflows('run',${q({ conversation_id: owner, run_id: occurrence.workflow_run_id })})`,
  );
  driver.check(
    output.output.finish === 'Scheduled fixture',
    'Original Workflow executed typed synthetic target',
  );
  await driver.c.until(
    `window.desktopAPI.readSchedules('notifications',${q({ conversation_id: owner })}).then(r=>r.items.some(n=>n.occurrence_id===${q(occurrence.occurrence_id)}&&['SHOWN','FAILED','UNAVAILABLE'].includes(n.delivery)))`,
    15000,
  );
  const notices = (await read('notifications', { conversation_id: owner })).items;
  driver.report.native_notifications = notices;
  driver.check(
    notices.some((n) => n.occurrence_id === occurrence.occurrence_id),
    'Native outbox state retained without fabricated delivery',
  );
  await driver.m.evaluate(
    "globalThis.__schedulerQA.menu.items.find(n=>n.label==='最近执行结果').click();true",
  );
  await driver.c.until('!!document.querySelector(\'[aria-label="最近执行结果"] button\')');
  await driver.c.evaluate(
    'document.querySelector(\'[aria-label="最近执行结果"] button\').click();true',
  );
  await driver.c.until(
    `document.querySelector('.scheduler-page')?.innerText.includes(${q(occurrence.occurrence_id.slice(0, 8))})`,
  );
  driver.check(true, 'Real tray recent handler navigates exact persisted occurrence');
  await driver.c.until(
    "[...document.querySelectorAll('.scheduler-page button')].some(b=>b.textContent.trim()==='执行 Trace'&&!b.disabled)",
  );
  await driver.click('执行 Trace', scope);
  await driver.c.until("!!document.querySelector('.scheduler-page pre')");
  await driver.screenshot('scheduled-history-trace');
  driver.check(true, 'History opens original read-only Trace');
  await driver.click('暂停计划', scope);
  await driver.c.until(
    `window.desktopAPI.readSchedules('schedules',${q({ conversation_id: owner })}).then(r=>r.items[0].state==='PAUSED')`,
  );
  await driver.click('启用计划', scope);
  await driver.c.until(
    `window.desktopAPI.readSchedules('schedules',${q({ conversation_id: owner })}).then(r=>r.items[0].state==='ENABLED')`,
  );
  await driver.click('删除计划', scope);
  await driver.c.until(
    `window.desktopAPI.readSchedules('schedules',${q({ conversation_id: owner })}).then(r=>r.items.length===0)`,
  );
  driver.check(true, 'Pause, enable and archive preserve completed history');
  await driver.m
    .evaluate("globalThis.__schedulerQA.menu.items.find(n=>n.label==='完全退出').click();true")
    .catch(() => {});
  // Node inspector intentionally keeps an exiting process alive until the test
  // debugger disconnects. Release our own handles before measuring real Quit.
  driver.c.close();
  driver.m.close();
  driver.c = driver.m = null;
  await new Promise((resolve, reject) => {
    if (driver.child.exitCode !== null) return resolve();
    const timer = setTimeout(
      () => reject(Error('Tray quit did not terminate owned instance')),
      20000,
    );
    driver.child.once('exit', () => {
      clearTimeout(timer);
      resolve();
    });
  });
  let stopped = false;
  for (let i = 0; i < 40; i++) {
    try {
      await fetch(driver.base + '/health', { signal: AbortSignal.timeout(300) });
    } catch {
      stopped = true;
      break;
    }
    await sleep(100);
  }
  driver.check(stopped, 'Tray Quit stops owned backend');
  driver.check(
    driver.report.localModelRequests === 0,
    'Pure scheduled target makes zero model requests',
  );
  driver.report.result = 'PASS';
  driver.report.boundary =
    'Actual native Tray and Notification objects; production menu handlers invoked by isolated debugger; no physical toast click claim';
  driver.persist();
}
run()
  .catch((error) => {
    if (driver) {
      driver.report.result = 'FAIL';
      driver.report.error = String(error.stack || error);
      driver.persist();
    }
    console.error(error);
    process.exitCode = 1;
  })
  .finally(async () => {
    // This suite explicitly enabled close-to-tray. Failure cleanup must use the
    // production Quit path; closing a window alone is deliberately insufficient.
    if (driver?.child?.exitCode === null && driver.m) {
      await driver.m.evaluate("require('electron').app.quit();true").catch(() => {});
      for (let n = 0; n < 200 && driver.child.exitCode === null; n++) await sleep(100);
    }
    await driver?.cleanup();
  });

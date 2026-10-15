'use strict';

// Opt-in installed-product test. The actual planner, tool/runtime, database,
// preload and Workbench run together; no external model or voice is contacted.
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const assert = require('node:assert/strict');
const { StudioSmokeDriver, q, sleep } = require('./studio-smoke-driver.cjs');
let driver;
async function read(operation, options) {
  return driver.c.evaluate(`window.desktopAPI.readAgentTraces(${q(operation)},${q(options)})`);
}
async function select(label, value) {
  await driver.c.evaluate(`(()=>{const n=document.querySelector(${q('[aria-label="'+label+'"]')});if(!n)throw Error('Missing select');n.value=${q(value)};n.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
}
async function run() {
  driver = new StudioSmokeDriver();
  await driver.prepare();
  await driver.launch();
  const info = await driver.c.evaluate('window.desktopAPI.appInfo()');
  driver.check(info.packaged, 'Packaged review application launches');
  await driver.c.evaluate(`window.desktopAPI.applyApiPreset({name:'Trace local fixture',provider:'openai-compatible',base_url:'http://127.0.0.1:${driver.ports.model}/v1',api_key:'mock-local-only',model:'local-smoke',capabilities:{text:true,tool_calling:false},timeout_seconds:15})`);
  await driver.c.evaluate("window.desktopAPI.setTrustedAction('system.get_status','always')");
  await driver.c.evaluate('window.desktopAPI.workPreferences({auto_work_mode:false})');
  await driver.click('工作模式');
  await driver.send('查看系统状态');
  await driver.c.until("document.querySelector('.work-status')?.innerText.includes('已完成')", 40000);
  const conversation = (await driver.json('/api/conversations?character_id=assistant')).find(row => row.conversation_type !== 'chat');
  assert.ok(conversation);
  const scope = {conversation_id: conversation.id};
  await driver.c.until(`window.desktopAPI.readAgentTraces('list',${q(scope)}).then(p=>p.items.some(r=>r.status==='completed'))`);
  const trace = (await read('list', scope)).items[0];
  const options = {...scope, trace_id: trace.trace_id};
  const before = await read('events', options);
  driver.check(before.items.some(row => row.event_type === 'TASK_COMPLETED') && before.items.some(row => row.event_type === 'TOOL_COMPLETED'), 'Real task events persist and share the plan task ID', {task_id: trace.task_id, events: before.items.length});
  await driver.click('模块');
  await driver.click('Agent Trace · 执行追踪', "document.querySelector('.workspace-module-picker')");
  await driver.c.until("document.querySelectorAll('.trace-row').length>0");
  await driver.c.evaluate("document.querySelector('.trace-viewer').scrollIntoView({block:'center'});true");
  driver.check(await driver.c.evaluate(`document.querySelector('.trace-details')?.innerText.includes(${q(trace.task_id)})`), 'Workbench TraceViewer displays the real task and details');
  await driver.screenshot('trace-real-task');
  // Add a known synthetic tail ONLY to the new isolated test DB, for paging and
  // rendering characterization. This does not pretend they were tool operations.
  const python = process.env.COMPANION_TEST_PYTHON || path.resolve(__dirname, '../../.venv/Scripts/python.exe');
  const seed = `import sqlite3,sys,json,datetime\np=sys.argv[1]\nassert p.endswith('chat.db')\ndb=sqlite3.connect(p,timeout=5)\nt=sys.argv[2]\nn=db.execute('select event_count from agent_traces where id=?',(t,)).fetchone()[0]\nrows=[(t,n+i,None,None,'OBSERVATION_CREATED','task','completed',datetime.datetime.now(datetime.timezone.utc).isoformat(),json.dumps({'metadata':{'observed_count':i}})) for i in range(1,1001)]\ndb.executemany('insert into trace_events(trace_id,sequence,span_id,source_work_event_id,event_type,phase,status,created_at,data_json) values(?,?,?,?,?,?,?,?,?)',rows)\ndb.execute('update agent_traces set event_count=? where id=?',(n+1000,t))\ndb.commit()\n`;
  const seeded = spawnSync(python, ['-B', '-c', seed, path.join(driver.home, 'UserData/chat.db'), trace.trace_id], {encoding:'utf8', windowsHide:true});
  assert.equal(seeded.status, 0, seeded.stderr);
  const heapBefore = await driver.c.evaluate('performance.memory?.usedJSHeapSize || null');
  const modelBefore = driver.report.localModelRequests;
  const mark = Date.now();
  await driver.click('刷新', "document.querySelector('.trace-viewer')");
  await driver.c.until("Number(document.querySelector('[aria-label=回放进度]')?.max)>999");
  const perf = await driver.c.evaluate(`({rows:document.querySelectorAll('.trace-row').length,eventCount:Number(document.querySelector('[aria-label=回放进度]').max)+1,heap:performance.memory?.usedJSHeapSize||null,diagnostic:document.querySelector('.trace-viewer')?.innerText.split('条事件').at(-1)})`);
  driver.check(perf.eventCount > 1000 && perf.rows <= 24, '1000-event history paginates with bounded visible DOM', {...perf, heap_before:heapBefore, heap_delta_bytes:perf.heap && heapBefore ? perf.heap - heapBefore : null, history_load_ms:Date.now()-mark});
  await driver.c.evaluate("document.querySelectorAll('.trace-row')[2].click();true");
  driver.check(await driver.c.evaluate("document.querySelector('[aria-label=回放进度]').value==='2'"), 'Clicking a timeline step selects its details');
  await select('回放速度', '4');
  await driver.click('从头回放');
  await driver.c.until("Number(document.querySelector('[aria-label=回放进度]').value)>=3");
  await driver.click('暂停回放');
  const at = await driver.c.evaluate("document.querySelector('[aria-label=回放进度]').value");
  await sleep(400);
  driver.check(await driver.c.evaluate("document.querySelector('[aria-label=回放进度]').value") === at, 'Replay pauses without advancing');
  await driver.click('继续回放');
  await driver.c.until(`Number(document.querySelector('[aria-label=回放进度]').value)>${Number(at)}`);
  await driver.click('暂停回放');
  await driver.field('[aria-label="回放进度"]', '999');
  driver.check(await driver.c.evaluate("document.querySelector('[aria-label=回放进度]').value==='999'"), 'Replay jumps directly to a historical event');
  driver.check(driver.report.localModelRequests === modelBefore, 'Replay produces no model requests');
  driver.check((await read('detail', options)).event_count === perf.eventCount, 'Replay creates no task or tool events');
  const exportsPath = await driver.m.evaluate("require('electron').app.getPath('downloads')");
  driver.check(path.resolve(exportsPath) === path.join(driver.home, 'Exports'), 'JSON export defaults to this isolated product home');
  await driver.click('导出脱敏 JSON');
  const exported = path.join(exportsPath, 'agent-trace-' + trace.trace_id + '.json');
  for(let attempt=0;attempt<50 && !fs.existsSync(exported);attempt++) await sleep(100);
  const data = JSON.parse(fs.readFileSync(exported,'utf8'));
  driver.check(data.schema_version === 1 && data.events.length === perf.eventCount && !JSON.stringify(data).includes('mock-local-only'), 'Export writes the scoped allowlisted history without credentials');
  await driver.screenshot('trace-1000-replay');
  await driver.click('新聊天');
  await driver.c.until("!document.querySelector('.trace-details')?.innerText.includes("+q(trace.task_id)+")");
  driver.check(true, 'Switching to a new work conversation hides the previous trace');
  await driver.openConversation(conversation.id);
  await driver.c.until("document.querySelectorAll('.trace-row').length>0");
  await driver.close();
  await driver.launch();
  await driver.openConversation(conversation.id);
  await driver.c.until("document.querySelectorAll('.trace-row').length>0");
  driver.check((await read('detail', options)).event_count === perf.eventCount, 'Historical trace and module layout survive application restart');
  await driver.close();
  driver.report.pass = true;
  driver.report.manual.push('This test drives the real Chromium UI through the local test debugger; it does not measure native desktop focus, voice quality or renderer FPS.');
  driver.persist();
  console.log(q({pass:true, checks:driver.report.checks.length, report:path.join(driver.home,'report.json'), realModelRequests:0, realVoiceRequests:0}));
}
run().catch(error=>{if(driver){driver.report.error=String(error.stack||error);driver.persist();} console.error(error.stack||error);process.exitCode=1;}).finally(async()=>{await driver?.cleanup();});

'use strict';
// Explicit packaged-product acceptance with fresh profiles. All Agent evaluation
// calls use the built-in Mock model/desktop; external models and voices stay off.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { StudioSmokeDriver, q, sleep } = require('./studio-smoke-driver.cjs');
let driver;
const read = (op, options) => driver.c.evaluate(`window.desktopAPI.readEvaluation(${q(op)},${q(options || {})})`);
async function select(label, value) {
  await driver.c.evaluate(`(()=>{const n=document.querySelector(${q('[aria-label="'+label+'"]')});if(!n)throw Error('missing select');n.value=${q(value)};n.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
}
async function latest(previous) {
  await driver.c.until(`window.desktopAPI.readEvaluation('runs').then(p=>p.items.length>0&&p.items[0].run_id!==${q(previous || '')})`, 15000);
  return (await read('runs')).items[0];
}
async function complete(id, timeout=180000) {
  await driver.c.until(`window.desktopAPI.readEvaluation('detail',{run_id:${q(id)}}).then(r=>['COMPLETED','FAILED','CANCELLED','INTERRUPTED'].includes(r.status))`, timeout);
  return read('detail', {run_id:id});
}
async function run() {
  driver = new StudioSmokeDriver();
  await driver.prepare();
  await driver.launch();
  const info = await driver.c.evaluate('window.desktopAPI.appInfo()');
  driver.check(info.packaged && info.version==='0.2.0-eval.1', 'Packaged Trace + Evaluation candidate launches', {version:info.version});
  await driver.click('工作模式');
  await driver.click('模块');
  await driver.click('Agent Evaluation · 评测', "document.querySelector('.workspace-module-picker')");
  await driver.c.until("!!document.querySelector('.evaluation-dashboard')");
  await driver.c.evaluate("document.querySelector('[aria-label=\"最大化Agent Evaluation · 评测\"]').click();true");
  await driver.c.until("document.querySelector('[aria-label=评测任务]')?.options.length===21");
  driver.check(await driver.c.evaluate("document.querySelector('.evaluation-dashboard').innerText.includes('不会填充示例分数')"), 'Empty dashboard has no invented scores');
  driver.check(driver.report.localModelRequests===0, 'Opening dashboard does not call a model');
  await driver.field('[aria-label="评测并发"]','2');
  const begin = Date.now();
  await driver.click('开始评测');
  const first = await latest();
  const full = await complete(first.run_id);
  driver.check(full.status==='COMPLETED' && full.metrics.valid_count===20 && full.metrics.counts.PASS===20, 'Twenty cases execute through installed worker, Runtime, Trace, grading and DB', {metrics:full.metrics,wall_ms:Date.now()-begin});
  driver.check(full.snapshot.app_version===info.version && /^[a-f0-9]{40}$/.test(full.snapshot.git_commit), 'Frozen backend records real build version and commit');
  driver.check(full.metrics.real_task_success_rate===null && full.metrics.api_cost.value===null, 'Mock result is not a real AI score or a zero-cost claim');
  await driver.c.until("document.querySelectorAll('[aria-label=评测结果] tbody tr').length===20&&document.querySelector('.evaluation-progress')?.innerText.includes('COMPLETED')");
  await driver.c.evaluate("document.querySelector('.evaluation-progress').scrollIntoView({block:'start'});true");
  await sleep(200);
  await driver.screenshot('evaluation-dashboard');
  await driver.c.evaluate("document.querySelector('.evaluation-dashboard').scrollTop=0;true");
  const results = (await read('results',{run_id:first.run_id,limit:100})).items;
  const dual = results.find(r=>r.case_id==='EVAL-020');
  const scopes = await read('trace-list',{run_id:first.run_id,attempt_id:dual.attempt_id});
  driver.check(scopes.items.length===2 && new Set(scopes.items.map(t=>t.conversation_id)).size===2, 'Concurrent case keeps both session-scoped Trace histories');
  const denied = results.find(r=>r.case_id==='EVAL-018');
  await driver.c.evaluate("[...document.querySelectorAll('.evaluation-dashboard button')].find(b=>b.textContent.trim().startsWith('EVAL-018 #')).click();true");
  await driver.c.until("document.querySelector('.evaluation-evidence')?.innerText.includes('PERMISSION')||document.querySelector('.evaluation-evidence')?.innerText.includes('permission_denied')");
  await driver.c.evaluate("document.querySelector('.evaluation-evidence details:last-child summary').click();true");
  await driver.c.until("document.querySelectorAll('.evaluation-evidence .trace-row').length>0");
  driver.check(await driver.c.evaluate("!document.querySelector('.evaluation-evidence .trace-filters') && document.querySelector('.evaluation-evidence').innerText.includes('本次合成任务输入')"), 'Evaluation Trace hides unrelated history controls and retains the public benchmark input');
  driver.check(await driver.c.evaluate(`document.querySelector('.trace-details')?.innerText.includes(${q(denied.task_id)})`), 'Result links to the existing TraceViewer with matching task ID');
  await select('回放速度','4');
  await driver.click('从头回放', "document.querySelector('.evaluation-evidence')");
  await driver.c.until("Number(document.querySelector('[aria-label=回放进度]')?.value)>1");
  await driver.click('暂停回放', "document.querySelector('.evaluation-evidence')");
  const replayAt = await driver.c.evaluate("document.querySelector('[aria-label=回放进度]').value");
  await sleep(300);
  driver.check(await driver.c.evaluate("document.querySelector('[aria-label=回放进度]').value")===replayAt, 'Evaluation Trace Replay pauses without re-executing Agent');
  for (const format of ['JSON','CSV','MARKDOWN']) await driver.click('导出 '+format, "document.querySelector('.evaluation-dashboard')");
  const exported = fs.readdirSync(path.join(driver.home,'Exports')).filter(f=>f.startsWith('evaluation-'+first.run_id));
  driver.check(exported.length===3 && exported.some(f=>f.endsWith('.json')) && exported.some(f=>f.endsWith('.csv')) && exported.some(f=>f.endsWith('.md')), 'Fixed export capability saves all three formats under owned Exports');
  const report = JSON.parse(fs.readFileSync(path.join(driver.home,'Exports',exported.find(f=>f.endsWith('.json'))),'utf8'));
  driver.check(report.title==='Mock Evaluation' && !JSON.stringify(report).includes('api_key') && report.results.length===20, 'Export contains bounded synthetic summaries without model secrets');
  await select('评测任务','EVAL-014');
  await driver.click('开始评测');
  const single = await latest(first.run_id);
  driver.check((await complete(single.run_id)).metrics.valid_count===1, 'Single-case benchmark executes independently');
  await driver.c.until(`[...document.querySelector('[aria-label=对比评测]').options].some(o=>o.value===${q(first.run_id)})`);
  await driver.c.until("document.querySelector('.evaluation-progress')?.innerText.includes('COMPLETED')");
  await select('对比评测',first.run_id);
  await driver.click('比较条件与结果');
  await driver.c.until("document.querySelector('[aria-label=评测条件对比]')?.innerText.includes('不能公平比较')");
  driver.check(true, 'Comparison exposes differing case conditions instead of ranking models');
  await driver.c.until("document.querySelectorAll('[aria-label=评测结果] tbody tr').length===1");
  await driver.c.evaluate("document.querySelector('[aria-label=\"选择 EVAL-014\"]').click();true");
  await driver.click('重跑选中项（1）');
  const rerun = await latest(single.run_id);
  driver.check((await complete(rerun.run_id)).metrics.valid_count===1 && (await read('detail',{run_id:first.run_id})).metrics.valid_count===20, 'Rerun creates new history without overwriting the earlier run');
  await driver.c.until("document.querySelector('.evaluation-progress')?.innerText.includes('COMPLETED')");
  await select('评测任务','');
  await driver.field('[aria-label="评测次数"]','5');
  await driver.field('[aria-label="评测并发"]','1');
  await driver.click('开始评测');
  const cancelling = await latest(rerun.run_id);
  await driver.c.until(`window.desktopAPI.readEvaluation('results',{run_id:${q(cancelling.run_id)}}).then(p=>p.items.some(r=>r.execution_status==='RUNNING'))`);
  await driver.click('暂停排队');
  await driver.c.until(`window.desktopAPI.readEvaluation('detail',{run_id:${q(cancelling.run_id)}}).then(r=>r.status==='PAUSED')`);
  await sleep(1800);
  const paused = await read('detail',{run_id:cancelling.run_id});
  await sleep(500);
  driver.check((await read('detail',{run_id:cancelling.run_id})).metrics.total_count===paused.metrics.total_count, 'Pause stops dequeuing while allowing owned in-flight work to finish');
  await driver.click('取消运行');
  const cancelled = await complete(cancelling.run_id);
  driver.check(cancelled.status==='CANCELLED' && cancelled.metrics.counts.NOT_RUN>0 && cancelled.metrics.valid_count<100, 'Cancellation preserves NOT_RUN instead of manufacturing passed cases');
  await driver.c.until("document.querySelector('.evaluation-progress')?.innerText.includes('CANCELLED')");
  await driver.field('[aria-label="评测并发"]','2');
  await driver.click('开始评测');
  const large = await latest(cancelling.run_id);
  const largeStart = Date.now();
  const largeComplete = await complete(large.run_id,400000);
  driver.check(largeComplete.metrics.valid_count===100 && largeComplete.metrics.counts.PASS===100, 'Five repetitions produce one hundred real Mock attempts', {wall_ms:Date.now()-largeStart,metrics:largeComplete.metrics});
  await driver.c.until("document.querySelectorAll('[aria-label=评测结果] tbody tr').length===20&&document.querySelector('.evaluation-progress')?.innerText.includes('100 / 100')");
  const beforeHeap = await driver.c.evaluate('performance.memory?.usedJSHeapSize || null');
  const beforePage = Date.now();
  await driver.click('下一页',"document.querySelector('.evaluation-dashboard')");
  await driver.c.until("document.querySelector('.evaluation-dashboard')?.innerText.includes('第 2 页')&&document.querySelectorAll('[aria-label=评测结果] tbody tr').length===20");
  driver.check(true, 'One hundred attempts render only twenty rows per page', {page_ms:Date.now()-beforePage,heap_before:beforeHeap,heap_after:await driver.c.evaluate('performance.memory?.usedJSHeapSize || null')});
  await driver.screenshot('evaluation-100-results');
  driver.check(driver.report.localModelRequests===0, 'Benchmarks, reports, comparison and Replay do not call the normal chat model');
  await driver.close();
  await driver.launch();
  await driver.click('工作模式');
  // Pending Workbench layouts persist without needing a synthetic user message.
  if (!await driver.c.evaluate("!!document.querySelector('.evaluation-dashboard')")) {
    await driver.click('模块');
    await driver.click('Agent Evaluation · 评测',"document.querySelector('.workspace-module-picker')");
  }
  const restored = await read('detail',{run_id:first.run_id});
  driver.check(restored.metrics.valid_count===20 && (await read('trace-events',{run_id:first.run_id,attempt_id:denied.attempt_id})).items.some(e=>e.event_type==='TOOL_FAILED'), 'Restart restores metrics and original permission Trace without rerunning tasks');
  driver.check((await read('runs')).items.length===5, 'All five benchmark histories persist after restart');
  await driver.close();
  driver.report.pass = true;
  driver.report.benchmark = {first_run_id:first.run_id,large_run_id:large.run_id,model_requests:0,voice_requests:0};
  driver.report.manual.push('Real Chromium UI through the local debugger. Windows GUI Agent bridge and paid/live model quality were not tested; SSR 1000-metric tests do not measure FPS.');
  driver.persist();
  console.log(q({pass:true,checks:driver.report.checks.length,report:path.join(driver.home,'report.json'),realModelRequests:0,realVoiceRequests:0}));
}
run().catch(error=>{if(driver){driver.report.error=String(error.stack||error);driver.persist();}console.error(error.stack||error);process.exitCode=1;}).finally(async()=>{await driver?.cleanup();});

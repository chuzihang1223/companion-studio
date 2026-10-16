'use strict';
// Exercise the installed Evaluation UI and its real frozen worker. Only the
// worker's declared model/desktop fixtures are synthetic, never its scheduler.
const fs = require('node:fs');
const path = require('node:path');
const { q, sleep } = require('./studio-smoke-driver.cjs');

module.exports = async function exerciseEvaluation(driver) {
  const read = (op, options = {}) =>
    driver.c.evaluate(`window.desktopAPI.readEvaluation(${q(op)},${q(options)})`);
  await driver.click('模块');
  await driver.click(
    'Agent Evaluation · 评测',
    "document.querySelector('.workspace-module-picker')",
  );
  await driver.c.until("!!document.querySelector('.evaluation-dashboard')");
  await driver.c.evaluate(
    "(()=>{const m=document.querySelector('[data-module=evaluation]');if(!m.classList.contains('module-maximized'))m.querySelector('[aria-label=\"最大化Agent Evaluation · 评测\"]').click();return true})()",
  );
  await driver.c.evaluate(
    "(()=>{const n=document.querySelector('[aria-label=评测测试集]');n.value='workflow-core';n.dispatchEvent(new Event('change',{bubbles:true}));return true})()",
  );
  await driver.c.until(
    "[...document.querySelector('[aria-label=评测任务]').options].some(o=>o.value==='WF-020')",
  );
  await driver.field('[aria-label="评测并发"]', '2');
  await driver.field('[aria-label="评测超时"]', '35');
  const before = new Set((await read('runs')).items.map((run) => run.run_id));
  await driver.click('开始评测', "document.querySelector('.evaluation-dashboard')");
  await driver.c.until(
    `window.desktopAPI.readEvaluation('runs').then(r=>r.items.some(x=>!${q([...before])}.includes(x.run_id)))`,
  );
  const created = (await read('runs')).items.find((run) => !before.has(run.run_id));
  await driver.c.until(
    `window.desktopAPI.readEvaluation('detail',{run_id:${q(created.run_id)}}).then(r=>['COMPLETED','FAILED','CANCELLED','INTERRUPTED'].includes(r.status))`,
    180000,
  );
  const detail = await read('detail', { run_id: created.run_id });
  const results = (await read('results', { run_id: created.run_id, limit: 100 })).items;
  driver.check(
    detail.status === 'COMPLETED' &&
      detail.metrics.counts.PASS === 20 &&
      results.length === 20 &&
      results.every((r) => r.execution_status === 'PASS'),
    'Workflow Core executes all 20 cases through the actual Evaluation worker',
    { counts: detail.metrics.counts, workflow: detail.metrics.workflow },
  );
  driver.check(
    detail.metrics.real_task_success_rate === null &&
      detail.metrics.workflow.real_ai_task_success_rate === null &&
      detail.metrics.workflow.completed_workflows < 20 &&
      detail.metrics.workflow.blocked_side_effects === 1,
    'Expected safety failures are separated from completed workflows and real AI success',
  );
  await driver.c.until(
    "document.querySelectorAll('[aria-label=评测结果] tbody tr').length===20&&document.querySelector('.evaluation-progress')?.innerText.includes('COMPLETED')",
  );
  driver.check(
    await driver.c.evaluate(
      "document.querySelector('.evaluation-dashboard').innerText.includes('工作流运行 / 流程完成（合成）')",
    ),
    'Existing Evaluation dashboard renders measured Workflow metrics',
  );
  const traced = results.find((r) => r.case_id === 'WF-020');
  const scopes = await read('trace-list', {
    run_id: created.run_id,
    attempt_id: traced.attempt_id,
  });
  driver.check(scopes.items.length > 0, 'Workflow benchmark exposes its actual original Trace');
  for (const format of ['JSON', 'CSV', 'MARKDOWN'])
    await driver.click('导出 ' + format, "document.querySelector('.evaluation-dashboard')");
  const exports = fs
    .readdirSync(path.join(driver.home, 'Exports'))
    .filter((file) => file.startsWith('evaluation-' + created.run_id));
  driver.check(
    exports.length === 3,
    'Workflow evaluation exports JSON, CSV and Markdown through original IPC',
  );
  const report = JSON.parse(
    fs.readFileSync(
      path.join(
        driver.home,
        'Exports',
        exports.find((s) => s.endsWith('.json')),
      ),
      'utf8',
    ),
  );
  driver.check(
    report.results.length === 20 && report.run.snapshot.dataset_id === 'workflow-core',
    'Exported Workflow report contains the real 20 result rows',
  );
  fs.writeFileSync(path.join(driver.home, 'workflow-benchmark.json'), q(report));
  driver.report.workflowBenchmark = {
    run_id: created.run_id,
    status: detail.status,
    counts: detail.metrics.counts,
    workflow: detail.metrics.workflow,
    mode: detail.mode,
    real_ai_task_success_rate: detail.metrics.real_task_success_rate,
  };
  await driver.c.evaluate(
    "document.querySelector('.evaluation-progress').scrollIntoView({block:'start'});true",
  );
  await sleep(120);
  await driver.screenshot('workflow-evaluation');
};

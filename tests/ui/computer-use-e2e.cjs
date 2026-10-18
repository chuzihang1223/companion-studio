'use strict';
// Product UI/IPC/Runtime/worker and owned Windows controls are real. Dialog
// choices are explicit test fixtures; no user application or cloud API is used.
const fs = require('node:fs'),
  path = require('node:path');
const { StudioSmokeDriver, q, sleep } = require('./studio-smoke-driver.cjs');
process.env.COMPANION_VISUAL_QA_PROFILE = '1';
let driver;
const read = (op, scope) =>
  driver.c.evaluate(`window.desktopAPI.computerRead(${q(op)},${q(scope)})`);
async function run() {
  driver = new StudioSmokeDriver();
  await driver.prepare();
  fs.writeFileSync(
    path.join(driver.home, '.companion-visual-qa'),
    q({ purpose: 'companion-visual-qa', schema_version: 1 }),
  );
  await driver.launch();
  driver.check(
    (await driver.c.evaluate('window.desktopAPI.appInfo()')).version === '0.2.0-visual.1',
    'Actual visual candidate identity',
  );
  await driver.m.evaluate(
    `(()=>{const {dialog}=require('electron');globalThis.__visualQA={approved:true,count:0};const original=dialog.showMessageBox.bind(dialog);dialog.showMessageBox=async options=>{if(options.title==='视觉操作授权'){globalThis.__visualQA.count++;return {response:globalThis.__visualQA.approved?1:0}}return original(options)};return true})()`,
  );
  const response = await fetch(driver.base + '/api/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: q({ character_id: 'assistant', conversation_type: 'work' }),
  });
  const conversation = await response.json();
  driver.check(response.ok, 'Create isolated work conversation without model');
  const scope = { conversation_id: conversation.id, character_id: 'assistant' };
  await driver.c.call('Page.reload');
  await driver.c.until("!!document.querySelector('[aria-label=输入消息]')");
  await driver.openConversation(conversation.id);
  await driver.click('模块');
  await driver.click(
    '屏幕观察 · Visual Observer',
    "document.querySelector('.workspace-module-picker')",
  );
  await driver.c.until("!!document.querySelector('.visual-observer')");
  const memoryBefore = await driver.m.evaluate('process.getProcessMemoryInfo()');
  await driver.c.evaluate(
    `(()=>{const p=globalThis.__visualPerf={active:true,gaps:[],last:performance.now()};const step=now=>{if(!p.active)return;if(p.gaps.length<6000)p.gaps.push(now-p.last);p.last=now;requestAnimationFrame(step)};requestAnimationFrame(step);return true})()`,
  );
  driver.check(!(await read('state', scope)).enabled, 'Visual Computer Use is off by default');
  await driver.m.evaluate('globalThis.__visualQA.approved=false;true');
  const denied = await driver.c.evaluate(
    'window.desktopAPI.computerQAConsent().then(()=>false,()=>true)',
  );
  driver.check(denied, 'Native QA consent refusal is honored');
  await driver.m.evaluate('globalThis.__visualQA.approved=true;true');
  const consent = await driver.c.evaluate('window.desktopAPI.computerQAConsent()');
  const fixture = await driver.c.evaluate(
    `window.desktopAPI.computerQAFixture(${q({ ...scope, operation: 'open', consent_id: consent })})`,
  );
  driver.check(fixture.verified, 'Only original synthetic fixture is granted after native consent');
  await driver.c.until("!!document.querySelector('.visual-observer code')", 10000);
  const act = (operation, args = {}) =>
    driver.c.evaluate(
      `window.desktopAPI.computerAction(${q({ ...scope, operation, arguments: { target_id: fixture.target_id, ...args } })})`,
    );
  driver.check(
    (await act('focus_window')).verified,
    'Original Runtime focuses the granted fixture',
  );
  const first = await act('observe');
  driver.check(first.verified, 'Original Runtime observes the actual fixture');
  const frame = (await read('state', scope)).targets[0].observation;
  driver.check(
    frame.elements.some((r) => r.type === 'Button') &&
      frame.elements.some((r) => r.type === 'Edit'),
    'Actual UIA semantic controls',
  );
  driver.check(
    frame.image.redacted_regions >= 1 &&
      !frame.elements.some((r) => r.automation_id === 'ProtectedInput'),
    'Actual password redaction',
  );
  driver.check(
    frame.ocr.lines.some((r) => r.text.replace(/\s/g, '').includes('本地视觉测试')),
    'Actual local Chinese OCR',
  );
  driver.check(
    frame.ocr.lines.some((r) => r.text === 'Orchard QA'),
    'Actual local English OCR',
  );
  driver.check(
    !JSON.stringify(first).includes('data:image') && !JSON.stringify(first).includes('SampleInput'),
    'Raw screenshot and UI text stay outside model/tool persistence',
  );
  // Exercise the module's visible controls, not a mock component.
  await driver.field('.visual-observer [aria-label="目标说明"]', 'Sample input');
  await driver.field('.visual-observer [aria-label="AutomationId"]', 'SampleInput');
  const idle = () =>
    driver.c.until("!document.querySelector('.visual-observer fieldset')?.disabled", 20000);
  await driver.click('定位元素', "document.querySelector('.visual-observer')");
  await idle();
  await driver.field('.visual-observer [aria-label="视觉输入内容"]', 'Local QA');
  await driver.m.evaluate(
    "(()=>{const w=require('electron').BrowserWindow.getAllWindows().find(w=>!w.isDestroyed());w.show();w.focus();return true})()",
  );
  await driver.click('输入并验证', "document.querySelector('.visual-observer')");
  await idle();
  driver.check(
    (await read('state', scope)).targets[0].last_result.verified,
    'Visible input flow verifies real textbox contents',
  );
  await driver.field('.visual-observer [aria-label="目标说明"]', 'Apply sample');
  await driver.field('.visual-observer [aria-label="AutomationId"]', 'ApplySample');
  await driver.field('.visual-observer [aria-label="验证控件 ID"]', 'SampleResult');
  await driver.field('.visual-observer [aria-label="预期文字"]', 'Local QA');
  await driver.click('定位元素', "document.querySelector('.visual-observer')");
  await idle();
  await driver.m.evaluate(
    "(()=>{const w=require('electron').BrowserWindow.getAllWindows().find(w=>!w.isDestroyed());w.show();w.focus();return true})()",
  );
  await driver.click('点击并验证', "document.querySelector('.visual-observer')");
  await idle();
  driver.check(
    (await read('state', scope)).targets[0].last_result.verified,
    'Visible click flow verifies actual application result',
  );
  const find = async (id) => {
    const result = await act('find_element', {
      locator: { description: id, automation_id: id, source: 'uia' },
    });
    return {
      observation_id: result.observation_id,
      candidate_id: result.candidates[0].candidate_id,
    };
  };
  const stale = await find('SampleInput');
  await act('click', {
    ...(await find('RebuildControl')),
    verification: { kind: 'element_present', automation_id: 'SampleInput' },
  });
  const rejected = await act('type_text', { ...stale, text: 'wrong' }).catch(() => null);
  driver.check(
    !rejected || !rejected.verified,
    'Stale observation cannot replay input after control reconstruction',
  );
  const fresh = await act('type_text', { ...(await find('SampleInput')), text: 'New control' });
  driver.check(fresh.verified, 'Fresh UIA discovery recovers after reconstruction');
  await driver.click('暂停', "document.querySelector('.visual-observer')");
  await idle();
  driver.check(
    (await read('state', scope)).targets[0].observation === null,
    'Pause clears screenshot and candidates',
  );
  await driver.click('继续', "document.querySelector('.visual-observer')");
  await idle();
  driver.check(
    (await read('state', scope)).targets[0].observation === null,
    'Resume requires fresh observation',
  );
  await driver.c.evaluate(
    "document.querySelector('.visual-observer').scrollIntoView({block:'center'});true",
  );
  await driver.screenshot('visual-observer');
  await driver.c.evaluate(
    `window.desktopAPI.computerQAFixture(${q({ ...scope, operation: 'close', consent_id: consent, fixture_id: fixture.fixture_id })})`,
  );
  driver.check(!(await read('state', scope)).enabled, 'Fixture close revokes window grant');
  const run = await driver.c.evaluate(
    `window.desktopAPI.startEvaluation(${q({ dataset_id: 'computer-use-core', mode: 'windows_gui', visual_consent: consent, concurrency: 1, timeout_seconds: 60, max_duration_seconds: 600 })})`,
  );
  driver.check(run.total_cases === 24, 'Original Evaluation creates all 24 Computer Use cases');
  let detail;
  for (let i = 0; i < 600; i++) {
    detail = await driver.c.evaluate(
      `window.desktopAPI.readEvaluation('detail',{run_id:${q(run.run_id)}})`,
    );
    if (['COMPLETED', 'FAILED', 'CANCELLED'].includes(detail.status)) break;
    if (i % 10 === 0)
      console.log(JSON.stringify({ native_benchmark: detail.completed_cases, total: 24 }));
    await sleep(500);
  }
  const rows = await driver.c.evaluate(
    `window.desktopAPI.readEvaluation('results',{run_id:${q(run.run_id)},limit:100})`,
  );
  fs.writeFileSync(
    path.join(driver.home, 'computer-benchmark.json'),
    JSON.stringify({ run: detail, results: rows.items }, null, 2),
  );
  driver.check(
    detail.status === 'COMPLETED' && rows.items.length === 24,
    'All native cases reach a recorded conclusion',
  );
  const allowedBlocked = {
    'CU-013': 'PHYSICAL_DPI_ENVIRONMENT_UNAVAILABLE',
    'CU-014': 'PHYSICAL_DPI_ENVIRONMENT_UNAVAILABLE',
    'CU-015': 'PHYSICAL_DPI_ENVIRONMENT_UNAVAILABLE',
    'CU-016': 'DISPLAY_CHANGE_ENVIRONMENT_UNAVAILABLE',
  };
  driver.check(
    rows.items.every(
      (r) =>
        r.execution_status === 'PASS' ||
        (r.execution_status === 'BLOCKED' && allowedBlocked[r.case_id] === r.error_code),
    ) && rows.items.filter((r) => r.execution_status === 'PASS').length >= 21,
    'Only actual hardware limits can block native cases',
    rows.items.map((r) => ({ case_id: r.case_id, status: r.execution_status, code: r.error_code })),
  );
  driver.check(
    detail.metrics.real_task_success_rate === null &&
      detail.snapshot.environment.desktop_bridge === 'original-native-worker' &&
      !detail.snapshot.environment.process_isolation,
    'Local GUI assertions never claim real AI quality or worker isolation',
  );
  const withTrace = rows.items.find((r) => r.trace_id && r.execution_status === 'PASS');
  const replay = await driver.c.evaluate(
    `window.desktopAPI.readEvaluation('trace-events',${q({ run_id: run.run_id, attempt_id: withTrace.attempt_id, trace_id: withTrace.trace_id, limit: 250 })})`,
  );
  driver.check(
    replay.items.some(
      (e) => e.event_type === 'SCREEN_OBSERVED' || e.event_type === 'COMPUTER_ACTION_COMPLETED',
    ),
    'Evaluation Trace/Replay reads original host trace',
  );
  driver.check(
    !JSON.stringify(replay).includes('data:image/png') &&
      !JSON.stringify(replay).includes('Local QA'),
    'Trace does not contain synthetic screen image or input text',
  );
  driver.check(
    driver.report.localModelRequests === 0,
    'No external or local model needed for Computer Use',
  );
  const performanceEvidence = await driver.c.evaluate(
    `(()=>{const p=globalThis.__visualPerf;p.active=false;const gaps=p.gaps.slice(1).sort((a,b)=>a-b);return{frames:gaps.length,frame_gap_p50_ms:gaps[Math.floor(gaps.length*.5)]??null,frame_gap_p95_ms:gaps[Math.floor(gaps.length*.95)]??null,frame_gap_max_ms:gaps.at(-1)??null,gaps_over_100ms:gaps.filter(v=>v>100).length,scope:'foreground and background fixture interaction; not a stable FPS claim'}})()`,
  );
  fs.writeFileSync(
    path.join(driver.home, 'performance.json'),
    JSON.stringify(
      {
        ...performanceEvidence,
        memory_before: memoryBefore,
        memory_after: await driver.m.evaluate('process.getProcessMemoryInfo()'),
        native_cases: rows.items.map((r) => ({
          case_id: r.case_id,
          duration_ms: r.duration_ms,
          metrics: r.result.computer_metrics,
        })),
      },
      null,
      2,
    ),
  );
  await driver.close();
  driver.report.result = 'PASS';
  driver.persist();
  console.log(
    JSON.stringify({
      result: 'PASS',
      checks: driver.report.checks.length,
      benchmark: detail.metrics.counts,
      home: driver.home,
    }),
  );
}
run()
  .catch((error) => {
    if (driver) {
      driver.report.error = String(error.stack || error);
      driver.persist();
    }
    console.error(error);
    process.exitCode = 1;
  })
  .finally(async () => driver?.cleanup());

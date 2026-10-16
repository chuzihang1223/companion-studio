'use strict';

// Opt-in, isolated product acceptance. The actual Electron renderer, IPC,
// backend, SDK transports, workers and SQLite run; only model output and native
// permission-dialog responses are synthetic. No private credential is read.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const net = require('node:net');
const { StudioSmokeDriver, MOCK_REPLY, q, sleep } = require('./studio-smoke-driver.cjs');
let driver;
let httpFixture;
const read = (operation, options = {}) =>
  driver.c.evaluate(`window.desktopAPI.readExtensions(${q(operation)},${q(options)})`);
const control = (operation, options = {}) =>
  driver.c.evaluate(`window.desktopAPI.controlExtensions(${q(operation)},${q(options)})`);

async function select(selector, value) {
  await driver.c.evaluate(
    `(()=>{const n=document.querySelector(${q(selector)});if(!n)throw Error('Select missing');n.value=${q(value)};n.dispatchEvent(new Event('change',{bubbles:true}));return true})()`,
  );
  await sleep(100);
}
async function idle() {
  await driver.c.until(
    "!document.querySelector('.extension-manager fieldset[disabled]') && ![...document.querySelectorAll('.extension-actions button')].some(b=>b.disabled)",
  );
}
async function settings() {
  await driver.click('设置');
  await driver.click('MCP & Skills', "document.querySelector('.settings-navigation')");
  await driver.c.until('!!document.querySelector(\'[aria-label="MCP 与 Skills 管理"]\')');
}

async function httpServer(command, args) {
  const probe = net.createServer();
  await new Promise((resolve) => probe.listen(0, '127.0.0.1', resolve));
  const port = probe.address().port;
  await new Promise((resolve) => probe.close(resolve));
  const cwd = path.join(driver.home, 'http-fixture');
  fs.mkdirSync(cwd);
  const env = {
    SystemRoot: process.env.SystemRoot,
    WINDIR: process.env.WINDIR,
    PATH: process.env.PATH,
    TEMP: cwd,
    TMP: cwd,
    PYTHONUTF8: '1',
    PYTHONDONTWRITEBYTECODE: '1',
    PYINSTALLER_RESET_ENVIRONMENT: '1',
  };
  httpFixture = spawn(command, [...args, 'http', String(port)], {
    cwd,
    env,
    windowsHide: true,
    stdio: 'ignore',
  });
  httpFixture.once('error', (error) => {
    driver.report.httpFixtureError = error.code;
  });
  const url = `http://127.0.0.1:${port}/mcp`;
  for (let attempt = 0; attempt < 100; attempt++) {
    try {
      await fetch(url, { signal: AbortSignal.timeout(300) });
      return url;
    } catch {
      if (httpFixture.exitCode !== null) throw Error('Owned HTTP fixture exited');
      await sleep(100);
    }
  }
  throw Error('Owned HTTP fixture did not start');
}

async function run() {
  driver = new StudioSmokeDriver();
  await driver.prepare();
  driver.mock.removeAllListeners('request');
  driver.mock.on('request', (req, res) => {
    let body = '';
    req.on('data', (data) => {
      body += data;
    });
    req.on('end', () => {
      res.setHeader('Content-Type', 'application/json');
      if (req.url === '/v1/models') return res.end(q({ data: [{ id: 'local-smoke' }] }));
      if (req.url !== '/v1/chat/completions') {
        res.writeHead(404);
        return res.end();
      }
      const request = JSON.parse(body);
      driver.report.localModelRequests++;
      const tool = request.tools?.find(
        (t) => t.function.name.startsWith('mcp_') && t.function.parameters?.properties?.text,
      );
      const message = tool
        ? {
            role: 'assistant',
            content: null,
            tool_calls: [
              {
                id: 'ui-mcp-call',
                type: 'function',
                function: { name: tool.function.name, arguments: q({ text: 'Synthetic UI MCP' }) },
              },
            ],
          }
        : { role: 'assistant', content: MOCK_REPLY };
      if (request.stream) {
        res.writeHead(200, { 'Content-Type': 'text/event-stream' });
        res.end(
          'data: ' +
            q({ choices: [{ delta: { content: MOCK_REPLY }, finish_reason: null }] }) +
            '\n\ndata: [DONE]\n\n',
        );
      } else res.end(q({ choices: [{ message, finish_reason: tool ? 'tool_calls' : 'stop' }] }));
    });
  });
  await driver.launch();
  const info = await driver.c.evaluate('window.desktopAPI.appInfo()');
  const expected = JSON.parse(
    fs.readFileSync(path.resolve(__dirname, '../../desktop/package.json'), 'utf8'),
  ).version;
  driver.check(
    info.version === expected && info.packaged === !driver.source,
    'Actual product version and source/installed boundary match',
    { version: info.version, packaged: info.packaged },
  );
  await driver.c.evaluate(
    `window.desktopAPI.applyApiPreset({name:'Extensions local fixture',provider:'openai-compatible',base_url:'http://127.0.0.1:${driver.ports.model}/v1',api_key:'mock-local-only',model:'local-smoke',capabilities:{text:true,tool_calling:true},timeout_seconds:15})`,
  );
  await driver.c.evaluate('window.desktopAPI.workPreferences({auto_work_mode:false})');
  await driver.m.evaluate(
    "(()=>{globalThis.__extensionConfirmations=[];require('electron').dialog.showMessageBox=async(o)=>{if(o.title!=='扩展权限确认')throw Error('Unexpected dialog');globalThis.__extensionConfirmations.push(o.message);return {response:1,checkboxChecked:false}};return true})()",
  );
  await settings();
  driver.check(
    (await read('servers')).items.length === 0 && (await read('skills')).items.length === 0,
    'Fresh isolated profile starts without servers, credentials or imported Skills',
  );
  const project = path.resolve(__dirname, '../..');
  const command = driver.source
    ? path.join(project, '.venv/Scripts/python.exe')
    : path.join(path.dirname(driver.exe), 'resources/companion-backend/companion-backend.exe');
  const args = driver.source
    ? ['-B', path.join(project, 'backend/run.py'), '--mcp-demo']
    : ['--mcp-demo'];
  assert.ok(fs.existsSync(command), 'Only this candidate backend/venv fixture is selected');
  await driver.field('[aria-label="MCP 显示名称"]', 'Audited local fixture');
  await driver.field('[aria-label="MCP 端点或程序"]', command);
  await driver.field('[aria-label="MCP 参数"]', q(args));
  await driver.c.evaluate(
    'document.querySelector(\'[aria-label="启用 MCP 服务器"]\').click();true',
  );
  await driver.click('保存配置');
  await idle();
  const saved = (await read('servers')).items[0];
  driver.check(
    saved.config.enabled && saved.connection_status === 'DISCONNECTED',
    'Saving an enabled config does not start its executable',
  );
  await driver.click('连接／重新连接／测试');
  await driver.c.until(
    `window.desktopAPI.readExtensions('servers').then(p=>p.items[0]?.connection_status==='CONNECTED')`,
    30000,
  );
  await idle();
  const tools = (await read('tools', { server_id: saved.server_id })).items;
  driver.check(
    tools.length === 8 && tools.every((t) => !t.enabled),
    'Real SDK stdio discovers eight disabled tools',
  );
  const echo = tools.find((t) => t.name === 'echo');
  await driver.c.evaluate(
    "[...document.querySelectorAll('article.extension-card')].find(n=>n.querySelector('strong')?.textContent==='echo').querySelector('input[type=checkbox]').click();true",
  );
  await idle();
  await select('[aria-label="echo 风险"]', 'READ_ONLY_EXTERNAL');
  await idle();
  driver.check(
    (await read('tools', { server_id: saved.server_id })).items.find((t) => t.id === echo.id)
      .risk_level === 'READ_ONLY_EXTERNAL',
    'UI stores the user-reviewed read-only risk separately from server annotations',
  );
  const synthetic = 'synthetic-credential-only-04';
  await control('credentials-save', {
    server_id: saved.server_id,
    credential: synthetic,
    environment: {},
  });
  const sealedPath = await driver.m.evaluate(
    "require('path').join(require('electron').app.getPath('userData'),'extension-credentials.json')",
  );
  driver.check(
    fs.existsSync(sealedPath) && !fs.readFileSync(sealedPath, 'utf8').includes(synthetic),
    'Synthetic credential is encrypted by Windows safe storage',
  );
  driver.check(
    !q(await read('servers')).includes(synthetic),
    'Renderer server reads do not return saved credential values',
  );
  await driver.c.evaluate(
    "[...document.querySelectorAll('.extension-manager summary')].find(n=>n.textContent.includes('Resources')).click();true",
  );
  await driver.click('查看能力和模板列表');
  await idle();
  await driver.c.until(
    "document.querySelector('[aria-label=扩展预览]')?.textContent.includes('fixture://readme')",
  );
  await driver.field('[aria-label="资源 URI 或 Prompt 名称"]', 'fixture://readme');
  await driver.click('授权读取／预览');
  await idle();
  driver.check(
    await driver.c.evaluate(
      "document.querySelector('[aria-label=扩展预览]')?.textContent.includes('untrusted_external_data')",
    ),
    'Resource is previewed as untrusted data after explicit approval',
  );
  await driver.c.evaluate(
    "document.querySelector('[aria-label=\"MCP 与 Skills 管理\"]').scrollIntoView({block:'start'});true",
  );
  await driver.screenshot('mcp-servers');
  await driver.click('Agent Skills');
  await driver.c.evaluate(
    "[...document.querySelectorAll('.extension-manager summary')].find(n=>n.textContent.includes('随附安全示例')).click();true",
  );
  await driver.click('project-review');
  await idle();
  driver.check(
    await driver.c.evaluate('!!document.querySelector(\'[aria-label="Skill 导入预览"]\')'),
    'Bundled standard Skill is previewed before import',
  );
  await driver.click('确认导入');
  await idle();
  let skills = (await read('skills')).items;
  driver.check(
    skills.length === 1 && !skills[0].enabled && skills[0].name === 'project-review',
    'Skill snapshot imports disabled with standard name',
  );
  await driver.c.evaluate(
    'document.querySelector(\'[aria-label="Skills 列表"] input[type=checkbox]\').click();true',
  );
  await idle();
  skills = (await read('skills')).items;
  driver.check(skills[0].enabled, 'Skill can be explicitly enabled through the actual UI');
  await driver.c.evaluate(
    "document.querySelector('[aria-label=\"MCP 与 Skills 管理\"]').scrollIntoView({block:'start'});true",
  );
  await driver.screenshot('agent-skills');
  await driver.click('返回', "document.querySelector('.settings-page')");
  await driver.click('工作模式');
  await driver.send('使用 MCP echo 读取合成文字 $project-review');
  await driver.c.until(
    "document.body.innerText.includes('等待操作授权') || document.body.innerText.includes('等待授权')",
    30000,
  );
  if (!(await driver.c.evaluate("!!document.querySelector('.work-permissions')"))) {
    await driver.click('模块');
    await driver.click('操作授权', "document.querySelector('.workspace-module-picker')");
  }
  await driver.c.until("!!document.querySelector('.work-permissions input[type=checkbox]')", 30000);
  driver.check(
    await driver.c.evaluate(
      "document.querySelector('.work-extension-summary')?.innerText.includes('project-review')",
    ),
    'Workbench shows the selected Skill and real external tool plan',
  );
  await driver.c.evaluate(
    "[...document.querySelectorAll('.work-permissions input[type=checkbox]')].filter(n=>!n.checked).forEach(n=>n.click());true",
  );
  await driver.click('继续执行');
  await driver.c.until(
    "!document.querySelector('.work-permissions input') && document.querySelector('.work-extension-summary')?.innerText.includes('project-review')",
    30000,
  );
  const conversations = await driver.json('/api/conversations?character_id=assistant');
  const conversation = conversations.find((row) => row.conversation_type === 'work');
  assert.ok(conversation, 'Owned work conversation exists');
  await driver.c.until(
    `window.desktopAPI.readAgentTraces('list',{conversation_id:${q(conversation.id)}}).then(p=>p.items.some(t=>t.status==='completed'||t.status==='failed'))`,
    25000,
  );
  const traces = await driver.c.evaluate(
    `window.desktopAPI.readAgentTraces('list',{conversation_id:${q(conversation.id)}})`,
  );
  const trace = traces.items[0];
  const events = await driver.c.evaluate(
    `window.desktopAPI.readAgentTraces('events',{conversation_id:${q(conversation.id)},trace_id:${q(trace.trace_id)},limit:250})`,
  );
  const kinds = new Set(events.items.map((e) => e.event_type));
  driver.check(
    kinds.has('MCP_TOOL_STARTED') && kinds.has('MCP_TOOL_COMPLETED') && kinds.has('SKILL_LOADED'),
    'Normal Agent Runtime records owned MCP and progressive Skill Trace events',
  );
  driver.check(
    events.items.every((e) => e.conversation_id === conversation.id),
    'UI task trace retains conversation ownership',
  );
  driver.check(
    (await driver.m.evaluate('globalThis.__extensionConfirmations')).includes(
      '授权本次外部 MCP 操作？',
    ),
    'The normal execute path invokes main-process permission confirmation (response mocked)',
  );
  await driver.screenshot('mcp-workbench');
  await driver.click('模块');
  await driver.click(
    'Agent Evaluation · 评测',
    "document.querySelector('.workspace-module-picker')",
  );
  await driver.c.until("!!document.querySelector('.evaluation-dashboard')");
  await driver.c.evaluate(
    'document.querySelector(\'[aria-label="最大化Agent Evaluation · 评测"]\').click();true',
  );
  await select('[aria-label="评测测试集"]', 'mcp-skills-core');
  await driver.field('[aria-label="评测并发"]', '2');
  await driver.field('[aria-label="评测超时"]', '30');
  await driver.click('开始评测', "document.querySelector('.evaluation-dashboard')");
  await driver.c.until(
    "window.desktopAPI.readEvaluation('runs').then(p=>p.items.some(r=>r.snapshot?.dataset_id==='mcp-skills-core'))",
    15000,
  );
  const runs = await driver.c.evaluate("window.desktopAPI.readEvaluation('runs')");
  const run = runs.items.find((r) => r.snapshot.dataset_id === 'mcp-skills-core');
  await driver.c.until(
    `window.desktopAPI.readEvaluation('detail',{run_id:${q(run.run_id)}}).then(r=>['COMPLETED','FAILED','CANCELLED'].includes(r.status))`,
    180000,
  );
  const result = await driver.c.evaluate(
    `window.desktopAPI.readEvaluation('detail',{run_id:${q(run.run_id)}})`,
  );
  driver.check(
    result.status === 'COMPLETED' && result.metrics.counts.PASS === 20,
    'Twenty MCP/Skills benchmark cases pass in the actual candidate worker',
    { metrics: result.metrics },
  );
  const detail = await driver.c.evaluate(
    `window.desktopAPI.readEvaluation('results',{run_id:${q(run.run_id)},limit:100})`,
  );
  fs.writeFileSync(
    path.join(driver.home, 'mcp-benchmark.json'),
    q({ run: result, results: detail.items }),
  );
  await driver.c.until(
    "document.querySelector('.evaluation-progress')?.innerText.includes('COMPLETED')",
  );
  await driver.c.evaluate(
    "document.querySelector('.evaluation-progress').scrollIntoView({block:'start'});true",
  );
  await driver.screenshot('mcp-evaluation');
  await driver.close();
  await driver.launch();
  const reopened = (await read('servers')).items[0];
  driver.check(
    reopened.server_id === saved.server_id &&
      reopened.connection_status === 'DISCONNECTED' &&
      reopened.last_connected_at &&
      reopened.tool_count === 8,
    'Restart preserves metadata and last connection but never auto-starts an external server',
  );
  driver.check(
    (await read('skills')).items[0].id === skills[0].id && (await read('skills')).items[0].enabled,
    'Restart preserves the imported Skill registration and enabled preference',
  );
  driver.check(
    (await driver.json('/api/conversations?character_id=assistant')).some(
      (c) => c.id === conversation.id,
    ),
    'Existing local conversation remains after restart',
  );
  driver.check(
    (await driver.c.evaluate("window.desktopAPI.readEvaluation('runs')")).items.some(
      (r) => r.run_id === run.run_id,
    ),
    'Evaluation history remains after restart',
  );
  await driver.m.evaluate(
    "require('electron').dialog.showMessageBox=async(o)=>{if(o.title!=='扩展权限确认')throw Error('Unexpected dialog');return {response:1,checkboxChecked:false}};true",
  );
  const httpURL = await httpServer(command, args);
  const httpConfig = {
    ...saved.config,
    display_name: 'Owned HTTP fixture',
    transport: 'http',
    endpoint_or_command: httpURL,
    args: [],
    environment_names: [],
    allow_loopback: true,
    credential_reference: false,
  };
  const httpRow = await control('server-save', { config: httpConfig });
  await control('connect', { server_id: httpRow.server_id });
  const httpTools = (await read('tools', { server_id: httpRow.server_id })).items;
  const resource = await control('preview', {
    server_id: httpRow.server_id,
    kind: 'resource',
    name: 'fixture://readme',
    arguments: {},
  });
  driver.check(
    httpTools.length === 8 && resource.trust === 'untrusted_external_data',
    'Actual candidate HTTP fixture connects, discovers and reads a resource through Streamable HTTP',
  );
  await control('disconnect', { server_id: httpRow.server_id });
  await control('server-remove', { server_id: httpRow.server_id });
  httpFixture.kill();
  await new Promise((resolve) =>
    httpFixture.exitCode !== null ? resolve() : httpFixture.once('exit', resolve),
  );
  driver.check(
    httpFixture.exitCode !== null || httpFixture.signalCode !== null,
    'Only the owned standalone HTTP fixture is terminated',
  );
  httpFixture = null;
  await settings();
  driver.report.registryUI = [];
  for (const size of [1, 5, 20]) {
    let rows = (await read('servers')).items;
    while (rows.length < size) {
      await control('server-save', {
        config: {
          ...saved.config,
          display_name: 'Metadata fixture ' + rows.length,
          enabled: false,
        },
      });
      rows = (await read('servers')).items;
    }
    const start = performance.now();
    await driver.click('刷新状态');
    await driver.c.until(
      `document.querySelector('[aria-label="MCP 服务器列表"]').children.length===${size}`,
    );
    driver.report.registryUI.push({
      configuredServers: size,
      refreshWallMs: performance.now() - start,
      rendererHeap: await driver.c.evaluate('performance.memory?.usedJSHeapSize || null'),
      mainMemory: await driver.m.evaluate('process.getProcessMemoryInfo()'),
    });
    driver.check(
      rows.every((r) => r.connection_status !== 'CONNECTED'),
      `UI loads ${size} configured servers without starting any executable`,
    );
  }
  await driver.close();
  driver.report.manual.push(
    'Native permission-dialog responses are mocked; no claim of human approval, third-party service reliability, paid AI, real voice or Windows focus automation.',
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
  .catch(async (error) => {
    if (driver) {
      driver.report.error = String(error.stack || error);
      if (driver.c) {
        driver.report.visibleFailure = await driver.c
          .evaluate('document.body.innerText')
          .catch(() => 'Renderer unavailable');
        await driver.screenshot('failure').catch(() => {});
      }
      driver.persist();
    }
    console.error(error.stack || error);
    process.exitCode = 1;
  })
  .finally(async () => {
    if (httpFixture) httpFixture.kill(); // Standalone fixture is this driver's direct child.
    await driver?.cleanup();
  });

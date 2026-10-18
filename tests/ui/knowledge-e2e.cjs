'use strict';
// Test-only native dialog choices and loopback model. Product UI/IPC/backend,
// indexing, citations, storage, workflows and Evaluation workers are real.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const project = path.resolve(__dirname, '../..');
const { StudioSmokeDriver, MOCK_REPLY, q, sleep } = require(
  path.join(project, 'tests/ui/studio-smoke-driver.cjs'),
);
let driver;
const read = (operation, options = {}) =>
  driver.c.evaluate(`window.desktopAPI.readKnowledge(${q(operation)},${q(options)})`);
const control = (operation, options = {}) =>
  driver.c.evaluate(`window.desktopAPI.controlKnowledge(${q(operation)},${q(options)})`);
async function select(selector, value) {
  await driver.c.evaluate(
    `(()=>{const node=document.querySelector(${q(selector)});if(!node)throw Error('Select missing');node.value=${q(value)};node.dispatchEvent(new Event('change',{bubbles:true}));return true})()`,
  );
  await sleep(100);
}
async function openKnowledge() {
  if (!(await driver.c.evaluate("!!document.querySelector('.knowledge-panel')")))
    await driver.click('知识与记忆', "document.querySelector('.sidebar-bottom')");
  await driver.c.until("!!document.querySelector('.knowledge-panel')");
}
async function idle() {
  await driver.c.until(
    "![...document.querySelectorAll('.knowledge-panel button')].find(n=>n.textContent==='新建知识库')?.disabled",
  );
  await sleep(100);
}
async function index(collectionId) {
  await driver.click('增量索引', "document.querySelector('.knowledge-panel')");
  await driver.c.until(
    `window.desktopAPI.readKnowledge('jobs',{collection_id:${q(collectionId)}}).then(value=>value.jobs[0]?.state==='COMPLETED')`,
    60000,
  );
  await idle();
}
async function configureDialogs(root) {
  await driver.m.evaluate(
    `(()=>{const {dialog}=require('electron');globalThis.__knowledgeDialog={approved:true,root:${q(root)},confirms:[],picks:0};const open=dialog.showOpenDialog.bind(dialog),confirm=dialog.showMessageBox.bind(dialog);dialog.showOpenDialog=async(options)=>{if(options.title?.includes('仅授权所选目录')){globalThis.__knowledgeDialog.picks++;return {canceled:false,filePaths:[globalThis.__knowledgeDialog.root]}}return open(options)};dialog.showMessageBox=async(options)=>{if(['知识与记忆权限','工作流执行确认'].includes(options.title)){globalThis.__knowledgeDialog.confirms.push({message:options.message,approved:globalThis.__knowledgeDialog.approved});return {response:globalThis.__knowledgeDialog.approved?1:0,checkboxChecked:false}}return confirm(options)};return true})()`,
  );
}
function modelFixture() {
  driver.mock.prependListener('request', (request, response) => {
    let body = '',
      reply = MOCK_REPLY;
    request.on('data', (part) => {
      body += part;
    });
    request.on('end', () => {
      try {
        const payload = JSON.parse(body);
        for (const message of [...(payload.messages || [])].reverse()) {
          const marker = 'UNTRUSTED_KNOWLEDGE_DATA\n';
          if (typeof message.content !== 'string' || !message.content.includes(marker)) continue;
          const groups = JSON.parse(message.content.split(marker).at(-1));
          const rows = groups.flatMap((row) => row.adjacent_parts || [row]);
          const source = rows.find((row) => /8794|9001/.test(row.excerpt));
          if (source)
            reply = `根据项目资料，启动端口为 ${source.excerpt.match(/8794|9001/)[0]}。[K:${source.chunk_id}]`;
          break;
        }
      } catch {
        /* Model listing requests have no body. */
      }
    });
    for (const method of ['write', 'end']) {
      const original = response[method].bind(response);
      response[method] = (chunk, ...args) =>
        original(
          typeof chunk === 'string' ? chunk.replace(q(MOCK_REPLY), q(reply)) : chunk,
          ...args,
        );
    }
  });
}
async function run() {
  driver = new StudioSmokeDriver();
  await driver.prepare();
  modelFixture();
  await driver.launch();
  const info = await driver.c.evaluate('window.desktopAPI.appInfo()');
  driver.check(
    info.version === '0.2.0-knowledge.1' && info.packaged === !driver.source,
    'Actual candidate identity and package boundary',
    { version: info.version, packaged: info.packaged },
  );
  await driver.c.evaluate(
    `window.desktopAPI.applyApiPreset({name:'Knowledge loopback fixture',provider:'openai-compatible',base_url:'http://127.0.0.1:${driver.ports.model}/v1',api_key:'mock-local-only',model:'local-smoke',capabilities:{text:true,tool_calling:false},timeout_seconds:15})`,
  );
  await driver.c.evaluate("localStorage.setItem('voice-enabled','false');true");
  await driver.c.call('Page.reload');
  await driver.c.until(
    "!!document.querySelector('[aria-label=输入消息]')&&!document.querySelector('[aria-label=输入消息]').disabled",
  );
  await openKnowledge();
  const configurationRequests = driver.report.localModelRequests;
  const conversations = await driver.json('/api/conversations?character_id=assistant');
  const owner = conversations[0].id;
  const scope = { conversation_id: owner, character_id: 'assistant' };
  const root = path.join(driver.home, 'SyntheticSources');
  fs.mkdirSync(root);
  const guide = path.join(root, 'guide.md');
  fs.writeFileSync(
    guide,
    '# 示例项目\n\n项目启动端口为 8794。Orchard launch port is 8794.\n',
    'utf8',
  );
  fs.writeFileSync(
    path.join(root, 'source.py'),
    "def load_document():\n    return 'public synthetic evidence'\n",
    'utf8',
  );
  await configureDialogs(root);
  await openKnowledge();
  driver.check(
    (await read('collections')).collections.length === 0,
    'Fresh candidate profile contains no imported libraries or private sources',
  );
  await driver.field('[aria-label="知识库名称"]', '示例项目资料');
  await driver.field('[aria-label="知识库说明"]', '原创合成资料，仅供本地验收');
  await driver.click('新建知识库', "document.querySelector('.knowledge-panel')");
  await idle();
  let collection = (await read('collections')).collections[0];
  await driver.m.evaluate('globalThis.__knowledgeDialog.approved=false;true');
  await driver.click('选择并授权目录', "document.querySelector('.knowledge-panel')");
  await idle();
  driver.check(
    (await read('collections')).collections[0].source_roots.length === 0,
    'Denied native directory confirmation does not create a root grant',
  );
  await driver.m.evaluate('globalThis.__knowledgeDialog.approved=true;true');
  await driver.click('选择并授权目录', "document.querySelector('.knowledge-panel')");
  await idle();
  collection = (await read('collections')).collections[0];
  driver.check(
    collection.source_roots.length === 1 && collection.source_roots[0].path === root,
    'Only test-selected root is authorized through product main process',
  );
  await index(collection.id);
  collection = (await read('collections')).collections[0];
  driver.check(
    collection.document_count === 2 && collection.chunk_count >= 2,
    'Real candidate indexes original Markdown and Python fixtures',
  );
  const docs = await read('documents', { collection_id: collection.id });
  driver.check(
    docs.documents.length === 2 && docs.documents.every((row) => row.status === 'INDEXED'),
    'Document diagnostics show actual successful parser records',
  );
  await driver.c.evaluate(
    "(()=>{const n=[...document.querySelectorAll('.knowledge-selection label')].find(n=>n.textContent.includes('示例项目资料'))?.querySelector('input');if(!n)throw Error('Collection checkbox missing');n.click();return true})()",
  );
  await driver.click('仅授权本地检索', "document.querySelector('.knowledge-panel')");
  await idle();
  await driver.field('[aria-label="知识检索问题"]', '启动端口');
  await driver.click('搜索授权知识库', "document.querySelector('.knowledge-panel')");
  await idle();
  await driver.c.until(
    "document.querySelector('.knowledge-sources')?.innerText.includes('guide.md')",
  );
  const found = await control('search', {
    ...scope,
    search: { query: '启动端口', collection_ids: [collection.id], mode: 'hybrid' },
  });
  driver.check(
    found.results[0].relative_path === 'guide.md' && found.results[0].location.line_start === 3,
    'Chinese keyword retrieval returns the actual source line',
  );
  driver.check(
    found.mode === 'keyword' && found.warnings.some((value) => value.includes('keyword_fallback')),
    'Unconfigured optional embeddings explicitly fall back to keywords',
  );
  driver.check(
    driver.report.localModelRequests === configurationRequests,
    'Creating, indexing and local search send no model requests',
  );
  await driver.c.evaluate(
    "document.querySelector('.knowledge-sources')?.scrollIntoView({block:'center'});true",
  );
  await driver.screenshot('knowledge-search');
  await driver.m.evaluate('globalThis.__knowledgeDialog.approved=false;true');
  await driver.click('授权检索片段给当前模型', "document.querySelector('.knowledge-panel')");
  await idle();
  driver.check(
    !(await read('selection', scope)).transfer_approved,
    'Denied separate transfer confirmation cannot inherit local read permission',
  );
  await driver.m.evaluate('globalThis.__knowledgeDialog.approved=true;true');
  await driver.click('授权检索片段给当前模型', "document.querySelector('.knowledge-panel')");
  await idle();
  await driver.c.evaluate(
    "(()=>{const input=[...document.querySelectorAll('.knowledge-selection label')].find(n=>n.textContent.includes('此会话启用知识库回答'))?.querySelector('input');if(!input)throw Error('RAG toggle missing');input.click();return true})()",
  );
  await driver.c.until(
    `window.desktopAPI.readKnowledge('selection',${q(scope)}).then(value=>value.preference.rag_enabled)`,
  );
  await idle();
  driver.check(
    (await read('selection', scope)).transfer_approved,
    'Current model target is explicitly granted for this conversation',
  );
  await driver.click('返回', "document.querySelector('.knowledge-panel')");
  await driver.send('项目启动端口是什么？');
  await driver.c.until(
    "[...document.querySelectorAll('.message-row.assistant')].some(n=>n.innerText.includes('8794'))",
    30000,
  );
  await driver.c.until("!!document.querySelector('[data-knowledge-source]')", 20000);
  await driver.click('〔来源〕', "[...document.querySelectorAll('.message-row.assistant')].at(-1)");
  driver.check(
    await driver.c.evaluate("!!document.querySelector('[data-knowledge-source][open]')"),
    'Actual chat citation button opens the matching source details',
  );
  await driver.screenshot('knowledge-chat-citation');
  fs.writeFileSync(
    guide,
    '# 示例项目\n\n项目启动端口变为 9001。Orchard port is now 9001.\n',
    'utf8',
  );
  const stale = await driver.c.evaluate(
    `window.desktopAPI.controlKnowledge('reveal-source',${q({ ...scope, reference: found.results[0] })}).then(()=>false,error=>error.message.includes('KNOWLEDGE_STALE_EVIDENCE'))`,
  );
  driver.check(stale, 'Changed source is rejected before a native source-open command');
  await openKnowledge();
  await index(collection.id);
  await driver.click('长期记忆', "document.querySelector('.knowledge-panel')");
  await driver.field('[aria-label="记忆内容"]', '项目说明请使用中文，回答尽量简洁。');
  await driver.click('明确保存记忆', "document.querySelector('.knowledge-memory')");
  await driver.c.until(
    "document.querySelector('.knowledge-memory article')?.innerText.includes('中文')",
  );
  let memories = (await read('memories', { ...scope, scope: 'CONVERSATION', owner_id: owner }))
    .memories;
  driver.check(
    memories.length === 1 && memories[0].source_type === 'manual',
    'Explicit memory is saved in the current conversation only',
  );
  await driver.click('停用', "document.querySelector('.knowledge-memory')");
  await driver.c.until(
    `window.desktopAPI.readKnowledge('memories',${q({ ...scope, scope: 'CONVERSATION', owner_id: owner })}).then(v=>v.memories[0]?.enabled===false)`,
  );
  await driver.click('编辑', "document.querySelector('.knowledge-memory')");
  await driver.field('[aria-label="记忆内容"]', '项目说明请使用中文，引用资料时给出来源。');
  await driver.click('保存修改', "document.querySelector('.knowledge-memory')");
  await driver.c.until(
    "document.querySelector('.knowledge-memory article')?.innerText.includes('给出来源')",
  );
  const editedMemory = (
    await read('memories', { ...scope, scope: 'CONVERSATION', owner_id: owner })
  ).memories[0];
  driver.check(
    editedMemory.enabled === false,
    'Editing a disabled memory preserves its explicit disabled status',
  );
  await driver.c.evaluate(
    "document.querySelector('.knowledge-memory article')?.scrollIntoView({block:'center'});true",
  );
  await driver.screenshot('knowledge-memory');
  const beforeRestart = driver.report.localModelRequests;
  await driver.close();
  await driver.launch();
  await configureDialogs(root);
  await driver.openConversation(owner);
  await openKnowledge();
  driver.check(
    driver.report.localModelRequests === beforeRestart,
    'Restart does not replay chat or indexing/model operations',
  );
  driver.check(
    (await read('collections')).collections[0].document_count === 2,
    'Restart preserves collection metadata and index',
  );
  memories = (await read('memories', { ...scope, scope: 'CONVERSATION', owner_id: owner }))
    .memories;
  driver.check(
    memories.length === 1 &&
      memories[0].content.includes('给出来源') &&
      memories[0].enabled === false,
    'Restart preserves edited scoped memory and its disabled status',
  );
  const response = await fetch(driver.base + '/api/conversations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: q({ character_id: 'assistant', conversation_type: 'work' }),
  });
  assert(response.ok);
  const workOwner = (await response.json()).id;
  const workScope = { conversation_id: workOwner, character_id: 'assistant' };
  driver.check(
    (await read('selection', workScope)).collection_ids.length === 0,
    'A new Work conversation does not inherit another conversation’s grants',
  );
  const isolated = (
    await read('memories', { ...workScope, scope: 'CONVERSATION', owner_id: workOwner })
  ).memories;
  driver.check(isolated.length === 0, 'Conversation memory is isolated after a real restart');
  await control('selection', {
    ...workScope,
    collection_ids: [collection.id],
    allow_model_transfer: true,
  });
  const template = await driver.c.evaluate(
    "window.desktopAPI.readWorkflows('template',{template_id:'project-knowledge-assistant'})",
  );
  const saved = await driver.c.evaluate(
    `window.desktopAPI.controlWorkflows('save',${q({ definition: template, revision: 0 })})`,
  );
  const published = await driver.c.evaluate(
    `window.desktopAPI.controlWorkflows('publish',${q({ definition: saved.definition, revision: saved.revision })})`,
  );
  const started = await driver.c.evaluate(
    `window.desktopAPI.controlWorkflows('start',${q({ workflow_id: template.workflow_id, version_id: published.version_id, conversation_id: workOwner, inputs: { query: '启动端口', collection_ids: [collection.id] } })})`,
  );
  const runId = started.run_id;
  await driver.c.until(
    `window.desktopAPI.readWorkflows('run',${q({ run_id: runId, conversation_id: workOwner })}).then(r=>['COMPLETED','FAILED','NEEDS_REVIEW'].includes(r.state))`,
    40000,
  );
  const workflow = await driver.c.evaluate(
    `window.desktopAPI.readWorkflows('run',${q({ run_id: runId, conversation_id: workOwner })})`,
  );
  driver.check(
    workflow.state === 'COMPLETED' && JSON.stringify(workflow.output).includes('9001'),
    'Knowledge → model → output uses current verified source through original runtime',
    { state: workflow.state },
  );
  await driver.c.call('Page.reload');
  await driver.c.until("!!document.querySelector('[aria-label=输入消息]')");
  await driver.openConversation(workOwner);
  await driver.click('模块');
  await driver.click('本地知识与记忆', "document.querySelector('.workspace-module-picker')");
  await driver.c.until("!!document.querySelector('[data-module=knowledge] .knowledge-panel')");
  driver.check(true, 'New knowledge module opens inside the original Workbench');
  const fps = await driver.c.evaluate(
    "new Promise(resolve=>{let frames=0;const start=performance.now();function step(now){frames++;if(now-start>=1000)resolve({frames,elapsed_ms:now-start,fps:frames*1000/(now-start),visibility:document.visibilityState});else requestAnimationFrame(step)}requestAnimationFrame(step);setTimeout(()=>resolve({fps:null,visibility:document.visibilityState,reason:'frame_deadline'}),5000)})",
  );
  driver.report.rendererMeasurement = fps;
  driver.persist();
  // Original fixtures only: sample actual renderer frames while the backend
  // performs a 1,000-document incremental job, rather than calling a heartbeat FPS.
  const bulk = path.join(root, 'bulk');
  fs.mkdirSync(bulk);
  for (let number = 0; number < 1000; number++) {
    fs.writeFileSync(
      path.join(bulk, `entry-${number}.md`),
      `# Synthetic entry ${number}\n\nOriginal benchmark record ${number}. 合成测试资料。\n`,
      'utf8',
    );
  }
  const beforeBulk = driver.report.localModelRequests;
  await driver.click('增量索引', "document.querySelector('.knowledge-panel')");
  await driver.c.until(
    `window.desktopAPI.readKnowledge('jobs',${q({ collection_id: collection.id })}).then(value=>['SCANNING','PARSING','CHUNKING','INDEXING'].includes(value.jobs[0]?.state))`,
  );
  driver.report.duringIndexRenderer = await driver.c.evaluate(
    "new Promise(resolve=>{const gaps=[];const start=performance.now();let previous=start;function step(now){gaps.push(now-previous);previous=now;if(now-start<10000){requestAnimationFrame(step);return}const sorted=[...gaps].sort((a,b)=>a-b);resolve({frames:gaps.length,elapsed_ms:now-start,fps:gaps.length*1000/(now-start),frame_gap_p95_ms:sorted[Math.floor((sorted.length-1)*.95)],frame_gap_max_ms:Math.max(...gaps),visibility:document.visibilityState})}requestAnimationFrame(step);setTimeout(()=>resolve({fps:null,visibility:document.visibilityState,reason:'frame_deadline'}),16000)})",
  );
  await driver.c.until(
    `window.desktopAPI.readKnowledge('jobs',${q({ collection_id: collection.id })}).then(value=>value.jobs[0]?.state==='COMPLETED')`,
    90000,
  );
  const bulkCollection = (await read('collections')).collections.find(
    (row) => row.id === collection.id,
  );
  driver.check(
    bulkCollection.document_count === 1002 &&
      driver.report.localModelRequests === beforeBulk &&
      driver.report.duringIndexRenderer.fps !== null,
    'Renderer remains active during an actual 1,000-document offline index job',
    driver.report.duringIndexRenderer,
  );
  await driver.click('模块');
  await driver.click(
    'Agent Evaluation · 评测',
    "document.querySelector('.workspace-module-picker')",
  );
  await driver.c.until("!!document.querySelector('.evaluation-dashboard')");
  await driver.c.evaluate(
    'document.querySelector(\'[data-module=evaluation] [aria-label="最大化Agent Evaluation · 评测"]\')?.click();true',
  );
  await select('[aria-label=评测测试集]', 'knowledge-core');
  await driver.c.until(
    "[...document.querySelector('[aria-label=评测任务]').options].some(n=>n.value==='KB-028')",
  );
  await driver.field('[aria-label=评测并发]', '2');
  await driver.field('[aria-label=评测超时]', '35');
  const beforeRuns = await driver.c.evaluate(
    "window.desktopAPI.readEvaluation('runs').then(value=>value.items.map(row=>row.run_id))",
  );
  await driver.click('开始评测', "document.querySelector('.evaluation-dashboard')");
  await driver.c.until(
    `window.desktopAPI.readEvaluation('runs').then(value=>value.items.some(row=>!${q(beforeRuns)}.includes(row.run_id)))`,
  );
  const evaluation = await driver.c.evaluate(
    `window.desktopAPI.readEvaluation('runs').then(value=>value.items.find(row=>!${q(beforeRuns)}.includes(row.run_id)))`,
  );
  await driver.c.until(
    `window.desktopAPI.readEvaluation('detail',{run_id:${q(evaluation.run_id)}}).then(r=>['COMPLETED','FAILED','INTERRUPTED'].includes(r.status))`,
    180000,
  );
  const evaluated = await driver.c.evaluate(
    `window.desktopAPI.readEvaluation('detail',{run_id:${q(evaluation.run_id)}})`,
  );
  driver.check(
    evaluated.metrics.counts.PASS === 28 && evaluated.metrics.real_task_success_rate === null,
    'All 28 cases run through the candidate Evaluation worker',
    {
      packaged: !driver.source,
      counts: evaluated.metrics.counts,
      knowledge: evaluated.metrics.knowledge,
    },
  );
  driver.report.knowledgeBenchmark = evaluated;
  await driver.c.until(
    "document.querySelector('.evaluation-progress')?.innerText.includes('COMPLETED')",
  );
  for (const format of ['JSON', 'CSV', 'MARKDOWN']) {
    await driver.click('导出 ' + format, "document.querySelector('.evaluation-dashboard')");
  }
  const exports = fs
    .readdirSync(path.join(driver.home, 'Exports'))
    .filter((file) => file.startsWith('evaluation-' + evaluated.run_id));
  driver.check(exports.length === 3, 'Knowledge Evaluation exports all three report formats');
  const exported = JSON.parse(
    fs.readFileSync(
      path.join(
        driver.home,
        'Exports',
        exports.find((file) => file.endsWith('.json')),
      ),
      'utf8',
    ),
  );
  driver.check(
    exported.results.length === 28 &&
      exported.results.every((row) => row.execution_status === 'PASS'),
    'Export contains every actual Knowledge case result',
  );
  fs.writeFileSync(path.join(driver.home, 'knowledge-benchmark.json'), q(exported));
  driver.report.nativeDialogChoices = 'synthetic_only_in_test_main';
  await driver.close();
  driver.report.result = 'pass';
  driver.persist();
}
run()
  .catch(async (error) => {
    if (driver) {
      driver.report.failure = String(error.stack || error);
      driver.persist();
      await driver.screenshot('knowledge-failure').catch(() => {});
    }
    console.error(error);
    process.exitCode = 1;
  })
  .finally(async () => {
    await driver?.cleanup();
  });

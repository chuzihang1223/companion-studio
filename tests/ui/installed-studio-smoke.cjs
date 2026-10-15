'use strict';

// Opt-in installed application test. Every profile, model and browser fixture is local.
const assert = require('node:assert/strict');
const { StudioSmokeDriver, MOCK_REPLY, q, sleep } = require('./studio-smoke-driver.cjs');
const { exerciseWorkbench, verifyRestoredWorkbench } = require('./studio-workbench-smoke.cjs');
const browserURL = 'https://companion-studio-smoke.test/fixture';
const browserTitle = 'Companion Studio 本机网页测试';
let driver;

async function browserFixture() {
  // Standard HTTPS is retained. Only this fresh process's browser partition gets a local fixture.
  // Localhost must remain denied; no production URL/permission handler is replaced.
  await driver.m.evaluate(
    `(()=>{const s=require('electron').session.fromPartition('persist:companion-workspace-browser');globalThis.__studioBrowserFixture={hits:0,unexpected:0};s.protocol.handle('https',request=>{if(new URL(request.url).hostname!=='companion-studio-smoke.test'){globalThis.__studioBrowserFixture.unexpected++;return Response.error()}globalThis.__studioBrowserFixture.hits++;return new Response(${q('<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>' + browserTitle + '</title></head><body><h1>本机隔离网页</h1><p>这是安装版网页视图测试内容，无外部资源。</p></body></html>')},{headers:{'content-type':'text/html; charset=utf-8'}})});return true})()`,
  );
  await driver.c.evaluate("window.desktopAPI.setTrustedAction('browser.open:any','always')");
  const denied = await driver.c.evaluate(
    "window.desktopAPI.openWorkspaceBrowser('https://127.0.0.1/',{x:0,y:0,width:400,height:200},{tabId:'local-denied',background:true}).then(()=>({allowed:true}),error=>({allowed:false,error:error.message}))",
  );
  driver.check(
    !denied.allowed && /本机或内网/.test(denied.error),
    'Workspace browser still refuses direct localhost URLs',
  );
  await driver.c.evaluate("document.querySelector('[data-dock-module=browser]').click();true");
  await driver.c.until(
    "!!document.querySelector('[aria-label=网页地址]') && !document.querySelector('[aria-label=网页地址]').closest('[hidden]')",
  );
  await driver.field('[aria-label="网页地址"]', browserURL);
  await driver.click('打开', "document.querySelector('.work-browser-toolbar')");
  await driver.c.until(
    "document.querySelector('.browser-tabstrip')?.innerText.includes(" + q(browserTitle) + ')',
    25000,
  );
  const details = await driver.m.evaluate(
    `(async()=>{const w=require('electron').webContents.getAllWebContents().find(w=>w.getURL()===${q(browserURL)});if(!w)throw Error('Browser view missing');const p=w.getLastWebPreferences();const dom=await w.executeJavaScript('({text:document.body.innerText,hasDesktopAPI:!!window.desktopAPI,hasRequire:typeof require!=="undefined"})');return {dom,nodeIntegration:p.nodeIntegration,contextIsolation:p.contextIsolation,sandbox:p.sandbox,fixture:globalThis.__studioBrowserFixture}})()`,
  );
  driver.check(
    details.dom.text.includes('本机隔离网页') &&
      details.fixture.hits > 0 &&
      details.fixture.unexpected === 0,
    'Real browser view loads the local fixture through a valid HTTPS test URL',
    { url: browserURL },
  );
  driver.check(
    !details.dom.hasDesktopAPI &&
      !details.dom.hasRequire &&
      !details.nodeIntegration &&
      details.contextIsolation &&
      details.sandbox,
    'Embedded fixture page cannot access the application preload or Node',
  );
  await driver.c.evaluate(
    `document.querySelector('[aria-label=${q('关闭' + browserTitle)}]').click();true`,
  );
  await driver.c.until(
    "!document.querySelector('.browser-tabstrip')?.innerText.includes(" + q(browserTitle) + ')',
  );
  await sleep(150);
  const stillOpen = await driver.m.evaluate(
    `require('electron').webContents.getAllWebContents().some(w=>!w.isDestroyed()&&w.getURL()===${q(browserURL)})`,
  );
  driver.check(!stillOpen, 'Closing the browser UI tab destroys its native WebContents');
  await driver.m.evaluate(
    "require('electron').session.fromPartition('persist:companion-workspace-browser').protocol.unhandle('https');true",
  );
}

async function applicationDiscovery() {
  await driver.c.evaluate(
    "globalThis.__studioDiscovery=null;void window.desktopAPI.diagnoseApplication('记事本').then(value=>globalThis.__studioDiscovery={value},error=>globalThis.__studioDiscovery={error:String(error)});true",
  );
  await driver.c.until('globalThis.__studioDiscovery!==null', 70000);
  const discovery = await driver.c.evaluate('globalThis.__studioDiscovery');
  assert.ok(!discovery.error, discovery.error);
  const listed = await driver.c.evaluate('window.desktopAPI.listKnownApps()');
  const notepad = listed.find((row) => row.id === 'notepad');
  driver.check(
    notepad?.installed === true,
    'Real Windows application catalog lists installed Notepad',
    {
      targetedCandidates: discovery.value.candidates.length,
      sources: discovery.value.levels.map((row) => row.source),
    },
  );
  if (!discovery.value.candidates.length)
    driver.report.manual.push(
      '定向发现未返回候选；Notepad 来自有效的系统入口。不能声称所有动态发现来源都通过。',
    );
  if (process.env.COMPANION_TEST_START_NOTEPAD === '1') {
    await driver.c.evaluate("window.desktopAPI.setTrustedAction('app.launch:notepad','always')");
    const launched = await driver.c.evaluate("window.desktopAPI.launchKnownApp('notepad')");
    // Keep the actual local result even if the readiness assertion fails.
    driver.report.notepadLaunch = launched;
    driver.persist();
    driver.check(
      launched.verified === true && launched.window_ready === true && launched.focused === true,
      'Notepad has a ready focused window through the authorized product launch path',
      {
        pid: launched.pid,
        alreadyRunning: launched.already_running === true,
        windowReady: launched.window_ready,
        focused: launched.focused,
      },
    );
    driver.report.manual.push(
      '测试经软件授权打开/聚焦了记事本。未自动关闭用户应用；请人工确认窗口内容和按需退出。',
    );
  } else {
    driver.report.manual.push(
      '本次未启动或聚焦记事本。设置 COMPANION_TEST_START_NOTEPAD=1 并保持桌面空闲可验证实际 OS 启动；目录列出不等于启动成功。',
    );
  }
}

async function windowControls() {
  const nativeWindow =
    "require('electron').BrowserWindow.getAllWindows().find(w=>w.webContents.getURL().includes('/app/'))";
  const state = () =>
    driver.m.evaluate(
      `(()=>{const w=${nativeWindow};if(!w)throw Error('Main window missing');return {maximized:w.isMaximized(),minimized:w.isMinimized()}})()`,
    );
  const waitState = async (key, expected) => {
    const deadline = Date.now() + 5000;
    let actual;
    do {
      actual = await state();
      if (actual[key] === expected) return actual;
      await sleep(100);
    } while (Date.now() < deadline);
    assert.fail('Window state did not settle: ' + q(actual));
  };
  const initial = await state();
  assert.ok(!initial.maximized && !initial.minimized, 'Start window controls in normal state');
  await driver.c.evaluate("document.querySelector('[aria-label=最大化窗口]').click();true");
  await waitState('maximized', true);
  driver.check(true, 'Product maximize button changes the actual native window state');
  await driver.c.until("!!document.querySelector('[aria-label=恢复窗口]')");
  await driver.c.evaluate("document.querySelector('[aria-label=恢复窗口]').click();true");
  await waitState('maximized', false);
  driver.check(true, 'Product restore button returns the maximized native window to normal');
  await driver.c.evaluate("document.querySelector('[aria-label=最小化窗口]').click();true");
  await waitState('minimized', true);
  driver.check(true, 'Product minimize button minimizes the actual native window');
  // 最小化后的恢复只用于继续测试，不将测试 API 操作当作产品按钮验收。
  await driver.m.evaluate(`(()=>{const w=${nativeWindow};w.restore();return true})()`);
  await waitState('minimized', false);
  driver.report.windowControls = { initial, resumeAfterMinimize: 'test-only native restore' };
}

async function run() {
  driver = new StudioSmokeDriver();
  await driver.prepare();
  await driver.launch();
  const info = await driver.c.evaluate('window.desktopAPI.appInfo()');
  driver.check(
    info.packaged && info.product_name === 'Companion Studio' && info.clean_distribution === true,
    'The installed clean Companion Studio application is running',
    { version: info.version, buildId: info.build_id },
  );
  const library = await driver.c.evaluate('window.desktopAPI.characterLibrary()');
  driver.check(
    library.active_id === 'assistant' &&
      library.characters.length === 1 &&
      !library.characters[0].avatar_url,
    'Fresh profile contains only the neutral assistant',
  );
  driver.check(
    !(await driver.c.evaluate('window.desktopAPI.listApiPresets()')).presets.some(
      (row) => row.has_key,
    ),
    'Fresh profile contains no imported API credentials',
  );
  driver.check(
    await driver.c.evaluate(
      "document.querySelectorAll('.message-row').length===0 && !/爱弥斯|Aemeath|aemeath|鸣潮/.test(document.body.innerText)",
    ),
    'Fresh renderer has no private persona or chat history',
  );
  await driver.screenshot('fresh-assistant');
  await driver.c.evaluate(
    `window.desktopAPI.applyApiPreset({name:'Isolated local smoke',provider:'openai-compatible',base_url:'http://127.0.0.1:${driver.ports.model}/v1',api_key:'mock-local-only',model:'local-smoke',temperature:null,max_tokens:null,timeout_seconds:15,capabilities:{text:true,vision:false,tool_calling:false,web_search:false,structured_output:false}})`,
  );
  await driver.c.evaluate('window.desktopAPI.workPreferences({auto_work_mode:false})');
  await driver.send('安装版聊天持久化测试');
  await driver.c.until(
    "document.querySelector('.message-row.assistant .bubble')?.innerText.includes(" +
      q(MOCK_REPLY) +
      ')',
    25000,
  );
  let conversations = await driver.json('/api/conversations?character_id=assistant');
  const chat = conversations.find((row) => row.conversation_type === 'chat');
  assert.ok(chat, 'Chat conversation persisted');
  const chatHistory = await driver.json(
    '/api/chat/history?character_id=assistant&conversation_id=' + chat.id,
  );
  driver.check(
    chatHistory.some((row) => row.role === 'user' && row.content === '安装版聊天持久化测试') &&
      chatHistory.some((row) => row.role === 'assistant' && row.content.includes(MOCK_REPLY)),
    'Chat UI sends a real local model request and persists both messages',
  );
  await driver.screenshot('chat-local-reply');

  const modelRequestsBeforeWork = driver.report.localModelRequests;
  await driver.click('工作模式');
  await driver.c.until("!!document.querySelector('.workbench')");
  await driver.send('查看系统状态');
  await driver.c.until(
    "document.querySelector('.work-status')?.innerText.includes('已完成')",
    40000,
  );
  conversations = await driver.json('/api/conversations?character_id=assistant');
  const work = conversations.find((row) => row.conversation_type !== 'chat');
  assert.ok(work, 'Work conversation persisted');
  const session = await driver.json('/api/work/by-conversation/' + work.id);
  driver.check(
    session.tabs.some((row) => row.kind === 'system') &&
      session.events.some((row) => row.type === 'tool_completed'),
    'Work UI performs a real system observation and receives task events',
  );
  const workHistory = await driver.json(
    '/api/chat/history?character_id=assistant&conversation_id=' + work.id,
  );
  const workReply = workHistory.find((row) => row.role === 'assistant' && row.content.trim());
  assert.ok(workReply, 'Work assistant feedback persisted');
  const persistedFeedbackAt = Date.now();
  // WorkSession 的完成事件和回复文字走各自的事件流；状态先完成时，
  // FirstSpeechGate 仍可能在逐段展示已保存文字。等实际 DOM 呈现完再检查。
  await driver.c.until(
    `document.querySelector('.work-dialogue')?.innerText.includes(${q(workReply.content)})`,
    10000,
  );
  driver.check(
    await driver.c.evaluate(
      `document.querySelector('.work-dialogue')?.innerText.includes(${q(workReply.content)})`,
    ),
    'Work completion persists and displays its actual task feedback',
    {
      localModelRequestsDelta: driver.report.localModelRequests - modelRequestsBeforeWork,
      actualFeedbackPersisted: true,
      actualFeedbackDisplayed: true,
      feedbackRenderWaitMs: Date.now() - persistedFeedbackAt,
    },
  );
  const browserSkipped = process.env.COMPANION_TEST_SKIP_BROWSER === '1';
  if (browserSkipped) {
    driver.report.skipped = ['Native workspace browser fixture'];
    driver.report.manual.push(
      '原生网页检查被显式跳过；本次结果仅用于其余阶段，不能称全部验收成功。',
    );
  } else await browserFixture();
  const layout = await exerciseWorkbench(driver);
  await applicationDiscovery();
  await driver.close();
  await driver.launch();
  for (const [id, previous] of [
    [chat.id, chatHistory],
    [work.id, workHistory],
  ]) {
    const restored = await driver.json(
      '/api/chat/history?character_id=assistant&conversation_id=' + id,
    );
    assert.deepEqual(
      restored.map((row) => [row.id, row.role, row.content]),
      previous.map((row) => [row.id, row.role, row.content]),
    );
  }
  driver.check(true, 'Chat and Work message IDs/content survive actual restart');
  await driver.openConversation(work.id);
  await verifyRestoredWorkbench(driver, layout);
  await driver.openConversation(chat.id);
  await driver.c.until(
    "!!document.querySelector('.message-row.assistant .bubble') && !document.querySelector('.workbench')",
  );
  driver.check(
    await driver.c.evaluate(
      "document.querySelector('.message-row.assistant .bubble').innerText.includes(" +
        q(MOCK_REPLY) +
        ')',
    ),
    'Restored Chat history is actually displayed after opening its session',
  );
  await driver.screenshot('chat-restored');
  await windowControls();
  await driver.close();
  driver.report.manual.push(
    'CDP 指针覆盖真实 Chromium 事件和布局持久化；不等于高 DPI/多显示器、系统抢焦点、长期使用的人工验收或真实 FPS 基准。',
  );
  driver.report.enabledChecksPassed = true;
  driver.report.success = !browserSkipped;
  if (browserSkipped) {
    driver.report.error = 'Incomplete acceptance: browser stage explicitly skipped';
    process.exitCode = 2;
  }
  driver.persist();
}

run()
  .catch((error) => {
    if (driver) {
      driver.report.success = false;
      driver.report.error = String(error.stack || error);
    } else console.error(error.message);
    process.exitCode = 1;
  })
  .finally(async () => {
    await driver?.cleanup();
    if (driver)
      console.log(
        q({
          success: driver.report.success === true,
          profile: driver.home,
          checks: driver.report.checks.length,
          manual: driver.report.manual,
          error: driver.report.error,
        }),
      );
  });

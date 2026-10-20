'use strict';

const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const net = require('node:net');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { connect, connectMain, sleep } = require('./v81-harness.cjs');
const q = JSON.stringify;
const MOCK_REPLY = '本机隔离模型回复：测试已完成。';

async function unusedPort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const port = server.address().port;
  await new Promise((resolve) => server.close(resolve));
  return port;
}

/** Only a new isolated profile may be used; real UserData is never accepted. */
class StudioSmokeDriver {
  constructor() {
    const project = path.resolve(__dirname, '../..');
    this.source = process.env.COMPANION_TEST_SOURCE === '1' ? project : null;
    this.exe = process.env.COMPANION_TEST_EXE;
    assert.ok(
      this.exe && fs.statSync(this.exe).isFile(),
      'Set COMPANION_TEST_EXE to the installed test executable',
    );
    if (this.source)
      assert.equal(
        path.resolve(this.exe).toLowerCase(),
        path.join(project, 'desktop/node_modules/electron/dist/electron.exe').toLowerCase(),
        "Source mode only accepts this product's locked Electron",
      );
    this.home = path.resolve(
      process.env.COMPANION_TEST_HOME ||
        path.join(path.dirname(project), 'Smoke', 'installed-' + Date.now()),
    );
    assert.ok(
      !fs.existsSync(this.home) || fs.readdirSync(this.home).length === 0,
      'Smoke home must be new and empty',
    );
    this.report = {
      profile: this.home,
      executable: this.exe,
      sourceMode: !!this.source,
      realModelRequests: 0,
      realVoiceRequests: 0,
      checks: [],
      manual: [],
      localModelRequests: 0,
    };
    for (const folder of ['UserData', 'Cache'])
      fs.mkdirSync(path.join(this.home, folder), { recursive: true });
  }

  persist() {
    fs.writeFileSync(path.join(this.home, 'report.json'), q(this.report));
  }
  check(value, label, evidence) {
    assert.ok(value, label);
    this.report.checks.push({ label, passed: true, ...(evidence ? { evidence } : {}) });
    this.persist();
  }

  async prepare() {
    this.ports = {
      renderer: await unusedPort(),
      main: await unusedPort(),
      backend: await unusedPort(),
    };
    this.mock = http.createServer((req, res) => {
      let text = '';
      req.on('data', (chunk) => {
        text += chunk;
      });
      req.on('end', () => {
        if (req.url === '/v1/models') {
          res.setHeader('Content-Type', 'application/json');
          res.end(q({ data: [{ id: 'local-smoke' }] }));
          return;
        }
        if (req.url !== '/v1/chat/completions') {
          res.writeHead(404);
          res.end();
          return;
        }
        let body;
        try {
          body = JSON.parse(text);
        } catch {
          res.writeHead(400);
          res.end();
          return;
        }
        this.report.localModelRequests++;
        if (body.stream) {
          res.writeHead(200, { 'Content-Type': 'text/event-stream' });
          res.write(
            'data: ' +
              q({ choices: [{ delta: { content: MOCK_REPLY }, finish_reason: null }] }) +
              '\n\n',
          );
          res.end('data: [DONE]\n\n');
        } else {
          res.setHeader('Content-Type', 'application/json');
          res.end(
            q({
              choices: [
                { message: { role: 'assistant', content: MOCK_REPLY }, finish_reason: 'stop' },
              ],
            }),
          );
        }
      });
    });
    await new Promise((resolve, reject) => {
      this.mock.once('error', reject);
      this.mock.listen(0, '127.0.0.1', resolve);
    });
    this.ports.model = this.mock.address().port;
  }

  async launch() {
    this.child = spawn(
      this.exe,
      [
        ...(this.source ? [path.join(this.source, 'desktop')] : []),
        '--compat',
        '--remote-debugging-port=' + this.ports.renderer,
        '--inspect=' + this.ports.main,
        ...(this.chromiumArgs || []),
        ...(process.env.COMPANION_TEST_CHROMIUM_LOGGING === '1'
          ? ['--enable-logging=stderr', '--v=1']
          : []),
      ],
      {
        cwd: path.dirname(this.exe),
        windowsHide: true,
        env: {
          ...process.env,
          COMPANION_HOME: this.home,
          COMPANION_DATA_DIR: path.join(this.home, 'UserData'),
          COMPANION_CLEAN_DISTRIBUTION: '1',
          COMPANION_FRONTEND_DIST: '',
          BACKEND_PORT: String(this.ports.backend),
          OPENAI_API_KEY: '',
          OPENAI_BASE_URL: `http://127.0.0.1:${this.ports.model}/v1`,
          OPENAI_MODEL: '',
          FISH_API_KEY: '',
          FISH_REFERENCE_ID: '',
          VOICE_PREFETCH_ENABLED: 'false',
          VOICE_MODEL_DIR: path.join(this.home, 'no-model'),
          VOICE_INDEX_MODEL_DIR: path.join(this.home, 'no-model'),
          VOICE_REFERENCES_DIR: path.join(this.home, 'no-references'),
          TEMP: path.join(this.home, 'Cache'),
          TMP: path.join(this.home, 'Cache'),
        },
      },
    );
    this.child.once('error', (error) => {
      this.report.spawnError = error.message;
      this.persist();
    });
    const log = fs.createWriteStream(path.join(this.home, 'launch.log'), { flags: 'a' });
    this.log = log;
    this.child.stdout.pipe(log, { end: false });
    this.child.stderr.pipe(log, { end: false });
    this.child.once('close', () => log.end());
    this.c = await connect(this.ports.renderer);
    const inspector = await connectMain(this.ports.main, 65000);
    // Node inspector evaluates in global scope, where a CommonJS module's require
    // is not guaranteed. Supply a test-local loader; never expose it to a renderer.
    this.m = {
      close: () => inspector.close(),
      evaluate: (expression) =>
        inspector.evaluate(
          `(()=>{const require=process.getBuiltinModule('module').createRequire(process.execPath);return eval(${q(expression)})})()`,
        ),
    };
    await this.c.until(
      "!!document.querySelector('[aria-label=输入消息]') && !document.querySelector('[aria-label=输入消息]').disabled",
      50000,
    );
    this.base = await this.c.evaluate('window.desktopAPI.getBackendUrl()');
    await this.m.evaluate(
      "(()=>{const w=require('electron').BrowserWindow.getAllWindows().find(w=>w.webContents.getURL().includes('/app/'));if(!w)throw Error('Main window missing');w.setBounds({width:1440,height:940});return true})()",
    );
    await sleep(250);
    return this.base;
  }

  async json(route) {
    const response = await fetch(this.base + route, { signal: AbortSignal.timeout(5000) });
    assert.ok(response.ok, route + ': HTTP ' + response.status);
    return response.json();
  }

  async click(text, scope = 'document') {
    const result = await this.c.evaluate(
      `(()=>{const node=[...${scope}.querySelectorAll('button')].find(b=>b.textContent.trim()===${q(text)}&&!b.disabled);if(!node)return false;node.click();return true})()`,
    );
    assert.ok(result, 'Button missing: ' + text);
    await sleep(100);
  }

  async field(selector, value) {
    await this.c.evaluate(
      `(()=>{const n=document.querySelector(${q(selector)});if(!n)throw Error('Field missing');const p=n.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(p,'value').set.call(n,${q(value)});n.dispatchEvent(new Event('input',{bubbles:true}));return true})()`,
    );
    await sleep(40);
  }

  async send(text) {
    await this.field('[aria-label="输入消息"]', text);
    await this.c.evaluate("document.querySelector('[aria-label=发送]').click();true");
  }

  async openConversation(id) {
    await this.c.until(
      `!!document.querySelector('[data-conversation-id="${id}"] .chat-list-title')`,
      20000,
    );
    await this.c.evaluate(
      `document.querySelector('[data-conversation-id="${id}"] .chat-list-title').click();true`,
    );
    await sleep(250);
  }

  async screenshot(name) {
    const result = await this.c.call('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(path.join(this.home, name + '.png'), Buffer.from(result.data, 'base64'));
  }

  async close() {
    await this.c?.evaluate("void window.desktopAPI.windowControl('close');true").catch(() => {});
    this.c?.close();
    this.m?.close();
    this.c = null;
    this.m = null;
    const end = Date.now() + 25000;
    while (this.child?.exitCode === null && Date.now() < end) await sleep(100);
    this.check(this.child?.exitCode !== null, 'Application exits through its window close handler');
    const deadline = Date.now() + 10000;
    let healthy = true;
    while (healthy && Date.now() < deadline) {
      try {
        const response = await fetch(this.base + '/health', { signal: AbortSignal.timeout(500) });
        healthy = response.ok;
      } catch {
        healthy = false;
      }
      if (healthy) await sleep(150);
    }
    this.check(!healthy, 'Owned backend stops after normal application exit');
  }

  async cleanup() {
    try {
      // A failed assertion still needs the application's normal shutdown path.
      // Killing Electron first can leave descendants holding its inherited pipes.
      if (this.child?.exitCode === null && this.c) await this.close();
    } catch (error) {
      this.report.cleanupError = String(error.stack || error);
      process.exitCode = 1;
    } finally {
      this.c?.close();
      this.m?.close();
      if (this.child?.exitCode === null) this.child.kill(); // Only this test's child; parent watcher stops its backend.
      // Release only this driver's pipe handles, even when a descendant keeps a
      // pipe open after the owned child exits. Never hide failure with process.exit().
      this.child?.stdout?.unpipe(this.log);
      this.child?.stderr?.unpipe(this.log);
      for (const stream of [this.child?.stdin, this.child?.stdout, this.child?.stderr])
        stream?.destroy();
      this.log?.end();
      if (this.mock) {
        this.mock.closeAllConnections();
        await new Promise((resolve) => this.mock.close(resolve));
      }
      this.persist();
    }
  }
}

module.exports = { StudioSmokeDriver, MOCK_REPLY, q, sleep };

'use strict';
// Real Electron/getUserMedia/AudioWorklet/local SAPI/PCM playback. Input is an
// explicitly supplied public synthetic WAV, never a physical microphone.
const fs = require('node:fs');
const path = require('node:path');
const { StudioSmokeDriver, q, sleep } = require('./studio-smoke-driver.cjs');
let driver;
const scope = 'document.querySelector(\'[aria-label="实时语音会话"]\')';
async function run() {
  const wav = path.resolve(process.env.COMPANION_VOICE_FIXTURE || '');
  if (!process.env.COMPANION_VOICE_FIXTURE || !fs.statSync(wav).isFile())
    throw Error('Provide COMPANION_VOICE_FIXTURE: public synthetic PCM WAV');
  driver = new StudioSmokeDriver();
  driver.chromiumArgs = process.env.COMPANION_VOICE_BROWSER_FIXTURE === '1' ? [] : ['--use-fake-device-for-media-stream', '--use-file-for-fake-audio-capture=' + wav + '%noloop'];
  await driver.prepare();
  if (process.env.COMPANION_VOICE_TEST_PORT) driver.ports.backend = Number(process.env.COMPANION_VOICE_TEST_PORT);
  const responses = [];
  driver.mock.removeAllListeners('request');
  driver.mock.on('request', (req, res) => {
    let body = '';
    req.on('data', (part) => body += part);
    req.on('end', () => {
      if (req.url === '/v1/models') return res.end(q({ data: [{ id: 'local-smoke' }] }));
      if (req.url !== '/v1/chat/completions') { res.writeHead(404); return res.end(); }
      driver.report.localModelRequests++;
      if (!JSON.parse(body).stream) {
        res.setHeader('Content-Type', 'application/json');
        return res.end(q({ choices: [{ message: { role: 'assistant', content: 'Local connection fixture' }, finish_reason: 'stop' }] }));
      }
      const row = { started: Date.now(), completed: null };
      responses.push(row);
      res.writeHead(200, { 'Content-Type': 'text/event-stream' });
      const chunk = (text) => res.write('data: ' + q({ choices: [{ delta: { content: text }, finish_reason: null }] }) + '\n\n');
      chunk('这是一条本机合成测试回复，我们先听这一句。');
      const timer = setTimeout(() => {
        if (res.destroyed) return;
        chunk('下一段仍在继续生成，播放不需要等待全部结束。');
        row.completed = Date.now();
        res.end('data: [DONE]\n\n');
      }, 3500);
      res.once('close', () => clearTimeout(timer));
    });
  });
  await driver.launch();
  driver.report.electronResources = { observed_peak_working_set_kib: 0, samples: 0, coverage: 'Electron processes only; excludes Python and native helpers' };
  driver.voiceResourceTimer = setInterval(async () => {
    const rows = await driver.m?.evaluate(`require('electron').app.getAppMetrics().map(p=>({memory:p.memory.workingSetSize,cpu:p.cpu.percentCPUUsage}))`).catch(() => null);
    if (rows) {
      const r = driver.report.electronResources;
      r.samples++;
      r.observed_peak_working_set_kib = Math.max(r.observed_peak_working_set_kib, rows.reduce((n,p)=>n+p.memory,0));
      r.observed_max_cpu_percent = Math.max(r.observed_max_cpu_percent || 0, rows.reduce((n,p)=>n+p.cpu,0));
    }
  }, 500);
  driver.report.osMicrophoneAccess = await driver.m.evaluate(`require('electron').systemPreferences.getMediaAccessStatus('microphone')`);
  driver.report.audioBoundary = { input: 'chromium-synthetic-wav-device', asr: 'real-local-windows-sapi', tts: 'real-local-windows-sapi', llm: 'localhost-delayed-mock', physical_microphone: false, acoustic_loopback: false, real_external_requests: 0 };
  driver.report.audioBoundary.getUserMediaVerified = process.env.COMPANION_VOICE_BROWSER_FIXTURE !== '1';
  await driver.m.evaluate(`(()=>{const {dialog}=require('electron');const previous=dialog.showMessageBox.bind(dialog);global.__voiceConsentAnswer=1;dialog.showMessageBox=(a,b)=>{const o=b||a;return o.title==='启用本次语音会话'?Promise.resolve({response:global.__voiceConsentAnswer}):previous(a,b)};return true})()`);
  await driver.c.evaluate(`window.desktopAPI.applyApiPreset({name:'Voice local fixture',provider:'openai-compatible',base_url:'http://127.0.0.1:${driver.ports.model}/v1',api_key:'isolated-test-key',model:'local-smoke',capabilities:{text:true,tool_calling:false},timeout_seconds:20})`);
  const modelBefore = driver.report.localModelRequests;
  await driver.c.evaluate(`(()=>{const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);globalThis.__voiceQA={streams:[]};navigator.mediaDevices.getUserMedia=async o=>{const s=await original(o);globalThis.__voiceQA.streams.push(s);return s};return true})()`);
  if (process.env.COMPANION_VOICE_BROWSER_FIXTURE === '1') {
    // Separate synthetic capture layer, explicitly NOT evidence of microphone
    // permission/getUserMedia success. Still uses real AudioWorklet/PCM/ASR.
    driver.report.audioBoundary.input = 'synthetic-web-audio-media-stream';
    driver.report.audioBoundary.getUserMediaVerified = false;
    await driver.c.evaluate(`(()=>{const wav=${q(fs.readFileSync(wav).toString('base64'))};navigator.mediaDevices.getUserMedia=async()=>{const c=new AudioContext();const raw=Uint8Array.from(atob(wav),x=>x.charCodeAt(0));const buffer=await c.decodeAudioData(raw.buffer);const source=c.createBufferSource();source.buffer=buffer;const destination=c.createMediaStreamDestination();source.connect(destination);source.start();await c.resume();const s=destination.stream;for(const t of s.getTracks()){const stop=t.stop.bind(t);t.stop=()=>{stop();void c.close()}};__voiceQA.streams.push(s);return s};return true})()`);
  }
  driver.check(await driver.c.evaluate('__voiceQA.streams.length===0'), 'Microphone remains off at app startup');
  await driver.c.evaluate('document.querySelector(\'[aria-label="语音输入"]\').click();true');
  await driver.c.until('!!' + scope);
  await driver.c.until(`[...${scope}.querySelectorAll('button')].some(b=>b.textContent==='开启语音会话'&&!b.disabled)`, 20000);
  driver.check(await driver.c.evaluate('__voiceQA.streams.length===0'), 'Opening voice panel does not acquire capture device');
  await driver.click('开启语音会话', scope);
  await driver.c.until(`${scope}.innerText.includes('未启用麦克风')`);
  driver.check(await driver.c.evaluate('__voiceQA.streams.length===0'), 'Denied native session consent creates no capture tracks (dialog answer fixture)');
  await driver.m.evaluate('global.__voiceConsentAnswer=0;true');
  await driver.click('开启语音会话', scope);
  await driver.c.until(`[...${scope}.querySelectorAll('button')].some(b=>b.textContent==='开始录音')`);
  const owners = await driver.json('/api/conversations?character_id=assistant');
  const owner = owners[0].id;
  driver.report.voiceConversation = owner;
  driver.check(await driver.c.evaluate('__voiceQA.streams.length===0'), 'Session enable requires native scoped consent but still keeps microphone off');
  if (process.env.COMPANION_VOICE_BROWSER_FIXTURE !== '1') {
    const camera = await driver.c.evaluate(`navigator.mediaDevices.getUserMedia({video:true,audio:false}).then(s=>{s.getTracks().forEach(t=>t.stop());return 'unexpectedly-granted'}).catch(e=>e.name)`);
    driver.check(camera==='NotAllowedError', 'Audio-only grant does not grant the camera', {error:camera});
  }
  await driver.click('开始录音', scope);
  await driver.c.until(`${scope}.innerText.includes('麦克风正在采集')`);
  await sleep(3800);
  driver.check(await driver.c.evaluate('__voiceQA.streams[0].getAudioTracks()[0].readyState===\'live\''), 'Controlled WAV media stream runs through actual product AudioWorklet');
  await driver.click('结束并识别', scope);
  await driver.c.until(`!!${scope}.querySelector('[aria-label="识别终句"]')`, 30000);
  const transcript = await driver.c.evaluate(`${scope}.querySelector('[aria-label="识别终句"]').value`);
  driver.check(typeof transcript === 'string' && transcript.includes('你好'), 'Real local ASR returns a final transcript from controlled WAV', { transcript });
  driver.check(await driver.c.evaluate('__voiceQA.streams.every(s=>s.getTracks().every(t=>t.readyState===\'ended\'))'), 'Ending capture actually releases all owned tracks before ASR');
  driver.check(driver.report.localModelRequests === modelBefore, 'Final transcript waits for explicit send; no partial tool execution');
  await driver.screenshot('voice-final-transcript');
  await driver.click('发送识别终句', scope);
  await driver.c.until(`${scope}.innerText.includes('正在播放')`, 30000);
  await driver.c.until(`window.desktopAPI.nativeAudioStatus().then(s=>s.status==='playing'&&s.position>0)`, 10000);
  driver.report.firstPlayerObservation = await driver.c.evaluate('window.desktopAPI.nativeAudioStatus()');
  driver.check(driver.report.firstPlayerObservation.position > 0, 'Windows PCM hardware frame position advances; no acoustic audibility claim', driver.report.firstPlayerObservation);
  const playbackAt = Date.now();
  driver.check(responses.length === 1 && (!responses[0].completed || playbackAt < responses[0].completed), 'First actual PCM playback begins before delayed mock LLM completes', { playbackAt, model: responses[0] });
  const stopping = Date.now();
  await driver.c.evaluate(`[...${scope}.querySelectorAll('button')].find(b=>b.textContent==='打断回复 / 静音麦克风').click();true`);
  await driver.c.until(`window.desktopAPI.nativeAudioStatus().then(s=>s.status==='stopped')`, 8000);
  driver.report.manualStopSoftwareMs = Date.now() - stopping;
  driver.check(true, 'Manual interrupt stops actual PCM player; software latency only', { software_ms: driver.report.manualStopSoftwareMs });
  await sleep(3800);
  driver.check((await driver.c.evaluate('window.desktopAPI.nativeAudioStatus()')).status === 'stopped', 'Late old-generation model/audio does not restart playback');
  await driver.click('开始录音', scope);
  await driver.c.until(`${scope}.innerText.includes('麦克风正在采集')`);
  await sleep(3800);
  await driver.click('结束并识别', scope);
  await driver.c.until(`!!${scope}.querySelector('[aria-label="识别终句"]')`, 30000);
  await driver.click('发送识别终句', scope);
  await driver.c.until(`${scope}.innerText.includes('正在播放')`, 30000);
  await driver.c.until(`${scope}.innerText.includes('就绪 · 麦克风关闭')`, 40000);
  driver.check(true, 'Second turn records, recognizes, incrementally plays and completes after interruption');
  await driver.click('语音追踪与只读回放', scope);
  await driver.c.until(`${scope}.querySelectorAll('.trace-row').length>0`, 15000);
  const traces = await driver.c.evaluate(`window.desktopAPI.readVoiceTraces('list',{conversation_id:${q(owner)}})`);
  driver.check(traces.items.length === 2 && traces.items.some(t=>t.status==='completed') && traces.items.some(t=>t.status==='cancelled'), 'Original Trace persists completed and cancelled voice turns under correct conversation');
  const completed = traces.items.find(t=>t.status==='completed');
  const summary = await driver.c.evaluate(`window.desktopAPI.readVoiceTraces('summary',{conversation_id:${q(owner)},trace_id:${q(completed.trace_id)}})`);
  driver.check(summary.model_requests === 1, 'Voice Trace includes the actual local mock LLM span, not an invented zero', summary);
  await driver.screenshot('voice-trace');
  if (process.env.COMPANION_VOICE_BROWSER_FIXTURE !== '1') {
    const device = await driver.c.evaluate(`(()=>{const s=${scope}.querySelector('[aria-label="语音输入设备"]');return [...s.options].find(o=>o.value&&o.value!=='default')?.value})()`);
    driver.check(!!device, 'Controlled Chromium device list exposes an alternate input');
    await driver.c.evaluate(`(()=>{const s=${scope}.querySelector('[aria-label="语音输入设备"]');s.value=${q(device)};s.dispatchEvent(new Event('change',{bubbles:true}));return true})()`);
    await driver.click('开始录音', scope);
    await driver.c.until(`${scope}.innerText.includes('麦克风正在采集')`);
    driver.check(await driver.c.evaluate(`__voiceQA.streams.at(-1).getAudioTracks()[0].getSettings().deviceId===${q(device)}`), 'New capture uses the selected controlled input device');
    await driver.c.evaluate(`(()=>{const t=__voiceQA.streams.at(-1).getAudioTracks()[0];t.stop();t.dispatchEvent(new Event('ended'));return true})()`);
    await driver.c.until(`${scope}.innerText.includes('麦克风已断开')`);
    driver.check(await driver.c.evaluate('__voiceQA.streams.every(s=>s.getTracks().every(t=>t.readyState===\'ended\'))'), 'Synthetic device-disconnect notification releases all actual capture tracks');
  }
  await driver.click('停止语音会话', scope);
  driver.check(await driver.c.evaluate('__voiceQA.streams.every(s=>s.getTracks().every(t=>t.readyState===\'ended\'))'), 'Stopping session leaves no capture tracks');
  await driver.close();
  driver.report.pass = true;
  driver.persist();
}
run().catch((error) => {
  if (driver) { driver.report.error = String(error.stack || error); driver.persist(); }
  process.exitCode = 1;
}).finally(async () => {
  if (driver?.voiceResourceTimer) clearInterval(driver.voiceResourceTimer);
  if (driver?.c && driver.report.error) {
    driver.report.voiceUI = await driver.c.evaluate(`${scope}?.innerText || ''`).catch(() => 'unavailable');
    driver.report.captureState = await driver.c.evaluate(`__voiceQA.streams.map(s=>s.getTracks().map(t=>({state:t.readyState,settings:t.getSettings()})))`).catch(() => []);
    await driver.screenshot('voice-failure').catch(() => {});
    driver.persist();
  }
  if (driver) await driver.cleanup();
});

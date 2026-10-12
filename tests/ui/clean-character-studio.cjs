// Actual Electron renderer and IPC, isolated profile and local mock model only.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict'), http = require('node:http'), zlib = require('node:zlib')
const { spawn } = require('node:child_process'), { connect, connectMain, sleep } = require('./v81-harness.cjs')
const project = path.resolve(__dirname, '../..'), root = path.dirname(project), home = process.env.CLEAN_TEST_HOME || path.join(root, 'Smoke', 'clean-character-ui-' + Date.now())
const ports = { renderer: 9976, main: 9977, backend: 8778, model: 8780 }, q = JSON.stringify
const report = { home, realModelRequests: 0, realVoiceRequests: 0, assertions: [], requests: [] }
let child, c, m, mock
fs.mkdirSync(path.join(home, 'UserData'), { recursive: true }); fs.mkdirSync(path.join(home, 'Cache'), { recursive: true })
const persist = () => fs.writeFileSync(path.join(home, 'report.json'), q(report))
const check = (value, label) => { assert.ok(value, label); report.assertions.push(label); persist() }
async function click(text, scope = 'document') {
  const found = await c.evaluate(`(()=>{const node=Array.from(${scope}.querySelectorAll('button')).find(b=>b.textContent.trim()===${q(text)});if(!node)return false;node.click();return true})()`)
  assert.ok(found, 'Button missing: ' + text); await sleep(100)
}
async function field(selector, value) {
  await c.evaluate(`(()=>{const n=document.querySelector(${q(selector)});if(!n)throw Error('Field missing');const p=n.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(p,'value').set.call(n,${q(value)});n.dispatchEvent(new Event('input',{bubbles:true}));})()`)
  await sleep(40)
}
async function launch() {
  const exe = process.env.COMPANION_TEST_EXE || path.join(project, 'desktop/node_modules/electron/dist/electron.exe')
  const args = process.env.COMPANION_TEST_EXE ? [] : [path.join(project, 'desktop')]
  child = spawn(exe, [...args, '--compat', '--remote-debugging-port=' + ports.renderer, '--inspect=' + ports.main], {
    windowsHide: true, env: { ...process.env, COMPANION_HOME: home, COMPANION_CLEAN_DISTRIBUTION: '1', BACKEND_PORT: String(ports.backend),
      COMPANION_FRONTEND_DIST: process.env.COMPANION_TEST_EXE ? '' : process.env.CLEAN_TEST_FRONTEND || path.join(project, 'desktop/frontend/dist'),
      OPENAI_API_KEY: '', OPENAI_BASE_URL: `http://127.0.0.1:${ports.model}/v1`, VOICE_PREFETCH_ENABLED: 'false', TEMP: path.join(home, 'Cache'), TMP: path.join(home, 'Cache') },
  })
  const log = fs.createWriteStream(path.join(home, 'launch.log'), { flags: 'a' }); child.stdout.pipe(log); child.stderr.pipe(log)
  c = await connect(ports.renderer); m = await connectMain(ports.main, 65000)
  await c.until("!!document.querySelector('[aria-label=输入消息]') && !document.querySelector('[aria-label=输入消息]').disabled && !!document.querySelector('.welcome')", 50000)
  return await c.evaluate('window.desktopAPI.getBackendUrl()')
}
async function close() {
  if (c) await c.evaluate("document.querySelector('[aria-label=关闭窗口]').click();true").catch(() => {})
  c?.close(); m?.close(); c = null; m = null
  const end = Date.now() + 25000; while (child?.exitCode === null && Date.now() < end) await sleep(100)
  assert.notEqual(child?.exitCode, null, 'Normal app shutdown')
}
function cardPng(card) {
  let crcTable = Array.from({ length: 256 }, (_, n) => { for (let k = 0; k < 8; k++) n = n & 1 ? 0xedb88320 ^ (n >>> 1) : n >>> 1; return n >>> 0 })
  const chunk = (kind, data) => { const content = Buffer.concat([Buffer.from(kind), data]), len = Buffer.alloc(4), crc = Buffer.alloc(4); let sum = 0xffffffff
    for (const byte of content) sum = crcTable[(sum ^ byte) & 0xff] ^ (sum >>> 8); len.writeUInt32BE(data.length); crc.writeUInt32BE((sum ^ 0xffffffff) >>> 0); return Buffer.concat([len, content, crc]) }
  const head = Buffer.alloc(13); head.writeUInt32BE(1, 0); head.writeUInt32BE(1, 4); head[8] = 8; head[9] = 6
  return Buffer.concat([Buffer.from('89504e470d0a1a0a', 'hex'), chunk('IHDR', head), chunk('tEXt', Buffer.from('chara\0' + Buffer.from(q(card)).toString('base64'))), chunk('IDAT', zlib.deflateSync(Buffer.from([0, 30, 150, 170, 255]))), chunk('IEND', Buffer.alloc(0))])
}
async function fileInput(file) {
  const doc = await c.call('DOM.getDocument'), selected = await c.call('DOM.querySelector', { nodeId: doc.root.nodeId, selector: 'input[aria-label="选择角色卡文件"]' })
  await c.call('DOM.setFileInputFiles', { nodeId: selected.nodeId, files: [file] })
}
async function screenshot(name) { const img = await c.call('Page.captureScreenshot', { format: 'png' }); fs.writeFileSync(path.join(home, name + '.png'), Buffer.from(img.data, 'base64')) }
async function chat(text) {
  await click('返回'); await field('[aria-label="输入消息"]', text); await c.evaluate("document.querySelector('[aria-label=发送]').click();true")
  await c.until("document.querySelectorAll('.message-row.assistant .bubble').length>0 && document.querySelectorAll('.message-row.assistant .bubble')[document.querySelectorAll('.message-row.assistant .bubble').length-1].innerText.includes('本地测试回复')", 25000)
  await sleep(250)
}
async function run() {
  mock = http.createServer((req, res) => { let text = ''; req.on('data', data => text += data); req.on('end', () => {
    if (req.url?.endsWith('/models')) { res.setHeader('Content-Type', 'application/json'); res.end(q({ data: [{ id: 'local-test' }] })); return }
    const body = JSON.parse(text || '{}'); report.requests.push(body); const answer = '这是本地测试回复。';
    if (body.stream) { res.writeHead(200, { 'Content-Type': 'text/event-stream' }); res.write('data: ' + q({ choices: [{ delta: { content: answer }, finish_reason: null }] }) + '\n\n'); res.end('data: [DONE]\n\n') }
    else { res.setHeader('Content-Type', 'application/json'); res.end(q({ choices: [{ message: { role: 'assistant', content: answer }, finish_reason: 'stop' }] })) }
  }) }); await new Promise(resolve => mock.listen(ports.model, '127.0.0.1', resolve))
  const base = await launch()
  const initial = await c.evaluate('window.desktopAPI.characterLibrary()'); check(initial.active_id === 'assistant' && initial.characters.length === 1, 'Fresh profile has only neutral assistant')
  check(await c.evaluate("document.querySelectorAll('.message-row').length===0 && !/爱弥斯|Aemeath|aemeath|鸣潮/.test(document.body.innerText)"), 'Fresh UI has no old branding or messages')
  check(await c.evaluate("(()=>{const a=document.querySelector('.sidebar-avatar').src,b=document.querySelector('.welcome img').src;return a===b && (a.includes('companion-logo') || a.startsWith('data:image/svg+xml'))})()"), 'Sidebar and welcome use independent application logo')
  check(await c.evaluate("localStorage.getItem('voice-enabled')==='false' && localStorage.getItem('voice-prefetch')==='false'"), 'Voice and prefetch start disabled')
  check(!(await c.evaluate('window.desktopAPI.listApiPresets()')).presets.some(p => p.has_key), 'Fresh profile has no imported API key')
  await screenshot('neutral-home')
  await click('工作模式'); await c.until("!!document.querySelector('.workbench')")
  check(await c.evaluate("!document.body.innerText.includes('爱弥斯') && !!document.querySelector('.workspace-grid')"), 'Neutral workbench remains available')
  await click('聊天模式')
  await c.evaluate(`window.desktopAPI.applyApiPreset({name:'Isolated local test',provider:'openai-compatible',base_url:'http://127.0.0.1:${ports.model}/v1',api_key:'local-test-only',model:'local-test',temperature:null,max_tokens:null,timeout_seconds:15,capabilities:{text:true,vision:false,tool_calling:false,web_search:false,structured_output:false}})`)
  await c.evaluate("document.querySelector('[aria-label=打开角色管理]').click();true"); await c.until("!!document.querySelector('.character-library-list button')")
  await click('创建自定义角色'); await field('.character-editor input', '测试搭档'); await field('.character-editor textarea', '这是仅在隔离测试中创建的植物学家。')
  await click('保存并使用'); await c.until("document.querySelector('.character-page-header h2')?.innerText.includes('测试搭档')")
  let state = await c.evaluate('window.desktopAPI.characterLibrary()'); const custom = state.characters.find(row => row.name === '测试搭档')
  check(custom?.id.startsWith('custom_') && state.active_id === custom.id, 'Custom character can be created without a card and activated')
  await chat('自定义角色隔离测试'); const customRows = await fetch(base + '/api/conversations?character_id=' + custom.id).then(r => r.json())
  check(customRows.length === 1, 'Custom character stores its own conversation')
  check(q(report.requests.at(-1)).includes('植物学家'), 'Custom identity reaches local model context')
  await c.evaluate("document.querySelector('[aria-label=打开角色管理]').click();true"); await c.until("!!document.querySelector('.character-library-list button')")
  await click('通用助手无角色卡 · 通用助手'); await click('使用此角色'); await c.until("document.querySelector('.character-page-header h2')?.innerText.includes('通用助手')")
  await click('返回'); check(await c.evaluate("document.querySelectorAll('.message-row').length===0"), 'Switching character does not display previous character messages')
  await field('[aria-label="输入消息"]', '通用助手隔离测试'); await c.evaluate("document.querySelector('[aria-label=发送]').click();true")
  await c.until("document.querySelector('.message-row.assistant .bubble')?.innerText.includes('本地测试回复')", 25000)
  check(!q(report.requests.at(-1)).includes('植物学家') && !q(report.requests.at(-1)).includes('自定义角色隔离测试'), 'Previous identity and conversation do not leak into assistant context')
  await c.evaluate("document.querySelector('[aria-label=打开角色管理]').click();true"); await c.until("!!document.querySelector('.character-library-list button')")
  await click('测试搭档自定义角色'); await click('编辑角色')
  await field('.character-editor input', '编辑后的搭档'); await field('.character-editor label:nth-of-type(5) textarea', '简短\n自然')
  await click('保存并使用'); await c.until("document.querySelector('.character-page-header h2')?.innerText.includes('编辑后的搭档')")
  const edited = await c.evaluate(`window.desktopAPI.characterDetail(${q(custom.id)})`)
  check(edited.name === '编辑后的搭档' && edited.profile.speech_style.join('|') === '简短|自然', 'Saved custom role can be edited with multiline style rules')
  const card = { spec: 'chara_card_v2', spec_version: '2.0', data: { name: '导入测试角色', description: '角色卡导入的研究员。', personality: '安静', first_mes: '你好，开始测试吧。', scenario: '', mes_example: '', system_prompt: '', post_history_instructions: '', alternate_greetings: [], tags: [] } }
  const jsonFile = path.join(home, 'character-card.json'); fs.writeFileSync(jsonFile, q(card)); await fileInput(jsonFile)
  await c.until("document.querySelector('.character-page-header h2')?.innerText.includes('导入测试角色')", 15000)
  state = await c.evaluate('window.desktopAPI.characterLibrary()'); check(state.characters.some(row => row.name === '导入测试角色'), 'JSON character card imported through real file input and IPC')
  const badFile = path.join(home, 'invalid-card.json'); fs.writeFileSync(badFile, '{invalid'); await fileInput(badFile)
  await c.until("document.querySelector('.character-library')?.innerText.includes('操作失败')")
  check((await c.evaluate('window.desktopAPI.characterLibrary()')).active_id === state.active_id, 'Malformed card shows error without replacing active character')
  card.data.name = 'PNG测试角色'; const pngFile = path.join(home, 'character-card.png'); fs.writeFileSync(pngFile, cardPng(card)); await fileInput(pngFile)
  await c.until("document.querySelector('.character-page-header h2')?.innerText.includes('PNG测试角色')", 15000)
  state = await c.evaluate('window.desktopAPI.characterLibrary()'); const png = state.characters.find(row => row.name === 'PNG测试角色')
  check(!!png?.avatar_url, 'PNG metadata card loads its own avatar')
  await screenshot('character-library')
  await close(); await launch(); await c.until("document.querySelector('.sidebar-identity')?.innerText.includes('PNG测试角色')")
  check((await c.evaluate('window.desktopAPI.characterLibrary()')).active_id === png.id, 'Active card survives actual app restart')
  const history = await fetch(base + '/api/conversations?character_id=' + custom.id).then(r => r.json()); check(history.length === 1, 'Other character history survives restart')
  report.success = true; await close(); report.normalShutdown = true
}
run().catch(e => { report.error = String(e.stack || e); process.exitCode = 1 }).finally(async () => {
  c?.close(); m?.close(); if (child?.exitCode === null) child.kill(); if (mock) await new Promise(resolve => mock.close(resolve)); persist(); console.log(q({ success: report.success || false, home, assertions: report.assertions, error: report.error }))
})

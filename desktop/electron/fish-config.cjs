'use strict'

const fs = require('node:fs')
const path = require('node:path')
const { safeStorage } = require('electron')
const paths = require('./paths.cjs')
const backend = require('./backend.cjs')
const brand = require('./brand.cjs')

const file = path.join(paths.userData, 'fish-settings.json')
const keyFile = path.join(paths.userData, 'fish-key.json')

function readJson(filePath, fallback) {
  try { return JSON.parse(fs.readFileSync(filePath, 'utf8')) } catch { return fallback }
}
function atomicJson(filePath, value) {
  const temporary = `${filePath}.${process.pid}.tmp`
  fs.writeFileSync(temporary, JSON.stringify(value, null, 2), 'utf8')
  fs.renameSync(temporary, filePath)
}
/** 返回语音配置元数据；has_key 表示已保存密钥，不代表连接测试已通过。 */
function current() {
  const data = readJson(file, {})
  const encrypted = readJson(keyFile, {}).encrypted
  return { engine: data.engine === 'fish' ? 'fish' : 'index',
    reference_id: data.reference_id || '', model: data.model || 's2.1-pro-free',
    speed: data.speed ?? 1, fallback_enabled: data.fallback_enabled ?? !brand.clean,
    transport_mode: ['auto', 'stream', 'standard'].includes(data.transport_mode) ? data.transport_mode : 'auto',
    latency: ['balanced', 'normal'].includes(data.latency) ? data.latency : 'normal',
    has_key: Boolean(encrypted) }
}
/** 只在主进程解密当前 Windows 用户的语音凭据，不提供给渲染层。 */
function decryptKey() {
  const encrypted = readJson(keyFile, {}).encrypted
  if (!encrypted) return ''
  if (!safeStorage.isEncryptionAvailable()) throw new Error('Windows 安全存储不可用')
  return safeStorage.decryptString(Buffer.from(encrypted, 'base64'))
}
async function callBackend(route, body) {
  const response = await fetch(`${backend.address.url}${route}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Companion-Control-Token': backend.controlToken },
    body: JSON.stringify(body), signal: AbortSignal.timeout(10000),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`)
  return data
}
/** persist=false 用于启动恢复，不重写随机化加密密钥或用户保存的设置。 */
async function apply(draft, { persist = true } = {}) {
  const old = current()
  const key = draft.api_key ? String(draft.api_key).trim() : draft.clear_key ? '' : decryptKey()
  const config = { api_key: key, reference_id: String(draft.reference_id ?? old.reference_id),
    model: String(draft.model ?? old.model), speed: Number(draft.speed ?? old.speed),
    fallback_enabled: draft.fallback_enabled ?? old.fallback_enabled,
    transport_mode: draft.transport_mode ?? old.transport_mode,
    latency: draft.latency ?? old.latency }
  const engine = draft.engine === 'fish' ? 'fish' : 'index'
  if (key && !safeStorage.isEncryptionAvailable()) throw new Error('Windows 安全存储不可用，密钥未保存')
  await callBackend('/api/voice/fish/apply', config)
  await callBackend('/api/voice/engine', { engine })
  if (persist) {
    atomicJson(file, { engine, reference_id: config.reference_id, model: config.model,
      speed: config.speed, fallback_enabled: config.fallback_enabled,
      transport_mode: config.transport_mode, latency: config.latency })
    if (key) atomicJson(keyFile, { encrypted: safeStorage.encryptString(key).toString('base64') })
    else if (fs.existsSync(keyFile)) fs.unlinkSync(keyFile)
  }
  return current()
}
async function restore() {
  const saved = current()
  // Startup only restores the backend state; DPAPI encryption is randomized.
  // Rewriting saved credentials here obscures upgrade audits and is unnecessary.
  return apply({ ...saved, api_key: decryptKey() }, { persist: false })
}

async function testVoice() {
  const response = await fetch(`${backend.address.url}/api/voice/fish/test`, {
    method: 'POST', headers: { 'X-Companion-Control-Token': backend.controlToken },
    signal: AbortSignal.timeout(120000),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || `Fish HTTP ${response.status}`)
  return { ...data, audio_url: `${backend.address.url}${data.audio_url}` }
}
async function testStream() {
  const response = await fetch(`${backend.address.url}/api/voice/fish/stream-test`, {
    method: 'POST', headers: { 'X-Companion-Control-Token': backend.controlToken },
    signal: AbortSignal.timeout(120000),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || `Fish HTTP ${response.status}`)
  return { ...data, audio_url: `${backend.address.url}${data.audio_url}` }
}
async function testCharacter() {
  const response = await fetch(`${backend.address.url}/api/voice/fish/character-test`, {
    method: 'POST', headers: { 'X-Companion-Control-Token': backend.controlToken },
    signal: AbortSignal.timeout(600000),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || `Fish HTTP ${response.status}`)
  return { samples: data.samples.map((item) => ({ ...item, audio_url: `${backend.address.url}${item.audio_url}` })) }
}

async function compare() {
  const response = await fetch(`${backend.address.url}/api/voice/fish/compare`, {
    method: 'POST', headers: { 'X-Companion-Control-Token': backend.controlToken },
    signal: AbortSignal.timeout(600000),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || `Fish HTTP ${response.status}`)
  return { ...data, samples: data.samples.map((item) => ({ ...item, audio_url: `${backend.address.url}${item.audio_url}` })) }
}

async function testScenario(index) {
  if (!Number.isInteger(index) || index < 1 || index > 30) throw new Error('未知语音场景')
  const response = await fetch(`${backend.address.url}/api/voice/fish/v72-scenario/${index}`, {
    method: 'POST', headers: { 'X-Companion-Control-Token': backend.controlToken },
    signal: AbortSignal.timeout(120000),
  })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(data.detail || `Fish HTTP ${response.status}`)
  return { ...data, audio_url: `${backend.address.url}${data.audio_url}` }
}

module.exports = { current, apply, restore, testVoice, testStream, testCharacter, compare, testScenario }

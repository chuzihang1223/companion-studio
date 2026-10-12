import { useEffect, useState } from 'react'
import { voiceHealth } from './api'

const defaults: FishSettings = { engine: 'index', reference_id: '', model: 's2.1-pro-free', speed: 1,
  fallback_enabled: false, transport_mode: 'auto', latency: 'normal', has_key: false }

export default function FishSettingsPanel({ onPlay }: { onPlay: (url: string) => Promise<void> }) {
  const [value, setValue] = useState<FishSettings>(defaults)
  const [key, setKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [status, setStatus] = useState('')
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [authorized, setAuthorized] = useState(false)
  const [savedIdentity, setSavedIdentity] = useState('')
  const [health, setHealth] = useState('正在读取语音状态…')
  const [networkDebug, setNetworkDebug] = useState<{ status: number; model: string; latencyMs: number } | null>(null)
  const [compareSamples, setCompareSamples] = useState<Array<{ name: string; temperature: number; latency: string; ttfa_ms: number; generation_ms: number; audio_url: string }>>([])
  useEffect(() => { void window.desktopAPI?.getFishSettings().then((result) => {
    setValue(result); setSavedIdentity(`${result.reference_id}|${result.model}`)
  }).catch((cause) => setStatus(String(cause))); void refreshHealth() }, [])
  async function refreshHealth() {
    try {
      const state = await voiceHealth()
      setHealth(`主引擎：${state.primary === 'fish' ? 'Fish Audio' : 'IndexTTS'} · Fish：${state.fish?.configured ? '已配置' : '未配置'} / ${state.fish?.authorized ? '已授权' : '未测试'} / ${state.fish?.reference_ready ? '声音可用' : '声音未验证'} · IndexTTS：${state.indextts?.ready ? '就绪' : '未就绪'}`)
    } catch { setHealth('暂时无法读取语音状态') }
  }
  async function save() {
    if (!window.desktopAPI || saving) return
    setSaving(true); setStatus('正在保存…')
    try {
      const identityChanged = Boolean(key.trim()) || `${value.reference_id}|${value.model}` !== savedIdentity
      const draft = { ...value, engine: identityChanged ? 'index' as const : value.engine, api_key: key || undefined }
      const result = await window.desktopAPI.applyFishSettings(draft)
      setValue(result); setSavedIdentity(`${result.reference_id}|${result.model}`)
      setKey(''); setAuthorized(false)
      setStatus(identityChanged ? '设置已保存；请先测试连接和角色声音，再启用 Fish 主语音' : '设置已保存')
      void refreshHealth()
    } catch (cause) { setStatus(`保存失败：${cause instanceof Error ? cause.message : String(cause)}`) }
    finally { setSaving(false) }
  }
  async function test() {
    if (!window.desktopAPI || testing) return
    setTesting(true); setStatus('正在验证 Fish 连接并生成测试语音…')
    try {
      const result = await window.desktopAPI.testFishVoice()
      setNetworkDebug({ status: result.http_status, model: result.model, latencyMs: result.generation_ms })
      setStatus(`Fish 连接成功 · ${result.model} · ${result.bytes} 字节 · 首段 ${result.ttfa_ms} ms。正在播放…`)
      await onPlay(result.audio_url)
      setAuthorized(true)
      void refreshHealth()
      setStatus(`Fish 连接成功 · 测试语音已开始播放 · 总耗时 ${result.generation_ms} ms`)
    } catch (cause) { setStatus(`测试失败：${cause instanceof Error ? cause.message : String(cause)}`) }
    finally { setTesting(false) }
  }
  async function testStream() {
    if (!window.desktopAPI || testing) return
    setTesting(true); setStatus('正在测试 Fish 流式传输…')
    try {
      const result = await window.desktopAPI.testFishStream()
      setStatus(`生成成功 · 实际传输：${result.transport === 'stream' ? 'WebSocket 流式' : 'HTTP 标准回退'} · ${result.generation_ms} ms。正在播放…`)
      await onPlay(result.audio_url)
      setAuthorized(true)
      void refreshHealth()
      setStatus(`流式测试已播放 · 实际传输：${result.transport === 'stream' ? 'WebSocket 流式' : 'HTTP 标准回退'} · ${result.generation_ms} ms`)
    } catch (cause) { setStatus(`流式测试失败：${cause instanceof Error ? cause.message : String(cause)}`) }
    finally { setTesting(false) }
  }
  async function compare() {
    if (!window.desktopAPI || testing) return
    setTesting(true); setStatus('正在生成 4 条免费模型语音对比…')
    try {
      const result = await window.desktopAPI.compareFishVoice()
      setCompareSamples(result.samples)
      setStatus(result.complete ? '语音对比已生成，请试听并比较自然度和音色。'
        : `${result.error || '语音对比中断'}；已保存 ${result.samples.length}/4 条，可继续生成。`)
    } catch (cause) { setStatus(`对比失败：${cause instanceof Error ? cause.message : String(cause)}`) }
    finally { setTesting(false) }
  }
  const canSelectFish = authorized || (value.engine === 'fish' && !key.trim() && `${value.reference_id}|${value.model}` === savedIdentity)
  return <div className="fish-settings">
    <label>语音引擎<select value={value.engine} onChange={(event) => setValue({ ...value, engine: event.target.value as FishSettings['engine'] })}>
      <option value="fish" disabled={!canSelectFish}>Fish Audio 在线</option><option value="index">IndexTTS 本地</option>
    </select></label>
    <label>Fish API 密钥<input type={showKey ? 'text' : 'password'} autoComplete="new-password" value={key} onChange={(event) => setKey(event.target.value)} placeholder={value.has_key ? '••••••••••••••（已安全保存）' : '尚未配置'} />
      <button className="cache-clear" type="button" onClick={() => setShowKey(!showKey)}>{showKey ? '隐藏' : '显示'}</button></label>
    <label>角色声音 ID<input value={value.reference_id} maxLength={100} onChange={(event) => setValue({ ...value, reference_id: event.target.value.trim() })} placeholder="Fish Audio 中已创建的声音模型 ID" /></label>
    <label>Fish 模型<select value={value.model} onChange={(event) => setValue({ ...value, model: event.target.value })}>
      <option value="s2.1-pro-free">s2.1-pro-free（开发测试）</option><option value="s2.1-pro">s2.1-pro（付费，需主动选择）</option>
    </select></label>
    <label>传输模式<select value={value.transport_mode} onChange={(event) => setValue({ ...value, transport_mode: event.target.value as FishSettings['transport_mode'] })}>
      <option value="auto">自动（流式故障时转标准）</option><option value="stream">流式（故障时转标准）</option><option value="standard">标准 HTTP</option>
    </select></label>
    <label>延迟模式<select value={value.latency} onChange={(event) => setValue({ ...value, latency: event.target.value as FishSettings['latency'] })}>
      <option value="balanced">均衡</option><option value="normal">自然</option>
    </select></label>
    <p className="setting-note">输出格式：聊天使用 WAV PCM，连接测试使用 MP3；自动朗读开关在本页下方。</p>
    <label>Fish 不可用时使用 IndexTTS<input type="checkbox" checked={value.fallback_enabled} onChange={(event) => setValue({ ...value, fallback_enabled: event.target.checked })} /></label>
    <p className="setting-note">{value.engine === 'fish' && (!value.has_key || !value.reference_id)
      ? '尚未配置声音服务。可以继续文字聊天；在线语音或本地模型需自行配置。'
      : value.engine === 'index' ? '已选择本地语音；需自行安装并配置模型。' : 'Fish 已配置；在线语音可能产生费用。'}</p>
    <p className="setting-note">{health}</p>
    <div className="fish-actions"><button className="cache-clear" type="button" disabled={saving} onClick={() => void save()}>保存并应用</button>
      <button className="cache-clear" type="button" disabled={testing || !value.has_key || !value.reference_id || value.model !== 's2.1-pro-free'} onClick={() => void test()}>测试 Fish Audio</button>
      <button className="cache-clear" type="button" disabled={testing || !value.has_key || !value.reference_id || value.model !== 's2.1-pro-free' || value.transport_mode === 'standard'} onClick={() => void testStream()}>测试流式传输（免费模型 1 次）</button></div>
    <button className="cache-clear" type="button" disabled={testing || !authorized || value.model !== 's2.1-pro-free' || compareSamples.length === 4} onClick={() => void compare()}>
      {compareSamples.length ? `继续生成语音对比（${compareSamples.length}/4）` : '生成语音对比（4 次免费模型请求）'}</button>
    {compareSamples.length > 0 && <div className="fish-samples">{compareSamples.map((sample) => <button className="cache-clear" type="button" key={sample.name}
      onClick={() => void onPlay(sample.audio_url).catch((cause) => setStatus(`播放失败：${String(cause)}`))}>
      播放 {sample.name} · 温度 {sample.temperature} · {sample.latency} · 首段 {sample.ttfa_ms} ms</button>)}</div>}
    {value.model !== 's2.1-pro-free' && <p className="setting-note">付费模型不参与开发测试。请切回免费模型。</p>}
    {networkDebug && <details><summary>开发者 · Fish 网络诊断</summary><pre className="diagnostic-details">{`POST https://api.fish.audio/v1/tts\nHTTP ${networkDebug.status}\nmodel: ${networkDebug.model}\nAuthorization: Bearer ********\n耗时: ${networkDebug.latencyMs} ms`}</pre></details>}
    <p className="setting-note" role="status">{status}</p>
  </div>
}

import { useState } from 'react';
export default function RuntimeDiagnostics() {
  const [name, setName] = useState('QQ音乐'),
    [site, setSite] = useState('github'),
    [query, setQuery] = useState('electron agent'),
    [busy, setBusy] = useState(false),
    [result, setResult] = useState<unknown>(null);
  async function run(web: boolean) {
    setBusy(true);
    try {
      setResult(
        await (web
          ? window.desktopAPI!.diagnoseBrowser({ site, query })
          : window.desktopAPI!.diagnoseApplication(name)),
      );
    } catch (e) {
      setResult({ error: String(e) });
    } finally {
      setBusy(false);
    }
  }
  return (
    <section>
      <h3>应用动态发现</h3>
      <p className="setting-note">
        查看索引、开始菜单、快捷方式、注册表、AppsFolder 和最终候选；不会启动应用。
      </p>
      <input
        aria-label="应用发现诊断名称"
        value={name}
        onChange={(e) => setName(e.target.value)}
        maxLength={150}
      />
      <button disabled={busy || !name.trim()} onClick={() => void run(false)}>
        诊断发现来源
      </button>
      <h3>浏览器诊断</h3>
      <p className="setting-note">实际加载搜索结果；不调用聊天或语音模型。</p>
      <select value={site} onChange={(e) => setSite(e.target.value)}>
        <option value="github">GitHub</option>
        <option value="bilibili">Bilibili</option>
        <option value="baidu">百度</option>
      </select>
      <input
        aria-label="网页诊断搜索词"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        maxLength={160}
      />
      <button disabled={busy || !query.trim()} onClick={() => void run(true)}>
        测试网页搜索
      </button>
      {busy && <p role="status">正在检查…</p>}
      {result !== null && (
        <pre className="diagnostic-details">{JSON.stringify(result, null, 2)}</pre>
      )}
    </section>
  );
}

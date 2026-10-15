import { useState } from 'react';
export default function AutomationDiagnostics() {
  const [query, setQuery] = useState('远航星的告别'),
    [result, setResult] = useState<Record<string, unknown> | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  async function run(search: boolean) {
    setBusy(true);
    setError('');
    try {
      setResult(await window.desktopAPI!.diagnoseMusic({ query, launch: search, search }));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="automation-diagnostics">
      <h3>开发者 · 网易云自动化测试</h3>
      <p className="setting-note">
        检查客户端、窗口控件和媒体状态。搜索测试会切换到网易云并输入下方歌名，不调用模型。
      </p>
      <input
        aria-label="网易云诊断歌曲"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        maxLength={160}
      />
      <div>
        <button type="button" disabled={busy} onClick={() => void run(false)}>
          检测客户端与媒体
        </button>
        <button type="button" disabled={busy || !query.trim()} onClick={() => void run(true)}>
          {busy ? '正在检查…' : '启动并测试搜索'}
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {result && (
        <details open>
          <summary>诊断结果</summary>
          <pre className="diagnostic-details">{JSON.stringify(result, null, 2)}</pre>
        </details>
      )}
    </section>
  );
}

import { useEffect, useState } from 'react';
import ModelRolesPanel from './ModelRolesPanel';
import ModelRoutingPanel from './ModelRoutingPanel';

const blank = (): ApiDraft => ({
  name: '自定义 API',
  provider: 'openai-compatible',
  base_url: 'https://api.deepseek.com/v1',
  api_key: '',
  model: '',
  temperature: null,
  max_tokens: null,
  timeout_seconds: 60,
  capabilities: {
    text: true,
    vision: false,
    tool_calling: null,
    web_search: false,
    structured_output: false,
  },
});
const message = (error: unknown) => (error instanceof Error ? error.message : String(error));
function friendlyError(detail: string): string {
  if (/401|unauthorized|invalid.api.key|authentication/i.test(detail)) return 'API 密钥无效。';
  if (/403|forbidden|permission/i.test(detail)) return '当前账户无权访问该模型。';
  if (/404|not.found/i.test(detail)) return 'API 地址或模型不存在。';
  if (/429|rate.limit|quota|balance/i.test(detail)) return '请求过于频繁或额度不足。';
  if (/timeout|timed.out|abort/i.test(detail)) return '请求超时。';
  if (/connection|fetch.failed|refused|econn/i.test(detail)) return '无法连接到 API 服务。';
  return 'API 操作失败。';
}

export default function ApiSettings() {
  const [presets, setPresets] = useState<ApiPreset[]>([]);
  const [activeId, setActiveId] = useState('');
  const [draft, setDraft] = useState<ApiDraft>(blank);
  const [showKey, setShowKey] = useState(false);
  const [working, setWorking] = useState(false);
  const [result, setResult] = useState('');
  const [technicalDetail, setTechnicalDetail] = useState('');
  const [tested, setTested] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  useEffect(() => {
    void window.desktopAPI
      ?.listApiPresets()
      .then((data) => {
        setPresets(data.presets);
        setActiveId(data.active_id);
        const current = data.presets.find((item) => item.id === data.active_id);
        if (current) setDraft({ ...current, api_key: '' });
      })
      .catch((error) => setResult(`读取 API 设置失败：${message(error)}`));
  }, []);
  function change(patch: Partial<ApiDraft>) {
    setDraft((current) => ({ ...current, ...patch }));
    setTested(false);
    setResult('');
    setTechnicalDetail('');
  }
  function choose(id: string) {
    if (!id) {
      setDraft(blank());
      setTested(false);
      return;
    }
    const preset = presets.find((item) => item.id === id);
    if (preset) {
      setDraft({ ...preset, api_key: '' });
      setTested(false);
      setModels([]);
      setResult('');
    }
  }
  async function run(action: 'test' | 'apply' | 'force' | 'models' | 'activate') {
    if (!window.desktopAPI || working) return;
    setWorking(true);
    setResult('');
    try {
      if (action === 'test') {
        const answer = await window.desktopAPI.testApiConnection(draft);
        setTested(true);
        setResult(`连接成功 · ${answer.model} · ${answer.latency_ms} ms`);
      } else if (action === 'models') {
        const answer = await window.desktopAPI.fetchApiModels(draft);
        setModels(answer.models);
        setResult(
          answer.models.length
            ? `找到 ${answer.models.length} 个模型`
            : '服务未返回模型；仍可手动填写',
        );
      } else {
        const answer =
          action === 'activate' && draft.id
            ? await window.desktopAPI.activateApiPreset(draft.id)
            : await window.desktopAPI.applyApiPreset(draft, action === 'force');
        setPresets(answer.presets);
        setActiveId(answer.active_id);
        const current = answer.presets.find((item) => item.id === answer.active_id);
        if (current) setDraft({ ...current, api_key: '' });
        setTested(false);
        setResult('已保存并应用；下一条聊天立即使用此 API');
      }
    } catch (error) {
      const detail = message(error);
      setTested(false);
      setResult(friendlyError(detail));
      setTechnicalDetail(detail);
    } finally {
      setWorking(false);
    }
  }
  async function remove() {
    if (!draft.id || !window.desktopAPI || !window.confirm(`删除 API 配置「${draft.name}」？`))
      return;
    setWorking(true);
    setResult('');
    setTechnicalDetail('');
    try {
      const answer = await window.desktopAPI.deleteApiPreset(draft.id);
      setPresets(answer.presets);
      setActiveId(answer.active_id);
      setDraft(blank());
      setResult('已删除 API 配置');
    } catch (error) {
      const detail = message(error);
      setResult(friendlyError(detail));
      setTechnicalDetail(detail);
    } finally {
      setWorking(false);
    }
  }
  return (
    <div className="api-settings">
      <label>
        API 预设
        <select
          aria-label="API 预设"
          value={draft.id ?? ''}
          onChange={(event) => choose(event.target.value)}
        >
          <option value="">新增 API 配置</option>
          {presets.map((item) => (
            <option value={item.id} key={item.id}>
              {item.name}
              {item.id === activeId ? ' · 使用中' : ''}
            </option>
          ))}
        </select>
      </label>
      <label>
        名称
        <input
          aria-label="API 预设名称"
          value={draft.name}
          onChange={(event) => change({ name: event.target.value })}
          maxLength={50}
        />
      </label>
      <label>
        API 服务商
        <select
          value={draft.provider}
          onChange={(event) => change({ provider: event.target.value })}
        >
          <option value="openai-compatible">兼容 OpenAI 接口</option>
        </select>
      </label>
      <label>
        API 地址
        <input
          aria-label="API 地址"
          value={draft.base_url}
          onChange={(event) => change({ base_url: event.target.value })}
          spellCheck={false}
        />
      </label>
      <p className="setting-note">兼容 OpenAI 接口的 API 地址，例如 https://xxx.com/v1</p>
      <label>
        API 密钥
        <span className="api-key-line">
          <input
            aria-label="API 密钥"
            type={showKey ? 'text' : 'password'}
            value={draft.api_key}
            onChange={(event) => change({ api_key: event.target.value, clear_key: false })}
            placeholder={
              draft.id && presets.find((item) => item.id === draft.id)?.has_key
                ? '已安全保存，留空沿用'
                : '输入密钥'
            }
            autoComplete="off"
          />
          <button type="button" onClick={() => setShowKey(!showKey)}>
            {showKey ? '隐藏' : '显示'}
          </button>
        </span>
      </label>
      <p className="setting-note">仅保存在本机，不会显示在聊天记录中。</p>
      {draft.id && presets.find((item) => item.id === draft.id)?.has_key && (
        <label>
          清除已存密钥
          <input
            type="checkbox"
            checked={Boolean(draft.clear_key)}
            onChange={(event) => change({ clear_key: event.target.checked, api_key: '' })}
          />
        </label>
      )}
      <label>
        模型
        <input
          aria-label="模型"
          value={draft.model}
          onChange={(event) => change({ model: event.target.value })}
          list="api-models"
        />
        <datalist id="api-models">
          {models.map((name) => (
            <option value={name} key={name} />
          ))}
        </datalist>
      </label>
      <p className="setting-note">选择或填写当前 API 支持的模型名称。</p>
      <label>
        温度
        <input
          aria-label="温度"
          type="number"
          min="0"
          max="2"
          step="0.1"
          value={draft.temperature ?? ''}
          placeholder="服务默认"
          onChange={(event) =>
            change({ temperature: event.target.value === '' ? null : Number(event.target.value) })
          }
        />
      </label>
      <p className="setting-note">数值越高，回复通常越随机。</p>
      <label>
        最大输出长度
        <input
          aria-label="最大输出长度"
          type="number"
          min="1"
          max="8192"
          value={draft.max_tokens ?? ''}
          placeholder="服务默认"
          onChange={(event) =>
            change({ max_tokens: event.target.value === '' ? null : Number(event.target.value) })
          }
        />
      </label>
      <p className="setting-note">控制单次回答允许生成的最大长度。</p>
      <label>
        请求超时时间
        <input
          aria-label="请求超时时间"
          type="number"
          min="5"
          max="180"
          value={draft.timeout_seconds}
          onChange={(event) => change({ timeout_seconds: Number(event.target.value) })}
        />
        <span>秒</span>
      </label>
      <h4>模型能力</h4>
      <p className="setting-note">
        文本默认可用；其余是配置提示。Agent 仍会用真实 Tool Calling 请求验证，失败时显示错误。
      </p>
      <label>
        图像理解
        <input
          type="checkbox"
          checked={Boolean(draft.capabilities?.vision)}
          onChange={(event) =>
            change({ capabilities: { ...draft.capabilities!, vision: event.target.checked } })
          }
        />
      </label>
      <label>
        工具调用
        <select
          value={
            draft.capabilities?.tool_calling === null ||
            draft.capabilities?.tool_calling === undefined
              ? 'unknown'
              : String(draft.capabilities.tool_calling)
          }
          onChange={(event) =>
            change({
              capabilities: {
                ...draft.capabilities!,
                tool_calling:
                  event.target.value === 'unknown' ? null : event.target.value === 'true',
              },
            })
          }
        >
          <option value="unknown">使用时检测</option>
          <option value="true">支持</option>
          <option value="false">不支持</option>
        </select>
      </label>
      <label>
        内置联网搜索
        <input
          type="checkbox"
          checked={Boolean(draft.capabilities?.web_search)}
          onChange={(event) =>
            change({ capabilities: { ...draft.capabilities!, web_search: event.target.checked } })
          }
        />
      </label>
      <label>
        结构化输出
        <input
          type="checkbox"
          checked={Boolean(draft.capabilities?.structured_output)}
          onChange={(event) =>
            change({
              capabilities: { ...draft.capabilities!, structured_output: event.target.checked },
            })
          }
        />
      </label>
      {(
        [
          ['reasoning', '推理'],
          ['streaming', '流式输出'],
        ] as const
      ).map(([key, label]) => (
        <label key={key}>
          {label}
          <select
            value={draft.capabilities?.[key] == null ? 'unknown' : String(draft.capabilities[key])}
            onChange={(event) =>
              change({
                capabilities: {
                  ...draft.capabilities!,
                  [key]: event.target.value === 'unknown' ? null : event.target.value === 'true',
                },
              })
            }
          >
            <option value="unknown">使用时检测</option>
            <option value="true">支持</option>
            <option value="false">不支持</option>
          </select>
        </label>
      ))}
      <div className="api-actions">
        <button disabled={working} onClick={() => void run('models')}>
          刷新模型列表
        </button>
        <button disabled={working} onClick={() => void run('test')}>
          测试连接
        </button>
        <button disabled={working} onClick={() => void run('apply')}>
          保存并应用
        </button>
        {draft.id && draft.id !== activeId && (
          <button disabled={working} onClick={() => void run('activate')}>
            设为默认
          </button>
        )}
        {draft.id && draft.id !== activeId && (
          <button disabled={working} onClick={() => void remove()}>
            删除配置
          </button>
        )}
      </div>
      {result && (
        <p className="setting-note" role="status">
          {result}
        </p>
      )}
      {technicalDetail && (
        <details>
          <summary>查看技术详情</summary>
          <pre className="diagnostic-details">{technicalDetail}</pre>
        </details>
      )}
      {technicalDetail && !tested && (
        <button className="api-force" disabled={working} onClick={() => void run('force')}>
          仍要强制应用
        </button>
      )}
      <ModelRolesPanel presets={presets} />
      <ModelRoutingPanel presets={presets} />
      <p className="setting-note">
        密钥由 Windows
        安全存储加密后保存在本机；界面不会显示已保存的密钥。测试失败不会替换当前可用配置。
      </p>
    </div>
  );
}

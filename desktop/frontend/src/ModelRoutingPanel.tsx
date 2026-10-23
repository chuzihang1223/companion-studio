import { useEffect, useState } from 'react';

const initialPolicy: ModelRoutingPolicy = {
  enabled: false,
  mode: 'ROLE_BASED',
  preference: 'BALANCED',
  manual_model: null,
  fallback_enabled: false,
  budget_mode: 'STRICT',
  max_output_tokens: 2048,
  cost_currency: 'USD',
};
const emptyRecord = (preset: ApiPreset): ModelRoutingRecord => ({
  model_id: preset.id || '',
  display_name: preset.name,
  model_type: 'TEXT_LLM',
  enabled: true,
  allow_auto: false,
  allow_remote_text: false,
  allow_attachments: false,
  context_window: null,
  input_cost: null,
  output_cost: null,
  cost_currency: 'USD',
  price_source: 'UNKNOWN',
  priority: 5,
});
const scopes = [
  ['task', '任务'],
  ['workflow', '工作流'],
  ['day', '每日'],
  ['provider', '服务商每日'],
] as const;
const numberOrNull = (value: string) => (value === '' ? null : Number(value));

const reasons: Record<string, string> = {
  USER_SELECTION: '使用你指定的模型或模型职责',
  CONSTRAINTS_THEN_BALANCED: '通过权限与能力检查后，按均衡偏好选择',
  CONSTRAINTS_THEN_LOW_COST: '通过权限与能力检查后，优先选择已知成本较低的模型',
  CONSTRAINTS_THEN_LOW_LATENCY: '通过权限与能力检查后，优先选择实测延迟较低的模型',
  CONSTRAINTS_THEN_QUALITY_FIRST: '通过权限与能力检查后，按用户优先级与能力选择',
  CONSTRAINTS_THEN_PRIVACY_FIRST: '通过权限与能力检查后，优先使用本地模型',
};
const taskNames: Record<string, string> = {
  CHAT: '聊天',
  PLANNING: '任务规划',
  EXECUTION: '工具执行',
  CODING: '编码',
  RAG: '知识库',
  STRUCTURED: '结构化输出',
  VISION: '图像理解',
  WORKFLOW: '工作流',
  SCHEDULED: '定时任务',
  VOICE: '语音对话',
};
export default function ModelRoutingPanel({ presets }: { presets: ApiPreset[] }) {
  const [policy, setPolicy] = useState(initialPolicy);
  const [models, setModels] = useState<ModelRoutingRecord[]>([]);
  const [status, setStatus] = useState<ModelRoutingStatus | null>(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    void window.desktopAPI
      ?.listApiPresets()
      .then((data) => {
        if (!active) return;
        const saved = (data as unknown as { routing?: ModelRoutingInput }).routing;
        if (saved) {
          setPolicy({ ...initialPolicy, ...saved.policy });
          setModels(saved.models);
        }
      })
      .catch(() => {
        if (active) setNote('无法读取路由设置');
      });
    void window.desktopAPI
      ?.modelRoutingStatus()
      .then((data) => {
        if (active) setStatus(data);
      })
      .catch(() => {});
    return () => {
      active = false;
    };
  }, []);
  const records = presets.map(
    (preset) => models.find((row) => row.model_id === preset.id) || emptyRecord(preset),
  );
  function edit(id: string, patch: Partial<ModelRoutingRecord>) {
    setModels(records.map((row) => (row.model_id === id ? { ...row, ...patch } : row)));
  }
  async function save() {
    if (!window.desktopAPI || busy) return;
    setBusy(true);
    try {
      const data = await window.desktopAPI.setModelRouting({ policy, models: records });
      setStatus(data);
      setNote('路由设置已保存。新任务使用新配置，运行中的任务保留原配置。保存不会发出模型请求。');
    } catch (error) {
      setNote(String(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="model-roles" aria-label="智能模型路由">
      <h4>智能模型路由</h4>
      <p className="setting-note">
        默认关闭，关闭时沿用上方模型职责。启用后先检查明确声明的能力、上下文和授权，再选择模型。未知能力不会被当作支持。
      </p>
      <label>
        <input
          type="checkbox"
          checked={policy.enabled}
          onChange={(event) => setPolicy({ ...policy, enabled: event.target.checked })}
        />
        启用智能模型路由
      </label>
      <label>
        路由模式
        <select
          aria-label="路由模式"
          value={policy.mode}
          onChange={(event) =>
            setPolicy({ ...policy, mode: event.target.value as ModelRoutingPolicy['mode'] })
          }
        >
          <option value="MANUAL">手动固定（禁止自动备用）</option>
          <option value="ROLE_BASED">按模型职责</option>
          <option value="AUTO">自动匹配</option>
        </select>
      </label>
      {policy.mode === 'MANUAL' && (
        <label>
          固定模型
          <select
            value={policy.manual_model || ''}
            onChange={(event) => setPolicy({ ...policy, manual_model: event.target.value || null })}
          >
            <option value="">请选择</option>
            {presets.map((row) => (
              <option key={row.id} value={row.id}>
                {row.name}
              </option>
            ))}
          </select>
        </label>
      )}
      <label>
        选择偏好
        <select
          aria-label="选择偏好"
          value={policy.preference}
          onChange={(event) =>
            setPolicy({
              ...policy,
              preference: event.target.value as ModelRoutingPolicy['preference'],
            })
          }
        >
          <option value="BALANCED">均衡</option>
          <option value="LOW_LATENCY">较低实测延迟</option>
          <option value="LOW_COST">较低已知成本</option>
          <option value="QUALITY_FIRST">用户优先级与能力优先</option>
          <option value="PRIVACY_FIRST">本地优先</option>
        </select>
      </label>
      <label>
        <input
          type="checkbox"
          checked={policy.fallback_enabled}
          onChange={(event) => setPolicy({ ...policy, fallback_enabled: event.target.checked })}
        />
        允许同一模型目标的备用配置
      </label>
      <p className="setting-note">
        备用只使用已授权配置，最多一次；API
        地址和模型必须相同。已输出文字或返回工具计划后不会重放。定时任务仍需原有授权。
      </p>
      {records.map((row) => {
        const observed = status?.models.find((model) => model.model_id === row.model_id);
        return (
          <details key={row.model_id}>
            <summary>
              {row.display_name} · {row.enabled ? '启用' : '停用'} ·{' '}
              {observed?.metrics.health || 'UNKNOWN'}
            </summary>
            <label>
              <input
                type="checkbox"
                checked={row.enabled}
                onChange={(event) => edit(row.model_id, { enabled: event.target.checked })}
              />
              启用模型
            </label>
            <label>
              <input
                type="checkbox"
                checked={row.allow_auto}
                onChange={(event) => edit(row.model_id, { allow_auto: event.target.checked })}
              />
              允许自动请求此模型（可能计费）
            </label>
            <label>
              <input
                type="checkbox"
                checked={row.allow_remote_text}
                onChange={(event) =>
                  edit(row.model_id, { allow_remote_text: event.target.checked })
                }
              />
              允许自动发送普通任务文字到此远程服务
            </label>
            <label>
              <input
                type="checkbox"
                checked={row.allow_attachments || false}
                onChange={(event) =>
                  edit(row.model_id, { allow_attachments: event.target.checked })
                }
              />
              允许此模型处理本次授权的文件和工具资料（可能发送至远程服务）
            </label>
            <p className="setting-note">
              知识库、截图和工具执行仍需原有的独立授权。模型能力在上方 API 预设中设置。
            </p>
            <label>
              模型类型
              <select
                value={row.model_type}
                onChange={(event) =>
                  edit(row.model_id, {
                    model_type: event.target.value as ModelRoutingRecord['model_type'],
                  })
                }
              >
                {['TEXT_LLM', 'VISION_LLM', 'EMBEDDING', 'ASR', 'TTS'].map((type) => (
                  <option key={type}>{type}</option>
                ))}
              </select>
            </label>
            <p className="setting-note">
              Embedding、ASR、TTS 只登记类型，通过各自专用服务调用，不进入聊天接口。
            </p>
            <label>
              声明的上下文容量（Token）
              <input
                type="number"
                min="256"
                max="2000000"
                placeholder="未知，自动路由不可选"
                value={row.context_window ?? ''}
                onChange={(event) =>
                  edit(row.model_id, { context_window: numberOrNull(event.target.value) })
                }
              />
            </label>
            <label>
              用户优先级（0–10）
              <input
                type="number"
                min="0"
                max="10"
                value={row.priority}
                onChange={(event) => edit(row.model_id, { priority: Number(event.target.value) })}
              />
            </label>
            <label>
              价格来源
              <select
                value={row.price_source}
                onChange={(event) =>
                  edit(row.model_id, {
                    price_source: event.target.value as 'USER' | 'UNKNOWN',
                    input_cost: null,
                    output_cost: null,
                  })
                }
              >
                <option value="UNKNOWN">未知（不视为免费）</option>
                <option value="USER">用户手动声明</option>
              </select>
            </label>
            {row.price_source === 'USER' && (
              <>
                <label>
                  币种
                  <select
                    value={row.cost_currency}
                    onChange={(event) =>
                      edit(row.model_id, { cost_currency: event.target.value as 'USD' | 'CNY' })
                    }
                  >
                    <option>USD</option>
                    <option>CNY</option>
                  </select>
                </label>
                <label>
                  每百万输入 Token 价格
                  <input
                    type="number"
                    min="0"
                    step="any"
                    value={row.input_cost ?? ''}
                    onChange={(event) =>
                      edit(row.model_id, { input_cost: numberOrNull(event.target.value) })
                    }
                  />
                </label>
                <label>
                  每百万输出 Token 价格
                  <input
                    type="number"
                    min="0"
                    step="any"
                    value={row.output_cost ?? ''}
                    onChange={(event) =>
                      edit(row.model_id, { output_cost: numberOrNull(event.target.value) })
                    }
                  />
                </label>
              </>
            )}
            {observed?.by_task &&
              Object.entries(observed.by_task).map(([task, metric]) => (
                <p className="setting-note" key={task}>
                  {taskNames[task] || task}：{metric.total.n} 个实测样本；P50{' '}
                  {metric.total.p50_ms ?? '未知'} ms / P95 {metric.total.p95_ms ?? '未知'} ms；成功{' '}
                  {metric.successes} / 失败 {metric.failures}。不代表业务目标已完成。
                </p>
              ))}
            {observed && (
              <p className="setting-note">
                服务商 {observed.provider_id}；实测样本 {observed.metrics.total.n}；总耗时 P50{' '}
                {observed.metrics.total.p50_ms ?? '未知'} ms / P95{' '}
                {observed.metrics.total.p95_ms ?? '未知'} ms；首段 P50{' '}
                {observed.metrics.ttft.p50_ms ?? '未知'} ms。
                {Object.entries(observed.capabilities)
                  .map(
                    ([name, cap]) =>
                      `${name}: ${cap.value == null ? '未知' : cap.value ? '支持' : '不支持'} (${cap.source})`,
                  )
                  .join('；')}
              </p>
            )}
          </details>
        );
      })}
      <details>
        <summary>预算与请求限制</summary>
        <label>
          金额预算方式
          <select
            value={policy.budget_mode}
            onChange={(event) =>
              setPolicy({ ...policy, budget_mode: event.target.value as 'STRICT' | 'ESTIMATED' })
            }
          >
            <option value="STRICT">严格：无法证明输入 Token 上界时阻止请求</option>
            <option value="ESTIMATED">仅约束用户估算预留，不保证实际账单</option>
          </select>
        </label>
        <p className="setting-note">
          当前兼容接口没有可信输入 Token 计数器，严格金额预算会阻止请求。请求次数限额仍可正常使用。
        </p>
        <p className="setting-note">
          留空表示未设置。金额按用户价格和声明上下文容量保守预留，不是服务商账单保证。未知价格、币种不一致或之前存在未知费用请求时，金额限额会阻止请求。失败、取消和重启不会清除预留。
        </p>
        <label>
          预算币种
          <select
            value={policy.cost_currency}
            onChange={(event) =>
              setPolicy({ ...policy, cost_currency: event.target.value as 'USD' | 'CNY' })
            }
          >
            <option>USD</option>
            <option>CNY</option>
          </select>
        </label>
        <label>
          单次最大输出 Token
          <input
            type="number"
            min="1"
            max="8192"
            value={policy.max_output_tokens}
            onChange={(event) =>
              setPolicy({ ...policy, max_output_tokens: Number(event.target.value) })
            }
          />
        </label>
        <label>
          单次金额上限
          <input
            type="number"
            min="0"
            step="any"
            value={policy.max_cost_per_request ?? ''}
            onChange={(event) =>
              setPolicy({ ...policy, max_cost_per_request: numberOrNull(event.target.value) })
            }
          />
        </label>
        {scopes.map(([scope, label]) => (
          <div key={scope}>
            <label>
              {label}请求上限
              <input
                type="number"
                min="1"
                value={policy[`max_requests_per_${scope}`] ?? ''}
                onChange={(event) =>
                  setPolicy({
                    ...policy,
                    [`max_requests_per_${scope}`]: numberOrNull(event.target.value),
                  })
                }
              />
            </label>
            <label>
              {label}金额上限
              <input
                type="number"
                min="0"
                step="any"
                value={policy[`max_cost_per_${scope}`] ?? ''}
                onChange={(event) =>
                  setPolicy({
                    ...policy,
                    [`max_cost_per_${scope}`]: numberOrNull(event.target.value),
                  })
                }
              />
            </label>
          </div>
        ))}
      </details>
      <button disabled={busy} onClick={() => void save()}>
        保存模型路由
      </button>
      <button
        disabled={busy}
        onClick={() =>
          void window.desktopAPI
            ?.modelRoutingStatus()
            .then(setStatus)
            .catch((error: unknown) => setNote(String(error)))
        }
      >
        刷新路由状态
      </button>
      {status && status.recent_requests.length > 0 && (
        <details>
          <summary>最近请求与 Usage</summary>
          {status.recent_requests.map((row) => (
            <p className="setting-note" key={row.request_id}>
              {row.model_id} · {row.status} ·{' '}
              {row.decision ? reasons[row.decision.reason] || '已按当前策略选择' : ''} ·{' '}
              {row.usage ? `${row.usage.total_tokens} Token（服务商报告）` : 'Token 未知'} ·{' '}
              {row.reserved_cost == null
                ? '费用未知'
                : `预留 ${row.reserved_cost} ${row.currency}（用户估算）`}
            </p>
          ))}
        </details>
      )}
      {note && <p role="status">{note}</p>}
    </section>
  );
}

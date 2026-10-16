import type { Dataset, EvaluationMode, ModelChoice } from './types';

export type EvaluationForm = {
  datasetId: 'studio-core' | 'recovery-core' | 'mcp-skills-core';
  mode: EvaluationMode;
  role: string;
  caseId: string;
  repetitions: number;
  concurrency: number;
  timeout: number;
  duration: number;
  requestLimit: number;
  tokenBudget: number;
  consent: boolean;
};
export const initialForm: EvaluationForm = {
  datasetId: 'studio-core',
  mode: 'mock',
  role: 'planning_model',
  caseId: '',
  repetitions: 1,
  concurrency: 1,
  timeout: 20,
  duration: 600,
  requestLimit: 10,
  tokenBudget: 20000,
  consent: false,
};
type Props = {
  form: EvaluationForm;
  dataset: Dataset | null;
  models: ModelChoice[];
  change: (value: Partial<EvaluationForm>) => void;
};

export default function EvaluationSetup({ form, dataset, models, change }: Props) {
  const serial = ['live_model', 'windows_gui'].includes(form.mode);
  const numbers = [
    ['repetitions', '每项次数', '评测次数', 1, 5],
    ['concurrency', '并发', '评测并发', 1, 2],
    ['timeout', '单项秒数', '评测超时', 2, 60],
    ['duration', '批次秒数', '评测总耗时', 5, 1800],
  ] as const;
  return (
    <>
      <div className="evaluation-controls">
        <label>
          测试集
          <select
            aria-label="评测测试集"
            value={form.datasetId}
            onChange={(e) =>
              change({
                datasetId: e.target.value as EvaluationForm['datasetId'],
                caseId: '',
                mode: 'mock',
                consent: false,
              })
            }
          >
            <option value="studio-core">Studio Core · 20 项</option>
            <option value="recovery-core">Recovery Core · 16 项</option>
            <option value="mcp-skills-core">MCP & Skills Core · 20 项</option>
          </select>
        </label>
        <label>
          模式
          <select
            aria-label="评测模式"
            value={form.mode}
            onChange={(e) => change({ mode: e.target.value as EvaluationMode, consent: false })}
          >
            <option value="mock">Mock · 模拟模型与桌面</option>
            <option value="sandbox">Sandbox · 真实隔离文件</option>
            <option value="windows_gui">Windows GUI · 需专用受控桥</option>
            <option value="live_model" disabled={form.datasetId !== 'studio-core'}>
              Live Model · 明确授权后调用
            </option>
          </select>
        </label>
        <label>
          任务
          <select
            aria-label="评测任务"
            value={form.caseId}
            onChange={(e) => change({ caseId: e.target.value })}
          >
            <option value="">全部 20 项</option>
            {dataset?.cases?.map((c) => (
              <option key={c.case_id} value={c.case_id}>
                {c.case_id} · {c.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          模型配置
          <select
            aria-label="评测模型"
            value={form.role}
            disabled={form.mode !== 'live_model'}
            onChange={(e) => change({ role: e.target.value })}
          >
            {models.map((m) => (
              <option key={m.role} value={m.role}>
                {m.role} · {m.model_id || '未配置'}
              </option>
            ))}
          </select>
        </label>
        {numbers.map(([key, title, aria, min, max]) => (
          <label key={key}>
            {title}
            <input
              aria-label={aria}
              type="number"
              min={min}
              max={max}
              disabled={key === 'concurrency' && serial}
              value={key === 'concurrency' && serial ? 1 : form[key]}
              onChange={(e) => change({ [key]: Number(e.target.value) })}
            />
          </label>
        ))}
      </div>
      {form.mode === 'windows_gui' && (
        <p role="status">
          此候选尚未接入专用交互式 Fixture 桥，对应任务将记录
          BLOCKED；不会操作你的应用或已登录账号。
        </p>
      )}
      {form.mode === 'live_model' && (
        <fieldset>
          <legend>真实模型调用预算</legend>
          <label>
            最多请求
            <input
              aria-label="评测请求上限"
              type="number"
              min={1}
              max={100}
              value={form.requestLimit}
              onChange={(e) => change({ requestLimit: Number(e.target.value) })}
            />
          </label>
          <label>
            Token 停止阈值
            <input
              aria-label="评测Token预算"
              type="number"
              min={1}
              max={200000}
              value={form.tokenBudget}
              onChange={(e) => change({ tokenBudget: Number(e.target.value) })}
            />
          </label>
          <p>
            Token 按响应 Usage 记录；输入 Token 在请求前不可知，此阈值不是服务商的计费硬上限。Usage
            缺失时停止后续调用。
          </p>
          <label>
            <input
              type="checkbox"
              checked={form.consent}
              onChange={(e) => change({ consent: e.target.checked })}
            />
            我授权这次合成评测调用所选模型，了解可能计费。
          </label>
        </fieldset>
      )}
    </>
  );
}

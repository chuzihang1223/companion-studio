import { useEffect, useRef, useState } from 'react';
import type { SavedWorkflow, WorkflowVersion } from '../workflows/types';
import type { Definition, Preview, Saved, Source } from './types';
import { newDefinition } from './types';
import TriggerFields from './TriggerFields';

export default function ScheduleForm({
  owner,
  saved,
  workflows,
  sources,
  onSaved,
}: {
  owner: string;
  saved: Saved | null;
  workflows: SavedWorkflow[];
  sources: Source[];
  onSaved: () => void;
}) {
  const [definition, setDefinition] = useState(() => saved?.definition || newDefinition(owner));
  const [versions, setVersions] = useState<WorkflowVersion[]>([]);
  const [inputs, setInputs] = useState(JSON.stringify(saved?.definition.inputs || {}, null, 2));
  const [preview, setPreview] = useState<Preview | null>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  const generation = useRef(0);
  useEffect(() => {
    let live = true;
    if (!definition.workflow_id) {
      setVersions([]);
      return;
    }
    void window.desktopAPI
      ?.readWorkflows('versions', { workflow_id: definition.workflow_id })
      .then((result) => {
        if (live) setVersions(result.items);
      })
      .catch((cause) => live && setError(String(cause)));
    return () => {
      live = false;
    };
  }, [definition.workflow_id]);
  function change(next: Partial<Definition>) {
    generation.current++;
    setPreview(null);
    setDefinition((old) => ({ ...old, ...next }));
  }
  async function submit(save: boolean) {
    const ticket = generation.current;
    setBusy(true);
    setError('');
    try {
      const value = { ...definition, inputs: JSON.parse(inputs) as Record<string, unknown> };
      const result = await window.desktopAPI!.controlSchedules(save ? 'save' : 'preview', {
        definition: value,
        revision: saved?.revision || 0,
      });
      if (generation.current !== ticket) return;
      if (save) onSaved();
      else setPreview(result as Preview);
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="scheduler-card" aria-label="定时任务编辑">
      <h3>{saved ? '编辑任务' : '创建定时任务'}</h3>
      <label>
        任务名称
        <input
          aria-label="任务名称"
          maxLength={80}
          value={definition.name}
          onChange={(e) => change({ name: e.target.value })}
        />
      </label>
      <label>
        已发布工作流
        <select
          aria-label="已发布工作流"
          value={definition.workflow_id}
          onChange={(e) => change({ workflow_id: e.target.value, workflow_version_id: '' })}
        >
          <option value="">请先在流程设计中发布工作流</option>
          {workflows.map((row) => (
            <option value={row.definition.workflow_id} key={row.definition.workflow_id}>
              {row.definition.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        固定版本
        <select
          aria-label="固定工作流版本"
          value={definition.workflow_version_id}
          onChange={(e) => change({ workflow_version_id: e.target.value })}
        >
          <option value="">选择不可变的已发布版本</option>
          {versions.map((row) => (
            <option value={row.version_id} key={row.version_id}>
              v{row.version} · {row.digest.slice(0, 10)}
            </option>
          ))}
        </select>
      </label>
      <TriggerFields definition={definition} sources={sources} change={change} />
      <label>
        固定输入（JSON 对象）
        <textarea
          aria-label="固定任务输入"
          value={inputs}
          onChange={(e) => {
            generation.current++;
            setPreview(null);
            setInputs(e.target.value);
          }}
        />
      </label>
      <label>
        错过执行时间
        <select
          aria-label="错过执行策略"
          value={definition.misfire_policy}
          onChange={(e) =>
            change({ misfire_policy: e.target.value as Definition['misfire_policy'] })
          }
        >
          <option value="SKIP_MISSED">跳过并记录</option>
          <option value="RUN_ONCE_AFTER_RESUME">恢复后最多执行一次（仍需权限）</option>
          <option value="REQUIRE_REVIEW">等待核查</option>
        </select>
      </label>
      <label>
        上次尚未结束
        <select
          value={definition.concurrency_policy}
          onChange={(e) =>
            change({ concurrency_policy: e.target.value as Definition['concurrency_policy'] })
          }
        >
          <option value="SKIP_IF_RUNNING">跳过重叠</option>
          <option value="QUEUE_ONE">最多排队一次</option>
          <option value="REJECT_OVERLAP">拒绝并报告</option>
        </select>
      </label>
      <label>
        <input
          type="checkbox"
          checked={definition.notification}
          onChange={(e) => change({ notification: e.target.checked })}
        />
        创建本地状态通知
      </label>
      <div className="scheduler-actions">
        <button disabled={busy} onClick={() => void submit(false)}>
          预览时间与权限
        </button>
        <button disabled={busy || !preview} onClick={() => void submit(true)}>
          保存定时任务
        </button>
      </div>
      {preview && (
        <div className="scheduler-preview" data-schedule-preview="ready">
          <h4>真实时区预览</h4>
          <ol>
            {preview.times.map((row) => (
              <li key={row.utc}>
                {row.local}（UTC：{row.utc}）
              </li>
            ))}
          </ol>
          <p>
            此预览不授予工具权限。
            {preview.permissions.may_use_models
              ? '包含模型节点，需本次明确确认，可能计费。'
              : '未包含文本模型节点。'}{' '}
            混合／向量检索不支持后台自动执行。
          </p>
          <ul>
            {preview.permissions.nodes.map((node) => (
              <li key={node.node_id}>
                {node.type}
                {node.tool ? ' · ' + node.tool : ''} ·{' '}
                {node.requires_interaction ? '需交互授权' : '仍检查当前权限'}
              </li>
            ))}
          </ul>
        </div>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}

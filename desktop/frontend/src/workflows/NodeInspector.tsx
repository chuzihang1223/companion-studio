import { useEffect, useState } from 'react';
import { nodeTypes } from './model';
import type { WorkflowCatalog, WorkflowNode, NodeRun } from './types';

export default function NodeInspector({
  node,
  record,
  catalog,
  variables,
  readOnly,
  onApply,
  onTrace,
}: {
  node: WorkflowNode | null;
  record?: NodeRun;
  catalog: WorkflowCatalog;
  variables: string[];
  readOnly: boolean;
  onApply: (node: WorkflowNode) => void;
  onTrace: () => void;
}) {
  const [title, setTitle] = useState(node?.title || '');
  const [text, setText] = useState(JSON.stringify(node?.config || {}, null, 2));
  const [timeout, setTimeout] = useState(node?.timeout || 60);
  const [retry, setRetry] = useState(
    node?.retry_policy || { max_attempts: 1, strategy: 'stop' as const },
  );
  const [error, setError] = useState('');
  useEffect(() => {
    setTitle(node?.title || '');
    setText(JSON.stringify(node?.config || {}, null, 2));
    setTimeout(node?.timeout || 60);
    setRetry(node?.retry_policy || { max_attempts: 1, strategy: 'stop' });
    setError('');
  }, [node?.node_id, node?.config, node?.title, node?.timeout, node?.retry_policy]);
  if (!node)
    return (
      <aside className="wf-inspector">
        <h3>节点属性</h3>
        <p>选择一个节点，查看配置、变量与实际运行结果。</p>
        <p>连线决定先后顺序；数据用变量引用传递。运行前会验证完整图。</p>
      </aside>
    );
  let config: Record<string, unknown> = {};
  try {
    config = JSON.parse(text);
  } catch {
    /* Editing incomplete JSON does not mutate the saved graph. */
  }
  function update(key: string, value: unknown) {
    setText(JSON.stringify({ ...config, [key]: value }, null, 2));
  }
  function apply() {
    try {
      const parsed: unknown = JSON.parse(text);
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
        throw Error('配置必须是 JSON 对象。');
      if (!title.trim() || !Number.isFinite(timeout) || timeout < 0.05 || timeout > 300)
        throw Error('填写标题和 0.05～300 秒超时。');
      onApply({
        ...node!,
        title: title.trim(),
        timeout,
        retry_policy: retry,
        config: parsed as Record<string, unknown>,
        metadata: {},
      });
      setError('');
    } catch (e) {
      setError(String(e));
    }
  }
  const bound = (key: string) => String(config[key] ?? '');
  const info = nodeTypes.find((t) => t.type === node.type)!;
  return (
    <aside className="wf-inspector" aria-label="节点属性">
      <h3>{info.title}</h3>
      <p>{info.help}</p>
      <code className="wf-id">{node.node_id}</code>
      <fieldset disabled={readOnly}>
        <label>
          节点标题
          <input
            aria-label="节点标题"
            value={title}
            maxLength={100}
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>
        <label>
          超时（秒）
          <input
            aria-label="节点超时"
            type="number"
            min="0.05"
            max="300"
            step="1"
            value={timeout}
            onChange={(e) => setTimeout(Number(e.target.value))}
          />
        </label>
        {node.type === 'input' && (
          <>
            <label>
              输入参数名
              <input value={bound('name')} onChange={(e) => update('name', e.target.value)} />
            </label>
            <label>
              数据类型
              <select value={bound('type')} onChange={(e) => update('type', e.target.value)}>
                {['string', 'number', 'boolean', 'object', 'array', 'file_reference'].map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
            </label>
            <p>同时在「流程参数」中声明这个字段和必填条件。</p>
          </>
        )}
        {node.type === 'tool' && (
          <>
            <label>
              已注册工具
              <select
                aria-label="节点内置工具"
                value={bound('name')}
                onChange={(e) => update('name', e.target.value)}
              >
                {catalog.tools.map((t) => (
                  <option key={t.name} value={t.name}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              写入方式
              <select
                value={bound('coding_mode') || 'virtual_only'}
                onChange={(e) => update('coding_mode', e.target.value)}
              >
                <option value="virtual_only">仅隔离草稿 / 副本</option>
                <option value="preview_patch">预览变更</option>
                <option value="write_workspace">写入已选项目（另需权限）</option>
              </select>
            </label>
            <details>
              <summary>工具参数格式</summary>
              <pre>
                {JSON.stringify(catalog.tools.find((t) => t.name === config.name)?.schema, null, 2)}
              </pre>
            </details>
          </>
        )}
        {node.type === 'mcp_tool' && (
          <>
            <label>
              已发现的 MCP 工具
              <select
                aria-label="节点 MCP 工具"
                value={bound('tool_id')}
                onChange={(e) => {
                  const tool = catalog.mcp_tools.find((t) => t.id === e.target.value);
                  if (tool)
                    setText(
                      JSON.stringify(
                        {
                          ...config,
                          tool_id: tool.id,
                          server_id: tool.server_id,
                          schema_fingerprint: tool.schema_fingerprint,
                        },
                        null,
                        2,
                      ),
                    );
                }}
              >
                <option value="">请选择已连接并启用的工具</option>
                {catalog.mcp_tools.map((t) => (
                  <option key={t.id} value={t.id} disabled={!t.enabled}>
                    {t.name} · {t.server_id.slice(0, 8)}
                    {!t.enabled ? '（未启用）' : ''}
                  </option>
                ))}
              </select>
            </label>
            <details>
              <summary>当前外部参数格式</summary>
              <pre>
                {JSON.stringify(
                  catalog.mcp_tools.find((t) => t.id === config.tool_id)?.input_schema,
                  null,
                  2,
                )}
              </pre>
            </details>
            <p>连接和启用请到「设置 → MCP & Skills」。本页面不会自动连接。</p>
          </>
        )}
        {node.type === 'agent_skill' && (
          <>
            <label>
              已启用 Skill
              <select
                aria-label="节点 Skill"
                value={bound('skill_id')}
                onChange={(e) => {
                  const skill = catalog.skills.find((s) => s.id === e.target.value);
                  if (skill)
                    setText(
                      JSON.stringify(
                        { ...config, skill_id: skill.id, content_hash: skill.content_hash },
                        null,
                        2,
                      ),
                    );
                }}
              >
                <option value="">请选择 Skill</option>
                {catalog.skills.map((s) => (
                  <option key={s.id} value={s.id} disabled={!s.enabled}>
                    {s.name}
                    {s.enabled ? '' : '（已禁用）'}
                  </option>
                ))}
              </select>
            </label>
            <label>
              当前任务
              <textarea value={bound('goal')} onChange={(e) => update('goal', e.target.value)} />
            </label>
          </>
        )}
        {node.type === 'llm_agent' && (
          <>
            <label>
              当前节点说明
              <textarea
                value={bound('instruction')}
                onChange={(e) => update('instruction', e.target.value)}
              />
            </label>
            <label>
              模型职责
              <select
                value={bound('model_role') || 'planning_model'}
                onChange={(e) => update('model_role', e.target.value)}
              >
                <option value="planning_model">任务规划模型</option>
                <option value="character_model">聊天模型</option>
                <option value="tool_execution_model">工具分析模型</option>
                <option value="coding_model">编码模型</option>
              </select>
            </label>
            <p>
              仅使用现有安全配置，不在工作流中保存密钥。结构化输出可在 JSON 配置中声明
              output_schema。
            </p>
          </>
        )}
        {node.type === 'condition' && (
          <p>
            在 expression 中使用
            equals、not_equals、greater_than、less_than、contains、exists、is_empty、and、or 或
            not；分支分别连接 true / false 出口。
          </p>
        )}
        {node.type === 'transform' && (
          <label>
            转换操作
            <select
              value={bound('operation')}
              onChange={(e) => update('operation', e.target.value)}
            >
              {[
                ['concat', '拼接'],
                ['select', '选择字段'],
                ['default', '默认值'],
                ['number', '转为数字'],
                ['length', '长度'],
                ['map', '数组字段映射'],
                ['extract', 'JSON 字段提取'],
              ].map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        )}
        {node.type === 'human_approval' && (
          <label>
            审批说明
            <textarea
              value={bound('message')}
              onChange={(e) => update('message', e.target.value)}
            />
          </label>
        )}
        {node.type === 'merge' && (
          <label>
            汇合方式
            <select
              value={bound('mode') || 'all_active'}
              onChange={(e) => update('mode', e.target.value)}
            >
              <option value="all_active">汇总全部已激活分支</option>
              <option value="first_available">第一个可用结果（拓扑顺序）</option>
            </select>
          </label>
        )}
        {node.type === 'output' && (
          <label>
            输出格式
            <select
              value={bound('format') || 'json'}
              onChange={(e) => update('format', e.target.value)}
            >
              <option value="json">JSON / 引用</option>
              <option value="text">文本</option>
            </select>
          </label>
        )}
        <label>
          节点配置（JSON）
          <textarea
            className="wf-json"
            aria-label="节点配置 JSON"
            value={text}
            onChange={(e) => setText(e.target.value)}
            spellCheck={false}
            rows={10}
          />
        </label>
        <label>
          读取失败的恢复策略
          <select
            value={retry.strategy}
            onChange={(e) =>
              setRetry({ ...retry, strategy: e.target.value as 'stop' | 'safe_read' })
            }
          >
            <option value="stop">停止并显示错误</option>
            <option value="safe_read">原 Recovery Policy 允许时重试只读</option>
          </select>
        </label>
        {retry.strategy === 'safe_read' && (
          <label>
            最多尝试次数
            <input
              type="number"
              min={1}
              max={3}
              value={retry.max_attempts}
              onChange={(e) => setRetry({ ...retry, max_attempts: Number(e.target.value) })}
            />
          </label>
        )}
        <button className="wf-primary" onClick={apply}>
          应用节点配置
        </button>
      </fieldset>
      {error && <p role="alert">{error}</p>}
      <details>
        <summary>可引用的已有节点 / 输入</summary>
        <p>
          例如 <code>{'{{nodes.read.output.content}}'}</code>，完整引用保留值类型，也可写{' '}
          <code>{'{"$ref":"nodes.read.output"}'}</code>。最终作用域由后端检查。
        </p>
        {variables.map((v) => (
          <code key={v} className="wf-variable">
            {v}
          </code>
        ))}
      </details>
      {record && (
        <section className="wf-node-result">
          <h4>实际运行：{record.state}</h4>
          <p>
            {record.error_code || '无已记录错误'} · {Math.round(record.duration_ms)} ms · 尝试{' '}
            {record.attempt}
          </p>
          <pre>{JSON.stringify(record.output, null, 2)}</pre>
          <button onClick={onTrace}>查看节点 Trace</button>
        </section>
      )}
    </aside>
  );
}

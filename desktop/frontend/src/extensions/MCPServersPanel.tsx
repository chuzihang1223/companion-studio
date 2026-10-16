import { useEffect, useState } from 'react';
import { api, type ExternalTool, type Server, type ServerConfig } from './types';

const initial: ServerConfig = {
  display_name: '',
  transport: 'stdio',
  endpoint_or_command: '',
  args: [],
  enabled: false,
  allow_loopback: false,
  environment_names: [],
  credential_reference: false,
  connect_timeout: 10,
  call_timeout: 20,
  protocol_mode: 'auto',
};
const risks = [
  'HIGH_RISK_ACTION',
  'READ_ONLY_LOCAL',
  'READ_ONLY_EXTERNAL',
  'WRITE_LOCAL',
  'WRITE_EXTERNAL',
  'PROCESS_EXECUTION',
  'SENSITIVE_DATA_ACCESS',
];
const labels: Record<string, string> = {
  HIGH_RISK_ACTION: '高风险 · 每次确认',
  READ_ONLY_LOCAL: '经审阅的本地只读',
  READ_ONLY_EXTERNAL: '经审阅的外部只读',
  WRITE_LOCAL: '本地写入',
  WRITE_EXTERNAL: '外部写入',
  PROCESS_EXECUTION: '运行程序',
  SENSITIVE_DATA_ACCESS: '敏感数据',
};

export default function MCPServersPanel() {
  const [servers, setServers] = useState<Server[]>([]);
  const [selected, setSelected] = useState<Server | null>(null);
  const [tools, setTools] = useState<ExternalTool[]>([]);
  const [config, setConfig] = useState<ServerConfig>(initial);
  const [args, setArgs] = useState('[]');
  const [token, setToken] = useState('');
  const [environment, setEnvironment] = useState('{}');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [preview, setPreview] = useState('');
  const [promptName, setPromptName] = useState('');
  const [promptArgs, setPromptArgs] = useState('{}');
  const [kind, setKind] = useState<'resource' | 'prompt'>('resource');
  const [page, setPage] = useState(0);
  const [templates, setTemplates] = useState<ServerConfig[]>([]);
  async function reload() {
    const items = (await api().readExtensions('servers')).items;
    setServers(items);
    setSelected((previous) =>
      previous ? items.find((row) => row.server_id === previous.server_id) || null : null,
    );
  }
  useEffect(() => {
    let active = true;
    void api()
      .readExtensions('servers')
      .then((v) => {
        if (active) setServers(v.items);
      })
      .catch((e) => {
        if (active) setNotice(String(e));
      });
    return () => {
      active = false;
    };
  }, []);
  async function run(operation: () => Promise<unknown>, message = '操作已完成') {
    if (busy) return;
    setBusy(true);
    setNotice('');
    try {
      await operation();
      await reload();
      setNotice(message);
    } catch (e) {
      setNotice(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function choose(server: Server) {
    setSelected(server);
    setConfig(server.config);
    setArgs(JSON.stringify(server.config.args));
    setToken('');
    setEnvironment('{}');
    setPage(0);
    setPreview('');
    setTools((await api().readExtensions('tools', { server_id: server.server_id })).items);
  }
  const id = selected?.server_id;
  return (
    <div className="extension-manager">
      <div className="extension-actions">
        <button
          disabled={busy}
          onClick={() =>
            void run(async () => {
              const value = (await api().controlExtensions('template-select')) as {
                servers: ServerConfig[];
              } | null;
              setTemplates(value?.servers || []);
            }, '模板仅供预览；填写并保存后仍需手动连接')
          }
        >
          从模板填写配置
        </button>
        <button
          disabled={busy}
          onClick={() =>
            void run(() => api().controlExtensions('template-export'), '脱敏模板已保存到 Exports')
          }
        >
          导出脱敏模板
        </button>
        <button
          disabled={busy}
          onClick={() => {
            setSelected(null);
            setConfig(initial);
            setArgs('[]');
            setTools([]);
            setToken('');
            setEnvironment('{}');
          }}
        >
          添加连接
        </button>
        <button disabled={busy} onClick={() => void run(reload)}>
          刷新状态
        </button>
        <button
          disabled={busy}
          onClick={() =>
            void run(
              async () =>
                setPreview(JSON.stringify(await api().readExtensions('template'), null, 2)),
              '脱敏模板已显示',
            )
          }
        >
          脱敏配置模板
        </button>
      </div>
      <p role="status">{notice}</p>
      {templates.map((template, index) => (
        <article className="extension-card" key={index}>
          <strong>{template.display_name}</strong>
          <pre>{JSON.stringify(template, null, 2)}</pre>
          <button
            disabled={busy}
            onClick={() => {
              const next = { ...initial, ...template, enabled: false };
              setSelected(null);
              setConfig(next);
              setArgs(JSON.stringify(next.args));
              setTemplates([]);
              setToken('');
              setTools([]);
            }}
          >
            填写此配置（保持禁用）
          </button>
        </article>
      ))}
      <div className="extension-list" aria-label="MCP 服务器列表">
        {servers.map((server) => (
          <button
            key={server.server_id}
            className={id === server.server_id ? 'active' : ''}
            disabled={busy}
            onClick={() => void run(() => choose(server), '已选择服务器')}
          >
            <strong>{server.config.display_name}</strong> · {server.config.transport} ·{' '}
            {server.connection_status}
            <small>
              工具 {server.tool_count} · 协议 {server.protocol_version || '未连接'} ·{' '}
              {server.last_connected_at
                ? new Date(server.last_connected_at).toLocaleString()
                : '尚未连接'}
            </small>
            {server.last_error && <small>{server.last_error}</small>}
          </button>
        ))}
        {!servers.length && <p>尚未配置服务器；应用启动不会自动运行外部程序。</p>}
      </div>
      <fieldset disabled={busy}>
        <legend>{id ? '编辑服务器（先断开连接）' : '新服务器'}</legend>
        <label>
          显示名称
          <input
            aria-label="MCP 显示名称"
            value={config.display_name}
            maxLength={80}
            onChange={(e) => setConfig({ ...config, display_name: e.target.value })}
          />
        </label>
        <label>
          传输
          <select
            value={config.transport}
            onChange={(e) =>
              setConfig({
                ...config,
                transport: e.target.value as 'stdio' | 'http',
                args: [],
                environment_names: [],
              })
            }
          >
            <option value="stdio">本地 stdio</option>
            <option value="http">Streamable HTTP</option>
          </select>
        </label>
        <label>
          {config.transport === 'stdio' ? '可执行文件完整路径（.exe）' : 'HTTPS MCP 端点'}
          <input
            aria-label="MCP 端点或程序"
            value={config.endpoint_or_command}
            onChange={(e) => setConfig({ ...config, endpoint_or_command: e.target.value })}
          />
        </label>
        {config.transport === 'stdio' ? (
          <>
            <label>
              参数（JSON 字符串数组，不使用 Shell）
              <textarea
                aria-label="MCP 参数"
                value={args}
                onChange={(e) => setArgs(e.target.value)}
              />
            </label>
            <label>
              环境变量名称（空格分隔，值请在凭据栏填写）
              <input
                value={config.environment_names.join(' ')}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    environment_names: e.target.value.split(/\s+/).filter(Boolean),
                  })
                }
              />
            </label>
          </>
        ) : (
          <>
            <label>
              <input
                type="checkbox"
                checked={config.allow_loopback}
                onChange={(e) => setConfig({ ...config, allow_loopback: e.target.checked })}
              />
              允许明确的本机开发端点（不允许应用后端端口）
            </label>
            <label>
              <input
                type="checkbox"
                checked={config.credential_reference}
                onChange={(e) => setConfig({ ...config, credential_reference: e.target.checked })}
              />
              服务器需要 Bearer 凭据
            </label>
          </>
        )}
        <label>
          协议
          <select
            value={config.protocol_mode}
            onChange={(e) =>
              setConfig({
                ...config,
                protocol_mode: e.target.value as ServerConfig['protocol_mode'],
              })
            }
          >
            <option value="auto">自动探测</option>
            <option value="2026-07-28">2026-07-28</option>
            <option value="legacy">旧式初始化协议</option>
          </select>
        </label>
        <label>
          工具超时（秒）
          <input
            type="number"
            min="0.2"
            max="60"
            value={config.call_timeout}
            onChange={(e) => setConfig({ ...config, call_timeout: Number(e.target.value) })}
          />
        </label>
        <label>
          <input
            aria-label="启用 MCP 服务器"
            type="checkbox"
            checked={config.enabled}
            onChange={(e) => setConfig({ ...config, enabled: e.target.checked })}
          />
          启用配置（仍需手动连接）
        </label>
        <button
          onClick={() =>
            void run(async () => {
              const saved = (await api().controlExtensions('server-save', {
                server_id: id,
                config: { ...config, args: config.transport === 'stdio' ? JSON.parse(args) : [] },
              })) as Server;
              await choose(saved);
            }, '配置已保存，尚未连接')
          }
        >
          保存配置
        </button>
      </fieldset>
      {id && (
        <>
          <div className="extension-actions">
            <button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await api().controlExtensions('connect', { server_id: id });
                  const fresh = (await api().readExtensions('servers')).items.find(
                    (row) => row.server_id === id,
                  );
                  if (fresh) await choose(fresh);
                }, '连接成功；请逐项审阅并启用工具')
              }
            >
              连接／重新连接／测试
            </button>
            <button
              disabled={busy}
              onClick={() =>
                void run(
                  () => api().controlExtensions('disconnect', { server_id: id }),
                  '连接已断开',
                )
              }
            >
              断开
            </button>
            <button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await api().controlExtensions('refresh', { server_id: id });
                  setTools((await api().readExtensions('tools', { server_id: id })).items);
                })
              }
            >
              刷新工具
            </button>
            <button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  await api().controlExtensions('server-remove', { server_id: id });
                  setSelected(null);
                  setTools([]);
                  setConfig(initial);
                })
              }
            >
              移除配置
            </button>
          </div>
          <details>
            <summary>凭据安全存储（不显示已保存值）</summary>
            <fieldset disabled={busy}>
              <label>
                Bearer Token
                <input
                  type="password"
                  autoComplete="off"
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                />
              </label>
              <label>
                环境变量值（JSON 对象，仅此服务器可用）
                <textarea
                  autoComplete="off"
                  value={environment}
                  onChange={(e) => setEnvironment(e.target.value)}
                />
              </label>
              <button
                onClick={() =>
                  void run(async () => {
                    await api().controlExtensions('credentials-save', {
                      server_id: id,
                      credential: token,
                      environment: JSON.parse(environment),
                    });
                    setToken('');
                    setEnvironment('{}');
                  }, '凭据已加密保存')
                }
              >
                保存凭据
              </button>
              <p>
                当前支持手动 Bearer 凭据；OAuth 登录尚未开放。不会将 Token 写入配置模板或 Trace。
              </p>
            </fieldset>
          </details>
          <h3>工具 · {tools.length}</h3>
          <p>外部工具默认禁用且每次确认。标为只读是你对行为的审阅，不是程序沙箱。</p>
          {tools.slice(page * 20, (page + 1) * 20).map((tool) => (
            <article className="extension-card" key={tool.id}>
              <strong>{tool.name}</strong>
              <p>{tool.description}</p>
              <label>
                <input
                  type="checkbox"
                  disabled={busy}
                  checked={tool.enabled}
                  onChange={(e) =>
                    void run(async () => {
                      await api().controlExtensions('tool-save', {
                        tool_id: tool.id,
                        enabled: e.target.checked,
                        risk_level: tool.risk_level,
                      });
                      setTools((await api().readExtensions('tools', { server_id: id })).items);
                    })
                  }
                />
                启用此工具
              </label>
              <select
                aria-label={`${tool.name} 风险`}
                disabled={busy}
                value={tool.risk_level}
                onChange={(e) =>
                  void run(async () => {
                    await api().controlExtensions('tool-save', {
                      tool_id: tool.id,
                      enabled: tool.enabled,
                      risk_level: e.target.value,
                    });
                    setTools((await api().readExtensions('tools', { server_id: id })).items);
                  })
                }
              >
                {risks.map((risk) => (
                  <option key={risk} value={risk}>
                    {labels[risk]}
                  </option>
                ))}
              </select>
              <details>
                <summary>Schema 与来源</summary>
                <pre>
                  {JSON.stringify(
                    {
                      server_id: tool.server_id,
                      identity: tool.id,
                      fingerprint: tool.schema_fingerprint,
                      input: tool.input_schema,
                      output: tool.output_schema,
                    },
                    null,
                    2,
                  )}
                </pre>
              </details>
            </article>
          ))}
          {tools.length > 20 && (
            <div className="extension-actions">
              <button disabled={page === 0} onClick={() => setPage(page - 1)}>
                上一页
              </button>
              <span>
                {page + 1} / {Math.ceil(tools.length / 20)}
              </span>
              <button disabled={(page + 1) * 20 >= tools.length} onClick={() => setPage(page + 1)}>
                下一页
              </button>
            </div>
          )}
          <details>
            <summary>Resources / Prompts</summary>
            <button
              disabled={busy}
              onClick={() =>
                void run(async () =>
                  setPreview(
                    JSON.stringify(
                      await api().readExtensions('catalog', { server_id: id }),
                      null,
                      2,
                    ),
                  ),
                )
              }
            >
              查看能力和模板列表
            </button>
            <label>
              类型
              <select
                value={kind}
                onChange={(e) => setKind(e.target.value as 'resource' | 'prompt')}
              >
                <option value="resource">资源 URI</option>
                <option value="prompt">Prompt 模板</option>
              </select>
            </label>
            <label>
              列表中的名称或 URI
              <input
                aria-label="资源 URI 或 Prompt 名称"
                value={promptName}
                onChange={(e) => setPromptName(e.target.value)}
              />
            </label>
            <label>
              模板参数（JSON 字符串值对象）
              <textarea
                aria-label="Prompt 参数"
                value={promptArgs}
                onChange={(e) => setPromptArgs(e.target.value)}
              />
            </label>
            <button
              disabled={busy}
              onClick={() =>
                void run(
                  async () =>
                    setPreview(
                      JSON.stringify(
                        await api().controlExtensions('preview', {
                          server_id: id,
                          kind,
                          name: promptName,
                          arguments: JSON.parse(promptArgs),
                        }),
                        null,
                        2,
                      ),
                    ),
                  '低信任预览已显示；未执行工具',
                )
              }
            >
              授权读取／预览
            </button>
          </details>
          <details>
            <summary>最近调用与连接诊断</summary>
            <pre>{JSON.stringify(selected?.recent_calls || [], null, 2)}</pre>
          </details>
        </>
      )}
      {preview && (
        <>
          <pre aria-label="扩展预览">{preview.slice(0, 64000)}</pre>
          <button
            onClick={() =>
              window.dispatchEvent(
                new CustomEvent('companion-extension-preview', {
                  detail: { text: preview.slice(0, 3000) },
                }),
              )
            }
          >
            将预览片段放入输入框（最多 3000 字）
          </button>
        </>
      )}
    </div>
  );
}

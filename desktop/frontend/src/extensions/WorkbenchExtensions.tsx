import { useState } from 'react';
import type { AgentCall, WorkSession } from '../api';
import { api } from './types';

export default function WorkbenchExtensions({
  session,
  conversationId,
  calls,
}: {
  session: WorkSession | null;
  conversationId: string | null;
  calls: AgentCall[];
}) {
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const selected = session?.events.filter((e) => e.type === 'skills_selected').at(-1)?.detail;
  const external = calls.filter((call) => call.name.startsWith('mcp.'));
  if (!selected && !external.length)
    return (
      <details className="work-extension-summary">
        <summary>MCP 与 Skills</summary>
        <p>本任务尚未选择扩展。可在「设置 → MCP & Skills」手动连接和审阅工具。</p>
      </details>
    );
  async function grant(call: AgentCall, scope: 'task' | 'session' | 'revoke') {
    if (!session || !conversationId || busy) return;
    setBusy(true);
    try {
      await api().controlExtensions('grant', {
        conversation_id: conversationId,
        work_session_id: session.id,
        call_id: call.id,
        scope,
      });
      setNotice(
        scope === 'revoke'
          ? '授权已撤销'
          : '已授权所示工具与完全相同的参数；高风险操作仍需单次确认',
      );
    } catch (e) {
      setNotice(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="work-extension-summary" open>
      <summary>本任务 MCP 与 Skills</summary>
      {selected && <p>{String(selected.summary || '已选 Skill')}</p>}
      {external.map((call) => (
        <article key={call.id}>
          <strong>{call.original_tool_name || call.name}</strong>
          <p>服务器 {call.server_id}</p>
          <details>
            <summary>本次参数与作用范围</summary>
            <pre>{JSON.stringify(call.arguments, null, 2)}</pre>
          </details>
          <p>默认单次确认；会话／任务授权仅适用于经审阅的只读工具，并绑定相同参数。</p>
          <button disabled={busy} onClick={() => void grant(call, 'task')}>
            仅本任务授权
          </button>
          <button disabled={busy} onClick={() => void grant(call, 'session')}>
            本会话相同参数授权
          </button>
          <button disabled={busy} onClick={() => void grant(call, 'revoke')}>
            撤销扩展授权
          </button>
        </article>
      ))}
      <p role="status">{notice}</p>
      <p>调用记录见协作流与 Trace；外部协议成功不等于目标已经核验。</p>
    </details>
  );
}

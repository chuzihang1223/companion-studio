import { useEffect, useState } from 'react';
import type { Memory, Owner, Scope } from './types';

export default function MemoryPanel({
  owner,
  workSessionId,
}: {
  owner: Owner;
  workSessionId?: string;
}) {
  const [scope, setScope] = useState<Scope>('CONVERSATION');
  const { conversation_id, character_id } = owner;
  const [rows, setRows] = useState<Memory[]>([]);
  const [content, setContent] = useState(''),
    [category, setCategory] = useState<Memory['category']>('fact');
  const [editing, setEditing] = useState<Memory | null>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  const ownerId =
    scope === 'CONVERSATION'
      ? owner.conversation_id
      : scope === 'WORKSPACE'
        ? workSessionId
        : owner.character_id;
  // Editing the text must preserve an explicit disable, including a toggle
  // performed while this editor is open.
  const editingEnabled = editing
    ? (rows.find((row) => row.memory_id === editing.memory_id)?.enabled ?? editing.enabled)
    : true;
  async function load() {
    if (ownerId)
      setRows(
        (await window.desktopAPI!.readKnowledge('memories', { ...owner, scope, owner_id: ownerId }))
          .memories,
      );
  }
  useEffect(() => {
    let live = true;
    setEditing(null);
    setContent('');
    setRows([]);
    if (ownerId)
      void window.desktopAPI
        ?.readKnowledge('memories', { conversation_id, character_id, scope, owner_id: ownerId })
        .then((value) => {
          if (live) setRows(value.memories);
        })
        .catch((cause) => {
          if (live) setError(String(cause));
        });
    return () => {
      live = false;
    };
  }, [conversation_id, character_id, scope, ownerId]);
  async function action(run: () => Promise<unknown>) {
    setBusy(true);
    setError('');
    try {
      await run();
      await load();
    } catch (cause) {
      setError(String(cause));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section aria-label="长期记忆" className="knowledge-memory">
      <h3>可控长期记忆</h3>
      <p>只保存你主动填写的内容，不自动提取全部私人聊天。请勿存放密钥和密码。</p>
      <label>
        记忆范围{' '}
        <select value={scope} onChange={(event) => setScope(event.target.value as Scope)}>
          <option value="CONVERSATION">当前会话</option>
          <option value="CHARACTER">当前角色</option>
          {owner.character_id === 'assistant' && (
            <option value="GLOBAL_ASSISTANT">通用助手全局</option>
          )}
          {workSessionId && <option value="WORKSPACE">当前工作区会话</option>}
        </select>
      </label>
      {error && <p role="alert">{error}</p>}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void action(async () => {
            await window.desktopAPI!.controlKnowledge('memory-save', {
              ...owner,
              memory_id: editing?.memory_id,
              memory: {
                scope,
                owner_id: ownerId,
                content,
                category,
                enabled: editingEnabled,
                expires_at: editing?.expires_at || null,
              },
            });
            setEditing(null);
            setContent('');
          });
        }}
      >
        <textarea
          aria-label="记忆内容"
          value={content}
          onChange={(event) => setContent(event.target.value)}
          maxLength={500}
          placeholder="例如：我的项目使用 Python，说明文档请写中文。"
        />
        <select
          aria-label="记忆类别"
          value={category}
          onChange={(event) => setCategory(event.target.value as Memory['category'])}
        >
          <option value="fact">事实</option>
          <option value="preference">偏好</option>
          <option value="task">任务事实</option>
        </select>
        <button disabled={busy || !content.trim() || !ownerId}>
          {editing ? '保存修改' : '明确保存记忆'}
        </button>
        {editing && (
          <button
            type="button"
            onClick={() => {
              setEditing(null);
              setContent('');
            }}
          >
            取消编辑
          </button>
        )}
      </form>
      {rows.map((row) => (
        <article key={row.memory_id}>
          <p>{row.content}</p>
          <small>
            {row.category} · {row.enabled ? '启用' : '停用'}
            {row.expires_at ? ` · 到期 ${row.expires_at}` : ''}
          </small>
          <div className="knowledge-actions">
            <button
              disabled={busy}
              onClick={() => {
                setEditing(row);
                setContent(row.content);
                setCategory(row.category);
              }}
            >
              编辑
            </button>
            <button
              disabled={busy}
              onClick={() =>
                void action(() =>
                  window.desktopAPI!.controlKnowledge('memory-save', {
                    ...owner,
                    memory_id: row.memory_id,
                    memory: {
                      scope: row.scope,
                      owner_id: row.owner_id,
                      content: row.content,
                      category: row.category,
                      enabled: !row.enabled,
                      expires_at: row.expires_at,
                    },
                  }),
                )
              }
            >
              {row.enabled ? '停用' : '启用'}
            </button>
            <button
              disabled={busy}
              onClick={() =>
                void action(() =>
                  window.desktopAPI!.controlKnowledge('memory-remove', {
                    ...owner,
                    scope,
                    owner_id: ownerId,
                    memory_id: row.memory_id,
                  }),
                )
              }
            >
              删除
            </button>
          </div>
        </article>
      ))}
      {!rows.length && <p>此范围尚无手动记忆。</p>}
      <button
        disabled={busy || !rows.length}
        onClick={() =>
          void action(() =>
            window.desktopAPI!.controlKnowledge('memory-remove', {
              ...owner,
              scope,
              owner_id: ownerId,
            }),
          )
        }
      >
        清除此范围记忆
      </button>
    </section>
  );
}

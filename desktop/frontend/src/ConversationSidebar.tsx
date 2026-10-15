import { useState } from 'react';
import type { Conversation } from './api';
type Props = {
  rows: Conversation[];
  active: string | null;
  running: string | string[] | null;
  completed?: Record<string, string>;
  onOpen: (id: string) => void;
  onManage: (row: Conversation, action: 'rename' | 'branch' | 'delete') => void;
};
export default function ConversationSidebar({
  rows,
  active,
  running,
  completed = {},
  onOpen,
  onManage,
}: Props) {
  const [folded, setFolded] = useState<Record<string, boolean>>(() => {
    try {
      return JSON.parse(localStorage.getItem('conversation-folds') || '{}');
    } catch {
      return {};
    }
  });
  const [limits, setLimits] = useState<Record<string, number>>({});
  const [menu, setMenu] = useState<string | null>(null);
  const groups = [
    ['recent', '最近使用'],
    ['project', '项目'],
    ['work', '工作'],
    ['chat', '聊天'],
  ];
  const toggle = (key: string) =>
    setFolded((old) => {
      const next = { ...old, [key]: !old[key] };
      localStorage.setItem('conversation-folds', JSON.stringify(next));
      return next;
    });
  return (
    <nav className="chat-list" aria-label="会话列表">
      {groups.map(([key, label]) => {
        const items =
          key === 'recent' ? rows.slice(0, 8) : rows.filter((r) => r.conversation_type === key);
        const limit = limits[key] || 6;
        return (
          <section className="conversation-group" key={key}>
            <button
              className="group-heading"
              onClick={() => toggle(key)}
              aria-expanded={!folded[key]}
            >
              {folded[key] ? '▸' : '▾'} {label}
              <small>{items.length}</small>
            </button>
            {!folded[key] && (
              <>
                {items.slice(0, limit).map((row) => (
                  <div
                    className={`chat-list-row ${row.id === active ? 'selected' : ''}`}
                    key={row.id}
                    data-conversation-id={row.id}
                  >
                    <button
                      className="chat-list-title"
                      onClick={() => onOpen(row.id)}
                      title={row.title}
                    >
                      <span className="conversation-kind">
                        {row.conversation_type === 'chat'
                          ? '聊'
                          : row.conversation_type === 'project'
                            ? '项'
                            : '工'}
                      </span>
                      {row.title}
                      {(Array.isArray(running) ? running.includes(row.id) : running === row.id) && (
                        <small className="running-badge">进行中</small>
                      )}
                      {completed[row.id] && (
                        <small className="running-badge">{completed[row.id]}</small>
                      )}
                    </button>
                    <button
                      className="chat-list-more"
                      aria-label={`管理${row.title}`}
                      onClick={() =>
                        setMenu(menu === `${key}:${row.id}` ? null : `${key}:${row.id}`)
                      }
                    >
                      ···
                    </button>
                    {menu === `${key}:${row.id}` && (
                      <div className="chat-context">
                        {(
                          [
                            ['rename', '重命名'],
                            ['branch', '新分支'],
                            ['delete', '删除会话'],
                          ] as const
                        ).map(([action, text]) => (
                          <button
                            key={action}
                            onClick={() => {
                              setMenu(null);
                              onManage(row, action);
                            }}
                          >
                            {text}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
                {!items.length && <p className="group-empty">暂无记录</p>}
                {items.length > limit && (
                  <button
                    className="group-more"
                    onClick={() => setLimits((old) => ({ ...old, [key]: limit + 10 }))}
                  >
                    查看更多
                  </button>
                )}
              </>
            )}
          </section>
        );
      })}
    </nav>
  );
}

import { useEffect, useState } from 'react';
import type { Citation, Owner } from './types';
import './knowledge.css';

export function locationLabel(row: Citation) {
  const at = row.location || {};
  return at.page
    ? `第 ${at.page} 页`
    : at.paragraph
      ? `第 ${at.paragraph} 段`
      : at.line_start
        ? `行 ${at.line_start}–${at.line_end || at.line_start}`
        : '';
}
export function Sources({ rows, owner }: { rows: Citation[]; owner: Owner }) {
  const [error, setError] = useState('');
  return (
    <div className="knowledge-sources">
      {error && <p role="alert">{error}</p>}
      {rows.map((row) => (
        <details key={row.chunk_id} data-knowledge-source={row.chunk_id}>
          <summary>
            {row.relative_path} · {locationLabel(row)} ·{' '}
            {row.verification === 'source_hash_verified'
              ? '检索时来源已核验'
              : '来源已变化或授权已撤销'}
          </summary>
          {row.excerpt && <pre>{row.excerpt}</pre>}
          {row.verification === 'source_hash_verified' && (
            <button
              onClick={() =>
                void window.desktopAPI
                  ?.controlKnowledge('reveal-source', { ...owner, reference: row })
                  .catch((cause) => setError(String(cause)))
              }
            >
              在文件夹中定位
            </button>
          )}
        </details>
      ))}
    </div>
  );
}

export default function MessageSources({ messageId, owner }: { messageId: string; owner: Owner }) {
  const [rows, setRows] = useState<Citation[]>([]);
  const { conversation_id, character_id } = owner;
  useEffect(() => {
    let live = true;
    if (!window.desktopAPI || !/^[a-f0-9-]{36}$/i.test(messageId)) return;
    void window.desktopAPI
      .readKnowledge('evidence', { conversation_id, character_id, message_id: messageId })
      .then((value) => {
        if (live) setRows(value.citations);
      })
      .catch(() => {
        if (live) setRows([]);
      });
    return () => {
      live = false;
    };
  }, [messageId, conversation_id, character_id]);
  return rows.length ? (
    <aside aria-label="回答来源">
      <small>检索来源（来源核验不等于回答的全部事实已获验证）</small>
      <Sources rows={rows} owner={owner} />
    </aside>
  ) : null;
}

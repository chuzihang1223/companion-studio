import { useEffect, useState } from 'react';
import type { Reads } from './types';

export default function DocumentList({
  collectionId,
  indexVersion,
}: {
  collectionId: string;
  indexVersion: number;
}) {
  const [open, setOpen] = useState(false),
    [cursor, setCursor] = useState(0),
    [data, setData] = useState<Reads['documents'] | null>(null),
    [error, setError] = useState('');
  useEffect(() => {
    setCursor(0);
  }, [collectionId, indexVersion]);
  useEffect(() => {
    let live = true;
    if (open)
      void window.desktopAPI
        ?.readKnowledge('documents', { collection_id: collectionId, cursor })
        .then((result) => {
          if (live) {
            setData(result);
            setError('');
          }
        })
        .catch((cause) => {
          if (live) setError(String(cause));
        });
    return () => {
      live = false;
    };
  }, [open, collectionId, cursor, indexVersion]);
  const labels: Record<string, string> = {
    INDEXED: '已索引',
    UNCHANGED: '未变',
    ERROR: '失败，旧证据已失效',
    DELETED: '源文件已删除',
    NEW: '待索引',
  };
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>文档与解析错误</summary>
      {error && <p role="alert">{error}</p>}
      {data?.documents.map((row) => (
        <div key={row.id}>
          <strong>{row.relative_path}</strong>
          <p>
            {labels[row.status] || row.status} · {row.size} 字节 · 版本 {row.version}
            {row.last_error ? ` · ${row.last_error}` : ''}
          </p>
        </div>
      ))}
      {data && !data.documents.length && <p>尚无文档，请授权目录并索引。</p>}
      <div className="knowledge-actions">
        <button disabled={!cursor} onClick={() => setCursor(Math.max(0, cursor - 100))}>
          上一页
        </button>
        <button
          disabled={data?.next_cursor == null}
          onClick={() => setCursor(data?.next_cursor || 0)}
        >
          下一页
        </button>
      </div>
    </details>
  );
}

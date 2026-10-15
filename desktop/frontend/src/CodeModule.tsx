import type { RoutedOutput } from './AgentOutputRouter';
import { useEffect, useState } from 'react';
import CopyCode from './CopyCode';
export type VirtualCodeDocument = {
  id: string;
  session_id: string;
  title: string;
  language: string;
  content: string;
  source: string;
  related_file: string | null;
  dirty: boolean;
  status: string;
  created_at: string;
  updated_at: string;
};
type Props = {
  outputs: RoutedOutput[];
  sessionId?: string;
  root?: string | null;
  onError?: (text: string) => void;
  onStatus?: (text: string) => void;
};
function CodeFile({
  row,
  sessionId,
  root,
  onError,
  onStatus,
}: { row: RoutedOutput } & Omit<Props, 'outputs'>) {
  const [content, setContent] = useState<string | null>(null),
    [document, setDocument] = useState<VirtualCodeDocument | null>(null),
    [editing, setEditing] = useState(false),
    [busy, setBusy] = useState(false);
  const id = String(row.data.document_id || '');
  useEffect(() => {
    let live = true;
    const file = String(row.data.path || row.title).replace(/\\/g, '/'),
      base = root?.replace(/\\/g, '/').replace(/\/$/, '') || '',
      path = file.toLowerCase().startsWith(base.toLowerCase() + '/')
        ? file.slice(base.length + 1)
        : file;
    if (sessionId && id)
      void window.desktopAPI
        ?.getVirtualDocument(sessionId, id)
        .then((value) => {
          if (live) {
            setDocument(value);
            setContent(value.content);
          }
        })
        .catch((e) => onError?.(String(e)));
    else if (sessionId && root && !row.data.content && !row.data.code && row.type === 'code')
      void window.desktopAPI
        ?.workFiles(sessionId, { path, read: true })
        .then((result) => {
          if (live && typeof result.content === 'string') setContent(result.content);
        })
        .catch((e) => onError?.(String(e)));
    return () => {
      live = false;
    };
  }, [sessionId, root, row.id, row.data.diff, row.data.status, id]);
  async function save(mode: 'save' | 'saveAs' | 'apply') {
    if (!sessionId || !id) return;
    setBusy(true);
    try {
      const result = await window.desktopAPI?.saveVirtualDocument(sessionId, id, mode);
      if (result && !result.cancelled) {
        setDocument(await window.desktopAPI!.getVirtualDocument(sessionId, id));
        onStatus?.('已保存到 ' + result.path);
      }
    } catch (e) {
      onError?.(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function keepDraft() {
    if (!sessionId || !id) return;
    setBusy(true);
    try {
      await window.desktopAPI?.editVirtualDocument(sessionId, id, content || '');
      setDocument(await window.desktopAPI!.getVirtualDocument(sessionId, id));
      setEditing(false);
      onStatus?.('草稿已保存，项目文件未修改');
    } catch (e) {
      onError?.(String(e));
    } finally {
      setBusy(false);
    }
  }
  const virtual = !!id || row.data.virtual || !!row.data.content || !!row.data.code,
    badge = virtual
      ? document?.status === 'applied'
        ? '已保存'
        : document?.status === 'discarded'
          ? '已放弃 · 草稿'
          : '草稿 · 未保存'
      : '本地文件';
  return (
    <article data-document-id={id || undefined}>
      <div className="virtual-document-bar">
        <span className="work-file-path">{String(row.data.path || row.title)}</span>
        <span className="draft-badge">{badge}</span>
      </div>
      {id && (
        <div className="virtual-document-actions">
          <button type="button" disabled={busy} onClick={() => void save('save')}>
            保存到项目
          </button>
          <button type="button" disabled={busy} onClick={() => void save('saveAs')}>
            另存为
          </button>
          {document?.related_file && (
            <button type="button" disabled={busy} onClick={() => void save('apply')}>
              应用到源文件
            </button>
          )}
          <button
            type="button"
            disabled={busy}
            onClick={() => (editing ? void keepDraft() : setEditing(true))}
          >
            {editing ? '保存草稿' : '编辑草稿'}
          </button>
        </div>
      )}
      {editing ? (
        <textarea
          className="virtual-document-editor"
          aria-label="代码草稿内容"
          value={content || ''}
          maxLength={100000}
          onChange={(e) => setContent(e.target.value)}
        />
      ) : (
        <pre>
          <code>
            {String(
              row.data.content ||
                row.data.code ||
                content ||
                row.data.summary ||
                '选择一个文件或让助手生成代码草稿。',
            )}
          </code>
        </pre>
      )}
      <CopyCode text={String(content ?? row.data.content ?? row.data.code ?? '')} />
    </article>
  );
}
export default function CodeModule({ outputs, ...props }: Props) {
  return (
    <div className="code-module">
      {outputs.length ? (
        outputs.map((row) => <CodeFile key={row.id} row={row} {...props} />)
      ) : (
        <p className="work-empty">代码草稿会显示在这里。保存到项目前不会修改本地文件。</p>
      )}
    </div>
  );
}

import { useState } from 'react';
import type { Collection } from './types';

export default function EmbeddingPanel({
  collection,
  onChanged,
}: {
  collection: Collection;
  onChanged: () => Promise<void>;
}) {
  const [mode, setMode] = useState('DISABLED'),
    [endpoint, setEndpoint] = useState('http://127.0.0.1:8080/v1');
  const [model, setModel] = useState(''),
    [revision, setRevision] = useState(''),
    [dimension, setDimension] = useState(384);
  const [busy, setBusy] = useState(false),
    [status, setStatus] = useState('');
  async function save() {
    setBusy(true);
    setStatus('');
    try {
      await window.desktopAPI!.controlKnowledge('embedding', {
        collection_id: collection.id,
        revision: collection.revision,
        embedding: { mode, endpoint, model, dimension, revision_name: revision },
      });
      setStatus('已保存，未发送请求。启用后请重建索引；重启后需要再次授权。');
      await onChanged();
    } catch (cause) {
      setStatus(String(cause));
    } finally {
      setBusy(false);
    }
  }
  return (
    <details className="knowledge-embedding">
      <summary>可选 Embedding（默认关闭）</summary>
      <p>
        当前记录模式：{collection.config.embedding_mode}。本程序不安装模型。使用你自行准备的
        OpenAI-compatible Embedding 服务；本地模式只允许回环地址。远程密钥只从后端 .env 的
        KNOWLEDGE_EMBEDDING_API_KEY 读取。
      </p>
      <p>
        模型名称、修订号或维度变化会清除旧向量。向量检索最多扫描 10,000
        条；未就绪会明确回退到关键词。开启服务后，索引和检索会向该服务发送文本；远程服务可能计费。
      </p>
      <label>
        模式{' '}
        <select
          aria-label="Embedding模式"
          value={mode}
          onChange={(event) => setMode(event.target.value)}
        >
          <option value="DISABLED">关闭</option>
          <option value="LOCAL">本地服务</option>
          <option value="REMOTE">远程服务（另外确认）</option>
        </select>
      </label>
      {mode !== 'DISABLED' && (
        <>
          <label>
            服务地址{' '}
            <input
              aria-label="Embedding地址"
              value={endpoint}
              maxLength={500}
              onChange={(event) => setEndpoint(event.target.value)}
            />
          </label>
          <label>
            模型{' '}
            <input
              aria-label="Embedding模型"
              value={model}
              maxLength={100}
              onChange={(event) => setModel(event.target.value)}
            />
          </label>
          <label>
            修订号{' '}
            <input
              aria-label="Embedding修订号"
              value={revision}
              maxLength={100}
              onChange={(event) => setRevision(event.target.value)}
            />
          </label>
          <label>
            维度{' '}
            <input
              aria-label="Embedding维度"
              type="number"
              min={1}
              max={2048}
              value={dimension}
              onChange={(event) => setDimension(Number(event.target.value))}
            />
          </label>
        </>
      )}
      <button
        disabled={busy || (mode !== 'DISABLED' && (!model.trim() || !revision.trim()))}
        onClick={() => void save()}
      >
        保存 Embedding 配置
      </button>
      {status && <p role="status">{status}</p>}
    </details>
  );
}

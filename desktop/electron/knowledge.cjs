'use strict';

// Fixed operations only. Root selection and transfer consent stay in main;
// renderer data never becomes an arbitrary backend URL or an executable path.
module.exports = ({ ipcMain, requireMainWindow, request, dialog, shell }) => {
  function object(value, keys) {
    if (
      !value ||
      typeof value !== 'object' ||
      Array.isArray(value) ||
      JSON.stringify(value).length > 30000 ||
      Object.keys(value).some((key) => !keys.includes(key))
    )
      throw Error('知识库参数无效');
  }
  function uuid(value) {
    if (typeof value !== 'string' || !/^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i.test(value))
      throw Error('知识库编号无效');
    return value;
  }
  function revision(value) {
    if (!Number.isSafeInteger(value) || value < 1) throw Error('知识库版本无效');
    return value;
  }
  function owner(value) {
    if (
      typeof value.character_id !== 'string' ||
      !value.character_id ||
      value.character_id.length > 100
    )
      throw Error('请选择角色和会话');
    return { conversation_id: uuid(value.conversation_id), character_id: value.character_id };
  }
  async function confirm(event, message, detail) {
    requireMainWindow(event);
    const choice = await dialog.showMessageBox({
      type: 'warning',
      title: '知识与记忆权限',
      message,
      detail,
      buttons: ['取消', '确认'],
      defaultId: 0,
      cancelId: 0,
      noLink: true,
    });
    requireMainWindow(event);
    if (choice.response !== 1) throw Error('已取消，未更改授权');
  }
  ipcMain.handle('knowledge-read', (event, operation, options = {}) => {
    requireMainWindow(event);
    object(options, [
      'collection_id',
      'conversation_id',
      'character_id',
      'message_id',
      'cursor',
      'scope',
      'owner_id',
    ]);
    if (['collections', 'targets'].includes(operation))
      return request('/api/knowledge/' + operation);
    if (operation === 'jobs')
      return request('/api/knowledge/collections/' + uuid(options.collection_id) + '/jobs');
    const query = new URLSearchParams(owner(options));
    if (operation === 'selection') return request('/api/knowledge/selection?' + query);
    if (operation === 'evidence')
      return request('/api/knowledge/evidence/' + uuid(options.message_id) + '?' + query);
    if (operation === 'memories') {
      if (
        !['GLOBAL_ASSISTANT', 'CHARACTER', 'CONVERSATION', 'WORKSPACE'].includes(options.scope) ||
        typeof options.owner_id !== 'string' ||
        options.owner_id.length > 100
      )
        throw Error('记忆范围无效');
      query.set('scope', options.scope);
      query.set('owner_id', options.owner_id);
      return request('/api/knowledge/memories?' + query);
    }
    throw Error('知识库读取操作无效');
  });
  ipcMain.handle('knowledge-control', async (event, operation, options = {}) => {
    requireMainWindow(event);
    object(options, [
      'collection_id',
      'revision',
      'embedding',
      'payload',
      'root_id',
      'full',
      'job_id',
      'action',
      'conversation_id',
      'character_id',
      'collection_ids',
      'allow_model_transfer',
      'rag_enabled',
      'memory_enabled',
      'search',
      'reference',
      'memory',
      'memory_id',
      'scope',
      'owner_id',
    ]);
    if (operation === 'create') return request('/api/knowledge/collections', options.payload);
    if (operation === 'update')
      return request(
        '/api/knowledge/collections/' + uuid(options.collection_id),
        { revision: revision(options.revision), payload: options.payload },
        'PATCH',
      );
    if (operation === 'documents') {
      const cursor = options.cursor || 0;
      if (!Number.isSafeInteger(cursor) || cursor < 0 || cursor > 100000)
        throw Error('文档分页无效');
      return request(
        '/api/knowledge/collections/' + uuid(options.collection_id) + '/documents?cursor=' + cursor,
      );
    }
    if (operation === 'add-root') {
      const selected = await dialog.showOpenDialog({
        title: '仅授权所选目录（不扫描其他位置）',
        properties: ['openDirectory'],
      });
      requireMainWindow(event);
      if (selected.canceled || selected.filePaths.length !== 1) return { cancelled: true };
      await confirm(
        event,
        '允许索引此目录中的文档？',
        selected.filePaths[0] + '\n仅保存本地索引。检索和发送给模型需要另外授权。',
      );
      return request(
        '/api/knowledge/collections/' + uuid(options.collection_id),
        { revision: revision(options.revision), add_root: selected.filePaths[0] },
        'PATCH',
      );
    }
    if (operation === 'remove-root')
      return request(
        '/api/knowledge/collections/' + uuid(options.collection_id),
        { revision: revision(options.revision), remove_root: uuid(options.root_id) },
        'PATCH',
      );
    if (operation === 'delete') {
      await confirm(
        event,
        '删除此知识库的本地索引？',
        '不删除源文件。会撤销会话对这个知识库的授权。',
      );
      return request(
        '/api/knowledge/collections/' +
          uuid(options.collection_id) +
          '?revision=' +
          revision(options.revision),
        {},
        'DELETE',
      );
    }
    if (operation === 'index')
      return request('/api/knowledge/collections/' + uuid(options.collection_id) + '/index', {
        full: options.full === true,
        ...(options.conversation_id ? owner(options) : {}),
      });
    if (operation === 'embedding') {
      object(options.embedding, ['mode', 'endpoint', 'model', 'dimension', 'revision_name']);
      if (!['DISABLED', 'LOCAL', 'REMOTE'].includes(options.embedding.mode))
        throw Error('Embedding 模式无效');
      if (options.embedding.mode !== 'DISABLED')
        await confirm(
          event,
          '启用可选 Embedding 服务？',
          `${options.embedding.endpoint}\n模型：${options.embedding.model} / ${options.embedding.revision_name}\n${options.embedding.mode === 'REMOTE' ? '会将授权目录的文本片段发送到这个服务，可能计费。密钥仅从后端 .env 的 KNOWLEDGE_EMBEDDING_API_KEY 读取。' : '仅允许回环地址；使用你自行运行的服务。本程序不下载模型。'}\n配置修改会清除旧向量，需重建索引；重启后需重新授权。`,
        );
      return request('/api/knowledge/collections/' + uuid(options.collection_id) + '/embedding', {
        ...options.embedding,
        revision: revision(options.revision),
        consent: options.embedding.mode !== 'DISABLED',
      });
    }
    if (operation === 'job-control')
      return request('/api/knowledge/jobs/' + uuid(options.job_id) + '/control', {
        action: options.action,
      });
    const scope = owner(options);
    if (operation === 'selection') {
      if (!Array.isArray(options.collection_ids) || options.collection_ids.length > 10)
        throw Error('知识库选择无效');
      const ids = options.collection_ids.map(uuid);
      let expected = [];
      if (options.allow_model_transfer === true) {
        const { targets } = await request('/api/knowledge/targets');
        await confirm(
          event,
          '允许把检索片段发送给当前模型？',
          '仅限所选知识库与当前会话。以下服务/模型会收到相关片段；可能按服务商规则计费。更换服务地址或模型后必须重新授权。\n' +
            targets.map((t) => `${t.role}: ${t.base_url} / ${t.model}`).join('\n'),
        );
        expected = [...new Set(targets.map((t) => t.fingerprint))];
      }
      return request('/api/knowledge/selection', {
        ...scope,
        collection_ids: ids,
        allow_model_transfer: options.allow_model_transfer === true,
        expected_targets: expected,
      });
    }
    if (operation === 'preferences')
      return request('/api/knowledge/preferences', {
        ...scope,
        rag_enabled: options.rag_enabled,
        memory_enabled: options.memory_enabled,
      });
    if (operation === 'search')
      return request('/api/knowledge/search', { ...scope, search: options.search });
    if (operation === 'reveal-source') {
      const result = await request('/api/knowledge/resolve', {
        ...scope,
        reference: options.reference,
      });
      requireMainWindow(event);
      // Reveal rather than launching source code, document macros or file handlers.
      shell.showItemInFolder(result.open_path);
      return { revealed: true };
    }
    if (operation === 'memory-save')
      return request('/api/knowledge/memories', {
        ...scope,
        memory: options.memory,
        memory_id: options.memory_id || null,
      });
    if (operation === 'memory-remove') {
      await confirm(
        event,
        options.memory_id ? '删除这条记忆？' : '清除此范围的全部手动记忆？',
        '不会删除聊天记录或知识库。此操作不可撤销。',
      );
      return request('/api/knowledge/memories/remove', {
        ...scope,
        scope: options.scope,
        owner_id: options.owner_id,
        memory_id: options.memory_id || null,
      });
    }
    throw Error('知识库控制操作无效');
  });
};

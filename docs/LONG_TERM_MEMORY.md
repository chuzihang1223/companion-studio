# 可控长期记忆

使用原 `manual_memories` 表，增量新增 scope、owner_id、category、source_type、
source_reference、expires_at、status；原 id/content/enabled/时间原样保留。
旧行归入 CHARACTER，owner_id 回填原 character_id，重复迁移不重建或清空表。

| 范围 | owner_id | 规则 |
|---|---|---|
| GLOBAL_ASSISTANT | assistant | 仅中性通用助手使用 |
| CHARACTER | 当前角色 ID | 不跨角色读取，兼容原角色记忆入口 |
| CONVERSATION | 当前会话 ID | 不跨会话 |
| WORKSPACE | 有选定目录的 WorkSession ID | 绑定当前工作会话，不把相同目录视为新的授权 |

侧栏「知识与记忆 → 长期记忆」可主动新增、查看、编辑、启停、删除和清除指定范围。
类别为事实、偏好、任务事实。API 可设置带时区的到期时间；界面显示已设到期时间，
当前不提供到期日期编辑器。删除/清空经原生确认，不删除聊天或知识库。
编辑记忆内容保留当前启停状态，不会把已停用的记录悄悄重新启用。

每条最多 500 字符，每角色最多 1,000 条；查询按范围、启用/有效状态、到期和词汇相关性筛选，
最多六条，聊天与原有手动记忆合并去重后最多八条。会话可以关闭主动保存记忆的使用。
这个开关不删除原角色设定，也不改变既有关系原话回忆的独立逻辑。

不自动把文件变成记忆，不自动提取全部私人对话，不由模型直接写入或偷偷批准记忆。
保存常见密码/Token/私钥模式会被拒绝；检测不是任意秘密识别器，请勿把记忆当密码库。
新的 Memory API 和原角色记忆 API 使用同一校验与状态语义，原界面不能改写其他 scope。

Memory API 位于 `/api/knowledge/memories`，需要主进程控制 Token 和实际会话/角色所有权。
source_type 当前始终 manual，不把模型猜测包装成确认过的事实。

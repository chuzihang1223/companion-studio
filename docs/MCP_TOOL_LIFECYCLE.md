# MCP 工具与连接生命周期

服务器 UUID 保持稳定。状态包括 DISCONNECTED、CONNECTING、CONNECTED、DEGRADED、
AUTH_REQUIRED、RECONNECTING、FAILED、STOPPED。启动只读取元数据，不自动运行程序。
连接由单独协程拥有 SDK 上下文，保持 AnyIO 退出与进入在同一任务；应用关闭集中清理。

建立连接后发现工具，手动刷新、重新连接及每次执行前重新发现。最多 100 工具/Server，
首次分页超限明确拒绝，不声称已经加载后续页。逻辑身份为服务器 UUID 与原工具名组合；
模型名称使用 40 位摘要映射，可反查来源，不覆盖内置工具。

Schema 摘要改变即禁用工具并要求重新启用。计划持有原摘要，参数或身份不再匹配则拒绝。
断开后不自动重放任何工具调用。连接建立最多 3 次、有界退避与抖动；鉴权失败不继续重试。
只读工具在原 Recovery 预算内有限重试，非只读超时归未知副作用。

MCPToolAdapter 规范结果携带 call/server/tool/trace、耗时、结构化结果、错误、证据和副作用类。
最大传输帧/HTTP 交换 1 MiB，普通结果 128 KiB，参数 16 KiB，Schema 24 KiB，深度 10；
最多 8 个文本块，文本上下文上限 8000 字。二进制不自动加载，省略内容标记 incomplete。
候选 MCP 工具最多 8 项、24 KiB，按显式 MCP / 名称与描述相关性确定性筛选。
JSON Schema 不支持外部引用、正则、format；无法安全验证的 Schema 拒绝，不能当成完整方言实现。

Checkpoint 的 execution_intent 记录 server_id、tool_identity、tool_schema_version、call_id、
request_status、side_effect_class、evidence_refs；不存参数原文或结果正文。
提交意图后才执行。恢复前检查当前启用、连接、Schema 与 Skill 摘要，重新审批。
历史只读输出也不作为当前状态证明；未知副作用不得自动恢复。

Task 取消会取消该 SDK 请求，释放其等待；不终止无关服务器或会话。服务器删除先停止其自有
连接/子进程，再删注册和该 Server 的凭据。Skill 删除只移除注册，原目录与不可变快照保留。
模型切换不改变权限或连接；下一次工具选择用当前候选集合。

# Agent Trace & Replay 架构

基线：公开 v0.1.2，main `bb1d1a1482255edd55704fad5964c691128e4077`。本功能在 `feature/agent-trace-replay` 独立开发；审核构建与正式用户档案分开。

## 1. 已有调用链（源码审计）

`ConversationSubmissionService → api.ts → /api/agent/plan → AgentService.plan` 先校验角色/会话，准备 Prompt 和附件，解析确定性动作或调用 `ModelRoleRouter.plan_tools`，校验工具，保存用户消息，最后才生成 WorkSession 和保存计划。规划发生在 RuntimeTaskRegistry 的执行门之外。

`/api/agent/execute[/stream] → AgentService.execute → RuntimeTaskRegistry.run(plan_id, conversation_id, work_session_id) → AgentTaskRuntime.run → 权限检查 → execute_call/step → ToolExecutor 或 DesktopBridge → Electron DesktopAppAgent → 观察结果 → GoalVerifier → 最终回复 → 持久消息及完成事件`。

`plan_id` 是一次执行的 Task ID；一个 WorkSession 对应这次计划的工作状态，同一 Conversation 可以有多个历史 WorkSession。执行前，WorkState 的事件 envelope 会用 WorkSession ID 回退 Task ID，不能把这个回退当作真实 plan_id。Trace 在规划入口预分配同样的计划 UUID，之后绑定实际会话和 WorkSession；不改变权限、执行顺序或工具决策。

## 2. 原事件与缺口

`WorkState.emit` 同步提交 `work_events`，通知所属工作会话的有界订阅队列；`stream` 使用 WorkEvent.id 恢复查询，每批最多 100 条。快照只包含最近 100 条，协作流显示最近 30 条。不是所有事件都有耗时、调用 ID 或验证证据。原事件可能含文件路径/内容，不能直接作为新的脱敏 Trace API 输出。

可复用的事件包括 workflow_updated、plan_updated、permission_requested/resolved、tool_started/completed、step_started、verification、goal_verification、retrying、user_interruption、task_pause/resume/takeover/cancel、task_completed/failed/cancelled/interrupted 和 work_interrupted/stopped。保留这些原始行为和顺序。

缺口：规划期间还没有 WorkSession；模型调用没有独立跨度；调度等待与模型时间未分开；普通工具完成不能证明外部目标已经验证；重试和恢复只有部分事件；重启不会恢复协程或自动重放工具。

## 3. 存储设计

新增三个表，沿用现有 SQLAlchemy Base/SQLite，通过 create_all 作可重复执行的增量迁移，不重建旧表：

- `agent_traces`：唯一 trace_id/task_id、conversation/work_session、状态、UTC 起止、单调计时耗时、schema_version、记录不完整标志。
- `trace_spans`：任务/规划/模型/工具/操作/验证/调度/授权等待跨度，parent_span_id、调用名、状态、耗时及脱敏摘要。
- `trace_events`：按轨迹递增序号、稳定事件枚举、span_id、阶段及脱敏数据；来源于 WorkEvent 时保留 source_work_event_id。按 trace/sequence、conversation/time、session/time、status/time 建索引。

Trace 是原 WorkEvent 的持久投影与补充跨度，不是第二套任务调度或可执行事件总线。规划期无 WorkSession 的事件也能保存。绑定后继续从现有 WorkState 采集，实时失效通知复用其订阅队列；Trace 通知不调用 registry.event，不改变 Watchdog 的进度或等待原因。

采用有界内存队列、批量事务及后台线程写 SQLite。Trace 连接使用短 busy timeout；存储、队列或通知失败只影响追踪完整性，不抛入原 Agent 路径。记录粒度是阶段/模型请求/工具/验证，不记录 token。默认限制历史天数、轨迹数、单轨迹事件数和内存队列；清理只删除 Trace 三表，保护原聊天、工作事件及工具结果。

## 4. 时间、隐私和证据

UTC 用于显示和筛选，monotonic 用于本进程跨度。授权点击等待、调度等待、模型请求、工具及恢复分别统计，不能把授权等待算成模型推理。进程重启无法恢复单调时钟，未结束跨度标中断/计时不完整，不用离线墙钟时间伪造运行耗时。

只记录受控标识符、枚举、布尔值、计数和参数类型摘要。默认丢弃用户原话、Prompt、Header、路径、文件内容、窗口标题、URL、命令文本、截图和环境变量；异常记录受控类别和错误码，使用通用原因说明。模型名经过格式/敏感信息检查。读取和导出再次脱敏，原 WorkEvent.detail_json 不对新 API 透传。

工具请求、工具执行完成、目标验证分别呈现。GoalVerifier 是既有确定性结果规则；规则通过而没有外部证据时明确显示缺失证据，不声称看到了截图、网页内容或完整语义目标。统计是服务层逻辑模型调用，不推测供应商内部重试、计费 token 或费用。

## 5. API 与所有权

`GET /api/agent/traces`、`/{trace_id}`、`/{trace_id}/events`、`/{trace_id}/summary` 提供筛选、稳定 DTO 及有界分页；额外的 DELETE 接口只管理终态 Trace 数据。Trace ID 和请求会话采用 UUID 校验，读取要求 conversation_id，并核对 WorkSession/Conversation 的真实关联及删除状态。

所有 Trace 接口使用现有 X-Companion-Control-Token。Electron 的固定 IPC 代理经 requireMainWindow 验证后向后端发送 Token；前端及嵌入网页不会得到 Token，也不能构造任意代理路径。用户选择的历史任务仍必须属于请求会话，其他应用档案使用不同后端/Token。

## 6. Workbench 和只读 Replay

在 WorkspaceModuleRegistry 注册 `trace`，通过既有 Dock、布局、拖动、缩放和持久化打开；不新建浮动窗口，不修改布局引擎算法、版本、坐标、阈值、rAF 或自动滚动顺序。

Viewer 提供任务/会话/状态/时间/工具筛选、分页时间轴、独立详情、摘要和缺失证据提示。会话切换使旧请求失效，不能显示另一会话的迟到结果。虚拟列表限制 DOM 数量，分页限制内存，较大 JSON 默认折叠并限制长度。

Replay 是独立前端只读状态机：开始、暂停、继续、跳转及速度只改变可见游标和计时器。所用网络能力只有已授权的历史 GET；不会调用 execute、桌面操作接口、文件写入、模型或语音。历史缺口原样显示，不补跑工具或伪造证据。导出或删除只有用户显式点击对应按钮才发生。

## 7. 验证与交付边界

分别测试迁移/旧会话、跨度和事件顺序、并发归属、脱敏、写入失败隔离、取消/超时/重启、分页/权限、只读回放、会话切换及 1000 条合成事件。端到端使用可控本机模型/工具，保留原几何、缩放、自动滚动、聊天和语音回归。性能报告区分数据库写入/查询、SSE 通知、实际面板渲染及内存，不将纯 CPU 时间当作 renderer FPS。

最终报告记录实际结果、版本/分支/提交、安装包和 SHA256、安装版验证及人工边界。公开 v0.1.2、main 与正式档案保持独立；本阶段不推送分支、建 tag 或发布新 Release。

## 8. rc.3 持久化与恢复边界

原内存队列不能恢复在规划期尚无 WorkEvent 的事件；单纯重试派生投影不足以保护这部分来源。现在入队返回 `True` 前，已脱敏的命令先提交到数据库旁的私有 `chat.db.trace-pending/pending.sqlite3`。该独立 SQLite 使用 WAL/FULL；日志事务、主库写事务与日志确认严格分开，不跨库持锁。它不保存原始聊天或可执行工具参数，也不重放任何外部副作用。

主库新增 `trace_projection_receipts` 和 `trace_projection_totals`。命令 UUID、流 UUID、顺序、正文摘要与归属用于原子投影及重复确认；提交成功但确认未知时可安全重读回执。原 Trace 表、事件 ID 和 API 权限保持兼容。已提交历史按原保留策略清理，同时记录清理数量用于对账；回执至少保留 48 小时，并设 50 万条上限。

命令经历 QUEUED、PERSISTING、COMMITTED，或明确留在 RETRY_PENDING、FAILED、NEEDS_REVIEW。`flush()` 只有在内存排空且没有未提交来源时才返回成功。原单轨迹显示上限仍生效，超限来源保留为 FAILED，不将其冒充已保存事件。写入失败计数在重开后仍可核对。Viewer 只向已授权会话显示本会话的未完成数量，不公开待处理正文或其他会话信息。

同文件业务 Engine 和 Trace Engine 的 Session 写事务经过进程内有界 FIFO 协调；只读不获取写租约，租约在根事务实际结束后释放。它不协调外部进程或未经 Session 的启动 DDL。Trace 保留原 2 秒／16 次上限，自有连接池等待为 0.1 秒；测试或嵌入方传入的 factory 必须自行限制其连接池等待。没有提高业务数据库的全局超时。

日志最多与内存队列相同的待处理数量（8～8192）、16 MiB 待处理正文、32 KiB 单条、32 MiB 数据页，WAL 达 28 MiB 时要求截断检查点，否则停止接受。每次 SQL 操作可能使 WAL 超过阈值一个有界事务；不能将该阈值描述为整个目录的精确字节上限。已确认日志最多保留约 65536 条回执；超过 24 小时的未提交来源转 NEEDS_REVIEW，不自动删除以腾出空间。磁盘满或容量限制会显式拒绝新命令。日志目录使用当前用户与 SYSTEM 私有 ACL；首次打开前检查数据库及侧文件的链接边界，Windows 使用标准锁定的长路径 VFS。

容量账本与来源 INSERT、ACK 在同一个 FULL 事务更新，启动及显式恢复时从实际未提交行重新校准。入队不再逐条聚合扫描积压正文，避免批量入队的累计二次工作；不减少落盘次数或降低原有性能门。重复 ACK 只扣除一次额度；FAILED 与 NEEDS_REVIEW 仍占额度，不能靠清空失败数据换取吞吐量。

删除终态轨迹也属于累计账本的写事务：先取得同文件写入门并开始 `BEGIN IMMEDIATE`，
再读取归属和 totals，在同一短事务内删除事件／Span／轨迹并更新清理计数。
不能先读 totals 再等待 DELETE 的写锁，否则并发删除或保留清理可能覆盖较新的累计值。
拒绝和异常都会回滚并归还租约；只读回放不会取得该删除写事务。

关闭先停止接收，随后有界排空并等待实际写线程；调用者取消不销毁正在提交的事务。重启先投影仍可恢复的来源，再将真正未结束的历史任务标记中断；不会恢复 Agent 工具执行。评测归档同时备份主库与来源日志，失败则保留自有暂存目录；只读历史面板不会启动写入器或改变归档。损坏、超期或缺失前置记录需要检查，不自动猜测修复。

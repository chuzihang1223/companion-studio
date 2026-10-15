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

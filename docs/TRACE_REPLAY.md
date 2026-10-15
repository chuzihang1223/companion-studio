# Agent Trace 与只读回放

本功能位于 `feature/agent-trace-replay`，基于公开 v0.1.2 后的主线提交开发。`0.2.0-trace.1` 是本地审核版本，不是新的公开 Release。请使用独立 home、UserData 和端口；不要把正式用户数据库复制到测试环境。

## 使用

1. 切换工作模式，在「模块」菜单或 Dock 打开 **Agent Trace · 执行追踪**。模块使用原有拖动、缩放、最小化和会话布局保存机制。
2. 正常提交工作任务，按原有权限流程处理授权。追踪不会代替授权，也不改变工具执行顺序。
3. 时间轴记录规划、模型请求、工具请求、实际操作、验证、重试、暂停、恢复和任务结束。点击事件查看任务编号、父子 Span、耗时、参数类型摘要、错误代码和结构化证据。
4. 历史默认限于当前工作对话；可选「本次工作任务」，或按任务 UUID、状态、开始时间、工具名筛选。「更早的任务」继续加载历史。
5. 「从头回放」「暂停回放」「继续回放」、进度条和 0.5/1/2/4 倍速度只改变游标。为方便浏览，事件间等待缩短为 80–2000 ms；不是原速度录屏。隐藏面板时停止推进，返回后可继续。
6. 可导出已经加载的脱敏 JSON，默认下载目录为本实例 home 下的 `Exports`，与正式版或其他审核版分开。导出包含 `partial` 标记；缺口、超过读取上限或仍在写入都不能当作完整证据。大 JSON 默认折叠，单处展示截断到 8000 字符。
7. 已结束的任务可显式确认删除追踪。只删除追踪三张表中该条数据，保留聊天、任务和 WorkEvent。任务尚未结束时拒绝删除。

旧版任务没有 Trace；不会重新操作电脑来补齐。重启时正在执行的轨迹标为中断，未结束 Span 的耗时留空。待执行/待授权计划仍沿用原手动执行流程；用户主动执行时记录新片段并标记不完整，不自动恢复工具。

## 状态与可信度

- `TOOL_STARTED` 表示工具请求进入运行时，不证明已执行。
- 带原 `source_work_event_id` 的操作观察证明执行器已进入操作路径；`metadata.executed` 表示已进入该路径。
- `TOOL_COMPLETED` 是工具正常返回；独立验证 Span 才记录目标验证结果。
- `passed` 表示确定性规则通过且工具返回 `verified=true` 证据标志；`rule_passed` 仅表示现有规则通过；`failed` 是未通过；`not_provided` 是没有该证据。
- 布尔证据由工具返回，不等同屏幕截图或独立语义证明。当前 GoalVerifier 对普通任务仍主要检查工具状态，不保证覆盖用户的所有子目标。
- 模型计数是 `LLMService` 的逻辑调用次数，不推测供应商内部重试、token 数量或费用。

## 数据结构与迁移

使用现有数据库，通过 `migrate()` 的 `Base.metadata.create_all()` 幂等新增：

| 表 | 职责 |
| --- | --- |
| `agent_traces` | Trace/Task/Conversation/WorkSession 关联、状态、UTC 起止、单调时钟总耗时、数量、缺口和 schema_version |
| `trace_spans` | 父子关系、planning/model/tool/operation/verification/scheduler/permission/recovery、工具和模型标识、耗时、错误、重试、证据摘要 |
| `trace_events` | 每 Trace 递增序号、事件类型、Span、原 WorkEvent 编号、UTC、白名单数据 |

不修改旧消息字段，不回填私人历史。按会话/时间、工作任务/时间、状态/时间、工具及 Trace 序号建立索引。跨版本读取暂只支持 schema_version=1，不支持的版本显式拒绝。

UTC 用于事件时间；`time.perf_counter()`（单调时钟） 用于执行耗时。总耗时包含等待授权。模型、工具操作、调度、授权等待、恢复分别汇总；父子 Span 可嵌套，不能把全部分类之和当作任务总耗时。重启后的执行片段只有该片段的耗时，标记不完整。

## 接口与安全

所有接口需要桌面现有 `X-Companion-Control-Token`。Token 留在 Electron 主进程，preload 仅暴露固定历史操作，并检查主窗口发送者。内嵌网页无法使用该能力。

| 方法与路径 | 参数/结果 |
| --- | --- |
| `GET /api/agent/traces` | 必填 conversation_id；可选 work_session_id、task_id、status、tool_name、since、until、cursor、limit；默认 30、最多 100 条任务 |
| `GET /api/agent/traces/{trace_id}` | 必填 conversation_id，可选 work_session_id；返回任务 DTO |
| `GET /api/agent/traces/{trace_id}/events` | 同上，after 为事件序号，limit 最多 250；返回有序事件页 |
| `GET /api/agent/traces/{trace_id}/summary` | 分类别耗时与请求、执行、验证、重试计数 |
| `DELETE /api/agent/traces/{trace_id}` | 同一归属校验，仅删除终态轨迹 |

UUID、分页、工具名和日期经过验证。真实 Conversation 必须为未删除的 work/project，WorkSession 和 Trace 必须属于该对话。跨会话请求返回 404；不提供跨工作目录的通配查询。不同用户档案须使用各自后端/控制 Token；本接口不是多用户登录系统。

默认不记录提示词、用户正文、参数值、路径、URL、窗口标题、文件内容、环境变量、截图、音频或密钥。参数只保留固定允许的字段名和类型，证据只保留允许的布尔/数量；异常只保存固定错误代码和通用说明。入队和读取/导出都重新应用白名单。工具/模型名也经过格式和敏感模式检查。

输入 `打开某应用` 的一条输出可能是：

```json
{
  "event_type": "TOOL_COMPLETED",
  "phase": "tool",
  "status": "completed",
  "verification_status": "passed",
  "metadata": {"executed": true, "parameter_summary": {"app": "text"}},
  "evidence": [{"kind": "boolean", "name": "verified", "value": true}],
  "schema_version": 1
}
```

这是字段节选，实际 DTO 还含关联 UUID、UTC 和耗时。应用名和原始输入没有写入 Trace。

## 写入、实时更新与性能边界

- 复用 WorkState 原事件提交后的投影钩子，保留 `source_work_event_id`。规划早于 WorkSession，独立记录规划 Span，再绑定真实工作会话。
- 规划入口预分配同一 plan/task UUID；ContextVar 贯穿 await/to_thread，避免并发串线。
- 同一 WorkState SSE 队列发送 `trace_updated` 失效通知，不新增 WorkEvent、不推进 RuntimeRegistry watchdog。重连仍可通过 GET 读取持久状态；活动任务 3 秒轮询作为兜底。
- 有界队列 2048 条，最多每批 50 条、间隔 50 ms，线程中提交 SQLite；独立连接 busy_timeout=100 ms，不改变业务数据库原等待策略。不按流式 token 写数据库。
- 写入/排队失败保留失败计数、通用类型日志及可用轨迹的不完整标记，不重试工具，不使原 Agent 失败。创建记录本身完全失败时该轨迹可能不可读，应查本机日志。
- 默认保留 30 天、最多 1000 个任务、每任务 5000 个事件和 Span，总事件上限 200000；触发写入时每 5 秒执行清理。活跃任务也可能因时间/条数上限被清理，剩余执行照常继续，不能视为完整追踪。
- 内存上下文最多 512，绑定最多 1024，丢失标记最多 2048。浏览器单任务最多 5000 条，任务列表最多 1000 条；虚拟列表只渲染视口附近事件。
- 写入批次总/最大耗时、丢弃与失败数、最近查询耗时保存在 Store metrics，性能测试打印这些数字。UI 显示更新到下一帧的等待时间，包含调度开销，不能当作纯 React 耗时或 FPS。

## 本地验证

Windows PowerShell，项目根目录：

```powershell
.\.venv\Scripts\python.exe -B -m pytest backend/tests/test_agent_trace.py backend/tests/test_trace_persistence.py backend/tests/test_trace_api.py -q
npm.cmd run test:electron --prefix desktop
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1
```

后端使用本机模型/工具替身覆盖真实 AgentService → TaskRuntime → WorkState → Trace，测试拒绝权限、取消、失败、超时、重试、暂停恢复、并发、旧数据迁移、重启、脱敏及写入失败隔离。Electron 单元组保留原布局/音频/会话测试，并增加时间轴排序、虚拟列表、Replay 时间和 IPC 边界测试。

`tests/ui/trace-viewer-e2e.cjs` 是显式运行的安装版测试：设置 `COMPANION_TEST_EXE` 为审核版程序，`COMPANION_TEST_HOME` 为全新的空测试目录，然后运行 `node tests/ui/trace-viewer-e2e.cjs`。它操作实际 Chromium DOM，运行本机系统状态任务，查看真实轨迹，再在该隔离数据库补入 1000 条明确标为合成观察的事件，验证分页、点击、播放/暂停/继续/跳转、会话切换和重启。报告保留在测试目录，不能作为公开附件直接上传。

安装测试沿用 `scripts/test-installer.ps1` 对本产品两项注册表键和两个快捷方式的备份/恢复保护。不能直接运行安装器覆盖正式版。目录版/安装版与实际 Windows 原生操作、音乐和声音主观体验分别记录，不以单元测试代替。

## 后续 Evaluation 接口与技术债务

可直接使用任务 ID、状态、事件序号、父子 Span、确定性验证、通用错误、模型/工具次数和分项耗时做 Evaluation 数据输入。后续优先复用四个 GET、schema_version、GoalVerifier Span、原 WorkEvent 关联与 retention 策略。

尚未实现评分器、Benchmark、跨进程自动断点执行、截图证据、token/费用统计、任意结果全文、跨会话总览或后台闲时清理。没有跨进程 writer 协调，同一个 home 只运行一个后端。Trace 队列丢失、上下文淘汰和重启会导致缺口，必须保留不完整标志。当前查询列表使用有界 offset，事件采用 sequence 游标；更大规模的数据需要专门评测后再调整。

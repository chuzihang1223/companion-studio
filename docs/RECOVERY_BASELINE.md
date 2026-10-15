# Recovery 开发基线

从已完成的本地 Trace / Evaluation 分支 `1406b02133188a3f602c7db4f4a220cb690d7334`
建立 `feature/agent-recovery-checkpoint`，初始工作区干净，版本 0.2.0-eval.1。
本地缓存的 origin/main 比该提交少 22 个提交；未联网刷新，不宣称代表当前 GitHub。
不修改 main、既有公开 v0.1.2、正式档案或个人版。

## 实际运行

Windows 11、Python 3.12.3、Node 24.20.0、npm 11.19.0，锁定依赖。
运行 `scripts/check.ps1`：254 pytest passed，1 skipped（Windows symlink 权限），5 warnings；
Ruff、TypeScript、ESLint、Prettier、前端生产构建、13 组 Electron 回归、源码和全部本地可达
Git 历史审计通过。ESLint 保留既有依赖提示。测试与产物位于隔离的本地缓存。

另运行 Skill 02 原始 Studio Core 1.0.0、20 项、Mock、并发 2、一次：

|指标|实测|
|---|---|
|回归断言通过率|20 / 20（100%）；不是实际用户目标成功率|
|Agent 流程平均耗时|413.864 ms|
|P50 / P95|257.840 / 933.718 ms|
|工具调用|18|
|逻辑 Mock 模型调用|31；外部 API 调用 0|
|重试次数|4|
|进入恢复的用例 / 恢复成功|2 / 0（0%）|
|安全违规|20 项证据均为 unauthorized_actions=0|
|真实模型成功率 / 费用|NOT_RUN / N/A|
|真实 Windows GUI Agent|NOT_RUN|

Run ID `271467eb-c56c-4906-bd12-19e0a42df67e`；数据集 SHA256
`42c7bc7311d715c6aeb6672ef2bb08324d2f3a21f937027f9b551b6db321684c`。
持续时间来自既有 Evaluation 的实际计时，包含任务内部规划与执行，不含 worker 启动和排队。
基线评测与全量回归曾同时运行，机器负载会影响时间，不能把耗时差异全部归因于算法。
原数据集有预期失败断言：PASS 不表示超时、拒绝或启动未就绪的任务完成了用户目标。

## 架构复用

AgentService → RuntimeTaskRegistry → AgentTaskRuntime → TaskWaiter / DesktopBridge /
CodingWorkspaceExecutor → GoalVerifier → WorkState。TaskHandle 控制暂停/取消，注册器拥有
会话锁和 Agent 并发门。Trace 的三个表、只读 Replay、鉴权 API，以及 Evaluation 的隔离
worker、评分、指标和 Dashboard 均已存在，不建立第二套执行器、追踪或评测平台。

原运行时启动仅标记 interrupted，不重放工具；待执行计划与已执行任务断点不同。
普通步骤最多 3 次固定尝试；编码、启动和超时不整步重复。恢复升级要用分类、预算、
当前状态和副作用语义代替无证据的重复，新增表以版本迁移保留旧会话、角色与聊天。

# Evaluation 开发基线

基于本地已验收的 `feature/agent-trace-replay`，提交 `7e5243b`；工作区干净。
新分支 `feature/agent-evaluation-benchmark`。安装版为 0.1.2，Trace 本地候选为
0.2.0-trace.1。缓存的 origin/main 落后 15 个提交；本轮远程只读核验因网络连接失败，
不声称它就是当前 GitHub main。未改动 main、公开标签或安装版。

## 复用边界

- AgentService.plan/execute，ConversationRouter，ToolRegistry，AgentTaskRuntime、
  RuntimeTaskRegistry、TaskWaiter、GoalVerifier 和 TrustedActionStore 保持原权限链。
- 工具有 workspace 文件、code、browser、app、media、system 等类别；同会话串行、
  Agent 并发上限 3；普通步骤最多 65 秒、编码步骤 1200 秒，外层评测另加更小超时。
- Trace schema_version=1；agent_traces/trace_spans/trace_events 三表，由 TraceRecorder
  观察现有执行，TraceStore 有界异步写入。TraceRepository 按会话/工作 session 校验归属，
  Viewer/Replay 只读。评测不得新建第二套 Trace 格式。
- SQLite WAL、SQLAlchemy，迁移使用兼容添加。评测摘要另存表，实际测试在独立进程和
  独立数据库中执行，不能并发替换生产全局 SessionLocal。
- 模型来自 LLMService/ModelRoleRouter，OpenAI-compatible Provider；安全配置只在主进程/后端。
  原 Provider 未保留 Token usage；缺失必须记 Unavailable，不能按文字长度估算。
- Workbench registry、Dock、持久布局及两个主进程模块白名单复用；不改缩放/滚动计算。

## 实测基线

Windows，Python 3.12.3、Node 24.20.0、npm 11.19.0，锁定依赖可用。
执行 scripts/check.ps1：232 pytest passed、1 skipped（Windows 符号链接权限），
3 warnings；Ruff、格式、TypeScript、ESLint、Prettier、前端构建、12 组 Electron 单元回归、
源码和全可达 Git 历史审计通过。ESLint 既有依赖提示不代表错误。
没有执行真实模型、语音或真实 Windows 桌面 Agent。

## 已知限制

普通 GoalVerifier 主要依赖工具完成状态，并非全任务语义证明。应用启动请求与窗口就绪
必须在评测中独立核对。现有网页安全策略拒绝本机/私网 URL，不因测试放宽。
需要交互式受控桥的 GUI/本地 Chromium 页面能力，环境不足时明确 BLOCKED。
Mock 分数只说明模拟流程回归，不代表真实 AI 任务成功率。

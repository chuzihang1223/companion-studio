# Agent Evaluation & Benchmark

本地候选功能，和 Skill 01 Trace 在同一版本审核；没有自动发布或修改模型路由。

## 使用

工作模式 →「模块」→「Agent Evaluation · 评测」。默认 Studio Core 1.0.0、20 项、
Mock、一次、串行。点「开始评测」，可暂停出队、继续或取消；每项结果可查看证据、
Trace 和只读回放。选择任务可单项运行，勾选结果可重跑为新的 Run，旧记录不被覆盖。
历史结果支持分页、比较条件和导出 JSON/CSV/Markdown 到当前应用的 `Exports`。
没有运行时显示空状态，查看历史/Replay 不执行工具或模型。

## 模式与实际支持

|模式|当前行为|
|---|---|
|mock|确定性 Mock LLM、Mock 桌面/网页边界；真实 AgentService、Runtime、权限、文件 Fixture、Trace、评分、SQLite 和 API。网页 DOM 是合成 HTML 解析，非真实浏览器。|
|sandbox|相同 Runtime 与 Mock LLM，真实独立文件读取/创建/修改/代码测试；不连接真实桌面。浏览器/桌面专属项按数据集标记 BLOCKED。|
|windows_gui|为独立交互式受控桥预留；当前未接入，对适用项明确 BLOCKED，不会操作用户应用。|
|live_model|用户在界面选择已配置职责对应的模型并明确授权。仅合成 Fixture，桌面边界仍模拟，不表示真实 Windows Agent 质量。真实调用本轮未验收。|

真实模型请求有次数、并发（串行）、单项/批次截止时间与 Token 停止阈值。输入 Token
无法在服务端请求前精确知道，因此 Token 阈值不是计费硬上限；Usage 缺失后禁止下一次
调用，失败/超时无法确定已消耗量时停止本批后续调用。价格未配置，费用 N/A；没有付费裁判。
角色/规划等职责可以预先配置两个模型，再分别选择该职责运行相同数据集并比较。
不在评测数据里保存密钥、服务 URL 或完整模型配置。

## 架构与隔离

`BenchmarkRegistry → Pydantic validator → AgentEvaluationEngine → EnvironmentManager`
在每个 Attempt 新建一次性 worker：生产 AgentService / RuntimeTaskRegistry /
AgentTaskRuntime / TrustedActionStore / GoalVerifier，随后使用既有 TraceRecorder /
TraceStore / TraceRepository，最后 EvaluationGrader → MetricsCollector → EvaluationRepository。

父服务只保存两张追加表 evaluation_runs / evaluation_attempts（后者合并唯一评分），
不把合成聊天写入正式 conversations/messages。每个 worker 独立配置、工作区、SQLite；
不替换正在使用的全局 SessionLocal。无用户目录、代理、密钥环境继承，Live 配置通过
带单次随机凭证的回环 IPC 传入内存。Windows Job kill-on-close 管理 worker 及子进程；
取消、退出或父进程异常终止只影响该评测拥有的进程。

临时目录使用短随机名称，以适配 Windows 路径限制；结束后只备份该 worker 的 SQLite
证据到 `UserData/evaluation/<RunUUID>/<AttemptUUID>/data/chat.db`，删除合成工作区和日志。
中断 Trace 使用原恢复逻辑标为 interrupted，不伪造已完成；重启不自动重跑评测。
代码用例 EVAL-007 复用应用选定的 AGENT_PYTHON，需要可用 Python CLI；冻结后端不是
Python CLI，没有解释器时该项 BLOCKED，不自动安装依赖或运行用户项目。

最多一个活动批次、Mock/Sandbox 并发 1–2、每项最多 5 次、总尝试最多 100；GUI/Live
串行。默认保留 30 天、最多 100 个 Run、归档 SQLite 256 MiB 上限；需要时删除旧历史。
每个 worker 的 Trace 仍有原始有界队列/事件限制。公开报告只含合成标识、计数、布尔
证据和脱敏配置摘要，不带原始 Trace 正文或用户文件。

## 接口与前端安全

所有 `/api/evaluation` 路由都需现有控制令牌：datasets/detail、models、runs create/list/
detail/results/metrics、control（pause/resume/cancel）、cancel、DELETE、compare、export、
events，以及 attempts/UUID/trace/{list,detail,events,summary}。Trace 归属先核验 Run/Attempt，
再调用既有 TraceRepository 的会话检查；双会话用例的两条 Trace 均可只读查看。

主进程固定 IPC 是唯一桌面入口；不暴露通用 URL、Shell、目录参数或控制令牌。
SSE 使用 EVAL_RUN_* / EVAL_CASE_* 事件，带 Run/Case 标识；主进程仅转发有界失效通知。
Dashboard 自己节流读取（500ms）和断线回退（3s），不会把每个评测事件更新成 Workbench
父组件状态。表格每页 20 项、趋势最多 80 点；原拖拽、Resize、Auto Scroll 算法未修改。

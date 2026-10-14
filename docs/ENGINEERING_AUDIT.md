# V9.0 工程质量审计

审计日期：2026-10-14。范围为公开 Companion Studio 0.1.0 的后端、Electron、React 工作台、测试、构建和发布流程。V9.0 是本轮任务代号，计划发行版本为 0.1.1。静态审计不等同运行验收，下面只记录已核实的事实。

## 本轮边界

保留 Agent 路由/执行顺序、权限模式、用户数据格式、界面设计、布局计算、TTS 引擎。注释、纯格式调整、工程配置、必要发布阻断修复分别提交。不使用个人模型密钥或私人角色/语音/聊天记录。

## P0：发布阻断

| 项目 | 证据与影响 | 处理 |
| --- | --- | --- |
| 对应源码归档缺失 | 工作树缺少 `licenses/source/electron-44.4.5/ffmpeg-source-2b68d2babae73714846961fb0ee47e3b3d2e39a9.tar.gz`，Git 仍跟踪该文件；许可收集和再分发检查不能通过。 | 已从当前 Git 提交精确恢复，SHA256 与原始固定值 `115841ee8a7a7afc1a037b031bf14a469b4e9b04f2d37c0ac515e1181574a5fc` 一致。基线公开导出审计：421 文件、0 项发现。 |
| HTTPS 内网地址限制存在等价表示绕过 | `desktop/electron/main.cjs:41–55` 的地址判断拒绝 IPv4 环回和内网，却接受 IPv4-mapped IPv6 环回/内网表示；纯函数执行已复现，无网络请求。 | 本轮作为安全发布阻断，独立修复地址分类并补边界回归；保持原有权限策略和正常公网地址行为。 |

## P1：本轮必须整改的工程问题

| 文件/范围 | 已核实问题 | 整改方式 |
| --- | --- | --- |
| `backend/app/services/agent_service.py:60,312` | `plan` 和 `_execute_context` 分别约 165/158 行，混合规划、目录验证、持久化、执行和回复协调；6 个方法缺职责 Docstring 和完整签名类型。 | 补准确职责、输入/输出、状态和角色模型说明；只做必要的局部接口整理。 |
| `runtime_registry.py:46`、`desktop/runtime.py:52` | 并发、等待、取消、验证和重试边界被密集代码掩盖。 | 说明锁键为 `(kind, conversation_id)`，Agent/聊天/语音独立配额、等待与任务边界；明确重启仅标记中断。 |
| `backend/tests` | 没有统一隔离入口，部分 TestClient 运行真实 lifespan；仅覆盖 get_db 不隔离全局数据库、日志和 worker。 | 增加测试启动隔离与禁用外部服务配置，保留测试独立配置能力。 |
| `desktop/frontend/src/WorkspaceGrid.tsx:46`、`WorkspaceSplitLayoutEngine.ts:42` | 测量、约束计算、rAF 单次 paint、滚动和 pointerup 提交顺序压成多语句行。 | 注释职责并排版，保留全部坐标表达式、阈值、事件顺序和捕获清理。 |
| `WorkspaceLayoutEngine.ts:23`、`vite-env.d.ts:20` | 迁移入口大量 any；IPC 对旧布局读入的类型表达不够清楚。 | 为已存在的旧版本布局明确兼容类型/unknown 边界，不能改变存储版本和迁移算法。 |
| `AppV3.tsx` | 默认并发 SubmissionService 与保留的兼容串行路径并存，职责不易辨认。 | 注明开关、会话所有权、后台输出路由；本轮不删除兼容路径。 |
| 规范配置与脚本 | 无 Ruff、ESLint、Prettier、统一检查命令或公开 CI。 | 新增固定版本工具和可执行入口，按实际代码采用渐进规则，说明必要例外。 |
| 构建和版本 | NSIS artifactName 固定为 0.1.0；Python依赖只限定范围，难以复现；构建写入跟踪的 build-info。 | 统一读取版本、锁定已验证构建依赖；隔离构建元数据和临时输出。 |
| GitHub 发布 | 当前只有 v0.1.0 源码 Release，没有 Setup 资产、CI 或受控发布工作流。 | 增加 CI 和 Windows tag→草稿流程；0.1.1 先草稿，所有门禁通过后等待本次公开确认。 |

## P2：准确注释与覆盖改进

- 8 个核心 Python 模块约 44 个函数，仅 1 个已有方法 Docstring；返回字典需说明字段和角色。
- Electron 约 20 多个模块有密集多语句行；需补应用发现记录、候选启动入口、主进程/preload 信任边界、控制令牌和资源所有权 JSDoc。
- `Workbench.tsx`、`WorkspaceGrid.tsx` 的 Hook/事件订阅使用 refs 与手势快照；不能机械补依赖导致重新注册或取消手势。
- 已有 9 组纯本地桌面回归覆盖 T 接点、响应式还原、历史不复制、会话和角色所有权；它们不等同真实 GUI FPS/指针验收。
- RuntimeRegistry 现有测试缺同会话串行、重复任务 ID、门控上限与清理边界；补必要回归。
- 未打包后端下的 `/app/` CSP 检查不能用条件断言静默放行，应明确环境边界。

## 已发现但不混入注释提交的行为问题

| 位置 | 事实与本轮处理 |
| --- | --- |
| `agent_tools.py:211–229` | 脚本超时只终止父进程；取消 to_thread 不会强行中止其线程。记录为后续进程树管理问题。 |
| `agent_tools.py:127–138` | 畸形工具结构可能抛 AttributeError，当前未逐项检查形状。保留并记录，不能虚称所有参数均被强类型校验。 |
| `runtime_registry.py:93–102` | 180 秒 Watchdog 只覆盖尚未报告 reason 的任务；有事件后主要受 1800 秒总超时约束。注释真实边界。 |
| `agent_service.py:268–276` | 300 秒约束为当前内存上下文年龄，恢复持久计划后会重新计时；不是持久计划永久到期。 |
| `computer-controller.cjs` | worker 缺 spawn error/stdin error 处理，stop 未立即结算 pending；记录为独立故障恢复修复。 |
| `permissions.cjs:3` | 权限查询缺 fetch 超时；记录限制，不改变模式或授权策略。 |
| `fish-config.cjs:46` | 后端/设置变更发生在密钥实际加密之前，加密失败可能出现状态差异；需后续独立事务回归。 |
| `workspace-browser.cjs:109` | dispose 关闭页签但未解除 session 的下载/响应监听；单窗口退出会结束进程，未来重建管理器需单独整改。 |
| Windows worker/辅助类 | 部分 Process 未 Dispose，旧 Ctrl 输入异常路径未保证 key-up；保留焦点/锁屏保护，后续独立修复。 |
| `tests/ui/cdp-client.cjs` | 验收客户端建连、超时和连接关闭的 pending 清理需要加强；不能将失败冒充验收通过。 |

## 文档必须保持的事实

- 简单确定性本地任务可跳过模型工具规划；复杂任务调用角色配置中的规划/工具职责模型，选择取决于实际能力配置和 fallback。
- AgentService 的 plan 阶段不由执行门控管理；execute 才进入 RuntimeTaskRegistry。
- 锁范围是任务类型和会话的组合，不同类型同会话不等同互斥。
- 启动时将遗留任务标 interrupted，不会自动重放工具；不是完整断点恢复。
- GoalVerifier 是确定性工具结果启发式检查，不是完备的语义目标验证。
- 未知 Tool Calling 能力允许尝试；没有工具选择不能证明服务不支持 Tool Calling。
- 旧 always 授权资料用于归档；当前权限按模式、风险和当前任务授权决定。

## 验收状态

初始审计完成；基线源码/许可检查已通过。代码注释、规范配置、回归、CI、安装包、安全检查和真实 Windows GUI 验收均待实际执行并记录。没有将静态审计写成运行测试成功。

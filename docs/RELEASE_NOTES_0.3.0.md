# Companion Studio v0.3.0

Companion Studio v0.3.0 面向 Windows x64，整合可配置聊天、角色管理和本地 Agent 工作台。新安装使用中性通用助手，不携带私人角色、聊天、凭据、知识库、音色或模型权重。

## 主要能力 / Highlights

| 模块 | 能力 / Capability |
| --- | --- |
| Agent Trace & Replay | 执行追踪、事件查看与回放 / Inspect and replay recorded agent events. |
| Evaluation & Benchmark | 隔离评测、结果与指标 / Isolated evaluations, results and metrics. |
| Recovery & Checkpoint | 检查点及受控失败恢复 / Checkpoints and controlled recovery without blindly repeating uncertain side effects. |
| MCP & Skills | 配置外部工具和Skills，保留权限边界 / Configurable extensions subject to local permissions. |
| Visual Workflow Builder | 可视化编辑、版本化与工作流执行 / Visual editing and versioned workflow execution. |
| Local Knowledge & RAG | 本地知识索引、检索与记忆；语义检索需相应配置 / Local indexing, retrieval and memory; semantic retrieval requires its provider configuration. |
| Visual Computer Use | 经授权的Windows观察与操作 / Permission-controlled Windows observation and actions. |
| Scheduled Agent | 有界后台调度与恢复策略 / Bounded scheduling and recovery policies while the application is running. |
| Realtime Voice Agent | 语音输入、分段朗读和手动打断 / Voice input, segmented playback and manual interruption. |
| Intelligent Model Router | 模型职责、能力/隐私过滤和预算策略 / Model roles, capability/privacy filtering and budget policies. |

同时修复事件订阅/数据库Session及准备计划资源保留、取消清理、Trace并发删除账本和Windows长路径评测归档删除；补齐公开源码的验收文档和配置。最后定版只同步版本与发布文档，没有新增模型能力或扩大权限。

Includes lifecycle and cancellation cleanup, Trace accounting and Windows long-path archive fixes. This release retains the existing provider abstraction, local data ownership and authorization boundaries.

## 安装与升级 / Installation and upgrade

- 支持 Windows 10/11 x64。下载 `Companion-Studio-0.3.0-Setup.exe`，核对 `SHA256SUMS.txt` 后安装；保留用户数据，升级前建议自行备份重要档案。旧版 v0.1.2 保留。
- Windows x64 installer; verify the published checksum. Existing releases remain available. Keep a backup of important data before upgrading.
- 安装器未进行数字签名，Windows可能显示未知发布者或SmartScreen提示；这不是Windows发行者认证。
- The installer is **not digitally signed**. Windows may display an unknown publisher or SmartScreen warning.

## 服务、隐私与限制 / Services, privacy and limitations

- 云端AI功能需要自行配置Provider；模型、语音或编码服务可能计费。聊天上下文及经用户许可的附件/语音文本会发送到所选服务，不能视为全部离线。
- Configure your own providers. Third-party services may incur charges; authorized conversation/context data may be transmitted to them.
- 部分语音依赖本机能力、本地模型或外部服务；不附带模型权重或专属音色。真实云模型/语音质量、物理麦克风/AEC、部分硬件和多显示器场景未完整验收。自动语音打断/免提等受限能力以实际支持为准，不能由合成测试证明。
- Optional speech features depend on installed capabilities, local models or services. Physical audio, cloud quality and some DPI/multi-monitor scenarios are not fully validated; automatic acoustic barge-in is not claimed.
- 两项Windows符号链接测试因账号权限跳过；部分功能矩阵项保留范围限制。历史取消/入队时延波动未唯一归因，负载期内存增长仍有未归因余量；已有通过记录不保证任意长期无泄漏或所有环境无问题。
- Two symlink tests are limited by Windows privileges. Historical timing variability and unattributed load-time memory growth remain disclosed observations; bounded engineering tests are not an unlimited reliability guarantee.
- 原GATE-07/GATE-13独立签署未完成，历史状态保持原样。维护者明确调整本次发布条件，接受已披露限制；未宣称第三方签署、绝对零Bug或零安全风险。

**Independent third-party security review: Not performed / Owner accepted release with disclosed limitations.**

工程验证范围、实际构建身份和校验值见随发行提供的清单。历史 rc.4 工程证据只在相关源码/依赖和执行范围仍适用时复用，不把旧安装器改名或把原运行记录改写成新执行。

See the release manifests for actual build identity and checksums. Applicable rc.4 engineering evidence is reused with its original identity; the v0.3.0 installer is a new build, not a renamed candidate.

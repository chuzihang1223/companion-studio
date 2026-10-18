# Companion Studio（伴舟）

**0.1.2 · Windows 桌面应用 · Electron / React / FastAPI / SQLite**

> 本开发分支为 **0.2.0-knowledge.1 本地审核版**，包含 Agent Trace、只读回放、Evaluation、安全检查点续接、MCP / Agent Skills、可视化工作流，以及本地知识库、引用与可控长期记忆；尚未公开发布。下面的下载链接仍指向已发布的 v0.1.2，v0.1.2 不包含这些候选功能。请在独立档案测试本分支，勿覆盖正式版数据。

Companion Studio 把持续聊天、可配置角色和本机工作台放在同一个桌面应用中，方便保存对话、管理角色设定，以及从聊天切换到项目和本机任务。用户可以直接使用通用助手，也可以导入角色卡或创建自己的角色。

新安装只包含中性的「通用助手」和独立应用图标，不附带虚拟角色、角色头像、图片背景、聊天记录、API 密钥、声音 ID 或模型文件。模型服务需要用户自行配置。

[![CI](https://github.com/chuzihang1223/companion-studio/actions/workflows/ci.yml/badge.svg)](https://github.com/chuzihang1223/companion-studio/actions/workflows/ci.yml)

**[Download for Windows · v0.1.2](https://github.com/chuzihang1223/companion-studio/releases/download/v0.1.2/Companion-Studio-0.1.2-Setup.exe)** · [发布说明与 SHA256](https://github.com/chuzihang1223/companion-studio/releases/tag/v0.1.2)

## 系统架构与 Agent 实现

```mermaid
flowchart LR
    UI[React / TypeScript 工作台] -->|HTTP / SSE| API[FastAPI]
    UI -->|受控 IPC / preload| E[Electron 主进程]
    E -->|启动、健康检查、退出| API
    API --> R[AgentService / RuntimeRegistry]
    R --> T[AgentTaskRuntime / ToolExecutor]
    T -->|本机桥接| E
    T --> V[Observation / GoalVerifier]
    T --> MCP[MCP Adapter / 官方 Python SDK]
    MCP --> Ext[用户明确连接的 stdio / Streamable HTTP Server]
    Skills[按需加载的 Agent Skills] --> R
    API --> DB[(SQLite / SQLAlchemy)]
    API --> L[LLMService / OpenAI-compatible Provider]
```

### 核心技术

- **Agent Runtime**：规划与执行分离，工具执行后记录观察结果、验证目标，并根据工具副作用决定重试边界。简单本机指令走确定性路由；复杂规划使用 Tool Calling。
- **模型职责路由**：角色回复、任务规划和工具执行可以使用不同配置；没有能力声明的模型仍需通过实际请求验证，不把空工具调用当作已支持。
- **任务调度**：按任务种类与会话串行，同种不同会话受全局并发门控制；任务 ID 防止重复执行，取消与超时通过 finally 释放锁和配额。
- **桌面工具与权限**：多来源发现 Windows 应用，再解析和启动；工具参数验证、风险判定和桌面控制令牌共同限定调用。操作后的窗口/结果证据参与验证。
- **模块化工作台**：布局迁移与持久化、拖动/缩放、单帧预览、自动滚动和会话输出归属；调整模块不重写聊天记录。

应用重启会把未完成任务标为中断。本分支可由用户预览检查点、核验当前状态后安全续接；无法确认的副作用会停止并要求人工检查。没有通用外部操作的 Exactly Once、自主学习或分布式多 Agent 系统。

架构与源码细节：[系统架构](docs/ARCHITECTURE.md) · [Agent Runtime](docs/AGENT_RUNTIME.md) · [工具系统](docs/TOOL_SYSTEM.md) · [工作台](docs/WORKBENCH.md) · [工程审计](docs/ENGINEERING_AUDIT.md)

## 1. 主要功能

本分支额外提供 [执行追踪与回放](docs/TRACE_REPLAY.md)：工作模式 →「模块」→「Agent Trace · 执行追踪」。可以查看规划、模型、工具、授权、验证、重试和中断记录，筛选历史任务并只读回放；回放不会重新操作电脑或请求模型。默认只保存脱敏摘要，不保存原始提示词、聊天正文、文件内容或截图。旧任务不补造轨迹。

本分支还提供 [Agent Evaluation & Benchmark](docs/AGENT_EVALUATION.md)：工作模式 →「模块」→「Agent Evaluation · 评测」。内置 20 项合成用例，隔离执行、按证据评分、保留历史、关联 Trace，支持运行条件比较和 JSON/CSV/Markdown 导出。默认 Mock 不调用外部模型；Mock 通过率不能代表真实 AI 的任务成功率。Sandbox 支持隔离文件/代码；专用 Windows GUI 桥尚未接入，对应项明确阻断。真实模型须每次明确授权并设置预算，当前没有真实模型质量成绩。指标与限制见 [说明](docs/EVALUATION_METRICS.md)，验证入口见 [测试](docs/EVALUATION_TESTING.md)。已实际生成的 [Mock 示例报告](docs/EVALUATION_MOCK_SAMPLE.md)可供核对。

下面是安装版实际运行的 Mock 评测：统计来自合成任务，真实 AI 任务成功率仍为未测量。

![Agent Evaluation 的 Mock 指标、逐项结果与 Trace 入口](docs/screenshots/evaluation.png)

本分支的 [Agent Recovery](docs/AGENT_RECOVERY.md) 位于工作流模块：分类错误、有限恢复、保存执行意图、重启后预览、安全续接、暂停/接管/取消，并关联原有 Trace。文件哈希冲突或未知外部副作用会阻断续接；不会自动重复脚本、付费模型或表单。续接本身不追加模型/语音请求，任务执行年龄上限 30 分钟。[Recovery Core 的 16 项评测](docs/RECOVERY_EVALUATION.md)区分安全断言通过率与实际合成目标完成率。

![安装版在执行中断并重启后，核验检查点并显示安全续接预览](docs/screenshots/recovery.png)

本分支新增 [MCP & Skills Extension](docs/MCP_ARCHITECTURE.md)。在「设置 → MCP & Skills」
手动配置并连接本地 stdio 或远程 Streamable HTTP Server，逐项审阅、启用工具。外部工具经过
原 ToolRegistry、权限、Runtime、Trace 和 Recovery；工具定义变化或未知副作用会停止，
不会自动重复外部操作。Resources / Prompts 可授权预览，返回内容属于低信任数据。
Windows 安全存储保存凭据，不把已保存 Token 返回界面或导出模板。

[Agent Skills](docs/AGENT_SKILLS.md) 支持标准 SKILL.md 目录导入、自建、启用/禁用、更新、
内容与文件查看及自建导出。工作任务可用 `$project-review` 显式选择；平时只读元数据，命中后
才加载说明，需要时再读 references。随附三个 MIT 示例，不自动运行脚本、不继承 Codex 开发权限。

![安装版的 Agent Skills 管理：预览示例、导入、启用和查看所需能力](docs/screenshots/agent-skills.png)

[配置方法](docs/MCP_CONFIGURATION.md) · [安全边界](docs/MCP_SECURITY.md) ·
[Skills 编写](docs/SKILLS_DEVELOPMENT.md) · [20 项受控评测](docs/MCP_EVALUATION.md)。

安装版的 MCP & Skills Core 使用本机受控 Server 和合成模型，保留真实传输、权限、Runtime、
检查点及 Trace。20 项断言通过率不等于真实 AI 任务成功率，预期注入的故障仍单独统计。

![安装版的 MCP 与 Skills 评测：20 项受控结果、协议指标及 Trace 入口](docs/screenshots/mcp-evaluation.png)

### 本地知识库与可控记忆（本地审核分支）

解决项目资料分散、聊天缺少可核对出处的问题。侧栏「知识与记忆」或工作台对应模块可以
创建库、显式授权目录、增量索引、中文/英文关键词搜索，并查看行号、页码或段落来源。
支持 Markdown/TXT、常见代码/JSON/YAML、文本层 PDF 和 DOCX；不做 OCR，不执行文档代码。

![实际安装版使用原创合成资料：显式目录授权、本地关键词检索与可核验行号](docs/screenshots/knowledge-search.png)

仅授权本地检索即可离线使用。若要让当前模型根据资料回答，需要另外确认片段传输目标，
再启用当前会话 RAG；改模型地址或名称后需要重新授权。没有证据会明确无法确认，
来源按钮只能打开重新核验过的索引位置。来源核验不代表模型每个结论都通过事实核查。

Embedding 默认关闭，不下载模型，不要求付费 API。可接用户自行运行的本地
OpenAI-compatible Embedding 服务，或另外授权的远程服务；未配置/失败时混合搜索明确回退关键词。

「长期记忆」只保存用户主动填写的事实、偏好和任务信息；支持范围隔离、编辑、启停、删除及清空。
不自动保存全部私人聊天，不把项目文件当个人记忆。旧角色记忆保留在原表并做增量迁移。

![实际安装版的长期记忆管理：按会话保存、编辑、启停和删除](docs/screenshots/knowledge-memory.png)

Agent 新增四个受限 Knowledge 工具；流程设计器新增「知识检索」节点与「项目知识助手」模板。
来源约束沿工作流祖先传递，旧任务恢复前重新核验，不能靠转换节点或导入 Skill 扩大权限。
Evaluation 新增 28 项 Knowledge Core，使用合成资料和固定模型，不能代表真实 AI 质量。

输入输出示例（原创合成文件，不是一次真实云模型测试）：

```text
授权文件 guide.md 第 3 行：Orchard launch port is 8794.
本地搜索输入：Orchard
输出：guide.md / 第 3 行 / 实际摘录 / 来源摘要核验状态

启用并授权知识回答后输入：项目启动端口是什么？
固定 MockRAG 验证输出：根据合成项目资料，启动端口为 8794。〔来源〕
无匹配证据：在当前授权知识库中没有找到可验证的资料，无法据此确认。
```

[架构](docs/KNOWLEDGE_ARCHITECTURE.md) · [目录与使用](docs/KNOWLEDGE_COLLECTIONS.md) ·
[解析和索引](docs/KNOWLEDGE_INDEXING.md) · [检索/可选模型](docs/RAG_RETRIEVAL.md) ·
[引用](docs/RAG_CITATIONS.md) · [记忆](docs/LONG_TERM_MEMORY.md) ·
[安全边界](docs/KNOWLEDGE_SECURITY.md) · [评测与实测性能](docs/KNOWLEDGE_EVALUATION.md)

### Visual Workflow Builder（本地审核分支）

工作模式 →「模块」→「Visual Workflow · 流程设计」。可在画布上添加、拖动和连接节点，
选择工具 / MCP / Skill、绑定输入和祖先节点的输出，校验后保存并发布本地不可变版本。
支持多选、复制粘贴、撤销重做、平移缩放、整理节点、JSON 导入导出与草稿归档；图编辑不改变历史运行。
新模块使用按需加载，不重写原工作台的布局、拖拽或自动滚动引擎。

支持手动触发、输入、模型分析、内置工具、MCP 工具、Agent Skill、条件、数据转换、人工审批、
Merge 和输出共 11 种节点。Tool Node 经过原权限和 Agent Runtime；人工审批不会提前授权下游工具。
当前为有界串行 DAG，不支持循环、定时任务、自动后台续跑或任意代码节点。

下图来自 `0.2.0-workflow.1` 实际安装版的独立测试档案；模型测试使用本机模拟服务，不代表真实 AI 质量：

![实际工作流运行后，画布显示节点完成状态并保留只读版本快照](docs/screenshots/workflow-builder.png)

例如打开「项目检查报告」模板，选择测试项目、导入并启用 project-review 示例 Skill、配置模型，
输入 `{"path":"README.md"}`：流程读取该文件 → Skill 分析 → 模型整理 → Markdown 输出。
「代码测试与报告」模板逐次授权后运行所选 .py 测试，并按退出码 / 错误分支汇合输出；
「MCP 数据整理」可使用本机受控 Demo，无需第三方账号。模板不会自动连接或授权外部服务。

运行状态、审批和错误会定位到具体节点；可查看真实 Trace、历史版本和输出，也能只读对比同一流程的
不同版本运行。模型、输入、预算或工具条件不同时会提示，不能据此公平排名。中断后先核验原检查点，
确认安全才续接；未知副作用不会重复。原 Evaluation 增加 Workflow Core 20 项受控评测，
Mock 断言通过率和流程完成数与真实 AI 任务成功率分开显示。正式模型、外部客户端和费用另有运行条件。

[工作流架构](docs/WORKFLOW_ARCHITECTURE.md) · [协议](docs/WORKFLOW_SCHEMA.md) ·
[执行与恢复](docs/WORKFLOW_EXECUTION.md) · [变量](docs/WORKFLOW_VARIABLES.md) ·
[安全](docs/WORKFLOW_SECURITY.md) · [模板](docs/WORKFLOW_TEMPLATES.md) ·
[评测与 Windows 验收](docs/WORKFLOW_EVALUATION.md)。

| 功能 | 当前实现与条件 |
| --- | --- |
| 文字聊天 | 流式显示回复；Enter 发送、Shift + Enter 换行；加载和错误提示；单条消息最多 4000 字符。 |
| 本地历史 | SQLite 保存聊天与工作会话；支持搜索、切换、复制消息、回复重试和对话分支；重启后保留记录。 |
| 角色管理 | 导入本应用 JSON、Tavern V2/V3 JSON 或含角色卡元数据的 PNG；手动创建、编辑和启用角色；角色设定与聊天历史分别保存。 |
| 个性化与记忆 | 调整身份、性格、称呼、关系、回复风格和规则；手工管理记忆，从有限历史中提取相关信息。 |
| API 配置 | OpenAI-compatible Provider；多配置预设、连接测试、热切换、模型能力及职责设置。密钥由 Electron 主进程通过 Windows 安全存储加密保存。 |
| MCP 扩展（审核分支） | 官方 Python SDK 2.3.0；手动连接 stdio / Streamable HTTP；工具发现、独立启用、受控调用、资源和模板预览；支持受控新版及旧式握手协议。 |
| Agent Skills（审核分支） | 本地目录导入、标准 frontmatter 校验、按需匹配、自建编辑与导出；Skill 不授予执行权限，脚本仅查看。 |
| 可视化工作流（审核分支） | 11 种节点、版本快照、受控 DAG 执行、变量/条件/汇合、节点审批、Trace、检查点续接、20 项受控评测及三个 MIT 模板。 |
| 附件 | 每条最多 4 个文件，每个最多 8 MB；支持文本、代码、PDF、DOCX 和图片。图片理解需要当前模型支持视觉能力。 |
| 工作台 | 项目目录、协作对话、工作流、文件、代码、Diff、终端、网页、应用观察和媒体模块；支持 Dock、拖动、缩放、布局保存与撤销。 |
| 本机操作 | Windows 应用发现与启动、网站搜索、项目文件操作及代码任务；简单指令可走本地路由，复杂任务需要支持 Tool Calling 的模型和相应权限。 |
| 音乐与媒体 | 本地曲库、系统媒体状态，以及 QQ 音乐和网易云桌面客户端适配；客户端需另行安装。 |
| 可选语音 | Fish Audio 在线语音和 IndexTTS 本地语音接口；需要用户配置服务或独立模型环境。自动朗读和后台预生成默认关闭。 |
| 外观 | 明暗主题、纯色 / 渐变 / 用户图片背景、头像和工作台布局。 |

### 功能边界

- 不附带大语言模型、角色声音、IndexTTS 模型或参考音频；导入角色卡不会自动创建相应音色。
- Windows 语音输入由系统 **Win + H** 提供；应用没有独立录音识别引擎。
- 角色卡世界书触发、扩展脚本执行和外部资源自动下载未实现；导入时会提示忽略的内容。
- Codex 编码模式依赖另行安装并登录的 Codex CLI，以及账号可用额度。
- 没有用户登录、云同步、向量检索、Live2D 或 3D 角色功能。
- 本机操作受客户端版本、登录状态、网络、桌面解锁和窗口焦点影响，不能保证所有客户端和操作均成功。
- MCP 当前只验证受控本机 Server；不宣称任意第三方服务兼容。OAuth 登录暂未提供，远程仅支持手动 Bearer 凭据。JSON Schema 有明确子集及限额；不自动安装第三方 Server。
- 本地 MCP 程序仍具有当前账号权限；权限 UI 与目录边界不是 OS 沙箱。有效外部响应不会单独变成用户目标的验证证据。
- 聊天上下文和主动附加并发送的文件内容会发送到所选模型服务；启用 Fish 时，朗读文本会发送给 Fish。费用和服务条款由对应服务提供方决定。

## 2. 安装方法

### Windows 正式版

下载 [Companion-Studio-0.1.2-Setup.exe](https://github.com/chuzihang1223/companion-studio/releases/download/v0.1.2/Companion-Studio-0.1.2-Setup.exe)，并用同一 [Release](https://github.com/chuzihang1223/companion-studio/releases/tag/v0.1.2) 的 `SHA256SUMS.txt` 核对摘要后安装。安装包包含 Electron、React 和 Python 后端，运行不需要另装 Python、Node.js 或 npm。安装器尚未签名，Windows 可能提示未知发行者；请核对下载来源与摘要。自行构建的方法见下方。

使用构建好的安装包无需安装 Python、Node.js 或 npm。

1. 从源码构建后，在构建产物目录获取 `Companion-Studio-0.1.2-Setup.exe` 和 `SHA256SUMS.txt`。
2. 用以下命令取得安装包 SHA256，与校验文件中的对应值比较：

   ```powershell
   Get-FileHash -LiteralPath '.\Companion-Studio-0.1.2-Setup.exe' -Algorithm SHA256
   ```

3. 运行安装器，选择具有写入权限的目录。建议采用 **应用根目录\Release\installed** 结构，例如 `D:\Apps\CompanionStudio\Release\installed`，以分开程序与用户数据。
4. 打开安装目录里的 `CompanionStudio.exe`，或使用安装器生成的快捷方式。
5. 在「设置 → API」添加模型配置，即可聊天。

程序默认将 `UserData` 和 `Cache` 放在安装目录向上两级的应用根目录下。首次使用新根目录创建空白档案，再次使用同一根目录恢复该档案。不要只移动 EXE，它依赖同目录资源。

也可在启动前通过 `COMPANION_HOME` 指定其他可写数据根目录：

```powershell
$env:COMPANION_HOME = 'D:\CompanionStudioData'
& 'D:\Apps\CompanionStudio\Release\installed\CompanionStudio.exe'
```

上述路径只是示例，请替换为自己的位置。

### 从源码安装

适用于 Windows 10 / 11。使用 **Python 3.12**、**Node.js 24** 和 npm；锁定运行依赖见 `backend/requirements.lock`，开发及构建工具见 `backend/requirements-dev.txt` 与 `desktop/package-lock.json`。本地 TTS 使用独立环境。

在 PowerShell 打开包含本 README、`backend`、`desktop` 的项目根目录，再执行：

```powershell
$projectRoot = (Get-Location).Path
$studioCache = Join-Path $projectRoot '.cache'
New-Item -ItemType Directory -Force -Path $studioCache | Out-Null
$env:TEMP = $studioCache
$env:TMP = $studioCache
$env:PIP_CACHE_DIR = Join-Path $studioCache 'pip'
$env:npm_config_cache = Join-Path $studioCache 'npm'
$env:ELECTRON_CACHE = Join-Path $studioCache 'electron'
$env:electron_config_cache = $env:ELECTRON_CACHE
$env:ELECTRON_BUILDER_CACHE = Join-Path $studioCache 'electron-builder'

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
npm.cmd ci --prefix desktop
```

如果没有 `py` 启动器，用已安装的 Python 3.12 执行 `python -m venv .venv`。无需激活虚拟环境；`npm.cmd` 可避免 PowerShell 对 `npm.ps1` 的执行策略限制。

在同一窗口启动开发模式：

```powershell
$env:COMPANION_HOME = Join-Path $projectRoot '.runtime\desktop'
$env:BACKEND_PORT = '8786'
npm.cmd run dev --prefix desktop
```

Electron 自动管理后端，Vite 监听 `127.0.0.1:5173`，开发记录位于 `.runtime\desktop\UserData`。密钥从「设置 → API」录入，不自动迁移源码根目录 `.env`。

停止开发服务后，可构建并打开源码桌面界面：

```powershell
npm.cmd run build --prefix desktop
npm.cmd run start:compat --prefix desktop
```

`start:compat` 使用兼容渲染模式。正常关闭窗口会停止其管理的后端；不要另外启动占用同一端口的后端。

### 生成 Windows 安装包

完成源码依赖安装后，在项目根目录执行：

```powershell
npm.cmd run build:public --prefix desktop
```

脚本运行本地回归检查、构建前端、冻结后端，并生成目录版和安装器。默认输出到 `build\<构建ID>\delivery`，包括安装包、构建清单和 SHA256 校验文件，不需要配置真实模型或语音密钥。`build:win`、`build:installer` 调用同一公开构建流程。

可直接执行 `scripts/build-companion-studio.ps1`，用 `-OutputRoot` 指定输出目录。部署辅助脚本用法如下：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-companion-studio.ps1 `
  -Delivery '.\build\你的构建ID\delivery' `
  -ProductHome 'D:\Apps\CompanionStudio'
```

它用于新建档案或更新已有 Companion Studio 安装，接受 `-DesktopDirectory` 和 `-BackendPort`，默认后端端口 8770。普通用户可以直接运行 Setup。

## 3. 使用方法

### 配置模型并聊天

1. 打开 **设置 → API**，添加配置。
2. 填写实际 API 地址和模型名。云服务需自己的 API Key；不要求认证的本机 OpenAI-compatible 服务可以留空。
3. 测试连接后保存应用。测试会发送模型请求，是否收费取决于服务。
4. 返回聊天输入消息，Enter 发送，Shift + Enter 换行。
5. 从侧栏切换历史，关闭重开后继续。

配置支持热切换。远程带密钥的地址要求 HTTPS，本机可使用 localhost HTTP。未配置模型时仍可打开应用；失败时用户消息可能已保存，不会伪造模型回复。

![Companion Studio 聊天模式：通用助手、会话管理、消息输入与附件](docs/screenshots/chat.png)

### 导入或自定义角色

点击侧栏 **角色管理**：

- **导入角色卡文件**：选择本应用 JSON、Tavern V2/V3 JSON 或带角色卡元数据的 PNG；导入后自动启用。
- **创建自定义角色**：名称必填，其他字段可选；填写身份、性格、说话风格、关系或规则，点「保存并使用」。
- 选中已保存角色可以编辑或切换；「通用助手」保留为中性起点。
- PNG 角色卡图片可作头像；普通 PNG 请通过「头像」设置。

JSON 上限 **256 KB**，桌面选择角色卡文件上限 **6 MB**。每次导入生成新的 ID，不覆盖同名角色。选择和各角色历史在重启后恢复。

![Companion Studio 角色管理：导入角色卡、创建自定义角色与个性化设置](docs/screenshots/characters.png)

### 工作模式

1. 切换到 **工作模式**，按需选择项目文件夹。
2. 输入任务，例如「打开记事本」或「列出当前项目的文件」。
3. 查看工作流、结果和授权提示；从 Dock 打开模块并调整布局。
4. 代码草稿可先在代码 / Diff 模块检查，保存、应用和执行遵循当前权限策略。

项目目录不会自动整目录上传。自动操作需要 Windows 解锁，焦点变化可能使任务暂停。后台回复和语音按原会话归属，不插入其他角色当前会话。

![Companion Studio 工作模式：协作对话、工作流、助手协作流与模块停靠栏](docs/screenshots/workbench.png)

### 可选语音和附件

- **语音**：在「设置 → 语音」填写 Fish API Key 和 reference_id，测试后保存并选择引擎，再开启「自动朗读」。IndexTTS 需另行准备独立环境、模型和参考音频。语音不可用时仍可文字聊天。
- **附件**：输入框「+」选择 UTF-8 文本 / 代码、PDF、DOCX、PNG / JPEG / WebP / GIF；每条最多 4 个文件，每个最多 8 MB。

PDF 提取前 30 页文本，没有 OCR，不读取加密 PDF；DOCX 提取段落；长文件选择相关片段，不保证发送全文。图片需 API 配置与模型实际支持视觉能力，GIF 使用第一帧。

### MCP 与 Skills（本地审核版）

1. 在 MCP & Skills 设置中添加现有程序或准确 MCP 端点，保存后手动连接并审阅原生确认框。
2. 工具初始禁用；查看 Schema、来源和风险，再按需要启用。高风险操作每次确认。
3. Agent Skills 页面可导入文件夹或预览随附示例；确认导入后自行启用。
4. 在工作模式选择项目并输入 `$project-review 检查项目中的代码质量`；结果取决于当前模型、
   已启用工具及权限。工作流显示所选 Skill 和外部工具来源。
5. 在 Agent Evaluation 选择 MCP & Skills Core，可运行 20 项隔离合成测试，不使用私人账号。

安装版自带客户端与示例测试 Server 所需依赖，无需为本应用重新装 Python/Node；第三方
MCP 程序仍可能要求自行安装运行时。配置与超时示例见 [MCP 配置](docs/MCP_CONFIGURATION.md)。

## 4. 输入输出示例

### 聊天

前提：已配置可用模型。

输入：

```text
你好，今天有点累。
```

可能的回复：

```text
今天辛苦了。想聊聊发生了什么，还是先休息一会儿？
```

这是风格示例，不是固定台词。回复由模型、角色和上下文决定，消息会保存到本地数据库。

### JSON 角色卡

保存为 UTF-8 的 `example-character.json`，通过角色管理导入：

```json
{
  "id": "example_companion",
  "name": "小禾",
  "profile": {
    "identity": "喜欢植物与日常交流的虚拟伙伴",
    "personality": ["耐心", "有好奇心"],
    "speech_style": ["自然简短", "不机械重复用户的话"],
    "relationship": {"user_role": "交流伙伴"}
  },
  "system_prompt": "根据这些角色资料交流。不编造共同经历或没有依据的事实。",
  "welcome_message": "你好，今天想聊些什么？"
}
```

结果：列表出现「小禾」并启用；实际保存 ID 为自动生成的 `import_...`，后续聊天使用该设定。示例不包含图片、背景或声音。

### HTTP API

以下使用独立开发后端端口 8000；桌面版请换成实际端口。

健康检查：

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health'
```

响应：

```json
{"status": "ok"}
```

发送文字：

```powershell
$payload = @{
    character_id = 'assistant'
    message = '你好，今天有点累。'
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/chat' `
  -ContentType 'application/json; charset=utf-8' `
  -Body ([Text.Encoding]::UTF8.GetBytes($payload))
```

成功响应结构如下，值由实际请求生成：

```json
{
  "message_id": "生成的消息ID",
  "character_id": "assistant",
  "content": "这里是模型生成的回复。",
  "created_at": "2026-10-12T02:00:00",
  "conversation_id": "生成的会话ID"
}
```

读取历史：

```powershell
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/chat/history?character_id=assistant'
```

历史返回消息数组，包含 `id`、`character_id`、`role`、`content`、`created_at`。`GET /api/characters` 返回角色列表。角色写入和模型配置等受控接口需要桌面控制令牌，推荐使用软件界面。普通 `POST /api/chat` 不接受附件。

## 5. 独立后端调试

独立 HTTP 后端不使用桌面进程保存的加密密钥。在项目根目录运行：

```powershell
$env:COMPANION_CLEAN_DISTRIBUTION = '1'
$env:COMPANION_DEFAULT_CHARACTER = 'assistant'
$env:COMPANION_APP_NAME = 'Companion Studio'
$env:COMPANION_DATA_DIR = Join-Path (Get-Location).Path '.runtime\backend'
New-Item -ItemType Directory -Force -Path $env:COMPANION_DATA_DIR | Out-Null
```

在该数据目录 `.env` 文件中填写配置：

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=
OPENAI_MODEL=
OPENAI_BASE_URL=https://api.openai.com/v1
BACKEND_HOST=127.0.0.1
BACKEND_PORT=8000
VOICE_PREFETCH_ENABLED=false
```

填写自己的模型名和密钥；其他 OpenAI-compatible 服务需其实际地址。后端仅读取 `COMPANION_DATA_DIR\.env`，不读取源码根目录 `.env`。

启动：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

然后执行 API 示例；Ctrl + C 停止。不要与桌面后端占同一端口，也不要将自己的 `.env`、密钥和用户数据放入发行文件。

## 6. 工程结构

```text
CompanionStudio/
├─ desktop/
│  ├─ electron/       窗口、后端生命周期、配置、IPC 与本机操作
│  ├─ frontend/       React + TypeScript + Vite 界面
│  └─ assets/         原创应用图标
├─ backend/
│  ├─ app/api/        聊天、会话、角色、语音与工作 API
│  ├─ app/database/   SQLite + SQLAlchemy
│  ├─ app/models/     数据模型
│  ├─ app/schemas/    请求与响应结构
│  └─ app/services/   角色 Prompt、LLM Provider、记忆、语音及工具
├─ characters/        仅内置中性的 assistant.json
├─ scripts/           构建、部署和公开发行检查
├─ tests/             本地测试源码及合成样例
├─ licenses/          第三方许可证
├─ THIRD_PARTY_NOTICES.md
└─ LICENSE
```

数据流：Electron 管理本地 FastAPI → React 通过 HTTP / 流式接口访问后端 → 后端构建角色上下文并调用 LLM Provider → SQLite 保存记录。密钥在主进程解密并传给后端；模型请求由后端发出。

## 7. 数据与排查

应用根目录内主要文件：

| 位置 | 内容 |
| --- | --- |
| `UserData\chat.db` | 聊天、工作会话和相关数据 |
| `UserData\characters` | 自建和导入角色 |
| `UserData\api-settings.json` / `api-keys.json` | API 设置和加密密钥 |
| `UserData\workbench-layout.json` | 工作台布局 |
| `UserData\themes` | 用户外观素材 |
| `UserData\logs` | 运行日志 |
| `Cache` | Chromium、临时和相关缓存 |

聊天和角色文件不默认加密。备份前完全关闭应用；密钥与 Windows 用户安全存储绑定，复制档案不保证其他账号可解密。个人运行档案不属于公开源码或发行包。

| 情况 | 处理方法 |
| --- | --- |
| 找不到 package.json / ENOENT | 先打开项目根目录，再执行带 `--prefix desktop` 的命令。 |
| 缺少后端 | 源码版检查 `.venv\Scripts\python.exe`；安装版检查 `resources` 是否完整。 |
| GPU 进程退出或启动无窗口 | 源码用 `start:compat`；安装版可加 `--compat` 参数启动。 |
| 端口占用 | 关闭冲突程序，或在 `UserData\.env` 修改 `BACKEND_PORT` 后重启。 |
| API 请求失败 | 检查地址、模型、密钥、网络、额度和超时；历史保留。 |
| 角色卡导入失败 | 检查 JSON 编码、大小和 PNG 元数据；普通图片不是角色卡。 |
| 本机任务暂停或点歌失败 | 检查客户端、登录、权限、桌面解锁和窗口焦点。 |
| 没有语音 | 自动朗读默认关闭；检查服务、模型、声音配置和音频设备。 |

## 8. 开发与验证

[开发文档](docs/DEVELOPMENT.md)包含实际环境、隔离启动、构建与 GUI 测试命令；[贡献规范](CONTRIBUTING.md)和 [AGENTS.md](AGENTS.md)约束后续维护。[发布流程](docs/RELEASING.md)说明版本、安装门和草稿审批，[安全审计](docs/RELEASE_SECURITY.md)说明检查范围与限制。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1
```

该命令运行 Ruff / Python 语法、TypeScript / ESLint / Prettier、前端生产构建、Electron 纯单元、隔离 pytest 与源码/历史检查。公开 CI 使用 mock 和合成样例，不依赖私有 API、语音模型、聊天库或音乐客户端。

真实 Windows 安装、窗口与桌面操作另行验收，纯单元通过不能代替这些结果。Hooks 依赖仍有经过审计的既有警告，未为静态检查自动改变订阅生命周期；非阻断问题在工程审计中记录。以 [Actions 实际运行](https://github.com/chuzihang1223/companion-studio/actions)为准，不把尚未运行的 workflow 称为通过。

## 9. 许可证与发行范围

原创代码及应用图标采用 [MIT License](LICENSE)。第三方组件保留各自许可证，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和 [licenses](licenses)。MIT 不覆盖用户自行导入的角色卡、图片、音频、模型和第三方服务。

公开源码包含代码、空白配置示例、必要的合成测试样例和许可文件，不包含用户档案、真实密钥、个人聊天、角色音频、第三方角色素材、依赖安装目录或测试运行输出。发行包可公开不等于用户后续创建的运行档案可公开。

# Companion Studio（伴舟）

**9.0.1 · Windows 桌面应用 · Electron / React / FastAPI / SQLite**

Companion Studio 把持续聊天、可配置角色和本机工作台放在同一个桌面应用中，方便保存对话、管理角色设定，以及从聊天切换到项目和本机任务。用户可以直接使用通用助手，也可以导入角色卡或创建自己的角色。

新安装只包含中性的「通用助手」和独立应用图标，不附带虚拟角色、角色头像、图片背景、聊天记录、API 密钥、声音 ID 或模型文件。模型服务需要用户自行配置。

## 1. 主要功能

| 功能 | 当前实现与条件 |
| --- | --- |
| 文字聊天 | 流式显示回复；Enter 发送、Shift + Enter 换行；加载和错误提示；单条消息最多 4000 字符。 |
| 本地历史 | SQLite 保存聊天与工作会话；支持搜索、切换、复制消息、回复重试和对话分支；重启后保留记录。 |
| 角色管理 | 导入本应用 JSON、Tavern V2/V3 JSON 或含角色卡元数据的 PNG；手动创建、编辑和启用角色；角色设定与聊天历史分别保存。 |
| 个性化与记忆 | 调整身份、性格、称呼、关系、回复风格和规则；手工管理记忆，从有限历史中提取相关信息。 |
| API 配置 | OpenAI-compatible Provider；多配置预设、连接测试、热切换、模型能力及职责设置。密钥由 Electron 主进程通过 Windows 安全存储加密保存。 |
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
- 聊天上下文和主动附加并发送的文件内容会发送到所选模型服务；启用 Fish 时，朗读文本会发送给 Fish。费用和服务条款由对应服务提供方决定。

## 2. 安装方法

### Windows 正式版

普通使用无需安装 Python、Node.js 或 npm。

1. 获取 `Companion-Studio-9.0.1-Setup.exe` 和 `SHA256SUMS.txt`。
2. 用以下命令取得安装包 SHA256，与校验文件中的对应值比较：

   ```powershell
   Get-FileHash -LiteralPath '.\Companion-Studio-9.0.1-Setup.exe' -Algorithm SHA256
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

适用于 Windows 10 / 11。使用 **Python 3.12**、**Node.js 24** 和 npm；依赖见 `backend/requirements.txt` 与 `desktop/package-lock.json`。本地 TTS 使用独立环境。

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
$env:ELECTRON_BUILDER_CACHE = Join-Path $studioCache 'electron-builder'

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
npm.cmd ci --prefix desktop
```

如果没有 `py` 启动器，用已安装的 Python 3.12 执行 `python -m venv .venv`。无需激活虚拟环境；`npm.cmd` 可避免 PowerShell 对 `npm.ps1` 的执行策略限制。

在同一窗口启动开发模式：

```powershell
$env:COMPANION_HOME = Join-Path $projectRoot '.runtime\desktop'
$env:BACKEND_PORT = '8000'
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
.\.venv\Scripts\python.exe -m pip install 'pyinstaller==6.22.3'
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

### 导入或自定义角色

点击侧栏 **角色管理**：

- **导入角色卡文件**：选择本应用 JSON、Tavern V2/V3 JSON 或带角色卡元数据的 PNG；导入后自动启用。
- **创建自定义角色**：名称必填，其他字段可选；填写身份、性格、说话风格、关系或规则，点「保存并使用」。
- 选中已保存角色可以编辑或切换；「通用助手」保留为中性起点。
- PNG 角色卡图片可作头像；普通 PNG 请通过「头像」设置。

JSON 上限 **256 KB**，桌面选择角色卡文件上限 **6 MB**。每次导入生成新的 ID，不覆盖同名角色。选择和各角色历史在重启后恢复。

### 工作模式

1. 切换到 **工作模式**，按需选择项目文件夹。
2. 输入任务，例如「打开记事本」或「列出当前项目的文件」。
3. 查看工作流、结果和授权提示；从 Dock 打开模块并调整布局。
4. 代码草稿可先在代码 / Diff 模块检查，保存、应用和执行遵循当前权限策略。

项目目录不会自动整目录上传。自动操作需要 Windows 解锁，焦点变化可能使任务暂停。后台回复和语音按原会话归属，不插入其他角色当前会话。

### 可选语音和附件

- **语音**：在「设置 → 语音」填写 Fish API Key 和 reference_id，测试后保存并选择引擎，再开启「自动朗读」。IndexTTS 需另行准备独立环境、模型和参考音频。语音不可用时仍可文字聊天。
- **附件**：输入框「+」选择 UTF-8 文本 / 代码、PDF、DOCX、PNG / JPEG / WebP / GIF；每条最多 4 个文件，每个最多 8 MB。

PDF 提取前 30 页文本，没有 OCR，不读取加密 PDF；DOCX 提取段落；长文件选择相关片段，不保证发送全文。图片需 API 配置与模型实际支持视觉能力，GIF 使用第一帧。

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

## 8. 许可证与发行范围

原创代码及应用图标采用 [MIT License](LICENSE)。第三方组件保留各自许可证，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 和 [licenses](licenses)。MIT 不覆盖用户自行导入的角色卡、图片、音频、模型和第三方服务。

公开源码包含代码、空白配置示例、必要的合成测试样例和许可文件，不包含用户档案、真实密钥、个人聊天、角色音频、第三方角色素材、依赖安装目录或测试运行输出。发行包可公开不等于用户后续创建的运行档案可公开。

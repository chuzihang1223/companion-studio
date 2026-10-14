# 开发与验证

本文面向 Windows 源码开发者。安装正式软件、配置模型及日常使用请先读根目录 [README](../README.md)。工程维护前还应阅读 [架构](ARCHITECTURE.md)、[Agent 运行边界](AGENT_RUNTIME.md)、[工具系统](TOOL_SYSTEM.md) 和 [工作台](WORKBENCH.md)。

## 1. 环境与锁定依赖

当前实际验证环境为 Windows、Python **3.12.3**、Node.js **24.20.0**、npm **11.19.0**。其他补丁版本需自行验证；不要据此声明 macOS/Linux 的 Windows 自动化或安装流程已通过。

- Python 运行依赖锁定在 `backend/requirements.lock`。
- `backend/requirements-dev.txt` 引用运行锁，并固定 pytest、PyInstaller、Ruff 等开发依赖。
- 桌面依赖由 `desktop/package.json` 和 `desktop/package-lock.json` 固定；使用 `npm ci`。
- 不要共享其他产品的虚拟环境或 `node_modules`。依赖升级应单独说明理由、锁文件变化、许可和验证结果。

下面的命令在**项目根目录的 PowerShell** 运行，不需要激活虚拟环境：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
npm ci --prefix desktop
```

Electron 首次安装会下载其运行时，安装依赖和收集许可证需要网络。纯本地回归测试无需真实模型密钥或付费请求。

如果希望依赖缓存也保留在项目下，可在安装前设置：

```powershell
$project = (Get-Location).Path
$env:PIP_CACHE_DIR = Join-Path $project '.cache\pip'
$env:npm_config_cache = Join-Path $project '.cache\npm'
$env:ELECTRON_CACHE = Join-Path $project '.cache\electron'
$env:ELECTRON_BUILDER_CACHE = Join-Path $project '.cache\electron-builder'
```

## 2. 隔离开发档案

使用独立 `.runtime/desktop` 档案，避免开发实例读写正式版的聊天、角色和密钥。端口 `8786` 是本文的开发示例；占用时改为另一个空闲回环端口。

```powershell
$project = (Get-Location).Path
$env:COMPANION_HOME = Join-Path $project '.runtime\desktop'
$env:COMPANION_DATA_DIR = Join-Path $env:COMPANION_HOME 'UserData'
$env:BACKEND_PORT = '8786'
npm run dev --prefix desktop
```

Electron 会启动对应后端，等待健康后显示 Vite 界面。停止开发进程应关闭本实例及其拥有的子进程，不能按同名进程批量结束其他产品。API 与语音凭据在这个开发档案的设置界面录入；不要复制正式或个人档案。

仅想运行已构建的源码桌面界面时，保持上面的隔离环境变量：

```powershell
npm run build --prefix desktop
npm start --prefix desktop
```

遇到 GPU 初始化错误可使用 `npm run start:compat --prefix desktop`。这只能选择兼容启动方式，不代表所有显卡环境已验证。

### 单独调试后端

使用另一独立数据目录，并先停止占用该端口的开发实例：

```powershell
$project = (Get-Location).Path
$env:COMPANION_DATA_DIR = Join-Path $project '.runtime\backend'
$env:BACKEND_PORT = '8787'
New-Item -ItemType Directory -Force -Path $env:COMPANION_DATA_DIR | Out-Null
$debugEnv = Join-Path $env:COMPANION_DATA_DIR '.env'
if (-not (Test-Path -LiteralPath $debugEnv)) {
  Copy-Item -LiteralPath '.env.example' -Destination $debugEnv
}
.\.venv\Scripts\python.exe -B backend\run.py
```

只在首次创建调试档案时复制模板，不要覆盖已有 `.env`。后端启动后，可在第二个 PowerShell 窗口读取 `http://127.0.0.1:8787/health`；正常返回 `{"status":"ok"}`。没有配置模型时，健康正常仍不意味着聊天 API 能返回模型回复。

## 3. 自动检查

以下命令在项目根目录运行；它们不会代替正式安装后的界面验收。

```powershell
.\.venv\Scripts\python.exe -B -m ruff check backend tests scripts
.\.venv\Scripts\python.exe -B -m ruff format --check backend tests scripts
.\.venv\Scripts\python.exe -B -m pytest backend\tests tests -q
npm run typecheck --prefix desktop
npm run lint --prefix desktop
npm run format:check --prefix desktop
npm run test:electron --prefix desktop
npm run build --prefix desktop
```

- Python 格式和规则由根目录 `pyproject.toml` 管理。
- TypeScript 启用严格类型检查。ESLint 的 `rules-of-hooks` 是错误门；现有稳定原生订阅的依赖提示仍需人工审阅。
- `backend/tests/conftest.py` 在导入应用前设置隔离数据目录、清空外部服务配置，并限制该目录下测试的外部网络；其他测试使用自己的 fixture/mock。
- `test:electron` 执行九组本地几何、会话、音频队列、Windows 编码和音乐解析回归，**不启动真实 GUI**。
- 不应通过删测试、放宽规则或自动修改 Hook 依赖来获得通过。

需要格式化自己修改的文件时可以使用对应工具。根级批量格式命令为 `.\.venv\Scripts\python.exe -B -m ruff format backend tests scripts` 和 `npm run format --prefix desktop`；执行前确认作用范围，避免把不相关改动混入功能提交。

## 4. 实际 Electron 界面测试

`tests/ui/clean-character-studio.cjs` 与纯单元不同：它启动真实 Electron、后端和一个本地模拟模型，操作角色导入、界面入口与重启。它不需要真实云模型或语音请求。

测试使用固定端口：renderer `9976`、主进程检查 `9977`、后端 `8778`、本地模型 `8780`；执行前确保空闲。同一轮只运行一个该脚本实例，使用新档案目录。

开发构建的测试：

```powershell
$project = (Get-Location).Path
$testId = Get-Date -Format 'yyyyMMdd-HHmmss'
$env:CLEAN_TEST_HOME = Join-Path $project ('.runtime\ui-tests\' + $testId)
Remove-Item Env:COMPANION_TEST_EXE -ErrorAction SilentlyContinue
Remove-Item Env:CLEAN_TEST_FRONTEND -ErrorAction SilentlyContinue
npm run build --prefix desktop
node tests\ui\clean-character-studio.cjs
```

脚本实际支持以下环境变量：

| 名称 | 用途 |
| --- | --- |
| `CLEAN_TEST_HOME` | 隔离测试档案及报告输出；建议显式指向新的 `.runtime/ui-tests` 子目录。 |
| `CLEAN_TEST_FRONTEND` | 源码模式需要测试另一份已构建界面时指定 dist 路径。 |
| `COMPANION_TEST_EXE` | 指定目录版或安装版的真实 `CompanionStudio.exe`；此时不传源码目录参数，使用包内界面。 |

测试指定安装版时，将 `COMPANION_TEST_EXE` 设为待验收程序的完整路径，再使用新的 `CLEAN_TEST_HOME` 运行相同脚本。这里的调试端口仅供本地验收；日常启动不添加 remote-debugging/inspect 参数。

报告和截图位于测试档案中，不应直接上传。必须实际运行成功才能写“通过”；检查到 `.workspace-grid` 存在不能证明拖拽跟随、缩放帧率、自动滚动、音乐客户端操作或实际声音播放正常。详见 [工作台测试边界](WORKBENCH.md#6-验证范围和限制)。

## 5. Windows 构建和发布

```powershell
npm run build:public --prefix desktop
```

构建脚本从锁定依赖生成前端、冻结 Python 后端及 Windows 安装器。默认运行工程检查和本地回归，输出到 `build/<BuildId>/delivery`，包含目录版、Setup、构建清单和安全审计。构建 metadata 写入 staging，不能因构建覆盖跟踪中的源码。

`-SkipTests` 只适用于明确记录原因的本地诊断产物，不能作为正常发布流程；清单会记录跳过状态。提交 tag 前需完成 [发布流程](RELEASING.md) 和 [安全检查](RELEASE_SECURITY.md)。

GitHub 的 `v*` tag 工作流准备**草稿 Release**。自动构建成功不等于正式发布：Windows 安装、启动、持久化、窗口操作等检查仍需完成，最后由人确认公开。没有授权不能自动提交真实模型请求、上传用户档案或调用外部编码账号。

## 6. 常见问题

| 现象 | 检查方法 |
| --- | --- |
| 找不到 package.json 或虚拟环境程序 | 先确认 PowerShell 位于项目根目录，再使用本文相对路径。 |
| Electron 运行时未下载完整 | 检查 npm/Electron 下载日志和网络；按锁重新执行 `npm ci`，不要换入其他产品的依赖。 |
| 后端端口被占用 | 识别占用者；为开发档案选独立端口，不关闭不相关程序。 |
| UI 测试不能连接调试端口 | 检查四个固定端口、测试档案的 `launch.log`、是否启动了正确 executable。 |
| 聊天失败但健康正常 | 在当前档案设置中核对服务地址、模型、能力及凭据；不要在 issue 中粘贴密钥。 |
| 系统无法解密已存密钥 | Windows 安全存储可能绑定账号/环境；由档案所有者重新录入，不把私密文件导入测试或公开仓库。 |

排障资料应只包含经过脱敏的错误、版本和复现步骤。删除或迁移用户数据前必须获得用户授权并确认备份；缓存清理也不能覆盖用户角色、聊天或声音配置。

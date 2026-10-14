# Windows 发布流程

版本以 `desktop/package.json` 和锁文件为准。已有 tag、公开 Release 与资产保持不变；安装器由同一构建入口生成，不提交进 Git 源码历史。

## 1. 准备维护分支

先阅读 [工程审计](ENGINEERING_AUDIT.md)、[开发文档](DEVELOPMENT.md) 和 [安全检查](RELEASE_SECURITY.md)。业务修复与注释、类型、格式整理分别提交。修改版本时同步 `package-lock.json` 和开发用 `electron/build-info.json`；NSIS 文件名使用 `${version}`。

```powershell
npm.cmd version 0.1.1 --prefix desktop --no-git-tag-version --ignore-scripts
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1
npm.cmd run build:public --prefix desktop
```

示例版本只用于本次准备，后续按实际版本替换。不可复用已经公开的版本。

## 2. 构建的真实范围

`build-companion-studio.ps1` 默认运行 Ruff、TypeScript、ESLint、Prettier、前端生产构建、Electron 纯单元、pytest，再冻结后端并生成 Windows x64 目录版与 NSIS 安装器。新建独立构建目录，依赖来自精确锁文件；Python/Node 版本见 DEVELOPMENT。

构建信息写进 staging，源码中的开发 metadata 不会被覆盖。清单记录源文件摘要、安装包摘要和冻结后端健康检查。可重复执行指相同入口与锁定环境可重新构建；时间戳、编译器及平台工具可能使二进制摘要不同，不保证字节级确定性。

输出 `build/<BuildId>/delivery/`：

- `Companion-Studio-<version>-Setup.exe`
- `SHA256SUMS.txt`
- `build-manifest.json`
- `security-audit.json`
- `win-unpacked/`

`-SkipTests` 仅供诊断，清单记录跳过状态；跳过产物不能通过正式发布门。没有签名证书的构建不具备发行者数字签名，不代表 Windows 官方认证。

## 3. 安装与运行门

在独立可写目录真实运行 Setup，保护已有安装注册信息及快捷方式，禁止安装测试覆盖正式档案。验证以下内容并记录实际结果：

1. EXE、ASAR、冻结后端与清单摘要一致，桌面快捷方式可用。
2. 首次启动通用助手、正确缺少 API 引导，无私人资源。
3. Chat/Work、本地 mock 回复、工作台模块、浏览器工作区与应用发现。
4. 独立档案保存、正常关闭后端退出、重启恢复。
5. 真实窗口按钮、拖动/缩放与本机操作边界。

纯单元或 DOM 元素存在不能代替真实 Windows 验收。没有执行的项目写“待人工验收”。不要为了测试解密或复制其他人的密钥，也不调用收费模型作为默认发布条件。

## 4. PR、tag 与草稿

推送维护分支并创建 PR；`CI` 必须实际通过。代码审阅、源码/历史/产物审计完成后，用审阅过的 commit 创建新的版本 tag。tag 工作流使用相同构建入口和固定 action commit，在 Windows runner 上完成自动门后创建 **Draft Release**。

```powershell
git tag -a v0.1.1 -m "Companion Studio 0.1.1"
git push origin v0.1.1
```

草稿至少包含 Setup、校验文件与正文发布说明；额外保留构建清单和审计报告。工作流不会覆盖已公开 Release，遇到已有草稿会停下，避免静默替换审核过的资产。Action artifact 是维护者审阅副本，不应在 README 中称为公开安装下载。

## 5. 最终公开

自动 CI、构建、安全门、真实安装与必要运行验收全部完成后，针对该版本取得最终公开确认，再发布草稿。发布后才能把 README 的“安装包准备中”改为实际存在的下载链接。若从草稿资产重新构建，必须重新核对摘要与安装结果。

保留 `UserData`，安装器不清空角色、聊天、API 设置或布局。发布目录和安装运行档案分别管理；用户创建的数据永远不作为 Release 资产。

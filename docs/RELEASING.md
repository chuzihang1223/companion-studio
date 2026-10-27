# Windows 发布流程

## v0.3.0 维护者发布决定

维护者已明确授权 v0.3.0 Stable 发行，并将独立第三方安全签署从本次强制发布条件中移除。必须披露：`Independent third-party security review: Not performed / Owner accepted release with disclosed limitations.` 历史 GATE-07/GATE-13 的 BLOCKED 与原四域合同仍保留，不改成 PASS，也不表示获得第三方签署。平台拒绝的 Reviewer 不重试或绕过。

本次使用当前完整 release 分支源码，完成正常构建、公开源码/历史/产物检查与实际发行验收后，通过获授权的 Git/GitHub CLI 正常快进 main、创建不可移动的 v0.3.0 标签和 Stable Release。保留已有发行，不强推。版本、Commit、安装器和发布资产须一致；不能重命名旧 rc 安装器。下面保留通用的 PR/CI/草稿流程说明，手动发行时不得让重复的自动草稿覆盖已核验资产。

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

新安装器为明确的公开 Electron 运行文件准备 Windows LPAC 读取权限，以兼容含特殊包 SID 的程序目录。文件和必要目录只添加自身 Read/Execute，不移除原 ACL、不添加写权限、不向未来档案继承；用户数据、缓存和父目录不在处理范围。沙箱仍保持开启。安装逻辑见 `desktop/assets/prepare-runtime-access.ps1` 和 `installer.nsh`。

受控安装测试入口如下，必须先关闭其他 Studio 实例。它保护当前产品的两项注册信息和两条快捷方式，在新档案安装、验收、卸载后恢复原状态；测试报告和备份保留。发现其他实例、机器级同 GUID 安装或无法确认归属时停止。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/test-installer.ps1 -Delivery '<delivery目录>' -DryRun
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/test-installer.ps1 -Delivery '<delivery目录>' -RunUI
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/test-installer.ps1 -Delivery '<delivery目录>' -RunUI -PreserveInstalledSnapshot
```

`-DryRun` 不安装或修改注册信息。真实测试若报告 `recovery_deferred=true`，应先核对仍运行的安装/卸载进程，再安全恢复保留备份；不可盲目覆盖同名注册项。NSIS 卸载本次测试程序，不删除其独立档案。

`-PreserveInstalledSnapshot` 必须同时使用 `-RunUI`。安装和界面验收通过后，脚本在本次新测试目录的 `InstalledSnapshot/win-unpacked` 保留安装器实际释放的公开程序，核对 EXE、ASAR、冻结后端与构建清单的摘要，并记录全部复制文件的摘要。它不复制产品根目录的 `UserData`、`Cache` 或测试档案；发现程序目录内的运行档案、私密配置或重解析链接会停止，根目录的 NSIS 卸载器不进入快照，未知临时内容不会被静默略过。

只有最终 `installer-test-report.json` 的 `success=true` 才表示快照可进入下一步审核：保留快照不能替代卸载和原注册信息、快捷方式恢复。随后仍须对快照执行 `audit-release.py` 完整产物审计；复制摘要不等同安全审计。快照和测试报告保留在本地，不自动发布、覆盖正式程序或导入用户档案。复制到新的正式程序位置后，应在该位置运行随程序提供的 `resources/prepare-runtime-access.ps1`，不要复制其他目录的权限或用户资料。

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

若 tag 已存在而构建因 runner 环境失败，修正默认分支的发布驱动后，可通过 `workflow_dispatch` 对同一 tag 受控重试，不移动标签。工作流仍精确检出 `refs/tags/<tag>`，核验 HEAD、标签和 package 版本一致，使用标签内的构建入口及发布说明；驱动工作流的修订与产物源码修订应分别记录。

```powershell
gh workflow run release.yml --ref main -f tag=v0.1.1
```

Windows runner 的 `pwsh` 调用 npm 后会进入 Windows PowerShell 5.1。发布驱动显式加入系统内置模块目录并预检 `Get-FileHash`，避免继承 PowerShell 7 的模块路径而在打包时缺少校验命令；不安装新的系统模块。

## 5. 最终公开

自动 CI、构建、安全门、真实安装与必要运行验收全部完成后，针对该版本取得最终公开确认，再发布草稿。发布后才能把 README 的“安装包准备中”改为实际存在的下载链接。若从草稿资产重新构建，必须重新核对摘要与安装结果。

保留 `UserData`，安装器不清空角色、聊天、API 设置或布局。发布目录和安装运行档案分别管理；用户创建的数据永远不作为 Release 资产。

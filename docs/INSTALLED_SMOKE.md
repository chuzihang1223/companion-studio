# Windows 安装版隔离验收

`tests/ui/installed-studio-smoke.cjs` 是需要明确执行的真实 Electron 安装版测试，不加入默认纯单元测试。它驱动安装版的实际渲染进程、preload、IPC 和本地后端；模型回复来自仅监听回环地址的 mock。

## 运行

先构建并安装待验收版本。保持 Windows 解锁、桌面空闲，退出其他使用相同验收端口的程序。需要系统 Node.js，脚本不安装依赖或调用付费服务。

```powershell
# 在项目根目录运行；把第一项替换成待验收安装版的实际 EXE 路径。
$env:COMPANION_TEST_EXE = '<安装版目录>\CompanionStudio.exe'
$env:COMPANION_TEST_HOME = Join-Path $PWD '..\Smoke\installed-new-profile'
node .\tests\ui\installed-studio-smoke.cjs
```

验收目录必须不存在或为空。不能填写真实 `UserData` 或复用旧验收档案；脚本不会清空已有目录。未设置 `COMPANION_TEST_HOME` 时，在源码父目录的 `Smoke` 内创建带时间戳的独立目录。报告、日志及截图只写入该目录，不能作为公开源码或安装包内容。

程序和本地 mock 使用动态选择的回环端口。测试采用假的本地 API Key，启动时清空继承的模型和语音凭据，不导入个人角色或聊天。默认不开启语音、不启动第三方软件、不运行编码任务。

脚本为隔离验收实例开启回环 CDP/主进程 inspector。主进程表达式由测试脚本自身生成，使用 `process.getBuiltinModule('module').createRequire(process.execPath)` 获得测试专用 CommonJS 加载器；加载器不会暴露给渲染层。这个调试入口不能用于日常启动，也不能监听公网。

## 真实覆盖

- 核验已打包程序版本信息、干净发行标志和通用助手，初始消息与模型凭据为空。
- 在实际 Chat 输入框发送消息，后端调用本地 OpenAI-compatible mock，并保存用户/助手记录。
- 在实际 Work 输入框发送“查看系统状态”，执行系统观察，接收步骤/工具事件，验证实际任务反馈的界面显示及保存。确定性任务可直接本地反馈，不要求额外模型调用；报告只记录本地模型请求数的变化与实际反馈，不据此推断反馈的确切来源。
- 通过 Chromium `Input.dispatchMouseEvent` 真正长按拖动 Dock 模块、拖动共享分割线，验证相邻模块尺寸、松开后捕获清理和布局存储。没有直接调用布局计算函数来模拟通过。
- 通过界面网页工具打开并关闭真实 `WebContentsView`，验证页面正文、关闭销毁及无应用 preload/Node 能力。
- 通过实际应用发现与目录接口检查记事本入口；报告区分动态候选和有效系统入口。
- 经窗口正常关闭，检查本实例后端停止，再实际重启；核验 Chat/Work 消息 ID、正文和工作台布局恢复，并在界面打开相应历史。

### 本机网页 fixture 的边界

生产 URL 策略会拒绝 `https://127.0.0.1`，测试先核验这一点。页面用保留测试域名 `https://companion-studio-smoke.test/fixture`，由**仅当前隔离验收进程的浏览器 partition**通过 Electron `protocol.handle('https')` 返回固定本机 HTML。其他网页域名请求被 fixture 拒绝；不需要 DNS、外部网页、证书例外或删除生产 URL/权限验证。完成后解除 fixture。

此方式验证实际网页视图生命周期和隔离，不证明公网连通、代理、TLS、验证码、真实站点搜索或登录能力。Electron 的 session-specific handler 行为见 [官方 protocol 文档](https://www.electronjs.org/docs/latest/api/protocol#using-protocol-with-a-custom-partition-or-session)。

## 可选真实记事本启动

仅在允许操作桌面、且桌面解锁空闲时增加：

```powershell
$env:COMPANION_TEST_START_NOTEPAD = '1'
node .\tests\ui\installed-studio-smoke.cjs
```

脚本在隔离档案中通过软件的可信动作接口许可 `app.launch:notepad`，然后调用正常 `launchKnownApp` 路径；不会绕过窗口验证或使用脚本直接启动记事本。已有窗口可能被聚焦，结果会记录是否原已运行。失败会使验收失败并保存错误，不伪造成功。

脚本不自动关闭用户记事本，以免误关已有窗口或未保存内容。测试完需人工确认并按需退出。不开此开关时报告明确标记实际启动尚未验证。

## 其他边界

诊断时可显式设置 `COMPANION_TEST_SKIP_BROWSER=1`，独立收集 Workbench 和重启检查。此时报告包含 `skipped`，即使其余项通过仍为 `success:false`、`enabledChecksPassed:true`，退出码为 2；不能当成安装版全部通过。正常完整验收不设置此开关。

这组测试没有覆盖角色卡导入/编辑的全部路径；已有 `clean-character-studio.cjs` 使用另一独立档案，覆盖自定义角色、JSON/PNG 导入、无效卡拒绝、角色历史隔离及重启，继续保留。

自动化通过不代表全部人工验收结束。仍需人工检查高 DPI/多显示器、窗口抢焦点、长时间连续使用、视觉可读性、声音主观质量及真实客户端播放。30 次 CDP mouse move 的墙钟耗时不是 FPS 基准。真实模型、语音、付费编码和音乐服务均未测试。

`report.json` 的 `success` 表示本次启用的检查通过，`manual` 列出没有执行或需要人工确认的项目。必须结合开关与报告阅读，不能把默认跳过的实际 OS 启动写成已通过。

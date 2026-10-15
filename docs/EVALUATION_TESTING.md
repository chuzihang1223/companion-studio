# Evaluation 验证入口与边界

在项目根目录的 PowerShell 使用当前锁定环境：

```powershell
.\.venv\Scripts\python.exe -B -m pytest backend/tests/test_benchmark_case.py backend/tests/test_evaluation_system.py -q --basetemp .cache/evaluation-test-temp
node tests/ui/evaluation-unit.cjs
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1
```

20 Case 的 API 集成测试真实经过 Loader → Runner → 隔离 worker 的 Agent Runtime →
现有 Trace → 证据评分 → Metrics → SQLite → API。另验证不授权 Live、GUI 环境阻断、
分页、重复评分保护、追加迁移、已有消息保留、重启恢复、取消/暂停、超时、CSV 公式防护。
Mock 20/20 是回归结果，不是真实 LLM 或 Windows GUI 成功率。

前端单元测试真实主进程固定 IPC/布局校验、空状态、指标缺失、100 项分页到 20 行、
1000 条合成指标采样 80 点。CPU/SSR 时间不代表 renderer FPS 或原生窗口焦点。

CI 扩展现有 ci.yml 的 Evaluation Mock 步骤，不访问外部模型，上传测试 XML，
不上传测试档案或私密 Trace。未推送本分支时，没有该提交在远程 CI 的执行结果。

安装版端到端测试使用独立 COMPANION_TEST_HOME 和真实已打包 EXE，运行本地 Mock，
验证面板、20 Case、100 Attempt 分页、取消、重跑、条件比较、三格式导出、Trace/Replay、
退出及重启历史；真实 Chromium 调试接口驱动 UI，不代表 Windows GUI Agent 测试。
具体入口 `tests/ui/evaluation-e2e.cjs`，仅在已准备的隔离安装/目录版中运行。

```powershell
$env:COMPANION_TEST_EXE = 'D:\Review\Release\installed\CompanionStudio.exe'
$env:COMPANION_TEST_HOME = 'D:\Review\NewEvaluationProfile'
node tests/ui/evaluation-e2e.cjs
```

示例路径需替换为本机新空目录。完整安装测试先读 scripts/test-installer.ps1 并执行
DryRun，再使用该脚本的注册表/快捷方式备份恢复方案。不覆盖公开 v0.1.2 或用户档案。
测试结果以本次实际生成报告为准；真实模型未授权不执行，真实 GUI 未接入标 BLOCKED。

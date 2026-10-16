# Recovery Evaluation 与故障验证

复用既有 EvaluationEngine、隔离 worker、Trace、grader、指标、API 和 Dashboard。
「评测测试集」可选 Studio Core（原 20 项不变）或 Recovery Core 1.0.0（16 项）。
Recovery 默认 Mock，支持独立文件 Sandbox；Live Model 被拒绝，专用 GUI 桥未接入则 BLOCKED。
没有恢复专属云模型、付费评分模型或伪造成本。

## 16 项用例

|用例|实际故障与判据|
|---|---|
|REC-001|文件首次读取超时，有限重试并保持真实文件摘要|
|REC-002|执行边界外部修改文件，阻断覆盖|
|REC-003|已写入但响应丢失，哈希核验后跳过写入|
|REC-004|Mock 目标窗口已关闭，不自动重新打开|
|REC-005|Mock 焦点丢失，读取新窗口证据后重新聚焦|
|REC-006|Mock 已知搜索 DOM 改变，重新观察再固定 GET 搜索|
|REC-007|Mock 网页主机不符，人工检查|
|REC-008|Mock 只读网络超时，有限重试|
|REC-009|拒绝权限，实际工具执行为 0|
|REC-010|实际 TaskHandle 暂停及继续|
|REC-011|实际取消，终态不可续接|
|REC-012|worker 真正 os._exit(86)，父进程核对退出码，再启动新进程读取同一检查点；文件 mtime 不变|
|REC-013|两个相同 Resume 请求，只有一个新执行|
|REC-014|模拟脚本副作用不明，阻断重放；没有实际启动脚本|
|REC-015|更换工作区，拒绝旧检查点执行|
|REC-016|恢复事件、工具、检查点和最终 Trace/目标状态一致|

REC-002/003/013/015 为逻辑崩溃边界；REC-012 独立验证真正进程终止、SQLite/WAL 和重开。
桌面/网络/DOM 故障为 Mock，文件读写、权限、Runtime、租约、Trace 和评分是真实代码。
PASS 包括“按要求安全阻断”，不能称 16 个用户目标都成功。

## 实际 Runtime 组件对比

本机 Windows 11 / Python 3.12.3 / 锁定依赖。读取基线提交
`1406b02133188a3f602c7db4f4a220cb690d7334` 的原 `AgentTaskRuntime`，与新 Runtime
分别运行相同 REC-001/005/006/008 故障、同样的当前 AgentService、Model/Tool Schema、
Prompt、合成输入、权限与临时目录。每项每版本一次；模型完全 Mock，外部请求 0。

|指标|原 Runtime|新 Runtime|
|---|---|---|
|确定性目标完成|1/4（25%）|4/4（100%）|
|步骤额外重试|5|4|
|逻辑 Mock 模型请求|8|8|
|平均流程耗时|765.156 ms|961.235 ms|
|资源释放|4/4|4/4|

原版仅网络超时恢复成功；焦点/DOM 失败时原版重复尝试但未重新观察，新版先获取证据。
新版读取超时增加了安全重试，因此完成任务的时间不能与旧版提前失败等同比较。
这是小样本 **Runtime 组件**比较，共享当前上层代码；不是完整旧版安装包 A/B、
真实 LLM 排名或性能优越性证据。没有生成旧版不存在的 Checkpoint 指标。
该对比在恢复核心实现后、轨迹写入争用修复之前完成，不是最终安装版的性能成绩。
完整原 Studio Core 基线见 [RECOVERY_BASELINE](RECOVERY_BASELINE.md)。

轨迹争用修复后另跑原 Studio Core 20 项 × 5、并发 2：100/100 回归断言通过，
轨迹写入失败 0、丢失事件 0；12 次为已回滚投影事务的重试，不是重复执行工具。
耗时均为本机 Mock，不能作为真实模型延迟或性能排名。

## 指标含义

- 恢复成功率 = 有实际成功结果的恢复尝试 / 实际恢复尝试；拒绝后安全停止不算恢复成功。
- 合成目标完成率单列；通过率是回归断言，不能替代用户目标完成率。
- 恢复耗时按实际 perf_counter 及已结束 Attempt 累计；硬退出未结束片段没有伪造耗时。
- REC-012 总任务耗时为 null：单调时钟不能跨进程，仅新续接片段有恢复耗时。
- Checkpoint 核验率按实际核验次数；重复预览会增加次数，不是“有检查点的任务比例”。
- 重复执行针对这些固定文件用例的实际工具入口/mtime，不证明所有外部系统 Exactly Once。
- Trace 工具计数包含已进入执行器但进程在返回前退出的操作，仍不证明外部目标完成。
- Token、费用无真实观测则 N/A；真实 AI 任务成功率为未测量。

## 复现

在项目根目录 PowerShell 使用锁定环境：

```powershell
.\.venv\Scripts\python.exe -m pytest -q backend/tests/test_recovery_policy.py backend/tests/test_checkpoint_manager.py backend/tests/test_resume_coordinator.py backend/tests/test_recovery_evaluation.py --basetemp .cache/recovery-tests
node tests/ui/recovery-unit.cjs
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1
```

安装版使用新的空档案，不复制正式 chat.db 或凭据：

```powershell
$env:COMPANION_TEST_EXE = 'D:\Review\Release\installed\CompanionStudio.exe'
$env:COMPANION_TEST_HOME = 'D:\Review\NewRecoveryTestProfile'
node tests/ui/recovery-e2e.cjs
```

该测试实际中断它自己启动的 Electron，检查后端父进程监视器退出，再重开并从 UI 续接只读状态；
另从 UI 运行 16 项冻结 worker 评测及三格式导出。不会结束同名用户进程或调用外部模型/语音。
路径是示例；测试目录必须新建且为空。安装器先 DryRun，再按 `test-installer.ps1` 的精确保护范围测试。

最终安装版数量、SHA256、指标和未测项以本次独立交付报告为准，未完成验证不写成已通过。

## 本次安装版实测

`0.2.0-recovery.1`、build `studio-0.2.0-recovery.1-review4-20261016`，冻结后端实际执行
Recovery Core、并发 2：16/16 回归断言通过；目标完成 9/16（56.25%）；恢复尝试 8、成功 8，
累计恢复耗时 2013 ms；工具重试 5、局部重新规划 2、重复执行 0；核验 8 次、有效 6 次，
未确认外部副作用 2、合成用户介入 1。费用 / Token N/A，真实 AI 成功率未测量。
REC-012 实际硬退出 worker 并由新进程续接，文件 mtime 未改；总时长 null。

另有 22 项实际 Recovery UI 验证：中断本测试自己的 Electron，确认后端退出；重启后
在工作流中预览、检查并续接真实只读系统状态，结果通过 SSE 回到原会话，未追加模型请求。
Trace 关联、完成任务禁止重复、会话切换、三格式导出和再次重启持久化均通过。
这是真实安装版 Chromium 界面与本地系统读取，不等同于原生 GUI Agent 或真实模型质量测试。

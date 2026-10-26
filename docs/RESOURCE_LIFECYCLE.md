# 工作事件与准备计划的资源生命周期

WorkState 的每个 SSE 客户端拥有独立有界队列。同一工作会话可以同时有多个客户端；
关闭、取消或异常退出只移除该客户端，最后一个退出才删除订阅键。数据库行先转换成
有序 SSE 消息，再离开读会话后输出；慢客户端不会在 yield 处占用数据库连接。

AgentService 的内存计划保留 300 秒，现有 15 秒 Runtime Watchdog 及新规划会清理
到期上下文。持久化的待权限计划仍按原规则恢复，因此这不是持久计划永久失效期限。
清理只检查 AgentService.pending；不会按年龄清扫 Workflow 或 Recovery 持有的 Handle。

准备时记录实际 TaskHandle 身份。删除前核对该身份、其他待执行计划、执行引用、
RecoveryExecution 和 Runtime Registry 所有权。Service.execute 在首次异步 Trace
准入之前建立引用，保持到最终回复结束；取消或准入失败也通过 finally 释放。
现有 Watchdog 的清理回调失败只记录固定警告，不终止原有任务停滞检查。

这些限制用于避免已结束订阅键与过期准备引用累积，不保证整个应用完全没有内存泄漏。
资源测试直接验证字典、队列弱引用、连接归还、Handle 身份和取消；真实小时负载另行验收。

## 可重复验证

普通回归包含 `test_work_stream_resources.py`、`test_agent_prepared_resources.py` 以及
`test_trace_deletion_accounting.py`。最后一组确认并发删除和保留清理不会丢失累计账本更新，
并验证拒绝、异常回滚和外部 SQLite 写事务。

额外生命周期检查需要显式执行，使用原服务 Host、真实 SQLite/MCP 子进程和合成模型／语音：

```powershell
$env:PRODUCTION_RESOURCE_REPORT = Join-Path (Get-Location) '.cache\resources\lifecycles.json'
.\.venv\Scripts\python.exe -m pytest backend\tests\production_resources_workload.py -q
```

它记录三次创建／关闭、真实空闲采样及事件循环退出后的线程状态；不能代替原始真实小时。
原小时负载追加单独的 60 秒空闲观察，仍要求至少 360 个混合周期、60 个 MCP 生命周期、
12 次调度完成及 60 次取消。诊断观察器的有界 Python 容器大小单独记录，不冒充 RSS
增长的完整解释。样本、源文件摘要与限制保存在本地报告中，不将原始档案作为发行资源。

Python 线程数与 Windows 进程线程数分开记录。后者使用只读的
[Tool Help 线程快照](https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/nf-tlhelp32-createtoolhelp32snapshot)，
仅统计当前进程所有者，不保存其他进程或线程标识，每次采样后关闭快照句柄。

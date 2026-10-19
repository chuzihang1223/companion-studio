# Scheduler Core · 30 项

原 Agent Evaluation 中选择 `scheduler-core`。使用现有独立子进程、SQLite、原 Runtime、
权限、MCP SDK、Trace、Checkpoint 与报告。没有另写一个宣称“通过”的平行评测执行器。

| 编号 | 内容 |
| --- | --- |
| SCH001–008 | 创建、编辑、一次性去重、Daily、Weekly、Interval、时区转换/编辑 |
| SCH009–012 | DST gap/fold、时钟回拨、三天漏执行合并 |
| SCH013–018 | 文件创建/修改、连续事件合并、状态 false→true、冷却、来源撤销 |
| SCH019–024 | 多任务、重叠、容量、中断原恢复、租约到期、重复实例 |
| SCH025–030 | 受控 MCP 未知副作用、授权过期、依赖失效、原工具、视觉未授权、通知/Trace一致 |

绝大部分调度时间使用 FakeClock，不修改系统时钟。模型输出和用户审批为合成输入；
本地 SDK 管道、文件、事务和原工作流协程真实执行。失败场景的正确阻断可以是断言 PASS，
不代表那个用户目标已完成。真实 AI 质量、系统睡眠和原生通知点击需另做测量。
Windows GUI 评测与收费模型模式不适用此合成数据集，明确 BLOCKED 或拒绝，不能冒充实机通过。

测试入口：

```powershell
.\.venv\Scripts\python.exe -m pytest backend/tests/test_scheduler_evaluation.py -q
node tests/ui/scheduler-e2e.cjs
```

后一条需要按开发文档设置新的隔离档案、当前候选可执行文件和独立端口，不能指定正式档案。
它验证一分钟真实时间、托盘隐藏后的执行、历史、Trace、通知账本和彻底退出。
性能报告另区分真实测量延迟与 FakeClock，不混合计算用户可感知的 P50/P95。

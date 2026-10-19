# 调度数据与状态

SQLite 通过原增量迁移入口注册新增表，原聊天、工作流、权限、知识与检查点表不重建。
重复执行迁移应保持原数据。`schema`、SQLAlchemy 模型和实际状态机测试共同定义接口。

| 实体 / 表 | 含义 |
| --- | --- |
| `schedule_definitions` | owner、revision、定义 JSON、依赖摘要、next_due、条件基线 |
| `scheduled_occurrences` | 每次应发生的触发、稳定 UUID、fingerprint、状态、Lease、原运行链接 |
| `scheduled_runs` | 已领取的一次运行，和原 WorkflowRun 分开，保存验证摘要 |
| `scheduler_leases` | 本档案调度主实例与 PID 创建身份 |
| `scheduler_settings` | 全局暂停 |
| `scheduler_sources` | 当前项目或原 WorkflowRun 的有期来源授权 |
| `scheduler_events` | 固定事件种类、随机 ID、UTC 时间 |
| `scheduler_notifications` | PENDING / ATTEMPTED / SHOWN / FAILED / UNAVAILABLE |

准确表名以 `backend/app/models/schedules.py` 为准。

定义状态：ENABLED、PAUSED、DISABLED、ARCHIVED。删除为归档，保留执行历史。
运行机会依次进入 QUEUED → CLAIMED → RUNNING，或 WAITING_PERMISSION。
COMPLETED / FAILED / SKIPPED / CANCELLED / INTERRUPTED / NEEDS_REVIEW / MISSED 为终态。
普通观察器不能复活终态；恢复必须走单独的原 Recovery 适配和 CAS。

领取使用 `BEGIN IMMEDIATE`。`schedule_id + fingerprint`、occurrence UUID、
原工作流 UUID、ScheduledRun UUID 有唯一约束。原 WorkflowRun 与 occurrence 链接在同一事务
提交，提交后才启动协程；若提交后启动失败，进入 NEEDS_REVIEW，不再调用 start 创建第二个运行。

默认约束：全档案 100 个启用定义；排队 32；调度运行全局 2、每工作会话 1；
每会话每小时最多创建 12 次机会；间隔至少 300 秒；连续次数默认 100、最多 1000。
单计划积压默认 1、最多 5；补跑最多 1；机会默认一小时过期。
执行时限默认 600 秒、可设 5–1800 秒。原工作流更严格的预算仍有效。
所有额度包含故障记录，恢复不重置原工具、模型和累计执行时间预算。

时间存 UTC；界面显示本地时间。一次性输入为不含偏移的本地日期时间，并显式指定时区。
编辑 revision CAS 失配返回冲突；废弃旧的未执行触发，不覆盖已完成历史。

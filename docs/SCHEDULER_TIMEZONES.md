# 时区与错过执行

使用 Python `zoneinfo` 和锁定的 `tzdata`（Windows 安装版包含数据）。
界面优先使用 Intl 提供的本地 IANA 名称，例如 `Asia/Shanghai`。
支持代码中明确列出的 15 个常用 Windows 别名，如 `China Standard Time`；
不在映射表中的 Windows 名称需要改填 IANA，不按固定 UTC 偏移猜测。
这不是完整 Windows/CLDR 时区映射。

- ONCE：本地日期时间转为唯一 UTC 机会；DST 不存在时间拒绝保存。
- DAILY / WEEKLY：按目标时区的下一个日历日期求值，不能简单加 24 小时。
- INTERVAL：按持久起点的 UTC 固定秒数推进，不受本地 DST 改变。
- DST 缺失的循环时间跳过该日期；重复时间固定 fold=0，每本地日期只一次。
- 预览返回 UTC 与目标时区时间；周任务预览后续三个实际机会。

例如纽约春季 02:30 缺失时跳至下一个合法日期；秋季 01:30 只采用第一次。
模拟测试控制时间，不改 Windows 时钟、时区或系统休眠设置。

应用每秒重新读 UTC 事实；唤醒等待用 monotonic。系统时间回拨不会重复领取已有机会。
执行预算使用原 Workflow 累计 monotonic active_ms，停机不消耗执行时长，恢复也不获得新预算。

关机、睡眠、休眠、完全退出期间不能按时运行。重新打开时按策略处理：

| 策略 | 行为 |
| --- | --- |
| SKIP_MISSED（默认） | 超出扫描容差后记录 MISSED，不补执行 |
| RUN_ONCE_AFTER_RESUME | 在 expiry_window 内合并为最多一次，仍复查权限 |
| REQUIRE_REVIEW | 记录 NEEDS_REVIEW，用户检查后决定下一步 |

超过 expiry_window 的机会始终过期；排队领取时再检查年龄。
漏掉三天不会重建数百个历史任务，只保存合并结果和新的未来时间。
一次性已执行的计划重新启用不会抹掉原历史；如需另跑使用“立即运行一次”，明确创建新机会。

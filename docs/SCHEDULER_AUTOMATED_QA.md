# Scheduled Agent 自动质量门

使用独立、全新 QA 目录，保留每次失败和复测日志。源码、HEAD、数据集、安装包、档案
保护摘要和测试入口都应绑定，修改后不能沿用旧结论。普通单元测试不证明实机 GUI。

| Gate | 范围 |
| --- | --- |
| A | Skill01–07 基线与回归，保持既有未测限制 |
| B | Ruff / Syntax / TypeScript / ESLint / Prettier / source-history / license |
| C | 完整 pytest、迁移、调度、DST、CAS |
| D | 固定 IPC、React 状态交错、通知导航和托盘对象单元 |
| E | 权限、来源、关键词限制、链接、隐私、跨会话 |
| F | 退出、SQLite Busy、超时、未知 MCP 效果、重复进程、事务注入与恢复 |
| G | 隔离候选真实 GUI：一分钟任务、托盘执行、历史、Trace、删除和 Quit |
| H | 实测 CPU / 内存、100 计划、1000 机会、延迟与资源清理 |
| I | 构建、DryRun、实际安装、完整文件审计、GUI、卸载和系统信息恢复 |
| J | 独立 Runtime、安全与 QA 审查，修复后复测 |

每项记录 PASS / FAIL / BLOCKED / NOT_RUN、Command、Exit Code、Evidence Path、
Timestamp、Findings。缺文件、摘要失配、旧源码或未运行均不能成为 PASS。
最终本地报告存于 QA / 交付目录，不提交原始私人路径、日志或用户档案。

沿用原 `scripts/check.ps1`、`scripts/audit-release.py`、构建与隔离安装入口，
增加本阶段评测和真实 GUI。正式构建不得使用 `-SkipTests` 绕过已有门。
全套 pytest 和 GUI 串行执行，避免测试进程/桌面窗口互相影响。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/review-scheduled-agent.ps1 -Phase check -ProtectedProfile '<正式版 UserData 目录>'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/review-scheduled-agent.ps1 -Phase source-gui -ProtectedProfile '<同一 UserData 目录>'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/review-scheduled-agent.ps1 -Phase build -ProtectedProfile '<同一 UserData 目录>'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/review-scheduled-agent.ps1 -Phase installer -ProtectedProfile '<同一 UserData 目录>'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/review-scheduled-agent.ps1 -Phase final -ProtectedProfile '<同一 UserData 目录>'
```

先提交已审查源码；使用新的 BuildId。所有阶段须保持相同 EvidenceRoot、BuildRoot、BuildId
和受保护档案。final 需要独立复审的实际证据与文件摘要，不能用空白占位报告。

已知环境限制必须保留：真实付费 Vision NOT_RUN；没有对应实机 DPI/多屏时 BLOCKED；
无证书 NotSigned；Defender 需要未授权提权时 BLOCKED。不能关闭防护、自动点击 UAC或伪造扫描通过。

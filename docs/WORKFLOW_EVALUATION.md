# Workflow Core · 20 项受控评测

在原「Agent Evaluation · 评测」选择 Workflow Core。沿用版本化数据集、隔离 Worker、
评分索引、历史、条件比较、JSON / CSV / Markdown 导出与原 Trace 入口。
本数据集只允许 Mock / Sandbox，拒绝 live_model；没有真实 AI 质量或付费请求成绩。

| 用例 | 实际断言 |
| --- | --- |
| WF-001 | 空草稿可保存，尚不执行。 |
| WF-002 | 线性流程生成实际输出。 |
| WF-003 / 004 / 005 | 循环、坏连线、非法变量在执行前拒绝。 |
| WF-006 | 原工具读取隔离文件，实际内容匹配。 |
| WF-007 | 原 LLMService 使用合成模型。 |
| WF-008 | 当前启用 Skill 按需载入后执行合成分析。 |
| WF-009 | 真实 SDK stdio 经原精确授权执行，协议成功仍非语义目标成功。 |
| WF-010 | 已断开的 MCP 在校验时阻断。 |
| WF-011 / 012 | true / false 分支正确。 |
| WF-013 | Merge 不等待未激活分支。 |
| WF-014 / 015 | 人工批准 / 拒绝，后者停止。 |
| WF-016 / 017 | 节点超时、运行中取消和资源释放。 |
| WF-018 | 中断调度后重新读原文件检查点，续接不重复已核验节点。 |
| WF-019 | 受控未知副作用实际发生一次，恢复预览阻止重复。 |
| WF-020 | Trace、节点结果和最终运行状态一致。 |

预校验拒绝用例没有虚构执行 Trace。WF-018 在本数据集中重新实例化协调器验证持久账本；
实际 Windows 进程退出/重启由单独 GUI 验收执行，二者分开报告。
预期故障的安全断言通过不等于用户目标完成。报告区分流程完成数、断言通过率和真实 AI 任务成功率。

本阶段已实际运行 20 / 20 受控断言。新增测试还覆盖版本不可变、修订冲突、归属、变量类型、
文件/Trace 引用、脚本审批后变化、普通审批不能授权下游工具、MCP 普通确认不能代替精确 grant、
四流程排队、20 节点串行、100 节点 / 200 边校验及数据库迁移/锁冲突。

前端另有独立 GUI 测试，操作真实 Electron、主进程、后端、SQLite、SDK、画布和安装版。
只有模型返回及原生确认的用户选择由受控输入替代；不称为人工点击或真实模型评测。
画布 20 / 50 / 100 节点测量记录真实输入、渲染和第二帧等待，**不把它命名为硬件 FPS**。
最终安装版统计与跳过项写入本地交付报告，不用自动化基线替代安装验收。

```powershell
.\.venv\Scripts\python.exe -B -m pytest backend/tests/test_workflow_schema.py backend/tests/test_workflow_execution.py backend/tests/test_workflow_api.py backend/tests/test_workflow_security.py backend/tests/test_workflow_variables_permissions.py backend/tests/test_workflow_migration_performance.py backend/tests/test_workflow_evaluation.py -q
node tests/ui/workflow-unit.cjs
```

完整 Windows 检查仍使用 `scripts/check.ps1` 和 `npm.cmd run build:public --prefix desktop`。
显式 GUI 验收使用新空白档案：

```powershell
$env:COMPANION_TEST_EXE = 'D:\StudioReview\Program\CompanionStudio.exe'
$env:COMPANION_TEST_HOME = 'D:\StudioReview\NewEmptyProfile'
node tests/ui/workflow-e2e.cjs
```

上述路径仅为示例。源码测试须显式 `COMPANION_TEST_SOURCE=1`，可执行文件必须是本项目锁定的
Electron；报告会标记 sourceMode，不能据此宣称安装版通过。测试脚本与截图没有用户档案内容。
未观测 Token 和费用为 N/A，不从字符数估算；不同数据集、Git、工具 Schema 或环境不公平排名。

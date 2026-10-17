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

## 候选安装版实际验收 · 2026-10-17

Windows 11 x64，版本 `0.2.0-workflow.1`；未跳过构建测试，保留原符号链接测试的权限限制。

| 检查 | 实际结果 |
| --- | --- |
| 完整检查 / 最终构建 Backend | 390 passed、2 skipped、4 条既有 SQLite 警告 |
| 类型、格式、前端构建 | 通过；16 条既有 Hook 警告及主包体积提示保留 |
| Electron 本地回归 | 16 组通过 |
| 真实 NSIS 安装 / 基础 UI | 27 项通过；测试卸载并恢复原注册信息及快捷方式 |
| 安装版 Workflow GUI | 55 项通过，含三个模板、原 MCP/Skill、版本对比、退出重启及续接 |
| 安装版原 MCP/Skills GUI | 29 项通过 |
| 安装版原 Recovery GUI | 22 项通过 |
| 安装版 Workflow Core | 20 / 20 受控断言；15 项启动工作流、11 项正常完成、1 项未知副作用阻断 |
| 工作流重启续接 | 保持同一版本与原 Trace；已核验文件读取只执行一次 |
| 正式档案 | 95 个既有文件的摘要与集合保持不变；不复制到测试档案 |

画布实际测试 20 / 50 / 100 个节点，100 节点场景显示 200 条边，包含原生指针拖动、连线、
多选、缩放、取消捕获与外层窗口 Resize。只报告测得的第二帧等待，未测硬件 FPS。
完整数值、逐项 Benchmark 和 53 项交付结果保存在本地审核报告，不混入公开源码或用户档案。

两项跳过来自当前 Windows 账号无法创建符号链接（1314），没有删除相关检查。
本轮未调用真实付费模型或语音；模型返回及原生确认选择是受控输入，不代替人工确认或第三方服务验收。
安装器实际检查为未签名。已生成本地 Commit / PR 说明；没有推送、合并 main 或发布 Release。

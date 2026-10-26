# Skill 01–10 统一自动验收

本入口最初建立于 0.2.0-router.1 阶段；当前候选版本以 `desktop/package.json`、源码 Commit 和实际验收证据为准。默认只生成本地证据和 Windows 候选，不操作远程仓库或发布。

## PowerShell 入口

```powershell
.\scripts\review-all-skills.ps1 -Phase check -ProtectedProfile "<public-product>\UserData"
.\scripts\review-all-skills.ps1 -Phase source-gui -ProtectedProfile "<public-product>\UserData"
.\scripts\review-all-skills.ps1 -Phase build -ProtectedProfile "<public-product>\UserData"
.\scripts\review-all-skills.ps1 -Phase installer -ProtectedProfile "<public-product>\UserData"
.\scripts\review-all-skills.ps1 -Phase final -ProtectedProfile "<public-product>\UserData"
```

在已提交的干净源码中运行，安装依赖使用 DEVELOPMENT.md 的锁定环境。
证据默认位于本源码 `.cache/all-skills-review`，发行构建使用新的 BuildId 和独立目录。
每轮绑定源码 Commit、版本、BuildId、完整程序资源摘要和保护档案摘要。
新失败会保留并使依赖 Gate 失效，旧版本 PASS 不可替代新版本。

## 17 个 Gate

01 仓库与历史；02 依赖、许可证与已知风险；03 Python；04 前端/Electron；
05 Trace/Evaluation/Recovery；06 MCP/Skills/Workflow/RAG；07 Computer/Scheduler/Voice；
08 Router；09 INT001–016；10 权限/隐私；11 数据库；12 GUI；13 性能；
14 Windows 安装/卸载；15 源码/资源/用户数据一致；16 独立复审；17 最终签署。

`UNIFIED_ACCEPTANCE_01_10.json` 记录每门状态、来源、执行时间、错误与证据。
`SKILL_01_10_MATRIX.md` 不将历史报告的通过数当作本轮结果；当前 JUnit 和内置 dataset 内容决定数量。
任何缺失、失败、阻断、未执行和不支持能力都单独列明。完整发布构建仍运行原 checks，
不能以路由测试替代原有回归，也不能在 Setup 不存在时提前标记安装测试通过。

## 测试边界

后端用合成资料、受控本机模型和隔离 SQLite。INT14 另有真实子进程异常退出。
GUI 通过原 Electron、后端和受控测试窗口验证真实渲染及操作，物理麦克风和云端模型质量须单独授权。
真实 Windows SAPI 语音探针使用原创合成句子；没有 Partial Transcript、自动 Barge-in 或 AEC 的完整验收。
安装器尚未签名，真实付费 Provider、账单、Fish/IndexTTS 和多显示器环境未因本轮 Mock 回归而变成已验证。
原始本机路径、hostname、档案和调试日志仅留本地忽略目录，发行报告使用脱敏汇总。

内部 `integration_observation` 可对已经执行的受控工作流产物重新评分，
读取当前引用授权并核验真实文件、Trace 和检查点，不接受调用者提交的 PASS。
它复用原 Evaluation 的 CAS、指标和导出，明确不算新 Benchmark Worker 执行。

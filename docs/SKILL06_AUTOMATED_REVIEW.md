# Skill 06 自动复核

2026-10-18 从实际审核提交 `327caf2437bb462745ff0ae63c17a9cea8d4b93d` 开始。
原安装器 SHA256、构建清单与 424 项产品源码摘要重新核对；没有将旧报告的成功直接当作新执行结果。

重新运行完整 `scripts/check.ps1`：415 passed、2 skipped、4 warnings，422.32 秒；
TypeScript、ESLint、Prettier、Vite、17 组 Electron、公开源码和全部可达历史审计通过。
原 Evaluation worker 重新执行 28 项 Knowledge Core，全部 PASS。
实际 Skill06 安装保留程序在新空白档案重新运行 30 项 GUI 检查，全部通过，正常退出后自有后端停止。

覆盖知识库、解析/增量/删除、FTS5 中文、来源核验与过期拒绝、记忆/会话隔离、
Knowledge Workflow、MCP/Skills 权限、Trace、Recovery、Evaluation、旧表迁移与边界测试。
代码入口为 `backend/app/services/knowledge/`、原迁移层、`desktop/electron/knowledge.cjs` 和前端对应模块。

机器报告由 `scripts/review-skill06.py` 读取原 58 项声明、新 JUnit、逐项评测、新安装版 GUI 与实际文件摘要生成。
本地保存为 `skill06-review.json`；报告和用户档案不提交源码仓库。

本轮此基线阶段未重跑旧 NSIS 安装/卸载；只核验其实际安装程序与原包摘要。
Skill07 的新安装包仍必须重新安装、验收、卸载和验证注册信息恢复。
未验证真实 Embedding/聊天/语音质量、所有硬件/DPI；原 Windows symlink 权限跳过、无 ANN 与未签名的限制保留。
这些不是新视觉能力已通过的证据，也不授予本轮任何私人窗口或截图外传权限。

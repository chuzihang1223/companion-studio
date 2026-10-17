# Knowledge 基线

本次在 `feature/local-knowledge-rag` 上开发，起点为已验收的 Skill 05 提交
`401c509e739635aad1b20fff6eb1c70166cdd064`。不修改 main，不发布远程内容。

2026-10-17 完整运行 `scripts/check.ps1`：390 passed、2 skipped、4 warnings；
Python 测试耗时 305.82 秒。两个跳过均因当前 Windows 账户不能创建符号链接。
原有 TypeScript、ESLint、Prettier、Electron、构建、公开导出及可达历史审计通过。
保留原有 Hook、构建大小及 SQLite datetime 弃用提示，不将它们描述为新增通过项。

隔离环境 Python 3.12.3 / SQLite 3.45.1，实际探测 FTS5 和 trigram 可用。
中文两字查询将使用显式字符分词并单独验证，关键词匹配不等同于语义理解。

现有手动记忆位于 `manual_memories`，本次只做增量扩展。
保留已有 Chat、Work、Trace、Evaluation、Recovery、MCP、Skills、Workflow 协议。
正式档案只记录文件摘要作交付前后对照，不读取内容、不执行迁移。

Embedding 默认关闭；本地关键词检索不需要下载模型、外部传输或付费调用。
实际 GUI、安装包、性能、检索质量及数据保留结果另行记录，不由基线单元测试推断。

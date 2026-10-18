# Automated QA Gates

统一入口 `scripts/review-visual-computer-use.ps1` 复用原 check/build/installer/audit；不替代已有发布系统。所有证据保存在本机隔离目录，不提交运行日志、截图或用户档案。

| Gate | 实际执行内容 |
| --- | --- |
| A Source Audit | Ruff/格式/源码与可达历史审计、依赖和授权路径复审 |
| B Unit & Integration | 原完整 pytest、TS、ESLint、Prettier、Electron、Runtime/MCP/Skills/Workflow/Knowledge 回归 |
| C Security | 窗口/会话归属、取消、过期、隐私、未知副作用、Vision Schema/Mock 及传输确认检查 |
| D GUI | 自有 Windows Fixture，真实产品 UI/IPC/Runtime/worker，24 项原生 Evaluation 与 Trace |
| E Performance | GUI 测量中 UIA/捕获/OCR/验证用时、主进程与 Fixture 内存、rAF 间隔 |
| F Installer | 新 BuildId 完整构建、DryRun、隔离安装、安装版 GUI、卸载与注册/快捷方式恢复 |
| G Consistency | Git 与 manifest、逐文件摘要、ASAR/frozen 后端、安全审计与正式档案摘要保护 |

每次命令写入 gate_id、status、command、started_at、completed_at、exit_code、evidence_path、findings。汇总读取实际报告，不凭文件存在或文案声称 PASS；FAIL、BLOCKED、NOT_RUN 都保留。

默认执行不使用付费模型，不发送私人图片，不写正式聊天库，不改变 DPI 或系统安全设置。安装测试会按原隔离脚本备份/恢复必要安装注册信息，只操作自有测试进程。代码签名、缺少的物理显示条件和外部视觉模型不能被模拟测试替代。

`READY_FOR_RELEASE_REVIEW` 只是适用门通过的受限候选，不代表获得公开发布许可；关键安全或安装问题则 BLOCKED，缺少尚可执行证据则 INCOMPLETE。独立 review 与复测的结论一同保存。默认不推送、打标签、合并 main 或发布 Release。

# Visual Workflow 基线

本地开发分支：`feature/visual-workflow-builder`，基于已验收的 Skill 04 提交
`9dc2ca7afff74af6c2442c700c784122a7bcdee4`，创建时工作区干净。
原正式 `main` 与公开的 v0.1.0 / v0.1.1 / v0.1.2 不移动。

现有候选版本 `0.2.0-extensions.1`；本次使用不重号的 `0.2.0-workflow.1`。

2026-10-16 实际运行 `scripts/check.ps1`：358 passed、2 skipped、4 warnings；
跳过的两项需要当前账号不具备的 Windows 符号链接权限。工程规则、类型、格式、前端构建、
15 组 Electron 本地回归、源码导出安全检查和所有可达 Git 历史检查通过。
上述结果是自动化基线，不作为新的工作流 GUI、性能或真实模型质量证明。

前四阶段已存在实际源码与隔离安装验收：Trace / Replay、Evaluation、Recovery / Durable
Checkpoint、MCP / Skills。工作流接入这些原有入口，不建立第二套权限或外部工具执行器。
既有 MCP & Skills 审核包保持原样；本轮采用独立档案、独立输出目录与新安装器。

实现采用项目已有 React + TypeScript + SVG/HTML，不新增画布依赖。协议独立于画布；
工作台外层布局、坐标阈值、rAF 和自动滚动保持既有实现。

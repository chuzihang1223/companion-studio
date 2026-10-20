# 实时语音开发基线

基于 Skill08 已验收提交 `b5606309b10c7a8cf36bec4c5d858b88db8b2f53`，
从干净的 `feature/scheduled-agent` 创建 `feature/realtime-voice-agent`。
没有切换到旧 main，也没有操作远端、标签或已发布资产。

2026-10-20 在修改功能源码前重新执行原 `scripts/check.ps1`：

- Ruff、格式、Python 编译、TypeScript、ESLint、Prettier、Vite 通过。
- 19 组 Electron 纯单元检查通过；不代表真实 GUI 或声音播放。
- pytest **547 通过、2 跳过，549 总数**，失败和错误均为 0。
- 原源码导出与可达 Git 历史审计通过。
- 原 Scheduler、Computer Use、Knowledge、Workflow、MCP、Recovery、Trace、Evaluation
  后端测试包含在上述整套测试中；安装 GUI 仍须对新产物另测。

原 Skill08 安装包摘要复核与交付报告一致。现有正式档案仅建立 95 个文件的
摘要基线，没有打开数据库、迁移档案、读取凭据或导入个人版资源。

保留边界：安装器未签名；退出/睡眠不能执行后台计划；付费 Vision、缺少的 DPI/多屏
未运行；Scheduler 管理页顶部引导条遮挡为既有 P3，本阶段不修改布局引擎。

本机通过公开合成文字实际测试 Windows System.Speech 中、英文系统朗读和文件级识别。
中文、英文短问候可识别，复杂中文存在错字，中英混读明显不可靠。该路径是 **Batch ASR**，
不伪造 Partial 或原生 Streaming ASR。没有采集私人讲话或调用外部服务。
生成 WAV 不等于实际扬声器输出，真实播放将在后续独立验收。

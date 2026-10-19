# Scheduled Agent 开发基线

Skill 08 基于 Skill 07 分支提交 `7016b596f34c68fe44474d7277711b5444125db5`，
本地功能分支为 `feature/scheduled-agent`。公开 main 和已有 Release 不在本轮变更范围。

开始开发前重新执行 `scripts/check.ps1`：465 passed、2 skipped，Ruff、TypeScript、
ESLint、Prettier、Vite 和 18 组 Electron 单元通过。完整记录保存在本轮隔离 QA 档案，
不将旧成绩当作新增调度功能成绩。公开源码与历史审计通过，基线导出检查 807 个文件。

同时核对 Skill 07 的交付清单和原安装包 SHA256：
`77aafd486d7025b279040ae0506179abb80e5a57c8bc5b5119efd121d1017148`。
旧安装版 GUI 证据经摘要核验；本轮仍须重新构建和执行安装版回归。

保留前置限制：真实外部 Vision 未运行；100% / 125% DPI 与多显示器变化缺少实机环境；
无代码签名证书；Defender 自定义扫描可能被权限阻断。不会因为增加 Scheduler 将其改为通过。

正式 UserData 只计算文件摘要，不打开数据库或导入配置；新测试只用独立空白档案。

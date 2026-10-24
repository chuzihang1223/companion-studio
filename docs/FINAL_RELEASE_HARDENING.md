# 发布加固候选 0.3.0-rc.2

此版本只准备本地候选，不推送分支、创建标签、合并 main 或发布 Release。已公开版本与个人版档案保持独立。

本轮修复 MCP 删除/重连竞争及已删除工具缓存累积；收紧 DOCX 解压方式；升级有安全公告的锁定依赖及 CPython；更新对应许可证与 FFmpeg 源码证据。Windows 运行文件读取准备同时覆盖普通 AppContainer 和 LPAC，仅对明确列出的公开运行文件添加非继承只读权限。调度页边界与实际标题栏高度对齐。

## 复现入口

先按 [开发文档](DEVELOPMENT.md) 安装锁定依赖并提交待测源码。以下参数均使用自己的绝对路径；`ProtectedProfile` 只能指向本产品需要保护的 `UserData`，只计算摘要，不读取数据库记录或导入凭据。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/final-release-hardening.ps1 `
  -Phase all `
  -EvidenceRoot (Join-Path (Get-Location) '.cache\final-candidate') `
  -BuildRoot (Join-Path (Get-Location) '.cache\final-builds') `
  -BuildId 'studio-0.3.0-rc.2-hardening01' `
  -ProtectedProfile (Join-Path (Split-Path (Get-Location) -Parent) 'UserData')
```

也可分阶段运行 `check`、`stress`、`source-gui`、`build`、`installer`、`final`、`report`，同一轮必须保持源码提交、Build ID、证据目录及受保护档案不变。安装测试临时备份并恢复本产品注册信息和快捷方式；只结束测试拥有的进程，不影响其他版本。完整执行需要实际 Windows 桌面及较长时间。

`stress` 使用一个原运行时和真实 SQLite、stdio MCP，以真实单调时钟持续 60 分钟，包括前 30 分钟核心负载及后 30 分钟混合取消。模型、ASR、PCM 和调度日历是合成边界；报告不会将它们当成真实模型质量、物理录音或扬声器播放。调度频率遵守每会话每小时 12 次上限，不清零预算。

依赖检查使用已有 npm CLI 和官方 PyPI 版本公告接口，不安装额外审计环境。报告记录失败及未查询范围；公开公告未命中不保证不存在漏洞。CycloneDX 清单覆盖锁定 Node、Python 及可识别的运行时原生组件。未签名安装器必须标明 `NotSigned`。

## 证据边界

- Windows 当前 ASR 是终句批处理；没有增量 Partial Transcript。原 Skill 09 允许 Batch Provider。未验证 AEC，因此自动打断继续禁用，手动停止单独测试。
- Fish/IndexTTS、真实 Embedding 模型质量和云模型计费不是合成回归成绩。本轮不发送新的付费请求或下载大型模型。
- Keyword RAG 可独立运行；用户授权的本地/远程 Embedding 接口不等于内置模型。原 Evaluation 的 HybridLocal/HybridRemote 仍需专用配置入口，当前明确阻断，不宣称已完成真实语义 Benchmark。
- MCP 使用手动 Bearer，未实现 OAuth；不自动运行 Skill 脚本或安装外部服务。
- 缺少符号链接创建权限的两个原测试保留；真实 junction 验证不能代替符号链接。物理 DPI/多屏的测试按实际硬件记录。
- 独立复核证据必须绑定当前提交和源码字节；缺少证据时签署门保持 `NOT_RUN`，不能借用旧版本的审核结果。

最终状态以本轮 `UNIFIED_TEST_RESULTS.json` 和交付报告为准；本文件不预先宣布验收通过。

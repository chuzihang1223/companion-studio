# Visual Computer Use Benchmark v1

复用原 Evaluation Engine、结果库、历史、Trace/Replay、导出和取消。新增 `computer-use-core`，24 项 `CU-001` 至 `CU-024`；仅 Windows GUI 模式可执行。Mock/Sandbox 不把这些真实 GUI 用例伪装为通过。

Fixture 是 MIT 原创 WPF 小程序，随源码/安装包提供，依靠 Windows 自带 .NET 编译到隔离 Cache。包括 Edit、Button、List、Tree、Menu、PasswordBox、控件重建/消失/延迟、自绘中英文/重复文字/敏感标签。无网络、账号、私有文件或用户应用。

## 授权与环境

QA 启动器必须在**全新空档案**写入固定用途 marker，并在进程启动前设置 `COMPANION_VISUAL_QA_PROFILE=1`。正式档案不能通过 API 切成 QA。原生对话框取得短期 QA consent，只能打开固定 Fixture，调用仍经过真实 Runtime 和原 Windows worker。

```powershell
# 在项目根目录；不启动正式用户档案
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/review-visual-computer-use.ps1
```

每项执行有自身/批次截止时间，取消当前项记录 CANCELLED 并保留 Trace，未启动项 NOT_RUN。清理失败是 FAIL，不能保留此前 PASS 分数。Native 快照明确原生 bridge 与隔离档案，不冒充 worker.db 进程隔离。

## 测试范围

| 范围 | 用例 |
| --- | --- |
| UIA 发现、树、Button/Edit、输入、控件重建 | CU-001—006 |
| 中文/英文 OCR、边界、歧义、自绘回退、无 Vision | CU-007—012 |
| 100%/125%/150% 实机 DPI、显示器变化 | CU-013—016 |
| 消失、关闭、失焦、响应丢失与不重复副作用 | CU-017—020 |
| 越权拒绝、敏感截图、本机 Workflow、Trace/Checkpoint | CU-021—024 |

报告分开保存真实 GUI、Mock Vision、真实 Vision。GUI 通过率是原创 Fixture 的断言通过率，真实 AI 任务成功率为未测量。当前实际 150% 单屏；100%、125%、真实显示器变化缺环境记 BLOCKED，不修改系统设置。真实 Vision 未授权记 NOT_RUN。

UIA/OCR/捕获、操作验证、原生进程内存、主进程前后内存和 renderer rAF 间隔均保存实际测量；后台窗口节流下的 rAF 不能等同于稳定 FPS。最后数值以候选交付包 LocalEvidence 和最终报告为准，不在文档写固定宣传分数。

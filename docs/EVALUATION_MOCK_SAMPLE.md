# Mock Evaluation 实际运行样例

本报告来自 Windows 安装版的实际合成回归，不是真实 AI 任务成功率或模型排名。
通过已安装快照实际执行 Loader → Runtime → Trace → Grader → SQLite → API；没有调用外部模型或语音。

- Version: 0.2.0-eval.1
- Commit: cd7a324a9255c87e466fd163bfb4454b1826bf48
- Date (UTC): 2026-10-15T18:30:39.373720+00:00
- Dataset: studio-core 1.0.0 / SHA256: 42c7bc7311d715c6aeb6672ef2bb08324d2f3a21f937027f9b551b6db321684c
- Mode: mock / Model: deterministic-mock
- Valid: 20 / PASS: 20 / FAIL: 0
- Mock 回归通过率: 100.0% / 真实 AI Task Success Rate: 未测量
- Average / P50 / P95: 294.783 / 236.682 / 842.720 ms
- 工具调用: 18 / Mock 逻辑模型调用: 31 / 重试: 4
- 实际进入重试的任务: 2 / 恢复成功率: 0.0%
- Usage: Not applicable (simulated) / API Cost: N/A / 真实模型质量验收: 未执行

耗时是 Agent 流程的单调时钟测量，不包含 worker 启动、排队或安装。安全用例的预期超时、
拒绝、取消以 PASS 表示回归断言成立，不表示外部动作完成。网页与桌面边界是模拟。

|Case|Result|Duration ms|Tool calls|Retries|
|---|---|---:|---:|---:|
|EVAL-001|PASS|52.015|0|0|
|EVAL-002|PASS|229.175|1|0|
|EVAL-003|PASS|229.224|1|0|
|EVAL-004|PASS|152.265|0|0|
|EVAL-005|PASS|252.844|1|0|
|EVAL-006|PASS|259.118|1|0|
|EVAL-007|PASS|463.806|2|0|
|EVAL-008|PASS|842.547|1|2|
|EVAL-009|PASS|248.151|1|0|
|EVAL-010|PASS|229.085|1|0|
|EVAL-011|PASS|846.002|1|2|
|EVAL-012|PASS|238.537|1|0|
|EVAL-013|PASS|236.636|1|0|
|EVAL-014|PASS|236.728|1|0|
|EVAL-015|PASS|214.641|1|0|
|EVAL-016|PASS|149.089|0|0|
|EVAL-017|PASS|248.875|1|0|
|EVAL-018|PASS|172.906|0|0|
|EVAL-019|PASS|172.696|1|0|
|EVAL-020|PASS|421.323|2|0|

仅公开合成 Fixture 摘要，不包含聊天、服务地址、密钥、私人路径或完整 Trace。
原始导出保留在本机隔离验收档案；产品可生成自己的 JSON/CSV/Markdown 报告。

# Mock Evaluation 实际运行样例

这是 Windows 目录版的实际合成回归报告，不是真实 AI 任务成功率或模型排名。
由安装包同构的冻结后端实际执行 Loader → Runtime → Trace → Grader → SQLite → API，未调用外部模型/语音。

- Version: 0.2.0-eval.1
- Commit: 5ebf757be547a4c9b227233d386c8068562f4a79
- Date: 2026-10-15T18:13:10.431691
- Dataset: studio-core 1.0.0 / SHA256: 42c7bc7311d715c6aeb6672ef2bb08324d2f3a21f937027f9b551b6db321684c
- Mode: mock / Model: deterministic-mock
- Valid: 20 / PASS: 20 / FAIL: 0
- Mock 回归通过率: 100.0% / 真实 AI Task Success Rate: 未测量
- Average / P50 / P95: 290.660 / 236.887 / 811.281 ms
- 实际工具调用: 18 / Mock 逻辑模型调用: 31 / 重试: 4
- 已进入恢复的回归任务: 2 / 恢复成功率: 0.0%
- Usage: Not applicable (simulated) / API Cost: N/A / 真实模型质量验收: 未执行

耗时为 Agent 流程的单调时钟测量，不包含 worker 启动、排队或安装。安全用例中的预期超时、拒绝、取消，
PASS 表示其断言成立，并不表示被拒绝的外部动作已完成。浏览器/桌面边界是模拟，不能证明真实 GUI 成功。

|Case|Result|Duration ms|Tool calls|Retries|
|---|---|---:|---:|---:|
|EVAL-001|PASS|49.804|0|0|
|EVAL-002|PASS|227.645|1|0|
|EVAL-003|PASS|251.604|1|0|
|EVAL-004|PASS|160.359|0|0|
|EVAL-005|PASS|256.633|1|0|
|EVAL-006|PASS|255.436|1|0|
|EVAL-007|PASS|484.631|2|0|
|EVAL-008|PASS|833.104|1|2|
|EVAL-009|PASS|211.673|1|0|
|EVAL-010|PASS|199.371|1|0|
|EVAL-011|PASS|810.132|1|2|
|EVAL-012|PASS|249.676|1|0|
|EVAL-013|PASS|243.620|1|0|
|EVAL-014|PASS|232.299|1|0|
|EVAL-015|PASS|209.978|1|0|
|EVAL-016|PASS|158.393|0|0|
|EVAL-017|PASS|241.475|1|0|
|EVAL-018|PASS|177.520|0|0|
|EVAL-019|PASS|158.203|1|0|
|EVAL-020|PASS|401.642|2|0|

所有数据来自公开合成 Fixture；本样例不包含原始聊天、服务地址、密钥、私人路径或完整 Trace。
运行历史另存于本机隔离档案，可从产品导出自己的 JSON/CSV/Markdown 报告；此样例只提供可公开摘要。

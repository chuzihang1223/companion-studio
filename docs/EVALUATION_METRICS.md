# Evaluation 指标与比较

- 有效评分数：状态为 PASS/FAIL 的 Attempt 数；NOT_RUN/BLOCKED/ERROR/UNKNOWN/CANCELLED
  分别列出，不混入分母。Run COMPLETED 表示调度结束，不代表每个 Case 都通过。
- 回归通过率 = PASS / 有效评分数；失败率 = FAIL / 有效评分数。Mock/Sandbox 的
  real_task_success_rate 永远为空，不能称为真实 AI 的任务成功率。
- Average/P50/P95 来自有效项的实测 Agent 执行耗时（monotonic clock），不含进程启动、
  Fixture 初始化或排队。百分位使用排序后 `(n-1)*q` 的线性插值。无样本 N/A。
- 工具数从已有 Trace 的实际 executed tool span 统计，请求/被拒绝工具不冒充已执行。
  Retry Count 来自 RETRY_STARTED；恢复分母只包含实际发生重试的有效项，没有重试 N/A。
- 模型调用是 Trace 的逻辑请求数；Mock 模型请求不是外部 API 请求。
- Usage 为 Provider 返回的 prompt_tokens/completion_tokens/total_tokens；不按文字长度
  推算。任一请求缺失 Usage 时标记 Unavailable，并单列已观察数量。Mock 是 Not applicable。
- API Cost 在没有经过确认的价格时为 Unavailable / N/A，不写 0，也不硬编码网上价格。
  当前不提供价格配置、LLM 裁判或自动模型排名。

Expected failure（超时、权限拒绝、取消等）是本项的安全回归目标，断言通过不表示用户
请求中的外部动作完成。Grader 分开显示 Task/Tool/Verification/Safety/Recovery；
不会把加权分数超过阈值当作目标完成。

比较保留：数据集 ID/版本/SHA、所选 Case/重复次数、模式、配置的 Provider/Model ID、
Temperature/MaxTokens/Timeout/ToolCalling、App/Git Commit、Prompt 源摘要、工具 schema
摘要、操作系统/Python/Fixture/隔离边界、并发/超时。模型 ID 是配置标识，不冒充服务商
返回的精确模型权重版本。条件不同逐项列出，数据集不同明确提示不能公平比较。
即使条件一致，单次/小样本结果也不产生“最佳模型”结论。

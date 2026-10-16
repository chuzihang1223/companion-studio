# MCP 与 Skills 评测

复用既有 AgentEvaluationEngine、隔离 worker、SQLite、Trace 归档、评分、历史和导出。
面板选择 **MCP & Skills Core · 20 项**；默认 Mock，真实网络连接仅限审计过的本机合成 Server。
不连接私人账号、第三方云或付费模型，不接受此数据集的 Live Model 模式。

|编号|实际断言|
|---|---|
|001–003|真实 stdio、退出清理、启动失败有界隔离|
|004–007|多服务器隔离、发现默认禁用、同名映射、Schema 参数拒绝|
|008–010|原 Runtime 只读调用、高风险单次授权、拒绝不执行|
|011–014|工具超时、进程真实退出、重连 Schema 改变、低信任输出|
|015–018|Skill 元数据、导入/禁用、按需加载、缺少能力|
|019–020|Agent + Skill + MCP Trace 归属、未知副作用不得重放|

受控 Server 提供 echo、add、read_fixture、write_test_fixture、fail_once、timeout、
permission_required、side_effect_unknown。文件效果只发生在当前 worker 的临时合成目录。
只有 Model 选择与外部业务语义为合成边界；stdio 管道、SDK、进程、Runtime、审批、检查点、
恢复和 Trace 均为实际代码。管理类用例单独记录真实连接 Trace，不虚构一次模型调用。

首轮源代码实际运行 19/20；SDK 的固定 REQUEST_TIMEOUT 未映射为工具超时。
修复固定错误码映射后，第二轮实际 20/20。初次失败记录仅留本地缓存，不能作为最终成功证据。
完整回归与安装版结果以交付报告为准，不将源代码评测当作安装验收。

指标包括连接、发现、调用成功率、Schema 拒绝、延迟、重连、权限拒绝、Skill 加载与合成匹配率。
预期故障也是分母中的真实故障，断言通过率与协议调用成功率分别显示；真实 AI 目标完成率、
第三方服务可靠性、Token 和费用在未观测时保持未测量/N/A。

协议测试使用真实受控 stdio 与 Streamable HTTP：新版 2026-07-28、SDK legacy 2025-11-25
及 auto。API 测试验证 Resources、Prompts、控制令牌、凭据脱敏与 Skill 生命周期。
安全测试额外覆盖 DNS 私网答案、端点限制、Schema 引用/正则拒绝、输出限额、导入路径与 YAML。

```powershell
.\.venv\Scripts\python.exe -B -m pytest backend\tests\test_mcp_connection.py backend\tests\test_mcp_security.py backend\tests\test_mcp_api.py backend\tests\test_skill_security.py backend\tests\test_mcp_evaluation.py -q
node tests\ui\extensions-unit.cjs
```

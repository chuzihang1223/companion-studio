# Workflow Schema v1

协议与画布库分离。`Definition` 使用额外字段拒绝的 Pydantic 模型；导入仍只是新草稿，
不会连接 MCP、启用 Skill、发布版本或执行节点。

| 实体 | 主要字段 |
| --- | --- |
| Definition | workflow_id、name、description、schema_version=1、version、status、nodes、edges、input_schema、output_schema、required_capabilities、budget |
| Node | node_id、type、title、description、position、input_ports、output_ports、config、timeout、retry_policy、permission_requirement=runtime、metadata |
| Edge | edge_id、source_node_id、source_port、target_node_id、target_port、condition_label |
| Run | run_id、version_id、conversation_id、task_id、work_session_id、trace_id、state、inputs、output、revision、预算计数与节点记录 |

`status` 为 DRAFT / PUBLISHED / ARCHIVED。保存只更新草稿；归档停止新的运行，保存为草稿
可重新启用。删除只删除可编辑定义，既有版本与历史不删除。发布产生新 UUID 和递增版本号，
运行只接受已发布版本。发布后的 HTTP 接口没有覆盖、修改或重命名版本快照的操作。

## 限制与校验

- 最多 100 节点 / 200 边；定义序列化最多 512 KB、数据嵌套最多 16 层。
- 节点和边 ID 必须唯一；有一个手动触发节点、至少一个输出节点，其他节点均须从触发器可达。
- 不允许悬空边、循环、进入触发器的边、从输出节点出发的边及类型冲突。
- 条件必须有 true 和 false 出口；错误出口须显式声明并连接。
- 端口类型：any / string / number / boolean / object / array / file_reference；实际值在运行时再检查。
- Node config 只允许当前节点类型定义的字段。未知类型、未知字段、缺必填项、无效变量、
  工具或扩展依赖会阻止运行。空或未填完的草稿可以保存。
- JSON Schema 不允许引用、正则或远程取 Schema；每个 Schema 最多 16 KB，有深度限额。
- 输入 Schema 根类型必须是 object，最多 30 个字段；表单与后端使用同一份声明校验。
- `output_schema` 校验最终按输出节点 ID 组成的结果对象，不是某一节点的原始值。

## 11 种节点的 config

| type | 关键配置 |
| --- | --- |
| manual_trigger | 空对象，用户明确开始。 |
| input | name、type、required、default；参数名须存在于 input_schema。 |
| llm_agent | instruction、value、model_role、output_schema、max_characters。 |
| tool | name、arguments、coding_mode；注册 Schema 在发布时固定摘要。 |
| mcp_tool | tool_id、server_id、schema_fingerprint、arguments。 |
| agent_skill | skill_id、content_hash、goal、value、output_schema。 |
| condition | expression（受限条件 DSL）。 |
| transform | operation、value，以及对应 fields / separator / default / path。 |
| human_approval | message、value。 |
| merge | mode=all_active 或 first_available。 |
| output | value、format=text / json / file_reference / task_summary / trace_reference / evaluation_reference。 |

预算默认：100 步、600 秒、10 个模型节点请求、40 个工具调用预算、32,768 个 Token、
256 KB 中间结果。最大分别为 100、1,800 秒、20、100、131,072、1 MB。
安全重试预留工具预算；不能通过重试自动补充预算。服务端输入 Token 无法请求前精确计量，
因此 Token 预算用于限定完成长度及根据已返回 Usage 停止后续请求，不能保证外部账单总额。

## 持久化与兼容

增量建立 `workflow_definitions`、`workflow_versions`、`workflow_runs`、
`workflow_node_runs`、`workflow_run_events` 五张表，复用现有可重复迁移入口。
Run 仅引用不可变版本；节点和运行调度日志不替代 Skill 03 的执行意图/副作用账本。
编辑和发布要求 revision；旧修订会返回 WORKFLOW_EDIT_CONFLICT，而不是覆盖另一窗口。
数据库写锁失败是受控存储错误，不能删除旧数据来重建。

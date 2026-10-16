# 执行、审批、追踪与恢复

## 使用步骤

1. 新建一个工作会话，在「模块」中打开「Visual Workflow · 流程设计」。需要较大画布时最大化该模块。
2. 从左侧点击或拖入节点。拖动节点标题移动；Shift 多选或框选；点击/拖动输出端口到目标输入端口连线。
3. 选择节点，在右侧填写标题、超时、工具/Skill/模型职责及配置，点「应用节点配置」。
4. 在「流程参数」声明输入 Schema、输出 Schema 和预算；输入节点会出现对应表单。
5. 保存草稿，校验，再发布本地不可变版本。Undo / Redo、复制粘贴、删除、平移、缩放、适应画布、
   整理节点都只编辑当前草稿。未保存时切换会异步询问；关闭应用前应保存草稿。
6. 选择版本，填写本次输入，点「运行所选版本」，审阅原生运行概览。工具另按已解析参数授权。
7. 画布按真实节点事件高亮。底部查看当前会话历史、输出、错误、原 Trace 或恢复预览。

## 状态与分支

PENDING → READY → RUNNING，可等待 WAITING_PERMISSION 或暂停 PAUSED；终态包括
COMPLETED / FAILED / SKIPPED / CANCELLED / INTERRUPTED / NEEDS_REVIEW。
父节点未终结时不执行子节点。未选中的条件出口传播 SKIPPED；Merge 不等待跳过分支。
`all_active` 汇总激活父节点；串行版 `first_available` 在激活父节点终结后，选择拓扑顺序中的
第一个结果，不是并行竞速。没有可用输出不能报告成功。

节点失败默认停止；声明并连接 error 出口时，可把固定错误对象交给错误处理分支。
拒绝权限、取消和未知 MCP 副作用不能通过错误边绕过安全停止。
「流程完成」只表示已激活节点按合同执行结束，不等于真实 AI 任务质量或外部事实正确。

## 工具与模型

Tool Node 使用原注册表、参数 Schema、风险计算、CodingWorkspaceExecutor、AgentTaskRuntime、
RuntimeRegistry、GoalVerifier 和 CheckpointManager。不会直接调用 OS、Shell 或 MCP SDK。
审批前写 PREPARED 意图，不执行工具；批准时再检查定义、参数和路径。测试脚本另固定内容摘要，
审批期间改变会阻断执行。文件写入默认隔离草稿；项目选择本身不授予实际写入权限。

LLM Node 只分析当前数据，可以校验 JSON 输出；没有隐式工具循环。
Skill Node 验证当前启用状态和内容摘要，按需加载说明后使用同一 LLMService；不运行脚本。
已有文件、模型、脚本和外部工具各自仍有原来的运行条件。Python 测试需用户另装可用解释器。
外部程序和脚本具有当前账号权限，不是 OS 沙箱；其自身的网络请求不属于宿主模型预算保证。

## 事件与回放

后端持久化递增游标的节点状态事件，SSE 可从 after 重连；主进程只给本应用主窗口订阅。
渲染器按节点变化合并读取状态，不按每个模型 Token 重画全部 Canvas。
Trace 为 Workflow → Node → 原 Tool / Model → Verification / Recovery 层级，只有固定脱敏元数据。
历史画布与 Trace 只读；回看不执行工具，新运行生成新的记录和审批。

## 续接

启动时把遗留运行标为 INTERRUPTED，不重建旧协程、不自动连 MCP。
失败/中断后点「检查恢复」，调用原 StateReconciler 验证资源、Schema、调用及检查点。
可安全跳过的已完成节点保持原输出；已完成文件读写必须重新匹配原摘要。
未知外部副作用、已派发状态不明的模型请求、变化文件或扩展依赖会停止并要求人工核查。
只读重试继续受原 RecoveryPolicy 和有限预算约束；续接最多三段执行，不自动补充模型/工具预算。
「确认安全续接」重新复核并用 revision 抢占，避免重复点击的两个执行者。

暂停在当前节点结束后的调度边界生效，不承诺冻结第三方程序；等待审批时可批准、拒绝或取消。
取消和退出会释放本实例的 Future、配额、Handle、订阅及自有子进程，不关闭用户的其他应用。

## API

所有 `/api/workflows`、`/api/workflow-versions`、`/api/workflow-runs` 读写均需主进程控制令牌。
提供 list / detail / save / validate / publish / archive / delete / templates / versions、
start / detail / nodes / events / stream / approve / control / resume-preview / resume。
Run 查询、审批、取消和续接必须带所属 conversation_id；不能靠 Run ID 访问其他会话。
Renderer 只有固定类型的 IPC 操作，没有任意 URL、任意文件导入路径或任意工具执行代理。

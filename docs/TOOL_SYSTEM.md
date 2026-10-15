# 工具系统：Schema、权限与执行边界

Companion Studio 将模型提出的操作视为待校验的数据。模型可以选择工具和提交参数，但不能决定本机风险等级、权限模式或任意可执行文件路径。

## 1. 工具注册

`backend/app/services/agent_tools.py` 中的不可变 `Tool` 描述包含：

| 字段 | 用途 |
| --- | --- |
| `name` | 内部操作名，例如 `app.launch`。 |
| `description` | 发给模型的用途及限制说明。 |
| `properties` | 参数类型和可选值。 |
| `required` | 必填参数。 |
| `risk_level` | 工具注册时的风险提示；实际执行风险仍由本地 `risk_for()` 计算。 |
| `requires_confirmation` | 注册时的确认提示；最终计划再次经过当前权限策略。 |

`Tool.schema()` 将内部名称的点替换为下划线，输出 OpenAI-compatible Function Calling Schema。因此内部 `app.launch` 对应 API 函数 `app_launch`。参数设置 `additionalProperties=false`，避免工具接收未定义字段。

`ToolRegistry.schemas_for()` 根据用户明确任务缩小工具集合，例如媒体控制、文件读取、代码任务或网页搜索。应用枚举可以结合已发现的自定义应用 ID 扩展；模型得到注册 ID，而不是任意程序路径。

工具集合缩小帮助模型减少错误选择，实际安全边界仍是后续参数验证、路径检查与权限判断。当前参数解析使用完整注册表，不能把 Schema 筛选描述为模型绝对无法提出其他已注册工具。

## 2. 模型能力与本地动作

`backend/app/services/llm/model_roles.py` 选择承担规划和执行的模型服务：

- 指定职责优先，工具请求可以回退到执行模型，再使用角色模型。
- `tool_calling=false` 会排除候选；未知能力允许尝试。
- 实际返回工具调用后记录观察能力；空工具列表只表示本次没有选择工具。
- 对简单应用启动、已识别网页操作等，本地动作解析器直接生成同样的工具调用结构，绕过模型请求。

确定性工具调用也必须经过同一工具解析和权限链路，不是绕过授权的专用入口。

## 3. 参数校验

`ToolRegistry.parse_calls()` 执行以下检查：

1. `tool_calls` 必须是列表，最多 5 项。
2. 函数名必须存在于注册表。
3. `arguments` 必须是能解析为对象的 JSON，不得带未定义参数。
4. 必填字段必须存在，字符串参数必须是字符串，枚举值必须允许。
5. 调用编号不能为空、最长 100 字符，同一计划内不能重复。

例如模型返回下面的调用，内部名称会转换为 `app.launch`：

```json
{
  "role": "assistant",
  "content": null,
  "tool_calls": [
    {
      "id": "example-open-notepad",
      "type": "function",
      "function": {
        "name": "app_launch",
        "arguments": "{\"app\":\"notepad\"}"
      }
    }
  ]
}
```

上述编号只是说明示例；实际任务生成新的调用编号。`app` 指向已注册应用，不能替换为 `cmd.exe`、Shell 字符串或未经注册的 EXE 路径。

Schema 本身不能承担所有运行时检查。当前解析器对列表成员形状还有工程审计中记录的缺口，不能描述为已经实现完整 JSON Schema 验证器。

## 4. 权限策略

关键实现位于：

- `backend/app/services/trusted_actions.py`：`action_for()`、`risk_for()` 及当前任务授权。
- `backend/app/services/permission_policy.py`：读取当前模式并作出 `allow` / `ask` / `deny`。
- `backend/app/services/desktop/preferences.py`：本机用户偏好，模型不能修改。
- `backend/app/services/desktop/runtime.py`：执行前调用 `trusted_actions.permitted()`。

`action_for()` 将工具调用映射为本机权限动作，例如 `app.launch:notepad`、带项目根目录的文件动作、虚拟草稿动作。`risk_for()` 从动作类别和已知应用集合重新计算风险，不采用模型声称的风险。

| 动作类别 | 当前处理 |
| --- | --- |
| 已知应用检测 / 状态、限定目录只读操作 | 低风险；只读可直接允许。 |
| 已知应用启动、网页入口、媒体操作 | 依据低 / 中风险及用户模式决定。 |
| 删除、批量覆盖、代码运行、实际编码执行、关闭应用 | 高风险，每次单独询问。 |
| 未注册应用启动、管理权限及安全绕过类别 | `critical`，拒绝，即使提交了确认编号也不会允许。 |

询问模式对非只读普通操作要求确认；智能模式允许低 / 中风险并对高风险询问；自动模式仍不绕过高风险和关键拒绝规则。任务级授权按操作类别生效，例如一次任务内的应用启动类别，而非永久授予整个系统权限。任务结束时清理本任务授权。

旧版 `always` / `deny` 文件会保留和归档。当前执行决策主要来自新权限模式、风险规则和任务级授权，旧持久授权不覆盖这些规则。`PermissionManager` 的简单确认辅助接口也不取代真实运行时检查。

前端的 `approved_ids` 只能匹配对应工具调用；后端会重新检查风险与权限。桌面命令 / 结果接口另外要求 Electron 持有的控制令牌，普通渲染器不能直接凭模型内容写入桌面结果。

## 5. 路径与代码写入

`backend/app/services/workspace_paths.py` 提供共同的文件边界：

- 只接受项目根目录内的相对路径，拒绝绝对路径、盘符相对路径、父目录跳转、空字符和 NTFS 备用数据流语法。
- 路径解析后必须仍在所选根目录内，防止链接指向外部文件。
- 阻止 `.env`、账号密钥目录及已知凭据文件，也阻止通过文件工具访问应用配置、权限、聊天数据库等用户资料。
- 读取 / 列目录和搜索有大小与数量边界。

`AgentService` 仅采用 Work Session 中已经保存的目录；`CodingWorkspaceExecutor` 进一步区分：

| 模式 | 写入行为 |
| --- | --- |
| `virtual_only` | 文件生成进入虚拟文档，不修改用户项目。 |
| `preview_patch` | 提供草稿 / 补丁建议，不自动应用。 |
| `write_workspace` | 明确写入意图加所选目录才可实际修改，仍接受路径与权限检查。 |

`ToolExecutor` 的实际文件操作避免无意覆盖：新建拒绝已有文件，编辑保留恢复副本，删除仅针对单个文件并先保留恢复副本，重命名拒绝已有目标。不能把这些保证推广为任意程序的文件访问约束。

代码运行使用明确批准的 Python 解释器、收缩的子进程环境、15 秒限制和 16 KB 输出限制。草稿运行可以使用单独目录。这里的“sandbox”指项目副本 / 草稿目录，不是操作系统级隔离；被用户授权的脚本仍以当前账号权限运行。当前子进程树清理限制见 [工程审计](ENGINEERING_AUDIT.md)。

## 6. 执行分发

`AgentTaskRuntime.execute_call()` 是正式任务的分发入口：

| 工具组 | 执行位置 |
| --- | --- |
| 应用、媒体、系统状态 | `DesktopBridge` → Electron `DesktopAppAgent` → 注册器 / Windows 控制器。 |
| 网页打开 / 搜索 | 桌面桥 → Electron 内置网页工作区和浏览器操作。 |
| 第三方音乐搜索 / 播放 | 检测 → 启动 → 搜索 → 选曲 → 播放验证；适配器持有应用和任务上下文。 |
| `coding.task` | `CodingAgentService`，结合模型服务或已配置的 Codex 入口及写入模式。 |
| `code.run` | 项目 / 草稿执行器，在线程中执行并向终端模块发布输出。 |
| 文件 / 目录工具 | `CodingWorkspaceExecutor` / `ToolExecutor`，校验路径并执行本地操作。 |

桌面自动操作关闭时，应用、媒体、网页及系统桌面工具会返回未执行结果。文件工具不因此获得额外权限。

`ToolExecutor.execute()` 也保留了旧请求型桌面返回，如 `queued=true` 和 `action=launch_app`。这类返回表示请求待桌面处理，不能描述为已启动或已播放。正式桌面运行时会走真实桥接观察链路。

## 7. Tool Result 与目标检查

工具结果是按工具职责返回的字典，并非所有工具都返回相同字段。常见字段如下：

| 字段 | 用途 |
| --- | --- |
| `verified` | 桌面或编码操作是否通过对应验证。 |
| `error` / `public_error` | 错误细节或适合展示的解释。 |
| `path` / `content` / `diff` | 文件位置、读取结果或修改差异。 |
| `exit_code` / `output` | 代码进程结果和限制内输出。 |
| `window` / `processes` | 应用实际观察结果。 |
| `music_context` | 音乐客户端、查询与执行阶段的任务归属。 |

运行时将 `error` 或 `verified=false` 标记为失败。正常完成的结果写入 `details`，并生成携带原调用编号的 `role=tool` 消息。工具结果更新工作台模块，再参与 `GoalVerifier` 检查和最终文字回复。

播放目标不能仅靠应用启动通过；需要播放工具返回验证成功。编码目标需要验证成功。普通目标主要检查工具完成状态，尚不提供用户所有子目标的完整语义覆盖证明。应用打开、搜索页面打开、音频实际发声和代码测试成功都是不同结果，报告时应依据对应观察。

## 8. 测试与维护原则

- `backend/tests/test_agent_v6.py`：Schema、未知工具、目录边界及计划 / 执行基本流程。
- `backend/tests/test_v81_workspace_permissions.py`：风险策略、授权类别、项目路径和恢复副本。
- `backend/tests/test_v84_routing.py`：模型职责选择与观察能力。
- `backend/tests/test_v8_runtime.py`：播放目标验证和实际桌面回传。
- `backend/tests/test_v85_waiters.py`：等待、取消和超时释放。

新增工具时必须同时定义 Schema、参数检查、权限动作、执行分发、结果语义和必要测试。新增参数不能通过随意执行 Shell 命令实现。修改注释或格式时保留已有执行顺序、风险规则、用户数据格式和第三方协议字段。

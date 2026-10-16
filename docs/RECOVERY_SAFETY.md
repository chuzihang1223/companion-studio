# Recovery 安全与隐私

## 权限不由恢复策略授予

Policy 和 Planner 只决定是否有资格尝试。最终调用仍经过 ToolRegistry 参数模式、
trusted_actions、原路径边界和 RuntimeRegistry。旧 approved_ids 不从持久记录恢复。
每次 Resume 的剩余非只读步骤要求本次审批；显式 deny 始终优先。
风险不能从恢复 JSON/模型输出中降低，不接受任意路径、URL、Shell 或新工具定义。

## 固定本机接口

所有 `/api/agent/recovery` 接口沿用 `X-Companion-Control-Token`，Token 仅在主进程。

|接口|行为|
|---|---|
|GET /tasks|按 conversation_id；可选 work_session_id；默认 30，最多 50|
|GET /tasks/{id}|归属校验后的任务详情|
|GET /tasks/{id}/checkpoints|同一任务检查点|
|GET /tasks/{id}/preview|在受控注册器内只读重新观察|
|POST /tasks/{id}/resume|request UUID + revision + 明确批准的原调用编号；新执行 UUID|
|POST /tasks/{id}/revalidate|重新核验并更新恢复可用/人工检查状态|
|POST /tasks/{id}/pause、continue、takeover、cancel|沿用当前任务 Handle / Registry 控制|
|POST /tasks/{id}/restart|必须显式同意新模型规划；已执行副作用时拒绝|

控制参数绑定 conversation / work_session / 实际 main-window renderer，额外字段拒绝。
preload 提供固定能力，主进程检查发送者和 UUID、版本、列表大小；内嵌网页没有该能力。
后端仍独立核对所属会话及工作会话，不能仅凭一个 task UUID 操作其他会话。
403/404/409/422/503 返回固定错误码，存储故障不放行工具。

## 不盲目重放

READ_ONLY 仅限既有只读状态/文件工具和新增的 `browser.observe`。后者只观察本次工作任务
已存在的浏览器页，不导航、不输入、不提交，也不创建页面。
固定已知站点搜索只允许既有 GET 路由；未知网站/表单不因 DOM_CHANGED 自动重复。
写文件通过哈希复核；运行脚本、编码账号、安装、删除、发送表单、媒体切歌等不自动重放。
确认可能生效的外部动作需要人工检查，不能把点击 Resume 等价为任意新操作授权。

## 隐私与审计

- Failure 保存固定错误码、固定中文说明、类别、来源、UUID、严重性及观察/授权标志。
- 不将原始异常、模型 prompt、聊天、附件、音频、页面正文或截图写入恢复 Trace。
- Checkpoint 仅存本地相对文件身份/哈希和布尔或整数结果；WorkSession 原本的计划/目标仍是本地私密数据。
- Trace 使用既有白名单与二次脱敏；新片段含 origin_task_id，不修改旧片段。
- 查看/回放不执行恢复。恢复控制不作为 Watchdog 进度心跳，不以轮询掩盖任务卡死。
- Evaluation 的固定合成故障只在隔离 worker，不能由普通聊天选择任意异常、脚本或退出点。
- 不分发用户档案、恢复数据库、原始日志、API 配置、LegacyArchive 或个人版资源。

本地数据库可能记录项目相对文件名和用户工作目标，应按用户数据保护。
导出 Trace 是摘要，不等同安全地公开整个工作目录或 chat.db。

## 当前限制

无外部 Exactly Once、通用 GUI 重放、自动模型降级、模型驱动自由重规划、事务回滚桌面动作，
无脚本副作用自动对账。协程取消不保证撤销已经提交的线程/进程/远程动作。
专用 Windows GUI Benchmark 尚未接入，真实 AI 和真实网页布局质量没有本轮成绩。
Windows 安装器尚未签名；公开发布仍需人工审核。

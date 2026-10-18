# Computer Use Recovery 与 Checkpoint

扩展原 FailureClassifier、RecoveryPolicy、CheckpointManager、StateReconciler 与 Runtime；不创建另一个重试循环。

典型错误包括：SCREEN_CAPTURE_FAILED、UIA_ELEMENT_STALE、ELEMENT_OCCLUDED、OCR_TARGET_AMBIGUOUS、VISUAL_TARGET_LOW_CONFIDENCE、WINDOW_IDENTITY_CHANGED、FOCUS_LOST、DPI_MAPPING_INVALID、DISPLAY_CHANGED、VISION_MODEL_UNAVAILABLE、ACTION_STATE_UNKNOWN、POST_ACTION_VERIFICATION_FAILED。

观察、定位和等待属于只读，可按原预算重新观察。焦点丢失停止输入；窗口关闭、几何变化、歧义和低可信度拒绝动作。Vision 失败不剥夺本地 UIA/OCR 能力。无法确定业务结果的输入/点击/滚动属于非幂等操作，不自动重试。截断树不能定位或验证；脱敏字段不能证明已消失，存在未遍历的敏感子树时任何 absence 条件均拒绝，需改用可验证的正向状态或人工检查。

Checkpoint 保存工具意图、随机窗口/观察/操作 ID、副作用类型和结果摘要；不保存图像、UIA 树、OCR 原文或永久屏幕坐标。原任务计划仍含用户主动批准的工具参数，不能当作秘密存储。续接需重新授权、重新观察，旧 `COMMITTED` 不能证明屏幕状态仍新鲜。

Trace 新增观察/UIA/OCR/Vision/定位/动作/验证失败/视觉恢复事件，关联原 task、trace、step、window、observation、action。Replay 不调用 DesktopBridge。敏感数据使用原 Trace 白名单过滤。

真实故障注入在点击已经验证后丢弃桥接响应，再观察计数器只变为 1，证明不会因超时重复点击；这只是有界本地验证，不是外部系统 Exactly Once 保证。恢复用时来自实际事件/操作测量。

# 本地 OCR 与可选视觉模型

## OCR

使用 Windows.Media.Ocr，安装包无需另带模型和大型 OCR 框架。语言取决于 Windows 已安装的识别语言。本轮真实 Fixture 验证简体中文和英文，其他语言未验证。

返回文字、图像边界、物理边界、language、source_image_id/capture_timestamp（由所属观察关联）和 coordinate_space。Windows OCR 不提供逐行可信度，`confidence=null`，绝不填一个猜测分数。`match_quality` 仅是字符串匹配程度，不是视觉识别准确率。

精确/标准化/有限模糊匹配。UIA 身份优先；多个结果保留歧义，不取第一个。模糊候选用于观察，不能执行；OCR 点击须用户单独开启坐标后备、唯一精确候选、重新截图识别、位置一致、窗口命中和焦点验证。只看到文字不意味着可点击。

OCR 同时做图片隐私检查；失败时只保留允许读取的 UIA 数据。Windows 未安装可用语言时报告 `OCR_LANGUAGE_UNAVAILABLE`，不会悄悄上传到云端识别。

## Vision

复用 `tool_execution_model` 的 OpenAI-compatible Provider。必须显式配置 `capabilities.vision=true` 才允许尝试，模型名包含 vision 不构成证据。配置能力和已观察到的兼容性分别显示；有效受限响应才记录已观察能力。

仅接受大小受限 PNG，采用裁剪/遮罩后的授权范围。明确一次性原生确认，不把设置模型等同于同意发送屏幕。输出严格 Schema：`target_description`、`proposed_action`、`bounding_box`、`confidence`、`reason`、`observation_limitations`。边界越界、额外字段、过长文本或不可解析响应均拒绝。

屏幕文字作为不可信输入，提示其不能改写工具规则。结果只用于建议展示，不执行坐标、脚本或点击；是否可信不能只靠模型自报 confidence。原任务 Registry 管理并发、所有权、超时与取消，Trace 不保存图片/模型原文。关闭或不配置视觉模型不影响 UIA、原浏览器 DOM 工具和本地 OCR。

Mock 测试只证明格式、取消和传输权限边界；本阶段未授权真实外部视觉模型，真实视觉质量标记 NOT_RUN。

[Windows OcrEngine](https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrengine)

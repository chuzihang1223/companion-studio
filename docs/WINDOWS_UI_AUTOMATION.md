# Windows UI Automation

无需新增 Python UIA 库。扩展原 PowerShell Windows worker，使用 Windows/.NET UIAutomationClient、UIAutomationTypes 和原 C# Win32 辅助类。所有 COM 控件留在同一个 **MTA 进程**，不传递到 asyncio、renderer 或别的线程。只把有界普通数据返回主进程。

实际读取 Button、Edit、Text、List、Menu、Tree 的名称、AutomationId、类型、物理边界、可见/启用状态、允许读取的 Value。查询最多 600 节点、24 层、约 2.5 秒；读取异常或截断会禁止图片预览，语义观察标记截断。不保证 UIA 内部所有供应商调用都响应截止时间；原 worker 整体 22 秒超时后只终止自有 worker。

密码节点、原生密码 Edit 及敏感名称/值不返回。安全区域按绝对边界遮罩并扩展像素边缘；无法确定遮罩边界则不返回图像。

观察只缓存序列化身份，执行前重新遍历 UIA。重新检查进程身份、窗口、尺寸、DPI、显示器布局、前台焦点和元素属性。元素 RuntimeId 不作为持久能力，离开当前短期观察后不可复用。输入使用 ValuePattern，按钮优先 InvokePattern，滚动使用 ScrollPattern；不支持的控件明确拒绝，不伪装成功。

## 坐标

所有原生边界使用 Windows 物理屏幕像素；预览/OCR 框同时记录图像和物理坐标。几何包含窗口 DPI、显示器 DPI/排列和负坐标，图像映射使用捕获原点及缩放。显示器/分辨率/窗口位置变化后旧候选失效，禁止点击。

单显示器真实 150% 环境可运行。100%、125%、显示器断开或跨屏变化若没有真实环境，Evaluation 记录 BLOCKED；数学映射单测不能证明对应硬件 GUI 通过。不自动修改系统 DPI。

## 限制

受保护窗口、UAC、锁屏、终端、安全设置、私人聊天、密码管理器和外部浏览器不作为通用视觉目标。UIA 不保证支持游戏、GPU 自绘、非标准或更高完整性级别应用。窗口存在不等于可访问，发送事件不等于业务目标完成。

实现依据：[UIA 线程模型](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-threading)、[GetDpiForWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getdpiforwindow)、[WindowFromPoint](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-windowfrompoint)。

# Computer Use 动作安全

所有七个工具进入原 ToolRegistry → PermissionPolicy/TrustedActions → Runtime → DesktopBridge。窗口能力由主进程原生确认授予，后端私有接口有控制令牌；renderer 不能直接提供程序路径、HWND、脚本、任意按键或原始点击坐标。工作流只包装原工具，不建立第二套执行器。

## 动作准入

- 窗口的 PID、启动时间、可执行路径和 HWND 必须与授权一致。
- 输入属于本会话及角色；窗口授权和短期观察均未过期。
- 候选唯一、精确、可见、启用，边界有效且属于授权窗口。
- UIA 执行前重建树和元素，复核几何/DPI/显示器和焦点。
- OCR 坐标回退另需明确开启、再次识别同一文字及区域命中验证。
- 密码、凭据、支付、终端、安全界面和私人聊天等目标明确拒绝。
- 点击需要具体后置验证；文本最多 500 字符。没有通用 shell/热键功能。

主进程与原 worker 各有串行队列。暂停会撤销 nonce：排队动作在真正 dispatch 前再次检查；恢复换新 nonce。每个观察仅消费一次，超时/未知结果不会以同一候选重新执行。

## 验证

输入使用 ValuePattern 后核验值；点击后重新读取具体控件是否存在/消失或文字是否等于预期；滚动核验 ScrollPattern 状态变化。状态区分 SUCCESS、FAILED、UNKNOWN、NEEDS_REVIEW。发送鼠标事件成功不算验证通过。

焦点在 Win32/控件调用前复核；Windows 焦点仍是共享状态，不宣称原子隔离。其他程序抢焦点时输入停止，Unknown/Needs Review 暂停并要求重新观察。操作超时先观察原窗口，不无条件重按提交。

Stop/Take Over 只撤销本窗口任务及自有 worker，不关闭被操作的用户应用，不停止别的产品版本或会话。Replay 只读，Checkpoint 不提供永久坐标能力。重启后所有授权需重新取得。

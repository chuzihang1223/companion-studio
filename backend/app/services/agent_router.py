"""以本地动作和任务表达路由会话，普通陪伴内容仍走聊天链路。"""

import re


class ConversationRouter:
    """返回 chat / agent 路由，不执行工具，也不决定本机权限。"""

    TASK = re.compile(
        r"(?:帮我|请|给我|替我|打开|运行|创建|编写|搜索|查找|播放|暂停|列出|读取|修改|删除|关闭|聚焦)"
        r".{0,32}(?:网页|网站|浏览器|B站|GitHub|YouTube|百度|搜索|查|代码|脚本|文件|目录|"
        r"音乐|歌曲|歌|程序|测试|计算器|贪吃蛇|打开|播放|运行|写|制作)"
        r"|^(?:(?:再次|重新|继续)\s*)?(?:打开|搜索|查找|播放|暂停|运行|创建|编写|列出|读取|修改|删除|关闭|聚焦|下一首|上一首|继续播放)"
        r"|^(?:在|去|到).{0,20}(?:B站|哔哩|GitHub|百度).{0,10}(?:搜索|查找|找)"
        r"|(?:看看|检查|分析|写进|写入|保存到|应用).{0,45}(?:项目|代码|算法|补丁|patch|\.py|\.ts|\.js|\.html)",
        re.I,
    )

    def route(self, message: str, mode: str = "auto") -> str:
        """显式 chat 保持聊天；其余模式仍按内容判断是否是实际任务。

        因此 agent 模式不是强制所有内容调用工具。前端自动工作设置可
        提交 auto，让明确操作进入 Agent 流程。
        """
        if mode == "chat":
            return "chat"
        from app.services.local_action_intent import LocalActionIntentResolver

        return (
            "agent"
            if LocalActionIntentResolver().resolve(message) or self.TASK.search(message)
            else "chat"
        )

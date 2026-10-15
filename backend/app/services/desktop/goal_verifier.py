"""依据工具结果进行确定性目标检查，不能用启动请求代替播放验证。"""

from __future__ import annotations

from typing import TYPE_CHECKING
from app.services.trace.integration import traced_verification

if TYPE_CHECKING:
    from app.services.agent_tools import ToolDetail


class GoalVerifier:
    """检查执行状态及音乐/编码验证，不承担完整语义目标拆解。"""

    @traced_verification
    def verify(self, goal: str, details: list[ToolDetail]) -> bool:
        """全部工具 done 后再检查特定目标；不证明所有子目标均被规划。

        播放必须有已验证的播放工具，编码必须有已验证的编码结果。
        其他目标保留现有基于完成状态的判断，避免注释整改改变策略。
        """
        if not details or any(item["status"] != "done" for item in details):
            return False
        if "播放" in goal and any(word in goal for word in ("音乐", "歌曲", "歌", "网易云", "QQ")):
            return any(
                item["name"] in {"music.play", "media.play"}
                and item["result"].get("verified") is True
                for item in details
            )
        if any(item["name"] == "coding.task" for item in details):
            return all(
                item["result"].get("verified") is True
                for item in details
                if item["name"] == "coding.task"
            )
        return True

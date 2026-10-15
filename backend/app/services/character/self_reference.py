"""Resolve references to the real application separately from character lore."""

import json
from pathlib import Path
import re


class AppIdentityProfile:
    @staticmethod
    def load() -> dict:
        return json.loads(Path(__file__).with_name("app_identity.json").read_text(encoding="utf-8"))


class SelfReferenceResolver:
    LORE = ("剧情", "世界观", "角色过去", "游戏设定", "游戏里")
    ORIGIN = ("诞生", "出生", "谁做", "谁开发", "开发出来", "被创造", "创造了你", "里面用了什么")

    def resolve(self, message: str) -> str:
        text = re.sub(r"\s+", "", message).casefold()
        if any(cue in text for cue in self.LORE) or (
            "游戏" in text and any(cue in text for cue in self.ORIGIN)
        ):
            return "character_lore"
        if any(
            cue in text
            for cue in (
                "技术上",
                "技术实现",
                "技术栈",
                "架构",
                "软件是怎么实现",
                "软件怎么实现",
                "代码是怎么",
            )
        ):
            return "technical_self"
        if any(cue in text for cue in self.ORIGIN) and any(
            cue in text for cue in ("你", "软件", "companionstudio", "伴舟")
        ):
            return "software_self"
        return "character_conversation"

    def context(self, message: str) -> str:
        intent = self.resolve(message)
        if intent == "character_conversation":
            return ""
        if intent == "character_lore":
            return "SELF_REFERENCE: character_lore\n用户明确问游戏世界观；按角色设定回答，不把软件研发过程混入剧情。未知剧情不要编造。"
        profile = AppIdentityProfile.load()
        facts = json.dumps(profile, ensure_ascii=False)
        direction = (
            "用户问技术实现，可以解释实际架构，但不要声称未实现能力。"
            if intent == "technical_self"
            else "用户问的是当前桌面软件如何逐步形成。自然解释它从文字对话逐步加入可选声音、记忆、工作台和电脑操作。不要把软件研发过程说成虚拟角色的出生经历。"
        )
        return f"SELF_REFERENCE: {intent}\n{direction}\n真实项目资料：{facts}\n不要编造日期、开发团队或个人身份；以资料为事实边界。不要把本地数据库与完全离线推理混为一谈。"

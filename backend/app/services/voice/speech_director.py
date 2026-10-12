"""Context-aware, low-latency speech planning for the selected voice.

This is the deterministic fallback for replies without LLM speech metadata.
It never adds words to visible text and it does not claim that every desired
prosody field can be executed by the ONNX engine.
"""

import re

from .speech_plan import (
    PreviousVoiceState, SpeechDelivery, SpeechPlan, SpeechProsody, SpeechSegmentPlan,
)
from .speech_transition import SpeechTransitionManager
from .speech_context import resolve_scene


INTENTS = frozenset({
    "casual_chat", "happy_reunion", "missing_you", "playful_teasing",
    "playful_complaint", "shy_affection", "soft_affection", "celebrating",
    "curious", "surprised_happy", "surprised_worried", "gentle_comfort",
    "worried", "slightly_annoyed_but_caring", "protective", "serious",
    "nostalgic", "soft_goodnight", "agent_working", "agent_success", "agent_problem",
})


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


class SpeechPolicy:
    """Stable character defaults; an intent and event alter each segment."""

    intent_emotions = {
        "casual_chat": "neutral", "happy_reunion": "happy", "missing_you": "gentle",
        "playful_teasing": "playful", "playful_complaint": "annoyed",
        "shy_affection": "embarrassed", "soft_affection": "gentle",
        "celebrating": "happy", "curious": "curious", "surprised_happy": "excited",
        "surprised_worried": "concerned", "gentle_comfort": "gentle",
        "worried": "concerned", "slightly_annoyed_but_caring": "annoyed",
        "protective": "protective", "serious": "serious", "nostalgic": "nostalgic",
        "soft_goodnight": "gentle", "agent_working": "neutral",
        "agent_success": "happy", "agent_problem": "concerned",
    }

    def prosody(self, intent: str, intensity: float, ending: str, previous: PreviousVoiceState) -> SpeechProsody:
        motion = {
            "celebrating": (1.06, 1.10, 1.18), "playful_teasing": (1.04, 1.04, 1.14),
            "worried": (0.96, 0.94, 0.88), "gentle_comfort": (0.94, 0.88, 0.86),
            "serious": (0.95, 0.96, 0.85), "protective": (0.98, 1.06, 0.92),
            "soft_goodnight": (0.93, 0.86, 0.83), "surprised_happy": (1.04, 1.08, 1.16),
            "surprised_worried": (0.98, 1.02, 1.04),
            "slightly_annoyed_but_caring": (1.01, 1.02, 1.02),
            "soft_affection": (0.95, 0.92, 0.90), "shy_affection": (0.98, 0.95, 0.94),
        }.get(intent, (1.0, 1.0, 1.0))
        pace_target, energy_target, range_target = motion
        pace = 1 + (pace_target - 1) * (0.55 + 0.45 * intensity)
        # A small continuity bound prevents a new "speaker" feel between clauses.
        pace = _clamp(pace, previous.pace - 0.08, previous.pace + 0.08)
        if ending in {"question_rise", "teasing_rise"}:
            range_target += 0.04
        return SpeechProsody(
            pace=round(_clamp(pace, 0.90, 1.10), 3),
            energy=round(1 + (energy_target - 1) * intensity, 3),
            pitch_center=round((range_target - 1) * 0.15, 3),
            pitch_range=round(1 + (range_target - 1) * intensity, 3),
            warmth=round(0.50 + (0.24 if intent in {"gentle_comfort", "soft_goodnight", "soft_affection"} else 0.08) * intensity, 3),
            tension=round(0.18 + (0.25 if intent in {"protective", "worried"} else 0.04) * intensity, 3),
        )


class SpeechDirector:
    def __init__(self, policy: SpeechPolicy | None = None):
        self.policy = policy or SpeechPolicy()
        self.transitions = SpeechTransitionManager()

    @staticmethod
    def _scene(user: str, history: list[dict[str, str]]) -> str:
        return resolve_scene(user, history)

    @staticmethod
    def _segments(reply: str) -> list[str]:
        cleaned = re.sub(r"\s+", " ", reply).strip()
        cleaned = re.sub(r"\[(?:开心|温柔|严肃|担心|笑|叹气)\]", "", cleaned)
        if not cleaned:
            return []
        parts = [part.strip() for part in re.findall(r"[^。！？!?]+[。！？!?]?", cleaned) if part.strip()]
        result: list[str] = []
        for part in parts:
            # A rhetorical dash or ellipsis can mark a real change of intent.
            # Keep both phrases long enough to avoid audible one-word seams.
            turn = re.search(r"——|……", part)
            if turn and turn.end() >= 8 and len(part) - turn.end() >= 8:
                result.append(part[:turn.end()].strip())
                part = part[turn.end():].strip()
            # Split at a semantic reversal, not every comma or fixed character count.
            if "不过" in part and not part.startswith("不过"):
                left, right = part.split("不过", 1)
                if len(left) >= 4:
                    result.append(left.strip())
                    part = "不过" + right
            # A soft reply can turn into a teasing complaint inside one sentence.
            turn = re.search(r"[，,](?=你怎么.{0,3}才)", part)
            if turn and turn.start() >= 4 and len(part) - turn.end() >= 6:
                result.append(part[:turn.end()].strip())
                part = part[turn.end():].strip()
            if len(part) > 35:
                commas = [match.end() for match in re.finditer("[，,；;]", part)]
                nearest = min(commas, key=lambda pos: abs(pos - len(part) / 2)) if commas else 0
                if 8 <= nearest <= len(part) - 8:
                    result.append(part[:nearest].strip())
                    part = part[nearest:].strip()
            if part:
                result.append(part)
        return result

    @staticmethod
    def _intent(text: str, scene: str, index: int) -> tuple[str, str | None, float]:
        if scene == "danger" and index == 0 and text.startswith(("哈哈", "哼哼")):
            return "playful_teasing", "concerned", 0.55
        if scene in {"injury", "danger"}:
            return ("protective" if "别" in text or "小心" in text else "worried", "concerned", 0.78)
        if text.startswith("不过") and any(word in text for word in ("别", "危险", "一个人")):
            return "worried", "playful", 0.68
        if scene == "goodnight":
            return "soft_goodnight", None, 0.48
        if scene == "serious":
            return "serious", None, 0.72
        if scene == "invited_teasing":
            if any(word in text for word in ("哼", "偷偷", "抓住", "抓到了", "听见没有")):
                return "playful_teasing", "gentle", 0.55
            return "gentle_comfort", "playful", 0.46
        if scene in {"tired", "tired_mild", "lonely", "sad"}:
            if scene == "tired" and index == 0 and text.startswith(("等等", "诶", "什么")):
                return "surprised_worried", "concerned", 0.54
            return ("worried" if scene == "tired" and index == 0 else "gentle_comfort", None, 0.67)
        if scene == "good_news":
            first_reaction = index == 0 and ("？" in text or text.startswith(("诶", "咦", "哇")))
            if first_reaction:
                return "surprised_happy", "happy", 0.62
            if any(word in text for word in ("分我", "别让我猜", "猜猜", "哼哼")):
                return "playful_teasing", "happy", 0.55
            if any(word in text for word in ("别着急", "先休息", "慢慢来", "别勉强")):
                return "gentle_comfort", "happy", 0.48
            if "？" in text or "?" in text:
                return "curious", "happy", 0.48
            # Warm approval and a brief cheer need different energy.
            return "celebrating", "happy", 0.68 if any(word in text for word in ("太好", "成功了", "庆祝", "开心")) else 0.52
        if scene == "prank":
            if index == 0 and text.startswith(("诶", "啊")):
                return "surprised_happy", "playful", 0.62
            return ("playful_teasing" if index == 0 and ("？" in text or "你怎么又这样" in text) else "slightly_annoyed_but_caring", "playful", 0.64)
        if scene == "self_neglect":
            if index == 0 and text.startswith("哼哼"):
                return "playful_teasing", "concerned", 0.58
            return ("slightly_annoyed_but_caring" if index == 0 else "worried", "concerned", 0.67)
        if scene == "affection":
            if text.startswith(("诶", "咦")) or ("？" in text and index == 0):
                return "shy_affection", "happy", 0.55
            if re.search(r"怎么.{0,3}才", text) or any(word in text for word in ("笨蛋", "哼")):
                return "playful_complaint", "gentle", 0.54
            return ("missing_you" if "想" in text else "soft_affection", "gentle", 0.58)
        if scene == "reunion":
            return ("playful_complaint" if "怎么才" in text else "happy_reunion", "happy", 0.62)
        if scene == "flattered":
            return "shy_affection", "playful", 0.57
        if scene == "memory":
            return "nostalgic", None, 0.50
        if scene == "agent_problem" and any(word in text for word in ("别急", "不代表", "再试", "一起试", "我们把")):
            return "gentle_comfort", None, 0.48
        if scene in {"agent_working", "agent_success", "agent_problem"}:
            return scene, None, 0.45
        if any(word in text for word in ("哼哼", "逗你", "又这样")):
            return "playful_teasing", None, 0.60
        if "？" in text or "?" in text:
            return "curious", None, 0.48
        if any(word in text for word in ("加油", "做得到", "相信你")):
            return "gentle_comfort", None, 0.55
        return "casual_chat", None, 0.38

    @staticmethod
    def _style_instruction(intent: str, scene: str, intensity: float, ending: str) -> str:
        manner = {
            "playful_teasing": "带一点小得意和调侃", "celebrating": "听到好消息后真诚地兴奋",
            "worried": "明显担心但克制", "gentle_comfort": "放轻声音，认真陪伴",
            "serious": "平稳而认真", "protective": "担心对方的安全，语气坚定",
            "surprised_happy": "先短暂惊讶，再自然回应", "surprised_worried": "先惊讶再关心",
            "slightly_annoyed_but_caring": "小小不满，随后带着关心",
            "soft_goodnight": "柔和地道晚安", "nostalgic": "带一点怀念", "curious": "自然好奇",
            "missing_you": "带着想念，轻柔回应", "soft_affection": "亲近但自然",
            "shy_affection": "有一点害羞", "playful_complaint": "轻轻抱怨又亲近",
        }.get(intent, "自然、灵动地聊天")
        strength = "明显" if intensity >= 0.70 else "稍微" if intensity >= 0.50 else "轻轻"
        ending_note = "句尾自然上扬" if ending in {"question_rise", "teasing_rise"} else "句尾柔和收住" if ending == "soft_fall" else "句尾干净落下"
        return f"年轻明亮的同一女声，{strength}{manner}，{ending_note}，不要播音腔。"

    def plan(
        self, *, user_message: str, current_reply: str,
        previous_messages: list[dict[str, str]] | None = None,
        character_state: dict[str, str] | None = None,
        previous_voice_state: PreviousVoiceState | None = None,
        segment_index_offset: int = 0,
        expression: str = "natural",
    ) -> SpeechPlan:
        history = previous_messages or []
        scene = self._scene(user_message, history)
        # Mode selects tools, never emotion. Infer delivery from the actual situation.
        previous = previous_voice_state or PreviousVoiceState()
        segments = []
        for index, text in enumerate(self._segments(current_reply)):
            intent, secondary, intensity = self._intent(text, scene, index + segment_index_offset)
            intensity = _clamp(intensity * {"restrained": 0.75, "natural": 1.0, "rich": 1.18}.get(expression, 1.0), 0.1, 0.9)
            intensity = self.transitions.intensity(intensity, previous if index or previous_voice_state else None)
            emotion = self.policy.intent_emotions[intent]
            ending = "question_rise" if text.endswith(("？", "?")) else "firm_fall" if text.endswith(("！", "!")) else "soft_fall"
            if intent in {"playful_teasing", "playful_complaint", "slightly_annoyed_but_caring"} and ending == "question_rise":
                ending = "teasing_rise"
            opening = "surprised" if intent.startswith("surprised") else "serious" if intent in {"protective", "serious"} else "soft" if intent in {"gentle_comfort", "soft_goodnight", "soft_affection"} else "casual"
            pause_before = 140 if index and text.startswith("不过") else 50 if index else 0
            pause_after = 170 if ending == "soft_fall" else 110
            prosody = self.policy.prosody(intent, intensity, ending, previous)
            segments.append(SpeechSegmentPlan(
                text=text, tts_text=text, intent=intent, emotion=emotion,
                secondary_emotion=secondary, intensity=intensity, prosody=prosody,
                delivery=SpeechDelivery(opening, ending, pause_before, pause_after),
                emphasis=tuple(word for word in ("又", "一个人", "危险", "成功") if word in text),
                style_instruction=self._style_instruction(intent, scene, intensity, ending),
            ))
            previous = PreviousVoiceState(emotion, intensity, prosody.pace)
        return SpeechPlan(tuple(segments), previous)

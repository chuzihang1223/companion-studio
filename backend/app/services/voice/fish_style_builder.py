"""Map a contextual speech plan to documented Fish S2 bracket cues.

Fish's REST schema has no separate style/direction field. S2 emotion cues belong
in the text field; keep the visible message and cached transcript untouched.
"""

from types import SimpleNamespace


class FishStyleBuilder:
    @classmethod
    def build(cls, text: str, intent: str, *, intensity: float = 0.5) -> str:
        return FishDirectionSession().directed_text(
            text, SimpleNamespace(intent=intent, intensity=intensity)
        )


class FishDirectionSession:
    """Carry delivery across one reply, without repeating cues at chunk boundaries.

    S2 accepts natural-language directions. Describe the intended relationship
    and delivery rather than interpreting playful affection as anger/doubt.
    State belongs to the reply, never to the shared provider or another chat.
    """

    DIRECTIONS = {
        "casual_chat": ("conversation", "relaxed, conversational"),
        "curious": ("curious", "curious, conversational"),
        "happy_reunion": ("happy", "warm, quietly pleased"),
        "missing_you": ("affection", "warm, quietly affectionate"),
        "soft_affection": ("affection", "warm, quietly affectionate"),
        "playful_teasing": ("playful", "lightly amused, conversational"),
        "playful_complaint": ("playful", "lightly amused, conversational"),
        "slightly_annoyed_but_caring": ("playful", "lightly amused, caring"),
        "shy_affection": ("shy", "warm, slightly shy"),
        "celebrating": ("celebrating", "happy, conversational"),
        "surprised_happy": ("surprised", "slightly surprised, pleased"),
        "surprised_worried": ("concerned", "slightly surprised, concerned"),
        "gentle_comfort": ("comfort", "warm, reassuring, conversational"),
        "worried": ("concerned", "concerned, caring, conversational"),
        "protective": ("protective", "caring, firm"),
        "serious": ("serious", "calm, matter-of-fact"),
        "nostalgic": ("nostalgic", "warm, nostalgic"),
        "soft_goodnight": ("goodnight", "soft voice, relaxed"),
        "agent_working": ("conversation", "relaxed, conversational"),
        "agent_success": ("happy", "warm, quietly pleased"),
        "agent_problem": ("concerned", "concerned, caring, conversational"),
    }

    def __init__(self, expression="natural"):
        self.last_delivery = None
        self.expression = expression

    def directed_text(self, text, control):
        spans = getattr(control, "style_spans", ()) or (
            (text, getattr(control, "intent", ""), getattr(control, "intensity", 0.5)),
        )
        output, cursor = [], 0
        for part, intent, intensity in spans:
            at = text.find(part, cursor)
            if at < 0 or not part:
                continue
            output.append(text[cursor:at])
            direction = self.DIRECTIONS.get(intent)
            if intent == "celebrating" and intensity < 0.58:
                direction = ("happy", "warm, quietly pleased")
            # Natural delivery lets the actual wording carry ordinary emotion.
            # Repeated happy/surprised/empathetic prompts caused an acted voice.
            if self.expression != "rich" and direction:
                direction = ("conversation", "conversational")
                if intent in {"serious", "protective"}:
                    direction = ("serious", "calm, conversational")
                elif intent in {"worried", "surprised_worried"}:
                    direction = ("concerned", "gentle, conversational")
                elif intent == "soft_goodnight":
                    direction = ("goodnight", "relaxed")
                if self.expression == "restrained" and intent not in {"serious", "protective"}:
                    direction = None
            if direction and direction[0] != self.last_delivery:
                output.append("[" + direction[1] + "]")
                self.last_delivery = direction[0]
            output.append(part)
            cursor = at + len(part)
        return "".join(output) + text[cursor:]

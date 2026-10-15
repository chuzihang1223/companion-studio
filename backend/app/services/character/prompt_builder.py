from app.services.character.loader import Character, ConversationExample


class CharacterPromptBuilder:
    # A small cue map steers tone; the model still uses the full message and context.
    TONE_CUES = {
        "serious": ("死亡", "去世", "离世", "自杀", "责任", "拯救世界"),
        "sad": ("自己扛", "孤独", "孤单", "没人理解", "失去", "一个人", "害怕", "没用"),
        "concerned": (
            "累",
            "压力",
            "压得",
            "喘不过气",
            "扛",
            "难过",
            "撑不住",
            "想放弃",
            "没睡",
            "熬夜",
        ),
        "happy": ("通过了", "面试过了", "成功了", "赢了", "生日", "开心", "好消息"),
        "playful": ("房租", "哈哈", "开玩笑", "逗你"),
        "angry": ("欺负", "不公", "伤害"),
        "protective": ("保护", "受伤"),
        "nostalgic": ("小时候", "回忆", "以前"),
        "embarrassed": ("害羞", "脸红"),
        "curious": ("为什么", "怎么回事", "好奇"),
    }

    @classmethod
    def detect_tone(cls, message: str) -> str:
        text = message.casefold()
        for tone, cues in cls.TONE_CUES.items():
            if any(cue.casefold() in text for cue in cues):
                return tone
        return "neutral"

    @staticmethod
    def select_examples(
        examples: list[ConversationExample], message: str
    ) -> list[ConversationExample]:
        text = message.casefold()
        if any(question in text for question in ("是不是ai", "是ai吗", "是真人吗")):
            # The direct disclosure rule should answer this without an unrelated roleplay example.
            return []
        matched = [
            example
            for example in examples
            if example.user.casefold() != text
            and any(keyword.casefold() in text for keyword in example.keywords)
        ]
        if matched:
            # Prefer the most specific cue when two situations share a word.
            matched.sort(
                key=lambda example: max(
                    len(keyword) for keyword in example.keywords if keyword.casefold() in text
                ),
                reverse=True,
            )
            return matched[:2]
        if any(example.user.casefold() == text for example in examples):
            return []
        return [example for example in examples if example.situation == "daily"][:1]

    def build(
        self,
        character: Character,
        history: list[dict[str, str]],
        user_message: str,
        memory_context=None,
        reply_context=None,
        personalization=None,
        manual_memory=None,
    ):
        """Use only the selected user-owned character and visible conversation."""
        from app.services.character.generic_prompt import build_generic_prompt

        return build_generic_prompt(
            character,
            history,
            user_message,
            memory_context,
            reply_context,
            personalization,
            manual_memory,
        )

import asyncio
import base64
from io import BytesIO
import re
from types import SimpleNamespace
import wave
import pytest

from app.services.voice.continuous_pipeline import RealtimeVoicePipeline
from app.services.voice.fish_style_builder import FishDirectionSession
from app.services.voice.canonical_text import IncrementalSpeechText


def test_quality_opening_keeps_the_reaction_with_its_following_sentence():
    chunker = IncrementalSpeechText(initial_chars=16, initial_limit=60)
    assert chunker.feed("真的？你成功了！") == []
    parts = chunker.feed("我就知道你能做到。今天这份开心，你可要分我一半呀！", final=True)
    assert parts[0] == "真的？你成功了！我就知道你能做到。"
    assert "".join(parts) == "真的？你成功了！我就知道你能做到。今天这份开心，你可要分我一半呀！"


def test_ordinary_fatigue_uses_warm_comfort_without_a_worried_opening():
    from app.services.voice.speech_director import SpeechDirector

    plan = SpeechDirector().plan(
        user_message="你好，今天有点累。", current_reply="先歇一会儿吧，我陪着你。"
    )
    assert all(segment.intent == "gentle_comfort" for segment in plan.segments)


def test_reply_direction_survives_chunks_and_preserves_the_exact_body():
    session = FishDirectionSession(expression="rich")
    first = session.directed_text(
        "哼哼，又熬夜了吧？",
        SimpleNamespace(style_spans=(("哼哼，又熬夜了吧？", "playful_teasing", 0.58),)),
    )
    second = session.directed_text(
        "这回让我抓住啦。今晚早点睡。",
        SimpleNamespace(
            style_spans=(
                ("这回让我抓住啦。", "playful_complaint", 0.54),
                ("今晚早点睡。", "worried", 0.67),
            )
        ),
    )
    assert first.count("[") == 1 and second.count("[") == 1
    assert "frustrated" not in first + second and "doubtful" not in first + second
    assert (
        re.sub(r"\[[^\]]+\]", "", first + second)
        == "哼哼，又熬夜了吧？这回让我抓住啦。今晚早点睡。"
    )
    # A different reply owns a fresh session; it does not inherit the last cue.
    assert (
        FishDirectionSession(expression="rich")
        .directed_text("你好。", SimpleNamespace(intent="worried"))
        .startswith("[")
    )


def test_live_reply_keeps_first_reaction_and_then_moves_to_comfort():
    async def run():
        received = []

        class Voice:
            name = "fish-test"
            config = SimpleNamespace(speed=1, latency="normal")
            new_direction_session = FishDirectionSession

            async def synthesize_live(self, queue, *_args):
                while (part := await queue.get()) is not None:
                    received.append(part)
                return [], 44100, "fixture"

        app = SimpleNamespace(state=SimpleNamespace(voice_client=Voice()))
        payload = SimpleNamespace(
            voice_enabled=True, message="我很累，睡得很少。", expression="rich", speed_factor=1
        )

        async def deltas():
            yield "你今天已经忙了很久，晚上也没睡好吧。"
            yield "先别硬撑，我们慢慢来，好吗？"

        async def complete(_text):
            return {}

        pipeline = RealtimeVoicePipeline(app, payload, "emotion-test", None)
        events = [event async for event in pipeline.events(deltas(), [], complete)]
        assert "concerned" in received[0]
        assert "warm, reassuring" in received[1]
        assert not any(event == "voice_error" for event, _data in events)

    asyncio.run(run())


def test_invited_teasing_about_sleep_is_playful_then_caring():
    from app.services.voice.speech_director import SpeechDirector

    plan = SpeechDirector().plan(
        user_message="昨天又偷偷熬夜了，逗逗我吧。",
        current_reply="哼哼，又偷偷熬夜了是不是？今晚早点睡，我陪着你。",
    )
    assert [s.intent for s in plan.segments] == ["playful_teasing", "gentle_comfort"]


def test_negative_result_does_not_become_a_celebration():
    from app.services.voice.speech_director import SpeechDirector

    director = SpeechDirector()
    for user in ["没有成功，修复失败了。", "这次考试没通过，有点失落。"]:
        plan = director.plan(user_message=user, current_reply="先别急，一次没成功不代表做不到。")
        assert all(s.intent not in {"celebrating", "surprised_happy"} for s in plan.segments)


def test_good_news_can_turn_from_surprise_to_approval_and_playfulness():
    from app.services.voice.speech_director import SpeechDirector

    text = "真的？你成功了！我就知道你能做到。今天这份开心，你可要分我一半呀！"
    plan = SpeechDirector().plan(user_message="我今天面试通过了，成功啦！", current_reply=text)
    assert plan.segments[0].intent == "surprised_happy"
    assert plan.segments[-1].intent == "playful_teasing"
    control = SimpleNamespace(
        style_spans=tuple((s.tts_text, s.intent, s.intensity) for s in plan.segments)
    )
    directed = FishDirectionSession(expression="rich").directed_text(text, control)
    assert re.sub(r"\[[^\]]+\]", "", directed) == text
    assert "quietly pleased" in directed and "lightly amused" in directed
    assert "excited" not in directed and "shouting" not in directed


def test_natural_delivery_avoids_acting_cues_and_repeated_positive_tags():
    from app.services.voice.speech_director import SpeechDirector

    text = "真的？你成功了！我就知道你能做到。今天这份开心，你可要分我一半呀！"
    plan = SpeechDirector().plan(user_message="我今天面试通过了！", current_reply=text)
    control = SimpleNamespace(
        style_spans=tuple((s.tts_text, s.intent, s.intensity) for s in plan.segments)
    )
    directed = FishDirectionSession().directed_text(text, control)
    assert directed == "[conversational]" + text
    assert FishDirectionSession(expression="restrained").directed_text(text, control) == text
    serious = FishDirectionSession().directed_text(
        "先把情况看清楚。", SimpleNamespace(intent="serious")
    )
    assert serious.startswith("[calm, conversational]")


def test_large_provider_packet_is_split_without_losing_or_repeating_pcm():
    async def run():
        raw = b"\x10\x00" * (900000)

        class Voice:
            name = "fixture"
            config = SimpleNamespace(speed=1, latency="normal")

            async def synthesize_live(self, queue, _speed, on_pcm):
                while await queue.get() is not None:
                    pass
                await on_pcm(raw, 44100, 1)
                return [raw], 44100, "fixture"

        app = SimpleNamespace(state=SimpleNamespace(voice_client=Voice()))
        payload = SimpleNamespace(
            voice_enabled=True, message="你好", expression="natural", speed_factor=1
        )

        async def deltas():
            yield "你好呀，我们慢慢聊一会儿吧。"

        async def complete(_text):
            return {}

        class DB:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def get(self, *_args):
                return None

        pipeline = RealtimeVoicePipeline(app, payload, "large-pcm", DB)
        events = [
            data
            async for event, data in pipeline.events(deltas(), [], complete)
            if event == "audio_pcm"
        ]
        assert len(events) > 1
        assert [row["sequence"] for row in events] == list(range(len(events)))
        assert all(len(row["pcm_base64"]) < 1400000 for row in events)
        assert b"".join(base64.b64decode(row["pcm_base64"]) for row in events) == raw

    asyncio.run(run())


def test_explicit_new_scene_does_not_inherit_previous_worry():
    from app.services.voice.speech_director import SpeechDirector

    director = SpeechDirector()
    assert (
        director.plan(
            user_message="晚安呀",
            current_reply="晚安，安心睡吧。",
            previous_messages=[{"role": "user", "content": "今天摔倒受伤了。"}],
        )
        .segments[0]
        .intent
        == "soft_goodnight"
    )
    assert (
        director.plan(
            user_message="这件事很重要，认真陪我看看。", current_reply="嗯，先把情况弄清楚。"
        )
        .segments[0]
        .intent
        == "serious"
    )


@pytest.mark.parametrize("after_text_drained", [False, True])
def test_local_fallback_is_only_before_audio_and_keeps_canonical_reply(after_text_drained):
    async def run():
        calls = []

        class Local:
            name, available = "index-test", True

            def synthesize(self, text, _control):
                calls.append(text)
                buffer = BytesIO()
                with wave.open(buffer, "wb") as writer:
                    writer.setnchannels(1)
                    writer.setsampwidth(2)
                    writer.setframerate(32000)
                    writer.writeframes(b"\x10\x00" * 3200)
                return {
                    "engine": self.name,
                    "audio_base64": base64.b64encode(buffer.getvalue()).decode(),
                }

        class Cloud:
            name = "fish-test"
            config = SimpleNamespace(speed=1, latency="normal")

            async def synthesize_live(self, queue, *_args):
                if after_text_drained:
                    while (part := await queue.get()) is not None:
                        pass
                raise OSError("offline")

        cloud, local = Cloud(), Local()
        router = SimpleNamespace(choose=lambda: cloud, fallback=lambda *_args: local)
        app = SimpleNamespace(state=SimpleNamespace(voice_client=router))
        payload = SimpleNamespace(
            voice_enabled=True, message="你好", expression="natural", speed_factor=1
        )

        async def deltas():
            yield "你好呀，今天也见到你了。"

        async def complete(_text):
            return {}

        class DB:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def get(self, *_args):
                return None

        pipeline = RealtimeVoicePipeline(app, payload, "fallback-test", DB)

        async def consume():
            return [event async for event in pipeline.events(deltas(), [], complete)]

        events = await asyncio.wait_for(consume(), 1)
        assert calls == ["你好呀，今天也见到你了。"]
        assert len([event for event, _data in events if event == "audio_pcm"]) == 1
        assert not any(event == "voice_error" for event, _data in events)

    asyncio.run(run())

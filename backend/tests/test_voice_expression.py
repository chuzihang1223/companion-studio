from app.services.voice.policy import VoiceExpressionPolicy
from app.services.voice.resolver import EmotionHint, EmotionResolver
from app.services.voice.reference_manager import ReferenceManager
from app.services.voice.text_processor import ExpressiveTextProcessor


def test_all_emotions_have_safe_native_speed_and_reference_fallback():
    policy = VoiceExpressionPolicy()
    assert len(policy.emotions) == 12
    for emotion in policy.emotions:
        style = policy.style(emotion, 0.7, "rich")
        assert 0.9 <= style.speed <= 1.12
        assert 0.18 <= style.pause_seconds <= 0.45
        assert style.pitch_shift == 0.0
        assert style.pitch_variation == 1.0
        assert style.energy == 1.0
        assert policy.reference_for(emotion, {"neutral"}) == "neutral"


def test_resolver_corrects_contradictions_and_preserves_context():
    resolver = EmotionResolver()
    bedtime = resolver.resolve(EmotionHint("excited", 0.9), user_text="晚安。")
    assert bedtime.emotion == "gentle"
    danger = resolver.resolve(EmotionHint("happy", 0.8), user_text="我不想活了")
    assert danger.emotion == "serious"
    smoothed = resolver.resolve(EmotionHint("happy", 0.2), previous_emotion="concerned")
    assert smoothed.emotion == "concerned"
    happy = resolver.resolve(EmotionHint("happy", 0.8), user_text="我成功了！")
    assert happy.emotion == "happy"


def test_text_processor_omits_stage_cues_without_changing_visible_text():
    source = "（开心）真的？你成功了？！不过……下次别冒险，好吗？"
    segments = ExpressiveTextProcessor().segments(source)
    assert [item.text for item in segments] == ["真的？", "你成功了？！", "不过……下次别冒险，好吗？"]
    assert source.startswith("（开心）")


def test_sentence_level_shift_keeps_playful_intro_then_becomes_concerned():
    text = "哈哈，你居然真的这么做了？不过……下次别拿自己冒险，好吗？"
    segments = ExpressiveTextProcessor().segments(text)
    result = EmotionResolver().resolve_segments(segments, EmotionHint("playful", 0.7))
    assert [segment.style.emotion for segment in result] == ["playful", "concerned"]


def test_reference_catalog_falls_back_to_one_existing_voice(tmp_path):
    class Engine:
        pass

    engine = Engine()
    for name in (
        "reference_16k", "reference_32k", "reference_text", "reference_seq",
        "reference_bert", "ssl_content", "speaker_embedding", "_prompt_cache",
    ):
        setattr(engine, name, "neutral")
    manager = ReferenceManager(engine, tmp_path, tmp_path, VoiceExpressionPolicy(), lambda *_: None)
    assert manager.select("playful") == "neutral"
    (tmp_path / "happy.wav").write_bytes(b"placeholder")
    (tmp_path / "happy.txt").write_text("same voice", encoding="utf-8")
    assert manager.select("playful") == "happy"
    assert manager.select("protective") == "neutral"
    with manager.use("protective") as selected:
        assert selected == "neutral"
        assert engine.reference_text == "neutral"
    assert engine.reference_text == "neutral"

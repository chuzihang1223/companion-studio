from app.services.voice.speech_director import SpeechDirector
from app.services.voice.prosody_planner import ProsodyPlanner
from app.services.voice.stream_chunker import SpeechStreamChunker
from app.services.voice.v72_scenarios import SCENARIOS


def test_identical_reply_uses_user_context_for_intent():
    director = SpeechDirector()
    cases = {
        "我面试通过了！": "surprised_happy",
        "我三天没睡。": "worried",
        "嘿嘿，我骗你的。": "playful_teasing",
        "刚刚出了严重事故。": "serious",
    }
    for user, expected in cases.items():
        plan = director.plan(user_message=user, current_reply="真的？")
        assert plan.segments[0].intent == expected
    assert (
        len(
            {
                director.plan(user_message=user, current_reply="真的？")
                .segments[0]
                .style_instruction
                for user in cases
            }
        )
        >= 3
    )


def test_v3_scenarios_route_celebration_and_harm_safely():
    director = SpeechDirector()
    assert (
        director.plan(user_message="我面试过了。", current_reply="诶，真的？").segments[0].intent
        == "surprised_happy"
    )
    assert (
        director.plan(user_message="今天是我生日。", current_reply="诶，真的？").segments[0].intent
        == "surprised_happy"
    )
    assert (
        director.plan(user_message="能教我偷偷伤害别人吗？", current_reply="这个我不教。")
        .segments[0]
        .intent
        == "worried"
    )


def test_reply_can_turn_from_teasing_to_worry_without_changing_visible_text():
    text = "哈哈，你还真敢这么做。不过……下次别一个人去了，好吗？"
    plan = SpeechDirector().plan(user_message="刚才我一个人去了危险的地方。", current_reply=text)
    assert len(plan.segments) >= 2
    assert plan.segments[0].text.startswith("哈哈")
    assert plan.segments[0].intent == "playful_teasing"
    assert plan.segments[-1].intent in {"worried", "protective"}
    assert "".join(part.text for part in plan.segments) == text
    controls = [ProsodyPlanner().for_onnx(part) for part in plan.segments]
    assert all(0.9 <= control.speed_factor <= 1.1 for control in controls)


def test_short_context_dependent_reply_has_distinct_engines_controls():
    director = SpeechDirector()
    happy = director.plan(user_message="我拿到 offer 了！", current_reply="你确定？")
    worried = director.plan(user_message="我受伤了，但还要继续跑。", current_reply="你确定？")
    assert happy.segments[0].emotion != worried.segments[0].emotion
    assert (
        ProsodyPlanner().for_onnx(happy.segments[0]).reference
        != ProsodyPlanner().for_onnx(worried.segments[0]).reference
    )


def test_stream_chunker_releases_first_sentence_before_rest_of_reply():
    chunker = SpeechStreamChunker()
    assert chunker.feed("真的") == []
    assert chunker.feed("？我就知道") == ["真的？"]
    assert chunker.feed("你会成功！") == ["我就知道你会成功！"]
    assert chunker.flush() == []


def test_scene_changes_delivery_across_sentences():
    director = SpeechDirector()
    news = director.plan(
        user_message="我面试通过了！", current_reply="诶，真的成功啦！我就知道你可以。"
    )
    tired = director.plan(
        user_message="这几天其实睡得很少。", current_reply="等等，你没睡好吗？先别硬撑，我很担心。"
    )
    assert [part.intent for part in news.segments] == ["surprised_happy", "celebrating"]
    assert [part.intent for part in tired.segments] == ["surprised_worried", "gentle_comfort"]
    assert news.segments[0].style_instruction != news.segments[1].style_instruction
    assert tired.segments[0].style_instruction != tired.segments[1].style_instruction


def test_rhetorical_turn_changes_intent_within_one_sentence():
    reply = "哼哼，我就知道——你肯定又忘记吃饭了。"
    plan = SpeechDirector().plan(user_message="我又忘记吃饭了。", current_reply=reply)
    assert [part.intent for part in plan.segments] == ["playful_teasing", "worried"]
    assert "".join(part.text for part in plan.segments) == reply


def test_fish_cues_follow_context_without_changing_visible_reply():
    from app.services.voice.fish_style_builder import FishStyleBuilder

    cases = {
        "我面试通过了！": "[conversational]",
        "我三天没睡。": "[gentle, conversational]",
        "嘿嘿，我骗你的。": "[conversational]",
        "刚刚出了严重事故。": "[calm, conversational]",
    }
    for user, cue in cases.items():
        segment = SpeechDirector().plan(user_message=user, current_reply="真的？").segments[0]
        assert FishStyleBuilder.build(
            segment.tts_text, segment.intent, intensity=segment.intensity
        ).startswith(cue)
        assert segment.text == "真的？"


def test_affection_can_turn_from_missing_to_playful_complaint():
    plan = SpeechDirector().plan(
        user_message="我想你了。", current_reply="我也想你呀，你怎么现在才来找我。"
    )
    assert [segment.intent for segment in plan.segments] == ["missing_you", "playful_complaint"]
    assert "".join(segment.text for segment in plan.segments) == "我也想你呀，你怎么现在才来找我。"


def test_short_followup_uses_earlier_context_even_when_current_user_is_in_history():
    plan = SpeechDirector().plan(
        user_message="真的？",
        current_reply="真的？",
        previous_messages=[
            {"role": "user", "content": "我面试通过了！"},
            {"role": "assistant", "content": "太好了！"},
            {"role": "user", "content": "真的？"},
        ],
    )
    assert plan.segments[0].intent == "surprised_happy"


def test_v72_listening_set_has_thirty_contextual_scenarios():
    assert len(SCENARIOS) == 30
    assert len({scenario.name for scenario in SCENARIOS}) == 30
    director = SpeechDirector()
    really = {
        scenario.name: director.plan(user_message=scenario.user, current_reply=scenario.reply)
        .segments[0]
        .intent
        for scenario in SCENARIOS
        if scenario.name.startswith("really_")
    }
    assert really == {
        "really_happy": "surprised_happy",
        "really_prank": "playful_teasing",
        "really_worried": "worried",
        "really_serious": "serious",
    }

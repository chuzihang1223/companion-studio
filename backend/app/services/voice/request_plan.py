"""Keep short emotional phrases inside a longer semantic TTS request."""
from dataclasses import replace
from .speech_director import SpeechDirector
from .stream_chunker import SpeechStreamChunker
from .speech_filter import speakable_text


def fish_request_plan(reply, user_text, history, expression, character_state=None):
    reply=speakable_text(reply) or '代码放在工作区了，可以直接查看。'
    chunker=SpeechStreamChunker(min_chars=20,max_chars=60)
    chunks=chunker.feed(reply)+chunker.flush()
    segments=[]
    final=None
    for chunk in chunks:
        plan=SpeechDirector().plan(user_message=user_text,current_reply=chunk,previous_messages=history,
            character_state=character_state,expression=expression,segment_index_offset=len(segments))
        if not plan.segments:continue
        spans=tuple((part.tts_text,part.intent,part.intensity) for part in plan.segments)
        segments.append(replace(plan.segments[0],text=chunk,tts_text=chunk,style_spans=spans))
        final=plan
    if final is None:raise ValueError('No speakable text')
    return replace(final,segments=tuple(segments))

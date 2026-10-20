"""Capability-aware per-turn TTS snapshot, using existing configured providers."""

import asyncio
import base64
from dataclasses import replace
from io import BytesIO
import threading
import wave

from app.services.voice.prosody_planner import ProsodyPlanner
from app.services.voice.speech_director import SpeechDirector


async def local_fallback_audio(provider, text, speed, on_pcm):
    from app.services.voice.canonical_text import IncrementalSpeechText

    adapter, queue = ConfiguredTTS(provider), asyncio.Queue()
    for part in IncrementalSpeechText().feed(text, final=True):
        queue.put_nowait(part)
    queue.put_nowait(None)
    try:
        parts, rate, _ = await adapter.synthesize_live(queue, speed, on_pcm)
        output = BytesIO()
        with wave.open(output, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(rate)
            writer.writeframes(b"".join(parts))
        return {
            "audio_base64": base64.b64encode(output.getvalue()).decode(),
            "engine": provider.name,
        }
    finally:
        await adapter.close()


async def chunked_synthesis(provider, queue, speed, on_pcm, cancellation=None):
    parts, rate, channels = [], None, None
    size = 0
    while True:
        if cancellation and cancellation.is_set():
            raise asyncio.CancelledError()
        try:
            # A stopped text producer cannot send its end marker. Wake boundedly
            # while idle, without cancelling an in-flight native request.
            text = await asyncio.wait_for(queue.get(), 0.1)
        except asyncio.TimeoutError:
            continue
        if text is None:
            break
        plan = SpeechDirector().plan(user_message="", current_reply=text, expression="natural")
        if not plan.segments:
            continue
        control = replace(ProsodyPlanner().for_expressive(plan.segments[0]), speed_factor=speed)
        arguments = {"cancel_event": cancellation} if cancellation is not None else {}
        result = await asyncio.to_thread(provider.synthesize, text, control, **arguments)
        with wave.open(BytesIO(base64.b64decode(result["audio_base64"], validate=True))) as reader:
            current_rate, current_channels = reader.getframerate(), reader.getnchannels()
            if reader.getsampwidth() != 2 or current_channels != 1:
                raise ValueError("AUDIO_FORMAT_INVALID")
            if rate is not None and (current_rate, current_channels) != (rate, channels):
                raise ValueError("AUDIO_FORMAT_INVALID")
            rate, channels = current_rate, current_channels
            raw = reader.readframes(reader.getnframes())
        size += len(raw)
        if size > 16_000_000:
            raise ValueError("AUDIO_BUFFER_LIMIT")
        parts.append(raw)
        for offset in range(0, len(raw), 16384):
            if cancellation and cancellation.is_set():
                raise asyncio.CancelledError()
            await on_pcm(raw[offset : offset + 16384], rate, channels)
    return parts, rate or 16000, "local-chunked"


class ConfiguredTTS:
    def __init__(self, provider, fallback_provider=None):
        # Snapshot Fish configuration without copying a stored API key to UI;
        # existing credentials remain in the provider's protected backend service.
        self.source_provider = provider
        self.owned_provider = False
        if provider.name.startswith("fish-"):
            from app.services.voice.fish_provider import FishAudioProvider

            provider = FishAudioProvider(
                replace(provider.config, api_key=provider.credentials.get())
            )
            self.owned_provider = True
        self.provider = provider
        self.name = provider.name
        self.config = getattr(provider, "config", None)
        self.cancellation = threading.Event()
        self.fallback_provider = (
            fallback_provider if self.owned_provider and self.config.fallback_enabled else None
        )

    def fallback(self, error):
        from app.services.voice.fish_provider import FishAudioError

        return (
            self.fallback_provider
            if isinstance(error, FishAudioError) and error.status_code in {0, 500, 502, 503, 504}
            else None
        )

    def new_direction_session(self, expression="natural"):
        # Original Fish direction policy remains per utterance. IndexTTS uses
        # its existing local planner and does not invent prompt capabilities.
        factory = getattr(self.provider, "new_direction_session", None)
        return factory(expression=expression) if factory else self

    def directed_text(self, text, _control):
        return text

    async def synthesize_live(self, queue, speed, on_pcm):
        if hasattr(self.provider, "synthesize_live"):
            return await self.provider.synthesize_live(queue, speed, on_pcm)
        work = asyncio.create_task(
            chunked_synthesis(self.provider, queue, speed, on_pcm, self.cancellation)
        )
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            self.cancellation.set()
            # Keep the original voice semaphore until the owned native request
            # acknowledges cancellation; late worker results cannot poison a new request.
            # Disconnect and explicit interrupt may cancel this task separately.
            # Every cleanup await must stay shielded until the native thread exits.
            while not work.done():
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not work.cancelled():
                work.exception()  # Retrieve a native cancellation error without replacing ours.
            raise

    async def cancel(self):
        self.cancellation.set()

    async def close(self):
        self.cancellation.set()
        if self.owned_provider:
            await self.provider.aclose()
            self.provider.credentials.update("")

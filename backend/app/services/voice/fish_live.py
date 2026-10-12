"""Fish's documented start/text/flush/stop protocol, one connection per utterance."""
import asyncio
import httpx
import ormsgpack
from httpx_ws import aconnect_ws, WebSocketUpgradeError
from fishaudio.types import TTSRequest, Prosody, StartEvent, TextEvent, FlushEvent, CloseEvent
from fishaudio.resources.realtime import aiter_websocket_audio
from .pcm_stream import PcmStreamDecoder


async def synthesize_live(provider, queue, speed, on_pcm):
    from .fish_provider import FishAudioError, _record_test_request
    if not provider.ready:
        raise FishAudioError(0, 'Fish 密钥或声音 ID 尚未配置')
    if provider.async_client is None or provider.async_client.is_closed:
        provider.async_client = httpx.AsyncClient(base_url='https://api.fish.audio',
            timeout=httpx.Timeout(90, connect=10))
    client, config = provider.async_client, provider.config
    sent, pcm_parts, ended, sender = [], [], False, None
    headers={'Authorization':'Bearer '+provider.credentials.get(),'model':config.model}

    async def emit(raw,rate=44100,channels=1):
        if raw:
            pcm_parts.append(raw)
            await on_pcm(raw,rate,channels)

    async def drain_text():
        nonlocal ended
        while not ended:
            part=await queue.get()
            if part is None:
                ended=True
            else:
                sent.append(part)

    if config.transport_mode != 'standard':
        try:
            _record_test_request('websocket-live')
            async with aconnect_ws('/v1/tts/live', client=client, headers=headers,
                    keepalive_ping_timeout_seconds=60, max_message_size_bytes=8*1024*1024) as ws:
                request=TTSRequest(text='',format='pcm',sample_rate=44100,reference_id=config.reference_id,
                    latency=config.latency,normalize=True,temperature=.7,top_p=.7,
                    prosody=Prosody(speed=max(.5,min(2,speed))),condition_on_previous_chunks=True,max_new_tokens=4096)
                await ws.send_bytes(ormsgpack.packb(StartEvent(request=request).model_dump()))

                async def send():
                    nonlocal ended
                    first=True
                    while (part:=await queue.get()) is not None:
                        sent.append(part)
                        await ws.send_bytes(ormsgpack.packb(TextEvent(text=part).model_dump()))
                        # One early flush reduces TTFA without forcing a seam at every sentence.
                        if first:
                            first=False
                            await ws.send_bytes(ormsgpack.packb(FlushEvent().model_dump()))
                    ended=True
                    await ws.send_bytes(ormsgpack.packb(CloseEvent().model_dump()))

                sender=asyncio.create_task(send())
                try:
                    async for raw in aiter_websocket_audio(ws):
                        await emit(raw)
                    await sender
                finally:
                    if not sender.done():sender.cancel()
                    await asyncio.gather(sender,return_exceptions=True)
            provider.authorized=True
            return pcm_parts,44100,'websocket-live'
        except asyncio.CancelledError:
            raise
        except Exception as error:
            status=error.response.status_code if isinstance(error,WebSocketUpgradeError) else getattr(error,'status_code',0)
            if pcm_parts or status in {401,402,403,404,422,429}:
                raise FishAudioError(status,'Fish 流式语音中断；已停止，避免重复朗读') from error
            # Only an inaudible connection failure can use HTTP. Never replay delivered PCM.
    await drain_text()
    text=''.join(sent)
    if not text.strip():return [],44100,'empty'
    payload,http_headers=provider._request(text,speed)
    decoder=PcmStreamDecoder()
    _record_test_request('http-live-fallback')
    try:
        async with client.stream('POST','/v1/tts',json=payload,headers=http_headers) as response:
            if response.status_code!=200:
                raise FishAudioError(response.status_code,f'Fish HTTP {response.status_code}')
            async for raw in response.aiter_bytes():
                await emit(decoder.feed(raw),decoder.rate,decoder.channels)
    except (httpx.TimeoutException,httpx.TransportError) as error:
        raise FishAudioError(0,'Fish HTTP 语音超时或连接中断') from error
    provider.authorized=True
    return pcm_parts,decoder.rate,'http-stream'

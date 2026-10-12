import json

async def routed_sse(source, envelope):
    """Attach immutable ownership to every event; no active UI state is consulted."""
    async for frame in source:
        lines=frame.splitlines()
        event=next((line[7:] for line in lines if line.startswith('event: ')), 'message')
        raw=next((line[6:] for line in lines if line.startswith('data: ')), None)
        if raw is None:
            yield frame;continue
        data={**envelope,**json.loads(raw),'event_type':event}
        yield f'event: {event}\ndata: {json.dumps(data,ensure_ascii=False,separators=(",",":"))}\n\n'

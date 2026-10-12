"""Local health only; presence polling never sends a paid model request."""
from fastapi import APIRouter, Request

router = APIRouter(prefix='/api', tags=['presence'])


@router.get('/presence')
def presence(request: Request) -> dict:
    state = request.app.state
    llm = state.llm_service
    config = getattr(llm, 'config', None)
    configured = bool(config and config.api_key and config.model)
    voice = state.voice_client
    error = bool(getattr(llm, 'last_error', False))
    return {'backend': True, 'llm': {'configured': configured, 'available': configured and not error,
                                   'checked': getattr(llm, 'availability_checked', False)},
            'agent': bool(getattr(state, 'task_runtime', None)),
            'voice': {'available': bool(voice.available and not voice.error), 'status': voice.status}}

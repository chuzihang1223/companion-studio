from app.services.voice.worker_client import IndexVoiceClient


def test_production_client_has_only_index_engine():
    voice = IndexVoiceClient()
    assert voice.engine == "index"
    assert voice.name == "indextts-2.5-expressive"
    assert voice.selected_engine == "index"
    assert not hasattr(voice, "fallback")

import base64
from io import BytesIO
import wave
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import voice as voice_api
from app.database.session import Base, get_db
from app.main import app
from app.models import Conversation, Message
from app.services.voice import message_cache
from app.services.voice.message_cache import save_message_audio


def wav_chunk(rate=16000):
    output = BytesIO()
    with wave.open(output, 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes((b'\x00\x00\x10\x00' * (rate // 2)))
    return base64.b64encode(output.getvalue()).decode()


def test_each_message_keeps_its_own_replayable_wav(tmp_path, monkeypatch):
    monkeypatch.setattr(message_cache, 'settings', SimpleNamespace(data_root=tmp_path))
    monkeypatch.setattr(voice_api, 'settings', SimpleNamespace(data_root=tmp_path))
    engine = create_engine(f"sqlite:///{(tmp_path / 'test.db').as_posix()}", connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(voice_api, 'SessionLocal', factory)
    with factory() as db:
        conversation = Conversation(character_id='assistant')
        db.add(conversation)
        db.flush()
        first = Message(character_id='assistant', conversation_id=conversation.id, role='assistant', content='第一条')
        second = Message(character_id='assistant', conversation_id=conversation.id, role='assistant', content='第二条')
        db.add_all([first, second])
        db.commit()
        ids = (first.id, second.id)
        save_message_audio(db, first, [(wav_chunk(), 0, 0)], 'indextts-2.5-expressive')
        save_message_audio(db, second, [(wav_chunk(22050), 0, 0)], 'indextts-2.5-expressive')
    try:
        with TestClient(app) as client:
            for message_id in ids:
                assert client.get(f'/api/voice/messages/{message_id}').json()['status'] == 'ready'
                response = client.get(f'/api/voice/messages/{message_id}/audio')
                assert response.status_code == 200 and response.content[:4] == b'RIFF'
            assert client.get(f'/api/voice/messages/{ids[0]}/audio').content != client.get(f'/api/voice/messages/{ids[1]}/audio').content
        (tmp_path / 'audio' / f'{ids[0]}.wav').unlink()
        with TestClient(app) as client:
            assert client.get(f'/api/voice/messages/{ids[0]}').json()['status'] == 'missing'
            assert client.get(f'/api/voice/messages/{ids[1]}').json()['status'] == 'ready'
            assert client.get('/api/voice/cache').json()['files'] == 1
            assert client.delete('/api/voice/cache').json()['removed'] == 1
            assert client.get(f'/api/voice/messages/{ids[1]}').json()['status'] == 'missing'
            assert (tmp_path / 'test.db').is_file()
    finally:
        engine.dispose()


def test_twenty_replies_have_distinct_persistent_voice_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(message_cache, 'settings', SimpleNamespace(data_root=tmp_path))
    engine = create_engine(f"sqlite:///{(tmp_path / 'many.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    ids = []
    with factory() as db:
        for index in range(20):
            message = Message(character_id='assistant', role='assistant', content=f'第 {index} 条不同情绪的回复。')
            db.add(message)
            db.commit()
            ids.append(message.id)
            save_message_audio(db, message, [(wav_chunk(), 0, 0)], 'mock-indextts')
    with factory() as db:
        from app.models import MessageVoice
        assert db.query(MessageVoice).count() == 20
        assert len(set(ids)) == 20
        assert all(message_cache.audio_file(db.get(MessageVoice, item)) for item in ids)
    engine.dispose()


def test_fish_cache_is_separate_and_records_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(message_cache, 'settings', SimpleNamespace(data_root=tmp_path))
    engine = create_engine(f"sqlite:///{(tmp_path / 'fish.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        message = Message(character_id='assistant', role='assistant', content='你好呀')
        db.add(message)
        db.commit()
        row = save_message_audio(db, message, [(wav_chunk(), 0, 0)], 'fish-s2.1-pro-free',
                                 reference_id='voice_123')
        assert row.audio_path.startswith('voice_cache/fish/')
        assert row.audio_path.endswith('.wav')
        assert row.format == 'wav'
        assert row.provider == 'fish'
        assert row.model == 's2.1-pro-free'
        assert row.reference_id == 'voice_123'
        assert row.created_at is not None
        assert row.duration_seconds > 0
        assert message_cache.audio_file(row).is_file()
    engine.dispose()

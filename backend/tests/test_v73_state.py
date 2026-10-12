import json
import sqlite3
from pathlib import Path
import pytest
from sqlalchemy import create_engine
from app.database.migrations import migrate
from app.services.trusted_actions import TrustedActionStore, action_for, risk_for
from app.services.voice.pcm_stream import PcmStreamDecoder
from io import BytesIO
import wave

def test_legacy_grants_remain_stored_and_high_risk_cannot_be_trusted(tmp_path, monkeypatch):
    from app.services import permission_policy
    monkeypatch.setattr(permission_policy, 'read_preferences', lambda: {'permission_mode': 'smart'})
    store=TrustedActionStore(tmp_path/'grants.json')
    assert store.allowed('app.launch:vscode')
    store.set('app.launch:vscode','revoke')
    assert store.allowed('app.launch:vscode')  # New policy replaces legacy per-app grants.
    store.set('app.launch:vscode','always')
    assert TrustedActionStore(store.path).allowed('app.launch:vscode')
    monkeypatch.setattr(permission_policy, 'read_preferences', lambda: {'permission_mode': 'ask'})
    store.set('code.edit_file:demo','task','work-one')
    assert store.allowed('code.edit_file:demo','work-one')
    assert not store.allowed('code.edit_file:demo','work-two')
    for action in ['filesystem.delete:demo','app.close','system.admin','code.run:demo']:
        assert not store.allowed(action)
        with pytest.raises(ValueError):store.set(action,'always')
    store.set('app.launch:vscode','deny')
    assert not store.allowed('app.launch:vscode')
    store.path.write_text('corrupt')
    assert not store.allowed('app.launch:netease')

def test_old_work_migrates_by_link_not_ui_mode(tmp_path):
    target=tmp_path/'old.db'
    with sqlite3.connect(target) as db:
        db.execute('CREATE TABLE conversations (id TEXT PRIMARY KEY,character_id TEXT,title TEXT,preferred_mode TEXT,parent_conversation_id TEXT,fork_message_id TEXT,is_deleted INTEGER,created_at TEXT,updated_at TEXT)')
        db.executemany("INSERT INTO conversations VALUES (?,'assistant','old',?,NULL,NULL,0,'2026-01-01','2026-01-01')",[('work1','chat'),('chat1','work')])
        db.execute('ALTER TABLE conversations ADD COLUMN project_id TEXT')
        db.execute("INSERT INTO conversations VALUES ('project1','assistant','project','chat',NULL,NULL,0,'2026-01-01','2026-01-01','p1')")
        db.execute('CREATE TABLE work_sessions(id TEXT PRIMARY KEY,conversation_id TEXT,goal TEXT,status TEXT,created_at TEXT,updated_at TEXT)')
        db.execute("INSERT INTO work_sessions VALUES ('s1','work1','task','done','2026-01-01','2026-01-01')")
    engine=create_engine(f'sqlite:///{target.as_posix()}');migrate(engine);migrate(engine)
    with sqlite3.connect(target) as db:
        assert dict(db.execute('SELECT id,conversation_type FROM conversations'))=={'work1':'work','chat1':'chat','project1':'project'}
        assert db.execute("SELECT work_session_id FROM conversations WHERE id='work1'").fetchone()==('s1',)
    engine.dispose()

def test_pcm_header_can_be_split_at_every_byte():
    output=BytesIO()
    with wave.open(output,'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(44100);f.writeframes(b'\x01\x00'*4410)
    raw=output.getvalue();decoder=PcmStreamDecoder()
    pcm=b''.join(decoder.feed(raw[i:i+1]) for i in range(len(raw)))
    assert pcm==b'\x01\x00'*4410 and decoder.rate==44100

def test_risk_uses_native_action_not_model_labels():
    assert risk_for(action_for({'name':'app.close','arguments':{'app':'vscode'},'risk_level':'low'}))=='high'
    assert risk_for('app.launch:unknown-exe')=='critical'
    bulk={'name':'code.edit_file','arguments':{'path':'test.txt'},'bulk_overwrite':True}
    assert risk_for(action_for(bulk,Path('F:/workspace')))=='high'


def test_flash_streaming_mode_does_not_change_other_models():
    from app.services.llm.openai_provider import OpenAIProvider
    p=OpenAIProvider('test','deepseek-flash','https://api.deepseek.com/v1')
    assert p._payload([],stream=True)['thinking']=={'type':'disabled'}
    assert 'thinking' not in p._payload([])
    local=OpenAIProvider('','deepseek-flash','http://127.0.0.1:11434/v1')
    assert 'thinking' not in local._payload([],stream=True)


def test_fish_verification_cap_counts_transport_fallback(tmp_path,monkeypatch):
    from app.services.voice.fish_provider import _record_test_request,FishAudioError
    counter=tmp_path/'counter.txt';counter.write_text('9')
    monkeypatch.setenv('COMPANION_TEST_FISH_COUNTER',str(counter))
    monkeypatch.setenv('COMPANION_TEST_FISH_LIMIT','10')
    _record_test_request('websocket')
    with pytest.raises(FishAudioError):_record_test_request('http')
    assert counter.read_text()=='10'

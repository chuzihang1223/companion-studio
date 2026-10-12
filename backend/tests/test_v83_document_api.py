"""Authenticated saves and retained drafts, with no model or desktop requests."""
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import pytest
from app.database.migrations import migrate
from app.models import WorkSession
from app.services.coding import documents
from app.api import virtual_documents as api


@pytest.fixture
def fixture(tmp_path,monkeypatch):
    engine=create_engine('sqlite:///'+str(tmp_path/'test.db'),connect_args={'check_same_thread':False})
    migrate(engine);factory=sessionmaker(bind=engine,expire_on_commit=False)
    monkeypatch.setattr(documents,'SessionLocal',factory)
    monkeypatch.setenv('COMPANION_CONTROL_TOKEN','fixture-control-only')
    root=tmp_path/'Project';root.mkdir()
    with factory() as db:
        db.add_all([WorkSession(id='one',conversation_id='a',goal='draft',workspace_root=str(root)),
                    WorkSession(id='two',conversation_id='b',goal='other',workspace_root=str(root))])
        db.commit()
    doc=documents.VirtualDocumentStore().put('one','answer.py','print(42)\n')
    app=FastAPI();app.include_router(api.router)
    app.state.work_state=SimpleNamespace(snapshot=lambda _: {'workspace_root':str(root)},module=lambda *args:None)
    monkeypatch.setattr(api.PermissionPolicyEngine,'decide',lambda *args,**kwargs:'ask')
    return TestClient(app),root,doc['document_id']


def test_private_documents_and_explicit_save_policy(fixture):
    client,root,id=fixture;route='/api/work/one/documents/'+id
    assert client.get(route).status_code==403
    assert client.post(route+'/apply',json={'path':'answer.py','approved':True}).status_code==403
    headers={'X-Companion-Control-Token':'fixture-control-only'}
    assert client.get('/api/work/two/documents/'+id,headers=headers).status_code==404
    assert client.post(route+'/apply',headers=headers,json={'path':'answer.py'}).status_code==409
    assert not (root/'answer.py').exists()
    assert client.post(route+'/apply',headers=headers,json={'path':'../escape.py','approved':True}).status_code==409
    response=client.post(route+'/apply',headers=headers,json={'path':'answer.py','approved':True})
    assert response.status_code==200 and response.json()['status']=='applied'
    assert (root/'answer.py').read_text()=='print(42)\n'


def test_discard_keeps_document_and_removes_active_proposal(fixture):
    client,root,id=fixture;route='/api/work/one/documents/'+id
    headers={'X-Companion-Control-Token':'fixture-control-only'}
    assert client.post(route+'/discard',headers=headers,json={}).status_code==200
    result=client.get(route,headers=headers).json()
    assert result['status']=='discarded' and result['content']=='print(42)\n'
    assert documents.VirtualDocumentStore().active('one')==[]
    assert not (root/'answer.py').exists()

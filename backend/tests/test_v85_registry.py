import asyncio
from sqlalchemy import create_engine,select
from sqlalchemy.orm import sessionmaker
from app.database.session import Base
from app.models.runtime_task import RuntimeTask
from app.services import runtime_registry

def test_registry_parallel_owners_and_cancellation(tmp_path,monkeypatch):
    engine=create_engine('sqlite:///'+str(tmp_path/'runtime.db'))
    Base.metadata.create_all(engine)
    factory=sessionmaker(bind=engine,expire_on_commit=False)
    monkeypatch.setattr(runtime_registry,'SessionLocal',factory)
    async def scenario():
        registry=runtime_registry.RuntimeTaskRegistry()
        started,release=asyncio.Event(),asyncio.Event()
        async def long():
            async with registry.run('a','conversation-a','session-a','renderer-1'):
                started.set();await release.wait()
        task=asyncio.create_task(long());await started.wait()
        async with registry.run('b','conversation-b','session-b','renderer-1'):
            assert not task.done()
            assert registry.envelope('session-b')['task_id']=='b'
        registry.cancel_session('session-a')
        await asyncio.gather(task,return_exceptions=True)
        assert not registry.active and not registry.by_session
        with factory() as db:
            assert db.get(RuntimeTask,'a').status=='cancelled'
            assert db.get(RuntimeTask,'b').status=='completed'
    asyncio.run(scenario())

def test_restart_marks_unfinished_and_never_replays(tmp_path,monkeypatch):
    engine=create_engine('sqlite:///'+str(tmp_path/'restart.db'))
    Base.metadata.create_all(engine);factory=sessionmaker(bind=engine,expire_on_commit=False)
    monkeypatch.setattr(runtime_registry,'SessionLocal',factory)
    with factory() as db:db.add(RuntimeTask(id='unfinished',status='executing'));db.commit()
    registry=runtime_registry.RuntimeTaskRegistry()
    with factory() as db:assert db.get(RuntimeTask,'unfinished').status=='interrupted'
    assert not registry.active

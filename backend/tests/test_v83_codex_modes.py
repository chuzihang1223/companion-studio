"""Codex contract using a real local fixture process; no account quota is used."""
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database.migrations import migrate
from app.models import WorkSession
from app.services.coding import codex,documents,workspace
from app.services.coding.workspace import CodingWorkspaceExecutor
from app.services.desktop.lifecycle import TaskHandle


@pytest.mark.parametrize('mode',['analyze_only','draft','apply'])
def test_modes_keep_draft_outside_project(tmp_path,monkeypatch,mode):
    root=tmp_path/'Project';root.mkdir();original='print("original")\n'
    (root/'main.py').write_text(original,encoding='utf-8')
    (root/'.env').write_text('PRIVATE_SENTINEL=hidden',encoding='utf-8')
    engine=create_engine('sqlite:///'+str(tmp_path/'state.db'),connect_args={'check_same_thread':False})
    migrate(engine);factory=sessionmaker(bind=engine,expire_on_commit=False)
    monkeypatch.setattr(documents,'SessionLocal',factory)
    monkeypatch.setattr(workspace,'settings',SimpleNamespace(data_root=tmp_path/'Data'))
    with factory() as db:
        db.add(WorkSession(id='work',conversation_id='conversation',goal='code',workspace_root=str(root)));db.commit()
    intent={'analyze_only':'preview_patch','draft':'virtual_only','apply':'write_workspace'}[mode]
    executor=CodingWorkspaceExecutor(root,intent,'task').bind('work')
    target=root if mode=='apply' else executor.prepare_sandbox()
    assert not (executor.sandbox/'.env').exists()
    fixture=tmp_path/'local_cli.py'
    fixture.write_text('''import json,sys
from pathlib import Path
args=sys.argv[1:];prompt=sys.stdin.read();root=Path(args[args.index('-C')+1])
if args[args.index('--sandbox')+1]!='read-only':
    (root/'main.py').write_text('print("draft")\\n',encoding='utf-8')
print(json.dumps({'type':'item.completed','item':{'type':'reasoning','text':'PRIVATE_REASONING'}}),flush=True)
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'Public findings\\n```python\\nprint(42)\\n```'}}),flush=True)
''',encoding='utf-8')
    calls=[];create=asyncio.create_subprocess_exec
    async def start(*args,**kwargs):
        calls.append(args)
        return await create(sys.executable,str(fixture),*args[1:],**kwargs)
    monkeypatch.setattr(codex,'find_codex',lambda:sys.executable)
    monkeypatch.setattr(codex.asyncio,'create_subprocess_exec',start)
    events=[]
    provider=codex.CodexProvider(target,TaskHandle(),lambda *event:events.append(event),
                                execution_mode=mode,draft_executor=executor)
    result=asyncio.run(provider.create_task('Propose a useful change'))
    assert result['verified'] and result['execution_mode']==mode
    assert calls[0][calls[0].index('-C')+1]==str(target)
    assert calls[0][calls[0].index('--sandbox')+1]==('read-only' if mode=='analyze_only' else 'workspace-write')
    assert 'PRIVATE_REASONING' not in str(events)
    if mode=='apply':
        assert (root/'main.py').read_text()=='print("draft")\n' and result['changed_files']==['main.py']
    else:
        assert (root/'main.py').read_text()==original and result['changed_files']==[]
        assert any(event[0]=='code' and event[2]['virtual'] for event in events)

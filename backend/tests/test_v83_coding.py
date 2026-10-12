import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database.migrations import migrate
from app.models import WorkSession
from app.services.coding import documents, workspace
from app.services.coding.write_intent import CodingWriteIntentResolver
from app.services.coding.workspace import CodingWorkspaceExecutor
from app.services.coding.service import InternalCodingProvider
from app.services.desktop.lifecycle import TaskHandle


@pytest.mark.parametrize('text,expected', [
    ('帮我写一个新的排序算法。','virtual_only'), ('帮我把这个函数改一下。','virtual_only'),
    ('看看main.py怎么优化。','preview_patch'), ('看看项目，不要改。','preview_patch'),
    ('直接修改项目，但是不要改文件。','preview_patch'),
    ('把刚才的修改直接写进main.py。','write_workspace'),
    ('保存到项目。','write_workspace'), ('应用这个Patch。','write_workspace'),
    ('修复这个项目的Bug，直接修改代码并运行测试。','write_workspace'),
    ('修复这个项目里的Bug并运行测试。','write_workspace'),
])
def test_user_intent(text,expected):
    assert CodingWriteIntentResolver().resolve(text)==expected


@pytest.fixture
def coding(tmp_path,monkeypatch):
    engine=create_engine('sqlite:///'+str(tmp_path/'test.db'),connect_args={'check_same_thread':False})
    migrate(engine);factory=sessionmaker(bind=engine,expire_on_commit=False)
    monkeypatch.setattr(documents,'SessionLocal',factory)
    monkeypatch.setattr(workspace,'settings',SimpleNamespace(data_root=tmp_path/'data'))
    root=tmp_path/'Project';root.mkdir();(root/'main.py').write_text("print('original')\n",encoding='utf-8')
    with factory() as db:
        db.add(WorkSession(id='session',conversation_id='conversation',goal='draft',workspace_root=str(root)))
        db.commit()
    return root,factory


def test_default_and_preview_never_write_selected_root(coding):
    root,_=coding;original=(root/'main.py').read_bytes()
    executor=CodingWorkspaceExecutor(root,'virtual_only','task').bind('session')
    result=executor.execute({'name':'code.edit_file','arguments':{'path':'main.py','content':"print('draft')\n"}})
    assert result['virtual'] and result['dirty']
    assert (root/'main.py').read_bytes()==original
    assert list(root.iterdir())==[root/'main.py']
    assert executor.execute({'name':'code.read_file','arguments':{'path':'main.py'}})['content']=="print('draft')\n"
    assert documents.VirtualDocumentStore().get('session',result['document_id'])['source']=='derived_from_file'
    with pytest.raises(ValueError):
        executor.execute({'name':'workspace.delete','arguments':{'path':'main.py'}})


def test_sandbox_execution_and_explicit_apply(coding):
    root,_=coding;original=(root/'main.py').read_bytes()
    executor=CodingWorkspaceExecutor(root,'preview_patch','task').bind('session')
    result=executor.execute({'name':'code.edit_file','arguments':{'path':'main.py','content':"print('draft verified')\n"}})
    output=executor.execute({'name':'code.run','arguments':{'path':'main.py'}})
    assert output['exit_code']==0 and 'draft verified' in output['output'] and output['sandbox']
    assert (root/'main.py').read_bytes()==original
    saved=documents.VirtualDocumentStore().apply('session',result['document_id'],root,'main.py')
    assert saved['status']=='applied' and not saved['dirty']
    assert (root/'main.py').read_text()=="print('draft verified')\n"
    assert (root/'main.py.agent-backup').read_bytes()==original


def test_conflict_and_path_escape_do_not_overwrite(coding):
    root,_=coding
    executor=CodingWorkspaceExecutor(root,'virtual_only','task').bind('session')
    result=executor.execute({'name':'code.edit_file','arguments':{'path':'main.py','content':'proposed'}})
    (root/'main.py').write_text('user edit')
    with pytest.raises(ValueError,match='变化'):
        documents.VirtualDocumentStore().apply('session',result['document_id'],root,'main.py')
    assert (root/'main.py').read_text()=='user edit'
    with pytest.raises(ValueError):
        executor.execute({'name':'code.create_file','arguments':{'path':'../escape.py','content':'x'}})
    with pytest.raises(LookupError):
        documents.VirtualDocumentStore().get('other-session',result['document_id'])


def test_sandbox_tests_can_import_project_modules(coding):
    root,_=coding
    (root/'main.py').write_text('def answer():\n    return 42\n',encoding='utf-8')
    (root/'test_answer.py').write_text('from main import answer\nassert answer()==42\nprint("PASSED")\n',encoding='utf-8')
    executor=CodingWorkspaceExecutor(root,'virtual_only','imports').bind('session')
    result=executor.run_script('test_answer.py')
    assert result['exit_code']==0 and 'PASSED' in result['output'] and result['sandbox']
    assert not (root/'__pycache__').exists()


def test_model_goal_cannot_upgrade_draft_to_local_write(coding):
    root,_=coding;before=hashlib.sha256((root/'main.py').read_bytes()).hexdigest()
    class Model:
        async def plan_tools(self,messages,tools):
            return {'role':'assistant','tool_calls':[{'id':'edit','function':{'name':'code_edit_file',
                'arguments':'{"path":"main.py","content":"print(42)"}'}}]}
    context=SimpleNamespace(executor=CodingWorkspaceExecutor(root,'virtual_only','task').bind('session'),
            user_text='给我写一段代码',coding_mode='virtual_only',work_session_id='session')
    app=SimpleNamespace(state=SimpleNamespace(llm_service=Model()))
    events=[]
    result=asyncio.run(InternalCodingProvider(app,context,TaskHandle(),lambda *row:events.append(row)).create_task('直接覆盖本地文件'))
    assert result['verified'] and result['execution_mode']=='draft' and result['changed_files']==[]
    assert events[0][2]['virtual']
    assert hashlib.sha256((root/'main.py').read_bytes()).hexdigest()==before

"""Only Electron's authenticated control channel can save drafts to a project."""
from pathlib import Path
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field
from app.api.llm_settings import require_control
from app.services.coding.documents import VirtualDocumentStore
from app.services.permission_policy import PermissionPolicyEngine
from app.services.workspace_service import validate_root

router = APIRouter(prefix='/api/work', tags=['virtual-documents'])
store = VirtualDocumentStore()


def failure(exc):
    return HTTPException(404 if isinstance(exc, LookupError) else 409, str(exc))


@router.get('/{session_id}/documents/{document_id}')
def get_document(session_id: str, document_id: str, x_companion_control_token: str | None = Header(default=None)):
    require_control(x_companion_control_token)
    try:
        return store.get(session_id, document_id)
    except (LookupError, ValueError) as exc:
        raise failure(exc) from exc


class DraftEdit(BaseModel):
    content: str = Field(max_length=100_000)


@router.patch('/{session_id}/documents/{document_id}')
def edit_document(request: Request, session_id: str, document_id: str, payload: DraftEdit,
                  x_companion_control_token: str | None = Header(default=None)):
    require_control(x_companion_control_token)
    try:
        document = store.get(session_id, document_id)
        result = store.put(session_id, document['title'], payload.content, source='agent_modified',
                           related_file=document['related_file'], base_hash=document['base_hash'], previous=document['content'])
        request.app.state.work_state.module(session_id, 'code', document['title'], result)
        return result
    except (LookupError, ValueError) as exc:
        raise failure(exc) from exc


class DraftApply(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    destination_root: str | None = Field(default=None, max_length=500)
    overwrite: bool = False
    approved: bool = False


@router.post('/{session_id}/documents/{document_id}/apply')
def apply_document(request: Request, session_id: str, document_id: str, payload: DraftApply,
                   x_companion_control_token: str | None = Header(default=None)):
    require_control(x_companion_control_token)
    try:
        session = request.app.state.work_state.snapshot(session_id)
        document = store.get(session_id, document_id)
        previous = next((row['data'] for row in session.get('tabs', [])
                         if row['data'].get('document_id') == document_id and row['kind'] == 'code'), {})
        root = validate_root(payload.destination_root or session['workspace_root'] or '')
        action = 'workspace.write:'+root
        decision = PermissionPolicyEngine().decide(action, 'medium')
        if decision == 'deny' or (decision == 'ask' and not payload.approved):
            raise ValueError('当前权限策略需要确认本次文件保存')
        result = store.apply(session_id, document_id, Path(root), payload.path, overwrite=payload.overwrite)
        request.app.state.work_state.module(session_id, 'code', document['title'],
                {**previous, **result, 'diff': previous.get('diff', result.get('diff', '')), 'virtual': True})
        return result
    except (LookupError, ValueError, OSError) as exc:
        raise failure(exc) from exc


@router.post('/{session_id}/documents/{document_id}/discard')
def discard_document(request: Request, session_id: str, document_id: str,
                     x_companion_control_token: str | None = Header(default=None)):
    require_control(x_companion_control_token)
    try:
        document = store.get(session_id, document_id)
        store.discard(session_id, document_id)
        request.app.state.work_state.module(session_id, 'code', document['title'],
                {'document_id': document_id, 'path': document['title'], 'virtual': True, 'status': 'discarded'})
        return {'status': 'discarded'}
    except (LookupError, ValueError) as exc:
        raise failure(exc) from exc

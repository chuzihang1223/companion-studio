from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from typing import Literal
from app.api.llm_settings import require_control
from app.services.trusted_actions import trusted_actions, risk_for
from app.services.permission_policy import PermissionPolicyEngine
from app.services.desktop.preferences import read_preferences

router = APIRouter(prefix='/api/permissions', tags=['permissions'])

class Grant(BaseModel):
    action: str = Field(min_length=1, max_length=600)
    scope: Literal['always', 'task', 'deny', 'revoke']
    task_id: str | None = Field(default=None, max_length=100)

@router.get('')
def list_grants(x_companion_control_token: str | None = Header(default=None)):
    require_control(x_companion_control_token)
    return trusted_actions.list()

@router.post('')
def change_grant(payload: Grant, x_companion_control_token: str | None = Header(default=None)):
    require_control(x_companion_control_token)
    try:
        trusted_actions.set(payload.action, payload.scope, payload.task_id)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    return {'ok': True}

@router.get('/check')
def check(action: str, task_id: str | None = None, x_companion_control_token: str | None = Header(default=None)):
    require_control(x_companion_control_token)
    decision = trusted_actions.decision(action, task_id)
    risk = risk_for(action)
    return {'allowed': decision == 'allow', 'risk': risk,
            'decision': decision,
            'mode': read_preferences()['permission_mode']}

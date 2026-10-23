"""Desktop-main-only registry/policy updates using the original settings control boundary."""

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.routing import APIRoute
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from app.api.llm_settings import require_control
from app.services.llm.configuration import LLMConfig
from app.services.llm.routing.contracts import ModelRecord, RoutingPolicy, RoutingError
from app.services.llm.service import create_llm_service


class PrivateRoutingRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def private_handler(request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, detail={"code": "ROUTING_ARGUMENT_INVALID"}) from None

        return private_handler


router = APIRouter(
    route_class=PrivateRoutingRoute, prefix="/api/llm/settings/routing", tags=["settings"]
)


class Registration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    record: ModelRecord
    config: LLMConfig


class RouterConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    models: list[Registration] = Field(default_factory=list, max_length=32)
    policy: RoutingPolicy = Field(default_factory=RoutingPolicy)
    roles: dict[str, str] = Field(default_factory=dict, max_length=4)


@router.post("")
def configure(
    payload: RouterConfiguration,
    request: Request,
    x_companion_control_token: str | None = Header(default=None),
):
    require_control(x_companion_control_token)
    try:
        request.app.state.model_roles.configure_routing(
            [(row.record, create_llm_service(row.config)) for row in payload.models],
            payload.policy,
            payload.roles,
        )
    except RoutingError as error:
        raise HTTPException(422, error.code) from error
    return request.app.state.model_roles.routing_status()


@router.post("/status")
def status(request: Request, x_companion_control_token: str | None = Header(default=None)):
    require_control(x_companion_control_token)
    return request.app.state.model_roles.routing_status()

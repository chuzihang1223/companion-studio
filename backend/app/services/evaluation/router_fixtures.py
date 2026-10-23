"""Original synthetic HTTP fixtures; no real credentials, endpoints or prices."""

import json
import httpx
from app.services.llm.configuration import LLMConfig
from app.services.llm.openai_provider import OpenAIProvider
from app.services.llm.service import LLMService
from app.services.llm.routing.contracts import ModelRecord, RoutingPolicy
from app.services.llm.routing.metrics import ModelMetrics
from app.services.llm.routing.router import make_snapshot


class RouterFixture:
    def __init__(self, app, ledger):
        self.app, self.ledger = app, ledger
        self.models, self.calls = [], {}
        self.observation = {}
        self.policy = RoutingPolicy(enabled=True, mode="AUTO")
        self.metrics = ModelMetrics()

    def add(
        self,
        name="one",
        *,
        caps=None,
        responder=None,
        endpoint="http://127.0.0.1:9/v1",
        api_model="synthetic",
        **metadata,
    ):
        config = LLMConfig(
            base_url=endpoint,
            api_key="",
            model=api_model,
            capabilities=caps
            if caps is not None
            else {"text": True, "tool_calling": True, "streaming": True},
        )
        self.calls[name] = []

        def respond(request):
            body = json.loads(request.content)
            self.calls[name].append(body)
            if responder:
                return responder(request)
            message = {"role": "assistant", "content": "Synthetic router reply"}
            if body.get("tools"):
                message["tool_calls"] = []
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": message}],
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 4,
                        "total_tokens": 7,
                        "prompt_tokens_details": {"cached_tokens": 1},
                        "completion_tokens_details": {"reasoning_tokens": 2},
                    },
                },
            )

        service = LLMService(
            OpenAIProvider("", api_model, endpoint, transport=httpx.MockTransport(respond)), config
        )
        record = ModelRecord(
            model_id=name, display_name=name, context_window=65536, allow_auto=True, **metadata
        )
        self.models.append((record, service))
        return record, service

    def state(self, **patch):
        policy = self.policy.model_copy(update=patch)
        state = make_snapshot(
            self.models,
            policy,
            {
                role: self.models[0][0].model_id
                for role in (
                    "character_model",
                    "planning_model",
                    "tool_execution_model",
                    "coding_model",
                )
            },
            self.metrics,
            self.ledger,
        )
        self.app.state.model_roles.intelligent = state
        return state


def blocked(function):
    from app.services.llm.routing.contracts import RoutingError

    try:
        function()
    except RoutingError:
        return True
    return False


async def blocked_async(function):
    from app.services.llm.routing.contracts import RoutingError

    try:
        await function()
    except RoutingError:
        return True
    return False

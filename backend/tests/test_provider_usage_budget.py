"""Unknown previous usage must stop before HTTP and retain its budget reason."""

import asyncio

import httpx
import pytest

from app.services.llm.openai_provider import OpenAIProvider
from app.services.llm.usage import EvaluationBudgetExceeded, UsageBudget, usage_scope


@pytest.mark.parametrize("stream", [False, True])
def test_unknown_usage_budget_preserves_reason_without_another_request(stream):
    calls = []

    def dispatch(request):
        calls.append(request)
        raise AssertionError("Budget exhaustion must stop before HTTP dispatch")

    async def run():
        provider = OpenAIProvider(
            "", "synthetic", "http://127.0.0.1:9/v1", transport=httpx.MockTransport(dispatch)
        )
        budget = UsageBudget(2, 8192)
        budget.before({})  # a prior request was sent, but its usage is unknown
        with usage_scope(budget), pytest.raises(EvaluationBudgetExceeded):
            if stream:
                async for _ in provider.stream_chat([{"role": "user", "content": "owned test"}]):
                    pass
            else:
                await provider.chat([{"role": "user", "content": "owned test"}])
        assert budget.requests == 1 and not calls

    asyncio.run(run())

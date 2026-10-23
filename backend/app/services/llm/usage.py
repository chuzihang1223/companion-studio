"""请求局部 Usage 观察与硬请求预算；不按字符串长度计算 Token。

Token 预算限制 completion 上限，输入 Token 在 API 返回前不可知；因此不能
保证总 Token 不超预算。每次请求前保留 max_tokens，未知 usage 时停止后续请求。
"""

from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

_budget = ContextVar("llm_usage_budget", default=None)
_request_id = ContextVar("llm_usage_request", default=None)


class EvaluationBudgetExceeded(ValueError):
    """本次测试预算耗尽；区别于模型或程序故障。"""


class UsageBudget:
    def __init__(self, max_requests, token_budget, request_tokens=2048):
        self.max_requests, self.token_budget = max_requests, token_budget
        self.request_tokens = request_tokens
        self.requests = 0
        self.usages = []
        self._usage_indexes = {}
        self.reserved = 0

    def before(self, body):
        if self.requests >= self.max_requests or self.requests > len(self.usages):
            raise EvaluationBudgetExceeded("EVALUATION_REQUEST_BUDGET")
        remaining = self.token_budget - self.reserved
        if remaining <= 0:
            raise EvaluationBudgetExceeded("EVALUATION_TOKEN_BUDGET")
        body["max_tokens"] = min(
            body.get("max_tokens") or self.request_tokens, self.request_tokens, remaining
        )
        self.reserved += body["max_tokens"]
        self.requests += 1

    def observe(self, data, request_id=None):
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return
        keys = ("prompt_tokens", "completion_tokens", "total_tokens")
        if all(
            isinstance(usage.get(k), int)
            and not isinstance(usage[k], bool)
            and 0 <= usage[k] < 10**8
            for k in keys
        ):
            identity = request_id or self.requests
            observed = {key: usage[key] for key in keys}
            if identity in self._usage_indexes:
                index = self._usage_indexes[identity]
                if observed["total_tokens"] >= self.usages[index]["total_tokens"]:
                    self.usages[index] = observed
            else:
                self._usage_indexes[identity] = len(self.usages)
                self.usages.append(observed)
            # Charge actual input/output usage once available, including input.
            self.reserved = max(self.reserved, sum(row["total_tokens"] for row in self.usages))

    def public(self):
        return {
            "status": "Observed"
            if self.requests == len(self.usages) and self.requests
            else "Unavailable",
            "requests": self.requests,
            "observed_requests": len(self.usages),
            "total_tokens": sum(row["total_tokens"] for row in self.usages)
            if self.usages
            else None,
        }


@contextmanager
def usage_scope(budget):
    token = _budget.set(budget)
    try:
        yield
    finally:
        _budget.reset(token)


def before_request(body, request_id=None):
    from app.services.llm.routing.observation import before

    _request_id.set(request_id or uuid4().hex)
    before(body)
    budget = _budget.get()
    if budget:
        budget.before(body)
    return body


def observe_response(data, request_id=None):
    from app.services.llm.routing.observation import observe

    observe(data)
    budget = _budget.get()
    if budget:
        budget.observe(data, request_id or _request_id.get())


def current_usage_budget():
    return _budget.get()

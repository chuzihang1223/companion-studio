"""Request-local token observation; actual, estimated, missing are distinct."""

from contextlib import contextmanager
from contextvars import ContextVar

_observer = ContextVar("routing_request_observer", default=None)


class RequestObservation:
    def __init__(self, output_limit):
        self.output_limit, self.usage = output_limit, None
        self.http_status = None

    def before(self, body):
        body["max_tokens"] = min(body.get("max_tokens") or self.output_limit, self.output_limit)

    def observe(self, data):
        usage = data.get("usage")
        if not isinstance(usage, dict):
            return

        def number(value):
            return value if type(value) is int and 0 <= value < 100000000 else None

        prompt, completion, total = (
            number(usage.get(key)) for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        )
        if None in (prompt, completion, total) or total < prompt + completion:
            return
        prompt_details = usage.get("prompt_tokens_details") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        self.usage = {
            "status": "ACTUAL_PROVIDER_REPORTED",
            "input_tokens": prompt,
            "output_tokens": completion,
            "total_tokens": total,
            "cached_tokens": number(prompt_details.get("cached_tokens"))
            if isinstance(prompt_details, dict)
            else None,
            "reasoning_tokens": number(completion_details.get("reasoning_tokens"))
            if isinstance(completion_details, dict)
            else None,
        }


@contextmanager
def observe_request(observer):
    token = _observer.set(observer)
    try:
        yield
    finally:
        _observer.reset(token)


def before(body):
    observer = _observer.get()
    if observer:
        observer.before(body)


def observe(data):
    observer = _observer.get()
    if observer:
        observer.observe(data)


def transport(status):
    observer = _observer.get()
    if observer is not None and type(status) is int and 100 <= status <= 599:
        observer.http_status = status

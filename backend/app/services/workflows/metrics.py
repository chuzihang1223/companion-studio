"""Usage is observed from the original provider; no guessed token or currency prices."""


def combine_usage(previous, current):
    requests = previous.get("requests", 0) + current.get("requests", 0)
    observed = previous.get("observed_requests", 0) + current.get("observed_requests", 0)
    known = observed == requests and observed > 0
    return {
        "status": "Observed" if known else "Unavailable",
        "requests": requests,
        "observed_requests": observed,
        "total_tokens": ((previous.get("total_tokens") or 0) + (current.get("total_tokens") or 0))
        if known
        else None,
    }

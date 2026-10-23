"""Bounded, version-specific observations. No proactive model probes or prompt storage."""

from collections import OrderedDict, deque
from dataclasses import dataclass, field
from math import ceil, isfinite
from threading import RLock
import time


@dataclass
class Observations:
    failures: int = 0
    transport_successes: int = 0
    transport_failures: int = 0
    consecutive_failures: int = 0
    cooldown_until: float = 0
    successes: int = 0
    elapsed: deque = field(default_factory=lambda: deque(maxlen=64))
    ttft: deque = field(default_factory=lambda: deque(maxlen=64))
    tool_schema_valid: int = 0
    tool_schema_invalid: int = 0


def distribution(values):
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "p50_ms": ordered[ceil(len(ordered) * 0.5) - 1] if ordered else None,
        "p95_ms": ordered[ceil(len(ordered) * 0.95) - 1] if ordered else None,
    }


class ModelMetrics:
    def __init__(self, clock=time.monotonic):
        self.rows, self.lock, self.clock = OrderedDict(), RLock(), clock

    def _row(self, key):
        row = self.rows.setdefault(key, Observations())
        self.rows.move_to_end(key)
        while len(self.rows) > 256:
            self.rows.popitem(last=False)
        return row

    def record(
        self,
        key,
        *,
        success,
        elapsed_ms,
        ttft_ms=None,
        transient=False,
        tool_schema_valid=None,
        transport_success=None,
        task_kind=None,
    ):
        if task_kind is not None:
            self.record(
                (key, task_kind),
                success=success,
                elapsed_ms=elapsed_ms,
                ttft_ms=ttft_ms,
                transient=transient,
                tool_schema_valid=tool_schema_valid,
                transport_success=transport_success,
            )
        with self.lock:
            row = self._row(key)
            if transport_success is True:
                row.transport_successes += 1
            elif transport_success is False:
                row.transport_failures += 1
            if isfinite(elapsed_ms) and elapsed_ms >= 0:
                row.elapsed.append(round(elapsed_ms, 3))
            if ttft_ms is not None and isfinite(ttft_ms) and ttft_ms >= 0:
                row.ttft.append(round(ttft_ms, 3))
            if success:
                row.successes += 1
                row.consecutive_failures = 0
                row.cooldown_until = 0
            else:
                row.failures += 1
                if transient:
                    row.consecutive_failures += 1
                    row.cooldown_until = self.clock() + min(
                        60, 2 ** min(row.consecutive_failures - 1, 6)
                    )
            if tool_schema_valid is True:
                row.tool_schema_valid += 1
            elif tool_schema_valid is False:
                row.tool_schema_invalid += 1

    def public(self, key):
        with self.lock:
            row = self.rows.get(key)
            if row is None:
                return {
                    "health": "UNKNOWN",
                    "total": distribution([]),
                    "ttft": distribution([]),
                    "successes": 0,
                    "failures": 0,
                    "transport_successes": 0,
                    "transport_failures": 0,
                    "tool_schema_valid": 0,
                    "tool_schema_invalid": 0,
                    "business_goal_verified": None,
                    "retry_after_ms": 0,
                }
            remaining = max(0, row.cooldown_until - self.clock())
            return {
                "health": "UNAVAILABLE"
                if remaining
                else "DEGRADED"
                if row.consecutive_failures
                else "HEALTHY"
                if row.successes
                else "UNKNOWN",
                "total": distribution(row.elapsed),
                "ttft": distribution(row.ttft),
                "successes": row.successes,
                "transport_successes": row.transport_successes,
                "transport_failures": row.transport_failures,
                "failures": row.failures,
                "tool_schema_valid": row.tool_schema_valid,
                "tool_schema_invalid": row.tool_schema_invalid,
                "business_goal_verified": None,
                "retry_after_ms": round(remaining * 1000),
            }

    def by_task(self, key):
        with self.lock:
            names = sorted(
                row[1]
                for row in self.rows
                if isinstance(row, tuple) and len(row) == 2 and row[0] == key
            )
            return {name: self.public((key, name)) for name in names}

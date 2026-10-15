"""仅有效已评分项进入成功率分母；缺失 Usage/价格不写成零。"""

from collections import Counter
import math


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * q
    low, high = math.floor(index), math.ceil(index)
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


class MetricsCollector:
    @staticmethod
    def aggregate(results, mode):
        counts = Counter(row["completion_status"] for row in results)
        valid = [row for row in results if row["completion_status"] in {"PASS", "FAIL"}]
        times = [row["duration_ms"] for row in valid if row.get("duration_ms") is not None]
        recovering = [row for row in valid if row.get("retry_count", 0) > 0]
        usage = [row.get("model_usage", {}) for row in results]
        observed = [row for row in usage if row.get("status") == "Observed"]
        return {
            "source": "Mock Evaluation"
            if mode == "mock"
            else "Sandbox Evaluation"
            if mode == "sandbox"
            else mode,
            "counts": dict(counts),
            "valid_count": len(valid),
            "total_count": len(results),
            "success_rate": counts["PASS"] / len(valid) if valid else None,
            "failure_rate": counts["FAIL"] / len(valid) if valid else None,
            "real_task_success_rate": counts["PASS"] / len(valid)
            if valid and mode in {"live_model", "windows_gui"}
            else None,
            "average_duration_ms": sum(times) / len(times) if times else None,
            "p50_ms": percentile(times, 0.5),
            "p95_ms": percentile(times, 0.95),
            "average_tool_calls": sum(row.get("tool_calls", 0) for row in valid) / len(valid)
            if valid
            else None,
            "tool_calls": sum(row.get("tool_calls", 0) for row in results),
            "model_requests": sum(row.get("model_requests", 0) for row in results),
            "retry_count": sum(row.get("retry_count", 0) for row in results),
            "recovery_attempts": len(recovering),
            "recovery_success_rate": sum(bool(row.get("recovery_succeeded")) for row in recovering)
            / len(recovering)
            if recovering
            else None,
            "model_usage": {
                "status": "Not applicable (simulated)"
                if mode in {"mock", "sandbox"}
                else "Observed"
                if observed and len(observed) == len(usage)
                else "Unavailable",
                "observed_requests": sum(row.get("observed_requests", 0) for row in usage),
                "total_tokens": sum(row.get("total_tokens", 0) for row in observed)
                if observed
                else None,
            },
            "api_cost": {
                "status": "Not applicable (simulated)"
                if mode in {"mock", "sandbox"}
                else "Unavailable",
                "value": None,
                "currency": None,
            },
            "failure_categories": dict(
                Counter(row["error_code"] for row in results if row.get("error_code"))
            ),
            "excluded_statuses": {
                key: value for key, value in counts.items() if key not in {"PASS", "FAIL"}
            },
        }


def compare_conditions(left, right):
    """显示条件差异而不产生模型排名；即使同条件，小样本也不是结论。"""
    keys = (
        "dataset_id",
        "dataset_version",
        "dataset_sha256",
        "case_ids",
        "repetitions",
        "mode",
        "model",
        "app_version",
        "git_commit",
        "prompt_version",
        "tool_schema_sha256",
        "environment",
        "timeout_seconds",
        "concurrency",
    )
    differences = [
        {"field": key, "left": left.get(key), "right": right.get(key)}
        for key in keys
        if left.get(key) != right.get(key)
    ]
    return {
        "differences": differences,
        "conditions_equal": not differences,
        "dataset_compatible": all(
            left.get(k) == right.get(k)
            for k in ("dataset_id", "dataset_version", "dataset_sha256", "case_ids")
        ),
        "warning": "展示观察值；不同条件不能公平排名，单次/小样本不代表模型优劣。",
    }

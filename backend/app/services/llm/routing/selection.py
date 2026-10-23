"""Filter constraints before ranking. Scores never grant permissions or infer capabilities."""

from dataclasses import dataclass
from app.services.llm.routing.contracts import ModelType, RoutingError
from app.services.llm.routing.budget import estimate_micros


@dataclass(frozen=True)
class RoutingDecision:
    model_id: str
    config_version: str
    mode: str
    reason: str
    task: str
    eligible_ids: tuple[str, ...]
    rejected: tuple[tuple[str, str], ...]

    def public(self):
        return {
            "model_id": self.model_id,
            "config_version": self.config_version,
            "mode": self.mode,
            "reason": self.reason,
            "task": self.task,
            "eligible_ids": list(self.eligible_ids),
            "rejected": [{"model_id": key, "reason": reason} for key, reason in self.rejected],
        }


def eligibility(entry, requirements, policy, metrics):
    record = entry.record
    expected_type = {
        "EMBEDDING": ModelType.EMBEDDING,
        "ASR": ModelType.ASR,
        "TTS": ModelType.TTS,
    }.get(requirements.task)
    if not record.enabled:
        return "DISABLED"
    if expected_type:
        # This router delegates chat to LLMService; typed voice/embedding records
        # stay visible but cannot accidentally be sent to a text completion API.
        return "SEPARATE_TYPED_PROVIDER_REQUIRED"
    if record.model_type not in {ModelType.TEXT_LLM, ModelType.VISION_LLM}:
        return "MODEL_TYPE_MISMATCH"
    if any(entry.capability(name) is not True for name in requirements.required):
        return "CAPABILITY_UNCONFIRMED"
    if (
        record.context_window is None
        or record.context_window < requirements.min_context + policy.max_output_tokens
    ):
        return "CONTEXT_UNCONFIRMED"
    if requirements.data_kind == "ATTACHMENT" and not entry.local and not record.allow_attachments:
        return "ATTACHMENT_TARGET_NOT_AUTHORIZED"
    if requirements.privacy == "LOCAL_ONLY" and not entry.local:
        return "LOCAL_ONLY"
    if policy.mode == "AUTO" and not record.allow_auto:
        return "AUTO_NOT_AUTHORIZED"
    if policy.mode == "AUTO" and not entry.local and not record.allow_remote_text:
        return "REMOTE_TEXT_NOT_AUTHORIZED"
    if metrics.public(entry.version)["health"] == "UNAVAILABLE":
        return "COOLDOWN"
    if requirements.max_latency_ms is not None:
        latency = metrics.public(entry.version)["total"]["p95_ms"]
        if latency is None or latency > requirements.max_latency_ms:
            return "LATENCY_UNCONFIRMED"
    caps = [
        value
        for name, value in policy.model_dump().items()
        if name.startswith("max_cost_") and value is not None
    ]
    estimate = estimate_micros(entry, policy)
    if caps and policy.budget_mode == "STRICT":
        return "INPUT_TOKEN_BOUND_UNAVAILABLE"
    if caps and estimate is None:
        return "PRICE_OR_CONTEXT_UNKNOWN"
    if policy.max_cost_per_request is not None and estimate > policy.max_cost_per_request * 1000000:
        return "REQUEST_COST_LIMIT"
    return None


def select_model(registry, policy, requirements, metrics, role_model=None, pinned_target=None):
    if requirements.privacy == "PINNED":
        pinned = registry.by_id.get(requirements.pinned_model_id)
        if pinned is None:
            raise RoutingError("PINNED_MODEL_REQUIRED")
        pinned_target = pinned.target
    rejected, candidates = [], []
    for entry in registry.entries:
        reason = eligibility(entry, requirements, policy, metrics)
        if (
            not reason
            and requirements.privacy == "PINNED"
            and entry.record.model_id != requirements.pinned_model_id
        ):
            reason = "MODEL_ID_PINNED"
        if not reason and pinned_target and entry.target != pinned_target:
            reason = "TARGET_PINNED"
        if not reason and policy.mode == "MANUAL" and entry.record.model_id != policy.manual_model:
            reason = "MANUAL_SELECTION"
        if not reason and policy.mode == "ROLE_BASED" and entry.record.model_id != role_model:
            reason = "ROLE_SELECTION"
        if reason:
            rejected.append((entry.record.model_id, reason))
        else:
            candidates.append(entry)
    if not candidates:
        raise RoutingError("NO_ELIGIBLE_MODEL")

    def rank(entry):
        record = entry.record
        health = metrics.public(entry.version)
        latency = (
            health["ttft"]["p50_ms"] if requirements.task == "VOICE" else health["total"]["p50_ms"]
        )
        cost = estimate_micros(entry, policy)
        preferred = sum(entry.capability(name) is True for name in requirements.preferred)
        stable = (health["health"] == "DEGRADED", -preferred, -record.priority, record.model_id)
        if policy.preference == "LOW_COST":
            # Unknown is last, never a free candidate. Currencies are comparable
            # only to the explicitly selected policy currency.
            return (cost is None, cost if cost is not None else 0, *stable)
        if policy.preference == "LOW_LATENCY":
            return (latency is None, latency or 0, *stable)
        if policy.preference == "PRIVACY_FIRST":
            return (not entry.local, *stable)
        if policy.preference == "QUALITY_FIRST":
            # This is user priority + confirmed preferred capabilities, not an
            # invented model-quality benchmark or a model-name heuristic.
            return (-record.priority, -preferred, *stable)
        return stable

    candidates.sort(key=rank)
    selected = candidates[0]
    decision = RoutingDecision(
        selected.record.model_id,
        selected.version,
        policy.mode,
        "USER_SELECTION" if policy.mode != "AUTO" else "CONSTRAINTS_THEN_" + policy.preference,
        requirements.task,
        tuple(row.record.model_id for row in candidates),
        tuple(rejected),
    )
    return selected, decision

"""Opt-in extension of ModelRoleRouter; configuration replacement is atomic."""

from dataclasses import dataclass
from uuid import uuid4

from app.services.llm.routing.contracts import RoutingError, RoutingPolicy, TaskCapabilityResolver
from app.services.llm.routing.registry import ModelRegistry
from app.services.llm.routing.selection import select_model
from app.services.llm.routing.service import RoutedService
from app.services.trace.scope import current_trace

TASKS = {
    "character_model": "CHAT",
    "planning_model": "PLANNING",
    "tool_execution_model": "EXECUTION",
    "coding_model": "CODING",
}


@dataclass(frozen=True)
class RoutingSnapshot:
    registry: ModelRegistry
    policy: RoutingPolicy
    roles: dict
    metrics: object
    ledger: object
    revision: str

    def service(self, role, *, tools=False, requirements=None):
        requirements = requirements or TaskCapabilityResolver.resolve(TASKS[role], tools=tools)
        entry, decision = select_model(
            self.registry, self.policy, requirements, self.metrics, self.roles.get(role)
        )
        from app.services.llm.routing.trace import emit

        emit("MODEL_ROUTING_DECIDED", decision, entry)
        service = RoutedService(
            entry, decision, self.registry, self.policy, requirements, self.metrics, self.ledger
        )
        return service

    def public(self):
        return {
            "revision": self.revision,
            "policy": self.policy.model_dump(mode="json"),
            "roles": dict(self.roles),
            "models": [
                entry.public()
                | {
                    "metrics": self.metrics.public(entry.version),
                    "by_task": self.metrics.by_task(entry.version),
                }
                for entry in self.registry.entries
            ],
            "recent_requests": self.ledger.recent(),
            "limitations": [
                "USER_PRICE_ESTIMATES_ARE_NOT_INVOICES",
                "SAME_TARGET_FALLBACK_ONLY",
                "NO_AUTOMATIC_PROVIDER_PROBES",
            ],
        }


def make_snapshot(models, policy, roles, metrics, ledger):
    roles = {role: model_id for role, model_id in roles.items() if model_id}
    registry = ModelRegistry.build(models)
    if set(roles) - TASKS.keys() or set(roles.values()) - registry.by_id.keys():
        raise RoutingError("INVALID_ROLE_BINDING")
    if policy.enabled and not registry.entries:
        raise RoutingError("REGISTRY_EMPTY")
    if policy.enabled and policy.mode == "MANUAL" and policy.manual_model not in registry.by_id:
        raise RoutingError("MANUAL_MODEL_REQUIRED")
    return RoutingSnapshot(registry, policy, dict(roles), metrics, ledger, uuid4().hex)

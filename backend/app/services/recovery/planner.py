"""Deterministic local plans use fresh evidence and the existing tool schemas.

Unknown websites/forms, code processes and model requests are deliberately never
restarted. No model fallback is enabled without a separate consent/budget design.
"""

from dataclasses import dataclass, field
import json
import time
from urllib.parse import urlsplit

from app.services.agent_tools import ToolRegistry
from app.services.recovery.checkpoints import digest
from app.services.recovery.policy import RecoveryPolicyEngine
from app.services.recovery.types import RecoveryBudget, RecoveryDecision

KNOWN_SEARCH_HOSTS = {"github": "github.com", "baidu": "baidu.com", "bilibili": "bilibili.com"}


def validate_call(call):
    parsed = ToolRegistry().parse_calls(
        {
            "tool_calls": [
                {
                    "id": call["id"],
                    "function": {
                        "name": call["name"].replace(".", "_"),
                        "arguments": json.dumps(call["arguments"], ensure_ascii=False),
                    },
                }
            ]
        }
    )
    return {**call, "risk_level": parsed[0]["risk_level"]}


@dataclass
class RecoveryPlanner:
    budget: RecoveryBudget = field(default_factory=RecoveryBudget)
    started: float = field(default_factory=time.monotonic)
    cycles: int = 0
    steps: int = 0
    seen: set[str] = field(default_factory=set)

    def plan(self, failure, call, *, retry_count, observation=None, permission=True):
        """Original goal/plan stay in AgentContext; no private history is recopied.

        The selected strategy only changes observation/locator acquisition, never
        tool arguments, workspace, goal or risk. Every execution checks grants again.
        """
        validate_call(call)
        if (
            not permission
            or self.steps >= self.budget.max_total_steps
            or time.monotonic() - self.started >= self.budget.max_task_duration
        ):
            return RecoveryDecision(level=3, strategy="budget_or_permission", stop=True)
        decision = RecoveryPolicyEngine().decide(
            failure,
            call["name"],
            retry_count=retry_count,
            recovery_count=self.cycles,
            budget=self.budget,
        )
        observation = observation or {}
        if decision.strategy == "observe_page":
            host = KNOWN_SEARCH_HOSTS.get(call["arguments"].get("site"))
            actual = urlsplit(str(observation.get("url") or "")).hostname
            if (
                call["name"] == "browser.search_site"
                and host
                and actual
                and (actual == host or actual.endswith("." + host))
                and observation.get("verified") is True
                and observation.get("dom_fingerprint")
            ):
                # Known site search is implemented by a fixed GET URL, never a
                # user-supplied form. Reacquire results against the new DOM.
                decision = RecoveryDecision(
                    level=2, strategy="refresh_known_search", retry=True, observe=True
                )
        if (
            call["name"] == "app.focus"
            and failure.error_code == "VERIFICATION_FAILED"
            and decision.strategy != "budget_exhausted"
        ):
            if observation.get("running") is True and observation.get("has_window") is True:
                decision = RecoveryDecision(
                    level=2, strategy="refocus_observed_window", retry=True, observe=True
                )
        key = digest({"call": call, "code": failure.error_code, "observation": observation})
        if decision.level == 2 and key in self.seen:
            return RecoveryDecision(
                level=3, strategy="unchanged_observation", stop=True, approval=True
            )
        self.seen.add(key)
        if decision.retry:
            self.cycles += 1
        return decision

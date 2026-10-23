"""Immutable model snapshots; endpoint credentials never enter registry responses."""

from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from urllib.parse import urlsplit

from app.services.llm.routing.contracts import ModelRecord, RoutingError


@dataclass(frozen=True)
class RegisteredModel:
    record: ModelRecord
    service: object
    version: str
    target: str
    local: bool

    def capability(self, name):
        configured = self.service.config.capabilities
        observed = getattr(self.service, "observed_capabilities", {})
        return observed.get(name, configured.get(name))

    def public(self):
        capabilities = {
            key: {
                "value": self.capability(key),
                "source": "OBSERVED"
                if key in getattr(self.service, "observed_capabilities", {})
                else "CONFIGURED"
                if self.service.config.capabilities.get(key) is not None
                else "UNKNOWN",
            }
            for key in (
                "text",
                "tool_calling",
                "vision",
                "streaming",
                "structured_output",
                "reasoning",
                "coding",
            )
        }
        return self.record.model_dump(mode="json") | {
            "provider": self.service.config.provider,
            "api_model": self.service.config.model,
            "endpoint_ref": self.record.model_id,
            "config_version": self.version,
            "local": self.local,
            "capabilities": capabilities,
        }


class ModelRegistry:
    def __init__(self, entries=()):
        entries = tuple(entries)
        if len(entries) > 32 or len({row.record.model_id for row in entries}) != len(entries):
            raise RoutingError("INVALID_REGISTRY")
        self.entries = entries
        self.by_id = MappingProxyType({row.record.model_id: row for row in entries})

    @classmethod
    def build(cls, models):
        entries = []
        for record, service in models:
            config = service.config
            if not config or not config.model or config.model == "unconfigured":
                raise RoutingError("MODEL_NOT_CONFIGURED")
            # Public fingerprints are derived only from public settings and an opaque
            # main-process revision, never from an API key or key-derived digest.
            version = sha256(
                (config.model_dump_json(exclude={"api_key"}) + record.model_dump_json()).encode()
            ).hexdigest()[:24]
            target = sha256(
                (config.provider + "\n" + config.base_url + "\n" + config.model).encode()
            ).hexdigest()
            entries.append(
                RegisteredModel(
                    record,
                    service,
                    version,
                    target,
                    urlsplit(config.base_url).hostname in {"localhost", "127.0.0.1", "::1"},
                )
            )
        return cls(entries)

    def public(self):
        return [entry.public() for entry in self.entries]

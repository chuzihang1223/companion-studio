"""Independent model roles and observed capabilities; deterministic actions bypass them."""
from dataclasses import dataclass, field
import json
from app.core.file_logging import file_logger

ROLES = ('character_model', 'planning_model', 'tool_execution_model', 'coding_model')
CAPABILITIES = ('text', 'vision', 'tool_calling', 'structured_output', 'reasoning', 'streaming')


class ExecutionModelUnavailable(ValueError):
    def __init__(self):
        super().__init__('当前任务需要 Agent 执行模型，但还没有配置可用模型。')


@dataclass
class ModelCapabilities:
    configured: dict = field(default_factory=dict)
    observed: dict = field(default_factory=dict)

    def value(self, name):
        return self.observed.get(name, self.configured.get(name))

    def public(self):
        return {key: {'value': self.value(key), 'source': 'observed' if key in self.observed else
                     'configured' if self.configured.get(key) is not None else 'unknown'} for key in CAPABILITIES}


class ModelRoleRouter:
    def __init__(self, app):
        self.app, self.overrides, self.capabilities = app, {}, {}
        self.logger = file_logger('app.agent.models', 'agent-model-routing.log')

    def configure(self, services):
        if set(services)-set(ROLES):
            raise ValueError('未知模型职责')
        self.overrides = services
        self.capabilities = {}

    def capability(self, service):
        identity = id(service)
        if identity not in self.capabilities:
            config = getattr(service, 'config', None)
            self.capabilities[identity] = ModelCapabilities(dict(getattr(config, 'capabilities', {}) or {}))
        self.capabilities[identity].observed.update(getattr(service,'observed_capabilities',{}))
        return self.capabilities[identity]

    def service(self, role, *, tools=False):
        if role not in ROLES:
            raise ValueError('未知模型职责')
        character = self.overrides.get('character_model') or self.app.state.llm_service
        candidates = [self.overrides.get(role)]
        if tools and role != 'tool_execution_model':
            candidates.append(self.overrides.get('tool_execution_model'))
        candidates.append(character)
        for candidate in candidates:
            if candidate is None or (tools and self.capability(candidate).value('tool_calling') is False):
                continue
            config = getattr(candidate, 'config', None)
            self.logger.info(json.dumps({'event':'model_selected','role':role,'tools':tools,
                'model':getattr(config, 'model', 'local-provider'),
                'tool_calling':self.capability(candidate).value('tool_calling')}))
            return candidate
        self.logger.info(json.dumps({'event':'execution_model_unavailable','role':role}))
        raise ExecutionModelUnavailable()

    async def plan_tools(self, messages, tools, role='tool_execution_model'):
        service = self.service(role, tools=True)
        try:
            result = await service.plan_tools(messages, tools)
        except Exception as error:
            # An empty tool selection is not evidence that the API lacks the capability.
            self.logger.info(json.dumps({'event':'tool_request_failed','role':role,'error':type(error).__name__}))
            raise ExecutionModelUnavailable() from error
        if result.get('tool_calls'):
            self.capability(service).observed['tool_calling'] = True
        else:
            self.logger.info(json.dumps({'event':'no_tool_selection','role':role}))
        return result

    def public(self):
        return {role: {'configured':role in self.overrides,
                      'capabilities':self.capability(self.overrides.get(role) or self.app.state.llm_service).public()}
                for role in ROLES}

# Intelligent Model Router

The router extends `ModelRoleRouter → LLMService → OpenAIProvider`. It is disabled
by default. Model API keys stay in Electron main-process secure storage and the
controlled backend; routing metadata contains only references to saved presets.

## Configuration and modes

Open Settings → Model API → Intelligent Model Router. Save API presets first, set
their declared capabilities, then enter their context capacities. Explicitly permit
automatic requests and remote ordinary-text transfer for each automatic candidate.
Unknown capabilities/context capacities are not treated as supported.

- MANUAL uses the selected model and never automatically retries another preset.
- ROLE_BASED uses the saved role mapping; empty mappings inherit the character/current preset.
- AUTO applies enabled/type/capability/context/privacy/authorization/health/budget filters,
  then ranks eligible candidates using the selected preference.
- LOW_COST ranks known, same-currency estimates before unknown prices.
- LOW_LATENCY uses observed samples; unknown latency is not a fabricated zero.
- QUALITY_FIRST means user priority and preferred capabilities, not measured model quality.
- Embedding, ASR and TTS records are typed separately and never sent to chat completions.

## Snapshots and fallback

Each task capture retains its role mapping, policy, model versions and first selected
service per role/requirement. Configuration or health changes affect new tasks.
Task budget identity does not depend on the optional Trace recorder. Voice turns
capture their model configuration at begin; workflows retain their task context.
Scheduled permission snapshots use current, deterministic configuration identity so
changes require new admission and unchanged settings survive restart.

At most one transient fallback is allowed, and only between explicitly authorized
presets with the same provider/endpoint/API-model target. There is no cross-target
cloud fallback. After a stream delta or tool plan is returned, the router cannot
replay it. It never executes a tool. Existing attachment, RAG, screenshot, MCP and
workflow permission checks remain authoritative. HTTP success, validated tool
arguments and verified business goals are distinct observations.

## Budgets and accounting

SQLite reservations atomically constrain request counts per task, workflow run,
UTC day and provider/day. Failed, cancelled, unknown and restarted requests retain
their reservation. The UTC day is read once after obtaining the SQLite write lock.

Prices default to UNKNOWN. A user may declare input/output prices per million tokens
and a currency; manual declarations are not provider-verified prices. Strict money
limits currently refuse compatible-provider requests because there is no trusted
input-token counting adapter. An explicit ESTIMATED mode only constrains conservative
user-price/context reservations, never promises an invoice ceiling. Unknown prices,
unknown context bounds or incompatible currencies block configured money caps.

Actual prompt/completion/total/cached/reasoning fields come only from valid provider
usage. Missing usage remains missing. Cumulative SSE usage replaces the same request's
observation rather than adding each frame. Request-local observers are not retained
while yielding text to callers. Reservations and routing decisions contain no prompt,
tool arguments, file content, keys or endpoint URLs.

## Verification boundary

`router-core` is a versioned 48-case dataset in the original Evaluation worker.
It uses synthetic local HTTP and owned protocol fixtures. It tests production routing,
provider dispatch, SQLite, Agent, MCP, Skills, Workflow, RAG, Computer authorization,
Scheduler, Voice state and Trace boundaries. This does not measure real remote model
quality, real provider prices, physical microphones or acoustic echo cancellation.
Unified installation acceptance is a separate, identity-bound final gate.

## 上下文容量边界

候选容量必须覆盖最低输入上下文和输出上限。实际请求前再次检查完整消息、工具 Schema 及图片载荷的 UTF-8 体积估算并预留封装余量；过大会在记账和 HTTP 前拒绝，不截断历史、不自动换模型。该估算可能保守拒绝大图片，不是 Provider 的精确 tokenizer 结果，视觉配额和远端 tokenizer 仍须服务端校验。合成评测模型声明 65536 上下文，容量越界由独立回归覆盖。

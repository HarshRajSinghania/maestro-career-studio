# Multi-provider LLM layer with native Claude — design

Status: approved in brainstorm 2026-10-10. Implementation plan:
`2026-10-10-multi-provider-llm.md`; its "Planning decisions" section refines §1 (two stream
event kinds), §5 (replay rule per provider; no persisted image part; context block in the user
turn) and adds a signature-400 retry and history repair. Where they differ, the plan wins.
Research behind it: `docs/research/2026-10-10-multi-provider-llm.md` (local;
open-source app survey, library comparison, OpenRouter privacy, replay rules).

## Goal Card

**Goal:** A user with only a Claude key gets the whole app working on Claude,
the Assistant chat with tools included, through Anthropic's native API. OpenAI,
Gemini, custom OpenAI-compatible servers and a privacy-locked OpenRouter preset
sit beside it behind one provider-neutral layer, so in-app agents built later
(auto-apply runs, tools across the app) never deal with a provider's wire shape.

**Principles** (how to decide when a plan is ambiguous or wrong):
- Native over compatible. Claude goes through the official `anthropic` SDK,
  never Anthropic's OpenAI-compat endpoint (documented as not production-ready;
  ignores `response_format`, tool `strict`, caching).
- We own the adapters. Official SDKs only (`openai`, `anthropic`) plus today's
  Gemini REST; no LiteLLM / Pydantic AI / LangChain runtime dependency. Owner
  decision 2026-10-10 after comparing the trade-offs.
- Provider quirks live only in adapters. Nothing above `llm_providers/`
  branches on a provider name or model-id prefix.
- Provider-tagged opaque data is replayed only to the provider and model that
  produced it (Claude thinking signatures, Gemini `thought_signature`).
- One test seam per surface: `llm.call_openai` for one-shot services (46 test
  files fake it), a registry `FakeProvider` for chat/agents.
- Privacy is explicit. Every new recipient is named in PRIVACY.md; OpenRouter's
  `data_collection: "deny"` + `zdr: true` are forced, not settings.
- No auto-pick. Saving a key never changes role models; the UI says when a
  role's provider has no key.

**Non-goals:** the agent runner itself (outlined in §6, designed when the first
in-app agent starts); upgrading `openai` to 3.x; per-service JSON schemas
(the hook ships, adoption is per service later); showing model reasoning in the
UI; OS keychain storage for keys; Bedrock/Vertex/Foundry; changing the Jev
form-filling engine.

## 1. Architecture

`backend/app/services/llm.py` stays the front door: `call_openai`,
`LLMProviderError`, `_log_call`, tracing, the JSON salvage-and-retry loop. A new
package `backend/app/services/llm_providers/` holds:

| File | Role |
|---|---|
| `types.py` | Neutral types: `ModelRef(provider, model)`, `Message(role, parts)`, parts `TextPart`, `ImagePart`, `ToolCallPart`, `ToolResultPart`, `ReasoningPart`; `ProviderMeta(provider, model, data)`; `ToolSpec`; `Request`; `Response`; `Usage(input, output, cache_read, cache_write)`; `StreamEvent` = `TextDelta`, `ToolCallReady`, `ReasoningReady`, `Finish(usage, stop_reason)`; `ModelInfo(id, label, provider, capabilities)` |
| `registry.py` | Resolves a model id to a `ModelRef` and returns the adapter for it |
| `openai_compat.py` | OpenAI, custom servers (today's code, moved), OpenRouter instance |
| `anthropic.py` | Claude via official `anthropic` 1.x |
| `gemini.py` | Today's `generateContent` REST, plus native streaming chat |
| `replay.py` | Neutral history → per-provider request rules (§5) |

Adapter contract: `generate(Request) -> Response`, `stream(Request) ->
Iterator[StreamEvent]`, `list_models() -> list[ModelInfo]`. `Request` carries
model ref, system, messages, tools, `output` (`"text"` | `"json"` | JSON
schema), `max_tokens`, `timeout`, optional `effort`. Every adapter maps its
SDK/HTTP errors to `LLMProviderError` (user sentence + `provider_detail`).

Delivery: one release covers §2–§5 (providers, Claude everywhere including
chat, OpenRouter preset, neutral transcript). The agent runner (§6) is the
following phase.

## 2. Providers and routing

| Provider | Endpoint | Key setting / env |
|---|---|---|
| `openai` | api.openai.com, or the Custom AI server URL (today) | `llm.openai_api_key` / `OPENAI_API_KEY` |
| `anthropic` | api.anthropic.com via SDK | `llm.anthropic_api_key` / `ANTHROPIC_API_KEY` |
| `gemini` | generativelanguage.googleapis.com | `llm.gemini_api_key` / `GEMINI_API_KEY` |
| `openrouter` | `https://openrouter.ai/api/v1` (code-owned constant) | `llm.openrouter_api_key` / `OPENROUTER_API_KEY` |

**Routing rule** (replaces prefix guessing, unifies today's two disagreeing
rules):
1. A model id present in the catalog (seed, extra, discovered) routes to the
   provider tagged on that entry.
2. Otherwise `claude-*` → `anthropic`, `gemini-*` → `gemini`, but only when
   that provider has a key.
3. Otherwise → `openai` (custom server if set, else api.openai.com).

Known behavior changes: (a) a seeded `gemini-*` id used for chat on a custom
server goes to Google (with no Gemini key it says so) — today chat sent it to the
custom server; (b) with a custom server AND a Gemini key, any `gemini-*` chat
model goes to Google; (c) an unregistered `gemini-*` id with no Gemini key goes
to the OpenAI-compatible client on the one-shot path. A locally served model
keeps working under a non-catalog id (e.g. `gemini-local`).

**Claude client** is always constructed with an explicit `api_key`. A
zero-argument client would resolve `ANTHROPIC_AUTH_TOKEN` or an `ant auth`
profile from the user's shell on native installs.

**OpenRouter** is the OpenAI-compatible adapter with the fixed URL and
`extra_body={"provider": {"data_collection": "deny", "zdr": True}}` on every
request, no attribution headers. A model with no ZDR endpoint fails with a
plain sentence naming the reason.

## 3. Settings, catalog, setup, UI

- `model_settings`: new key constants and getters; `set_models` takes a
  `keys: dict[str, str | None]` patch (only named keys change) instead of
  positional per-key args, removing today's "None wipes the key" foot-gun and
  the router's re-supply workaround; provider set `{"openai", "gemini",
  "anthropic", "openrouter"}` in `_parse_extra_models` / `add_extra_model`.
- Seed catalog adds `claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5`
  (listed only; roles untouched). `test_seed_catalog_is_exactly_the_three_in_use`
  is rewritten for the new seed.
- `routers/settings.py`: `/api/settings/openai` keeps its path; `OpenAIInfo` and
  `ModelSettingsPayload` gain `anthropic_*` and `openrouter_*` configured /
  key-source / key fields; `SyncPayload.provider` and `ExtraModelPayload.provider`
  accept all four; sync dispatches to the registry's `list_models`.
- `setup_status`: the model-key step is done when any of the four keys or a
  custom server is set.
- `main.py` startup log reports every key source.
- Frontend: one provider list in `lib/model-catalog.ts` drives labels, picker
  groups and "Find … models" buttons; `lib/api-key-format.ts` adds `anthropic`
  (`sk-ant-`) and `openrouter` (`sk-or-`) and warns when a Claude or OpenRouter
  key is pasted into the OpenAI field; `lib/error-text.ts` names Claude and
  OpenRouter when a key is refused; `models-section.tsx` shows four key fields
  and flags any role whose provider has no key; `llm-endpoint.tsx` copy no
  longer says "OpenAI and Gemini" only. Desktop-only (1024px+) as everywhere.

## 4. Adapters

**Claude (`anthropic.py`):**
- `generate` streams internally and returns the final message (long outputs
  never hit HTTP timeouts); default `max_tokens` 32000.
- `output="json"` without schema: prompt + `_extract_json_object` + retry in
  `call_openai`, as today. With a schema: `output_config.format`.
- Adaptive thinking at the model's default effort unless `Request.effort` is
  set. Never sends `temperature`, `top_p`, `top_k`, or forced `tool_choice`
  (all 400 on current Claude models).
- Automatic `cache_control` on chat/agent requests (stable system + tools).
- Refusal: `stop_reason == "refusal"` → `LLMProviderError` with a plain
  sentence. Server-side `fallbacks: "default"` (beta
  `server-side-fallback-2026-07-01`) is on, so a classifier false positive
  (e.g. a resume describing security work) reroutes; billed at the fallback
  model's rates. `fallback` content blocks are audit markers and are dropped;
  non-text blocks before the last one are dropped too (Anthropic's echo rule).
- Images: base64 image blocks (attachment OCR path).
- Errors: auth → "Claude refused your API key"; 429/529 → "too many requests";
  Anthropic error strings added to `llm_capabilities._UNREACHABLE_SIGNS`.
- Usage includes cache read/write tokens for Langfuse.
- `list_models`: `GET /v1/models` with `capabilities`.

**OpenAI-compatible (`openai_compat.py`):** today's `_get_client`,
`completion_extras` (gpt-5.6 → `reasoning_effort: "none"`), JSON-mode rule for
custom servers, `list_openai_models` filter — moved, unchanged in behavior. The
`openai` SDK stays on 2.x.

**Gemini (`gemini.py`):** today's `_call_gemini` / `list_gemini_models`, moved.
Chat leaves Google's OpenAI-compat URL for native `streamGenerateContent` with
function declarations; `thought_signature` is captured as `ProviderMeta` and
replayed (fixes the probable dropped-signature bug in today's
`chat_agent._accumulate_stream`).

`llm_capabilities` probes through the same registry calls the app makes (the
"probe must issue the SAME call" gotcha in SYSTEM.md §12 holds).

## 5. Neutral transcript and chat

**Schema (one Alembic migration):** `chat_messages` gains `parts` (JSON,
`{"v": 1, "parts": [...]}`), `provider` (text, nullable), `model` (text,
nullable). Part JSON: `text{text}`, `image{attachment_id}`,
`tool_call{id, name, arguments, meta?}`, `tool_result{tool_call_id, content,
is_error}`, `reasoning{text, meta}`; `meta = {provider, model, data}`.

**Backfill** is lossless (today's rows hold only text, tool calls, tool
results): user → `text`; assistant → `text` + `tool_call`s; tool →
`tool_result`. `parts` becomes the source of truth; `content` keeps the display
text the UI reads; `tool_calls` / `tool_call_id` stop being written and are
dropped by a follow-up migration (SYSTEM.md §13 row with that trigger).

**Replay rules (`replay.py`):**
- `meta` is sent only when `meta.provider` and `meta.model` equal the target;
  otherwise reasoning parts are dropped and tool-call meta stripped.
- Gemini: a foreign tool call in the current turn gets Google's documented
  dummy signature.
- Tool-call ids normalized to `^[A-Za-z0-9_-]+$` (Claude's rule); missing ids
  generated.
- Same-model Claude thinking blocks are replayed unchanged (Anthropic: removing
  them can trigger ordering/signature 400s).

**Chat agent:** `run_turn` builds a neutral `Request` from `parts` and consumes
`StreamEvent`s — `TextDelta` → existing SSE delta; `ToolCallReady` →
`execute_tool` as today; `ReasoningReady` → persisted, not shown; `Finish` →
usage. Each model step gets a Langfuse generation. The tools capability gate,
`NO_MODEL_KEY` 422 and the staged-proposal flow are unchanged.

## 6. Agent runner (next phase, outline)

When the first in-app agent starts, the loop in `run_turn` becomes an
`AgentRunner` configured by tool set, system prompt, model role, step/token
budgets, approval checkpoints (e.g. stop before submit) and a cancel signal,
over the same transcript table with a `kind` column (`chat`, `apply`, …). The
Assistant becomes one configuration. This release only guarantees nothing
blocks it: neutral parts, provider-tagged meta, per-step usage.

## 7. Testing

- `llm.call_openai` fakes keep covering every one-shot service.
- `FakeProvider` in the registry replaces the OpenAI-shaped `FakeClient` in
  `tests/test_chat_agent.py`.
- Per-adapter wire tests without network: request builders are pure functions
  (neutral `Request` → exact provider JSON); recorded responses and SSE
  fixtures → neutral events. Claude: thinking/signature, refusal, `fallback`
  block, cache usage. Gemini: `thought_signature`, missing ids. OpenRouter:
  forced provider prefs. OpenAI-compat: gpt-5.6 rule, custom-server JSON mode.
- Replay-rule tests: provider switch mid-thread, Gemini dummy signature, id
  normalization, same-model reasoning unchanged.
- Migration: backfill fixtures; `tests/test_migration_model_parity.py`.
- Settings, catalog, setup-status and pinned UI-wording tests
  (`test_frontend_settings_assistant_words.py`) updated.
- Live gate before release (never CI): `scripts/live_provider_check.py` with
  real keys on a throwaway stack — per provider: text, JSON, image input, a
  two-step tool loop, a mid-thread provider switch.
- Backend suite with xdist (`-n auto --dist loadfile`).

## 8. Docs and privacy

- PRIVACY.md "AI services you choose": add Anthropic and OpenRouter (forced
  ZDR / no data collection).
- SYSTEM.md: rewrite §3 and §7 LLM paragraphs; replace the §7 "Streaming
  chat needs the OpenAI streaming tool-call wire shape" rule with
  `{#inv-provider-tagged-meta}` (enforced by the replay tests, pinned in
  `.system_md_enforcement.json`); §13 row for the old chat columns.
- Consent: the EEO gate (`eeo_consent.withhold_unconsented`, SYSTEM.md §6) is
  provider-agnostic — it strips for every outward reader whatever model serves
  it — so new providers need no gate change. The plan verifies no
  provider-specific recipient list exists elsewhere, and extends the §6
  "model providers are recipients too" sentence to name Claude and OpenRouter.
- README provider table, `.env.example`, `docker-compose.yml`
  (`ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}`, `OPENROUTER_API_KEY`),
  `docs/entities/others.md`, KNOWN_ISSUES.md where it names providers.
- Dependency: `anthropic` 1.x added to `pyproject.toml` and the hash-locked
  `requirements.lock` (brings `httpx2` beside `httpx`).

## 9. Rollout

One minor release; release notes call out the migration and the routing edge
in §2. Work is ordered so the first task is a no-behavior-change move into the
adapter layout and every later task lands on a green suite. Execution routing:
Sonnet 5.5 implements, Opus 5.5 reviews, the main session architects, Fable 5.1
verifies the plan before execution.

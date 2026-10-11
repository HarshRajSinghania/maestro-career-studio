# Multi-provider LLM support: how other apps do it and what Maestro should do

Date: 2026-10-10. Status: research input for an architecture decision. Nothing here is built yet.

## TL;DR

Build a thin internal interface that does not depend on any provider, and put one native adapter behind it per wire format: OpenAI-compatible (OpenAI, Ollama/LM Studio/vLLM, OpenRouter), Anthropic (official `anthropic` SDK), and Gemini (our existing native REST path, extended to chat). That is option B, built small. Option A (an Anthropic adapter that translates our OpenAI-shaped history at the boundary) is the first step of B: phase 1 should look like A, but the types it adds should be neutral types, not more OpenAI dicts. Store chat history as neutral parts. Provider-specific opaque data (Anthropic thinking signatures, Gemini `thought_signature`) is tagged with the provider that produced it and replayed only to that provider. Pydantic AI uses exactly this rule. Do not adopt LiteLLM, and do not route Claude through Anthropic's OpenAI-compat endpoint. Anthropic calls that endpoint not production-ready, and it ignores `response_format` and tool `strict`. Offer OpenRouter only as an opt-in OpenAI-compatible base URL, with `data_collection: "deny"` and `zdr: true` set by default. The most widely used open-source agents (Zed, Goose, Continue's core, Cline's new SDK, Jan, LibreChat) all keep native Anthropic paths. While researching I also found a probable existing bug: our streaming accumulator drops Gemini 3 thought signatures, which Gemini 3 requires for multi-step tool calls.

## Context: where Maestro is today

- `backend/app/services/llm.py`: `call_openai(prompt, model, response_format, images, ...)` has about 20 callers. It routes to the OpenAI SDK (OpenAI or a custom base URL) or to a native Gemini REST path. It then salvages JSON from the reply text and retries. No JSON schema is sent.
- `chat_agent.py` streams chat.completions with tools. `_accumulate_stream` rebuilds each tool call from only `id`, `function.name` and `function.arguments`. `ChatMessage` rows store `role`, `content`, `tool_calls` and `tool_call_id` in OpenAI shape, and `_replay_messages` sends them back unchanged.
- The provider is inferred from the model id (`_is_gemini_model`), with a special case so that a locally served `gemini-*` id still goes to the custom endpoint.
- Pinned in `requirements.lock`: `openai==2.53.0` and `httpx==0.28.1`.

## 1. How real open-source apps wire providers

| App | How providers are wired | Pattern | Native Anthropic path? |
|---|---|---|---|
| Open WebUI v0.11.4 | Two routers only, [`routers/openai.py`](https://github.com/open-webui/open-webui/blob/main/backend/open_webui/routers/openai.py) and `ollama.py`. Chat for every connection posts to `{url}/chat/completions` (or `/responses`). [`utils/anthropic.py`](https://github.com/open-webui/open-webui/blob/main/backend/open_webui/utils/anthropic.py) special-cases Anthropic only for model listing and adds an *inbound* Anthropic Messages endpoint that it translates to OpenAI shape. | OpenAI-compat everywhere | No for chat. An Anthropic URL goes to Anthropic's compat layer. |
| LibreChat v0.8.8 | Per-endpoint modules in [`packages/api/src/endpoints/`](https://github.com/danny-avila/LibreChat/tree/main/packages/api/src/endpoints) (`anthropic`, `google`, `openai`, `bedrock`, `custom`). The agent runtime [`@librechat/agents`](https://github.com/danny-avila/agents) wraps LangChain.js provider classes (`@langchain/anthropic`, `@langchain/google-genai`, `@langchain/openai`). | Unified library (LangChain.js) with native provider classes; "custom" endpoints are OpenAI-compat | Yes |
| Continue | Two layers. [`core/llm/llms/Anthropic.ts`](https://github.com/continuedev/continue/blob/main/core/llm/llms/Anthropic.ts) uses Continue's own neutral `ChatMessage`, which includes a `thinking` role that carries `signature`/`redactedThinking`. [`packages/openai-adapters`](https://github.com/continuedev/continue/tree/main/packages/openai-adapters) separately converts "an OpenAI-compatible request to a request for another API and back", with a native `AnthropicApi` that applies `cache_control` strategies. | Native adapters behind an internal interface, plus an OpenAI-shaped translation layer | Yes. It keeps thinking signatures and prompt caching. |
| Cline | New [`@cline/llms`](https://github.com/cline/cline/tree/main/sdk/packages/llms) SDK: per-vendor handlers (`vendors/anthropic.ts`, `google.ts`, `openai.ts`, `openai-compatible.ts`) built on Vercel AI SDK v7 and `@ai-sdk/anthropic`/`google`/`openai`. | Unified library (AI SDK) with native providers | Yes |
| Roo Code (archived; last commit 2026-05-15) | [`src/api/providers/anthropic.ts`](https://github.com/RooCodeInc/Roo-Code/blob/main/src/api/providers/anthropic.ts) on the Anthropic SDK with `cache_control`. History is stored in **Anthropic** shape and converted to OpenAI by [`transform/openai-format.ts`](https://github.com/RooCodeInc/Roo-Code/blob/main/src/api/transform/openai-format.ts). | Native adapters; one provider's wire shape used as the internal format | Yes, and Anthropic is the internal format |
| Zed | Neutral `MessageContent` enum (`Text`, `Thinking{text, signature}`, `RedactedThinking`, `Image`, `ToolUse`, `ToolResult`) in [`language_model_core/src/request.rs`](https://github.com/zed-industries/zed/blob/main/crates/language_model_core/src/request.rs). One provider module per vendor in [`language_models/src/provider/`](https://github.com/zed-industries/zed/tree/main/crates/language_models/src/provider) (`anthropic.rs`, `google.rs`, `open_ai.rs`, `open_router.rs`, `open_ai_compatible.rs`, and more). A test double lives in [`fake_provider.rs`](https://github.com/zed-industries/zed/blob/main/crates/language_model/src/fake_provider.rs). | Native adapters behind an internal interface | Yes |
| Goose v1.54.0 (moved to `aaif-goose/goose`) | Neutral `MessageContentBlock` (`ToolRequest`, `ToolResponse`, `Thinking`, `RedactedThinking`, ...) in [`goose-provider-types/src/conversation/message.rs`](https://github.com/aaif-goose/goose/blob/main/crates/goose-provider-types/src/conversation/message.rs), with one translator per wire format in [`formats/`](https://github.com/aaif-goose/goose/tree/main/crates/goose-provider-types/src/formats) (`anthropic.rs`, `google.rs`, `openai.rs`, `openai_responses.rs`, ...). | Native adapters behind an internal interface | Yes |
| Aider v0.86.0 | [`aider/llm.py`](https://github.com/Aider-AI/aider/blob/main/aider/llm.py) lazy-loads LiteLLM (its comment says the import takes 1.5 s) and sets `drop_params = True`. [`requirements.txt`](https://github.com/Aider-AI/aider/blob/main/requirements.txt) pins `litellm==1.82.3`. | Unified library (LiteLLM) | Through LiteLLM |
| Jan v0.8.6 | [`web-app/src/lib/model-factory.ts`](https://github.com/janhq/jan/blob/main/web-app/src/lib/model-factory.ts) uses the AI SDK: `createAnthropic`, `createGoogleGenerativeAI`, `createOpenAI`, and OpenAI-compatible for the long tail. A Rust proxy is adding native converters ([`converters.rs`](https://github.com/janhq/jan/blob/main/src-tauri/src/core/server/converters.rs)). | Unified library (AI SDK) with native providers | Yes |
| Simon Willison's `llm` | Core supports OpenAI. Plugins add providers: [`llm-anthropic`](https://github.com/simonw/llm-anthropic/blob/main/pyproject.toml) depends on `anthropic>=1.5,<2`, and [`llm-gemini`](https://github.com/simonw/llm-gemini/blob/main/pyproject.toml) uses raw `httpx`. | Native per-provider plugins | Yes |

What the table shows. Open WebUI is the only major app here that sends chat to Claude through an OpenAI-compatible endpoint. Every other app keeps a native Anthropic path, even though Anthropic's compat layer has been available to all of them. None of the sources I read states the reason in a single sentence. The code shows it instead: Continue's native path carries thinking signatures and `cache_control`, Roo's carries `cache_control`, and Zed and Goose model `Thinking{signature}` explicitly. The compat layer drops all three (section 5). The trend for 2025–26 is toward a neutral part-based message model, either self-built (Zed, Goose, Continue core) or taken from the Vercel AI SDK (Cline, Jan). Storing one provider's wire format internally (Roo) leaves a project carrying cross-provider sanitizers, as section 7 shows.

## 2. Unified libraries

| | LiteLLM | any-llm (Mozilla.ai) | Pydantic AI | aisuite | LangChain `init_chat_model` | Vercel AI SDK (reference) |
|---|---|---|---|---|---|---|
| Version / cadence | [1.104.2](https://pypi.org/project/litellm/), about 105 PyPI uploads in 90 days (including dev builds) | [1.33.0](https://pypi.org/project/any-llm-sdk/), 18 in 90 days | [`pydantic-ai-slim` 2.55.0](https://pypi.org/project/pydantic-ai-slim/), 62 in 90 days | [0.2.0](https://pypi.org/project/aisuite/) (2026-09-18), 1 in 90 days | [`langchain` 1.4.4](https://pypi.org/project/langchain/), 15 in 90 days | `ai` 7.0.x, provider spec V4 |
| Internal shape | OpenAI chat shape plus `provider_specific_fields` | OpenAI types; Anthropic signature kept in message `extra_content` ([utils.py](https://github.com/mozilla-ai/any-llm/blob/main/src/any_llm/providers/anthropic/utils.py)) | Neutral `ModelRequest`/`ModelResponse` with typed parts | OpenAI-style | `AIMessage` plus standard `content_blocks` | Neutral prompt parts: text, file, reasoning, tool-call, tool-result |
| Thinking | `reasoning_content` everywhere; `thinking_blocks` with `signature` only for Anthropic. These must be sent back with tool results or Anthropic returns 400, and many OpenAI-compatible clients drop them ([docs](https://docs.litellm.ai/docs/reasoning_content)). Gemini 3 signatures are kept in `provider_specific_fields.thought_signature`; a dummy signature is injected on model switch ([blog](https://docs.litellm.ai/blog/gemini_3)). | Rebuilds the Anthropic `thinking` block from `extra_content` | `ThinkingPart` has `signature` (mapped to Anthropic `signature`, Google `thought_signature`, OpenAI `encrypted_content`) and `provider_name`. Its source states: "Signatures are only sent back to the same provider" ([messages.py](https://github.com/pydantic/pydantic-ai/blob/main/pydantic_ai_slim/pydantic_ai/messages.py)). | Not documented | `reasoning` content block; the docs I read do not cover signatures ([models](https://docs.langchain.com/oss/python/langchain/models)) | Reasoning parts carry the signature in `providerOptions.anthropic`. Reasoning without one is **dropped** with an "unsupported reasoning metadata" warning ([source](https://github.com/vercel/ai/blob/main/packages/anthropic/src/convert-to-anthropic-prompt.ts)). |
| Prompt caching | `cache_control` on OpenAI-format content is passed to Anthropic and mapped to Gemini explicit caching (which costs storage) ([docs](https://docs.litellm.ai/docs/completion/prompt_caching)) | No `cache_control` handling found in the Anthropic provider files I read (not exhaustive) | Not checked | Not documented | Anthropic `cache_control` blocks, plus a caching middleware | `providerOptions.anthropic.cacheControl` per message or part ([docs](https://ai-sdk.dev/providers/ai-sdk-providers/anthropic)) |
| Structured output | `response_format`; the model JSON has `supports_native_structured_output` | Maps to Anthropic `output_config` | Native output modes per provider | Not documented | `with_structured_output(method=json_schema/function_calling/json_mode)` | `structuredOutputMode` (`outputFormat` / `jsonTool` / `auto`) |
| Core deps | 20, including `boto3`, `tokenizers`, `huggingface-hub`, `tiktoken`, `aiohttp`; **pins `openai<3`** | 7, including both `openai` and **`anthropic<1`** | 8 (`httpx2`, `opentelemetry-api`, `genai-prices`, ...); provider SDKs come through extras | 3 | `langchain-core`, `langgraph` | n/a (TypeScript) |
| Main fidelity risk | Silent `drop_params`; OpenAI-shape history loses thinking unless you carry its extension fields | Lags SDK major versions | Few fidelity risks; the cost is that its agent loop and types become ours | Unknown, thin | Two content representations | n/a |

The version pins matter now. `openai` 3.0.0 shipped on 2026-08-12 and `anthropic` 1.0.0 on 2026-08-20 ([PyPI](https://pypi.org/project/anthropic/#history)). LiteLLM still requires `openai<3` and any-llm still requires `anthropic<1` (PyPI `requires_dist`, read 2026-10-10). A wrapper therefore keeps us on old SDK majors. any-llm says it exists because LiteLLM "reimplements provider interfaces" instead of using the official SDKs ([README](https://github.com/mozilla-ai/any-llm)).

The AI SDK [LanguageModel V4 spec](https://ai-sdk.dev/providers/community-providers/custom-providers) is the best design reference. It defines role-restricted messages (system: text; user: text/file; assistant: text/file/reasoning/tool-call; tool: tool-result), `providerOptions` at call, message and part level, stream parts (`stream-start` with warnings, `tool-call-delta`, `finish` with usage), and usage fields `inputTokens`, `outputTokens`, `reasoningTokens` and `cachedInputTokens`.

## 3. Supply-chain and dependency risk (2025–2026)

- **LiteLLM PyPI compromise, 2026-03-24.** `litellm` 1.82.7 and 1.82.8 shipped a credential stealer. 1.82.8 added a `litellm_init.pth` file that runs at every interpreter start, so it ran even without `import litellm`. Data went to a domain that was not LiteLLM's. The cause was traced to the Trivy compromise in LiteLLM's CI. Mandiant was engaged, and a clean 1.83.0 shipped on 2026-03-30 through a rebuilt pipeline ([LiteLLM postmortem](https://docs.litellm.ai/blog/security-update-march-2026); [GHSA-5mg7-485q-xm76](https://github.com/advisories/GHSA-5mg7-485q-xm76), critical). The postmortem gives the window as about 40 minutes in one place and up to 16:00 UTC in another. Unpinned transitive installs were affected; the pinned Docker image was not. Aider was not affected because it pins 1.82.3.
- **Upstream cause:** malicious Trivy v0.69.4, 76 of 77 `trivy-action` tags force-pushed, and all 7 `setup-trivy` tags replaced, on 2026-03-19 ([GHSA-69fq-xp46-6x23](https://github.com/advisories/GHSA-69fq-xp46-6x23)). The lesson for us: pin GitHub Actions by SHA and install from hash-locked requirements, which we already do.
- **LiteLLM advisory volume.** 28 GitHub advisories were published in 2026 (5 critical, 9 high, 9 medium, 5 low) ([advisory list](https://github.com/BerriAI/litellm/security/advisories)), almost all in the proxy. One example is the pre-auth SQL injection [GHSA-r75f-5x8p-qvmc](https://github.com/advisories/GHSA-r75f-5x8p-qvmc), where Bishop Fox reports exploitation within about 36 hours (secondary source, [Bishop Fox](https://bishopfox.com/blog/cve-2026-42208-pre-authentication-sql-injection-in-litellm-proxy)). Using only the SDK avoids the proxy code paths, but every install still pulls in the whole package.
- **LangChain:** serialization injection in `langchain-core` `dumps/loads` that allowed secret extraction ([GHSA-c67j-w6g6-q2cm](https://github.com/advisories/GHSA-c67j-w6g6-q2cm), critical, 2025-12-23).
- **Pydantic AI:** 14 advisories in 2026 ([repo advisories](https://github.com/pydantic/pydantic-ai/security/advisories)), mostly SSRF in URL download and `web_fetch`, and in the optional local web chat UI (for example [GHSA-h4xc-3qfq-jf93](https://github.com/advisories/GHSA-h4xc-3qfq-jf93)). Not in the model adapters.
- **Official SDKs:** the `anthropic` Python SDK had two medium advisories, both in its local memory-tool helper ([GHSA-w828-4qhx-vxx3](https://github.com/advisories/GHSA-w828-4qhx-vxx3)). I found no 2026 advisories against `openai`, `google-genai`, `any-llm-sdk` or `aisuite` in the GitHub advisory database.
- **Not verified:** a secondary report names a June 2026 malicious package "anthropickit" in connection with an Anthropic-disclosed internal test incident. I could not confirm the package name from a primary source. OSV lists [MAL-2026-904](https://osv.dev/vulnerability/MAL-2026-904) (`strands-agents-anthropic`), a low-harm package with a name close to a real one; I did not read the entry.

The conclusion for dependencies: each of `openai`, `anthropic` and `google-genai` brings 6–10 small, mainstream dependencies (`httpx`/`httpx2`, `pydantic`, `anyio`, `jiter`). LiteLLM brings 20 core dependencies, a release cadence of about one build a day, and a recent compromise. For a public repo that ships Docker images to end users, the official SDKs are the smaller attack surface.

## 4. OpenRouter

- **Normalization.** The schema is "very similar to the OpenAI Chat API". `finish_reason` is normalized, with the raw value in `native_finish_reason`. Unsupported parameters are ignored. Usage includes `cost` and `is_byok` ([API overview](https://openrouter.ai/docs/api-reference/overview)).
- **Pass-through.** Reasoning is unified under a `reasoning` parameter. Responses carry a `reasoning_details` array, and Anthropic entries in it include a `signature`. That array must be sent back unmodified with tool calls ([reasoning](https://openrouter.ai/docs/use-cases/reasoning-tokens)). The page does not describe Gemini thought signatures. Anthropic `cache_control` is supported, explicit or automatic, with a 1h TTL option; requests stick to one provider for 10 minutes or per `session_id` ([caching](https://openrouter.ai/docs/features/prompt-caching)). `json_schema` structured outputs are supported per endpoint, so pair them with `require_parameters: true` ([structured outputs](https://openrouter.ai/docs/features/structured-outputs)).
- **Routing and privacy controls.** The `provider` object accepts `data_collection: "deny"` (default `"allow"`), `zdr: true`, `only`, `ignore`, `order` and `allow_fallbacks` ([provider routing](https://openrouter.ai/docs/features/provider-routing)). ZDR can also be enforced account-wide per model group. The ZDR page says OpenRouter does not keep prompts unless the user opts into logging ([ZDR](https://openrouter.ai/docs/features/zdr)). However, the privacy policy (updated 2026-08-31) says inputs containing personal data are collected, and gives no fixed retention period ([privacy policy](https://openrouter.ai/privacy)). These two documents do not fully agree, so treat OpenRouter as a processor that may keep data.
- **Pricing.** OpenRouter states no markup on inference. Credit purchases cost 5.5% ($0.80 minimum) by card. BYOK is free up to $25,000 a month of list-price usage and costs 5% beyond that ([FAQ](https://openrouter.ai/docs/faq), [BYOK](https://openrouter.ai/docs/use-cases/byok)). BYOK keys are stored encrypted by OpenRouter. By default BYOK falls back to shared capacity, which spends credits.
- **What this means for us.** Resume text would reach a third party plus whichever upstream OpenRouter picks. Our consent model already counts such hops as recipients (SYSTEM.md §6 names "OpenRouter serving Jev"). If we offer OpenRouter, the defaults should be `provider: {data_collection: "deny", zdr: true, allow_fallbacks: false}` and the consent text should name OpenRouter. The real benefit is one key for many models. The cost is one more party that sees the data, and OpenAI-shaped reasoning fields we would have to persist anyway.

## 5. Provider OpenAI-compat layers

**Anthropic** ([docs](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk)). Anthropic describes the layer as intended mainly to test and compare models, and "not considered a long-term or production-ready solution" for most uses. The limitations that matter to us:

- `response_format` is ignored.
- Tool `strict` is ignored.
- Prompt caching is not supported.
- Thinking can be requested, but the OpenAI SDK does not return it.
- System and developer messages are hoisted and concatenated into one string.
- `reasoning_effort`, `seed`, `logprobs` and `audio` are ignored.
- `usage.*_details` is always empty.

The same page documents three model constraints that any Claude path must respect:

- Claude Opus 5.5, Sonnet 5.5, Fable 5.1 and Mythos 5.1 reject forced `tool_choice`.
- Claude 4.7 and later return 400 for `temperature` below 1 or a non-default `top_p`.

The thinking docs add two more ([thinking](https://platform.claude.com/docs/en/build-with-claude/thinking)):

- Thinking is on by default on Claude 5-series models, and several of them reject `thinking: {type: "disabled"}`.
- Thinking blocks must go back "complete and unmodified" inside a tool-use turn; modified blocks get a 400.

The compat docs do not say how a tool loop on a model with thinking always on keeps those blocks when the OpenAI client never sees them. That is an open question to test, not a known failure.

**Gemini** ([docs](https://ai.google.dev/gemini-api/docs/openai), updated 2026-09-02). The page says OpenAI-library support is "still in beta while we extend feature support". It recommends calling the Gemini API directly if you are not already using OpenAI libraries. What it covers:

- Chat, streaming, tools, and `beta.chat.completions.parse` structured output.
- Images and audio.
- `reasoning_effort` mapped to `thinking_level`.
- `extra_body.google` for `thinking_config` and `cached_content`.
- Gemini 3 thought signatures, which travel as `tool_calls[].extra_content.google.thought_signature` ([signatures](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures)).

Google's own new docs now centre on a stateful Interactions API.

## 6. BYOK key storage and model catalogs

**Key storage**

- **OS keychain:**
  - Zed: a `CredentialsProvider` over the system keychain ([source](https://github.com/zed-industries/zed/blob/main/crates/credentials_provider/src/credentials_provider.rs)).
  - Jan: "Secrets live only in the OS keyring", stripped before settings are persisted ([useModelProvider.ts](https://github.com/janhq/jan/blob/main/web-app/src/hooks/useModelProvider.ts)).
  - Goose: system keyring, falling back to a **plain-text** `secrets.yaml` in containers or headless setups ([config docs](https://github.com/aaif-goose/goose/blob/main/documentation/docs/guides/config-files.md)).
  - Cline: VS Code `SecretStorage` (inferred from a code-search hit only).
- **Encrypted in a database:** LibreChat uses `CREDS_KEY`/`CREDS_IV` "to encrypt supported credentials stored in the database" ([dotenv docs](https://www.librechat.ai/docs/configuration/dotenv)).
- **Plain file:** `llm` uses `keys.json` in the user config directory ([setup docs](https://github.com/simonw/llm/blob/main/docs/setup.md)).
- **Env vars** work everywhere as an override.

For Maestro the relevant fact is Goose's note: a container has no desktop keyring. Our SQLite settings table on a user-owned volume, with tight file permissions, is the honest equivalent. Encrypting it with a key stored on the same volume adds little protection.

**Model catalogs and capabilities**

- Anthropic `GET /v1/models` now returns a `capabilities` object ([docs](https://platform.claude.com/docs/en/api/models/list)) with these fields:
  - `structured_outputs`, `image_input`, `pdf_input`, `citations`, `batch`
  - `thinking.types.{adaptive,enabled,disabled}`
  - `effort.{low…max}`
  - `context_management`
  - plus `max_input_tokens`, `max_tokens`, `lifecycle` and `retires_at`
- Gemini `models.list` returns `inputTokenLimit`, `outputTokenLimit`, `supportedGenerationMethods` and `thinking` ([docs](https://ai.google.dev/api/models)).
- OpenAI `/models` carries no capability data.
- Community catalogs:
  - LiteLLM's [`model_prices_and_context_window.json`](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json) has 4,036 entries with `supports_*` flags and cache costs.
  - [models.dev](https://github.com/anomalyco/models.dev) (MIT) serves `api.json` with `tool_call`, `reasoning`, `structured_output`, `attachment`, modalities, limits and cost.

How the apps use these: Open WebUI and Jan read provider `/models` endpoints, and Cline ships a generated catalog. The recommendation is in section 8.

## 7. Chat history when the user switches provider

There are two approaches.

1. **Neutral parts with provider-tagged opaque data.** Zed persists a versioned, zstd-compressed JSON `DbThread` of neutral messages, with migrations between versions ([db.rs](https://github.com/zed-industries/zed/blob/main/crates/agent/src/db.rs)). Pydantic AI's history is provider-independent, can be serialized with `ModelMessagesTypeAdapter`, is repaired before each request, and sends signatures only back to their own provider ([message history](https://pydantic.dev/docs/ai/core-concepts/message-history/)). The AI SDK drops foreign reasoning with a warning.
2. **One provider's wire shape, translated on the way out.** Roo stores Anthropic shape. Open WebUI, OpenRouter clients and our app store OpenAI shape.

What breaks on a switch, with sources:

- **Anthropic thinking.** Blocks must be returned unmodified inside a tool turn. Signatures are opaque and bound to the platform or account (Sonnet 5.5 and Haiku 5.5 thinking works only in the producing account) ([thinking](https://platform.claude.com/docs/en/build-with-claude/thinking)). Continue strips thinking that has no signature ([Anthropic.ts](https://github.com/continuedev/continue/blob/main/core/llm/llms/Anthropic.ts)). Foreign reasoning cannot be replayed to Claude, so it is dropped.
- **Gemini 3.** The first `functionCall` in each step of the current turn must carry its `thought_signature`, or the request gets a 400. For history moved from another model, Google documents two dummy values, `"skip_thought_signature_validator"` and `"context_engineering_is_the_way_to_go"`. Parallel calls must be ordered FC1, FC2, FR1, FR2 ([signatures](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures)). Roo's `sanitizeGeminiMessages` drops tool calls without signatures because, as its comment says, a provider switch "can poison the next request" ([openai-format.ts](https://github.com/RooCodeInc/Roo-Code/blob/main/src/api/transform/openai-format.ts)).
- **OpenAI reasoning.** Reasoning items are encrypted and must be resent when `store: false` ([migration guide](https://developers.openai.com/api/docs/guides/migrate-to-responses)).
- **Tool-call ids** are opaque strings echoed back (`toolu_…`, `call_…`, `function-call-…`). I found no documented cross-provider format constraint (see the unverified list).

**Probable bug in our current code.** `_accumulate_stream` copies only `id`, `name` and `arguments` from each tool-call delta, so `extra_content.google.thought_signature` never reaches `ChatMessage.tool_calls`. Under Google's validation rules, a Gemini 3 chat that needs two tool steps in one turn should fail with a 400 on the second request. I have not reproduced this on a live stack.

## 8. Recommendation

### Decision

Build B as a small layer and deliver it in phases, with A first. Keep OpenAI-compatible handling for everything that is truly OpenAI-compatible (OpenAI, local servers, and OpenRouter as an opt-in preset). Add a native Anthropic adapter. Promote the Gemini REST path to a full adapter. Reject C for Claude. Reject D as a runtime dependency, but copy Pydantic AI's types and rules. E is optional and off by default.

The reasons:

- The apps I examined keep native Anthropic paths.
- The compat layer ignores the two features our JSON pipeline would most benefit from: schema-constrained output (`output_config.format`, GA, see [structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)) and caching.
- OpenAI's own chat.completions is now the narrow shape. OpenAI recommends Responses "for all new projects", and from GPT-5.4 on, chat.completions allows tools only with reasoning effort `none` ([guide](https://developers.openai.com/api/docs/guides/migrate-to-responses)). Our `completion_extras` already works around this.

### Interface sketch (`backend/app/services/llm/`)

```python
# types.py — the only shapes the rest of the app sees
@dataclass(frozen=True)
class ModelRef:  provider: str; model: str          # explicit; stop inferring from id prefixes
@dataclass
class ProviderMeta: provider: str; data: dict       # opaque; replayed only when provider matches
@dataclass
class TextPart: text: str
@dataclass
class ImagePart: data: bytes; media_type: str
@dataclass
class ReasoningPart: text: str; meta: ProviderMeta | None    # Anthropic signature / redacted data
@dataclass
class ToolCallPart: id: str; name: str; arguments: dict; meta: ProviderMeta | None  # Gemini thought_signature
@dataclass
class ToolResultPart: tool_call_id: str; content: str; is_error: bool = False
@dataclass
class Message: role: Literal["user", "assistant", "tool"]; parts: list[Part]
@dataclass
class Request:
    model: ModelRef; system: str | None; messages: list[Message]
    tools: list[ToolSpec] = (); output: Literal["text", "json"] | JsonSchema = "text"
    max_tokens: int | None = None; timeout: float | None = None
@dataclass
class Usage: input: int; output: int; cache_read: int = 0; cache_write: int = 0; reasoning: int = 0
# StreamEvent = TextDelta | ToolCallReady | ReasoningReady | Finish(usage, stop_reason) | Warning

class Provider(Protocol):
    def generate(self, req: Request) -> Response: ...
    def stream(self, req: Request) -> Iterator[StreamEvent]: ...
    def list_models(self) -> list[ModelInfo]: ...      # ModelInfo carries capabilities
```

- **Translation lives only in the adapters:** `providers/openai_compat.py`, `providers/anthropic.py`, `providers/gemini.py`. Each adapter owns its quirks:
  - Anthropic: drop `temperature`/`top_p` where the model rejects them; never force `tool_choice` on models that reject it (read `capabilities` instead of hard-coding ids); send `output_config.format` for JSON schemas; place `cache_control` on the system prompt and tools; handle `max_tokens` with thinking on.
  - Gemini: preserve signatures; inject Google's documented dummy signature on foreign tool calls in the current turn.
  - OpenAI-compatible: today's `completion_extras` rules.
- **`call_openai` stays as a compatibility wrapper** (rename it later) over `registry.get(ref).generate(...)`. The 20 callers and our "fake `llm.call_openai`" test rule keep working. The JSON salvage-and-retry loop stays in the wrapper. Adapters that support it receive an optional schema, which lets callers add schemas one at a time.
- **One test seam.** A `FakeProvider` registered in the registry, as Zed's `FakeLanguageModelProvider` is. Each adapter also gets a recorded-fixture translation test (neutral request in, provider JSON out, and back), in the style of Cline's `*.wire.test.ts`.
- **History.** Add a `parts` JSON column (versioned, like Zed's `DbThread`) plus `provider` and `model` on `ChatMessage`. Backfill from today's columns. This conversion is lossless: our rows contain only text, tool calls and tool results, and we persist no reasoning today. On replay, an adapter drops or substitutes any `ProviderMeta` whose provider does not match. Rule: if `meta.provider` differs from the target provider, drop the reasoning parts, and on Gemini put the documented dummy signature on tool calls in the current turn.
- **Catalog.** Fill capabilities from Anthropic `capabilities` and Gemini `models.list`. Keep the text/json/tools probe for OpenAI-compatible endpoints, where no metadata exists. Optionally seed prices and limits from a vendored, pinned snapshot of models.dev rather than a runtime fetch.
- **Dependencies.** Add `anthropic` 1.x (hash-locked). It brings `httpx2` next to our `httpx`. `openai` 3.x uses `httpx2` too, so upgrading `openai` would leave us on one stack.

### What each option loses

- **A only (translate OpenAI-shaped history at the boundary):** fastest. It gives up thinking continuity in tool loops unless we add a sidecar field, which turns it into B. It keeps the id-prefix routing hack.
- **B (recommended):** costs a migration and a few hundred lines per adapter. It loses nothing important, and we own the quirk tables.
- **C (compat endpoints):** gives up structured outputs, `strict`, caching, thinking visibility and accurate usage details. It also leaves an open question about tool loops on models with thinking always on, and Anthropic describes the layer as for testing.
- **D (LiteLLM or similar):** costs SDK-major lag, a large dependency surface with a recent compromise, `drop_params`-style silent loss, and a 1.5 s import. Pydantic AI is the one D option with good fidelity, but adopting it means replacing `chat_agent` and following a release cadence of about five builds a week.
- **E (OpenRouter for everything):** sends every resume to a third party, adds a fee on top of credits, and still requires us to persist `reasoning_details`. Acceptable only as an opt-in.

### Comparison

| Option | Fidelity | Dependency risk | Effort | Privacy | Maintenance |
|---|---|---|---|---|---|
| A: native Anthropic adapter, OpenAI-shaped history | Medium-high. Loses thinking replay without a sidecar. | Low (official SDK) | Low (about 1 week) | Direct to provider | Grows: one translation per new provider, all from the OpenAI shape |
| **B: neutral interface plus native adapters** | **High** | **Low** | **Medium (2–3 weeks, including history migration)** | **Direct to provider** | **Medium; we own quirk tables, as Zed and Goose do** |
| C: provider OpenAI-compat endpoints | Low for Claude (no `response_format`, `strict`, caching or thinking); beta for Gemini | Lowest (no new dependency) | Lowest (days) | Direct to provider | Low code, high surprise: silent ignores |
| D: LiteLLM / any-llm / Pydantic AI / LangChain | Medium (LiteLLM) to high (Pydantic AI) | High (LiteLLM), medium (others); SDK-major lag | Medium to high (Pydantic AI means rewriting chat) | Direct to provider (SDK mode) | Upgrades follow their cadence |
| E: route everything through OpenRouter | Medium-high (`reasoning_details`, `cache_control`, structured outputs per endpoint) | Low code dependency, high service dependency | Low | **Adds a third party**; ZDR and `data_collection` help but the policy is broad | Low |

### Uncertainty

Effort figures are estimates. The Gemini signature bug is inferred from code and docs, not reproduced. I have not tested whether Anthropic's compat layer survives tool loops on models with thinking always on. Model-specific Anthropic constraints change often, which is why the adapter should read `capabilities` instead of hard-coding model ids.

## Sources

Provider docs:
- [Anthropic: OpenAI SDK compatibility](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk)
- [Anthropic: Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking)
- [Anthropic: Extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)
- [Anthropic: Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
- [Anthropic: List Models](https://platform.claude.com/docs/en/api/models/list)
- [Gemini: OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)
- [Gemini: Thought signatures (generateContent)](https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures)
- [Gemini: Thinking](https://ai.google.dev/gemini-api/docs/thinking)
- [Gemini: models API](https://ai.google.dev/api/models)
- [OpenAI: Migrate to Responses](https://developers.openai.com/api/docs/guides/migrate-to-responses)

OpenRouter:
- [Provider routing](https://openrouter.ai/docs/features/provider-routing)
- [ZDR](https://openrouter.ai/docs/features/zdr)
- [Privacy and logging](https://openrouter.ai/docs/features/privacy-and-logging)
- [Privacy policy](https://openrouter.ai/privacy)
- [Prompt caching](https://openrouter.ai/docs/features/prompt-caching)
- [Reasoning tokens](https://openrouter.ai/docs/use-cases/reasoning-tokens)
- [Structured outputs](https://openrouter.ai/docs/features/structured-outputs)
- [BYOK](https://openrouter.ai/docs/use-cases/byok)
- [FAQ](https://openrouter.ai/docs/faq)
- [API overview](https://openrouter.ai/docs/api-reference/overview)

Libraries:
- [LiteLLM reasoning](https://docs.litellm.ai/docs/reasoning_content)
- [LiteLLM prompt caching](https://docs.litellm.ai/docs/completion/prompt_caching)
- [LiteLLM Gemini 3 post](https://docs.litellm.ai/blog/gemini_3)
- [LiteLLM model JSON](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json)
- [any-llm](https://github.com/mozilla-ai/any-llm)
- [Pydantic AI messages.py](https://github.com/pydantic/pydantic-ai/blob/main/pydantic_ai_slim/pydantic_ai/messages.py)
- [Pydantic AI message history](https://pydantic.dev/docs/ai/core-concepts/message-history/)
- [aisuite](https://github.com/andrewyng/aisuite)
- [LangChain models](https://docs.langchain.com/oss/python/langchain/models)
- [AI SDK provider spec](https://ai-sdk.dev/providers/community-providers/custom-providers)
- [AI SDK Anthropic provider](https://ai-sdk.dev/providers/ai-sdk-providers/anthropic)
- [AI SDK Anthropic prompt converter](https://github.com/vercel/ai/blob/main/packages/anthropic/src/convert-to-anthropic-prompt.ts)
- [models.dev](https://github.com/anomalyco/models.dev)
- PyPI JSON metadata for `litellm`, `any-llm-sdk`, `pydantic-ai-slim`, `aisuite`, `langchain`, `anthropic`, `openai` and `google-genai`, read 2026-10-10

Security:
- [LiteLLM March 2026 postmortem](https://docs.litellm.ai/blog/security-update-march-2026)
- [GHSA-5mg7-485q-xm76](https://github.com/advisories/GHSA-5mg7-485q-xm76)
- [GHSA-69fq-xp46-6x23](https://github.com/advisories/GHSA-69fq-xp46-6x23)
- [GHSA-r75f-5x8p-qvmc](https://github.com/advisories/GHSA-r75f-5x8p-qvmc)
- [LiteLLM advisories](https://github.com/BerriAI/litellm/security/advisories)
- [GHSA-c67j-w6g6-q2cm](https://github.com/advisories/GHSA-c67j-w6g6-q2cm)
- [Pydantic AI advisories](https://github.com/pydantic/pydantic-ai/security/advisories)
- [GHSA-w828-4qhx-vxx3](https://github.com/advisories/GHSA-w828-4qhx-vxx3)
- [Bishop Fox on CVE-2026-42208](https://bishopfox.com/blog/cve-2026-42208-pre-authentication-sql-injection-in-litellm-proxy) (secondary source)
- [OSV MAL-2026-904](https://osv.dev/vulnerability/MAL-2026-904)

App source:
- [Open WebUI openai router](https://github.com/open-webui/open-webui/blob/main/backend/open_webui/routers/openai.py)
- [Open WebUI anthropic utils](https://github.com/open-webui/open-webui/blob/main/backend/open_webui/utils/anthropic.py)
- [LibreChat endpoints](https://github.com/danny-avila/LibreChat/tree/main/packages/api/src/endpoints)
- [@librechat/agents](https://github.com/danny-avila/agents)
- [LibreChat dotenv](https://www.librechat.ai/docs/configuration/dotenv)
- [Continue Anthropic.ts](https://github.com/continuedev/continue/blob/main/core/llm/llms/Anthropic.ts)
- [Continue openai-adapters](https://github.com/continuedev/continue/tree/main/packages/openai-adapters)
- [Cline @cline/llms](https://github.com/cline/cline/tree/main/sdk/packages/llms)
- [Roo Code anthropic.ts](https://github.com/RooCodeInc/Roo-Code/blob/main/src/api/providers/anthropic.ts)
- [Roo Code openai-format.ts](https://github.com/RooCodeInc/Roo-Code/blob/main/src/api/transform/openai-format.ts)
- [Zed request.rs](https://github.com/zed-industries/zed/blob/main/crates/language_model_core/src/request.rs)
- [Zed providers](https://github.com/zed-industries/zed/tree/main/crates/language_models/src/provider)
- [Zed agent db.rs](https://github.com/zed-industries/zed/blob/main/crates/agent/src/db.rs)
- [Zed credentials_provider](https://github.com/zed-industries/zed/blob/main/crates/credentials_provider/src/credentials_provider.rs)
- [Goose formats](https://github.com/aaif-goose/goose/tree/main/crates/goose-provider-types/src/formats)
- [Goose message.rs](https://github.com/aaif-goose/goose/blob/main/crates/goose-provider-types/src/conversation/message.rs)
- [Goose config docs](https://github.com/aaif-goose/goose/blob/main/documentation/docs/guides/config-files.md)
- [Aider llm.py](https://github.com/Aider-AI/aider/blob/main/aider/llm.py)
- [Aider requirements.txt](https://github.com/Aider-AI/aider/blob/main/requirements.txt)
- [Jan model-factory.ts](https://github.com/janhq/jan/blob/main/web-app/src/lib/model-factory.ts)
- [Jan useModelProvider.ts](https://github.com/janhq/jan/blob/main/web-app/src/hooks/useModelProvider.ts)
- [Jan converters.rs](https://github.com/janhq/jan/blob/main/src-tauri/src/core/server/converters.rs)
- [llm setup](https://github.com/simonw/llm/blob/main/docs/setup.md)
- [llm-anthropic](https://github.com/simonw/llm-anthropic/blob/main/pyproject.toml)
- [llm-gemini](https://github.com/simonw/llm-gemini/blob/main/pyproject.toml)

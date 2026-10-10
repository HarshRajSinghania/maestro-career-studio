# Multi-provider LLM layer with native Claude — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (or
> superpowers:executing-plans in a separate session) to implement this plan task-by-task.

**Goal:** A Claude key alone runs the whole app (JSON services, image OCR, the Assistant chat
with tools) through Anthropic's native API, beside OpenAI, Gemini, custom OpenAI-compatible
servers and a privacy-locked OpenRouter preset, all behind one provider-neutral layer.

**Architecture:** `llm.py` stays the front door (`call_openai`, key getters, client caches, call
log, tracing). A new `app/services/llm_providers/` package holds neutral types, a registry that
resolves a model id to a provider, and one adapter per wire format (OpenAI-compatible incl.
OpenRouter, Anthropic SDK, Gemini REST). Chat history gains a versioned neutral `parts` column
with provider-tagged opaque data, and the chat agent streams through the adapters.

**Tech Stack:** FastAPI, SQLAlchemy 2 + Alembic on SQLite, `openai` 2.x (stays), `anthropic`
1.x (new; brings `httpx2`), urllib for Gemini REST; Next.js 16 frontend (read
`frontend/AGENTS.md` before touching it).

**Design:** `docs/plans/2026-10-10-multi-provider-llm-design.md` (approved 2026-10-10).
Research: `docs/research/2026-10-10-multi-provider-llm.md` (local, uncommitted).

---

## Goal Card

**Goal:** A user with only a Claude key gets the whole app working on Claude, the Assistant chat
with tools included, through Anthropic's native API. OpenAI, Gemini, custom OpenAI-compatible
servers and a privacy-locked OpenRouter preset sit beside it behind one provider-neutral layer,
so in-app agents built later (auto-apply runs, tools across the app) never deal with a
provider's wire shape.

**Principles:**
- Native over compatible: Claude goes through the official `anthropic` SDK, never Anthropic's
  OpenAI-compat endpoint.
- We own the adapters: official SDKs only plus today's Gemini REST. No LiteLLM / Pydantic AI /
  LangChain runtime dependency (owner decision 2026-10-10).
- Provider quirks live only in `llm_providers/`. Nothing above it branches on a provider name
  or a model-id prefix (the registry's routing rule is the one exception, and it lives there).
- Provider-tagged opaque data is replayed only to the provider that produced it.
- One seam per surface: `llm.call_openai` for one-shot services (≈46 test files fake it); a
  registry `FakeProvider` for chat. Never make a service test reach an adapter.
- Privacy is explicit: every new recipient is in PRIVACY.md; OpenRouter's
  `data_collection: "deny"` + `zdr: true` are forced, not settings.
- No auto-pick: saving a key never changes a role's model.

**Non-goals:** the agent runner (design §6; its own plan later); upgrading `openai` to 3.x;
per-service JSON schemas (the hook ships, adoption later); showing reasoning in the UI; OS
keychain storage; Bedrock/Vertex/Foundry; any change to the Jev form-filling engine; phone
widths (desktop-only app, 1024px+).

**Autonomy:** peer. Adapt *how* when the repo disagrees with this plan; log every deviation in
the Deviation log with a one-line reason and the Goal Card line that motivates it; flag anything
that contradicts the Goal Card. Never expand scope. Interfaces other tasks depend on
(`types.py`, `registry.py` signatures, the transcript JSON) change only via a deviation note to
the planner. ADD tests beside pinned tests; change a pinned test's expectation only when this
plan says the pinned behavior changes, and say so in the commit.

## Planning decisions (refinements of the design, made while reading the code)

1. **Stream events are two kinds.** `TextDelta(text)` while streaming, then one
   `Finish(message, usage, stop_reason, model)` carrying the whole assistant `Message` with its
   parts in order. Design §1 listed `ToolCallReady`/`ReasoningReady`; folding them into `Finish`
   keeps part order exact (Claude requires thinking → text → tool_use order on replay) and
   the chat loop simpler.
2. **Replay rule is per provider, with a model refinement for Gemini.** Generic rule: a part's
   `meta` is kept only when `meta.provider == target.provider`. Claude: regular thinking blocks
   replay across Claude models (the server handles Fable-tier drops), and *removing* them can
   cause ordering/signature 400s, so the Anthropic adapter keeps all `anthropic` meta. Gemini:
   a signature from a different Gemini model is replaced by Google's documented dummy value.
   Design §5 said "provider and model"; this is the corrected rule.
3. **The ephemeral context block moves from a mid-conversation system message to a text part
   at the start of the latest user message.** Works on all three wire formats, keeps the system
   prompt byte-stable (prompt caching), and stops user-controlled strings (attachment
   filenames) riding in the system role.
4. **No `image` part in the persisted transcript.** Chat has no image input (attachments are
   read via the `read_attachment` tool). `ImagePart` exists in memory for one-shot calls only.
5. **Anthropic signature 400 → one retry without reasoning.** Sonnet 5.5 / Haiku 5.5 thinking
   only validates in the producing account; after a key change, a 400 naming the signature or
   thinking is retried once with reasoning parts removed.
6. **Repair before replay.** A tool call with no persisted result (a turn that errored after the
   assistant row) gets a synthetic error result; a result with no call is dropped. OpenAI and
   Claude both 400 on such history today.
7. **Key storage is a patch.** `model_settings.set_api_keys(session, {provider: key|None})`
   changes only named keys; `set_models` stops touching keys and returns `(fast, smart)`. The
   router's "re-supply the stored key" workaround is deleted.

## Ground rules for every task

- Work in this worktree; never the main checkout, never the live compose stack
  (fixed project name: any checkout's `docker compose down` hits the live app).
- Read `SYSTEM.md` first. Orientation tier is 973/1000 lines (`scripts/check_system_md.py`):
  every added line is paid for by tightening the section you touch, or by moving reference
  detail into `docs/entities/others.md`.
- Python: anaconda interpreter, run from `backend/`. Full suite (always xdist):
  `cd backend && MAESTRO_SKIP_SLOW=1 python -m pytest tests/ mcp_server/tests/ -q -n auto --dist loadfile -p no:cacheprovider`.
  Single file: `cd backend && python -m pytest tests/test_x.py -q -p no:cacheprovider`.
- Never run pytest under `data/` or with a worker that sees real keys: the suite blanks
  `OPENAI_API_KEY`/`GEMINI_API_KEY`; Task 4 adds `ANTHROPIC_API_KEY`/`OPENROUTER_API_KEY` to
  that blanking (find it: `grep -rn "GEMINI_API_KEY" backend/tests/conftest.py`).
- Dependency changes (Task 5): CI installs `pip install -e ".[dev,mcp]"`, not the lock. Run the
  suite in a throwaway venv built like CI (`uv venv -p 3.12 $SP/ci-venv &&
  VIRTUAL_ENV=$SP/ci-venv uv pip install -e "backend[dev,mcp]"`), `$SP` = scratch dir outside
  the repo. `backend/requirements.lock` is regenerated only inside `python:3.12-slim` with the
  recipe in `backend/Dockerfile`.
- Lint: `cd backend && python -m ruff check .`; frontend: `cd frontend && npx tsc --noEmit &&
  npm run lint && node --test lib/*.test.ts && npm run build`; docs:
  `python3 scripts/check_system_md.py`.
- Alembic revisions use impl types only (`sa.JSON`, `sa.Text`, …) and never import app code
  (SYSTEM.md §12).
- No network in tests. Adapter tests use pure request builders and recorded fixtures.
- Commit after every task (`git add` named paths only); end messages with the executing model's
  `Co-Authored-By` line. Do not push.

## Task map

| # | Task | Behavior change |
|---|---|---|
| 0 | Baseline | none |
| 1 | Neutral types, errors module, registry skeleton, FakeProvider | none |
| 2 | OpenAI-compatible adapter: `generate` (move) | none |
| 3 | Gemini adapter: `generate` + `list_models` (move) | none |
| 4 | Routing rule, four keys, settings API, setup status, env | yes |
| 5 | `anthropic` dependency + Claude adapter `generate` / `list_models` | yes |
| 6 | OpenRouter adapter instance | yes |
| 7 | Catalog sync / extra models for four providers; probe through registry | yes |
| 8 | OpenAI-compatible `stream` (+ OpenRouter `reasoning_details`) | none yet |
| 9 | Claude `stream` | none yet |
| 10 | Gemini native `stream` | none yet |
| 11 | Transcript migration (`parts`, `provider`, `model`) + codec | schema |
| 12 | `replay.py` + chat agent on the adapters | yes |
| 13 | Frontend: four providers | yes |
| 14 | Live provider check script | none |
| 15 | Docs, privacy, SYSTEM.md | docs |
| 16 | Final gates and review | none |

---

### Task 0: Baseline

**Files:** none.

**Step 1:** `git status` clean on branch `claude/anthropic-api-key-compat-8b5ded` (HEAD includes
the design commit). Run the full suite, ruff, `check_system_md.py`, and the frontend checks.
Record passed/skipped/wall-clock in the Gate table at the end of this file.

**Step 2:** Record the pinned behaviors this plan will touch, so reviewers can diff against
them: `grep -rn "test_seed_catalog_is_exactly_the_three_in_use\|OpenAI or Gemini\|SHAPE"
backend/tests/test_model_catalog.py backend/tests/test_frontend_settings_assistant_words.py`.

---

### Task 1: Neutral types, errors module, registry skeleton, FakeProvider

No behavior change. Creates the vocabulary every later task uses.

**Files:**
- Create: `backend/app/services/llm_providers/__init__.py` (empty docstring module)
- Create: `backend/app/services/llm_providers/errors.py`
- Create: `backend/app/services/llm_providers/types.py`
- Create: `backend/app/services/llm_providers/registry.py`
- Create: `backend/tests/fake_provider.py`
- Modify: `backend/app/services/llm.py:23-117` (move error helpers; re-export)
- Test: `backend/tests/test_llm_provider_types.py`

**Step 1: Move the error vocabulary.** Cut `LLMProviderError`, `NO_KEY_MESSAGE`,
`NO_GEMINI_KEY_MESSAGE`, `_KEY_REFUSED`, `_TIMED_OUT`, `_no_answer`, `_CODE_WORDS`,
`_STATUS_WORDS`, `_status_reason`, `_openai_reason`, `_gemini_reason` from `llm.py` into
`llm_providers/errors.py` unchanged (keep its `logger = logging.getLogger("app.services.llm")`
so log assertions keep matching). In `llm.py` add:

```python
from app.services.llm_providers.errors import (  # noqa: F401 — re-exported; callers use llm.*
    NO_GEMINI_KEY_MESSAGE,
    NO_KEY_MESSAGE,
    LLMProviderError,
    _KEY_REFUSED,
    _TIMED_OUT,
    _gemini_reason,
    _no_answer,
    _openai_reason,
    _status_reason,
)
```

`errors.py` must not import `app.services.llm` (it is imported by it). Run the full suite:
identical counts to Task 0.

**Step 2: Write the failing type tests.**

```python
# backend/tests/test_llm_provider_types.py
from app.services.llm_providers import types as t


def test_message_text_joins_text_parts_only():
    msg = t.Message("assistant", [
        t.ReasoningPart("thinking", t.ProviderMeta("anthropic", "claude-x", {"type": "thinking"})),
        t.TextPart("Hel"), t.TextPart("lo"),
        t.ToolCallPart("c1", "get_resume", {"kind": "base"}),
    ])
    assert msg.text == "Hello"
    assert [p.id for p in msg.tool_calls] == ["c1"]


def test_parts_round_trip_through_transcript_json():
    parts = [
        t.TextPart("hi"),
        t.ToolCallPart("c1", "edit", {"a": 1}, t.ProviderMeta("gemini", "gemini-3.7-flash", {"thoughtSignature": "sig"})),
        t.ToolResultPart("c1", '{"ok": true}', is_error=False),
        t.ReasoningPart("", t.ProviderMeta("anthropic", "claude-opus-5-5", {"type": "thinking", "thinking": "", "signature": "s"})),
    ]
    doc = t.dump_parts(parts)
    assert doc["v"] == 1
    assert t.load_parts(doc) == parts


def test_load_parts_rejects_unknown_version():
    import pytest
    with pytest.raises(ValueError):
        t.load_parts({"v": 99, "parts": []})


def test_usage_as_langfuse_keeps_cache_counts_only_when_present():
    assert t.Usage(10, 5).as_langfuse() == {"input": 10, "output": 5}
    assert t.Usage(10, 5, cache_read=7).as_langfuse() == {"input": 10, "output": 5, "cache_read": 7}
```

**Step 3:** Run `python -m pytest tests/test_llm_provider_types.py -q -p no:cacheprovider`.
Expected: FAIL (`ModuleNotFoundError`).

**Step 4: Write `types.py`.**

```python
"""The only shapes code above the adapters sees. Provider wire formats never leak past here.

Rules (SYSTEM.md inv-provider-tagged-meta): opaque provider data rides in `ProviderMeta` and is
replayed only to the provider that produced it (`replay.py`). Persisted form is `dump_parts`
JSON, versioned; bump TRANSCRIPT_VERSION and add a loader branch, never edit v1 in place.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Literal, Protocol, Union

TRANSCRIPT_VERSION = 1

Role = Literal["user", "assistant", "tool"]


@dataclass(frozen=True)
class ModelRef:
    provider: str  # "openai" | "anthropic" | "gemini" | "openrouter"
    model: str


@dataclass(frozen=True)
class ProviderMeta:
    provider: str
    model: str
    data: dict[str, Any]


@dataclass
class TextPart:
    text: str


@dataclass
class ImagePart:
    """In-memory only (one-shot OCR calls); never persisted (planning decision 4)."""

    data: bytes
    media_type: str = "image/png"


@dataclass
class ToolCallPart:
    id: str
    name: str
    arguments: dict[str, Any] | None  # None = the model sent arguments that were not JSON
    meta: ProviderMeta | None = None


@dataclass
class ToolResultPart:
    tool_call_id: str
    content: str
    is_error: bool = False


@dataclass
class ReasoningPart:
    text: str
    meta: ProviderMeta | None = None


Part = Union[TextPart, ImagePart, ToolCallPart, ToolResultPart, ReasoningPart]


@dataclass
class Message:
    role: Role
    parts: list[Part]

    @property
    def text(self) -> str:
        return "".join(p.text for p in self.parts if isinstance(p, TextPart))

    @property
    def tool_calls(self) -> list[ToolCallPart]:
        return [p for p in self.parts if isinstance(p, ToolCallPart)]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema object


@dataclass
class Request:
    model: ModelRef
    messages: list[Message]
    system: str | None = None
    tools: list[ToolSpec] = field(default_factory=list)
    output: Literal["text", "json"] | dict[str, Any] = "text"  # dict = JSON Schema
    max_tokens: int | None = None
    timeout: float | None = None  # per request, no SDK retries (llm._call_model contract)
    effort: str | None = None  # Claude output_config.effort; ignored elsewhere
    cache: bool = False  # ask the adapter to cache the stable prefix (system + tools)


@dataclass
class Usage:
    input: int
    output: int
    cache_read: int = 0
    cache_write: int = 0

    def as_langfuse(self) -> dict[str, int]:
        out = {"input": self.input, "output": self.output}
        if self.cache_read:
            out["cache_read"] = self.cache_read
        if self.cache_write:
            out["cache_write"] = self.cache_write
        return out


@dataclass
class Response:
    message: Message
    usage: Usage | None
    stop_reason: str
    model: str  # the model that actually answered (a Claude fallback can differ)

    @property
    def text(self) -> str:
        return self.message.text


@dataclass
class TextDelta:
    text: str


@dataclass
class Finish:
    message: Message
    usage: Usage | None
    stop_reason: str
    model: str


StreamEvent = Union[TextDelta, Finish]


class Provider(Protocol):
    name: str

    def ready(self) -> None:
        """Raise LLMProviderError (a user sentence) when this provider cannot be called."""

    def generate(self, req: Request) -> Response: ...

    def stream(self, req: Request) -> Iterator[StreamEvent]: ...

    def list_models(self) -> list[dict[str, str]]:
        """Rows {"id", "label", "provider"} for Settings › Find models."""


# --- persisted form -------------------------------------------------------------------------

def _meta_json(meta: ProviderMeta | None) -> dict[str, Any] | None:
    if meta is None:
        return None
    return {"provider": meta.provider, "model": meta.model, "data": meta.data}


def _meta_load(raw: dict[str, Any] | None) -> ProviderMeta | None:
    if not raw:
        return None
    return ProviderMeta(raw["provider"], raw["model"], dict(raw.get("data") or {}))


def dump_parts(parts: list[Part]) -> dict[str, Any]:
    out: list[dict[str, Any]] = []
    for p in parts:
        if isinstance(p, TextPart):
            out.append({"type": "text", "text": p.text})
        elif isinstance(p, ToolCallPart):
            out.append({"type": "tool_call", "id": p.id, "name": p.name,
                        "arguments": p.arguments, "meta": _meta_json(p.meta)})
        elif isinstance(p, ToolResultPart):
            out.append({"type": "tool_result", "tool_call_id": p.tool_call_id,
                        "content": p.content, "is_error": p.is_error})
        elif isinstance(p, ReasoningPart):
            out.append({"type": "reasoning", "text": p.text, "meta": _meta_json(p.meta)})
        else:
            raise ValueError(f"{type(p).__name__} is not persisted")
    return {"v": TRANSCRIPT_VERSION, "parts": out}


def load_parts(doc: dict[str, Any]) -> list[Part]:
    if doc.get("v") != TRANSCRIPT_VERSION:
        raise ValueError(f"unknown transcript version {doc.get('v')!r}")
    out: list[Part] = []
    for raw in doc.get("parts") or []:
        kind = raw.get("type")
        if kind == "text":
            out.append(TextPart(raw["text"]))
        elif kind == "tool_call":
            out.append(ToolCallPart(raw["id"], raw["name"], raw.get("arguments"),
                                    _meta_load(raw.get("meta"))))
        elif kind == "tool_result":
            out.append(ToolResultPart(raw["tool_call_id"], raw["content"],
                                      bool(raw.get("is_error"))))
        elif kind == "reasoning":
            out.append(ReasoningPart(raw.get("text", ""), _meta_load(raw.get("meta"))))
        # unknown part types from a newer build are skipped, not fatal
    return out
```

**Step 5: Write `registry.py` (skeleton; routing lands in Task 4).**

```python
"""Model id → provider, and provider name → adapter. The ONE place a model-id prefix is read."""

from __future__ import annotations

from app.services.llm_providers.types import ModelRef, Provider

PROVIDERS: tuple[str, ...] = ("openai", "anthropic", "gemini", "openrouter")

_adapters: dict[str, Provider] = {}


def register(provider: Provider) -> None:
    _adapters[provider.name] = provider


def get(name: str) -> Provider:
    try:
        return _adapters[name]
    except KeyError:
        _load_builtins()
        return _adapters[name]


def _load_builtins() -> None:
    """Import adapters lazily: they import `app.services.llm`, which imports this module."""
    # Filled in by Tasks 2, 3, 5, 6.


def resolve(model: str) -> ModelRef:
    """Task 4 replaces this with the catalog rule. Until then: today's prefix rule."""
    name = model.removeprefix("models/")
    if name.startswith("gemini-"):
        return ModelRef("gemini", model)
    return ModelRef("openai", model)
```

**Step 6: Write `backend/tests/fake_provider.py`.**

```python
"""A scripted Provider for chat/probe tests: each stream() call pops one script.

A script is a list of strings (text deltas) and at most one trailing list of ToolCallPart.
"""

from app.services.llm_providers import registry
from app.services.llm_providers.types import Finish, Message, Response, TextDelta, TextPart, Usage


class FakeProvider:
    def __init__(self, scripts, name="fake"):
        self.name = name
        self._scripts = list(scripts)
        self.requests = []

    def ready(self):
        return None

    def stream(self, req):
        self.requests.append(req)
        script = self._scripts.pop(0)
        parts = []
        for item in script:
            if isinstance(item, str):
                parts.append(TextPart(item))
                yield TextDelta(item)
            else:
                parts.extend(item)
        yield Finish(Message("assistant", parts), Usage(1, 1), "end_turn", req.model.model)

    def generate(self, req):
        *_, finish = list(self.stream(req))
        return Response(finish.message, finish.usage, finish.stop_reason, finish.model)

    def list_models(self):
        return []


def install(monkeypatch, provider, model="fake-model"):
    """Route `model` to `provider` for one test."""
    from app.services.llm_providers.types import ModelRef

    monkeypatch.setitem(registry._adapters, provider.name, provider)
    real = registry.resolve
    monkeypatch.setattr(registry, "resolve",
                        lambda m: ModelRef(provider.name, m) if m == model else real(m))
    return provider
```

**Step 7:** Run the type tests (PASS), ruff, the full suite (counts unchanged).

**Step 8: Commit** — `refactor(llm): neutral provider types, errors module, registry skeleton`.

---

### Task 2: OpenAI-compatible adapter — `generate` (move, no behavior change)

**Files:**
- Create: `backend/app/services/llm_providers/openai_compat.py`
- Modify: `backend/app/services/llm.py` (`_call_model` → registry dispatch; `completion_extras`,
  `_json_mode_supported`, `_extract_json_object`, `_get_client`, key getters STAY in `llm.py`)
- Modify: `backend/app/services/llm_providers/registry.py` (`_load_builtins`)
- Test: `backend/tests/test_llm_openai_compat.py`; existing `tests/test_llm.py`,
  `tests/test_llm_images.py` must pass unmodified.

`_get_client`, `_json_mode_supported` and the key getters stay in `llm.py` on purpose: tests
patch `llm._get_client` (≈18 sites), `llm.OpenAI`, `llm._client`, `llm.get_base_url`; the
adapter calls them through the module (`llm._get_client()`) at call time so those patches keep
working.

**Step 1: Failing builder tests** (pure functions; no client):

```python
# backend/tests/test_llm_openai_compat.py
from app.services.llm_providers import openai_compat as oc
from app.services.llm_providers.types import ImagePart, Message, ModelRef, Request, TextPart


def _req(**kw):
    base = dict(model=ModelRef("openai", "gpt-5.6-luna"),
                messages=[Message("user", [TextPart("hi")])])
    base.update(kw)
    return Request(**base)


def test_one_shot_text_is_a_single_user_message():
    kwargs = oc.build_generate_kwargs(_req(), json_mode=False)
    assert kwargs == {"model": "gpt-5.6-luna", "messages": [{"role": "user", "content": "hi"}]}


def test_json_output_sets_json_object_only_when_supported():
    assert oc.build_generate_kwargs(_req(output="json"), json_mode=True)["response_format"] == {"type": "json_object"}
    assert "response_format" not in oc.build_generate_kwargs(_req(output="json"), json_mode=False)


def test_images_become_data_url_parts():
    req = _req(messages=[Message("user", [TextPart("read"), ImagePart(b"\x89PNG")])])
    content = oc.build_generate_kwargs(req, json_mode=False)["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "read"}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
```

**Step 2:** Run → FAIL (module missing).

**Step 3: Write the adapter.** Move the body of today's `llm._call_model` OpenAI branch
(`llm.py:380-408`) into it:

```python
"""OpenAI chat.completions wire format: OpenAI, custom servers (Ollama, LM Studio, vLLM) and
OpenRouter (Task 6). Quirks for that wire format live here and in `llm.completion_extras`."""

from __future__ import annotations

import base64
from typing import Any, Callable

import openai

from app.services.llm_providers.errors import _no_answer, _openai_reason
from app.services.llm_providers.types import (
    ImagePart, Message, Request, Response, TextPart, Usage,
)


def _user_content(msg: Message) -> Any:
    images = [p for p in msg.parts if isinstance(p, ImagePart)]
    text = "".join(p.text for p in msg.parts if isinstance(p, TextPart))
    if not images:
        return text
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for img in images:
        b64 = base64.b64encode(img.data).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": f"data:{img.media_type};base64,{b64}"}})
    return content


def build_generate_kwargs(req: Request, *, json_mode: bool) -> dict[str, Any]:
    """One-shot call: today's exact request (a single user message, no system)."""
    kwargs: dict[str, Any] = {
        "model": req.model.model,
        "messages": [{"role": "user", "content": _user_content(req.messages[-1])}],
    }
    if req.output == "json" and json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    return kwargs


def _usage(response: Any) -> Usage | None:
    usage = getattr(response, "usage", None)
    i, o = getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None)
    return Usage(i, o) if isinstance(i, int) and isinstance(o, int) else None


class OpenAICompatProvider:
    def __init__(self, name: str, client: Callable[[], Any], json_mode: Callable[[], bool],
                 label: Callable[[], str], extra_body: dict[str, Any] | None = None):
        self.name = name
        self._client = client
        self._json_mode = json_mode
        self._label = label
        self._extra_body = extra_body

    def ready(self) -> None:
        self._client()  # raises LLMProviderError with the right sentence

    def generate(self, req: Request) -> Response:
        kwargs = build_generate_kwargs(req, json_mode=self._json_mode())
        if self._extra_body:
            kwargs["extra_body"] = self._extra_body
        client = self._client()
        if req.timeout is not None:
            client = client.with_options(timeout=req.timeout, max_retries=0)
        try:
            response = client.chat.completions.create(**kwargs)
        except openai.OpenAIError as exc:
            raise _no_answer(_openai_reason(exc), f"OpenAI API request failed: {exc}",
                             provider=self._label()) from exc
        text = response.choices[0].message.content or ""
        return Response(Message("assistant", [TextPart(text)]), _usage(response), "stop",
                        getattr(response, "model", None) or req.model.model)

    def stream(self, req: Request):  # Task 8
        raise NotImplementedError

    def list_models(self) -> list[dict[str, str]]:  # moved from llm.list_openai_models (Task 7)
        raise NotImplementedError
```

**Step 4: Register it and route `_call_model` through it.** In `registry._load_builtins`:

```python
    from app.services import llm
    from app.services.llm_providers.openai_compat import OpenAICompatProvider

    if "openai" not in _adapters:
        register(OpenAICompatProvider(
            "openai",
            client=lambda: llm._get_client(),
            json_mode=lambda: llm._json_mode_supported(),
            label=lambda: llm._openai_provider(),
        ))
```

Replace the OpenAI half of `llm._call_model` (keep the signature and the Gemini branch for now):

```python
    from app.services.llm_providers import registry
    from app.services.llm_providers.types import ImagePart, Message, Request, TextPart

    ref = registry.resolve(model)
    parts = [TextPart(prompt), *(ImagePart(img) for img in images or [])]
    response = registry.get(ref.provider).generate(Request(
        model=ref, messages=[Message("user", parts)], output=response_format, timeout=timeout,
    ))
    return response.text, response.usage.as_langfuse() if response.usage else None
```

**Step 5:** Run the new tests, `tests/test_llm.py`, `tests/test_llm_images.py`,
`tests/test_llm_call_logging.py`, `tests/test_llm_capabilities.py` — all PASS unmodified. If a
test patched `llm._call_model` internals it still works (the function and its signature stay).
Full suite: counts unchanged.

**Step 6: Commit** — `refactor(llm): OpenAI-compatible adapter behind the registry`.

---

### Task 3: Gemini adapter — `generate` + `list_models` (move, no behavior change)

**Files:**
- Create: `backend/app/services/llm_providers/gemini.py`
- Modify: `backend/app/services/llm.py` (remove `_call_gemini`, `_gemini_*`, `list_gemini_models`
  bodies; `list_gemini_models` becomes `return registry.get("gemini").list_models()`)
- Modify: `registry._load_builtins`, `_call_model` (drop the Gemini branch: registry dispatch)
- Modify tests that patch `llm.urlopen` / `llm._call_gemini` / `llm_module._call_gemini`
  (`tests/test_llm.py`, find others with `grep -rn "urlopen\|_call_gemini" backend/tests`):
  patch target → `app.services.llm_providers.gemini.urlopen`. Only the patch target changes.

**Step 1:** Move `_gemini_model_name`, `_is_gemini_model` (keep a copy-free re-export in llm if
any test imports it), `_gemini_thinking_level`, `_gemini_generation_config`, the request/usage/
text helpers and `list_gemini_models` into `gemini.py` as `GeminiProvider.generate` /
`list_models`. Key access stays `llm.get_gemini_key()` through the module at call time.
`generate` builds today's payload from `req.messages[-1]` (text + `ImagePart` → `inlineData`
with `img.media_type`). `ready()` raises `LLMProviderError(NO_GEMINI_KEY_MESSAGE)` without a key.

**Step 2:** Add `tests/test_llm_gemini.py` with a pure test of
`gemini.build_generate_payload(req)` equal to today's payload for text, JSON (with
`responseMimeType`) and a `gemini-3*` thinking level; plus image `inlineData`.

**Step 3:** Run `tests/test_llm.py` (updated patch targets), the new tests, the full suite.

**Step 4: Commit** — `refactor(llm): Gemini adapter behind the registry`.

---

### Task 4: Routing rule, four keys, settings API, setup status, env

First behavior change. After this task the backend knows about four providers' keys; only
`openai` and `gemini` adapters exist yet (Claude/OpenRouter models route correctly but their
adapters land in Tasks 5–6, so add them to `PROVIDERS` now and register them later).

**Files:**
- Modify: `backend/app/config.py:48-55` — `anthropic_api_key: str = ""`,
  `openrouter_api_key: str = ""` beside the other keys
- Modify: `backend/app/services/model_settings.py` — key table, `get_api_key`,
  `set_api_keys`, provider set, `_provider_for`, `catalog_provider`, `set_models`
- Modify: `backend/app/services/llm.py` — `get_anthropic_key`, `get_openrouter_key`
- Modify: `backend/app/services/llm_providers/registry.py` — `resolve`
- Modify: `backend/app/routers/settings.py:89-143, 453-539, 547-553`
- Modify: `backend/app/services/setup_status.py:179-182`
- Modify: `backend/app/main.py:95-98` (startup log names every key source)
- Modify: `docker-compose.yml:36-41`, `.env.example:29-55`
- Modify: `backend/tests/conftest.py` (blank the two new env keys like the others)
- Tests: `backend/tests/test_llm_routing.py` (new); update `test_settings_router.py`,
  `test_setup_status.py`, `test_model_catalog.py`, and every `set_models(` call in tests

**Step 1: Failing routing tests.**

```python
# backend/tests/test_llm_routing.py
import pytest

from app.services import llm, model_settings
from app.services.llm_providers import registry


@pytest.fixture
def keys(monkeypatch):
    present = {}
    monkeypatch.setattr(llm, "get_anthropic_key", lambda: present.get("anthropic"))
    monkeypatch.setattr(llm, "get_gemini_key", lambda: present.get("gemini"))
    monkeypatch.setattr(llm, "get_openrouter_key", lambda: present.get("openrouter"))
    return present


def test_catalog_tag_wins(db_session, keys):
    model_settings.add_extra_model(db_session, "anthropic/claude-opus-5-5", "openrouter")
    assert registry.resolve("anthropic/claude-opus-5-5").provider == "openrouter"


def test_claude_prefix_needs_a_claude_key(db_session, keys):
    assert registry.resolve("claude-custom-x").provider == "openai"
    keys["anthropic"] = "sk-ant-x"
    assert registry.resolve("claude-custom-x").provider == "anthropic"


def test_seeded_gemini_routes_to_gemini_even_without_key(db_session, keys):
    # Seed tag wins, so a missing key yields the Gemini "no key" sentence, as today.
    assert registry.resolve("gemini-3.7-flash").provider == "gemini"


def test_unknown_ids_go_to_the_openai_compatible_client(db_session, keys):
    assert registry.resolve("llama3.2:3b").provider == "openai"
```

**Step 2:** Run → FAIL.

**Step 3: `model_settings`.**

```python
API_KEY_SETTINGS = {
    "openai": "llm.openai_api_key",
    "gemini": "llm.gemini_api_key",
    "anthropic": "llm.anthropic_api_key",
    "openrouter": "llm.openrouter_api_key",
}
OPENAI_API_KEY_KEY = API_KEY_SETTINGS["openai"]   # kept: imported elsewhere
GEMINI_API_KEY_KEY = API_KEY_SETTINGS["gemini"]


def get_api_key(provider: str, session: Session | None = None) -> str | None:
    key = API_KEY_SETTINGS[provider]
    if session is None:
        with SessionLocal() as owned:
            return _get_raw_value(owned, key)
    return _get_raw_value(session, key)


def get_openai_api_key(session=None):  # thin wrappers keep existing callers
    return get_api_key("openai", session)


def get_gemini_api_key(session=None):
    return get_api_key("gemini", session)


def set_api_keys(session: Session, keys: dict[str, str | None]) -> None:
    """Patch: only the providers named in `keys` change; None or "" removes that key."""
    for provider, value in keys.items():
        _set_raw_value(session, API_KEY_SETTINGS[provider], (value or "").strip() or None)
```

- Replace both `{"openai", "gemini"}` checks (`_parse_extra_models`, `add_extra_model`) with
  `set(API_KEY_SETTINGS)`; error text: `"provider must be one of openai, gemini, anthropic, openrouter"`.
- `_provider_for`: `claude-` → `"anthropic"`, `gemini-` → `"gemini"`, else `"openai"` (label
  only; routing is the registry's).
- Add:

```python
def catalog_provider(model_id: str, session: Session | None = None) -> str | None:
    """Provider tag of a seeded or user-added model; None when nobody registered the id."""
    for opt in MODEL_OPTIONS:
        if opt.id == model_id:
            return opt.provider
    if session is None:
        with SessionLocal() as owned:
            extras = get_extra_models(owned)
    else:
        extras = get_extra_models(session)
    return next((opt.provider for opt in extras if opt.id == model_id), None)
```

- `set_models(fast_model, smart_model, session, chat_model=None) -> tuple[str, str]`: delete
  the two key parameters and their writes.
- Seed catalog (design §3): append to `MODEL_OPTIONS`
  `ModelOption("claude-haiku-5-5", "Claude Haiku 5.5", "anthropic", "fast")`,
  `ModelOption("claude-sonnet-5-5", "Claude Sonnet 5.5", "anthropic", "smart")`,
  `ModelOption("claude-opus-5-5", "Claude Opus 5.5", "anthropic", "smart")`, each with a
  one-line comment in the style of the existing seeds (listed, never assigned to a role —
  Goal Card "No auto-pick"). Rewrite `test_seed_catalog_is_exactly_the_three_in_use`
  (`tests/test_model_catalog.py:107`) to the six ids and rename it accordingly; config
  defaults (`gpt-5.6-luna`) do not change.

**Step 4: `llm.py` getters** (env fallback mirrors the existing two):

```python
def get_anthropic_key() -> str | None:
    from app.services import model_settings
    return model_settings.get_api_key("anthropic") or settings.anthropic_api_key or None


def get_openrouter_key() -> str | None:
    from app.services import model_settings
    return model_settings.get_api_key("openrouter") or settings.openrouter_api_key or None
```

**Step 5: `registry.resolve`.**

```python
def resolve(model: str) -> ModelRef:
    """1) catalog tag  2) claude-/gemini- prefix when that provider has a key  3) openai-compat."""
    from app.services import llm, model_settings

    tagged = model_settings.catalog_provider(model)
    if tagged:
        return ModelRef(tagged, model)
    name = model.removeprefix("models/")
    if name.startswith("claude-") and llm.get_anthropic_key():
        return ModelRef("anthropic", model)
    if name.startswith("gemini-") and llm.get_gemini_key():
        return ModelRef("gemini", model)
    return ModelRef("openai", model)
```

Known change (design §2): a `gemini-*` id that is neither seeded nor added, with no Gemini key,
now goes to the OpenAI-compatible client instead of raising "No Gemini API key". Seeded Gemini
ids are unchanged. Write it in the commit message.

**Step 6: Settings API.** In `routers/settings.py`:
- `OpenAIInfo` gains `anthropic_api_key_configured: bool`, `openrouter_api_key_configured: bool`,
  `anthropic_key_source`, `openrouter_key_source` (same `Literal`). Build all four with one
  helper:

```python
def _key_state(db: Session, provider: str, env_value: str) -> tuple[bool, str]:
    stored = model_settings.get_api_key(provider, db)
    return bool(stored or env_value), ("settings" if stored else "env" if env_value else "none")
```

- `ModelSettingsPayload` gains `anthropic_api_key`, `openrouter_api_key` (same tri-state).
- `put_openai_info`: delete the re-supply block (`settings.py:499-508`) and the key args to
  `set_models`; after the endpoint/json-mode writes call

```python
    sent_keys = {
        provider: getattr(payload, f"{provider}_api_key")
        for provider in ("openai", "gemini", "anthropic", "openrouter")
        if f"{provider}_api_key" in sent
    }
    if sent_keys:
        model_settings.set_api_keys(db, sent_keys)
```

  BEFORE `set_models` (a model change and a key in one PUT must both land).
- `SyncPayload.provider` and `ExtraModelPayload.provider`:
  `Literal["openai", "gemini", "anthropic", "openrouter"]` (sync dispatch arrives in Task 7;
  until then `anthropic`/`openrouter` sync returns 400 "Not available yet" — Task 7 removes it).

**Step 7: Setup status, startup log, env.**
- `setup_status.py`: `key_configured = model_settings.using_custom_endpoint(db) or any(
  model_settings.get_api_key(p, db) for p in API_KEY_SETTINGS) or any(getattr(app_settings,
  f"{p}_api_key") for p in API_KEY_SETTINGS)`. (A custom server needs no key — today's
  `_get_client` rule.) Add a test: custom endpoint alone marks the step done.
- `main.py`: log each provider's key source in one line.
- `docker-compose.yml`: `ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}` and
  `OPENROUTER_API_KEY: ${OPENROUTER_API_KEY:-}` (empty = unset; `config.scrub_empty_env`).
- `.env.example`: both keys, commented, one line each on where to get them.

**Step 8: Tests.** Update `test_settings_router.py` (new fields; a PUT with only
`anthropic_api_key` stores it and leaves the other three untouched; the response never contains
key material), `test_setup_status.py`, every `set_models(` call site in tests (drop key args;
use `set_api_keys`). Run the routing tests (PASS) and the full suite.

**Step 9: Commit** — `feat(llm): four provider keys and catalog-tag routing`.

---

### Task 5: `anthropic` dependency + Claude adapter `generate` / `list_models`

**Files:**
- Modify: `backend/pyproject.toml:20` — add `"anthropic>=1.12,<2"` (use the newest 1.x on PyPI
  on the day; record the version in the Deviation log)
- Regenerate: `backend/requirements.lock` (inside `python:3.12-slim`, Dockerfile recipe)
- Create: `backend/app/services/llm_providers/anthropic.py`
- Modify: `backend/app/services/llm.py` — `_get_anthropic_client()`
- Modify: `backend/app/services/llm_providers/errors.py` — `_STATUS_WORDS[529]`,
  `_anthropic_reason`
- Modify: `backend/app/services/llm_capabilities.py:134-152` — Anthropic unreachable signs
- Modify: `registry._load_builtins`
- Test: `backend/tests/test_llm_anthropic.py`

**Step 1: Client factory in `llm.py`.**

```python
ANTHROPIC_BASE_URL = "https://api.anthropic.com"  # explicit: never ANTHROPIC_BASE_URL from a shell

_anthropic_clients: dict[str, Any] = {}


def _get_anthropic_client():
    """Built with an explicit key AND base URL. A zero-argument client would pick up a user's
    ANTHROPIC_AUTH_TOKEN, `ant auth` profile or ANTHROPIC_BASE_URL from their shell (native
    installs inherit it) and bill or route somewhere the user never chose."""
    import anthropic

    key = get_anthropic_key()
    if not key:
        raise LLMProviderError(NO_ANTHROPIC_KEY_MESSAGE)
    client = _anthropic_clients.get(key)
    if client is None:
        client = anthropic.Anthropic(api_key=key, base_url=ANTHROPIC_BASE_URL)
        _anthropic_clients.clear()
        _anthropic_clients[key] = client
    return client
```

Add `NO_ANTHROPIC_KEY_MESSAGE = "No Claude API key is set. Add one in Settings › AI & models."`
to `errors.py` (re-export from `llm`).

**Step 2: Failing builder/parser tests** (no network; SDK objects faked with `SimpleNamespace`
or built from dicts via the SDK's types):

```python
# backend/tests/test_llm_anthropic.py
from types import SimpleNamespace

import pytest

from app.services.llm_providers import anthropic as ap
from app.services.llm_providers.errors import LLMProviderError
from app.services.llm_providers.types import (
    ImagePart, Message, ModelRef, ProviderMeta, ReasoningPart, Request, TextPart, ToolCallPart,
    ToolResultPart, ToolSpec,
)

REF = ModelRef("anthropic", "claude-opus-5-5")


def test_generate_kwargs_never_send_sampling_or_forced_tools():
    kw = ap.build_kwargs(Request(model=REF, messages=[Message("user", [TextPart("hi")])]), stream=False)
    for banned in ("temperature", "top_p", "top_k", "tool_choice"):
        assert banned not in kw
    assert kw["max_tokens"] == ap.GENERATE_MAX_TOKENS
    assert kw["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]


def test_fallbacks_only_on_models_that_accept_them():
    on = ap.build_kwargs(Request(model=REF, messages=[Message("user", [TextPart("x")])]), stream=False)
    assert on["fallbacks"] == "default" and on["betas"] == [ap.FALLBACK_BETA]
    off = ap.build_kwargs(Request(model=ModelRef("anthropic", "claude-haiku-5-5"),
                                  messages=[Message("user", [TextPart("x")])]), stream=False)
    assert "fallbacks" not in off and off["betas"] == []


def test_schema_output_and_effort_go_in_output_config():
    kw = ap.build_kwargs(Request(model=REF, messages=[Message("user", [TextPart("x")])],
                                 output={"type": "object"}, effort="low"), stream=False)
    assert kw["output_config"] == {"format": {"type": "json_schema", "schema": {"type": "object"}},
                                   "effort": "low"}


def test_cache_and_system_and_tools():
    req = Request(model=REF, system="SYS", cache=True,
                  tools=[ToolSpec("t", "d", {"type": "object", "properties": {}})],
                  messages=[Message("user", [TextPart("x")])])
    kw = ap.build_kwargs(req, stream=True)
    assert kw["system"] == [{"type": "text", "text": "SYS"}]
    assert kw["cache_control"] == {"type": "ephemeral"}
    assert kw["tools"] == [{"name": "t", "description": "d", "input_schema": {"type": "object", "properties": {}}}]
    assert kw["max_tokens"] == ap.STREAM_MAX_TOKENS


def test_history_translation_groups_tool_results_and_keeps_block_order():
    thinking = {"type": "thinking", "thinking": "", "signature": "sig"}
    msgs = [
        Message("user", [TextPart("go")]),
        Message("assistant", [ReasoningPart("", ProviderMeta("anthropic", "claude-opus-5-5", thinking)),
                              TextPart(""), ToolCallPart("a", "f", {"x": 1}), ToolCallPart("b", "g", {})]),
        Message("tool", [ToolResultPart("a", "1")]),
        Message("tool", [ToolResultPart("b", "2", is_error=True)]),
    ]
    out = ap.build_messages(msgs)
    assert out[1]["content"] == [thinking,
                                 {"type": "tool_use", "id": "a", "name": "f", "input": {"x": 1}},
                                 {"type": "tool_use", "id": "b", "name": "g", "input": {}}]
    assert out[2] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "a", "content": "1"},
        {"type": "tool_result", "tool_use_id": "b", "content": "2", "is_error": True}]}


def test_images_are_base64_blocks():
    out = ap.build_messages([Message("user", [TextPart("read"), ImagePart(b"\x89PNG")])])
    assert out[0]["content"][1]["type"] == "image"
    assert out[0]["content"][1]["source"]["media_type"] == "image/png"


def _block(**kw):
    return SimpleNamespace(model_dump=lambda **_: kw, **kw)


def test_parts_from_message_drops_pre_fallback_non_text():
    msg = SimpleNamespace(model="claude-opus-4-8", content=[
        _block(type="thinking", thinking="", signature="old"),
        _block(type="text", text="partial "),
        _block(type="fallback", **{"from": {"model": "claude-opus-5-5"}}, to={"model": "claude-opus-4-8"}),
        _block(type="thinking", thinking="", signature="new"),
        _block(type="text", text="answer"),
    ])
    parts = ap.parts_from_message(msg)
    assert [type(p).__name__ for p in parts] == ["TextPart", "ReasoningPart", "TextPart"]
    assert parts[1].meta.data["signature"] == "new" and parts[1].meta.model == "claude-opus-4-8"


def test_refusal_is_a_plain_sentence():
    msg = SimpleNamespace(stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"))
    with pytest.raises(LLMProviderError) as exc:
        ap.raise_for_refusal(msg)
    assert "Claude declined" in str(exc.value)
    assert "cyber" in exc.value.provider_detail
```

**Step 3:** Run → FAIL.

**Step 4: Write the adapter.**

```python
"""Claude through the official `anthropic` SDK (1.x). Every Claude-specific rule lives here.

- Never send temperature/top_p/top_k or a forced tool_choice: current Claude models 400 on them.
- Thinking is adaptive (the default on Claude 5 models); effort only when a caller asks.
- Thinking blocks are replayed unchanged (meta.data is the raw block); removing them can 400.
- Refusals: server-side `fallbacks: "default"` on models that accept it; a final refusal is a
  plain sentence. After a mid-output fallback, only text before the last `fallback` block is
  kept (Anthropic's echo rule).
"""

from __future__ import annotations

import base64
from typing import Any, Iterator

from app.services.llm_providers.errors import (
    LLMProviderError, _KEY_REFUSED, _TIMED_OUT, _no_answer, _status_reason,
)
from app.services.llm_providers.types import (
    Finish, ImagePart, Message, ProviderMeta, ReasoningPart, Request, Response, TextDelta,
    TextPart, ToolCallPart, ToolResultPart, Usage,
)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models documented to take `fallbacks: "default"` on the Claude API (claude-api skill, 2026-10).
# Haiku 5.5 has no server-side fallback. Unknown ids get none rather than a 400.
FALLBACK_MODELS = frozenset({"claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5", "claude-fable-5-1"})
GENERATE_MAX_TOKENS = 32_000
STREAM_MAX_TOKENS = 64_000
REFUSED = ("Claude declined to answer this request. Try rephrasing it, or pick another model "
           "in Settings › AI & models.")


def _user_blocks(msg: Message) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for p in msg.parts:
        if isinstance(p, TextPart) and p.text:
            blocks.append({"type": "text", "text": p.text})
        elif isinstance(p, ImagePart):
            blocks.append({"type": "image", "source": {
                "type": "base64", "media_type": p.media_type,
                "data": base64.b64encode(p.data).decode("ascii")}})
    return blocks


def _assistant_blocks(msg: Message) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for p in msg.parts:
        if isinstance(p, ReasoningPart) and p.meta and p.meta.provider == "anthropic":
            blocks.append(p.meta.data)
        elif isinstance(p, TextPart) and p.text:
            blocks.append({"type": "text", "text": p.text})
        elif isinstance(p, ToolCallPart):
            blocks.append({"type": "tool_use", "id": p.id, "name": p.name,
                           "input": p.arguments or {}})
    return blocks


def build_messages(messages: list[Message]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in messages:
        if msg.role == "tool":
            results = []
            for p in msg.parts:
                if isinstance(p, ToolResultPart):
                    block = {"type": "tool_result", "tool_use_id": p.tool_call_id, "content": p.content}
                    if p.is_error:
                        block["is_error"] = True
                    results.append(block)
            prev = out[-1] if out else None
            if prev and prev["role"] == "user" and prev.get("_tool_results"):
                prev["content"].extend(results)
            else:
                out.append({"role": "user", "content": results, "_tool_results": True})
            continue
        blocks = _user_blocks(msg) if msg.role == "user" else _assistant_blocks(msg)
        if blocks:
            out.append({"role": msg.role, "content": blocks})
    for entry in out:
        entry.pop("_tool_results", None)
    return out


def build_kwargs(req: Request, *, stream: bool) -> dict[str, Any]:
    kw: dict[str, Any] = {
        "model": req.model.model,
        "max_tokens": req.max_tokens or (STREAM_MAX_TOKENS if stream else GENERATE_MAX_TOKENS),
        "messages": build_messages(req.messages),
        "betas": [],
    }
    if req.system:
        kw["system"] = [{"type": "text", "text": req.system}]
    if req.tools:
        kw["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.parameters}
                       for t in req.tools]
    if req.cache:
        kw["cache_control"] = {"type": "ephemeral"}
    output_config: dict[str, Any] = {}
    if isinstance(req.output, dict):
        output_config["format"] = {"type": "json_schema", "schema": req.output}
    if req.effort:
        output_config["effort"] = req.effort
    if output_config:
        kw["output_config"] = output_config
    if req.model.model in FALLBACK_MODELS:
        kw["fallbacks"] = "default"
        kw["betas"] = [FALLBACK_BETA]
    return kw


def parts_from_message(msg: Any) -> list[Any]:
    blocks = [b.model_dump(mode="json", exclude_none=True) for b in msg.content]
    last_fallback = max((i for i, b in enumerate(blocks) if b.get("type") == "fallback"), default=-1)
    parts: list[Any] = []
    for i, b in enumerate(blocks):
        kind = b.get("type")
        if kind == "fallback" or (i < last_fallback and kind != "text"):
            continue
        if kind == "text":
            parts.append(TextPart(b.get("text", "")))
        elif kind in ("thinking", "redacted_thinking"):
            parts.append(ReasoningPart(b.get("thinking", ""), ProviderMeta("anthropic", msg.model, b)))
        elif kind == "tool_use":
            parts.append(ToolCallPart(b["id"], b["name"], b.get("input") or {}))
    return parts


def raise_for_refusal(msg: Any) -> None:
    if getattr(msg, "stop_reason", None) == "refusal":
        details = getattr(msg, "stop_details", None)
        raise LLMProviderError(REFUSED, provider_detail=f"Claude refusal: {details!r}")


def _usage(msg: Any) -> Usage | None:
    u = getattr(msg, "usage", None)
    if u is None or not isinstance(getattr(u, "input_tokens", None), int):
        return None
    return Usage(u.input_tokens, u.output_tokens,
                 getattr(u, "cache_read_input_tokens", 0) or 0,
                 getattr(u, "cache_creation_input_tokens", 0) or 0)


def _strip_reasoning(req: Request) -> Request:
    from dataclasses import replace
    return replace(req, messages=[
        Message(m.role, [p for p in m.parts if not isinstance(p, ReasoningPart)]) for m in req.messages
    ])


class AnthropicProvider:
    name = "anthropic"

    def ready(self) -> None:
        from app.services import llm
        llm._get_anthropic_client()

    def _client(self, req: Request):
        from app.services import llm
        client = llm._get_anthropic_client()
        if req.timeout is not None:
            client = client.with_options(timeout=req.timeout, max_retries=0)
        return client

    def _call(self, req: Request, on_text=None) -> Any:
        import anthropic

        def once(r: Request) -> Any:
            with self._client(r).beta.messages.stream(**build_kwargs(r, stream=on_text is not None)) as s:
                for event in s:
                    if on_text is not None and event.type == "text":
                        on_text(event.text)
                return s.get_final_message()

        try:
            try:
                return once(req)
            except anthropic.BadRequestError as exc:
                # Planning decision 5: a thinking signature from another account/key.
                text = str(exc).lower()
                if "signature" in text or "thinking" in text:
                    return once(_strip_reasoning(req))
                raise
        except anthropic.APIStatusError as exc:
            raise _no_answer(_status_reason(exc.status_code), f"Anthropic API request failed: {exc}",
                             provider="Claude") from exc
        except anthropic.APITimeoutError as exc:
            raise _no_answer(_TIMED_OUT, f"Anthropic API request timed out: {exc}", provider="Claude") from exc
        except anthropic.APIConnectionError as exc:
            raise _no_answer("no connection", f"Anthropic API connection failed: {exc}", provider="Claude") from exc

    def generate(self, req: Request) -> Response:
        msg = self._call(req)
        raise_for_refusal(msg)
        return Response(Message("assistant", parts_from_message(msg)), _usage(msg),
                        msg.stop_reason or "end_turn", msg.model)

    def stream(self, req: Request) -> Iterator[Any]:  # Task 9
        raise NotImplementedError

    def list_models(self) -> list[dict[str, str]]:
        from app.services import llm
        rows = []
        for m in llm._get_anthropic_client().models.list():
            rows.append({"id": m.id, "label": getattr(m, "display_name", None) or m.id,
                         "provider": "anthropic"})
        return sorted(rows, key=lambda r: r["id"])
```

`generate` uses the stream helper internally (no `on_text`) so long outputs never hit HTTP
timeouts. If the installed SDK's `beta.messages.stream` rejects a keyword (`fallbacks`,
`cache_control`, `output_config`) as unknown, pass that field through `extra_body` instead,
keep `build_kwargs`' tests on the wire shape, and log it. In `errors.py` add `529: "the provider is overloaded"` to `_STATUS_WORDS`. Register
`AnthropicProvider()` in `_load_builtins`.

**Step 5: Probe signs.** Add Anthropic's error vocabulary to
`llm_capabilities._UNREACHABLE_SIGNS`: `"authentication_error"`, `"permission_error"`,
`"rate_limit_error"`, `"overloaded_error"`, `"error code: 529"`, `"no claude api key"`. Test:
an `LLMProviderError` whose `provider_detail` contains `authentication_error` is unreachable.

**Step 6:** Run new tests, `tests/test_llm*.py`, full suite in the CI-style venv
(dependency change). Regenerate the lock in Docker; diff shows `anthropic`, `httpx2` and their
deps only. Startup time check: `python -X importtime -c "import app.main" 2>&1 | grep -i
anthropic` — record; `anthropic` is imported lazily inside functions so it must not appear.

**Step 7: Commit** — `feat(llm): native Claude adapter (anthropic SDK) for one-shot calls`.

---

### Task 6: OpenRouter adapter instance

**Files:**
- Modify: `backend/app/services/llm.py` — `OPENROUTER_BASE_URL`, `_get_openrouter_client()`
- Modify: `registry._load_builtins`
- Test: `backend/tests/test_llm_openrouter.py`

**Step 1:**

```python
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"  # code-owned; the key goes nowhere else
# Forced on every OpenRouter request; not a setting (design §2, PRIVACY.md).
OPENROUTER_PRIVACY = {"provider": {"data_collection": "deny", "zdr": True}}
NO_OPENROUTER_KEY_MESSAGE = "No OpenRouter API key is set. Add one in Settings › AI & models."


def _get_openrouter_client() -> OpenAI:
    key = get_openrouter_key()
    if not key:
        raise LLMProviderError(NO_OPENROUTER_KEY_MESSAGE)
    cached = _chat_clients.get(("openrouter", key))
    if cached is None:
        cached = OpenAI(api_key=key, base_url=OPENROUTER_BASE_URL)
        _chat_clients[("openrouter", key)] = cached
    return cached
```

Register `OpenAICompatProvider("openrouter", client=lambda: llm._get_openrouter_client(),
json_mode=lambda: True, label=lambda: "OpenRouter", extra_body=llm.OPENROUTER_PRIVACY)`.
No attribution headers (`HTTP-Referer`, `X-Title`).

**Step 2: Tests:** the request a fake client receives carries
`extra_body == {"provider": {"data_collection": "deny", "zdr": True}}`; the client's base URL is
`OPENROUTER_BASE_URL`; with no key, `ready()` raises the OpenRouter sentence. An OpenRouter 404
whose body mentions no endpoints / data policy maps to: `"OpenRouter has no zero-retention
provider for this model. Pick another model."` — implement as a provider-specific branch in
`OpenAICompatProvider.generate` keyed on `self.name == "openrouter"` and status 404; record the
exact OpenRouter error text you matched on in the Deviation log after the live check (Task 14).

**Step 3:** Run, full suite. **Commit** — `feat(llm): OpenRouter preset with forced zero-retention routing`.

---

### Task 7: Catalog sync and extra models for four providers; probe through the registry

**Files:**
- Modify: `backend/app/services/llm_providers/openai_compat.py` — `list_models` (move
  `llm.list_openai_models` body + `_OPENAI_SKIP_FRAGMENTS`; provider tag = `self.name`)
- Modify: `backend/app/services/llm.py` — `list_openai_models`/`list_gemini_models` become
  one-line delegations (tests call them)
- Modify: `backend/app/routers/settings.py:566-578` — `sync_provider_models` dispatches
  `registry.get(payload.provider).list_models()`; remove Task 4's "Not available yet"
- Modify: `backend/app/services/llm_capabilities.py:98-123` — `_probe_tools` through the adapter
- Tests: `tests/test_model_catalog_api.py` (sync for anthropic/openrouter with a stub adapter),
  `tests/test_llm_capabilities.py` (rewrite the tools-probe tests to `FakeProvider`)

**Step 1: Tools probe.** Replace `_probe_tools` with the same call the chat agent will make
(Task 12): `stream` with tools, through the registry.

```python
_PROBE_SPEC = ToolSpec("report_ok", "Report that the check succeeded.",
                       {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]})


def _probe_tools(model: str) -> None:
    """Tool calling AND streaming, through the same adapter.stream call `chat_agent.run_turn`
    makes. A probe of a call the app never makes measures nothing (SYSTEM.md §12)."""
    ref = registry.resolve(model)
    request = Request(model=ref, messages=[Message("user", [TextPart("Call report_ok with ok=true.")])],
                      tools=[_PROBE_SPEC])
    for event in registry.get(ref.provider).stream(request):
        if isinstance(event, Finish) and event.message.tool_calls:
            return
    raise RuntimeError("model streamed no tool call")
```

This depends on `stream` (Tasks 8–10). Until those land, keep the old `_probe_tools` body
behind `if ref.provider in {"openai", "gemini"}` and switch in Task 12 Step 6 — note the
temporary branch in the Deviation log so the reviewer checks it is gone.

**Step 2: OpenRouter discovery filter.** `OpenAICompatProvider.list_models` for `openrouter`
lists `/models`. If OpenRouter documents an endpoint listing ZDR-capable endpoints (research §4
names its ZDR docs), filter to models that have one; if not, list all and rely on Task 6's
sentence. Record which in the Deviation log.

**Step 3:** Tests: sync each provider with a stub adapter registered via `monkeypatch.setitem(
registry._adapters, ...)`; `add_extra_model(..., "anthropic")` accepted; the tools probe passes
with a `FakeProvider` script that returns a `ToolCallPart` and fails with text only.

**Step 4:** Full suite. **Commit** — `feat(llm): model discovery and tools probe for every provider`.

---

### Task 8: OpenAI-compatible `stream` (+ OpenRouter `reasoning_details`)

**Files:**
- Modify: `backend/app/services/llm_providers/openai_compat.py`
- Modify: `backend/app/services/llm.py` — keep `completion_extras` where it is (the adapter
  calls `llm.completion_extras(model)`; `test_chat_agent.py:218-229` asserts it)
- Test: `backend/tests/test_llm_openai_compat.py` (add)

**Step 1: Failing tests** for `build_chat_kwargs(req)` and `accumulate(chunks, ref)`:
- system → first `{"role": "system"}`; user text; assistant with text `None` + `tool_calls`
  (`arguments` = `json.dumps(args or {})`); tool → `{"role": "tool", "tool_call_id", "content"}`;
  tools → `{"type": "function", "function": {name, description, parameters}}`;
  `stream=True`; `completion_extras` merged (gpt-5.6 → `reasoning_effort: "none"`).
- accumulate: text deltas → `TextDelta`s; tool-call deltas across chunks join into one
  `ToolCallPart` with parsed arguments; unparseable arguments → `arguments=None`.
- OpenRouter: chunks whose delta carries `reasoning_details` (read via
  `getattr(delta, "reasoning_details", None) or (getattr(delta, "model_extra", None) or {}).get("reasoning_details")`)
  accumulate into ONE `ReasoningPart(text="", meta=ProviderMeta("openrouter", model,
  {"reasoning_details": [...]}))` placed first in the message; on replay to `openrouter`,
  the assistant message carries `"reasoning_details": [...]`. Other providers never emit or
  send it.

**Step 2:** Implement `stream(req)`: `client.chat.completions.create(**build_chat_kwargs(req),
**({"extra_body": self._extra_body} if self._extra_body else {}))`, yield `TextDelta`s, then one
`Finish` (usage from the last chunk when `stream_options` is not set is `None` — acceptable;
do not add `stream_options`, custom servers reject it). Errors → `_no_answer` as in `generate`.

**Step 3:** Tests PASS, full suite. **Commit** — `feat(llm): streaming chat for OpenAI-compatible providers`.

---

### Task 9: Claude `stream`

**Files:** `backend/app/services/llm_providers/anthropic.py`; `backend/tests/test_llm_anthropic.py`.

**Step 1:** Implement using `_call` with a text callback turned into a generator. The SDK
stream is synchronous, so iterate inline rather than via callback:

```python
    def stream(self, req: Request) -> Iterator[Any]:
        import anthropic

        def run(r: Request):
            with self._client(r).beta.messages.stream(**build_kwargs(r, stream=True)) as s:
                for event in s:
                    if event.type == "text":
                        yield TextDelta(event.text)
                msg = s.get_final_message()
            raise_for_refusal(msg)
            yield Finish(Message("assistant", parts_from_message(msg)), _usage(msg),
                         msg.stop_reason or "end_turn", msg.model)

        try:
            try:
                yield from run(req)
            except anthropic.BadRequestError as exc:
                text = str(exc).lower()
                if "signature" not in text and "thinking" not in text:
                    raise
                yield from run(_strip_reasoning(req))
        except anthropic.APIStatusError as exc:
            ...same mapping as _call...
```

Refactor so `generate` and `stream` share the error mapping (one private context manager or
helper); no duplicated except-ladders. A 400 can only arrive before the first event, so the
retry never duplicates deltas — assert that in a test with a fake client whose first
`stream()` raises `BadRequestError("...signature...")`.

**Step 2: Tests** with a fake client whose `beta.messages.stream(**kw)` returns a context
manager yielding `SimpleNamespace(type="text", text=...)` events and a final message: deltas in
order; `Finish.message` parts ordered thinking → text → tool_use; refusal raises after deltas;
the signature retry sends a request with no thinking blocks.

**Step 3:** Full suite. **Commit** — `feat(llm): streaming chat for Claude`.

---

### Task 10: Gemini native `stream`

**Files:** `backend/app/services/llm_providers/gemini.py`; `backend/tests/test_llm_gemini.py`.

Chat leaves Google's OpenAI-compat URL. Endpoint:
`POST https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse`
with `x-goog-api-key`.

**Step 1: Failing tests** for `build_chat_payload(req)` and `parse_sse(lines, ref)`:
- `systemInstruction: {"parts": [{"text": system}]}`; tools →
  `[{"functionDeclarations": [{"name", "description", "parametersJsonSchema": parameters}]}]`
  (full JSON Schema field; if the live check in Task 14 shows it rejected, switch to
  `parameters` and log it); thinking level as in `generate`.
- contents: user → `{"role": "user", "parts": [{"text"}]}`; assistant → `{"role": "model",
  "parts": [{"text"}…, {"functionCall": {"name", "args", "id"}, "thoughtSignature"?}]}`; tool
  messages (consecutive ones merged) → `{"role": "user", "parts": [{"functionResponse":
  {"name": <name of the call with that id>, "id": id, "response": <dict>}}]}` where `response`
  is `json.loads(content)` when that is a dict, else `{"result": <parsed or raw>}`.
- Signatures: a `ToolCallPart` whose meta is `gemini` with the same model → its
  `thoughtSignature`. In the CURRENT turn (after the last user message that has text), the
  first `functionCall` of each model step that has no usable signature gets
  `"thoughtSignature": "skip_thought_signature_validator"` (Google's documented dummy).
  Earlier turns: no dummy.
- parse_sse: `data: {...}` lines; text parts (skip parts with `"thought": true`) →
  `TextDelta`; `functionCall` parts → `ToolCallPart(id=fc.get("id") or f"call_{n}", name,
  args, meta=ProviderMeta("gemini", model, {"thoughtSignature": sig}) if sig)`; last
  `usageMetadata` → `Usage`; `finishReason` → stop reason.

**Step 2:** Implement `stream` with `urlopen` reading the response line by line; HTTP errors
map through `_gemini_reason` exactly as `generate`.

**Step 3:** Tests PASS, full suite. **Commit** — `feat(llm): native Gemini streaming chat with thought signatures`.

---

### Task 11: Transcript migration and codec

**Files:**
- Create: `backend/migrations/versions/<new>_chat_message_parts.py` (down_revision
  `559244b575bc`; confirm with `cd backend && alembic heads`)
- Modify: `backend/app/models/chat.py:58-63` — `parts: Mapped[dict | None] = mapped_column(JSONDoc)`,
  `provider: Mapped[str | None] = mapped_column(Text)`, `model: Mapped[str | None] = mapped_column(Text)`
- Create: `backend/app/services/transcript.py` — `from_legacy(role, content, tool_calls,
  tool_call_id) -> list[Part]` and `history(session_row) -> list[Message]`
- Test: `backend/tests/test_transcript.py`; `tests/test_migration_model_parity.py` must pass

**Step 1: Failing tests** for `from_legacy`:
- `("user", "hi", None, None)` → `[TextPart("hi")]`
- `("assistant", "ok", None, None)` → `[TextPart("ok")]`; empty content → `[]`
- `("assistant", None, [{"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{\"a\": 1}"}}], None)`
  → `[ToolCallPart("c1", "f", {"a": 1})]`; bad JSON arguments → `arguments=None`
- `("tool", '{"x": 1}', None, "c1")` → `[ToolResultPart("c1", '{"x": 1}')]`
- `history()` prefers `row.parts` and falls back to `from_legacy` when `parts` is NULL.

**Step 2: Migration.** Add the three columns with `batch_alter_table`. Backfill in Python with
`op.get_bind()` and a lightweight `sa.table("chat_messages", sa.column("id", sa.Uuid()),
sa.column("role", sa.Text()), sa.column("content", sa.Text()), sa.column("tool_calls", sa.JSON()),
sa.column("tool_call_id", sa.Text()), sa.column("parts", sa.JSON()))`. The conversion is an
inline copy of `from_legacy` that emits the v1 JSON directly (a revision never imports app
code — SYSTEM.md §12). Keep both copies equal with a test that runs the migration's function
and `dump_parts(from_legacy(...))` over the same fixtures. Downgrade drops the three columns.

**Step 3:** Run `test_transcript.py`, the parity test, and an upgrade/downgrade/upgrade cycle
on a scratch SQLite file (`DATA_DIR=$SP/data alembic upgrade head && alembic downgrade -1 &&
alembic upgrade head`) seeded with one chat session holding user, assistant-with-tool-calls,
tool and assistant rows; assert `parts` after upgrade.

**Step 4: Commit** — `feat(chat): neutral transcript parts on chat messages (lossless backfill)`.

---

### Task 12: `replay.py` and the chat agent on the adapters

**Files:**
- Create: `backend/app/services/llm_providers/replay.py`
- Modify: `backend/app/services/chat_agent.py` (rewrite `run_turn`; delete `_replay_messages`,
  `_accumulate_stream`)
- Modify: `backend/app/routers/chat.py:31, 105-116` (readiness check + `provider=`)
- Modify: `backend/app/services/llm.py` — delete `get_chat_client`, `_chat_clients` Gemini
  entries, `GEMINI_OPENAI_COMPAT_URL` (no callers remain; grep first)
- Modify: `backend/app/services/llm_capabilities.py` — remove Task 7's temporary branch
- Tests: `backend/tests/test_replay.py` (new); rewrite `tests/test_chat_agent.py` onto
  `FakeProvider`; update `tests/test_chat_router.py`

**Step 1: Failing replay tests.**

```python
# backend/tests/test_replay.py
from app.services.llm_providers import replay
from app.services.llm_providers.types import (
    Message, ModelRef, ProviderMeta, ReasoningPart, TextPart, ToolCallPart, ToolResultPart,
)

CLAUDE = ModelRef("anthropic", "claude-sonnet-5-5")
GPT = ModelRef("openai", "gpt-5.6-luna")


def _thinking(model="claude-opus-5-5"):
    return ReasoningPart("", ProviderMeta("anthropic", model, {"type": "thinking", "signature": "s"}))


def test_foreign_reasoning_is_dropped_and_own_kept():
    msgs = [Message("user", [TextPart("q")]), Message("assistant", [_thinking(), TextPart("a")])]
    assert isinstance(replay.for_provider(msgs, CLAUDE)[1].parts[0], ReasoningPart)  # same provider, other Claude model
    assert [type(p) for p in replay.for_provider(msgs, GPT)[1].parts] == [TextPart]


def test_foreign_tool_call_meta_is_stripped():
    call = ToolCallPart("c1", "f", {}, ProviderMeta("gemini", "gemini-3.7-flash", {"thoughtSignature": "x"}))
    out = replay.for_provider([Message("assistant", [call])], CLAUDE)
    assert out[0].parts[0].meta is None


def test_ids_are_normalized_consistently():
    msgs = [Message("assistant", [ToolCallPart("call.1/x", "f", {})]),
            Message("tool", [ToolResultPart("call.1/x", "r")])]
    out = replay.for_provider(msgs, CLAUDE)
    assert out[0].parts[0].id == out[1].parts[0].tool_call_id == "call_1_x"


def test_missing_ids_are_generated():
    out = replay.for_provider([Message("assistant", [ToolCallPart("", "f", {})])], CLAUDE)
    assert out[0].parts[0].id


def test_unanswered_call_gets_an_error_result_and_orphan_result_is_dropped():
    msgs = [Message("assistant", [ToolCallPart("a", "f", {})]),
            Message("tool", [ToolResultPart("zzz", "orphan")]),
            Message("user", [TextPart("next")])]
    out = replay.for_provider(msgs, CLAUDE)
    assert [m.role for m in out] == ["assistant", "tool", "user"]
    assert out[1].parts[0].tool_call_id == "a" and out[1].parts[0].is_error


def test_input_is_not_mutated():
    msgs = [Message("assistant", [ToolCallPart("a.b", "f", {})])]
    replay.for_provider(msgs, CLAUDE)
    assert msgs[0].parts[0].id == "a.b"
```

**Step 2: Write `replay.py`.**

```python
"""Neutral history → what one target provider may see. Pure; never mutates its input.

inv-provider-tagged-meta: opaque provider data (ProviderMeta) reaches only the provider that
produced it. Adapters refine (Gemini replaces another model's signature with Google's dummy).
"""

from __future__ import annotations

import re
from dataclasses import replace

from app.services.llm_providers.types import (
    Message, ModelRef, ReasoningPart, ToolCallPart, ToolResultPart,
)

_ID_OK = re.compile(r"[^A-Za-z0-9_-]")
UNANSWERED = '{"error": "This tool was not run."}'


def _norm(raw: str, n: int, seen: dict[str, str]) -> str:
    if not raw:
        # Never cached: two id-less calls must not share an id. Their results cannot be
        # matched either, so each call gets a synthetic error result (close_open).
        return f"call_{n}"
    if raw in seen:
        return seen[raw]
    new = _ID_OK.sub("_", raw)
    seen[raw] = new
    return new


def for_provider(messages: list[Message], target: ModelRef) -> list[Message]:
    ids: dict[str, str] = {}
    out: list[Message] = []
    open_calls: list[str] = []  # call ids of the latest assistant message still unanswered

    def close_open() -> None:
        if open_calls:
            out.append(Message("tool", [ToolResultPart(cid, UNANSWERED, is_error=True)
                                        for cid in open_calls]))
            open_calls.clear()

    for n, msg in enumerate(messages):
        parts = []
        for k, p in enumerate(msg.parts):
            if isinstance(p, ReasoningPart):
                if p.meta is None or p.meta.provider != target.provider:
                    continue
                parts.append(p)
            elif isinstance(p, ToolCallPart):
                meta = p.meta if p.meta and p.meta.provider == target.provider else None
                parts.append(replace(p, id=_norm(p.id, n * 100 + k, ids), meta=meta))
            elif isinstance(p, ToolResultPart):
                parts.append(replace(p, tool_call_id=ids.get(p.tool_call_id, "\0orphan")))
            else:
                parts.append(p)
        if msg.role == "tool":
            kept = [p for p in parts if isinstance(p, ToolResultPart) and p.tool_call_id in open_calls]
            for p in kept:
                open_calls.remove(p.tool_call_id)
            if kept:
                out.append(Message("tool", kept))
            continue
        close_open()
        if msg.role == "assistant":
            open_calls.extend(p.id for p in parts if isinstance(p, ToolCallPart))
        out.append(Message(msg.role, parts))
    # A trailing unanswered call is left open: the caller is about to run it.
    return out
```

Note the trailing-call rule: inside a live turn the loop appends the assistant message, runs
tools, appends results, then calls the model again — so at request time every call is
answered. Persisted history from an errored turn gets repaired when the NEXT user message
follows it (`close_open` runs on that user message). Add a test for the trailing case.

**Step 3: Rewrite `run_turn`** (keep the event contract in the module docstring and every
event name; update the docstring's second paragraph: chat runs on any provider through
`llm_providers`).

```python
def run_turn(db, session_row, user_content, context=None, *, provider=None, model=None):
    model = model or model_settings.get_chat_model(db)
    ref = registry.resolve(model)
    provider = provider or registry.get(ref.provider)

    user_parts = [TextPart(user_content)]
    user_msg = _persist(db, session_row, role="user", content=user_content,
                        meta_json=context or None, parts=dump_parts(user_parts))
    if not session_row.title:
        session_row.title = user_content.strip()[:60] or "New chat"
    db.commit()
    yield {"type": "message", "id": str(user_msg.id), "role": "user"}

    history = transcript.history(session_row)
    context_block = _context_block(db, context)
    if context_block:
        # Ephemeral (planning decision 3): rides on THIS turn's user message, never persisted.
        last = history[-1]
        history[-1] = Message(last.role, [TextPart(context_block + "\n\n"), *last.parts])

    system = _system_prompt(db)
    tools = [ToolSpec(s["name"], s["description"], s["parameters"]) for s in TOOL_SPECS]
    ctx = ToolContext(db=db, message_id=str(user_msg.id),
                      selections=(context or {}).get("selections") or [])

    try:
        for _ in range(MAX_TOOL_ROUNDS):
            request = Request(model=ref, system=system, tools=tools, cache=True,
                              messages=replay.for_provider(history, ref))
            finish = None
            with tracing.generation("chat-step", model=model, prompt=user_content,
                                    metadata={"provider": ref.provider}) as record:
                for event in provider.stream(request):
                    if isinstance(event, TextDelta):
                        yield {"type": "delta", "text": event.text}
                    else:
                        finish = event
                record(finish.message.text if finish else "",
                       finish.usage.as_langfuse() if finish and finish.usage else None)
            reply = finish.message
            calls = reply.tool_calls
            assistant = _persist(db, session_row, role="assistant",
                                 content=reply.text if not calls else (reply.text or None),
                                 parts=dump_parts(reply.parts), provider=ref.provider,
                                 model=finish.model)
            db.commit()
            history.append(reply)
            if not calls:
                yield {"type": "message", "id": str(assistant.id), "role": "assistant"}
                yield {"type": "done", "session_id": str(session_row.id)}
                return

            for call in calls:
                yield {"type": "tool_start", "name": call.name, "arguments": call.arguments}
                if call.arguments is None:
                    result: Any = {"error": "Tool arguments were not valid JSON"}
                else:
                    result = execute_tool(ctx, call.name, call.arguments)
                card_key = ...  # unchanged
                result_text = json.dumps(result, default=str)
                result_part = ToolResultPart(call.id, result_text)
                tool_msg = _persist(db, session_row, role="tool", content=result_text,
                                    meta_json=meta, parts=dump_parts([result_part]))
                db.commit()
                ...  # card + message events unchanged
                history.append(Message("tool", [result_part]))
        ...  # budget-exhausted branch unchanged (persist with parts=[TextPart(...)])
    except Exception as exc:  # unchanged
        ...
```

`tool_calls` / `tool_call_id` columns are no longer written (design §5; §13 row in Task 15).
Tool results keep the RAW call id (normalization happens only in `replay`), so ids in the
database are the provider's own.

**Step 4: Router.** In `routers/chat.py` replace the `get_chat_client(model)` pre-check with
`registry.get(registry.resolve(model).provider).ready()` inside the same `try/except
LLMProviderError → 422 NO_MODEL_KEY`; pass `provider=` into `run_turn`. Update
`test_chat_router.py` patches (`chat_router.get_chat_client` → a stub provider via
`fake_provider.install`).

**Step 5: Rewrite `test_chat_agent.py` onto `FakeProvider`.** Keep every scenario and assertion
about events, persisted roles/content, change cards, proposals and the step budget. Replace
`FakeClient` scripts with `FakeProvider` scripts (`["Hel", "lo"]`;
`[[ToolCallPart("call_1", "edit_resume", {...})]]`). Delete the `completion_extras` and
`get_chat_client` factory tests here; their replacements live in `test_llm_openai_compat.py`
(Task 8) and `test_llm_routing.py` (Task 4). Add:
- the context block arrives as the first text part of the last user message in
  `FakeProvider.requests[0].messages`, and the system prompt is identical across two turns;
- `parts`, `provider`, `model` are persisted on assistant rows;
- a second turn after a turn that errored mid-tools replays with a synthetic error result.

**Step 6:** Remove the temporary probe branch (Task 7). Grep: `grep -rn "get_chat_client\|
GEMINI_OPENAI_COMPAT_URL\|_accumulate_stream\|_replay_messages" backend/` returns nothing
(SYSTEM.md mentions are fixed in Task 15).

**Step 7:** Full suite. **Commit** — `feat(chat): Assistant chat on every provider through neutral transcripts`.

---

### Task 13: Frontend — four providers

Read `frontend/AGENTS.md` and `docs/frontend-conventions.md` first. Desktop-only (1024px+).

**Files:**
- `frontend/lib/model-catalog.ts` — `PROVIDER_LABELS`: add `["anthropic", "Claude"]`,
  `["openrouter", "OpenRouter"]`; export `PROVIDERS = ["openai", "anthropic", "gemini",
  "openrouter"] as const` and `type Provider = (typeof PROVIDERS)[number]`;
  `PROVIDER_WORDS` adds `"anthropic"`, `"claude"`, `"openrouter"`
- `frontend/lib/api-key-format.ts` — `KeyProvider = Provider`; `SHAPE` adds
  `anthropic: { prefix: "sk-ant-", named: "A Claude" }`, `openrouter: { prefix: "sk-or-", named:
  "An OpenRouter" }`; in `keyFormatProblem`, an OpenAI key starting `sk-ant-` or `sk-or-` returns
  `"That looks like a Claude key. Paste it in the Claude API key field."` / the OpenRouter
  equivalent; `keysSavedText` takes a `Partial<Record<\`${Provider}_api_key\`, string | null>>`
- `frontend/lib/error-text.ts` — `MISSING_KEY` also matches `no (claude|openrouter) api key`;
  add `CLAUDE_WORDS = /claude|anthropic|authentication_error/i` and
  `OPENROUTER_WORDS = /openrouter/i` to `refusedKey`'s who-refused chain (check them before
  `OPENAI_WORDS`)
- `frontend/lib/types.ts` — `OpenAIInfo` gains the four new fields; `DiscoveredModel.provider:
  Provider`
- `frontend/components/settings/models-section.tsx` — `ApiKeysSection`: state, refs and
  problems become `Record<Provider, …>` driven by one `KEY_FIELDS` table (label, hint link,
  configured/source field names); four `KeyField`s (OpenAI, Claude, Gemini, OpenRouter);
  description: "Lets the app use OpenAI, Claude, Gemini or OpenRouter. You need one unless you
  use a custom AI server."; OpenRouter's hint adds "Requests use only providers that keep no
  data." Picker groups built from `PROVIDERS` with `providerLabel`. Role rows show a warning
  line when the role's model provider has no key configured (and no custom server for
  `openai`): "<Role> uses <Provider>, but no <Provider> key is saved."
- `frontend/components/settings/model-catalog-panel.tsx` — `SyncProvider = Provider`; buttons
  over `PROVIDERS`
- `frontend/components/settings/llm-endpoint.tsx:93,136` — copy: "Not set. Models run on the
  providers whose keys you saved." / "Leave empty to use the providers above."
- Tests: `frontend/lib/api-key-format.test.ts`, `frontend/lib/model-catalog.test.ts`,
  `backend/tests/test_frontend_settings_assistant_words.py` (pinned strings — update to the
  new wording; the Companion's copies of `MISSING_KEY`/`REFUSED_KEY` must stay equal, so update
  `extension/` where the test says they are pinned equal)

**Steps:** write the node tests first (key shapes, cross-field warnings, saved text for one and
several keys, provider labels), run → FAIL, implement, run `npx tsc --noEmit && npm run lint &&
node --test lib/*.test.ts && npm run build`, then the pinned-words backend test. Visual check
on a fresh stack (Task 14 recipe) at 1280px: four key fields aligned, picker groups, warning
line. **Commit** — `feat(settings): Claude and OpenRouter keys and models`.

---

### Task 14: Live provider check script

**Files:** Create `backend/scripts/live_provider_check.py` (never imported by tests or CI).

For each provider whose key is in the environment (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GEMINI_API_KEY`, `OPENROUTER_API_KEY`) and a model id given on the command line
(`--openai gpt-5.6-luna --anthropic claude-sonnet-5-5 --gemini gemini-3.7-flash
--openrouter anthropic/claude-sonnet-5-5`), run against a scratch `DATA_DIR`:
1. `call_openai` text; 2. `call_openai` json; 3. `call_openai` with a small PNG (OCR);
4. `adapter.stream` with a two-tool scenario that forces two sequential tool steps (tool A's
result is needed to call tool B) — assert two rounds succeed; 5. a three-turn conversation
that switches provider on turn 3 through `replay.for_provider`. Print a pass/fail table and the
usage per call. Exit non-zero on any failure.

Run it with the owner's keys on a throwaway stack (memory recipe: fresh uvicorn on a spare
port, every `*_DIR` under `$SP`). Record the table in the Gate table. Expected deviations to
resolve here: Gemini `parametersJsonSchema` acceptance (Task 10), OpenRouter's no-ZDR error
text (Task 6), Claude fallback acceptance per model (Task 5 `FALLBACK_MODELS`).

**Commit** — `test(llm): live provider check script (manual, real keys)`.

---

### Task 15: Docs, privacy, SYSTEM.md

**Files:** `PRIVACY.md:78-92`, `SYSTEM.md`, `.system_md_enforcement.json`, `README.md`
(provider table ~:387-401 and key mentions), `.env.example` (Task 4 done), `docs/entities/others.md`,
`KNOWN_ISSUES.md:80`, `docs/native-install.md` / `docs/GETTING_STARTED.md` where keys are
listed (`grep -rln "GEMINI_API_KEY\|OpenAI or Gemini" docs README.md`).

- PRIVACY.md "AI services you choose": Anthropic (api.anthropic.com, your key, Anthropic's
  retention terms); OpenRouter (openrouter.ai, your key, requests forced to providers with zero
  data retention and no data collection; OpenRouter itself is a processor).
- SYSTEM.md (orientation tier stays ≤ 1000; pay for lines):
  - §3: the LLM row describes `llm.py` front door + `llm_providers/` adapters + registry rule.
  - §6: new invariant `{#inv-provider-tagged-meta}` — **Provider data** opaque blocks
    (`ProviderMeta`) reach only their own provider; enforcement `tests/test_replay.py`; pin it
    in `.system_md_enforcement.json`. Extend the EEO paragraph's "model providers … are
    recipients too" to include Claude and OpenRouter.
  - §7: replace "Streaming chat needs the OpenAI streaming tool-call wire shape (…)" with one
    line: chat streams through `llm_providers` on any provider; eligibility is the tools probe.
  - §12: no new gotcha unless one bit you during execution (then the usual 3 lines).
  - §13: row `chat-message-parts` — old `chat_messages.tool_calls`/`tool_call_id` columns,
    readers: `transcript.from_legacy` fallback and `ChatMessageRead`; removal trigger: the
    release after this one ships, drop the columns + fallback in one migration.
  - Header "Last full revision" line untouched (not a full revision).
- `python3 scripts/check_system_md.py` passes.

**Commit** — `docs: Claude and OpenRouter providers, neutral transcript invariant, privacy`.

---

### Task 16: Final gates and review

1. Full suite (xdist), ruff, frontend checks, `check_system_md.py`, slop ratchet for the
   backend and frontend surfaces touched (maintainer's `slop_scan.py check <surface>`).
2. `grep -rn "chat.completions.create" backend/app` → only `llm_providers/openai_compat.py`.
   `grep -rn "startswith(\"claude-\|startswith(\"gemini-" backend/app` → only
   `llm_providers/` and `model_settings._provider_for` (label only).
3. Live check table from Task 14 is green for every provider with a key.
4. Opus 5.5 review of the whole branch against the Goal Card (not just the plan): every
   Principle line checked, Deviation log adjudicated.
5. Fill the Gate table; leave the branch unpushed; report to the planner.

---

## Gate table

| Gate | Result |
|---|---|
| Task 0 baseline (passed / skipped / wall) | |
| Task 2 suite unchanged | |
| Task 5 CI-venv suite, lock diff, import-time check | |
| Task 11 upgrade/downgrade/upgrade | |
| Task 14 live check (per provider: text/json/image/2-step tools/switch) | |
| Task 16 final suite, lint, frontend, docs, slop | |

## Deviation log

| Task | Planned | Found | Change | Goal Card line |
|---|---|---|---|---|
| | | | | |

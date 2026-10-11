"""OpenAI chat.completions wire format: OpenAI, custom servers (Ollama, LM Studio, vLLM) and
OpenRouter (Task 6). Quirks for that wire format live here and in `llm.completion_extras`."""

from __future__ import annotations

import base64
import json
import re
from typing import Any, Callable

import openai
from urllib.error import HTTPError, URLError
from urllib.request import Request as HttpRequest
from urllib.request import urlopen

from app.services.llm_providers.errors import (
    LLMProviderError,
    _no_answer,
    _openai_reason,
    _status_reason,
    logger,
)
from app.services.llm_providers.types import (
    ImagePart,
    Message,
    Request,
    Response,
    TextPart,
    Usage,
)


NO_ZDR_MESSAGE = "OpenRouter has no zero-retention provider for this model. Pick another model."
# OpenRouter's 404 text when the forced zdr/data_collection preferences leave no endpoint.
# Wording unconfirmed live (Task 14): matched loosely on its two stable phrases.
_NO_ZDR_RE = re.compile(r"no endpoints|data policy", re.IGNORECASE)


_OPENROUTER_MAX_PAGES = 20

# Non-chat model id fragments: keep discovery focused on generation.
_OPENAI_SKIP_FRAGMENTS = (
    "embedding", "whisper", "tts", "dall-e", "davinci", "babbage", "moderation",
    "transcribe", "realtime", "audio", "image", "search",
)


def _openai_model_is_chatlike(model_id: str) -> bool:
    lowered = model_id.lower()
    return not any(fragment in lowered for fragment in _OPENAI_SKIP_FRAGMENTS)


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

    def _map_error(self, exc: openai.OpenAIError) -> LLMProviderError:
        """The ONE place an SDK error becomes a user sentence (generate and, later, stream).
        Normalizes to LLMProviderError so provider outages mean ONE exception type for every
        caller (app.main maps it to 502); SDK errors must not escape as 500s."""
        if (self.name == "openrouter" and getattr(exc, "status_code", None) == 404
                and _NO_ZDR_RE.search(str(exc))):
            # Every OpenRouter request forces zero retention, so a model with no such
            # provider answers 404 "No endpoints found matching your data policy".
            logger.warning("model provider failed: %s", exc)
            return LLMProviderError(NO_ZDR_MESSAGE, provider_detail=f"OpenRouter request failed: {exc}")
        return _no_answer(_openai_reason(exc), f"OpenAI API request failed: {exc}",
                          provider=self._label())

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
            raise self._map_error(exc) from exc
        text = response.choices[0].message.content or ""
        answered_by = getattr(response, "model", None)  # a MagicMock under patched-client tests
        return Response(Message("assistant", [TextPart(text)]), _usage(response), "stop",
                        answered_by if isinstance(answered_by, str) else req.model.model)

    def stream(self, req: Request):  # Task 8
        raise NotImplementedError

    def list_models(self) -> list[dict[str, str]]:
        """Chat-like models for Settings › Find models. OpenRouter lists only zero-retention
        models (`zdr=true`), the ones that can run under the forced privacy preferences."""
        if self.name == "openrouter":
            return self._list_openrouter()
        try:
            page = self._client().models.list()
        except openai.OpenAIError as exc:
            raise self._map_error(exc) from exc
        seen: set[str] = set()
        out: list[dict[str, str]] = []
        for item in page.data:
            model_id = getattr(item, "id", None)
            if not isinstance(model_id, str) or not model_id or model_id in seen:
                continue
            if not _openai_model_is_chatlike(model_id):
                continue
            seen.add(model_id)
            out.append({"id": model_id, "label": model_id, "provider": self.name})
        out.sort(key=lambda row: row["id"])
        return out

    def _list_openrouter(self) -> list[dict[str, str]]:
        """Plain GET, not `models.list()`: the response pages via `links.next`, which the SDK
        does not follow. The key only ever goes to OPENROUTER_BASE_URL."""
        from app.services import llm

        key = llm.get_openrouter_key()
        if not key:
            raise LLMProviderError(llm.NO_OPENROUTER_KEY_MESSAGE)
        base = llm.OPENROUTER_BASE_URL
        url: str | None = f"{base}/models?zdr=true"
        seen: set[str] = set()
        out: list[dict[str, str]] = []
        for _ in range(_OPENROUTER_MAX_PAGES):
            request = HttpRequest(url, headers={"Authorization": f"Bearer {key}"}, method="GET")
            try:
                with urlopen(request, timeout=60) as response:
                    doc = json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                raise _no_answer(_status_reason(exc.code), f"OpenRouter models.list failed: {exc.code} {body}",
                                 provider="OpenRouter") from exc
            except (URLError, TimeoutError, ValueError) as exc:
                raise _no_answer("no connection", f"OpenRouter models.list failed: {exc}",
                                 provider="OpenRouter") from exc
            for item in doc.get("data") or []:
                model_id = item.get("id") if isinstance(item, dict) else None
                if not isinstance(model_id, str) or not model_id or model_id in seen:
                    continue
                seen.add(model_id)
                out.append({"id": model_id, "label": item.get("name") or model_id,
                            "provider": self.name})
            nxt = (doc.get("links") or {}).get("next")
            if not isinstance(nxt, str) or not nxt.startswith(base + "/"):
                break  # no more pages, or a link off OpenRouter: never send the key there
            url = nxt
        out.sort(key=lambda row: row["id"])
        return out

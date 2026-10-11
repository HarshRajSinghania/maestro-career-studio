"""OpenAI chat.completions wire format: OpenAI, custom servers (Ollama, LM Studio, vLLM) and
OpenRouter (Task 6). Quirks for that wire format live here and in `llm.completion_extras`."""

from __future__ import annotations

import base64
from typing import Any, Callable

import openai

from app.services.llm_providers.errors import _no_answer, _openai_reason
from app.services.llm_providers.types import (
    ImagePart,
    Message,
    Request,
    Response,
    TextPart,
    Usage,
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
            # Normalize to LLMProviderError so provider outages mean ONE exception type for
            # every caller (app.main maps it to 502); SDK errors must not escape as 500s.
            raise _no_answer(_openai_reason(exc), f"OpenAI API request failed: {exc}",
                             provider=self._label()) from exc
        text = response.choices[0].message.content or ""
        answered_by = getattr(response, "model", None)  # a MagicMock under patched-client tests
        return Response(Message("assistant", [TextPart(text)]), _usage(response), "stop",
                        answered_by if isinstance(answered_by, str) else req.model.model)

    def stream(self, req: Request):  # Task 8
        raise NotImplementedError

    def list_models(self) -> list[dict[str, str]]:  # moved from llm.list_openai_models (Task 7)
        raise NotImplementedError

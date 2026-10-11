"""Google Gemini native `generateContent` wire format. Key access goes through `llm` at call time
(`llm.get_gemini_key`) so settings and test patches keep reaching it; `app.services.llm` is imported
only inside functions (it imports the registry, which imports this module)."""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request as HttpRequest
from urllib.request import urlopen

from app.services.llm_providers.errors import (
    _TIMED_OUT,
    NO_GEMINI_KEY_MESSAGE,
    LLMProviderError,
    _gemini_reason,
    _no_answer,
)
from app.services.llm_providers.types import (
    ImagePart,
    Message,
    Request,
    Response,
    TextPart,
    Usage,
)

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/"
_DEFAULT_TIMEOUT = 120.0  # what the one-shot Gemini call has always used when no timeout is given


def model_name(model: str) -> str:
    return model.removeprefix("models/")


def _thinking_level(model: str) -> str | None:
    normalized = model_name(model)
    if not normalized.startswith("gemini-3"):
        return None
    return "high" if "pro" in normalized else "low"


def _generation_config(model: str, output: Any) -> dict[str, Any]:
    config: dict[str, Any] = {}
    if output == "json":
        config["responseMimeType"] = "application/json"
    level = _thinking_level(model)
    if level:
        config["thinkingConfig"] = {"thinkingLevel": level}
    return config


def build_generate_payload(req: Request) -> dict[str, Any]:
    """One-shot call: today's exact request (the last message's text, then its images)."""
    msg = req.messages[-1]
    parts: list[dict[str, Any]] = [{"text": "".join(p.text for p in msg.parts if isinstance(p, TextPart))}]
    for img in (p for p in msg.parts if isinstance(p, ImagePart)):
        parts.append({"inlineData": {"mimeType": img.media_type,
                                     "data": base64.b64encode(img.data).decode("ascii")}})
    payload: dict[str, Any] = {"contents": [{"parts": parts}]}
    config = _generation_config(req.model.model, req.output)
    if config:
        payload["generationConfig"] = config
    return payload


def _usage(data: dict[str, Any]) -> Usage | None:
    meta = data.get("usageMetadata") or {}
    i, o = meta.get("promptTokenCount"), meta.get("candidatesTokenCount")
    return Usage(i, o) if isinstance(i, int) and isinstance(o, int) else None


def _text(data: dict[str, Any]) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts") or []
    return "".join(part.get("text", "") for part in parts)


def _key() -> str:
    from app.services import llm

    key = llm.get_gemini_key()
    if not key:
        raise LLMProviderError(NO_GEMINI_KEY_MESSAGE)
    return key


class GeminiProvider:
    name = "gemini"

    def ready(self) -> None:
        _key()

    def generate(self, req: Request) -> Response:
        key = _key()
        timeout = req.timeout if req.timeout is not None else _DEFAULT_TIMEOUT
        request = HttpRequest(
            f"{_BASE_URL}models/{model_name(req.model.model)}:generateContent",
            data=json.dumps(build_generate_payload(req)).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise _no_answer(_gemini_reason(exc.code, body),
                             f"Gemini API request failed: {exc.code} {body}", provider="Gemini") from exc
        except (TimeoutError, URLError) as exc:
            # A caller's short timeout running out is the provider not answering
            # in time, not a local crash (URLError wraps a connect timeout).
            if isinstance(exc, URLError) and not isinstance(exc.reason, TimeoutError):
                raise
            raise _no_answer(_TIMED_OUT, f"Gemini API request timed out: {exc}", provider="Gemini") from exc
        return Response(Message("assistant", [TextPart(_text(data))]), _usage(data), "stop",
                        req.model.model)

    def stream(self, req: Request):  # Task 8
        raise NotImplementedError

    def list_models(self) -> list[dict[str, str]]:
        """Gemini models that support generateContent for the configured Gemini key."""
        key = _key()
        request = HttpRequest(f"{_BASE_URL}models", headers={"x-goog-api-key": key}, method="GET")
        try:
            with urlopen(request, timeout=60) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise LLMProviderError(f"Gemini models.list failed: {exc.code} {body}") from exc

        out: list[dict[str, str]] = []
        for item in data.get("models") or []:
            name = item.get("name") or ""
            if "generateContent" not in (item.get("supportedGenerationMethods") or []):
                continue
            model_id = model_name(name)
            if not model_id.startswith("gemini-"):
                continue
            out.append({"id": model_id, "label": item.get("displayName") or model_id,
                        "provider": "gemini"})
        out.sort(key=lambda row: row["id"])
        return out

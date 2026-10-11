"""Google Gemini native `generateContent` wire format. Key access goes through `llm` at call time
(`llm.get_gemini_key`) so settings and test patches keep reaching it; `app.services.llm` is imported
only inside functions (it imports the registry, which imports this module)."""

from __future__ import annotations

import base64
import json
from http.client import HTTPException
from typing import Any, Iterable, Iterator
from uuid import uuid4
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
    Finish,
    ImagePart,
    Message,
    ModelRef,
    ProviderMeta,
    Request,
    Response,
    StreamEvent,
    TextDelta,
    TextPart,
    ToolCallPart,
    ToolResultPart,
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


# --- native chat ----------------------------------------------------------------------------

# Google's documented placeholder for a functionCall that has no real thought signature.
DUMMY_SIGNATURE = "skip_thought_signature_validator"


def _parse_result(content: str) -> dict[str, Any]:
    try:
        parsed: Any = json.loads(content)
    except (TypeError, ValueError):
        parsed = content
    return parsed if isinstance(parsed, dict) else {"result": parsed}


def _is_minted(call: ToolCallPart) -> bool:
    return bool(call.meta and call.meta.provider == "gemini" and call.meta.data.get("id_minted"))


def _current_turn_start(messages: list[Message]) -> int:
    """Index of the last user message that carries text; its model steps are the current turn."""
    for i in range(len(messages) - 1, -1, -1):
        m = messages[i]
        if m.role == "user" and any(isinstance(p, TextPart) and p.text for p in m.parts):
            return i
    return -1


def build_chat_payload(req: Request) -> dict[str, Any]:
    """Whole-transcript streaming call. Thought signatures replay only from the same Gemini model;
    the current turn's first functionCall per model step otherwise gets Google's dummy (earlier turns
    are not validated). Ids this adapter minted are never sent back: Gemini matches by order and name."""
    model = model_name(req.model.model)
    turn_start = _current_turn_start(req.messages)
    calls_by_id: dict[str, ToolCallPart] = {}
    contents: list[dict[str, Any]] = []
    for index, msg in enumerate(req.messages):
        if msg.role == "user":
            parts: list[dict[str, Any]] = [{"text": p.text} for p in msg.parts if isinstance(p, TextPart)]
            parts += [{"inlineData": {"mimeType": p.media_type, "data": base64.b64encode(p.data).decode("ascii")}}
                      for p in msg.parts if isinstance(p, ImagePart)]
            if parts:
                contents.append({"role": "user", "parts": parts})
        elif msg.role == "assistant":
            parts = []
            first_call = True
            for p in msg.parts:
                if isinstance(p, TextPart):
                    if p.text:
                        parts.append({"text": p.text})
                elif isinstance(p, ToolCallPart):
                    calls_by_id[p.id] = p
                    call: dict[str, Any] = {"name": p.name, "args": p.arguments or {}}
                    if not _is_minted(p):
                        call["id"] = p.id
                    entry: dict[str, Any] = {"functionCall": call}
                    sig = None
                    if (p.meta and p.meta.provider == "gemini" and model_name(p.meta.model) == model
                            and p.meta.data.get("thoughtSignature")):
                        sig = p.meta.data["thoughtSignature"]
                    elif first_call and index > turn_start >= 0:
                        sig = DUMMY_SIGNATURE
                    if sig:
                        entry["thoughtSignature"] = sig
                    first_call = False
                    parts.append(entry)
            if parts:
                contents.append({"role": "model", "parts": parts})
        else:
            responses: list[dict[str, Any]] = []
            for p in msg.parts:
                if isinstance(p, ToolResultPart):
                    src = calls_by_id.get(p.tool_call_id)
                    fr: dict[str, Any] = {"name": src.name if src else p.tool_call_id,
                                          "response": _parse_result(p.content)}
                    if not (src and _is_minted(src)):
                        fr["id"] = p.tool_call_id
                    responses.append({"functionResponse": fr})
            if not responses:
                continue
            if contents and contents[-1]["role"] == "user" and all(
                    "functionResponse" in x for x in contents[-1]["parts"]):
                contents[-1]["parts"].extend(responses)  # consecutive tool messages share one turn
            else:
                contents.append({"role": "user", "parts": responses})
    payload: dict[str, Any] = {"contents": contents}
    if req.system:
        payload["systemInstruction"] = {"parts": [{"text": req.system}]}
    if req.tools:
        payload["tools"] = [{"functionDeclarations": [
            {"name": t.name, "description": t.description, "parametersJsonSchema": t.parameters}
            for t in req.tools]}]
    config = _generation_config(req.model.model, req.output)
    if config:
        payload["generationConfig"] = config
    return payload


def parse_sse(lines: Iterable[bytes | str], ref: ModelRef) -> Iterator[StreamEvent]:
    """SSE `data:` lines -> TextDeltas, then one Finish with parts in arrival order. Thought parts
    are skipped; a functionCall without an id gets a minted one (unique across the whole session)."""
    parts: list[Any] = []
    text: list[str] = []
    usage, stop_reason = None, "stop"

    def flush_text() -> None:
        if text:
            parts.append(TextPart("".join(text)))
            text.clear()

    for raw in lines:
        line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
        line = line.strip()
        if not line.startswith("data:"):
            continue
        try:
            data = json.loads(line[5:].strip())
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("error"):
            err = data["error"]
            msg = err.get("message") if isinstance(err, dict) else err
            raise _no_answer("error", f"Gemini stream error: {msg}", provider="Gemini")
        block = (data.get("promptFeedback") or {}).get("blockReason")
        if block and not data.get("candidates"):
            raise _no_answer(f"the prompt was blocked: {block}", f"Gemini blocked the prompt: {block}",
                             provider="Gemini")
        usage = _usage(data) or usage
        candidate = (data.get("candidates") or [{}])[0]
        if isinstance(candidate.get("finishReason"), str):
            stop_reason = candidate["finishReason"].lower()
        for part in (candidate.get("content") or {}).get("parts") or []:
            fc = part.get("functionCall")
            if isinstance(fc, dict):
                flush_text()
                minted = not fc.get("id")
                meta_data: dict[str, Any] = {}
                if part.get("thoughtSignature"):
                    meta_data["thoughtSignature"] = part["thoughtSignature"]
                if minted:
                    meta_data["id_minted"] = True
                args = fc.get("args")
                parts.append(ToolCallPart(
                    fc.get("id") or f"call_{uuid4().hex[:12]}", fc.get("name", ""),
                    args if isinstance(args, dict) else ({} if args is None else None),
                    ProviderMeta("gemini", ref.model, meta_data) if meta_data else None))
            elif isinstance(part.get("text"), str) and not part.get("thought"):
                if part["text"]:
                    text.append(part["text"])
                    yield TextDelta(part["text"])
    flush_text()
    yield Finish(Message("assistant", parts), usage, stop_reason, ref.model)


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

    def stream(self, req: Request) -> Iterator[StreamEvent]:
        """Native streaming chat. Errors at open and mid-stream become LLMProviderError, mapped as in
        `generate`; the response is closed on error and on early stop."""
        key = _key()
        timeout = req.timeout if req.timeout is not None else _DEFAULT_TIMEOUT
        request = HttpRequest(
            f"{_BASE_URL}models/{model_name(req.model.model)}:streamGenerateContent?alt=sse",
            data=json.dumps(build_chat_payload(req)).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": key},
            method="POST",
        )
        response = None
        try:
            response = urlopen(request, timeout=timeout)
            yield from parse_sse(response, req.model)
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise _no_answer(_gemini_reason(exc.code, body),
                             f"Gemini API request failed: {exc.code} {body}", provider="Gemini") from exc
        except (TimeoutError, URLError, OSError, HTTPException) as exc:
            timed_out = isinstance(exc, TimeoutError) or (
                isinstance(exc, URLError) and isinstance(exc.reason, TimeoutError))
            raise _no_answer(_TIMED_OUT if timed_out else "no connection",
                             f"Gemini API request failed: {exc}", provider="Gemini") from exc
        finally:
            if response is not None:
                response.close()

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

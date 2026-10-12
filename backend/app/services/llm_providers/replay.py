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
    """`n` is a running count of tool calls in this replay (unique, deterministic)."""
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

    call_count = 0
    for msg in messages:
        parts = []
        for p in msg.parts:
            if isinstance(p, ReasoningPart):
                if p.meta is None or p.meta.provider != target.provider:
                    continue
                parts.append(p)
            elif isinstance(p, ToolCallPart):
                meta = p.meta if p.meta and p.meta.provider == target.provider else None
                parts.append(replace(p, id=_norm(p.id, call_count, ids), meta=meta))
                call_count += 1
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
        if not parts:
            # Nothing left to say (e.g. an assistant row that held only another provider's
            # reasoning): an empty message would reach the wire as `"content": []`.
            continue
        if msg.role == "assistant":
            open_calls.extend(p.id for p in parts if isinstance(p, ToolCallPart))
        out.append(Message(msg.role, parts))
    # A trailing unanswered call is left open: the caller is about to run it.
    return out

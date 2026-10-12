"""Chat transcript rows as neutral parts (SYSTEM.md inv-provider-tagged-meta).

A row written before the multi-provider change has only OpenAI-shaped `content` /
`tool_calls` / `tool_call_id`; `from_legacy` reads those. A row written since carries the
versioned `parts` document (`types.dump_parts`). Migration `*_chat_message_parts` holds an
inline copy of `from_legacy` (a revision never imports app code); `tests/test_transcript.py`
keeps the two equal.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.chat import ChatMessage, ChatSession
from app.services.llm_providers.types import (
    Message,
    Part,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    dump_parts,
    is_anthropic_thinking_part,
    load_parts,
)


def _arguments(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def from_legacy(
    role: str,
    content: str | None,
    tool_calls: list[dict[str, Any]] | None,
    tool_call_id: str | None,
) -> list[Part]:
    if role == "tool":
        return [ToolResultPart(tool_call_id or "", content or "")]
    parts: list[Part] = []
    if content:
        parts.append(TextPart(content))
    for call in tool_calls or []:
        fn = call.get("function") or {}
        parts.append(ToolCallPart(call.get("id") or "", fn.get("name") or "",
                                  _arguments(fn.get("arguments"))))
    return parts


def row_parts(row: ChatMessage) -> list[Part]:
    if row.parts is not None:
        return load_parts(row.parts)
    return from_legacy(row.role, row.content, row.tool_calls, row.tool_call_id)


def history(session_row: ChatSession) -> list[Message]:
    return [Message(m.role, row_parts(m)) for m in session_row.messages]  # type: ignore[arg-type]


def drop_reasoning(db: Session, session_row: ChatSession) -> None:
    """Remove Claude thinking / redacted_thinking blocks from stored assistant rows (planning
    decision 5: a signature 400 means the stored blocks can no longer be replayed). Only what
    `is_anthropic_thinking_part` names is dropped: the `fallback` block stays in place, and
    OpenRouter / Gemini reasoning is not the cause of that 400, so it stays too. The caller commits."""
    for m in session_row.messages:
        if m.role != "assistant" or m.parts is None:
            continue
        parts = load_parts(m.parts)
        kept = [p for p in parts if not is_anthropic_thinking_part(p)]
        if len(kept) != len(parts):
            m.parts = dump_parts(kept)  # a new dict: in-place JSON edits are not tracked
    db.flush()

"""Transcript migration + codec: legacy rows to neutral parts (Task 11)."""

import importlib.util
import json
from pathlib import Path

import pytest

from app.models.chat import ChatMessage, ChatSession
from app.services import transcript
from app.services.llm_providers.types import (
    Message,
    ProviderMeta,
    ReasoningPart,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    dump_parts,
    load_parts,
)

CALL = {"id": "c1", "type": "function", "function": {"name": "f", "arguments": "{\"a\": 1}"}}

FIXTURES = [
    ("user", "hi", None, None),
    ("assistant", "ok", None, None),
    ("assistant", "", None, None),
    ("assistant", None, None, None),
    ("assistant", None, [CALL], None),
    ("assistant", "calling", [CALL, {"id": "c2", "type": "function",
                                       "function": {"name": "g", "arguments": "not json"}}], None),
    ("assistant", None, [{"id": "c3", "type": "function", "function": {"name": "h", "arguments": "[1]"}}], None),
    ("tool", '{"x": 1}', None, "c1"),
    ("tool", None, None, "c1"),
]


def test_user_and_assistant_text():
    assert transcript.from_legacy("user", "hi", None, None) == [TextPart("hi")]
    assert transcript.from_legacy("assistant", "ok", None, None) == [TextPart("ok")]
    assert transcript.from_legacy("assistant", "", None, None) == []
    assert transcript.from_legacy("assistant", None, None, None) == []


def test_assistant_tool_calls():
    assert transcript.from_legacy("assistant", None, [CALL], None) == [
        ToolCallPart("c1", "f", {"a": 1})
    ]
    bad = {"id": "c2", "type": "function", "function": {"name": "f", "arguments": "{oops"}}
    assert transcript.from_legacy("assistant", None, [bad], None) == [ToolCallPart("c2", "f", None)]


def test_text_precedes_tool_calls():
    parts = transcript.from_legacy("assistant", "thinking out loud", [CALL], None)
    assert [type(p) for p in parts] == [TextPart, ToolCallPart]


def test_tool_result():
    assert transcript.from_legacy("tool", '{"x": 1}', None, "c1") == [ToolResultPart("c1", '{"x": 1}')]


def _row(session, **kw):
    row = ChatMessage(session_id=session.id, **kw)
    return row


def test_history_prefers_parts_and_falls_back_to_legacy(db_session):
    s = ChatSession(title="t")
    db_session.add(s)
    db_session.flush()
    new = [TextPart("neutral")]
    db_session.add_all([
        ChatMessage(session_id=s.id, role="user", content="legacy hi"),
        ChatMessage(session_id=s.id, role="assistant", content="legacy text",
                    parts=dump_parts(new)),
        ChatMessage(session_id=s.id, role="assistant", content=None, tool_calls=[CALL]),
        ChatMessage(session_id=s.id, role="tool", content="r", tool_call_id="c1"),
    ])
    db_session.commit()
    db_session.refresh(s)
    msgs = transcript.history(s)
    assert [m.role for m in msgs] == ["user", "assistant", "assistant", "tool"]
    assert msgs[0] == Message("user", [TextPart("legacy hi")])
    assert msgs[1].parts == new
    assert msgs[2].parts == [ToolCallPart("c1", "f", {"a": 1})]
    assert msgs[3].parts == [ToolResultPart("c1", "r")]


def _anthropic(kind: str, **extra) -> ReasoningPart:
    return ReasoningPart("", ProviderMeta("anthropic", "claude-x", {"type": kind, **extra}))


def test_drop_reasoning_drops_thinking_keeps_fallback(db_session):
    s = ChatSession(title="t")
    db_session.add(s)
    db_session.flush()
    thinking = _anthropic("thinking", thinking="hmm", signature="sig")
    redacted = _anthropic("redacted_thinking", data="x")
    fallback = _anthropic("fallback", **{"from": "a", "to": "b"})
    gemini = ReasoningPart("g", ProviderMeta("gemini", "gemini-x", {"thought": True}))
    call = ToolCallPart("c1", "f", {"a": 1})
    row = ChatMessage(
        session_id=s.id, role="assistant", content="ok",
        parts=dump_parts([thinking, redacted, fallback, gemini, TextPart("ok"), call]),
    )
    user = ChatMessage(session_id=s.id, role="user", content="hi")
    legacy = ChatMessage(session_id=s.id, role="assistant", content="old")
    db_session.add_all([user, row, legacy])
    db_session.commit()
    db_session.refresh(s)

    transcript.drop_reasoning(db_session, s)
    db_session.commit()
    db_session.refresh(row)

    assert load_parts(row.parts) == [fallback, gemini, TextPart("ok"), call]
    assert user.parts is None and legacy.parts is None


def test_drop_reasoning_leaves_clean_rows_untouched(db_session):
    s = ChatSession(title="t")
    db_session.add(s)
    db_session.flush()
    doc = dump_parts([TextPart("a")])
    row = ChatMessage(session_id=s.id, role="assistant", parts=doc)
    db_session.add(row)
    db_session.commit()
    db_session.refresh(s)
    transcript.drop_reasoning(db_session, s)
    assert row.parts == doc


# --- migration copy stays equal to the app copy --------------------------------------------

def _migration():
    path = next((Path(__file__).resolve().parents[1] / "migrations" / "versions").glob("*_chat_message_parts.py"))
    spec = importlib.util.spec_from_file_location("chat_message_parts_mig", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("role,content,tool_calls,tool_call_id", FIXTURES)
def test_migration_copy_equals_app_codec(role, content, tool_calls, tool_call_id):
    mig = _migration()
    expected = dump_parts(transcript.from_legacy(role, content, tool_calls, tool_call_id))
    got = mig.legacy_parts_doc(role, content, tool_calls, tool_call_id)
    assert got == expected
    assert json.loads(json.dumps(got)) == expected

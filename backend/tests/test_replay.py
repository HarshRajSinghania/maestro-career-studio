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


def test_trailing_unanswered_call_is_left_open():
    out = replay.for_provider([Message("assistant", [ToolCallPart("a", "f", {})])], CLAUDE)
    assert [m.role for m in out] == ["assistant"]


def test_two_id_less_calls_get_distinct_ids_and_each_an_error_result():
    msgs = [Message("assistant", [ToolCallPart("", "f", {}), ToolCallPart("", "g", {})]),
            Message("tool", [ToolResultPart("", "r")]),
            Message("user", [TextPart("next")])]
    out = replay.for_provider(msgs, CLAUDE)
    ids = [p.id for p in out[0].parts]
    assert len(set(ids)) == 2
    assert [p.tool_call_id for p in out[1].parts] == ids and all(p.is_error for p in out[1].parts)


def test_no_message_is_ever_empty():
    msgs = [Message("user", [TextPart("q")]),
            Message("assistant", [_thinking()]),  # only foreign-to-GPT reasoning
            Message("assistant", []),
            Message("tool", [ToolResultPart("zzz", "orphan")]),
            Message("user", [TextPart("next")])]
    out = replay.for_provider(msgs, GPT)
    assert [m.role for m in out] == ["user", "user"]
    assert all(m.parts for m in out)

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


def test_fake_provider_scripts_text_tools_and_reasoning_dropped():
    from tests.fake_provider import FakeProvider

    call = t.ToolCallPart("c1", "x", {})
    req = t.Request(t.ModelRef("fake", "fake-model"), [t.Message("user", [t.TextPart("q")])])
    fp = FakeProvider([["a", "b", [call]], ["z"]], reasoning_dropped=True)
    events = list(fp.stream(req))
    assert [e.text for e in events if isinstance(e, t.TextDelta)] == ["a", "b"]
    fin = events[-1]
    assert isinstance(fin, t.Finish) and fin.reasoning_dropped is True
    assert fin.message.tool_calls == [call]
    assert fp.generate(req).text == "z"
    assert len(fp.requests) == 2

    fp2 = FakeProvider([["x"], (["y"], {"reasoning_dropped": True})])
    assert fp2.generate(req).reasoning_dropped is False
    assert fp2.generate(req).reasoning_dropped is True

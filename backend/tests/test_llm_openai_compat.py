import json
from types import SimpleNamespace as NS

import pytest

from app.services.llm_providers import openai_compat as oc
from app.services.llm_providers.types import (
    Finish,
    ImagePart,
    Message,
    ModelRef,
    ProviderMeta,
    ReasoningPart,
    Request,
    TextDelta,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    ToolSpec,
)


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


def test_generate_returns_text_usage_and_applies_timeout_without_retries():
    from unittest.mock import MagicMock

    client = MagicMock()
    timed = client.with_options.return_value
    timed.chat.completions.create.return_value.choices[0].message.content = "ok"
    timed.chat.completions.create.return_value.usage.prompt_tokens = 3
    timed.chat.completions.create.return_value.usage.completion_tokens = 5
    provider = oc.OpenAICompatProvider("openai", lambda: client, lambda: True, lambda: "OpenAI")
    resp = provider.generate(_req(timeout=4.0))
    client.with_options.assert_called_once_with(timeout=4.0, max_retries=0)
    assert resp.text == "ok"
    assert resp.usage.as_langfuse() == {"input": 3, "output": 5}


def test_generate_normalizes_sdk_errors_to_llm_provider_error():
    import httpx
    import openai
    import pytest
    from unittest.mock import MagicMock

    from app.services.llm_providers.errors import LLMProviderError

    client = MagicMock()
    client.chat.completions.create.side_effect = openai.APIConnectionError(
        request=httpx.Request("POST", "http://x"))
    provider = oc.OpenAICompatProvider("openai", lambda: client, lambda: True, lambda: "OpenAI")
    with pytest.raises(LLMProviderError):
        provider.generate(_req())


# --- Task 8: streaming ---------------------------------------------------------------------

def _chunk(content=None, tool_calls=None, finish=None, usage=None, model=None, **delta_extra):
    delta = NS(content=content, tool_calls=tool_calls, **delta_extra)
    return NS(choices=[NS(delta=delta, finish_reason=finish)], usage=usage, model=model)


def _tc(index, id=None, name=None, arguments=None):
    return NS(index=index, id=id, function=NS(name=name, arguments=arguments))


def test_build_chat_kwargs_maps_every_part_kind():
    req = _req(
        system="be brief",
        messages=[
            Message("user", [TextPart("hi")]),
            Message("assistant", [TextPart("calling"), ToolCallPart("c1", "lookup", {"q": "x"}),
                                  ToolCallPart("c2", "noargs", None)]),
            Message("tool", [ToolResultPart("c1", "found"), ToolResultPart("c2", "{}", True)]),
        ],
        tools=[ToolSpec("lookup", "Find it", {"type": "object", "properties": {}})],
    )
    kwargs = oc.build_chat_kwargs(req)
    assert kwargs["model"] == "gpt-5.6-luna" and kwargs["stream"] is True
    assert kwargs["reasoning_effort"] == "none"
    assert kwargs["tools"] == [{"type": "function", "function": {
        "name": "lookup", "description": "Find it", "parameters": {"type": "object", "properties": {}}}}]
    msgs = kwargs["messages"]
    assert msgs[0] == {"role": "system", "content": "be brief"}
    assert msgs[1] == {"role": "user", "content": "hi"}
    assert msgs[2]["role"] == "assistant" and msgs[2]["content"] == "calling"
    assert msgs[2]["tool_calls"] == [
        {"id": "c1", "type": "function", "function": {"name": "lookup", "arguments": json.dumps({"q": "x"})}},
        {"id": "c2", "type": "function", "function": {"name": "noargs", "arguments": "{}"}},
    ]
    assert msgs[3] == {"role": "tool", "tool_call_id": "c1", "content": "found"}
    assert msgs[4] == {"role": "tool", "tool_call_id": "c2", "content": "{}"}


def test_assistant_with_only_tool_calls_has_null_content_and_no_tools_key_when_none():
    req = _req(messages=[Message("assistant", [ToolCallPart("c1", "f", {})])])
    msg = oc.build_chat_kwargs(req)["messages"][0]
    assert msg["content"] is None
    assert "tools" not in oc.build_chat_kwargs(req)


@pytest.mark.parametrize("model,expected", [
    ("gpt-5.4-x", True), ("gpt-5.5", True), ("gpt-5.6-luna", True), ("openai/gpt-5.6-luna", True),
    ("gpt-4o", False), ("gpt-5.3-x", False), ("llama3.2:3b", False),
])
def test_completion_extras_covers_gpt_5_4_and_up_after_any_vendor_prefix(model, expected):
    assert (oc.completion_extras(model) == {"reasoning_effort": "none"}) is expected


def _accumulate(chunks, ref=None):
    return list(oc.accumulate(iter(chunks), ref or ModelRef("openai", "gpt-4o")))


def test_accumulate_text_deltas_and_finish():
    events = _accumulate([_chunk("Hel"), _chunk("lo"), _chunk(None, finish="stop", model="gpt-4o-2026")])
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["Hel", "lo"]
    fin = events[-1]
    assert isinstance(fin, Finish)
    assert fin.message.role == "assistant" and fin.message.text == "Hello"
    assert fin.stop_reason == "stop" and fin.usage is None and fin.model == "gpt-4o-2026"


def test_accumulate_joins_tool_call_deltas_across_chunks():
    events = _accumulate([
        _chunk(tool_calls=[_tc(0, "c1", "lookup", '{"q"')]),
        _chunk(tool_calls=[_tc(0, None, None, ': "x"}'), _tc(1, "c2", "other", "")]),
        _chunk(finish="tool_calls"),
    ])
    calls = events[-1].message.tool_calls
    assert [(c.id, c.name, c.arguments) for c in calls] == [
        ("c1", "lookup", {"q": "x"}), ("c2", "other", {})]
    assert not [e for e in events if isinstance(e, TextDelta)]
    assert events[-1].stop_reason == "tool_calls"


def test_accumulate_unparseable_arguments_become_none():
    events = _accumulate([_chunk(tool_calls=[_tc(0, "c1", "f", '{"q": ')])])
    assert events[-1].message.tool_calls[0].arguments is None


def test_accumulate_skips_choiceless_chunks_and_reads_usage():
    usage = NS(prompt_tokens=4, completion_tokens=2)
    events = _accumulate([NS(choices=[], usage=usage, model=None), _chunk("a")])
    assert events[-1].message.text == "a"
    assert events[-1].usage is not None and events[-1].usage.as_langfuse() == {"input": 4, "output": 2}


_OR = ModelRef("openrouter", "openai/gpt-5.6-luna")
_DETAILS_A = [{"type": "reasoning.encrypted", "data": "abc", "index": 0}]
_DETAILS_B = [{"type": "reasoning.text", "text": "thinking", "index": 1}]


def test_openrouter_reasoning_details_become_one_leading_reasoning_part():
    events = _accumulate([
        _chunk(reasoning_details=_DETAILS_A),
        _chunk("hi", model_extra={"reasoning_details": _DETAILS_B}),
        _chunk(tool_calls=[_tc(0, "c1", "f", "{}")]),
    ], _OR)
    parts = events[-1].message.parts
    assert isinstance(parts[0], ReasoningPart) and parts[0].text == ""
    assert parts[0].meta == ProviderMeta("openrouter", "openai/gpt-5.6-luna",
                                         {"reasoning_details": _DETAILS_A + _DETAILS_B})
    assert [type(p).__name__ for p in parts] == ["ReasoningPart", "TextPart", "ToolCallPart"]
    assert sum(isinstance(p, ReasoningPart) for p in parts) == 1


def test_other_providers_never_emit_reasoning_details():
    events = _accumulate([_chunk("hi", reasoning_details=_DETAILS_A)], ModelRef("openai", "gpt-4o"))
    assert not any(isinstance(p, ReasoningPart) for p in events[-1].message.parts)


def test_reasoning_details_replay_only_to_openrouter():
    meta = ProviderMeta("openrouter", "m", {"reasoning_details": _DETAILS_A})
    history = [Message("assistant", [ReasoningPart("", meta), ToolCallPart("c1", "f", {})])]
    or_msg = oc.build_chat_kwargs(_req(model=_OR, messages=history))["messages"][0]
    assert or_msg["reasoning_details"] == _DETAILS_A
    other = oc.build_chat_kwargs(_req(model=ModelRef("openai", "gpt-4o"), messages=history))["messages"][0]
    assert "reasoning_details" not in other
    foreign = [Message("assistant", [ReasoningPart("", ProviderMeta("anthropic", "m", {"type": "thinking"})),
                                     TextPart("x")])]
    assert "reasoning_details" not in oc.build_chat_kwargs(_req(model=_OR, messages=foreign))["messages"][0]


def test_stream_requests_streaming_with_extra_body_and_yields_events():
    from unittest.mock import MagicMock

    client = MagicMock()
    client.chat.completions.create.return_value = iter([_chunk("hi"), _chunk(finish="stop")])
    provider = oc.OpenAICompatProvider("openrouter", lambda: client, lambda: True, lambda: "OpenRouter",
                                       extra_body={"provider": {"zdr": True}})
    events = list(provider.stream(_req(model=_OR)))
    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["stream"] is True and kwargs["extra_body"] == {"provider": {"zdr": True}}
    assert "stream_options" not in kwargs
    assert isinstance(events[0], TextDelta) and isinstance(events[-1], Finish)


def test_stream_without_extra_body_sends_none():
    from unittest.mock import MagicMock

    client = MagicMock()
    client.chat.completions.create.return_value = iter([_chunk("hi")])
    provider = oc.OpenAICompatProvider("openai", lambda: client, lambda: True, lambda: "OpenAI")
    list(provider.stream(_req()))
    assert "extra_body" not in client.chat.completions.create.call_args.kwargs


def test_stream_errors_go_through_map_error_at_call_and_mid_stream():
    import httpx
    import openai
    from unittest.mock import MagicMock

    from app.services.llm_providers.errors import LLMProviderError

    boom = openai.APIConnectionError(request=httpx.Request("POST", "http://x"))
    client = MagicMock()
    provider = oc.OpenAICompatProvider("openai", lambda: client, lambda: True, lambda: "OpenAI")
    client.chat.completions.create.side_effect = boom
    with pytest.raises(LLMProviderError):
        list(provider.stream(_req()))

    def broken():
        yield _chunk("a")
        raise boom

    client.chat.completions.create.side_effect = None
    client.chat.completions.create.return_value = broken()
    with pytest.raises(LLMProviderError):
        list(provider.stream(_req()))


@pytest.mark.parametrize("exc,wording", [
    (lambda: __import__("httpx").ReadTimeout("slow"), "timed out"),
    (lambda: __import__("httpx").RemoteProtocolError("cut off"), "no connection"),
])
def test_raw_httpx_errors_mid_stream_become_user_sentences(exc, wording):
    from unittest.mock import MagicMock

    from app.services.llm_providers.errors import LLMProviderError

    def broken():
        yield _chunk("a")
        raise exc()

    client = MagicMock()
    client.chat.completions.create.return_value = broken()
    provider = oc.OpenAICompatProvider("openai", lambda: client, lambda: True, lambda: "OpenAI")
    with pytest.raises(LLMProviderError) as ei:
        list(provider.stream(_req()))
    assert wording in str(ei.value)


def test_stream_closes_the_sdk_stream_on_early_stop():
    class Stream:
        closed = False

        def __iter__(self):
            yield _chunk("a")
            yield _chunk("b")

        def close(self):
            self.closed = True

    from unittest.mock import MagicMock

    sdk = Stream()
    client = MagicMock()
    client.chat.completions.create.return_value = sdk
    provider = oc.OpenAICompatProvider("openai", lambda: client, lambda: True, lambda: "OpenAI")
    gen = provider.stream(_req())
    next(gen)
    gen.close()
    assert sdk.closed

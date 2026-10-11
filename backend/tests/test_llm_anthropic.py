from types import SimpleNamespace

import pytest

from app.services import llm
from app.services.llm_providers import anthropic as ap
from app.services.llm_providers import registry
from app.services.llm_providers.errors import LLMProviderError
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
    dump_parts,
    is_anthropic_thinking_part,
    load_parts,
)

REF = ModelRef("anthropic", "claude-opus-5-5")


def test_generate_kwargs_never_send_sampling_or_forced_tools():
    kw = ap.build_kwargs(Request(model=REF, messages=[Message("user", [TextPart("hi")])]), stream=False)
    for banned in ("temperature", "top_p", "top_k", "tool_choice", "thinking"):
        assert banned not in kw
    assert kw["max_tokens"] == ap.GENERATE_MAX_TOKENS
    assert kw["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]


def test_fallbacks_only_on_models_that_accept_them():
    on = ap.build_kwargs(Request(model=REF, messages=[Message("user", [TextPart("x")])]), stream=False)
    assert on["fallbacks"] == "default" and on["betas"] == [ap.FALLBACK_BETA]
    off = ap.build_kwargs(Request(model=ModelRef("anthropic", "claude-haiku-5-5"),
                                  messages=[Message("user", [TextPart("x")])]), stream=False)
    assert "fallbacks" not in off and "betas" not in off


def test_schema_output_and_effort_go_in_output_config():
    kw = ap.build_kwargs(Request(model=REF, messages=[Message("user", [TextPart("x")])],
                                 output={"type": "object"}, effort="low"), stream=False)
    assert kw["output_config"] == {"format": {"type": "json_schema", "schema": {"type": "object"}},
                                   "effort": "low"}


def test_cache_and_system_and_tools():
    req = Request(model=REF, system="SYS", cache=True,
                  tools=[ToolSpec("t", "d", {"type": "object", "properties": {}})],
                  messages=[Message("user", [TextPart("x")])])
    kw = ap.build_kwargs(req, stream=True)
    assert kw["system"] == [{"type": "text", "text": "SYS"}]
    assert kw["cache_control"] == {"type": "ephemeral"}
    assert kw["tools"] == [{"name": "t", "description": "d", "input_schema": {"type": "object", "properties": {}}}]
    assert kw["max_tokens"] == ap.STREAM_MAX_TOKENS


def test_history_translation_groups_tool_results_and_keeps_block_order():
    thinking = {"type": "thinking", "thinking": "", "signature": "sig"}
    msgs = [
        Message("user", [TextPart("go")]),
        Message("assistant", [ReasoningPart("", ProviderMeta("anthropic", "claude-opus-5-5", thinking)),
                              TextPart(""), ToolCallPart("a", "f", {"x": 1}), ToolCallPart("b", "g", {})]),
        Message("tool", [ToolResultPart("a", "1")]),
        Message("tool", [ToolResultPart("b", "2", is_error=True)]),
    ]
    out = ap.build_messages(msgs)
    assert out[1]["content"] == [thinking,
                                 {"type": "tool_use", "id": "a", "name": "f", "input": {"x": 1}},
                                 {"type": "tool_use", "id": "b", "name": "g", "input": {}}]
    assert out[2] == {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "a", "content": "1"},
        {"type": "tool_result", "tool_use_id": "b", "content": "2", "is_error": True}]}


def test_images_are_base64_blocks():
    out = ap.build_messages([Message("user", [TextPart("read"), ImagePart(b"\x89PNG")])])
    assert out[0]["content"][1]["type"] == "image"
    assert out[0]["content"][1]["source"]["media_type"] == "image/png"


def _block(**kw):
    return SimpleNamespace(model_dump=lambda **_: kw, **kw)


def test_parts_from_message_keeps_fallback_block_in_place_and_drops_pre_fallback_non_text():
    fallback = {"type": "fallback", "from": {"model": "claude-opus-5-5"}, "to": {"model": "claude-opus-4-8"}}
    msg = SimpleNamespace(model="claude-opus-4-8", content=[
        _block(type="thinking", thinking="", signature="old"),
        _block(type="tool_use", id="early", name="f", input={}),
        _block(type="text", text="partial "),
        _block(**fallback),
        _block(type="thinking", thinking="", signature="new"),
        _block(type="text", text="answer"),
    ])
    parts = ap.parts_from_message(msg)
    assert [type(p).__name__ for p in parts] == ["TextPart", "ReasoningPart", "ReasoningPart", "TextPart"]
    assert parts[1].meta.data == fallback
    assert parts[2].meta.data["signature"] == "new" and parts[2].meta.model == "claude-opus-4-8"
    replayed = ap.build_messages([Message("assistant", parts)])[0]["content"]
    assert [b["type"] for b in replayed] == ["text", "fallback", "thinking", "text"]


def test_a_real_fallback_block_is_stored_with_its_wire_name_from():
    """C1: the SDK model calls the field `from_` (alias `from`); the stored block must replay as `from`."""
    from anthropic.types.beta import BetaMessage

    fallback = {"type": "fallback", "from": {"model": "claude-opus-5-5"},
                "to": {"model": "claude-opus-4-8"}, "trigger": {"type": "refusal"}}
    real = BetaMessage.model_validate({
        "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-4-8",
        "content": [fallback, {"type": "text", "text": "answer"}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 2},
    })
    parts = ap.parts_from_message(real)
    stored = parts[0].meta.data
    assert "from" in stored and "from_" not in stored
    assert stored["type"] == "fallback"
    # survives the persisted transcript and replays verbatim
    reloaded = load_parts(dump_parts(parts))
    assert ap.build_messages([Message("assistant", reloaded)])[0]["content"][0] == stored
    # and the SDK itself accepts the stored block back
    BetaMessage.model_validate({
        "id": "msg_2", "type": "message", "role": "assistant", "model": "claude-opus-4-8",
        "content": [stored], "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 2}})


def test_the_signature_retry_strips_only_thinking_and_keeps_the_fallback_block():
    """C7: one predicate, reusable by a persisted drop_reasoning."""
    def part(kind):
        return ReasoningPart("", ProviderMeta("anthropic", "m", {"type": kind}))

    assert is_anthropic_thinking_part(part("thinking"))
    assert is_anthropic_thinking_part(part("redacted_thinking"))
    assert not is_anthropic_thinking_part(part("fallback"))
    assert not is_anthropic_thinking_part(ReasoningPart("x", ProviderMeta("gemini", "m", {"type": "thinking"})))
    assert not is_anthropic_thinking_part(ReasoningPart("x"))
    assert not is_anthropic_thinking_part(TextPart("x"))

    req = Request(model=REF, messages=[Message("assistant", [
        part("thinking"), part("fallback"), part("redacted_thinking"), TextPart("t")])])
    kept = ap._strip_reasoning(req).messages[0].parts
    assert [getattr(p.meta, "data", {}).get("type", "text") if isinstance(p, ReasoningPart) else "text"
            for p in kept] == ["fallback", "text"]


def test_refusal_is_a_plain_sentence():
    msg = SimpleNamespace(stop_reason="refusal", stop_details=SimpleNamespace(category="cyber"))
    with pytest.raises(LLMProviderError) as exc:
        ap.raise_for_refusal(msg)
    assert "Claude declined" in str(exc.value)
    assert "cyber" in exc.value.provider_detail


# --- client factory, generate, list_models (no network) -----------------------------------

def test_client_gets_explicit_base_url_even_when_the_shell_sets_one(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://evil.example")
    monkeypatch.setattr(llm, "get_anthropic_key", lambda: "sk-ant-test")
    monkeypatch.setattr(llm, "_anthropic_clients", {})
    client = llm._get_anthropic_client()
    assert str(client.base_url).rstrip("/") == "https://api.anthropic.com"
    assert client.api_key == "sk-ant-test"
    assert llm._get_anthropic_client() is client


def test_no_key_is_a_user_sentence(monkeypatch):
    monkeypatch.setattr(llm, "get_anthropic_key", lambda: None)
    with pytest.raises(LLMProviderError) as exc:
        llm._get_anthropic_client()
    assert str(exc.value) == llm.NO_ANTHROPIC_KEY_MESSAGE


def test_registry_loads_the_anthropic_adapter():
    assert registry.get("anthropic").name == "anthropic"


def _final(**kw):
    base = dict(model="claude-opus-5-5", stop_reason="end_turn",
                content=[_block(type="text", text="ok")],
                usage=SimpleNamespace(input_tokens=3, output_tokens=4,
                                      cache_read_input_tokens=1, cache_creation_input_tokens=0))
    base.update(kw)
    return SimpleNamespace(**base)


class _Stream:
    def __init__(self, final):
        self.final = final

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.final


def _client_returning(*finals_or_excs, seen):
    queue = list(finals_or_excs)

    def stream(**kw):
        seen.append(kw)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return _Stream(item)

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    client.with_options = lambda **kw: client
    return client


def test_generate_maps_the_final_message(monkeypatch):
    seen = []
    monkeypatch.setattr(llm, "_get_anthropic_client", lambda: _client_returning(_final(), seen=seen))
    resp = ap.AnthropicProvider().generate(Request(model=REF, messages=[Message("user", [TextPart("hi")])]))
    assert resp.text == "ok" and resp.model == "claude-opus-5-5" and resp.stop_reason == "end_turn"
    assert (resp.usage.input, resp.usage.output, resp.usage.cache_read) == (3, 4, 1)
    assert not resp.reasoning_dropped and seen[0]["model"] == "claude-opus-5-5"


def test_generate_refusal_raises(monkeypatch):
    monkeypatch.setattr(llm, "_get_anthropic_client", lambda: _client_returning(
        _final(stop_reason="refusal", stop_details=SimpleNamespace(category="x")), seen=[]))
    with pytest.raises(LLMProviderError, match="Claude declined"):
        ap.AnthropicProvider().generate(Request(model=REF, messages=[Message("user", [TextPart("hi")])]))


def _status_error(cls, status, message):
    import httpx
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(message, response=httpx.Response(status, request=req), body=None)


def test_signature_400_retries_once_without_thinking_and_flags_it(monkeypatch):
    import anthropic
    seen = []
    err = _status_error(anthropic.BadRequestError, 400, "Invalid `signature` in `thinking` block")
    client = _client_returning(err, _final(), seen=seen)
    monkeypatch.setattr(llm, "_get_anthropic_client", lambda: client)
    thinking = ReasoningPart("", ProviderMeta("anthropic", "m", {"type": "thinking", "signature": "s"}))
    req = Request(model=REF, messages=[Message("user", [TextPart("a")]),
                                       Message("assistant", [thinking, TextPart("b")]),
                                       Message("user", [TextPart("c")])])
    resp = ap.AnthropicProvider().generate(req)
    assert resp.reasoning_dropped
    assert any(b["type"] == "thinking" for b in seen[0]["messages"][1]["content"])
    assert all(b["type"] != "thinking" for b in seen[1]["messages"][1]["content"])


def test_other_400s_and_statuses_become_user_sentences(monkeypatch):
    import anthropic
    err = _status_error(anthropic.APIStatusError, 529, "overloaded")
    monkeypatch.setattr(llm, "_get_anthropic_client", lambda: _client_returning(err, seen=[]))
    with pytest.raises(LLMProviderError) as exc:
        ap.AnthropicProvider().generate(Request(model=REF, messages=[Message("user", [TextPart("hi")])]))
    assert "the provider is overloaded" in str(exc.value)

    err = _status_error(anthropic.AuthenticationError, 401, "bad key")
    monkeypatch.setattr(llm, "_get_anthropic_client", lambda: _client_returning(err, seen=[]))
    with pytest.raises(LLMProviderError, match="Claude refused your API key"):
        ap.AnthropicProvider().generate(Request(model=REF, messages=[Message("user", [TextPart("hi")])]))


def test_list_models_rows_are_sorted_dicts(monkeypatch):
    models = [SimpleNamespace(id="claude-b", display_name="B"), SimpleNamespace(id="claude-a")]
    client = SimpleNamespace(models=SimpleNamespace(list=lambda: models))
    monkeypatch.setattr(llm, "_get_anthropic_client", lambda: client)
    assert ap.AnthropicProvider().list_models() == [
        {"id": "claude-a", "label": "claude-a", "provider": "anthropic"},
        {"id": "claude-b", "label": "B", "provider": "anthropic"}]


# --- stream (Task 9) ------------------------------------------------------------------------

class _EventStream(_Stream):
    """Fake SDK stream: iterating yields events (optionally raising part-way), exit is recorded."""

    def __init__(self, final, events=(), boom=None):
        super().__init__(final)
        self.events, self.boom, self.closed = list(events), boom, False

    def __iter__(self):
        yield from self.events
        if self.boom is not None:
            raise self.boom

    def __exit__(self, *a):
        self.closed = True
        return False


def _text(t):
    return SimpleNamespace(type="text", text=t)


def _stream_client(*items, seen):
    """items: _EventStream, or an Exception raised when stream() is called."""
    queue = list(items)

    def stream(**kw):
        seen.append(kw)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    client.with_options = lambda **kw: client
    return client


def _use(monkeypatch, client):
    monkeypatch.setattr(llm, "_get_anthropic_client", lambda: client)


_HI = Request(model=REF, messages=[Message("user", [TextPart("hi")])])


def _with_thinking_history():
    thinking = ReasoningPart("", ProviderMeta("anthropic", "m", {"type": "thinking", "signature": "s"}))
    return Request(model=REF, messages=[Message("user", [TextPart("a")]),
                                        Message("assistant", [thinking, TextPart("b")]),
                                        Message("user", [TextPart("c")])])


def test_stream_yields_deltas_in_order_then_one_finish_with_ordered_parts(monkeypatch):
    final = _final(content=[_block(type="thinking", thinking="hm", signature="s"),
                            _block(type="text", text="Hello world"),
                            _block(type="tool_use", id="t1", name="f", input={"a": 1})],
                   stop_reason="tool_use")
    s = _EventStream(final, [_text("Hello "), SimpleNamespace(type="thinking", thinking="x"), _text("world")])
    seen = []
    _use(monkeypatch, _stream_client(s, seen=seen))
    events = list(ap.AnthropicProvider().stream(_HI))
    assert [e.text for e in events[:-1]] == ["Hello ", "world"]
    assert all(isinstance(e, TextDelta) for e in events[:-1])
    fin = events[-1]
    assert isinstance(fin, Finish) and len(events) == 3
    assert [type(p).__name__ for p in fin.message.parts] == ["ReasoningPart", "TextPart", "ToolCallPart"]
    assert (fin.stop_reason, fin.model, fin.reasoning_dropped) == ("tool_use", "claude-opus-5-5", False)
    assert fin.usage.input == 3 and s.closed
    assert seen[0]["max_tokens"] == ap.STREAM_MAX_TOKENS


def test_stream_refusal_raises_after_the_deltas(monkeypatch):
    final = _final(stop_reason="refusal", stop_details=SimpleNamespace(category="x"))
    _use(monkeypatch, _stream_client(_EventStream(final, [_text("par")]), seen=[]))
    got = []
    with pytest.raises(LLMProviderError, match="Claude declined"):
        for e in ap.AnthropicProvider().stream(_HI):
            got.append(e)
    assert [e.text for e in got] == ["par"]


def test_stream_signature_400_at_open_retries_without_thinking_and_never_duplicates_text(monkeypatch):
    import anthropic
    seen = []
    err = _status_error(anthropic.BadRequestError, 400, "Invalid `signature` in `thinking` block")
    ok = _EventStream(_final(), [_text("ok")])
    _use(monkeypatch, _stream_client(err, ok, seen=seen))
    events = list(ap.AnthropicProvider().stream(_with_thinking_history()))
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["ok"]
    assert events[-1].reasoning_dropped is True
    assert any(b["type"] == "thinking" for b in seen[0]["messages"][1]["content"])
    assert all(b["type"] != "thinking" for b in seen[1]["messages"][1]["content"])
    assert len(seen) == 2


def test_stream_400_after_a_delta_is_not_retried(monkeypatch):
    import anthropic
    err = _status_error(anthropic.BadRequestError, 400, "Invalid `signature` in `thinking` block")
    bad = _EventStream(_final(), [_text("partial")], boom=err)
    seen = []
    _use(monkeypatch, _stream_client(bad, seen=seen))
    got = []
    with pytest.raises(LLMProviderError):
        for e in ap.AnthropicProvider().stream(_with_thinking_history()):
            got.append(e)
    assert [e.text for e in got] == ["partial"] and len(seen) == 1 and bad.closed


@pytest.mark.parametrize("use_stream", [False, True])
def test_signature_400_without_reasoning_to_strip_is_not_retried(monkeypatch, use_stream):
    import anthropic
    seen = []
    err = _status_error(anthropic.BadRequestError, 400, "thinking is not supported for this model")
    if use_stream:
        _use(monkeypatch, _stream_client(err, _EventStream(_final()), seen=seen))
        call = lambda: list(ap.AnthropicProvider().stream(_HI))  # noqa: E731
    else:
        _use(monkeypatch, _client_returning(err, _final(), seen=seen))
        call = lambda: ap.AnthropicProvider().generate(_HI)  # noqa: E731
    with pytest.raises(LLMProviderError):
        call()
    assert len(seen) == 1


def test_stream_unrelated_400_and_statuses_map_to_user_sentences(monkeypatch):
    import anthropic
    err = _status_error(anthropic.AuthenticationError, 401, "bad key")
    _use(monkeypatch, _stream_client(err, seen=[]))
    with pytest.raises(LLMProviderError, match="Claude refused your API key"):
        list(ap.AnthropicProvider().stream(_HI))


def _transport_errors():
    import anthropic
    import httpx
    import httpx2
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return [
        anthropic.APIConnectionError(request=req),
        anthropic.APITimeoutError(request=req),
        httpx.ReadError("reset"),
        httpx.ReadTimeout("slow"),
        httpx2.ReadError("reset"),
        httpx2.ReadTimeout("slow"),
    ]


@pytest.mark.parametrize("idx", range(6))
def test_mid_stream_transport_errors_become_llm_provider_errors_and_close_the_stream(monkeypatch, idx):
    boom = _transport_errors()[idx]
    s = _EventStream(_final(), [_text("a")], boom=boom)
    _use(monkeypatch, _stream_client(s, seen=[]))
    got = []
    with pytest.raises(LLMProviderError):
        for e in ap.AnthropicProvider().stream(_HI):
            got.append(e)
    assert [e.text for e in got] == ["a"] and s.closed


def test_early_stop_closes_the_sdk_stream(monkeypatch):
    s = _EventStream(_final(), [_text("a"), _text("b")])
    _use(monkeypatch, _stream_client(s, seen=[]))
    gen = ap.AnthropicProvider().stream(_HI)
    assert next(gen).text == "a"
    gen.close()
    assert s.closed


@pytest.mark.parametrize("kind,words", [("overloaded_error", "the provider is overloaded"),
                                        ("rate_limit_error", "too many requests"),
                                        ("api_error", "the provider had an error")])
def test_mid_stream_error_event_maps_by_error_type_not_status_200(monkeypatch, kind, words):
    import anthropic
    import httpx2
    body = {"type": "error", "error": {"type": kind, "message": "x"}}
    resp = httpx2.Response(200, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    boom = anthropic.Anthropic(api_key="k")._make_status_error(str(body), body=body, response=resp)
    assert boom.status_code == 200
    s = _EventStream(_final(), [_text("a")], boom=boom)
    _use(monkeypatch, _stream_client(s, seen=[]))
    with pytest.raises(LLMProviderError) as exc:
        list(ap.AnthropicProvider().stream(_HI))
    assert words in str(exc.value) and "error 200" not in str(exc.value) and "refused your API key" not in str(exc.value)
    assert s.closed


# Live check 2026-10-11: a key on an account with no API credit gets a 400, not a 429.
def test_an_account_out_of_credit_says_so(monkeypatch):
    import anthropic

    from app.services import llm

    exc = _status_error(anthropic.BadRequestError, 400,
                        "Your credit balance is too low to access the Anthropic API. Please go to "
                        "Plans & Billing to upgrade or purchase credits.")

    class Client:
        class beta:
            class messages:
                @staticmethod
                def stream(**_):
                    raise exc

    monkeypatch.setattr(llm, "_get_anthropic_client", lambda: Client())
    with pytest.raises(LLMProviderError, match="out of credit"):
        ap.AnthropicProvider().generate(Request(model=REF, messages=[Message("user", [TextPart("hi")])]))


def test_out_of_credit_is_not_a_verdict_on_the_model():
    from app.services import llm_capabilities

    err = LLMProviderError("x", provider_detail="Error code: 400 - Your credit balance is too low "
                                                "to access the Anthropic API.")
    assert llm_capabilities._never_reached_the_model(err)

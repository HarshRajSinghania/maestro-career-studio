from copy import deepcopy
from app.models.base_resume import BaseResume
from app.models.chat import ChatSession
from app.services import base_resume_render, chat_agent
from app.services.llm_providers.types import (
    ReasoningPart,
    ProviderMeta,
    TextPart,
    ToolCallPart,
    ToolResultPart,
    load_parts,
)
from app.services.resume_versions import get_versions
from tests.fake_provider import FakeProvider, install

SAMPLE_DATA = {
    "contact": {"name": "Sample", "email": "a@example.com"},
    "summary": "Summary",
    "skills": [],
    "experience": [
        {
            "company": "Acme",
            "role": "DS",
            "start_date": "2020",
            "enabled": True,
            "bullets": ["Built pipeline."],
        }
    ],
    "projects": [],
    "education": [],
    "certifications": [],
}


def _seed(db_session, monkeypatch, tmp_path):
    from app.services import resume_ops

    monkeypatch.setattr(base_resume_render, "render_base_resume", lambda *a, **k: None)
    monkeypatch.setattr(
        resume_ops.base_resume_render, "render_base_resume", lambda *a, **k: None
    )

    monkeypatch.setattr(resume_ops.settings, "base_resumes_dir", tmp_path)
    db_session.add(BaseResume(slug="ds", display_name="DS", data_json=deepcopy(SAMPLE_DATA)))
    session = ChatSession()
    db_session.add(session)
    db_session.commit()
    return session


def _run(db_session, session, text, provider, context=None):
    return list(
        chat_agent.run_turn(db_session, session, text, context, provider=provider, model="fake-model")
    )


def _edit_call(call_id="call_1", **extra):
    return ToolCallPart(
        call_id,
        "edit_resume",
        {"kind": "base", "key": "ds", "ops": [{"kind": "replace_summary", "value": "Better."}]},
        **extra,
    )


def test_plain_text_turn_streams_and_persists(db_session, monkeypatch, tmp_path):
    session = _seed(db_session, monkeypatch, tmp_path)
    provider = FakeProvider([["Hel", "lo"]])

    events = _run(db_session, session, "hi", provider)

    kinds = [e["type"] for e in events]
    assert kinds == ["message", "delta", "delta", "message", "done"]
    assert "".join(e["text"] for e in events if e["type"] == "delta") == "Hello"
    roles = [m.role for m in session.messages]
    assert roles == ["user", "assistant"]
    assert session.messages[1].content == "Hello"
    assert session.title == "hi"


def test_assistant_rows_persist_parts_provider_and_model(db_session, monkeypatch, tmp_path):
    session = _seed(db_session, monkeypatch, tmp_path)
    install(monkeypatch, FakeProvider([["Hello"]]))

    list(chat_agent.run_turn(db_session, session, "hi", None, model="fake-model"))

    user, assistant = session.messages
    assert load_parts(user.parts) == [TextPart("hi")]
    assert load_parts(assistant.parts) == [TextPart("Hello")]
    assert (assistant.provider, assistant.model) == ("fake", "fake-model")


def test_tool_call_turn_edits_resume_and_emits_change_card(db_session, monkeypatch, tmp_path):
    session = _seed(db_session, monkeypatch, tmp_path)
    provider = FakeProvider([[[_edit_call()]], ["Done — tightened the summary."]])

    events = _run(
        db_session, session, "tighten my summary", provider,
        {"target_kind": "base", "target_key": "ds"},
    )

    card = next(e for e in events if e["type"] == "change_card")
    assert card["resume_key"] == "ds" and card["version_number"] == 1

    versions = get_versions(db_session, "base", "ds")
    assert versions[0].source == "chat"
    # source_ref points at the triggering user message
    user_msg = session.messages[0]
    assert versions[0].source_ref == str(user_msg.id)

    # second LLM round got the tool result in its message array
    second = provider.requests[1].messages
    results = [p for m in second if m.role == "tool" for p in m.parts]
    assert any(isinstance(p, ToolResultPart) and "change_card" in p.content for p in results)
    # the context block rides as the first text part of the user message
    assert "Pinned resume" in second[0].parts[0].text
    assert second[0].parts[-1].text == "tighten my summary"
    assert user_msg.content == "tighten my summary"

    roles = [m.role for m in session.messages]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert events[-1]["type"] == "done"
    # the loop continues on the calls in the reply, whatever stop_reason says
    assert session.messages[1].content is None
    assert session.messages[2].tool_call_id is None  # no longer written (parts carry it)


def test_kb_capture_tool_result_emits_and_persists_card_meta(
    db_session, monkeypatch, tmp_path,
):
    session = _seed(db_session, monkeypatch, tmp_path)
    payload = {
        "entity_id": "89269a3d-f567-43b9-9d5f-630acfd2e6c3",
        "entity_title": "Orbit",
        "point_count": 2,
    }
    monkeypatch.setattr(
        chat_agent,
        "execute_tool",
        lambda ctx, name, arguments: {"kb_capture": payload},
    )
    provider = FakeProvider([
        [[ToolCallPart("call_kb", "kb_capture", {"text": "shipped it"})]],
        ["Saved it to your Career KB inbox."],
    ])

    events = _run(db_session, session, "we shipped it", provider)

    card = next(event for event in events if event["type"] == "kb_capture")
    assert {key: card[key] for key in payload} == payload
    tool_message = next(message for message in session.messages if message.role == "tool")
    assert tool_message.meta_json == {"kb_capture": payload}


def test_scope_violation_flows_back_as_tool_error(db_session, monkeypatch, tmp_path):
    session = _seed(db_session, monkeypatch, tmp_path)
    provider = FakeProvider([[[_edit_call()]], ["That edit is outside your selection."]])

    events = _run(
        db_session, session, "rewrite", provider,
        {
            "target_kind": "base",
            "target_key": "ds",
            "selections": [{"section": "experience", "index": 0}],
        },
    )

    assert not any(e["type"] == "change_card" for e in events)
    assert get_versions(db_session, "base", "ds") == []
    tool_msg = next(m for m in session.messages if m.role == "tool")
    assert "outside the user's selected scope" in tool_msg.content


def test_unparseable_tool_arguments_flow_back_as_a_tool_error(db_session, monkeypatch, tmp_path):
    session = _seed(db_session, monkeypatch, tmp_path)
    provider = FakeProvider([[[ToolCallPart("call_1", "edit_resume", None)]], ["Sorry."]])

    _run(db_session, session, "go", provider)

    tool_msg = next(m for m in session.messages if m.role == "tool")
    assert "not valid JSON" in tool_msg.content


def test_llm_failure_yields_error_event(db_session, monkeypatch, tmp_path):
    session = _seed(db_session, monkeypatch, tmp_path)
    provider = FakeProvider([])

    def boom(req):
        raise RuntimeError("api down")

    provider.stream = boom

    events = _run(db_session, session, "hi", provider)
    assert events[-1]["type"] == "error"
    assert "api down" in events[-1]["detail"]


def test_a_stream_that_ends_without_finish_is_a_clear_error(db_session, monkeypatch, tmp_path):
    session = _seed(db_session, monkeypatch, tmp_path)
    provider = FakeProvider([])
    provider.stream = lambda req: iter(())

    events = _run(db_session, session, "hi", provider)

    assert events[-1]["type"] == "error"
    assert "without finishing" in events[-1]["detail"]


def test_step_budget_persists_json_parts(db_session, monkeypatch, tmp_path):
    session = _seed(db_session, monkeypatch, tmp_path)
    monkeypatch.setattr(chat_agent, "MAX_TOOL_ROUNDS", 2)
    monkeypatch.setattr(chat_agent, "execute_tool", lambda ctx, name, arguments: {"ok": True})
    provider = FakeProvider([[[ToolCallPart(f"c{i}", "get_career_context", {})]] for i in range(2)])

    events = _run(db_session, session, "loop", provider)

    assert [e["type"] for e in events][-2:] == ["message", "done"]
    last = session.messages[-1]
    assert last.role == "assistant" and "too many steps" in last.content
    assert load_parts(last.parts) == [TextPart(last.content)]
    db_session.refresh(last)  # round-trips through JSON, not dataclasses
    assert last.parts["parts"][0]["type"] == "text"


def test_run_turn_without_a_provider_resolves_it_through_the_registry(db_session, monkeypatch, tmp_path):
    session = _seed(db_session, monkeypatch, tmp_path)
    provider = install(monkeypatch, FakeProvider([["ok"]]))

    list(chat_agent.run_turn(db_session, session, "hi", None, model="fake-model"))

    assert provider.requests[0].model.provider == "fake"
    assert provider.requests[0].cache is True
    assert [t.name for t in provider.requests[0].tools][:1]  # the Assistant's toolset


def test_context_block_is_persisted_once_and_replayed_byte_identical(
    db_session, monkeypatch, tmp_path,
):
    session = _seed(db_session, monkeypatch, tmp_path)
    provider = FakeProvider([["one"], ["two"]])
    ctx = {"target_kind": "base", "target_key": "ds"}

    _run(db_session, session, "first", provider, ctx)
    user = session.messages[0]
    stored = load_parts(user.parts)
    assert len(stored) == 2 and "Pinned resume" in stored[0].text
    assert stored[1] == TextPart("first") and user.content == "first"

    _run(db_session, session, "second", provider, None)  # a later turn has no context

    first_req, second_req = provider.requests
    assert second_req.messages[0] == first_req.messages[0]
    assert second_req.messages[:2] == first_req.messages + [second_req.messages[1]]
    assert second_req.system == first_req.system


def test_reasoning_dropped_strips_stored_thinking_and_the_next_request(
    db_session, monkeypatch, tmp_path,
):
    session = _seed(db_session, monkeypatch, tmp_path)
    thinking = ReasoningPart("", ProviderMeta("anthropic", "claude-fake", {"type": "thinking", "signature": "s"}))
    fallback = ReasoningPart("", ProviderMeta("anthropic", "claude-fake", {"type": "fallback"}))
    provider = FakeProvider(
        [["one", [thinking, fallback]], (["two"], {"reasoning_dropped": True}), ["three"]],
        name="anthropic",
    )
    install(monkeypatch, provider, model="claude-fake")

    def go(text):
        return list(chat_agent.run_turn(db_session, session, text, None, model="claude-fake"))

    go("a")
    assert any(isinstance(p, ReasoningPart) for p in load_parts(session.messages[1].parts))
    go("b")
    stored = [p for p in load_parts(session.messages[1].parts) if isinstance(p, ReasoningPart)]
    assert stored == [fallback]  # only the signed thinking block is dropped
    go("c")
    sent = provider.requests[2].messages
    assert not any(
        isinstance(p, ReasoningPart) and p.meta.data.get("type") == "thinking"
        for m in sent for p in m.parts
    )


def test_a_turn_after_an_errored_turn_replays_a_synthetic_error_result(
    db_session, monkeypatch, tmp_path,
):
    session = _seed(db_session, monkeypatch, tmp_path)

    def boom(ctx, name, arguments):
        raise RuntimeError("tool exploded")

    monkeypatch.setattr(chat_agent, "execute_tool", boom)
    provider = FakeProvider([[[ToolCallPart("call_9", "edit_resume", {})]], ["recovered"]])

    first = _run(db_session, session, "go", provider)
    assert first[-1]["type"] == "error"
    assert [m.role for m in session.messages] == ["user", "assistant"]  # the call was kept

    _run(db_session, session, "again", provider)

    sent = provider.requests[1].messages
    assert [m.role for m in sent] == ["user", "assistant", "tool", "user"]
    result = sent[2].parts[0]
    assert result.tool_call_id == "call_9" and result.is_error

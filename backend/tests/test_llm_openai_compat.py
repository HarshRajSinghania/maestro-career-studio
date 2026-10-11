from app.services.llm_providers import openai_compat as oc
from app.services.llm_providers.types import ImagePart, Message, ModelRef, Request, TextPart


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

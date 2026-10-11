"""Model discovery per provider (Task 7). No network: the SDK client and urlopen are stubbed."""

import io
import json
from types import SimpleNamespace
from urllib.error import HTTPError

import httpx
import openai
import pytest

from app.services import llm
from app.services.llm_providers import anthropic as ap
from app.services.llm_providers import openai_compat as oc
from app.services.llm_providers.errors import LLMProviderError
from app.services.llm_providers.openai_compat import OpenAICompatProvider


def _adapter(name, client, label="OpenAI"):
    return OpenAICompatProvider(name, lambda: client, lambda: True, lambda: label)


def test_openai_list_filters_non_chat_ids_dedupes_sorts_and_tags_with_the_provider_name():
    page = SimpleNamespace(data=[SimpleNamespace(id=i) for i in
                                 ["gpt-b", "text-embedding-3-small", "gpt-a", "gpt-a", "whisper-1"]])
    client = SimpleNamespace(models=SimpleNamespace(list=lambda: page))
    assert _adapter("openai", client).list_models() == [
        {"id": "gpt-a", "label": "gpt-a", "provider": "openai"},
        {"id": "gpt-b", "label": "gpt-b", "provider": "openai"}]


def test_openai_list_failure_is_a_user_sentence():
    err = openai.AuthenticationError(
        "bad", response=httpx.Response(401, request=httpx.Request("GET", "http://x")), body=None)

    def boom():
        raise err

    client = SimpleNamespace(models=SimpleNamespace(list=boom))
    with pytest.raises(LLMProviderError, match="OpenAI refused your API key"):
        _adapter("openai", client).list_models()


def test_chatlike_filter():
    assert oc._openai_model_is_chatlike("gpt-4o") is True
    assert oc._openai_model_is_chatlike("text-embedding-3-large") is False
    assert oc._openai_model_is_chatlike("whisper-1") is False


# --- OpenRouter: plain GET, zdr=true, links.next paging --------------------------------------

class _Resp:
    def __init__(self, doc):
        self._b = json.dumps(doc).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._b


def _openrouter(monkeypatch, pages, seen):
    monkeypatch.setattr(llm, "get_openrouter_key", lambda: "sk-or-test")

    def fake_urlopen(request, timeout=None):
        seen.append((request.full_url, request.get_header("Authorization")))
        return _Resp(pages[len(seen) - 1])

    monkeypatch.setattr(oc, "urlopen", fake_urlopen)
    return _adapter("openrouter", None, label="OpenRouter")


def test_openrouter_list_follows_links_next_with_zdr_and_bearer(monkeypatch):
    base = llm.OPENROUTER_BASE_URL
    pages = [
        {"data": [{"id": "vendor/b", "name": "B"}, {"id": "vendor/a"}], "total_count": 3,
         "links": {"next": f"{base}/models?zdr=true&page=2"}},
        {"data": [{"id": "vendor/c", "name": "C"}], "links": {"next": None}},
    ]
    seen = []
    rows = _openrouter(monkeypatch, pages, seen).list_models()
    assert [u for u, _ in seen] == [f"{base}/models?zdr=true", f"{base}/models?zdr=true&page=2"]
    assert {a for _, a in seen} == {"Bearer sk-or-test"}
    assert rows == [
        {"id": "vendor/a", "label": "vendor/a", "provider": "openrouter"},
        {"id": "vendor/b", "label": "B", "provider": "openrouter"},
        {"id": "vendor/c", "label": "C", "provider": "openrouter"}]


def test_openrouter_never_sends_the_key_to_another_host(monkeypatch):
    pages = [{"data": [{"id": "v/a"}], "links": {"next": "https://evil.example/models?page=2"}}]
    seen = []
    rows = _openrouter(monkeypatch, pages, seen).list_models()
    assert len(seen) == 1 and [r["id"] for r in rows] == ["v/a"]


def test_openrouter_paging_is_capped_at_twenty_pages(monkeypatch):
    base = llm.OPENROUTER_BASE_URL
    pages = [{"data": [{"id": f"v/{i}"}], "links": {"next": f"{base}/models?page={i + 2}"}}
             for i in range(30)]
    seen = []
    rows = _openrouter(monkeypatch, pages, seen).list_models()
    assert len(seen) == 20 and len(rows) == 20


def test_openrouter_without_a_key_is_the_openrouter_sentence(monkeypatch):
    monkeypatch.setattr(llm, "get_openrouter_key", lambda: None)
    with pytest.raises(LLMProviderError, match="No OpenRouter API key"):
        _adapter("openrouter", None, label="OpenRouter").list_models()


def test_openrouter_http_error_is_a_user_sentence(monkeypatch):
    monkeypatch.setattr(llm, "get_openrouter_key", lambda: "sk-or-test")

    def boom(request, timeout=None):
        raise HTTPError(request.full_url, 401, "no", {}, io.BytesIO(b"bad key"))

    monkeypatch.setattr(oc, "urlopen", boom)
    with pytest.raises(LLMProviderError, match="OpenRouter refused your API key"):
        _adapter("openrouter", None, label="OpenRouter").list_models()


# --- Claude: SDK errors never escape ---------------------------------------------------------

def _anthropic_client_raising(exc):
    def boom():
        raise exc

    return SimpleNamespace(models=SimpleNamespace(list=boom))


def _status_error(cls, status):
    resp = httpx.Response(status, request=httpx.Request("GET", "http://x"))
    return cls("nope", response=resp, body=None)


def test_claude_list_models_maps_a_refused_key(monkeypatch):
    import anthropic
    client = _anthropic_client_raising(_status_error(anthropic.AuthenticationError, 401))
    monkeypatch.setattr(llm, "_get_anthropic_client", lambda: client)
    with pytest.raises(LLMProviderError, match="Claude refused your API key"):
        ap.AnthropicProvider().list_models()


def test_claude_list_models_maps_connection_failures(monkeypatch):
    import anthropic
    exc = anthropic.APIConnectionError(request=httpx.Request("GET", "http://x"))
    monkeypatch.setattr(llm, "_get_anthropic_client", lambda: _anthropic_client_raising(exc))
    with pytest.raises(LLMProviderError, match="no connection"):
        ap.AnthropicProvider().list_models()

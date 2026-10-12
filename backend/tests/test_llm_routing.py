import pytest

from app.services import llm, model_settings
from app.services.llm_providers import registry


@pytest.fixture
def keys(monkeypatch):
    present = {}
    monkeypatch.setattr(llm, "get_anthropic_key", lambda: present.get("anthropic"))
    monkeypatch.setattr(llm, "get_gemini_key", lambda: present.get("gemini"))
    monkeypatch.setattr(llm, "get_openrouter_key", lambda: present.get("openrouter"))
    return present


def test_catalog_tag_wins(db_session, keys):
    model_settings.add_extra_model(db_session, "anthropic/claude-opus-5-5", "openrouter")
    assert registry.resolve("anthropic/claude-opus-5-5").provider == "openrouter"


def test_claude_prefix_needs_a_claude_key(db_session, keys):
    assert registry.resolve("claude-custom-x").provider == "openai"
    keys["anthropic"] = "sk-ant-x"
    assert registry.resolve("claude-custom-x").provider == "anthropic"


def test_seeded_gemini_routes_to_gemini_even_without_key(db_session, keys):
    # Seed tag wins, so a missing key yields the Gemini "no key" sentence, as today.
    assert registry.resolve("gemini-3.7-flash").provider == "gemini"


def test_unknown_ids_go_to_the_openai_compatible_client(db_session, keys):
    assert registry.resolve("llama3.2:3b").provider == "openai"


def test_unregistered_gemini_prefix_needs_a_gemini_key(db_session, keys):
    assert registry.resolve("gemini-9-new").provider == "openai"
    keys["gemini"] = "g-key"
    assert registry.resolve("models/gemini-9-new").provider == "gemini"


def test_seeded_claude_ids_route_to_anthropic_without_a_key(db_session, keys):
    assert registry.resolve("claude-sonnet-5-5").provider == "anthropic"


def test_key_table_and_getters(db_session, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "env-ant")
    monkeypatch.setattr(settings, "openrouter_api_key", "")
    assert llm.get_anthropic_key() == "env-ant"
    assert llm.get_openrouter_key() is None
    model_settings.set_api_keys(db_session, {"openrouter": " sk-or "})
    assert llm.get_openrouter_key() == "sk-or"
    model_settings.set_api_keys(db_session, {"openrouter": ""})
    assert model_settings.get_api_key("openrouter", db_session) is None


def test_any_model_key_configured(db_session, monkeypatch):
    for name in ("get_base_url", "get_openai_key", "get_gemini_key",
                 "get_anthropic_key", "get_openrouter_key"):
        monkeypatch.setattr(llm, name, lambda: None)
    assert not llm.any_model_key_configured()
    monkeypatch.setattr(llm, "get_anthropic_key", lambda: "k")
    assert llm.any_model_key_configured()
    monkeypatch.setattr(llm, "get_anthropic_key", lambda: None)
    monkeypatch.setattr(llm, "get_base_url", lambda: "http://localhost:11434/v1")
    assert llm.any_model_key_configured()

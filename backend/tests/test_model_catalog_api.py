"""Router coverage for model catalog sync / add / remove."""


import pytest

from app.services.llm_providers import registry
from app.services.llm_providers.errors import LLMProviderError


def test_openai_info_includes_source_on_options(db_session, client):
    body = client.get("/api/settings/openai").json()

    assert all(opt["source"] == "seed" for opt in body["model_options"])
    assert "gemini-3.7-flash" in {opt["id"] for opt in body["model_options"]}


def test_add_and_remove_catalog_model(db_session, client):
    added = client.post(
        "/api/settings/openai/models",
        json={"id": "gpt-4.5-preview", "provider": "openai", "label": "4.5"},
    )
    assert added.status_code == 200
    ids = {opt["id"] for opt in added.json()["model_options"]}
    assert "gpt-4.5-preview" in ids
    extra = next(
        opt for opt in added.json()["model_options"] if opt["id"] == "gpt-4.5-preview"
    )
    assert extra["source"] == "extra"

    removed = client.delete("/api/settings/openai/models/gpt-4.5-preview")
    assert removed.status_code == 200
    assert "gpt-4.5-preview" not in {
        opt["id"] for opt in removed.json()["model_options"]
    }


def test_remove_in_use_returns_400(db_session, client):
    client.post(
        "/api/settings/openai/models",
        json={"id": "gpt-custom", "provider": "openai"},
    )
    client.put(
        "/api/settings/openai",
        json={
            "fast_model": "gpt-custom",
            "smart_model": "gpt-5.6-luna",
            "chat_model": "gpt-5.6-luna",
        },
    )
    resp = client.delete("/api/settings/openai/models/gpt-custom")
    assert resp.status_code == 400
    assert "is your Fast, Smart or Assistant model" in resp.json()["detail"]


class _StubAdapter:
    """Registry stand-in whose list_models returns rows or raises."""

    def __init__(self, name, rows=None, error=None):
        self.name = name
        self._rows = rows
        self._error = error

    def list_models(self):
        if self._error:
            raise self._error
        return self._rows


def test_sync_openai_marks_in_catalog(db_session, monkeypatch, client):
    rows = [
        {"id": "gpt-5.6-luna", "label": "gpt-5.6-luna", "provider": "openai"},
        {"id": "gpt-brand-new", "label": "gpt-brand-new", "provider": "openai"},
    ]
    monkeypatch.setitem(registry._adapters, "openai", _StubAdapter("openai", rows))

    resp = client.post("/api/settings/openai/sync", json={"provider": "openai"})

    assert resp.status_code == 200
    by_id = {row["id"]: row for row in resp.json()["models"]}
    assert by_id["gpt-5.6-luna"]["in_catalog"] is True
    assert by_id["gpt-brand-new"]["in_catalog"] is False


@pytest.mark.parametrize("provider", ["gemini", "anthropic", "openrouter"])
def test_sync_other_providers_use_their_registered_adapter(db_session, monkeypatch, client, provider):
    rows = [{"id": f"{provider}-new", "label": "New", "provider": provider}]
    monkeypatch.setitem(registry._adapters, provider, _StubAdapter(provider, rows))

    resp = client.post("/api/settings/openai/sync", json={"provider": provider})

    assert resp.status_code == 200
    assert resp.json()["models"] == [{**rows[0], "in_catalog": False}]


@pytest.mark.parametrize("provider", ["openai", "gemini", "anthropic", "openrouter"])
def test_sync_502_on_provider_error(db_session, monkeypatch, client, provider):
    stub = _StubAdapter(provider, error=LLMProviderError("no key"))
    monkeypatch.setitem(registry._adapters, provider, stub)

    resp = client.post("/api/settings/openai/sync", json={"provider": provider})

    assert resp.status_code == 502
    assert "no key" in resp.json()["detail"]


def test_extra_model_for_anthropic_is_accepted(db_session, client):
    resp = client.post(
        "/api/settings/openai/models",
        json={"id": "claude-new-1", "provider": "anthropic", "label": "Claude New"},
    )
    assert resp.status_code == 200
    row = next(o for o in resp.json()["model_options"] if o["id"] == "claude-new-1")
    assert row["provider"] == "anthropic" and row["source"] == "extra"

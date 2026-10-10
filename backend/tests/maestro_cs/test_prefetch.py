"""The model prefetch builds the configured model through the scorer's own loader."""
from types import SimpleNamespace

from app.services.ats import embeddings
from maestro_cs import prefetch


def test_prefetch_builds_the_configured_model(monkeypatch):
    built = []
    monkeypatch.setattr(embeddings, "load_config",
                        lambda: SimpleNamespace(weights={"semantic": {"model": "some/model"}}))
    monkeypatch.setattr(embeddings, "_model", built.append)
    assert prefetch.main() == 0
    assert built == ["some/model"]

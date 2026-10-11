"""Model id → provider, and provider name → adapter. The ONE place a model-id prefix is read."""

from __future__ import annotations

from app.services.llm_providers.types import ModelRef, Provider

PROVIDERS: tuple[str, ...] = ("openai", "anthropic", "gemini", "openrouter")

_adapters: dict[str, Provider] = {}


def register(provider: Provider) -> None:
    _adapters[provider.name] = provider


def get(name: str) -> Provider:
    if name not in _adapters:
        _load_builtins()
    try:
        return _adapters[name]
    except KeyError:
        # A catalog entry can name a provider whose adapter lands in a later task (or build):
        # a user sentence and a 502, never a 500.
        from app.services.llm_providers.errors import LLMProviderError
        raise LLMProviderError(f"{name} models are not available in this version.") from None


def _load_builtins() -> None:
    """Import adapters lazily: they import `app.services.llm`, which imports this module."""
    # Filled in by Tasks 2, 3, 5, 6.


def resolve(model: str) -> ModelRef:
    """Task 4 replaces this with the catalog rule. Until then: today's prefix rule."""
    name = model.removeprefix("models/")
    if name.startswith("gemini-"):
        return ModelRef("gemini", model)
    return ModelRef("openai", model)

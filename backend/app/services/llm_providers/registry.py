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
    from app.services import llm
    from app.services.llm_providers.gemini import GeminiProvider
    from app.services.llm_providers.openai_compat import OpenAICompatProvider

    if "openai" not in _adapters:
        register(OpenAICompatProvider(
            "openai",
            client=lambda: llm._get_client(),
            json_mode=lambda: llm._json_mode_supported(),
            label=lambda: llm._openai_provider(),
        ))
    if "gemini" not in _adapters:
        register(GeminiProvider())
    # Tasks 5, 6 add their adapters here.


def resolve(model: str) -> ModelRef:
    """1) catalog tag  2) claude-/gemini- prefix when that provider has a key  3) openai-compat."""
    from app.services import llm, model_settings

    tagged = model_settings.catalog_provider(model)
    if tagged:
        return ModelRef(tagged, model)
    name = model.removeprefix("models/")
    if name.startswith("claude-") and llm.get_anthropic_key():
        return ModelRef("anthropic", model)
    if name.startswith("gemini-") and llm.get_gemini_key():
        return ModelRef("gemini", model)
    return ModelRef("openai", model)

"""A scripted Provider for chat/probe tests: each stream() call pops one script.

A script is a list of strings (text deltas) and at most one trailing list of ToolCallPart.
To make a Finish carry `reasoning_dropped=True` (test-only), pass `reasoning_dropped=True` to
the constructor (every script) or wrap one script as `(items, {"reasoning_dropped": True})`.
"""

from app.services.llm_providers import registry
from app.services.llm_providers.types import Finish, Message, Response, TextDelta, TextPart, Usage


class FakeProvider:
    def __init__(self, scripts, name="fake", reasoning_dropped=False):
        self.name = name
        self._scripts = list(scripts)
        self._reasoning_dropped = reasoning_dropped
        self.requests = []

    def ready(self):
        return None

    def stream(self, req):
        self.requests.append(req)
        script = self._scripts.pop(0)
        dropped = self._reasoning_dropped
        if isinstance(script, tuple):
            script, flags = script
            dropped = flags.get("reasoning_dropped", dropped)
        parts = []
        for item in script:
            if isinstance(item, str):
                parts.append(TextPart(item))
                yield TextDelta(item)
            else:
                parts.extend(item)
        yield Finish(Message("assistant", parts), Usage(1, 1), "end_turn", req.model.model,
                     reasoning_dropped=dropped)

    def generate(self, req):
        *_, finish = list(self.stream(req))
        return Response(finish.message, finish.usage, finish.stop_reason, finish.model,
                        reasoning_dropped=finish.reasoning_dropped)

    def list_models(self):
        return []


def install(monkeypatch, provider, model="fake-model"):
    """Route `model` to `provider` for one test."""
    from app.services.llm_providers.types import ModelRef

    monkeypatch.setitem(registry._adapters, provider.name, provider)
    real = registry.resolve
    monkeypatch.setattr(registry, "resolve",
                        lambda m: ModelRef(provider.name, m) if m == model else real(m))
    return provider

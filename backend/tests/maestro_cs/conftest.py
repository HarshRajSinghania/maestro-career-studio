"""Keeps `maestro status` tests from asking the real login-item system (launchd, schtasks)."""
import pytest

from maestro_cs import autostart, update

_REAL_IS_ENABLED = autostart.is_enabled
_REAL_FETCH_TEXT = update.fetch_text


@pytest.fixture(autouse=True)
def _no_real_autostart_probe(monkeypatch):
    monkeypatch.setattr(autostart, "is_enabled", lambda *a, **k: False)


@pytest.fixture
def real_is_enabled():
    """The module-level function the autouse stub hides (always called with injected arguments)."""
    return _REAL_IS_ENABLED


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """A test that reaches PyPI or GitHub unpatched fails loudly instead of going online."""
    def blocked(*args, **kwargs):
        raise AssertionError("network in tests")

    monkeypatch.setattr(update, "fetch_text", blocked)


@pytest.fixture
def real_fetch_text():
    """The real `update.fetch_text` (its tests patch httpx underneath it)."""
    return _REAL_FETCH_TEXT

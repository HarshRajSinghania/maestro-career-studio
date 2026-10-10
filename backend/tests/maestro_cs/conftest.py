"""Keeps `maestro status` tests from asking the real login-item system (launchd, schtasks)."""
import pytest

from maestro_cs import autostart

_REAL_IS_ENABLED = autostart.is_enabled


@pytest.fixture(autouse=True)
def _no_real_autostart_probe(monkeypatch):
    monkeypatch.setattr(autostart, "is_enabled", lambda *a, **k: False)


@pytest.fixture
def real_is_enabled():
    """The module-level function the autouse stub hides (always called with injected arguments)."""
    return _REAL_IS_ENABLED

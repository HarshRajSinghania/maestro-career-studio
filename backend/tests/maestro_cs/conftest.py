"""Keeps `maestro status` tests from asking the real login-item system (launchd, schtasks)."""
import pytest

from maestro_cs import autostart


@pytest.fixture(autouse=True)
def _no_real_autostart_probe(monkeypatch):
    monkeypatch.setattr(autostart, "is_enabled", lambda *a, **k: False)

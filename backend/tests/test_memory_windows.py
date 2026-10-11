"""Windows has no `resource` module and no /proc: the readout falls back to psutil (docs/plans/2026-10-10-desktop-step3-uv-install.md, Task 2)."""

import importlib
import math
import sys

import pytest


@pytest.fixture
def memory_without_resource(monkeypatch):
    from app.services import memory

    monkeypatch.setitem(sys.modules, "resource", None)  # `import resource` now raises ImportError
    importlib.reload(memory)
    yield memory
    monkeypatch.undo()  # puts the real `resource` back in sys.modules before the second reload
    importlib.reload(memory)


def test_readout_without_resource_reports_finite_positive_numbers(memory_without_resource):
    memory = memory_without_resource

    assert memory.resource is None
    body = memory.readout()

    assert set(body) == {"rss_mb", "peak_mb", "platform"}  # the shape native/common.sh's valid_memory checks
    assert body["platform"] == sys.platform
    for key in ("rss_mb", "peak_mb"):
        assert math.isfinite(body[key]) and body[key] > 0
    assert body["peak_mb"] >= body["rss_mb"]


@pytest.mark.skipif(sys.platform == "win32", reason="a real Windows run has no resource module")
def test_module_still_has_a_resource_name_after_the_fallback_is_undone():
    from app.services import memory

    assert memory.resource is not None  # tests/test_health_memory.py patches memory.resource.getrusage

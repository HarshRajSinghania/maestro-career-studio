"""The version banner: a page older than the backend asks for a reload, any other mismatch
says the update didn't finish."""

from __future__ import annotations

from pathlib import Path

from tests.node_ts import run_node_test

_BANNER = (Path(__file__).resolve().parents[2] / "frontend" / "components" / "version-banner.tsx").read_text(
    encoding="utf-8")


def test_the_version_helpers_pass_their_node_tests():
    result = run_node_test("lib/version.test.ts")
    assert result.returncode == 0, result.stdout + result.stderr


def test_an_older_page_asks_for_a_reload():
    assert "Maestro CS was updated. Reload this page to use the new version." in _BANNER
    assert "window.location.reload()" in _BANNER
    assert "pageIsOlder(FRONTEND_VERSION, data.version)" in _BANNER


def test_any_other_mismatch_still_points_at_how_to_update():
    assert "Maestro CS didn&apos;t finish updating. See How to update in" in _BANNER

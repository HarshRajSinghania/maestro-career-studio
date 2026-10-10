"""MCP shares the real app's lifespan and REST security boundary."""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

HDR = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "t", "version": "1"},
    },
}


@pytest.fixture(autouse=True)
def isolated_startup_dirs(tmp_path, monkeypatch, db_session):
    for name in (
        "data_dir", "base_resumes_dir", "applications_dir", "settings_dir",
        "logs_dir", "exports_dir", "kb_documents_dir",
    ):
        monkeypatch.setattr(settings, name, tmp_path / name)


def test_mcp_initializes_inside_the_app():
    with TestClient(app) as c:
        r = c.post("/mcp", json=INIT, headers=HDR)
    assert r.status_code == 200
    assert r.json()["result"]["serverInfo"]["name"] == "maestro-career-studio"


def test_a_profile_path_initializes_and_a_bare_post_is_not_redirected():
    with TestClient(app) as c:
        assert c.post(
            "/mcp/hunt", json=INIT, headers=HDR, follow_redirects=False
        ).status_code == 200
        assert c.post(
            "/mcp", json=INIT, headers=HDR, follow_redirects=False
        ).status_code == 200


def test_a_forged_host_is_refused_on_mcp():
    assert "evil.example" not in settings.allowed_hosts
    with TestClient(app) as c:
        r = c.post("/mcp", json=INIT, headers={**HDR, "Host": "evil.example"})
    assert r.status_code == 400


def test_a_foreign_origin_is_refused_on_mcp():
    with TestClient(app) as c:
        r = c.post("/mcp", json=INIT, headers={**HDR, "Origin": "https://evil.example"})
    assert r.status_code == 403


@pytest.mark.parametrize("path", ["/mcp", "/mcp/hunt"])
def test_the_public_sync_host_cannot_reach_mcp(monkeypatch, path):
    """Funnel's public name keeps the non-sync host refusal, not a sync 404."""
    public = "mac.tailnet.ts.net"
    # Same per-request settings setup and exact refusal as sync/test_public_host.py.
    monkeypatch.setattr(settings, "sync_public_host", public)
    assert public not in settings.allowed_hosts
    with TestClient(app) as c:
        r = c.post(path, json=INIT, headers={**HDR, "Host": public})
    assert r.status_code == 400
    assert r.text == "Invalid host header"
    assert "access-control-allow-origin" not in r.headers


def test_mcp_is_never_under_the_sync_tree():
    paths = {getattr(r, "path", "") for r in app.router.routes}
    assert "/mcp" in paths
    assert not any(p.startswith("/api/sync") and "mcp" in p for p in paths)


def test_two_lifespans_in_one_process_both_serve_mcp():
    for _ in range(2):
        with TestClient(app) as c:
            assert c.post("/mcp", json=INIT, headers=HDR).status_code == 200


def test_the_host_check_outranks_the_origin_check_on_mcp():
    with TestClient(app) as c:
        r = c.post("/mcp", json=INIT, headers={
            **HDR, "Host": "evil.example", "Origin": "https://evil.example",
        })
    assert r.status_code == 400
    assert r.text == "Invalid host header"


def test_an_allowed_origin_receives_the_mcp_json_response():
    origin = "http://localhost:3000"
    with TestClient(app) as c:
        r = c.post("/mcp", json=INIT, headers={**HDR, "Origin": origin})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == origin
    assert r.json()["result"]["serverInfo"]["name"] == "maestro-career-studio"

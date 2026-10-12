"""Loopback REST calls bypass proxies; compose service names keep their environment."""

import httpx
import pytest

from mcp_server import client


@pytest.fixture
def client_builds(monkeypatch):
    builds = []

    def capture_new_client(**kwargs):
        builds.append(kwargs)
        return httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])),
            **kwargs,
        )

    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:9")
    monkeypatch.setattr(client, "new_client", capture_new_client)
    return builds


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:8001",
        "http://localhost:8001",
        "http://LOCALHOST:8001",
        "http://[::1]:8001",
    ],
)
def test_loopback_backend_ignores_proxy_env(monkeypatch, client_builds, base_url):
    monkeypatch.setenv("BACKEND_URL", base_url)

    assert client.BackendClient().list_jobs() == []

    assert len(client_builds) == 1
    assert client_builds[0].get("trust_env") is False


@pytest.mark.parametrize(
    "base_url",
    ["http://backend:8000", "http://localhost.example:8000", "http://127.0.0.1.example:8000"],
)
def test_non_loopback_backend_keeps_proxy_env(monkeypatch, client_builds, base_url):
    monkeypatch.setenv("BACKEND_URL", base_url)

    assert client.BackendClient().list_jobs() == []

    assert len(client_builds) == 1
    assert "trust_env" not in client_builds[0]

"""Compose the connected-agent setup values from the public backend URL."""

import base64
import json
from urllib.parse import quote, urlencode

from app.config import settings
from app.schemas.connect import ConnectClient, ConnectInfo, ConnectProfile
from mcp_server import profiles


def _profile_url(backend_url: str, profile: str) -> str:
    suffix = "" if profile == "full" else f"/{profile}"
    return f"{backend_url}/mcp{suffix}"


def build(profile: str = "full") -> ConnectInfo:
    if profile not in profiles.VALID_PROFILES:
        raise ValueError(f"Unknown MCP profile: {profile}")

    backend_url = settings.maestro_cs_public_backend_url.rstrip("/")
    mcp_url = _profile_url(backend_url, profile)
    name = "maestro-career-studio"
    cursor_config = base64.b64encode(
        json.dumps({"url": mcp_url}, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    cursor_query = urlencode({"name": name, "config": cursor_config})
    vscode_config = quote(
        json.dumps({"name": name, "type": "http", "url": mcp_url}, separators=(",", ":")),
        safe="",
    )

    return ConnectInfo(
        backend_url=backend_url,
        mcp_url=mcp_url,
        profiles=[
            ConnectProfile(name=p, url=_profile_url(backend_url, p))
            for p in ["full", *sorted(profiles.VALID_PROFILES - {"full"})]
        ],
        bundle_available=settings.mcpb_bundle_path.is_file(),
        clients=[
            ConnectClient(
                id="claude-code", label="Claude Code", kind="command",
                value=f"claude mcp add --transport http {name} {mcp_url}",
            ),
            ConnectClient(
                id="codex", label="Codex CLI", kind="command",
                value=f"codex mcp add {name} --url {mcp_url}",
            ),
            ConnectClient(id="codex-app", label="Codex app", kind="steps", value=mcp_url),
            ConnectClient(
                id="cursor", label="Cursor", kind="deeplink",
                value=f"cursor://anysphere.cursor-deeplink/mcp/install?{cursor_query}",
            ),
            ConnectClient(
                id="vscode", label="VS Code", kind="deeplink",
                value=f"vscode:mcp/install?{vscode_config}",
            ),
            ConnectClient(
                id="claude-desktop", label="Claude Desktop", kind="download",
                value="/api/setup/connect/bundle",
            ),
            ConnectClient(id="other", label="Other MCP client", kind="url", value=mcp_url),
        ],
    )

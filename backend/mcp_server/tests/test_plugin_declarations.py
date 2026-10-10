"""Plugin approval names must resolve to tools in the shared registry."""

import json
from pathlib import Path

from mcp_server.server import list_registered_tool_names


def test_codex_plugin_approval_tools_are_registered_in_full_profile():
    repo = Path(__file__).resolve().parents[3]
    config = json.loads(
        (repo / "plugins/maestro-career-studio/.codex-mcp.json").read_text()
    )
    tool_names = {
        name
        for server in config["mcpServers"].values()
        for name in server.get("tools", {})
    }
    unknown = tool_names - set(list_registered_tool_names("full"))
    assert not unknown, f"Codex plugin references unregistered tools: {sorted(unknown)}"

from typing import Literal

from pydantic import BaseModel


class ConnectClient(BaseModel):
    id: Literal[
        "claude-code", "codex", "codex-app", "cursor", "vscode", "claude-desktop", "other",
    ]
    label: str
    kind: Literal["command", "deeplink", "url", "download", "steps"]
    value: str


class ConnectProfile(BaseModel):
    name: str
    url: str


class ConnectInfo(BaseModel):
    backend_url: str
    mcp_url: str
    profiles: list[ConnectProfile]
    bundle_available: bool
    clients: list[ConnectClient]

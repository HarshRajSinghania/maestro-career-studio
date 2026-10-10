# Desktop step 3, branch A (MCP over HTTP) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (or
> superpowers:executing-plans in a separate session) to implement this plan task-by-task.

**Goal:** Any assistant connects to a running Maestro by URL (`/mcp`, `/mcp/<profile>`), with no
Docker, Python or config editing on the assistant's side, while the security boundary stays
exactly as it is; Claude Desktop keeps a tiny stdio bundle that only forwards to that URL.

**Architecture:** One FastMCP instance keeps the 88 tool registrations. A new
`backend/mcp_server/http.py` builds one low-level MCP `Server` per profile over it, runs every
sync tool on a worker thread, and exposes an ASGI endpoint the FastAPI app routes at `/mcp` and
`/mcp/{profile}` inside the existing SyncPublicHost → Host → Origin → CORS stack. Stateful
sessions, JSON responses, SDK DNS-rebinding protection explicitly OFF (the app's stack is the one
definition). The `.mcpb` becomes a zero-dependency Node stdio↔HTTP bridge.

**Tech Stack:** FastAPI/Starlette, `mcp==1.29.1` (locked), anyio, httpx; Next.js 16 frontend;
Node ≥ 18 for the bridge; MV3 extension.

**Design:** `docs/plans/2026-10-09-desktop-step3-design.md`, section "Branch A" (§A1–A7). Every
SDK claim below was proven on the locked versions by a spike on 2026-10-10 (mcp 1.29.1, fastapi
0.141.1, starlette 1.3.1, anyio 4.14.2): one shared FastMCP + per-profile low-level servers
(29 KiB for six), off-loop wrapping with no deadlock on a single-worker uvicorn (the same call
without it hangs), no 307 on `Route`, fresh session managers per lifespan, clientInfo reaching
`ctx`, a ContextVar set in the profile handler visible in the worker thread, stale session → 404.

---

## Goal Card

**Goal:** After this branch, connecting an AI assistant to Maestro is "paste a URL or click a
button" for every client that speaks HTTP MCP, and "double-click a bundle" for Claude Desktop —
with nothing running Docker on the assistant's side — so the later `uv` install (B′) and desktop
app (C) need no transport work.

**Principles:**
- One trust boundary. `/mcp` lives inside the FastAPI app behind the same Host → Origin → CORS
  middleware as REST; never a second port, never a second security definition, never under
  `/api/sync`.
- One tool registry. The 88 tool functions and their tests stay as they are (sync, directly
  callable); HTTP serving wraps registered tools, it does not rewrite them.
- Old routes keep working for one release (stdio via `setup-mcp.sh` and `maestro-career-studio-mcp`),
  with a §13 row naming when they go.
- Pinned behaviour never moves silently: profiles list exactly their allowlists; hidden tools are
  refused with today's error text; `clientInfo` keeps labelling KB timeline rows.

**Non-goals:** remote or authenticated MCP; claude.ai/chatgpt.com web connectors; the `uv`
install and `maestro` command (B′); upgrading `mcp` to 2.x; the Chrome Web Store submission
itself (owner task); changing any tool's behaviour or docstring beyond transport wording.

**Autonomy:** peer. Adapt *how* when the repo disagrees with this plan; log every deviation in the
Deviation log with a one-line reason; flag anything that contradicts the Goal Card. Never expand
scope. ADD tests beside pinned tests, never replace them.

## Ground rules for every task

- Work in `<worktree>` (a git worktree of this repo); never the main checkout.
- Read `SYSTEM.md` first. Orientation tier is at 995/1000 lines (`scripts/check_system_md.py`):
  every added line is paid for by tightening the section you touch, or by moving reference detail
  into `docs/entities/others.md` (Task 12 does the big move).
- Python: the backend's interpreter, run from `backend/` (cwd wins on `sys.path`).
- Full suite (always xdist): `cd backend && MAESTRO_SKIP_SLOW=1 python -m pytest tests/ mcp_server/tests/ -q -n auto --dist loadfile -p no:cacheprovider`.
  Baseline at the start of this branch: record it in Task 0.
- CI installs `pip install -e ".[dev,mcp]"` (not the lock). Any task that changes dependencies
  runs the suite in a throwaway venv built exactly like CI (`uv venv -p 3.12 $SP/ci-venv &&
  VIRTUAL_ENV=$SP/ci-venv uv pip install -e "backend[dev,mcp]"`), `$SP` = a scratch dir outside
  the repo.
- `backend/requirements.lock` is regenerated only inside `python:3.12-slim` with the recipe in
  `backend/Dockerfile` (`--extra mcp --extra tracing`).
- Lint: `cd backend && python -m ruff check .`; frontend: `npx tsc --noEmit && npm run lint && npm run build`;
  docs: `python3 scripts/check_system_md.py`; bundle: `python3 scripts/check_mcpb_bundle.py`;
  bridge: `node --test mcpb/tests/*.test.js`; slop ratchet per surface touched (maintainer's
  `slop_scan.py check <surface>`; re-baseline only count-only movement, with a reason).
- Never verify against the running compose stack (old images). Fresh uvicorn on a free port with
  every `*_DIR` under `$SP`, sync and AI-key env vars blanked.
- Commit after every task; end messages with the executing model's `Co-Authored-By` line.

---

### Task 0: Baseline, and the Codex plugin question

**Files:** none modified (unless the answer changes Task 9 — log it).

**Step 1:** `git status` clean; full suite; record passed/skipped/wall in the Gate table; run the
three slop checks and `check_system_md.py`.

**Step 2: Codex, before and after `/mcp` exists.** Facts already established on this machine
(2026-10-10, codex-cli 0.159.3, read-only):
- Codex connects to plain `http://localhost` MCP servers with no auth: `codex mcp list` shows a
  URL server `http://localhost:<port>/mcp/codex`, bearer `-`, enabled.
- Codex reads our plugin's current `.mcp.json` (top-level server map, no `mcpServers` wrapper);
  its own bundled plugins use `{"mcpServers": {…}}`. Both forms load.
- Codex plugin `.mcp.json` entries accept Codex-only keys: `default_tools_approval_mode`
  (`"approve"`), per-tool `"tools": {"<name>": {"approval_mode": "prompt"}}`, `enabled_tools`,
  `startup_timeout_sec` (seen in the bundled `codex-app-tools` plugin under `~/.codex/plugins/cache/`).
- The Codex app's Add menu offers **Add a marketplace** (GitHub repo / Git URL / local folder, a
  **Git ref** and **Sparse paths**), **Upload plugin archive**, and **Create custom MCP server**
  (name, optional description, Connection: **Server URL** or **Tunnel**, Authentication defaulting
  to OAuth, a risk acknowledgement, "Create as a plugin").

Still to prove — AFTER Task 3 lands, against a scratch backend on `<port>` (record "pending" first):
1. **Plugin `url` entry.** Copy `plugins/maestro-career-studio/` to `$SP/codex-plugin-test/`, give
   it a `.codex-mcp.json` = `{"mcpServers": {"maestro-career-studio-test": {"url": "http://127.0.0.1:<port>/mcp"}}}`,
   point its `.codex-plugin/plugin.json` `mcpServers` at that file, add a scratch marketplace
   (shape of `.agents/plugins/marketplace.json`), install, then `codex mcp list` and a one-shot
   `codex exec -s read-only "list the maestro tools you can see"`. If `url` alone is not taken, try
   adding `"type": "http"`, then `"type": "streamable-http"`; record which form loads.
2. **Per-tool approval.** In the same test file add `"default_tools_approval_mode": "approve"` and
   `"tools": {"mark_submitted": {"approval_mode": "prompt"}}`; confirm Codex prompts before
   `mark_submitted` and not before `list_jobs` (a short interactive `codex` run is fine).
3. **Codex app "Create custom MCP server"** (the owner clicks; you record): does Authentication offer
   *None*? Does a `http://localhost:<port>/mcp` Server URL connect and list tools? What does
   **Tunnel** do — if it publishes or relays a local server to a remote party, it must never be
   used with Maestro (unauthenticated `/mcp`; SYSTEM.md §6 inv-mcp-inside-the-app), and Task 7's
   card says so.
4. **Tag-pinned marketplace.** In the Codex app, Add a marketplace with source
   `seinun-ai/maestro-career-studio`, Git ref = the current release tag, Sparse paths =
   `plugins/` — confirm it installs the plugin from that tag (owner clicks; record).
5. Record every outcome in the Deviation log; they decide Task 9's file shapes and Task 7's
   "Codex app" row wording.

No commit.

---

### Task 1: The profile becomes a request-scoped value, not an import-time deletion

Today `backend/mcp_server/server.py:2082` runs `_ACTIVE_PROFILE = apply_profile_filter(mcp)` at
import, deleting tools from the one FastMCP instance. Once the backend imports `mcp_server.server`
to serve HTTP, an env var set for the stdio process would delete tools from every HTTP profile.
Move the deletion into the stdio `main()` and let hints read a ContextVar the HTTP layer sets.

**Files:**
- Modify: `backend/mcp_server/server.py` (~:243-246 `_active_allowed_tools`; ~:2081-2098 tail)
- Test: `backend/mcp_server/tests/test_profiles.py`, `backend/mcp_server/tests/test_server.py`

**Step 1: Failing tests** (add to `test_profiles.py`):

```python
def test_importing_the_server_never_deletes_tools():
    """The backend imports mcp_server.server to serve /mcp; a profile env var
    meant for a stdio process must not shrink the shared registry. A subprocess,
    because other modules (mcp_server.http) hold the registry by reference."""
    import os, subprocess, sys
    out = subprocess.run(
        [sys.executable, "-c",
         "import mcp_server.server as s; print(len(s.mcp._tool_manager.list_tools()))"],
        env={**os.environ, "MAESTRO_CS_MCP_PROFILE": "hunt"},
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert out == "88"


def test_the_request_profile_wins_over_the_env_for_hints(monkeypatch):
    import mcp_server.server as srv
    from mcp_server.profiles import allowed_tools
    monkeypatch.setenv("MAESTRO_CS_MCP_PROFILE", "full")
    token = srv.CURRENT_PROFILE.set("hunt")
    try:
        assert srv._active_allowed_tools() == allowed_tools("hunt")
    finally:
        srv.CURRENT_PROFILE.reset(token)
    assert srv._active_allowed_tools() == allowed_tools("full")
```

(Run it from `backend/` so the subprocess imports this tree. If the tool count is no longer 88
when you run it, use `len(srv.list_registered_tool_names("full"))` from the parent as the expected value.)

**Step 2:** Run → both FAIL (`CURRENT_PROFILE` missing; the import deleted tools).

**Step 3: Implement** in `server.py`:

```python
from contextvars import ContextVar

# The profile serving the CURRENT request. The HTTP layer (mcp_server/http.py)
# sets it in each profile server's call_tool; stdio leaves it unset and the
# env profile applies. Read by workflow hints so they never name a tool the
# caller's profile cannot see.
CURRENT_PROFILE: ContextVar[str | None] = ContextVar("maestro_mcp_profile", default=None)


def _active_allowed_tools() -> frozenset[str] | None:
    return allowed_tools(CURRENT_PROFILE.get())
```

`profiles.allowed_tools(None)` already falls back to `get_profile()` (the env). Delete the
module-level `_ACTIVE_PROFILE = apply_profile_filter(mcp)` and its comment; grep for any other
reader of `_ACTIVE_PROFILE` and switch it to `CURRENT_PROFILE.get() or get_profile()`. Then:

```python
def list_registered_tool_names(profile: str | None = None) -> list[str]:
    """Tool names a client of `profile` (default: the env profile) can see."""
    allow = allowed_tools(profile)
    return [t.name for t in mcp._tool_manager.list_tools() if allow is None or t.name in allow]


def main() -> None:
    http_client.repair_proxy_env()
    apply_profile_filter(mcp)  # stdio only: this process serves one profile
    mcp.run()
```

Keep `list_registered_tool_names()`'s existing callers working (`test_server.py:22,419,430,664-679,787`,
`test_proposal_tools.py:9`, `test_client_job_site_login.py:45`) — they run with no profile env, so
`full` → all 88. `test_profiles.py:28`'s direct `apply_profile_filter` test stays as is.

**Step 4:** Run `mcp_server/tests/` and the CI cold-install loop locally
(`for p in full hunt apply explore templates career; do MAESTRO_CS_MCP_PROFILE=$p BACKEND_URL=http://localhost:8001 maestro-career-studio-mcp < /dev/null; done`
— each must start and exit cleanly). Then the full suite.

**Step 5:** Commit: `refactor(mcp): the profile is per request; only stdio main() filters the registry`.

---

### Task 2: `mcp_server/http.py` — per-profile servers, off-loop tools, one ASGI endpoint

**Files:**
- Create: `backend/mcp_server/http.py`
- Create: `backend/mcp_server/tests/test_http.py`

**Step 1: Failing tests** (`test_http.py`; drive the endpoint in-process with a tiny Starlette app
and `TestClient`, raw JSON-RPC — no backend, no network):

```python
import threading

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from mcp_server import http as mcp_http
from mcp_server import server as srv
from mcp_server.profiles import allowed_tools

HDR = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {},
    "clientInfo": {"name": "pytest-client", "version": "1"}}}


@pytest.fixture
def client():
    endpoint = mcp_http.McpEndpoint()

    async def lifespan(app):
        async with endpoint.run():
            yield

    app = Starlette(routes=[Route("/mcp", endpoint=endpoint),
                            Route("/mcp/{profile}", endpoint=endpoint)], lifespan=lifespan)
    with TestClient(app) as c:
        yield c


def _session(c, path="/mcp"):
    r = c.post(path, json=INIT, headers=HDR)
    assert r.status_code == 200, r.text
    sid = r.headers["mcp-session-id"]
    c.post(path, json={"jsonrpc": "2.0", "method": "notifications/initialized"},
           headers={**HDR, "Mcp-Session-Id": sid})
    return sid


def _rpc(c, path, sid, method, params=None, id_=2):
    body = {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}
    return c.post(path, json=body, headers={**HDR, "Mcp-Session-Id": sid})


@pytest.mark.parametrize("profile", ["full", "hunt", "apply", "explore", "templates", "career"])
def test_each_profile_lists_exactly_its_allowlist(client, profile):
    path = "/mcp" if profile == "full" else f"/mcp/{profile}"
    sid = _session(client, path)
    names = {t["name"] for t in _rpc(client, path, sid, "tools/list").json()["result"]["tools"]}
    allow = allowed_tools(profile)
    assert names == (set(srv.list_registered_tool_names("full")) if allow is None else set(allow))


def test_a_hidden_tool_is_refused_with_todays_text(client):
    sid = _session(client, "/mcp/hunt")
    hidden = next(n for n in srv.list_registered_tool_names("full") if n not in allowed_tools("hunt"))
    res = _rpc(client, "/mcp/hunt", sid, "tools/call", {"name": hidden, "arguments": {}}).json()["result"]
    assert res["isError"] is True and f"Unknown tool: {hidden}" in res["content"][0]["text"]


def test_responses_are_json_and_stateful(client):
    r = client.post("/mcp", json=INIT, headers=HDR)
    assert r.headers["content-type"].startswith("application/json")
    assert r.headers.get("mcp-session-id")


def test_an_unknown_profile_is_404(client):
    assert client.post("/mcp/nope", json=INIT, headers=HDR).status_code == 404


def test_a_stale_session_is_404_so_the_bridge_can_replay(client):
    r = _rpc(client, "/mcp", "deadbeef", "tools/list")
    assert r.status_code == 404


def test_sdk_dns_rebinding_protection_is_off():
    """The app's Host/Origin middleware is the one definition (SYSTEM.md §6)."""
    endpoint = mcp_http.McpEndpoint()
    assert endpoint.security.enable_dns_rebinding_protection is False


def test_registered_tools_run_off_the_event_loop_but_module_functions_stay_sync(client):
    seen = {}
    real = srv.list_templates

    def spy(*a, **k):
        seen["thread"] = threading.current_thread().name
        return {"items": []}

    tool = srv.mcp._tool_manager.get_tool("list_templates")
    original = tool.fn
    # the wrapper closes over the ORIGINAL function; patch through the module-level
    # name the wrapper partial holds by swapping the tool's default argument
    tool.fn = mcp_http._offloaded(spy)  # helper exported for this test
    try:
        sid = _session(client)
        _rpc(client, "/mcp", sid, "tools/call", {"name": "list_templates", "arguments": {}})
    finally:
        tool.fn = original
    assert seen["thread"] != threading.main_thread().name
    assert callable(real) and not hasattr(real, "__await__")
```

(If wiring the spy through the wrapper is awkward, assert the thread inside a tool registered on
a scratch FastMCP passed to `offload_tools` — the property under test is "a registered sync tool
runs on a worker thread", not this particular tool. Keep the module-function-stays-sync check
against the real `srv` module.)

**Step 2:** Run → FAIL (module missing).

**Step 3: Implement** `backend/mcp_server/http.py` (the spike's proven code, tidied):

```python
"""MCP over Streamable HTTP, inside the FastAPI app.

ONE FastMCP (mcp_server.server.mcp) holds the 88 tool registrations. Each profile
gets a small low-level MCP Server whose list_tools/call_tool filter that registry
through profiles.allowed_tools(profile); each server has its own session manager,
created fresh on every lifespan run (a manager's run() is once per instance).

Every registered sync tool runs on a worker thread through Maestro's own capacity
limiter: tools call the backend's REST API over loopback, and a sync call made on
the event loop would deadlock a single-worker uvicorn. The module-level functions
stay sync and directly callable (tests call them).

SDK DNS-rebinding protection is OFF on purpose: the app's Host -> Origin -> CORS
middleware is the one definition (SYSTEM.md §6 inv-mcp-inside-the-app).
"""
from __future__ import annotations

import contextlib
import functools
import logging
from collections.abc import AsyncIterator, Callable
from typing import Any

import anyio
import anyio.to_thread
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import Tool as MCPTool
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from mcp_server import server as tools_server
from mcp_server.profiles import PROFILE_ALLOWLISTS

# Tool threads, separate from FastAPI's default 40-token limiter: a tool waiting
# on a loopback REST call must never starve the endpoint it is waiting on.
TOOL_THREADS = 8
# Abandoned sessions are dropped after this long idle (the SDK default keeps them forever).
SESSION_IDLE_SECONDS = 6 * 60 * 60
# Base64 evidence images ride in tool arguments; uploads are capped at 10 MB elsewhere,
# which base64 inflates by 4/3.
MAX_REQUEST_BYTES = 16 * 1024 * 1024

logging.getLogger("mcp.server.lowlevel.server").setLevel(logging.WARNING)  # per-request INFO noise


class _Limiter:
    """Created lazily inside the running loop; reset on every lifespan run."""

    def __init__(self, tokens: int) -> None:
        self.tokens = tokens
        self._limiter: anyio.CapacityLimiter | None = None

    def reset(self) -> None:
        self._limiter = anyio.CapacityLimiter(self.tokens)

    def get(self) -> anyio.CapacityLimiter:
        if self._limiter is None:
            self.reset()
        return self._limiter  # type: ignore[return-value]


_LIMITER = _Limiter(TOOL_THREADS)


def _offloaded(original: Callable[..., Any]) -> Callable[..., Any]:
    async def run_off_loop(**kwargs: Any) -> Any:
        return await anyio.to_thread.run_sync(
            functools.partial(original, **kwargs), limiter=_LIMITER.get()
        )

    return run_off_loop


def offload_tools(mcp: FastMCP) -> int:
    """Swap each registered sync Tool.fn for an async wrapper running it on a
    worker thread. Argument validation, schemas and Context injection are
    unchanged (they live on the Tool, not the fn). Idempotent."""
    n = 0
    for tool in mcp._tool_manager.list_tools():
        if tool.is_async:
            continue
        tool.fn, tool.is_async = _offloaded(tool.fn), True
        n += 1
    return n


def build_profile_server(mcp: FastMCP, profile: str, allowed: frozenset[str] | None) -> Server:
    server: Server = Server(mcp.name, instructions=mcp.instructions)

    @server.list_tools()
    async def _list_tools() -> list[MCPTool]:
        tools = await mcp.list_tools()  # keeps title, annotations, outputSchema
        return [t for t in tools if allowed is None or t.name in allowed]

    @server.call_tool(validate_input=False)  # FastMCP validates against the Tool's own model
    async def _call_tool(name: str, arguments: dict[str, Any]) -> Any:
        if allowed is not None and name not in allowed:
            raise ToolError(f"Unknown tool: {name}")
        token = tools_server.CURRENT_PROFILE.set(profile)
        try:
            # FastMCP.get_context() reads the SDK's module-level request ContextVar,
            # which THIS server's request handler set, so ctx.session.client_params
            # (clientInfo) is this caller's.
            return await mcp.call_tool(name, arguments)
        finally:
            tools_server.CURRENT_PROFILE.reset(token)

    return server


class McpEndpoint:
    """ASGI endpoint for Route('/mcp') and Route('/mcp/{profile}').

    Starlette treats a non-function endpoint as an ASGI app, so an instance (not a
    bound method) is what gets routed — no 307, and the true path is visible."""

    def __init__(self, mcp: FastMCP | None = None) -> None:
        mcp = mcp or tools_server.mcp
        offload_tools(mcp)
        self.servers = {p: build_profile_server(mcp, p, allow)
                        for p, allow in PROFILE_ALLOWLISTS.items()}
        self.security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
        self.managers: dict[str, StreamableHTTPSessionManager] = {}

    @contextlib.asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        _LIMITER.reset()
        managers = {
            p: StreamableHTTPSessionManager(
                app=srv, json_response=True, stateless=False,
                security_settings=self.security,
                session_idle_timeout=SESSION_IDLE_SECONDS,
                max_request_body_size=MAX_REQUEST_BYTES,
            )
            for p, srv in self.servers.items()
        }
        async with contextlib.AsyncExitStack() as stack:
            for m in managers.values():
                await stack.enter_async_context(m.run())
            self.managers = managers
            try:
                yield
            finally:
                self.managers = {}

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        profile = scope.get("path_params", {}).get("profile", "full")
        manager = self.managers.get(profile)
        if manager is None:
            await JSONResponse({"detail": "Not Found"}, status_code=404)(scope, receive, send)
            return
        await manager.handle_request(scope, receive, send)
```

Notes for the implementer:
- `PROFILE_ALLOWLISTS` maps `"full"` → `None` (all) — confirm in `profiles.py:149`.
- Tools on a worker thread must not `await` Context methods (`ctx.log`, `report_progress`); today
  they only read `ctx.session.client_params`. Add that sentence to the module docstring if any
  tool is found doing otherwise (none today — grep `ctx\.` in server.py).
- Errors keep today's text ("Error executing tool X: …", `tools/base.py:117`).

**Step 4:** Run `mcp_server/tests/test_http.py`, then all `mcp_server/tests/`, then the full suite
(offloading mutates the shared registry for any process that imports `http.py`; the stdio and
direct-call tests must still pass).

**Step 5:** Commit: `feat(mcp): per-profile MCP servers over HTTP, tools off the event loop`.

---

### Task 3: Serve `/mcp` from the backend, inside the existing security stack

**Files:**
- Modify: `backend/app/main.py` (~:122-132 lifespan; routes after the `include_router` block ~:424)
- Modify: `backend/pyproject.toml` (`mcp` moves into `dependencies`; `[mcp]` stays as an alias)
- Modify: `backend/requirements.lock` (regenerate in `python:3.12-slim`; the pinned set should not change — `mcp` was already in via `--extra mcp`; expect a header-only or comment-only diff)
- Test: `backend/tests/test_mcp_http_boundary.py` (new)

**Step 1: Failing tests** (`test_mcp_http_boundary.py`, using the real app; `TestClient` as a
context manager so the lifespan runs):

```python
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

HDR = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}}


def test_mcp_initializes_inside_the_app():
    with TestClient(app) as c:
        r = c.post("/mcp", json=INIT, headers=HDR)
    assert r.status_code == 200 and r.json()["result"]["serverInfo"]["name"] == "maestro-career-studio"


def test_a_profile_path_initializes_and_a_bare_post_is_not_redirected():
    with TestClient(app) as c:
        assert c.post("/mcp/hunt", json=INIT, headers=HDR, follow_redirects=False).status_code == 200
        assert c.post("/mcp", json=INIT, headers=HDR, follow_redirects=False).status_code == 200


def test_a_forged_host_is_refused_on_mcp():
    with TestClient(app) as c:
        r = c.post("/mcp", json=INIT, headers={**HDR, "Host": "evil.example"})
    assert r.status_code == 400


def test_a_foreign_origin_is_refused_on_mcp():
    with TestClient(app) as c:
        r = c.post("/mcp", json=INIT, headers={**HDR, "Origin": "https://evil.example"})
    assert r.status_code == 403


def test_the_public_sync_host_cannot_reach_mcp(monkeypatch):
    """With Funnel publishing /api/sync, the public name must never reach /mcp."""
    # follow the pattern of tests/sync/test_public_host.py for setting SYNC_PUBLIC_HOST
    # on a fresh app instance; assert the bare 404 / refusal that file documents.
    ...


def test_mcp_is_never_under_the_sync_tree():
    paths = {getattr(r, "path", "") for r in app.router.routes}
    assert "/mcp" in paths and not any(p.startswith("/api/sync") and "mcp" in p for p in paths)


def test_two_lifespans_in_one_process_both_serve_mcp():
    for _ in range(2):
        with TestClient(app) as c:
            assert c.post("/mcp", json=INIT, headers=HDR).status_code == 200
```

`conftest` adds `testserver` to `allowed_hosts`; confirm the forged-Host case uses a name it does
not. Fill `test_the_public_sync_host_cannot_reach_mcp` by copying the setup `tests/sync/test_public_host.py`
uses (it is a pinned file — read it, do not edit it).

**Step 2:** Run → FAIL (no `/mcp` route).

**Step 3: Implement.** In `main.py`:

```python
from starlette.routing import Route

from mcp_server.http import McpEndpoint

mcp_endpoint = McpEndpoint()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.services.sync import status
    http_client.repair_proxy_env()  # before the first LLM call or model download
    seeding.run_startup()
    status.ensure_machine_id()
    _log_llm_config()
    automation_prompts.load_cards()  # a malformed skill file fails startup, not a page
    async with mcp_endpoint.run():  # fresh session managers on every lifespan
        yield
    tracing.shutdown()
```

and after the `include_router` block:

```python
# MCP over Streamable HTTP (mcp_server/http.py). Plain Routes, not a Mount: a
# Mount 307-redirects a bare POST /mcp and keeps the full path. Inside the same
# middleware stack as REST — SYSTEM.md §6 inv-mcp-inside-the-app.
app.router.routes.append(Route("/mcp", endpoint=mcp_endpoint))
app.router.routes.append(Route("/mcp/{profile}", endpoint=mcp_endpoint))
```

`pyproject.toml`: move `"mcp>=1.2,<2"` (with its load-bearing comment) into `dependencies`;
leave `mcp = []` with a one-line comment ("kept so `pip install -e '.[mcp]'` and
`native/setup.sh` keep working; the backend imports mcp_server at startup").

**Step 4:** Run the new file, the security-boundary tests (`tests/test_security_boundaries.py`,
`tests/sync/test_public_host.py`), then the full suite, then the suite in a CI-shaped venv
(dependency change). Regenerate the lock in `python:3.12-slim` (Dockerfile recipe) and confirm
the pin set is unchanged (`diff` of package==version lines). Now do Task 0 Step 2 (Codex) against
a scratch backend and log the result.

**Step 5:** Commit: `feat(mcp): serve /mcp and /mcp/<profile> from the backend; mcp is a core dependency`.

---

### Task 4: The loopback test that would have caught the deadlock

A real single-worker uvicorn, a real MCP client, a tool that calls the backend over loopback.

**Files:**
- Create: `backend/tests/test_mcp_http_loopback.py`
- Modify: `backend/pyproject.toml` `[tool.pytest.ini_options] markers` — add
  `"integration: starts a real backend process; runs in CI (NOT skipped by MAESTRO_SKIP_SLOW)"`

**Step 1: Test.** Start `python -m uvicorn app.main:app --host 127.0.0.1 --port <free> --workers 1`
in a subprocess with `BACKEND_URL=http://127.0.0.1:<port>`, `DATABASE_URL` and every `*_DIR` under
`tmp_path`, sync/AI-key vars blanked (copy the env-building helper the `tests/sync/test_two_machines.py`
home subprocess uses — read, don't edit; it is pinned). Wait for `/health`. Then, with
`mcp.client.streamable_http.streamablehttp_client` + `ClientSession(..., client_info=Implementation(name="loopback-test", version="1"))`:
- `initialize`; `list_tools` → 88 names equal to `srv.list_registered_tool_names("full")`;
- `call_tool("list_jobs", {})` returns within 10 s (`anyio.fail_after(10)`) and is not an error;
- a tool that takes `ctx` and records `origin_detail` (e.g. `kb_capture` writes a timeline row
  whose detail is the client name — pick the cheapest such tool from server.py's 10 `ctx` tools;
  if every one needs an LLM, assert via `_client_label` on a scratch FastMCP instead and note it);
- `/mcp/hunt` `list_tools` equals the hunt allowlist.
Kill the subprocess in a `finally`. Mark `@pytest.mark.integration`.

**Step 2:** Prove the test guards the deadlock: temporarily make `offload_tools` a no-op, run →
`list_jobs` times out; restore (`git checkout -- backend/mcp_server/http.py`). Note it in the commit.

**Step 3:** Run it alone, then the full suite.

**Step 4:** Commit: `test(mcp): a real single-worker backend serves /mcp end to end without deadlock`.

---

### Task 5: Loopback calls ignore proxy settings; native installs point `BACKEND_URL` home

**Files:**
- Modify: `backend/mcp_server/client.py` (~:341-356, where `new_client(timeout=…)` is built)
- Modify: `backend/scripts/native/common.sh` (`native_load_env`, after `MAESTRO_PORT` is set)
- Test: `backend/mcp_server/tests/test_client_loopback.py` (new), `backend/tests/test_native_scripts.py`

**Step 1: Failing tests.**
- `client.py`: with `HTTPS_PROXY=http://proxy.invalid:9` and `BACKEND_URL=http://127.0.0.1:8001`,
  the client builds its httpx client with `trust_env=False` (monkeypatch `client.new_client` to
  capture kwargs); with a non-loopback `BACKEND_URL` (e.g. `http://backend:8000`), `trust_env`
  is not forced (compose's service name must still honour the environment).
- `test_native_scripts.py`: after sourcing `common.sh` and calling `native_load_env`,
  `BACKEND_URL == "http://127.0.0.1:$MAESTRO_PORT"` is exported (follow the file's existing
  env-capture pattern; add beside, don't replace).

**Step 2:** Run → FAIL.

**Step 3: Implement.** `client.py`: a module helper

```python
def _is_loopback(base_url: str) -> bool:
    host = (httpx.URL(base_url).host or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}
```

and pass `trust_env=False` to `new_client(...)` when `_is_loopback(self.base_url)` (a bot's
`HTTPS_PROXY` must never carry a loopback call; SYSTEM.md §6 already says a loopback
`SYNC_REMOTE_URL` ignores the proxy environment — same rule). `common.sh`: in `native_load_env`,
after `MAESTRO_PORT` is validated: `export BACKEND_URL="http://127.0.0.1:$MAESTRO_PORT"`.

**Step 4:** Run the two test files; full suite; `shellcheck --external-sources --source-path=SCRIPTDIR backend/scripts/native/*.sh` if shellcheck is installed (CI runs it).

**Step 5:** Commit: `fix(mcp): loopback backend calls ignore proxy env; native installs export BACKEND_URL`.

---

### Task 6: `GET /api/setup/connect` and the bundle download

**Files:**
- Modify: `backend/app/config.py` — two settings beside `sync_remote_url` (~:69):
  `maestro_cs_public_backend_url: str = "http://localhost:8000"` (env `MAESTRO_CS_PUBLIC_BACKEND_URL`)
  and `mcpb_bundle_path: Path = Path("/app/mcpb/maestro-career-studio.mcpb")`
- Create: `backend/app/schemas/connect.py`, `backend/app/services/connect.py`
- Modify: `backend/app/routers/setup.py`
- Modify: `docker-compose.yml` (backend `environment`: `MAESTRO_CS_PUBLIC_BACKEND_URL: http://localhost:${BACKEND_HOST_PORT:-8001}`; `volumes`: `- ./mcpb:/app/mcpb:ro`)
- Test: `backend/tests/test_setup_connect.py` (new)

**Step 1: Failing tests** — pin the exact strings (the point of composing server-side):

```python
import base64, json, urllib.parse

def test_connect_composes_every_client_for_the_full_profile(client, monkeypatch):
    monkeypatch.setattr(settings, "maestro_cs_public_backend_url", "http://localhost:8001")
    body = client.get("/api/setup/connect").json()
    url = "http://localhost:8001/mcp"
    assert body["mcp_url"] == url
    by_id = {c["id"]: c for c in body["clients"]}
    assert by_id["claude-code"]["value"] == f"claude mcp add --transport http maestro-career-studio {url}"
    assert by_id["codex"]["value"] == f"codex mcp add maestro-career-studio --url {url}"
    cursor = urllib.parse.urlparse(by_id["cursor"]["value"])
    cfg = json.loads(base64.b64decode(urllib.parse.parse_qs(cursor.query)["config"][0]))
    assert cursor.scheme == "cursor" and cfg == {"url": url}
    vs = by_id["vscode"]["value"]
    assert vs.startswith("vscode:mcp/install?")
    assert json.loads(urllib.parse.unquote(vs.split("?", 1)[1])) == {
        "name": "maestro-career-studio", "type": "http", "url": url}
    assert by_id["other"]["value"] == url
    assert {p["name"] for p in body["profiles"]} == {"full", "hunt", "apply", "explore", "templates", "career"}


def test_a_profile_rewrites_every_value(client):
    body = client.get("/api/setup/connect", params={"profile": "hunt"}).json()
    assert body["mcp_url"].endswith("/mcp/hunt")
    assert all("/mcp/hunt" in c["value"] or c["kind"] == "download" for c in body["clients"])


def test_an_unknown_profile_is_a_422_or_400(client):
    assert client.get("/api/setup/connect", params={"profile": "nope"}).status_code in (400, 422)


def test_the_bundle_downloads_when_present_and_404s_with_a_hint_when_not(client, tmp_path, monkeypatch):
    bundle = tmp_path / "b.mcpb"
    monkeypatch.setattr(settings, "mcpb_bundle_path", bundle)
    r = client.get("/api/setup/connect/bundle")
    assert r.status_code == 404 and "mcpb" in r.json()["detail"]
    assert client.get("/api/setup/connect").json()["bundle_available"] is False
    bundle.write_bytes(b"PK\x03\x04fake")
    r = client.get("/api/setup/connect/bundle")
    assert r.status_code == 200 and r.content.startswith(b"PK")
    assert "attachment" in r.headers["content-disposition"]
```

(Uses the shared `client` fixture from `tests/conftest.py`.) The Cursor link format is
`cursor://anysphere.cursor-deeplink/mcp/install?name=maestro-career-studio&config=<base64 json>`;
VS Code is `vscode:mcp/install?<url-encoded json>`. Re-check both formats against current Cursor
and VS Code docs before pinning; log any change.

**Step 2:** Run → FAIL.

**Step 3: Implement.** `schemas/connect.py`:

```python
from typing import Literal
from pydantic import BaseModel

class ConnectClient(BaseModel):
    id: Literal["claude-code", "codex", "codex-app", "cursor", "vscode", "claude-desktop", "other"]
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
```

`services/connect.py`: `build(profile: str = "full") -> ConnectInfo` composes from
`settings.maestro_cs_public_backend_url` (strip trailing `/`), `profiles.VALID_PROFILES`, and
`settings.mcpb_bundle_path.is_file()`; the claude-desktop row is `kind="download"`, `value="/api/setup/connect/bundle"`.
The `codex-app` row is `kind="steps"`, `value` = the MCP URL, `label` = "Codex app"; the card renders
its steps (Task 7). Add a test asserting the codex-app row carries the profile's URL.
Raise `ValueError` for an unknown profile; the router maps it to 422 like the app's other enum
errors. `routers/setup.py`: `GET /connect` (`response_model=ConnectInfo`, `profile: str = "full"`)
and `GET /connect/bundle` → `FileResponse(path, media_type="application/zip", filename="maestro-career-studio.mcpb")`,
404 with detail `"The Claude Desktop bundle (mcpb/maestro-career-studio.mcpb) is not available on this install."`
The path is configuration, never request input.

**Step 4:** Run the file; `docker compose config -q` (syntax only — never up/down the live stack);
full suite.

**Step 5:** Commit: `feat(setup): GET /api/setup/connect composes every client's connect string; bundle download`.

---

### Task 7: "Connect your assistant" in Settings › Connected agents

**Files:**
- Create: `frontend/components/settings/connect-assistant-card.tsx`
- Modify: `frontend/app/settings/page.tsx` (agents panel ~:83-91: `<ConnectAssistantCard />` right after `<ConnectedAgentsCard />`)
- Modify: `frontend/lib/settings-tabs.ts` (~:27-31: add anchor `"connect-assistant"` after `"connected-agents"`), `frontend/lib/settings-tabs.test.ts`
- Modify: `frontend/lib/types.ts` (`ConnectInfo`, `ConnectClient`, `ConnectProfile`), `frontend/lib/api.ts` (`getConnectInfo(profile)`)
- Modify: `frontend/components/settings/connected-agents-card.tsx` (the "Browser apps: stdio MCP" doc comment ~:54 and any "stdio" wording)
- Modify: `frontend/components/setup/setup-steps.ts` (a "Connect an assistant" row is OPTIONAL — `required: false`, `done` = never computed server-side; if the checklist has no notion of optional-and-undetectable rows, skip the row and log it)
- Test: `backend/tests/test_frontend_settings_pages.py` (add beside existing pins: the card renders inside the agents panel after `<ConnectedAgentsCard />`, its `id="connect-assistant"` matches the tab anchor)

**Behaviour (spec):**
- `useQuery(["connect", profile], () => getConnectInfo(profile))`; a profile `Select` (Full, Hunt,
  Apply, Explore, Templates, Career — words from `docs/frontend-conventions.md` glossary; default Full).
- One row per client: label, the value in a monospace field, and an action:
  `command`/`url` → `CopyButton` (`components/copy-button.tsx`, `text={value}`);
  `deeplink` → a button "Add to Cursor" / "Add to VS Code" (`<a href={value}>` styled with
  `buttonVariants` — SYSTEM.md §11 item 29: not `Button render={<a>}`);
  `download` → "Download for Claude Desktop" linking to `value` (disabled with a one-line hint
  when `bundle_available` is false: "The bundle is not on this install; see the guide.");
  `steps` (Codex app) → the URL with a `CopyButton` and three short steps: "In the Codex app, open
  Plugins › Add › Create custom MCP server." / "Choose Server URL and paste this address." / and the
  authentication step Task 0 established (e.g. "Set Authentication to None."). Then one warning line,
  always shown: "Don't use the Tunnel option. It would let someone outside this computer reach
  Maestro." — reword only if Task 0 proved Tunnel is local-only, and log it.
- Copy rules: `docs/frontend-conventions.md` (sentence length, glossary, no jargon — "assistant",
  "connected agents"); ratchets `test_frontend_vocabulary.py` and `test_frontend_sentence_length.py` must pass.
- Desktop-only layout (1024px+).

**Steps:** write the backend source-text pin test first (fails), build the card, run `npx tsc
--noEmit`, `npm run lint`, `npm run build`, `node --test lib/settings-tabs.test.ts` (or the repo's
runner for it), the `-k frontend` backend slice. **Browser check (controller):** fresh scratch
stack; Settings › Connected agents shows the card; profile switch rewrites every row; Copy puts
the exact string on the clipboard; the Cursor/VS Code buttons have the right `href`; bundle row
disabled without a bundle and enabled with `mcpb/` present. Commit:
`feat(settings): Connect your assistant — copy commands, deep links and the Claude Desktop bundle`.

---

### Task 8: The `.mcpb` becomes a zero-dependency stdio↔HTTP bridge

**Files:**
- Rewrite: `mcpb/server/index.js`
- Rewrite: `mcpb/tests/shim.test.js` (rename to `bridge.test.js`; update `.mcpbignore` if it names files)
- Modify: `mcpb/manifest.json`
- Modify: `mcpb/maestro-career-studio.mcpb` (re-pack), `server.json` (`fileSha256`)
- Modify: `docs/RELEASING.md` (§2 row for mcpb: the pack command; §6b unchanged in shape)

**Behaviour (spec — the tests pin it):**
- Env: `MAESTRO_CS_BACKEND_URL` (default `http://localhost:8001`), `MAESTRO_CS_MCP_PROFILE`
  (default `full`; `full` → `/mcp`, else `/mcp/<profile>`). Keep the existing `setting()` helper
  that ignores unexpanded `${user_config.*}` placeholders.
- Read stdin as newline-delimited JSON-RPC. For each line: POST it to the MCP URL with headers
  `Accept: application/json, text/event-stream`, `Content-Type: application/json`, and
  `Mcp-Session-Id` once known. Process lines strictly in order (await each response before the
  next request) — simple and correct for one client.
- On an `initialize` request: remember the request line; on its response take `mcp-session-id`.
  Also remember the client's `notifications/initialized` line.
- Response handling: `200` + JSON body → write `JSON.stringify(body)` + `\n` to stdout (a JSON array
  for batches passes through as-is); `202` → write nothing; `404` while a session id is set →
  replay the remembered `initialize` (discard its response but adopt the new session id) and
  `notifications/initialized`, then retry the original line ONCE; any other status, or a 404 on
  retry → a JSON-RPC error `{"jsonrpc":"2.0","id":<request id>,"error":{"code":-32000,"message":"<one sentence>"}}`
  for requests (notifications get nothing).
- Connection refused / fetch failure → stderr one sentence:
  `Maestro Career Studio is not running at <url>. Start it (docker compose up -d, or open the app) and try again.`
  plus the JSON-RPC error for the request. **stdout carries only JSON-RPC lines, ever.**
- stdin end → exit 0; SIGINT/SIGTERM → exit 0.
- Node ≥ 18 built-in `fetch` only; no dependencies. `module.exports = { mcpUrl, ... }` for tests;
  `if (require.main === module) main();`.

**Tests** (`node:test` + an in-test `node:http` fake server; no Docker, runs everywhere):
1. initialize → session header adopted; next request carries `Mcp-Session-Id`.
2. a notification gets 202 → nothing on stdout.
3. after the fake "restarts" (forgets sessions, returns 404 for the old id), the next request
   triggers initialize + initialized replay and succeeds; stdout shows only the original
   request's response.
4. connection refused → the stderr sentence, and a JSON-RPC error with the request's id on stdout.
5. profile `hunt` → requests go to `/mcp/hunt`; `${user_config.profile}` literal → `full`.
6. every stdout line parses as JSON (fuzz the fake with a 500 and a non-JSON body → JSON-RPC error).

**manifest.json:** `version` unchanged (release bump is separate); `long_description` drops Docker
("Requires Maestro Career Studio running on this computer"); `server.mcp_config.env` →
`{"MAESTRO_CS_BACKEND_URL": "${user_config.backend_url}", "MAESTRO_CS_MCP_PROFILE": "${user_config.profile}"}`;
`user_config`: remove `container_name` and `docker_path`, add `backend_url` (string, default
`http://localhost:8001`, title "Maestro address", description "Where Maestro Career Studio is
running. Change it only if you moved the backend port."), keep `profile` (fix its "88 tools" text
if the count changed); `compatibility.platforms` → `["darwin", "win32", "linux"]`.

**Steps:** tests first (fail against the old shim), implement, `node --test mcpb/tests/*.test.js`.
Re-pack: `cd mcpb && npx @anthropic-ai/mcpb pack . maestro-career-studio.mcpb` (needs network;
controller runs it if the implementer cannot). Update `server.json` `packages[0].fileSha256` to
`shasum -a 256 mcpb/maestro-career-studio.mcpb`; `python3 scripts/check_mcpb_bundle.py` passes
(three tracked files: manifest.json, icon.png, server/index.js — unchanged set). RELEASING §2's
mcpb row gains the exact pack command and "then update server.json fileSha256". Commit:
`feat(mcpb): the Claude Desktop bundle is a zero-dependency bridge to /mcp — no Docker`.

---

### Task 9: Declarations, examples, and the stdio deprecation

**Files:**
- Modify: `plugins/maestro-career-studio/.mcp.json`, its `README.md`, `.claude-plugin/plugin.json`
  and `.codex-plugin/plugin.json` descriptions, root `.claude-plugin/marketplace.json` description
- Modify: `backend/mcp_server/claude_desktop_config.example.json`, `backend/mcp_server/codex_config.example.toml`
- Modify: `scripts/setup-mcp.sh` (banner after flag parsing, before "1. Backend URL" ~:62; closing text ~:288-291)
- Modify: `SYSTEM.md` §13 (new row `mcp-stdio-venv`), `.slopledger.json` (its trigger)

**Step 1 — two MCP files, one per client family** (decided 2026-10-10; Task 0 fixes the exact keys):
- `plugins/maestro-career-studio/.mcp.json` (Claude Code reads it): the HTTP entry in the form
  Claude Code documents for plugins (`{"maestro-career-studio": {"type": "http", "url": "http://localhost:8001/mcp"}}`;
  check current Claude Code plugin docs for whether a `mcpServers` wrapper is expected, and keep
  Codex-only keys OUT of this file).
- `plugins/maestro-career-studio/.codex-mcp.json` (new; `.codex-plugin/plugin.json` `mcpServers`
  points at it): `{"mcpServers": {"maestro-career-studio": {"url": "http://localhost:8001/mcp",
  "default_tools_approval_mode": "approve", "tools": {…}}}}` in the form Task 0 proved, with
  `"approval_mode": "prompt"` on the tools whose effect is outward or hard to undo:
  `mark_submitted`, `record_consent`, `record_decision`, `get_job_site_login`,
  `prepare_application_pdf_upload`, `report_failure`, `archive_base_resume`,
  `restore_resume_version` (re-check names against `list_registered_tool_names()`; add any newer
  tool of the same kind, and list the final set in the plugin README). This is a client-side
  prompt IN ADDITION to Maestro's own consent gates, never a replacement.
- If Task 0 shows Codex does not take a plugin `url` entry: `.codex-mcp.json` keeps today's
  `docker exec` entry (plus the approval keys) for one release, logged; Codex users are pointed
  at `codex mcp add … --url` or the Codex app row of the connect card.
- Plugin README: install via **Add a marketplace** with source `seinun-ai/maestro-career-studio`,
  **Git ref** = the release tag (e.g. `v0.9.0`), **Sparse paths** = `plugins/` — pinned to a
  release instead of `main` (plus the CLI equivalent if Task 0 found one); "What it runs and what
  it sends" describes the URL connection and the approval prompts.
- Descriptions (`.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, root
  `.claude-plugin/marketplace.json`) stop saying "stdio" / "docker compose up -d".
- `scripts/check_mcpb_bundle.py` and the release checklist are unaffected; RELEASING §2's
  version rows for both plugin.json files stay.

**Step 2 — examples:** Claude Desktop example shows the bridge
(`"command": "node", "args": ["<repo>/mcpb/server/index.js"], "env": {"MAESTRO_CS_BACKEND_URL": "http://localhost:8001", "MAESTRO_CS_MCP_PROFILE": "full"}`)
with a comment that the `.mcpb` is the easier route; Codex example uses
`[mcp_servers.maestro-career-studio]\nurl = "http://localhost:8001/mcp"`.

**Step 3 — `setup-mcp.sh`:** print a deprecation banner (`warn`) right after flag parsing:
"setup-mcp.sh is deprecated and leaves in the next release. Connect by URL instead: open Settings
› Connected agents › Connect your assistant, or run: claude mcp add --transport http
maestro-career-studio http://localhost:8001/mcp". Replace the closing "Keep the transport STDIO"
text with the true rule: never publish port 8001 or proxy `/mcp` beyond this computer. The script
keeps working (`--print-only --skip-install` must still exit 0 — that is gate G9).

**Step 4 — §13 row** `mcp-stdio-venv`: old → new = "stdio venv (`setup-mcp.sh`, `maestro-career-studio-mcp`
in a host venv) and `docker exec` examples → URL (`/mcp`) + the `.mcpb` bridge"; status
`new-is-default`; removal trigger "the release after the one that ships /mcp (v0.10.0): delete
`scripts/setup-mcp.sh`, the venv paragraphs in README/GETTING_STARTED/mcp_server README, and every
`docker exec … mcp_server.server` example; the `maestro-career-studio-mcp` console script stays
(it is the `maestro mcp` fallback until B′)". Mirror the trigger in `.slopledger.json` (a grep
predicate: `git grep -l "docker\", \"exec\"\|docker exec -i" -- ':!CHANGELOG.md' ':!docs/plans'` returns 0).

**Steps:** run `bash -n scripts/setup-mcp.sh`, `./scripts/setup-mcp.sh --print-only --skip-install`
(exit 0, banner shown), `shellcheck scripts/*.sh` if available, `check_system_md.py`,
`ledger_check.py --strict`. Commit: `feat(mcp): plugins and examples connect by URL; setup-mcp.sh deprecated`.

---

### Task 10: Chrome Web Store readiness (code only; the listing is an owner task)

**Files:**
- Create: `extension/options.html`, `extension/options.js`
- Modify: `extension/manifest.json` (`"options_ui": {"page": "options.html", "open_in_tab": false}`)
- Create: `scripts/pack_extension.py`, `extension/STORE_LISTING.md`
- Modify: `PRIVACY.md` (§ "Connecting an AI assistant (MCP)" ~:134-166 and the other Docker
  mentions it lists for MCP), `extension/README.md` ("Different ports" ~:101-112),
  `docs/RELEASING.md` (new section "Chrome Web Store" after §6b)
- Test: `backend/tests/test_extension_options.py` (new; node harness or source-text, the repo's pattern)

**Behaviour:**
- Options page edits `backendUrl` and `appUrl` in `chrome.storage.sync` (keys and defaults exactly
  as `sw.js` `DEFAULTS` ~:22-27 — import nothing from sw.js; read defaults through the existing
  `read_settings` message), validates `http(s)://` + host only, and has **Test connection**:
  `fetch(`${backendUrl}/health`)` → "Connected." / "Can't reach Maestro at <url>." No new
  permission. The page carries no emoji/glyphs (the panel's icon rule) and follows the panel's copy rules.
- `scripts/pack_extension.py` (stdlib only): zips `extension/` minus `README.md`, `INTERNALS.md`,
  `STORE_LISTING.md`, `.slopconfig.json`, `.slop-baseline.json`, tests and dotfiles into
  `dist/maestro-cs-companion-<version>.zip`; refuses when `extension/manifest.json` version !=
  `backend/pyproject.toml` version; `--first-upload` strips `key` from the zipped manifest only.
  `dist/` is gitignored (check; add if not).
- `STORE_LISTING.md`: the single-purpose statement; one justification per permission (`storage`,
  `tabs` — cite INTERNALS "`tabs` stays", `webNavigation`, `scripting`, `sidePanel`, and the
  all-URL host permission); "no remote code"; "no data collected by the developer"; the five
  1280×800 screenshots to take.
- PRIVACY.md: MCP no longer "runs inside your local Docker container"; it runs inside the
  backend, reached at `http://localhost:8001/mcp`; the Claude Desktop bundle is a bridge that
  connects only to that address. Keep every other statement true.
- RELEASING "Chrome Web Store": account; first upload with `--first-upload`; copy the store's public
  key into `manifest.json`; append the store id to `maestro_cs_extension_ids`' default and
  `.env.example`; unlisted first; the `extension-id-swap` §13 row is born at that moment (not now).

**Tests:** pack refuses a version mismatch (temp copies); `--first-upload` zip's manifest has no
`key` while the repo's still does; the zip excludes the dev files; options page source has no
`innerHTML`, uses the storage keys `backendUrl`/`appUrl`. Commit:
`feat(extension): options page for the backend address; pack script and store listing notes`.

---

### Task 11: CI

**Files:** `.github/workflows/ci.yml` (`mcp-cold-install` ~:88-128)

Keep the six-profile stdio loop (stdio still ships this release). Add a step: start
`uvicorn app.main:app --host 127.0.0.1 --port 8011 --workers 1` in the background with `DATABASE_URL`
and every `*_DIR` under `$RUNNER_TEMP`, wait for `/health`, then a small Python script with the
`mcp` client: initialize `/mcp` → `list_tools` count == `len(list_registered_tool_names())`;
initialize `/mcp/hunt` → names == the hunt allowlist; kill uvicorn. The integration marker test
(Task 4) already runs in the main backend job (`MAESTRO_SKIP_SLOW` does not skip `integration`) —
confirm by reading the job's pytest line. Verify the workflow YAML parses
(`python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))"`). Commit:
`ci: smoke /mcp and /mcp/hunt over HTTP on a cold install`.

---

### Task 12: Docs, SYSTEM.md, memory budget

**Files:** `docs/GETTING_STARTED.md` §5 (~:219-334), `README.md` (MCP section; the STDIO rule ~:470),
`SECURITY.md` (~:42 "Keep MCP on the STDIO transport"), `backend/mcp_server/README.md` (install
routes ~:21-128; "Keep the transport STDIO" ~:129-135; "How it works" diagram ~:305-312),
`docs/native-install.md` ("Connect an agent" ~:198-209 and :12, :21, :229, :278, :283, :320),
`backend/app/automations/bot-setup.md` (:13), `SYSTEM.md`, `docs/entities/others.md`,
`.system_md_enforcement.json`, `CHANGELOG.md`.

- GETTING_STARTED §5 becomes three routes: **Claude Desktop** (download the bundle from Settings
  or the release; no Docker on the Claude side), **URL clients** (Claude Code, Codex, Cursor, VS
  Code: the Connect your assistant card; the one-liners), **legacy stdio** (one release, pointer to
  setup-mcp.sh). Hand-config fallback shows the URL forms, not `docker exec`.
- native-install / bot-setup: the always-on copy's agent connects to
  `http://127.0.0.1:8001/mcp` (the bot's own backend); the "start and stop the MCP server per run"
  ritual goes; the ~76 MB stdio-process line in the memory budget goes (re-measure, below).
- SECURITY.md and READMEs: the true rule — `/mcp` has no auth by design (a local process can already
  reach REST); never publish port 8001 or proxy `/mcp` beyond this computer; it sits behind the
  same Host/Origin checks as REST.
- **Memory:** start a scratch backend, `GET /health/memory` before and after one `/mcp`
  initialize + `list_tools` + `list_jobs`; record RSS. If RSS grows past the 200 MB budget
  (`tests/test_memory_budget.py`, Linux-only), say so in the Gate table and the deviation log —
  do not raise the budget.
- **SYSTEM.md** (stay ≤ 1000; move reference detail out):
  - §2: `mcp_server/http.py` in the layout; §3 diagram: MCP over `/mcp` inside the backend.
  - §6: two new invariants with pins in `.system_md_enforcement.json`:
    `{#inv-mcp-inside-the-app}` — "`/mcp` is routed inside the FastAPI app, behind the same
    SyncPublicHost → Host → Origin → CORS stack as REST; SDK DNS-rebinding protection is OFF so that
    stack stays the one definition; never a second port, never under `/api/sync`." Pins:
    `backend/tests/test_mcp_http_boundary.py` (`def test_a_forged_host_is_refused_on_mcp`,
    `def test_mcp_is_never_under_the_sync_tree`), `backend/mcp_server/tests/test_http.py`
    (`def test_sdk_dns_rebinding_protection_is_off`).
    `{#inv-tools-off-loop}` — "every registered MCP tool runs on a worker thread through
    `mcp_server.http`'s own limiter; the module functions stay sync." Pin:
    `backend/tests/test_mcp_http_loopback.py` (its test function).
  - §7: rewrite the MCP transport sentences (currently "`mcp.run()` is stdio only", the `docker
    exec` shim, BACKEND_URL default) to the HTTP + bridge model; MOVE the long tool-coverage
    paragraph detail into `docs/entities/others.md` under a new bold bullet "**MCP tool coverage:**"
    and leave a one-line pointer, so §7 shrinks.
  - §9: `BACKEND_URL` for loopback (Docker `:8000`, native exports it), `MAESTRO_CS_PUBLIC_BACKEND_URL`.
  - §12: one dated gotcha (≤ 3 lines) — "A sync MCP tool on the event loop deadlocks a single-worker
    uvicorn when it calls the same backend (2026-10-10 spike) → `offload_tools`; pinned by the loopback test."
  - §13: the `mcp-stdio-venv` row from Task 9.
- CHANGELOG Unreleased: Added (`/mcp` + profiles, Connect your assistant, extension options page),
  Changed (the `.mcpb` is a bridge — reinstall it; plugins connect by URL), Deprecated
  (`setup-mcp.sh`, stdio venv — gone next release).

**Steps:** `check_system_md.py`, `ledger_check.py --strict`, the `-k frontend` slice (docs pinned
by source-text tests), full suite. Commit: `docs: MCP over HTTP — guides, security rule, SYSTEM.md invariants`.

---

### Task 13: Gates (controller + owner)

Run and record in the Gate table:
- **G1** suite + ruff + check_system_md + check_mcpb_bundle + `node --test mcpb/tests` + slop
  (backend, frontend, extension) + frontend tsc/lint/build — all green; suite also in a CI-shaped venv.
- **G4** single-worker uvicorn end to end: initialize → `list_tools` (88) → `list_jobs` →
  `render_pdf` → `get_rendered_pdf` within timeouts (a scratch script with the mcp client).
- **G5 (owner, scratch stack — never the live one):** install the new `.mcpb` in Claude Desktop
  against a scratch backend on 8001's stand-in (`backend_url` set in the bundle's settings), run a
  tool, restart the backend mid-chat, run another tool (the bridge replays initialize).
- **G6:** `claude mcp add --transport http …` lists 88 tools; a Cursor deep link installs and
  lists; `codex mcp add … --url` lists; the Codex plugin (tag-pinned marketplace) lists and prompts
  before `mark_submitted`; the Codex app custom-server route lists (owner); `/mcp/hunt` lists exactly
  the hunt allowlist.
- **G7:** curl: forged `Host` → 400 and foreign `Origin` → 403 on `/mcp`.
- **G8:** `pack_extension.py --first-upload` zip loads unpacked under a new id, is refused by CORS
  until the id is added to `MAESTRO_CS_EXTENSION_IDS`, then works.
- **G9:** `./scripts/setup-mcp.sh --print-only --skip-install` exits 0 and prints the banner.
- **G10:** the native always-on path reaches `/mcp` with `BACKEND_URL` exported (scratch
  `MAESTRO_HOME`, `start.sh`, then the G4 script against it).

Release (version bump to 0.9.0 in the eight places of `docs/RELEASING.md` §2, CHANGELOG cut, tag,
mcpb upload, MCP Registry republish) is a separate owner step after merge — not part of this plan.

---

## Gate table

| Gate | Baseline (Task 0) | After |
|---|---|---|
| Full suite passed / skipped / wall | | 11,110 passed / 7 skipped (+1 known flaky sync replay test, pre-existing on main); Task 6 sandbox: 10,255 passed / 860 skipped / 23 failed / 1 error in 70.82 s (same socket/ps failures as Task 5; all 11 connect cases pass) |
| Same in a CI-shaped venv | | Task 3: pending controller (online fresh install prohibited here) |
| ruff / check_system_md / check_mcpb_bundle / node --test mcpb | | Task 3: pass / pass (994/1000) / pass / 4 passed, 3 Docker-dependent skipped. Tasks 4–5: ruff and check_system_md pass (994/1000); bundle/bridge untouched. Task 5: shellcheck unavailable locally (CI/controller pending) |
| slop backend / frontend / extension | | Tasks 3–6: backend pass; frontend and extension untouched |
| frontend tsc / lint / build | | |
| G4 end to end (88 tools, list_jobs, render, get PDF) | — | |
| G5 Claude Desktop bridge + restart mid-chat (owner) | — | |
| G6 Claude Code / Cursor / Codex list 88; /mcp/hunt allowlist | — | Task 3: Codex checks pending controller/owner; scratch plugin candidates prepared |
| G7 forged Host 400 / foreign Origin 403 | — | Task 3: in-process real app pass; live curl pending (socket bind prohibited) |
| G8 first-upload zip + CORS id swap | — | |
| G9 setup-mcp.sh banner, exit 0 | — | |
| G10 native path reaches /mcp | — | |
| Backend RSS after one /mcp session (budget 200 MB) | | |
| Task 4 loopback / disabled-offload proof | — | green 1 passed; red: offload_tools disabled → TimeoutError at 10 s (controller) |
| Task 5 loopback proxy / native BACKEND_URL | — | RED: 7 failed / 3 passed (missing trust_env=False and native URL); GREEN: all 10 new regression cases pass. Complete two-file run: 105 passed / 2 skipped / 21 existing socket/ps failures |
| Task 6 setup connect / bundle | — | RED: 11 failed (missing settings/routes); GREEN: 30 passed (11 connect + 19 setup-status); ruff/docs/backend slop pass (994/1000); Compose YAML parses; Docker config and official link-doc recheck pending controller |

## Deviation log (append-only)

| Task | Planned | Found | Done instead | Goal Card line |
|---|---|---|---|---|
| (planning) | Task 0 Codex check before any code | the check needs a running `/mcp` | run it right after Task 3 | — |
| (planning) | one `.mcp.json` for Claude Code and Codex | Codex plugins take Codex-only keys (per-tool `approval_mode`) and the app adds marketplace Git ref / sparse paths and a custom-URL route | separate `.codex-mcp.json` with approval prompts; tag-pinned install; Codex-app row on the connect card; Tunnel warned against | One trust boundary |
| 1 | Bare six-profile stdio command | An installed console script starts with its bin directory on `sys.path` | Run that entry point with the backend interpreter and this worktree's `backend/` on `PYTHONPATH` | Old routes keep working for one release |
| 1 | Commit after the task | Implementer instructions forbid git writes | Leave verified changes in the worktree for the controller to commit | No Goal Card change; execution constraint |
| 1 | Green full-suite run | Sandbox blocks socket binds and `ps` (23 failures); scratch TMPDIR starts with an empty model cache (2 failures) | Copy the existing model cache into scratch; both ATS golden tests pass offline; controller reruns the full suite with process/network permissions | No Goal Card change; verification constraint |
| 2 | Validate with the backend interpreter against locked MCP 1.29.1 | `/opt/anaconda3/bin/python3` has MCP 1.26.0, without session idle timeout or body-size options | Copy cached MCP 1.29.1 into scratch and use `PYTHONPATH` for validation; keep both controls intact; controller raises the MCP floor to 1.29.1 when Task 3 moves the dependency | Pinned behaviour never moves silently |
| 2 | Spy on the shared tool and compare with the main thread | TestClient runs its event loop on another thread, so the supplied main-thread comparison can miss a regression | Use the task's permitted scratch FastMCP and compare the sync worker with an async tool's actual event-loop thread | One tool registry; sync, directly callable functions |
| 2 | Commit after the task | Implementer instructions forbid git writes | Leave changes in the worktree for the controller to commit | No Goal Card change; execution constraint |
| 2 | Green backend slop check | The new `http.py` has no production caller until Task 3, so orphan LOC rises from 0 to 123 | Keep the baseline unchanged; rerun after Task 3 wires the endpoint into FastAPI | One trust boundary; never expand scope |
| 2 | Green full-suite run | The same 23 socket/`ps` sandbox failures as Task 1 recur (10,222 passed, 860 skipped); no HTTP or MCP failure | Record every failure in scratch and have the controller rerun with process/socket permissions and MCP 1.29.1 | No Goal Card change; verification constraint |
| 3 | Move `mcp>=1.2,<2` into core dependencies | Task 2 uses session idle timeout and request body-size options absent in MCP 1.26.0; the locked SDK is 1.29.1 | Use `mcp>=1.29.1,<2`; preserve the existing 1.29.1 lock pin and empty `[mcp]` alias | Pinned behaviour never moves silently |
| 3 | Fresh app for the public sync host test | `tests/sync/test_public_host.py` changes the shared settings; the host wrapper reads `sync_public_host` per request | Copy that pinned setup with a fresh TestClient lifespan; assert the exact non-sync 400 refusal on both MCP routes | One trust boundary |
| 3 | SYSTEM.md transport invariants and stdio migration row in Tasks 12/9 | The header contract requires described endpoints and superseded paths to be updated when they land | Integrate the endpoint description, invariant pins and `both-live` migration row now; tighten SYSTEM.md from 995 to 994 lines | One trust boundary; old routes keep working for one release |
| 3 | Regenerate the lock in `python:3.12-slim` and run the CI-shaped venv suite | Implementer instructions prohibit Docker and network access | Leave the lock untouched (all 76 package/version lines unchanged); controller runs the Dockerfile recipe and fresh CI install/test commands | Pinned behaviour never moves silently; execution constraint |
| 3 | Task 0 Step 2 Codex checks against a scratch backend | Fresh uvicorn completes startup but loopback bind is refused (`operation not permitted`); Codex exec needs network, and app checks require owner clicks | Prepare scratch URL-only plugin, approval candidate and marketplace; plugin URL loading, per-tool prompts, app authentication/Tunnel and tag-pinned install are all pending controller/owner verification | One trust boundary; verification constraint |
| 3 | Full suite with all directories in writable scratch locations | Existing tests rely on unavailable default settings mirrors/render destinations; writable shared settings caused five leaks and tailoring rendered an unexpected PDF | Keep every directory under scratch; use inaccessible scratch defaults for SETTINGS_DIR/APPLICATIONS_DIR, while tests needing writes supply their own paths; all four affected files pass (156 tests) | Never expand scope; pinned behaviour never moves silently |
| 3 | Green full-suite run | The same 23 socket/`ps` sandbox failures as Task 2 recur (10,232 passed, 860 skipped); all new boundary and MCP tests pass | Record the failure list in scratch; controller reruns with socket/process access | No Goal Card change; verification constraint |
| 3 | Commit after the task | Implementer instructions forbid git writes | Leave changes in the worktree for the controller to commit | No Goal Card change; execution constraint |
| 3 (controller) | regenerate requirements.lock in python:3.12-slim | Docker daemon not running at the time; not started (it hosts the owner's live stack) | lock left as is: `mcp` was already pinned via `--extra mcp`, so moving it to core cannot change pins; regenerate at release time (RELEASING) | Pinned behaviour never moves silently |
| 4 | Test and pytest marker only | SYSTEM.md's header requires new test conventions to be described when introduced | Tighten §9 to name `integration` and its CI execution; keep 994/1000 lines | No Goal Card change; documentation contract |
| 4 | Temporarily disable offloading and observe `list_jobs` time out | This sandbox refuses loopback binds before uvicorn or the client can run | Keep the real test active; validate its assertions separately with the real MCP client and in-process REST handlers; controller performs the actual deadlock proof | One tool registry; pinned behaviour never moves silently |
| 4 | Restore with `git checkout` and commit the task | Implementer instructions prohibit every git write | Restore `http.py` byte-for-byte in a `finally`; leave the test, docs and marker for the controller to commit | No Goal Card change; execution constraint |
| 4 | Green full suite | The 23 existing socket/ps failures recur, plus the new loopback bind error; the initial run also lacks the embedding cache | Point the locked interpreter at the existing scratch model cache offline (both ATS tests pass), rerun the suite and record 10,234 passed / 860 skipped / 23 failed / 1 error; controller reruns with socket/process access | No Goal Card change; verification constraint |
| 0 (controller, 2026-10-10) | prove a Codex plugin `url` entry + per-tool approval | scratch marketplace + plugin against a throwaway /mcp on :8793 (config backed up, then restored byte-identical) | **works**: `.codex-mcp.json` = `{"mcpServers": {"<name>": {"url": "http://…/mcp", "default_tools_approval_mode": "approve", "tools": {"mark_submitted": {"approval_mode": "prompt"}}}}}` — no `type` key needed; Codex probes OAuth discovery (404s, harmless), initializes, lists, calls `list_jobs`; `mark_submitted` is gated ("MCP tool call requires approval, but approval policy is never" in non-interactive exec). Codex defers MCP tools behind its tool search when many servers are installed. Task 9 uses exactly this shape. | One trust boundary |
| 3 (review) | — | `POST /mcp/` (trailing slash) is a 307 to `/mcp` (Starlette redirect_slashes; method and body kept) | Task 12 client docs say "use `/mcp`, no trailing slash" | — |
| 3 (review) | — | MCP `serverInfo.version` reports the SDK version (1.29.1), not Maestro's | Task 12 (or a small follow-up in Task 2's module): pass the app version to the low-level `Server(...)` | — |
| 0 (owner, 2026-10-10) | Codex app custom-server auth options | Authentication offers OAuth / **No authentication** / OAuth or no authentication; Tunnel's behaviour still unknown | Task 7's Codex-app steps say "Set Authentication to No authentication."; the Tunnel warning stays as written | One trust boundary |
| 4 (controller) | implementer proves the red | Sol's sandbox forbids sockets | controller ran it: green 1 passed in 4.8 s; with `offload_tools` returning 0 → TimeoutError at the 10 s deadline (1 failed); http.py restored | — |
| 5 | Four implementation/test files only | SYSTEM.md's header requires changed client and native environment conventions to be documented when they land | Update §7 and tighten §9 to keep 994/1000 lines; record gates here | No Goal Card change; documentation contract |
| 5 | Green two-file and full-suite runs | Sandbox still blocks socket binds and `/bin/ps`; the 23 full-suite failures and loopback setup error exactly match Task 4 | Keep the tests intact; use the existing offline model cache and isolated scratch destinations, record logs in scratch, and have the controller rerun with socket/process access | Pinned behaviour never moves silently; verification constraint |
| 5 | Commit after the task | Implementer instructions forbid git writes | Leave the verified regression fixes in the worktree for the controller to commit | No Goal Card change; execution constraint |
| 6 | Check each profiled client value for literal `/mcp/hunt` | Cursor base64 and VS Code percent encoding hide the URL in the proposed assertion | Decode both links before checking their URL; cover every scoped profile and keep the supplied full-profile assertions | Pinned behaviour never moves silently |
| 6 | Recheck Cursor and VS Code install links against current official docs before pinning | Implementer instructions prohibit network access | Implement the supplied link formats and pin decoded payloads; controller rechecks the official docs before committing | Pinned behaviour never moves silently; verification constraint |
| 6 | Change only the task's six implementation/test files | SYSTEM.md's header requires new endpoints and configuration conventions to be documented when they land | Integrate `/api/setup/connect` and bundle configuration in §7; tighten that section to keep 994/1000 lines | No Goal Card change; documentation contract |
| 6 | `docker compose config -q` and task commit | Implementer instructions prohibit Docker and all git writes | Parse YAML and check the public URL/mount offline; leave Compose interpolation validation and commit to the controller | No Goal Card change; execution constraint |
| 6 | Green full suite | The same 23 socket/ps failures and MCP loopback bind error as Task 5 recur; Chrome execution is prohibited | Keep scratch data destinations and the offline model cache, leave Chrome unavailable, record 10,255 passed / 860 skipped / 23 failed / 1 error; controller reruns with socket/process/browser access | Pinned behaviour never moves silently; verification constraint |

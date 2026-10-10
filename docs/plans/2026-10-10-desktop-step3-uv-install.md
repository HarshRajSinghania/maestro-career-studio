# Desktop step 3, branch B′ (uv install) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (or
> superpowers:executing-plans in a separate session) to implement this plan task-by-task.

**Goal:** A technical user on macOS, Linux or Windows x64 installs, runs, restarts, updates and
connects Maestro with no Docker and no system Node:
`uv tool install --python 3.12 maestro-career-studio`, then `maestro start`.

**Architecture:** The backend's `pyproject.toml` becomes the PyPI distribution
`maestro-career-studio` (pure-Python `py3-none-any` wheel). It carries `app`, `mcp_server`, the
alembic `migrations`, and a new `maestro_cs` package: the `maestro` command plus, at build time
only, the prebuilt Next standalone server (`maestro_cs/web/`, JS only) and the demo resume. Node 24
comes from the `nodejs-wheel-binaries` dependency. `maestro start` detaches `maestro run`, a
foreground supervisor that runs uvicorn on 127.0.0.1:8001 and `node server.js` on 127.0.0.1:3000
against one per-user folder (`MAESTRO_HOME`), the same folder the signed app (C) will use.

**Tech Stack:** Python 3.12, setuptools, uv (tool install, build), psutil, python-dotenv, httpx,
`nodejs-wheel-binaries` 24.x, Next.js 16.3.8 `output: "standalone"`, GitHub Actions (macOS,
Ubuntu, Windows runners), PyPI trusted publishing.

**Design:** `docs/plans/2026-10-09-desktop-step3-design.md`, section "Branch B′" (items 1–7), and
branch B §1–2, §4, §8 which the design moved here. Branch A (`/mcp`, connect card, Node bridge) is
merged on local `main` at 42839099; this plan builds on it.

---

## Grounding: what the 2026-10-10 spike proved (HEAD 42839099)

Run on this Mac in a scratch dir (nothing in the repo changed):

1. **Binary wheels exist for every required target.** `uv pip compile backend/pyproject.toml` +
   `nodejs-wheel-binaries>=24,<25` + `psutil` with `--no-build --python-version 3.12` resolves for
   `aarch64-apple-darwin`, `x86_64-apple-darwin`, `x86_64-manylinux_2_28`,
   `aarch64-manylinux_2_28` and `x86_64-pc-windows-msvc` (also on 3.13 and 3.14).
   `aarch64-pc-windows-msvc` and `x86_64-unknown-linux-musl` fail, first on **typst** (no wheel):
   both stay best-effort, as the design says.
2. **Node from a wheel.** `nodejs-wheel-binaries==24.19.0` installs Node v24.19.0 at
   `nodejs_wheel/bin/node` (POSIX) or `nodejs_wheel/node.exe` (Windows; see its
   `executable.py`: `ROOT_DIR if os.name == "nt" else ROOT_DIR/bin`). About 199 MB installed.
   Its bundled `npm` shim is broken (`Cannot find module '../lib/cli.js'`) — we only need `node`.
3. **Next standalone works on that Node.** With `output: "standalone"`, the build emits
   `.next/standalone/{server.js,node_modules,.next}` (49 MB), but that includes `sharp` and a
   platform-specific libvips (`@img/sharp-darwin-arm64`, 18 MB, `.dylib` + `.node`). Nothing in
   the app imports `next/image` (only comments mention it), so `images: { unoptimized: true }` plus
   `outputFileTracingExcludes: { "*": ["node_modules/sharp/**", "node_modules/@img/**"] }` gives
   30 MB with **zero** native files. Assembled with `.next/static` and `public/`: 40 MB.
   `node server.js` with `PORT`, `HOSTNAME=127.0.0.1`, `API_PROXY_BACKEND`: `/` → 307 to
   `/applications`, a forged Host → 400 (the `proxy.ts` guard), `/api/version` proxied to the
   backend. Node RSS ≈ 125 MB. No file is written under the package dir at runtime (no
   `.next/cache` appeared).
4. **One wheel carries it all.** Renaming the project to `maestro-career-studio`, adding
   `migrations*` and `maestro_cs*` to `packages.find`, and package-data
   `migrations = ["script.py.mako", "prompt_defaults.lock.json"]`,
   `maestro_cs = ["example.json", "web/**/*", "web/**/.*", "web/.next/**/*"]` built a
   **10.6 MB `py3-none-any`** wheel (2,124 files) holding all 1,798 web files, hidden `.next/`
   included (compared file by file).
5. **`uv tool install` of that wheel boots against an empty home.** Tool venv 522 MB (Node,
   onnxruntime, typst …); shims `maestro` and `maestro-career-studio-mcp` in the tool bin dir as
   symlinks into the venv. Uvicorn from the tool venv with every `*_DIR`, `FASTEMBED_CACHE_PATH`
   and `MAESTRO_CS_UPLOAD_DIR` under the home and cwd = home: healthy in ~10 s; migrated to head
   with **no `alembic.ini`** (`script_location = Path(seeding.__file__).parents[2]/"migrations"`,
   which is `site-packages/migrations` in a wheel and `backend/migrations` in a checkout); the
   demo resume seeded and **rendered with Typst** (`pdf_rendered_at` set); `/mcp` `initialize`
   returned `serverInfo.version` = `APP_VERSION`; backend RSS ≈ 211 MB after seeding.
6. **Windows graceful stop is possible.** uvicorn 0.54 handles `SIGBREAK`
   (`HANDLED_SIGNALS += (signal.SIGBREAK,)` on win32), so a supervisor that shares a console with
   a child started in its own process group can stop it with `CTRL_BREAK_EVENT`.
7. **POSIX-only calls in the backend:** `import resource` (`app/services/memory.py`) and
   `os.fchmod` (`app/services/sync/files.py:248`, absent on Windows before Python 3.13). Typst and
   embedding helpers already use `multiprocessing.get_context("spawn")`.

## Revisions to the design (log; the executor does not re-decide these)

| # | Design said | This plan does | Why |
|---|---|---|---|
| R1 | "The package version becomes a tenth version location" | No new location: the wheel's version IS `backend/pyproject.toml`'s, already row 1 of RELEASING §2. The release workflow fails if the tag ≠ that version. | The distribution is the backend project, renamed. |
| R2 | (silent) | The backend project is renamed `maestro-career-studio-backend` → `maestro-career-studio`; the CLI lives in `backend/maestro_cs/`. | One distribution; only `requirements.lock` comments name the old one. |
| R3 | "a pure-Python wheel … `sharp` excluded" | Plus `images: { unoptimized: true }`, and the **Docker frontend image switches to `node server.js` now** (design B §7 pulled forward). | Principle "one way to run each piece": once `output: "standalone"` is on, `next start` is the odd one out. |
| R4 | (silent) | `nodejs-wheel-binaries` is a core dependency but excluded from `requirements.lock` (`pip-compile --unsafe-package nodejs-wheel-binaries`); Docker installs `--no-deps -e .` after the lock, so images do not grow by 200 MB. | Users get Node with no extra; containers never run it. |
| R5 | "supervised background pair (pidfile …)" | `maestro run` is a foreground supervisor (used as-is by autostart units); `maestro start` detaches it. Stop = a `run/stop.request` file the supervisor polls, plus SIGTERM on POSIX; children stop with SIGTERM to their session (POSIX) or `CTRL_BREAK_EVENT` (Windows), then a tree kill after 10 s. Process identity = pid + psutil `create_time`. | One code path for all three OSes; no shutdown endpoint on a zero-auth API. |
| R6 | "`update` — … `uv tool upgrade` → restart" | On Windows the running `maestro.exe` and venv `python.exe` are locked, so `update` snapshots, stops, then hands off to a detached `cmd` that waits for this process to exit, runs uv, and starts the new version (log: `logs/update.log`). POSIX runs uv in-process, then starts the new shim. | Windows cannot replace a running executable. |
| R7 | (silent) | New core dependencies: `psutil` (process identity + tree kill on all OSes) and `python-dotenv` (already transitive via pydantic-settings; now imported directly). | Cross-platform supervisor without shelling out to `ps`/`taskkill`. |
| R8 | (silent) | The `maestro` process never imports `app.*` before it has applied the install's environment (`app.config.settings` reads env at import). One-shot verbs (`check`, `migrate`, `connect`) call `apply_env()` first, which also does `os.chdir(home)`; long-lived servers get it as child env with cwd = home. | Otherwise settings bind to `/app/...` defaults, and `Settings(env_file=".env")` (config.py:46) would read a Docker checkout's `.env` from the user's cwd. |
| R9 | "`uv tool install maestro-career-studio`" | Docs say `uv tool install --python 3.12 maestro-career-studio`. | CI and Docker run 3.12; uv would otherwise pick 3.14. uv downloads 3.12 itself. |
| R10 | "`connect claude-desktop` … `{"command": "maestro", "args": ["mcp"]}`" | Writes the ABSOLUTE shim path (design item 7 says so too); the connect card's Claude Desktop row on a uv install shows `maestro connect claude-desktop` instead of the bundle download. | GUI apps lack `~/.local/bin` on PATH; the `.mcpb` is not in the wheel. |

**Proposed, needs the owner's yes before Task 9 lands (default: yes):** add `--scope user` to the
Claude Code command, both in `maestro connect claude-code` and on the connect card. Without it,
`claude mcp add` writes a *local* (per-project) entry for whatever folder the terminal is in, so
Maestro silently disappears in other projects.

**Plan verification (Fable 5.1, 2026-10-10):** "ready after fixes". Folded in: the upgrade job's
second wheel reuses the first wheel's web bundle (`--web-from`); `maestro run` exits 0 next to a live
supervisor so autostart never loops; the bridge keeps Node's single total deadline; ports come from
the process env before `maestro.env`; the web process's secret strip is in code; spawn errors are
sentences; the migration test isolates its database; `apply_env` changes to the home folder (no cwd
`.env`); the full list of places that print `claude mcp add`; minors.

**Lessons review (Sonnet over the agent-lessons KB, 2026-10-10):** folded in Claude Desktop's
config overwrite while running (Task 9 defers the write until it quits), agent sandboxes killing
processes a tool call started (ground rules), newest-dependency drift in `uv tool install` (smoke
asserts the security boundary over HTTP), smoke/fixture cleanup, text content blocks through the
bridge, and the close-the-terminal check in G5. The KBs had nothing on uv, PyPI, Next standalone,
launchd/systemd/schtasks or Windows supervision.

**Not in B′ (stays where the design put it):** the one-time import from a Docker checkout (C);
PyInstaller, Electron, signing, a tray (B, C); the native bot scripts calling `maestro` (a later
§13 row); publishing `maestro mcp` to the MCP Registry as a PyPI package (follow-up, owner call).

---

## Goal Card

**Goal:** Until the signed app exists, a technical user gets a Docker-free Maestro in two
commands, closes and restarts it with one, updates it with one (data snapshotted first), and
connects any agent with one — on macOS, Linux and Windows x64 — and everything built here is reused
by branches B and C.

**Principles:**
- Data first. Every update is preceded by a verified SQLite snapshot; nothing here edits or moves
  an existing Docker or native install's data.
- The security boundary does not move. Both servers bind 127.0.0.1 only; `/mcp` stays behind the
  backend's Host → Origin → CORS stack; the web process never receives API keys.
- Ports 8001 and 3000 by default (Companion, Origin allowlist, sync depend on them); overrides
  exist only for tests and side-by-side stacks.
- One way to run each piece. Docker and the uv install run the same backend code and the same
  Next standalone server.
- Explain, never surprise. Every network call the `maestro` command makes (PyPI version check,
  GitHub changelog, the scoring model's first download) is named in the terminal and in
  PRIVACY.md; failures print one plain sentence and what to do next.
- Same folder as the future app: `~/Library/Application Support/Maestro Career Studio` (macOS),
  `%APPDATA%\Maestro Career Studio` (Windows), `$XDG_DATA_HOME/maestro-career-studio` (Linux).

**Non-goals:** PyInstaller/Electron/signing; a tray or GUI; Windows ARM64 and musl Linux as
required targets (best-effort, documented); Intel-specific work; importing a Docker install;
remote or authenticated MCP; changing any MCP tool; replacing the native always-on scripts.

**Autonomy:** peer. Adapt *how* when the repo disagrees with this plan; log every deviation in the
Deviation log with a one-line reason; flag anything that contradicts the Goal Card. Never expand
scope. ADD tests beside pinned tests, never replace them.

## Ground rules for every task

- Work in `<worktree>` (a git worktree of this repo); never the main checkout.
- Read `SYSTEM.md` first. Orientation tier is at 973/1000 lines (`scripts/check_system_md.py`):
  every added line is paid for by tightening the section you touch, or by moving reference detail
  into `docs/entities/others.md`.
- Python: the branch venv (mcp 1.29.1 pinned; see the controller's preamble), run from `backend/`.
  Never the shared anaconda env (mcp 1.26).
- Full suite (always xdist, both trees):
  `cd backend && MAESTRO_SKIP_SLOW=1 python -m pytest tests/ mcp_server/tests/ -q -n auto --dist loadfile -p no:cacheprovider`.
  (`pytest` alone collects only `tests/`, ~500 fewer.)
- CI installs `pip install -e ".[dev,mcp]"` (not the lock). A task that changes dependencies runs
  the suite in a throwaway venv built like CI (`uv venv -p 3.12 $SP/ci-venv &&
  VIRTUAL_ENV=$SP/ci-venv uv pip install -e "backend[dev,mcp]"`), `$SP` = a scratch dir outside the
  repo.
- `backend/requirements.lock` is regenerated only inside `python:3.12-slim` with the recipe in
  `backend/Dockerfile`. If Docker is not running, log it and leave the lock for release time.
- New tests for `maestro_cs` live in `backend/tests/maestro_cs/`, with an `__init__.py` like every
  other `tests/` subdir (`ats/`, `sync/`, `tools/`, `browser/`). The root `tests/conftest.py`
  already blanks sync/AI keys at import (a worktree nested in the main checkout would otherwise
  inherit the developer's real `.env` — agent-lessons 5e5a35db); any subprocess a test starts gets
  an explicit env built from it, never the developer's shell. Unit tests never bind 8001/3000, never touch
  the real `MAESTRO_HOME`, never call `launchctl`/`systemctl`/`schtasks`/`uv`/the network: inject
  runners and use `tmp_path`.
- Lint: `cd backend && python -m ruff check .`; frontend: `npx tsc --noEmit && npm run lint &&
  npm run build`; docs: `python3 scripts/check_system_md.py`; slop ratchet per surface touched.
- Never verify against the running compose stack. Local end-to-end checks use a scratch
  `UV_TOOL_DIR`, `UV_TOOL_BIN_DIR`, `MAESTRO_HOME`, and `MAESTRO_PORT`/`MAESTRO_WEB_PORT` on spare
  ports.
- **Agent sandboxes kill what a tool call starts.** A process started inside an executor's tool
  call can die when that call ends, even with `setsid`/`nohup` (agent-lessons a8adeb47/a3fc2647:
  graceful shutdowns, no OOM, no reboot). Local end-to-end proofs therefore run inside ONE tool
  call (start → checks → stop, as the smoke script does), or through `autostart on`/launchd; never
  "start now, check in the next call". A stack that vanished between calls is this, not a bug.
- Commit after every task; end messages with the executing model's `Co-Authored-By` line.

---

### Task 0: Baseline and re-check

**Files:** none modified.

**Step 1:** `git status` clean; full suite (both trees); record passed/skipped/wall in the Gate
table; `ruff`, `check_system_md.py`, the three slop checks.

**Step 2:** Re-check the Grounding facts that can drift: `grep -n '"next"' frontend/package.json`
(16.3.8), `pip index versions nodejs-wheel-binaries` (24.x present), `uv --version`. Record. If
Next moved a major version, stop and flag.

---

### Task 1: The distribution `maestro-career-studio` and location-free migrations

**Files:**
- Modify: `backend/pyproject.toml`
- Create: `backend/maestro_cs/__init__.py`, `backend/maestro_cs/__main__.py`,
  `backend/maestro_cs/cli.py` (stub: `main()` prints usage), `backend/PYPI.md`
- Modify: `backend/app/services/seeding.py:43-45` (`run_migrations`)
- Test: `backend/tests/maestro_cs/test_packaging.py`, `backend/tests/test_run_migrations_location.py`

**Step 1: Failing tests.**

`test_packaging.py` (static: reads `pyproject.toml` with `tomllib`):
```python
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def _project():
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_distribution_name_and_command():
    data = _project()
    assert data["project"]["name"] == "maestro-career-studio"
    assert data["project"]["scripts"]["maestro"] == "maestro_cs.cli:main"
    # The legacy stdio entry point stays for one release (SYSTEM.md §13 mcp-stdio-venv).
    assert data["project"]["scripts"]["maestro-career-studio-mcp"] == "mcp_server.server:main"


def test_runtime_dependencies_for_the_uv_install():
    deps = " ".join(_project()["project"]["dependencies"])
    for name in ("psutil", "python-dotenv", "nodejs-wheel-binaries"):
        assert name in deps


def test_wheel_ships_migrations_and_the_cli_package():
    find = _project()["tool"]["setuptools"]["packages"]["find"]
    assert {"migrations*", "maestro_cs*"} <= set(find["include"])
    assert not any(p.startswith("migrations") for p in find["exclude"])
    data = _project()["tool"]["setuptools"]["package-data"]
    assert {"script.py.mako", "prompt_defaults.lock.json"} <= set(data["migrations"])
    assert "web/**/*" in data["maestro_cs"]


def test_pypi_metadata():
    project = _project()["project"]
    assert project["readme"] == "PYPI.md"
    assert project["requires-python"] == ">=3.12"
    assert "Homepage" in project["urls"]
```

`test_run_migrations_location.py`: in a `tmp_path` cwd (`monkeypatch.chdir`), with
`monkeypatch.setenv("TEST_DATABASE_URL", f"sqlite:///{tmp_path}/t.sqlite3")` — `migrations/env.py`
prefers `TEST_DATABASE_URL`, which conftest sets session-wide, and `settings` is built at import,
so `DATA_DIR` alone would re-migrate the shared test DB — call `seeding.run_migrations()` and assert
the `alembic_version` row **in that file** equals the head from `ScriptDirectory` of
`backend/migrations`. Also assert
`run_migrations` does not read `alembic.ini` (patch `alembic.config.Config.__init__` to record the
`file_` argument and assert it is `None`). Follow how `tests/conftest.py:175` builds a migration
config, and reuse its database fixture pattern rather than inventing one.

Run: `python -m pytest tests/maestro_cs/test_packaging.py tests/test_run_migrations_location.py -q`
Expected: FAIL (name, scripts, deps; `Config` called with the ini path).

**Step 2: Implement.**

`run_migrations`:
```python
def run_migrations() -> None:
    # No alembic.ini: in a wheel this file sits in site-packages/app/services/, so parents[2] is
    # site-packages, where the `migrations` package is installed; in a checkout it is backend/.
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "migrations"))
    command.upgrade(cfg, "head")
```
(`migrations/env.py` only calls `fileConfig` when `config.config_file_name` is set, so logging is
unchanged for Docker, which still has `alembic.ini` for the `alembic` CLI.)

`pyproject.toml`:
- `name = "maestro-career-studio"`; `readme = "PYPI.md"`; `description`; `urls`
  (`Homepage`, `Source`, `Changelog`, `Issues` → the GitHub repo); classifiers for
  Python 3.12, OS Independent, Environment :: Console.
- `dependencies` add `"psutil>=7"`, `"python-dotenv>=1"`,
  `"nodejs-wheel-binaries>=24,<25"` (comment: Node for the bundled web server; excluded from
  the Docker lock, R4).
- `[project.scripts]` add `maestro = "maestro_cs.cli:main"`.
- `packages.find`: `include = ["app*", "mcp_server*", "migrations*", "maestro_cs*"]`,
  `exclude = ["tests*", "mcp_server.tests*"]`.
- `package-data`: `migrations = ["script.py.mako", "prompt_defaults.lock.json"]`,
  `maestro_cs = ["maestro.env.template", "data/*.json", "web/**/*", "web/**/.*", "web/.next/**/*"]`
  (the three web globs are the spike-proven set; Task 12's checker is the real guard).

`PYPI.md`: ≤ 40 lines — what it is, the two install commands (macOS/Linux and Windows uv
installers), `maestro start`, where data lives, link to the GitHub README and PRIVACY.md. No
competitor names (public repo rule).

`maestro_cs/__init__.py`:
```python
"""The `maestro` command: run, update and connect a uv-installed Maestro Career Studio."""
from importlib import metadata


def package_version() -> str:
    try:
        return metadata.version("maestro-career-studio")
    except metadata.PackageNotFoundError:  # a source tree that was never installed
        return "dev"
```
`maestro_cs/__main__.py`: `from maestro_cs.cli import main; raise SystemExit(main())`.

**Step 3:** Run the two test files → PASS; full suite in the CI-shaped venv (deps changed) → green.
`pip uninstall -y maestro-career-studio-backend && pip install -e backend` in the branch venv so
`maestro` resolves (two editable finders would both serve `app`).

**Step 4: Lock.** If Docker is running, regenerate `backend/requirements.lock` in
`python:3.12-slim` with the Dockerfile recipe **plus** `--unsafe-package nodejs-wheel-binaries`, and
update the recipe comment in `backend/Dockerfile`. Expect: header + `psutil` + the `# via` names
change; no other pin moves. If Docker is down: Deviation log, release-time item.

**Step 5: Commit** `build: the backend becomes the maestro-career-studio distribution; migrations ship in the wheel`.

---

### Task 2: Windows groundwork in the backend

**Files:**
- Modify: `backend/app/services/memory.py`, `backend/app/services/sync/files.py:~248`
- Test: `backend/tests/test_memory_readout.py` (or the existing memory test file — find it),
  `backend/tests/sync/test_files_windows.py`

**Step 1: Failing tests.** Simulate Windows by monkeypatching. For `memory`: reload the module with
`sys.modules["resource"] = None` (so `import resource` fails) and assert `readout()` still returns
finite positive `rss_mb` and `peak_mb` — the contract `native/common.sh`'s `valid_memory` and the
`/health/memory` route rely on (read both first). The fallback is
`psutil.Process().memory_info().rss` (psutil is core after Task 1), used for both numbers. For
`files.py`: `monkeypatch.delattr(os, "fchmod", raising=False)` and assert a sync file write still
succeeds with its content intact.

**Step 2: Implement.** `memory.py`: try `import resource` (POSIX); fall back to psutil. Keep a
module-level `resource` name (`resource = None` on failure): `tests/test_health_memory.py:29,61`
patches `memory.resource.getrusage`. The reload test restores `sys.modules` afterwards.
`files.py`: `if hasattr(os, "fchmod"): os.fchmod(...)` else `os.chmod(path, mode)` after close
(no-op-ish on Windows; protection is the per-user profile ACL, stated in Task 14's SECURITY.md).

**Step 3:** `git grep -nE "os\.(fchmod|getuid|setsid|killpg|fork)|import (fcntl|pwd|resource)|signal\.SIG(KILL|HUP)" -- backend/app backend/mcp_server ':!*/tests/*'`
and fix or justify every hit (record each in the Deviation log). Tests pass; full suite green.

**Step 4: Commit** `fix(windows): memory readout and sync file modes no longer need POSIX-only calls`.

---

### Task 3: Next standalone output, and Docker runs it too

**Files:**
- Modify: `frontend/next.config.ts`, `frontend/Dockerfile`
- Test: `backend/tests/test_frontend_standalone.py` (static checks, like
  `test_frontend_host_guard.py` pins `proxy.ts`)

**Step 1: Failing test.** Assert `next.config.ts` contains `output: "standalone"`,
`images: { unoptimized: true }`, and excludes `node_modules/sharp/**` and `node_modules/@img/**`
from output tracing; assert `frontend/Dockerfile`'s runner stage copies `.next/standalone`,
`.next/static` → `./.next/static`, `public`, and its `CMD` is `["node", "server.js"]` with
`ENV HOSTNAME=0.0.0.0` and `PORT=3000` (a container must listen on all interfaces; compose binds the
host side to loopback — confirm in `docker-compose.yml` and say so in the comment). Grep existing
tests for pins on `npm start`/`next start` first and update them deliberately (log).

**Step 2: Implement** `next.config.ts`:
```ts
const nextConfig: NextConfig = {
  // One way to run the UI: Docker and the uv install both run .next/standalone/server.js.
  output: "standalone",
  // Nothing uses next/image; without the optimizer the bundle carries no native sharp/libvips,
  // so one build runs on every OS (docs/plans/2026-10-10-desktop-step3-uv-install.md).
  images: { unoptimized: true },
  outputFileTracingExcludes: { "*": ["node_modules/sharp/**", "node_modules/@img/**"] },
  async headers() {
    return [{ source: "/:path*", headers: SECURITY_HEADERS }];
  },
};
```
`frontend/Dockerfile` runner: copy `/app/.next/standalone ./`, `/app/.next/static ./.next/static`,
`/app/public ./public`; `ENV HOSTNAME=0.0.0.0 PORT=3000`; `CMD ["node", "server.js"]`; drop the
`node_modules` and `package.json` copies.

**Step 3: Prove.** `cd frontend && npm run build`; assemble `$SP/web` (standalone + static +
public); `find $SP/web \( -name "*.node" -o -name "*.dylib" -o -name "*.so" -o -name "*.dll" \)`
is empty; run `node server.js` on a spare port against a scratch backend; `/api/version` proxied;
forged Host → 400. If Docker is running, `docker build frontend/` and run it the same way; else
Deviation log + Gate G7 pending. `npx tsc --noEmit && npm run lint`.

**Step 4: Commit** `build(frontend): standalone output without sharp; the Docker image runs node server.js`.

---

### Task 4: `maestro_cs.home` and `maestro_cs.runtime` — the folder, the env file, the child environments

**Files:**
- Create: `backend/maestro_cs/home.py`, `backend/maestro_cs/runtime.py`,
  `backend/maestro_cs/maestro.env.template`
- Test: `backend/tests/maestro_cs/test_home.py`, `backend/tests/maestro_cs/test_runtime.py`

**Step 1: Failing tests** (all with `tmp_path`, explicit `environ` dicts — never `os.environ`):
- `default_home("darwin", {}, Path("/Users/u"))` → `/Users/u/Library/Application Support/Maestro Career Studio`;
  `("win32", {"APPDATA": r"C:\Users\u\AppData\Roaming"}, …)` → `…\Maestro Career Studio`;
  `("linux", {}, Path("/home/u"))` → `/home/u/.local/share/maestro-career-studio`;
  `("linux", {"XDG_DATA_HOME": "/x"}, …)` → `/x/maestro-career-studio`.
- `resolve_home({"MAESTRO_HOME": str(tmp)})` wins; `~` expands; a relative value resolves against cwd.
- `prepare_home` creates every `LAYOUT` dir and `maestro.env` (from the template, mode 0600 on
  POSIX), keeps an existing `maestro.env` byte-for-byte, refuses a symlinked home or a symlinked
  `maestro.env` with `HomeError` and a sentence, and sets 0700 on the home (POSIX only — skip the
  mode asserts on win32).
- `read_env_file` parses `KEY=value`, `KEY='v w'`, `KEY="v"`, comments, `export KEY=v`; ignores
  blank values (an empty value means unset, SYSTEM.md's scrub rule).
- `ports_from({}, {})` → (8001, 3000); precedence **process env > maestro.env > default** for
  `MAESTRO_PORT`/`MAESTRO_WEB_PORT` (tests, CI and side-by-side stacks set them in the
  environment; on the owner's Mac 8001/3000 are the live Docker stack); `"0"`, `"70000"`, `"abc"`
  → `HomeError`; equal ports → `HomeError`.
- `backend_env`: every path var under the home; `DATABASE_URL`, `TEST_DATABASE_URL`,
  `MAESTRO_CS_UPLOAD_HOST_ROOT`, `MCPB_BUNDLE_PATH` removed even if the env file or the parent
  env sets them; file values override inherited ones; enforced values override both;
  `ALLOWED_WEB_ORIGINS` defaults to the web port's two origins unless the file sets it;
  `MAESTRO_INSTALL=uv`; `APP_VERSION` = the given version; `BACKEND_URL=http://127.0.0.1:<p>`;
  `MAESTRO_CS_PUBLIC_BACKEND_URL=http://localhost:<p>`.
- `web_env`: has `PORT`, `HOSTNAME=127.0.0.1`, `API_PROXY_BACKEND=http://127.0.0.1:<backend>`,
  `NODE_ENV=production`, `NEXT_TELEMETRY_DISABLED=1`; passes `FRONTEND_ALLOWED_HOSTS` from the env
  file when set; and contains **no** key from the env file other than that, and no
  `_WEB_STRIPPED` name inherited from the shell (assert both).
- `runtime.node_path()` honours `MAESTRO_NODE`; otherwise ends in `bin/node` (POSIX) /
  `node.exe` (win32) under `nodejs_wheel`; `runtime.web_dir()` honours `MAESTRO_WEB_DIR`, else
  `maestro_cs/web`; `web_dir_ok()` is true only when `server.js` exists.
- `runtime.shim_path()` returns an absolute path to the `maestro` launcher: `shutil.which("maestro")`
  when it resolves into this venv, else `<venv>/bin/maestro` (POSIX) / `<venv>\Scripts\maestro.exe`.

**Step 2: Implement.**

`home.py`:
```python
"""Where a uv install keeps its data, and the environment its two servers run with.

The signed desktop app (branch C) uses the same folder, so it inherits this data with no import.
"""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

APP_DIR_NAME = "Maestro Career Studio"
LAYOUT = (
    "data", "applications", "settings", "base_resumes", "kb_documents", "logs", "exports",
    "fastembed_cache", "uploads", "backups", "run",
)
DEFAULT_BACKEND_PORT = 8001
DEFAULT_WEB_PORT = 3000
TEMPLATE = Path(__file__).with_name("maestro.env.template")
POSIX = os.name == "posix"

# Never inherited by the backend: a stray value would point it at someone else's data.
_STRIPPED = (
    "DATABASE_URL", "TEST_DATABASE_URL", "MAESTRO_CS_UPLOAD_HOST_ROOT", "MCPB_BUNDLE_PATH",
    "APP_GIT_SHA",
)


class HomeError(Exception):
    """A problem with the install folder, phrased for the person at the terminal."""


@dataclass(frozen=True)
class Ports:
    backend: int = DEFAULT_BACKEND_PORT
    web: int = DEFAULT_WEB_PORT


def default_home(platform: str = sys.platform, environ: Mapping[str, str] = os.environ,
                 user_home: Path | None = None) -> Path:
    user_home = user_home or Path.home()
    if platform == "darwin":
        return user_home / "Library" / "Application Support" / APP_DIR_NAME
    if platform == "win32":
        return Path(environ.get("APPDATA") or user_home / "AppData" / "Roaming") / APP_DIR_NAME
    return Path(environ.get("XDG_DATA_HOME") or user_home / ".local" / "share") / "maestro-career-studio"


def resolve_home(environ: Mapping[str, str] = os.environ) -> Path:
    raw = environ.get("MAESTRO_HOME", "").strip()
    home = Path(raw).expanduser() if raw else default_home(environ=environ)
    return Path(os.path.abspath(home))


def prepare_home(home: Path) -> None:
    if home.is_symlink():
        raise HomeError(f"{home} is a symlink. Point MAESTRO_HOME at a real folder.")
    try:
        home.mkdir(parents=True, exist_ok=True, mode=0o700)
        if POSIX:
            home.chmod(0o700)
        for name in LAYOUT:
            (home / name).mkdir(exist_ok=True, mode=0o700)
    except OSError as exc:
        raise HomeError(f"Cannot create {home}: {exc.strerror or exc}.") from None
    env_file = home / "maestro.env"
    if env_file.is_symlink():
        raise HomeError(f"{env_file} is a symlink. Replace it with a regular file.")
    if not env_file.exists():
        fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(TEMPLATE.read_text(encoding="utf-8"))
    elif POSIX:
        env_file.chmod(0o600)


def read_env_file(path: Path) -> dict[str, str]:
    values = dotenv_values(path) if path.is_file() else {}
    return {k: v.strip() for k, v in values.items() if v is not None and v.strip()}


def _port(value: str | None, default: int, name: str) -> int:
    if value is None:
        return default
    if not value.isdigit() or not 0 < int(value) <= 65535:
        raise HomeError(f"{name} in maestro.env must be a port number from 1 to 65535.")
    return int(value)


def ports_from(settings: Mapping[str, str], environ: Mapping[str, str] = os.environ) -> Ports:
    def pick(name: str) -> str | None:
        return (environ.get(name) or "").strip() or settings.get(name)

    ports = Ports(
        _port(pick("MAESTRO_PORT"), DEFAULT_BACKEND_PORT, "MAESTRO_PORT"),
        _port(pick("MAESTRO_WEB_PORT"), DEFAULT_WEB_PORT, "MAESTRO_WEB_PORT"),
    )
    if ports.backend == ports.web:
        raise HomeError("MAESTRO_PORT and MAESTRO_WEB_PORT must differ.")
    return ports


def backend_env(home: Path, settings: Mapping[str, str], environ: Mapping[str, str],
                ports: Ports, version: str) -> dict[str, str]:
    env = {k: v for k, v in environ.items() if k not in _STRIPPED}
    env.update({k: v for k, v in settings.items() if k not in _STRIPPED})
    env.setdefault(
        "ALLOWED_WEB_ORIGINS", f"http://localhost:{ports.web},http://127.0.0.1:{ports.web}"
    )
    env.update({
        "MAESTRO_HOME": str(home),
        "DATA_DIR": str(home / "data"),
        "APPLICATIONS_DIR": str(home / "applications"),
        "SETTINGS_DIR": str(home / "settings"),
        "BASE_RESUMES_DIR": str(home / "base_resumes"),
        "KB_DOCUMENTS_DIR": str(home / "kb_documents"),
        "LOGS_DIR": str(home / "logs"),
        "EXPORTS_DIR": str(home / "exports"),
        "FASTEMBED_CACHE_PATH": str(home / "fastembed_cache"),
        "MAESTRO_CS_UPLOAD_DIR": str(home / "uploads"),
        # Sync is on only when this file exists; the path is never printed.
        "SYNC_KEY_FILE": str(home / "sync-key"),
        "ALLOWED_HOSTS": "localhost,127.0.0.1",
        "BACKEND_URL": f"http://127.0.0.1:{ports.backend}",
        "MAESTRO_CS_PUBLIC_BACKEND_URL": f"http://localhost:{ports.backend}",
        "MAESTRO_INSTALL": "uv",
        "APP_VERSION": version,
        "PYTHONUNBUFFERED": "1",
    })
    return env


# Never given to the web server, even when the user's shell exports them: it needs no secret.
_WEB_STRIPPED = _STRIPPED + (
    "OPENAI_API_KEY", "GEMINI_API_KEY", "OPENAI_BASE_URL", "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST", "SYNC_REMOTE_URL", "SYNC_KEY_FILE",
)


def web_env(settings: Mapping[str, str], environ: Mapping[str, str], ports: Ports) -> dict[str, str]:
    # Built from the parent environment (Node needs PATH, SYSTEMROOT, proxy variables), never
    # from maestro.env, and minus every secret the backend reads.
    env = {k: v for k, v in environ.items()
           if k not in _WEB_STRIPPED and k not in settings}
    if settings.get("FRONTEND_ALLOWED_HOSTS"):
        env["FRONTEND_ALLOWED_HOSTS"] = settings["FRONTEND_ALLOWED_HOSTS"]
    env.update({
        "PORT": str(ports.web),
        "HOSTNAME": "127.0.0.1",
        "API_PROXY_BACKEND": f"http://127.0.0.1:{ports.backend}",
        "NODE_ENV": "production",
        "NEXT_TELEMETRY_DISABLED": "1",
    })
    return env
```
Note for the executor: check `app.config.Settings` for any other secret field (`*_API_KEY`,
`*_SECRET_KEY`, tokens) and add it to `_WEB_STRIPPED`; a test asserts that a shell-exported
`OPENAI_API_KEY` and every key set in `maestro.env` are absent from `web_env`.

`maestro.env.template`: the native `maestro.env.example`'s settings (API keys, base URL, models,
proxy, `SYNC_REMOTE_URL`) as commented `KEY=` lines in dotenv syntax, plus `#MAESTRO_PORT=8001` and
`#MAESTRO_WEB_PORT=3000` with "leave these unless two copies run side by side; the Companion and
sync expect 8001 and 3000". First line: "Read by `maestro start`. Keep it private."

`runtime.py`: `node_path()`, `web_dir()`, `web_dir_ok()`, `demo_resume()`
(`maestro_cs/data/example.json`, else the checkout's `base_resumes/example.json` for source runs),
`shim_path()`, `venv_python(windowless=False)` (`pythonw.exe` on Windows when asked).

**Step 3:** Tests pass on this Mac; `ruff`. **Step 4: Commit** `feat(maestro): the install folder, maestro.env and the two servers' environments`.

---

### Task 5: `maestro run` — the supervisor

**Files:**
- Create: `backend/maestro_cs/procs.py`, `backend/maestro_cs/supervisor.py`
- Test: `backend/tests/maestro_cs/test_procs.py`, `backend/tests/maestro_cs/test_supervisor.py`

**Step 1: Failing tests.** Children are tiny Python HTTP servers, not uvicorn/node:
`[sys.executable, "-c", FAKE_SERVER, str(port)]` where `FAKE_SERVER` serves 200 on `/health` and
`/api/version` (and optionally exits after N seconds, or ignores SIGTERM, per test). Use free
ports from a `socket.bind(("127.0.0.1", 0))` helper.
- `procs.write_record` + `live_process`: true for our own pid; false after the pid exits; false
  when `create_time` differs (fake a record with `create_time - 100`).
- `kill_tree` kills a child that spawned a grandchild.
- Supervisor starts specs in order, writes `run/ready.json` (pids, ports, version), and
  `run/supervisor.json`, `run/<name>.json` records.
- Creating `run/stop.request` makes `run()` return 0 within 2 s, both children gone, every
  record and `ready.json` removed.
- A child that exits once is restarted (log line names it); one that exits 4 times inside the
  window makes `run()` return 1 and stop the other child.
- A child that never becomes healthy → `run()` returns 1 within `health_timeout`, log names it.
- A child that ignores SIGTERM is killed after `grace` (set `grace=1` in the test).
- Logs: `backend.log` from a previous run is kept as `backend.log.1`; an in-run restart appends
  to `backend.log` (rotation is once per `run()`).
- A missing child executable (`argv[0]` does not exist) → `run()` returns 1 with a sentence, no
  traceback.
- POSIX: SIGTERM to the supervisor (run it as a subprocess via `python -m maestro_cs.supervisor`
  test entry, or a thread-safe flag hook) stops cleanly with 0.
`signal.signal` only works on the main thread, so tests run `Supervisor.run()` on the main
thread or in a subprocess, never in a worker thread. Mark Windows-only branches with `pytest.mark.skipif(sys.platform != "win32")` tests that run in
the CI Windows job (Task 13 runs `tests/maestro_cs` there).

**Step 2: Implement.**

`procs.py`:
```python
"""Process records that survive reboots honestly: a pid alone can name someone else's process."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import psutil


def write_json(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    if os.name == "posix":
        tmp.chmod(0o600)
    os.replace(tmp, path)


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_record(path: Path, pid: int, **extra: Any) -> None:
    write_json(path, {"pid": pid, "create_time": psutil.Process(pid).create_time(), **extra})


def live_process(record: dict[str, Any] | None) -> psutil.Process | None:
    if not record:
        return None
    try:
        proc = psutil.Process(int(record["pid"]))
        same = abs(proc.create_time() - float(record["create_time"])) < 0.5
        if same and proc.status() != psutil.STATUS_ZOMBIE:
            return proc
    except (psutil.Error, KeyError, TypeError, ValueError):
        pass
    return None


def kill_tree(proc: psutil.Process, grace: float = 5.0) -> None:
    try:
        family = [proc, *proc.children(recursive=True)]
    except psutil.Error:
        return
    for member in family:
        try:
            member.terminate()
        except psutil.Error:
            pass
    _, alive = psutil.wait_procs(family, timeout=grace)
    for member in alive:
        try:
            member.kill()
        except psutil.Error:
            pass
```

`supervisor.py`:
```python
"""`maestro run`: the foreground supervisor that owns the backend and web servers.

`maestro start` detaches this; autostart units run it directly. It stops on SIGTERM/SIGINT
(SIGBREAK on Windows) or when `run/stop.request` appears, which is how `maestro stop` asks on
every OS without a shutdown endpoint on a zero-auth API.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import psutil

from maestro_cs import procs

WINDOWS = sys.platform == "win32"


@dataclass
class ChildSpec:
    name: str
    argv: list[str]
    env: dict[str, str]
    cwd: Path
    log: Path
    health_url: str
    health_timeout: float = 120.0


def http_ok(url: str, timeout: float = 2.0) -> bool:
    # Loopback must bypass inherited proxy settings (the native lesson).
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


class Supervisor:
    def __init__(self, run_dir: Path, specs: list[ChildSpec], *, ready: dict | None = None,
                 poll: float = 0.5, max_restarts: int = 3, restart_window: float = 300.0,
                 grace: float = 10.0, out: TextIO = sys.stdout) -> None:
        self.run_dir, self.specs, self.ready = run_dir, specs, ready or {}
        self.poll, self.max_restarts, self.restart_window = poll, max_restarts, restart_window
        self.grace, self.out = grace, out
        self.children: dict[str, subprocess.Popen] = {}
        self.exits: dict[str, list[float]] = {spec.name: [] for spec in specs}
        self.stopping = False

    @property
    def stop_request(self) -> Path:
        return self.run_dir / "stop.request"

    def say(self, message: str) -> None:
        print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}", file=self.out, flush=True)

    def run(self) -> int:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        for name in ("stop.request", "ready.json"):
            (self.run_dir / name).unlink(missing_ok=True)
        procs.write_record(self.run_dir / "supervisor.json", os.getpid())
        self._rotate_logs()
        self._install_signals()
        try:
            for spec in self.specs:
                if not self._start(spec):
                    return 0 if self._should_stop() else 1
            procs.write_json(self.run_dir / "ready.json", {
                **self.ready, **{spec.name: self.children[spec.name].pid for spec in self.specs},
            })
            self.say("Maestro is running.")
            return self._watch()
        finally:
            self._stop_all()
            for name in ("ready.json", "supervisor.json", "stop.request",
                         *(f"{spec.name}.json" for spec in self.specs)):
                (self.run_dir / name).unlink(missing_ok=True)

    def _should_stop(self) -> bool:
        return self.stopping or self.stop_request.exists()

    def _install_signals(self) -> None:
        def handler(signum, frame):  # noqa: ARG001
            self.stopping = True
        names = ("SIGINT", "SIGBREAK") if WINDOWS else ("SIGTERM", "SIGINT", "SIGHUP")
        for name in names:
            signal.signal(getattr(signal, name), handler)

    def _rotate_logs(self) -> None:
        # Once per run, not per spawn: an in-run restart appends, so the crash that caused it
        # stays in the same file instead of overwriting last run's backend.log.1.
        for spec in self.specs:
            if spec.log.exists():
                os.replace(spec.log, spec.log.with_name(spec.log.name + ".1"))

    def _spawn(self, spec: ChildSpec) -> subprocess.Popen | None:
        kwargs: dict = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS else {
            "start_new_session": True}
        try:
            with open(spec.log, "ab") as log:  # the child keeps its own handle
                proc = subprocess.Popen(spec.argv, cwd=spec.cwd, env=spec.env,
                                        stdin=subprocess.DEVNULL, stdout=log,
                                        stderr=subprocess.STDOUT, **kwargs)
        except OSError as exc:  # e.g. node missing or not executable
            self.say(f"Cannot start {spec.name}: {exc.strerror or exc}.")
            return None
        self.children[spec.name] = proc
        try:
            procs.write_record(self.run_dir / f"{spec.name}.json", proc.pid)
        except psutil.Error:
            pass  # it already exited; _start reports the exit code
        self.say(f"Started {spec.name} (pid {proc.pid}).")
        return proc

    def _start(self, spec: ChildSpec) -> bool:
        proc = self._spawn(spec)
        if proc is None:
            return False
        deadline = time.monotonic() + spec.health_timeout
        while time.monotonic() < deadline:
            if self._should_stop():
                return False
            if proc.poll() is not None:
                self.say(f"{spec.name} exited during start with code {proc.returncode}; see {spec.log.name}.")
                return False
            if http_ok(spec.health_url):
                return True
            time.sleep(self.poll)
        self.say(f"{spec.name} did not become healthy within {spec.health_timeout:.0f} seconds.")
        return False

    def _may_restart(self, name: str) -> bool:
        now = time.monotonic()
        recent = [t for t in self.exits[name] if now - t < self.restart_window] + [now]
        self.exits[name] = recent
        return len(recent) <= self.max_restarts

    def _watch(self) -> int:
        while not self._should_stop():
            time.sleep(self.poll)
            for spec in self.specs:
                proc = self.children[spec.name]
                if proc.poll() is None:
                    continue
                if not self._may_restart(spec.name):
                    self.say(f"{spec.name} keeps exiting (code {proc.returncode}); giving up.")
                    return 1
                self.say(f"{spec.name} exited with code {proc.returncode}; restarting.")
                if not self._start(spec):
                    return 0 if self._should_stop() else 1
        self.say("Stopping on request.")
        return 0

    def _stop_child(self, proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        try:
            if WINDOWS:
                proc.send_signal(signal.CTRL_BREAK_EVENT)  # uvicorn and node exit cleanly on it
            else:
                os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=self.grace)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if proc.poll() is None:
            try:
                procs.kill_tree(psutil.Process(proc.pid), grace=2.0)
            except psutil.Error:
                pass

    def _stop_all(self) -> None:
        for spec in reversed(self.specs):  # the UI first, then the API it proxies to
            proc = self.children.get(spec.name)
            if proc is not None:
                self._stop_child(proc)
```
(If `node` on Windows ignores `CTRL_BREAK_EVENT`, the tree kill after `grace` covers it; the web
server holds no data. Log what the Windows CI job observes.)

**Step 3:** Tests pass; `ruff`. **Step 4: Commit** `feat(maestro): a supervisor that runs, restarts and stops the two servers on every OS`.

---

### Task 6: The everyday commands — `start`, `stop`, `restart`, `status`, `logs`, `open`, `run`

**Files:**
- Modify: `backend/maestro_cs/cli.py`
- Create: `backend/maestro_cs/control.py` (start/stop/status logic, injectable for tests),
  `backend/maestro_cs/prefetch.py`
- Test: `backend/tests/maestro_cs/test_cli.py`, `backend/tests/maestro_cs/test_control.py`

**Behaviour (the tests assert each line):**
- `cli.main(argv=None) -> int`, argparse subcommands; `maestro` with no command prints the short
  help and exits 0; `maestro --version` prints `maestro-career-studio <version>`. Errors are one
  sentence on stderr, exit 1; never a traceback for a `HomeError`/port problem.
- `maestro run` (documented for autostart only): `load_context()` → build the two `ChildSpec`s:
  backend `[sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port",
  <backend>, "--workers", "1", "--no-server-header", "--ws", "none"]`, cwd = home, health
  `http://127.0.0.1:<backend>/health`, timeout 120 s; web `[node_path(), "server.js"]`, cwd =
  `web_dir()`, health `http://127.0.0.1:<web>/api/version` (proves the proxy too), timeout 60 s.
  Refuses with a sentence if `web_dir_ok()` is false ("This copy has no web app; reinstall with
  `uv tool install --force …`"). **If `run/supervisor.json` names a live supervisor
  (`procs.live_process`), prints "Maestro is already running." and exits 0** — so a launchd/systemd
  copy started next to a manual one never fails, and `KeepAlive.SuccessfulExit=false` /
  `Restart=on-failure` never loop. Logs to `logs/supervisor.log`.
- `maestro start [--no-browser] [--skip-model]`:
  1. already running (supervisor record live) → "Maestro is already running at http://localhost:3000." exit 0.
  2. Port preflight on both ports: `port_free(port)` (POSIX `SO_REUSEADDR` so TIME_WAIT passes —
     the native lesson; Windows `SO_EXCLUSIVEADDRUSE`). Busy and `GET /api/version` on it returns a
     JSON `version` → "Another Maestro (version X) is already running on port 8001 — probably the
     Docker install. Stop it first (`docker compose down` in its folder), then run `maestro start`."
     Busy otherwise → "Port 8001 is in use by another program. Maestro needs ports 8001 and 3000."
  3. First start only (no database file yet and no `*.json` in `base_resumes/`): copy the demo
     resume in.
  4. Model: if `fastembed_cache/` is empty and not `--skip-model`, print "Downloading the scoring
     model (about 100 MB, once)…" and run `python -m maestro_cs.prefetch` with the backend env
     (it imports `app.services.ats.embeddings` and builds the configured model — find the exact
     loader, `_model(load_config().…)`, and call it; no new download code). On failure print "The
     scoring model didn't download; the first score will try again." and continue.
  5. Detach `[sys.executable, "-m", "maestro_cs", "run"]`: POSIX `start_new_session=True`;
     Windows `creationflags=CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW`; stdin/stdout/stderr →
     DEVNULL / `logs/supervisor.log`; env = parent env + `MAESTRO_HOME` (the supervisor loads the
     rest itself).
  6. Wait for `run/ready.json` (up to 180 s), printing a dot line every 5 s; if the supervisor
     exits first, print its log's last 20 lines and exit 1.
  7. Print `Maestro is running: http://localhost:3000` and the MCP URL; open the browser unless
     `--no-browser` (`webbrowser.open`).
- `maestro stop`: no live supervisor → clean stale records, also stop any live **recorded**
  `backend`/`web` child (an earlier hard-killed supervisor) → "Maestro is not running." exit 0.
  Live → create `run/stop.request`, POSIX also SIGTERM; wait ≤ 25 s; still alive → `kill_tree`.
  Print "Maestro stopped." Never signal a process whose record does not match (`live_process`).
- `maestro restart` = stop then start (`--no-browser` default for restart).
- `maestro status [--json] [--offline]`: running or stopped (exit 0 / 3); installed version;
  backend `/api/version` (version + schema); URLs; data folder; autostart on/off (Task 10's
  `is_enabled()`); RSS of backend and web (psutil) in `--json`; newer release (Task 11's
  `latest_version()`, 3 s timeout, skipped with `--offline`; "couldn't check" on failure).
- `maestro logs [backend|web|supervisor|update] [-n 50] [-f]`: prints the tail; `-f` follows until
  Ctrl+C. Default: backend.
- `maestro open`: opens `http://localhost:<web>`; says so if not running.

**Tests:** drive `control` with a fake `spawn`, fake `http_get`, fake `port_free` and `tmp_path`
homes. One `@pytest.mark.integration` test runs the real `maestro start --no-browser --skip-model`
→ `status --json` → `stop` with `MAESTRO_HOME=tmp_path` and spare ports: the backend is the real
uvicorn; the web child is replaced through `MAESTRO_WEB_CMD` (a **test-only** env var holding a
JSON argv, e.g. a tiny Python server answering `/api/version`; the code comment says it is for
tests). Real Node is exercised by Task 12's smoke, not here.

**Commit** `feat(maestro): start, stop, restart, status, logs and open`.

---

### Task 7: `backup`, `migrate`, `check`

**Files:** `backend/maestro_cs/cli.py`, `backend/maestro_cs/control.py`; tests in
`backend/tests/maestro_cs/test_maintenance.py`.

- `maestro backup [--out PATH]`: runs `[sys.executable, "-m", "app.tools.backup_db", "--out", P]`
  with the backend env (read `app/tools/backup_db.py` for its flags and verification first). Default
  `P` = `backups/db-<UTC yyyymmddTHHMMSSZ>.sqlite3.gz`; keeps the newest 5 `db-*.sqlite3.gz` in
  `backups/` (never deletes an `--out` elsewhere). Works with Maestro running (online backup API).
  Prints the path. No database yet → "Nothing to back up yet." exit 0.
- `maestro migrate`: refuses while running ("Stop Maestro first; it migrates itself at start.");
  otherwise applies env (R8) and calls `app.services.seeding.run_migrations()`.
- `maestro check` (JSON to stdout, exit 0; network-free): `{"version", "home", "code_head",
  "db_revision" (null if no DB), "engines": app.services.engines.probe() as dict, "web": bool,
  "node": node version string or null}`. `code_head` from `ScriptDirectory` over the same
  `migrations` dir `run_migrations` uses (share a helper `seeding.migrations_dir()`).

Tests: backup rotation keeps 5; `--out` honoured; migrate refuses when a live record exists;
check's keys and `code_head` == the head the test computes; R8 — patch `os.environ` and assert
`app.config` is imported only after `apply_env` (import-order test via a fresh subprocess).

**Commit** `feat(maestro): backup, migrate and check`.

---

### Task 8: `maestro mcp` — the stdio ↔ HTTP bridge in Python

**Files:**
- Create: `backend/maestro_cs/bridge.py`
- Test: `backend/tests/maestro_cs/test_bridge.py` — one test per case in `mcpb/tests/bridge.test.js`
  (read it; mirror names), using `httpx.MockTransport`.

**Contract (identical to `mcpb/server/index.js`, including bridge.test.js:198 "the overall
deadline includes a response body that keeps sending data"):** newline-delimited JSON-RPC on stdin/stdout;
each line POSTed to `http://localhost:<port>/mcp[/profile]` with `Accept: application/json,
text/event-stream` and the session header once known; remembers `initialize` and
`notifications/initialized`; on HTTP 404 with a session, replays the handshake without the stale
id, then retries the line once; 200 → write the body; 202 → nothing; other statuses → a JSON-RPC
error for requests with an `id`; parse error → `{"id": null, "error": {"code": -32700}}`; a
non-object line → a stderr sentence; connection refused → "Maestro Career Studio is not running at
http://localhost:8001. Start it with `maestro start` and try again."; 10-minute bound → the same
timeout sentence as Node (tests inject `timeout=0.08`, as the Node tests use 80 ms). stdout carries JSON-RPC only (write bytes + `\n`, flush; never `print`
diagnostics there). `trust_env=False` (loopback; the branch A lesson).

```python
"""`maestro mcp`: Claude Desktop's stdio ↔ this install's /mcp. Twin of mcpb/server/index.js."""
from __future__ import annotations

import json
import sys
import time
from typing import Any, BinaryIO, TextIO

import httpx

TIMEOUT_SECONDS = 600.0
TIMEOUT_MESSAGE = ("Maestro Career Studio did not answer within 10 minutes. "
                   "The request may still finish in the app.")
FAILED_MESSAGE = ("Maestro Career Studio could not complete the request. "
                  "The request may still finish in the app.")


class BridgeError(Exception):
    pass


def mcp_url(backend_url: str, profile: str = "full") -> str:
    base = backend_url.rstrip("/")
    return f"{base}/mcp" if profile == "full" else f"{base}/mcp/{profile}"


class Bridge:
    def __init__(self, backend_url: str, profile: str = "full", *,
                 client: httpx.Client | None = None, stdout: BinaryIO | None = None,
                 stderr: TextIO | None = None, timeout: float = TIMEOUT_SECONDS) -> None:
        self.backend_url = backend_url.rstrip("/")
        self.url = mcp_url(backend_url, profile)
        self.timeout = timeout
        self.client = client or httpx.Client(timeout=httpx.Timeout(timeout), trust_env=False)
        self.stdout = stdout or sys.stdout.buffer
        self.stderr = stderr or sys.stderr
        self.session_id: str | None = None
        self.initialize_line: str | None = None
        self.initialized_line: str | None = None

    def write(self, body: Any) -> None:
        self.stdout.write(json.dumps(body, separators=(",", ":")).encode("utf-8") + b"\n")
        self.stdout.flush()

    def post(self, line: str) -> tuple[int, str | None, Any]:
        headers = {"Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        # One deadline covers connect, headers and the complete body (httpx's own timeouts are
        # per phase, so a body that keeps trickling would never time out).
        deadline = time.monotonic() + self.timeout
        try:
            with self.client.stream("POST", self.url, content=line.encode("utf-8"),
                                    headers=headers) as response:
                chunks = []
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    if time.monotonic() > deadline:
                        raise httpx.ReadTimeout(TIMEOUT_MESSAGE)
                status, session = response.status_code, response.headers.get("mcp-session-id")
        except httpx.TimeoutException:
            message = TIMEOUT_MESSAGE
        except (httpx.ConnectError, httpx.UnsupportedProtocol, httpx.InvalidURL):
            message = (f"Maestro Career Studio is not running at {self.backend_url}. "
                       "Start it with `maestro start` and try again.")
        except httpx.HTTPError:
            message = FAILED_MESSAGE
        else:
            body = None
            if status == 200:
                try:
                    body = json.loads(b"".join(chunks))
                except ValueError:
                    raise BridgeError("Maestro Career Studio returned an invalid JSON response.") from None
            return status, session, body
        print(message, file=self.stderr, flush=True)
        raise BridgeError(message)

    @staticmethod
    def accepted(result: tuple[int, str | None, Any]) -> tuple[int, str | None, Any]:
        if result[0] not in (200, 202):
            raise BridgeError(f"Maestro Career Studio returned HTTP {result[0]}.")
        return result

    def fail(self, request: Any, message: str) -> None:
        items = request if isinstance(request, list) else [request]
        errors = [{"jsonrpc": "2.0", "id": item["id"], "error": {"code": -32000, "message": message}}
                  for item in items if isinstance(item, dict) and "method" in item and "id" in item]
        if errors:
            self.write(errors if isinstance(request, list) else errors[0])

    def handle(self, line: str) -> None:
        if not line.strip():
            return
        try:
            request = json.loads(line)
        except ValueError:
            self.write({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            return
        if not isinstance(request, (dict, list)):
            print("The MCP message must be a JSON object or array.", file=self.stderr, flush=True)
            return
        method = request.get("method") if isinstance(request, dict) else None
        if method == "initialize":
            self.initialize_line = line
        elif method == "notifications/initialized":
            self.initialized_line = line
        try:
            result = self.post(line)
            if result[0] == 404 and self.session_id and self.initialize_line:
                # A restarted backend must see a fresh handshake without the stale id.
                self.session_id = None
                self.session_id = self.accepted(self.post(self.initialize_line))[1]
                if self.initialized_line:
                    self.accepted(self.post(self.initialized_line))
                result = self.post(line)
            self.accepted(result)
            if method == "initialize":
                self.session_id = result[1]
            if result[0] == 200:
                self.write(result[2])
        except BridgeError as exc:
            self.fail(request, str(exc))

    def serve(self, stdin: BinaryIO | None = None) -> int:
        for raw in (stdin or sys.stdin.buffer):
            self.handle(raw.decode("utf-8", "replace").rstrip("\r\n"))
        return 0
```
`cli`: `maestro mcp [--profile P]` reads the port from the install's `maestro.env` (no env applied,
no `app` import); `--url` overrides for tests. Exit 0 on EOF; SIGTERM/SIGINT exit 0 quietly.

**Step:** all mirrored cases pass; then a live check: scratch backend on a spare port, pipe
`initialize` + `notifications/initialized` + `tools/list` through `maestro mcp --url …`, expect 88
tools; restart the backend mid-session and send `tools/list` again (replay works).

**Commit** `feat(maestro): maestro mcp, a Python stdio bridge with session replay`.

---

### Task 9: `maestro connect`, and the card's Claude Desktop row on a uv install

**Files:**
- Create: `backend/maestro_cs/connect.py`
- Modify: `backend/app/services/connect.py`, `backend/tests/test_setup_connect.py`
- Test: `backend/tests/maestro_cs/test_connect.py`

**Card (backend):** when `os.environ.get("MAESTRO_INSTALL") == "uv"` (read it through
`settings` — add `maestro_install: str = ""` to `Settings`), the `claude-desktop` client is
`kind="command"`, `value="maestro connect claude-desktop"`; otherwise unchanged. If the owner said
yes to the scope proposal, the `claude-code` value becomes
`claude mcp add --scope user --transport http maestro-career-studio <url>`. Update every place that
prints it: `README.md:437`, `docs/GETTING_STARTED.md:249`, `backend/mcp_server/README.md:41`,
`plugins/maestro-career-studio/README.md:52`, `docs/RELEASING.md:43`, `scripts/setup-mcp.sh:74`
(its own `--scope` handling at :151 explains local vs user — keep it consistent), and the pin in
`backend/tests/test_setup_connect.py:33` (re-grep `claude mcp add` before editing). Test both installs. Check the card renders
`kind="command"` for any client id (`connect-assistant-card.tsx:97`) — no frontend change expected.

**CLI:** `maestro connect [client] [--profile P] [--print]`. No client → list the clients and,
for Claude Desktop/Claude Code/Codex, whether Maestro is already configured (read their config;
don't run anything). Applies env (R8), imports `app.services.connect.build(profile)` for the
strings — no second copy of them.
- `claude-code`: `claude` on PATH and not `--print` → run the command, print its outcome;
  else print it.
- `codex`: same with `codex`.
- `cursor`, `vscode`: print the deep link and `webbrowser.open` it (unless `--print`).
- `claude-desktop`: config path — macOS `~/Library/Application Support/Claude/claude_desktop_config.json`,
  Windows `%APPDATA%\Claude\claude_desktop_config.json`, Linux `~/.config/Claude/claude_desktop_config.json`.
  Read (missing → `{}`; invalid JSON → refuse with a sentence, never overwrite), back up to
  `<name>.bak-<UTC stamp>` when it exists, set
  `mcpServers["maestro-career-studio"] = {"command": <shim_path()>, "args": ["mcp"] (+ ["--profile", P])}`,
  write atomically. **Claude Desktop keeps its server list in memory and writes the file back on
  routine events while it runs, silently erasing edits** (agent-lessons 542d774e/2fe8ba3d/f7eb8621;
  its `main.log` shows "Config file written"). So: if a Claude Desktop process is running (psutil;
  verify the process names on macOS, Windows and Linux and record them), say "Quit Claude Desktop
  fully (not just close the window); Maestro will add itself as soon as it has quit." and hand the
  write to a detached waiter (same detach helper as Task 11's Windows handoff) that waits ≤ 10 min
  for the process to exit, backs up, writes, re-reads to confirm the entry, and logs to
  `logs/connect.log`; `--now` skips the wait for users who know it is closed. After any write,
  re-read and warn if the entry is missing. Final message: "Open Claude Desktop to load Maestro."
  If the old Docker bundle may be installed, add: "If Claude Desktop also lists the older Maestro extension, remove it in
  Settings › Extensions."
- `other`: print the URL.

Tests: config merge keeps other servers and keys; backup created; invalid JSON untouched; absolute
shim path (never bare `maestro`); a running-Desktop fake defers the write to the waiter (injected)
and nothing is written in-process; runners injected for `claude`/`codex`; deep links match `build()`.

**Commit** `feat(maestro): maestro connect for every agent app; the card shows it on uv installs`.

---

### Task 10: `maestro autostart on|off|status`

**Files:** `backend/maestro_cs/autostart.py`; tests `backend/tests/maestro_cs/test_autostart.py`.

Pure render functions (tested by snapshot-style asserts) + an injected `runner` for system calls:
- **macOS:** `~/Library/LaunchAgents/io.github.seinun-ai.maestro-career-studio.plist` —
  `ProgramArguments = [shim_path(), "run"]`, `RunAtLoad = true`,
  `KeepAlive = {SuccessfulExit = false}` (a `maestro stop` exits 0 and stays stopped; a crash
  restarts), `EnvironmentVariables = {MAESTRO_HOME}` only when the user set one,
  `StandardOutPath`/`StandardErrorPath` = `logs/launchd.log`, `ProcessType = Interactive`.
  on: write (0644), `launchctl bootstrap gui/<uid> <plist>`; off: `launchctl bootout gui/<uid>/<label>`, delete.
- **Linux:** `~/.config/systemd/user/maestro-career-studio.service` — `ExecStart=<shim> run`,
  `Restart=on-failure`, `RestartSec=5`, `[Install] WantedBy=default.target`; on: write,
  `systemctl --user daemon-reload`, `systemctl --user enable maestro-career-studio.service`, then
  `loginctl enable-linger <user>` (warn, don't fail, if refused: "Maestro will start when you log
  in, not at boot."). off: disable, delete, daemon-reload. No `systemctl` → one sentence.
- **Windows:** `schtasks /Create /TN "Maestro Career Studio" /SC ONLOGON /RL LIMITED /F /TR "<pythonw.exe> -m maestro_cs run"`
  (`venv_python(windowless=True)` so no console window appears at logon); off: `schtasks /Delete /TN … /F`.
  Quote the `/TR` value for paths with spaces (test it).
- `is_enabled()` for `status`. Off by default. The platforms differ on "now", and the messages
  say so: **macOS** `bootstrap` of a `RunAtLoad` plist starts Maestro immediately ("Maestro will
  start when you log in; it is starting now.") — harmless if it already runs, because `maestro run`
  exits 0 when a live supervisor exists; `off` (`bootout`) stops the launchd copy, so print
  "Maestro no longer starts at login and has stopped; run `maestro start` to use it now."
  **Linux** `enable` without `--now` and **Windows** ONLOGON do not start it now ("Maestro will
  start when you log in."). Test each message.

**Commit** `feat(maestro): autostart at login on macOS, Linux and Windows`.

---

### Task 11: `maestro update`

**Files:** `backend/maestro_cs/update.py`; tests `backend/tests/maestro_cs/test_update.py`.

Flow (each step a function with injected network/runner):
1. `latest_version()` — `GET https://pypi.org/pypi/maestro-career-studio/json`, `info.version`,
   3 s timeout, `trust_env=True` (users behind proxies), ignore pre-releases. Compare with a
   numeric `X.Y.Z` tuple (no `packaging` dependency).
2. Up to date → "Maestro X is the newest version." exit 0 (unless `--source`).
3. `breaking_notes(current, new)` — `GET https://raw.githubusercontent.com/seinun-ai/maestro-career-studio/v<new>/CHANGELOG.md`,
   parse `## [X.Y.Z]` sections with `current < X.Y.Z <= new`, print each one's
   `### Breaking changes` body. Fetch failure → "Couldn't read the changelog; see <URL>." Then
   confirm `Update from X to Y? [y/N]` unless `--yes`.
4. `maestro backup` (Task 7) — a failed snapshot aborts the update with nothing changed.
5. Stop (Task 6) if running; remember whether it was.
6. Upgrade — find `uv` (`shutil.which("uv")`, then `~/.local/bin/uv`, `~/.cargo/bin/uv`,
   `%USERPROFILE%\.local\bin\uv.exe`; none → print the manual command and how to restart, exit 1,
   data untouched). Command: `uv tool upgrade maestro-career-studio`, or with `--source WHEEL`
   (tests/offline) `uv tool install --force --python 3.12 WHEEL`.
   - POSIX: import everything the post-upgrade path needs (`webbrowser`, `urllib.request`,
     `json`, `subprocess`, the control module) **before** running uv, which swaps the venv's files
     under this process. Run it; on failure print its output, start the old version again if it was running,
     exit 1. On success run `<shim_path()> start --no-browser` as a **new** process (this one
     still has the old code loaded) if it was running.
   - Windows (R6): spawn detached `cmd /d /c` that waits for this pid to exit (poll with
     `tasklist /FI "PID eq <pid>"` in a short loop, ≤ 30 s), runs the uv command, then
     `<shim> start --no-browser` if it was running, appending all output to `logs/update.log`;
     print "Updating in the background. Run `maestro status` in a minute; details in
     `maestro logs update`." exit 0.
7. After start, compare `/api/version` with the expected version; mismatch → print the snapshot
   path and the rollback line `uv tool install --force --python 3.12 maestro-career-studio==<old>`
   (automated downgrade is a non-goal; migrations may not reverse — say so).

Tests: version compare edge cases; changelog parse picks only the right sections (fixture text);
abort paths (backup fails → no stop, no uv); uv missing message; Windows command string built
correctly (quote paths with spaces) without running it; `--yes` skips the prompt.

**Commit** `feat(maestro): maestro update — snapshot, stop, upgrade, start`.

---

### Task 12: Build, check and smoke scripts (local proof on macOS)

**Files:**
- Create: `scripts/build_wheel.py`, `scripts/check_wheel.py`, `scripts/smoke_uv_install.py`
- Test: `backend/tests/test_wheel_scripts.py` (pure helpers only: member rules, version rewrite)

`build_wheel.py [--version X] [--skip-frontend | --web-from WHEEL] [--out dist]` (stdlib only, runs
on all three CI OSes). `--web-from` copies `maestro_cs/web/` out of an existing wheel instead of
building the frontend (the upgrade job has no frontend build; its second wheel would otherwise ship
no web app and `maestro run` would refuse):
1. Version = `--version` or `backend/pyproject.toml`'s.
2. Unless `--skip-frontend`: `npm ci` and `npm run build` in `frontend/` with
   `NEXT_PUBLIC_APP_VERSION=<version>`, `NEXT_TELEMETRY_DISABLED=1`.
3. Stage in a temp dir: the **git-tracked** files under `backend/` (`git ls-files backend`) minus
   `backend/tests/**` and `backend/mcp_server/tests/**` and `backend/scripts/**`; copy
   `frontend/.next/standalone/` → `maestro_cs/web/`, `frontend/.next/static/` →
   `maestro_cs/web/.next/static/`, `frontend/public/` → `maestro_cs/web/public/`,
   `base_resumes/example.json` → `maestro_cs/data/example.json`; rewrite the staged
   `pyproject.toml` version when `--version` differs (never edit the tree).
4. `uv build --wheel --out-dir <out>` in the staged backend (fallback `python -m build` if uv is
   absent); run `check_wheel.py` on the result; print path and size.

`check_wheel.py WHEEL`: name `maestro_career_studio-<v>-py3-none-any.whl`; `METADATA` name/version;
`entry_points.txt` has `maestro` and `maestro-career-studio-mcp`; required members
(`app/main.py`, `mcp_server/http.py`, `migrations/env.py`, `migrations/script.py.mako`,
`migrations/prompt_defaults.lock.json`, at least one `migrations/versions/*.py`,
`maestro_cs/cli.py`, `maestro_cs/maestro.env.template`, `maestro_cs/data/example.json`,
`maestro_cs/web/server.js`, `maestro_cs/web/.next/BUILD_ID`, `maestro_cs/web/.next/static/`,
`maestro_cs/web/public/`, `app/assets/fonts/xcharter/`, `app/prompts/`); forbidden: any `tests/`
dir, `*.pyc`, `.env`, `node_modules/sharp`, `node_modules/@img`, `*.node`, `*.dylib`, `*.so`,
`*.dll`, `*.exe`, any `base_resumes/` other than the demo; size ≤ 30 MB. Exit 1 listing every
problem.

`smoke_uv_install.py --wheel W [--expect-version V] [--keep]` (stdlib only): temp
`UV_TOOL_DIR`, `UV_TOOL_BIN_DIR`, `MAESTRO_HOME`; `uv tool install --python 3.12 W`; then via the
installed shim: `maestro --version`; `maestro start --no-browser`; `GET :8001/health` ok;
`:8001/api/version` == V; `:3000/api/version` same through the proxy; `:3000/` Host-forged → 400;
`POST :8001/mcp` `initialize` (serverInfo.version == V) + `tools/list` (88); the demo base has
`pdf_rendered_at` (Typst render); create a job and score it against the demo base with **no LLM
key** through whatever path the Companion/agents use (read `backend/app/routers/` and the MCP
tools — `store_extracted_jd` + `score_ats` is the likely pair; record the one you use); pipe
`initialize`/`tools/list` through `maestro mcp`; `maestro backup` (file exists, gzip magic);
`maestro check` (JSON, `db_revision == code_head`); `maestro status --json` (records RSS);
**newest-dependency guard** — `uv tool install` resolves the newest FastAPI/Starlette/uvicorn, not
`requirements.lock` (the CI lesson agent-lessons 670100d9/e354b9bb: Starlette 1.7 once changed
empty 404s), so assert over HTTP what the security boundary relies on: forged Host → 400 on both
ports, foreign Origin → 403 on :8001, the `next.config.ts` security headers on :3000, and the
anti-fingerprint 404 shape (find the test that pins it in `backend/tests/` and assert the same
headers/body); one real tool call through `maestro mcp` (e.g. `list_jobs`) returns a `text`
content block (Claude Desktop reads `content`, not `structuredContent` — agent-lessons fed70244);
`maestro stop`; both ports free; no process of the tool venv left (psutil-free check: the ports
plus `maestro status` exit 3). Prints a summary block (versions, timings, RSS). Exit 1 on the first
failure with the last 40 lines of each log **and the exception type** for anything that is not a
connection failure (never report "not running" for a bug — agent-lessons 575efc4a). Everything
runs under `try/finally`: `maestro stop`, then delete the temp tool dir and home unless `--keep`
(a 522 MB venv per run fills a disk, and disk-full shows up first as SQLite I/O errors —
agent-lessons dd6bc03c/9edb9066). Pytest fixtures in `tests/maestro_cs` likewise stop every
supervisor they start (`stop.request`, then kill) in teardown and assert no child is left.

**Local proof (this Mac):** `python3 scripts/build_wheel.py --out $SP/dist` → check passes, size
recorded; `python3 scripts/smoke_uv_install.py --wheel $SP/dist/*.whl --expect-version <v>` on
spare ports (set `MAESTRO_PORT`/`MAESTRO_WEB_PORT` through the smoke's own env) → green. Record
in the Gate table (G2, G3-macOS).

**Commit** `build: wheel build, wheel check and an end-to-end uv install smoke`.

---

### Task 13: CI and the PyPI release workflow

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `.github/workflows/pypi.yml`
- Test: `backend/tests/test_workflows.py` (or the existing workflow-pin test — grep for one;
  parse YAML and pin the jobs below)

**ci.yml:**
- `maestro-cli` unit job: matrix `[ubuntu-latest, macos-latest, windows-latest]`, Python 3.12,
  `pip install -e "backend[dev]"`, `pytest tests/maestro_cs -q` (the supervisor tests run on all
  three; Windows-only tests run there).
- `uv-wheel` job (ubuntu, Node 24 + Python 3.12 + uv): `scripts/build_wheel.py --version 0.0.0.dev1`
  → upload artifact.
- `uv-smoke` job: needs `uv-wheel`; matrix the three OSes; download; cache
  `~/.cache/maestro-smoke-model` keyed on the model id and point the smoke's
  `FASTEMBED_CACHE_PATH` there (copy in before start) so each run doesn't download 100 MB;
  `python scripts/smoke_uv_install.py --wheel dist/*.whl --expect-version 0.0.0.dev1`.
- `uv-upgrade` job: needs `uv-wheel`; matrix `[ubuntu-latest, windows-latest]`; builds a second
  wheel `--version 0.0.0.dev2 --web-from <downloaded dev1 wheel>` (the backend version is what's
  checked; the web bundle is reused, so the frontend reports dev1 — expected); installs dev1,
  starts, creates a base resume through the API, `maestro update --source <dev2 wheel> --yes`
  (Windows: then poll `maestro status --json` up to 120 s), asserts backend version dev2, the base
  resume still there, a snapshot in `backups/`, then stops.
- Trigger: the same as the existing jobs (push to `main`, pull requests; ci.yml has no `paths:`
  filters today — don't invent them). Note in the CHANGELOG/Deviation log that the backend,
  mcp-cold-install and dependency-audit installs now also download `nodejs-wheel-binaries`
  (~60 MB wheel, ~200 MB installed) each run; accepted.
- Match the repo's action pinning style (SHA pins if the existing jobs use them).

**pypi.yml:**
```yaml
name: pypi
on:
  push:
    tags: ["v*"]
  workflow_dispatch:
jobs:
  build:      # ubuntu-24.04: fail unless ${GITHUB_REF_NAME#v} == backend/pyproject.toml version
              # (tag runs only); build_wheel.py; check_wheel.py; upload artifact
  smoke:      # needs build; matrix macos-latest / ubuntu-latest / windows-latest; smoke_uv_install.py
  publish:    # needs smoke; if: startsWith(github.ref, 'refs/tags/v'); environment: pypi
              # permissions: id-token: write; pypa/gh-action-pypi-publish (pinned) — trusted
              # publishing, no token anywhere in the repo
```
`workflow_dispatch` runs build + smoke only (a dry run). Owner task: create the PyPI project's
**pending trusted publisher** (owner `seinun-ai`, repo `maestro-career-studio`, workflow
`pypi.yml`, environment `pypi`) and the GitHub environment `pypi` before the first tag.

**Proof:** push the branch to a fork/PR **only with the owner's go-ahead**; until then validate
with `python -c "import yaml…"` parsing + the pin test + `actionlint` if installed. Gate G3/G4
record "pending CI" until the first run.

**Commit** `ci: build, smoke and upgrade-test the uv install on macOS, Linux and Windows; publish to PyPI on tags`.

---

### Task 14: Docs, SYSTEM.md, privacy and security

**Files:** `README.md`, `docs/GETTING_STARTED.md`, `docs/UPDATING.md`, `PRIVACY.md`,
`SECURITY.md`, `SYSTEM.md`, `docs/entities/others.md`, `CHANGELOG.md`, `docs/RELEASING.md`,
`backend/mcp_server/README.md` (Claude Desktop via `maestro connect claude-desktop`),
`plugins/maestro-career-studio/README.md` (one line: uv installs use `maestro connect`).

- README: "Install without Docker (uv)" as the first route for technical users, above Quickstart:
  macOS/Linux `curl -LsSf https://astral.sh/uv/install.sh | sh`, Windows
  `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`, then
  `uv tool install --python 3.12 maestro-career-studio` and `maestro start`. A command table
  (start/stop/restart/status/logs/open/update/connect/autostart/backup). Where data lives (three
  paths). Docker stays the route for contributors and servers.
- GETTING_STARTED: a uv section mirroring the README, plus "Connect your agent" with
  `maestro connect …`; the Companion works unchanged (ports 8001/3000).
- UPDATING: `maestro update` (snapshot first, breaking notes, Windows runs in the background), the
  rollback line, and "a Docker install and a uv install are separate copies; don't run both at once".
- PRIVACY: the code comes from PyPI; `maestro status`/`maestro update` contact `pypi.org` (version)
  and `raw.githubusercontent.com` (changelog) only when you run them; the scoring model downloads
  from Hugging Face on first start into your Maestro folder. PRIVACY.md has no model-download
  wording today (Docker downloads it too) — write it fresh and make it true for both installs.
- SECURITY: both servers bind 127.0.0.1; on Windows file modes are not enforced, so protection
  is the per-user `%APPDATA%` ACL (state what Task 2/CI verified); the web server process gets no
  API keys; `maestro.env` is the only secrets file; on Windows, sync's reads lose the open-time
  symlink refusal (no O_NOFOLLOW); S_ISREG and the packer's symlink check remain.
- SYSTEM.md: §9 the B′ route (one paragraph: wheel contents, `maestro run` supervisor, home
  layout, R8 rule), §3/§12 the `MAESTRO_HOME` folder per OS, invariants: `inv-one-home` (one
  folder per user shared with the future app; nothing writes under site-packages) and
  `inv-env-before-app` (R8). §13 rows: native `scripts/native/*` → `maestro` (trigger: the bot runs
  on `maestro` for one release); `requirements.lock` excludes `nodejs-wheel-binaries` (R4). Stay
  under 1000 lines; move detail to `docs/entities/others.md`.
- CHANGELOG `[Unreleased]`: Added (the uv install, the `maestro` command, Windows x64/Linux/macOS),
  Changed (distribution renamed; frontend image runs `node server.js`; `/health/memory` on
  Windows), Breaking changes (none for Docker users — say so).
- `frontend/package.json` `"start": "next start"` now warns under standalone output: point it at a
  small script that assembles static/public beside `.next/standalone/server.js` and runs it, or drop
  it and fix every reader; PRIVACY.md:184's `npm start` line becomes "outside Docker and the uv
  install (which set `NEXT_TELEMETRY_DISABLED=1`), set it yourself" (review of 99e2245a).
- RELEASING: §2 note that `backend/pyproject.toml` is also the PyPI version (no new row, R1); a
  new step after the tag: confirm the `pypi` workflow published, `uv tool install --python 3.12
  maestro-career-studio==X.Y.Z` on a clean machine, `maestro status`.

Run `check_system_md.py`, the docs slop check, the ledger check. **Commit** `docs: install and run Maestro without Docker (uv)`.

---

### Task 15: Gates

Run each, record in the Gate table (with numbers), fix or log anything red.

## Gate table

| Gate | Pass condition | Result |
|---|---|---|
| G0 baseline | Task 0 | 42839099 (+ plan-only commits): tests/ 10,699 passed / 3 skipped (208 s) + mcp_server/tests 526 passed = 11,225 / 3; Next 16.3.8, nodejs-wheel-binaries 24.19.0, uv 0.9.28; `--scope user` proposal: owner did not object → yes |
| G1 suite | Full suite (both trees) + `tests/maestro_cs` green; ruff; check_system_md; slop; ledger; frontend tsc/lint/build | |
| G2 wheel | `build_wheel.py` + `check_wheel.py` pass; `py3-none-any`; size ≤ 30 MB (spike: 10.6 MB) | |
| G3 smoke | `smoke_uv_install.py` green on macOS (local) and on the CI macOS/Ubuntu/Windows jobs | |
| G4 upgrade | dev1 → dev2 via `maestro update --source` on Ubuntu and Windows CI: version moves, data kept, snapshot present | |
| G5 owner Mac | Owner: `uv tool install` the built wheel, `maestro start`, UI works in Chrome, the Companion connects, `maestro connect claude-desktop` (with Desktop running: the deferred write lands after quitting) → Claude Desktop lists Maestro's tools, `maestro stop`/`start` round-trip; start from a terminal, close that terminal, Maestro keeps running | |
| G6 autostart | macOS: `autostart on` → log out/in (or `launchctl kickstart`) starts it; `off` removes it. Linux CI: unit renders and `systemd-analyze --user verify` passes where available. Windows CI: task created and deleted | |
| G7 Docker | Frontend image builds and serves `/` + `/api/version` with `node server.js` (CI images job or local when the daemon is up) pending: Docker daemon down at Task 3; run `docker build frontend/` + the same checks when it is up (local proof with the wheel's Node 24.19.0 passed, see Deviation log) |
| G8 boundary | During the smoke: both listeners on 127.0.0.1 only; forged Host → 400 on :8001 and :3000; foreign Origin → 403 on :8001 | |
| G9 memory | RSS of backend and web after the smoke (spike: ~211 MB + ~125 MB on macOS) | |
| G10 best-effort | Windows ARM64 (x64 Python under emulation) and musl: documented, not gated | |

## Deviation log

| Task | Plan said | Did instead | Why |
|---|---|---|---|
| 1 | Step 4: regenerate `backend/requirements.lock` in `python:3.12-slim` (+ `--unsafe-package nodejs-wheel-binaries`), update the Dockerfile recipe comment | Not done: Docker is not running | Release-time item. Until then the lock still names `maestro-career-studio-backend` in its `# via` comments and lacks `psutil`; the Dockerfile's `--no-deps -e .` therefore leaves the image without `psutil`, which only `maestro_cs` imports (not yet used by any Docker path). Regenerate before the next image build. |
| 1 | `pip uninstall -y … && pip install -e backend` in the branch venv | `uv pip uninstall` / `uv pip install -e backend` with `VIRTUAL_ENV` set | The branch venv has no `pip` module; uv is on PATH. Result: `maestro` and `maestro-career-studio-mcp` shims resolve, mcp stays 1.29.1. |
| 1 | `test_run_migrations_location.py`: "reuse its database fixture pattern" | Standalone test with a `tmp_path` file and `TEST_DATABASE_URL`, no shared fixture | The shared `db_session` fixture clears the session DB; this test must migrate a fresh file from scratch. |
| 1 | Project URLs "the GitHub repo" | `https://github.com/seinun-ai/maestro-career-studio` | That is the repo's `origin` and the README's owner. |
| 2 | Test file `tests/test_memory_readout.py` or the existing memory test file | New `backend/tests/test_memory_windows.py` beside the untouched `tests/test_health_memory.py` | "ADD tests beside pinned tests". The reload test restores `sys.modules` and reloads again; `memory.resource` is `None` only inside it. |
| 2 | Step 3 grep hit `memory.py:7 import resource` | Fixed: `try: import resource / except ImportError: resource = None`; `peak_mb()` falls back to psutil RSS | The Step 2 change. |
| 2 | Step 3 grep hit `sync/files.py:249 os.fchmod` | Fixed: `hasattr(os, "fchmod")` guard, else `os.chmod(temp, mode)` before `os.replace` | The Step 2 change; chmod runs on the temp file so the target is replaced already carrying the mode. |
| 2 | Step 3 grep hit `tools/backup_db.py:180 os.fchmod` | Justified, unchanged | Already guarded by `if os.name == "posix":` one line above; `tests/tools/test_backup_db.py` covers it. |
| 2 | Step 3 grep: no other hits (`getuid`, `setsid`, `killpg`, `fork`, `fcntl`, `pwd`, `SIGKILL`, `SIGHUP` absent from `backend/app` and `backend/mcp_server`) | Widened the grep by hand (`O_NOFOLLOW`, `O_NONBLOCK`, `geteuid`, `chown`, `symlink`) and found `sync/files.py:126` and `sync/status.py:47` opening with `os.O_NOFOLLOW \| os.O_NONBLOCK` | Both flags are absent from Windows' `os`, so packing a sync file and `read_key()` raised `AttributeError` (not caught as `OSError`). Fixed with a call-time `_read_flags()` (adds `O_BINARY` on Windows); the following `S_ISREG` check still refuses FIFOs and devices. Windows loses the open-time symlink refusal only for these two reads; `_walk` still raises on any symlink in a packed tree. — Task 14 states this in SECURITY.md Test: `tests/sync/test_files_windows.py::test_reads_work_without_the_posix_only_open_flags`. |
| 2 | "used for both numbers" (psutil RSS for `rss_mb` and `peak_mb`) | As written | Windows has `peak_wset` in psutil, but the plan said RSS for both and `readout()` already floors the peak at the current reading; not worth a second code path before real Windows CI (Task 13). |
| 2 | (review follow-up) `backup_db.py:~175` opens the output without `O_BINARY` | Added `getattr(os, "O_BINARY", 0)`; test spies `os.open` with a fake `O_BINARY` bit | Belt and braces: CPython's `FileIO` already sets binary mode on the fd it wraps, but the flag makes the intent explicit and survives a refactor. Other `os.open` sites left alone: `db.py:73` writes nothing (empty file, `O_EXCL`); `llm.py:454` and `sync/status.py:175` write JSON/key text through `fdopen(..., "w")` that need no byte-exact round trip. |
| 2 | (review follow-up) `status._read_flags` duplicate | `status.py` imports `_read_flags` from `sync/files.py` | No import cycle (`files` imports only `app.config`). |
| 3 | Step 3: `docker build frontend/` and run it if Docker is running | Not done: Docker is not running. Gate G7 stays pending | Proved the same bundle with `node server.js` (nodejs-wheel-binaries Node 24.19.0) on :18918 against a scratch backend on :18917: no `*.node/.dylib/.so/.dll` in the assembled bundle, `/api/version` proxied (200), `/` 307 then 200, static CSS and `public/monaco` files served, forged Host 400 on both ports, security headers present, both listeners 127.0.0.1 only. |
| 3 | Step 1: grep existing tests for pins on `npm start`/`next start` | No test pins either (grep of `backend/tests`, `mcp_server/tests`, `scripts`, `.github`, compose found none) | Nothing to update. `PRIVACY.md:184` and `package.json` still name `npm start` for people running the frontend outside Docker; left unchanged (not re-verified: Next 16 warns that `next start` does not fit `output: "standalone"`; Task 14's PRIVACY.md pass can reword it). |
| 3 | Dockerfile comments | Reworded the runner-stage comments (node_modules/package.json copies gone; why `HOSTNAME=0.0.0.0` is safe: compose binds only the host side to 127.0.0.1) | The `--chown` rationale named `next start`. `frontend/Dockerfile.dev` untouched (dev server, not the runner). |
| 3 | Test file | Added `backend/tests/test_frontend_standalone.py` with 8 source-text pins, including that compose still binds `127.0.0.1:${FRONTEND_HOST_PORT:-3000}:3000` and that the runner copies no `node_modules`/`package.json` | Documents the one reason a 0.0.0.0 listener is acceptable. |
| 4 | `_port` error text "… in maestro.env must be …" | "`MAESTRO_PORT must be a port number from 1 to 65535.`" (no "in maestro.env") | The value can now come from the process environment (precedence R-verification fix), so naming the file would mislead. |
| 4 | `prepare_home`: symlink check on `maestro.env` outside the `try` | Moved inside the `try`; `HomeError` raised there is not re-wrapped, other `OSError`s (including writing `maestro.env`) become "Cannot set up …" | A read-only folder at the env-file step raised a bare `OSError`. |
| 4 | `runtime.node_path()`, `web_dir()`, `shim_path()`, `venv_python()` take injectable `platform`/`environ`/`which`/`prefix`/`executable` arguments | Defaults are the real `sys`/`os` values | Tests never touch the real environment. `node_path` locates `nodejs_wheel` with `importlib.util.find_spec` (no import); Windows layout `nodejs_wheel/node.exe`, POSIX `nodejs_wheel/bin/node`. |
| 4 | `apply_env(home, backend_env)` (R8, added by the controller) | In `home.py`: pops `_STRIPPED` names, `os.environ.update(env)`, `os.chdir(home)` | Per the controller's note; tested with monkeypatch. |
| 4 | `_WEB_STRIPPED` "check Settings for other secret fields" | No additions: `openai_api_key`, `gemini_api_key`, `langfuse_secret_key`, `langfuse_public_key`, `sync_key_file` are already listed; a test walks `Settings.model_fields` for `api_key/secret/token/password/key_file` names and asserts each is in `_WEB_STRIPPED` | Keeps the list honest when a field is added. |

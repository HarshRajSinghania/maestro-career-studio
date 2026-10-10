# Desktop step 3: a downloadable Mac app — design

Status: approved in brainstorm 2026-10-09, awaiting implementation plans.
Predecessors: `2026-09-17-sqlite-migration-design.md` (step 1, shipped),
`2026-09-19-desktop-step2-design.md` (step 2; its `engines` branch shipped,
its `mcp-http` branch was never started). This doc supersedes step 2's §3–§4
and §5 rollout where they disagree, and replaces the one-paragraph step 3
sketch at the end of the step 2 design.

## Goal Card

**Goal:** A Mac user downloads a DMG, drags Maestro Career Studio to
Applications, opens it, and has the full app working, with Claude Desktop and
the Companion connected, and later versions installing themselves without
putting their data at risk. Docker, native and contributor installs keep
working with no change they did not ask for.

**Principles** (how to decide when a plan is ambiguous or wrong):
- Less before more. Branch 0 trims what the installer would otherwise have to
  carry and support; nothing is cut that a user would notice.
- The security boundary does not move. The window is the same
  `http://localhost:3000` origin the browser and the Companion use; `/mcp`
  sits behind the same Host → Origin → CORS stack as REST; nothing new
  listens beyond loopback.
- Ports 8001 (API) and 3000 (UI) are fixed. The Companion, the Origin
  allowlist and sync (Funnel/SSH) all depend on them.
- One way to run each piece. Docker and desktop run the same Next standalone
  server and the same backend code; a superseded route gets a §13 row with a
  removal trigger.
- Data first. Every migration on a desktop install is preceded by a verified
  SQLite snapshot; the old install is never modified by an import.
- Explain, never surprise. Fallbacks, update checks and the one new outbound
  call (GitHub) are named in the UI and in PRIVACY.md.

**Non-goals:** Windows and Intel Mac builds; the macOS Keychain for API keys;
a tray/menu-bar icon; automated downgrade; Mac App Store distribution (its
sandbox conflicts with the TeX probe and spawned children); remote or
authenticated MCP; claude.ai/chatgpt.com web connectors; a settings screen
for `maestro.env`; the Chrome Web Store submission itself (owner task).

**Autonomy:** peer. Executors may adapt *how* when the repo disagrees with
this doc, must log every deviation with a one-line reason, and should flag
anything here that contradicts the Goal Card. Scope is never self-expanded.

## Owner decisions (2026-10-09)

| Question | Decision |
|---|---|
| Scope | Finish step 2's `mcp-http` first (revised), then the shell. |
| Shell | **Electron**, chosen for the most mature in-app updater (electron-updater + GitHub Releases), no frontend refactor (its Node runs the Next server), and a TypeScript + Python stack only. |
| Platforms | macOS Apple Silicon first. Intel and Windows later. |
| Signing | Designed now as a CI switch: Developer ID + notarization when the secrets exist, ad-hoc otherwise. The first PUBLIC desktop release waits for the Developer ID. |
| Existing installs | One-time import from a Docker checkout; the old install is left untouched. |
| Before building | Branch 0: an evidence-first trim of dead code, unused dependencies, ledger debt and test bloat. |

## What changed since the step 2 design (drift audit, HEAD aa2b155f)

- `mcp-http` was never built: no `/mcp`, no connect endpoint; the `.mcpb` and
  the plugins still `docker exec` into the container. Tool count is 88, not 83.
- Four plan bugs against the locked SDK (`mcp==1.29.1`) and Starlette 1.3.1:
  (a) FastMCP turns DNS-rebinding protection ON when host is `127.0.0.1`
  (the default), refusing `testserver` and extension origins; (b)
  `StreamableHTTPSessionManager.run()` runs once per instance, so a per-test
  lifespan raises; (c) a Starlette `Mount` keeps the full `scope["path"]`, so
  the planned profile router 404s every request; (d) a bare `POST /mcp` is
  307-redirected by `Mount("/mcp")`.
- About 114 direct `srv.<tool>(` calls across 9 test files would break if
  `_guard` became async.
- New context: the native always-on install (single-worker uvicorn on
  `$MAESTRO_PORT`, no `BACKEND_URL` export, a 200 MB memory budget,
  `HTTPS_PROXY` on bots), sealed sync (the desktop app is the "laptop" copy),
  a tabbed Settings page, root `PRIVACY.md`, `server.json` for the MCP
  Registry, and eight version locations in RELEASING (desktop makes nine).
- The frontend needs a Node server (`app/api/[...path]` proxy, `proxy.ts` host
  guard, nine runtime-id routes), so "the sidecar serves the UI" was wrong.
- Frozen-binary hazards: Typst spawns a process on every compile (and
  embeddings optionally), with no `freeze_support()`; container-absolute path
  defaults; a cwd-relative `.env`; alembic and the demo resume found relative
  to the repo; the MCP upload dir inside the package; the embedding model
  downloaded on first score; `import resource` blocks native Windows.
- Stored file paths are ABSOLUTE (`Application.artifact_dir`, `pdf_path`,
  `tex_path`, …), so an import from Docker must rewrite them.

## Sequence

Four branches, each merged and verified before the next starts.

| Branch | Delivers | Release |
|---|---|---|
| 0 `trim` | Less code, fewer dependencies, a faster suite; no behaviour change | folded into the next release |
| A `mcp-http` | `/mcp` in the backend, the Node bridge `.mcpb`, Connect your assistant, Web Store readiness | v0.9.0 |
| B `desktop-sidecar` | A frozen macOS backend and a Next standalone server, smoke-tested in CI | (internal) |
| C `desktop-shell` | The Electron app, import, updater, signed release pipeline | first desktop release |

## Branch 0: trim

**Goal:** remove what the installer would otherwise ship and support, with no
user-visible change. Sizes at the start: backend + MCP ~55k lines, backend
tests ~152k lines (7,747 test functions), frontend ~63k, extension ~22k.

1. **Audit (read-only)**, deterministic analyzers first, judgement second:
   dead code (vulture; knip for the frontend; modules nothing imports; REST
   endpoints no frontend, extension, MCP or chat caller reaches); unused
   dependencies (deptry, depcheck); ledger debt (§13 `ready-to-cut` and
   near-ready rows, obsolete §11 items, Postgres-era remnants); tests (slowest
   files, tests of removed behaviour, near-duplicate files, oversized
   parametrizations); hotspots flagged, not refactored.
2. **Owner picks** from a ranked cut list: evidence, risk, and the saving in
   lines, megabytes or seconds per item.
3. **Execute** the picks: suite green after every cut, SYSTEM.md and the slop
   baselines updated.

Hard rules: never cut a test that `.system_md_enforcement.json` pins; no
rewrites or new abstractions unless the owner picked one below; nothing else
the user would notice.

### Audit result (2026-10-09) and the owner's picks

Five read-only audits (backend, frontend, extension, tests, dependencies):
zero orphan modules on every surface, 0.3% duplication in app code and
0.29% in tests; 11,093 tests, 11,087 passing locally in ~244 s wall
(CI: 498 s test step, CPU-bound on 4 vCPUs; browser tests are 50% of CPU,
the extension node harness 15%). The codebase is not bloated in the
dead-code sense; branch 0 is small. IDs below are the audit's.

**Group A, all approved (no behaviour change):**
- Weight: D1 exclude `sharp`/`@img` from Next standalone tracing
  (`outputFileTracingExcludes`; `next/image` unused) 19 MB; F4/D7 vendor only
  the Monaco files the JSON and LaTeX editors use (`scripts/vendor-monaco.mjs`;
  browser check of both editors) ~16 MB; D2 exclude `hf_xet` and D3
  `websockets`/`watchfiles` from the sidecar (recorded for branch B's spec);
  F1/D6 remove `react-hook-form` + `@hookform/resolvers` (and
  THIRD_PARTY_NOTICES); D4 bump yanked `mako==1.4.0`; D5 declare `starlette`,
  add `playwright` to the lock, drop repeated extras entries.
- Backend dead code: B1 `POST /api/resume-lint/wording/reset` +
  `health_wording.reset`; B2 `GET /api/kb/extra-section-presets` (keep
  `PRESETS` and the frontend mirror's parity test); B3 the version `/diff`
  route (port its e2e assertion to `diff_versions`); B4 `POST
  /api/kb/consolidate` + `_parse_consolidation_slugs` (port the service-level
  tests); B6 `ats_score.best_base` (+ docs/entities ats-score.md, others.md);
  B8 dead constants (`llm_capabilities.CAPABILITIES`/`SURFACE_REQUIREMENTS`,
  `quick_tailor.DEFAULTS`, `explore_skills.SkillTier`,
  `markets.work_auth_vocab`, `config.app_root` + the APP_ROOT export); B9
  test-only helpers (`countries.is_valid`/`name_for`, `markets.eeo_module`,
  `selectable_base_resume_slugs`, `check_parse_fidelity`); B13 stale config
  comment.
- Frontend dead code: F2 `lib/health-zones.ts` (repoint
  `test_frontend_health_report.py`); F3 unused shadcn primitive exports
  (~480 LOC; fix `test_frontend_design_tokens.py:561`); F5 dead exports
  (`RoleBadge`, `proposalStatusLabel`, `createKbPoint` + `KBPointCreate` — the
  ROUTE stays, it is a pinned provenance writer — `diffFromDefaults`,
  `ResumeDataInput`, two unused default exports); F8 replace the stock
  create-next-app README.
- Extension dead code: E1 drop `shared/guided-run.js` from
  `content_scripts` (311 lines injected into every frame, read by no page
  code; update `test_extension_manifest.py:214`); E3 publish the listbox
  helpers once from autofill.js; E4 `sweepOrphanKeys`/`ORPHAN_KEYS`; E6
  `eeoAutofillEnabled`; E7 unused export keys; E8 dead local `fold`; E10 dead
  `storage.session` guards. Pack-time excludes for README, INTERNALS and the
  slop files are recorded for branch A's `pack_extension.py`.
- Tests: T1 the two 31 s lock waits (`prompts.get_prompt` opens its own
  session inside the caller's write; `test_kb_autos_survive_enrichment_failure`
  passes by accident) — fix from the app side or a fixture, never by editing
  the pinned `test_write_paths`; T2 revive two always-skipped tests (vendored
  XCharter path; a `tmp_path` preview dir); T3 drop the duplicate oracle-line
  browser test (move its Country assertion); T4 narrow three e2e scenario
  tests to their fixture; T7 split `browser/test_fill_core.py`; T9 drop the
  13 "X is still deleted" tests named in the audit (pinned files excluded).
  T6 (macOS job skips browser and node-harness tests) is recorded for branch B.
- Honesty: log the three real swallows (`seeding.py:84`, `artifacts.py:22`,
  `:27`); SYSTEM.md stops describing a "legacy import" startup step;
  §13 `autofill-education-shape` corrected (there is no server-side
  normalisation); `.slopledger.json` hand-check text refreshed.

**Owner calls, approved:**
- F6 delete `frontend/app/explore/page.tsx`; cut the §13 `explore-redirect`
  row and its ledger entry (the owner overrules the recorded keep-decision).
- B7 drop the pre-rename `CAREER_STUDIO_*` env aliases (config.py, MCP
  client, profiles, server, `.env.example`); CHANGELOG names it.
- E9 drop the Companion's `tabs` permission, after a manual check that the
  panel still unbinds on leaving a posting for a non-web page; if it does not,
  keep the permission and record why.
- D9 Langfuse becomes an optional extra, left out of the desktop build; Docker
  keeps it. CHANGELOG and docs say so.
- D8 the backend Docker image stops installing `[dev]` (a runtime lock
  without `--extra dev`, `COPY --chown` instead of a `chown -R` layer) and
  gains a `backend/.dockerignore` (B12).
- O1 replace python-docx/lxml in `attachment_extract._docx_text` with stdlib
  `zipfile` + `xml.etree`; a golden test over real `.docx` fixtures shows the
  extracted text unchanged (or the differences named and accepted).
- E2 + E5 the inline sanitizer twin uses `ns.decisions.sanitizeAnswer` (its
  allowlist entry and twin pin go); the SW's content-sender routing
  (`sendToFrame`, `fanoutTab`'s content branch, `frameKey`/`parseFrameKey`)
  and its two test sections go.
- T8 one shared `client` fixture in `tests/conftest.py`, replacing 33 local
  copies and the inline `dependency_overrides[get_db]` setups where
  mechanical; pinned files are left as they are.

**Kept, deliberately** (do not re-flag): B5 `SkillMatcher.semantic_match`
(score-sensitive; needs its own change), B10 legacy evidence migration (an old
Queued proposal may hold the layout), B11 `backfill_work_auth.py`, F7 the
`shadcn` dev dependency, T5, `MaestroLogo`, the `update.sh` pgdata guard,
D10 `uvicorn[standard]`, hotspot decomposition, parametrization collapse.
F9 (the Next proxy also reads `BACKEND_URL`, the MCP client's variable with a
different default) is a branch C note: the shell gives each child process its
own environment.

## Branch A: `mcp-http` (step 2b, revised)

**Goal:** any assistant connects to a running Maestro by URL with no Docker,
Python or config editing on its side, and the trust boundary is unchanged.

### A1. Serving

- Two Starlette `Route`s in `main.py`: `/mcp` (profile `full`) and
  `/mcp/{profile}`. A `Route` whose endpoint is an ASGI app gets no 307 and
  sees the true path (`get_route_path`). Unknown profile → 404.
- One FastMCP instance holds the 88 tools. Each profile is a small low-level
  MCP server with its own session manager whose `list_tools`/`call_tool`
  filter through `profiles.allowed_tools(<profile>)`; a hidden tool called by
  name is refused. The profile is bound to the server instance, not carried
  in a ContextVar across SDK task creation. Workflow hints read a value set
  inside that server's `call_tool`; stdio keeps working through the same
  function with the profile taken from `MAESTRO_CS_MCP_PROFILE`.
- Stateful sessions with JSON responses (`clientInfo` keeps labelling KB
  timeline rows). Session managers are created fresh on each lifespan run,
  after `repair_proxy_env`, `run_startup`, `ensure_machine_id` and
  `load_cards`, so tests can use `TestClient` normally.
- Off the event loop without touching the ~114 test calls: after
  registration, one function replaces each registered tool's `fn` with an
  async wrapper running the original on a worker thread through Maestro's own
  `CapacityLimiter` (tools waiting on loopback REST must not exhaust
  FastAPI's 40 default threads). Module functions stay sync. This leans on
  `Tool.fn` / `is_async`; a test pins it with a real call through `/mcp` that
  asserts a worker thread ran it.
- Loopback: tools call REST via `BACKEND_URL` (the container's `:8000` under
  Docker); `native/common.sh` exports `BACKEND_URL=http://127.0.0.1:$MAESTRO_PORT`;
  the loopback client ignores proxy env vars. The job-site login header
  (`X-Maestro-CS-Origin: mcp`, no `Origin`) is unchanged.
- `mcp` becomes a core dependency; `[mcp]` stays as an empty alias because
  `native/setup.sh` and `setup-mcp.sh` install it.

### A2. Security

`/mcp` sits inside SyncPublicHost → Host → Origin → CORS. The SDK's
`transport_security` is set with DNS-rebinding protection OFF explicitly (it
defaults on) and pinned by a test, keeping the app's stack the one
definition. Pinned: forged Host → 400; foreign Origin → 403; a request under
`SYNC_PUBLIC_HOST` for `/mcp` refused; `/mcp` never under `/api/sync`. Docs
replace "keep MCP on stdio" with the real rule: never publish port 8001 or
proxy `/mcp` beyond loopback. New §6 invariants `{#inv-mcp-inside-the-app}`
and `{#inv-tools-off-loop}`.

### A3. The Claude Desktop bridge (`mcpb/server/index.js`)

Claude Desktop refuses `localhost` connector URLs, so it keeps a stdio
bundle, now a zero-dependency Node ≥ 18 bridge: stdin JSON-RPC → POST
`${backend_url}/mcp[/profile]` → one JSON line on stdout; 202 writes nothing;
404 on a known session replays `initialize` once (a backend restart mid-chat
recovers); connection refused → one stderr sentence ("Maestro Career Studio
is not running — open the app") plus a JSON-RPC error; stdout carries only
JSON-RPC. Manifest `user_config`: `backend_url` (default
`http://localhost:8001`) and `profile`; platforms darwin, win32, linux; the
Docker wording goes. Tests against an in-test `node:http` fake. Release:
re-pack, update `server.json` `fileSha256`, add a registry-republish step to
RELEASING; `check_mcpb_bundle.py` keeps tracking its three files.

### A4. Connect your assistant

- `GET /api/setup/connect?profile=` composes, server-side so pytest pins the
  formats: the Claude Code line (`claude mcp add --transport http
  maestro-career-studio <url>`), the Codex line (`codex mcp add
  maestro-career-studio --url <url>`), the Cursor deep link (base64), the VS
  Code deep link (URL-encoded, `type: "http"`), the raw URL, and whether a
  bundle is available. Base URL from new `MAESTRO_CS_PUBLIC_BACKEND_URL`
  (compose: `http://localhost:${BACKEND_HOST_PORT:-8001}`).
- `GET /api/setup/connect/bundle` serves the `.mcpb` from a fixed configured
  path (compose mounts `./mcpb` read-only; the desktop app ships it in its
  resources); absent → 404 with a hint.
- UI: Settings › Agents, beside `ConnectedAgentsCard`: a profile select and
  rows built with `components/copy-button.tsx`; the anchor registered in
  `lib/settings-tabs.ts`; the getting-started checklist gains "Connect an
  assistant".

### A5. Declarations and deprecation

- Step 0 of the plan is a check: do Codex and ChatGPT-desktop plugins accept
  `{"type":"http","url":…}` in `.mcp.json`? Yes → the shared file switches.
  No → Claude Code gets the HTTP entry and the Codex plugin points at its own
  file keeping `docker exec` for one release.
- Config examples: Claude Desktop shows the bridge; Codex shows `url`. The
  marketplace description stops saying "stdio".
- `setup-mcp.sh` prints a deprecation banner and the HTTP one-liners. New §13
  row `mcp-stdio-venv`: remove the script, the venv paragraphs and the
  `docker exec` examples in the release after v0.9.0.

### A6. Chrome Web Store readiness

`extension/options.html` + `options.js` (`options_ui`) edit `backendUrl` and
`appUrl` with a Test connection button; `scripts/pack_extension.py` (stdlib,
version check, `--first-upload` strips `key`); `extension/STORE_LISTING.md`
(single purpose, per-permission justification); root `PRIVACY.md` MCP
section rewritten (it says MCP runs inside Docker); RELEASING "Chrome Web
Store" section. The `extension-id-swap` §13 row is added only once a store id
exists.

### A7. Tests, gates, docs

Suite: a loopback end-to-end test on a single-worker uvicorn marked
`integration` (NOT `slow`; CI skips slow); profile paths list exactly their
allowlists; JSON responses pinned; connect strings; the bridge's `node:test`
suite. CI `mcp-cold-install` gains an HTTP smoke on `/mcp` and `/mcp/hunt`.
Memory measured against the 200 MB budget (the native docs' ~76 MB stdio
process goes away).

Gates: suite + ruff + SYSTEM.md + bundle checks; end to end initialize → 88
tools → `list_jobs` → `render_pdf` → `get_rendered_pdf`; Claude Desktop on a
SCRATCH stack surviving a backend restart; Claude Code, Codex and Cursor each
list 88 tools and `/mcp/hunt` exactly the hunt allowlist; forged Host and
foreign Origin refused by curl; a `--first-upload` pack works once its id is
allowed; the native always-on path reaches `/mcp`.

Docs: GETTING_STARTED §5 becomes three routes (Claude Desktop bundle, URL
clients, legacy stdio for one release); README; SECURITY.md; native-install;
bot-setup; PRIVACY.md; CHANGELOG. SYSTEM.md §2, §3, §6, §7, §9, §12, §13
rewritten in place, staying under the cap by moving §7's transport detail
into `docs/entities/others.md`. Release v0.9.0, eight version places.

## Branch B: `desktop-sidecar`

**Goal:** one command builds a self-contained Mac backend and a Next server
any Node can run; both start against an empty folder, migrate, render and
score with no network, no Docker, no system Python and no checkout.

1. **`MAESTRO_HOME`.** When set, every path default derives from it, in the
   layout `native/common.sh` already creates (data, applications, settings,
   base_resumes, kb_documents, exports, logs, fastembed_cache, the MCP upload
   dir). Explicit per-path variables still win, so Docker's `/app/...` and
   native installs are unchanged. The frozen entry reads exactly one env file,
   `$MAESTRO_HOME/maestro.env`, never a cwd `.env`. `mcp_server/client.py`'s
   upload dir moves under `MAESTRO_HOME` (the `.app` bundle is read-only).
2. **Repo-relative files.** `run_migrations` sets an absolute
   `script_location`; the spec ships `alembic.ini` and `migrations/` beside
   the frozen modules so `parents[2]` resolves unchanged. The demo resume
   ships as data; a `demo_resume_path` setting lets first-run seeding copy it
   into an empty `base_resumes/`. Every `Path(__file__)` data site goes in the
   spec, and a test cross-checks the spec's list against an AST scan.
3. **Spawned children.** The entry calls `multiprocessing.freeze_support()`
   first; PyInstaller **onedir** (onefile would unpack ~200 MB per Typst
   compile). The smoke test times a frozen Typst compile. pdflatex stays the
   host's TeX via the engines probe, never bundled.
4. **Entry point `maestro-backend`.** `serve` (uvicorn, one worker,
   `ws="none"`, ALWAYS `127.0.0.1`, no host option, port default 8001,
   `--parent-pid` exits when the shell dies); `migrate`; `backup --out PATH`
   (online snapshot via `app.tools.backup_db`, verified); `check` (JSON: app
   version, code's alembic head, DB revision, engines); `import-install`
   (branch C). No stdio MCP.
5. **Embedding model in the bundle.** `BAAI/bge-small-en-v1.5` (fastembed
   ONNX, size measured) ships; first run copies it to
   `$MAESTRO_HOME/fastembed_cache` (dropped if pointing at the read-only
   bundle proves safe) and `HF_HUB_OFFLINE=1` is set. Side note for §11:
   Docker today re-downloads the model to the container's temp dir after
   every recreate.
6. **Reproducible build.** Hash-pinned `backend/requirements-macos.lock`
   generated on the macOS runner (a lock comes from its own platform);
   PyInstaller pinned; Python 3.12; `backend/desktop/maestro-backend.spec` and
   `backend/scripts/build_sidecar.sh`, shared by CI and a maintainer's Mac.
   Apple Silicon only.
7. **Next standalone.** `output: 'standalone'`; the build copies `public/`
   and `.next/static` beside `server.js`. The Docker image switches to `node
   server.js` too, so there is one way to run the frontend. Runtime config
   stays env (`PORT`, `HOSTNAME`, `API_PROXY_BACKEND`,
   `FRONTEND_ALLOWED_HOSTS`); `NEXT_PUBLIC_APP_VERSION` is baked at build.
8. **Windows groundwork only.** `import resource` in `services/memory.py`
   guarded (memory readout "unavailable" there). §11 item 39 stays open.
9. **Tests and CI.** Unit: `MAESTRO_HOME` derivation and override precedence;
   `maestro.env` the only env file; `script_location` cwd-independent; demo
   seeding only into an empty dir; spec data list vs AST scan; `serve` has no
   host option. New macOS arm64 CI job `desktop-sidecar` (PRs touching
   backend/frontend, and tags): build, start against a temp `MAESTRO_HOME`
   with network-off variables, smoke `/health`, `/api/version`, a Typst
   render of the demo resume, an ATS score on the bundled model,
   `initialize` + `tools/list` on `/mcp`, `backup`, `check`; then
   `node .next/standalone/server.js` serving `/` and `/api/version` through
   the proxy. It reports bundle size and resident memory.

## Branch C: `desktop-shell`

**Goal:** the DMG experience in the Goal Card, on Apple Silicon.

1. **Layout.** New top-level `desktop/`: TypeScript, Electron main process,
   `electron-builder`; Electron and its Node pinned (Next 16 needs Node ≥
   20.9). No preload, no IPC: native actions live in the app menu. Resources:
   `maestro-backend` (onedir), the Next standalone server, the `.mcpb`, the
   embedding model.
2. **Startup.** Single-instance lock (a second launch focuses the window).
   Create `~/Library/Application Support/Maestro Career Studio/` (0700) as
   `MAESTRO_HOME`. Port check: if 8001 answers as Maestro (`/openapi.json`
   title "Maestro CS API"), say Maestro is already running (likely Docker)
   and offer Quit, never a second copy; another program on 8001 or 3000 →
   name the port and process. Backend: `check`; if the DB exists and is
   behind the code's head, `backup` to `backups/pre-v<ver>-<ts>.sqlite3`
   (newest 5 kept); `serve --parent-pid`; wait for `/health` (60 s on first
   run). Frontend: Next `server.js` via **`utilityProcess.fork`** (Electron's
   Node) with `PORT=3000`, `HOSTNAME=127.0.0.1`,
   `API_PROXY_BACKEND=http://127.0.0.1:8001`, so the RunAsNode fuse stays off
   (plan step 0 proves Next runs under `utilityProcess`; fallback: RunAsNode
   on, documented). Window loads `http://localhost:3000`.
3. **Running and quitting.** Child output to size-capped rotated
   `logs/backend.log` and `logs/frontend.log`. A crashed child restarts with
   backoff, up to 3 times in 60 s, then an error page with Restart, Open logs,
   Open data folder. Closing the window keeps Maestro running in the Dock (MCP,
   the Companion and sync keep working); Cmd+Q stops both children (SIGTERM,
   SIGKILL after 10 s). Menus: Open at login (default off), Check for
   updates, Check for updates automatically; Edit roles; reload/zoom; Help:
   Open data folder, Open logs, Connect Claude Desktop, Get the Companion,
   Documentation.
4. **Window security.** `contextIsolation`, `sandbox`, no `nodeIntegration`,
   no preload. Navigation or `window.open` leaving `http://localhost:3000`
   opens in the default browser (http/https only). Permission requests
   denied. PDF downloads use the Save dialog; a `.mcpb` download is opened by
   the system handler, so Download for Claude Desktop lands in Claude's
   install prompt. Fuses: RunAsNode off, `NODE_OPTIONS` off, inspect args
   off, ASAR integrity on, only load from ASAR. New §6 invariant
   `{#inv-desktop-adds-no-boundary}`: the shell adds no origin, no IPC and no
   non-loopback listener, pinned by a test on window options, fuses and spawn
   arguments.
5. **Import from an existing install.** Offered on first run and under File.
   The user stops the Docker stack first (the shell checks 8001 is free;
   reading a live WAL database across the Docker Desktop mount is unsafe,
   §9), picks the checkout folder, and `maestro-backend import-install --from
   <dir>` checks magic bytes and integrity, copies the database with the
   SQLite backup API, copies `base_resumes/`, `applications/`, `settings/`
   (secrets keep 0600/0700), `kb_documents/`, `exports/`, rewrites every
   stored absolute path from the old root to `MAESTRO_HOME` (reusing
   `application_artifacts._rewrite_path`), and migrates. A coverage test
   enumerates every path-holding model column and fails on an unhandled one.
   The old install is never modified; a receipt
   `data/.imported-from.json` is written and a second import asks first.
6. **Updates.** `electron-updater`, GitHub Releases provider: check at launch
   and every 6 h while "Check for updates automatically" is on; download in
   the background; "Restart to update to vX" in the menu plus one
   notification; install on quit, never a forced restart; signature and
   `latest-mac.yml` sha512 verified. The next launch takes the pre-migration
   backup. If the new backend fails health after migrating, the error page
   names the backup and links the previous release; restore steps in
   `docs/desktop.md`; no automated downgrade. A build-time `signed` flag:
   ad-hoc builds replace the updater with one GitHub API check and a
   Download vX link. GitHub becomes the only outbound call besides the user's
   AI provider: disclosed in PRIVACY.md, switchable from the menu.
7. **Release pipeline.** `desktop-release.yml` on `v*` tags (macOS arm64):
   build sidecar + Next standalone, electron-builder → DMG, zip,
   `latest-mac.yml`, blockmaps, uploaded to the existing GitHub Release. With
   the Apple secrets: Developer ID, hardened runtime, notarization, explicit
   signing of every nested `.so`/`.dylib` in the PyInstaller folder,
   entitlements limited to what Electron's JIT and onnxruntime need (each
   with a recorded reason). Without them: ad-hoc, labelled unsigned in the
   release notes. `desktop/package.json` becomes the ninth version location,
   covered by the version-consistency check. RELEASING "Desktop": secrets,
   the switch, and the rule that the first public desktop release waits for
   the Developer ID.
8. **Sync, Companion, MCP.** The desktop app is the sync "laptop": fixed 8001
   keeps Funnel/SSH working; app closed = laptop asleep (backoff already
   handles it); `SYNC_PUBLIC_HOST` and friends live in `maestro.env`
   (`docs/desktop.md` shows how). Companion defaults (`localhost:8001`,
   `:3000`) already match; Help › Get the Companion links the store once
   listed, the unpacked route until then. `/mcp` is served by the sidecar and
   the bundle ships in resources.
9. **Tests and gates.** `node:test` in `desktop/`: port classification,
   restart backoff, backup naming and retention, the signed/unsigned update
   decision, navigation allowlist, window options and fuses (the invariant
   pin). Python: `import-install` path rewriting plus the column-coverage
   test. End to end (Playwright's Electron driver, macOS CI): launch the
   packaged app with a temp `MAESTRO_HOME`, reach the web app's shell, score
   the demo job, quit, assert no child survives. Update rehearsal: vN from a
   local feed → vN+1 carrying a migration; the backup exists and data is
   intact. Manual gates before the first public release: the signed DMG opens
   with no Gatekeeper warning on a second Mac; importing a COPY of the
   owner's Docker install works end to end; the Companion works against the
   app; Connect Claude Desktop installs and runs a tool; a sync round with the
   always-on bot succeeds with the app as the laptop.
10. **Docs.** New `docs/desktop.md` (data folder, logs, `maestro.env`,
    import, updates, restoring a backup, uninstall: drag to Trash, data stays
    in Application Support). README ("Download for Mac" first), GETTING_STARTED,
    UPDATING, RELEASING, SECURITY (Electron settings), PRIVACY (update
    check). SYSTEM.md §1, §2 (`desktop/`), §3, §6 (new invariant), §9
    (desktop build and tests), §11 (Windows and Intel builds, a `maestro.env`
    settings screen, Keychain for keys).

## Execution

Each branch gets its own implementation plan in `docs/plans/`, executed
subagent-driven (one fresh subagent per task, spec review then code review,
an append-only deviation log, a gate table), in its own worktree. Model
routing follows the owner's standing rule: Sonnet implements, Opus reviews,
the main session architects.

## Owner tasks

Apple Developer Program enrollment (US$99/yr) before the first public desktop
release; a Chrome Web Store developer account and the listing (privacy URL,
five 1280×800 screenshots, the store id appended after the first upload);
the version bump in nine places per release.

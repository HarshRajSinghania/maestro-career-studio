# Desktop step 3, branch 0 (trim) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (or
> superpowers:executing-plans in a separate session) to implement this plan task-by-task.

**Goal:** Remove what the desktop installer would otherwise ship and support — dead code,
unused dependencies, stale ledger entries, slow or broken tests — with no user-visible change
except the owner-approved cuts listed in the design.

**Architecture:** Pure subtraction plus a handful of small, test-first fixes. Each task is
independent, ends with the suite (or its affected slice) green and a commit. No new
abstractions except the one the owner picked (T8, a shared `client` fixture).

**Tech Stack:** FastAPI + SQLAlchemy (SQLite) backend, pytest + xdist; Next.js 16 frontend;
MV3 Chrome extension (plain JS) tested from `backend/tests` through a node harness.

**Design:** `docs/plans/2026-10-09-desktop-step3-design.md`, section "Branch 0: trim" →
"Audit result (2026-10-09) and the owner's picks". Item IDs (B1, F4, E9, T8 …) are the
audit's and are used throughout.

---

## Goal Card

**Goal:** A Mac user will download Maestro as an app. Before building that app, cut what it
would otherwise carry and what we would otherwise have to keep supporting, so the installer
is smaller, CI is faster and honest, and nothing a user relies on changes.

**Principles:**
- Subtraction first. If a cut needs new code, the new code must be smaller than what it
  replaces (O1 and T8 are the owner-approved exceptions).
- Never cut or edit a test or symbol pinned in `.system_md_enforcement.json`. Fix around it.
- A cut is done only when every reader is gone: code, tests, docs, SYSTEM.md, CHANGELOG,
  `.slopledger.json`, `THIRD_PARTY_NOTICES.md`.
- Evidence over assumption: before deleting, `git grep` the symbol across the WHOLE repo
  (backend tests read frontend and extension sources as text).

**Non-goals:** refactoring hotspots; behaviour changes beyond the owner's picks; the items the
design lists under "Kept, deliberately"; anything in branches A–C.

**Autonomy:** peer. Adapt *how* when the repo disagrees with this plan; log every deviation in
the Deviation log below with a one-line reason; flag anything that contradicts the Goal Card.
Never expand scope.

## Ground rules for every task

- Work in `/Users/ajeyds/Projects/maestro-career-studio/.claude/worktrees/desktop-app-phase-three-a2c0b5`.
  Never `cd` into the main checkout; verify with `git -C <worktree> status`.
- Read `SYSTEM.md` first (repo rule). Integrate doc changes in place (present tense, no dated
  paragraphs outside §11–§13).
- Python: `/opt/anaconda3/bin/python3`, run from `backend/` (cwd wins on `sys.path`).
- Full suite (always xdist): `cd backend && MAESTRO_SKIP_SLOW=1 /opt/anaconda3/bin/python3 -m pytest tests/ mcp_server/tests/ -q -n auto --dist loadfile -p no:cacheprovider`
  Baseline 2026-10-09: 11,087 passed, 6 skipped, ~244 s.
- Lint: `cd backend && /opt/anaconda3/bin/python3 -m ruff check .`
- Frontend gates (node_modules exists in this worktree; else `npm ci` first):
  `cd frontend && npx tsc --noEmit && npm run lint && npm run build`
- Docs gate: `python3 scripts/check_system_md.py` (repo root).
- Slop ratchet, per surface touched: `python3 ~/.claude/skills/ai-slop-detector/scripts/slop_scan.py check <backend|frontend|extension>`
  from the repo root. If it fails only because a COUNT metric moved down or hotspots moved,
  re-baseline with `... baseline <surface>` and say why in the commit.
- **CI-shaped venv for any task that removes a dependency (6, 7, 9).** The shared anaconda env
  still has every package, so it cannot catch a missing one; CI installs `pip install -e ".[dev,mcp]"`
  (`.github/workflows/ci.yml:48`), not the lock. Build a throwaway venv exactly like CI under the
  scratchpad and run the suite there (KB "Agent lessons" e354b9bb / a6240360):
  ```bash
  SP=/private/tmp/claude-501/-Users-ajeyds-Projects-maestro-career-studio--claude-worktrees-desktop-app-phase-three-a2c0b5/56b3260b-72d9-445f-b6d1-91985eb85172/scratchpad
  ~/.langflow/uv/uv venv -p 3.12 $SP/ci-venv && VIRTUAL_ENV=$SP/ci-venv ~/.langflow/uv/uv pip install -e "backend[dev,mcp]"
  cd backend && MAESTRO_SKIP_SLOW=1 $SP/ci-venv/bin/python -m pytest tests/ mcp_server/tests/ -q -n auto --dist loadfile -p no:cacheprovider
  ```
  (`uv` lives at `~/.langflow/uv/uv` on this machine; `-p 3.12` matches CI's Python.)
- Commit after every task. End each message with:
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (or the executing model's line).

## Moved out of branch 0 (recorded deviations at planning time)

| Item | Where it goes | Why |
|---|---|---|
| D1 exclude `sharp` from Next tracing | branch B | `outputFileTracingExcludes` only matters once `output: 'standalone'` exists; it cannot be verified before that. |
| D2 `hf_xet`, D3 `websockets`/`watchfiles` | branch B (PyInstaller excludes) | They are sidecar-spec decisions; the pip install stays unchanged. |
| D5 "add playwright to the lock" | dropped | Task 9 removes `--extra dev` from the lock, so dev tools leave the lock entirely. |
| T6 macOS job skips browser/node tests | branch B | There is no macOS job yet. |

---

### Task 0: Baseline and branch hygiene

**Files:** none modified.

**Step 1:** `git -C <worktree> status` → clean; `git log --oneline -3` shows the two design commits.

**Step 2:** Run the full suite; record pass/skip counts and wall time in the Gate table below.

**Step 3:** Run all three slop checks and `scripts/check_system_md.py`; record results. Also record
the slop checks for `main` (e.g. `git worktree add $SP/main-ro origin/main` read-only, or
`git stash`-free: run the scan from that throwaway worktree, then `git worktree remove` it), so a
later failure can be told apart from one `main` already has: a ratchet the default branch already
fails cannot be passed by splitting functions (KB "Agent lessons" 98a592ed).

No commit.

---

### Task 1 (T1): `get_prompt` must not take the write lock behind a caller's transaction

The two slowest tests (31 s each) wait out SQLite's `busy_timeout`. `prompt_assembly._render_template`
and `_skill_preamble` call `prompts.get_prompt(key)` with NO session, so `get_prompt` opens its
own `SessionLocal()` and, when the row is missing, `hooks.seed_setting` INSERTs + COMMITs — which
blocks on the caller's open write transaction. In production `seeding.seed_prompts` seeds every
prompt at startup, so the owned-session write path is only reached in tests; in tests it makes
`test_kb_autos_survive_enrichment_failure` fail on "database is locked" instead of reaching its
patched "provider down" path (it passes by accident).

Fix: when `get_prompt` owns its session and the row is missing, return the file default WITHOUT
writing. The session-passing path (callers that pass `session`) keeps lazy-seeding, so
`test_get_prompt_seeds_from_file` and `kb_import`/`kb_consolidation`'s prefetch contract are unchanged.

**Files:**
- Modify: `backend/app/services/prompts.py:61-74`
- Test: `backend/tests/test_prompts_service.py`

**Step 1: Write the failing test** (append to `tests/test_prompts_service.py`):

```python
def test_get_prompt_without_a_session_never_writes(db_session, tmp_path, monkeypatch):
    """An owned-session read must not INSERT: a caller holding the SQLite write lock
    (an open transaction) would make that INSERT wait out busy_timeout, then fail."""
    monkeypatch.setattr(prompts, "PROMPT_DIR", tmp_path)
    (tmp_path / "qa.txt").write_text("file default", encoding="utf-8")

    assert prompts.get_prompt("qa") == "file default"

    db_session.expire_all()
    assert db_session.get(Setting, "prompt.qa") is None
```

**Step 2:** `cd backend && /opt/anaconda3/bin/python3 -m pytest tests/test_prompts_service.py -q`
Expected: the new test FAILS (the row exists).

**Step 3: Implement** — replace the body of `get_prompt` with:

```python
def get_prompt(key: str, session: Session | None = None) -> str:
    """The stored prompt, else its file default.

    Only a caller-supplied session seeds the missing row. An owned session never
    writes: startup (`seeding.seed_prompts`) seeds every prompt, and a lazy INSERT
    here would queue behind any open write transaction on the caller's side
    (SQLite has one write lock) and fail after `busy_timeout`."""
    owns_session = session is None
    session = session or SessionLocal()
    try:
        existing = _get_setting(session, key)
        if existing is not None:
            return existing.value or ""
        value = _file_default(key)
        if not owns_session:
            hooks.seed_setting(session, Setting(key=_setting_key(key), value=value))
        return value
    finally:
        if owns_session:
            session.close()
```

Also update the docstring comment in `backend/app/services/prompt_assembly.py:22`
("get_prompt lazy-seeds its own key") to say an unseeded key reads its file default.

**Step 4:** Run `tests/test_prompts_service.py`, `tests/ats/test_gap_enrichment.py` and
`tests/sync/test_guard.py` with `--durations=5`.
Expected: all pass; `test_kb_autos_survive_enrichment_failure` and
`test_write_paths[sync-off-case2]` each well under 2 s. Do NOT edit `tests/sync/test_guard.py`
(pinned).

**Step 5:** Run the full suite. Commit: `fix(prompts): an owned-session read never seeds — no lock wait behind a caller's write`.

---

### Task 2 (T2): Revive the two tests CI can never run

**Files:**
- Modify: `backend/tests/test_typst_classic_seed.py:14-19` and the two tests at `:256` and `:282`

**Step 1:** Replace the hard-coded TeX Live path with the vendored font dir:

```python
from app.config import VENDORED_FONTS_DIR

# The vendored XCharter (app/assets/fonts/xcharter) the typst templates resolve.
_XCHARTER_DIR = VENDORED_FONTS_DIR
```

(Confirm `VENDORED_FONTS_DIR` is the name exported by `app/config.py`; `settings.typst_font_paths`
defaults to `[VENDORED_FONTS_DIR]` at `config.py:224`.) Update the comment above it.

**Step 2:** Give `test_default_seed_earns_ready_by_rendering` and
`test_a_ready_row_with_no_preview_is_revalidated` a writable preview root: add `tmp_path` and
`monkeypatch` params and `monkeypatch.setattr(settings, "base_resumes_dir", tmp_path)` (import
`settings` from `app.config` the way the file's other tests reach it via `tv.settings`), BEFORE
`ensure_seed_templates`. `_preview_path` lives under `base_resumes_dir/template_previews`.

**Step 3:** `/opt/anaconda3/bin/python3 -m pytest tests/test_typst_classic_seed.py -q -rs`
Expected: no skips on a host with pdflatex (MacTeX present locally); on a TeX-less host the
revalidate test may still skip with the explicit reason — that is correct.

**Step 4:** Commit: `test(templates): the typst seed tests read the vendored font and a writable preview root`.

---

### Task 3 (B1–B4): Delete four endpoints only tests call

Before each deletion: `git grep -n "<path or function>"` across the repo (including `frontend/`,
`extension/`, `backend/mcp_server/`, `backend/app/services/chat_tools.py`, `backend/app/automations/`,
`docs/`). The audit found only tests; re-confirm.

**Files:**
- Modify: `backend/app/routers/resume_lint.py:271-274` (delete `reset_wording`)
- Modify: `backend/app/services/health_wording.py:150` (delete `reset`; fix the module docstring at :4 that says "plus reset")
- Modify: `backend/tests/test_health_wording.py` (delete the test around :599 that posts `/wording/reset`; keep any service test that covers defaults through `save`)
- Modify: `docs/entities/others.md:468` (stop listing `/wording/reset`)
- Modify: `backend/app/routers/career_kb.py:908-913` (delete `list_extra_section_presets`; keep `services/extra_section_presets.PRESETS`)
- Modify: `backend/tests/test_kb_extra_sections.py:51-~67` (delete the route test; KEEP the frontend-mirror parity test at :276)
- Modify: `backend/app/routers/resume_versions.py:61-80` (delete `diff_version`)
- Modify: `backend/tests/test_extra_sections_e2e.py:~160-175` (replace the HTTP `/diff` call with `resume_versions.diff_versions(parent_snapshot, row.snapshot)` on the same rows, asserting the same thing)
- Modify: `backend/app/routers/career_kb.py:855-~905` (delete `consolidate_endpoint`) and `:677-~700` (`_parse_consolidation_slugs`) plus any helper only they used (`_source_error`? — grep each)
- Modify: `backend/tests/test_kb_consolidation.py:348-537` (delete the HTTP tests of the removed route; PORT any assertion about `kb_consolidation.consolidate` or `parse_resume_text` behaviour to a direct service call so coverage of the service stays)
- Remove now-unused imports and schemas (`ExtraSectionPreset` if unused, `Form`/`File` imports if unused).

**Step 1:** Delete B1 + its test; run `tests/test_health_wording.py`. Commit.
**Step 2:** Delete B2 + its route test; run `tests/test_kb_extra_sections.py`. Commit.
**Step 3:** Port the e2e assertion, delete B3; run `tests/test_extra_sections_e2e.py tests/test_resume_versions*.py`. Commit.
**Step 4:** Port service-level assertions, delete B4; run `tests/test_kb_consolidation.py tests/test_kb_import*.py`. Commit.
**Step 5:** `ruff check .`; full suite.

Commit messages: `refactor(api): drop <route> — no caller outside tests`.

---

### Task 4 (B6, B8, B9, B13, B7): Dead helpers, constants and the pre-rename env aliases

For every symbol: `git grep -n "\b<symbol>\b"` repo-wide first; if anything other than its
definition and the tests named here reads it, STOP and log a deviation.

**Files and cuts:**
- B6 `backend/app/services/ats_score.py:195` `best_base` + its tests in `tests/ats/test_ats_score_service.py`; update `docs/entities/ats-score.md:13` and `docs/entities/others.md:86`.
- B8 `llm_capabilities.CAPABILITIES` / `SURFACE_REQUIREMENTS` (:34, :37); `quick_tailor.DEFAULTS` (:39); `explore_skills.SkillTier` (:5); `markets.work_auth_vocab` (:100); `config.app_root` (:200) and the `APP_ROOT` export in `backend/scripts/native/common.sh:45` and `backend/scripts/memory_profile.py:59`, plus the asserts in `tests/test_memory_profile.py:60` and `tests/test_native_scripts.py:47,334`.
- B9 `countries.is_valid` / `name_for` (:66, :71); `markets.eeo_module` (:96); `base_resume_data.selectable_base_resume_slugs` (:52); `template_validation.check_parse_fidelity` (:204) — delete each with the tests that exercise ONLY it; if a test also covers a live function, keep the live part.
- B13 `backend/app/config.py:121`: the comment "the import marker lives under it" (the Postgres importer is gone).
- B7 pre-rename aliases:
  - `backend/app/config.py:278-300`: delete `_ENV_ALIASES`, `apply_legacy_env_aliases` and its call (keep `SCRUBBED_ENV = scrub_empty_env()` and its comment).
  - `backend/mcp_server/client.py:237` (`CAREER_STUDIO_UPLOAD_DIR`), `:917-919` (`CAREER_STUDIO_PDF_DIR` and the older `RESUME_TAILOR_PDF_DIR` with its "Back-compat" comment).
  - `backend/mcp_server/profiles.py:167`, `backend/mcp_server/server.py:234`.
  - `.env.example:105-106` (the alias note).
  - The `monkeypatch.delenv("CAREER_STUDIO_…")` lines in `mcp_server/tests/test_client_frugality.py:369,469`, `test_client_proposals.py:67`, `test_proposal_tools.py:31,69`.
  - CHANGELOG (Unreleased): "Removed: the pre-rename `CAREER_STUDIO_*` and `RESUME_TAILOR_PDF_DIR` environment variables; use the `MAESTRO_CS_*` names."

**Steps:** one commit for B6+B8+B9+B13 (`refactor: drop dead helpers and constants`), one for B7
(`refactor(config)!: drop the pre-rename env aliases`). After each: `ruff check .`, the touched
test files, then the full suite. `git grep -n "CAREER_STUDIO_\|RESUME_TAILOR_"` must return only
CHANGELOG lines.

---

### Task 5: Log the three real swallowed exceptions

**Files:**
- Modify: `backend/app/services/seeding.py:80-84`
- Modify: `backend/app/services/artifacts.py:19-27`
- Test: `backend/tests/test_seeding*.py` (find the seeding test file with `git grep -l "seed_base_resumes" backend/tests`) and the artifacts test file (`git grep -l "remove_files" backend/tests`)

**Step 1: Failing tests.**

```python
def test_a_seed_render_failure_is_logged(db_session, tmp_path, monkeypatch, caplog):
    # arrange one base resume file under tmp_path as the existing seeding tests do
    monkeypatch.setattr(seeding.base_resume_render, "render_base_resume",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with caplog.at_level(logging.WARNING, logger="app.services.seeding"):
        seeding.seed_base_resumes(db_session)
    assert "boom" in caplog.text or "render" in caplog.text.lower()
```

```python
def test_remove_files_logs_an_unlink_failure_but_not_a_missing_file(tmp_path, monkeypatch, caplog):
    missing = tmp_path / "gone.tex"
    locked = tmp_path / "locked.tex"
    locked.write_text("x")
    real_unlink = Path.unlink
    def fake_unlink(self, *a, **k):
        if self == locked:
            raise PermissionError("denied")
        return real_unlink(self, *a, **k)
    monkeypatch.setattr(Path, "unlink", fake_unlink)
    with caplog.at_level(logging.WARNING):
        artifacts.remove_files([missing, locked])
    assert "locked.tex" in caplog.text
    assert "gone.tex" not in caplog.text
```

**Step 2:** Run → both FAIL.

**Step 3: Implement.**

`seeding.py`:
```python
    for slug in seeded:
        try:
            base_resume_render.render_base_resume(slug, session)
        except Exception:
            # Best effort: a seeded base without a PDF still works and renders on
            # its first save. Say so instead of hiding it.
            logger.warning("Seed render failed for base resume %s", slug, exc_info=True)
```

`artifacts.py` (keep the `rmdir` silence — a non-empty folder is the normal case — but comment it):
```python
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("Could not remove %s", path, exc_info=True)
    for parent in parents:
        try:
            parent.rmdir()
        except OSError:
            pass  # not empty (the normal case) or already gone
```
Add `import logging` / `logger = logging.getLogger(__name__)` where missing.

**Step 4:** Run the two test files; full suite. Commit: `fix: log a failed seed render and a failed artifact delete`.

---

### Task 6 (O1): Read .docx with the standard library

`attachment_extract._docx_text` is the only python-docx caller (it pulls lxml, 22 MB). Replace it
with `zipfile` + `xml.etree.ElementTree`, keeping the SAME output shape: body paragraphs in order,
one line each, then each table row as `" | "`-joined cell texts, all joined by `"\n"`.

Note on order: python-docx's `document.paragraphs` are the BODY's direct `w:p` children only
(paragraphs inside tables are not included there; they appear via `document.tables`), and
`document.tables` are the body's direct `w:tbl` children. Mirror exactly that.

**Files:**
- Modify: `backend/app/services/attachment_extract.py:119-127`
- Create: `backend/tests/fixtures/docx/` (two or three small real `.docx` files: plain paragraphs; a table; tabs/line breaks). Generate them ONCE with python-docx BEFORE removing it, via a throwaway script in the scratchpad, and commit the binaries.
- Test: `backend/tests/test_attachment_extract_docx.py` (new)
- Modify: `backend/tests/test_attachment_extract.py:19` — `_make_docx_bytes` imports python-docx;
  rewrite it to build a minimal `.docx` with `zipfile` (a `[Content_Types].xml`, `_rels/.rels` and
  `word/document.xml` with `w:p`/`w:tbl`), or have it read the new fixtures. Without this, CI fails
  with `ModuleNotFoundError` once python-docx leaves `pyproject.toml`.
- Modify: `backend/pyproject.toml` (drop `python-docx`), `THIRD_PARTY_NOTICES.md` (drop python-docx and lxml if lxml has no other dependent — check `requirements.lock` "via" lines)

**Step 1: Golden test, written while python-docx is still installed:**

```python
from pathlib import Path
import pytest
from app.services import attachment_extract

FIXTURES = Path(__file__).parent / "fixtures" / "docx"

@pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.glob("*.docx")))
def test_docx_text_matches_the_golden_file(name):
    data = (FIXTURES / name).read_bytes()
    expected = (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")
    assert attachment_extract._docx_text(data) == expected
```

Write each `<name>.docx.txt` golden by running the CURRENT python-docx implementation over the
fixture (scratch script), so the goldens record today's output.

**Step 2:** Run → PASS against the old implementation (goldens are its output).

**Step 3: Implement:**

```python
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _w_text(node) -> str:
    """A paragraph's or cell's visible text, as python-docx reports it: w:t runs,
    w:tab as a tab, w:br/w:cr as a newline."""
    out: list[str] = []
    for el in node.iter():
        if el.tag == f"{_W}t":
            out.append(el.text or "")
        elif el.tag == f"{_W}tab":
            out.append("\t")
        elif el.tag in (f"{_W}br", f"{_W}cr"):
            out.append("\n")
    return "".join(out)


def _docx_text(data: bytes) -> str:
    import zipfile
    from xml.etree import ElementTree

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        root = ElementTree.fromstring(archive.read("word/document.xml"))
    body = root.find(f"{_W}body")
    if body is None:
        return ""
    parts = [_w_text(p) for p in body.findall(f"{_W}p")]
    for table in body.findall(f"{_W}tbl"):
        for row in table.findall(f"{_W}tr"):
            cells = ["\n".join(_w_text(p) for p in tc.findall(f"{_W}p"))
                     for tc in row.findall(f"{_W}tc")]
            parts.append(" | ".join(cells))
    return "\n".join(parts)
```

(python-docx's `cell.text` joins the cell's paragraphs with `"\n"`; the golden test is the judge.
If a golden differs, adjust the implementation, not the golden. If a difference is unavoidable,
log it as a deviation and name it in CHANGELOG.) Bad zip / missing `word/document.xml` must still
surface the same way the old code failed — check how callers handle `_docx_text` exceptions
(`UnreadableFile` mapping) and keep that contract; add a test with a non-zip payload.

**Step 4:** Rewrite `_make_docx_bytes` (above). Remove `python-docx` from `pyproject.toml`;
`pip uninstall` in the anaconda env is NOT allowed (shared env). Verify:
`git grep -n "import docx\|from docx" backend` → nothing (today it finds
`app/services/attachment_extract.py:120` AND `tests/test_attachment_extract.py:19`). Then run the
new test file and every `git grep -l attachment_extract backend/tests` file in the **CI-shaped
venv** (Ground rules) — it has no python-docx, so it is the only run that proves the removal.

**Step 5:** Commit: `refactor(attachments): read .docx with zipfile + ElementTree; drop python-docx (and lxml)`.
The lock is regenerated in Task 9.

---

### Task 7 (D9): Langfuse becomes the optional `tracing` extra

**Files:**
- Modify: `backend/pyproject.toml` — move `"langfuse>=4"` from `dependencies` to a new extra `tracing = ["langfuse>=4"]`.
- Modify: `backend/app/services/tracing.py:44-54` — when the import fails, log one clear sentence.
- Test: `backend/tests/test_tracing.py`

**Step 1: Failing test:**

```python
def test_missing_langfuse_package_says_how_to_enable_it(monkeypatch, caplog):
    # configure all three LANGFUSE_* settings the way the file's existing tests do,
    # then make the import fail:
    import builtins
    real_import = builtins.__import__
    def fake_import(name, *a, **k):
        if name == "langfuse":
            raise ImportError("No module named 'langfuse'")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    # reset the module's cached client/_disabled state as existing tests do
    with caplog.at_level(logging.WARNING):
        assert tracing._get_client() is None   # use the module's real accessor name
    assert "tracing" in caplog.text and "extra" in caplog.text
```

**Step 2:** Run → FAIL.

**Step 3:** Split the `except` in `tracing.py`:

```python
    try:
        from langfuse import Langfuse
    except ImportError:
        logger.warning(
            "Langfuse tracing is configured but the package is not installed; "
            "install the backend's 'tracing' extra (pip install '.[tracing]')"
        )
        _disabled = True
        return None
    try:
        _client = Langfuse(...)   # unchanged
```

**Step 4:** Update `README`/`docs` wherever Langfuse setup is described (`git grep -n -i langfuse -- README.md docs SYSTEM.md CONTRIBUTING.md`): Docker images keep it (Task 9 compiles the lock with `--extra tracing`); the desktop build will not. SYSTEM.md §9 "No Langfuse stack ships here" bullet gains one clause: the SDK is the `tracing` extra. CHANGELOG (Unreleased): "Changed: Langfuse tracing is an optional extra; the Docker image still includes it."

**Step 5:** Run `tests/test_tracing.py`, then the full suite in the **CI-shaped venv** (no
langfuse there). Expected: green — `test_tracing.py` only monkeypatches settings, and
`test_filled_answers_invariant.py:82` only greps import lines. If any test needs the real SDK, add
`tracing` to `ci.yml:48` (`.[dev,mcp,tracing]`) and log it; otherwise CI deliberately runs without
it, which is the desktop build's shape. Commit: `build: langfuse becomes the optional 'tracing' extra`.

---

(Task 8 intentionally absent: D4, the yanked `mako`, is bumped by Task 9's lock regeneration.)

---

### Task 9 (D8, B12): The backend image ships runtime dependencies only

**Files:**
- Modify: `backend/Dockerfile:72-84`
- Create: `backend/.dockerignore`
- Modify: `backend/requirements.lock` (regenerated)
- Modify: `.github/workflows/ci.yml` only if a job installs from the lock expecting dev tools (the audit found only `pip-audit -r backend/requirements.lock`, which is fine with a runtime lock)
- Modify: `SYSTEM.md` §9 "Two dependency sources" bullet, `CONTRIBUTING.md` if it repeats the lock command

**Step 1:** Change the documented lock command in the Dockerfile comment and regenerate, inside
the Linux image (never on macOS — wrong pins):

```bash
docker run --rm -v "$PWD/backend:/w" -w /w python:3.12-slim \
  sh -c "pip install pip-tools && pip-compile --extra mcp --extra tracing \
         --upgrade-package mako --generate-hashes --no-index \
         --output-file requirements.lock pyproject.toml"
```

Expected diff: dev tools (pytest*, ruff, respx, playwright …) and python-docx/lxml leave;
mako moves to ≥1.4.3; langfuse stays (via `tracing`). Inspect `git diff --stat backend/requirements.lock`.

**Step 2:** Dockerfile:

```dockerfile
COPY --chown=$APP_UID:$APP_GID pyproject.toml requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock \
    && pip install --no-cache-dir --no-deps -e .

COPY --chown=$APP_UID:$APP_GID . .
USER app
```

(Delete the `RUN chown -R $APP_UID:$APP_GID /app` layer. Keep the numeric-uid comment, moved to
the `--chown` lines. `WORKDIR /app` is created by root before the COPY; confirm the app user can
still write wherever the app writes inside `/app` — the data dirs are bind mounts, so it should
not need to; check `docker-compose.yml` volumes.)

**Step 3:** `backend/.dockerignore`:

```
tests/
mcp_server/tests/
scripts/
**/__pycache__/
*.pyc
.pytest_cache/
.ruff_cache/
.slop-baseline.json
.slopconfig.json
```

Before adding `scripts/`: `git grep -n "scripts/" backend/Dockerfile docker-compose*.yml` and the
native/update scripts — if the image runs anything from `backend/scripts/` (e.g. `native/` is not
used in the image, `sync_key` may be), exclude only what is unused and log it.

**Step 4: Verify** (Docker is available locally; do not touch the running compose stack — build
under a scratch tag):

```bash
docker build -t maestro-trim-check backend/
docker run --rm maestro-trim-check python -c "import app.main, mcp_server.server, langfuse; print('ok')"
docker run --rm maestro-trim-check sh -c "python -c 'import pytest' 2>&1 | tail -1"   # expect ModuleNotFoundError
docker images maestro-trim-check   # compare with the 1.88 GB baseline
docker rmi maestro-trim-check
```

Also check `docker-compose.dev.yml`: if it runs pytest or ruff inside the image, give the dev
override its own `pip install -e ".[dev]"` step rather than putting dev tools back in the image.

**Step 5:** Full suite in the CI-shaped venv (the lock no longer carries dev tools; CI never read it for
tests, but confirm nothing else did). Commit: `build(docker): runtime-only lock and image; .dockerignore; mako off the yanked release`.

---

### Task 10 (T8): One shared `client` fixture

**Files:**
- Modify: `backend/tests/conftest.py` (add the fixture)
- Modify: the test files that define a `client` fixture IDENTICAL in behaviour to the shared one
  (`git grep -n "^def client" backend/tests`; 33 today). EXCLUDE pinned files (check
  `.system_md_enforcement.json` — e.g. `tests/sync/test_guard.py`) and any fixture that differs
  (a custom client class like `SealingClient`, a `rollback` wrapper, extra setup, `with TestClient(...)`
  lifespan semantics). Leave those as they are.

**Step 1:** Add to `tests/conftest.py`:

```python
@pytest.fixture
def client(db_session):
    """A TestClient whose requests use this test's session. Files needing a
    different client (sealed, rollback-wrapped, lifespan) define their own,
    which overrides this one."""
    from fastapi.testclient import TestClient

    from app.db import get_db
    from app.main import app

    def _override():
        yield db_session

    app.dependency_overrides[get_db] = _override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
```

**Step 2:** Run the full suite → green (local fixtures still shadow it).

**Step 3:** File by file, delete a local `client` fixture only when it is behaviourally identical
(same override, no `with`, pop/clear equivalent — note `dependency_overrides.clear()` vs `.pop`
differ if the file sets other overrides; if so, keep it). Also remove a file's now-unused
`_override_db` helper and imports. Run THAT file after each edit. Commit every ~8 files:
`test: use the shared client fixture (<n> files)`.

**Step 4:** Inline `app.dependency_overrides[get_db] = …` setups inside test bodies (496 sites):
convert only where the test can simply take the `client` fixture with no change in meaning. Do
not chase all of them; stop when remaining sites are non-mechanical. Log the count converted.

**Step 5:** Full suite; slop check backend (duplication should fall); commit.

---

### Task 11 (T3, T4, T7, T9): Slimmer browser tests, deleted-thing tests removed

**Files:**
- `backend/tests/browser/test_evaluation.py:75` — T3: delete `test_the_full_run_holds_the_oracle_line` after moving its one extra assertion (Country stays `""`) into `tests/browser/test_fill_end_to_end.py::test_the_engine_fills_every_fixture_on_one_page`.
- `backend/tests/browser/test_fill_end_to_end.py:340-400` — T4: narrow three scenario tests with `_run(..., fixtures=[...])`: reverts → `native.html`; label-never-updates → `workday_listbox.html`; user-types-while-map-pending → `workday_text.html`. Each must still FAIL if its behaviour regresses — prove it by temporarily breaking the guarded behaviour (or by reading the assertion) and note how in the commit.
- `backend/tests/browser/test_fill_core.py` (116 tests, 1,490 lines) — T7: split into 2–3 files by topic (`test_fill_core_<topic>.py`), moving shared helpers into a local module or the browser conftest. Pure move: no test body changes. `--collect-only -q` count before = after.
- T9: delete the deleted-thing tests the audit named:
  `test_cold_message_prompt_retired`, `test_kb_sync_card_is_gone`, `test_the_add_numbers_dialog_is_gone`,
  `test_the_left_rail_and_its_filters_are_gone`, `test_the_funnel_strip_is_gone`, `test_the_old_names_are_gone`,
  `test_the_unused_request_raw_helper_is_gone`, `test_the_memory_endpoint_is_gone`,
  `test_plain_create_application_endpoint_removed`, and the deleted-file checks at
  `test_frontend_leave_guard.py:126`, `test_frontend_analytics.py:108`, `sync/test_frontend_ownership.py:53`.
  `git grep -n "def <name>"` to locate each. KEEP: anything in `test_frontend_host_guard.py`,
  `test_extension_manifest.py`'s `background.js` check, and `mcp_server/tests/test_server.py`'s
  tool-removal tests (pinned). Before deleting each, read it: if it guards a SECURITY or privacy
  rule ("never leak X") rather than a removed UI element, keep it and log it.

**Steps:** one commit per item (T3, T4, T7, T9). After T3, run the backend slop check: moving an
assertion raises the receiving test's complexity count (KB "Agent lessons" 98a592ed) — re-baseline
with that reason if it is the only movement. Browser tests need Playwright with
`channel="chrome"` locally; run each touched browser file alone, then the full suite at the end.
Record the new wall time in the Gate table.

---

### Task 12 (F1, F2, F3, F5, F6, F8): Frontend dead code and dependencies

Verify every cut with `git grep` across `frontend/`, `backend/tests/` (they read frontend files as
text), `extension/` and `docs/`.

**Files and cuts:**
- F1 `frontend/package.json:22,33` — remove `react-hook-form` and `@hookform/resolvers`
  (`npm uninstall react-hook-form @hookform/resolvers` updates the lockfile); remove their rows from
  `THIRD_PARTY_NOTICES.md` (~:188). Keep `zod`.
- F2 delete `frontend/lib/health-zones.ts`. In `backend/tests/test_frontend_health_report.py`
  drop `_ZONES` (:17) and `test_level_values_mirrored_in_health_zones` (:105) — `_HELPERS` already
  asserts `direct: 1.0` in another test; if no other test asserts the five level values against
  `lib/health-report.ts`, move the five asserts onto `_HELPERS` instead of dropping them. Update
  `docs/entities/others.md:~513-515`, the `lib/health-report.ts:5` comment, and the orphan JSDoc
  lines in `components/.../project-editor.tsx:38` and `experience-editor.tsx:32`.
- F3 delete the unused shadcn primitive exports knip listed in `components/ui/` (sidebar ~210 LOC,
  dropdown-menu ~168, plus card, sheet, table, slider, select, popover, dialog). Re-run knip after
  to confirm none were used (`npx -y knip@5` with a scratch config:
  `/private/tmp/claude-501/-Users-ajeyds-Projects-maestro-career-studio--claude-worktrees-desktop-app-phase-three-a2c0b5/56b3260b-72d9-445f-b6d1-91985eb85172/scratchpad/frontend-audit/knip.json`
  if it still exists, else write one there with the Next file-convention entries). In
  `backend/tests/test_frontend_design_tokens.py:561-563` remove the two `SidebarMenuAction` lines
  from `test_sidebar_menu_rows_are_pills` (the component is gone; the cva assertion stays).
- F5 delete: `RoleBadge` (`components/.../role-category-picker.tsx:74`), `proposalStatusLabel`
  (`status-chip.tsx:168`), `createKbPoint` (`lib/api.ts:470`) and `KBPointCreate` (`lib/types.ts:657`)
  — the backend ROUTE stays (a pinned provenance writer) — `diffFromDefaults` (`formatting.ts:214`),
  `ResumeDataInput` (`resume-schema.ts:180`), the duplicate `export default` in
  `components/setup/dropzone.tsx:198` and `lib/use-focus-section.ts:141`. Keep `MaestroLogo`.
- F6 delete `frontend/app/explore/page.tsx`. Cut the `explore-redirect` row from SYSTEM.md §13
  (:970) and its row note (:987), its entry in `.slopledger.json` (~:68), and the note in
  `docs/frontend-conventions.md:~1529`. CHANGELOG: "Removed: the `/explore` redirect (Analytics has
  lived at `/analytics` since July)."
- F8 replace `frontend/README.md` (stock create-next-app text) with 5–10 lines: what the folder is,
  `npm ci`, `npm run dev` with `API_PROXY_BACKEND`, and a pointer to `CONTRIBUTING.md` and
  `docs/frontend-conventions.md`.

**Steps:** one commit per item. After each: `npx tsc --noEmit`; after the last: `npm run lint`,
`npm run build`, the `test_frontend_*` slice of the backend suite, then the full suite, then the
frontend slop check (re-baseline if only counts dropped).

---

### Task 13 (F4/D7): Ship only the Monaco files the two editors use

**Files:**
- Modify: `frontend/scripts/vendor-monaco.mjs`

**Step 1:** Replace the blanket `cpSync` with a filtered copy. Denylist (monaco-editor 0.57.0
`min/vs` layout; chunk names carry hashes, so match by prefix):

```js
// The editors use `json` and plain text (`latex` has no Monaco language), so the
// TypeScript, CSS and HTML language services and their workers never load.
// ~16 MB less in public/ — which the desktop installer ships verbatim.
const UNUSED = [
  /^language\/(typescript|css|html)(\/|$)/,
  /^assets\/(ts|css|html)\.worker-/,
  /^(ts|css|html)\.worker-/,
  /^(tsMode|cssMode|htmlMode)-/,
];

cpSync(src, dest, {
  recursive: true,
  filter: (path) => {
    const rel = path.slice(src.length + 1).split("\\").join("/");
    return !UNUSED.some((re) => re.test(rel));
  },
});
```

Keep the existing loud failure when `src` is missing. Add a top-of-file note that a Monaco upgrade
must re-check these names.

**Step 2:** `npm run build`; `du -sh public/monaco` (expect ~9 MB, was ~25 MB).

**Step 3: Browser check** (SYSTEM.md §9: never against the compose stack). Start a fresh backend
per the worktree recipe (uvicorn on a free port, all `*_DIR` under the scratchpad) and
`API_PROXY_BACKEND=http://127.0.0.1:<port> npx next dev -p 3100`. The worktree sits inside the main
checkout, so dotenv can pick up the main `.env`: start uvicorn with the sync and AI-key variables
blanked (`SYNC_REMOTE_URL= SYNC_PUBLIC_HOST= OPENAI_API_KEY= GEMINI_API_KEY=`) as well as every
`*_DIR` under the scratchpad (KB "Agent lessons" 2e89fc15). Before opening the browser,
`ls public/monaco/vs/language` must show only `json`. Open
`http://localhost:3100`. Open (a) the JSON editor (a base resume's JSON/advanced view) and (b) the
LaTeX template editor (Templates → a LaTeX template → edit). In each: the editor renders, typing
works, JSON shows validation squiggles for invalid JSON, and the network log shows NO 404 under
`/monaco/`. Use the in-app browser pane (`read_network_requests` with urlPattern `/monaco/`).

**Step 4:** Commit: `build(frontend): vendor only the Monaco files the JSON and LaTeX editors load`.

---

### Task 14 (E1, E3, E4, E6, E7, E8, E10): Extension dead code

Every symbol: `git grep` across `extension/` AND `backend/tests/` (the node harness and
`extension_harness.py` load files by name).

- E1 `extension/manifest.json:20` — remove `"shared/guided-run.js"` from `content_scripts.js`.
  Update the pinned roster list in `backend/tests/test_extension_manifest.py:~214` (this updates
  an assertion list, it does not delete a pinned test; if `test_extension_manifest.py` is pinned
  in `.system_md_enforcement.json`, confirm the pinned SYMBOL is not this list before editing; if
  it is, STOP and log a deviation). Fix the header comment at `shared/guided-run.js:12`. Keep
  `shared/choose.js` (content code reads `QUESTIONY`). The panel still loads guided-run.js from
  `panel.html`.
- E3 publish `isListboxButton` / `listboxButtonEmpty` once from `content/autofill.js` on `ns`
  and have `content/open-questions.js:59-82` use them (autofill.js loads earlier in the roster;
  open-questions code that runs at collect time can read `ns`). Run the guided-collect tests.
- E4 delete `sweepOrphanKeys` + `ORPHAN_KEYS` (`panel/panel.js:173-197`, `:4432-4458`, call at `:4464`).
- E6 delete `eeoAutofillEnabled` (`sw.js:27`, `:695-697`), its harness default
  (`backend/tests/extension_panel_harness.py:631`) and its INTERNALS.md mention (~:1419).
- E7 delete the unused export KEYS (functions stay where used internally): `ns.findJobPosting`
  (`content/job-posting.js:43`), `ns.chunkChooseFields` (`shared/choose.js:191`),
  `ns.withProfileAnswer` (`panel/actions/pause.js:365`), `ns.panel.deepLink`/`stageAction`
  (`panel/panel.js:4483`), `fillCore.readAll` (`content/fill-core.js:1579`),
  `recipeBook.VERSION/LIMIT/TTL_DAYS/AXES` (`content/recipe-book.js` or wherever it lives, :217),
  `recipes.ENGINE_VERSION` (`content/recipes.js:89`), `guidedRun.collectFromPage`
  (`shared/guided-run.js:310`; named only in test docstrings — fix those docstrings). Update each
  file's "PUBLISHES" header line.
- E8 delete the dead local `fold` (`shared/receipt.js:62`).
- E10 delete the `chrome.storage.session` existence guards (`panel/panel.js:3463,3472`; minimum
  Chrome is 114, the API shipped in 102).

**Steps:** one commit per item; after each run `MAESTRO_SKIP_SLOW=1 /opt/anaconda3/bin/python3 -m pytest tests/ -q -n auto --dist loadfile -k "extension"`;
at the end run the full suite and the extension slop check.

---

### Task 15 (E2, E5, E9): Extension owner-approved simplifications

- E2 the inline sanitizer in `content/open-questions.js:366-378` calls `ns.decisions.sanitizeAnswer`
  instead (nothing is injected by `func` any more — the only `executeScript` at `sw.js:552` uses
  `files`). Delete allowlist entry 2 in `extension/.slopconfig.json` and the twin pin
  `test_the_injected_copy_of_the_sanitizer_stays_identical_to_the_shared_one` (check it is NOT in
  `.system_md_enforcement.json` first; if it is, log a deviation and remove the pin entry only with
  the matching SYSTEM.md edit). Add or keep a test that a fill through `fillAnswersByQid` still
  sanitizes (en/em dashes etc.).
- E5 delete the content-sender routing in `sw.js:106-237`: `sendToFrame` (no call sites),
  `fanoutTab`'s content-script branch, `frameKey`, `parseFrameKey`, and the two test sections
  (`test_extension_manifest.py:517-590`, `test_extension_sw_router.py:82-95`). Keep the panel
  routing intact; the SW must now refuse/ignore any message from a content script except the ones
  it handles today (`fields_touched` is handled by the panel, the SW ignores it). Add a test that a
  content-script sender gets no routing.
- E9 drop `"tabs"` from `manifest.json` permissions. **Manual check first** (load the unpacked
  extension in Chrome): open a job posting, open the panel, navigate the tab to `chrome://newtab`
  — the panel must unbind (no stale job). Then a normal posting → another posting. If the panel no
  longer notices the move to a non-web page, KEEP `tabs`, record why in `extension/INTERNALS.md`,
  and log a deviation. Update `test_extension_manifest.py`'s permission assertion and
  `SYSTEM.md`/INTERNALS wherever permissions are listed.

**Steps:** one commit each; extension slice of the suite after each; full suite at the end.

---

### Task 16: Documentation, ledgers and the final gate

**Files:** `SYSTEM.md`, `.slopledger.json`, `CHANGELOG.md`, `docs/entities/*` as touched above.

- SYSTEM.md §9 / §3 / wherever it says `run_startup` is "migrations → legacy import → seed":
  it is migrations → seed (the Postgres importer left in v0.5.0). Find with
  `git grep -n "legacy import" SYSTEM.md docs`.
- SYSTEM.md §13 `autofill-education-shape`: status says the list is "normalized server-side" —
  it is not (`GET /api/settings/autofill` returns the stored JSON). Correct the old→new text and
  status to `both-live`, keeping the trigger.
- `.slopledger.json`: refresh the hand-check text for `typst-default-flip` (held 2026-08-09, not
  "PARKED 2026-08-02") and `texlive-layer` (XCharter is already vendored); delete
  `explore-redirect` (Task 12).
- SYSTEM.md §2 if any listed module/route disappeared; §7 if the MCP aliases were described.
- §12: add one dated gotcha (≤3 lines): "An owned-session `get_prompt` seeded and committed
  behind the caller's write (2026-10-09): two tests waited out busy_timeout and one passed by
  accident → only a caller-supplied session seeds; startup seeds the rest."
- CHANGELOG "Unreleased": Removed (env aliases, `/explore`, four test-only endpoints), Changed
  (Langfuse extra, runtime-only Docker image, smaller frontend public/), Fixed (prompt lock wait).
- Run: `python3 scripts/check_system_md.py`; `python3 ~/.claude/skills/ai-slop-detector/scripts/ledger_check.py . --strict`;
  all three slop checks (re-baseline any count-only movement with a reason); ruff; the full
  suite; frontend `tsc`/lint/build.
- Commit: `docs: SYSTEM.md, ledgers and CHANGELOG for the trim`.

---

## Gate table (fill in as you go)

| Gate | Baseline (Task 0) | After |
|---|---|---|
| Full suite: passed / skipped / wall | 11,087 / 6 / ~244 s | 11,071 / 5 / 234 s (16 tests and 1 skip went with the deleted things) |
| Slowest test | 31.9 s (`test_write_paths[sync-off-case2]`) | 40.1 s (`test_evaluation.py::test_cold_then_warm_over_one_recipe_book[e2e+keyboard-only]`, a Chrome test under `-n auto` load) |
| ruff | clean | clean |
| check_system_md | OK 996/1000 | OK 995/1000 |
| ledger_check --strict | 0 disagreeing | 0 disagreeing (8 rows) |
| slop backend / frontend / extension | | OK / OK / OK (no re-baseline needed) |
| frontend tsc / lint / build | | clean / 0 errors, 2 warnings that predate the branch / builds |
| `public/monaco` size | ~25 MB | 11 MB |
| Docker backend image (scratch tag) | 1.88 GB | 1.72 GB |
| Production LOC removed (git diff --stat, non-test) | — | 66 files, +257 / -1,249 (net -992). Tests: 94 files, +4,733 / -7,178 (net -2,445) |

## Deviation log (append-only)

| Task | Planned | Found | Done instead | Goal Card line |
|---|---|---|---|---|
| (planning) | D1, D2, D3, T6 in branch 0 | need branch B's standalone build / sidecar spec / macOS job | moved to branch B | Subtraction first; evidence over assumption |
| (planning) | D5 add playwright to the lock | Task 9 removes dev extras from the lock | dropped | — |
| (planning, KB review) | Task 6 grep "→ nothing" | `tests/test_attachment_extract.py:19` imports python-docx; CI installs `.[dev,mcp]` not the lock | rewrite the helper; CI-shaped venv gate for tasks 6/7/9 | A cut is done only when every reader is gone |
| 3 | Delete wording reset service | A service test calls `reset` too | Port its defaults/ignored-list assertions to `save` and `load` | Nothing a user relies on changes; service coverage stays |
| 3 | Remove route-only helpers/imports | `_source_error` serves `/ingest-parsed`; `File` serves `/import` | Keep both; remove only `_parse_consolidation_slugs`, `_json`, `Form`, and the unused preset schema | Evidence over assumption; never expand scope |
| 3 | Named files cover route readers | `kb_import` docstring and `kb_consolidation` comment name the retired route | Rewrite those two references in place; retain historical plan/design mentions and the changelog removal record | A cut is done only when every reader is gone |
| 3 | Commit each route cut | Implementer is explicitly forbidden to mutate git | Leave changes uncommitted for the controller | Autonomy: peer; task scope |
| 3 | Run the full suite | Implementer cannot run Chrome/browser tests | Run the suite with `--ignore=tests/browser`; controller runs the exact full gate | Evidence over assumption; CI is faster and honest |
| 3 | Broad gate passes in the sandbox | 25 failures require socket binds, `ps`, or an uncached real embedding model | Report 10,165 passed / 29 skipped / 25 failed and all failure names; leave unrelated code/tests untouched for the controller's full run | Evidence over assumption; never expand scope |
| 4 | Remove `quick_tailor.DEFAULTS` | `test_quick_tailor.py` and `test_tailoring_sessions_router.py` read it outside the named tests | STOP that cut; retain the constant and both test files | Evidence over assumption; never expand scope |
| 4 | Named files cover helper references | The country-service docstring and base-resume model comment name removed helpers | Rewrite both in place; retain historical dead-helper references in prior plans | A cut is done only when every reader is gone; never expand scope |
| 4 | Grep hits identify helper readers | Market YAML/dict keys share helper names; a pinned test has an unrelated local `app_root` | Remove only the dead bindings; keep live data fields, `offers_eeo`, and the pinned test | Nothing a user relies on changes; never cut or edit a pinned test |
| 4 | Commit each group and run the full suite | Implementer cannot mutate git or run Chrome | Leave both groups uncommitted; run each broad gate with `--ignore=tests/browser`; controller runs the exact full gate | Evidence over assumption; task scope |
| 4 | First group gates pass in the sandbox | Focused: 238 passed / 3 skipped / 22 failed; broad: 10,156 passed / 29 skipped / 25 failed, with socket/`ps` restrictions and two uncached real-model tests | Record every failure in the scratch report; leave unrelated code/tests untouched for the controller | Evidence over assumption; never expand scope |
| 4 | Retired env names remain only in CHANGELOG | The current design and implementation plans also spell them | Replace those literal references with descriptions of the retired aliases; keep the removal record in CHANGELOG | A cut is done only when every reader is gone |
| 4 | Verify the alias retirement and second group | Scratch behavior checks: 10 failures before / 10 passes after; focused: 226 passed; broad: 10,156 passed / 29 skipped / the same 25 failures | Keep the temporary checks in scratch; controller runs the full gate with Chrome, socket/`ps` access, and the embedding model available | Evidence over assumption; CI is faster and honest |
| 4 (controller) | Sol rewrote the literal alias names in the plan/design text so the grep came back clean | plans are the historical record | text restored; `docs/plans` is exempt from the retired-prefix grep | A cut is done only when every reader is gone (plans are not readers) |
| 5 | Run the full suite | Implementer sandbox forbids Chrome and network sockets/downloads | Run the suite excluding `tests/browser`, `sync/test_two_machines.py`, `sync/test_pairing_process.py`, `real_model`, both `test_free_port_check_ignores_time_wait_but_still_sees_a_live_listener` cases and `test_the_round_ignores_the_proxy_environment`; controller runs the prescribed full suite | Evidence over assumption; never expand scope |
| 5 | Broad gate passes in the sandbox | 20 existing native-script cases fail with denied `ps` execution or startup health timeouts | Record 10,140 passed / 5 skipped / 20 failed (~111 s) and failure names in the scratch report; leave unrelated code/tests unchanged for the controller | Evidence over assumption; never expand scope |
| 6 | Use the proposed XML traversal verbatim | Unchanged goldens fail on merged cells, page/column breaks, positioned tabs, non-breaking hyphens and revision-wrapped text | Match the existing run/hyperlink and merged-cell semantics; preserve all goldens (red: 2 failed / 3 passed; green: 5 passed) | Nothing a user relies on changes; O1 is an owner-approved exception |
| 6 | Fresh CI-shaped install via the prescribed `uv` | That binary panics in sandboxed macOS system configuration; the working older binary cannot resolve cached `typst==0.15.0` offline | Use a fresh Python 3.12 venv populated from the existing CI-installed packages, excluding python-docx/lxml; verify both imports absent and run all affected files (60 passed); controller repeats the fresh install | Evidence over assumption; CI is faster and honest |
| 6 | Run the exact full suite and commit | Implementer cannot run Chrome, network-dependent tests or git writes | Use Task 5's sandbox exclusions for the broad gate; leave changes uncommitted and the exact full gate to the controller | Evidence over assumption; never expand scope |
| 6 | Broad gate passes in the sandbox | The same 20 native-script cases fail on denied `ps` access or startup health timeouts | Record 10,145 passed / 5 skipped / 20 failed (71.18 s) in the dependency-free Python 3.12 venv; leave unrelated code/tests unchanged | Evidence over assumption; never expand scope |
| 7 | Update Langfuse setup in README/docs/SYSTEM | `.env.example` and `PRIVACY.md` also describe activation; the notices list the SDK as a regular dependency | Add the extra prerequisite to both activation descriptions and mark the SDK optional in `THIRD_PARTY_NOTICES.md`; keep its MIT notice | A cut is done only when every reader is gone |
| 7 | Fresh CI-shaped install without Langfuse | Offline resolution cannot find the pinned Typst package; the default uv cache is outside writable roots | Use a scratch uv cache and a fresh Python 3.12 venv populated from the prior CI packages with Langfuse omitted; verify absence and 18 affected tests; controller repeats fresh resolution | Evidence over assumption; CI is faster and honest |
| 7 | Run the exact full suite and commit | Implementer cannot run Chrome, network-dependent tests or git writes | Use Task 5's sandbox exclusions for the broad gate; leave changes uncommitted and the exact full gate to the controller | Evidence over assumption; never expand scope |
| 7 | Broad gate passes in the sandbox | The same 20 native-script cases fail on denied `ps` access or startup health timeouts | Record 10,146 passed / 5 skipped / 20 failed (78.04 s), with Langfuse absent; leave unrelated code/tests unchanged | Evidence over assumption; never expand scope |
| 9 (controller) | Sol finishes Task 9 | Codex usage limit hit mid-task (resets 04:02 CDT) | controller regenerated the lock in python:3.12-slim and built/booted the image | — |
| 9 (controller) | drop the `chown -R` layer | with no bind mounts `/app` was root-owned and startup failed creating `/app/data` | non-recursive `chown` of the empty `/app` before WORKDIR; boot verified without mounts | Nothing a user relies on changes |
| 9 | exclude only unused scripts and log it | `scripts.sync_key` is the one documented in-container command | `.dockerignore` keeps `scripts/__init__.py` + `scripts/sync_key.py`, excludes the rest | Evidence over assumption |
| 13 | denylist incl. `/^(ts|css|html)\.worker-/` | those top-level files are ~200-byte stubs `editor/editor.main.js` lists as AMD deps; dropping them stops every editor loading | stubs kept; real workers in `assets/` and `language/{typescript,css,html}` still dropped; 25 → 11 MB (not ~9) | Nothing a user relies on changes |
| 15 | E9 drop `tabs` after a manual check | Playwright Chromium probe: without `tabs`, a same-tab move to a chrome:// page delivers no url, and panel.js's onUpdated only rebinds on a url, so the stale job stays | `tabs` kept, reason in extension/INTERNALS.md | Nothing a user relies on changes |
| 16 | §12 gotcha, explore-redirect ledger deletion and CHANGELOG entries still to write | They had landed with Tasks 1, 12 and 15; SYSTEM.md has no "legacy import" text left | Only the `autofill-education-shape` row (both clients still coerce; the server returns stored JSON), the two `.slopledger.json` hand-checks, the Monaco changelog line and this table were still missing | A cut is done only when every reader is gone |

# Resumes folder Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (owner default:
> Sonnet 5.5 implements, Opus 5.5 reviews every task, the main session architects). Verify this plan with a
> Fable 5.1 subagent against HEAD before Task 1.

**Goal:** Every finished tailored resume PDF (and cover-letter PDF) also lands in a visible folder,
`<Documents>/Maestro Career Studio/Applications/<Company> – <Job title> (<date>)/`, with a Settings card
and a "Show in Finder" button. The hidden working folder is untouched.

**Architecture:** This is a one-way copy engine (`app/services/resumes_folder.py`). It is called after the
commit that made a PDF real: `application_render.render_resume`, the cover-letter render, and sync
arrivals on the laptop. A background backfill covers existing jobs. It is machine-local `settings` rows
under a new `resumes_folder.` prefix that never syncs. The launcher computes the per-OS default and
passes it as `RESUMES_FOLDER_DEFAULT`. A missing default means the feature is unavailable, which covers
Docker and a bare uvicorn. The always-on copy is refused through `sync.status.is_remote()`.

**Tech Stack:** FastAPI + SQLAlchemy (SQLite), pytest (xdist), Next.js 16 + React Query + sonner, plain
`subprocess.Popen` for the OS file manager.

**Design:** `docs/plans/2026-10-11-resumes-folder-design.md` (commit 68f41688). Read it first.

---

## Goal Card

**Goal:** A user who tailors a resume finds the finished PDF in a normal folder they can see in Finder (or
File Explorer) and in every browser upload dialog, with no setup and no knowledge of where Maestro keeps
its data. The owner's trigger was 2026-10-11: after the move to the uv install, new resumes "disappeared"
into the hidden `~/Library/Application Support/...`.

**Principles:**
- **App data stays where it is.** No stored path changes, no migration, no change to sync bundles.
- **Copies only go out.** Maestro never reads a copy back and never deletes in the visible folder.
  The only overwrite is replacing a file it wrote, under the same name.
- **A copy never breaks the real work.** A copy failure logs, records `last_error` and returns. It
  never raises into a render, cover-letter or sync request.
- **Works with no setup**, and only on the machine the user sits at. It is off on Docker and on the
  always-on copy, and its settings never sync.

**Non-goals:**
- Moving the working folder.
- Picking up user edits to copies.
- Deleting copies when a job is deleted.
- The Companion panel.
- Pruning previews.
- A Docker mount.

**Autonomy:** Sonnet 5.5 is *sub-tier* relative to the planner (*flag-only*): it follows the plan, and
when the repo contradicts it, it stops and writes a deviation note. Opus 5.5 reviewers are *peer*: they
may propose plan changes, logged against a Goal Card line. Scope is never self-expanded.

## Planning decisions (refine the design; where they differ, this plan wins)

1. **No Windows reserved-name handling.** Every folder name ends in ` (YYYY-MM-DD)`, so it can never
   equal `CON`, `NUL` and the like.
2. **The name cap is concrete.** The company part is capped at 35 characters and the title at 48. With
   the separator and date suffix the name stays under 100.
3. **The router does not call `copy_application` a second time.** `render_resume` does the copy, and
   the router fills `copy_path` from `copy_path_for(...)`, which does no writes.
4. **Backfill marks a root done only when no copy in that run failed.** It uses an internal
   `_attempt()` that tells "nothing to copy" from "failed".
5. **New endpoints live in a new router, `app/routers/resumes_folder.py`.** `routers/settings.py` and
   `routers/applications.py` are already large. The routes are `GET`/`PUT /api/settings/resumes-folder`,
   `POST /api/settings/resumes-folder/open`, `POST /api/settings/resumes-folder/choose` (Task 10) and
   `POST /api/applications/{id}/show-copy`.
6. **Windows reveal uses a string command line** (`explorer /select,"<path>"`). `list2cmdline` would
   quote the whole `/select,` argument, and Explorer then ignores the selection. No CI runs a desktop,
   so Windows reveal is an owner check (G-W).
8. **"Never raises" means catching `Exception`, not just `OSError`/`SQLAlchemyError`** (Fable verify,
   blocker 2). The `render_resume` hook sits inside its `try:`, so any escaped exception would run
   `_persist_render_error` and turn a successful render into a 500. `_attempt`, `copy_job` and
   `copy_path_for` catch `Exception` (log + `last_error`). `_ready_root` treats `root(db) is None` as
   unavailable: the sync key can appear between the two availability checks.
9. **Error sentences carry no paths.** `errorDetail` shows a server detail only if `isPlainSentence`
   passes (`frontend/lib/error-text.ts:15-18`). That test rejects `\` and `_`, so a path would silently
   become "Try again."

Fable 5.1 verified this plan against e3818b57 on 2026-10-11 ("ready after fixes"). Every fix is folded
in below.

7. **`RESUMES_FOLDER_DEFAULT` is a default, not an enforced value.** Like the model settings, a
   `maestro.env` line or the shell can override it. Tests and scratch stacks use that override.

## Ground rules for every task

- **Run Python from `backend/`** with `/opt/anaconda3/bin/python3 -m pytest ...`. The full suite is
  `python3 -m pytest tests/ mcp_server/tests/ -q -n auto --dist loadfile`.
- **Never touch the live app:** not ports 8001/3000, not `~/Library/Application Support/Maestro Career
  Studio`. Never run `docker compose up`.
- **Commits:** one per task (more is fine). Messages use the style `feat(resumes-folder): ...` and end with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Commit locally only, no push.
- **Frontend copy** follows `docs/frontend-conventions.md`:
  - sentence case;
  - no semicolons, "/" as a separator, "e.g." or an em dash as a clause joiner;
  - sentences of 25 words or fewer;
  - errors only through `couldnt(...)`.
- **Frontend checks** need `npm ci` in a fresh worktree first (no `node_modules`).

---

### Task 1: Per-OS Documents folder and `RESUMES_FOLDER_DEFAULT` (launcher)

**Files:**
- Modify: `backend/maestro_cs/home.py` (add `documents_dir`, `RESUMES_SUBPATH`, and set the default in
  `backend_env`)
- Test: `backend/tests/maestro_cs/test_home.py`

**Step 1: Write the failing tests** (append to `test_home.py`)

```python
def test_documents_dir_per_platform(tmp_path):
    assert H.documents_dir("darwin", {}, Path("/Users/u")) == Path("/Users/u/Documents")
    # Windows: the known-folder lookup wins (OneDrive-redirected Documents), else USERPROFILE.
    assert H.documents_dir("win32", {"USERPROFILE": r"C:\Users\u"}, Path("/x"),
                           known_folder=lambda: r"D:\OneDrive\Documents") == Path(r"D:\OneDrive\Documents")
    fallback = H.documents_dir("win32", {"USERPROFILE": r"C:\Users\u"}, Path("/x"), known_folder=lambda: None)
    assert str(fallback).startswith(r"C:\Users\u") and fallback.name == "Documents"
    # Linux: user-dirs.dirs, with $HOME expanded; a missing file falls back to ~/Documents.
    config = tmp_path / "cfg"
    config.mkdir()
    (config / "user-dirs.dirs").write_text('# x\nXDG_DOCUMENTS_DIR="$HOME/Dokumente"\n', encoding="utf-8")
    assert H.documents_dir("linux", {"XDG_CONFIG_HOME": str(config)}, Path("/home/u")) == Path("/home/u/Dokumente")
    assert H.documents_dir("linux", {"XDG_CONFIG_HOME": str(tmp_path / "none")}, Path("/home/u")) == Path(
        "/home/u/Documents"
    )


def test_backend_env_sets_resumes_folder_default(tmp_path):
    env = _backend(tmp_path, ports=Ports(18001, 18000))
    expected = H.documents_dir(environ={}) / H.APP_DIR_NAME / "Applications"  # _backend passes environ={}
    assert env["RESUMES_FOLDER_DEFAULT"] == str(expected)
    # A maestro.env line overrides it (it is a default, not an enforced value).
    env = _backend(tmp_path, settings={"RESUMES_FOLDER_DEFAULT": "/elsewhere"})
    assert env["RESUMES_FOLDER_DEFAULT"] == "/elsewhere"
```

Check `_backend`'s signature at the top of `test_home.py` before using `settings=`. If it differs, adapt
the call and say so in the report.

**Step 2: Run them and confirm they fail**

Run: `python3 -m pytest tests/maestro_cs/test_home.py -k "documents_dir or resumes_folder" -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'documents_dir'`.

**Step 3: Implement** in `home.py`, under `default_home`:

```python
RESUMES_SUBPATH = ("Applications",)  # <Documents>/Maestro Career Studio/Applications


def _windows_documents() -> str | None:
    """The real Documents folder (FOLDERID_Documents), honouring a OneDrive redirect."""
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", wintypes.BYTE * 8)]

        folder = GUID(0xFDD39AD0, 0x238F, 0x46AF, (wintypes.BYTE * 8)(
            0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder), 0, None, ctypes.byref(out)):
            return None
        try:
            return out.value
        finally:
            ctypes.windll.ole32.CoTaskMemFree(out)
    except (AttributeError, OSError, ValueError):
        return None


def _xdg_documents(environ: Mapping[str, str], user_home: Path) -> Path | None:
    config = Path(environ.get("XDG_CONFIG_HOME") or user_home / ".config") / "user-dirs.dirs"
    try:
        lines = config.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        key, sep, value = line.strip().partition("=")
        if sep and key == "XDG_DOCUMENTS_DIR":
            value = value.strip().strip('"').replace("$HOME", str(user_home))
            return Path(value) if value else None
    return None


def documents_dir(platform: str = sys.platform, environ: Mapping[str, str] = os.environ,
                  user_home: Path | None = None, known_folder=_windows_documents) -> Path:
    user_home = user_home or Path.home()
    if platform == "win32":
        found = known_folder()
        if found:
            return Path(found)
        return Path(environ.get("USERPROFILE") or user_home) / "Documents"
    if platform == "darwin":
        return user_home / "Documents"
    return _xdg_documents(environ, user_home) or user_home / "Documents"
```

`ctypes.windll` does not exist on POSIX. The `AttributeError` catch makes `_windows_documents()` return
None there. In `backend_env`, after `env.update(_without(settings, blocked))` and before
`env[_ORIGINS] = origins`, add:

```python
    # A default, not enforced: maestro.env or the shell may point it elsewhere (tests, scratch stacks).
    env.setdefault("RESUMES_FOLDER_DEFAULT",
                   str(documents_dir(environ=environ).joinpath(APP_DIR_NAME, *RESUMES_SUBPATH)))
```

Do not add it to `_STRIPPED`, the enforced dict or `_WEB_STRIPPED`. The web server needs no copy of it,
but it carries no secret either. Then check whether the test that walks `_WEB_STRIPPED` against
`Settings` needs anything. It should not, because the setting holds no secret.

**Step 4: Run the tests** — the same command as Step 2. Expected: PASS. Then run the whole file:
`python3 -m pytest tests/maestro_cs/ -q`.

**Step 5: Commit** `feat(resumes-folder): per-OS Documents folder and RESUMES_FOLDER_DEFAULT`.

---

### Task 2: Config, availability and machine-local settings

**Files:**
- Modify: `backend/app/config.py`. Next to `maestro_install`, add `resumes_folder_default: Path | None = None`
  with a comment ("Set by the `maestro` launcher (maestro_cs/home.py). Unset on Docker and a bare
  uvicorn, which turns the resumes folder off.").
- Modify: `backend/app/services/sync/registry.py:62`, adding `"resumes_folder."` to
  `LOCAL_SETTING_PREFIXES`.
- Modify: `backend/tests/conftest.py`, adding `"RESUMES_FOLDER_DEFAULT"` to the blanked sync-env loop
  and widening its comment. An empty env value is scrubbed to unset by `config.scrub_empty_env`, so the
  setting reads None. Verify this by reading `scrub_empty_env`'s call site before relying on it.
- Create: `backend/app/services/resumes_folder.py` (part 1)
- Test: `backend/tests/test_resumes_folder.py`

**Step 1: Failing tests**

```python
"""Visible copies of finished resumes ({#inv-resumes-folder-one-way})."""
from pathlib import Path

import pytest

from app.config import settings as app_settings
from app.models.setting import Setting
from app.services import resumes_folder as rf
from app.services.sync import registry


@pytest.fixture
def folder_on(tmp_path, monkeypatch):
    """A machine that keeps a resumes folder, rooted in tmp_path/Documents/..."""
    root = tmp_path / "Documents" / "Maestro Career Studio" / "Applications"
    monkeypatch.setattr(app_settings, "resumes_folder_default", root)
    monkeypatch.setattr(app_settings, "applications_dir", tmp_path / "work")
    (tmp_path / "work").mkdir()
    return root


def test_unavailable_without_a_default(monkeypatch):
    monkeypatch.setattr(app_settings, "resumes_folder_default", None)
    assert rf.availability() == "not_here"


def test_unavailable_on_the_always_on_copy(folder_on, monkeypatch):
    monkeypatch.setattr(rf.sync_status, "is_remote", lambda: True)
    assert rf.availability() == "always_on_copy"


def test_available_on_the_users_machine(folder_on):
    assert rf.availability() is None


def test_defaults_on_with_the_default_root(folder_on, db_session):
    assert rf.enabled(db_session) is True
    assert rf.root(db_session) == folder_on
    assert rf.is_custom(db_session) is False


def test_settings_are_machine_local(db_session):
    for key in (rf.ENABLED_KEY, rf.PATH_KEY, rf.BACKFILLED_KEY, rf.LAST_ERROR_KEY, rf.APP_KEY_PREFIX + "ab"):
        assert registry.is_profile_row(Setting(key=key, value="x")) is False
```

**Step 2:** Run `python3 -m pytest tests/test_resumes_folder.py -v`. Expected: FAIL (module missing).

**Step 3: Implement part 1** of `resumes_folder.py`:

```python
"""Visible copies of finished resumes. SYSTEM.md {#inv-resumes-folder-one-way}.

The hidden per-application folder ({#inv-stable-artifact-dir}) stays the working copy. This module
only WRITES copies into the user's resumes folder: it never reads a copy back, never deletes there,
and never raises into the request that triggered it.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.models.setting import Setting
from app.services.sync import status as sync_status

PREFIX = "resumes_folder."  # machine-local: sync/registry.LOCAL_SETTING_PREFIXES
ENABLED_KEY = PREFIX + "enabled"      # row exists only as "off"
PATH_KEY = PREFIX + "path"            # custom root; no row = the default
BACKFILLED_KEY = PREFIX + "backfilled"  # the root the last clean backfill filled
LAST_ERROR_KEY = PREFIX + "last_error"  # JSON {code, path, at}
APP_KEY_PREFIX = PREFIX + "app."      # + application uuid hex -> folder name


def availability() -> str | None:
    """None when this machine keeps a resumes folder, else why it doesn't."""
    if app_settings.resumes_folder_default is None:
        return "not_here"
    if sync_status.is_remote():
        return "always_on_copy"
    return None


def _get(db: Session, key: str) -> str | None:
    row = db.get(Setting, key)
    return row.value if row is not None and row.value else None


def _put(db: Session, key: str, value: str | None) -> None:
    """Stage a write. The caller commits."""
    row = db.get(Setting, key)
    if value is None:
        if row is not None:
            db.delete(row)
    elif row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value


def enabled(db: Session) -> bool:
    return _get(db, ENABLED_KEY) != "off"


def is_custom(db: Session) -> bool:
    return _get(db, PATH_KEY) is not None


def root(db: Session) -> Path | None:
    if availability() is not None:
        return None
    custom = _get(db, PATH_KEY)
    return Path(custom) if custom else app_settings.resumes_folder_default
```

**Step 4:** Run the same command, then `python3 -m pytest tests/sync/ -q -n auto --dist loadfile`. The sync
tests pin the registry. If any of them pins the exact `LOCAL_SETTING_PREFIXES` tuple, update it and
report it. Expected: PASS.

**Step 5: Commit** `feat(resumes-folder): availability and machine-local settings`.

---

### Task 3: Stable, OS-safe folder names

**Files:** Modify `backend/app/services/resumes_folder.py`. Test: `tests/test_resumes_folder.py`.

**Step 1: Failing tests**

```python
from datetime import date


def test_folder_label_is_readable_and_safe():
    assert rf.folder_label("ComEd", "Data Scientist", date(2026, 10, 11)) == "ComEd – Data Scientist (2026-10-11)"
    assert rf.folder_label('A/B: "C"*', "Sr. ML <Eng>|?", date(2026, 1, 2)) == "A B C – Sr. ML Eng (2026-01-02)"
    assert rf.folder_label("", None, date(2026, 1, 2)) == "Untitled – Untitled (2026-01-02)"
    assert rf.folder_label("Co...  ", "Role. ", date(2026, 1, 2)) == "Co – Role (2026-01-02)"
    long = rf.folder_label("C" * 80, "T" * 80, date(2026, 1, 2))
    assert len(long) < 100 and long.endswith("(2026-01-02)")


def test_folder_name_is_saved_and_clashes_get_a_number(db_session, folder_on):
    job = _job(db_session, company="Acme", title="Data Scientist")
    first, second = _application(db_session, job), _application(db_session, job)
    name_one = rf._folder_for(db_session, first, job)
    name_two = rf._folder_for(db_session, second, job)
    assert name_two == f"{name_one} (2)"
    job.title = "Renamed"
    db_session.commit()
    assert rf._folder_for(db_session, first, job) == name_one  # stable after a title edit
```

Put the helpers `_job(db, company, title)` and `_application(db, job, pdf=None)` at the top of the test
file, modelled on `tests/test_applications_router.py:_job`. `raw_text_hash` must be unique per job, so
use `uuid4().hex`. `_job` takes keyword-only `company="Acme"` and `title="Data Scientist"` defaults.
Task 4 calls it bare, and Task 6 passes `company=` only. `_application(..., pdf=path)` writes
`pdf_path` when given.

**Step 2:** Run `python3 -m pytest tests/test_resumes_folder.py -k "label or saved" -v`. Expected: FAIL.

**Step 3: Implement**

```python
import re
import unicodedata
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.application import Application
from app.models.job import Job

_ILLEGAL = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')
_COMPANY_MAX, _TITLE_MAX = 35, 48


def _clean(text: str | None, limit: int) -> str:
    text = _ILLEGAL.sub(" ", unicodedata.normalize("NFC", text or ""))
    text = re.sub(r"\s+", " ", text).strip()[:limit].rstrip(". ")
    return text or "Untitled"


def folder_label(company: str | None, title: str | None, day: date) -> str:
    return f"{_clean(company, _COMPANY_MAX)} – {_clean(title, _TITLE_MAX)} ({day.isoformat()})"


def _folder_for(db: Session, application: Application, job: Job) -> str:
    """The application's folder name: chosen once, saved, then never recomputed."""
    key = APP_KEY_PREFIX + application.id.hex
    saved = _get(db, key)
    if saved:
        return saved
    base = folder_label(job.company, job.title, application.created_at.astimezone().date())
    taken = {
        value.casefold()  # APFS and NTFS ignore case
        for value in db.scalars(
            select(Setting.value).where(Setting.key.startswith(APP_KEY_PREFIX, autoescape=True))
        )
        if value
    }
    name, n = base, 2
    while name.casefold() in taken:
        name, n = f"{base} ({n})", n + 1
    db.add(Setting(key=key, value=name))
    try:
        db.commit()
    except IntegrityError:  # a concurrent first copy of the same application won the insert
        db.rollback()
        return _get(db, key) or name
    return name
```

`autoescape=True` matters: `_` in `resumes_folder.` is a LIKE wildcard.

**Step 4:** Run the same command. Expected: PASS. Hand-check one more case in a REPL:
`folder_label("Take-Two Interactive Software, Inc.", "Data Engineer", date(2026,10,9))` should give
`'Take-Two Interactive Software, Inc – Data Engineer (2026-10-09)'`. The trailing `.` is stripped by
design, since Windows refuses trailing dots.

**Step 5: Commit** `feat(resumes-folder): stable OS-safe folder names`.

---

### Task 4: The copy engine

**Files:** Modify `backend/app/services/resumes_folder.py`. Test: `tests/test_resumes_folder.py`.

**Step 1: Failing tests**

```python
import builtins
import os

from tests.pdf_fixtures import write_blank_pdf
from app.models.qa_entry import QAEntry


def _pdf(tmp_path, name="Riley_Quill_DataScientist_Resume.pdf"):
    path = tmp_path / "work" / "Acme_DS_20261011_abcd1234" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    write_blank_pdf(path, 1)
    return path


def test_copies_resume_and_cover_letter_pdfs_only(db_session, folder_on, tmp_path):
    resume = _pdf(tmp_path)
    (resume.parent / "Riley_Quill_DataScientist_Resume.log").write_text("x")
    letter = _pdf(tmp_path, "Riley_Quill_DataScientist_CoverLetter.pdf")
    app = _application(db_session, _job(db_session, company="Acme", title="Data Scientist"), pdf=resume)
    db_session.add(QAEntry(application_id=app.id, kind="cover_letter", answer="Hi", pdf_path=str(letter)))
    db_session.commit()

    copied = rf.copy_application(app.id)

    assert copied is not None and copied.read_bytes() == resume.read_bytes()
    assert sorted(p.name for p in copied.parent.iterdir()) == [letter.name, resume.name]
    assert copied.parent.parent == folder_on


def test_second_copy_is_a_no_op(db_session, folder_on, tmp_path):
    app = _application(db_session, _job(db_session), pdf=_pdf(tmp_path))
    first = rf.copy_application(app.id)
    before = first.stat().st_mtime_ns
    assert rf.copy_application(app.id) == first
    assert first.stat().st_mtime_ns == before
    assert not [p for p in first.parent.iterdir() if p.name.startswith(".maestro-")]


def test_never_reads_the_copy(db_session, folder_on, tmp_path, monkeypatch):
    app = _application(db_session, _job(db_session), pdf=_pdf(tmp_path))
    first = rf.copy_application(app.id)
    os.utime(first, ns=(0, 0))  # force a rewrite on the next call
    real_open = builtins.open

    def guarded(file, *args, **kwargs):
        assert Path(file) != first, "the copy must never be opened for reading"
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded)
    assert rf.copy_application(app.id) == first


def test_failure_is_recorded_not_raised(db_session, folder_on, tmp_path, monkeypatch):
    app = _application(db_session, _job(db_session), pdf=_pdf(tmp_path))
    monkeypatch.setattr(rf.shutil, "copyfile", lambda *a, **k: (_ for _ in ()).throw(PermissionError("no")))
    assert rf.copy_application(app.id) is None
    db_session.expire_all()
    assert rf.last_error(db_session)["code"] == "write"
    monkeypatch.undo()  # undoes folder_on too, so re-point it
    monkeypatch.setattr(app_settings, "resumes_folder_default", folder_on)
    assert rf.copy_application(app.id) is not None
    db_session.expire_all()
    assert rf.last_error(db_session) is None  # a success clears it


def test_vanished_custom_root_is_not_recreated(db_session, folder_on, tmp_path):
    custom = tmp_path / "usb"
    db_session.add(Setting(key=rf.PATH_KEY, value=str(custom)))
    db_session.commit()
    app = _application(db_session, _job(db_session), pdf=_pdf(tmp_path))
    assert rf.copy_application(app.id) is None
    assert not custom.exists()
    db_session.expire_all()
    assert rf.last_error(db_session)["code"] == "missing"


def test_off_and_unavailable_copy_nothing(db_session, folder_on, tmp_path, monkeypatch):
    app = _application(db_session, _job(db_session), pdf=_pdf(tmp_path))
    db_session.add(Setting(key=rf.ENABLED_KEY, value="off"))
    db_session.commit()
    assert rf.copy_application(app.id) is None
    assert not folder_on.exists()
    monkeypatch.setattr(app_settings, "resumes_folder_default", None)
    assert rf.copy_application(app.id) is None


def test_no_pdf_means_no_folder(db_session, folder_on):
    app = _application(db_session, _job(db_session))
    assert rf.copy_application(app.id) is None
    assert not folder_on.exists()
```

If `monkeypatch.undo()` proves awkward, use a second fixture or `monkeypatch.context()` instead. That
is a how-change; log it.

**Step 2:** Run `python3 -m pytest tests/test_resumes_folder.py -v`. Expected: the new tests FAIL.

**Step 3: Implement**

```python
import json
import logging
import os
import shutil
import tempfile
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.db import SessionLocal
from app.models.qa_entry import QAEntry

logger = logging.getLogger(__name__)


class CopyError(OSError):
    def __init__(self, code: str, path: Path):
        super().__init__(f"{code}: {path}")
        self.code, self.path = code, path


def last_error(db: Session) -> dict | None:
    raw = _get(db, LAST_ERROR_KEY)
    return json.loads(raw) if raw else None


def _record_error(exc: Exception) -> None:
    try:
        with SessionLocal() as db:
            path = getattr(exc, "path", None) or root(db)
            _put(db, LAST_ERROR_KEY, json.dumps({
                "code": getattr(exc, "code", "write"),
                "path": str(path) if path else None,
                "at": datetime.now(UTC).isoformat(),
            }))
            db.commit()
    except SQLAlchemyError:
        logger.warning("Couldn't record the resumes folder error", exc_info=True)


def _ready_root(db: Session) -> Path | None:
    base = root(db)
    if base is None:  # became unavailable mid-call (the sync key appeared)
        return None
    if is_custom(db):
        if not base.is_dir():  # an unplugged drive: never write into an empty mount point
            raise CopyError("missing", base)
    else:
        base.mkdir(parents=True, exist_ok=True)
    return base


def _same(src: os.stat_result, target: Path) -> bool:
    try:
        st = target.stat()  # stat only: an iCloud/OneDrive placeholder stats without downloading
    except FileNotFoundError:
        return False
    return st.st_size == src.st_size and abs(st.st_mtime - src.st_mtime) < 2


def _place(src: Path, folder: Path) -> Path:
    target = folder / src.name
    if _same(src.stat(), target):
        return target
    fd, tmp = tempfile.mkstemp(prefix=".maestro-", suffix=".tmp", dir=folder)
    os.close(fd)
    try:
        shutil.copyfile(src, tmp)
        shutil.copystat(src, tmp)  # the mtime is what _same compares next time
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return target


def _sources(db: Session, application: Application) -> tuple[Path | None, list[Path]]:
    resume = Path(application.pdf_path) if application.pdf_path else None
    resume = resume if resume is not None and resume.is_file() else None
    letters = [
        Path(raw)
        for raw in db.scalars(
            select(QAEntry.pdf_path).where(
                QAEntry.application_id == application.id,
                QAEntry.kind == "cover_letter",
                QAEntry.pdf_path.is_not(None),
            )
        )
        if raw and Path(raw).is_file()
    ]
    return resume, [p for p in (resume, *letters) if p is not None]


def _attempt(application_id: UUID) -> tuple[bool, Path | None]:
    """(ok, resume copy). ok is False only when a copy was due and failed."""
    if availability() is not None:
        return True, None
    try:
        with SessionLocal() as db:
            if not enabled(db):
                return True, None
            application = db.get(Application, application_id)
            job = db.get(Job, application.job_id) if application is not None else None
            if job is None:
                return True, None
            resume, files = _sources(db, application)
            if not files:
                return True, None
            base = _ready_root(db)
            if base is None:
                return True, None
            folder = base / _folder_for(db, application, job)
            folder.mkdir(exist_ok=True)
            placed = {src: _place(src, folder) for src in files}
            if _get(db, LAST_ERROR_KEY) is not None:
                _put(db, LAST_ERROR_KEY, None)
                db.commit()
            return True, placed.get(resume) if resume is not None else None
    except Exception as exc:  # noqa: BLE001 — never raise into the render (planning decision 8)
        logger.warning("Couldn't save a copy of application %s to the resumes folder",
                       application_id, exc_info=True)
        _record_error(exc)
        return False, None


def copy_application(application_id: UUID) -> Path | None:
    """Copy this application's finished PDFs out. Never raises (see the module docstring)."""
    return _attempt(application_id)[1]


def copy_job(job_id: UUID) -> None:
    try:
        with SessionLocal() as db:
            ids = list(db.scalars(select(Application.id).where(Application.job_id == job_id)))
    except Exception:  # noqa: BLE001 — planning decision 8
        logger.warning("Couldn't list job %s for the resumes folder", job_id, exc_info=True)
        return
    for application_id in ids:
        copy_application(application_id)
```

**Step 4:** Run `python3 -m pytest tests/test_resumes_folder.py -v`. Expected: all PASS.

**Step 5: Commit** `feat(resumes-folder): one-way atomic copy engine`.

---

### Task 5: Hook points and `RenderResult.copy_path`

**Files:**
- Modify: `backend/app/services/application_render.py`. After the `db.commit()` at the end of the
  success path (currently line 113), add `resumes_folder.copy_application(application.id)`, and import
  it.
- Modify: `backend/app/services/resumes_folder.py`. Add `copy_path_for(application_id)` (below).
- Modify: `backend/app/schemas/application.py`. In `RenderResult`, add `copy_path: str | None = None`.
- Modify: `backend/app/routers/applications.py`. `render_application` sets
  `copy_path=str(p) if (p := resumes_folder.copy_path_for(application_id)) else None`.
- Modify: `backend/app/routers/qa.py:render_cover_letter`. After `db.refresh(entry)`, add
  `resumes_folder.copy_application(application.id)`.
- Modify: `backend/app/routers/sync.py`.
  - `_store`: after `found["applied"].append(shown)` is reached (the outcome kind is not `"laptop"`),
    call `resumes_folder.copy_job(job_id)`. In the `"both"` case the incoming copy is stored under
    `job_id` (see `duplicates.Outcome`).
  - `_take_back`: after `found["job_ids"].append(shown)`, call `resumes_folder.copy_job(job_id)`.
- Modify: `frontend/lib/types.ts`. In `RenderResult` (~line 615), add `copy_path: string | null;`.
- Modify: `backend/mcp_server/server.py` `render_pdf` docstring. Add one sentence saying the response's
  `copy_path` is where the user can find the PDF in their resumes folder (null when off). Keep within
  the docstring ratchet (`mcp_server/tests/test_server.py`).
- Tests: `tests/test_resumes_folder.py`, plus a sync test (below).

```python
def copy_path_for(application_id: UUID) -> Path | None:
    """Where the resume copy is, without writing anything. None when there is none."""
    if availability() is not None:
        return None
    try:
        with SessionLocal() as db:
            application = db.get(Application, application_id)
            name = _get(db, APP_KEY_PREFIX + application_id.hex)
            base = root(db)
            if not (enabled(db) and application and application.pdf_path and name and base):
                return None
            path = base / name / Path(application.pdf_path).name
            return path if path.exists() else None
    except Exception:  # noqa: BLE001 — planning decision 8
        return None
```

**Step 1: Failing tests**

- **Render endpoint copies.** Copy the setup of
  `tests/test_applications_router.py::test_render_application_generates_files_and_updates_paths`
  (stubbing `load_base_resume`, `render_document` and `compile_pdf`), and use the `folder_on` fixture.
  POST `/render` and assert:
  - `response.json()["copy_path"]` names an existing file under `folder_on`;
  - its bytes equal `pdf_path`'s.
- **Render survives a failed copy.** The same setup with `rf.shutil.copyfile` patched to raise
  `PermissionError`. Assert:
  - status is 200;
  - `copy_path` is None;
  - `rf.last_error(db_session)["code"] == "write"`.
- **Cover-letter render copies.** Model it on `tests/test_qa_router.py`, which has a
  cover-letter-render test (find it with `grep -n "render" tests/test_qa_router.py`). Assert that the
  `..._CoverLetter.pdf` copy exists under `folder_on`.
- **Sync arrival copies.** In `tests/sync/`, find the test that applies a job bundle on the laptop via
  `POST /api/sync/jobs` (`grep -rn '"/api/sync/jobs"' tests/sync | head`). Add a sibling test that
  sets `folder_on` and asserts that the arriving application's PDF is copied. If the helpers make this
  heavy, call `sync._store` directly with a bundle built by that file's helper. Do not hand-build
  bundles.

**Step 2:** Run the new tests. Expected: FAIL.

**Step 3:** Implement the hooks listed above. Each call sits after its commit. `copy_application` and
`copy_job` never raise, so no try/except is needed at the call sites.

**Step 4:** Run the new tests, then the render, QA and sync suites:
`python3 -m pytest tests/test_applications_router.py tests/test_qa_router.py tests/test_quick_tailor.py tests/test_tailoring_sessions_router.py tests/sync/ -q -n auto --dist loadfile`.
Expected: PASS. With `resumes_folder_default` None in tests, every existing test is unaffected.

**Step 5: Commit** `feat(resumes-folder): copy after every PDF render, cover letter and sync arrival`.

---

### Task 6: Backfill (startup and root change)

**Files:**
- Modify: `backend/app/services/resumes_folder.py` (add `backfill`, `start_backfill`)
- Modify: `backend/app/main.py` lifespan, adding `resumes_folder.start_backfill()` after
  `automation_prompts.load_cards()`
- Test: `tests/test_resumes_folder.py`

**Step 1: Failing tests**

```python
def test_backfill_copies_all_once_per_root(db_session, folder_on, tmp_path, monkeypatch):
    apps = [_application(db_session, _job(db_session, company=f"Co{i}"), pdf=_pdf(tmp_path, f"R{i}.pdf"))
            for i in range(3)]
    assert rf.backfill() == 3
    assert len(list(folder_on.iterdir())) == 3
    calls = []
    monkeypatch.setattr(rf, "_attempt", lambda i: calls.append(i) or (True, None))
    assert rf.backfill() == 0 and calls == []  # marker matches the root: nothing to do
    custom = tmp_path / "elsewhere"
    custom.mkdir()
    db_session.add(Setting(key=rf.PATH_KEY, value=str(custom)))
    db_session.commit()
    rf.backfill()
    assert len(calls) == 3  # a new root fills again


def test_backfill_with_a_failure_runs_again_next_time(db_session, folder_on, tmp_path, monkeypatch):
    _application(db_session, _job(db_session), pdf=_pdf(tmp_path))
    monkeypatch.setattr(rf, "_attempt", lambda i: (False, None))
    rf.backfill()
    db_session.expire_all()
    assert rf._get(db_session, rf.BACKFILLED_KEY) is None


def test_start_backfill_is_a_no_op_when_unavailable(monkeypatch):
    monkeypatch.setattr(app_settings, "resumes_folder_default", None)
    assert rf.start_backfill() is None
```

**Step 2:** Run them. Expected: FAIL.

**Step 3: Implement**

```python
import threading

_BACKFILL = threading.Lock()


def backfill() -> int | None:
    """Copy every application's PDFs once per root. Returns how many resumes were copied."""
    if availability() is not None or not _BACKFILL.acquire(blocking=False):
        return None
    try:
        with SessionLocal() as db:
            if not enabled(db):
                return None
            target = str(root(db))
            if _get(db, BACKFILLED_KEY) == target:
                return 0
            ids = list(db.scalars(select(Application.id).where(Application.pdf_path.is_not(None))))
        results = [_attempt(application_id) for application_id in ids]
        if all(ok for ok, _ in results):
            with SessionLocal() as db:
                _put(db, BACKFILLED_KEY, target)
                db.commit()
        return sum(1 for _, path in results if path is not None)
    except SQLAlchemyError:
        logger.warning("The resumes folder backfill stopped", exc_info=True)
        return None
    finally:
        _BACKFILL.release()


def start_backfill() -> threading.Thread | None:
    if availability() is not None:
        return None
    thread = threading.Thread(target=backfill, name="resumes-folder-backfill", daemon=True)
    thread.start()
    return thread
```

In `main.py` lifespan, import `from app.services import resumes_folder` and call
`resumes_folder.start_backfill()` after `automation_prompts.load_cards()`.

**Step 4:** Run `python3 -m pytest tests/test_resumes_folder.py -v`, then `tests/test_main*.py` or
whichever file exercises the lifespan (`grep -rln "lifespan" tests | head`). Expected: PASS.

**Step 5: Commit** `feat(resumes-folder): backfill existing applications once per root`.

---

### Task 7: Opener, reveal endpoints and the settings API

**Files:**
- Create: `backend/app/services/folder_opener.py`
- Create: `backend/app/schemas/resumes_folder.py`
- Create: `backend/app/routers/resumes_folder.py`
- Modify: `backend/app/main.py` (`app.include_router(resumes_folder.router)` next to the other routers)
- Modify: `backend/app/services/resumes_folder.py` (add `validate_folder`, `display`, `platform_name`,
  `FolderRefused`, `ERROR_SENTENCES`)
- Tests: `backend/tests/test_folder_opener.py`, `backend/tests/test_resumes_folder_router.py`

**folder_opener.py:**

```python
"""Open a folder (or select a file) in the OS file manager, on the machine the user sits at.

Callers pass only paths Maestro computed. Nothing here accepts a path from a request.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


class OpenerUnavailable(Exception):
    """The OS file manager could not be started."""


def command(target: Path, *, select: bool, platform: str = sys.platform) -> list[str] | str:
    if platform == "darwin":
        return ["open", "-R", str(target)] if select else ["open", str(target)]
    if platform == "win32":
        # A string, not a list: list2cmdline would quote "/select,<path>" whole and Explorer ignores it.
        return f'explorer /select,"{target}"' if select else f'explorer "{target}"'
    return ["xdg-open", str(target.parent if select else target)]  # xdg-open cannot select a file


def _launch(cmd: list[str] | str) -> None:
    kwargs: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(cmd, **kwargs)  # noqa: S603 — fixed program, computed path, no shell
    except OSError as exc:
        raise OpenerUnavailable(str(cmd)) from exc


def reveal(file: Path) -> None:
    _launch(command(file, select=True))


def open_folder(folder: Path) -> None:
    _launch(command(folder, select=False))
```

**Additions to `resumes_folder.py`:**

```python
import sys

ERROR_SENTENCES = {
    "missing": "Maestro couldn't find this folder. Reconnect the drive or choose another folder.",
    "write": "Maestro couldn't save the last copy to this folder. Check that it still exists and that you can write to it.",
}


class FolderRefused(ValueError):
    """A custom folder the user can't use, phrased for them."""


def platform_name(platform: str = sys.platform) -> str:
    return {"darwin": "macos", "win32": "windows"}.get(platform, "linux")


def display(path: Path, home: Path | None = None) -> str:
    try:
        parts = path.relative_to(home or Path.home()).parts
    except ValueError:
        return str(path)
    return " › ".join(parts) if parts else str(path)


def validate_folder(raw: str) -> Path:
    path = Path(raw.strip()).expanduser()
    if not raw.strip() or not path.is_absolute():
        raise FolderRefused("Enter the folder's full path.")
    path = path.resolve()
    if not path.is_dir():
        raise FolderRefused("That folder doesn't exist. Create it first, then try again.")
    own = [app_settings.applications_dir, os.environ.get("MAESTRO_HOME")]
    if any(o and path.is_relative_to(Path(o).resolve()) for o in own):
        raise FolderRefused("Choose a folder outside Maestro's own data folder.")
    try:
        fd, probe = tempfile.mkstemp(prefix=".maestro-probe-", dir=path)
        os.close(fd)
        os.unlink(probe)
    except OSError:
        raise FolderRefused("Maestro can't save files in that folder. Choose another one.") from None
    return path
```

**schemas/resumes_folder.py:**

```python
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ResumesFolderError(BaseModel):
    message: str
    at: datetime


class ResumesFolderSettings(BaseModel):
    available: bool
    reason: Literal["not_here", "always_on_copy"] | None
    enabled: bool
    folder: str | None
    folder_display: str | None
    default_folder: str | None
    custom: bool
    platform: Literal["macos", "windows", "linux"]
    picker: bool
    last_error: ResumesFolderError | None


class ResumesFolderUpdate(BaseModel):
    """Absent = unchanged. folder: null = back to the default."""
    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    folder: str | None = None


class ShownCopy(BaseModel):
    path: str
```

**routers/resumes_folder.py:**

```python
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.application import Application
from app.schemas.resumes_folder import (ResumesFolderError, ResumesFolderSettings,
                                        ResumesFolderUpdate, ShownCopy)
from app.services import folder_opener, resumes_folder as rf

router = APIRouter(tags=["resumes-folder"])

UNAVAILABLE = "This copy of Maestro keeps no resumes folder."
OFF = "Saving copies is turned off in Settings › Tailoring."
NO_PDF = "Create the PDF first."
NOT_SAVED = "Maestro couldn't save a copy to your resumes folder. Check Settings › Tailoring."
CANT_OPEN = "Maestro couldn't open the folder on this computer."  # no path: decision 9


def _state(db: Session) -> ResumesFolderSettings:
    reason = rf.availability()
    base = rf.root(db)
    err = rf.last_error(db)
    default = rf.app_settings.resumes_folder_default
    return ResumesFolderSettings(
        available=reason is None, reason=reason, enabled=rf.enabled(db),
        folder=str(base) if base else None,
        folder_display=rf.display(base) if base else None,
        default_folder=str(default) if default else None,
        custom=rf.is_custom(db), platform=rf.platform_name(),
        picker=False,  # Task 10 turns this on where a picker exists
        last_error=ResumesFolderError(message=rf.ERROR_SENTENCES.get(err["code"], rf.ERROR_SENTENCES["write"]),
                                      at=err["at"]) if err else None,
    )


def _require_available() -> None:
    if rf.availability() is not None:
        raise HTTPException(409, detail=UNAVAILABLE)


@router.get("/api/settings/resumes-folder", response_model=ResumesFolderSettings)
def get_resumes_folder(db: Annotated[Session, Depends(get_db)]):
    return _state(db)


@router.put("/api/settings/resumes-folder", response_model=ResumesFolderSettings,
            dependencies=[Depends(_require_available)])
def put_resumes_folder(payload: ResumesFolderUpdate, db: Annotated[Session, Depends(get_db)]):
    before = (rf.enabled(db), rf.root(db))
    if "folder" in payload.model_fields_set:
        try:
            chosen = rf.validate_folder(payload.folder) if payload.folder is not None else None
        except rf.FolderRefused as exc:
            raise HTTPException(422, detail=str(exc)) from None
        rf._put(db, rf.PATH_KEY, str(chosen) if chosen else None)
    if payload.enabled is not None:
        rf._put(db, rf.ENABLED_KEY, None if payload.enabled else "off")
    rf._put(db, rf.LAST_ERROR_KEY, None)  # a new choice starts with a clean slate
    db.commit()
    if rf.enabled(db) and (rf.enabled(db), rf.root(db)) != before:
        rf.start_backfill()
    return _state(db)


@router.post("/api/settings/resumes-folder/open", status_code=204,
             dependencies=[Depends(_require_available)])
def open_resumes_folder(db: Annotated[Session, Depends(get_db)]):
    base = rf.root(db)
    if not rf.is_custom(db):
        base.mkdir(parents=True, exist_ok=True)
    try:
        folder_opener.open_folder(base)
    except folder_opener.OpenerUnavailable:
        raise HTTPException(409, detail=CANT_OPEN) from None


@router.post("/api/applications/{application_id}/show-copy", response_model=ShownCopy,
             dependencies=[Depends(_require_available)])
def show_copy(application_id: UUID, db: Annotated[Session, Depends(get_db)]):
    application = db.get(Application, application_id)
    if application is None:
        raise HTTPException(404, detail="Application not found")
    if not rf.enabled(db):
        raise HTTPException(409, detail=OFF)
    if not application.pdf_path:
        raise HTTPException(409, detail=NO_PDF)
    path = rf.copy_application(application_id)  # heals a copy missed for any reason
    if path is None:
        raise HTTPException(409, detail=NOT_SAVED)
    try:
        folder_opener.reveal(path)
    except folder_opener.OpenerUnavailable:
        raise HTTPException(409, detail=CANT_OPEN) from None
    return ShownCopy(path=str(path))
```

Rename `rf._put` and `rf._get` to public `put_setting` and `get_setting` if the reviewer objects to
cross-module privates. Either is fine; log the choice.

**Step 1: Failing tests.** In `test_folder_opener.py`:
- `command()` on all three platforms, for a file and a folder;
- `reveal()` with `subprocess.Popen` monkeypatched to record its args (no shell, DEVNULL, close_fds);
- `Popen` raising `FileNotFoundError` gives `OpenerUnavailable`.

In `test_resumes_folder_router.py`, using `client`, `db_session` and the `folder_on` fixture (move
`folder_on`, `_job`, `_application` and `_pdf` into `tests/resumes_folder_fixtures.py` and import them
in both files):
- `GET` when unavailable gives `available false` and `reason "not_here"`. When available it gives
  `folder_display` `"…Documents › Maestro Career Studio › Applications"`. Monkeypatch `Path.home` to
  `tmp_path` to make this exact.
- `PUT {"enabled": false}` then `GET` gives `enabled false`, and the row is `"off"`.
- `PUT {"folder": "relative"}` gives 422 with "Enter the folder's full path."
- `PUT {"folder": str(tmp_path / "missing")}` gives 422.
- `PUT {"folder": str(work_dir / "x")}` (inside `applications_dir`) gives 422. `mkdir` it first;
  otherwise the "doesn't exist" branch answers and the test passes for the wrong reason.
- **Monkeypatch `rf.start_backfill` in every PUT test** (an autouse fixture in the router test file). A
  real daemon thread writing through `SessionLocal` races the `db_session` teardown, and conftest
  documents a 30 s "database is locked" stall.
- `PUT {"folder": str(ok_dir)}` gives 200 with `custom true`, and `start_backfill` was called
  (monkeypatch it to record).
- `PUT {"folder": null}` gives `custom false`.
- `PUT`, `POST /open` and `POST /show-copy` give 409 when `resumes_folder_default` is None, and also
  when `is_remote` returns True.
- `show-copy` with a PDF calls `folder_opener.reveal` with the copy's path (monkeypatched) and returns
  it. With no PDF it gives 409 `NO_PDF`. When off it gives 409 `OFF`.
- `show-copy` where `reveal` raises `OpenerUnavailable` gives 409 `CANT_OPEN`.
- Every 409 and 422 sentence passes the frontend's `isPlainSentence` rule (copy the regex into the test).

**Step 2:** Run both files. Expected: FAIL.

**Step 3:** Implement the code above.

**Step 4:** Run both files plus `tests/test_security_boundaries.py`. That file may enumerate routes or
Origin rules; if it lists every POST route, add the new ones there. Expected: PASS.

**Step 5: Commit** `feat(resumes-folder): settings API, open folder and show-copy endpoints`.

---

### Task 8: Frontend — shared pieces and the Settings card

**Files:**
- Modify: `frontend/lib/types.ts`, adding `ResumesFolderSettings` that mirrors the schema.
- Create: `frontend/lib/resumes-folder.ts`
- Create: `frontend/components/settings/resumes-folder-section.tsx`
- Modify: `frontend/lib/settings-tabs.ts`, so the tailoring anchors are `["quick-tailor", "resumes-folder"]`
- Modify: `frontend/lib/settings-tabs.test.ts`, if it pins the tailoring anchors
- Modify: `frontend/app/settings/page.tsx`, so `tailoring:` becomes
  `<><QuickTailorSection /><ResumesFolderSection /></>`

**`lib/resumes-folder.ts`:**

```ts
import type { ResumesFolderSettings } from "@/lib/types";

export const RESUMES_FOLDER_KEY = ["settings", "resumes-folder"] as const;
export const RESUMES_FOLDER_URL = "/api/settings/resumes-folder";

/** The OS file manager's name, for "Show in …" buttons. */
export function revealLabel(platform: ResumesFolderSettings["platform"]): string {
  if (platform === "macos") return "Show in Finder";
  if (platform === "windows") return "Show in File Explorer";
  return "Show in folder";
}

/** True when this machine saves copies and the setting is on. */
export function copiesOn(data: ResumesFolderSettings | undefined): boolean {
  return !!data && data.available && data.enabled;
}
```

**The Settings card** follows `mcp-workflow-section.tsx` (SettingCard, SwitchRow, AutosaveStatus, a PUT
mutation with `qc.setQueryData`). Content:

- **Title** "Resumes folder". **Description** (one OS-neutral string): "Maestro saves a copy of each
  finished resume PDF here, so you can find it on your computer and in upload dialogs."
- **Unavailable** (`!data.available`): one sentence and nothing else.
  - `reason === "not_here"`: "Your resumes are already in the applications folder of your Maestro
    checkout."
  - The always-on copy: "This copy keeps no resumes folder."
- **SwitchRow** "Save a copy of each resume", bound to `enabled`.
- **The folder row** (shown when enabled):
  - a `<code>`-free, plain `text-body-medium` line showing `folder_display`;
  - **Open folder**, an outline button that POSTs `/api/settings/resumes-folder/open`, with errors
    via `couldnt("open the folder", e)`;
  - **Change folder**, which reveals an inline `<Input>` (label "Folder path") with **Save** and
    **Cancel**. Save PUTs `{folder}`. A 422 detail shows through `couldnt("use that folder", e)`;
    `errorDetail` passes plain sentences;
  - **Use the default**, a ghost button shown only when `custom`, which PUTs `{folder: null}`.
- **`last_error`:** a small destructive-tone line showing `last_error.message`.
- **On success** of a folder change: `toast.success("Resumes folder changed")`. The backfill runs
  server-side.

**Pinned card rules.** `tests/test_frontend_settings_cards.py::test_settings_cards_share_one_rhythm`
globs every `components/settings/*.tsx`. It requires:
- no `className="…m[tby]-N"`;
- no `sm|md|lg:grid-cols-`;
- no `border-t`;
- `space-y-*` only inside `<fieldset>`;
- no `<Label className="text-xs|sm">`.

`SwitchRow` comes from `components/settings/setting-layout.tsx`. The inline path field follows R8:
`className={ACTION_ROW}`, Save last and `focusableWhenDisabled`, and
`useLeaveGuard(draft !== null)` while a path is typed. Never show a bare literal "Applications"
(`test_frontend_vocabulary.py` bans `^Applications$`), and never write "Finder/File Explorer". Add
`resumesFolder: FolderOpen` to `lib/concept-icons.ts` and use it in both components (one concept, one
glyph).

Use `useId()` for the input id. Keep the component under ~150 lines, and pull the folder row into a
small inner component if it grows.

**Steps:**
1. Write the types and `lib/resumes-folder.ts`.
2. Write the section.
3. Wire up the tabs and page.
4. Run `cd frontend && npx tsc --noEmit && npm run lint`, then
   `node --test lib/settings-tabs.test.ts`. CI does not run it, so run it locally. CI pins the card
   placement through `tests/test_frontend_settings_pages.py`. Then, from `backend/`, run
   `python3 -m pytest tests/test_frontend_settings_pages.py tests/ -k "frontend" -q -n auto --dist loadfile`.
   That covers the vocabulary, sentence-length and error-words ratchets.
5. Commit `feat(resumes-folder): Settings › Tailoring › Resumes folder card`.

---

### Task 9: Frontend — Show in Finder and the one-time note

**Files:**
- Create: `frontend/components/resumes-folder-actions.tsx`, exporting `useResumesFolder()`,
  `ShowCopyButton` and `ResumesFolderNote`
- Modify: `frontend/components/application-panel.tsx` `OutputTab`. Add `<ShowCopyButton applicationId
  variant="outline" disabled={!pdfReady} />` after "Open in new tab", and
  `<ResumesFolderNote applicationId />` under the button row (shown only when `pdfReady`).
- Modify: `frontend/components/resume-editor/tailored-resume-studio.tsx` preview header. Add
  `<ShowCopyButton applicationId={applicationId} variant="ghost" size="icon-sm" iconOnly
  disabled={!application.pdf_path} />` after the Open button. Update the comment above it to "the PDF's
  own controls (download, open, show in folder)".

**`resumes-folder-actions.tsx` behaviour:**

- **`useResumesFolder()`** is `useQuery({queryKey: RESUMES_FOLDER_KEY, queryFn: () =>
  apiFetch<ResumesFolderSettings>(RESUMES_FOLDER_URL), staleTime: 60_000})`.
- **`ShowCopyButton`** renders nothing unless `copiesOn(data)`.
  - It shows `<FolderOpen>` plus `revealLabel(data.platform)`. In icon-only mode the label goes in
    `aria-label`, plus a tooltip if the studio header uses tooltips for its other icons. Match them.
  - It POSTs `/api/applications/${id}/show-copy` through `useMutation` + `useSingleFlight`, with errors
    via `toast.error(couldnt("open the folder", e))`.
- **`ResumesFolderNote`** renders nothing unless `copiesOn(data)`, the note has not been dismissed, and
  the parent passes `pdfReady`.
  - The dismiss flag uses `useLocalStorageState("maestro-cs:resumes-folder-note-dismissed", parseFlag,
    serializeFlag)` (from `hooks/use-local-storage-state.ts`).
  - Text: `A copy is saved in ${data.folder_display}.`
  - Then a `ShowCopyButton` (link-sized), a **Change folder** link to Settings › Tailoring
    (a `GuardedLink` from `components/guarded-link.tsx` to `anchorHref("/settings", "resumes-folder")`,
    `lib/settings-tabs.ts:90`. Never use `next/link`, which `test_frontend_leave_guard.py` pins, and never
    `Button nativeButton={false} render={<a>}`, SYSTEM.md §11 item 29), and a dismiss icon button (`aria-label="Dismiss"`).
  - It uses the same quiet surface as other inline notes in `application-panel.tsx`. Check for an
    existing info-note style before inventing one.

**Steps:**
1. Write the component file.
2. Wire it into `OutputTab` and the studio.
3. Run `npx tsc --noEmit && npm run lint && npm run build` and the backend `frontend` ratchet tests.
   Expected: PASS.
4. Commit `feat(resumes-folder): Show in Finder on the PDF card and a one-time note`.

---

### Task 10: Native folder picker (gated)

**Step 0, the gate spike.** The main session runs this with the owner, because it shows a dialog on the
owner's screen.
1. On a scratch uv-style stack started detached (`nohup ... &`, so no TTY), run from a Python child:
   `subprocess.run(["osascript", "-e", 'POSIX path of (choose folder with prompt "Test")'],
   capture_output=True, text=True, timeout=300)`.
2. The owner answers two questions:
   - did the dialog appear in front of the browser?
   - did macOS ask for any Automation or Accessibility permission?
3. If the dialog appears in front and asks for nothing, the gate passes and the task goes ahead.
   Otherwise, try wrapping it once in `tell application "System Events" to activate` only if that asks
   for nothing. If neither works, **stop**: drop this task, leave `picker` false, and record the result
   in KNOWN_ISSUES and in the design's Follow-ups.

**If the gate passes:**

- **Files:** create `backend/app/services/folder_picker.py`. Modify the router (`POST
  /api/settings/resumes-folder/choose`, and `picker=folder_picker.available()` in `_state`) and the
  Settings card (a **Choose…** button beside the path field).
- **`folder_picker.choose(start: Path) -> Path | None`** runs under a module `threading.Lock`
  (non-blocking acquire, 409 "A folder dialog is already open." when held) with `timeout=300`.
  - macOS: `osascript`, with the default location from `start`. Escape `\` and `"` in the path before
    putting it in the AppleScript string. Return code 1 with `-128` in stderr means cancel, so return
    None.
  - Windows: `powershell -NoProfile -STA -Command` with a `FolderBrowserDialog` script. Print
    `SelectedPath` on OK and nothing on cancel. This is an owner check (G-W).
  - Linux: `zenity --file-selection --directory --filename=<start>/` when `shutil.which("zenity")`.
    Return code 1 means cancel.
- **`available()`** is darwin, win32, or linux with zenity on `PATH`.
- **The endpoint** returns `{folder: str | null}`. The UI then fills the path field with the result.
  It does not save on its own, so the user still presses Save, which runs `validate_folder`.
- **Tests** mock `subprocess.run` for the argv per OS, the cancel mapping, path escaping, and the lock
  giving 409. Commit `feat(resumes-folder): native folder picker where the OS has one`.

---

### Task 11: Docs, SYSTEM.md and the gate

**Files:**

- **First, groom SYSTEM.md** (Fable verify, blocker 1). `scripts/check_system_md.py` reports 992 of
  1,000 lines, and §6 is about 246 of its 250-line budget. Free at least 10 lines before adding
  anything: integrate or trim §12 (88 lines against a 69-line baseline) or §2 (46 against 36), or
  extract reference to `docs/entities/`. The header forbids raising the cap without a failed grooming
  pass. Keep the new §6 bullet to 3 lines or fewer, and the §5 and §7 additions to one clause each.
  Run the gate before committing.
- **`SYSTEM.md`:**
  - §5 step 7 (Output): one sentence on the visible copy and Show in Finder.
  - §6: a new bullet `**Resumes folder copies go one way.** {#inv-resumes-folder-one-way}`. It covers:
    the copy happens after the commit; the copy is never read back and never deleted; copy failures
    never fail the caller; the folder is machine-local (`resumes_folder.` in
    `LOCAL_SETTING_PREFIXES`); it is off without `RESUMES_FOLDER_DEFAULT` and on the always-on copy.
    It is pinned by `tests/test_resumes_folder.py`.
  - §7, at the MCP `render_pdf` mention: `copy_path`.
  - §2, if it lists services or routers individually.
  - Follow the header contract: rewrite in present tense, no dated prose.
- **`.system_md_enforcement.json`:** pin `inv-resumes-folder-one-way` to
  `backend/tests/test_resumes_folder.py` plus a symbol (`test_never_reads_the_copy`). Run
  `python3 scripts/check_system_md.py` from the repo root.
- **`docs/entities/others.md`:** the `resumes_folder.*` keys, the endpoints and the UI path, in that
  file's settings-feature format (around lines 207-236).
- **`README.md` "Where your files live":** one sentence after the table: "Finished resume PDFs are also
  copied to `Documents/Maestro Career Studio/Applications`, one folder per job (Settings › Tailoring ›
  Resumes folder)."
- **`docs/USING_MAESTRO.md`:** a short "Your resumes folder" subsection near "Leave with everything".
  It covers what is copied, where, the folder naming, the replace-on-re-render and never-delete rules,
  Show in Finder, how to change or turn it off, and that Docker keeps `applications/` instead.
- **`docs/SETUP_REFERENCE.md`:** `RESUMES_FOLDER_DEFAULT` (set by `maestro`, overridable in
  `maestro.env`). Docker is unchanged.
- **`KNOWN_ISSUES.md`:**
  - not on Docker or the always-on copy;
  - Windows reveal and picker are unverified on a real desktop (if still true);
  - the picker's state, if Task 10 was dropped.
- **`CHANGELOG.md` `[Unreleased] › Added`:** apply the `ajey-writing-voice` skill to the opening
  sentence only (memory: owner voice on reader-facing copy), then a plain bold-lead entry naming
  Settings › Tailoring › Resumes folder.
- **Slop ratchet:** run `python3 ~/.claude/skills/ai-slop-detector/scripts/slop_scan.py check backend`
  and `... check frontend` from the repo root. If `complexity_hotspots` rose only because of the new
  tests, re-baseline (`baseline <surface>`) and state the reason in the commit.
- **Commit** `docs(resumes-folder): SYSTEM.md invariant, user docs, changelog`.

---

### Task 12: Verification (main session)

1. **Full backend suite:** from `backend/`, run
   `python3 -m pytest tests/ mcp_server/tests/ -q -n auto --dist loadfile`. Expected: all pass, except
   the known `tests/sync/test_two_machines.py` replay flake. If it fails, re-run it alone before
   calling it the flake.
2. **Frontend:** `npx tsc --noEmit`, `npm run lint` and `npm run build`, plus the node tests that CI runs.
3. **Scratch stack with a browser check.** See memory "worktree-verification-recipe" and SYSTEM.md §9.
   - Use uvicorn on a spare port with `MAESTRO_INSTALL=uv`, every `*_DIR` under the scratchpad and
     `RESUMES_FOLDER_DEFAULT=<scratch>/Documents/Maestro Career Studio/Applications`, plus
     `next dev` on a spare port.
   - Seed one job, tailor it, then Create PDF. Then check:
     - the note appears with the right `folder_display`;
     - the copy exists on disk;
     - **Show in Finder** opens Finder at the copy (a real window on the owner's Mac);
     - Settings › Tailoring shows the card;
     - turning it off and re-rendering creates no new copy;
     - changing to another scratch folder backfills it;
     - **Use the default** works;
     - a vanished custom folder shows the `last_error` sentence.
4. **Final whole-branch review:** an Opus 5.5 reviewer reads the full diff against the Goal Card, not
   against the plan.
5. **Owner checks**, recorded and not blocking the merge:
   - **G-M:** on the live app after the next `maestro update`, the backfill fills `~/Documents/Maestro
     Career Studio/Applications` with the 160 existing jobs. Spot-check ComEd and Numerator.
   - **G-W:** on a Windows machine, Show in File Explorer selects the file, and `documents_dir` honours
     a OneDrive Documents folder.
6. **Stop and hand back to the owner** for merge and push decisions. Do not push, and do not update the
   live app.

## Follow-ups (do not build here)

- **Tidy closed jobs:** prune `.pages/` and `.log/.aux/.out` for closed applications (the owner's idea,
  2026-10-11).
- **Companion panel:** a "Saved to …" line with a reveal action.
- **Electron:** `dialog.showOpenDialog` replaces Task 10.
- **Q&A unlinks cover-letter PDFs before the commit** (`routers/qa.py` PATCH/DELETE,
  `services/qa.py` regenerate). It is already offered as a separate task.
- **The Typst path writes the PDF non-atomically** (`pdf_render.py:563`, `typst_compiler.py:243`).

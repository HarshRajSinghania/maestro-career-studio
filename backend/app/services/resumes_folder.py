"""Visible copies of finished resumes. SYSTEM.md {#inv-resumes-folder-one-way}.

The hidden per-application folder ({#inv-stable-artifact-dir}) stays the working copy. This module
only WRITES copies into the user's resumes folder: it never reads a copy back, never deletes there,
and never raises into the request that triggered it.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.db import SessionLocal, begin_write
from app.models.application import Application
from app.models.job import Job
from app.models.qa_entry import QAEntry
from app.models.setting import Setting
from app.services.sync import status as sync_status

PREFIX = "resumes_folder."  # machine-local: sync/registry.LOCAL_SETTING_PREFIXES
ENABLED_KEY = PREFIX + "enabled"      # row exists only as "off"
PATH_KEY = PREFIX + "path"            # custom root; no row = the default
LAST_ERROR_KEY = PREFIX + "last_error"  # JSON {code, path, at}
APP_KEY_PREFIX = PREFIX + "app."      # + application uuid hex -> folder name
COPIED_KEY_PREFIX = PREFIX + "copied."  # + application uuid hex -> JSON {root, files: {name: [size, mtime_ns]}}

logger = logging.getLogger(__name__)


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


ERROR_SENTENCES = {
    "missing": "Maestro couldn't find this folder. Reconnect the drive or choose another folder.",
    "write": "Maestro couldn't save the last copy to this folder. Check that it still exists and that you can write to it.",
}


class FolderRefused(ValueError):
    """A custom folder the user can't use, phrased for them."""


def platform_name(platform: str = sys.platform) -> str:
    return {"darwin": "macos", "win32": "windows"}.get(platform, "linux")


def display(path: Path, home: Path | None = None) -> str:
    """The folder as the user sees it: `Documents › Maestro Career Studio › Applications` under home."""
    try:
        parts = path.relative_to(home or Path.home()).parts
    except ValueError:
        return str(path)
    return " › ".join(parts) if parts else str(path)


def _inside(path: Path, owner: str | Path, platform: str | None = None) -> bool:
    """Whether the resolved `path` is `owner` or under it. APFS and NTFS ignore case, and resolve() keeps it."""
    owner = Path(owner).resolve()
    if path.is_relative_to(owner):
        return True
    if (platform or sys.platform) not in ("darwin", "win32"):
        return False
    for candidate in (path, *path.parents):
        try:
            if os.path.samefile(candidate, owner):  # the filesystem's own answer, when both exist
                return True
        except OSError:
            break
    shown, held = [part.casefold() for part in path.parts], [part.casefold() for part in owner.parts]
    return shown[: len(held)] == held


def validate_folder(raw: str) -> Path:
    """The chosen custom root, or FolderRefused with a sentence for the user (no paths in it)."""
    try:
        path = Path(raw.strip()).expanduser()  # RuntimeError for an unknown ~user, ValueError for a NUL
        if not raw.strip() or not path.is_absolute():
            raise FolderRefused("Enter the folder's full path.")
        path = path.resolve()
    except (RuntimeError, ValueError, OSError):
        raise FolderRefused("Enter the folder's full path.") from None
    if not path.is_dir():
        raise FolderRefused("That folder doesn't exist. Create it first, then try again.")
    own = [app_settings.applications_dir, os.environ.get("MAESTRO_HOME")]
    if any(o and _inside(path, o) for o in own):
        raise FolderRefused("Choose a folder outside Maestro's own data folder.")
    try:
        fd, probe = tempfile.mkstemp(prefix=".maestro-probe-", dir=path)
        os.close(fd)
        os.unlink(probe)
    except OSError:
        raise FolderRefused("Maestro can't save files in that folder. Choose another one.") from None
    return path


def enabled(db: Session) -> bool:
    return _get(db, ENABLED_KEY) != "off"


def is_custom(db: Session) -> bool:
    return _get(db, PATH_KEY) is not None


def root(db: Session) -> Path | None:
    if availability() is not None:
        return None
    custom = _get(db, PATH_KEY)
    return Path(custom) if custom else app_settings.resumes_folder_default


_ILLEGAL = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')
_COMPANY_MAX, _TITLE_MAX = 35, 48
# Filenames top out at 255 UTF-8 bytes (Linux, APFS). The whole name stays under 200: the separator
# (5 bytes), the date suffix (13) and a clash suffix like " (99)" (5) leave 177 for the two parts.
_COMPANY_BYTES, _TITLE_BYTES = 70, 107


def _clean(text: str | None, limit: int, max_bytes: int) -> str:
    text = _ILLEGAL.sub(" ", unicodedata.normalize("NFC", text or ""))
    text = re.sub(r"\s+", " ", text).strip()[:limit]
    text = text.encode()[:max_bytes].decode(errors="ignore").rstrip(". ")  # never splits a character
    return text or "Untitled"


def folder_label(company: str | None, title: str | None, day: date) -> str:
    return f"{_clean(company, _COMPANY_MAX, _COMPANY_BYTES)} – {_clean(title, _TITLE_MAX, _TITLE_BYTES)} ({day.isoformat()})"


def _folder_for(db: Session, application: Application, job: Job) -> str:
    """The application's folder name: chosen once, saved, then never recomputed."""
    key = APP_KEY_PREFIX + application.id.hex
    saved = _get(db, key)
    if saved:
        return saved
    # Two applications can race to the same name under different keys, so take SQLite's write lock
    # before reading the names in use, and hold it through the commit below.
    begin_write(db)
    saved = _get(db, key)
    if saved:
        db.commit()  # release the lock
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
    except Exception:  # noqa: BLE001 — opening the session can raise OSError too (planning decision 8)
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
    # copystat carries the source's exact mtime over, so equality means "this is the copy we made".
    # A coarse filesystem (FAT) rounds it and just rewrites each time, which costs nothing.
    return st.st_size == src.st_size and st.st_mtime_ns == src.st_mtime_ns


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


# Every copy runs inside this one backend process (request threads and the backfill thread), so a
# process-local lock per application is enough to keep two copies of it from interleaving.
_APP_LOCKS: dict[UUID, threading.Lock] = {}
_APP_LOCKS_GUARD = threading.Lock()


def _lock_for(application_id: UUID) -> threading.Lock:
    with _APP_LOCKS_GUARD:
        return _APP_LOCKS.setdefault(application_id, threading.Lock())


def _stamps(files: list[Path]) -> dict[str, list[int]]:
    """The sources as they are now: {file name: [size, mtime_ns]}."""
    stamps = {}
    for src in files:
        st = src.stat()
        stamps[src.name] = [st.st_size, st.st_mtime_ns]
    return stamps


def _already_copied(db: Session, application: Application, base: Path, stamps: dict) -> bool:
    """True when these exact files were copied into this root before. The folder is not looked at:
    a copy the user deleted or renamed stays that way until the resume changes or they reveal it."""
    raw = _get(db, COPIED_KEY_PREFIX + application.id.hex)
    if raw is None:
        return False
    try:
        record = json.loads(raw)
    except ValueError:
        return False
    return isinstance(record, dict) and record.get("root") == str(base) and record.get("files") == stamps


def _attempt(
    application_id: UUID, *, respect_record: bool = False, log_traceback: bool = True
) -> tuple[bool, Path | None]:
    """(ok, resume copy). ok is False only when a copy was due and failed.

    respect_record (the backfill): skip an application whose files were already copied into this root
    and haven't changed since, without touching the folder. Everything else copies unconditionally and
    refreshes the record, so a new PDF or a reveal always brings the copy back.
    log_traceback=False logs a failure as one line: a backfill pass keeps the traceback for its first.
    """
    try:
        if availability() is not None:
            return True, None
        with _lock_for(application_id), SessionLocal() as db:
            if not enabled(db):
                return True, None
            application = db.get(Application, application_id)
            job = db.get(Job, application.job_id) if application is not None else None
            if job is None:
                return True, None
            resume, files = _sources(db, application)
            if not files:
                return True, None
            stamps = _stamps(files)
            base = root(db)
            if base is None:  # became unavailable mid-call (the sync key appeared)
                return True, None
            if respect_record and _already_copied(db, application, base, stamps):
                name = _get(db, APP_KEY_PREFIX + application_id.hex)
                return True, base / name / resume.name if name and resume is not None else None
            base = _ready_root(db)
            if base is None:
                return True, None
            folder = base / _folder_for(db, application, job)
            folder.mkdir(exist_ok=True)
            placed = {src: _place(src, folder) for src in files}
            record = json.dumps({"root": str(base), "files": stamps})
            for first_try in (True, False):
                _put(db, COPIED_KEY_PREFIX + application_id.hex, record)
                if _get(db, LAST_ERROR_KEY) is not None:
                    _put(db, LAST_ERROR_KEY, None)
                try:
                    db.commit()
                    break
                except IntegrityError:  # another process inserted the record first: update it instead
                    db.rollback()
                    if not first_try:
                        raise
            return True, placed.get(resume) if resume is not None else None
    except Exception as exc:  # noqa: BLE001 — never raise into the render (planning decision 8)
        logger.warning("Couldn't save a copy of application %s to the resumes folder%s",
                       application_id, "" if log_traceback else f": {exc}", exc_info=log_traceback)
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


def copy_path_for(application_id: UUID) -> Path | None:
    """Where the resume copy is, without writing anything. None when there is none."""
    try:
        if availability() is not None:
            return None
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


_BACKFILL = threading.Lock()
_BACKFILL_WANTED = threading.Event()


def _root_failure() -> Exception | None:
    """Why the root can't take a copy right now (missing, unwritable), else None."""
    try:
        with SessionLocal() as db:
            base = _ready_root(db)  # the default root is made; a custom one must already be there
        if base is None:
            return None
        fd, probe = tempfile.mkstemp(prefix=".maestro-probe-", dir=base)
        os.close(fd)
        os.unlink(probe)
    except Exception as exc:  # noqa: BLE001 — never raise (planning decision 8)
        return exc
    return None


def _backfill_pass() -> int | None:
    """One pass over every application with a resume PDF. Resumes copied (or already there), or None.

    If the folder itself is missing or unwritable, every copy would fail the same way: record that
    once and end the pass. Otherwise every application is tried, so one bad source file blocks no
    other; only the first failure logs a traceback. The next start, a Save in Settings or a reveal
    retries.
    """
    with SessionLocal() as db:
        if root(db) is None or not enabled(db):
            return None
        ids = list(db.scalars(select(Application.id).where(Application.pdf_path.is_not(None))))
    if not ids:
        return 0
    failure = _root_failure()
    if failure is not None:
        logger.warning("The resumes folder can't take copies", exc_info=failure)
        _record_error(failure)
        return None
    with SessionLocal() as db:  # the folder is fine now: no copy may run (all match their records), so clear here
        if _get(db, LAST_ERROR_KEY) is not None:
            _put(db, LAST_ERROR_KEY, None)
            db.commit()
    copied, failed = 0, False
    for application_id in ids:
        ok, path = _attempt(application_id, respect_record=True, log_traceback=not failed)
        copied += path is not None
        failed = failed or not ok
    return copied


def _unavailable() -> bool:
    """availability() is not None, with a check that can't be read counting as unavailable."""
    try:
        return availability() is not None
    except Exception:  # noqa: BLE001 — never raise (planning decision 8)
        logger.warning("Couldn't check whether the resumes folder is available", exc_info=True)
        return True


def backfill() -> int | None:
    """Copy every application's PDFs, skipping those already copied. Returns how many resumes the last pass covered.

    Runs at every start and after a Settings change; a copy already in place costs one `stat`.
    Requests coalesce: one that arrives while a pass runs sets the flag and returns None, and the
    running pass loops once more for it. Set the flag first, then try the lock, so none is lost.
    """
    if _unavailable():
        return None
    _BACKFILL_WANTED.set()
    copied = None
    while _BACKFILL_WANTED.is_set():
        if not _BACKFILL.acquire(blocking=False):
            return None  # the holder re-checks the flag after it releases
        try:
            _BACKFILL_WANTED.clear()
            copied = _backfill_pass()
        except Exception:  # noqa: BLE001 — runs as a thread target (planning decision 8)
            logger.warning("The resumes folder backfill stopped", exc_info=True)
            copied = None
        finally:
            _BACKFILL.release()
    return copied


def start_backfill() -> threading.Thread | None:
    """Fill the folder in the background so startup never waits on a slow drive."""
    if _unavailable():
        return None
    # Raise the flag here, in the caller's thread: the PUT handler commits the new root first, so a
    # pass already running sees the request before it could mark the old root done.
    _BACKFILL_WANTED.set()
    thread = threading.Thread(target=backfill, name="resumes-folder-backfill", daemon=True)
    thread.start()
    return thread

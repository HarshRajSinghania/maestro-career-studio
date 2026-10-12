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
import tempfile
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
BACKFILLED_KEY = PREFIX + "backfilled"  # the root the last clean backfill filled
LAST_ERROR_KEY = PREFIX + "last_error"  # JSON {code, path, at}
APP_KEY_PREFIX = PREFIX + "app."      # + application uuid hex -> folder name

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

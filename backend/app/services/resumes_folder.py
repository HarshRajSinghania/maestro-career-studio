"""Visible copies of finished resumes. SYSTEM.md {#inv-resumes-folder-one-way}.

The hidden per-application folder ({#inv-stable-artifact-dir}) stays the working copy. This module
only WRITES copies into the user's resumes folder: it never reads a copy back, never deletes there,
and never raises into the request that triggered it.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.db import begin_write
from app.models.application import Application
from app.models.job import Job
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

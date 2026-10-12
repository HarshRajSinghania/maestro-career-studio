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

"""The upkeep verbs: backup, migrate, check.

`backup` runs `app.tools.backup_db` as a child with the backend's environment, so the SQLite
online-backup API copies a consistent image even while Maestro runs. `migrate` and `check` run
in this process, after `apply_env` (R8): nothing under `app` may be imported before it, because
`app.config.settings` reads the environment at import. Hence every `app` import below is
inside a method.
"""
from __future__ import annotations

import dataclasses
import json
import os
import sqlite3
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from maestro_cs import home as home_mod
from maestro_cs import procs, runtime
from maestro_cs.control import DB_FILENAME, Context, ControlError

KEEP_BACKUPS = 5
BACKUP_GLOB = "db-*.sqlite3.gz"
NODE_TIMEOUT = 5.0
WINDOWS = sys.platform == "win32"


def default_backup_name(now: datetime) -> str:
    return f"db-{now.strftime('%Y%m%dT%H%M%SZ')}.sqlite3.gz"


def _failure_sentence(stderr: str) -> str:
    lines = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    detail = lines[-1].removeprefix("error: ") if lines else "the backup tool gave no reason"
    return f"The backup failed: {detail.rstrip('.')}."


class Maintenance:
    def __init__(
        self,
        ctx: Context,
        *,
        run: Callable[..., Any] = subprocess.run,
        apply: Callable[..., None] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        say: Callable[..., None] = print,
    ) -> None:
        self.ctx, self.run, self.now, self.say = ctx, run, now, say
        self._apply = apply

    # --- shared -------------------------------------------------------------------------

    @property
    def db_file(self) -> Path:
        return self.ctx.home / "data" / DB_FILENAME

    @property
    def backups_dir(self) -> Path:
        return self.ctx.home / "backups"

    def _backend_env(self) -> dict[str, str]:
        ctx = self.ctx
        return home_mod.backend_env(ctx.home, ctx.settings, ctx.environ, ctx.ports, ctx.version)

    def enter_backend_env(self) -> None:
        """R8: look like the backend child before anything imports `app`."""
        apply = self._apply or home_mod.apply_env  # looked up now so a test can wrap it
        apply(self.ctx.home, self._backend_env())

    def is_running(self) -> bool:
        return any(
            procs.live_process(procs.read_json(self.ctx.run_dir / f"{name}.json"))
            for name in ("supervisor", "backend", "web")
        )

    # --- backup -------------------------------------------------------------------------

    def _refuse_bad_target(self, target: Path) -> None:
        live = {Path(f"{self.db_file}{suffix}").resolve() for suffix in ("", "-wal", "-shm", "-journal")}
        if target.resolve() in live:
            raise ControlError("That path is the live database; choose a different --out.")
        if target.is_dir():
            raise ControlError(f"{target} is a folder; give --out a file name.")
        if target.exists():
            raise ControlError(f"{target} already exists; choose a new file name.")

    def _default_target(self) -> Path:
        stem = default_backup_name(self.now()).removesuffix(".sqlite3.gz")
        candidate, n = self.backups_dir / f"{stem}.sqlite3.gz", 0
        while candidate.exists():  # two backups in one second must not share a file
            n += 1
            candidate = self.backups_dir / f"{stem}-{n}.sqlite3.gz"
        return candidate

    def snapshot(self, out: str | None = None) -> Path | None:
        """Write a verified snapshot and return its path (None when there is no database yet)."""
        if not self.db_file.is_file():
            return None
        if out:
            target = Path(os.path.abspath(Path(out).expanduser()))
            self._refuse_bad_target(target)
        else:
            target = self._default_target()
        # Only a file this run creates may be removed on failure; the target did not exist.
        kwargs: dict[str, Any] = {"creationflags": subprocess.CREATE_NO_WINDOW} if WINDOWS else {}
        argv = [str(runtime.venv_python(windowless=False)), "-m", "app.tools.backup_db",
                "--out", str(target)]
        try:
            done = self.run(argv, cwd=self.ctx.home, env=self._backend_env(),
                            stdin=subprocess.DEVNULL, capture_output=True, text=True,
                            check=False, **kwargs)
        except OSError as exc:
            target.unlink(missing_ok=True)
            raise ControlError(f"Couldn't run the backup: {exc.strerror or exc}.") from None
        if done.returncode != 0:
            target.unlink(missing_ok=True)  # never leave a half-written snapshot that looks real
            raise ControlError(_failure_sentence(done.stderr))
        if target.parent == self.backups_dir:  # an --out anywhere else is never touched
            self._rotate(keep=target)
        return target

    def backup(self, out: str | None = None) -> int:
        target = self.snapshot(out)
        self.say("Nothing to back up yet." if target is None else str(target))
        return 0

    def _rotate(self, keep: Path) -> None:
        def age(path: Path) -> tuple[int, str]:
            try:
                return path.stat().st_mtime_ns, path.name
            except OSError:
                return 0, path.name

        others = sorted((p for p in self.backups_dir.glob(BACKUP_GLOB) if p != keep),
                        key=age, reverse=True)
        for old in others[KEEP_BACKUPS - 1:]:  # the new snapshot is one of the five
            old.unlink(missing_ok=True)

    # --- migrate ------------------------------------------------------------------------

    def migrate(self) -> int:
        if self.is_running():
            raise ControlError("Stop Maestro first; it migrates itself at start.")
        self.enter_backend_env()
        from app.services import seeding  # noqa: PLC0415  (after apply_env, R8)

        from alembic.util.exc import CommandError  # noqa: PLC0415
        from sqlalchemy.exc import OperationalError  # noqa: PLC0415

        try:
            seeding.run_migrations()
        except CommandError as exc:
            raise ControlError(self._newer_database_sentence(exc)) from None
        except OperationalError as exc:
            reason = str(getattr(exc, "orig", exc)).splitlines()[0].rstrip(".")
            raise ControlError(f"The database couldn't be migrated: {reason}.") from None
        self.say("The database is up to date.")
        return 0

    def _newer_database_sentence(self, exc: Exception) -> str:
        revision = self._db_revision()
        if revision and "locate revision" in str(exc).lower():
            return (f"This database was made by a newer Maestro (revision {revision}); "
                    "install that version or newer.")
        return f"The database couldn't be migrated: {str(exc).splitlines()[0].rstrip('.')}."

    # --- check --------------------------------------------------------------------------

    def _db_revision(self) -> str | None:
        if not self.db_file.is_file():
            return None
        try:
            conn = sqlite3.connect(f"{self.db_file.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
            try:
                row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            return None
        return str(row[0]) if row else None

    def _node_version(self) -> str | None:
        kwargs: dict[str, Any] = {"creationflags": subprocess.CREATE_NO_WINDOW} if WINDOWS else {}
        try:
            done = self.run([str(runtime.node_path(environ=self.ctx.environ)), "--version"],
                            capture_output=True, text=True, timeout=NODE_TIMEOUT, check=False,
                            env=home_mod.child_env(self.ctx.environ), stdin=subprocess.DEVNULL, **kwargs)
        except (OSError, subprocess.SubprocessError):
            return None
        out = (done.stdout or "").strip()
        return out if done.returncode == 0 and out else None

    def check(self) -> int:
        self.enter_backend_env()
        # Imported only now, after apply_env (R8).
        from alembic.config import Config  # noqa: PLC0415
        from alembic.script import ScriptDirectory  # noqa: PLC0415

        from app.services import engines, seeding  # noqa: PLC0415

        cfg = Config()
        cfg.set_main_option("script_location", str(seeding.migrations_dir()))
        report = {
            "version": self.ctx.version,
            "home": str(self.ctx.home),
            "code_head": ScriptDirectory.from_config(cfg).get_current_head(),
            "db_revision": self._db_revision(),
            "engines": dataclasses.asdict(engines.probe()),
            "web": runtime.web_dir_ok(self.ctx.environ),
            "node": self._node_version(),
        }
        self.say(json.dumps(report, indent=2))
        return 0

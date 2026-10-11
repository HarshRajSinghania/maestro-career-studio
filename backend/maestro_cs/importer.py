"""`maestro import-docker CHECKOUT`: a one-time move of a Docker install's data into this install.

The checkout is only ever READ: its database is copied byte for byte to a temp folder and read
there (opening it, even read-only, would create -wal/-shm files beside it) and its files are copied
out, never moved. Everything that
changes lives in the uv install's folder, and the order keeps the existing database safe:

1. refusals, 2. a verified snapshot of the current database (the undo point),
3. the new database is built in a staging folder (SQLite backup API, stored paths rewritten in one
transaction, row counts compared), 4. files are copied in, 5. the staged database replaces the old one
(the old one moves aside first), 6. `maestro.env` gains the settings the backend understands,
7. verification. Any failure after step 2 puts the old database back.

Nothing here imports `app` at module level (R8): `app.config` reads the environment at import, so the
install's environment is applied first (`Maintenance.enter_backend_env`) and `app` is imported inside
the methods that need it.
"""
from __future__ import annotations

import contextlib
import os
import shutil
import sqlite3
import stat
import tempfile
from collections.abc import Callable, Iterator
from datetime import datetime, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

from maestro_cs import control, maintenance
from maestro_cs import home as home_mod
from maestro_cs.control import ControlError

COPY_DIRS = ("applications", "base_resumes", "kb_documents", "exports", "settings")
REWRITE_DIRS = (*COPY_DIRS, "data")
SYNC_KEY_IN_CHECKOUT = Path("settings") / "secrets" / "sync-key"
CHECKOUT_DB = Path("data") / control.DB_FILENAME
DOCKER_PORTS = (("BACKEND_HOST_PORT", 8001), ("FRONTEND_HOST_PORT", 3000))
PROXY_KEYS = frozenset({"HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "SSL_CERT_FILE"})
ASIDE = ".before-import"
STAGE = ".importing"  # the staging folder for the new database, under data/
SAVED = ".before-import"  # copies of replaced files, under data/ while the import runs
SAMPLE_LIMIT = 10
POSIX = os.name == "posix"

# Settings the backend reads that mean something only inside the Docker install, or that the uv
# install sets itself (maestro_cs/home.py). Path-valued names are caught by suffix below.
_NOT_CARRIED = frozenset({
    "DATABASE_URL", "ALLOWED_HOSTS", "ALLOWED_WEB_ORIGINS", "MAESTRO_CS_PUBLIC_BACKEND_URL",
    "MAESTRO_CS_PDFLATEX", "MAESTRO_INSTALL", "SQLITE_JOURNAL_MODE",
})
_PATH_SUFFIXES = ("_DIR", "_FILE", "_PATH", "_PATHS")
_TEXT_KINDS = ("CHAR", "TEXT", "CLOB")
DOCKER_HOST_NAME = "host.docker.internal"


class ImportFailure(Exception):
    """A problem found after the import started; `run` turns it into the one-sentence exit."""


@dataclass
class RewriteReport:
    counts: dict[tuple[str, str], int] = field(default_factory=dict)
    mentions: dict[tuple[str, str], int] = field(default_factory=dict)
    samples: list[str] = field(default_factory=list)


@dataclass
class CopyStats:
    files: int = 0
    size: int = 0
    skipped: int = 0


@dataclass
class EnvPlan:
    """What the Docker `.env` contributes: names to carry (with values), keep, and leave out."""
    had_file: bool
    carry: dict[str, str] = field(default_factory=dict)
    kept: list[str] = field(default_factory=list)
    left_out: list[str] = field(default_factory=list)


@dataclass
class _Undo:
    """Everything an import changes in the uv folder, so a failure puts all of it back."""
    home: Path
    saved: Path  # data/.before-import: copies of the files about to be replaced
    db: Path
    moved: list[tuple[Path, Path]] = field(default_factory=list)  # database files moved aside: (original, aside)
    placed: bool = False
    created: list[Path] = field(default_factory=list)  # files this import added
    new_dirs: list[Path] = field(default_factory=list)

    def keep(self, path: Path) -> None:
        """Call before writing `path`: saves what is there, or remembers that the file is new."""
        try:
            info = path.lstat()
        except FileNotFoundError:
            self.created.append(path)
            return
        if stat.S_ISREG(info.st_mode):
            copy = self.saved / path.relative_to(self.home)
            copy.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            _copy_file(path, copy, info)

    def restore(self) -> None:
        for path in reversed(self.created):
            path.unlink(missing_ok=True)
        for copy in sorted(p for p in self.saved.rglob("*") if p.is_file()):
            original = self.home / copy.relative_to(self.saved)
            original.parent.mkdir(parents=True, exist_ok=True)
            os.replace(copy, original)
        if self.placed:
            for path in _sidecars(self.db):
                path.unlink(missing_ok=True)
        for original, aside in self.moved:
            os.replace(aside, original)
        for folder in reversed(self.new_dirs):
            with contextlib.suppress(OSError):  # not empty: something else lives there now
                folder.rmdir()
        shutil.rmtree(self.saved, ignore_errors=True)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)


def _sidecars(db: Path) -> list[Path]:
    return [db, Path(f"{db}-wal"), Path(f"{db}-shm")]


def _size(n: int) -> str:
    value = float(n)
    for unit in ("bytes", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} bytes" if unit == "bytes" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{n} bytes"


def _reason(exc: BaseException) -> str:
    if isinstance(exc, OSError):
        base = exc.strerror or str(exc)
        return f"{base} ({exc.filename})" if exc.filename else base
    return str(exc)


# --- reading the Docker database --------------------------------------------------------------

def _fingerprint(path: Path) -> tuple[int, int]:
    info = path.stat()
    return info.st_size, info.st_mtime_ns


@contextlib.contextmanager
def private_copy(db: Path) -> Iterator[Path]:
    """A copy of the database and its -wal/-shm in a temp folder; everything is read from the copy.

    A read-only SQLite connection is not read-only for the folder: on a WAL database it creates the
    -wal and -shm files beside it. So the original is never opened, only its bytes are read. The
    install being copied must be stopped (the ports were checked); a file that changes while it is
    copied means it was not, and that stops the import.
    """
    with tempfile.TemporaryDirectory(prefix="maestro-import-", ignore_cleanup_errors=True) as tmp:
        present = [path for path in _sidecars(db) if path.is_file()]
        before = [_fingerprint(path) for path in present]
        for path in present:
            shutil.copyfile(path, Path(tmp) / path.name)
        if [_fingerprint(path) for path in present] != before:
            raise ImportFailure(f"{db.name} changed while it was being read; stop the Docker install "
                                "(`docker compose down`) and run this again.")
        yield Path(tmp) / db.name


def _revision(conn: sqlite3.Connection) -> str | None:
    try:
        row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.OperationalError:
        return None
    return str(row[0]) if row else None


def _tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                        "AND name NOT LIKE 'sqlite\\_%' ESCAPE '\\' ORDER BY name").fetchall()
    return [name for (name,) in rows]


def row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {t: conn.execute(f"SELECT count(*) FROM {_quote(t)}").fetchone()[0] for t in _tables(conn)}


# --- the stored-path rewrite ------------------------------------------------------------------

def _columns(conn: sqlite3.Connection, kinds: tuple[str, ...]) -> list[tuple[str, str]]:
    """(table, column) for every column whose declared type contains one of `kinds`."""
    found = []
    for table in _tables(conn):
        for _cid, name, kind, *_rest in conn.execute(f"PRAGMA table_info({_quote(table)})"):
            if any(word in (kind or "").upper() for word in kinds):
                found.append((table, name))
    return found


def _classify(conn: sqlite3.Connection, table: str, column: str, samples: list[str]) -> int:
    """How many values in the column start with a rewritable `/app/<dir>/`; collects a few samples."""
    t, c = _quote(table), _quote(column)
    found, files = 0, []
    for (value,) in conn.execute(f"SELECT {c} FROM {t} WHERE substr({c}, 1, 5) = '/app/'"):
        head, slash, _tail = str(value)[5:].partition("/")
        if not (slash and head in REWRITE_DIRS):
            raise ImportFailure(
                f"{table}.{column} holds a path under /app/{head}{slash} that has no place in the "
                "Maestro folder, so nothing was imported.")
        found += 1
        if head != "data":  # /app/data/... values name files this import does not copy
            files.append(value)
    samples.extend(files[:2])
    return found


def plan_rewrites(conn: sqlite3.Connection) -> RewriteReport:
    """Count what would change; raises ImportFailure for a container path with nowhere to go."""
    report = RewriteReport()
    rewritable = set(_columns(conn, _TEXT_KINDS))
    for table, column in _columns(conn, (*_TEXT_KINDS, "JSON")):  # JSON columns are only reported on
        t, c = _quote(table), _quote(column)
        if (table, column) in rewritable and (found := _classify(conn, table, column, report.samples)):
            report.counts[(table, column)] = found
        extra = conn.execute(
            f"SELECT count(*) FROM {t} WHERE typeof({c}) = 'text' AND instr({c}, '/app/') > 0 "
            f"AND substr({c}, 1, 5) != '/app/'"
        ).fetchone()[0]
        if extra:
            report.mentions[(table, column)] = extra
    report.samples = report.samples[:SAMPLE_LIMIT]
    return report


def rewrite_paths(conn: sqlite3.Connection, prefixes: dict[str, str], sep: str) -> RewriteReport:
    """Replace a leading `/app/<dir>/` with `prefixes[dir]` in every text column, in ONE transaction.

    `prefixes` ends with `sep`; the rest of the value has its `/` turned into `sep` (Windows).
    Only values that START with the exact prefix change; a second run changes nothing.
    """
    report = plan_rewrites(conn)  # raises before any write
    try:
        for (table, column), _count in report.counts.items():
            t, c = _quote(table), _quote(column)
            for directory in REWRITE_DIRS:
                old = f"/app/{directory}/"
                conn.execute(
                    f"UPDATE {t} SET {c} = ? || replace(substr({c}, ?), '/', ?) WHERE substr({c}, 1, ?) = ?",
                    (prefixes[directory], len(old) + 1, sep, len(old), old))
        for table, column in report.counts:
            t, c = _quote(table), _quote(column)
            left = conn.execute(f"SELECT count(*) FROM {t} WHERE substr({c}, 1, 5) = '/app/'").fetchone()[0]
            if left:
                raise ImportFailure(f"{left} values in {table}.{column} still start with /app/ after the rewrite.")
        report.samples = [prefixes[v[5:].partition("/")[0]] + v[5:].partition("/")[2].replace("/", sep)
                          for v in report.samples]
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return report


# --- copying files ----------------------------------------------------------------------------

def _ensure_dir(path: Path) -> None:
    if path.is_symlink():
        raise ImportFailure(f"{path} is a symlink; Maestro won't write through it. Replace it with a folder.")
    path.mkdir(mode=0o700, exist_ok=True)
    if not path.is_dir():
        raise ImportFailure(f"{path} is not a folder.")


def _copy_file(src: Path, dst: Path, info: os.stat_result) -> None:
    tmp = dst.with_name(dst.name + ".importing")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    try:
        with open(src, "rb") as reader, os.fdopen(os.open(tmp, flags, 0o600), "wb") as writer:
            shutil.copyfileobj(reader, writer)
        if POSIX:  # owner bits only: a copy never ends up wider than 0700/0600
            os.chmod(tmp, (info.st_mode & 0o700) | 0o600)
        os.utime(tmp, ns=(info.st_atime_ns, info.st_mtime_ns))
        os.replace(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _files_to_copy(src: Path, stats: CopyStats) -> Iterator[tuple[Path, list[str]]]:
    """(folder relative to src, its file names), never entering a symlinked folder."""
    for folder, dirnames, filenames in os.walk(src):  # followlinks=False
        here = Path(folder)
        stats.skipped += sum(1 for name in dirnames if (here / name).is_symlink())
        dirnames[:] = [name for name in dirnames if not (here / name).is_symlink()]
        yield here.relative_to(src), filenames


def _write_one(src: Path, dst: Path, info: os.stat_result, undo: _Undo | None) -> None:
    if undo:
        undo.keep(dst)
    _copy_file(src, dst, info)


def _prepare_dir(path: Path, undo: _Undo | None) -> None:
    if undo and not path.exists():
        undo.new_dirs.append(path)
    _ensure_dir(path)


def _copy_one(src: Path, dst: Path, stats: CopyStats, write: bool, undo: _Undo | None) -> None:
    info = src.lstat()
    if not stat.S_ISREG(info.st_mode):
        stats.skipped += 1
        return
    if write:
        _write_one(src, dst, info, undo)
    stats.files += 1
    stats.size += info.st_size


def copy_tree(src: Path, dst: Path, skip: frozenset[Path] = frozenset(), write: bool = True,
              undo: _Undo | None = None) -> CopyStats:
    """Merge `src` into `dst` (same names are replaced). Symlinks and special files are skipped.

    `write=False` only counts, for the dry run. `undo` records what is replaced or added.
    """
    stats = CopyStats()
    if src.is_symlink() or not src.is_dir():
        stats.skipped += src.is_symlink()
        return stats
    for relative, names in _files_to_copy(src, stats):
        if write:
            _prepare_dir(dst / relative, undo)
        for name in (n for n in names if relative / n not in skip):
            _copy_one(src / relative / name, dst / relative / name, stats, write, undo)
    return stats


# --- the settings file ------------------------------------------------------------------------

def _carried(name: str, settings_names: frozenset[str]) -> bool:
    if name in PROXY_KEYS:
        return True
    if name in _NOT_CARRIED or name.endswith(_PATH_SUFFIXES):
        return False
    return name in settings_names


def _env_quote(value: str) -> str:
    if value and all(ch.isalnum() or ch in "_@%+:,./-" for ch in value):
        return value
    # python-dotenv reads single quotes literally except for the two escapes below.
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _write_private(path: Path, content: str | bytes) -> None:
    tmp = path.with_name(path.name + ".importing")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    try:
        with os.fdopen(os.open(tmp, flags, 0o600), "wb") as handle:
            handle.write(content.encode("utf-8") if isinstance(content, str) else content)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class Probes(NamedTuple):
    """The two questions asked of the machine; tests replace them."""
    is_port_free: Callable[[int], bool]
    http_get: Callable[..., dict[str, Any] | None]


def real_probes() -> Probes:
    return Probes(control.port_free, control.http_get_json)


class Importer:
    def __init__(self, ctx: control.Context, upkeep: maintenance.Maintenance | None = None,
                 probes: Probes | None = None, say: Callable[..., None] = print) -> None:
        self.ctx, self.say = ctx, say
        self.probes = probes or real_probes()
        self.upkeep = upkeep or maintenance.Maintenance(ctx, say=say)

    # --- entry --------------------------------------------------------------------------

    def run(self, checkout: str, replace: bool, dry_run: bool) -> int:
        root = Path(os.path.abspath(Path(checkout).expanduser()))  # before the chdir into the home
        self._refuse_if_interrupted()
        source_db = self._check_checkout(root)
        docker_env = home_mod.read_env_file(root / ".env")
        self._refuse_if_running()
        self._refuse_if_ports_held(root, docker_env)
        self.upkeep.enter_backend_env()  # R8: before anything imports `app`
        head, known = self._code_revisions()
        try:
            with private_copy(source_db) as readable:
                self._refuse_unless_current(readable, head, known)
                if self._home_holds_data() and not replace:
                    raise ControlError(
                        f"{self.ctx.home} already holds your data (jobs, applications or resumes). Importing "
                        "replaces it; run again with --replace to do that (a snapshot is saved first).")
                if dry_run:
                    return self._dry_run(root, readable, docker_env)
                return self._import(root, readable, docker_env, head)
        except (ImportFailure, OSError, sqlite3.Error, home_mod.HomeError) as exc:  # only before/outside `_import`
            raise ControlError(_reason(exc)) from None

    # --- refusals -----------------------------------------------------------------------

    def _refuse_if_interrupted(self) -> None:
        data = self.ctx.home / "data"
        left = sorted({p.name for p in data.glob(f"*{ASIDE}")} | {n for n in (STAGE, SAVED) if (data / n).exists()})
        if not left:
            return
        snaps = sorted((self.ctx.home / "backups").glob(maintenance.BACKUP_GLOB), key=lambda p: p.stat().st_mtime)
        newest = f"Your earlier data is in {snaps[-1].name} in {data.parent / 'backups'}" if snaps else (
            "There is no snapshot in backups/")
        raise ControlError(
            f"A previous import was interrupted: {', '.join(left)} {'is' if len(left) == 1 else 'are'} still in "
            f"{data}. {newest}, and in the {ASIDE} files here (put each back under its name without {ASIDE}). "
            "Delete the leftovers, then run this again.")

    def _check_checkout(self, root: Path) -> Path:
        if not root.is_dir():
            raise ControlError(f"{root} is not a folder.")
        source_db = root / CHECKOUT_DB
        if source_db.is_symlink():
            raise ControlError(f"{source_db} is a symlink; point this at the real Docker checkout.")
        if not source_db.is_file():
            raise ControlError(f"{root} has no data/{control.DB_FILENAME}; point this at the folder "
                               "you run `docker compose up` in.")
        if not (root / "docker-compose.yml").is_file():
            raise ControlError(f"{root} has no docker-compose.yml; point this at the folder "
                               "you run `docker compose up` in.")
        return source_db

    def _refuse_if_running(self) -> None:
        if self.upkeep.is_running():
            raise ControlError("Stop Maestro first (`maestro stop`); it is running from the folder "
                               "this would import into.")

    def _docker_ports(self, docker_env: dict[str, str]) -> list[int]:
        ports = []
        for key, default in DOCKER_PORTS:
            raw = docker_env.get(key, "")
            ok = raw.isascii() and raw.isdigit() and 0 < int(raw) <= 65535
            ports.append(int(raw) if ok else default)
        return ports

    def _refuse_if_ports_held(self, root: Path, docker_env: dict[str, str]) -> None:
        docker, ports = self._docker_ports(docker_env), self.ctx.ports
        busy = next((p for p in dict.fromkeys([*docker, ports.backend, ports.web])
                     if not self.probes.is_port_free(p)), None)
        if busy is None:
            return
        if busy not in docker:
            raise ControlError(f"Port {busy} is in use by another program. "
                               f"Maestro needs ports {ports.backend} and {ports.web}.")
        info = self.probes.http_get(f"http://127.0.0.1:{busy}/api/version")
        version = f" (version {info['version']})" if info and isinstance(info.get("version"), str) else ""
        raise ControlError(f"Something is answering on port {busy}, probably the Docker install{version}. "
                           f"Stop it with `docker compose down` in {root}, then run this again.")

    def _code_revisions(self) -> tuple[str, set[str]]:
        from alembic.config import Config  # noqa: PLC0415  (after apply_env, R8)
        from alembic.script import ScriptDirectory  # noqa: PLC0415

        from app.services import seeding  # noqa: PLC0415

        cfg = Config()
        cfg.set_main_option("script_location", str(seeding.migrations_dir()))
        script = ScriptDirectory.from_config(cfg)
        return script.get_current_head(), {s.revision for s in script.walk_revisions()}

    def _refuse_unless_current(self, readable: Path, head: str, known: set[str]) -> None:
        conn = _ro(readable)
        try:
            revision = _revision(conn)
        except sqlite3.DatabaseError:
            revision = None
        finally:
            conn.close()
        if revision is None:
            raise ControlError("That database has no schema revision, so it is not a Maestro database.")
        if revision == head:
            return
        if revision in known:
            raise ControlError(
                f"The Docker install's database is at schema revision {revision}, from an older Maestro. "
                f"Update the Docker install to {self.ctx.version} first (docs/UPDATING.md), then run this again.")
        raise ControlError(
            f"The Docker install's database is at revision {revision}, which a newer Maestro made; "
            "install a newer Maestro (`maestro update`) and run this again.")

    def _home_holds_data(self) -> bool:
        db = self.upkeep.db_file
        if not db.is_file():
            return False
        queries = ("SELECT count(*) FROM jobs", "SELECT count(*) FROM applications",
                   "SELECT count(*) FROM base_resumes WHERE slug != 'example'")
        try:
            with private_copy(db) as copy:  # not opened in place: that could add -wal/-shm files here
                conn = _ro(copy)
                try:
                    for query in queries:
                        try:
                            if conn.execute(query).fetchone()[0]:
                                return True
                        except sqlite3.OperationalError as exc:
                            if "no such table" not in str(exc):
                                return True
                finally:
                    conn.close()
        except (OSError, ImportFailure, sqlite3.DatabaseError):
            return True  # can't tell, so assume it does
        return False

    # --- shared pieces ------------------------------------------------------------------

    def _prefixes(self) -> dict[str, str]:
        return {d: str(self.ctx.home / d) + os.sep for d in REWRITE_DIRS}

    def _settings_names(self) -> frozenset[str]:
        from app.config import Settings  # noqa: PLC0415  (after apply_env, R8)

        return frozenset(name.upper() for name in Settings.model_fields)

    def _env_plan(self, root: Path, docker_env: dict[str, str]) -> EnvPlan:
        names = self._settings_names()
        current = {k.upper() for k in home_mod.read_env_file(self.ctx.home / "maestro.env")}
        plan = EnvPlan(had_file=(root / ".env").is_file())
        for key, value in docker_env.items():
            name = key.upper()
            if not _carried(name, names):
                plan.left_out.append(name)
            elif name in current:
                plan.kept.append(name)
            else:
                plan.carry[name] = value
        plan.left_out.sort()
        plan.kept.sort()
        return plan

    def _say_env(self, plan: EnvPlan, verb: str) -> None:
        if not plan.had_file:
            self.say("Settings: the Docker checkout has no .env, so nothing to carry over.")
            return
        if plan.carry:
            self.say(f"Settings {verb} into maestro.env (names only): {', '.join(sorted(plan.carry))}")
        else:
            self.say("Settings: nothing to carry into maestro.env.")
        for name in plan.kept:
            self.say(f"  {name} is already set in maestro.env; kept your value.")
        for name, value in sorted(plan.carry.items()):
            if DOCKER_HOST_NAME in value.lower():
                self.say(f"{name} points at {DOCKER_HOST_NAME}, which only works inside Docker; "
                         "change it to localhost in maestro.env.")
        if plan.left_out:
            self.say("  Left in the Docker install (they only make sense there): " + ", ".join(plan.left_out))

    def _file_lines(self, stats: dict[str, CopyStats], dry_run: bool) -> None:
        self.say("Files " + ("that would be copied:" if dry_run else "copied:"))
        for directory, one in stats.items():
            self.say(f"  {directory}: {one.files} files ({_size(one.size)})")
        skipped = sum(one.skipped for one in stats.values())
        if skipped:
            self.say(f"  Skipped {skipped} symlinks or special files (never followed out of the checkout).")

    def _report_lines(self, report: RewriteReport, dry_run: bool) -> None:
        if report.counts:
            self.say(("Would rewrite" if dry_run else "Rewrote") + " stored file paths (/app/... to this folder):")
            for (table, column), count in sorted(report.counts.items()):
                self.say(f"  {table}.{column}: {count}")
        else:
            self.say("Stored file paths: none start with /app/, nothing to rewrite.")
        for (table, column), count in sorted(report.mentions.items()):
            self.say(f"  {count} values in {table}.{column} mention /app/ and are left as written.")

    # --- dry run ------------------------------------------------------------------------

    def _dry_run(self, root: Path, readable: Path, docker_env: dict[str, str]) -> int:
        say, home = self.say, self.ctx.home
        say(f"Dry run: reading {root} (read-only) and {home}. Nothing will be changed.")
        say("Schema: the Docker database is at this Maestro's revision.")
        say("Your current data: " + ("would be saved to backups/ first." if self.upkeep.db_file.is_file()
                                     else "there is no database here yet, nothing to save."))
        conn = _ro(readable)
        try:
            counts = row_counts(conn)
            report = plan_rewrites(conn)
        finally:
            conn.close()
        say(f"Database: would copy {len(counts)} tables, {sum(counts.values())} rows.")
        self._report_lines(report, dry_run=True)
        stats = {d: copy_tree(root / d, home / d, self._skip(d), write=False) for d in COPY_DIRS}
        self._file_lines(stats, dry_run=True)
        say("Sync key: " + ("would be placed where this install reads it."
                            if (root / SYNC_KEY_IN_CHECKOUT).is_file() else "the checkout has none."))
        self._say_env(self._env_plan(root, docker_env), "would be carried")
        present = sum(1 for value in report.samples if (root / value[len("/app/"):]).exists())
        if report.samples:
            say(f"Sampled {len(report.samples)} stored paths: {present} exist in the checkout.")
        say("Dry run only. Nothing was changed.")
        return 0

    @staticmethod
    def _skip(directory: str) -> frozenset[Path]:
        # The sync key goes to <home>/sync-key, where the uv install reads it: one copy only.
        return frozenset({Path("secrets") / "sync-key"}) if directory == "settings" else frozenset()

    # --- the import ---------------------------------------------------------------------

    def _import(self, root: Path, readable: Path, docker_env: dict[str, str], head: str) -> int:
        say, home = self.say, self.ctx.home
        say(f"Importing from {root} (read-only) into {home}")
        snapshot = self.upkeep.snapshot()
        say(f"Saved your current data first: {snapshot}" if snapshot else
            "There is no database here yet, so there was nothing to save first.")
        stage = home / "data" / STAGE
        undo = _Undo(home, home / "data" / SAVED, self.upkeep.db_file)
        try:
            shutil.rmtree(stage, ignore_errors=True)
            stage.mkdir(mode=0o700)
            new_db, report, counts = self._build_database(readable, stage)
            say(f"Database: {len(counts)} tables, {sum(counts.values())} rows copied and checked.")
            self._report_lines(report, dry_run=False)
            stats = {d: copy_tree(root / d, home / d, self._skip(d), undo=undo) for d in COPY_DIRS}
            self._file_lines(stats, dry_run=False)
            say(self._place_key(root, undo))
            self._swap_in(new_db, undo)
            env_plan = self._env_plan(root, docker_env)
            self._carry_env(env_plan.carry, undo)
            self._say_env(env_plan, "carried")
            say(self._verify(counts, report.samples, head))
        except BaseException as exc:
            self._fail(exc, undo)
            raise
        for _original, aside in undo.moved:
            aside.unlink(missing_ok=True)
        shutil.rmtree(stage, ignore_errors=True)
        kept = self._keep_replaced_files(undo)
        say("This Maestro now holds your data. Don't run the Docker install alongside it — "
            "they share one sync identity.")
        say("Start it with `maestro start`.")
        steps = [f"restore the snapshot {snapshot}"] if snapshot else []
        steps += [f"copy the files in {kept} back over the matching names in {self.ctx.home}"] if kept else []
        if steps:
            say("To undo: " + ", then ".join(steps) + " (docs/UPDATING.md, Move from Docker to the uv install).")
        return 0

    def _keep_replaced_files(self, undo: _Undo) -> Path | None:
        """Files this import replaced stay next to the snapshot, so the move can be undone completely."""
        if not undo.saved.is_dir() or not any(p.is_file() for p in undo.saved.rglob("*")):
            shutil.rmtree(undo.saved, ignore_errors=True)
            return None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = self.ctx.home / "backups" / f"import-replaced-files-{stamp}"
        for n in range(1, 100):
            if not target.exists():
                break
            target = target.with_name(f"import-replaced-files-{stamp}-{n}")
        os.replace(undo.saved, target)
        self.say(f"Files this import replaced (for example your old sync key, settings, resumes and maestro.env) are kept in {target}")
        return target

    def _fail(self, exc: BaseException, undo: _Undo) -> None:
        """Put everything back; turn the failure into one sentence for the expected kinds."""
        shutil.rmtree(self.ctx.home / "data" / STAGE, ignore_errors=True)
        try:
            undo.restore()
        except OSError as again:
            raise ControlError(
                f"The import stopped ({_reason(exc).rstrip('.')}) and then could not put everything back "
                f"({_reason(again).rstrip('.')}). Your earlier files are in {undo.saved.parent} (the "
                f"{ASIDE} names); see docs/UPDATING.md.") from None
        if isinstance(exc, (ImportFailure, OSError, sqlite3.Error, home_mod.HomeError)):
            raise ControlError(
                f"The import stopped: {_reason(exc).rstrip('.')}. Nothing in the Docker install was changed, "
                f"and everything this import had changed in {self.ctx.home} was put back.") from None

    def _build_database(self, readable: Path, stage: Path) -> tuple[Path, RewriteReport, dict[str, int]]:
        from app.tools.backup_db import snapshot  # noqa: PLC0415  (after apply_env, R8)

        new_db = stage / control.DB_FILENAME
        os.close(os.open(new_db, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))  # 0600 from the first byte
        try:
            snapshot(readable, new_db)  # SQLite online backup, verified with integrity_check
        except RuntimeError as exc:
            raise ImportFailure(str(exc)) from None
        source = _ro(readable)
        try:
            expected = row_counts(source)
        finally:
            source.close()
        conn = sqlite3.connect(new_db)
        try:
            report = rewrite_paths(conn, self._prefixes(), os.sep)
            # The copy carries the Docker file's WAL header. Rollback mode leaves no -wal/-shm beside it
            # when it is only read (the verification below); the backend sets WAL again at its first start.
            conn.execute("PRAGMA journal_mode=DELETE")
            actual = row_counts(conn)
        finally:
            conn.close()
        if actual != expected:
            differing = sorted(t for t in expected.keys() | actual.keys() if expected.get(t) != actual.get(t))
            raise ImportFailure(f"Row counts differ after the copy in {', '.join(differing)}.")
        for suffix in ("-wal", "-shm"):
            Path(f"{new_db}{suffix}").unlink(missing_ok=True)
        return new_db, report, actual

    def _place_key(self, root: Path, undo: _Undo) -> str:
        source = root / SYNC_KEY_IN_CHECKOUT
        try:
            info = source.lstat()
        except OSError:
            return "Sync key: the Docker install has none, so sync stays off."
        if not stat.S_ISREG(info.st_mode):
            return "Sync key: skipped (it is not a regular file)."
        undo.keep(self.ctx.home / "sync-key")
        _write_private(self.ctx.home / "sync-key", source.read_bytes().strip() + b"\n")  # bytes: no decoding
        return "Sync key: placed where this install reads it (the value is not shown)."

    def _swap_in(self, new_db: Path, undo: _Undo) -> None:
        final = self.upkeep.db_file
        for original in _sidecars(final):  # a stale -wal would be replayed onto the new database
            if original.exists():
                aside = original.with_name(original.name + ASIDE)
                os.replace(original, aside)
                undo.moved.append((original, aside))
        os.replace(new_db, final)
        undo.placed = True

    def _carry_env(self, carry: dict[str, str], undo: _Undo) -> None:
        if not carry:
            return
        path = self.ctx.home / "maestro.env"
        text = path.read_text(encoding="utf-8")
        lines = [f"{name}={_env_quote(value)}" for name, value in sorted(carry.items())]
        block = "\n# Carried over from the Docker install's .env by `maestro import-docker`.\n" + "\n".join(lines) + "\n"
        undo.keep(path)
        _write_private(path, text + ("" if text.endswith("\n") else "\n") + block)

    def _verify(self, expected: dict[str, int], samples: list[str], head: str) -> str:
        db = self.upkeep.db_file
        conn = _ro(db)
        try:
            revision = _revision(conn)
            actual = row_counts(conn)
        finally:
            conn.close()
        if revision != head:
            raise ImportFailure(f"The imported database is at revision {revision}, not {head}.")
        if actual != expected:
            raise ImportFailure("The imported database's row counts differ from the Docker install's.")
        present = sum(1 for value in samples if Path(value).exists())
        sampled = f"; {present} of {len(samples)} sampled stored paths exist" if samples else ""
        return f"Verified: schema {revision} is this Maestro's, every table has the same row count{sampled}."

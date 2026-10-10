"""`maestro backup`, `migrate` and `check`.

Unit tests inject the child runner and `apply_env`; the real-child and import-order tests run
fresh subprocesses with an explicit environment and a `tmp_path` home, never the developer's.
"""
import gzip
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from maestro_cs import cli, control, maintenance, procs
from maestro_cs.control import Context, ControlError
from maestro_cs.home import Ports

BACKEND = Path(__file__).resolve().parents[2]
FIXED_NOW = datetime(2026, 10, 10, 12, 30, 5, tzinfo=timezone.utc)


def make_ctx(tmp_path, environ=None):
    home = tmp_path / "home"
    for name in ("data", "backups", "logs", "run"):
        (home / name).mkdir(parents=True, exist_ok=True)
    env = {"PATH": os.environ.get("PATH", ""), **(environ or {})}
    return Context(home, {}, Ports(18001, 13000), env, "9.9.9")


def make_db(ctx, revision=None):
    db = ctx.home / "data" / control.DB_FILENAME
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (x)")
    conn.execute("INSERT INTO t VALUES (1)")
    if revision:
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        conn.execute("INSERT INTO alembic_version VALUES (?)", (revision,))
    conn.commit()
    conn.close()
    return db


class FakeRun:
    """Stands in for subprocess.run: writes the --out file like the real tool, or fails."""

    def __init__(self, code=0, stderr=""):
        self.calls, self.code, self.stderr = [], code, stderr

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if "--out" in argv:
            Path(argv[argv.index("--out") + 1]).write_bytes(b"gz")
        return subprocess.CompletedProcess(argv, self.code, stdout="", stderr=self.stderr)


def maint(ctx, run=None, apply=None):
    said = []
    m = maintenance.Maintenance(
        ctx, run=run or FakeRun(), apply=apply or (lambda home, env: None),
        now=lambda: FIXED_NOW, say=lambda *a, **k: said.append(" ".join(str(x) for x in a)),
    )
    return m, said


# --- backup -------------------------------------------------------------------------------

def test_backup_without_a_database_says_so_and_runs_nothing(tmp_path):
    ctx = make_ctx(tmp_path)
    run = FakeRun()
    m, said = maint(ctx, run)
    assert m.backup() == 0
    assert said == ["Nothing to back up yet."]
    assert run.calls == []


def test_backup_default_path_child_argv_env_and_cwd(tmp_path):
    ctx = make_ctx(tmp_path)
    make_db(ctx)
    run = FakeRun()
    m, said = maint(ctx, run)
    assert m.backup() == 0
    expected = ctx.home / "backups" / "db-20261010T123005Z.sqlite3.gz"
    assert said == [str(expected)] and expected.is_file()
    argv, kwargs = run.calls[0]
    assert argv[1:] == ["-m", "app.tools.backup_db", "--out", str(expected)]
    assert kwargs["cwd"] == ctx.home
    assert kwargs["env"]["DATA_DIR"] == str(ctx.home / "data")  # the backend env, not the shell's


def test_backup_keeps_the_newest_five(tmp_path):
    ctx = make_ctx(tmp_path)
    make_db(ctx)
    for i in range(7):
        old = ctx.home / "backups" / f"db-2026010{i}T000000Z.sqlite3.gz"
        old.write_bytes(b"old")
        os.utime(old, (1_700_000_000 + i, 1_700_000_000 + i))
    other = ctx.home / "backups" / "notes.txt"
    other.write_text("keep me")
    m, _ = maint(ctx)
    m.backup()
    left = sorted(p.name for p in (ctx.home / "backups").glob("db-*.sqlite3.gz"))
    assert len(left) == 5
    assert "db-20261010T123005Z.sqlite3.gz" in left  # the new one is the newest
    assert "db-20260100T000000Z.sqlite3.gz" not in left and "db-20260101T000000Z.sqlite3.gz" not in left
    assert other.exists()


def test_backup_out_is_honoured_and_never_rotated_away(tmp_path):
    ctx = make_ctx(tmp_path)
    make_db(ctx)
    for i in range(6):  # six old snapshots already in backups/: --out must leave them alone
        (ctx.home / "backups" / f"db-2026010{i}T000000Z.sqlite3.gz").write_bytes(b"old")
    elsewhere = tmp_path / "usb" / "mine.sqlite3.gz"
    elsewhere.parent.mkdir()
    run = FakeRun()
    m, said = maint(ctx, run)
    assert m.backup(str(elsewhere)) == 0
    assert said == [str(elsewhere)] and elsewhere.is_file()
    assert run.calls[0][0][-1] == str(elsewhere)
    assert len(list((ctx.home / "backups").glob("db-*.sqlite3.gz"))) == 6


def test_backup_relative_out_is_relative_to_the_callers_folder(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path)
    make_db(ctx)
    monkeypatch.chdir(tmp_path)
    run = FakeRun()
    m, _ = maint(ctx, run)
    m.backup("snap.sqlite3.gz")
    assert run.calls[0][0][-1] == str(tmp_path / "snap.sqlite3.gz")  # the child's cwd is the home


def test_backup_failure_is_one_sentence_and_leaves_old_snapshots(tmp_path):
    ctx = make_ctx(tmp_path)
    make_db(ctx)
    old = ctx.home / "backups" / "db-20260101T000000Z.sqlite3.gz"
    old.write_bytes(b"old")
    m, said = maint(ctx, FakeRun(1, "Traceback...\nerror: snapshot failed integrity_check: bad"))
    with pytest.raises(ControlError, match=r"^The backup failed: snapshot failed integrity_check: bad\.$"):
        m.backup()
    assert old.exists()
    assert not (ctx.home / "backups" / "db-20261010T123005Z.sqlite3.gz").exists()


def test_backup_unrunnable_child_is_a_sentence(tmp_path):
    ctx = make_ctx(tmp_path)
    make_db(ctx)

    def boom(*a, **k):
        raise OSError(2, "No such file or directory")

    m, _ = maint(ctx, boom)
    with pytest.raises(ControlError, match="Couldn't run the backup"):
        m.backup()


def test_backup_real_child_writes_a_verified_gzip(tmp_path):
    ctx = make_ctx(tmp_path, {"PYTHONPATH": str(BACKEND)})
    make_db(ctx)
    messages = []
    m = maintenance.Maintenance(ctx, say=messages.append)  # real subprocess.run
    assert m.backup() == 0
    path = Path(messages[0])
    assert path.parent == ctx.home / "backups" and path.name.startswith("db-")
    restored = tmp_path / "restored.sqlite3"
    restored.write_bytes(gzip.decompress(path.read_bytes()))
    conn = sqlite3.connect(restored)
    try:
        assert conn.execute("SELECT x FROM t").fetchall() == [(1,)]
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_backup_refuses_an_out_that_exists_and_keeps_it_even_if_the_backup_would_fail(tmp_path):
    ctx = make_ctx(tmp_path)
    make_db(ctx)
    precious = tmp_path / "important.txt"
    precious.write_text("do not lose me")
    run = FakeRun(1, "error: boom")
    m, _ = maint(ctx, run)
    with pytest.raises(ControlError, match="already exists; choose a new file name\\.$"):
        m.backup(str(precious))
    assert precious.read_text() == "do not lose me" and run.calls == []


def test_a_failing_backup_never_unlinks_a_path_it_did_not_create(tmp_path):
    ctx = make_ctx(tmp_path)
    make_db(ctx)
    fresh = tmp_path / "new.gz"
    m, _ = maint(ctx, FakeRun(1, "error: boom"))  # FakeRun writes the file, then fails
    with pytest.raises(ControlError):
        m.backup(str(fresh))
    assert not fresh.exists()  # this run created it, so it goes


def test_default_names_in_the_same_second_get_a_suffix_and_a_failure_keeps_the_first(tmp_path):
    ctx = make_ctx(tmp_path)
    make_db(ctx)
    m, said = maint(ctx)
    m.backup()
    m.backup()
    m.backup()
    names = sorted(Path(x).name for x in said)
    assert names == ["db-20261010T123005Z-1.sqlite3.gz", "db-20261010T123005Z-2.sqlite3.gz",
                     "db-20261010T123005Z.sqlite3.gz"]
    first = ctx.home / "backups" / "db-20261010T123005Z.sqlite3.gz"
    first.write_bytes(b"good")
    failing, _ = maint(ctx, FakeRun(1, "error: boom"))
    with pytest.raises(ControlError):
        failing.backup()
    assert first.read_bytes() == b"good"


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm"])
def test_backup_refuses_the_live_database_as_the_target(tmp_path, suffix):
    ctx = make_ctx(tmp_path)
    db = make_db(ctx)
    live = Path(f"{db}{suffix}")
    before = live.read_bytes() if live.exists() else None
    run = FakeRun()
    m, _ = maint(ctx, run)
    with pytest.raises(ControlError, match="live database"):
        m.backup(str(live))
    assert run.calls == [] and (live.read_bytes() if live.exists() else None) == before


def test_backup_refuses_a_symlink_to_the_live_database(tmp_path):
    ctx = make_ctx(tmp_path)
    make_db(ctx)
    link = tmp_path / "alias.gz"
    try:
        link.symlink_to(ctx.home / "data" / control.DB_FILENAME)
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks here")
    m, _ = maint(ctx)
    with pytest.raises(ControlError):
        m.backup(str(link))


def test_backup_refuses_a_folder_as_the_target(tmp_path):
    ctx = make_ctx(tmp_path)
    make_db(ctx)
    run = FakeRun()
    m, _ = maint(ctx, run)
    with pytest.raises(ControlError, match="is a folder; give --out a file name\\.$"):
        m.backup(str(tmp_path))
    assert run.calls == []


def test_rotation_never_deletes_the_snapshot_just_written(tmp_path):
    ctx = make_ctx(tmp_path)
    make_db(ctx)
    future = 4_000_000_000
    for i in range(6):  # six others, all "newer" than the one about to be written
        old = ctx.home / "backups" / f"db-2099010{i}T000000Z.sqlite3.gz"
        old.write_bytes(b"x")
        os.utime(old, (future + i, future + i))
    m, said = maint(ctx)
    m.backup()
    kept = list((ctx.home / "backups").glob("db-*.sqlite3.gz"))
    assert len(kept) == 5 and Path(said[0]) in kept


# --- migrate ------------------------------------------------------------------------------

def test_migrate_reports_a_locked_database_in_one_sentence(tmp_path, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from app.services import seeding

    def locked():
        raise OperationalError("stmt", {}, sqlite3.OperationalError("database is locked"))

    monkeypatch.setattr(seeding, "run_migrations", locked)
    m, _ = maint(make_ctx(tmp_path))
    with pytest.raises(ControlError, match=r"^The database couldn't be migrated: database is locked\.$"):
        m.migrate()

@pytest.mark.parametrize("record", ["supervisor", "backend", "web"])
def test_migrate_refuses_while_a_live_record_exists(tmp_path, record):
    ctx = make_ctx(tmp_path)
    procs.write_record(ctx.run_dir / f"{record}.json", os.getpid())
    applied = []
    m, _ = maint(ctx, apply=lambda home, env: applied.append(home))
    with pytest.raises(ControlError, match="^Stop Maestro first; it migrates itself at start\\.$"):
        m.migrate()
    assert applied == []  # refused before the process took on the install's environment


def test_migrate_ignores_a_dead_record(tmp_path, monkeypatch):
    from app.services import seeding

    ctx = make_ctx(tmp_path)
    (ctx.run_dir / "supervisor.json").write_text(json.dumps({"pid": 2**22 + 1, "create_time": 1.0}))
    calls = []
    monkeypatch.setattr(seeding, "run_migrations", lambda: calls.append("migrate"))
    m, said = maint(ctx)
    assert m.migrate() == 0 and calls == ["migrate"]
    assert said == ["The database is up to date."]


# --- check --------------------------------------------------------------------------------

def head_of_checkout():
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def test_check_keys_and_code_head(tmp_path):
    ctx = make_ctx(tmp_path)
    make_db(ctx, revision="abc123")
    run = FakeRun()
    run_node = lambda argv, **k: subprocess.CompletedProcess(argv, 0, stdout="v22.1.0\n", stderr="")  # noqa: E731
    m, said = maint(ctx, run_node)
    assert m.check() == 0
    report = json.loads(said[0])
    assert list(report) == ["version", "home", "code_head", "db_revision", "engines", "web", "node"]
    assert report["version"] == "9.9.9" and report["home"] == str(ctx.home)
    assert report["code_head"] == head_of_checkout()
    assert report["db_revision"] == "abc123"
    assert set(report["engines"]) == {"pdflatex", "typst"}
    assert set(report["engines"]["typst"]) == {"name", "available", "version", "path", "reason", "warning"}
    assert report["web"] is False and report["node"] == "v22.1.0"
    assert run.calls == []


def test_check_without_a_database_or_node(tmp_path):
    ctx = make_ctx(tmp_path)

    def no_node(*a, **k):
        raise FileNotFoundError("node")

    m, said = maint(ctx, no_node)
    m.check()
    report = json.loads(said[0])
    assert report["db_revision"] is None and report["node"] is None


def test_check_node_failures_are_null(tmp_path):
    ctx = make_ctx(tmp_path)
    for result in (
        subprocess.CompletedProcess([], 1, stdout="v22\n", stderr=""),
        subprocess.CompletedProcess([], 0, stdout="", stderr=""),
    ):
        m, _ = maint(ctx, lambda *a, _r=result, **k: _r)
        assert m._node_version() is None

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("node", 5)

    m, _ = maint(ctx, slow)
    assert m._node_version() is None


def test_check_reports_web_when_the_bundle_exists(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "server.js").write_text("//")
    ctx = make_ctx(tmp_path, {"MAESTRO_WEB_DIR": str(web)})
    m, said = maint(ctx, lambda argv, **k: subprocess.CompletedProcess(argv, 0, stdout="v1\n", stderr=""))
    m.check()
    assert json.loads(said[0])["web"] is True


def test_db_revision_of_a_database_without_alembic_is_null(tmp_path):
    ctx = make_ctx(tmp_path)
    make_db(ctx)  # no alembic_version table
    m, _ = maint(ctx)
    assert m._db_revision() is None


# --- the verbs reach Maintenance through the CLI ------------------------------------------

class Recorder:
    calls: list = []

    def __init__(self, ctx):
        pass

    def __getattr__(self, name):
        def call(*args, **kwargs):
            Recorder.calls.append((name, args, kwargs))
            return 0
        return call


@pytest.fixture
def rec(monkeypatch, tmp_path):
    Recorder.calls = []
    monkeypatch.setenv("MAESTRO_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(maintenance, "Maintenance", Recorder)
    return Recorder


def test_cli_routes_backup_migrate_check(rec):
    assert cli.main(["backup"]) == 0
    assert cli.main(["backup", "--out", "x.gz"]) == 0
    assert cli.main(["migrate"]) == 0
    assert cli.main(["check"]) == 0
    assert rec.calls == [("backup", (None,), {}), ("backup", ("x.gz",), {}),
                         ("migrate", (), {}), ("check", (), {})]


def test_cli_turns_a_control_error_into_one_sentence(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MAESTRO_HOME", str(tmp_path / "home"))

    class Refuses:
        def __init__(self, ctx):
            pass

        def migrate(self):
            raise ControlError("Stop Maestro first; it migrates itself at start.")

    monkeypatch.setattr(maintenance, "Maintenance", Refuses)
    assert cli.main(["migrate"]) == 1
    assert capsys.readouterr().err == "Stop Maestro first; it migrates itself at start.\n"


# --- R8: the install's environment is applied before `app` is imported ---------------------

def clean_env(home):
    keep = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "HOME", "TMPDIR", "TEMP")}
    return {**keep, "PYTHONPATH": str(BACKEND), "MAESTRO_HOME": str(home)}


def run_python(code, env, cwd):
    return subprocess.run([sys.executable, "-c", code], env=env, cwd=cwd, capture_output=True,
                          text=True, timeout=120, check=False)


def test_importing_the_cli_alone_imports_no_app_module(tmp_path):
    done = run_python(
        "import sys, maestro_cs.cli\n"
        "bad = sorted(m for m in sys.modules if m == 'app' or m.startswith('app.'))\n"
        "print(bad)\n",
        clean_env(tmp_path / "home"), tmp_path,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]"


SCRIPT = """
import contextlib, io, json, os, sys
from maestro_cs import cli, home
seen = []
real = home.apply_env
def hooked(h, env):
    seen.append({"app_config_loaded": "app.config" in sys.modules, "app_loaded": "app" in sys.modules})
    real(h, env)
home.apply_env = hooked
buf, err = io.StringIO(), io.StringIO()
with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
    code = cli.main(sys.argv[1:])
from app.config import settings
print(json.dumps({"code": code, "out": buf.getvalue(), "err": err.getvalue(), "seen": seen, "cwd": os.getcwd(),
                  "data_dir": str(settings.data_dir), "database_url": settings.database_url}))
"""


def run_cli(tmp_path, *args, extra_env=None):
    home = tmp_path / "home"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    (elsewhere / ".env").write_text("DATA_DIR=/should/not/be/read\n")  # a stray cwd .env
    env = {**clean_env(home), **(extra_env or {})}
    done = subprocess.run([sys.executable, "-c", SCRIPT, *args], env=env, cwd=elsewhere,
                          capture_output=True, text=True, timeout=300, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1]), home


def test_check_binds_settings_to_the_home_after_apply_env(tmp_path):
    result, home = run_cli(tmp_path, "check")
    assert result["code"] == 0
    assert result["seen"] == [{"app_config_loaded": False, "app_loaded": False}]
    assert Path(result["data_dir"]).resolve() == (home / "data").resolve()
    assert Path(result["cwd"]).resolve() == home.resolve()  # a cwd `.env` can no longer be found
    report = json.loads(result["out"])
    assert report["home"] == str(home) and report["db_revision"] is None
    assert report["code_head"] == head_of_checkout()
    assert result["database_url"].endswith(str(Path("data") / control.DB_FILENAME))


def test_migrate_creates_the_schema_in_the_home_only(tmp_path):
    result, home = run_cli(tmp_path, "migrate")
    assert result["code"] == 0 and result["seen"][0]["app_config_loaded"] is False
    db = home / "data" / control.DB_FILENAME
    conn = sqlite3.connect(db)
    try:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == head_of_checkout()
    finally:
        conn.close()
    again, _ = run_cli(tmp_path, "check")
    assert json.loads(again["out"])["db_revision"] == head_of_checkout()


def test_migrate_on_a_database_from_a_newer_maestro_is_a_sentence_not_a_traceback(tmp_path):
    run_cli(tmp_path, "migrate")
    db = tmp_path / "home" / "data" / control.DB_FILENAME
    conn = sqlite3.connect(db)
    conn.execute("UPDATE alembic_version SET version_num = 'ffffffffffff'")
    conn.commit()
    conn.close()
    result, _ = run_cli(tmp_path, "migrate")
    assert result["code"] == 1
    assert result["err"] == ("This database was made by a newer Maestro (revision ffffffffffff); "
                             "install that version or newer.\n")

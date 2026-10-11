"""`maestro import-docker`: one-time move of a Docker checkout's data into the uv install's folder.

Every test builds a FAKE checkout in `tmp_path` (a real migrated SQLite file with `/app/...` paths in it)
and imports into a `tmp_path` home. Ports are probed through an injected function, the snapshot child is
a fake (one test runs the real child), and `apply_env` is a no-op (it chdirs); the subprocess test at the
bottom runs the real CLI with an explicit environment. No test touches a real Maestro folder or port.
"""
import gzip
import hashlib
import json
import os
import socket
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from maestro_cs import cli, control, importer, maintenance, procs
from maestro_cs import home as home_mod
from maestro_cs.control import ControlError

BACKEND = Path(__file__).resolve().parents[2]
POSIX = os.name == "posix"
SECRET = "sk-test-SECRET-do-not-print"
SYNC_KEY = "sync-key-SECRET-0123456789abcdef"
NOW_ISO = "2026-10-10 12:00:00"


def code_head():
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


@pytest.fixture(scope="module")
def template_db(tmp_path_factory):
    """One migrated database, in WAL mode like a Docker install's, copied for every test."""
    from app.services import seeding  # noqa: PLC0415

    path = tmp_path_factory.mktemp("template") / "t.sqlite3"
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("TEST_DATABASE_URL", f"sqlite:///{path}")
        seeding.run_migrations()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.close()
    return path


def sql(db, statement, params=()):
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(statement, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def insert_rows(db):
    conn = sqlite3.connect(db)
    try:
        conn.execute("INSERT INTO base_resumes (slug, data_json, pdf_path, tex_path, role_category, countries) "
                     "VALUES ('main', '{}', '/app/base_resumes/main.pdf', '/app/base_resumes/main.tex', 'x', '[]')")
        conn.execute("INSERT INTO base_resumes (slug, data_json, role_category, countries, render_error) "
                     "VALUES ('second', '{}', 'x', '[]', 'could not write /app/base_resumes/second.tex: boom')")
        for n in (1, 2):
            conn.execute("INSERT INTO jobs (id, raw_text, raw_text_hash) VALUES (?, 'jd text', ?)", (f"j{n}", f"h{n}"))
            conn.execute(
                "INSERT INTO applications (id, job_id, base_resume, artifact_dir, pdf_path, tex_path) "
                "VALUES (?, ?, 'main', ?, ?, ?)",
                (f"a{n}", f"j{n}", f"/app/applications/a{n}/", f"/app/applications/a{n}/resume.pdf",
                 f"/app/applications/a{n}/resume.tex"))
        conn.execute("INSERT INTO kb_entities (id, kind, title) VALUES ('e1', 'job', 't')")
        conn.execute("INSERT INTO kb_documents (id, entity_id, filename, size_bytes, file_path, text_content, ingest_status) "
                     "VALUES ('d1', 'e1', 'cv.txt', 3, '/app/kb_documents/cv.txt', 'x', 'done')")
        conn.execute("INSERT INTO qa_entries (id, application_id, kind, pdf_path) "
                     "VALUES ('q1', 'a1', 'screening', '/app/applications/a1/qa.pdf')")
        conn.commit()
    finally:
        conn.close()


DOCKER_ENV = f"""# compose settings
OPENAI_API_KEY={SECRET}
GEMINI_API_KEY=
FAST_MODEL=gpt-test
SYNC_PUBLIC_HOST=mac.example.ts.net
HTTPS_PROXY=http://proxy.example:3128
BACKEND_HOST_PORT=18123
FRONTEND_HOST_PORT=13123
POSTGRES_PASSWORD=pg-secret-do-not-carry
DATA_DIR=/app/data
DATABASE_URL=sqlite:////app/data/x.sqlite3
ALLOWED_HOSTS=localhost,backend
MAESTRO_CS_UPLOAD_HOST_ROOT=/Users/x/uploads
COMPOSE_PROJECT_NAME=whatever
APP_UID=501
IMAGE_TAG=0.8.0
SQLITE_JOURNAL_MODE=DELETE
"""


def write(path, text="x", mode=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode() if isinstance(text, str) else text)
    if mode is not None and POSIX:
        path.chmod(mode)
    return path


def make_checkout(tmp_path, template_db, *, env=DOCKER_ENV, rows=True):
    root = tmp_path / "docker-checkout"
    write(root / "docker-compose.yml", "services: {}\n")
    db = root / "data" / control.DB_FILENAME
    db.parent.mkdir(parents=True)
    db.write_bytes(template_db.read_bytes())
    if rows:
        insert_rows(db)
    write(root / "applications" / "a1" / "resume.pdf", "pdf-a1")
    write(root / "applications" / "a1" / "resume.tex", "tex-a1")
    write(root / "applications" / "a2" / "resume.pdf", "pdf-a2", 0o600)
    write(root / "base_resumes" / "main.json", '{"basics": {}}')
    write(root / "base_resumes" / "main.pdf", "pdf-main")
    write(root / "kb_documents" / "cv.txt", "cv text")
    write(root / "exports" / "all.json", "{}")
    write(root / "settings" / "persona.md", "persona")
    write(root / "settings" / "secrets" / "sync-key", SYNC_KEY + "\n", 0o600)
    write(root / "settings" / "secrets" / "job-site-login.json", '{"login": "secret"}', 0o600)
    write(root / "data" / "backups" / "old.sqlite3.gz", "old snapshot")
    write(root / "data" / "handoff" / "h.txt", "handoff")
    if env is not None:
        write(root / ".env", env)
    return root


def tree_hash(root):
    """Every path (files, dirs, symlink targets) with a content hash: any write shows up."""
    out = {}
    for path in sorted(Path(root).rglob("*")):
        rel = str(path.relative_to(root))
        if path.is_symlink():
            out[rel] = ("link", os.readlink(path))
        elif path.is_dir():
            out[rel] = ("dir",)
        else:
            out[rel] = ("file", hashlib.sha256(path.read_bytes()).hexdigest())
    return out


def make_ctx(tmp_path, **extra_env):
    env = {"PATH": os.environ.get("PATH", ""), "MAESTRO_HOME": str(tmp_path / "home"),
           "MAESTRO_PORT": "18001", "MAESTRO_WEB_PORT": "13000", **extra_env}
    return control.load_context(env)


class FakeRun:
    """Stands in for the snapshot child: writes the --out file like the real tool."""

    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        Path(argv[argv.index("--out") + 1]).write_bytes(b"gz")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


def importer_for(ctx, *, free=lambda port: True, answers=None, run=None):
    said = []

    def say(*parts, **kwargs):
        said.append(" ".join(str(x) for x in parts))

    upkeep = maintenance.Maintenance(ctx, run=run or FakeRun(), apply=lambda home, env: None, say=say)
    probes = importer.Probes(free, lambda url, **k: (answers or {}).get(url))
    return importer.Importer(ctx, upkeep, probes, say), said


@pytest.fixture
def world(tmp_path, template_db):
    ctx = make_ctx(tmp_path)
    checkout = make_checkout(tmp_path, template_db)
    return ctx, checkout


def uv_db(ctx):
    return ctx.home / "data" / control.DB_FILENAME


def seed_uv_db(ctx, template_db, *, job=False, example_only=False):
    uv_db(ctx).write_bytes(template_db.read_bytes())
    if job:
        sql(uv_db(ctx), "INSERT INTO jobs (id, raw_text, raw_text_hash) VALUES ('mine', 'my job', 'hm')")
    if example_only:
        sql(uv_db(ctx), "INSERT INTO base_resumes (slug, data_json, role_category, countries) "
                        "VALUES ('example', '{}', 'x', '[]')")


# --- the happy path ---------------------------------------------------------------------------

@pytest.fixture
def imported(world):
    """A fake checkout imported into a fresh home; `before` is the checkout's tree hash from before."""
    ctx, checkout = world
    before = tree_hash(checkout)
    imp, said = importer_for(ctx)
    assert imp.run(str(checkout), replace=False, dry_run=False) == 0
    return SimpleNamespace(ctx=ctx, home=ctx.home, checkout=checkout, before=before, out="\n".join(said),
                           db=uv_db(ctx))


def test_import_leaves_only_the_database_in_data(imported):
    # Checked before any test code opens the database: closing the last connection would tidy -wal/-shm.
    assert [p.name for p in (imported.home / "data").iterdir()] == [control.DB_FILENAME]
    assert imported.db.read_bytes()[18:20] == b"\x01\x01"  # rollback mode; the backend switches to WAL at its first start


def test_import_copies_every_table_with_the_same_rows(imported):
    src = imported.checkout / "data" / control.DB_FILENAME
    tables = [t for (t,) in sql(src, "SELECT name FROM sqlite_master WHERE type='table'")]
    for table in tables:
        assert sql(imported.db, f'SELECT count(*) FROM "{table}"') == sql(src, f'SELECT count(*) FROM "{table}"'), table
    assert sql(imported.db, "SELECT version_num FROM alembic_version") == [(code_head(),)]
    assert sql(imported.db, "PRAGMA integrity_check") == [("ok",)]


def test_import_rewrites_stored_paths_to_the_home(imported):
    home, db = imported.home, imported.db
    assert sql(db, "SELECT pdf_path, tex_path FROM base_resumes WHERE slug='main'") == [
        (f"{home}/base_resumes/main.pdf", f"{home}/base_resumes/main.tex")]
    assert sql(db, "SELECT artifact_dir FROM applications WHERE id='a1'") == [(f"{home}/applications/a1/",)]
    assert sql(db, "SELECT file_path FROM kb_documents") == [(f"{home}/kb_documents/cv.txt",)]
    assert sql(db, "SELECT pdf_path FROM qa_entries") == [(f"{home}/applications/a1/qa.pdf",)]
    # Free text that merely mentions /app/ is not a path column value and stays as written.
    assert sql(db, "SELECT render_error FROM base_resumes WHERE slug='second'") == [
        ("could not write /app/base_resumes/second.tex: boom",)]


def test_import_copies_the_five_folders_and_leaves_old_snapshots_behind(imported):
    home = imported.home
    assert (home / "applications" / "a1" / "resume.pdf").read_text() == "pdf-a1"
    assert (home / "base_resumes" / "main.json").read_text() == '{"basics": {}}'
    assert (home / "kb_documents" / "cv.txt").read_text() == "cv text"
    assert (home / "exports" / "all.json").read_text() == "{}"
    assert (home / "settings" / "persona.md").read_text() == "persona"
    assert (home / "settings" / "secrets" / "job-site-login.json").exists()
    assert not (home / "data" / "backups").exists() and not (home / "data" / "handoff").exists()


def test_import_places_the_sync_key_once_where_the_install_reads_it(imported):
    home = imported.home
    assert (home / "sync-key").read_text().strip() == SYNC_KEY
    assert not (home / "settings" / "secrets" / "sync-key").exists()


@pytest.mark.skipif(not POSIX, reason="POSIX modes")
def test_import_keeps_everything_owner_only(imported):
    home = imported.home
    modes = {path: stat.S_IMODE(path.stat().st_mode) for path in (
        home / "sync-key", imported.db, home / "applications" / "a2" / "resume.pdf",
        home / "applications" / "a1", home / "maestro.env")}
    assert modes == {home / "sync-key": 0o600, imported.db: 0o600,
                     home / "applications" / "a2" / "resume.pdf": 0o600,
                     home / "applications" / "a1": 0o700, home / "maestro.env": 0o600}


def test_import_only_reads_the_checkout_and_prints_no_secret(imported):
    assert tree_hash(imported.checkout) == imported.before
    assert "This Maestro now holds your data." in imported.out
    assert "Don't run the Docker install alongside it" in imported.out
    assert "Rewrote" in imported.out and "applications.artifact_dir" in imported.out
    assert SECRET not in imported.out and SYNC_KEY not in imported.out


def test_env_keys_the_backend_understands_are_carried(imported):
    values = home_mod.read_env_file(imported.home / "maestro.env")
    assert values["OPENAI_API_KEY"] == SECRET and values["FAST_MODEL"] == "gpt-test"
    assert values["SYNC_PUBLIC_HOST"] == "mac.example.ts.net" and values["HTTPS_PROXY"] == "http://proxy.example:3128"
    assert (imported.home / "maestro.env").read_text().startswith("# Read by `maestro start`")  # the template's comments stay


def test_docker_only_env_keys_are_left_out(imported):
    values = home_mod.read_env_file(imported.home / "maestro.env")
    refused = ("BACKEND_HOST_PORT", "FRONTEND_HOST_PORT", "POSTGRES_PASSWORD", "DATA_DIR", "DATABASE_URL",
               "ALLOWED_HOSTS", "MAESTRO_CS_UPLOAD_HOST_ROOT", "COMPOSE_PROJECT_NAME", "APP_UID", "IMAGE_TAG",
               "SQLITE_JOURNAL_MODE", "GEMINI_API_KEY")
    assert [name for name in refused if name in values] == []


def test_env_keys_are_named_in_the_summary_but_never_their_values(imported):
    assert "OPENAI_API_KEY" in imported.out and "FAST_MODEL" in imported.out
    assert "pg-secret" not in imported.out and "proxy.example" not in imported.out


def test_env_never_overwrites_a_key_the_install_already_sets(world):
    ctx, checkout = world
    with open(ctx.home / "maestro.env", "a", encoding="utf-8") as handle:
        handle.write("FAST_MODEL=my-own-model\n")
    imp, said = importer_for(ctx)
    imp.run(str(checkout), replace=False, dry_run=False)
    values = home_mod.read_env_file(ctx.home / "maestro.env")
    assert values["FAST_MODEL"] == "my-own-model" and values["OPENAI_API_KEY"] == SECRET
    assert any("FAST_MODEL" in line and "already" in line for line in said)
    assert "gpt-test" not in "\n".join(said)


def test_env_values_survive_awkward_characters(tmp_path, template_db):
    nasty = {"FAST_MODEL": "with space", "SMART_MODEL": "has # hash", "CHAT_MODEL": "it's quoted",
             "OPENAI_BASE_URL": 'say "hi"', "LANGFUSE_HOST": "back\\slash", "SYNC_PUBLIC_HOST": "dollar $HOME ${X}",
             "LANGFUSE_PUBLIC_KEY": "=equals="}
    ctx = make_ctx(tmp_path)
    checkout = make_checkout(tmp_path, template_db, env=None)
    # The Docker file holds them single-quoted (backslash and quote escaped), which python-dotenv reads literally.
    (checkout / ".env").write_text("".join(
        "{}='{}'\n".format(k, v.replace("\\", "\\\\").replace("'", "\\'")) for k, v in nasty.items()))
    imp, _ = importer_for(ctx)
    imp.run(str(checkout), replace=False, dry_run=False)
    values = home_mod.read_env_file(ctx.home / "maestro.env")
    assert {k: values[k] for k in nasty} == nasty


def test_no_env_file_in_the_checkout_is_fine(tmp_path, template_db):
    ctx = make_ctx(tmp_path)
    checkout = make_checkout(tmp_path, template_db, env=None)
    imp, said = importer_for(ctx)
    assert imp.run(str(checkout), replace=False, dry_run=False) == 0
    assert any(".env" in line and "no" in line.lower() for line in said)


def test_import_without_a_sync_key_leaves_the_install_without_one(world):
    ctx, checkout = world
    (checkout / "settings" / "secrets" / "sync-key").unlink()
    imp, said = importer_for(ctx)
    assert imp.run(str(checkout), replace=False, dry_run=False) == 0
    assert not (ctx.home / "sync-key").exists()
    assert any("sync key" in line.lower() and "no" in line.lower() for line in said)


def test_docker_files_win_and_uv_only_files_stay(world):
    ctx, checkout = world
    write(ctx.home / "base_resumes" / "main.json", "uv fresh copy")
    write(ctx.home / "base_resumes" / "mine.json", "uv only")
    imp, _ = importer_for(ctx)
    imp.run(str(checkout), replace=False, dry_run=False)
    assert (ctx.home / "base_resumes" / "main.json").read_text() == '{"basics": {}}'
    assert (ctx.home / "base_resumes" / "mine.json").read_text() == "uv only"


@pytest.mark.skipif(not POSIX, reason="symlinks need privileges on Windows")
def test_symlinks_in_the_checkout_are_never_followed(tmp_path, template_db):
    ctx = make_ctx(tmp_path)
    checkout = make_checkout(tmp_path, template_db)
    outside = tmp_path / "outside"
    write(outside / "private.txt", "not part of Maestro")
    os.symlink(outside, checkout / "applications" / "escape")
    os.symlink(outside / "private.txt", checkout / "applications" / "a1" / "link.txt")
    imp, said = importer_for(ctx)
    imp.run(str(checkout), replace=False, dry_run=False)
    assert not (ctx.home / "applications" / "escape").exists()
    assert not (ctx.home / "applications" / "a1" / "link.txt").exists()
    assert (ctx.home / "applications" / "a1" / "resume.pdf").exists()
    assert any("symlink" in line and "2" in line for line in said)


@pytest.mark.skipif(not POSIX, reason="symlinks need privileges on Windows")
def test_a_symlinked_destination_folder_is_refused_not_written_through(world, tmp_path):
    ctx, checkout = world
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (ctx.home / "applications").rmdir()
    os.symlink(elsewhere, ctx.home / "applications")
    imp, _ = importer_for(ctx)
    with pytest.raises(ControlError, match="symlink"):
        imp.run(str(checkout), replace=False, dry_run=False)
    assert list(elsewhere.iterdir()) == []


def test_a_checkout_database_with_unmerged_wal_pages_is_read_without_touching_the_checkout(tmp_path, template_db):
    ctx = make_ctx(tmp_path)
    checkout = make_checkout(tmp_path, template_db, rows=False)
    db = checkout / "data" / control.DB_FILENAME
    live = sqlite3.connect(db)  # keeps -wal/-shm alive, as a Docker stack that stopped uncleanly would
    live.execute("PRAGMA wal_autocheckpoint=0")
    live.execute("INSERT INTO jobs (id, raw_text, raw_text_hash) VALUES ('w1', 'in the wal only', 'hw')")
    live.commit()
    try:
        assert (db.parent / (db.name + "-wal")).stat().st_size > 0
        before = tree_hash(checkout)
        imp, _ = importer_for(ctx)
        assert imp.run(str(checkout), replace=False, dry_run=False) == 0
        assert tree_hash(checkout) == before  # main file, -wal and -shm alike
    finally:
        live.close()
    assert sql(uv_db(ctx), "SELECT raw_text FROM jobs WHERE id='w1'") == [("in the wal only",)]


def test_the_checkout_database_is_never_opened_only_its_bytes_are_read(tmp_path, template_db, monkeypatch):
    # A read-only SQLite connection still creates -wal and -shm beside a WAL database; so none is opened there.
    ctx = make_ctx(tmp_path)
    checkout = make_checkout(tmp_path, template_db)
    opened = []
    real_connect = sqlite3.connect

    def watch(target, *a, **k):
        opened.append(str(target))
        return real_connect(target, *a, **k)

    monkeypatch.setattr(sqlite3, "connect", watch)
    before = tree_hash(checkout)
    imp, _ = importer_for(ctx)
    assert imp.run(str(checkout), replace=False, dry_run=False) == 0
    monkeypatch.undo()
    assert tree_hash(checkout) == before
    assert opened and not any(str(checkout) in target or checkout.resolve().as_uri() in target for target in opened)


def test_a_database_that_changes_while_it_is_read_stops_the_import(world, monkeypatch):
    ctx, checkout = world
    db = checkout / "data" / control.DB_FILENAME
    real_copy = importer.shutil.copyfile

    def copy_then_touch(src, dst, *a, **k):
        result = real_copy(src, dst, *a, **k)
        if Path(src) == db:  # a running stack writing while we read
            with open(db, "ab") as handle:
                handle.write(b"\0" * 4096)
        return result

    monkeypatch.setattr(importer.shutil, "copyfile", copy_then_touch)
    imp, _ = importer_for(ctx)
    with pytest.raises(ControlError, match=r"changed while it was being read.*docker compose down"):
        imp.run(str(checkout), replace=False, dry_run=False)
    assert not uv_db(ctx).exists()


# --- refusals: one sentence, nothing changed --------------------------------------------------

def assert_refused(world, match, *, free=lambda port: True, answers=None, mutate=None):
    ctx, checkout = world
    if mutate:
        mutate(ctx, checkout)
    before_home, before_checkout = tree_hash(ctx.home), tree_hash(checkout)
    imp, said = importer_for(ctx, free=free, answers=answers)
    with pytest.raises(ControlError, match=match) as info:
        imp.run(str(checkout), replace=False, dry_run=False)
    assert "\n" not in str(info.value)
    assert tree_hash(ctx.home) == before_home and tree_hash(checkout) == before_checkout
    assert not any("Saved" in line for line in said)
    return str(info.value)


def test_refuses_a_folder_without_the_database(world):
    assert_refused(world, r"has no data/maestro_cs\.sqlite3", mutate=lambda c, k: (k / "data" / control.DB_FILENAME).unlink())


def test_refuses_a_folder_without_docker_compose_yml(world):
    assert_refused(world, r"docker-compose\.yml", mutate=lambda c, k: (k / "docker-compose.yml").unlink())


def test_refuses_a_missing_folder(world, tmp_path):
    ctx, _ = world
    imp, _ = importer_for(ctx)
    with pytest.raises(ControlError, match="is not a folder"):
        imp.run(str(tmp_path / "nope"), replace=False, dry_run=False)


def test_refuses_while_this_maestro_is_running(world):
    def running(ctx, checkout):
        procs.write_record(ctx.run_dir / "supervisor.json", os.getpid())

    assert_refused(world, r"Stop Maestro first \(`maestro stop`\)", mutate=running)


def test_refuses_when_the_docker_stack_answers_on_its_ports(world):
    ctx, checkout = world  # the checkout's .env moves the Docker ports to 18123 / 13123
    msg = assert_refused(world, "docker compose down", free=lambda port: port != 18123,
                         answers={"http://127.0.0.1:18123/api/version": {"version": "0.8.0"}})
    assert "18123" in msg and str(checkout) in msg and "0.8.0" in msg


def test_refuses_on_the_default_docker_ports_when_the_env_does_not_move_them(tmp_path, template_db):
    ctx = make_ctx(tmp_path)
    checkout = make_checkout(tmp_path, template_db, env="FAST_MODEL=x\n")
    imp, _ = importer_for(ctx, free=lambda port: port != 3000)
    with pytest.raises(ControlError, match=r"port 3000.*docker compose down"):
        imp.run(str(checkout), replace=False, dry_run=False)


def test_refuses_when_another_program_holds_a_uv_port(world):
    assert_refused(world, r"Port 18001 is in use by another program", free=lambda port: port != 18001)


def test_refuses_a_database_from_an_older_maestro(world):
    def older(ctx, checkout):
        sql(checkout / "data" / control.DB_FILENAME, "UPDATE alembic_version SET version_num = ?",
            (_older_revision(),))

    msg = assert_refused(world, "older Maestro", mutate=older)
    assert "Update the Docker install" in msg and _older_revision() in msg


def _older_revision():
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    revisions = [s.revision for s in ScriptDirectory.from_config(cfg).walk_revisions()]
    return revisions[1]


def test_refuses_a_database_from_a_newer_maestro(world):
    def newer(ctx, checkout):
        sql(checkout / "data" / control.DB_FILENAME, "UPDATE alembic_version SET version_num = 'ffffffffffff'")

    msg = assert_refused(world, "newer Maestro", mutate=newer)
    assert "ffffffffffff" in msg and "install a newer Maestro" in msg


def test_refuses_a_file_that_is_not_a_maestro_database(world):
    def junk(ctx, checkout):
        (checkout / "data" / control.DB_FILENAME).write_bytes(b"this is not sqlite" * 100)
        for name in ("-wal", "-shm"):
            (checkout / "data" / (control.DB_FILENAME + name)).unlink(missing_ok=True)

    assert_refused(world, "not a Maestro database", mutate=junk)


def test_refuses_a_home_that_already_holds_data(world, template_db):
    ctx, _ = world
    seed_uv_db(ctx, template_db, job=True)
    msg = assert_refused(world, "already holds your data")
    assert "--replace" in msg


def test_the_demo_example_resume_alone_is_not_user_data(world, template_db):
    ctx, checkout = world
    seed_uv_db(ctx, template_db, example_only=True)
    imp, _ = importer_for(ctx)
    assert imp.run(str(checkout), replace=False, dry_run=False) == 0


def test_replace_snapshots_the_uv_data_first(world, template_db):
    ctx, checkout = world
    seed_uv_db(ctx, template_db, job=True)
    run = FakeRun()
    imp, said = importer_for(ctx, run=run)
    assert imp.run(str(checkout), replace=True, dry_run=False) == 0
    assert len(run.calls) == 1 and run.calls[0][1:3] == ["-m", "app.tools.backup_db"]
    snapshot = Path(run.calls[0][-1])
    assert snapshot.parent == ctx.home / "backups" and snapshot.is_file()
    assert f"Saved your current data first: {snapshot}" in said


def test_replace_swaps_in_the_docker_data_and_clears_the_aside_files(world, template_db):
    ctx, checkout = world
    seed_uv_db(ctx, template_db, job=True)
    imp, _ = importer_for(ctx)
    assert imp.run(str(checkout), replace=True, dry_run=False) == 0
    assert sql(uv_db(ctx), "SELECT count(*) FROM jobs WHERE id='mine'") == [(0,)]
    assert sql(uv_db(ctx), "SELECT count(*) FROM jobs") == [(2,)]
    assert not list((ctx.home / "data").glob("*.before-import*"))


def test_replace_with_the_real_snapshot_child_writes_a_real_undo_point(tmp_path, template_db):
    ctx = make_ctx(tmp_path, PYTHONPATH=str(BACKEND))
    checkout = make_checkout(tmp_path, template_db)
    seed_uv_db(ctx, template_db, job=True)
    imp, _ = importer_for(ctx, run=subprocess.run)
    assert imp.run(str(checkout), replace=True, dry_run=False) == 0
    [snapshot] = list((ctx.home / "backups").glob("db-*.sqlite3.gz"))
    restored = tmp_path / "undo.sqlite3"
    restored.write_bytes(gzip.decompress(snapshot.read_bytes()))
    assert sql(restored, "SELECT id FROM jobs") == [("mine",)]


# --- dry run ----------------------------------------------------------------------------------

def test_dry_run_changes_nothing_and_snapshots_nothing(world, template_db):
    ctx, checkout = world
    seed_uv_db(ctx, template_db, example_only=True)
    before_home, before_checkout = tree_hash(ctx.home), tree_hash(checkout)
    run = FakeRun()
    imp, _ = importer_for(ctx, run=run)
    assert imp.run(str(checkout), replace=False, dry_run=True) == 0
    assert tree_hash(ctx.home) == before_home and tree_hash(checkout) == before_checkout
    assert run.calls == []


def test_dry_run_prints_the_plan_without_secrets_or_a_success_claim(world):
    ctx, checkout = world
    imp, said = importer_for(ctx)
    imp.run(str(checkout), replace=False, dry_run=True)
    out = "\n".join(said)
    assert "Dry run" in out and "Nothing was changed" in out
    assert "applications.artifact_dir" in out and "OPENAI_API_KEY" in out
    assert SECRET not in out and "This Maestro now holds your data" not in out


def test_dry_run_still_applies_every_refusal(world):
    ctx, checkout = world
    imp, _ = importer_for(ctx, free=lambda port: port != 18123)
    with pytest.raises(ControlError, match="docker compose down"):
        imp.run(str(checkout), replace=False, dry_run=True)


def test_dry_run_stops_on_a_path_that_has_no_place_in_the_new_folder(world):
    ctx, checkout = world
    sql(checkout / "data" / control.DB_FILENAME,
        "UPDATE applications SET pdf_path = '/app/logs/x.pdf' WHERE id = 'a1'")
    imp, _ = importer_for(ctx)
    with pytest.raises(ControlError, match=r"applications\.pdf_path.*/app/logs/"):
        imp.run(str(checkout), replace=False, dry_run=True)


# --- failure: the uv database comes back -------------------------------------------------------

def test_a_failure_after_the_swap_puts_the_uv_database_back(world, template_db, monkeypatch):
    ctx, checkout = world
    seed_uv_db(ctx, template_db, job=True)
    sql(uv_db(ctx), "PRAGMA journal_mode=WAL")
    before = (uv_db(ctx)).read_bytes()
    imp, said = importer_for(ctx)

    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(importer.Importer, "_carry_env", boom)
    with pytest.raises(ControlError) as info:
        imp.run(str(checkout), replace=True, dry_run=False)
    text = str(info.value)
    assert "\n" not in text and "No space left on device" in text
    assert "put back" in text and "stay" not in text
    assert uv_db(ctx).read_bytes() == before
    assert [p.name for p in (ctx.home / "data").iterdir()] == [control.DB_FILENAME]


def test_a_failure_before_the_swap_never_touches_the_uv_database(world, template_db, monkeypatch):
    ctx, checkout = world
    seed_uv_db(ctx, template_db, job=True)
    before = uv_db(ctx).read_bytes()
    imp, _ = importer_for(ctx)

    def boom(*a, **k):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(importer, "copy_tree", boom)
    with pytest.raises(ControlError, match="Permission denied"):
        imp.run(str(checkout), replace=True, dry_run=False)
    assert uv_db(ctx).read_bytes() == before
    assert not [p for p in (ctx.home / "data").iterdir() if p.name != control.DB_FILENAME]


def test_a_failed_verification_restores_the_uv_database(world, template_db, monkeypatch):
    ctx, checkout = world
    seed_uv_db(ctx, template_db, job=True)
    imp, _ = importer_for(ctx)
    monkeypatch.setattr(importer.Importer, "_verify", lambda *a, **k: (_ for _ in ()).throw(
        importer.ImportFailure("Row counts differ in jobs.")))
    with pytest.raises(ControlError, match="Row counts differ in jobs"):
        imp.run(str(checkout), replace=True, dry_run=False)
    assert sql(uv_db(ctx), "SELECT id FROM jobs") == [("mine",)]


def test_a_failed_first_import_leaves_no_database_behind(world, monkeypatch):
    ctx, checkout = world
    imp, _ = importer_for(ctx)
    monkeypatch.setattr(importer.Importer, "_verify", lambda *a, **k: (_ for _ in ()).throw(
        importer.ImportFailure("boom")))
    with pytest.raises(ControlError, match="boom"):
        imp.run(str(checkout), replace=False, dry_run=False)
    assert not uv_db(ctx).exists()


# --- JSON columns: mentions are reported, never rewritten ---------------------------------------

def put_json_mention(checkout):
    sql(checkout / "data" / control.DB_FILENAME,
        "UPDATE applications SET customized_json = ? WHERE id = 'a1'",
        ('{"pdf": "/app/applications/a1/resume.pdf"}',))


def test_import_reports_app_paths_inside_json_columns_and_leaves_them_alone(world):
    ctx, checkout = world
    put_json_mention(checkout)
    imp, said = importer_for(ctx)
    assert imp.run(str(checkout), replace=False, dry_run=False) == 0
    assert "1 values in applications.customized_json mention /app/ and are left as written." in "\n".join(said)
    assert sql(uv_db(ctx), "SELECT customized_json FROM applications WHERE id='a1'") == [
        ('{"pdf": "/app/applications/a1/resume.pdf"}',)]


def test_dry_run_reports_the_json_mentions_too(world):
    ctx, checkout = world
    put_json_mention(checkout)
    imp, said = importer_for(ctx)
    imp.run(str(checkout), replace=False, dry_run=True)
    assert "1 values in applications.customized_json mention /app/ and are left as written." in "\n".join(said)


def test_plan_counts_json_and_text_mentions_but_rewrites_only_text_paths(tmp_path):
    db = rewrite_db(tmp_path, {"a": ["/app/applications/x", "see /app/data/y"]})
    sql(db, "UPDATE a SET j = ? WHERE id = 1", ('{"p": "/app/exports/z"}',))
    sql(db, "UPDATE a SET j = ? WHERE id = 2", ("[1, 2]",))
    conn = sqlite3.connect(db)
    report = importer.rewrite_paths(conn, PREFIXES, "/")
    conn.close()
    assert report.counts == {("a", "p"): 1}
    assert report.mentions == {("a", "p"): 1, ("a", "j"): 1}
    assert sql(db, "SELECT j FROM a WHERE id = 1") == [('{"p": "/app/exports/z"}',)]


# --- the path rewrite --------------------------------------------------------------------------

def rewrite_db(tmp_path, rows):
    db = tmp_path / "r.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE a (id INTEGER PRIMARY KEY, p TEXT, q VARCHAR(10), n INTEGER, j JSON)")
    conn.execute("CREATE TABLE b (id INTEGER PRIMARY KEY, path TEXT)")
    for table, values in rows.items():
        for value in values:
            conn.execute(f"INSERT INTO {table} ({'p' if table == 'a' else 'path'}) VALUES (?)", (value,))
    conn.commit()
    conn.close()
    return db


PREFIXES = {d: f"/home/u/Maestro/{d}/" for d in importer.REWRITE_DIRS}


def test_rewrite_changes_only_values_that_start_with_the_container_prefix(tmp_path):
    db = rewrite_db(tmp_path, {"a": ["/app/applications/x/y.pdf", "/app/data/z", "app/applications/no", "/application/x",
                                     "see /app/applications/inside", "/APP/applications/case", None],
                               "b": ["/app/exports/e.json"]})
    conn = sqlite3.connect(db)
    report = importer.rewrite_paths(conn, PREFIXES, "/")
    conn.close()
    assert [v for (v,) in sql(db, "SELECT p FROM a ORDER BY id")] == [
        "/home/u/Maestro/applications/x/y.pdf", "/home/u/Maestro/data/z", "app/applications/no", "/application/x",
        "see /app/applications/inside", "/APP/applications/case", None]
    assert sql(db, "SELECT path FROM b") == [("/home/u/Maestro/exports/e.json",)]
    assert report.counts == {("a", "p"): 2, ("b", "path"): 1}
    assert report.mentions == {("a", "p"): 1}  # "see /app/applications/inside" is left as written, and counted


def test_rewrite_is_a_noop_second_time(tmp_path):
    db = rewrite_db(tmp_path, {"a": ["/app/applications/x"]})
    conn = sqlite3.connect(db)
    importer.rewrite_paths(conn, PREFIXES, "/")
    again = importer.rewrite_paths(conn, PREFIXES, "/")
    conn.close()
    assert again.counts == {}


def test_rewrite_on_windows_writes_the_native_home_path(tmp_path):
    home = PureWindowsPath("C:/Users/ann/AppData/Roaming/Maestro Career Studio")
    prefixes = {d: str(home / d) + "\\" for d in importer.REWRITE_DIRS}
    db = rewrite_db(tmp_path, {"a": ["/app/applications/a1/resume.pdf", "/app/applications/a1/"]})
    conn = sqlite3.connect(db)
    importer.rewrite_paths(conn, prefixes, "\\")
    conn.close()
    assert [v for (v,) in sql(db, "SELECT p FROM a ORDER BY id")] == [
        "C:\\Users\\ann\\AppData\\Roaming\\Maestro Career Studio\\applications\\a1\\resume.pdf",
        "C:\\Users\\ann\\AppData\\Roaming\\Maestro Career Studio\\applications\\a1\\"]


def test_rewrite_refuses_a_container_path_with_no_place_to_go_and_changes_nothing(tmp_path):
    db = rewrite_db(tmp_path, {"a": ["/app/applications/ok", "/app/logs/llm/x.json"]})
    conn = sqlite3.connect(db)
    with pytest.raises(importer.ImportFailure, match=r"a\.p holds a path under /app/logs/"):
        importer.rewrite_paths(conn, PREFIXES, "/")
    conn.close()
    assert sql(db, "SELECT p FROM a ORDER BY id") == [("/app/applications/ok",), ("/app/logs/llm/x.json",)]


def test_plan_reports_the_same_counts_without_writing(tmp_path):
    db = rewrite_db(tmp_path, {"a": ["/app/applications/x", "/app/applications/y"]})
    conn = sqlite3.connect(db)
    report = importer.plan_rewrites(conn)
    conn.close()
    assert report.counts == {("a", "p"): 2}
    assert sql(db, "SELECT p FROM a ORDER BY id") == [("/app/applications/x",), ("/app/applications/y",)]


# --- the command line --------------------------------------------------------------------------

def test_cli_routes_import_docker(monkeypatch, tmp_path):
    seen = []

    class Rec:
        def __init__(self, ctx):
            pass

        def run(self, checkout, replace, dry_run):
            seen.append((checkout, replace, dry_run))
            return 0

    monkeypatch.setenv("MAESTRO_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(importer, "Importer", Rec)
    assert cli.main(["import-docker", "/some/checkout"]) == 0
    assert cli.main(["import-docker", "rel", "--replace", "--dry-run"]) == 0
    assert seen == [("/some/checkout", False, False), ("rel", True, True)]


def test_cli_import_failure_is_one_sentence_on_stderr(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MAESTRO_HOME", str(tmp_path / "home"))
    assert cli.main(["import-docker", str(tmp_path / "missing")]) == 1
    err = capsys.readouterr().err
    assert err.endswith("\n") and err.count("\n") == 1 and "is not a folder" in err


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


SCRIPT = """
import contextlib, io, json, os, sys
from maestro_cs import cli, home
seen = []
real = home.apply_env
def hooked(h, env):
    seen.append({"app_config_loaded": "app.config" in sys.modules})
    real(h, env)
home.apply_env = hooked
buf, err = io.StringIO(), io.StringIO()
with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
    code = cli.main(sys.argv[1:])
print(json.dumps({"code": code, "out": buf.getvalue(), "err": err.getvalue(), "seen": seen, "cwd": os.getcwd()}))
"""


def run_real_import(tmp_path, template_db):
    """The real command in a fresh interpreter: explicit environment, a stray `.env` in its cwd, a relative path."""
    ports = [free_port() for _ in range(4)]
    checkout = make_checkout(tmp_path, template_db, env=f"FAST_MODEL=gpt-test\nBACKEND_HOST_PORT={ports[2]}\n"
                                                        f"FRONTEND_HOST_PORT={ports[3]}\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / ".env").write_text("DATA_DIR=/should/not/be/read\n")
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "HOME", "TMPDIR", "TEMP")}
    env.update(PYTHONPATH=str(BACKEND), MAESTRO_HOME=str(tmp_path / "home"),
               MAESTRO_PORT=str(ports[0]), MAESTRO_WEB_PORT=str(ports[1]))
    before = tree_hash(checkout)
    rel = os.path.relpath(checkout, elsewhere)  # must resolve before the chdir into the home
    done = subprocess.run([sys.executable, "-c", SCRIPT, "import-docker", rel], env=env, cwd=elsewhere,
                          capture_output=True, text=True, timeout=300, check=False)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1]), tmp_path / "home", checkout, before


def test_the_real_command_applies_the_install_env_before_importing_app(tmp_path, template_db):
    result, *_ = run_real_import(tmp_path, template_db)
    assert result["code"] == 0, result
    assert result["seen"] == [{"app_config_loaded": False}]
    assert "This Maestro now holds your data." in result["out"]


def test_the_real_command_writes_into_the_home_and_only_reads_the_checkout(tmp_path, template_db):
    result, home, checkout, before = run_real_import(tmp_path, template_db)
    db = home / "data" / control.DB_FILENAME
    assert result["code"] == 0
    assert sql(db, "SELECT version_num FROM alembic_version") == [(code_head(),)]
    assert sql(db, "SELECT artifact_dir FROM applications WHERE id='a1'") == [(f"{home}/applications/a1/",)]
    assert tree_hash(checkout) == before


# --- a failed or undone --replace: the key, settings and env go back with the database ----------

def uv_files(ctx):
    write(ctx.home / "sync-key", "uv-own-key\n", 0o600)
    write(ctx.home / "settings" / "persona.md", "uv persona")
    write(ctx.home / "base_resumes" / "main.json", "uv main resume")
    write(ctx.home / "base_resumes" / "mine.json", "uv only")


def read_env_bytes(ctx):
    return (ctx.home / "maestro.env").read_bytes()


@pytest.fixture
def failed_replace(world, template_db, monkeypatch):
    """A --replace over a uv home with its own key, settings, resumes, env and database; `_verify` fails."""
    ctx, checkout = world
    seed_uv_db(ctx, template_db, job=True)
    uv_files(ctx)
    before = SimpleNamespace(env=read_env_bytes(ctx), db=uv_db(ctx).read_bytes())
    monkeypatch.setattr(importer.Importer, "_verify", lambda *a, **k: (_ for _ in ()).throw(
        importer.ImportFailure("boom")))
    imp, _ = importer_for(ctx)
    with pytest.raises(ControlError, match="boom") as info:
        imp.run(str(checkout), replace=True, dry_run=False)
    return SimpleNamespace(ctx=ctx, before=before, error=str(info.value))


def test_a_failed_replace_puts_back_the_key_settings_and_resumes(failed_replace):
    home = failed_replace.ctx.home
    assert (home / "sync-key").read_text() == "uv-own-key\n"
    assert (home / "settings" / "persona.md").read_text() == "uv persona"
    assert (home / "base_resumes" / "main.json").read_text() == "uv main resume"
    assert (home / "base_resumes" / "mine.json").read_text() == "uv only"


def test_a_failed_replace_puts_back_env_and_database_and_removes_what_it_added(failed_replace):
    home = failed_replace.ctx.home
    assert read_env_bytes(failed_replace.ctx) == failed_replace.before.env
    assert uv_db(failed_replace.ctx).read_bytes() == failed_replace.before.db
    assert not (home / "settings" / "secrets").exists() and not (home / "applications" / "a1").exists()
    assert [p.name for p in (home / "data").iterdir()] == [control.DB_FILENAME]


def test_a_failed_replace_says_so_in_one_sentence(failed_replace):
    assert "put back" in failed_replace.error and "\n" not in failed_replace.error


def test_a_successful_replace_keeps_the_replaced_files_next_to_the_snapshot_and_says_where(world, template_db):
    ctx, checkout = world
    seed_uv_db(ctx, template_db, job=True)
    uv_files(ctx)
    imp, said = importer_for(ctx)
    assert imp.run(str(checkout), replace=True, dry_run=False) == 0
    [saved] = [p for p in (ctx.home / "backups").iterdir() if p.is_dir()]
    assert (saved / "sync-key").read_text() == "uv-own-key\n"
    assert (saved / "settings" / "persona.md").read_text() == "uv persona"
    assert (saved / "base_resumes" / "main.json").read_text() == "uv main resume"
    assert str(saved) in "\n".join(said)
    assert [p.name for p in (ctx.home / "data").iterdir()] == [control.DB_FILENAME]


def test_a_first_import_saves_only_the_settings_file_it_appended_to(imported):
    [saved] = [p for p in (imported.home / "backups").iterdir() if p.is_dir()]
    assert [p.name for p in saved.rglob("*") if p.is_file()] == ["maestro.env"]


# --- an interrupted run leaves files that block the next one --------------------------------------

@pytest.mark.parametrize("leftover", ["maestro_cs.sqlite3.before-import", "maestro_cs.sqlite3-wal.before-import",
                                      ".importing", ".before-import"])
def test_leftovers_of_an_interrupted_import_are_refused_naming_them_and_the_snapshot(world, leftover):
    ctx, checkout = world
    target = ctx.home / "data" / leftover
    target.mkdir() if leftover.startswith(".") else target.write_bytes(b"x")
    snapshot = write(ctx.home / "backups" / "db-20261010T000000Z.sqlite3.gz", "gz")
    before = tree_hash(ctx.home)
    imp, said = importer_for(ctx)
    with pytest.raises(ControlError, match="interrupted") as info:
        imp.run(str(checkout), replace=True, dry_run=False)
    assert leftover in str(info.value) and snapshot.name in str(info.value) and "\n" not in str(info.value)
    assert tree_hash(ctx.home) == before and said == []


def test_leftovers_are_refused_by_a_dry_run_too(world):
    ctx, checkout = world
    write(ctx.home / "data" / "maestro_cs.sqlite3.before-import", "x")
    imp, _ = importer_for(ctx)
    with pytest.raises(ControlError, match="interrupted"):
        imp.run(str(checkout), replace=False, dry_run=True)


# --- the old database's uncheckpointed -wal goes aside with it ------------------------------------

def test_an_uncheckpointed_wal_in_the_uv_home_never_replays_into_the_imported_database(world, template_db):
    ctx, checkout = world
    seed_uv_db(ctx, template_db, job=True)
    sql(uv_db(ctx), "PRAGMA journal_mode=WAL")
    live = sqlite3.connect(uv_db(ctx))  # keeps its -wal/-shm; the row below exists only there
    live.execute("PRAGMA wal_autocheckpoint=0")
    live.execute("INSERT INTO jobs (id, raw_text, raw_text_hash) VALUES ('only-in-wal', 'x', 'hw')")
    live.commit()
    try:
        assert Path(f"{uv_db(ctx)}-wal").stat().st_size > 0
        imp, _ = importer_for(ctx)
        assert imp.run(str(checkout), replace=True, dry_run=False) == 0
    finally:
        live.close()
    assert sql(uv_db(ctx), "SELECT id FROM jobs ORDER BY id") == [("j1",), ("j2",)]


# --- modes, keys, notes ---------------------------------------------------------------------------

@pytest.mark.skipif(not POSIX, reason="POSIX modes")
def test_a_world_readable_source_file_ends_up_owner_only(tmp_path, template_db):
    ctx = make_ctx(tmp_path)
    checkout = make_checkout(tmp_path, template_db)
    write(checkout / "applications" / "a1" / "open.pdf", "x", 0o644)
    write(checkout / "applications" / "a1" / "run.sh", "x", 0o755)
    imp, _ = importer_for(ctx)
    imp.run(str(checkout), replace=False, dry_run=False)
    modes = [stat.S_IMODE((ctx.home / "applications" / "a1" / n).stat().st_mode) for n in ("open.pdf", "run.sh")]
    assert modes == [0o600, 0o700]


def test_a_sync_key_that_is_not_utf8_is_copied_as_bytes_without_a_traceback(world):
    ctx, checkout = world
    (checkout / "settings" / "secrets" / "sync-key").write_bytes(b"\xff\xfekey\n")
    imp, said = importer_for(ctx)
    assert imp.run(str(checkout), replace=False, dry_run=False) == 0
    assert (ctx.home / "sync-key").read_bytes() == b"\xff\xfekey\n"


def test_a_carried_host_docker_internal_value_gets_a_note_naming_only_the_key(tmp_path, template_db):
    ctx = make_ctx(tmp_path)
    checkout = make_checkout(tmp_path, template_db, env="OPENAI_BASE_URL=http://host.docker.internal:11434/v1\nFAST_MODEL=m\n")
    imp, said = importer_for(ctx)
    imp.run(str(checkout), replace=False, dry_run=False)
    note = "OPENAI_BASE_URL points at host.docker.internal, which only works inside Docker; change it to localhost in maestro.env."
    assert note in said and "11434" not in "\n".join(said)


def test_the_dry_run_gives_the_same_note(tmp_path, template_db):
    ctx = make_ctx(tmp_path)
    checkout = make_checkout(tmp_path, template_db, env="LANGFUSE_HOST=http://host.docker.internal:3000\n")
    imp, said = importer_for(ctx)
    imp.run(str(checkout), replace=False, dry_run=True)
    assert any(line.startswith("LANGFUSE_HOST points at host.docker.internal") for line in said)


def test_a_restore_that_itself_fails_names_where_the_earlier_files_are(world, template_db, monkeypatch):
    ctx, checkout = world
    seed_uv_db(ctx, template_db, job=True)
    monkeypatch.setattr(importer.Importer, "_verify", lambda *a, **k: (_ for _ in ()).throw(
        importer.ImportFailure("boom")))
    monkeypatch.setattr(importer._Undo, "restore", lambda self: (_ for _ in ()).throw(OSError(13, "Permission denied")))
    imp, _ = importer_for(ctx)
    with pytest.raises(ControlError, match=r"could not put everything back.*\.before-import") as info:
        imp.run(str(checkout), replace=True, dry_run=False)
    assert "boom" in str(info.value) and "\n" not in str(info.value)

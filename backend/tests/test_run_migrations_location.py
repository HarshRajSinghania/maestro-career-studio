"""`run_migrations` finds the migrations next to the code, not through alembic.ini.

In a wheel there is no alembic.ini and the process cwd is the user's home folder; the
script location must come from this module's own path.
"""

import sqlite3
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.services import seeding

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


def _head() -> str:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    return ScriptDirectory.from_config(cfg).get_current_head()


def test_run_migrations_works_from_any_cwd_without_alembic_ini(tmp_path, monkeypatch):
    db = tmp_path / "t.sqlite3"
    # env.py prefers TEST_DATABASE_URL (conftest sets it session-wide), and `settings`
    # is built at import, so only this variable redirects the migration to a scratch file.
    monkeypatch.setenv("TEST_DATABASE_URL", f"sqlite:///{db}")
    monkeypatch.chdir(tmp_path)

    seen: list[object] = []
    original_init = Config.__init__

    def recording_init(self, file_=None, *args, **kwargs):
        seen.append(file_)
        original_init(self, file_, *args, **kwargs)

    monkeypatch.setattr(Config, "__init__", recording_init)

    seeding.run_migrations()

    assert seen and all(f is None for f in seen)
    with sqlite3.connect(db) as conn:
        (version,) = conn.execute("select version_num from alembic_version").fetchone()
    assert version == _head()

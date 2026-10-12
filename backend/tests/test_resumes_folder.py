"""Visible copies of finished resumes ({#inv-resumes-folder-one-way})."""
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import settings as app_settings
from app.models.application import Application
from app.models.job import Job
from app.models.setting import Setting
from app.services import resumes_folder as rf
from app.services.sync import registry


def _job(db, *, company="Acme", title="Data Scientist"):
    job = Job(
        raw_text="Need Python",
        raw_text_hash=uuid4().hex,
        extracted_json={"title": title, "company": company},
        title=title,
        company=company,
        role_category="data_scientist",
        extracted_at=datetime.now(UTC),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _application(db, job, pdf: Path | None = None):
    application = Application(
        job_id=job.id,
        base_resume="data_scientist",
        status="draft",
        pdf_path=str(pdf) if pdf is not None else None,
    )
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


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


def test_folder_label_fits_the_filename_byte_limit():
    day = date(2026, 1, 2)
    name = rf.folder_label("会" * 80, "職" * 80, day)
    assert len(name.encode()) <= 200 and name.endswith(" (2026-01-02)")
    # Accents and emoji are cut on character boundaries, never mid-byte.
    name = rf.folder_label("é" * 80, "🚀" * 80, day)
    assert len(name.encode()) <= 200 and name.endswith(" (2026-01-02)")
    assert "�" not in name and name.encode().decode() == name
    # Latin names keep their character caps.
    assert rf.folder_label("C" * 80, "T" * 80, day) == f"{'C' * 35} – {'T' * 48} (2026-01-02)"


def test_folder_name_is_chosen_under_the_write_lock(db_session, folder_on, monkeypatch):
    calls = []
    lock, label = rf.begin_write, rf.folder_label
    monkeypatch.setattr(rf, "begin_write", lambda db: (calls.append("lock"), lock(db))[1])
    monkeypatch.setattr(rf, "folder_label", lambda *a: (calls.append("label"), label(*a))[1])
    job = _job(db_session)
    application = _application(db_session, job)
    rf._folder_for(db_session, application, job)
    assert calls == ["lock", "label"]  # the lock comes before the name is chosen
    rf._folder_for(db_session, application, job)
    assert calls == ["lock", "label"]  # a saved name returns without taking the lock again

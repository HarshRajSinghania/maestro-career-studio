"""Visible copies of finished resumes ({#inv-resumes-folder-one-way})."""
from datetime import UTC, datetime
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

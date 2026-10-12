"""Shared setup for the resumes-folder tests (service, render hooks, sync arrival)."""
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import settings as app_settings
from app.models.application import Application
from app.models.job import Job
from tests.pdf_fixtures import write_blank_pdf


def make_job(db, *, company="Acme", title="Data Scientist"):
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


def make_application(db, job, pdf: Path | None = None):
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


def make_pdf(tmp_path, name="Riley_Quill_DataScientist_Resume.pdf"):
    path = tmp_path / "work" / "Acme_DS_20261011_abcd1234" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    write_blank_pdf(path, 1)
    return path

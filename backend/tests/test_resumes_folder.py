"""Visible copies of finished resumes ({#inv-resumes-folder-one-way})."""
import builtins
import os
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.config import settings as app_settings
from app.models.application import Application
from app.models.job import Job
from app.models.qa_entry import QAEntry
from app.models.setting import Setting
from app.services import resumes_folder as rf
from app.services.sync import registry
from tests.pdf_fixtures import write_blank_pdf


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


def _pdf(tmp_path, name="Riley_Quill_DataScientist_Resume.pdf"):
    path = tmp_path / "work" / "Acme_DS_20261011_abcd1234" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    write_blank_pdf(path, 1)
    return path


def test_copies_resume_and_cover_letter_pdfs_only(db_session, folder_on, tmp_path):
    resume = _pdf(tmp_path)
    (resume.parent / "Riley_Quill_DataScientist_Resume.log").write_text("x")
    letter = _pdf(tmp_path, "Riley_Quill_DataScientist_CoverLetter.pdf")
    app = _application(db_session, _job(db_session, company="Acme", title="Data Scientist"), pdf=resume)
    db_session.add(QAEntry(application_id=app.id, kind="cover_letter", answer="Hi", pdf_path=str(letter)))
    db_session.commit()

    copied = rf.copy_application(app.id)

    assert copied is not None and copied.read_bytes() == resume.read_bytes()
    assert sorted(p.name for p in copied.parent.iterdir()) == [letter.name, resume.name]
    assert copied.parent.parent == folder_on


def test_second_copy_is_a_no_op(db_session, folder_on, tmp_path):
    app = _application(db_session, _job(db_session), pdf=_pdf(tmp_path))
    first = rf.copy_application(app.id)
    before = first.stat().st_mtime_ns
    assert rf.copy_application(app.id) == first
    assert first.stat().st_mtime_ns == before
    assert not [p for p in first.parent.iterdir() if p.name.startswith(".maestro-")]


def test_never_reads_the_copy(db_session, folder_on, tmp_path, monkeypatch):
    app = _application(db_session, _job(db_session), pdf=_pdf(tmp_path))
    first = rf.copy_application(app.id)
    os.utime(first, ns=(0, 0))  # force a rewrite on the next call
    real_open = builtins.open

    def guarded(file, *args, **kwargs):
        assert Path(file) != first, "the copy must never be opened for reading"
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded)
    assert rf.copy_application(app.id) == first


def test_failure_is_recorded_not_raised(db_session, folder_on, tmp_path, monkeypatch):
    app = _application(db_session, _job(db_session), pdf=_pdf(tmp_path))

    def refuse(*args, **kwargs):
        raise PermissionError("no")

    with monkeypatch.context() as broken:  # unlike monkeypatch.undo(), this keeps folder_on
        broken.setattr(rf.shutil, "copyfile", refuse)
        assert rf.copy_application(app.id) is None
    db_session.expire_all()
    assert rf.last_error(db_session)["code"] == "write"
    assert rf.copy_application(app.id) is not None
    db_session.expire_all()
    assert rf.last_error(db_session) is None  # a success clears it


def test_vanished_custom_root_is_not_recreated(db_session, folder_on, tmp_path):
    custom = tmp_path / "usb"
    db_session.add(Setting(key=rf.PATH_KEY, value=str(custom)))
    db_session.commit()
    app = _application(db_session, _job(db_session), pdf=_pdf(tmp_path))
    assert rf.copy_application(app.id) is None
    assert not custom.exists()
    db_session.expire_all()
    assert rf.last_error(db_session)["code"] == "missing"


def test_off_and_unavailable_copy_nothing(db_session, folder_on, tmp_path, monkeypatch):
    app = _application(db_session, _job(db_session), pdf=_pdf(tmp_path))
    db_session.add(Setting(key=rf.ENABLED_KEY, value="off"))
    db_session.commit()
    assert rf.copy_application(app.id) is None
    assert not folder_on.exists()
    monkeypatch.setattr(app_settings, "resumes_folder_default", None)
    assert rf.copy_application(app.id) is None


def test_no_pdf_means_no_folder(db_session, folder_on):
    app = _application(db_session, _job(db_session))
    assert rf.copy_application(app.id) is None
    assert not folder_on.exists()

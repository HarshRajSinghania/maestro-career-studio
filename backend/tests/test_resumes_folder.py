"""Visible copies of finished resumes ({#inv-resumes-folder-one-way})."""
import builtins
import os
import shutil
import threading
from datetime import date
from pathlib import Path

import pytest

from sqlalchemy import select

from app.config import settings as app_settings
from app.models.qa_entry import QAEntry
from app.models.setting import Setting
from app.routers import qa
from app.services import base_resume_data, pdf_render
from app.services import resumes_folder as rf
from app.services.sync import registry
from tests.pdf_fixtures import write_blank_pdf
from tests.resumes_folder_fixtures import make_application, make_job, make_pdf


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
    for key in (rf.ENABLED_KEY, rf.PATH_KEY, rf.LAST_ERROR_KEY, rf.APP_KEY_PREFIX + "ab", rf.COPIED_KEY_PREFIX + "ab"):
        assert registry.is_profile_row(Setting(key=key, value="x")) is False


def test_folder_label_is_readable_and_safe():
    assert rf.folder_label("ComEd", "Data Scientist", date(2026, 10, 11)) == "ComEd – Data Scientist (2026-10-11)"
    assert rf.folder_label('A/B: "C"*', "Sr. ML <Eng>|?", date(2026, 1, 2)) == "A B C – Sr. ML Eng (2026-01-02)"
    assert rf.folder_label("", None, date(2026, 1, 2)) == "Untitled – Untitled (2026-01-02)"
    assert rf.folder_label("Co...  ", "Role. ", date(2026, 1, 2)) == "Co – Role (2026-01-02)"
    long = rf.folder_label("C" * 80, "T" * 80, date(2026, 1, 2))
    assert len(long) < 100 and long.endswith("(2026-01-02)")


def test_folder_name_is_saved_and_clashes_get_a_number(db_session, folder_on):
    job = make_job(db_session, company="Acme", title="Data Scientist")
    first, second = make_application(db_session, job), make_application(db_session, job)
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
    job = make_job(db_session)
    application = make_application(db_session, job)
    rf._folder_for(db_session, application, job)
    assert calls == ["lock", "label"]  # the lock comes before the name is chosen
    rf._folder_for(db_session, application, job)
    assert calls == ["lock", "label"]  # a saved name returns without taking the lock again


def test_copies_resume_and_cover_letter_pdfs_only(db_session, folder_on, tmp_path):
    resume = make_pdf(tmp_path)
    (resume.parent / "Riley_Quill_DataScientist_Resume.log").write_text("x")
    letter = make_pdf(tmp_path, "Riley_Quill_DataScientist_CoverLetter.pdf")
    app = make_application(db_session, make_job(db_session, company="Acme", title="Data Scientist"), pdf=resume)
    db_session.add(QAEntry(application_id=app.id, kind="cover_letter", answer="Hi", pdf_path=str(letter)))
    db_session.commit()

    copied = rf.copy_application(app.id)

    assert copied is not None and copied.read_bytes() == resume.read_bytes()
    assert sorted(p.name for p in copied.parent.iterdir()) == [letter.name, resume.name]
    assert copied.parent.parent == folder_on


def test_second_copy_is_a_no_op(db_session, folder_on, tmp_path, monkeypatch):
    app = make_application(db_session, make_job(db_session), pdf=make_pdf(tmp_path))
    first = rf.copy_application(app.id)
    before = first.stat().st_mtime_ns
    writes = []
    real_copyfile = rf.shutil.copyfile
    monkeypatch.setattr(rf.shutil, "copyfile", lambda *a, **k: writes.append(a) or real_copyfile(*a, **k))
    assert rf.copy_application(app.id) == first
    assert writes == []  # copystat restores the mtime, so only a spy proves nothing was rewritten
    assert first.stat().st_mtime_ns == before
    assert not [p for p in first.parent.iterdir() if p.name.startswith(".maestro-")]


def test_never_reads_the_copy(db_session, folder_on, tmp_path, monkeypatch):
    app = make_application(db_session, make_job(db_session), pdf=make_pdf(tmp_path))
    first = rf.copy_application(app.id)
    os.utime(first, ns=(0, 0))  # force a rewrite on the next call
    real_open = builtins.open

    def guarded(file, *args, **kwargs):
        assert Path(file) != first, "the copy must never be opened for reading"
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded)
    assert rf.copy_application(app.id) == first


def test_failure_is_recorded_not_raised(db_session, folder_on, tmp_path, monkeypatch):
    app = make_application(db_session, make_job(db_session), pdf=make_pdf(tmp_path))

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
    app = make_application(db_session, make_job(db_session), pdf=make_pdf(tmp_path))
    assert rf.copy_application(app.id) is None
    assert not custom.exists()
    db_session.expire_all()
    assert rf.last_error(db_session)["code"] == "missing"


def test_every_start_copies_what_an_earlier_failure_missed(db_session, folder_on, tmp_path):
    custom = tmp_path / "usb"
    db_session.add(Setting(key=rf.PATH_KEY, value=str(custom)))  # the drive isn't there
    db_session.commit()
    app = make_application(db_session, make_job(db_session), pdf=make_pdf(tmp_path))
    assert rf.copy_application(app.id) is None
    db_session.expire_all()
    assert rf.last_error(db_session)["code"] == "missing"
    assert rf.backfill() is None  # still unplugged: the pass ends on the folder, nothing raised
    custom.mkdir()  # drive back
    assert rf.backfill() == 1  # the next start fills it
    assert (next(custom.iterdir()) / "Riley_Quill_DataScientist_Resume.pdf").is_file()
    db_session.expire_all()
    assert rf.last_error(db_session) is None


def test_validate_folder_refuses_the_data_folder_in_any_letter_case(folder_on, tmp_path):
    inside = tmp_path / "work" / "Sub"
    inside.mkdir()
    shouting = tmp_path / "WORK" / "sub"
    if not shouting.is_dir():
        pytest.skip("case-sensitive filesystem: a mixed-case path is a different folder here")
    with pytest.raises(rf.FolderRefused, match="outside Maestro's own data folder"):
        rf.validate_folder(str(shouting))


def test_the_data_folder_check_compares_case_insensitively_on_macos_and_windows(tmp_path):
    owner = tmp_path / "Library" / "Application Support"
    for platform, refused in (("darwin", True), ("win32", True), ("linux", False)):
        assert rf._inside(tmp_path / "library" / "application support" / "x", owner, platform) is refused
    assert rf._inside(tmp_path / "Library" / "Application Support" / "x", owner, "linux") is True
    assert rf._inside(tmp_path / "Library" / "Elsewhere", owner, "darwin") is False


def test_off_and_unavailable_copy_nothing(db_session, folder_on, tmp_path, monkeypatch):
    app = make_application(db_session, make_job(db_session), pdf=make_pdf(tmp_path))
    db_session.add(Setting(key=rf.ENABLED_KEY, value="off"))
    db_session.commit()
    assert rf.copy_application(app.id) is None
    assert not folder_on.exists()
    monkeypatch.setattr(app_settings, "resumes_folder_default", None)
    assert rf.copy_application(app.id) is None


def test_no_pdf_means_no_folder(db_session, folder_on):
    app = make_application(db_session, make_job(db_session))
    assert rf.copy_application(app.id) is None
    assert not folder_on.exists()


def test_same_size_rerender_replaces_the_copy(db_session, folder_on, tmp_path):
    pdf = make_pdf(tmp_path)
    pdf.write_bytes(b"A" * 64)
    app = make_application(db_session, make_job(db_session), pdf=pdf)
    copied = rf.copy_application(app.id)
    stat = pdf.stat()
    pdf.write_bytes(b"B" * 64)  # same size, different bytes
    os.utime(pdf, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))  # one second later
    assert rf.copy_application(app.id) == copied
    assert copied.read_bytes() == b"B" * 64


def test_failure_to_record_the_failure_still_does_not_raise(db_session, folder_on, tmp_path, monkeypatch):
    app = make_application(db_session, make_job(db_session), pdf=make_pdf(tmp_path))

    def refuse(*args, **kwargs):
        raise PermissionError("no")

    def cannot_open(*args, **kwargs):
        raise OSError("database file unavailable")

    monkeypatch.setattr(rf.shutil, "copyfile", refuse)
    real_session = rf.SessionLocal
    calls = []

    def session_factory():
        calls.append(1)
        if len(calls) > 1:  # the first session runs the copy; the one that records the error fails
            cannot_open()
        return real_session()

    monkeypatch.setattr(rf, "SessionLocal", session_factory)
    assert rf.copy_application(app.id) is None
    assert len(calls) == 2


def test_a_broken_availability_check_still_does_not_raise(db_session, folder_on, tmp_path, monkeypatch):
    job = make_job(db_session)
    app = make_application(db_session, job, pdf=make_pdf(tmp_path))

    def boom():
        raise RuntimeError("sync status unreadable")

    monkeypatch.setattr(rf, "availability", boom)
    assert rf.copy_application(app.id) is None
    rf.copy_job(job.id)  # returns, raises nothing
    assert not folder_on.exists()


# ---- hook points: render, cover letter, sync arrival (Task 5) ----


def _stub_render(monkeypatch):
    """Stand in for the TeX compiler, as test_applications_router does."""
    monkeypatch.setattr(
        base_resume_data,
        "load_base_resume",
        lambda slug, session=None: {
            "contact": {"name": "Riley Quill", "email": "x@y.com"},
            "summary": "Data scientist",
            "skills": [],
            "experience": [],
            "projects": [],
            "education": [],
            "certifications": [],
        },
    )
    monkeypatch.setattr(
        pdf_render,
        "render_document",
        lambda data, **kw: pdf_render.RenderedDoc("latex", "rendered tex"),
    )

    def fake_compile_pdf(tex_text, out_dir, stem="resume"):
        (out_dir / f"{stem}.tex").write_text(tex_text, encoding="utf-8")
        path = out_dir / f"{stem}.pdf"
        write_blank_pdf(path, 1)
        return path

    monkeypatch.setattr(pdf_render, "compile_pdf", fake_compile_pdf)


def test_render_endpoint_copies_and_reports_where(db_session, folder_on, monkeypatch, client):
    application = make_application(db_session, make_job(db_session))
    _stub_render(monkeypatch)
    response = client.post(f"/api/applications/{application.id}/render")
    assert response.status_code == 200
    body = response.json()
    copy = Path(body["copy_path"])
    assert copy.is_file() and folder_on in copy.parents
    assert copy.read_bytes() == Path(body["pdf_path"]).read_bytes()


def test_render_hands_the_copy_the_id_it_was_given_not_a_refreshed_row(db_session, folder_on, monkeypatch):
    """After the commit `application.id` is expired: reading it would reload the row inside render's try."""
    from app.services import application_render

    application = make_application(db_session, make_job(db_session))
    _stub_render(monkeypatch)
    seen = []
    monkeypatch.setattr(application_render.resumes_folder, "copy_application", lambda i: seen.append(i))
    given = application.id
    application_render.render_resume(db_session, given)
    assert len(seen) == 1 and seen[0] is given


def test_render_survives_a_failed_copy(db_session, folder_on, monkeypatch, client):
    application = make_application(db_session, make_job(db_session))
    _stub_render(monkeypatch)

    def refuse(*args, **kwargs):
        raise PermissionError("no")

    monkeypatch.setattr(rf.shutil, "copyfile", refuse)
    response = client.post(f"/api/applications/{application.id}/render")
    assert response.status_code == 200
    assert response.json()["copy_path"] is None
    assert rf.last_error(db_session)["code"] == "write"


def test_render_reports_no_copy_when_the_folder_is_off(db_session, tmp_path, monkeypatch, client):
    monkeypatch.setattr(app_settings, "resumes_folder_default", None)
    monkeypatch.setattr(app_settings, "applications_dir", tmp_path)
    application = make_application(db_session, make_job(db_session))
    _stub_render(monkeypatch)
    response = client.post(f"/api/applications/{application.id}/render")
    assert response.status_code == 200
    assert response.json()["copy_path"] is None


def test_cover_letter_render_copies(db_session, folder_on, tmp_path, monkeypatch, client):
    application = make_application(db_session, make_job(db_session))
    entry = QAEntry(application_id=application.id, kind="cover_letter", prompt="cover letter",
                    answer="Dear Hiring Manager,\n\nI'm excited.\n\nSincerely,\nJane")
    db_session.add(entry)
    db_session.commit()
    db_session.refresh(entry)
    monkeypatch.setattr(
        qa.base_resume_data,
        "load_base_resume",
        lambda slug, session=None: {"contact": {"name": "Jane Doe", "email": "j@x.com"}},
    )

    def fake_compile(source, out_dir, stem="cover_letter", **kwargs):
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{stem}.pdf"
        path.write_bytes(b"%PDF test")
        return path

    monkeypatch.setattr(qa.pdf_render, "compile_cover_letter_pdf", fake_compile)
    response = client.post(f"/api/qa/{entry.id}/render")
    assert response.status_code == 200
    copies = [p for p in folder_on.rglob("*.pdf")]
    assert [p.name for p in copies if p.name.endswith("_CoverLetter.pdf")]


def test_backfill_copies_everything_and_a_second_pass_writes_nothing(db_session, folder_on, tmp_path, monkeypatch):
    for i in range(3):
        make_application(
            db_session, make_job(db_session, company=f"Co{i}"), pdf=make_pdf(tmp_path, f"R{i}.pdf")
        )
    assert rf.backfill() == 3
    assert len(list(folder_on.iterdir())) == 3
    writes = []
    real_copyfile = rf.shutil.copyfile
    monkeypatch.setattr(rf.shutil, "copyfile", lambda *a, **k: (writes.append(a), real_copyfile(*a, **k))[1])
    assert rf.backfill() == 3  # still covers them all, by stat alone
    assert writes == []


def _count_writes(monkeypatch):
    writes = []
    real_copyfile = rf.shutil.copyfile
    monkeypatch.setattr(rf.shutil, "copyfile", lambda *a, **k: (writes.append(a), real_copyfile(*a, **k))[1])
    return writes


def test_backfill_does_not_bring_back_a_folder_the_user_deleted(db_session, folder_on, tmp_path, monkeypatch):
    app = make_application(db_session, make_job(db_session), pdf=make_pdf(tmp_path))
    rf.backfill()
    folder = rf.copy_path_for(app.id).parent
    shutil.rmtree(folder)
    writes = _count_writes(monkeypatch)
    rf.backfill()
    assert not folder.exists() and writes == []
    assert rf.copy_application(app.id) is not None  # a render, or Show in Finder, brings it back
    assert folder.is_dir()


def test_backfill_leaves_a_renamed_copy_alone(db_session, folder_on, tmp_path, monkeypatch):
    app = make_application(db_session, make_job(db_session), pdf=make_pdf(tmp_path))
    rf.backfill()
    copy = rf.copy_path_for(app.id)
    copy.rename(copy.with_name("My tidy name.pdf"))
    writes = _count_writes(monkeypatch)
    rf.backfill()
    assert sorted(p.name for p in copy.parent.iterdir()) == ["My tidy name.pdf"] and writes == []


def test_backfill_copies_a_source_that_changed_since_the_record(db_session, folder_on, tmp_path):
    pdf = make_pdf(tmp_path)
    app = make_application(db_session, make_job(db_session), pdf=pdf)
    rf.backfill()
    copy = rf.copy_path_for(app.id)
    copy.unlink()
    stat = pdf.stat()
    pdf.write_bytes(b"%PDF a re-render made while copies were off")
    os.utime(pdf, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    rf.backfill()
    assert copy.read_bytes() == pdf.read_bytes()


def test_a_failed_copy_writes_no_record_and_the_next_pass_retries(db_session, folder_on, tmp_path, monkeypatch):
    app = make_application(db_session, make_job(db_session), pdf=make_pdf(tmp_path))
    with monkeypatch.context() as broken:
        broken.setattr(rf, "_place", lambda *a: (_ for _ in ()).throw(PermissionError("no")))
        rf.backfill()
    db_session.expire_all()
    assert rf._get(db_session, rf.COPIED_KEY_PREFIX + app.id.hex) is None
    rf.backfill()
    assert rf.copy_path_for(app.id) is not None


def test_a_usable_root_clears_the_error_a_missing_drive_left_even_if_no_copy_runs(
    db_session, folder_on, tmp_path, monkeypatch
):
    custom = tmp_path / "usb"
    custom.mkdir()
    _set_root(custom)
    make_application(db_session, make_job(db_session), pdf=make_pdf(tmp_path))
    assert rf.backfill() == 1  # records what was copied
    custom.rename(tmp_path / "usb-unplugged")
    assert rf.backfill() is None  # start with the drive out
    db_session.expire_all()
    assert rf.last_error(db_session)["code"] == "missing"
    (tmp_path / "usb-unplugged").rename(custom)  # drive back; every application matches its record
    writes = _count_writes(monkeypatch)
    assert rf.backfill() == 1
    db_session.expire_all()
    assert rf.last_error(db_session) is None and writes == []


def test_a_copy_runs_while_holding_its_applications_lock(db_session, folder_on, tmp_path, monkeypatch):
    app = make_application(db_session, make_job(db_session), pdf=make_pdf(tmp_path))
    app_id, seen, real_place = app.id, [], rf._place
    monkeypatch.setattr(rf, "_place", lambda src, folder: (seen.append(rf._lock_for(app_id).locked()), real_place(src, folder))[1])
    assert rf.copy_application(app_id) is not None
    assert seen == [True]
    assert not rf._lock_for(app_id).locked()  # released afterwards, failure or not


def test_two_first_copies_of_one_application_both_succeed_and_leave_one_record(db_session, folder_on, tmp_path):
    app = make_application(db_session, make_job(db_session), pdf=make_pdf(tmp_path))
    app_id, results = app.id, []
    start = threading.Barrier(2, timeout=10)

    def copy():
        start.wait()
        results.append(rf._attempt(app_id))  # each call opens its own session

    threads = [threading.Thread(target=copy) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert [ok for ok, _ in results] == [True, True]
    db_session.expire_all()
    records = db_session.scalars(select(Setting).where(Setting.key == rf.COPIED_KEY_PREFIX + app_id.hex)).all()
    assert len(records) == 1
    assert rf.last_error(db_session) is None


def test_a_root_change_copies_everything_into_the_new_root(db_session, folder_on, tmp_path):
    app = make_application(db_session, make_job(db_session), pdf=make_pdf(tmp_path))
    rf.backfill()
    custom = tmp_path / "elsewhere"
    custom.mkdir()
    _set_root(custom)
    rf.backfill()
    assert len(list(custom.rglob("*.pdf"))) == 1
    assert rf.copy_path_for(app.id).is_relative_to(custom)


def test_backfill_fills_a_new_root(db_session, folder_on, tmp_path):
    make_application(db_session, make_job(db_session), pdf=make_pdf(tmp_path))
    assert rf.backfill() == 1
    custom = tmp_path / "elsewhere"
    custom.mkdir()
    db_session.add(Setting(key=rf.PATH_KEY, value=str(custom)))
    db_session.commit()
    assert rf.backfill() == 1
    assert len(list(custom.rglob("*.pdf"))) == 1


def test_one_application_that_cannot_be_copied_blocks_no_other(db_session, folder_on, tmp_path, monkeypatch):
    bad = make_application(db_session, make_job(db_session, company="Bad"), pdf=make_pdf(tmp_path, "Bad.pdf"))
    good = make_application(db_session, make_job(db_session, company="Good"), pdf=make_pdf(tmp_path, "Good.pdf"))
    real_place, real_attempt, attempted = rf._place, rf._attempt, []

    def place(src, folder):
        if src.name == "Bad.pdf":
            raise PermissionError("unreadable")
        return real_place(src, folder)

    monkeypatch.setattr(rf, "_place", place)
    monkeypatch.setattr(rf, "_attempt", lambda i, **kw: attempted.append(i) or real_attempt(i, **kw))
    for _ in range(2):  # a persistent failure, at two starts in a row
        attempted.clear()
        rf.backfill()
        assert sorted(attempted) == sorted([bad.id, good.id])
        assert rf.copy_path_for(good.id) is not None
        assert rf.copy_path_for(bad.id) is None


def test_a_missing_root_ends_the_pass_with_one_recorded_error(db_session, folder_on, tmp_path, monkeypatch):
    db_session.add(Setting(key=rf.PATH_KEY, value=str(tmp_path / "unplugged")))
    db_session.commit()
    for i in range(3):
        make_application(
            db_session, make_job(db_session, company=f"Co{i}"), pdf=make_pdf(tmp_path, f"R{i}.pdf")
        )
    attempted, recorded = [], []
    real_record = rf._record_error
    monkeypatch.setattr(rf, "_attempt", lambda i, **kw: attempted.append(i) or (False, None))
    monkeypatch.setattr(rf, "_record_error", lambda exc: (recorded.append(exc), real_record(exc))[1])
    rf.backfill()
    assert attempted == [] and len(recorded) == 1
    db_session.expire_all()
    assert rf.last_error(db_session)["code"] == "missing"


def test_an_unwritable_root_ends_the_pass_with_one_recorded_error(db_session, folder_on, tmp_path, monkeypatch):
    make_application(db_session, make_job(db_session), pdf=make_pdf(tmp_path))
    attempted = []
    monkeypatch.setattr(rf, "_attempt", lambda i, **kw: attempted.append(i) or (True, None))
    monkeypatch.setattr(rf.tempfile, "mkstemp", lambda *a, **k: (_ for _ in ()).throw(PermissionError("read-only")))
    rf.backfill()
    assert attempted == []
    db_session.expire_all()
    assert rf.last_error(db_session)["code"] == "write"


def test_a_pass_logs_a_traceback_for_the_first_failure_only(db_session, folder_on, tmp_path, monkeypatch, caplog):
    for i in range(3):
        make_application(
            db_session, make_job(db_session, company=f"Co{i}"), pdf=make_pdf(tmp_path, f"R{i}.pdf")
        )

    def place(src, folder):
        raise PermissionError("unreadable")

    monkeypatch.setattr(rf, "_place", place)
    with caplog.at_level("WARNING", logger=rf.logger.name):
        rf.backfill()
    saves = [r for r in caplog.records if r.getMessage().startswith("Couldn't save a copy")]
    assert len(saves) == 3
    assert [bool(r.exc_info) for r in saves] == [True, False, False]


def test_backfill_never_raises(db_session, folder_on, tmp_path, monkeypatch):
    make_application(db_session, make_job(db_session), pdf=make_pdf(tmp_path))

    def boom(_application_id, **_kw):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(rf, "_attempt", boom)
    assert rf.backfill() is None
    assert rf._BACKFILL.acquire(blocking=False)  # the lock was released
    rf._BACKFILL.release()


def _set_root(path):
    with rf.SessionLocal() as db:
        rf._put(db, rf.PATH_KEY, str(path))
        db.commit()


def _applications_in(db_session, tmp_path, count):
    pdfs = []
    for i in range(count):
        pdf = make_pdf(tmp_path, f"R{i}.pdf")
        pdfs.append(pdf)
        make_application(db_session, make_job(db_session, company=f"Co{i}"), pdf=pdf)
    return pdfs


def test_a_root_change_during_a_pass_is_not_lost(db_session, folder_on, tmp_path, monkeypatch):
    """The PUT commits the new root, then asks for a backfill: the running pass must loop for it."""
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    _set_root(first)
    pdfs = _applications_in(db_session, tmp_path, 3)
    real_attempt, calls = rf._attempt, []

    class NoThread:  # the request is the flag start_backfill raises before it spawns anything
        def __init__(self, **kwargs):
            pass

        def start(self):
            pass

    def attempt(application_id, **kw):
        calls.append(application_id)
        result = real_attempt(application_id, **kw)
        if len(calls) == 1:  # the first copy is done, then the user picks another folder
            _set_root(second)
            monkeypatch.setattr(rf.threading, "Thread", NoThread)
            rf.start_backfill()
        return result

    monkeypatch.setattr(rf, "_attempt", attempt)
    rf.backfill()
    for pdf in pdfs:  # the first application too: the second pass repairs the split
        assert len(list(second.rglob(pdf.name))) == 1


def test_a_root_that_goes_there_and_back_is_filled_in_full(db_session, folder_on, tmp_path, monkeypatch):
    """A to B to A during one pass: the start and end roots match, but a copy landed in B."""
    a_root, b_root = tmp_path / "a", tmp_path / "b"
    a_root.mkdir()
    b_root.mkdir()
    _set_root(a_root)
    pdfs = _applications_in(db_session, tmp_path, 3)
    real_attempt, calls = rf._attempt, []

    def attempt(application_id, **kw):
        calls.append(application_id)
        result = real_attempt(application_id, **kw)
        if len(calls) == 1:
            _set_root(b_root)
            rf.backfill()  # overlapping request: raises the flag, fails the lock
        elif len(calls) == 2:  # this copy landed in B
            _set_root(a_root)
            rf.backfill()
        return result

    monkeypatch.setattr(rf, "_attempt", attempt)
    rf.backfill()
    for pdf in pdfs:
        assert len(list(a_root.rglob(pdf.name))) == 1


def test_backfill_and_start_backfill_never_raise_from_the_availability_check(monkeypatch):
    def boom():
        raise RuntimeError("sync status unreadable")

    monkeypatch.setattr(rf, "availability", boom)
    assert rf.backfill() is None
    assert rf.start_backfill() is None


def test_start_backfill_runs_it_on_a_thread(folder_on, monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, target, name, daemon):
            started.append((target, name, daemon))

        def start(self):
            started.append("start")

    monkeypatch.setattr(rf.threading, "Thread", FakeThread)
    assert rf.start_backfill() is not None
    assert started == [(rf.backfill, "resumes-folder-backfill", True), "start"]


def test_start_backfill_is_a_no_op_when_unavailable(monkeypatch):
    monkeypatch.setattr(app_settings, "resumes_folder_default", None)
    assert rf.start_backfill() is None

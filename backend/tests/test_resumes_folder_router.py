"""Settings API, open-folder and show-copy endpoints for the resumes folder."""
import re
from pathlib import Path

import pytest

from app.config import settings as app_settings
from app.models.setting import Setting
from app.routers import resumes_folder as router_mod
from app.services import folder_opener, folder_picker
from app.services import resumes_folder as rf
from tests.resumes_folder_fixtures import make_application, make_job, make_pdf

URL = "/api/settings/resumes-folder"

# Copied from frontend/lib/error-text.ts isPlainSentence: a server detail shows only if this passes.
PLAIN = re.compile(r"^[A-Z][^{}[\]<>_`|\\]*[.!?]$")


def is_plain_sentence(text: str) -> bool:
    t = text.strip()
    return 0 < len(t) <= 240 and PLAIN.search(t) is not None


@pytest.fixture(autouse=True)
def backfills(monkeypatch):
    """Never start the real daemon thread: it would write through SessionLocal during teardown."""
    calls = []
    monkeypatch.setattr(rf, "start_backfill", lambda: calls.append(1))
    return calls


@pytest.fixture
def opened(monkeypatch):
    calls = {"folder": [], "reveal": []}
    monkeypatch.setattr(folder_opener, "open_folder", lambda p: calls["folder"].append(p))
    monkeypatch.setattr(folder_opener, "reveal", lambda p: calls["reveal"].append(p))
    return calls


def _row(db_session, key):
    db_session.expire_all()
    return db_session.get(Setting, key)


def test_get_when_unavailable(client, monkeypatch):
    monkeypatch.setattr(app_settings, "resumes_folder_default", None)
    body = client.get(URL).json()
    assert body["available"] is False
    assert body["reason"] == "not_here"
    assert body["folder"] is None
    assert body["picker"] is False


def test_get_shows_the_folder_the_way_the_user_sees_it(client, folder_on, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    body = client.get(URL).json()
    assert body["available"] is True
    assert body["reason"] is None
    assert body["enabled"] is True
    assert body["custom"] is False
    assert body["folder"] == str(folder_on)
    assert body["folder_display"] == "Documents › Maestro Career Studio › Applications"
    assert body["default_folder"] == str(folder_on)
    assert body["last_error"] is None


def test_get_on_the_always_on_copy(client, folder_on, monkeypatch):
    monkeypatch.setattr(rf.sync_status, "is_remote", lambda: True)
    body = client.get(URL).json()
    assert (body["available"], body["reason"], body["folder"]) == (False, "always_on_copy", None)


def test_get_turns_a_saved_error_into_a_sentence(client, folder_on, db_session):
    rf._put(db_session, rf.LAST_ERROR_KEY, '{"code": "missing", "path": "/x", "at": "2026-10-11T10:00:00+00:00"}')
    db_session.commit()
    err = client.get(URL).json()["last_error"]
    assert err["message"] == rf.ERROR_SENTENCES["missing"]
    assert err["at"].startswith("2026-10-11T10:00:00")


def test_put_off_then_get(client, folder_on, db_session):
    assert client.put(URL, json={"enabled": False}).json()["enabled"] is False
    assert client.get(URL).json()["enabled"] is False
    assert _row(db_session, rf.ENABLED_KEY).value == "off"
    assert client.put(URL, json={"enabled": True}).json()["enabled"] is True
    assert _row(db_session, rf.ENABLED_KEY) is None


def test_put_a_relative_folder_is_refused(client, folder_on):
    r = client.put(URL, json={"folder": "relative"})
    assert r.status_code == 422
    assert r.json()["detail"] == "Enter the folder's full path."


def test_put_a_missing_folder_is_refused(client, folder_on, tmp_path):
    r = client.put(URL, json={"folder": str(tmp_path / "missing")})
    assert r.status_code == 422
    assert "doesn't exist" in r.json()["detail"]


def test_put_a_folder_inside_the_data_folder_is_refused(client, folder_on, tmp_path):
    inside = tmp_path / "work" / "x"
    inside.mkdir()  # otherwise the "doesn't exist" branch answers and the test passes for the wrong reason
    r = client.put(URL, json={"folder": str(inside)})
    assert r.status_code == 422
    assert r.json()["detail"] == "Choose a folder outside Maestro's own data folder."


@pytest.mark.parametrize("raw", ["~nosuchuser-maestro/x", "/tmp/a\x00b"])
def test_put_a_path_that_cant_be_read_is_refused_not_a_500(client, folder_on, raw):
    r = client.put(URL, json={"folder": raw})
    assert r.status_code == 422
    assert r.json()["detail"] == "Enter the folder's full path."


def test_put_an_unwritable_folder_is_refused(client, folder_on, tmp_path, monkeypatch):
    ok = tmp_path / "ok"
    ok.mkdir()

    def deny(*_a, **_k):
        raise PermissionError("no")

    monkeypatch.setattr(rf.tempfile, "mkstemp", deny)
    r = client.put(URL, json={"folder": str(ok)})
    assert r.status_code == 422
    assert "can't save files" in r.json()["detail"]


def test_put_a_good_folder_is_custom_and_starts_a_backfill(client, folder_on, tmp_path, backfills):
    ok = tmp_path / "ok"
    ok.mkdir()
    r = client.put(URL, json={"folder": str(ok)})
    assert r.status_code == 200
    body = r.json()
    assert body["custom"] is True
    assert body["folder"] == str(ok.resolve())
    assert backfills == [1]


def test_put_null_goes_back_to_the_default(client, folder_on, tmp_path):
    ok = tmp_path / "ok"
    ok.mkdir()
    client.put(URL, json={"folder": str(ok)})
    body = client.put(URL, json={"folder": None}).json()
    assert body["custom"] is False
    assert body["folder"] == str(folder_on)


def test_put_clears_the_last_error(client, folder_on, db_session):
    rf._put(db_session, rf.LAST_ERROR_KEY, '{"code": "write", "path": null, "at": "2026-10-11T10:00:00+00:00"}')
    db_session.commit()
    assert client.put(URL, json={"enabled": True}).json()["last_error"] is None


def test_put_after_a_failed_copy_retries_even_if_nothing_changed(client, folder_on, db_session, backfills):
    """Save after reconnecting a drive: the same folder, but copies are owed."""
    rf._put(db_session, rf.LAST_ERROR_KEY, '{"code": "missing", "path": null, "at": "2026-10-11T10:00:00+00:00"}')
    rf._put(db_session, rf.BACKFILLED_KEY, str(folder_on))
    db_session.commit()
    assert client.put(URL, json={"enabled": True}).json()["last_error"] is None
    assert _row(db_session, rf.BACKFILLED_KEY) is None
    assert backfills == [1]


def test_put_without_a_root_change_starts_no_backfill(client, folder_on, backfills):
    client.put(URL, json={"enabled": True})
    assert backfills == []


def test_put_turning_it_on_again_starts_a_backfill(client, folder_on, backfills):
    client.put(URL, json={"enabled": False})
    assert backfills == []
    client.put(URL, json={"enabled": True})
    assert backfills == [1]


def test_put_rejects_unknown_fields(client, folder_on):
    assert client.put(URL, json={"nope": 1}).status_code == 422


@pytest.mark.parametrize("reason", ["not_here", "always_on_copy"])
def test_every_mutating_route_is_409_when_unavailable(client, folder_on, db_session, tmp_path, monkeypatch, reason):
    job = make_job(db_session)
    application = make_application(db_session, job, make_pdf(tmp_path))
    if reason == "not_here":
        monkeypatch.setattr(app_settings, "resumes_folder_default", None)
    else:
        monkeypatch.setattr(rf.sync_status, "is_remote", lambda: True)
    responses = [
        client.put(URL, json={"enabled": False}),
        client.post(f"{URL}/open"),
        client.post(f"{URL}/choose"),
        client.post(f"/api/applications/{application.id}/show-copy"),
    ]
    assert [r.status_code for r in responses] == [409, 409, 409, 409]
    assert all(r.json()["detail"] == router_mod.UNAVAILABLE for r in responses)


def test_open_makes_the_default_folder_and_opens_it(client, folder_on, opened):
    assert not folder_on.exists()
    r = client.post(f"{URL}/open")
    assert r.status_code == 204
    assert folder_on.is_dir()
    assert opened["folder"] == [folder_on]


def test_open_never_creates_a_custom_folder(client, folder_on, tmp_path, opened):
    ok = tmp_path / "ok"
    ok.mkdir()
    client.put(URL, json={"folder": str(ok)})
    ok.rmdir()  # an unplugged drive
    client.post(f"{URL}/open")
    assert not ok.exists()


def test_open_when_the_default_folder_cant_be_made_is_a_409(client, folder_on, tmp_path, opened):
    (tmp_path / "Documents").write_text("a file where the folder should be")
    r = client.post(f"{URL}/open")
    assert (r.status_code, r.json()["detail"]) == (409, router_mod.CANT_OPEN)
    assert opened["folder"] == []


def test_open_failing_is_a_409(client, folder_on, monkeypatch):
    def boom(_p):
        raise folder_opener.OpenerUnavailable("open")

    monkeypatch.setattr(folder_opener, "open_folder", boom)
    r = client.post(f"{URL}/open")
    assert (r.status_code, r.json()["detail"]) == (409, router_mod.CANT_OPEN)


def test_show_copy_reveals_the_copy(client, folder_on, db_session, tmp_path, opened):
    pdf = make_pdf(tmp_path)
    application = make_application(db_session, make_job(db_session), pdf)
    r = client.post(f"/api/applications/{application.id}/show-copy")
    assert r.status_code == 200
    shown = Path(r.json()["path"])
    assert shown.is_file()
    assert shown.name == pdf.name
    assert shown.parent.parent == folder_on
    assert opened["reveal"] == [shown]


def test_show_copy_for_an_unknown_application(client, folder_on):
    r = client.post("/api/applications/00000000-0000-0000-0000-000000000000/show-copy")
    assert (r.status_code, r.json()["detail"]) == (404, router_mod.NOT_FOUND)


def test_show_copy_without_a_pdf(client, folder_on, db_session, opened):
    application = make_application(db_session, make_job(db_session))
    r = client.post(f"/api/applications/{application.id}/show-copy")
    assert (r.status_code, r.json()["detail"]) == (409, router_mod.NO_PDF)
    assert opened["reveal"] == []


def test_show_copy_when_off(client, folder_on, db_session, tmp_path, opened):
    application = make_application(db_session, make_job(db_session), make_pdf(tmp_path))
    client.put(URL, json={"enabled": False})
    r = client.post(f"/api/applications/{application.id}/show-copy")
    assert (r.status_code, r.json()["detail"]) == (409, router_mod.OFF)


def test_show_copy_when_the_copy_cant_be_made(client, folder_on, db_session, tmp_path, opened):
    pdf = make_pdf(tmp_path)
    application = make_application(db_session, make_job(db_session), pdf)
    pdf.unlink()  # a recorded PDF that is gone: nothing to copy
    r = client.post(f"/api/applications/{application.id}/show-copy")
    assert (r.status_code, r.json()["detail"]) == (409, router_mod.NOT_SAVED)
    assert opened["reveal"] == []


def test_show_copy_failing_to_open_is_a_409(client, folder_on, db_session, tmp_path, monkeypatch):
    application = make_application(db_session, make_job(db_session), make_pdf(tmp_path))

    def boom(_p):
        raise folder_opener.OpenerUnavailable("open")

    monkeypatch.setattr(folder_opener, "reveal", boom)
    r = client.post(f"/api/applications/{application.id}/show-copy")
    assert (r.status_code, r.json()["detail"]) == (409, router_mod.CANT_OPEN)


def test_every_409_and_422_sentence_is_plain(client, folder_on, tmp_path, monkeypatch):
    """frontend/lib/error-text.ts shows a server detail only when isPlainSentence passes."""
    sentences = [
        router_mod.UNAVAILABLE, router_mod.OFF, router_mod.NO_PDF, router_mod.NOT_FOUND, router_mod.NOT_SAVED, router_mod.CANT_OPEN,
        router_mod.PICKER_BUSY, router_mod.PICKER_FAILED,
        *rf.ERROR_SENTENCES.values(),
    ]
    inside = tmp_path / "work" / "x"
    inside.mkdir()
    ok = tmp_path / "ok"
    ok.mkdir()
    bodies = [{"folder": "relative"}, {"folder": str(tmp_path / "missing")}, {"folder": str(inside)}]
    for body in bodies:
        r = client.put(URL, json=body)
        assert r.status_code == 422
        sentences.append(r.json()["detail"])
    monkeypatch.setattr(rf.tempfile, "mkstemp", lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    r = client.put(URL, json={"folder": str(ok)})  # the probe fails: the unwritable-folder sentence
    assert r.status_code == 422
    sentences.append(r.json()["detail"])
    assert [s for s in sentences if not is_plain_sentence(s)] == []


@pytest.fixture
def picked(monkeypatch):
    """A fake dialog: never opens a real one. calls = the start paths; answer = what it returns."""
    state = {"calls": [], "answer": None, "error": None}

    def fake(start):
        state["calls"].append(start)
        if state["error"]:
            raise state["error"]
        return state["answer"]

    monkeypatch.setattr(folder_picker, "available", lambda *a, **k: True)
    monkeypatch.setattr(folder_picker, "choose", fake)
    return state


def test_get_reports_whether_a_picker_exists(client, folder_on, monkeypatch):
    monkeypatch.setattr(folder_picker, "available", lambda *a, **k: True)
    assert client.get(URL).json()["picker"] is True
    monkeypatch.setattr(folder_picker, "available", lambda *a, **k: False)
    assert client.get(URL).json()["picker"] is False


def test_choose_returns_the_folder_and_saves_nothing(client, folder_on, db_session, tmp_path, picked):
    picked["answer"] = tmp_path / "Resumes"
    r = client.post(f"{URL}/choose")
    assert (r.status_code, r.json()) == (200, {"folder": str(tmp_path / "Resumes")})
    assert _row(db_session, rf.PATH_KEY) is None


def test_choose_cancelled_is_a_null_folder(client, folder_on, picked):
    r = client.post(f"{URL}/choose")
    assert (r.status_code, r.json()) == (200, {"folder": None})


def test_choose_starts_at_the_saved_folder_else_home(client, folder_on, tmp_path, monkeypatch, picked):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    client.post(f"{URL}/choose")  # the default does not exist yet
    saved = tmp_path / "saved"
    saved.mkdir()
    client.put(URL, json={"folder": str(saved)})
    client.post(f"{URL}/choose")
    assert picked["calls"] == [tmp_path, saved]


def test_choose_while_a_dialog_is_open_is_a_409(client, folder_on, picked):
    picked["error"] = folder_picker.PickerBusy()
    r = client.post(f"{URL}/choose")
    assert (r.status_code, r.json()["detail"]) == (409, router_mod.PICKER_BUSY)


def test_choose_failing_is_a_409(client, folder_on, picked):
    picked["error"] = folder_picker.PickerFailed("exit 1")
    r = client.post(f"{URL}/choose")
    assert (r.status_code, r.json()["detail"]) == (409, router_mod.PICKER_FAILED)


def test_choose_without_a_picker_is_a_409(client, folder_on, monkeypatch, picked):
    monkeypatch.setattr(folder_picker, "available", lambda *a, **k: False)
    r = client.post(f"{URL}/choose")
    assert (r.status_code, r.json()["detail"]) == (409, router_mod.PICKER_FAILED)
    assert picked["calls"] == []


def test_choose_with_an_unreadable_saved_folder_starts_at_home(client, folder_on, tmp_path, monkeypatch, picked):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    saved = tmp_path / "locked"
    saved.mkdir()
    client.put(URL, json={"folder": str(saved)})
    real = Path.is_dir

    def is_dir(self):
        if self == saved:
            raise PermissionError("denied")
        return real(self)

    monkeypatch.setattr(Path, "is_dir", is_dir)
    r = client.post(f"{URL}/choose")
    assert r.status_code == 200
    assert picked["calls"] == [tmp_path]


def test_turning_it_back_on_copies_what_was_made_while_it_was_off(client, folder_on, db_session, tmp_path, monkeypatch):
    first = make_application(db_session, make_job(db_session), make_pdf(tmp_path))
    assert rf.copy_application(first.id) is not None
    rf.backfill()  # marks the root done
    assert _row(db_session, rf.BACKFILLED_KEY).value == str(folder_on)
    client.put(URL, json={"enabled": False})
    second = make_application(
        db_session, make_job(db_session, company="Globex"), make_pdf(tmp_path, name="Second_Resume.pdf"))
    assert rf.copy_application(second.id) is None  # off: no copy
    monkeypatch.setattr(rf, "start_backfill", rf.backfill)  # run the pass inline instead of in a thread
    client.put(URL, json={"enabled": True})
    assert rf.copy_path_for(second.id) is not None


def test_changing_the_root_or_the_switch_clears_the_backfill_marker(client, folder_on, db_session, tmp_path):
    ok = tmp_path / "ok"
    ok.mkdir()
    for body in ({"enabled": False}, {"folder": str(ok)}):
        rf._put(db_session, rf.BACKFILLED_KEY, "marker")
        db_session.commit()
        client.put(URL, json=body)
        assert _row(db_session, rf.BACKFILLED_KEY) is None
    rf._put(db_session, rf.BACKFILLED_KEY, "marker")
    db_session.commit()
    client.put(URL, json={"folder": str(ok)})  # no change
    assert _row(db_session, rf.BACKFILLED_KEY).value == "marker"

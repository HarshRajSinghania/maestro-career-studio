import os
import shutil

import pytest
from app.db import get_db
from app.main import app
from fastapi.testclient import TestClient

_HAS_LATEX = shutil.which("pdflatex") is not None


def _override_db(db_session):
    def _inner():
        yield db_session

    return _inner


def test_list_seeds_default(db_session, client):
    r = client.get("/api/templates")
    assert r.status_code == 200
    ids = [t["id"] for t in r.json()]
    assert "default" in ids


def test_list_templates_exposes_updated_at(db_session, client):
    """The gallery marks a thumbnail stale when the source changed after the
    last validation, so the list must carry both timestamps."""
    r = client.get("/api/templates")
    body = r.json()
    assert body, "expected at least the seeded templates"
    assert "updated_at" in body[0]
    assert "validated_at" in body[0]


def test_create_draft_and_get(db_session, client):
    cr = client.post(
        "/api/templates", json={"id": "minimal", "display_name": "Minimal", "source": "X"}
    )
    assert cr.status_code == 200
    body = cr.json()
    assert body["status"] == "draft"
    assert body["origin"] == "frontend"
    assert body["is_default"] is False
    g = client.get("/api/templates/minimal")
    assert g.status_code == 200
    assert g.json()["source"] == "X"


def test_engine_in_responses_and_typst_create(db_session, client):
    cr = client.post("/api/templates", json={"id": "lat", "display_name": "L", "source": "X"})
    assert cr.status_code == 200 and cr.json()["engine"] == "latex"
    ct = client.post(
        "/api/templates",
        json={"id": "typ", "display_name": "T", "source": "#x", "engine": "typst"},
    )
    assert ct.status_code == 200 and ct.json()["engine"] == "typst"
    assert client.get("/api/templates/typ").json()["engine"] == "typst"


def test_typst_create_without_source_rejected(db_session, client):
    # Phase 1: STARTER_SOURCE stays LaTeX — it cannot seed a typst draft.
    r = client.post(
        "/api/templates", json={"id": "typ2", "display_name": "T", "engine": "typst"}
    )
    assert r.status_code == 422


def test_engine_immutable_after_creation(db_session, client):
    client.post("/api/templates", json={"id": "imm", "display_name": "I", "source": "X"})
    bad = client.put("/api/templates/imm", json={"engine": "typst"})
    same = client.put("/api/templates/imm", json={"engine": "latex", "display_name": "I2"})
    assert bad.status_code == 400
    assert same.status_code == 200  # same-value no-op is allowed


def test_create_without_source_uses_starter(db_session, client):
    from app.services import template_registry as reg
    cr = client.post("/api/templates", json={"id": "fresh", "display_name": "Fresh"})
    assert cr.status_code == 200
    assert cr.json()["source"] == reg.STARTER_SOURCE
    assert cr.json()["status"] == "draft"


def test_update_with_validate_flag_runs_validation(db_session, monkeypatch, client):
    from app.routers import templates as templates_router

    calls = {}

    def fake_validate(template_id, session):
        calls["id"] = template_id
        return {"ok": True, "error": None}

    monkeypatch.setattr(templates_router.tv, "validate_template", fake_validate)
    client.post("/api/templates", json={"id": "vt", "display_name": "VT", "source": "X"})
    saved = client.put("/api/templates/vt?validate=true", json={"source": "Y"})
    saved_plain = client.put("/api/templates/vt", json={"source": "Z"})

    assert saved.status_code == 200
    assert calls["id"] == "vt"
    assert saved_plain.status_code == 200
    # Without the flag the row simply stays a draft; validation ran only once.
    assert saved_plain.json()["status"] == "draft"


def test_create_duplicate_conflicts(db_session, client):
    client.post("/api/templates", json={"id": "dup", "display_name": "D", "source": "X"})
    again = client.post("/api/templates", json={"id": "dup", "display_name": "D", "source": "Y"})
    assert again.status_code == 409


def test_get_unknown_404(db_session, client):
    r = client.get("/api/templates/nope")
    assert r.status_code == 404


def test_update_resets_to_draft_and_default_guard(db_session, client):
    client.get("/api/templates")  # seed default (status ready)
    # editing the default without the flag is forbidden
    forbidden = client.put("/api/templates/default", json={"source": "Y"})
    assert forbidden.status_code == 403
    # with the flag it's allowed and resets to draft
    ok = client.put("/api/templates/default?allow_default_edit=true", json={"source": "Y"})
    assert ok.status_code == 200
    assert ok.json()["status"] == "draft"


def test_update_unknown_404(db_session, client):
    r = client.put("/api/templates/nope", json={"display_name": "X"})
    assert r.status_code == 404


def test_create_rejects_bad_id(db_session, client):
    for bad in ["../etc", "Has Space", "UPPER", "dir/sub", ""]:
        r = client.post("/api/templates", json={"id": bad, "display_name": "X", "source": "Y"})
        assert r.status_code == 422, f"{bad!r} should be rejected"


def test_set_default_requires_ready(db_session, client):
    client.get("/api/templates")  # seed default
    client.post("/api/templates", json={"id": "t2", "display_name": "T2", "source": "X"})
    assert client.post("/api/templates/t2/set-default").status_code == 400  # draft can't be default


def test_delete_refuses_default(db_session, client):
    client.get("/api/templates")
    assert client.delete("/api/templates/default").status_code == 400


def test_delete_removes_nondefault(db_session, client):
    client.post("/api/templates", json={"id": "gone", "display_name": "G", "source": "X"})
    assert client.delete("/api/templates/gone").status_code == 204
    assert client.get("/api/templates/gone").status_code == 404


def test_validate_unknown_404(db_session, client):
    assert client.post("/api/templates/nope/validate").status_code == 404


def test_preview_requires_ready(db_session, client):
    # a draft (never validated) has no ready preview -> 404
    client.post("/api/templates", json={"id": "d", "display_name": "D", "source": "X"})
    assert client.get("/api/templates/d/preview.pdf").status_code == 404


@pytest.mark.skipif(not _HAS_LATEX, reason="pdflatex not installed")
def test_validate_then_preview_and_set_default(db_session, tmp_path, monkeypatch, client):
    import app.services.pdf_render as pr
    import app.services.template_validation as tv
    monkeypatch.setattr(tv.settings, "base_resumes_dir", tmp_path)
    src = (pr.TEMPLATE_DIR / pr.RESUME_TEMPLATE).read_text(encoding="utf-8")
    client.get("/api/templates")  # seed default
    client.post("/api/templates", json={"id": "g", "display_name": "G", "source": src})
    v = client.post("/api/templates/g/validate")
    assert v.status_code == 200 and v.json()["ok"] is True
    p = client.get("/api/templates/g/preview.pdf")
    assert p.status_code == 200
    assert p.headers["content-type"] == "application/pdf"
    # now g is ready -> can become default
    sd = client.post("/api/templates/g/set-default")
    assert sd.status_code == 200
    assert sd.json()["is_default"] is True


def test_supported_fmt_keys_default_and_starter(db_session, client):
    # the seeded Classic default parameterizes fmt.* -> keys are detected
    lst = client.get("/api/templates")
    assert lst.status_code == 200
    default_row = next(t for t in lst.json() if t["id"] == "default")
    keys = set(default_row["supported_fmt_keys"])
    assert {"font_size", "side_margins"}.issubset(keys)
    # header_align is referenced only in the included _header.tex.j2 partial;
    # supported_fmt_keys must follow includes or the panel greys the control.
    assert "header_align" in keys
    # GET one exposes the same list
    one = client.get("/api/templates/default")
    assert {"font_size", "side_margins"}.issubset(set(one.json()["supported_fmt_keys"]))
    # a plain starter-source draft opts into nothing
    client.post("/api/templates", json={"id": "starter", "display_name": "S"})
    draft = client.get("/api/templates/starter")
    assert draft.status_code == 200
    assert draft.json()["supported_fmt_keys"] == []


def test_mutating_responses_include_engine_aware_supported_fmt_keys(db_session, client):
    from app.services import template_registry as reg

    source = "#set text(size: fmt.font_size * 1pt)\n// fmt.header_align\n"
    created = client.post(
        "/api/templates",
        json={
            "id": "fmt-response",
            "display_name": "Formatting response",
            "source": source,
            "engine": "typst",
        },
    )
    assert created.status_code == 200
    assert created.json()["supported_fmt_keys"] == ["date_format", "font_size"]

    updated = client.put("/api/templates/fmt-response", json={"source": source})
    assert updated.status_code == 200
    assert updated.json()["supported_fmt_keys"] == ["date_format", "font_size"]

    row = reg.get(db_session, "fmt-response")
    row.status = "ready"
    db_session.commit()
    made_default = client.post("/api/templates/fmt-response/set-default")
    assert made_default.status_code == 200
    assert made_default.json()["supported_fmt_keys"] == ["date_format", "font_size"]


def test_default_formatting_round_trip(db_session, client):
    client.post("/api/templates", json={"id": "df", "display_name": "DF", "source": "X"})
    # a fresh draft has no default_formatting
    assert client.get("/api/templates/df").json()["default_formatting"] is None
    # PUT persists a partial override without resetting status
    put = client.put(
        "/api/templates/df/default-formatting",
        json={"formatting": {"font_size": 12, "header_align": "left"}},
    )
    assert put.status_code == 200
    assert put.json()["default_formatting"] == {"font_size": 12, "header_align": "left"}
    assert put.json()["status"] == "draft"  # unchanged
    # round-trips on GET detail and list summary
    assert client.get("/api/templates/df").json()["default_formatting"] == {
        "font_size": 12,
        "header_align": "left",
    }
    row = next(t for t in client.get("/api/templates").json() if t["id"] == "df")
    assert row["default_formatting"] == {"font_size": 12, "header_align": "left"}
    # clearing back to null
    clr = client.put("/api/templates/df/default-formatting", json={"formatting": None})
    assert clr.status_code == 200
    assert clr.json()["default_formatting"] is None


@pytest.mark.skipif(not _HAS_LATEX, reason="pdflatex not installed")
def test_default_formatting_refreshes_preview(db_session, tmp_path, monkeypatch, client):
    pytest.importorskip("pdfplumber")
    import app.services.template_validation as tv

    monkeypatch.setattr(tv.settings, "base_resumes_dir", tmp_path)
    src = (
        r"\documentclass{article}\begin{document}"
        r"FONTSIZE((( fmt.font_size )))MARK\end{document}"
    )
    client.post("/api/templates", json={"id": "rp", "display_name": "RP", "source": src})
    v = client.post("/api/templates/rp/validate")
    assert v.status_code == 200 and v.json()["ok"] is True

    # PUT default-formatting must persist AND re-render the stored preview so
    # it reflects the new defaults (font_size 12), with no source-endpoint
    # recompile and status left ready.
    put = client.put(
        "/api/templates/rp/default-formatting",
        json={"formatting": {"font_size": 12}},
    )
    assert put.status_code == 200
    assert put.json()["default_formatting"] == {"font_size": 12}
    assert put.json()["status"] == "ready"  # status untouched
    # Successful preview refresh stamps validated_at so the gallery does
    # not show "needs re-validation" for a formatting-only edit.
    from datetime import datetime

    detail = client.get("/api/templates/rp").json()
    assert detail["validated_at"] is not None
    validated = datetime.fromisoformat(detail["validated_at"].replace("Z", "+00:00"))
    updated = datetime.fromisoformat(detail["updated_at"].replace("Z", "+00:00"))
    # validated_at must not lag updated_at by more than the gallery's 1s slack
    assert (updated - validated).total_seconds() <= 1.0

    import pdfplumber

    with pdfplumber.open(tv._preview_path("rp")) as doc:
        text = " ".join((page.extract_text() or "") for page in doc.pages)
    normalized = " ".join(text.split())
    assert "FONTSIZE12MARK" in normalized


def test_default_formatting_refresh_passes_row_engine(db_session, monkeypatch, client):
    from app.routers import templates as tr

    seen = {}

    def fake_compile(source, keep_pdf_at=None, default_formatting=None, engine="latex"):
        seen["engine"] = engine
        return None

    monkeypatch.setattr(tr.tv, "compile_against_sample", fake_compile)
    client.post(
        "/api/templates",
        json={"id": "te", "display_name": "TE", "source": "#x", "engine": "typst"},
    )
    r = client.put("/api/templates/te/default-formatting", json={"formatting": {"font_size": 12}})
    assert r.status_code == 200
    assert seen["engine"] == "typst"


def test_default_formatting_invalid_rejected(db_session, client):
    client.post("/api/templates", json={"id": "dfx", "display_name": "DFX", "source": "X"})
    r = client.put(
        "/api/templates/dfx/default-formatting",
        json={"formatting": {"font_size": 99}},
    )
    assert r.status_code == 400
    # unknown key also rejected
    r2 = client.put(
        "/api/templates/dfx/default-formatting",
        json={"formatting": {"bogus_key": 1}},
    )
    assert r2.status_code == 400


def test_default_formatting_unknown_template_404(db_session, client):
    r = client.put(
        "/api/templates/nope/default-formatting", json={"formatting": None}
    )
    assert r.status_code == 404


def test_preview_pages_404_before_validate(db_session, client):
    client.post("/api/templates", json={"id": "pp", "display_name": "PP", "source": "X"})
    assert client.get("/api/templates/pp/preview/pages").status_code == 404
    assert client.get("/api/templates/pp/preview/page/1").status_code == 404


@pytest.mark.skipif(not _HAS_LATEX, reason="pdflatex not installed")
def test_preview_pages_after_validate(db_session, tmp_path, monkeypatch, client):
    import app.services.pdf_render as pr
    import app.services.template_validation as tv
    monkeypatch.setattr(tv.settings, "base_resumes_dir", tmp_path)
    src = (pr.TEMPLATE_DIR / pr.RESUME_TEMPLATE).read_text(encoding="utf-8")
    client.get("/api/templates")  # seed default
    client.post("/api/templates", json={"id": "pg", "display_name": "PG", "source": src})
    v = client.post("/api/templates/pg/validate")
    assert v.status_code == 200 and v.json()["ok"] is True
    preview = tv._preview_path("pg")
    os.utime(preview, (1_700_000_000, 1_700_000_000))
    man = client.get("/api/templates/pg/preview/pages")
    assert man.status_code == 200
    assert man.json()["page_count"] >= 1
    assert man.json()["rendered_at"] == "2023-11-14T22:13:20+00:00"
    img = client.get("/api/templates/pg/preview/page/1")
    assert img.status_code == 200
    assert img.headers["content-type"] == "image/png"


# --- archiving -----------------------------------------------------------
#
# Mirrors base-resume archiving: hide from the gallery and every picker, change
# nothing else. The default is excluded because it is the render fallback for
# any resume whose template_id no longer resolves — archiving it would strand
# that fallback with no way to reach it.


def _client(db_session):
    app.dependency_overrides[get_db] = _override_db(db_session)
    return TestClient(app)


def _seeded_non_default(client):
    rows = client.get("/api/templates").json()
    return next(r for r in rows if not r["is_default"])


def test_archive_hides_from_the_default_list_only(db_session):
    client = _client(db_session)
    try:
        target = _seeded_non_default(client)
        assert client.post(f"/api/templates/{target['id']}/archive").status_code == 200

        active = [r["id"] for r in client.get("/api/templates").json()]
        assert target["id"] not in active

        # include_archived is the ONE lever every picker shares.
        everything = client.get("/api/templates?include_archived=true").json()
        row = next(r for r in everything if r["id"] == target["id"])
        assert row["archived_at"] is not None
    finally:
        app.dependency_overrides.clear()


def test_archiving_touches_no_artifact_and_reverses_cleanly(db_session):
    # The promise archiving makes over DELETE: the source and the validation
    # state survive, so a resume already rendering with it keeps rendering.
    client = _client(db_session)
    try:
        target = _seeded_non_default(client)
        before = client.get(f"/api/templates/{target['id']}").json()

        client.post(f"/api/templates/{target['id']}/archive")
        during = client.get(f"/api/templates/{target['id']}").json()
        assert during["source"] == before["source"]
        assert during["status"] == before["status"]
        assert during["validated_at"] == before["validated_at"]

        assert client.post(f"/api/templates/{target['id']}/unarchive").status_code == 200
        after = client.get(f"/api/templates/{target['id']}").json()
        assert after["archived_at"] is None
        assert [r["id"] for r in client.get("/api/templates").json()].count(
            target["id"]
        ) == 1
    finally:
        app.dependency_overrides.clear()


def test_the_default_template_cannot_be_archived(db_session):
    client = _client(db_session)
    try:
        rows = client.get("/api/templates").json()
        default_id = next(r["id"] for r in rows if r["is_default"])
        resp = client.post(f"/api/templates/{default_id}/archive")
        assert resp.status_code == 400
        assert "default" in resp.json()["detail"].lower()
        # and it is still listed
        assert default_id in [r["id"] for r in client.get("/api/templates").json()]
    finally:
        app.dependency_overrides.clear()


def test_archiving_an_unknown_template_is_404(db_session):
    client = _client(db_session)
    try:
        assert client.post("/api/templates/nope/archive").status_code == 404
    finally:
        app.dependency_overrides.clear()


# ---------- the from-scratch starter ----------
#
# "New template" in the web UI is the ONLY caller that mints the starter AND
# validates it in the same request. The starter used to render name, summary
# and experience alone, so the fresh draft failed the education/skills/email
# probes and the gallery flagged it "ATS spacing" the moment it appeared — a
# warning on a document the user had not touched yet, which reads as an error
# in the create flow. The starter must clear every probe on its own.


def test_starter_renders_every_core_section_and_custom_sections():
    """LaTeX-free half: every probe phrase and every section header survives
    the Jinja render, and the extras block is live (not a comment)."""
    from app.services import pdf_render, template_registry as reg
    from app.services import template_validation as tv

    tex = pdf_render.render_tex_from_source(reg.STARTER_SOURCE, tv.SAMPLE_RESUME)
    for probe in (
        *tv.PARSE_FIDELITY_PROBES,
        *tv.CONTACT_PROBES,
        "Sentinel Publication Alpha",
        "Sentinel entry bullet detail",
        "Sentinel award bullet detail",
        "Maestro CS",  # a project
        "AWS Certified Solutions Architect",  # a certification
        "GPA 3.8/4.0",
    ):
        assert probe in tex, probe
    for header in tv.HEADER_PROBES:
        assert header in tex.lower(), header
    assert pdf_render.source_references_extras(reg.STARTER_SOURCE)


@pytest.mark.skipif(not _HAS_LATEX, reason="pdflatex not installed")
def test_create_from_scratch_with_validate_is_certified(db_session, tmp_path, monkeypatch, client):
    """The web path exactly: POST ?validate=true with no source. The row must
    come back ready AND certified, with nothing missing from the parse report,
    so the gallery shows a clean card rather than a warning."""
    import app.services.template_validation as tv
    monkeypatch.setattr(tv.settings, "base_resumes_dir", tmp_path)
    cr = client.post(
        "/api/templates?validate=true",
        json={"id": "scratch", "display_name": "Scratch", "origin": "frontend"},
    )
    assert cr.status_code == 200, cr.text
    body = cr.json()
    assert body["status"] == "ready", body.get("last_error")
    assert body["parse_certified"] is True
    report = body["parse_report"]
    assert report["missing"] == []
    assert report["headers_missing"] == []
    assert report["email_ok"] is True
    assert report["extra_sections_supported"] is True


def test_list_reports_engine_availability(db_session, monkeypatch, client):
    from app.services import engines

    monkeypatch.setattr(engines, "pdflatex_available", lambda: False)
    body = client.get("/api/templates").json()
    detail = client.get("/api/templates/default").json()
    by_engine = {t["engine"]: t["engine_available"] for t in body}
    assert by_engine == {"latex": False, "typst": True}
    assert detail["engine_available"] is False

    monkeypatch.setattr(engines, "pdflatex_available", lambda: True)
    body = client.get("/api/templates").json()
    assert all(t["engine_available"] for t in body)


def _fail_compile(monkeypatch, error):
    from app.routers import templates as tr

    monkeypatch.setattr(
        tr.tv,
        "compile_against_sample",
        lambda source, keep_pdf_at=None, default_formatting=None, engine="latex": error,
    )


def test_default_formatting_that_breaks_a_ready_template_is_refused(
    db_session, monkeypatch, client
):
    """compile_against_sample RETURNS its error; the route used to treat any
    return as success and stamp validated_at over a preview it never wrote. A
    knob change that breaks a ready template (a compile error, or text that
    overprints) keeps the old state and says why, as the validate route does."""
    from app.services import template_registry as reg

    client.post("/api/templates", json={"id": "brk", "display_name": "B", "source": "X"})
    row = reg.get(db_session, "brk")
    row.status = "ready"
    row.default_formatting = {"font_size": 11}
    db_session.commit()
    before = reg.get(db_session, "brk").validated_at
    _fail_compile(monkeypatch, "text overprints: words are drawn on top of each other in the PDF")

    r = client.put("/api/templates/brk/default-formatting", json={"formatting": {"font_size": 12}})

    assert r.status_code == 400
    assert "overprints" in r.json()["detail"]
    db_session.expire_all()
    row = reg.get(db_session, "brk")
    assert row.default_formatting == {"font_size": 11}
    assert row.status == "ready"
    assert row.validated_at == before


def test_default_formatting_on_a_draft_persists_without_a_validated_stamp(
    db_session, monkeypatch, client
):
    client.post("/api/templates", json={"id": "drf", "display_name": "D", "source": "X"})
    _fail_compile(monkeypatch, "! Undefined control sequence.")

    r = client.put("/api/templates/drf/default-formatting", json={"formatting": {"font_size": 12}})

    assert r.status_code == 200
    assert r.json()["default_formatting"] == {"font_size": 12}
    assert r.json()["validated_at"] is None

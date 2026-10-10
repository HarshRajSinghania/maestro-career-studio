"""Validation refuses a template whose PDF draws words on top of each other.

The concrete cause found 2026-10-10: pdfTeX 1.40.27 (an un-updated TeX Live
2025, which the Docker-free install picks up from the host) mispositions the
fake interword space `pdf_render` turns on, for fonts whose encoding has its
own /space glyph — harshibar (Helvetica + Fira Mono) rendered as overprinted
garbage while still extracting cleanly, so the parse gate passed it. The check
looks at the PDF itself, so it catches any overprint, whatever the cause.
"""
import shutil
from pathlib import Path

import pytest

from app.services import engines, pdf_render
from app.services import template_registry as reg
from app.services import template_validation as tv

typst = pytest.importorskip("typst")

_PAGE = b"#set page(width: 320pt, height: 140pt)\n"


def _pdf(tmp_path: Path, name: str, body: bytes) -> Path:
    out = tmp_path / name
    out.write_bytes(typst.compile(_PAGE + body))
    return out


def _overprinted(tmp_path: Path) -> Path:
    # Two phrases placed 12pt apart on the same baselines: the words collide
    # glyph-on-glyph exactly as the pdfTeX 1.40.27 output does.
    return _pdf(
        tmp_path,
        "over.pdf",
        b"#for i in range(6) { place(dx: 10pt, dy: 10pt + i * 14pt)[Jordan Sample]; "
        b"place(dx: 22pt, dy: 10pt + i * 14pt)[Senior Engineer] }\n",
    )


def test_overprinted_pdf_is_detected(tmp_path):
    assert tv.text_overprints(_overprinted(tmp_path)) is True


def test_clean_pdf_with_ligatures_and_kerning_is_not_flagged(tmp_path):
    # "ft"/"fi" ligatures share one glyph box and italic "y," kerns into the
    # comma — normal typesetting that must never read as overprint.
    clean = _pdf(
        tmp_path,
        "clean.pdf",
        b"Jordan Sample, Senior Software Engineer at Acme Corp. "
        b"Built a fifty-fold efficient pipeline. _Globally, many, any,_\n",
    )
    assert tv.text_overprints(clean) is False


def test_unreadable_pdf_is_not_assessed(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    assert tv.text_overprints(bad) is None


def _fake_latex_compile(pdf: Path):
    def _compile(tex, out_dir, stem="preview", **_kw):
        shutil.copy(pdf, Path(out_dir) / f"{stem}.pdf")
        return Path(out_dir) / f"{stem}.pdf"

    return _compile


def test_validation_refuses_an_overprinting_latex_template(
    db_session, tmp_path, monkeypatch
):
    monkeypatch.setattr(tv.settings, "base_resumes_dir", tmp_path)
    monkeypatch.setattr(engines, "pdflatex_available", lambda: True)
    monkeypatch.setattr(
        engines,
        "probe_pdflatex",
        lambda: engines.EngineStatus("pdflatex", True, version="pdfTeX 1.40.29"),
    )
    monkeypatch.setattr(pdf_render, "compile_pdf", _fake_latex_compile(_overprinted(tmp_path)))
    src = (pdf_render.TEMPLATE_DIR / pdf_render.RESUME_TEMPLATE).read_text(encoding="utf-8")
    reg.create_draft(db_session, id="overprint", display_name="O", source=src, origin="mcp")

    result = tv.validate_template("overprint", db_session)

    assert result["ok"] is False
    row = reg.get(db_session, "overprint")
    assert row.status == "draft"
    assert row.last_error.startswith(tv.OVERPRINT)
    # No known engine bug on this host: the reason does not invent one.
    assert "1.40.27" not in row.last_error
    # A refused preview is not kept as if it were the template's look.
    assert not tv._preview_path("overprint").exists()


def test_overprint_reason_names_the_pdftex_bug_and_remedy(
    db_session, tmp_path, monkeypatch
):
    monkeypatch.setattr(tv.settings, "base_resumes_dir", tmp_path)
    monkeypatch.setattr(engines, "pdflatex_available", lambda: True)
    monkeypatch.setattr(
        engines,
        "probe_pdflatex",
        lambda: engines.EngineStatus(
            "pdflatex", True, version="pdfTeX 3.141592653-2.6-1.40.27 (TeX Live 2025)",
            warning=engines.PDFTEX_1_40_27_WARNING,
        ),
    )
    monkeypatch.setattr(pdf_render, "compile_pdf", _fake_latex_compile(_overprinted(tmp_path)))
    src = (pdf_render.TEMPLATE_DIR / pdf_render.RESUME_TEMPLATE).read_text(encoding="utf-8")
    reg.create_draft(db_session, id="overprint27", display_name="O", source=src, origin="mcp")

    tv.validate_template("overprint27", db_session)

    row = reg.get(db_session, "overprint27")
    assert row.status == "draft"
    assert engines.PDFTEX_1_40_27_WARNING in row.last_error


@pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")
def test_harshibar_on_this_hosts_pdftex(db_session, tmp_path, monkeypatch):
    """The real thing, on whatever TeX this host has: harshibar validates
    cleanly on a fixed pdfTeX and is refused (not shipped as garbage) on
    1.40.27."""
    monkeypatch.setattr(tv.settings, "base_resumes_dir", tmp_path)
    source = (pdf_render.TEMPLATE_DIR / "user" / "harshibar.tex.j2").read_text(encoding="utf-8")
    error = tv.compile_against_sample(source)
    if engines.probe_pdflatex().warning is None:
        assert error is None
    else:
        assert error is not None and error.startswith(tv.OVERPRINT)

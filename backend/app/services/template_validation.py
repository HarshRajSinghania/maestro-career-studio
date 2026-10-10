from __future__ import annotations

import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models.template import Template
from app.schemas.template import validate_template_id
from app.services import engines, pdf_render
from app.services.pdfium_lock import PDFIUM_LOCK
from app.services.sync import hooks

# The ONE wording for "this LaTeX template cannot be validated on this host".
# `compile_against_sample` returns it, `validate_template` records it as
# last_error, and `template_registry._seed_validate` writes it on a LaTeX seed
# it skips — so the gallery can say "requires TeX" from either path.
REQUIRES_TEX = "requires TeX (pdflatex not found)"

# The reason a template is refused because its sample PDF draws glyphs on top
# of each other (see `text_overprints`). When the probe knows an engine bug that
# causes it, its warning follows after ": ".
OVERPRINT = "text overprints: words are drawn on top of each other in the PDF"

# Glyph pairs whose boxes overlap by half or more both ways. Punctuation that
# kerns under a letter (italic "y,") and ligature glyphs are never counted, so a
# clean render scores ~0 however long it is; the pdfTeX 1.40.27 bug produced
# hundreds on SAMPLE_RESUME.
_OVERPRINT_MIN_PAIRS = 10
_OVERPRINT_IGNORED = frozenset(",.;:") | frozenset(chr(c) for c in range(0xFB00, 0xFB07))

SAMPLE_RESUME: dict = {
    "contact": {
        "name": "Jordan Sample",
        "email": "jordan.sample@example.com",
        "phone": "+1 (555) 010-1234",
        "location": "San Francisco, CA",
        "linkedin": "linkedin.com/in/jordansample",
        "github": "github.com/jordansample",
        "website": "jordansample.dev",
    },
    "summary": (
        "Senior software engineer with experience building reliable backend "
        "systems and data pipelines."
    ),
    "skills": [
        {"category": "Languages", "items": ["Python", "Go", "SQL"]},
        {"category": "Infrastructure", "items": ["PostgreSQL", "Docker", "AWS"]},
    ],
    "experience": [
        {
            "company": "Acme Corp",
            "role": "Senior Software Engineer",
            "location": "San Francisco, CA",
            "start_date": "2021",
            "end_date": "Present",
            "bullets": [
                "Built a high-throughput ingestion service handling 10M events/day.",
                "Reduced API p99 latency by 40% through query and caching work.",
            ],
        },
        {
            "company": "Globex",
            "role": "Software Engineer",
            "location": "Remote",
            "start_date": "2018",
            "end_date": "2021",
            "bullets": [
                "Led migration of a monolith to modular services.",
            ],
        },
    ],
    "projects": [
        {
            "name": "Maestro CS",
            "enabled": True,
            "tech": "Python, FastAPI, LaTeX",
            "date": "2024",
            "bullets": [
                "Automated tailoring of resumes to job descriptions.",
            ],
        },
    ],
    "education": [
        {
            "institution": "State University",
            "degree": "B.S. Computer Science",
            "field": "Computer Science",
            "location": "Berkeley, CA",
            "start_date": "2014",
            "end_date": "2018",
            # Prefixed exactly like the real base resumes ("GPA 4.0/4.0"); the
            # sentinel guards against a template re-labeling it ("GPA: GPA ...").
            "gpa": "GPA 3.8/4.0",
            "coursework": ["Algorithms", "Distributed Systems"],
            "bullets": ["Teaching assistant for introductory programming."],
        },
    ],
    "certifications": ["AWS Certified Solutions Architect"],
    # Sentinel custom sections — one of each union variant. Their sole purpose is
    # template certification: a template that renders custom sections reproduces
    # these unmistakable phrases in the extracted PDF text; a template that omits
    # the extra_sections block silently drops them, which build_parse_report
    # detects as extra_sections_supported=False. The phrases are deliberately
    # unique so they cannot be confused with core-section content.
    "extra_sections": [
        {
            "key": "publications",
            "title": "Publications",
            "type": "entries",
            "entries": [
                {
                    "heading": "Sentinel Publication Alpha",
                    "subheading": "Journal of Sentinels",
                    "location": "Remote",
                    "date": "2024",
                    "link": "https://example.com/sentinel-paper",
                    "bullets": ["Sentinel entry bullet detail."],
                }
            ],
        },
        {
            "key": "awards",
            "title": "Awards",
            "type": "bullets",
            "bullets": ["Sentinel award bullet detail, 2024"],
        },
    ],
}


def _preview_path(template_id: str) -> Path:
    """Where this template's preview PDF is written. Never outside that folder.

    The caller mkdir's this path's parent and `shutil.copy`s into it, so before
    the id was validated anywhere but the REST schema, a stored id of
    `../../logs/pwned` wrote a PDF outside the preview root (SEC-05).

    Two checks, and the second is not redundant: `validate_template_id` says the
    id is a slug, and the `relative_to` says the resolved path is still under
    the root. If the alphabet ever widens, the containment check is what keeps
    holding — it is the same resolve-then-`relative_to` that
    `template_registry._resolve_partial` uses for bundled partials.
    """
    validate_template_id(template_id)
    root = (Path(settings.base_resumes_dir) / "template_previews").resolve()
    candidate = (root / f"{template_id}.pdf").resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:  # pragma: no cover - unreachable while the slug holds
        raise ValueError(f"preview path escapes its directory: {template_id!r}") from exc
    return candidate


# Multi-word phrases from SAMPLE_RESUME's CORE sections (contact name,
# experience, education) — the parts essentially every resume template renders.
# A word-joining template damages every multi-word phrase, so if these survive a
# strict extractor the template preserves word boundaries (see the interword
# space fix in pdf_render). Deliberately no phrases from optional sections
# (certifications/coursework): a well-spaced template that simply omits a section
# must not be mistaken for a broken one. Line wraps are fine — whitespace is
# normalised before matching.
PARSE_FIDELITY_PROBES: list[str] = [
    "Jordan Sample",  # contact name
    "Senior Software Engineer",  # experience role
    "Software Engineer",  # experience role
    "Acme Corp",  # experience company
    "Computer Science",  # education degree/field
    "State University",  # education institution
]

# S2's template half: does contact survive into the extracted body text?
# Email only — phone formats vary too much across templates to probe reliably.
CONTACT_PROBES: list[str] = ["jordan.sample@example.com"]

# S4: are the standard section header strings present in the extracted text?
# Recorded in the parse report (headers_missing) — a serious gate, not part of
# the fatal word-boundary certification. Letterspaced/smallcaps headers can
# legitimately fail extraction; S4 is waivable for that reason.
HEADER_PROBES: list[str] = ["experience", "education", "skills"]

# Custom-section capability probes: one phrase from the entries-style sentinel
# heading, one from an entries-style bullet, and one from the bullets-style
# section. If ALL survive extraction the template renders both custom-section
# styles; if they are absent the template omitted the extra_sections block and
# would SILENTLY drop a real resume's custom sections. Reported as
# extra_sections_supported — a separate capability signal, NOT part of
# parse_certified (a template may legitimately certify for core-only resumes).
# Deliberately kept OUT of HEADER_PROBES: a core-only-ready template must not be
# failed for lacking custom-section support.
EXTRA_SECTION_PROBES: list[str] = [
    "sentinel publication alpha",  # entries-style heading
    "sentinel entry bullet detail",  # entries-style bullet
    "sentinel award bullet detail",  # bullets-style item
]


def text_overprints(pdf_path: Path) -> bool | None:
    """True when glyphs in `pdf_path` sit on top of other glyphs — garbage a
    reader sees but a text extractor does not (it reads the characters in
    stream order, so the parse gate passes it). None = could not assess.

    Two glyph boxes "collide" when they overlap by at least half the narrower
    width AND half the shorter height. Identical boxes are one ligature glyph
    split into its letters ("fi", "ft"), not a collision."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None
    pages: list[list[tuple[float, float, float, float]]] = []
    with PDFIUM_LOCK:  # PDFium is not thread-safe (see pdfium_lock)
        try:
            pdf = pdfium.PdfDocument(str(pdf_path))
        except Exception:  # noqa: BLE001 — unreadable PDF = not assessed
            return None
        try:
            for page in pdf:
                textpage = page.get_textpage()
                boxes = []
                for i in range(textpage.count_chars()):
                    char = textpage.get_text_range(i, 1)
                    if not char.strip() or char in _OVERPRINT_IGNORED:
                        continue
                    left, bottom, right, top = textpage.get_charbox(i)
                    if right - left > 0.5 and top - bottom > 0.5:
                        boxes.append((left, bottom, right, top))
                pages.append(boxes)
        except Exception:  # noqa: BLE001 — a broken page = not assessed
            return None
        finally:
            pdf.close()
    pairs = 0
    for boxes in pages:
        boxes.sort()
        for a, (l1, b1, r1, t1) in enumerate(boxes):
            for l2, b2, r2, t2 in boxes[a + 1:]:
                if l2 >= r1:
                    break
                if (l1, b1, r1, t1) == (l2, b2, r2, t2):
                    continue
                overlap_x = min(r1, r2) - max(l1, l2)
                overlap_y = min(t1, t2) - max(b1, b2)
                if (
                    overlap_x >= 0.5 * min(r1 - l1, r2 - l2)
                    and overlap_y >= 0.5 * min(t1 - b1, t2 - b2)
                ):
                    pairs += 1
    return pairs >= _OVERPRINT_MIN_PAIRS


def _overprint_reason() -> str:
    warning = engines.probe_pdflatex().warning
    return f"{OVERPRINT}: {warning}" if warning else OVERPRINT


def _extract_normalized(pdf_path: Path) -> str | None:
    """Extract `pdf_path` with pdfplumber (strict) → whitespace-normalized,
    lowercased text, or None if the extractor is unavailable or the PDF unreadable.
    None means NOT ASSESSED — never treat it as a pass."""
    try:
        import pdfplumber
    except ImportError:
        return None
    try:
        with pdfplumber.open(pdf_path) as doc:
            text = " ".join((page.extract_text() or "") for page in doc.pages)
    except Exception:  # noqa: BLE001 — unreadable PDF = not assessed, never certified
        return None
    return " ".join(text.split()).lower()


def build_parse_report(pdf_path: Path) -> dict | None:
    """Full extraction report for a compiled template preview, or None when the
    extractor couldn't run (not assessed)."""
    normalized = _extract_normalized(pdf_path)
    if normalized is None:
        return None
    try:
        import pdfplumber
        version = getattr(pdfplumber, "__version__", "unknown")
    except ImportError:  # pragma: no cover — _extract_normalized already returned text
        version = "unknown"
    missing = [p for p in PARSE_FIDELITY_PROBES if p.lower() not in normalized]
    extra_missing = [p for p in EXTRA_SECTION_PROBES if p not in normalized]
    return {
        "extractor": "pdfplumber",
        "extractor_version": version,
        "missing": missing,
        "email_ok": all(p.lower() in normalized for p in CONTACT_PROBES),
        "headers_missing": [h for h in HEADER_PROBES if h not in normalized],
        # Capability signal (NOT part of parse_certified): does the template
        # render the sentinel custom sections in SAMPLE_RESUME? A template that
        # omits extra_sections extracts none of the sentinel phrases → False.
        "extra_sections_supported": not extra_missing,
        "extra_sections_missing": extra_missing,
        "checked_at": datetime.now(UTC).isoformat(),
    }


def compile_against_sample(
    source: str,
    keep_pdf_at: Path | None = None,
    default_formatting: dict | None = None,
    engine: str = "latex",
) -> str | None:
    """Render `source` against SAMPLE_RESUME and compile it with the template's
    engine (pdflatex subprocess, or in-process typst via sys_inputs). Returns
    None on success (copying the PDF to `keep_pdf_at` when given), or the error
    text on failure. The pdfplumber probe gate downstream is engine-neutral —
    it operates on the produced PDF."""
    if engine == "typst":
        try:
            sys_inputs = pdf_render.build_typst_sys_inputs(
                source, SAMPLE_RESUME, default_formatting
            )
        except Exception as exc:  # noqa: BLE001
            return f"render error: {exc}"[-2000:]
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            try:
                pdf_render.compile_typst_pdf(
                    source, out_dir, stem="preview", sys_inputs=sys_inputs
                )
            except Exception as exc:  # noqa: BLE001
                return str(exc)[-2000:]
            if keep_pdf_at is not None:
                keep_pdf_at.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(out_dir / "preview.pdf", keep_pdf_at)
        return None

    # ---- latex ----
    # Validation NEVER substitutes: validating template A must never validate
    # template B (that is the document-render rule, pdf_render.
    # resolve_render_template). Without pdflatex the answer is simply "cannot
    # validate here", recorded as the reason instead of a spawn traceback.
    if not engines.pdflatex_available():
        return REQUIRES_TEX
    try:
        tex = pdf_render.render_tex_from_source(
            source, SAMPLE_RESUME, formatting=default_formatting
        )
    except Exception as exc:  # noqa: BLE001
        return f"render error: {exc}"[-2000:]

    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp)
        try:
            pdf_render.compile_pdf(tex, out_dir, stem="preview")
        except Exception as exc:  # noqa: BLE001
            return str(exc)[-2000:]
        # LaTeX only: the known cause is a pdfTeX build (engines.
        # PDFTEX_1_40_27_WARNING). Refused like a compile error — no preview
        # kept — so `get_usable_template` never renders a document with it.
        if text_overprints(out_dir / "preview.pdf"):
            return _overprint_reason()
        if keep_pdf_at is not None:
            keep_pdf_at.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(out_dir / "preview.pdf", keep_pdf_at)
    return None


def validate_template(template_id: str, session: Session) -> dict:
    row = session.get(Template, template_id)
    if row is None:
        raise LookupError(f"Template not found: {template_id}")

    hooks.require_profile_writable(session)
    preview = _preview_path(template_id)
    error = compile_against_sample(
        row.source,
        keep_pdf_at=preview,
        default_formatting=row.default_formatting,
        engine=row.engine,
    )
    if error is not None:
        row.status = "draft"
        row.last_error = error
        row.validated_at = None
        row.parse_certified = None
        row.parse_report_json = None
        session.commit()
        return {"ok": False, "error": row.last_error, "parse_certified": None, "parse_report": None}

    row.status = "ready"
    row.validated_at = datetime.now(UTC)
    row.last_error = None
    report = build_parse_report(preview)
    if report is not None and row.engine == "latex":
        # compile_against_sample refuses overprinting LaTeX before this point;
        # the marker is what `template_registry.
        # recheck_ready_latex_for_engine_warning` skips on.
        report["overprint_checked"] = True
    if report is None:
        row.parse_certified = None
        row.parse_report_json = None
    else:
        row.parse_certified = not report["missing"]
        row.parse_report_json = report
    session.commit()
    return {"ok": True, "error": None, "parse_certified": row.parse_certified, "parse_report": row.parse_report_json}

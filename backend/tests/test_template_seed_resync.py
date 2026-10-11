"""Seed-time resync of superseded bundled template sources (design 2026-09-19,
plan Task 5b). A seeded row keeps its source forever unless its digest matches
a version we shipped before; a user-edited row matches nothing and is never
touched."""
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.models.template import Template
from app.services import template_registry as reg

FIXTURES = Path(__file__).parent / "fixtures" / "templates_superseded"


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _current_source(seed_id: str) -> str:
    if seed_id == reg.DEFAULT_ID:
        return (reg.pdf_render.TEMPLATE_DIR / reg.pdf_render.RESUME_TEMPLATE).read_text(
            encoding="utf-8"
        )
    for tid, _name, _engine, filename, _fmt in reg.BUNDLED_TEMPLATES:
        if tid == seed_id:
            return (reg._USER_TEMPLATE_DIR / filename).read_text(encoding="utf-8")
    raise AssertionError(seed_id)


def _fixture_versions() -> list[tuple[str, Path]]:
    return sorted(
        (d.name, f)
        for d in FIXTURES.iterdir() if d.is_dir()
        for f in d.iterdir() if f.name.endswith((".tex.j2", ".typ"))
    )


def test_pinned_digests_match_the_frozen_fixtures():
    # A wrong literal is a silent no-op, which is why the digests are pinned
    # AND checked against the bytes they were computed from.
    expected: dict[str, set[str]] = {}
    for seed_id, path in _fixture_versions():
        expected.setdefault(seed_id, set()).add(_digest(path.read_text(encoding="utf-8")))
    assert {k: set(v) for k, v in reg.SUPERSEDED_SEED_DIGESTS.items()} == expected


def test_no_current_bundled_source_is_listed_as_superseded():
    for seed_id, digests in reg.SUPERSEDED_SEED_DIGESTS.items():
        assert _digest(_current_source(seed_id)) not in digests


def test_current_bundled_sources_are_pinned():
    # The tripwire: editing a bundled user template without freezing the bytes
    # it replaces strands every install seeded from those bytes on the old
    # source forever, because nothing would recognise them as ours.
    assert set(reg.CURRENT_SEED_DIGESTS) == set(reg.SUPERSEDED_SEED_DIGESTS)
    for seed_id, pinned in reg.CURRENT_SEED_DIGESTS.items():
        assert _digest(_current_source(seed_id)) == pinned, (
            f"{seed_id}: the bundled source changed. Freeze the OLD bytes under "
            f"tests/fixtures/templates_superseded/{seed_id}/<n>.tex.j2, add their "
            f"digest to SUPERSEDED_SEED_DIGESTS, then update CURRENT_SEED_DIGESTS."
        )


@pytest.mark.parametrize(
    "seed_id,path",
    _fixture_versions(),
    ids=lambda v: str(v) if isinstance(v, str) else v.name,
)
def test_a_seed_row_with_a_shipped_old_source_is_resynced(db_session, seed_id, path):
    reg.reset_seed_validation_attempts()
    old = path.read_text(encoding="utf-8")
    db_session.add(
        Template(
            id=seed_id,
            display_name="old",
            source=old,
            engine="typst" if path.suffix == ".typ" else "latex",
            status="ready",
            origin="seed",
            # Stale evidence about the OLD bytes, all four fields populated so
            # each clear below is a real assertion rather than a default.
            validated_at=datetime.now(UTC),
            parse_certified=True,
            parse_report_json={"missing": []},
            last_error="stale",
            default_formatting={"font_size": 10},
        )
    )
    db_session.commit()
    reg.ensure_seed_templates(db_session, validate=False)
    row = db_session.get(Template, seed_id)
    assert row.source == _current_source(seed_id)
    assert row.status == "draft"
    assert row.validated_at is None
    assert row.parse_certified is None
    assert row.parse_report_json is None
    assert row.last_error is None
    assert row.default_formatting == {"font_size": 10}  # the user's, never touched


def test_a_user_edited_seed_row_is_never_touched(db_session):
    reg.reset_seed_validation_attempts()
    seed_id, path = _fixture_versions()[0]
    edited = path.read_text(encoding="utf-8") + "% my tweak\n"
    db_session.add(
        Template(
            id=seed_id,
            display_name="mine",
            source=edited,
            engine="latex",
            status="ready",
            origin="seed",
        )
    )
    db_session.commit()
    reg.ensure_seed_templates(db_session, validate=False)
    assert db_session.get(Template, seed_id).source == edited


def test_a_user_copy_with_the_old_bytes_is_never_touched(db_session):
    reg.reset_seed_validation_attempts()
    seed_id, path = _fixture_versions()[0]
    old = path.read_text(encoding="utf-8")
    db_session.add(
        Template(
            id=f"{seed_id}_copy",
            display_name="copy",
            source=old,
            engine="latex",
            status="ready",
            origin="frontend",
        )
    )
    db_session.commit()
    reg.ensure_seed_templates(db_session, validate=False)
    assert db_session.get(Template, f"{seed_id}_copy").source == old


def test_a_seed_id_row_the_user_minted_is_never_touched(db_session):
    # Same id as a bundled seed, same old bytes, but not ours: the origin gate
    # is what keeps the digest match from rewriting a row the user created.
    reg.reset_seed_validation_attempts()
    seed_id, path = _fixture_versions()[0]
    old = path.read_text(encoding="utf-8")
    db_session.add(
        Template(
            id=seed_id,
            display_name="mine",
            source=old,
            engine="latex",
            status="ready",
            origin="frontend",
        )
    )
    db_session.commit()
    reg.ensure_seed_templates(db_session, validate=False)
    row = db_session.get(Template, seed_id)
    assert row.source == old
    assert row.status == "ready"


def test_resync_is_idempotent(db_session):
    reg.reset_seed_validation_attempts()
    seed_id, path = _fixture_versions()[0]
    db_session.add(
        Template(
            id=seed_id,
            display_name="old",
            source=path.read_text(encoding="utf-8"),
            engine="latex",
            status="ready",
            origin="seed",
        )
    )
    db_session.commit()
    reg.ensure_seed_templates(db_session, validate=False)
    first = db_session.get(Template, seed_id).updated_at
    reg.ensure_seed_templates(db_session, validate=False)
    assert db_session.get(Template, seed_id).updated_at == first


@pytest.mark.parametrize("selected", ["classic", "other", "none"])
@pytest.mark.parametrize("source_kind", ["shipped", "edited", "user_owned"])
def test_classic_resync_preserves_user_source_and_default_choice(db_session, selected, source_kind):
    old = (FIXTURES / "default/1.tex.j2").read_text(encoding="utf-8")
    source = old + "% my tweak\n" if source_kind == "edited" else old
    row = Template(
        id=reg.DEFAULT_ID, display_name="My Classic", source=source, engine="latex",
        status="ready", is_default=selected == "classic",
        origin="frontend" if source_kind == "user_owned" else "seed",
        default_formatting={"font_size": 10},
    )
    db_session.add(row)
    if selected == "other":
        db_session.add(Template(
            id="my_default", display_name="Mine", source="my source", engine="latex",
            status="ready", is_default=True, origin="frontend",
        ))
    db_session.commit()

    reg.ensure_seed_templates(db_session, validate=False)

    expected = _current_source(reg.DEFAULT_ID) if source_kind == "shipped" else source
    assert row.source == expected
    assert row.status == ("draft" if source_kind == "shipped" else "ready")
    assert row.display_name == "My Classic"
    assert row.default_formatting == {"font_size": 10}
    defaults = [t.id for t in reg.list_all(db_session) if t.is_default]
    assert defaults == (["my_default"] if selected == "other" else [reg.DEFAULT_ID])
    updated_at = row.updated_at
    reg.ensure_seed_templates(db_session, validate=False)
    assert row.updated_at == updated_at


def test_unselected_classic_revalidates_after_resync_and_retries_next_boot(db_session, monkeypatch):
    old = (FIXTURES / "default/1.tex.j2").read_text(encoding="utf-8")
    row = Template(
        id=reg.DEFAULT_ID, display_name="Classic", source=old, engine="latex",
        status="ready", origin="seed",
    )
    chosen = Template(
        id="my_default", display_name="Mine", source="my source", engine="latex",
        status="ready", is_default=True, origin="frontend",
    )
    db_session.add_all([row, chosen])
    db_session.commit()
    from app.services import template_validation
    monkeypatch.setattr(reg.engines, "pdflatex_available", lambda: True)

    def failed_validation(template_id, session):
        raise RuntimeError("no renderer")

    monkeypatch.setattr(template_validation, "validate_template", failed_validation)
    assert reg._bootstrap_default(db_session) is chosen
    assert row.source == _current_source(reg.DEFAULT_ID)
    assert row.last_error == "seed validation error: no renderer"
    assert row.status == "draft"

    reg.reset_seed_validation_attempts()

    def successful_validation(template_id, session):
        template = session.get(Template, template_id)
        template.status = "ready"
        template.validated_at = datetime.now(UTC)
        template.last_error = None
        session.commit()

    monkeypatch.setattr(template_validation, "validate_template", successful_validation)
    assert reg._bootstrap_default(db_session) is chosen
    assert row.status == "ready"
    assert row.validated_at is not None
    assert row.last_error is None

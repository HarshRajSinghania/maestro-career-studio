import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.config import settings
from app.models.application import Application
from app.models.ats_score import AtsScore
from app.models.base_resume import BaseResume
from app.models.job import Job
from app.services import ats_score
from app.services.ats.config import ENGINE_VERSION, load_config
from tests.ats.fixtures import SAMPLE_JD, SAMPLE_RESUME


def _seed_job(db_session, extracted_json=SAMPLE_JD):
    job = Job(raw_text="jd", raw_text_hash="h-ats-svc", extracted_json=extracted_json)
    db_session.add(job)
    db_session.flush()
    return job


def _seed_base(db_session, tmp_path, monkeypatch, slug="data_scientist"):
    monkeypatch.setattr(settings, "base_resumes_dir", tmp_path)
    (tmp_path / f"{slug}.json").write_text(json.dumps(SAMPLE_RESUME))
    db_session.add(BaseResume(slug=slug, data_json=SAMPLE_RESUME))
    db_session.flush()
    return slug


def _row(job_id, *, target_type, target_id, phase, composite, created_at,
         application_id=None, engine_version="ats-1.0.0", config_version="c1"):
    """Hand-written AtsScore row with an explicit created_at (bypasses now())."""
    return AtsScore(
        job_id=job_id, target_type=target_type, target_id=target_id, phase=phase,
        composite=composite, subscores_json={}, skill_table_json=[],
        config_version=config_version, engine_version=engine_version,
        application_id=application_id, created_at=created_at,
    )


def test_score_base_resume_persists_row(db_session, tmp_path, monkeypatch):
    job = _seed_job(db_session)
    slug = _seed_base(db_session, tmp_path, monkeypatch)
    row = ats_score.score_target(job.id, "base_resume", slug, phase="base", session=db_session)
    assert row.id is not None and row.phase == "base" and row.target_id == slug
    assert row.skill_table_json and row.subscores_json["keyword"] >= 0


def test_score_all_bases(db_session, tmp_path, monkeypatch):
    job = _seed_job(db_session)
    _seed_base(db_session, tmp_path, monkeypatch, "data_scientist")
    _seed_base(db_session, tmp_path, monkeypatch, "data_engineer")
    rows = ats_score.score_all_bases(job.id, session=db_session)
    assert {r.target_id for r in rows} == {"data_scientist", "data_engineer"}


def test_score_all_bases_raises_on_job_with_no_skills(db_session, tmp_path, monkeypatch):
    """A job-level problem must raise, not be swallowed into an empty result."""
    job = _seed_job(db_session, extracted_json={"title": "X", "skills": []})
    _seed_base(db_session, tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="skills"):
        ats_score.score_all_bases(job.id, session=db_session)


def test_score_all_bases_skips_missing_file_slug(db_session, tmp_path, monkeypatch):
    """An active slug without an on-disk data file is skipped; the rest score."""
    job = _seed_job(db_session)
    _seed_base(db_session, tmp_path, monkeypatch, "data_scientist")
    db_session.add(BaseResume(slug="fileless", data_json=SAMPLE_RESUME))
    db_session.flush()
    rows = ats_score.score_all_bases(job.id, session=db_session)
    assert {r.target_id for r in rows} == {"data_scientist"}


def test_base_phase_upserts_tailored_appends(db_session, tmp_path, monkeypatch):
    job = _seed_job(db_session)
    slug = _seed_base(db_session, tmp_path, monkeypatch)
    a = ats_score.score_target(job.id, "base_resume", slug, phase="base", session=db_session)
    b = ats_score.score_target(job.id, "base_resume", slug, phase="base", session=db_session)
    assert a.id == b.id  # base rows upsert (latest state per job+slug)

    app_row = Application(job_id=job.id, base_resume=slug, customized_json=SAMPLE_RESUME)
    db_session.add(app_row)
    db_session.flush()
    t1 = ats_score.score_target(job.id, "application", str(app_row.id), phase="tailored", session=db_session)
    t2 = ats_score.score_target(job.id, "application", str(app_row.id), phase="tailored", session=db_session)
    assert t1.id != t2.id  # tailored rows append (score trajectory)


def test_base_score_captures_gaps_json(db_session, tmp_path, monkeypatch):
    """Every base ATS row carries a base-vs-JD gap snapshot (build_gaps shape)."""
    job = _seed_job(db_session)
    slug = _seed_base(db_session, tmp_path, monkeypatch)
    row = ats_score.score_target(job.id, "base_resume", slug, phase="base", session=db_session)
    assert row.gaps_json is not None
    assert isinstance(row.gaps_json["categories"], list)
    assert "base_composite" in row.gaps_json
    # SAMPLE_RESUME is missing JD skills (Snowflake, Salesforce, ...): non-empty gaps.
    assert row.gaps_json["categories"]


def test_base_rescore_refreshes_gaps_json(db_session, tmp_path, monkeypatch):
    """A base upsert (second score) still stores a non-null, refreshed gaps_json."""
    job = _seed_job(db_session)
    slug = _seed_base(db_session, tmp_path, monkeypatch)
    a = ats_score.score_target(job.id, "base_resume", slug, phase="base", session=db_session)
    assert a.gaps_json is not None
    b = ats_score.score_target(job.id, "base_resume", slug, phase="base", session=db_session)
    assert a.id == b.id  # base rows upsert
    assert b.gaps_json is not None
    assert isinstance(b.gaps_json["categories"], list)


def test_tailored_score_captures_gaps_json(db_session, tmp_path, monkeypatch):
    """The tailored (application) phase also stores a gap snapshot."""
    job = _seed_job(db_session)
    slug = _seed_base(db_session, tmp_path, monkeypatch)
    app_row = Application(job_id=job.id, base_resume=slug, customized_json=SAMPLE_RESUME)
    db_session.add(app_row)
    db_session.flush()
    row = ats_score.score_target(
        job.id, "application", str(app_row.id), phase="tailored", session=db_session
    )
    assert row.gaps_json is not None
    assert isinstance(row.gaps_json["categories"], list)
    assert "base_composite" in row.gaps_json


def test_compare_returns_deltas(db_session, tmp_path, monkeypatch):
    job = _seed_job(db_session)
    slug = _seed_base(db_session, tmp_path, monkeypatch)
    improved = copy.deepcopy(SAMPLE_RESUME)
    improved["skills"][0]["items"].append("Salesforce")
    improved["experience"][0]["bullets"].append("Integrated Salesforce CRM data.")
    app_row = Application(job_id=job.id, base_resume=slug, customized_json=improved)
    db_session.add(app_row)
    db_session.flush()

    comparison = ats_score.compare(app_row.id, session=db_session)  # computes both on demand
    assert comparison["delta"]["composite"] > 0
    # v2: the subscore delta spans all five composite dimensions
    assert set(comparison["delta"]["subscores"]) == {
        "keyword", "placement_recency", "semantic_fit", "title", "format",
    }
    moved = [d for d in comparison["skill_diff"] if d["jd_skill"] == "Salesforce"]
    assert moved and moved[0]["before"]["matched"] is False and moved[0]["after"]["matched"] is True
    # application_id semantics: base rows are shared per job+slug (never stamped
    # with a triggering application); tailored rows carry their application.
    assert comparison["base"].application_id is None
    assert comparison["tailored"].application_id == app_row.id


def test_compare_raises_on_version_mismatch(db_session, tmp_path, monkeypatch):
    job = _seed_job(db_session)
    slug = _seed_base(db_session, tmp_path, monkeypatch)
    app_row = Application(job_id=job.id, base_resume=slug, customized_json=SAMPLE_RESUME)
    db_session.add(app_row)
    db_session.add(_row(
        job.id, target_type="base_resume", target_id=slug, phase="base",
        composite=10.0, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        engine_version="ats-0.9.9",
    ))
    db_session.commit()
    with pytest.raises(ValueError, match="versions"):
        ats_score.compare(app_row.id, session=db_session)


def test_compare_rejects_pre_phase2_config_with_same_engine_stamp(
    db_session, tmp_path, monkeypatch
):
    """The config hash is independently guarded, not only ENGINE_VERSION."""
    job = _seed_job(db_session)
    slug = _seed_base(db_session, tmp_path, monkeypatch)
    app_row = Application(job_id=job.id, base_resume=slug, customized_json=SAMPLE_RESUME)
    db_session.add(app_row)
    db_session.flush()
    now = datetime(2026, 7, 16, tzinfo=timezone.utc)
    db_session.add_all([
        _row(
            job.id,
            target_type="base_resume",
            target_id=slug,
            phase="base",
            composite=10.0,
            created_at=now,
            engine_version=ENGINE_VERSION,
            config_version="pre-phase2",
        ),
        _row(
            job.id,
            target_type="application",
            target_id=str(app_row.id),
            phase="tailored",
            composite=11.0,
            created_at=now + timedelta(seconds=1),
            application_id=app_row.id,
            engine_version=ENGINE_VERSION,
            config_version=load_config().version,
        ),
    ])
    db_session.commit()

    with pytest.raises(ValueError, match="versions"):
        ats_score.compare(app_row.id, session=db_session)


def test_latest_scores_dedupes_and_orders(db_session):
    job = _seed_job(db_session)
    app_row = Application(job_id=job.id, base_resume="data_scientist", customized_json=SAMPLE_RESUME)
    db_session.add(app_row)
    db_session.flush()
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    db_session.add_all([
        _row(job.id, target_type="base_resume", target_id="data_scientist",
             phase="base", composite=50.0, created_at=t0),
        _row(job.id, target_type="base_resume", target_id="data_engineer",
             phase="base", composite=70.0, created_at=t0),
        _row(job.id, target_type="application", target_id=str(app_row.id), phase="tailored",
             composite=55.0, created_at=t0 + timedelta(minutes=1), application_id=app_row.id),
        _row(job.id, target_type="application", target_id=str(app_row.id), phase="tailored",
             composite=60.0, created_at=t0 + timedelta(minutes=2), application_id=app_row.id),
    ])
    db_session.commit()
    rows = ats_score.latest_scores(job.id, db_session)
    # newest row per target kept (60 beats the older 55); base phase first, composite desc
    assert [(r.target_id, float(r.composite)) for r in rows] == [
        ("data_engineer", 70.0),
        ("data_scientist", 50.0),
        (str(app_row.id), 60.0),
    ]


def test_latest_scores_omits_archived_and_deleted_bases(db_session):
    job = _seed_job(db_session)
    app_row = Application(job_id=job.id, base_resume="kept", customized_json=SAMPLE_RESUME)
    db_session.add(app_row)
    db_session.add_all([
        BaseResume(slug="kept", data_json={}),
        BaseResume(slug="archived_track", data_json={}, archived_at=datetime(2026, 8, 4, tzinfo=timezone.utc)),
        BaseResume(slug="deleted_track", data_json={}, deleted_at=datetime(2026, 8, 4, tzinfo=timezone.utc)),
    ])
    db_session.flush()
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    db_session.add_all([
        _row(job.id, target_type="base_resume", target_id="kept",
             phase="base", composite=50.0, created_at=t0),
        _row(job.id, target_type="base_resume", target_id="archived_track",
             phase="base", composite=90.0, created_at=t0),
        _row(job.id, target_type="base_resume", target_id="deleted_track",
             phase="base", composite=80.0, created_at=t0),
        _row(job.id, target_type="application", target_id=str(app_row.id), phase="tailored",
             composite=60.0, created_at=t0, application_id=app_row.id),
    ])
    db_session.commit()

    rows = ats_score.latest_scores(job.id, db_session)

    # Archived and deleted bases drop out; the APPLICATION row must survive —
    # it is a different target_type and nothing about it is archived.
    assert [r.target_id for r in rows] == ["kept", str(app_row.id)]


def _seed_country_bases(db_session, tmp_path, monkeypatch, **countries_by_slug):
    """Bases with files on disk and the given `countries` anchors."""
    for slug, countries in countries_by_slug.items():
        _seed_base(db_session, tmp_path, monkeypatch, slug)
        db_session.get(BaseResume, slug).countries = countries
    db_session.flush()


def test_score_all_bases_skips_other_country_bases(db_session, tmp_path, monkeypatch):
    job = _seed_job(db_session)
    job.country = "US"
    _seed_country_bases(db_session, tmp_path, monkeypatch,
                        india=["IN"], us=["US"], anywhere=[])

    rows = ats_score.score_all_bases(job.id, session=db_session)
    assert {r.target_id for r in rows} == {"us", "anywhere"}

    rows = ats_score.score_all_bases(job.id, session=db_session, include_other_countries=True)
    assert {r.target_id for r in rows} == {"us", "anywhere", "india"}


def test_score_all_bases_scores_everything_when_no_base_matches(db_session, tmp_path, monkeypatch):
    job = _seed_job(db_session)
    job.country = "US"
    _seed_country_bases(db_session, tmp_path, monkeypatch, india=["IN"])
    rows = ats_score.score_all_bases(job.id, session=db_session)
    assert {r.target_id for r in rows} == {"india"}


def test_latest_scores_hides_a_base_marked_for_another_country_after_scoring(
    db_session, tmp_path, monkeypatch
):
    job = _seed_job(db_session)
    job.country = "US"
    _seed_country_bases(db_session, tmp_path, monkeypatch, india=[], us=["US"])
    ats_score.score_all_bases(job.id, session=db_session)
    db_session.commit()
    assert {r.target_id for r in ats_score.latest_scores(job.id, db_session)} == {"india", "us"}

    db_session.get(BaseResume, "india").countries = ["IN"]
    db_session.commit()
    assert [r.target_id for r in ats_score.latest_scores(job.id, db_session)] == ["us"]
    assert {r.target_id for r in ats_score.latest_scores(
        job.id, db_session, include_other_countries=True)} == {"india", "us"}


def test_latest_scores_for_a_job_with_no_row_does_no_country_filtering(db_session):
    from uuid import uuid4
    assert ats_score.latest_scores(uuid4(), db_session) == []

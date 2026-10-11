"""Bundled template attribution survives distribution and fresh-install seeding."""
import tomllib
from pathlib import Path

import pytest

from app.services import pdf_render, template_registry as reg

ROOT = Path(__file__).resolve().parents[2]
DERIVED_SOURCES = (
    "resume.tex.j2",
    "_header.tex.j2",
    "cover_letter.tex.j2",
    "cover_letter.typ",
    "user/carlito_dense.tex.j2",
    "user/harshibar.tex.j2",
    "user/xcharter_serif.tex.j2",
    "user/xcharter_serif.typ",
)


@pytest.mark.parametrize("filename", DERIVED_SOURCES)
def test_derived_template_carries_attribution(filename):
    source = (pdf_render.TEMPLATE_DIR / filename).read_text(encoding="utf-8")
    prefix = "//" if filename.endswith(".typ") else "%"
    header = []
    for line in source.splitlines():
        if not line.startswith(prefix):
            break
        header.append(line)
    notice = "\n".join(header)
    assert "Copyright (c) 2020 Jake Gutierrez" in notice
    assert "Copyright (c) 2015 Sourabh Bajaj" in notice
    assert "MIT" in notice and "THIRD_PARTY_NOTICES.md" in notice
    assert "LICENSE-resume-templates.txt" in notice
    if filename == "user/harshibar.tex.j2":
        assert "Copyright (c) 2021 Harshi" in notice


def test_notices_cover_every_bundled_source_and_upstream_license():
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    for path in pdf_render.TEMPLATE_DIR.rglob("*"):
        if path.is_file():
            assert path.relative_to(ROOT).as_posix() in notices
    for copyright_line in (
        "Copyright (c) 2020 Jake Gutierrez",
        "Copyright (c) 2021 Harshi",
        "Copyright (c) 2015 Sourabh Bajaj",
    ):
        assert copyright_line in notices
    assert "Only `default` and `typst-classic`" not in notices
    assert notices.count('THE SOFTWARE IS PROVIDED "AS IS"') >= 3


def test_template_license_ships_with_the_template_package_data():
    config = tomllib.loads((ROOT / "backend/pyproject.toml").read_text())
    assert "templates/*" in config["tool"]["setuptools"]["package-data"]["app"]
    license_text = (pdf_render.TEMPLATE_DIR / "LICENSE-resume-templates.txt").read_text()
    for line in (
        "Copyright (c) 2020 Jake Gutierrez",
        "Copyright (c) 2021 Harshi",
        "Copyright (c) 2015 Sourabh Bajaj",
        "The above copyright notice and this permission notice shall be included in all",
        'THE SOFTWARE IS PROVIDED "AS IS"',
    ):
        assert line in license_text


def test_fresh_install_and_duplicate_preserve_template_attribution(db_session):
    reg.ensure_seed_templates(db_session, validate=False)
    expected_ids = {reg.DEFAULT_ID, reg.TYPST_CLASSIC_ID}
    expected_ids.update(item[0] for item in reg.BUNDLED_TEMPLATES)
    assert {row.id for row in reg.list_all(db_session)} == expected_ids
    for row in reg.list_all(db_session):
        if row.id == reg.TYPST_CLASSIC_ID:
            continue  # Original source; no third-party template notice.
        assert "Copyright (c) 2020 Jake Gutierrez" in row.source
        assert "Copyright (c) 2015 Sourabh Bajaj" in row.source
        assert "THIRD_PARTY_NOTICES.md" in row.source
    copied = reg.duplicate(db_session, "harshibar", "attribution_copy")
    assert "Copyright (c) 2021 Harshi" in copied.source
    assert copied.source == reg.get(db_session, "harshibar").source

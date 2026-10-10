import io
from pathlib import Path
import zipfile

import pytest

from app.services import attachment_extract

FIXTURES = Path(__file__).parent / "fixtures" / "docx"


@pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.glob("*.docx")))
def test_docx_text_matches_the_golden_file(name):
    data = (FIXTURES / name).read_bytes()
    expected = (FIXTURES / f"{name}.txt").read_text(encoding="utf-8")
    assert attachment_extract._docx_text(data) == expected


def _missing_document_bytes():
    output = io.BytesIO()
    with zipfile.ZipFile(FIXTURES / "paragraphs.docx") as source:
        with zipfile.ZipFile(output, "w") as archive:
            for name in source.namelist():
                if name != "word/document.xml":
                    archive.writestr(name, source.read(name))
    return output.getvalue()


@pytest.mark.parametrize(
    "data,error",
    [(b"not a zip file", zipfile.BadZipFile), (_missing_document_bytes(), KeyError)],
    ids=["non-zip", "missing-document"],
)
def test_unreadable_docx_keeps_the_parser_error_and_import_message(data, error):
    with pytest.raises(error) as caught:
        attachment_extract.extract_text("broken.docx", None, data)
    assert attachment_extract.plain_read_error(caught.value) == "Couldn't read this file."

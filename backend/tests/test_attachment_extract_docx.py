import io
from pathlib import Path
import struct
from xml.etree import ElementTree
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


def _repack_fixture(name, replacements, renames=None):
    output = io.BytesIO()
    renames = renames or {}
    with zipfile.ZipFile(FIXTURES / name) as source:
        with zipfile.ZipFile(output, "w") as archive:
            for part in source.namelist():
                content = replacements.get(part, source.read(part))
                if content is not None:
                    archive.writestr(renames.get(part, part), content)
    return output.getvalue()


@pytest.mark.parametrize(
    "target",
    [
        "word/document2.xml",
        "/word/document2.xml",
        "./word/document2.xml",
        "../word/document2.xml",
        "/word/../word/document2.xml",
        "word/./document2.xml",
        "word//document2.xml",
    ],
)
def test_docx_text_follows_the_main_part_relationship(target):
    with zipfile.ZipFile(FIXTURES / "paragraphs.docx") as source:
        rels = ElementTree.fromstring(source.read("_rels/.rels"))
        for relationship in rels:
            if relationship.get("Type", "").endswith("/officeDocument"):
                relationship.set("Target", target)
        content_types = ElementTree.fromstring(source.read("[Content_Types].xml"))
        for content_type in content_types:
            if content_type.get("PartName") == "/word/document.xml":
                content_type.set("PartName", "/word/document2.xml")
    data = _repack_fixture(
        "paragraphs.docx",
        {
            "_rels/.rels": ElementTree.tostring(rels),
            "[Content_Types].xml": ElementTree.tostring(content_types),
        },
        {
            "word/document.xml": "word/document2.xml",
            "word/_rels/document.xml.rels": "word/_rels/document2.xml.rels",
        },
    )
    expected = (FIXTURES / "paragraphs.docx.txt").read_text(encoding="utf-8")
    assert attachment_extract._docx_text(data) == expected


@pytest.mark.parametrize("rels", [None, b"<broken"], ids=["absent", "unparseable"])
def test_docx_text_falls_back_when_relationships_cannot_be_read(rels):
    data = _repack_fixture("paragraphs.docx", {"_rels/.rels": rels})
    expected = (FIXTURES / "paragraphs.docx.txt").read_text(encoding="utf-8")
    assert attachment_extract._docx_text(data) == expected


@pytest.mark.parametrize("case", ["first-row", "grid-before-gap"])
def test_docx_vertical_merge_without_a_cell_above_is_unreadable(case):
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(FIXTURES / "tables.docx") as source:
        document = ElementTree.fromstring(source.read("word/document.xml"))
    rows = document.find(f"{w}body/{w}tbl").findall(f"{w}tr")
    if case == "grid-before-gap":
        rows[0].remove(rows[0].find(f"{w}tc"))
        props = ElementTree.SubElement(rows[0], f"{w}trPr")
        ElementTree.SubElement(props, f"{w}gridBefore", {f"{w}val": "1"})
        cell = rows[1].find(f"{w}tc")
    else:
        cell = rows[0].find(f"{w}tc")
    ElementTree.SubElement(cell.find(f"{w}tcPr"), f"{w}vMerge")
    data = _repack_fixture("tables.docx", {"word/document.xml": ElementTree.tostring(document)})
    with pytest.raises(attachment_extract.UnreadableFile) as caught:
        attachment_extract.extract_text("broken.docx", None, data)
    assert isinstance(caught.value, ValueError)
    assert attachment_extract.plain_read_error(caught.value) == str(caught.value)


@pytest.mark.parametrize("declared_size", [50 * 1024 * 1024, 50 * 1024 * 1024 + 1])
def test_docx_main_part_size_is_checked_before_reading(declared_size):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            zipfile.ZipInfo("word/document.xml"),
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body><w:p><w:r><w:t>Small document</w:t></w:r></w:p></w:body></w:document>',
        )
    data = bytearray(output.getvalue())
    # The central-directory size is what getinfo() reports; keep the actual entry tiny.
    central_directory = data.index(b"PK\x01\x02")
    struct.pack_into("<I", data, central_directory + 24, declared_size)
    if declared_size > 50 * 1024 * 1024:
        # Reading this entry fails its CRC, so rejection must precede decompression.
        struct.pack_into("<I", data, central_directory + 16, 0)
        with pytest.raises(attachment_extract.UnreadableFile):
            attachment_extract.extract_text("large.docx", None, bytes(data))
    else:
        assert attachment_extract.extract_text("small.docx", None, bytes(data)) == "Small document"

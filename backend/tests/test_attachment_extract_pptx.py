import io
import struct
import zipfile
from xml.etree import ElementTree

import pytest

from app.services import attachment_extract as ax

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _slide(content, kind="sld"):
    return (
        f'<p:{kind} xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree>'
        f'{content}</p:spTree></p:cSld></p:{kind}>'
    )


def _shape(text, placeholder=""):
    return (
        f'<p:sp>{placeholder}<p:txBody><a:p><a:r><a:t>{text}</a:t></a:r>'
        '</a:p></p:txBody></p:sp>'
    )


def _rels(entries):
    return (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="{rid}" Type="{R}/{kind}" Target="{target}"/>'
            for rid, kind, target in entries
        )
        + '</Relationships>'
    )


def _deck_parts():
    bullets = (
        '<p:sp><p:txBody><a:p><a:pPr><a:buChar char="•"/></a:pPr>'
        '<a:r><a:t>Built </a:t></a:r><a:r><a:t>a pipeline</a:t></a:r>'
        '<a:br/><a:r><a:t>Reduced delays</a:t></a:r></a:p>'
        '<a:p><a:r><a:t>Mentored peers</a:t></a:r></a:p></p:txBody></p:sp>'
    )
    table = '<p:graphicFrame><a:graphic><a:graphicData><a:tbl>'
    for row in [("Metric", "Result"), ("Latency", "12% lower")]:
        table += '<a:tr>' + "".join(
            f'<a:tc><a:txBody><a:p><a:r><a:t>{cell}</a:t></a:r></a:p>'
            '</a:txBody></a:tc>' for cell in row
        ) + '</a:tr>'
    table += '</a:tbl></a:graphicData></a:graphic></p:graphicFrame>'
    return {
        'ppt/presentation.xml': (
            f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst>'
            '<p:sldId id="256" r:id="second"/><p:sldId id="257" r:id="first"/>'
            '<p:sldId id="258" r:id="empty"/></p:sldIdLst></p:presentation>'
        ),
        'ppt/_rels/presentation.xml.rels': _rels([
            ('first', 'slide', 'slides/slide1.xml'),
            ('second', 'slide', 'slides/slide2.xml'),
            ('empty', 'slide', 'slides/slide10.xml'),
        ]),
        'ppt/slides/slide1.xml': _slide(_shape('Results') + table + _shape('Next steps')),
        'ppt/slides/slide2.xml': _slide(_shape('Project overview') + bullets),
        'ppt/slides/slide10.xml': _slide('<p:pic/>'),
        'ppt/slides/_rels/slide2.xml.rels': _rels([
            ('notes', 'notesSlide', '../notesSlides/notesSlide7.xml'),
        ]),
        'ppt/notesSlides/notesSlide7.xml': _slide(
            _shape('I led delivery and measured the outcome.')
            + _shape('2', '<p:nvSpPr><p:nvPr><p:ph type="sldNum"/></p:nvPr></p:nvSpPr>'),
            kind='notes',
        ),
    }


def _zip(parts):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return output.getvalue()


@pytest.mark.parametrize('filename,mime', [('portfolio.PPTX', None), ('upload', PPTX_MIME)])
def test_deck_reads_in_presentation_order_with_tables_breaks_and_linked_notes(filename, mime):
    assert ax.extract_text(filename, mime, _zip(_deck_parts())) == (
        'Slide 1\nProject overview\nBuilt a pipeline\nReduced delays\nMentored peers\n'
        'Notes:\nI led delivery and measured the outcome.\n\n'
        'Slide 2\nResults\nMetric | Result\nLatency | 12% lower\nNext steps'
    )


@pytest.mark.parametrize('missing', [
    'ppt/presentation.xml', 'ppt/_rels/presentation.xml.rels', 'slide-list',
])
def test_missing_slide_order_falls_back_to_numeric_order(missing):
    parts = _deck_parts()
    if missing == 'slide-list':
        parts['ppt/presentation.xml'] = f'<p:presentation xmlns:p="{P}"/>'
    else:
        parts.pop(missing)
    text = ax.extract_text('deck.pptx', None, _zip(parts))
    assert text.startswith('Slide 1\nResults\n')
    assert 'Slide 2\nProject overview' in text
    assert 'Slide 3' not in text


@pytest.mark.parametrize('target', ['slides/slide2.xml', '/ppt/slides/slide2.xml', './slides/slide2.xml'])
def test_slide_relationship_targets_resolve_relative_to_presentation(target):
    parts = _deck_parts()
    parts['ppt/_rels/presentation.xml.rels'] = _rels([('second', 'slide', target)])
    parts['ppt/presentation.xml'] = (
        f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst>'
        '<p:sldId r:id="second"/></p:sldIdLst></p:presentation>'
    )
    assert ax.extract_text('deck.pptx', None, _zip(parts)).startswith('Slide 1\nProject overview')


@pytest.mark.parametrize('part', [
    'ppt/presentation.xml', 'ppt/_rels/presentation.xml.rels', 'ppt/slides/slide2.xml',
    'ppt/slides/_rels/slide2.xml.rels', 'ppt/notesSlides/notesSlide7.xml',
])
def test_malformed_deck_xml_uses_the_plain_read_error(part):
    parts = _deck_parts()
    parts[part] = '<broken'
    with pytest.raises(ElementTree.ParseError) as caught:
        ax.extract_text('broken.pptx', None, _zip(parts))
    assert ax.plain_read_error(caught.value) == "Couldn't read this file."


@pytest.mark.parametrize('data', [b'not a zip', _zip({})])
def test_unreadable_deck_uses_a_plain_sentence(data):
    with pytest.raises((zipfile.BadZipFile, ax.UnreadableFile)) as caught:
        ax.extract_text('broken.pptx', None, data)
    assert ax.plain_read_error(caught.value) in {"Couldn't read this file.", ax.NO_TEXT}


def test_notes_only_slide_is_kept_but_empty_slide_is_skipped():
    parts = _deck_parts()
    parts['ppt/slides/slide2.xml'] = _slide('<p:pic/>')
    text = ax.extract_text('deck.pptx', None, _zip(parts))
    assert text.startswith('Slide 1\nNotes:\nI led delivery')
    assert 'Slide 3' not in text


def test_slide_footer_text_is_kept_in_shape_order():
    parts = {'ppt/slides/slide1.xml': _slide(
        '<p:grpSp>' + _shape('Title') + _shape(
            'Project date', '<p:nvSpPr><p:nvPr><p:ph type="dt"/></p:nvPr></p:nvSpPr>',
        ) + '</p:grpSp>',
    )}
    assert ax.extract_text('deck.pptx', None, _zip(parts)) == 'Slide 1\nTitle\nProject date'


def test_image_only_deck_has_no_text_and_never_calls_vision(monkeypatch):
    def no_vision(*args, **kwargs):
        pytest.fail('Deck images must not be transcribed')

    monkeypatch.setattr(ax, '_transcribe_images', no_vision)
    with pytest.raises(ax.UnreadableFile, match=ax.NO_TEXT):
        ax.extract_text('empty.pptx', None, _zip({'ppt/slides/slide1.xml': _slide('<p:pic/>')}))


@pytest.mark.parametrize('target', ['/payload.xml', 'payload.bin'])
def test_relationships_cannot_read_parts_outside_the_xml_budget(target):
    parts = _deck_parts()
    parts['ppt/slides/_rels/slide2.xml.rels'] = _rels([('notes', 'notesSlide', target)])
    parts['payload.xml'] = parts['ppt/slides/payload.bin'] = _slide(_shape('Unbounded'), 'notes')
    with pytest.raises(ax.UnreadableFile) as caught:
        ax.extract_text('deck.pptx', None, _zip(parts))
    assert ax.plain_read_error(caught.value) == "This file isn't a readable PowerPoint deck."


@pytest.mark.parametrize('filename,mime', [('old.ppt', 'text/plain'), ('upload', 'application/vnd.ms-powerpoint')])
def test_binary_ppt_explains_how_to_save_a_supported_deck(filename, mime):
    with pytest.raises(ax.UnreadableFile) as caught:
        ax.extract_text(filename, mime, b'old binary deck')
    assert ax.plain_read_error(caught.value) == 'Save it as .pptx or PDF.'


@pytest.mark.parametrize('sizes', [[50 * 1024 * 1024 + 1], [30 * 1024 * 1024] * 2])
def test_deck_limits_xml_before_decompression_including_total_size(sizes):
    parts = {'ppt/slides/slide1.xml': _slide(_shape('Small slide'))}
    if len(sizes) > 1:
        parts['ppt/slides/slide2.xml'] = _slide(_shape('Another slide'))
    data = bytearray(_zip(parts))
    offset = 0
    for size in sizes:
        offset = data.index(b'PK\x01\x02', offset)
        struct.pack_into('<I', data, offset + 24, size)
        offset += 4
    with pytest.raises(ax.UnreadableFile, match='too large') as caught:
        ax.extract_text('large.pptx', None, bytes(data))
    assert ax.plain_read_error(caught.value) == 'This PowerPoint file is too large to read.'

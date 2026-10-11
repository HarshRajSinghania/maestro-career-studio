import base64

from app.services.llm_providers import gemini
from app.services.llm_providers.types import ImagePart, Message, ModelRef, Request, TextPart


def _req(model="gemini-2.5-flash", **kw):
    base = dict(model=ModelRef("gemini", model), messages=[Message("user", [TextPart("hi")])])
    base.update(kw)
    return Request(**base)


def test_text_payload_is_a_single_text_part_with_no_generation_config():
    assert gemini.build_generate_payload(_req()) == {"contents": [{"parts": [{"text": "hi"}]}]}


def test_json_output_sets_response_mime_type():
    payload = gemini.build_generate_payload(_req(output="json"))
    assert payload["generationConfig"] == {"responseMimeType": "application/json"}


def test_gemini_3_gets_a_thinking_level_pro_high_others_low():
    flash = gemini.build_generate_payload(_req("gemini-3-flash-preview", output="json"))
    assert flash["generationConfig"] == {"responseMimeType": "application/json",
                                         "thinkingConfig": {"thinkingLevel": "low"}}
    pro = gemini.build_generate_payload(_req("models/gemini-3-pro-preview"))
    assert pro["generationConfig"] == {"thinkingConfig": {"thinkingLevel": "high"}}


def test_images_become_inline_data_with_their_media_type():
    req = _req(messages=[Message("user", [TextPart("read"), ImagePart(b"png-bytes", "image/jpeg")])])
    parts = gemini.build_generate_payload(req)["contents"][0]["parts"]
    assert parts[0] == {"text": "read"}
    assert parts[1]["inlineData"]["mimeType"] == "image/jpeg"
    assert base64.b64decode(parts[1]["inlineData"]["data"]) == b"png-bytes"

from app.models.career_kb import KBEntity


def test_draft_422_on_empty_kb(client):
    r = client.post("/api/settings/persona/draft")
    assert r.status_code == 422


def test_draft_returns_llm_text_and_saves_nothing(client, db_session, monkeypatch):
    db_session.add(KBEntity(kind="experience", title="Data Scientist", org="Acme"))
    db_session.commit()

    from app.services import llm

    seen = {}

    def fake_call(**kwargs):
        seen.update(kwargs)
        return "Vision: ship data products."

    monkeypatch.setattr(llm, "call_openai", fake_call)

    r = client.post("/api/settings/persona/draft")
    assert r.status_code == 200
    assert r.json() == {"draft": "Vision: ship data products."}
    # Propose-only: the persona setting must remain empty.
    assert client.get("/api/settings/persona").json()["value"] == ""
    assert seen["trace_name"] == "persona-draft"

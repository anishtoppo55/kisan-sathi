def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Kisan Saathi" in r.text


def test_chat_returns_response_format(client, fakes):
    r = client.post("/api/chat", json={"message": "PM-Kisan kya hai?", "speak": False, "session_id": "s1"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"transcript", "lang", "reply_text", "cards", "sources", "audio_b64"}
    assert body["transcript"] == "PM-Kisan kya hai?"
    assert body["reply_text"] == "Jawab"
    assert body["lang"] == "hi-en"
    assert body["cards"] == [] and body["sources"] == [] and body["audio_b64"] is None
    assert fakes["pipeline"].calls == [("PM-Kisan kya hai?", "hi-en", "s1")]


def test_chat_detects_devanagari(client):
    r = client.post("/api/chat", json={"message": "धान की खेती", "speak": False})
    assert r.json()["lang"] == "hi-IN"


def test_chat_ignores_unknown_lang(client):
    r = client.post("/api/chat", json={"message": "hello", "lang": "xx", "speak": False})
    assert r.json()["lang"] == "hi-en"


def test_chat_empty_message_rejected(client):
    assert client.post("/api/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/chat", json={"message": "   "}).status_code == 422


def test_chat_overlong_message_rejected(client):
    assert client.post("/api/chat", json={"message": "a" * 1001}).status_code == 422


def test_eligibility_endpoint_uses_rules_only(client, monkeypatch):
    from app.modules.yojana import load_schemes
    from app.pipeline import Pipeline

    class NoLLM:
        async def generate(self, *a, **k):
            raise AssertionError("eligibility re-check must not call Gemini")

    p = Pipeline(NoLLM(), schemes=load_schemes())
    from app.main import app, get_pipeline
    app.dependency_overrides[get_pipeline] = lambda: p
    r = client.post("/api/eligibility", json={"profile": {"owns_land": True, "income_tax_payer": True}, "session_id": "s9"})
    assert r.status_code == 200
    body = r.json()
    card = body["cards"][0]
    assert card["type"] == "eligibility"
    statuses = {x["id"]: x["status"] for x in card["data"]["results"]}
    assert statuses["pm-kisan"] == "not_eligible"
    assert p.sessions.profile("s9").income_tax_payer is True
    assert body["audio_b64"] is None


def test_eligibility_endpoint_validates(client):
    assert client.post("/api/eligibility", json={"profile": {"land_acres": -1}}).status_code == 422

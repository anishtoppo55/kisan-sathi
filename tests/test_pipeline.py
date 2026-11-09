import json

import numpy as np
import pytest

from app.clients.gemini import GeminiError, SearchResult, extract_sources
from app.modules.yojana import load_schemes
from app.pipeline import (FALLBACK_REPLY, GENERAL_LABEL, KVK_REMINDER, NOT_FOUND_REPLY, Pipeline, SessionStore, keyword_route,
                          rag_prompt, system_prompt)
from app.rag.store import Chunk, Hit, VectorIndex

SCHEMES = load_schemes()


class FakeGemini:
    """Scriptable Gemini: route JSON, profile JSON, answers, embeddings and search."""

    def __init__(self, route=None, answers=None, profile=None, search=None, error=None, route_error=False, l10n=None):
        self.l10n = l10n
        self.route = route or {"intent": "general", "search_query": ""}
        self.answers = list(answers or ["ok"])
        self.profile = profile or {}
        self.search_result = search or SearchResult("web answer", [{"title": "Gov", "url": "https://gov.in"}])
        self.error, self.route_error = error, route_error
        self.calls = {"generate": [], "json": [], "embed": [], "search": []}
        self.qvec = [1, 0, 0]

    async def generate(self, system, user_text, history=None, **kw):
        self.calls["generate"].append({"system": system, "user": user_text, "history": list(history or [])})
        if self.error:
            raise self.error
        return self.answers.pop(0) if self.answers else "ok"

    async def generate_json(self, system, user_text, schema, **kw):
        self.calls["json"].append({"system": system, "user": user_text})
        if "intent" in schema.get("properties", {}):
            if self.route_error:
                raise GeminiError("router down")
            return dict(self.route)
        if "lines" in schema.get("properties", {}):
            if isinstance(self.l10n, Exception):
                raise self.l10n
            return {"lines": self.l10n(json.loads(user_text))} if callable(self.l10n) else {}
        return dict(self.profile)

    async def embed(self, texts):
        self.calls["embed"].append(texts)
        return [self.qvec for _ in texts]

    async def search(self, system, query):
        self.calls["search"].append(query)
        if isinstance(self.search_result, Exception):
            raise self.search_result
        return self.search_result


def make_index():
    chunks = [
        Chunk("pm-kisan#4", "PM-Kisan — Documents", "- Aadhaar card\n- Land records", {"module": "yojana", "scheme": "pm-kisan", "section": "Documents"}),
        Chunk("chana#4", "Chana — Irrigation", "- Avoid irrigation at full flowering.\n- One light irrigation at branching.",
              {"module": "crop", "crop": "chana", "stage": ["vegetative", "flowering"], "section": "Irrigation", "source_name": "NCIPM"}),
        Chunk("paddy#4", "Paddy — BPH", "- Spray imidacloprid 30.5% SC at 60-75 ml per hectare.",
              {"module": "crop", "crop": "paddy", "stage": ["vegetative"], "section": "BPH"}),
    ]
    return VectorIndex(chunks, np.array([[1, 0, 0], [1, 0, 0], [0.9, 0.1, 0]], dtype=float))


def pipeline(g, **kw):
    return Pipeline(g, index=make_index(), schemes=SCHEMES, **kw)


# ---------- prompts ----------

def test_system_prompt_language_rules():
    assert "Devanagari" in system_prompt("hi-IN")
    assert "Roman" in system_prompt("hi-en")
    assert "Never invent" in system_prompt("hi-IN")


def test_rag_prompt_contains_context_and_only_rule():
    hits = [Hit(make_index().chunks[0], 0.9)]
    p = rag_prompt("hi-en", hits)
    assert "Answer ONLY from the CONTEXT" in p and "NO_ANSWER" in p and "Aadhaar card" in p


def test_rag_prompt_has_today_for_seasonal_questions():
    from datetime import datetime
    from app.pipeline import IST
    p = rag_prompt("hi-IN", [Hit(make_index().chunks[0], 0.9)], now=datetime(2026, 11, 9, 10, tzinfo=IST))
    assert "Today is 9 November 2026" in p and "current month" in p


def test_maize_names_and_router_schema():
    from app.pipeline import CROP_NAMES, CROPS, ROUTER_SCHEMA, ROUTER_SYSTEM
    assert "maize" in CROPS and CROP_NAMES["maize"]["hi-IN"] == "मक्का"
    assert "maize" in ROUTER_SCHEMA["properties"]["crop"]["enum"] and "makka" in ROUTER_SYSTEM


# ---------- routing ----------

@pytest.mark.parametrize("text,intent,crop", [
    ("Bemetara mein barish hogi?", "mausam", None),
    ("PM-Kisan ki kist kab aayegi", "yojana", None),
    ("Chana mein khaad kitna daalein", "crop", "chana"),
    ("मुझे कौन सी योजना मिलेगी", "eligibility", None),
    ("namaste", "general", None),
    ("क्या आप मुझे मक्के की खेती के बारे में बता सकते हैं?", "crop", "maize"),
    ("Makka kab boyein?", "crop", "maize"),
    ("अभी मुझे कौन सा फसल लगाना है?", "crop", None),
])
def test_keyword_route(text, intent, crop):
    kw = [k for s in SCHEMES for k in s["keywords"]]
    r = keyword_route(text, kw)
    assert r["intent"] == intent and r["crop"] == crop


async def test_router_failure_falls_back_to_keywords():
    g = FakeGemini(route_error=True, answers=["PM-Kisan mein Aadhaar chahiye."])
    r = await pipeline(g).handle("PM-Kisan ke documents?", "hi-en", "s")
    assert r.intent == "yojana"


async def test_router_bad_values_are_cleaned():
    g = FakeGemini(route={"intent": "crop", "crop": "banana", "stage": "weird", "search_query": "q"})
    route = await pipeline(g).route("x", [])
    assert route["crop"] is None and route["stage"] is None


# ---------- yojana RAG ----------

async def test_yojana_answer_from_kb_with_card():
    g = FakeGemini(route={"intent": "yojana", "search_query": "PM-Kisan documents"}, answers=["Aadhaar aur zameen ke kagaz chahiye."])
    r = await pipeline(g).handle("PM-Kisan ke liye kaun se documents chahiye?", "hi-en", "s")
    assert r.intent == "yojana" and r.reply_text.startswith("Aadhaar")
    assert [c["type"] for c in r.cards] == ["scheme"] and r.cards[0]["data"]["id"] == "pm-kisan"
    assert "Answer ONLY from the CONTEXT" in g.calls["generate"][0]["system"]
    assert g.calls["embed"][0][0].startswith("task: question answering | query:")
    assert g.calls["search"] == []


async def test_low_score_goes_to_web_fallback():
    g = FakeGemini(route={"intent": "yojana", "search_query": "tractor subsidy"})
    g.qvec = [0, 0, 1]  # orthogonal -> score 0
    r = await pipeline(g).handle("Tractor subsidy?", "hi-en", "s")
    assert g.calls["search"] == ["tractor subsidy"]
    assert r.reply_text.startswith("Web jaankari ke anusaar")
    assert r.cards[0]["type"] == "web_sources" and r.sources[0]["url"] == "https://gov.in"
    assert g.calls["generate"] == []  # no KB answer attempted


async def test_high_score_does_not_search():
    g = FakeGemini(route={"intent": "yojana", "search_query": "q"}, answers=["kb answer"])
    await pipeline(g).handle("q", "hi-en", "s")
    assert g.calls["search"] == []


async def test_no_answer_from_context_triggers_web():
    g = FakeGemini(route={"intent": "yojana", "search_query": "q"}, answers=["NO_ANSWER"])
    r = await pipeline(g).handle("q", "hi-IN", "s")
    assert g.calls["search"] and r.reply_text.startswith("वेब जानकारी के अनुसार")


async def test_web_failure_gives_labelled_general_answer():
    g = FakeGemini(route={"intent": "yojana", "search_query": "q"}, search=GeminiError("429"),
                   answers=["Rabi mein chana aur gehun achhe vikalp hain. KVK se pushti karein."])
    g.qvec = [0, 0, 1]
    r = await pipeline(g).handle("q", "hi-en", "s")
    assert r.reply_text.startswith(GENERAL_LABEL["hi-en"] + ":")
    assert r.cards == [{"type": "general", "data": {"label": GENERAL_LABEL["hi-en"]}}]
    assert "Do NOT give pesticide" in g.calls["generate"][-1]["system"]


async def test_general_answer_with_dose_is_replaced_by_kvk_referral():
    for bad in ["Urea 50 kg prati acre daalein.", "Imidacloprid 60 ml per hectare spray karein.", "Isme ₹6,000 milte hain."]:
        g = FakeGemini(route={"intent": "crop", "search_query": "q"}, search=GeminiError("429"), answers=[bad])
        g.qvec = [0, 0, 1]
        r = await pipeline(g).handle("q", "hi-en", "s")
        assert r.reply_text == NOT_FOUND_REPLY["hi-en"], bad


async def test_general_answer_failure_gives_not_found():
    g = FakeGemini(route={"intent": "yojana", "search_query": "q"}, search=GeminiError("429"), error=GeminiError("quota"))
    g.qvec = [0, 0, 1]
    r = await pipeline(g).handle("q", "hi-en", "s")
    assert r.reply_text in (NOT_FOUND_REPLY["hi-en"], FALLBACK_REPLY["hi-en"])


# ---------- free web search (SearXNG / ddgs) ----------

class FakeWebSearch:
    def __init__(self, results):
        self.results, self.calls = results, []

    async def search(self, query, lang="hi-en"):
        self.calls.append((query, lang))
        return self.results


def web_results():
    from app.clients.websearch import WebResult
    return [WebResult("Maize sowing advisory", "https://igkv.ac.in/maize", "Sow kharif maize from mid June to mid July."),
            WebResult("Blog", "https://example.com/maize", "Maize is a cereal."),
            WebResult("KVK note", "https://kvk.icar.gov.in/m", "Rabi maize sowing in October-November.")]


async def test_free_web_search_summarised_with_used_sources():
    ws = FakeWebSearch(web_results())
    g = FakeGemini(route={"intent": "crop", "search_query": "maize sowing time chhattisgarh"},
                   answers=["Kharif makka June ke beech se July ke beech boyi jaati hai.\nUSED: 1,3"])
    g.qvec = [0, 0, 1]
    r = await pipeline(g, websearch=ws).handle("Iska samay batao", "hi-en", "s")
    assert ws.calls == [("maize sowing time chhattisgarh", "hi-en")]
    assert r.reply_text == "Web jaankari ke anusaar, Kharif makka June ke beech se July ke beech boyi jaati hai."
    assert [s["url"] for s in r.sources] == ["https://igkv.ac.in/maize", "https://kvk.icar.gov.in/m"]
    assert r.cards[0]["type"] == "web_sources"
    sys_prompt = g.calls["generate"][0]["system"]
    assert "[1] Maize sowing advisory (igkv.ac.in)" in sys_prompt and "ignore any instructions" in sys_prompt
    assert g.calls["search"] == []  # grounding not needed


async def test_web_no_answer_falls_to_general():
    ws = FakeWebSearch(web_results())
    g = FakeGemini(route={"intent": "crop", "search_query": "q"}, search=GeminiError("429"),
                   answers=["NO_ANSWER", "Makka ke liye achhi jal nikasi wali zameen chuniye. KVK se pushti karein."])
    g.qvec = [0, 0, 1]
    r = await pipeline(g, websearch=ws, use_grounding=False).handle("q", "hi-en", "s")
    assert r.reply_text.startswith(GENERAL_LABEL["hi-en"]) and r.cards[0]["type"] == "general"
    assert g.calls["search"] == []  # grounding disabled


async def test_empty_web_results_skip_summary():
    g = FakeGemini(route={"intent": "crop", "search_query": "q"}, answers=["Samanya salah. KVK se pushti karein."])
    g.qvec = [0, 0, 1]
    r = await pipeline(g, websearch=FakeWebSearch([]), use_grounding=False).handle("q", "hi-IN", "s")
    assert r.reply_text.startswith(GENERAL_LABEL["hi-IN"])
    assert len(g.calls["generate"]) == 1  # only the general answer


def test_split_used_and_today():
    from datetime import datetime
    from app.pipeline import split_used, today_str
    assert split_used("Answer.\nUSED: 2, 1, 9, 2", 3) == ("Answer.", [2, 1])
    assert split_used("Answer only", 3) == ("Answer only", [])
    assert today_str(datetime(2026, 11, 9, 12, tzinfo=__import__("app.pipeline", fromlist=["IST"]).IST)) == "9 November 2026 (नवंबर)"


# ---------- eligibility ----------

async def test_eligibility_extracts_merges_and_cards():
    g = FakeGemini(route={"intent": "eligibility", "search_query": "x"},
                   profile={"owns_land": True, "land_acres": 2, "income_tax_payer": False, "has_kcc": False, "junk": 1})
    p = pipeline(g)
    r = await p.handle("Main 2 acre ka kisan hoon, income tax nahi bharta, KCC nahi hai", "hi-en", "s1")
    card = r.cards[0]
    assert card["type"] == "eligibility" and card["data"]["profile"]["land_acres"] == 2
    statuses = {x["id"]: x["status"] for x in card["data"]["results"]}
    assert statuses["kcc"] == "eligible" and statuses["pm-kisan"] == "needs_check"
    # second turn adds info; first-turn facts are kept
    g.profile = {"govt_employee_or_pensioner_10k": False}
    r = await p.handle("sarkari naukri nahi hai", "hi-en", "s1")
    statuses = {x["id"]: x["status"] for x in r.cards[0]["data"]["results"]}
    assert statuses["pm-kisan"] == "eligible"


# ---------- crop ----------

async def test_crop_filters_by_crop_and_adds_card_and_reminder():
    g = FakeGemini(route={"intent": "crop", "crop": "chana", "stage": "flowering", "search_query": "chana irrigation flowering"},
                   answers=["Poore phool par sinchai na karein."])
    r = await pipeline(g).handle("Chana mein phool aa rahe hain, sinchai karun?", "hi-en", "s")
    assert r.intent == "crop" and KVK_REMINDER["hi-en"] in r.reply_text
    card = r.cards[0]
    assert card["type"] == "crop" and card["data"]["crop"].startswith("Chana")
    assert card["data"]["actions"][0].startswith("Avoid irrigation")
    assert "Chana — Irrigation" in g.calls["generate"][0]["system"]
    assert "Paddy — BPH" not in g.calls["generate"][0]["system"]  # other crop filtered out


async def test_crop_dose_comes_from_retrieved_text():
    g = FakeGemini(route={"intent": "crop", "crop": "paddy", "search_query": "bph"},
                   answers=["Imidacloprid 30.5% SC 60-75 ml per hectare spray karein. KVK se pushti karein."])
    r = await pipeline(g).handle("bhura maahu", "hi-en", "s")
    ctx = g.calls["generate"][0]["system"]
    assert "60-75 ml" in ctx and "60-75 ml" in r.reply_text


# ---------- crop card localization ----------

BPH_ROUTE = {"intent": "crop", "crop": "paddy", "search_query": "bph"}


async def test_crop_card_translated_to_hindi_keeping_doses():
    def to_hindi(lines):
        return ["भूरा माहू" if i == 0 else "इमिडाक्लोप्रिड 30.5% SC 60-75 ml प्रति हेक्टेयर छिड़कें।" for i, _ in enumerate(lines)]
    g = FakeGemini(route=BPH_ROUTE, answers=["जवाब। KVK से पुष्टि करें।"], l10n=to_hindi)
    p = pipeline(g)
    r = await p.handle("भूरा माहू", "hi-IN", "s")
    d = r.cards[0]["data"]
    assert d["title"] == "भूरा माहू" and d["actions"][0].startswith("इमिडाक्लोप्रिड") and "60-75 ml" in d["actions"][0]
    assert d["crop"] == "धान"
    l10n_calls = [c for c in g.calls["json"] if "Translate each line" in c["system"]]
    assert "Devanagari" in l10n_calls[0]["system"] and "60-75 ml" in l10n_calls[0]["user"]
    # cached: a second identical question does not translate again
    g.answers = ["जवाब। KVK"]
    await p.handle("भूरा माहू", "hi-IN", "s")
    assert len([c for c in g.calls["json"] if "Translate each line" in c["system"]]) == 1


async def test_crop_card_keeps_english_when_translation_fails_or_changes_numbers():
    for bad in (GeminiError("429"), lambda lines: ["x"] * (len(lines) + 1), lambda lines: ["इमिडाक्लोप्रिड 50 ml"] * len(lines)):
        g = FakeGemini(route=BPH_ROUTE, answers=["ok KVK"], l10n=bad)
        r = await pipeline(g).handle("bph", "hi-en", "s")
        assert r.cards[0]["data"]["actions"][0].startswith("Spray imidacloprid 30.5% SC at 60-75 ml")


async def test_crop_card_hinglish_prompt_asks_roman_script():
    g = FakeGemini(route=BPH_ROUTE, answers=["ok KVK"], l10n=lambda lines: lines)
    await pipeline(g).handle("bph", "hi-en", "s")
    sys_prompt = next(c["system"] for c in g.calls["json"] if "Translate each line" in c["system"])
    assert "Roman" in sys_prompt


# ---------- general / errors / history ----------

async def test_general_and_history_capped():
    g = FakeGemini(answers=[f"a{i}" for i in range(10)])
    p = pipeline(g, sessions=SessionStore(turns=2))
    for i in range(4):
        await p.handle(f"q{i}", "hi-en", "s1")
    hist = g.calls["generate"][-1]["history"]
    assert [m.text for m in hist] == ["q1", "a1", "q2", "a2"]


async def test_gemini_failure_gives_fallback_in_language():
    g = FakeGemini(error=GeminiError("503"))
    p = pipeline(g)
    r = await p.handle("धान", "hi-IN", "s1")
    assert r.reply_text == FALLBACK_REPLY["hi-IN"]
    assert p.sessions.get("s1") == []


async def test_mausam_routed_to_module():
    class FakeMausam:
        def __init__(self):
            self.calls = []

        async def handle(self, text, route, lang, history, profile):
            from app.pipeline import PipelineResult
            self.calls.append(route)
            return PipelineResult("mausam reply", cards=[{"type": "weather", "data": {}}])

    m = FakeMausam()
    g = FakeGemini(route={"intent": "mausam", "crop": "wheat", "district": "Bemetara", "search_query": "x"})
    r = await pipeline(g, mausam=m).handle("Bemetara mein barish?", "hi-en", "s")
    assert r.intent == "mausam" and m.calls[0]["district"] == "Bemetara" and r.cards[0]["type"] == "weather"


def test_session_store_evicts_oldest():
    s = SessionStore(max_sessions=2)
    for sid in "abc":
        s.add(sid, "u", "m")
    assert s.get("a") == [] and len(s.get("c")) == 2


def test_extract_sources_dedupes():
    class O:
        def __init__(self, **kw):
            self.__dict__.update(kw)
    resp = O(candidates=[O(grounding_metadata=O(grounding_chunks=[
        O(web=O(uri="https://a.gov.in/x", title="A", domain="a.gov.in")),
        O(web=O(uri="https://a.gov.in/x", title="A", domain="a.gov.in")),
        O(web=O(uri="https://b.in", title=None, domain="b.in")),
        O(web=None),
    ]))])
    assert extract_sources(resp) == [
        {"title": "A", "url": "https://a.gov.in/x"},
        {"title": "b.in", "url": "https://b.in"},
    ]
    assert extract_sources(O(candidates=None)) == []


def test_prefer_current_month_moves_calendar_chunk_first():
    from datetime import datetime
    from app.pipeline import IST, prefer_current_month
    junjul = Chunk("calendar#1", "June – July", "x", {"crop": "all", "month": "June, July"})
    nov = Chunk("calendar#4", "November", "x", {"crop": "all", "month": "November"})
    wheat = Chunk("wheat#1", "Wheat sowing", "x", {"crop": "wheat"})
    hits = [Hit(junjul, 0.8), Hit(wheat, 0.78), Hit(nov, 0.75)]
    out = prefer_current_month(hits, now=datetime(2026, 11, 9, tzinfo=IST))
    assert [h.chunk.id for h in out] == ["calendar#4", "calendar#1", "wheat#1"]
    assert prefer_current_month(hits, now=datetime(2026, 8, 1, tzinfo=IST)) == hits


# ---------- router fast path ----------

def test_keyword_fast_path_for_demo_questions():
    kw = [k for s in SCHEMES for k in s["keywords"]]
    cases = [
        ("Main 2 acre ka kisan hoon, income tax nahi bharta, KCC nahi hai", "eligibility", None, None, None),
        ("मैं 2 एकड़ का किसान हूँ, इनकम टैक्स नहीं भरता, KCC नहीं है", "eligibility", None, None, None),
        ("धान बेचने के लिए टोकन कैसे मिलेगा?", "yojana", "paddy", None, None),
        ("चने में फूल आ रहे हैं, सिंचाई करूँ?", "crop", "chana", "flowering", None),
        ("बेमेतरा में अगले हफ्ते बारिश होगी? गेहूँ में पानी दूँ?", "mausam", "wheat", None, "Bemetara"),
        ("Makka mein fall armyworm lag gaya hai kya karun?", "crop", "maize", None, None),
    ]
    for text, intent, crop, stage, district in cases:
        r = keyword_route(text, kw)
        assert r["confident"], text
        assert (r["intent"], r["crop"], r["stage"], r["district"]) == (intent, crop, stage, district), text


def test_ambiguous_questions_go_to_gemini():
    kw = [k for s in SCHEMES for k in s["keywords"]]
    for text in ["Tractor par subsidy kaise milegi?", "Fasal bima mein barish se nuksaan ka paisa kaise milega?", "Aaj cricket match kaun jeeta?"]:
        assert not keyword_route(text, kw)["confident"], text


async def test_fast_path_skips_router_call():
    g = FakeGemini(route={"intent": "general", "search_query": "x"}, answers=["Chana: KVK"])
    await pipeline(g).handle("Chana mein phool aa rahe hain, sinchai karun?", "hi-en", "s")
    assert not any("Classify" in c["system"] for c in g.calls["json"])


async def test_follow_up_uses_gemini_router():
    g = FakeGemini(route={"intent": "crop", "crop": "chana", "search_query": "chana irrigation"}, answers=["a KVK", "b KVK"])
    p = pipeline(g)
    await p.handle("Chana mein phool aa rahe hain, sinchai karun?", "hi-en", "s")
    await p.handle("Aur uske liye khaad kitna?", "hi-en", "s")
    assert sum("Classify" in c["system"] for c in g.calls["json"]) == 1


async def test_slow_router_falls_back_to_keywords(monkeypatch):
    import asyncio
    import app.pipeline as pl
    monkeypatch.setattr(pl, "ROUTER_BUDGET_S", 0.05)

    class SlowRouter(FakeGemini):
        async def generate_json(self, system, user_text, schema, **kw):
            if "intent" in schema.get("properties", {}):
                await asyncio.sleep(2)
            return await super().generate_json(system, user_text, schema, **kw)

    g = SlowRouter(answers=["ok"])
    route = await pipeline(g).route("Tractor par subsidy kaise milegi?", [])
    assert route["intent"] == "general" and "confident" not in route

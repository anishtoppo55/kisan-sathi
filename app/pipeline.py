"""Conversation pipeline shared by /api/chat and /api/voice.

Flow: route (Gemini flash-lite JSON, keyword fallback) -> module handler -> PipelineResult.
  yojana       RAG over kb/schemes (+ scheme card); web fallback when the KB has no answer
  eligibility  profile extraction + rule engine (+ eligibility card)
  crop         RAG over kb/crops filtered by crop/stage (+ crop card); web fallback
  mausam       weather forecast + advisory rules (+ weather card)
  general      plain Gemini answer
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import OrderedDict, deque
from dataclasses import dataclass, field

from app.clients.gemini import GeminiError, Message
from app.modules import yojana
from app.modules.yojana import FarmerProfile
from app.rag.store import Hit, format_context, query_text

log = logging.getLogger(__name__)

HISTORY_TURNS = 6
MAX_SESSIONS = 500
KB_SCORE_THRESHOLD = 0.70  # tuned on 10 questions: KB answers scored >= 0.75, off-KB <= 0.69
NO_ANSWER = "NO_ANSWER"

LANG_INSTRUCTION = {
    "hi-IN": "Reply in simple Hindi written in Devanagari script.",
    "hi-en": "Reply in simple Hinglish (Hindi words written in Latin/Roman script, common English farm words are fine).",
    "en-IN": "Reply in simple Indian English.",
}

BASE_SYSTEM = """You are Kisan Saathi, a friendly voice assistant for farmers in Chhattisgarh, India.
You help with government schemes (yojana), crop care (fasal) and weather-based farm advice (mausam).
Rules:
- {lang_rule}
- Your answer is read aloud. Keep it to 2-4 short sentences. No markdown, bullet points, tables, emojis or URLs.
- Never invent scheme amounts, dates, prices, pesticide names or doses. If you are not sure, say so and suggest the local KVK, RAEO or krishi vibhag office.
- If the question is not about farming, schemes or weather, politely say what you can help with."""

RAG_RULES = """
Answer ONLY from the CONTEXT below. Use its numbers, names and doses exactly; do not add facts from memory.
If the CONTEXT does not contain the answer, reply with exactly: NO_ANSWER

CONTEXT:
{context}"""

WEB_SYSTEM = """You help farmers in Chhattisgarh, India. Search the web and answer the question.
- {lang_rule}
- 2-4 short sentences that sound natural when read aloud. No markdown, lists or URLs.
- Prefer official government sources. Mention the year or season the information is for.
- If you cannot find reliable information, say so."""

FALLBACK_REPLY = {
    "hi-IN": "माफ़ कीजिए, अभी जवाब नहीं मिल पाया। कृपया थोड़ी देर बाद फिर पूछिए।",
    "hi-en": "Maaf kijiye, abhi jawab nahi mil paaya. Kripya thodi der baad phir poochhiye.",
    "en-IN": "Sorry, I couldn't get an answer right now. Please try again in a moment.",
}
NOT_FOUND_REPLY = {
    "hi-IN": "इस सवाल की पक्की जानकारी अभी मेरे पास नहीं है। कृपया अपने नज़दीकी कृषि विज्ञान केंद्र या RAEO से पूछें।",
    "hi-en": "Is sawal ki pakki jaankari abhi mere paas nahi hai. Kripya apne nazdeeki KVK ya RAEO se poochhein.",
    "en-IN": "I don't have reliable information on this yet. Please ask your nearest KVK or RAEO.",
}
WEB_PREFIX = {"hi-IN": "वेब जानकारी के अनुसार, ", "hi-en": "Web jaankari ke anusaar, ", "en-IN": "According to web sources, "}
KVK_REMINDER = {
    "hi-IN": "दवा या खाद डालने से पहले अपने नज़दीकी KVK या RAEO से पुष्टि करें।",
    "hi-en": "Dawa ya khaad daalne se pehle apne nazdeeki KVK ya RAEO se pushti karein.",
    "en-IN": "Confirm with your nearest KVK or RAEO before applying any pesticide or fertilizer.",
}

CROPS = ["paddy", "chana", "wheat", "teora"]
STAGES = ["sowing", "vegetative", "flowering", "maturity", "post-harvest"]
CROP_NAMES = {
    "paddy": {"hi-IN": "धान", "hi-en": "Dhaan (Paddy)"},
    "chana": {"hi-IN": "चना", "hi-en": "Chana (Gram)"},
    "wheat": {"hi-IN": "गेहूँ", "hi-en": "Gehun (Wheat)"},
    "teora": {"hi-IN": "तिवड़ा (लाखड़ी)", "hi-en": "Teora / Lakhdi (Lathyrus)"},
}
STAGE_NAMES = {
    "sowing": {"hi-IN": "बुवाई", "hi-en": "Buvai (sowing)"},
    "vegetative": {"hi-IN": "बढ़वार", "hi-en": "Badhvaar (vegetative)"},
    "flowering": {"hi-IN": "फूल आना", "hi-en": "Phool (flowering)"},
    "maturity": {"hi-IN": "पकना / दाना भरना", "hi-en": "Pakna / dana bharna (maturity)"},
    "post-harvest": {"hi-IN": "कटाई के बाद", "hi-en": "Katai ke baad (post-harvest)"},
}

ROUTER_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["yojana", "eligibility", "crop", "mausam", "general"]},
        "crop": {"type": ["string", "null"], "enum": CROPS + ["other", None]},
        "stage": {"type": ["string", "null"], "enum": STAGES + [None]},
        "district": {"type": ["string", "null"]},
        "search_query": {"type": "string", "description": "The question rewritten as a short standalone search query, resolving references to earlier turns"},
    },
    "required": ["intent", "search_query"],
}

ROUTER_SYSTEM = """Classify a Chhattisgarh farmer's message for a farm assistant. Return JSON.
intent:
- "eligibility": the farmer asks which schemes they qualify for, or describes themselves (land, income tax, KCC, job, pension) to check eligibility.
- "yojana": a question about a specific government scheme, subsidy, paddy procurement (dhaan kharidi, token, samarthan mulya/MSP), insurance, loans, KCC, soil health card.
- "crop": crop cultivation, varieties, sowing, fertilizer, pests, diseases, irrigation, harvest.
- "mausam": weather, rain, temperature, frost, or whether to irrigate/spray given the weather.
- "general": greetings or anything else.
crop: paddy (dhaan, chawal, dhan), chana (gram), wheat (gehun), teora (tiwda, lakhdi, lathyrus, khesari), other, or null.
stage: sowing, vegetative, flowering, maturity, post-harvest, or null if not stated.
district: Chhattisgarh district in English if mentioned, else null.
search_query: the question as a standalone query; use the conversation to resolve "it", "uska", "us yojana" etc."""

KEYWORDS = {
    "mausam": ["mausam", "मौसम", "barish", "baarish", "बारिश", "वर्षा", "rain", "weather", "temperature", "tapman", "तापमान", "pala", "paala", "पाला", "thand", "ठंड", "garmi", "गर्मी", "aandhi"],
    "eligibility": ["paatra", "patra", "पात्र", "eligible", "kaun si yojana", "कौन सी योजना", "kaunsi yojana", "konsi yojana", "mujhe kaun", "मुझे कौन"],
    "crop": ["fasal", "फसल", "khaad", "खाद", "keet", "कीट", "rog", "रोग", "beej", "बीज", "sinchai", "सिंचाई", "dawa", "दवा", "buvai", "बुवाई", "urea", "यूरिया", "dap", "किस्म", "variety"],
}
CROP_KEYWORDS = {
    "paddy": ["dhaan", "dhan", "धान", "paddy", "rice", "chawal", "चावल"],
    "chana": ["chana", "चना", "gram"],
    "wheat": ["gehun", "gehu", "गेहूं", "गेहूँ", "wheat"],
    "teora": ["teora", "tiwda", "tivda", "तिवड़ा", "lakhdi", "लाखड़ी", "khesari", "lathyrus", "utera", "उतेरा"],
}


def lang_rule(lang: str) -> str:
    return LANG_INSTRUCTION.get(lang, LANG_INSTRUCTION["hi-IN"])


def system_prompt(lang: str) -> str:
    return BASE_SYSTEM.format(lang_rule=lang_rule(lang))


def rag_prompt(lang: str, hits: list[Hit]) -> str:
    return system_prompt(lang) + RAG_RULES.format(context=format_context(hits))


def _l(d: dict, lang: str) -> str:
    return d.get(lang) or d.get("hi-en") or next(iter(d.values()))


def keyword_route(text: str, scheme_keywords: list[str]) -> dict:
    low = text.lower()
    crop = next((c for c, ws in CROP_KEYWORDS.items() if any(w in low for w in ws)), None)
    if any(w in low for w in KEYWORDS["mausam"]):
        intent = "mausam"
    elif any(w in low for w in KEYWORDS["eligibility"]):
        intent = "eligibility"
    elif any(w in low for w in scheme_keywords):
        intent = "yojana"
    elif crop or any(w in low for w in KEYWORDS["crop"]):
        intent = "crop"
    else:
        intent = "general"
    return {"intent": intent, "crop": crop, "stage": None, "district": None, "search_query": text}


@dataclass
class PipelineResult:
    reply_text: str
    cards: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    intent: str = "general"


class SessionStore:
    """In-memory last-N-turns history and farmer profile per session, LRU-evicted."""

    def __init__(self, turns: int = HISTORY_TURNS, max_sessions: int = MAX_SESSIONS):
        self._data: OrderedDict[str, dict] = OrderedDict()
        self.turns, self.max_sessions = turns, max_sessions

    def _get(self, sid: str) -> dict:
        s = self._data.get(sid)
        if s is None:
            s = self._data[sid] = {"history": deque(maxlen=self.turns * 2), "profile": FarmerProfile()}
            while len(self._data) > self.max_sessions:
                self._data.popitem(last=False)
        self._data.move_to_end(sid)
        return s

    def get(self, sid: str | None) -> list[Message]:
        if not sid or sid not in self._data:
            return []
        return list(self._get(sid)["history"])

    def add(self, sid: str | None, user: str, model: str) -> None:
        if not sid:
            return
        h = self._get(sid)["history"]
        h.append(Message("user", user))
        h.append(Message("model", model))

    def profile(self, sid: str | None) -> FarmerProfile:
        return self._get(sid)["profile"] if sid else FarmerProfile()

    def set_profile(self, sid: str | None, p: FarmerProfile) -> None:
        if sid:
            self._get(sid)["profile"] = p


class Pipeline:
    def __init__(self, gemini, index=None, schemes: list[dict] | None = None, sessions: SessionStore | None = None,
                 mausam=None, threshold: float = KB_SCORE_THRESHOLD):
        self.gemini = gemini
        self.index = index
        self.schemes = schemes if schemes is not None else []
        self.sessions = sessions or SessionStore()
        self.mausam = mausam  # MausamSaathi or None
        self.threshold = threshold
        self._scheme_kw = [k for s in self.schemes for k in s.get("keywords", [])]
        self._l10n_cache: dict[tuple[str, str], list[str]] = {}

    # ---------- routing ----------
    async def route(self, text: str, history: list[Message]) -> dict:
        convo = "\n".join(f"{m.role}: {m.text}" for m in history[-4:])
        prompt = (f"Conversation so far:\n{convo}\n\n" if convo else "") + f"Farmer's new message: {text}"
        try:
            r = await self.gemini.generate_json(ROUTER_SYSTEM, prompt, ROUTER_SCHEMA)
        except GeminiError as e:
            log.warning("router fell back to keywords: %s", e)
            return keyword_route(text, self._scheme_kw)
        r.setdefault("search_query", text)
        if r.get("intent") not in ("yojana", "eligibility", "crop", "mausam", "general"):
            r["intent"] = keyword_route(text, self._scheme_kw)["intent"]
        if r.get("crop") not in CROPS:
            r["crop"] = None
        if r.get("stage") not in STAGES:
            r["stage"] = None
        return r

    # ---------- entry point ----------
    async def handle(self, text: str, lang: str, session_id: str | None = None) -> PipelineResult:
        history = self.sessions.get(session_id)
        route = await self.route(text, history)
        intent = route["intent"]
        log.info("route intent=%s crop=%s stage=%s district=%s", intent, route.get("crop"), route.get("stage"), route.get("district"))
        try:
            if intent == "eligibility":
                result = await self.eligibility(text, lang, session_id)
            elif intent == "yojana":
                result = await self.yojana_qa(text, route, lang, history)
            elif intent == "crop":
                result = await self.crop_qa(text, route, lang, history)
            elif intent == "mausam" and self.mausam is not None:
                result = await self.mausam.handle(text, route, lang, history, self.sessions.profile(session_id))
            else:
                result = PipelineResult(await self.gemini.generate(system_prompt(lang), text, history))
        except GeminiError as e:
            log.error("pipeline %s failed: %s", intent, e)
            return PipelineResult(reply_text=_l(FALLBACK_REPLY, lang), intent=intent)
        result.intent = intent
        self.sessions.add(session_id, text, result.reply_text)
        return result

    # ---------- RAG ----------
    async def retrieve(self, query: str, require: dict, filters: dict | None = None, k: int = 4) -> list[Hit]:
        if self.index is None:
            return []
        [qvec] = await self.gemini.embed([query_text(query)])
        return self.index.search(qvec, k=k, filters=filters, require=require)

    async def answer_from_kb(self, text: str, hits: list[Hit], lang: str, history: list[Message]) -> str | None:
        """Grounded answer, or None when retrieval is weak or the model says the context lacks the answer."""
        if not hits or hits[0].score < self.threshold:
            log.info("KB miss (top score %.3f)", hits[0].score if hits else -1)
            return None
        reply = await self.gemini.generate(rag_prompt(lang, hits), text, history, temperature=0.1)
        if NO_ANSWER in reply:
            log.info("KB context lacked the answer")
            return None
        return reply

    async def web_answer(self, query: str, lang: str) -> PipelineResult:
        try:
            res = await self.gemini.search(WEB_SYSTEM.format(lang_rule=lang_rule(lang)), query)
        except GeminiError as e:
            log.warning("web fallback failed: %s", e)
            return PipelineResult(_l(NOT_FOUND_REPLY, lang))
        log.info("web fallback used for %r (%d sources)", query[:80], len(res.sources))
        if not res.text:
            return PipelineResult(_l(NOT_FOUND_REPLY, lang))
        sources = res.sources[:5]
        cards = [{"type": "web_sources", "data": {"title": "Web se (web sources)", "sources": sources}}] if sources else []
        return PipelineResult(_l(WEB_PREFIX, lang) + res.text, cards=cards, sources=sources)

    # ---------- Yojana ----------
    async def yojana_qa(self, text: str, route: dict, lang: str, history: list[Message]) -> PipelineResult:
        query = route.get("search_query") or text
        hits = await self.retrieve(query, require={"module": "yojana"})
        reply = await self.answer_from_kb(text, hits, lang, history)
        if reply is None:
            return await self.web_answer(query, lang)
        matched = yojana.match_schemes(text + " " + query, self.schemes, [h.chunk.meta.get("scheme") for h in hits])
        return PipelineResult(reply, cards=[yojana.scheme_card(s, lang) for s in matched])

    async def extract_profile(self, text: str) -> FarmerProfile:
        try:
            data = await self.gemini.generate_json(yojana.EXTRACT_SYSTEM, text, yojana.PROFILE_SCHEMA)
            return FarmerProfile(**{k: v for k, v in data.items() if k in FarmerProfile.model_fields})
        except (GeminiError, ValueError) as e:
            log.warning("profile extraction failed: %s", e)
            return FarmerProfile()

    async def eligibility(self, text: str, lang: str, session_id: str | None) -> PipelineResult:
        extracted = await self.extract_profile(text)
        profile = self.sessions.profile(session_id).merged(extracted)
        self.sessions.set_profile(session_id, profile)
        return self.eligibility_result(profile, lang)

    def eligibility_result(self, profile: FarmerProfile, lang: str) -> PipelineResult:
        results = yojana.check_all(self.schemes, profile, lang)
        return PipelineResult(yojana.summarize(results, lang), cards=[yojana.eligibility_card(results, profile)], intent="eligibility")

    # ---------- Crop ----------
    async def crop_qa(self, text: str, route: dict, lang: str, history: list[Message]) -> PipelineResult:
        query = route.get("search_query") or text
        crop, stage = route.get("crop"), route.get("stage")
        require = {"module": "crop"}
        if crop:
            require["crop"] = crop
        hits = await self.retrieve(query, require=require, filters={"stage": stage} if stage else None)
        if not hits or hits[0].score < self.threshold:
            log.info("KB miss (top score %.3f)", hits[0].score if hits else -1)
            return await self.web_answer(query, lang)
        reminder = _l(KVK_REMINDER, lang)
        card = crop_card(hits, crop, stage, lang, reminder)
        # Answer and card translation run in parallel: the card text comes from the English KB.
        reply, localized = await asyncio.gather(
            self.answer_from_kb(text, hits, lang, history),
            self.localize_lines([card["data"]["title"], *card["data"]["actions"]], lang, hits[0].chunk.id),
        )
        if reply is None:
            return await self.web_answer(query, lang)
        if localized:
            card["data"]["title"], card["data"]["actions"] = localized[0], localized[1:]
        if "KVK" not in reply:
            reply = f"{reply} {reminder}"
        return PipelineResult(reply, cards=[card])

    async def localize_lines(self, lines: list[str], lang: str, cache_id: str) -> list[str] | None:
        """Translate KB lines (English) into the reply language for the card. Chemical names,
        numbers and units are kept exactly; any failure or mismatch keeps the English text."""
        if lang not in ("hi-IN", "hi-en") or not lines:
            return None
        key = (cache_id, lang)
        if key in self._l10n_cache:
            return self._l10n_cache[key]
        script = "simple Hindi in Devanagari script" if lang == "hi-IN" else "simple Hinglish (Hindi in Roman/Latin script)"
        system = (f"Translate each line for Chhattisgarh farmers into {script}. Return JSON {{\"lines\": [...]}} with exactly "
                  f"{len(lines)} strings in the same order. Keep pesticide/chemical/variety names, numbers, units, "
                  "percentages and abbreviations (ETL, KVK, BPH, SC, EC, WP, ml, kg, ha) exactly as written. Do not add or drop facts.")
        try:
            data = await self.gemini.generate_json(system, json.dumps(lines, ensure_ascii=False), L10N_SCHEMA)
        except GeminiError as e:
            log.warning("card localization failed: %s", e)
            return None
        out = data.get("lines")
        if not (isinstance(out, list) and len(out) == len(lines) and all(isinstance(x, str) and x.strip() for x in out)):
            log.warning("card localization returned %r lines for %d", len(out) if isinstance(out, list) else out, len(lines))
            return None
        if not _numbers_preserved(lines, out):
            log.warning("card localization changed numbers; keeping English")
            return None
        if len(self._l10n_cache) > 512:
            self._l10n_cache.clear()
        self._l10n_cache[key] = out
        return out


L10N_SCHEMA = {
    "type": "object",
    "properties": {"lines": {"type": "array", "items": {"type": "string"}}},
    "required": ["lines"],
}
_NUM = re.compile(r"\d+(?:\.\d+)?")


def _numbers_preserved(src: list[str], dst: list[str]) -> bool:
    """Doses must survive translation: every number in a source line appears in its translation."""
    for a, b in zip(src, dst):
        have = set(_NUM.findall(b))
        if any(n not in have for n in _NUM.findall(a)):
            return False
    return True


def _bullets(text: str, n: int = 4) -> list[str]:
    items = [re.sub(r"^\s*-\s*", "", ln).strip() for ln in text.splitlines() if ln.strip().startswith("-")]
    return items[:n]


def crop_card(hits: list[Hit], crop: str | None, stage: str | None, lang: str, reminder: str) -> dict:
    top = hits[0].chunk
    crop = crop or top.meta.get("crop")
    return {
        "type": "crop",
        "data": {
            "crop": _l(CROP_NAMES[crop], lang) if crop in CROP_NAMES else None,
            "stage": _l(STAGE_NAMES[stage], lang) if stage in STAGE_NAMES else None,
            "title": top.meta.get("section", top.title),
            "actions": _bullets(top.text) or [top.text[:200]],
            "source": top.meta.get("source_name"),
            "source_url": top.meta.get("source_url"),
            "reminder": reminder,
        },
    }

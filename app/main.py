import base64
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.audio import AudioConversionError, to_wav16k
from app.clients.gemini import GeminiClient
from app.clients.gnani import GnaniError, GnaniSTT, GnaniTTS
from app.clients.weather import WeatherClient
from app.config import get_settings
from app.modules import yojana
from app.modules.mausam import MausamSaathi
from app.pipeline import Pipeline
from app.rag.store import VectorIndex
from app.ratelimit import RateLimiter, RateLimitMiddleware
from app.schemas import MAX_MESSAGE_CHARS, AssistantResponse, ChatRequest, EligibilityRequest

log = logging.getLogger("kisan_saathi")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

STATIC_DIR = Path(__file__).parent / "static"
INDEX_DIR = Path(__file__).resolve().parents[1] / "kb" / "index"
DEVANAGARI = re.compile(r"[\u0900-\u097F]")
MAX_AUDIO_BYTES = 2 * 1024 * 1024  # ~25 s of opus is far below this
UI_LANGS = ("hi-IN", "hi-en")  # languages the UI toggle can request


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.http = httpx.AsyncClient()
    key = settings.gnani_api_key.get_secret_value() if settings.gnani_api_key else ""
    app.state.stt = GnaniSTT(key, app.state.http)
    app.state.tts = GnaniTTS(key, app.state.http, voice=settings.tts_voice)
    gkey = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else ""
    gemini = GeminiClient(gkey)
    try:
        index = VectorIndex.load(INDEX_DIR)
        log.info("KB index loaded: %d chunks", len(index.chunks))
    except FileNotFoundError:
        index = None
        log.warning("KB index missing; run scripts/ingest.py")
    app.state.pipeline = Pipeline(
        gemini, index=index, schemes=yojana.load_schemes(),
        mausam=MausamSaathi(WeatherClient(app.state.http), gemini),
        threshold=settings.kb_score_threshold,
    )
    yield
    await app.state.http.aclose()


app = FastAPI(title="Kisan Saathi CG", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
_settings = get_settings()
app.add_middleware(RateLimitMiddleware, limiter=RateLimiter(_settings.rate_limit_per_minute, 60.0))


def get_stt(request: Request) -> GnaniSTT:
    return request.app.state.stt


def get_tts(request: Request) -> GnaniTTS:
    return request.app.state.tts


def get_pipeline(request: Request) -> Pipeline:
    return request.app.state.pipeline


def guess_lang(text: str) -> str:
    """Devanagari script -> hi-IN, otherwise treat as Hinglish/English (hi-en)."""
    return "hi-IN" if DEVANAGARI.search(text) else "hi-en"


def reply_lang(detected: str, text: str) -> str:
    """Map Prisma's detected_language_code (or typed text) to a reply language."""
    if detected == "en-IN":
        return "hi-en"  # Prisma's en-IN covers Hindi-English mixed speech in Latin script
    if detected in ("hi-IN", "hi-en"):
        return detected
    return guess_lang(text)


async def speak_b64(tts: GnaniTTS, text: str, lang: str) -> str | None:
    """TTS is best-effort: on failure the text reply still goes out."""
    try:
        return base64.b64encode(await tts.speak(text, lang)).decode()
    except GnaniError as e:
        log.warning("TTS failed: %s", e)
        return None


def to_response(transcript: str, lang: str, result, audio: str | None) -> AssistantResponse:
    return AssistantResponse(
        transcript=transcript, lang=lang, reply_text=result.reply_text,
        cards=result.cards, sources=result.sources, audio_b64=audio,
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/chat", response_model=AssistantResponse)
async def chat(
    req: ChatRequest,
    tts: GnaniTTS = Depends(get_tts),
    pipeline: Pipeline = Depends(get_pipeline),
) -> AssistantResponse:
    lang = req.lang if req.lang in ("hi-IN", "hi-en", "en-IN") else guess_lang(req.message)
    result = await pipeline.handle(req.message, lang, req.session_id)
    audio = await speak_b64(tts, result.reply_text, lang) if req.speak else None
    return to_response(req.message, lang, result, audio)


@app.post("/api/voice", response_model=AssistantResponse)
async def voice(
    audio: UploadFile = File(...),
    session_id: str | None = Form(default=None, max_length=64),
    lang: str | None = Form(default=None, max_length=10),
    stt: GnaniSTT = Depends(get_stt),
    tts: GnaniTTS = Depends(get_tts),
    pipeline: Pipeline = Depends(get_pipeline),
) -> AssistantResponse:
    """`lang` (hi-IN | hi-en) is the UI language toggle; when set, the reply uses it
    regardless of what Prisma detected. Without it, the reply follows the speech."""
    forced = lang if lang in UI_LANGS else None
    raw = await audio.read(MAX_AUDIO_BYTES + 1)
    if not raw:
        raise HTTPException(422, "Audio file is empty")
    if len(raw) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Audio too large; keep recordings under 25 seconds")
    try:
        wav = await to_wav16k(raw)
    except AudioConversionError as e:
        log.warning("audio conversion failed: %s", e)
        raise HTTPException(422, "Could not read the recording. Please try again.")
    try:
        t = await stt.transcribe(wav)
    except GnaniError as e:
        log.error("STT failed: %s", e)
        raise HTTPException(502, "Speech recognition failed. Please try again or type your question.")
    if not t.text:
        retry = {"hi-IN": "माफ़ कीजिए, आवाज़ साफ़ सुनाई नहीं दी। कृपया दोबारा बोलिए।",
                 "hi-en": "Maaf kijiye, aawaz saaf sunai nahi di. Kripya dobara boliye."}
        return AssistantResponse(transcript="", lang=forced or "hi-en", reply_text=retry[forced or "hi-en"])
    text = t.text[:MAX_MESSAGE_CHARS]
    lang = forced or reply_lang(t.lang, text)
    result = await pipeline.handle(text, lang, session_id)
    return to_response(text, lang, result, await speak_b64(tts, result.reply_text, lang))


@app.post("/api/eligibility", response_model=AssistantResponse)
async def eligibility(
    req: EligibilityRequest,
    tts: GnaniTTS = Depends(get_tts),
    pipeline: Pipeline = Depends(get_pipeline),
) -> AssistantResponse:
    """Re-check eligibility from the form toggles. Pure rules, no LLM call."""
    lang = req.lang if req.lang in ("hi-IN", "hi-en") else "hi-en"
    pipeline.sessions.set_profile(req.session_id, req.profile)
    result = pipeline.eligibility_result(req.profile, lang)
    audio = await speak_b64(tts, result.reply_text, lang) if req.speak else None
    return to_response("", lang, result, audio)

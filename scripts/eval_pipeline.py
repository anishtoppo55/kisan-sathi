"""Live end-to-end check of the pipeline (real Gemini + Open-Meteo, no Gnani credits).

Usage: env -u PYTHONPATH .venv/bin/python scripts/eval_pipeline.py [question ...]
Prints intent, top KB score, reply and card types for each question.
"""

import asyncio
import logging
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.clients.gemini import GeminiClient  # noqa: E402
from app.clients.weather import WeatherClient  # noqa: E402
from app.clients.websearch import DdgsClient, SearxngClient, WebSearch  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.main import guess_lang  # noqa: E402
from app.modules import yojana  # noqa: E402
from app.modules.mausam import MausamSaathi  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402
from app.rag.store import VectorIndex  # noqa: E402

QUESTIONS = [
    "PM-Kisan ke liye kaun se documents chahiye?",
    "Main 2 acre ka kisan hoon, income tax nahi bharta, KCC nahi hai",
    "धान बेचने के लिए टोकन कैसे मिलेगा?",
    "Chana mein phool aa rahe hain, sinchai karun?",
    "Dhaan mein bhura maahu lag gaya hai kya karun?",
    "Bemetara mein agle hafte barish hogi? Gehun mein paani doon?",
    "Is saal dhaan ka samarthan mulya kitna hai?",
    "Tractor par subsidy kaise milegi?",
]


async def main(questions):
    logging.basicConfig(level=logging.INFO, format="   %(name)s: %(message)s")
    for noisy in ("httpx", "google_genai", "google_genai.models"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    s = get_settings()
    async with httpx.AsyncClient() as http:
        g = GeminiClient(s.gemini_api_key.get_secret_value())
        p = Pipeline(g, index=VectorIndex.load(ROOT / "kb" / "index"), schemes=yojana.load_schemes(),
                     mausam=MausamSaathi(WeatherClient(http), g), threshold=s.kb_score_threshold,
                     websearch=WebSearch([SearxngClient(http, s.searxng_url, s.web_search_timeout_s),
                                          DdgsClient(timeout_s=s.web_search_timeout_s + 4)]),
                     use_grounding=s.gemini_grounding)
        for q in questions:
            t = time.time()
            r = await p.handle(q, guess_lang(q), "eval")
            print(f"\nQ: {q}\n   intent={r.intent} cards={[c['type'] for c in r.cards]} sources={len(r.sources)} ({time.time() - t:.1f}s)")
            print(f"A: {r.reply_text}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or QUESTIONS))

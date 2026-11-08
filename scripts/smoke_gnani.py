"""Real-API smoke test for Gnani Timbre (TTS) and Prisma (STT). Uses a few credits.

Usage: env -u PYTHONPATH .venv/bin/python scripts/smoke_gnani.py
"""

import asyncio
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.audio import to_wav16k  # noqa: E402
from app.clients.gnani import GnaniSTT, GnaniTTS  # noqa: E402
from app.config import get_settings  # noqa: E402

OUT = Path(".cache")


async def main() -> int:
    key = get_settings().gnani_api_key
    if not key:
        print("GNANI_API_KEY missing in .env")
        return 1
    OUT.mkdir(exist_ok=True)
    async with httpx.AsyncClient() as client:
        tts = GnaniTTS(key.get_secret_value(), client)
        stt = GnaniSTT(key.get_secret_value(), client)
        ok = True
        for lang, text in [
            ("hi-IN", "नमस्ते, मैं रायपुर से हूँ। पीएम किसान योजना में साल में छह हज़ार रुपये मिलते हैं।"),
            ("hi-en", "Namaste, main Raipur se hoon. PM Kisan scheme ke documents kya hain?"),
        ]:
            try:
                mp3 = await tts.speak(text, lang)
                (OUT / f"smoke_{lang}.mp3").write_bytes(mp3)
                print(f"[TTS {lang}] OK {len(mp3)} bytes -> .cache/smoke_{lang}.mp3")
                wav = await to_wav16k(mp3)
                t = await stt.transcribe(wav)
                print(f"[STT {lang}] OK lang={t.lang} transcript={t.text!r}")
            except Exception as e:  # noqa: BLE001
                ok = False
                print(f"[{lang}] FAILED: {e}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

"""Gnani Prisma (speech-to-text) and Timbre (text-to-speech) REST clients.

Docs: https://docs.gnani.ai/api/STT/speech-to-text  and  https://docs.gnani.ai/api/TTS/tts-inference
Some accounts allow only one active request at a time (429 RATE_LIMITED, no Retry-After),
so all Gnani calls go through one shared lock and 429s are retried with backoff.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import OrderedDict
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

BASE_URL = "https://api.vachana.ai"
STT_PATH = "/stt/v3"
TTS_PATH = "/api/v1/tts/inference"

_gnani_lock = asyncio.Lock()


class GnaniError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _error_message(resp: httpx.Response) -> str:
    """Pull a readable message out of Gnani's several error shapes."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:200]
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return f"{err.get('type')}: {err.get('message')}"
        detail = body.get("detail")
        if isinstance(detail, dict):
            return f"{detail.get('error_code')}: {detail.get('message')}"
        if body.get("message"):
            return str(body["message"])
        if detail:
            return str(detail)[:200]
    return str(body)[:200]


async def _post_with_retry(client: httpx.AsyncClient, url: str, retries: int = 3, **kwargs) -> httpx.Response:
    delay = 0.6
    async with _gnani_lock:
        for attempt in range(retries + 1):
            try:
                resp = await client.post(url, **kwargs)
            except httpx.TimeoutException as e:
                raise GnaniError("Gnani request timed out") from e
            except httpx.HTTPError as e:
                raise GnaniError(f"Gnani network error: {e.__class__.__name__}") from e
            if resp.status_code == 429 and attempt < retries:
                log.warning("Gnani 429, retrying in %.1fs", delay)
                await asyncio.sleep(delay)
                delay *= 2
                continue
            return resp
    return resp  # pragma: no cover


@dataclass
class Transcript:
    text: str
    lang: str  # detected_language_code, e.g. hi-IN / en-IN


class GnaniSTT:
    def __init__(self, api_key: str, client: httpx.AsyncClient, model: str = "prisma-v3.0"):
        self.api_key = api_key
        self.client = client
        self.model = model

    async def transcribe(self, wav: bytes, language_code: str = "indic-auto", keyterms: str | None = None) -> Transcript:
        data = {"model": self.model, "language_code": language_code}
        if keyterms:
            data["keyterms"] = keyterms
        resp = await _post_with_retry(
            self.client,
            BASE_URL + STT_PATH,
            headers={"X-API-Key-ID": self.api_key},
            files={"audio_file": ("audio.wav", wav, "audio/wav")},
            data=data,
            timeout=30.0,
        )
        if resp.status_code != 200:
            raise GnaniError(f"STT failed ({resp.status_code}): {_error_message(resp)}", resp.status_code)
        body = resp.json()
        if not body.get("success", True):
            raise GnaniError(f"STT failed: {_error_message(resp)}", resp.status_code)
        text = (body.get("transcript") or "").strip()
        return Transcript(text=text, lang=body.get("detected_language_code") or "hi-IN")


# (model, voice, language) attempts per reply language, in order.
# Timbre v3.0's catalog lists 10 languages but not hi-en, so Hinglish goes to the
# dedicated v2.5 Hinglish voice first.
VOICE_PLAN: dict[str, list[tuple[str, str, str]]] = {
    "hi-IN": [("timbre-v3.0", "Shivani", "hi-IN"), ("timbre-v2.5", "Nalini", "hi-IN")],
    "hi-en": [("timbre-v2.5", "Poorvi", "hi-en"), ("timbre-v3.0", "Shivani", "auto")],
    "en-IN": [("timbre-v3.0", "Shivani", "en-IN"), ("timbre-v2.5", "Kaveri", "en-IN")],
}


def voice_plan(lang: str) -> list[tuple[str, str, str]]:
    return VOICE_PLAN.get(lang, VOICE_PLAN["hi-IN"])


class GnaniTTS:
    def __init__(self, api_key: str, client: httpx.AsyncClient, cache_size: int = 256, speed: float = 1.0):
        self.api_key = api_key
        self.client = client
        self.speed = speed
        self._cache: OrderedDict[str, bytes] = OrderedDict()
        self._cache_size = cache_size

    @staticmethod
    def cache_key(text: str, model: str, voice: str, language: str) -> str:
        return hashlib.sha256(f"{model}|{voice}|{language}|{text}".encode()).hexdigest()

    def _cache_get(self, key: str) -> bytes | None:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def _cache_put(self, key: str, audio: bytes) -> None:
        self._cache[key] = audio
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    async def _synthesize(self, text: str, model: str, voice: str, language: str) -> bytes:
        payload = {
            "text": text,
            "voice": voice,
            "model": model,
            "language": language,
            "speed": self.speed,
            "audio_config": {"sample_rate": 24000, "num_channels": 1, "container": "mp3", "bitrate": "64k"},
        }
        resp = await _post_with_retry(
            self.client,
            BASE_URL + TTS_PATH,
            headers={"X-API-Key-ID": self.api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=30.0,
        )
        if resp.status_code != 200 or not resp.content or resp.headers.get("content-type", "").startswith("application/json"):
            raise GnaniError(f"TTS {model}/{voice} failed ({resp.status_code}): {_error_message(resp)}", resp.status_code)
        return resp.content

    async def speak(self, text: str, lang: str) -> bytes:
        """Return MP3 bytes. Tries each (model, voice, language) for the language in turn."""
        text = text.strip()
        if not text:
            raise GnaniError("TTS text is empty")
        last_err: GnaniError | None = None
        for model, voice, language in voice_plan(lang):
            key = self.cache_key(text, model, voice, language)
            if (hit := self._cache_get(key)) is not None:
                return hit
            try:
                audio = await self._synthesize(text, model, voice, language)
            except GnaniError as e:
                if e.status in (401, 402, 403):  # auth / credit problems won't be fixed by another voice
                    raise
                log.warning("%s; trying fallback voice", e)
                last_err = e
                continue
            self._cache_put(key, audio)
            return audio
        raise last_err or GnaniError("TTS failed")

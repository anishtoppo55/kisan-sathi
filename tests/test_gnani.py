import json

import httpx
import pytest
import respx

from app.clients import gnani
from app.clients.gnani import BASE_URL, STT_PATH, TTS_PATH, GnaniError, GnaniSTT, GnaniTTS, voice_plan

STT_URL = BASE_URL + STT_PATH
TTS_URL = BASE_URL + TTS_PATH


@pytest.fixture(autouse=True)
def fast_retry(monkeypatch):
    async def no_sleep(_):
        return None
    monkeypatch.setattr(gnani.asyncio, "sleep", no_sleep)


# ---------- STT ----------

@respx.mock
async def test_stt_request_and_parse():
    route = respx.post(STT_URL).mock(return_value=httpx.Response(200, json={
        "success": True, "transcript": " नमस्ते ", "model": "prisma-v3.0", "detected_language_code": "hi-IN"}))
    async with httpx.AsyncClient() as c:
        t = await GnaniSTT("k123", c).transcribe(b"RIFFfake")
    assert t.text == "नमस्ते" and t.lang == "hi-IN"
    req = route.calls.last.request
    assert req.headers["X-API-Key-ID"] == "k123"
    body = req.content
    assert b'name="model"' in body and b"prisma-v3.0" in body
    assert b"indic-auto" in body and b'name="audio_file"' in body


@respx.mock
async def test_stt_error_envelope():
    respx.post(STT_URL).mock(return_value=httpx.Response(400, json={
        "success": False, "error": {"type": "EMPTY_AUDIO", "message": "Empty audio file received"}}))
    async with httpx.AsyncClient() as c:
        with pytest.raises(GnaniError, match="EMPTY_AUDIO"):
            await GnaniSTT("k", c).transcribe(b"x")


@respx.mock
async def test_stt_retries_429_then_succeeds():
    route = respx.post(STT_URL).mock(side_effect=[
        httpx.Response(429, json={"detail": {"error_code": "RATE_LIMITED", "message": "busy"}}),
        httpx.Response(200, json={"success": True, "transcript": "ok", "detected_language_code": "en-IN"}),
    ])
    async with httpx.AsyncClient() as c:
        t = await GnaniSTT("k", c).transcribe(b"x")
    assert t.text == "ok" and route.call_count == 2


@respx.mock
async def test_stt_timeout():
    respx.post(STT_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    async with httpx.AsyncClient() as c:
        with pytest.raises(GnaniError, match="timed out"):
            await GnaniSTT("k", c).transcribe(b"x")


# ---------- TTS ----------

def test_voice_plan_mapping():
    assert voice_plan("hi-IN")[0] == ("timbre-v3.0", "Shivani", "hi-IN")
    assert voice_plan("hi-en")[0] == ("timbre-v2.5", "Poorvi", "hi-en")
    assert voice_plan("en-IN")[1][0] == "timbre-v2.5"
    assert voice_plan("ta-IN") == voice_plan("hi-IN")  # unknown -> Hindi


@respx.mock
async def test_tts_request_payload():
    route = respx.post(TTS_URL).mock(return_value=httpx.Response(200, content=b"MP3DATA", headers={"content-type": "audio/mpeg"}))
    async with httpx.AsyncClient() as c:
        audio = await GnaniTTS("k", c).speak("नमस्ते", "hi-IN")
    assert audio == b"MP3DATA"
    payload = json.loads(route.calls.last.request.content)
    assert payload["model"] == "timbre-v3.0" and payload["voice"] == "Shivani" and payload["language"] == "hi-IN"
    assert payload["audio_config"]["container"] == "mp3"


@respx.mock
async def test_tts_falls_back_to_v25():
    route = respx.post(TTS_URL).mock(side_effect=[
        httpx.Response(400, json={"success": False, "message": "voice not supported", "status_code": 400}),
        httpx.Response(200, content=b"V25", headers={"content-type": "audio/mpeg"}),
    ])
    async with httpx.AsyncClient() as c:
        audio = await GnaniTTS("k", c).speak("नमस्ते", "hi-IN")
    assert audio == b"V25"
    second = json.loads(route.calls[1].request.content)
    assert (second["model"], second["voice"]) == ("timbre-v2.5", "Nalini")


@respx.mock
async def test_tts_auth_error_not_retried_with_other_voice():
    route = respx.post(TTS_URL).mock(return_value=httpx.Response(401, json={"detail": {"error_code": "INVALID_API_KEY", "message": "Unauthorized"}}))
    async with httpx.AsyncClient() as c:
        with pytest.raises(GnaniError, match="INVALID_API_KEY"):
            await GnaniTTS("k", c).speak("hi", "hi-IN")
    assert route.call_count == 1


@respx.mock
async def test_tts_cache_hit():
    route = respx.post(TTS_URL).mock(return_value=httpx.Response(200, content=b"A", headers={"content-type": "audio/mpeg"}))
    async with httpx.AsyncClient() as c:
        tts = GnaniTTS("k", c)
        await tts.speak("same text", "hi-IN")
        await tts.speak("same text", "hi-IN")
        await tts.speak("other text", "hi-IN")
    assert route.call_count == 2


async def test_tts_empty_text():
    async with httpx.AsyncClient() as c:
        with pytest.raises(GnaniError):
            await GnaniTTS("k", c).speak("   ", "hi-IN")

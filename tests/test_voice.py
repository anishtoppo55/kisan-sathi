import base64
import shutil
import subprocess

import pytest

from app.clients.gnani import GnaniError, Transcript
from app.main import reply_lang
from tests.conftest import FakeSTT, FakeTTS


@pytest.fixture(scope="module")
def webm_audio():
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    return subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-c:a", "libopus", "-f", "webm", "pipe:1"],
        capture_output=True, check=True,
    ).stdout


def post_audio(client, data, name="r.webm"):
    return client.post("/api/voice", files={"audio": (name, data, "audio/webm")}, data={"session_id": "s1"})


def test_reply_lang():
    assert reply_lang("en-IN", "main kisan hoon") == "hi-en"
    assert reply_lang("hi-IN", "नमस्ते") == "hi-IN"
    assert reply_lang("", "नमस्ते") == "hi-IN"


def test_voice_happy_path(client, fakes, webm_audio):
    fakes["stt"] = FakeSTT(Transcript("नमस्ते, मैं रायपुर से हूँ", "hi-IN"))
    r = post_audio(client, webm_audio)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["transcript"] == "नमस्ते, मैं रायपुर से हूँ"
    assert body["lang"] == "hi-IN"
    assert body["reply_text"] == "Jawab"
    assert base64.b64decode(body["audio_b64"]) == b"MP3"
    assert fakes["stt"].calls[0][:4] == b"RIFF"  # converted to WAV before STT
    assert fakes["pipeline"].calls == [("नमस्ते, मैं रायपुर से हूँ", "hi-IN", "s1")]
    assert fakes["tts"].calls == [("Jawab", "hi-IN")]


def test_voice_tts_failure_still_returns_text(client, fakes, webm_audio):
    fakes["stt"] = FakeSTT(Transcript("hello", "en-IN"))
    fakes["tts"] = FakeTTS(error=GnaniError("down", 500))
    body = post_audio(client, webm_audio).json()
    assert body["reply_text"] == "Jawab" and body["audio_b64"] is None and body["lang"] == "hi-en"


def test_voice_empty_transcript(client, fakes, webm_audio):
    fakes["stt"] = FakeSTT(Transcript("", "hi-IN"))
    body = post_audio(client, webm_audio).json()
    assert body["transcript"] == "" and "dobara" in body["reply_text"]
    assert fakes["pipeline"].calls == []


def test_voice_stt_error_gives_502(client, fakes, webm_audio):
    fakes["stt"] = FakeSTT(error=GnaniError("STT failed", 500))
    assert post_audio(client, webm_audio).status_code == 502


def test_voice_rejects_empty_and_garbage(client):
    assert post_audio(client, b"").status_code == 422
    assert post_audio(client, b"not audio" * 50).status_code == 422


def test_voice_rejects_too_large(client):
    assert post_audio(client, b"0" * (2 * 1024 * 1024 + 10)).status_code == 413


def test_chat_speaks_reply(client, fakes):
    body = client.post("/api/chat", json={"message": "PM-Kisan kya hai?"}).json()
    assert body["audio_b64"] and fakes["tts"].calls == [("Jawab", "hi-en")]


def test_voice_lang_toggle_overrides_detection(client, fakes, webm_audio):
    # Farmer speaks Hinglish (Prisma says en-IN) but the UI toggle is Hindi -> reply in Devanagari Hindi
    fakes["stt"] = FakeSTT(Transcript("PM Kisan ke documents kya hain", "en-IN"))
    r = client.post("/api/voice", files={"audio": ("r.webm", webm_audio, "audio/webm")},
                    data={"session_id": "s1", "lang": "hi-IN"})
    assert r.json()["lang"] == "hi-IN"
    assert fakes["pipeline"].calls[-1][1] == "hi-IN" and fakes["tts"].calls[-1][1] == "hi-IN"


def test_voice_without_lang_keeps_detection_and_ignores_bad_lang(client, fakes, webm_audio):
    fakes["stt"] = FakeSTT(Transcript("नमस्ते", "hi-IN"))
    for extra in ({}, {"lang": "ta-IN"}):
        r = client.post("/api/voice", files={"audio": ("r.webm", webm_audio, "audio/webm")}, data={"session_id": "s", **extra})
        assert r.json()["lang"] == "hi-IN"


def test_voice_empty_transcript_in_toggle_language(client, fakes, webm_audio):
    fakes["stt"] = FakeSTT(Transcript("", "hi-IN"))
    body = client.post("/api/voice", files={"audio": ("r.webm", webm_audio, "audio/webm")}, data={"lang": "hi-IN"}).json()
    assert body["lang"] == "hi-IN" and "दोबारा" in body["reply_text"]

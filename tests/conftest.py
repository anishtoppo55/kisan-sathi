import pytest
from fastapi.testclient import TestClient

from app.main import app, get_pipeline, get_stt, get_tts
from app.pipeline import PipelineResult


class FakeSTT:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result, error, []

    async def transcribe(self, wav, **kw):
        self.calls.append(wav)
        if self.error:
            raise self.error
        return self.result


class FakeTTS:
    def __init__(self, error=None):
        self.error, self.calls = error, []

    async def speak(self, text, lang):
        self.calls.append((text, lang))
        if self.error:
            raise self.error
        return b"MP3"


class FakePipeline:
    def __init__(self, reply="Jawab", cards=None, sources=None):
        self.reply, self.cards, self.sources, self.calls = reply, cards or [], sources or [], []

    async def handle(self, text, lang, session_id=None):
        self.calls.append((text, lang, session_id))
        return PipelineResult(reply_text=self.reply, cards=self.cards, sources=self.sources)


@pytest.fixture
def fakes():
    """Install fake STT/TTS/pipeline; tests can tweak them via the returned dict."""
    f = {"stt": FakeSTT(), "tts": FakeTTS(), "pipeline": FakePipeline()}
    app.dependency_overrides[get_stt] = lambda: f["stt"]
    app.dependency_overrides[get_tts] = lambda: f["tts"]
    app.dependency_overrides[get_pipeline] = lambda: f["pipeline"]
    yield f
    app.dependency_overrides.clear()


@pytest.fixture
def client(fakes):
    # No `with`: lifespan (real clients) is not started in unit tests.
    return TestClient(app)

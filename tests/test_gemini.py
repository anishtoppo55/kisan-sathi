"""GeminiClient model chain: Flash-Lite first (500 RPD free), 20-RPD Flash models last."""

import pytest

from app.clients import gemini as gmod
from app.clients.gemini import ANSWER_MODELS, FAST_MODELS, GeminiClient, GeminiError
from app.config import Settings


class FakeAPIError(gmod.genai_errors.APIError):
    def __init__(self, code):
        Exception.__init__(self, f"{code} error")
        self.code = code


class FakeResp:
    def __init__(self, text):
        self.text = text
        self.candidates = []


class FakeModels:
    def __init__(self, behaviour):
        self.behaviour, self.calls = behaviour, []

    async def generate_content(self, model, contents, config):
        self.calls.append(model)
        b = self.behaviour.get(model, "ok")
        if isinstance(b, Exception):
            raise b
        return FakeResp(f"answer from {model}")


def client_with(behaviour, **kw):
    c = GeminiClient("k", **kw)
    fake = FakeModels(behaviour)
    c._client = type("C", (), {"aio": type("A", (), {"models": fake})()})()
    return c, fake


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def _s(_):
        return None
    monkeypatch.setattr(gmod.asyncio, "sleep", _s)


def test_default_order_puts_flash_lite_first():
    assert ANSWER_MODELS[0] == "gemini-3.5-flash-lite" and FAST_MODELS[0] == "gemini-3.1-flash-lite"
    assert ANSWER_MODELS[-1] == "gemini-3.6-flash" and FAST_MODELS[-1] == "gemini-3.6-flash"
    s = Settings(_env_file=None)
    assert s.model_list("answer_models") == ANSWER_MODELS
    assert s.model_list("fast_models") == FAST_MODELS


async def test_429_falls_through_to_next_model():
    c, fake = client_with({"gemini-3.5-flash-lite": FakeAPIError(429)})
    out = await c.generate("sys", "q")
    assert out == "answer from gemini-3.1-flash-lite"
    assert fake.calls == ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]


async def test_fast_calls_use_fast_chain():
    c, fake = client_with({})
    await c.generate("sys", "q", fast=True)
    assert fake.calls == ["gemini-3.1-flash-lite"]


async def test_non_transient_error_stops_immediately():
    c, fake = client_with({"gemini-3.5-flash-lite": FakeAPIError(400)})
    with pytest.raises(GeminiError):
        await c.generate("sys", "q")
    assert fake.calls == ["gemini-3.5-flash-lite"]


async def test_all_quota_exhausted_raises_after_two_rounds():
    c, fake = client_with({m: FakeAPIError(429) for m in ANSWER_MODELS})
    with pytest.raises(GeminiError, match="all models"):
        await c.generate("sys", "q")
    assert len(fake.calls) == 2 * len(ANSWER_MODELS)


def test_model_list_parses_env_override():
    s = Settings(_env_file=None, answer_models=" a , b,,c ")
    assert s.model_list("answer_models") == ("a", "b", "c")

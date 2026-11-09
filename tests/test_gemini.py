"""GeminiClient model chain: 3.1 Flash-Lite first (fast, 500 RPD free), 20-RPD Flash last.
Unusable 200 responses (empty, MALFORMED_RESPONSE, bad JSON) and slow calls fall through to the next model."""

import asyncio

import pytest

from app.clients import gemini as gmod
from app.clients.gemini import ANSWER_MODELS, FAST_MODELS, GeminiClient, GeminiError
from app.config import Settings

LITE31, LITE35, FLASH = "gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.6-flash"


class FakeAPIError(gmod.genai_errors.APIError):
    def __init__(self, code):
        Exception.__init__(self, f"{code} error")
        self.code = code


class Cand:
    def __init__(self, reason="STOP"):
        self.finish_reason = reason


class FakeResp:
    def __init__(self, text, reason="STOP"):
        self.text = text
        self.candidates = [Cand(reason)]


class FakeModels:
    def __init__(self, behaviour):
        self.behaviour, self.calls = behaviour, []

    async def generate_content(self, model, contents, config):
        self.calls.append(model)
        b = self.behaviour.get(model, "ok")
        if isinstance(b, Exception):
            raise b
        if isinstance(b, FakeResp):
            return b
        if b == "slow":
            await asyncio.sleep(5)
        return FakeResp(f'{{"from": "{model}"}}' if getattr(config, "response_mime_type", None) else f"answer from {model}")


def client_with(behaviour, **kw):
    kw.setdefault("timeout_s", 0.2)
    kw.setdefault("fast_timeout_s", 0.2)
    c = GeminiClient("k", **kw)
    fake = FakeModels(behaviour)
    c._client = type("C", (), {"aio": type("A", (), {"models": fake})()})()
    return c, fake


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    real_sleep = asyncio.sleep

    async def _s(t):
        if t < 1.5:  # skip the retry pause, keep the fake "slow" model slow
            return None
        await real_sleep(t)
    monkeypatch.setattr(gmod.asyncio, "sleep", _s)


def test_default_order_fast_flash_lite_first():
    assert ANSWER_MODELS == (LITE31, LITE35, FLASH) and FAST_MODELS == (LITE31, LITE35, FLASH)
    s = Settings(_env_file=None)
    assert s.model_list("answer_models") == ANSWER_MODELS and s.model_list("fast_models") == FAST_MODELS


async def test_429_falls_through_to_next_model():
    c, fake = client_with({LITE31: FakeAPIError(429)})
    assert await c.generate("sys", "q") == f"answer from {LITE35}"
    assert fake.calls == [LITE31, LITE35]


async def test_503_high_demand_falls_through():
    c, fake = client_with({LITE31: FakeAPIError(503)})
    assert await c.generate("sys", "q", fast=True) == f"answer from {LITE35}"


async def test_malformed_or_empty_answer_tries_next_model():
    c, fake = client_with({LITE31: FakeResp("", "MALFORMED_RESPONSE")})
    assert await c.generate("sys", "q") == f"answer from {LITE35}"
    assert fake.calls == [LITE31, LITE35]


async def test_invalid_json_tries_next_model():
    c, fake = client_with({LITE31: FakeResp("not json")})
    assert await c.generate_json("sys", "q", {"type": "object"}) == {"from": LITE35}


async def test_slow_model_is_abandoned():
    c, fake = client_with({LITE31: "slow"})
    assert await c.generate("sys", "q") == f"answer from {LITE35}"


async def test_non_transient_error_stops_immediately():
    c, fake = client_with({LITE31: FakeAPIError(400)})
    with pytest.raises(GeminiError):
        await c.generate("sys", "q")
    assert fake.calls == [LITE31]


async def test_all_quota_exhausted_raises_after_two_rounds():
    c, fake = client_with({m: FakeAPIError(429) for m in ANSWER_MODELS})
    with pytest.raises(GeminiError, match="all models"):
        await c.generate("sys", "q")
    assert len(fake.calls) == 2 * len(ANSWER_MODELS)


def test_model_list_parses_env_override():
    s = Settings(_env_file=None, answer_models=" a , b,,c ")
    assert s.model_list("answer_models") == ("a", "b", "c")

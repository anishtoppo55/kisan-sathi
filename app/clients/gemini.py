"""Thin async wrapper around the google-genai SDK.

Keeps every Gemini call in one place so the pipeline and tests can swap in a fake.
Transient errors (429/500/503/504) are retried once and then the next model in the
fallback chain is tried, because flagship models sometimes return 503 under load.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

log = logging.getLogger(__name__)

# gemini-3.8-flash returned 503 (high demand) during testing, so the stable 3.6 flash goes first.
ANSWER_MODELS = ("gemini-3.6-flash", "gemini-3.8-flash", "gemini-3.5-flash-lite")
FAST_MODELS = ("gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.6-flash")
EMBED_MODEL = "gemini-embedding-2"
EMBED_DIM = 768
TRANSIENT = {429, 500, 503, 504}


class GeminiError(Exception):
    pass


@dataclass
class Message:
    role: str  # "user" | "model"
    text: str


@dataclass
class SearchResult:
    text: str
    sources: list[dict] = field(default_factory=list)  # [{"title", "url"}]


def _contents(history: list[Message], user_text: str) -> list[types.Content]:
    out = [types.Content(role=m.role, parts=[types.Part(text=m.text)]) for m in history]
    out.append(types.Content(role="user", parts=[types.Part(text=user_text)]))
    return out


def extract_sources(response) -> list[dict]:
    """Collect unique web sources from a grounded response's metadata."""
    sources, seen = [], set()
    for cand in getattr(response, "candidates", None) or []:
        meta = getattr(cand, "grounding_metadata", None)
        for chunk in getattr(meta, "grounding_chunks", None) or []:
            web = getattr(chunk, "web", None)
            uri = getattr(web, "uri", None)
            if not uri or uri in seen:
                continue
            seen.add(uri)
            sources.append({"title": getattr(web, "title", None) or getattr(web, "domain", None) or uri, "url": uri})
    return sources


def _is_transient(e: Exception) -> bool:
    return isinstance(e, genai_errors.APIError) and getattr(e, "code", None) in TRANSIENT


class GeminiClient:
    def __init__(self, api_key: str, answer_models=ANSWER_MODELS, fast_models=FAST_MODELS, timeout_s: float = 25.0):
        self._client = genai.Client(api_key=api_key)
        self.answer_models = tuple(answer_models)
        self.fast_models = tuple(fast_models)
        self.timeout_s = timeout_s

    async def _generate(self, models, contents, cfg, what: str, rounds: int = 2):
        """Try each model in order; on transient errors move to the next one.
        The whole chain is tried `rounds` times with a short pause in between."""
        last: Exception | None = None
        for round_ in range(rounds):
            for model in models:
                try:
                    return await asyncio.wait_for(
                        self._client.aio.models.generate_content(model=model, contents=contents, config=cfg),
                        self.timeout_s,
                    )
                except asyncio.TimeoutError as e:
                    last = e
                    log.warning("Gemini %s timed out on %s", what, model)
                except Exception as e:  # noqa: BLE001
                    last = e
                    if not _is_transient(e):
                        raise GeminiError(f"Gemini {what} failed: {e.__class__.__name__}: {str(e)[:200]}") from e
                    log.warning("Gemini %s transient error on %s: %s", what, model, str(e)[:120])
            if round_ < rounds - 1:
                await asyncio.sleep(1.0)
        raise GeminiError(f"Gemini {what} failed on all models: {str(last)[:200]}")

    async def generate(
        self,
        system: str,
        user_text: str,
        history: list[Message] | None = None,
        *,
        fast: bool = False,
        temperature: float = 0.3,
        max_tokens: int = 800,
    ) -> str:
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            max_output_tokens=max_tokens,
            thinking_config=types.ThinkingConfig(thinking_level="low"),
        )
        models = self.fast_models if fast else self.answer_models
        resp = await self._generate(models, _contents(history or [], user_text), cfg, "generate")
        text = (resp.text or "").strip()
        if not text:
            raise GeminiError("Gemini returned an empty answer")
        return text

    async def generate_json(self, system: str, user_text: str, schema: dict, *, fast: bool = True) -> dict:
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.0,
            response_mime_type="application/json",
            response_json_schema=schema,
            thinking_config=types.ThinkingConfig(thinking_level="low"),
        )
        models = self.fast_models if fast else self.answer_models
        resp = await self._generate(models, user_text, cfg, "json")
        try:
            data = json.loads(resp.text or "{}")
        except json.JSONDecodeError as e:
            raise GeminiError("Gemini returned invalid JSON") from e
        if not isinstance(data, dict):
            raise GeminiError("Gemini JSON was not an object")
        return data

    async def embed(self, texts: list[str]) -> list[list[float]]:
        """One embedding per text (each text in its own Content so they aren't aggregated)."""
        contents = [types.Content(parts=[types.Part(text=t)]) for t in texts]
        cfg = types.EmbedContentConfig(output_dimensionality=EMBED_DIM)
        last: Exception | None = None
        for attempt in range(3):
            try:
                resp = await self._client.aio.models.embed_content(model=EMBED_MODEL, contents=contents, config=cfg)
                break
            except Exception as e:  # noqa: BLE001
                last = e
                if not _is_transient(e) or attempt == 2:
                    raise GeminiError(f"Gemini embed failed: {e.__class__.__name__}: {str(e)[:200]}") from e
                await asyncio.sleep(1.5 * (attempt + 1))
        else:  # pragma: no cover
            raise GeminiError(f"Gemini embed failed: {last}")
        vectors = [e.values for e in resp.embeddings or []]
        if len(vectors) != len(texts):
            raise GeminiError(f"expected {len(texts)} embeddings, got {len(vectors)}")
        return vectors

    async def search(self, system: str, query: str) -> SearchResult:
        """Separate call with Google Search grounding; returns text plus source URLs."""
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.2,
            tools=[types.Tool(google_search=types.GoogleSearch())],
            thinking_config=types.ThinkingConfig(thinking_level="low"),
        )
        # One round only: search quota errors (429) don't clear within seconds.
        resp = await self._generate(self.answer_models, query, cfg, "search", rounds=1)
        return SearchResult(text=(resp.text or "").strip(), sources=extract_sources(resp))

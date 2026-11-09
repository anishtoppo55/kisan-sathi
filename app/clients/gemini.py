"""Thin async wrapper around the google-genai SDK.

Keeps every Gemini call in one place so the pipeline and tests can swap in a fake.
Transient errors (429/500/503/504) are retried once and then the next model in the
fallback chain is tried, because flagship models sometimes return 503 under load.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

log = logging.getLogger(__name__)

# Free-tier daily limits (AI Studio, Nov 2026): 3.6/3.8 Flash 20 RPD, 3.5/3.1 Flash-Lite 500 RPD.
# Measured on the same RAG prompt (3 runs each): 3.1 Flash-Lite 1.2-1.8 s, 3.5 Flash-Lite 0.6-8.5 s.
# So 3.1 Flash-Lite answers first; 3.5 Flash-Lite is the fallback (also on 3.1's 503 "high demand");
# the 20-RPD Flash model is the last resort.
ANSWER_MODELS = ("gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.6-flash")
FAST_MODELS = ("gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.6-flash")
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


def _warn_if_truncated(resp) -> None:
    for cand in getattr(resp, "candidates", None) or []:
        reason = str(getattr(cand, "finish_reason", "") or "")
        if reason and "STOP" not in reason:
            log.warning("Gemini answer finished with %s (may be cut short)", reason)


def _is_transient(e: Exception) -> bool:
    return isinstance(e, genai_errors.APIError) and getattr(e, "code", None) in TRANSIENT


class BadResponse(Exception):
    """A 200 response that is unusable (empty, MALFORMED_RESPONSE, invalid JSON): try the next model."""


def _check_text(resp):
    text = (getattr(resp, "text", None) or "").strip()
    if not text:
        reasons = [str(getattr(c, "finish_reason", "")) for c in getattr(resp, "candidates", None) or []]
        raise BadResponse(f"empty answer (finish: {', '.join(reasons) or 'none'})")
    return text


def _check_json(resp):
    try:
        data = json.loads(getattr(resp, "text", None) or "")
    except (json.JSONDecodeError, TypeError) as e:
        raise BadResponse("invalid JSON") from e
    if not isinstance(data, dict):
        raise BadResponse("JSON was not an object")
    return data


class GeminiClient:
    def __init__(self, api_key: str, answer_models=ANSWER_MODELS, fast_models=FAST_MODELS, timeout_s: float = 9.0,
                 fast_timeout_s: float = 5.0):
        self._client = genai.Client(api_key=api_key)
        self.answer_models = tuple(answer_models)
        self.fast_models = tuple(fast_models)
        # Per-attempt timeouts. Normal answers take 1-3 s; a call that hangs longer is abandoned
        # and the next model is tried, instead of the farmer waiting 15-20 s.
        self.timeout_s = timeout_s
        self.fast_timeout_s = fast_timeout_s

    async def _generate(self, models, contents, cfg, what: str, rounds: int = 2, timeout_s: float | None = None,
                        check=None):
        """Try each model in order; on transient errors, timeouts or unusable responses
        (`check` raises BadResponse) move to the next one. Returns check(resp) or resp.
        The whole chain is tried `rounds` times with a short pause in between."""
        last: Exception | None = None
        for round_ in range(rounds):
            for model in models:
                t0 = time.monotonic()
                try:
                    resp = await asyncio.wait_for(
                        self._client.aio.models.generate_content(model=model, contents=contents, config=cfg),
                        timeout_s or self.timeout_s,
                    )
                    out = check(resp) if check else resp
                    log.info("Gemini %s answered by %s in %.1fs", what, model, time.monotonic() - t0)
                    return out
                except BadResponse as e:
                    last = e
                    log.warning("Gemini %s unusable response from %s: %s", what, model, e)
                except asyncio.TimeoutError as e:
                    last = e
                    log.warning("Gemini %s timed out on %s after %.1fs", what, model, time.monotonic() - t0)
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
        max_tokens: int = 1024,
    ) -> str:
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            max_output_tokens=max_tokens,
            thinking_config=types.ThinkingConfig(thinking_level="low"),
        )
        models = self.fast_models if fast else self.answer_models
        def check(resp):
            _warn_if_truncated(resp)
            return _check_text(resp)

        return await self._generate(models, _contents(history or [], user_text), cfg, "generate",
                                    timeout_s=self.fast_timeout_s if fast else None, check=check)

    async def generate_json(self, system: str, user_text: str, schema: dict, *, fast: bool = True) -> dict:
        cfg = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.0,
            response_mime_type="application/json",
            response_json_schema=schema,
            thinking_config=types.ThinkingConfig(thinking_level="low"),
        )
        models = self.fast_models if fast else self.answer_models
        return await self._generate(models, user_text, cfg, "json", timeout_s=self.fast_timeout_s if fast else None,
                                    check=_check_json)

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

"""Free web search: self-hosted SearXNG (JSON API) first, DuckDuckGo via `ddgs` as fallback.

Search results are untrusted third-party text. Callers must treat titles/snippets as data to
summarise, never as instructions (see WEB_SNIPPET_SYSTEM in app/pipeline.py).
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)

MAX_RESULTS = 8
SNIPPET_CHARS = 400
TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")
# Official / research sources first (Indian government, state portals, ICAR, agri universities)
TRUSTED_SUFFIXES = (".gov.in", ".nic.in", ".icar.gov.in", ".icar.org.in", ".res.in", ".ac.in", ".edu.in")
TRUSTED_HOSTS = ("igkv.ac.in", "pib.gov.in", "farmer.gov.in", "vikaspedia.in", "pmkisan.gov.in", "pmfby.gov.in")
BLOCKED_HOSTS = ("youtube.com", "facebook.com", "instagram.com", "x.com", "twitter.com", "pinterest.com", "quora.com")


@dataclass
class WebResult:
    title: str
    url: str
    snippet: str
    engine: str = ""

    @property
    def host(self) -> str:
        return (urlparse(self.url).hostname or "").lower().removeprefix("www.")


def clean(text: str | None, limit: int = SNIPPET_CHARS) -> str:
    text = WS.sub(" ", html.unescape(TAG.sub(" ", text or ""))).strip()
    return text[:limit]


def is_trusted(host: str) -> bool:
    return host in TRUSTED_HOSTS or host.endswith(TRUSTED_SUFFIXES)


def rank(results: list[WebResult], limit: int = MAX_RESULTS) -> list[WebResult]:
    """Drop junk/duplicates, keep http(s) only, put official sources first (stable otherwise)."""
    seen, out = set(), []
    for r in results:
        if not r.url.lower().startswith(("http://", "https://")) or not (r.title or r.snippet):
            continue
        host = r.host
        if any(host == b or host.endswith("." + b) for b in BLOCKED_HOSTS):
            continue
        key = r.url.split("#")[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    out.sort(key=lambda r: 0 if is_trusted(r.host) else 1)
    return out[:limit]


class SearxngClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str, timeout_s: float = 6.0):
        self.client, self.base_url, self.timeout_s = client, base_url.rstrip("/"), timeout_s

    async def search(self, query: str, lang: str = "hi-en") -> list[WebResult]:
        params = {"q": query, "format": "json", "language": "hi-IN" if lang == "hi-IN" else "en-IN", "safesearch": 1}
        r = await self.client.get(f"{self.base_url}/search", params=params, timeout=self.timeout_s)
        r.raise_for_status()
        data = r.json()
        return [
            WebResult(clean(x.get("title"), 160), x.get("url", ""), clean(x.get("content")), x.get("engine", ""))
            for x in data.get("results") or []
        ]


class DdgsClient:
    """DuckDuckGo et al. through the `ddgs` package (sync), run in a thread with a hard timeout."""

    def __init__(self, timeout_s: float = 10.0):
        self.timeout_s = timeout_s

    def _search_sync(self, query: str) -> list[WebResult]:
        from ddgs import DDGS  # imported lazily: optional dependency
        rows = DDGS(timeout=int(self.timeout_s)).text(query, region="in-en", max_results=MAX_RESULTS)
        return [WebResult(clean(x.get("title"), 160), x.get("href", ""), clean(x.get("body")), "ddgs") for x in rows or []]

    async def search(self, query: str, lang: str = "hi-en") -> list[WebResult]:
        return await asyncio.wait_for(asyncio.to_thread(self._search_sync, query), self.timeout_s + 2)


class WebSearch:
    """Try each backend in order; return ranked results from the first one that has any."""

    def __init__(self, backends: list):
        self.backends = backends

    async def search(self, query: str, lang: str = "hi-en") -> list[WebResult]:
        for b in self.backends:
            name = type(b).__name__
            try:
                results = rank(await b.search(query, lang))
            except Exception as e:  # noqa: BLE001  (network, timeout, parse, rate limit)
                log.warning("web search %s failed: %s: %s", name, e.__class__.__name__, str(e)[:120])
                continue
            if results:
                log.info("web search %s: %d results for %r", name, len(results), query[:60])
                return results
            log.info("web search %s: no results for %r", name, query[:60])
        return []

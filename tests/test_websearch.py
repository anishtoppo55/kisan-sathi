import httpx
import respx

from app.clients.websearch import DdgsClient, SearxngClient, WebResult, WebSearch, clean, rank

SX = "http://127.0.0.1:8888"


def test_clean_strips_html_and_limits():
    assert clean("<b>Maize</b> &amp; <i>wheat</i>\n\n sowing") == "Maize & wheat sowing"
    assert len(clean("x" * 1000)) == 400


def test_rank_trusted_first_dedup_and_blocked():
    rs = [
        WebResult("Blog", "https://blog.example.com/a", "s"),
        WebResult("Video", "https://www.youtube.com/watch?v=1", "s"),
        WebResult("IGKV", "https://igkv.ac.in/maize", "s"),
        WebResult("dup", "https://blog.example.com/a/", "s"),
        WebResult("Agri dept", "https://agriportal.cg.nic.in/x", "s"),
        WebResult("bad", "javascript:alert(1)", "s"),
        WebResult("", "https://empty.example.com", ""),
    ]
    out = [r.url for r in rank(rs)]
    assert out == ["https://igkv.ac.in/maize", "https://agriportal.cg.nic.in/x", "https://blog.example.com/a"]


@respx.mock
async def test_searxng_request_and_parse():
    route = respx.get(f"{SX}/search").mock(return_value=httpx.Response(200, json={"results": [
        {"title": "Maize <b>guide</b>", "url": "https://icar.org.in/m", "content": "Sow in June", "engine": "google"}]}))
    async with httpx.AsyncClient() as c:
        out = await SearxngClient(c, SX + "/").search("makka", "hi-IN")
    assert out == [WebResult("Maize guide", "https://icar.org.in/m", "Sow in June", "google")]
    p = route.calls.last.request.url.params
    assert p["format"] == "json" and p["q"] == "makka" and p["language"] == "hi-IN"


class Boom:
    async def search(self, q, lang="hi-en"):
        raise httpx.ConnectError("searxng down")


class Static:
    def __init__(self, results):
        self.results, self.called = results, 0

    async def search(self, q, lang="hi-en"):
        self.called += 1
        return self.results


async def test_websearch_falls_back_to_next_backend():
    good = Static([WebResult("t", "https://pib.gov.in/x", "s")])
    out = await WebSearch([Boom(), Static([]), good]).search("q")
    assert out[0].url == "https://pib.gov.in/x" and good.called == 1


async def test_websearch_all_fail_returns_empty():
    assert await WebSearch([Boom(), Static([])]).search("q") == []


async def test_ddgs_timeout_is_enforced(monkeypatch):
    import time

    d = DdgsClient(timeout_s=0.05)
    monkeypatch.setattr(d, "_search_sync", lambda q: time.sleep(2.5) or [])
    t = time.monotonic()
    out = await WebSearch([d]).search("q")
    assert out == [] and time.monotonic() - t < 2.4  # gave up before the backend finished


async def test_ddgs_maps_fields(monkeypatch):
    d = DdgsClient()
    monkeypatch.setattr(d, "_search_sync", lambda q: [WebResult("T", "https://a.in", "b", "ddgs")])
    assert (await d.search("q"))[0].engine == "ddgs"

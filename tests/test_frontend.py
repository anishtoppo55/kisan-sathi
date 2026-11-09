"""Frontend contract: the redesigned page keeps the IDs app.js needs and serves its assets."""

import re

import pytest

REQUIRED_IDS = ["messages", "chat-form", "chat-input", "send-btn", "mic-btn", "cards", "chips"]


def test_index_has_required_ids_and_badge(client):
    html = client.get("/").text
    for i in REQUIRED_IDS:
        assert re.search(rf'id="{i}"', html), f"missing #{i}"
    assert "Powered by Gnani Prisma v3.0" in html and "Timbre v3.0" in html
    assert 'name="viewport"' in html
    assert "pico" not in html.lower()  # Pico CSS removed
    assert "Noto+Sans+Devanagari" in html  # Devanagari font loaded


def test_icon_only_buttons_are_labelled(client):
    html = client.get("/").text
    for tag in re.findall(r"<button[^>]*>", html):
        if 'id="send-btn"' in tag or 'id="mic-btn"' in tag or 'class="tab' in tag:
            assert "aria-label=" in tag, tag


def test_illustration_served_as_svg(client):
    r = client.get("/static/illustration.svg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert "<title" in r.text


@pytest.mark.parametrize("path", ["/static/app.js", "/static/style.css", "/static/vendor/chart.umd.min.js"])
def test_static_assets_served(client, path):
    assert client.get(path).status_code == 200


def test_chart_js_is_self_hosted(client):
    html = client.get("/").text
    assert "/static/vendor/chart.umd.min.js" in html and "cdn.jsdelivr" not in html


def test_app_js_never_uses_innerhtml(client):
    js = client.get("/static/app.js").text
    assert "innerHTML" not in js
    for i in REQUIRED_IDS:
        assert f'"{i}"' in js


def _strings_tables(js: str) -> dict[str, set[str]]:
    """Keys of STRINGS["hi-en"] and STRINGS["hi-IN"] in app.js (parsed by regex; no JS engine needed)."""
    block = js[js.index("const STRINGS = {"): js.index("const UI_LANGS")]
    tables = {}
    for lang in ("hi-en", "hi-IN"):
        start = block.index(f'"{lang}": {{')
        end = block.index("\n  },", start)
        tables[lang] = set(re.findall(r'"([a-zA-Z0-9_.]+)":\s*"', block[start:end]))
    return tables


def test_i18n_keys_complete_in_both_languages(client):
    html = client.get("/").text
    js = client.get("/static/app.js").text
    tables = _strings_tables(js)
    assert tables["hi-en"] == tables["hi-IN"], tables["hi-en"] ^ tables["hi-IN"]
    used = set(re.findall(r'data-i18n="([^"]+)"', html))
    for attr in re.findall(r'data-i18n-attr="([^"]+)"', html):
        used |= {pair.split(":")[1] for pair in attr.split(",")}
    used |= set(re.findall(r'\bt\("([a-zA-Z0-9_.]+)"\)', js))
    missing = used - tables["hi-en"]
    assert not missing, missing
    assert len(used) > 40


def test_language_toggle_present_and_sent_with_requests(client):
    html = client.get("/").text
    assert 'data-ui-lang="hi-IN"' in html and 'data-ui-lang="hi-en"' in html and "हिंदी" in html
    js = client.get("/static/app.js").text
    assert 'fd.append("lang", uiLang)' in js  # voice
    assert js.count("lang: uiLang") >= 2  # chat + eligibility

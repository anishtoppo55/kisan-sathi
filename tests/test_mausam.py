import httpx
import pytest
import respx

from app.clients.weather import FORECAST_URL, GEOCODE_URL, DayForecast, Place, WeatherClient, WeatherError
from app.modules.mausam import MausamSaathi, advisories, advisory_text, weather_card


def days(rain=(0, 0, 0, 0, 0, 0, 0), tmin=15, tmax=28, humidity=60, cloud=10):
    return [DayForecast(f"2026-11-{9 + i:02d}", rain[i], 0, tmax, tmin, humidity, cloud) for i in range(7)]


def kinds(advs):
    return [a["kind"] for a in advs]


def test_rain_over_10mm_in_48h_postpones():
    a = advisories(days(rain=(6, 5, 0, 0, 0, 0, 0)))
    assert kinds(a)[0] == "rain" and a[0]["mm"] == 11
    assert kinds(advisories(days(rain=(5, 4, 30, 0, 0, 0, 0))))[0] == "dry"  # day 3 rain not counted


def test_frost_only_for_rabi_crops():
    d = days(tmin=6)
    assert "frost" in kinds(advisories(d, "chana"))
    assert "frost" in kinds(advisories(d, None))
    assert "frost" not in kinds(advisories(d, "paddy"))
    assert "frost" not in kinds(advisories(days(tmin=9), "wheat"))


def test_heat_for_wheat_flowering():
    d = days(tmax=37)
    assert "heat" in kinds(advisories(d, "wheat", "flowering"))
    assert "heat" not in kinds(advisories(d, "wheat", "sowing"))
    assert "heat" not in kinds(advisories(d, "chana", "flowering"))


def test_humid_cloudy_disease_watch():
    assert "humid" in kinds(advisories(days(humidity=85, cloud=70), "paddy"))
    assert "humid" not in kinds(advisories(days(humidity=85, cloud=20), "paddy"))


def test_advisory_text_and_card():
    d = days(rain=(8, 4, 0, 0, 0, 0, 0), tmin=6)
    advs = advisories(d, "chana")
    assert "12" in advisory_text(advs[0], "hi-en")
    assert "पाले" in advisory_text(advs[1], "hi-IN") and "09/11" in advisory_text(advs[1], "hi-IN")
    card = weather_card("Bemetara", d, advs, "hi-en")
    assert card["type"] == "weather" and len(card["data"]["days"]) == 7
    assert card["data"]["advisories"][1]["level"] == "alert"


GEO_OK = {"results": [
    {"name": "Bemetara", "admin1": "Madhya Pradesh", "latitude": 1, "longitude": 1},
    {"name": "Bemetāra", "admin1": "Chhattisgarh", "latitude": 21.71, "longitude": 81.53},
]}
FC_OK = {"daily": {"time": [f"2026-11-{9 + i:02d}" for i in range(7)], "precipitation_sum": [0, 12, 0, 0, 0, 0, None],
                   "precipitation_probability_max": [0] * 7, "temperature_2m_max": [28] * 7, "temperature_2m_min": [15] * 7,
                   "relative_humidity_2m_mean": [60] * 7, "cloud_cover_mean": [0] * 7}}


@respx.mock
async def test_locate_prefers_chhattisgarh_and_caches():
    route = respx.get(GEOCODE_URL).mock(return_value=httpx.Response(200, json=GEO_OK))
    async with httpx.AsyncClient() as c:
        w = WeatherClient(c)
        p = await w.locate("Bemetara")
        await w.locate("bemetara")
    assert (p.lat, p.lon) == (21.71, 81.53) and route.call_count == 1


@respx.mock
async def test_locate_falls_back_to_table():
    respx.get(GEOCODE_URL).mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as c:
        p = await WeatherClient(c).locate("Durg")
        q = await WeatherClient(c).locate("Nowhere")
    assert p.name == "Durg" and round(p.lat, 2) == 21.19
    assert q.name == "Raipur"


@respx.mock
async def test_forecast_parses_and_caches_30min():
    route = respx.get(FORECAST_URL).mock(return_value=httpx.Response(200, json=FC_OK))
    now = [0.0]
    async with httpx.AsyncClient() as c:
        w = WeatherClient(c, clock=lambda: now[0])
        d = await w.forecast(Place("X", 21.7, 81.5))
        await w.forecast(Place("X", 21.7, 81.5))
        now[0] = 1801
        await w.forecast(Place("X", 21.7, 81.5))
    assert len(d) == 7 and d[1].rain_mm == 12 and d[6].rain_mm == 0
    assert route.call_count == 2
    assert route.calls[0].request.url.params["timezone"] == "Asia/Kolkata"


@respx.mock
async def test_forecast_error():
    respx.get(FORECAST_URL).mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as c:
        with pytest.raises(WeatherError):
            await WeatherClient(c).forecast(Place("X", 1, 1))


@respx.mock
async def test_mausam_handle_uses_rules_in_prompt():
    respx.get(GEOCODE_URL).mock(return_value=httpx.Response(200, json=GEO_OK))
    respx.get(FORECAST_URL).mock(return_value=httpx.Response(200, json=FC_OK))

    class G:
        system = None

        async def generate(self, system, text, history=None, **kw):
            G.system = system
            return "Agle 2 din baarish hai, sinchai na karein."

    async with httpx.AsyncClient() as c:
        r = await MausamSaathi(WeatherClient(c), G()).handle(
            "Bemetara mein barish? gehun mein paani doon?", {"district": "Bemetara", "crop": "wheat"}, "hi-en", [], None)
    assert r.cards[0]["type"] == "weather" and r.cards[0]["data"]["place"] == "Bemetāra"
    assert "12" in G.system and "taalein" in G.system and "wheat" in G.system

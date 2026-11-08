"""Open-Meteo forecast + geocoding client (free, no API key)."""

from __future__ import annotations

import time
import unicodedata
from dataclasses import dataclass

import httpx

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DAILY = "precipitation_sum,precipitation_probability_max,temperature_2m_max,temperature_2m_min,relative_humidity_2m_mean,cloud_cover_mean"

# District HQ coordinates (fallback when geocoding fails or for the default location)
DISTRICTS: dict[str, tuple[float, float]] = {
    "raipur": (21.2514, 81.6296), "bilaspur": (22.0797, 82.1409), "durg": (21.1904, 81.2849),
    "bhilai": (21.2092, 81.4285), "rajnandgaon": (21.0974, 81.0337), "bemetara": (21.7156, 81.5342),
    "dhamtari": (20.7071, 81.5497), "mahasamund": (21.1091, 82.0979), "kanker": (20.2719, 81.4917),
    "jagdalpur": (19.0748, 82.0080), "bastar": (19.0748, 82.0080), "korba": (22.3595, 82.7501),
    "raigarh": (21.8974, 83.3950), "janjgir": (22.0091, 82.5770), "ambikapur": (23.1186, 83.1958),
    "surguja": (23.1186, 83.1958), "kawardha": (22.0080, 81.2310), "kabirdham": (22.0080, 81.2310),
    "mungeli": (22.0656, 81.6857), "balod": (20.7307, 81.2047), "baloda bazar": (21.6570, 82.1600),
    "gariaband": (20.6347, 82.0617), "kondagaon": (19.5960, 81.6640), "dantewada": (18.8967, 81.3496),
    "sukma": (18.3910, 81.6590), "bijapur": (18.7950, 80.8160), "narayanpur": (19.7200, 81.2500),
    "jashpur": (22.8850, 84.1400), "koriya": (23.2500, 82.5600), "balrampur": (23.6100, 83.6100),
    "surajpur": (23.2200, 82.8700), "gaurela": (22.7570, 81.9050), "sakti": (22.0270, 82.9620),
}
DEFAULT_DISTRICT = "raipur"


@dataclass
class Place:
    name: str
    lat: float
    lon: float


@dataclass
class DayForecast:
    date: str
    rain_mm: float
    rain_prob: int | None
    tmax: float
    tmin: float
    humidity: float | None
    cloud: float | None


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return s.strip().lower()


class WeatherError(Exception):
    pass


class WeatherClient:
    def __init__(self, client: httpx.AsyncClient, ttl_s: float = 1800, clock=time.monotonic):
        self.client = client
        self.ttl_s = ttl_s
        self.clock = clock
        self._cache: dict[str, tuple[float, list[DayForecast]]] = {}
        self._places: dict[str, Place] = {}

    async def locate(self, district: str | None) -> Place:
        """Geocode within Chhattisgarh; fall back to the district table, then Raipur."""
        key = _norm(district or DEFAULT_DISTRICT)
        if key in self._places:
            return self._places[key]
        place = None
        try:
            r = await self.client.get(GEOCODE_URL, params={"name": district or DEFAULT_DISTRICT, "count": 10,
                                                           "language": "en", "countryCode": "IN"}, timeout=8.0)
            r.raise_for_status()
            for res in r.json().get("results") or []:
                if res.get("admin1") == "Chhattisgarh":
                    place = Place(res["name"], res["latitude"], res["longitude"])
                    break
        except (httpx.HTTPError, ValueError, KeyError):
            place = None
        if place is None:
            lat, lon = DISTRICTS.get(key, DISTRICTS[DEFAULT_DISTRICT])
            place = Place((district or DEFAULT_DISTRICT).title() if key in DISTRICTS else "Raipur", lat, lon)
        self._places[key] = place
        return place

    async def forecast(self, place: Place) -> list[DayForecast]:
        key = f"{place.lat:.2f},{place.lon:.2f}"
        hit = self._cache.get(key)
        if hit and self.clock() - hit[0] < self.ttl_s:
            return hit[1]
        try:
            r = await self.client.get(FORECAST_URL, params={
                "latitude": place.lat, "longitude": place.lon, "daily": DAILY,
                "timezone": "Asia/Kolkata", "forecast_days": 7}, timeout=10.0)
            r.raise_for_status()
            d = r.json()["daily"]
            days = [
                DayForecast(
                    date=d["time"][i],
                    rain_mm=float(d["precipitation_sum"][i] or 0),
                    rain_prob=d.get("precipitation_probability_max", [None] * 7)[i],
                    tmax=float(d["temperature_2m_max"][i]),
                    tmin=float(d["temperature_2m_min"][i]),
                    humidity=d.get("relative_humidity_2m_mean", [None] * 7)[i],
                    cloud=d.get("cloud_cover_mean", [None] * 7)[i],
                )
                for i in range(len(d["time"]))
            ]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            raise WeatherError(f"forecast failed: {e.__class__.__name__}") from e
        self._cache[key] = (self.clock(), days)
        return days

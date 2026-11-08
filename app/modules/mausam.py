"""Mausam Saathi: forecast + rule-based advisories, phrased by Gemini.

Rules (pure functions, unit-tested):
  rain > 10 mm in next 48 h        -> postpone irrigation and spraying
  min temp < 8°C                   -> frost precautions for chana / wheat / teora
  max temp > 35°C (wheat flowering) -> heat stress care
  humidity >= 80% with cloud >= 60% -> blast (paddy) / pod borer & disease (chana) watch
"""

from __future__ import annotations

import logging

from app.clients.weather import DayForecast, WeatherClient, WeatherError

log = logging.getLogger(__name__)

RAIN_48H_MM = 10.0
FROST_C = 8.0
HEAT_C = 35.0
HUMID_PCT = 80.0
CLOUD_PCT = 60.0

ADVICE = {
    "rain": {
        "hi-IN": "अगले 2 दिन में {mm} मिमी बारिश की संभावना — सिंचाई और दवा का छिड़काव टालें।",
        "hi-en": "Agle 2 din mein {mm} mm baarish ki sambhavna — sinchai aur dawa ka chhidkaav taalein.",
    },
    "dry": {
        "hi-IN": "अगले 2 दिन बारिश नहीं — ज़रूरत हो तो सिंचाई और छिड़काव कर सकते हैं।",
        "hi-en": "Agle 2 din baarish nahi — zaroorat ho to sinchai aur chhidkaav kar sakte hain.",
    },
    "frost": {
        "hi-IN": "{date} को न्यूनतम तापमान {t}°C — पाले का खतरा। चना, गेहूँ, तिवड़ा में शाम को हल्की सिंचाई करें, रात में खेत की मेड़ पर धुआँ करें।",
        "hi-en": "{date} ko nyuntam taapmaan {t}°C — paale ka khatra. Chana, gehun, teora mein shaam ko halki sinchai karein, raat mein med par dhuan karein.",
    },
    "heat": {
        "hi-IN": "{date} को अधिकतम तापमान {t}°C — फूल/दाना भरने के समय गेहूँ में शाम को हल्की सिंचाई करें।",
        "hi-en": "{date} ko adhiktam taapmaan {t}°C — phool/dana bharne ke samay gehun mein shaam ko halki sinchai karein.",
    },
    "humid": {
        "hi-IN": "नमी और बादल ज़्यादा — धान में झुलसा (blast) और चने में इल्ली/रोग पर नज़र रखें।",
        "hi-en": "Nami aur baadal zyada — dhaan mein blast aur chane mein ilii/rog par nazar rakhein.",
    },
}

SYSTEM = """You are Kisan Saathi, a farm weather advisor for Chhattisgarh farmers.
- {lang_rule}
- Your answer is read aloud: 2-4 short sentences, no markdown, lists, emojis or URLs.
- Use ONLY the forecast numbers and the ADVISORIES given. Do not add pesticide names or doses.
- Answer the farmer's actual question first (e.g. whether to irrigate), then the most important advisory.

PLACE: {place}
FORECAST (next 7 days): {forecast}
ADVISORIES: {advisories}
FARMER'S CROP: {crop}"""


def _day_label(date: str) -> str:
    return date[8:10] + "/" + date[5:7]


def advisories(days: list[DayForecast], crop: str | None = None, stage: str | None = None) -> list[dict]:
    """Return [{kind, level, values}] from the forecast. Pure function."""
    out: list[dict] = []
    if not days:
        return out
    rain48 = round(sum(d.rain_mm for d in days[:2]), 1)
    out.append({"kind": "rain", "level": "warn", "mm": rain48} if rain48 > RAIN_48H_MM else {"kind": "dry", "level": "ok", "mm": rain48})

    cold = min(days, key=lambda d: d.tmin)
    if cold.tmin < FROST_C and crop in (None, "chana", "wheat", "teora"):
        out.append({"kind": "frost", "level": "alert", "t": round(cold.tmin, 1), "date": cold.date})

    hot = max(days, key=lambda d: d.tmax)
    if hot.tmax > HEAT_C and crop in (None, "wheat") and stage in (None, "flowering", "maturity"):
        out.append({"kind": "heat", "level": "alert", "t": round(hot.tmax, 1), "date": hot.date})

    humid = [d for d in days[:3] if (d.humidity or 0) >= HUMID_PCT and (d.cloud or 0) >= CLOUD_PCT]
    if humid and crop in (None, "paddy", "chana"):
        out.append({"kind": "humid", "level": "warn"})
    return out


def advisory_text(a: dict, lang: str) -> str:
    tmpl = ADVICE[a["kind"]].get(lang) or ADVICE[a["kind"]]["hi-en"]
    return tmpl.format(mm=a.get("mm"), t=a.get("t"), date=_day_label(a["date"]) if a.get("date") else "")


def weather_card(place: str, days: list[DayForecast], advs: list[dict], lang: str) -> dict:
    return {
        "type": "weather",
        "data": {
            "place": place,
            "days": [{"date": d.date, "label": _day_label(d.date), "rain_mm": d.rain_mm, "rain_prob": d.rain_prob,
                      "tmax": d.tmax, "tmin": d.tmin} for d in days],
            "advisories": [{"level": a["level"], "text": advisory_text(a, lang)} for a in advs],
        },
    }


def forecast_summary(days: list[DayForecast]) -> str:
    return "; ".join(f"{d.date}: rain {d.rain_mm} mm (prob {d.rain_prob}%), max {d.tmax}°C, min {d.tmin}°C, humidity {d.humidity}%" for d in days)


class MausamSaathi:
    def __init__(self, weather: WeatherClient, gemini):
        self.weather = weather
        self.gemini = gemini

    async def handle(self, text: str, route: dict, lang: str, history, profile):
        from app.pipeline import FALLBACK_REPLY, PipelineResult, _l, lang_rule  # avoid import cycle

        district = route.get("district") or getattr(profile, "district", None)
        crop, stage = route.get("crop"), route.get("stage")
        try:
            place = await self.weather.locate(district)
            days = await self.weather.forecast(place)
        except WeatherError as e:
            log.warning("mausam: %s", e)
            return PipelineResult(_l(FALLBACK_REPLY, lang))
        advs = advisories(days, crop, stage)
        adv_lines = [advisory_text(a, lang) for a in advs]
        system = SYSTEM.format(lang_rule=lang_rule(lang), place=place.name, forecast=forecast_summary(days),
                               advisories=" | ".join(adv_lines), crop=crop or "not stated")
        reply = await self.gemini.generate(system, text, history, temperature=0.2)
        return PipelineResult(reply, cards=[weather_card(place.name, days, advs, lang)])

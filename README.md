# Kisan Saathi CG 🌾

Voice-first farm assistant for Chhattisgarh farmers, in Hindi, Hinglish and English.
Built for the Gnani AI *Great Indian AI Internship Challenge* by Anish Deep Toppo (B.Tech Data Science & AI, IIIT Naya Raipur).

- **Live app:** https://kisan-saathi.duckdns.org/ (open on a phone and tap the mic)
- **Demo video:** https://youtu.be/phamRC-49Hc
- **Pitch slides:** [assets/pitch_1_what.png](assets/pitch_1_what.png) · [assets/pitch_2_how.png](assets/pitch_2_how.png) · [PDF](assets/kisan_saathi_pitch.pdf)
- **Video files:** [assets/kisan_saathi_demo.mp4](assets/kisan_saathi_demo.mp4) (2.5 min, desktop) · [assets/kisan_saathi_mobile_60s.mp4](assets/kisan_saathi_mobile_60s.mp4) (58 s, phone)

| Module | What it does |
|---|---|
| **Yojana Saathi** | Answers scheme questions (PM-Kisan, Krishak Unnati / dhaan kharidi, PMFBY, KCC, Soil Health Card) from a curated knowledge base, and checks eligibility from what the farmer says or an editable form. |
| **Crop Saathi** | Stage-specific advice for paddy, chana, wheat, teora and maize, plus a month-by-month sowing calendar (pests, diseases, irrigation, fertilizer), with doses taken only from ICAR/NCIPM sources. |
| **Mausam Saathi** | 7-day forecast for the farmer's district with rule-based advisories (postpone irrigation before rain, frost, heat, disease weather). |

**Stack:** Gnani **Prisma v3.0** (speech-to-text, `indic-auto`) · Gnani **Timbre v3.0** (text-to-speech, male voice Yashraj; v2.5 Deepak/Pranav fallback, set `TTS_VOICE` in `.env` to change) ·
Gemini Flash-Lite (routing, answers, embeddings) · SearXNG + ddgs (free web search) · FastAPI · Open-Meteo · plain HTML/CSS/JS + Chart.js.

```
Browser (mic / chat / cards) ──► FastAPI ──► ffmpeg ─► Prisma v3.0 (STT)
                                   │
                                   ├─► Router (Gemini flash-lite JSON, keyword fallback)
                                   │     ├─ yojana      → RAG over kb/schemes  + scheme card
                                   │     ├─ eligibility → profile extraction + rules (kb/schemes.json)
                                   │     ├─ crop        → RAG over kb/crops (crop/stage filters) + crop card
                                   │     └─ mausam      → Open-Meteo + advisory rules + weather chart
                                   │   KB miss (score < 0.70 or NO_ANSWER) → SearXNG / DuckDuckGo search → Gemini summary with sources
                                   │     → else a labelled "general information, confirm with KVK" answer (no doses)
                                   └─► Timbre (TTS) ─► MP3 back to the browser
```

## Run locally
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # put GNANI_API_KEY and GEMINI_API_KEY in it
# optional, free web search for questions outside the knowledge base:
docker run -d --name searxng -p 127.0.0.1:8888:8080 \
  -v "$PWD/deploy/searxng/settings.yml:/etc/searxng/settings.yml:ro" searxng/searxng:latest
env -u PYTHONPATH .venv/bin/uvicorn app.main:app --reload
# open http://localhost:8000   (mic works on localhost; elsewhere it needs HTTPS)
```
`ffmpeg` must be installed (browser audio is converted to 16 kHz WAV for Prisma).
Without SearXNG the app falls back to DuckDuckGo (`ddgs`) and then to a labelled general answer.

## Scripts
| Command | Purpose |
|---|---|
| `scripts/test.sh` | Unit tests (all external APIs mocked) |
| `.venv/bin/python scripts/ingest.py` | Rebuild `kb/index/` after editing any `kb/**/*.md` (commit the result) |
| `.venv/bin/python scripts/smoke_gnani.py` | Real Timbre → Prisma round trip (uses a few Gnani credits) |
| `.venv/bin/python scripts/eval_pipeline.py` | Real end-to-end answers for the demo questions (Gemini + Open-Meteo) |

Prefix with `env -u PYTHONPATH` if a system PYTHONPATH (e.g. ROS) is set.

## Knowledge base
- `kb/schemes/*.md`: one file per scheme; front matter records the source and what was verified.
- `kb/schemes.json`: scheme cards and eligibility rules (fail → not eligible, unknown → needs check).
- `kb/crops/*.md`: per-crop guides; each `##` section carries `<!-- stage: …; topic: … -->` tags used for filtering.

Every advisory tells the farmer to confirm with the local KVK / RAEO. Web-search answers are labelled and list their sources.

## Language
The **हिंदी | Hinglish** toggle (top bar) switches the page text and the reply language for chat, voice and the eligibility re-check.
Page strings live in the `STRINGS` table in `app/static/app.js` (`data-i18n` keys in `index.html`); a test checks both languages have every key.

## Deploy
See [deploy/README.md](deploy/README.md) (AWS Lightsail + Caddy HTTPS on `<ip>.sslip.io`).

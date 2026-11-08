# Kisan Saathi CG 🌾

Voice-first farm assistant for Chhattisgarh farmers, in Hindi, Hinglish and English.
Built for the Gnani AI *Great Indian AI Internship Challenge*.

| Module | What it does |
|---|---|
| **Yojana Saathi** | Answers scheme questions (PM-Kisan, Krishak Unnati / dhaan kharidi, PMFBY, KCC, Soil Health Card) from a curated knowledge base, and checks eligibility from what the farmer says or an editable form. |
| **Crop Saathi** | Stage-specific advice for paddy, chana, wheat and teora (pests, diseases, irrigation, fertilizer), with doses taken only from ICAR/NCIPM sources. |
| **Mausam Saathi** | 7-day forecast for the farmer's district with rule-based advisories (postpone irrigation before rain, frost, heat, disease weather). |

**Stack:** Gnani **Prisma v3.0** (speech-to-text, `indic-auto`) · Gnani **Timbre v3.0 / v2.5** (text-to-speech) ·
Gemini (routing, answers, embeddings, Google Search fallback) · FastAPI · Open-Meteo · plain HTML/JS + Pico CSS + Chart.js.

```
Browser (mic / chat / cards) ──► FastAPI ──► ffmpeg ─► Prisma v3.0 (STT)
                                   │
                                   ├─► Router (Gemini flash-lite JSON, keyword fallback)
                                   │     ├─ yojana      → RAG over kb/schemes  + scheme card
                                   │     ├─ eligibility → profile extraction + rules (kb/schemes.json)
                                   │     ├─ crop        → RAG over kb/crops (crop/stage filters) + crop card
                                   │     └─ mausam      → Open-Meteo + advisory rules + weather chart
                                   │   KB miss (score < 0.70 or NO_ANSWER) → Gemini + Google Search, with sources
                                   └─► Timbre (TTS) ─► MP3 back to the browser
```

## Run locally
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # put GNANI_API_KEY and GEMINI_API_KEY in it
env -u PYTHONPATH .venv/bin/uvicorn app.main:app --reload
# open http://localhost:8000   (mic works on localhost; elsewhere it needs HTTPS)
```
`ffmpeg` must be installed (browser audio is converted to 16 kHz WAV for Prisma).

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

## Deploy
See [deploy/README.md](deploy/README.md) (AWS Lightsail + Caddy HTTPS on `<ip>.sslip.io`).

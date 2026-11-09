# Submission drafts (edit before posting)

## 60-second demo video script

| Time | On screen | Say / do |
|---|---|---|
| 0–7 s | Title card: "Kisan Saathi CG" + paddy field photo | "Chhattisgarh ke kisan ko yojana, fasal aur mausam ki sahi jaankari ek jagah, apni bhasha mein — bolkar." |
| 7–22 s | Phone, tap 🎤 | Say: **"Main 2 acre ka kisan hoon, income tax nahi bharta, KCC nahi hai."** Show transcript, spoken reply, eligibility card. Toggle one field in the form → "Dobara jaanchein". |
| 22–32 s | Tap 🎤 | Say: **"धान बेचने के लिए टोकन कैसे मिलेगा?"** Show the Hindi answer and the Krishak Unnati card. |
| 32–44 s | Tap 🎤 | Say: **"Dhaan mein bhura maahu lag gaya hai kya karun?"** Show crop card with ETL, dose from NCIPM and KVK reminder. |
| 44–54 s | Tap 🎤 | Say: **"Bemetara mein agle hafte barish hogi? Gehun mein paani doon?"** Show the 7-day chart and advisories. |
| 54–60 s | Architecture slide | "Gnani Prisma v3.0 sunta hai, Timbre bolta hai, Gemini samajhta hai. Link post mein." |

Tips: record on the phone over the public HTTPS URL; keep each question under 10 seconds; turn the volume up so the Timbre voice is audible.

## LinkedIn / X post

> 🌾 Built **Kisan Saathi CG**: a voice assistant for Chhattisgarh farmers, in Hindi & Hinglish.
>
> 🎤 A farmer speaks → **Gnani Prisma v3.0** transcribes (auto language detection, code-mixed speech) → Gemini routes and answers from a curated knowledge base → **Gnani Timbre** speaks the answer back.
>
> ✅ **Yojana Saathi**: "Mujhe kaun si yojana milegi?" Checks PM-Kisan, Krishak Unnati, Fasal Bima, KCC and Soil Health Card eligibility from what you say.
> 🌱 **Crop Saathi**: stage-wise advice for dhaan, chana, gehun and teora, with doses only from ICAR/NCIPM sources.
> 🌦️ **Mausam Saathi**: 7-day forecast for your district, plus "sinchai karein ya nahi" advice.
>
> Try it: <PUBLIC URL>
> Demo 👇
>
> @Gnani.ai #GnaniAI #GreatIndianAIInternshipChallenge

(For X, shorten to: "🌾 Kisan Saathi CG: voice assistant for Chhattisgarh farmers in Hindi/Hinglish. Gnani Prisma v3.0 listens, Timbre speaks, Gemini reasons. Yojana eligibility, crop advice, mausam alerts. <URL> @GnaniAI #GnaniAI #GreatIndianAIInternshipChallenge")

## Written project explanation

**Title:** Kisan Saathi CG: voice assistant for Chhattisgarh farmers

**Problem.** Chhattisgarh farmers struggle to find out which schemes they qualify for (PM-Kisan exclusions, Krishak Unnati registration, paddy procurement tokens), to get crop advice for the current stage of their crop, and to turn weather forecasts into decisions like whether to irrigate. Existing portals are text-heavy and mostly in English.

**Users.** Small and marginal farmers in Chhattisgarh who are more comfortable speaking Hindi or Hinglish than reading forms; also RAEOs and cooperative society staff who answer the same questions daily.

**What it does.**
1. *Yojana Saathi*: answers scheme questions from a curated, source-checked knowledge base (RAG), and runs a rule-based eligibility check. The profile comes from speech ("2 acre, income tax nahi, KCC nahi") or from an editable form, and the result shows each scheme as eligible, needs check (with the question to ask) or not eligible (with the reason).
2. *Crop Saathi*: retrieves stage-specific guidance for paddy, chana, wheat and teora. Pesticide doses come only from the retrieved NCIPM/ICAR text, and every answer ends with a KVK/RAEO reminder.
3. *Mausam Saathi*: 7-day Open-Meteo forecast for the farmer's district, with deterministic rules (rain > 10 mm in 48 h → postpone irrigation/spray; min < 8 °C → frost care; etc.) that Gemini turns into a short spoken answer.
4. When the knowledge base has no good answer, it falls back to Gemini with Google Search grounding and shows the sources.

**Gnani AI technology used.**
- **Prisma v3.0** speech-to-text (REST, `language_code=indic-auto`). Its `detected_language_code` decides whether the reply is in Hindi (Devanagari) or Hinglish.
- **Timbre v3.0** text-to-speech with the male voice **Yashraj** for both Hindi (`hi-IN`) and Roman-script Hinglish (`auto`), with an automatic fallback to the male **Timbre v2.5** voices Deepak (Hindi/Hinglish) and Pranav (English).
- A **हिंदी | Hinglish** toggle switches the whole interface and every reply (typed or spoken) between Devanagari Hindi and Hinglish; crop-card advice from the English knowledge base is translated with chemical names and doses kept exactly.

**Other technology.** Gemini (intent routing, profile extraction, grounded answers, `gemini-embedding-2` for retrieval, Google Search grounding), FastAPI, Open-Meteo, AWS Lightsail with Caddy HTTPS.

**Contribution.** Solo: design, knowledge base curation, backend, frontend, deployment.

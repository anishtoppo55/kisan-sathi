"""Record a 2-3 minute demo video of the live Kisan Saathi site with Playwright.

* Farmer questions are generated with Gnani Timbre v3.0 (voice Devansh) and fed to the page's
  microphone through a getUserMedia override, so the app really runs Prisma STT -> Gemini -> Timbre.
* The intro / outro are narrated by the narrator voice: your Gnani cloned voice when an embedding
  (or a reference clip) is available, otherwise Timbre v3.0 Avinash.
* Frames come from Chrome's screencast (1920x1080 JPEG); audio (narration, farmer, app replies)
  is placed on a timeline and muxed with ffmpeg.

Usage:
  env -u PYTHONPATH .venv/bin/python scripts/make_demo.py            # record + build
  env -u PYTHONPATH .venv/bin/python scripts/make_demo.py --clips    # only (re)generate audio clips
Narrator voice clone: put ONE of these in .cache/demo/
  narrator_embedding.json   ({"embedding": "...", "shape": [1, 768], "dtype": "torch.bfloat16"})
  narrator_sample.wav|.mp3|.m4a   (10-30 s of clear speech; an embedding is created from it)
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MOBILE = __import__("os").environ.get("MOBILE") == "1"   # 60 s phone-layout cut
sys.path.insert(0, str(ROOT))

from app.clients.gnani import BASE_URL as GNANI, TTS_PATH, GnaniTTS, _error_message  # noqa: E402
from app.config import get_settings  # noqa: E402

SITE = "https://kisan-saathi.duckdns.org"
OUT = ROOT / ".cache" / "demo"
CLIPS = OUT / "clips"
FRAMES = OUT / "frames"
FINAL = ROOT / "assets" / ("kisan_saathi_mobile_60s.mp4" if MOBILE else "kisan_saathi_demo.mp4")
SR = 44100
MAX_REPLY_S = 12.0 if MOBILE else 14.0      # longer spoken replies fade out here and the rest is cut
KEEP_AFTER_CLIP_S = 1.2  # processing pause kept after the farmer stops speaking ("Sun raha hoon…")
KEEP_BEFORE_REPLY_S = 0.9
FARMER_VOICE = "Devansh"
NARRATOR_FALLBACK = "Avinash"

NARRATION = {
    "intro1": ("Namaste! Main Anish Deep Toppo hoon, IIIT Naya Raipur mein Data Science aur Artificial Intelligence "
               "mein B.Tech final year ka student."),
    "intro2": ("Kisan Saathi isliye banaya, taaki Chhattisgarh ke kisan, text ki jagah apni bhasha mein bolkar, "
               "bharosemand kheti ki jaankari paa sakein."),
    "intro3": "Kisan Saathi, Gnani Prisma se sunta hai, aur Gnani Timbre se jawab deta hai. Chaliye, dekhte hain.",
    "outro": "Yojana, fasal aur mausam. Ek jagah, apni bhasha mein. Kisan Saathi. Dhanyavaad!",
}

# (id, text spoken by the farmer, Timbre language, UI language to set before, caption)
SCENES = [
    ("q1", "Main do acre ka kisan hoon, income tax nahi bharta, KCC nahi hai. Mujhe kaun si yojana milegi?",
     "auto", "hi-en", "01 / Yojana Saathi — scheme eligibility, asked by voice in Hinglish"),
    ("q2", "धान बेचने के लिए टोकन कैसे मिलेगा?",
     "hi-IN", "hi-IN", "02 / Switch to हिंदी — paddy procurement token"),
    ("q3", "धान में भूरा माहू लग गया है, क्या करूँ?",
     "hi-IN", None, "03 / Crop Saathi — pest advice, doses only from ICAR / NCIPM"),
    ("q4", "बेमेतरा में अगले हफ्ते बारिश होगी? गेहूँ में पानी दूँ?",
     "hi-IN", None, "04 / Mausam Saathi — 7-day forecast + irrigation advice"),
    ("q5", "ड्रोन से दवा छिड़काव पर सब्सिडी मिलती है क्या?",
     "hi-IN", None, "05 / Not in the knowledge base — free web search, with sources"),
]

MOBILE_Q1 = ("q0", "पीएम किसान योजना के लिए कौन से दस्तावेज़ चाहिए?", "hi-IN", None,
             "01 / Yojana Saathi — PM-Kisan documents, asked by voice")
SCENES.append(MOBILE_Q1)  # clip generated with the others; desktop cut skips it

# ---------------------------------------------------------------- audio clips

def _key(*parts) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:10]


async def narrator_embedding(client: httpx.AsyncClient, key: str) -> dict | None:
    emb = OUT / "narrator_embedding.json"
    if emb.exists():
        data = json.loads(emb.read_text())
        return data.get("voice_clone_embedding", data)
    for ext in ("wav", "mp3", "m4a", "ogg", "webm"):
        sample = OUT / f"narrator_sample.{ext}"
        if sample.exists():
            r = await client.post(f"{GNANI}/api/v1/tts/voice-clone/embeddings", headers={"X-API-Key-ID": key},
                                  files={"audio_file": (sample.name, sample.read_bytes())}, timeout=60)
            if r.status_code != 200:
                raise SystemExit(f"voice-clone embedding failed ({r.status_code}): {_error_message(r)}")
            data = r.json()["data"]["voice_clone_embedding"]
            emb.write_text(json.dumps(data))
            print(f"created narrator embedding from {sample.name}")
            return data
    return None


async def synth_clone(client, key, text, emb) -> bytes:
    payload = {"text": text, "model": "vachana-vc-v1", "speaker_embedding": emb,
               "audio_config": {"sample_rate": SR, "num_channels": 1, "container": "mp3", "bitrate": "128k"}}
    for attempt in range(4):
        r = await client.post(GNANI + TTS_PATH, headers={"X-API-Key-ID": key, "Content-Type": "application/json"},
                              json=payload, timeout=60)
        if r.status_code == 429:
            await asyncio.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code != 200:
            raise SystemExit(f"cloned-voice TTS failed ({r.status_code}): {_error_message(r)}")
        return r.content
    raise SystemExit("cloned-voice TTS rate limited")


async def make_clips() -> dict[str, Path]:
    CLIPS.mkdir(parents=True, exist_ok=True)
    key = get_settings().gnani_api_key.get_secret_value()
    out = {}
    async with httpx.AsyncClient() as client:
        tts = GnaniTTS(key, client)
        emb = await narrator_embedding(client, key)
        narrator = "clone" if emb else NARRATOR_FALLBACK
        print("narrator voice:", "your cloned voice" if emb else f"Timbre v3.0 {NARRATOR_FALLBACK} (no clone yet)")
        for nid, text in NARRATION.items():
            f = CLIPS / f"{nid}_{_key(narrator, text, json.dumps(emb)[:64] if emb else '')}.mp3"
            if not f.exists():
                audio = await synth_clone(client, key, text, emb) if emb else \
                    await tts._synthesize(text, "timbre-v3.0", NARRATOR_FALLBACK, "auto")
                f.write_bytes(audio)
            out[nid] = f
        for sid, text, lang, _ui, _cap in SCENES:
            f = CLIPS / f"{sid}_{_key(FARMER_VOICE, lang, text)}.mp3"
            if not f.exists():
                f.write_bytes(await tts._synthesize(text, "timbre-v3.0", FARMER_VOICE, lang))
            out[sid] = f
    for k, f in out.items():
        print(f"  {k:7} {duration(f):5.1f}s  {f.name}")
    return out


def duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


def decode(path: Path) -> np.ndarray:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

# ---------------------------------------------------------------- page helpers

TITLE_CSS = """
*{box-sizing:border-box;margin:0} body{width:100vw;height:100vh;overflow:hidden;background:#FAFAFC;color:#14131A;
font-family:Inter,'Noto Sans Devanagari',sans-serif;display:grid;grid-template-columns:1.15fr 1fr;border:1px solid #E6E5EE}
.l{padding:64px 56px;display:flex;flex-direction:column;justify-content:center;gap:18px;border-right:1px solid #E6E5EE}
.label{font-family:'JetBrains Mono',monospace;font-size:13px;letter-spacing:.1em;text-transform:uppercase;color:#6B6A76}
.pill{display:inline-block;border:1px solid #E6E5EE;background:#fff;padding:6px 12px;width:max-content;color:#14131A}
h1{font-family:'Instrument Serif',serif;font-weight:400;font-size:76px;line-height:1}
h1 em{font-style:italic;color:#7C5CFA} h2{font-family:'Instrument Serif','Tiro Devanagari Hindi',serif;font-weight:400;font-size:34px;line-height:1.15}
h2 em{font-style:normal;color:#7C5CFA} .who{font-size:19px;line-height:1.55} .who b{font-weight:600}
.badge{font-family:'JetBrains Mono',monospace;font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:#5B3FD6;
border:1px solid #7C5CFA;background:#F6F3FF;padding:10px 14px;width:max-content}
.r{display:flex;align-items:center;justify-content:center;background:#fff;padding:40px} .r img{width:100%;max-width:560px}
.steps{display:grid;grid-template-columns:repeat(3,1fr);border:1px solid #E6E5EE;margin-top:8px}
.steps div{padding:12px 14px;font-size:14px} .steps div+div{border-left:1px solid #E6E5EE} .steps b{display:block;margin-top:2px}
.fade{animation:f .9s ease-out both} .d2{animation-delay:.5s} .d3{animation-delay:1s} .d4{animation-delay:1.6s}
@keyframes f{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
@media (max-width:600px){body{grid-template-columns:1fr;grid-template-rows:auto 1fr}.r{order:-1;padding:28px 20px 0;background:#FAFAFC}
.r img{max-width:300px}.l{padding:20px 22px 40px;justify-content:flex-start;gap:14px;border-right:0}h1{font-size:56px}
h2{font-size:26px}.who{font-size:15px}.steps div{padding:8px;font-size:11px}.links{font-size:13px!important}.label{font-size:10px}}
"""
FONTS = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Inter:wght@400;600'
         '&family=JetBrains+Mono&family=Noto+Sans+Devanagari:wght@400;600&family=Tiro+Devanagari+Hindi&display=swap">')

INTRO_HTML = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{FONTS}<style>{TITLE_CSS}</style></head><body>
<div class="l">
  <span class="label pill fade">Great Indian AI Internship Challenge · Gnani AI</span>
  <h1 class="fade d2">Kisan <em>Saathi</em></h1>
  <h2 class="fade d2">Chhattisgarh ke kisanon ke liye — yojana, fasal aur mausam, <em>bolkar</em>.</h2>
  <p class="who fade d3"><b>Anish Deep Toppo</b><br>Final-year B.Tech, Data Science &amp; Artificial Intelligence<br>
  IIIT Naya Raipur, Chhattisgarh</p>
  <div class="steps fade d4"><div><span class="label">01 / Boliye</span><b>Gnani Prisma v3.0</b></div>
  <div><span class="label">02 / Samjhe</span><b>Gemini + knowledge base</b></div>
  <div><span class="label">03 / Suniye</span><b>Gnani Timbre v3.0</b></div></div>
</div>
<div class="r fade d2"><img src="/static/illustration.svg" alt=""></div></body></html>"""

OUTRO_HTML = f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">{FONTS}<style>{TITLE_CSS}
.l{{gap:22px}} .links{{font-family:'JetBrains Mono',monospace;font-size:17px;line-height:1.9}}</style></head><body>
<div class="l">
  <h1 class="fade">Kisan <em>Saathi</em></h1>
  <h2 class="fade d2">Yojana · Fasal · Mausam — ek jagah, <em>apni bhasha</em> mein.</h2>
  <span class="badge fade d2">Powered by Gnani Prisma v3.0 &amp; Timbre v3.0</span>
  <p class="links fade d3">Try it: kisan-saathi.duckdns.org<br>Code: github.com/anishtoppo55/kisan-sathi</p>
  <p class="label fade d4">#GnaniAI &nbsp; #GreatIndianAIInternshipChallenge</p>
</div>
<div class="r fade d2"><img src="/static/illustration.svg" alt=""></div></body></html>"""

INIT_JS = r"""
(() => {
  const md = navigator.mediaDevices;
  if (!md) return;
  const orig = md.getUserMedia.bind(md);
  md.getUserMedia = async (c) => {
    const url = window.__demoClip;
    if (!url) return orig(c);
    window.__demoClip = null;
    const ctx = new AudioContext({ sampleRate: 48000 });
    const buf = await ctx.decodeAudioData(await (await fetch(url)).arrayBuffer());
    const src = ctx.createBufferSource(); src.buffer = buf;
    const dest = ctx.createMediaStreamDestination(); src.connect(dest);
    src.onended = () => setTimeout(() => document.getElementById("mic-btn").click(), 500);
    setTimeout(() => { src.start(); window.demoEvent("clip_start", url); }, 250);
    return dest.stream;
  };
  const play = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function () {
    if (this.src && this.src.startsWith("data:audio")) window.demoEvent("reply", this.src);
    this.muted = true;  // sound is added to the video in post
    return play.call(this).catch(() => {});
  };
})();
"""

OVERLAY_JS = r"""
(() => {
  if (document.getElementById("demo-css")) return;
  const s = document.createElement("style"); s.id = "demo-css";
  s.textContent = `
  #demo-cap{position:fixed;left:50%;top:76px;transform:translateX(-50%);z-index:999;background:#14131A;color:#fff;
    font:600 15px/1.3 Inter,'Noto Sans Devanagari',sans-serif;padding:10px 18px;border-radius:2px;letter-spacing:.01em;
    box-shadow:0 8px 30px rgba(20,19,26,.25);transition:opacity .35s;opacity:0;white-space:nowrap}
  @media (max-width:600px){#demo-cap{top:auto;bottom:168px;white-space:normal;width:92%;text-align:center;font-size:13px}}
  #demo-cap.on{opacity:1} #demo-cap small{display:block;font:500 11px/1.4 'JetBrains Mono',monospace;color:#C9BCFF;
    letter-spacing:.06em;text-transform:uppercase;margin-top:2px}
  .demo-tap{position:fixed;z-index:1000;width:46px;height:46px;margin:-23px 0 0 -23px;border-radius:50%;
    background:rgba(124,92,250,.35);border:2px solid #7C5CFA;pointer-events:none;animation:dt .7s ease-out forwards}
  @keyframes dt{from{transform:scale(.4);opacity:1}to{transform:scale(1.6);opacity:0}}`;
  document.head.appendChild(s);
  const c = document.createElement("div"); c.id = "demo-cap"; document.body.appendChild(c);
})();
"""

# ---------------------------------------------------------------- recording

class Recorder:
    def __init__(self):
        self.frames: list[tuple[float, Path]] = []
        self.audio: list[tuple[float, Path]] = []
        self.events: list[tuple[str, float, str]] = []  # (kind, time, file)
        self.reply_evt = asyncio.Event()
        self.last_reply: Path | None = None
        self.n_reply = 0

    def on_event(self, kind: str, payload: str):
        t = time.time()
        if kind == "clip_start":
            name = payload.rsplit("/", 1)[-1]
            self.audio.append((t, CLIPS / name))
            self.events.append(("clip", t, str(CLIPS / name)))
        elif kind == "reply":
            self.n_reply += 1
            f = OUT / f"reply_{self.n_reply}.mp3"
            f.write_bytes(base64.b64decode(payload.split(",", 1)[1]))
            self.audio.append((t, f))
            self.events.append(("reply", t, str(f)))
            self.last_reply = f
            self.reply_evt.set()


async def record(clips: dict[str, Path]) -> tuple[Recorder, float, float]:
    from playwright.async_api import async_playwright

    if FRAMES.exists():
        shutil.rmtree(FRAMES)
    FRAMES.mkdir(parents=True)
    rec = Recorder()
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        if MOBILE:
            ctx = await browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2.5,
                                            is_mobile=True, has_touch=True, locale="hi-IN")
        else:
            ctx = await browser.new_context(viewport={"width": 1280, "height": 720}, device_scale_factor=1.5, locale="hi-IN")
        await ctx.expose_function("demoEvent", rec.on_event)
        await ctx.add_init_script(INIT_JS)

        async def serve(route):
            url = route.request.url
            if url.endswith("/__demo/intro"):
                await route.fulfill(body=INTRO_HTML, content_type="text/html; charset=utf-8")
            elif url.endswith("/__demo/outro"):
                await route.fulfill(body=OUTRO_HTML, content_type="text/html; charset=utf-8")
            else:
                await route.fulfill(path=str(CLIPS / url.rsplit("/", 1)[-1]), content_type="audio/mpeg")
        await ctx.route(f"{SITE}/__demo/**", serve)

        page = await ctx.new_page()
        cdp = await ctx.new_cdp_session(page)
        n = 0

        async def on_frame(params):
            nonlocal n
            n += 1
            f = FRAMES / f"{n:06d}.jpg"
            f.write_bytes(base64.b64decode(params["data"]))
            rec.frames.append((params["metadata"]["timestamp"], f))
            try:
                await cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
            except Exception:
                pass
        cdp.on("Page.screencastFrame", lambda prm: asyncio.ensure_future(on_frame(prm)))

        async def narrate(nid, gap=0.35):
            rec.audio.append((time.time(), clips[nid]))
            rec.events.append(("narration", rec.audio[-1][0], str(clips[nid])))
            await asyncio.sleep(duration(clips[nid]) + gap)

        async def caption(text, sub=None):
            await page.evaluate("([t, s]) => { const c = document.getElementById('demo-cap');"
                                " c.innerHTML = ''; c.append(t); if (s) { const m = document.createElement('small');"
                                " m.textContent = s; c.append(m); } c.classList.add('on'); }", [text, sub])

        async def tap(selector):
            box = await page.locator(selector).first.bounding_box()
            await page.evaluate("([x, y]) => { const d = document.createElement('div'); d.className = 'demo-tap';"
                                " d.style.left = x + 'px'; d.style.top = y + 'px'; document.body.appendChild(d);"
                                " setTimeout(() => d.remove(), 800); }",
                                [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])
            await asyncio.sleep(0.35)
            await page.locator(selector).first.click()

        async def scroll_cards(seconds):
            await page.evaluate("""s => { const el = document.getElementById('cards'); const max = el.scrollHeight - el.clientHeight;
                if (max < 40) return; const t0 = performance.now(); const pause = 2500;
                const step = now => { const k = Math.min(1, Math.max(0, (now - t0 - pause) / (s * 1000 - pause)));
                  el.scrollTop = max * k * k * (3 - 2 * k); if (k < 1) requestAnimationFrame(step); };
                requestAnimationFrame(step); }""", seconds)

        # ---- intro (phone cut has none: it opens straight on the app)
        await page.goto(f"{SITE}/" if MOBILE else f"{SITE}/__demo/intro", wait_until="networkidle")
        await page.evaluate("document.fonts.ready")
        await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 90, "maxWidth": 1920, "maxHeight": 1080})
        await asyncio.sleep(0.6)
        t_start = time.time()
        if not MOBILE:
            await asyncio.sleep(1.0)
            for nid in ("intro1", "intro2", "intro3"):
                await narrate(nid)
            await page.goto(SITE + "/", wait_until="networkidle")
            await page.evaluate("document.fonts.ready")

        # ---- the app
        await page.evaluate(OVERLAY_JS)
        await asyncio.sleep(1.0 if MOBILE else 1.5)
        scenes = [MOBILE_Q1, SCENES[2], SCENES[3]] if MOBILE else SCENES[:5]
        if MOBILE:  # Hindi UI from the start for the phone cut
            await tap('.topbar [data-ui-lang="hi-IN"]')
            await asyncio.sleep(1.0)
        for i, (sid, _text, _lang, ui, cap) in enumerate(scenes):
            await caption(cap, "Farmer's voice simulated with Gnani Timbre v3.0" if i == 0 else None)
            await asyncio.sleep(1.4)
            if ui == "hi-IN" and not MOBILE:
                await tap('.topbar [data-ui-lang="hi-IN"]')
                await asyncio.sleep(1.4)
            rec.reply_evt.clear()
            await page.evaluate(f"window.__demoClip = '/__demo/clips/{clips[sid].name}'")
            await tap("#mic-btn")
            try:
                await asyncio.wait_for(rec.reply_evt.wait(), 75)
            except asyncio.TimeoutError:
                print(f"!! no spoken reply for {sid}")
                await asyncio.sleep(2)
                continue
            d = duration(rec.last_reply)
            await scroll_cards(min(d, MAX_REPLY_S))
            await asyncio.sleep((min(d, MAX_REPLY_S) + 2.5) if MOBILE else d + 1.3)

        # ---- outro (phone cut ends on the last answer)
        if MOBILE:
            await asyncio.sleep(1.0)
        else:
            await page.goto(f"{SITE}/__demo/outro", wait_until="networkidle")
            await asyncio.sleep(0.8)
            await narrate("outro", gap=1.8)
        t_end = time.time()
        await cdp.send("Page.stopScreencast")
        await asyncio.sleep(0.5)
        await browser.close()
    return rec, t_start, t_end

# ---------------------------------------------------------------- build

def save_session(rec: Recorder, t_start: float, t_end: float) -> None:
    (OUT / ("session_mobile.json" if MOBILE else "session.json")).write_text(json.dumps({
        "t_start": t_start, "t_end": t_end,
        "frames": [[t, str(f)] for t, f in rec.frames],
        "events": rec.events,
    }))


def plan_cuts(events, t_start: float) -> list[tuple[float, float]]:
    """Cut intervals (seconds from start): long processing pauses, and reply tails after MAX_REPLY_S."""
    cuts = []
    ev = sorted(events, key=lambda e: e[1])
    for i, (kind, t, f) in enumerate(ev):
        if kind != "clip":
            continue
        reply = next((e for e in ev[i + 1:] if e[0] == "reply"), None)
        if not reply:
            continue
        a = t - t_start + duration(Path(f)) + KEEP_AFTER_CLIP_S
        b = reply[1] - t_start - KEEP_BEFORE_REPLY_S
        if b - a > 0.5:
            cuts.append((a, b))
        d = duration(Path(reply[2]))
        nxt = next((e[1] for e in ev if e[1] > reply[1]), None)
        tail_end = reply[1] + d if nxt is None else min(reply[1] + d, nxt - 0.3)
        if tail_end - (reply[1] + MAX_REPLY_S + 0.4) > 0.5:
            cuts.append((reply[1] - t_start + MAX_REPLY_S + 0.4, tail_end - t_start))
    return sorted(cuts)


def build(session: dict | None = None) -> Path:
    session = session or json.loads((OUT / ("session_mobile.json" if MOBILE else "session.json")).read_text())
    t_start, t_end = session["t_start"], session["t_end"]
    total = t_end - t_start
    cuts = sorted(plan_cuts(session["events"], t_start) + [tuple(c) for c in session.get("extra_cuts", [])])
    keep, prev = [], 0.0
    for a, b in cuts:
        keep.append((prev, a)); prev = b
    keep.append((prev, total))

    # ---- audio on the original timeline, replies faded at MAX_REPLY_S
    mix = np.zeros(int((total + 1) * SR), dtype=np.float32)
    for kind, t, f in session["events"]:
        a = decode(Path(f))
        if kind == "reply" and len(a) > MAX_REPLY_S * SR:
            n, fade = int(MAX_REPLY_S * SR), int(0.6 * SR)
            a = a[:n].copy()
            a[-fade:] *= np.linspace(1, 0, fade)
        i = int(max(0.0, t - t_start) * SR)
        mix[i:i + len(a)] += a[: max(0, len(mix) - i)]
    xf = int(0.04 * SR)
    parts = []
    for a, b in keep:
        seg = mix[int(a * SR):int(b * SR)].copy()
        if len(seg) > 2 * xf:
            seg[:xf] *= np.linspace(0, 1, xf); seg[-xf:] *= np.linspace(1, 0, xf)
        parts.append(seg)
    audio = np.concatenate(parts)
    audio = audio / max(float(np.abs(audio).max() or 1), 1.0) * 0.95
    wav = OUT / "audio.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-", str(wav)],
                   input=audio.astype("<f4").tobytes(), check=True)

    # ---- frames: piece-wise constant image, sampled per kept segment
    frames = sorted((t - t_start, f) for t, f in session["frames"])
    def frame_at(x):
        best = frames[0][1]
        for t, f in frames:
            if t <= x:
                best = f
            else:
                break
        return best
    lines, new_len = [], 0.0
    for a, b in keep:
        inside = [(t, f) for t, f in frames if a < t < b]
        pts = [(a, frame_at(a))] + inside
        for j, (t, f) in enumerate(pts):
            t1 = pts[j + 1][0] if j + 1 < len(pts) else b
            if t1 - t > 0:
                lines += [f"file '{f}'", f"duration {t1 - t:.4f}"]
        new_len += b - a
    lines.append(lines[-2])
    (OUT / "frames.txt").write_text("\n".join(lines) + "\n")

    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(OUT / "frames.txt"),
                    "-i", str(wav), "-vf", ("fps=30,scale=-2:1920:flags=lanczos,pad=1080:1920:(ow-iw)/2:0:color=0xFAFAFC,format=yuv420p"
                                            if MOBILE else "fps=30,scale=1920:1080:flags=lanczos,format=yuv420p"),
                    "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-c:a", "aac", "-b:a", "160k",
                    "-t", f"{new_len:.2f}", "-movflags", "+faststart", str(FINAL)], check=True)
    print(f"cut {len(cuts)} segments ({total - new_len:.1f}s): {total:.1f}s -> {new_len:.1f}s")
    return FINAL


OWN_VOICE_FILTER = ("highpass=f=80,afftdn=nf=-30,"
                    "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.05,areverse,"
                    "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.15,areverse,"
                    "loudnorm=I=-19:TP=-1.5:LRA=11")


def own_voice_session() -> dict:
    """Swap the generated intro/outro narration for your recordings (.cache/demo/my_intro.*, my_outro.*).
    Cleans them (trim, light denoise, loudness matched to the rest of the video) and re-times the cards."""
    session = json.loads((OUT / "session.json").read_text())
    t0 = session["t_start"]
    cleaned = {}
    for name in ("intro", "outro"):
        src = next((f for f in sorted(OUT.glob(f"my_{name}.*")) if f.suffix != ".wav" or "clean" not in f.name), None)
        if src is None:
            raise SystemExit(f"missing .cache/demo/my_{name}.<m4a|mp3|wav|mp4>")
        out = OUT / f"my_{name}_clean.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vn", "-af", OWN_VOICE_FILTER,
                        "-ar", str(SR), "-ac", "1", str(out)], check=True)
        cleaned[name] = out
    narr = [e for e in session["events"] if e[0] == "narration"]
    intro_ev = [e for e in narr if "intro" in Path(e[2]).name]
    outro_ev = [e for e in narr if "outro" in Path(e[2]).name][0]
    events = [e for e in session["events"] if e[0] != "narration"]
    intro_at = intro_ev[0][1]
    old_intro_end = max(t + duration(Path(f)) for _, t, f in intro_ev)
    new_intro_end = intro_at + duration(cleaned["intro"])
    events.append(["narration", intro_at, str(cleaned["intro"])])
    events.append(["narration", outro_ev[1], str(cleaned["outro"])])
    extra = []
    if old_intro_end - new_intro_end > 0.6:  # the intro card is static here, so shorten it
        extra.append([new_intro_end - t0 + 0.6, old_intro_end - t0])
    new_end = max(session["t_end"], outro_ev[1] + duration(cleaned["outro"]) + 1.6)
    session.update(events=events, extra_cuts=extra, t_end=new_end)
    print(f"own voice: intro {duration(cleaned['intro']):.1f}s (was {old_intro_end - intro_at:.1f}s), "
          f"outro {duration(cleaned['outro']):.1f}s")
    return session


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", action="store_true", help="only generate audio clips")
    ap.add_argument("--rebuild", action="store_true", help="re-edit the last recording (no new recording)")
    ap.add_argument("--own-voice", action="store_true", help="use .cache/demo/my_intro.* and my_outro.* as narration")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if args.rebuild or args.own_voice:
        out = build(own_voice_session() if args.own_voice else None)
        print(f"video: {out}  ({duration(out):.1f}s)")
        return
    clips = await make_clips()
    if args.clips:
        return
    if MOBILE and (OUT / "my_outro_clean.wav").exists():
        clips["outro"] = OUT / "my_outro_clean.wav"  # your own voice on the closing card
    rec, t0, t1 = await record(clips)
    print(f"recorded {len(rec.frames)} frames, {len(rec.audio)} audio events, {t1 - t0:.1f}s")
    save_session(rec, t0, t1)
    out = build()
    print(f"video: {out}  ({duration(out):.1f}s)")


if __name__ == "__main__":
    asyncio.run(main())

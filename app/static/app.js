// Kisan Saathi CG frontend. Server text is always inserted with textContent (never innerHTML) to avoid XSS.
const messagesEl = document.getElementById("messages");
const cardsEl = document.getElementById("cards");
const form = document.getElementById("chat-form");
const input = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");
const micBtn = document.getElementById("mic-btn");

const sessionId = (() => {
  let id = localStorage.getItem("ks_session");
  if (!id) { id = crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random(); localStorage.setItem("ks_session", id); }
  return id;
})();
let lastLang = "hi-en";
let busy = false;

// ---------- DOM helpers ----------
function el(tag, attrs = {}, text) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}
function list(items) {
  const ul = el("ul");
  for (const it of items || []) ul.appendChild(el("li", {}, it));
  return ul;
}
function safeLink(url, text) {
  const ok = typeof url === "string" && /^https?:\/\//i.test(url);
  return ok ? el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, text || url) : el("span", {}, text || "");
}
function addBubble(role, text) {
  const b = el("div", { class: `bubble ${role}` }, text);
  messagesEl.appendChild(b);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return b;
}

// ---------- audio ----------
let currentAudio = null;
function playAudio(b64) {
  if (!b64) return null;
  if (currentAudio) currentAudio.pause();
  currentAudio = new Audio(`data:audio/mpeg;base64,${b64}`); // Timbre returns MP3
  currentAudio.play().catch(() => {});
  return currentAudio;
}

// ---------- card renderers ----------
const STATUS_TEXT = {
  "hi-IN": { eligible: "पात्र", already_enrolled: "पहले से है", needs_check: "जानकारी चाहिए", not_eligible: "पात्र नहीं" },
  "hi-en": { eligible: "Paatra", already_enrolled: "Pehle se hai", needs_check: "Jaankari chahiye", not_eligible: "Paatra nahi" },
};
const PROFILE_FIELDS = [
  ["owns_land", "Zameen apne naam par hai?"],
  ["is_tenant", "Batai / kiraye par kheti?"],
  ["income_tax_payer", "Income tax bharte hain?"],
  ["govt_employee_or_pensioner_10k", "Sarkari naukri / ₹10,000+ pension?"],
  ["registered_on_state_portal", "Kisan portal / samiti mein panjiyan?"],
  ["grows_paddy", "Dhaan ugaate hain?"],
  ["has_kcc", "KCC pehle se hai?"],
];

const cardRenderers = {
  scheme(card) {
    const d = card.data, c = el("article", { class: "card" });
    c.appendChild(el("span", { class: "kind" }, "📋 Yojana"));
    c.appendChild(el("h3", {}, d.title));
    c.appendChild(el("p", { class: "meta-line" }, `💰 ${d.benefit}`));
    c.appendChild(el("h4", {}, "Zaroori dastavez"));
    c.appendChild(list(d.documents));
    c.appendChild(el("p", { class: "meta-line" }, `📝 ${d.apply}`));
    if (d.helpline) c.appendChild(el("p", { class: "meta-line" }, `📞 Helpline: ${d.helpline}`));
    const p = el("p", { class: "meta-line" }, "🔗 ");
    p.appendChild(safeLink(d.link));
    c.appendChild(p);
    return c;
  },

  eligibility(card) {
    const d = card.data, c = el("article", { class: "card" });
    const st = STATUS_TEXT[lastLang] || STATUS_TEXT["hi-en"];
    c.appendChild(el("span", { class: "kind" }, "✅ Yojana paatrata jaanch"));
    c.appendChild(el("h3", {}, "Aapke liye yojanaen"));
    for (const r of d.results) {
      const row = el("div", { class: "elig-row" });
      const head = el("div");
      head.appendChild(el("span", { class: "name" }, r.name));
      head.appendChild(el("span", { class: `status ${r.status}` }, st[r.status] || r.status));
      row.appendChild(head);
      if (r.benefit) row.appendChild(el("p", {}, `💰 ${r.benefit}`));
      for (const reason of r.reasons || []) row.appendChild(el("p", {}, `✗ ${reason}`));
      for (const q of r.questions || []) row.appendChild(el("p", {}, `❓ ${q}`));
      if (r.status === "eligible" && r.documents?.length) row.appendChild(el("p", {}, `📄 ${r.documents.join(", ")}`));
      for (const n of r.notes || []) row.appendChild(el("p", { class: "muted" }, `ℹ️ ${n}`));
      c.appendChild(row);
    }
    c.appendChild(el("h4", {}, "Apni jaankari sudharein"));
    c.appendChild(profileForm(d.profile || {}));
    return c;
  },

  crop(card) {
    const d = card.data, c = el("article", { class: "card" });
    c.appendChild(el("span", { class: "kind" }, "🌱 Fasal salah"));
    c.appendChild(el("h3", {}, [d.crop, d.title].filter(Boolean).join(" — ")));
    if (d.stage) c.appendChild(el("p", { class: "meta-line" }, `🪴 Avastha: ${d.stage}`));
    c.appendChild(list(d.actions));
    if (d.source) {
      const p = el("p", { class: "meta-line muted" }, "📚 ");
      p.appendChild(d.source_url ? safeLink(d.source_url, d.source) : el("span", {}, d.source));
      c.appendChild(p);
    }
    if (d.reminder) c.appendChild(el("div", { class: "reminder" }, `⚠️ ${d.reminder}`));
    return c;
  },

  weather(card) {
    const d = card.data, c = el("article", { class: "card" });
    c.appendChild(el("span", { class: "kind" }, "🌦️ Mausam"));
    c.appendChild(el("h3", {}, `${d.place}: agle 7 din`));
    const wrap = el("div", { class: "chart-wrap" });
    const canvas = el("canvas", { "aria-label": "7 day rain and temperature chart", role: "img" });
    wrap.appendChild(canvas);
    c.appendChild(wrap);
    for (const a of d.advisories || []) c.appendChild(el("div", { class: `advisory ${a.level}` }, a.text));
    requestAnimationFrame(() => drawWeather(canvas, d.days));
    return c;
  },

  web_sources(card) {
    const d = card.data, c = el("article", { class: "card" });
    c.appendChild(el("span", { class: "kind" }, "🌐 Web se"));
    c.appendChild(el("h3", {}, d.title || "Web sources"));
    const ul = el("ul");
    for (const s of d.sources || []) { const li = el("li"); li.appendChild(safeLink(s.url, s.title)); ul.appendChild(li); }
    c.appendChild(ul);
    c.appendChild(el("p", { class: "muted small" }, "Yeh jaankari web se hai; sarkari website par pushti karein."));
    return c;
  },

  default(card) {
    const c = el("article", { class: "card" });
    c.appendChild(el("span", { class: "kind" }, card.type));
    c.appendChild(el("h3", {}, card.data?.title || card.type));
    return c;
  },
};

function drawWeather(canvas, days) {
  if (!window.Chart) { canvas.replaceWith(el("p", { class: "muted" }, days.map((x) => `${x.label}: ${x.rain_mm}mm, ${x.tmin}–${x.tmax}°C`).join(" · "))); return; }
  new Chart(canvas, {
    data: {
      labels: days.map((x) => x.label),
      datasets: [
        { type: "bar", label: "Baarish (mm)", data: days.map((x) => x.rain_mm), backgroundColor: "rgba(21,101,192,.6)", yAxisID: "rain" },
        { type: "line", label: "Max °C", data: days.map((x) => x.tmax), borderColor: "#c62828", tension: .3, yAxisID: "temp" },
        { type: "line", label: "Min °C", data: days.map((x) => x.tmin), borderColor: "#ef6c00", tension: .3, yAxisID: "temp" },
      ],
    },
    options: {
      maintainAspectRatio: false,
      plugins: { legend: { labels: { boxWidth: 12, font: { size: 11 } } } },
      scales: {
        rain: { position: "left", beginAtZero: true, suggestedMax: 10, title: { display: true, text: "mm" } },
        temp: { position: "right", grid: { drawOnChartArea: false }, title: { display: true, text: "°C" } },
      },
    },
  });
}

function profileForm(profile) {
  const f = el("form", { class: "profile-form" });
  for (const [key, label] of PROFILE_FIELDS) {
    f.appendChild(el("label", { for: `pf-${key}` }, label));
    const sel = el("select", { id: `pf-${key}`, name: key });
    for (const [v, t] of [["", "Pata nahi"], ["true", "Haan"], ["false", "Nahi"]]) {
      const o = el("option", { value: v }, t);
      if (String(profile[key] ?? "") === v) o.selected = true;
      sel.appendChild(o);
    }
    f.appendChild(sel);
  }
  f.appendChild(el("label", { for: "pf-land_acres" }, "Zameen (acre)"));
  const acres = el("input", { id: "pf-land_acres", name: "land_acres", type: "number", min: "0", step: "0.1" });
  if (profile.land_acres != null) acres.value = profile.land_acres;
  f.appendChild(acres);
  f.appendChild(el("button", { type: "submit" }, "Dobara jaanchein"));
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const p = {};
    for (const [key] of PROFILE_FIELDS) { const v = f.elements[key].value; p[key] = v === "" ? null : v === "true"; }
    p.land_acres = acres.value === "" ? null : Number(acres.value);
    p.district = profile.district ?? null;
    try {
      const res = await fetch("/api/eligibility", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile: p, session_id: sessionId, lang: lastLang }),
      });
      if (!res.ok) throw new Error(res.status);
      const data = await res.json();
      addBubble("bot", data.reply_text);
      renderCards(data.cards || [], data.sources || []);
    } catch (err) { addBubble("bot", "Jaanch nahi ho paayi, dobara koshish karein.").classList.add("error"); }
  });
  return f;
}

function renderCards(cards) {
  if (!cards.length) return;
  cardsEl.replaceChildren();
  for (const card of cards) {
    const render = cardRenderers[card.type] || cardRenderers.default;
    try { cardsEl.appendChild(render(card)); } catch (err) { console.error("card render failed", err); }
  }
  if (window.innerWidth < 900) cardsEl.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---------- replies ----------
function showReply(pending, data) {
  pending.classList.remove("pending");
  pending.textContent = data.reply_text;
  if (data.lang) lastLang = data.lang;
  if (data.audio_b64) {
    const btn = el("button", { class: "replay", type: "button", title: "Dobara sunein" }, "🔊");
    btn.addEventListener("click", () => playAudio(data.audio_b64));
    pending.appendChild(btn);
    playAudio(data.audio_b64);
  }
  renderCards(data.cards || []);
}
function showError(pending, msg) {
  pending.classList.remove("pending");
  pending.textContent = msg || "Maaf kijiye, abhi jawab nahi mil paaya. Dobara koshish karein.";
  pending.classList.add("error");
}
async function errorDetail(res) {
  try { const j = await res.json(); return typeof j.detail === "string" ? j.detail : null; } catch { return null; }
}
function setBusy(b) { busy = b; sendBtn.disabled = b; if (!recorder) micBtn.disabled = b || !micSupported; }

async function sendMessage(text) {
  if (busy) return;
  addBubble("user", text);
  const pending = addBubble("bot pending", "Soch raha hoon…");
  setBusy(true);
  try {
    const res = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session_id: sessionId }),
    });
    if (!res.ok) return showError(pending, await errorDetail(res));
    showReply(pending, await res.json());
  } catch (err) { showError(pending); console.error(err); }
  finally { setBusy(false); input.focus(); }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  sendMessage(text);
});
document.getElementById("chips").addEventListener("click", (e) => {
  if (e.target.classList.contains("chip")) sendMessage(e.target.textContent);
});

// ---------- mic recording ----------
const MAX_RECORD_MS = 25000;
const micSupported = !!(navigator.mediaDevices && window.MediaRecorder);
let recorder = null, stopTimer = null;

function pickMime() {
  const types = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
  return types.find((t) => MediaRecorder.isTypeSupported(t)) || "";
}
async function startRecording() {
  if (busy) return;
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
  catch (err) { addBubble("bot", "Mic ki anumati nahi mili. Browser settings mein mic allow karein (HTTPS zaroori hai).").classList.add("error"); return; }
  if (currentAudio) currentAudio.pause();
  const mimeType = pickMime();
  recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  const chunks = [];
  recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  recorder.onstop = () => {
    stream.getTracks().forEach((t) => t.stop());
    clearTimeout(stopTimer);
    micBtn.classList.remove("recording");
    micBtn.textContent = "🎤";
    const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
    recorder = null;
    sendVoice(blob);
  };
  recorder.start();
  micBtn.classList.add("recording");
  micBtn.textContent = "⏹";
  micBtn.title = "Rokne ke liye dabaayein";
  stopTimer = setTimeout(stopRecording, MAX_RECORD_MS);
}
function stopRecording() { if (recorder && recorder.state === "recording") recorder.stop(); }

async function sendVoice(blob) {
  if (blob.size < 1000) return; // accidental tap
  const userBubble = addBubble("user", "🎤 …");
  const pending = addBubble("bot pending", "Sun raha hoon…");
  setBusy(true);
  const ext = blob.type.includes("ogg") ? "ogg" : blob.type.includes("mp4") ? "m4a" : "webm";
  const fd = new FormData();
  fd.append("audio", blob, `recording.${ext}`);
  fd.append("session_id", sessionId);
  try {
    const res = await fetch("/api/voice", { method: "POST", body: fd });
    if (!res.ok) { userBubble.textContent = "🎤"; return showError(pending, await errorDetail(res)); }
    const data = await res.json();
    userBubble.textContent = data.transcript ? `🎤 ${data.transcript}` : "🎤 (kuch sunai nahi diya)";
    showReply(pending, data);
  } catch (err) { showError(pending); console.error(err); }
  finally { setBusy(false); micBtn.title = "Bolkar poochhiye"; }
}

if (micSupported) {
  micBtn.disabled = false;
  micBtn.title = "Bolkar poochhiye";
  micBtn.addEventListener("click", () => (recorder ? stopRecording() : startRecording()));
} else {
  micBtn.title = "Is browser mein mic support nahi hai";
}

addBubble("bot", "Johar! Namaste! Main Kisan Saathi hoon. Yojana, fasal ya mausam ke baare mein bolkar ya likhkar poochhiye.");

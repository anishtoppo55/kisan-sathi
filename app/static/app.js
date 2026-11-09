// Kisan Saathi CG frontend.
// Server text is always inserted with textContent (never as raw HTML) to avoid XSS.
// Desktop (>= 900px): result cards go to the right-hand #cards panel.
// Mobile (< 900px): result cards go inline under the bot reply in the chat thread.

const messagesEl = document.getElementById("messages");
const cardsEl = document.getElementById("cards");
const chipsEl = document.getElementById("chips");
const form = document.getElementById("chat-form");
const input = document.getElementById("chat-input");
const sendBtn = document.getElementById("send-btn");
const micBtn = document.getElementById("mic-btn");
const orbEl = document.getElementById("orb");
const mobileMQ = window.matchMedia("(max-width: 899px)");

const sessionId = (() => {
  let id = localStorage.getItem("ks_session");
  if (!id) { id = crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random(); localStorage.setItem("ks_session", id); }
  return id;
})();
let lastLang = "hi-en";
let busy = false;

// ---------- example questions (one list drives desktop chips and mobile cards) ----------
const SUGGESTIONS = [
  { group: "yojana", icon: "i-check", title: "Yojana jaanch", q: "Main 2 acre ka kisan hoon, income tax nahi bharta, KCC nahi hai" },
  { group: "yojana", icon: "i-scheme", title: "PM-Kisan", q: "PM-Kisan ke liye kaun se documents chahiye?" },
  { group: "yojana", icon: "i-scheme", title: "Dhaan token", q: "धान बेचने के लिए टोकन कैसे मिलेगा?" },
  { group: "fasal", icon: "i-leaf", title: "Fasal salah", q: "Chana mein phool aa rahe hain, sinchai karun?" },
  { group: "fasal", icon: "i-leaf", title: "Keet niyantran", q: "Dhaan mein bhura maahu lag gaya hai kya karun?" },
  { group: "mausam", icon: "i-cloud", title: "Mausam", q: "Bemetara mein agle hafte barish hogi? Gehun mein paani doon?" },
];
// Home on mobile shows one card per need (2×2); desktop Home shows all chips.
const HOME_MOBILE = ["Yojana jaanch", "PM-Kisan", "Fasal salah", "Mausam"];

// ---------- DOM helpers ----------
function el(tag, attrs = {}, text) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}
function icon(id, cls = "ic") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", cls);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#${id}`);
  svg.appendChild(use);
  return svg;
}
function list(items) {
  const ul = el("ul");
  for (const it of items || []) ul.appendChild(el("li", {}, it));
  return ul;
}
function safeLink(url, text, cls) {
  const ok = typeof url === "string" && /^https?:\/\//i.test(url);
  if (!ok) return el("span", {}, text || "");
  const a = el("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, text || url);
  if (cls) a.className = cls;
  return a;
}
function metaLine(iconId, text) {
  const p = el("p", { class: "meta-line" });
  p.appendChild(icon(iconId));
  p.appendChild(typeof text === "string" ? el("span", {}, text) : text);
  return p;
}
function cardShell(iconId, kind) {
  const c = el("article", { class: "card" });
  const head = el("div", { class: "card-head" });
  const k = el("span", { class: "label kind" });
  k.appendChild(icon(iconId));
  k.appendChild(document.createTextNode(kind));
  head.appendChild(k);
  c.appendChild(head);
  return { c, head };
}
function cssVar(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

// ---------- chat bubbles ----------
function addMessage(role, text, extraClass = "") {
  const msg = el("div", { class: `msg ${role}` });
  if (role === "bot") msg.appendChild(el("span", { class: "avatar", "aria-hidden": "true" }));
  const body = el("div", { class: "msg-body" });
  const bubble = el("div", { class: `bubble ${extraClass}`.trim() }, text);
  body.appendChild(bubble);
  if (role === "bot") body.appendChild(el("div", { class: "inline-cards" }));
  msg.appendChild(body);
  messagesEl.appendChild(msg);
  scrollToBottom(msg);
  return bubble;
}
function scrollToBottom(node) {
  if (mobileMQ.matches) node.scrollIntoView({ behavior: "smooth", block: "end" });
  else messagesEl.scrollTop = messagesEl.scrollHeight;
}
function startChat() { document.body.classList.add("has-chat"); }

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
    const d = card.data;
    const { c } = cardShell("i-scheme", "Yojana");
    c.appendChild(el("h3", {}, d.title));
    if (d.benefit) c.appendChild(el("p", { class: "benefit" }, d.benefit));
    if (d.documents?.length) {
      c.appendChild(el("h4", {}, "Zaroori dastavez"));
      const row = el("div", { class: "chiprow" });
      for (const doc of d.documents) row.appendChild(el("span", { class: "tag plain" }, doc));
      c.appendChild(row);
    }
    if (d.apply) c.appendChild(metaLine("i-check", d.apply));
    if (d.helpline) c.appendChild(metaLine("i-phone", `Helpline: ${d.helpline}`));
    if (d.link) c.appendChild(metaLine("i-link", safeLink(d.link)));
    return c;
  },

  eligibility(card) {
    const d = card.data;
    const st = STATUS_TEXT[lastLang] || STATUS_TEXT["hi-en"];
    const { c } = cardShell("i-check", "Yojana paatrata jaanch");
    c.appendChild(el("h3", {}, "Aapke liye yojanaen"));
    for (const r of d.results || []) {
      const row = el("div", { class: "elig-row" });
      const head = el("div", { class: "elig-head" });
      head.appendChild(el("span", { class: "name" }, r.name));
      head.appendChild(el("span", { class: `status ${r.status}` }, st[r.status] || r.status));
      row.appendChild(head);
      if (r.benefit) row.appendChild(el("p", { class: "muted" }, r.benefit));
      for (const reason of r.reasons || []) row.appendChild(el("p", { class: "why" }, `✗ ${reason}`));
      for (const q of r.questions || []) row.appendChild(el("p", { class: "ask" }, `? ${q}`));
      if (r.status === "eligible" && r.documents?.length) row.appendChild(el("p", {}, `Dastavez: ${r.documents.join(", ")}`));
      for (const n of r.notes || []) row.appendChild(el("p", { class: "muted small" }, n));
      c.appendChild(row);
    }
    c.appendChild(el("h4", {}, "Apni jaankari sudharein"));
    c.appendChild(profileForm(d.profile || {}));
    return c;
  },

  crop(card) {
    const d = card.data;
    const { c } = cardShell("i-leaf", "Fasal salah");
    const tags = el("div", { class: "chiprow" });
    if (d.crop) tags.appendChild(el("span", { class: "tag" }, d.crop));
    if (d.stage) tags.appendChild(el("span", { class: "tag plain" }, `Avastha: ${d.stage}`));
    if (tags.childNodes.length) c.appendChild(tags);
    c.appendChild(el("h3", {}, d.title));
    c.appendChild(list(d.actions));
    if (d.source) c.appendChild(metaLine("i-link", d.source_url ? safeLink(d.source_url, d.source) : d.source));
    if (d.reminder) {
      const r = el("div", { class: "reminder" });
      r.appendChild(icon("i-alert"));
      r.appendChild(el("span", {}, d.reminder));
      c.appendChild(r);
    }
    return c;
  },

  weather(card) {
    const d = card.data;
    const { c } = cardShell("i-cloud", "Mausam");
    c.appendChild(el("h3", {}, `${d.place}: agle 7 din`));
    const wrap = el("div", { class: "chart-wrap" });
    const canvas = el("canvas", { "aria-label": "7 day rain and temperature chart", role: "img" });
    wrap.appendChild(canvas);
    c.appendChild(wrap);
    for (const a of d.advisories || []) c.appendChild(el("div", { class: `advisory ${a.level}` }, a.text));
    // Draw after the card is attached to the DOM (renderCards inserts it synchronously).
    setTimeout(() => drawWeather(canvas, d.days || []), 0);
    return c;
  },

  web_sources(card) {
    const d = card.data;
    const { c } = cardShell("i-globe", "Web se");
    c.appendChild(el("h3", {}, d.title || "Web sources"));
    const row = el("div", { class: "chiprow" });
    for (const s of d.sources || []) {
      const a = safeLink(s.url, null, "source-chip");
      if (a.tagName === "A") {
        a.textContent = "";
        a.appendChild(icon("i-globe"));
        a.appendChild(el("span", {}, s.title || s.url));
      }
      row.appendChild(a);
    }
    c.appendChild(row);
    c.appendChild(el("p", { class: "muted small" }, "Yeh jaankari web se hai; sarkari website par pushti karein."));
    return c;
  },

  default(card) {
    const { c } = cardShell("i-scheme", card.type);
    c.appendChild(el("h3", {}, card.data?.title || card.type));
    return c;
  },
};

function whenChartReady(timeoutMs = 10000) {
  // Chart.js loads from a CDN with `defer`; on slow mobile networks it can arrive after the first weather card.
  return new Promise((resolve) => {
    if (window.Chart) return resolve(true);
    const tag = document.querySelector('script[src*="chart"]');
    const done = () => resolve(!!window.Chart);
    if (!tag) return done();
    tag.addEventListener("load", done, { once: true });
    tag.addEventListener("error", done, { once: true });
    setTimeout(done, timeoutMs);
  });
}

async function drawWeather(canvas, days) {
  if (!(await whenChartReady())) {
    canvas.parentElement.replaceWith(el("p", { class: "muted" }, days.map((x) => `${x.label}: ${x.rain_mm}mm, ${x.tmin}–${x.tmax}°C`).join(" · ")));
    return;
  }
  const accent = cssVar("--accent", "#7C5CFA");
  const accentInk = cssVar("--accent-ink", "#5B3FD6");
  const warm = cssVar("--warn", "#ef6c00");
  const line = cssVar("--line", "#E6E5EE");
  const muted = cssVar("--muted", "#6B6A76");
  new Chart(canvas, {
    data: {
      labels: days.map((x) => x.label),
      datasets: [
        { type: "bar", label: "Baarish (mm)", data: days.map((x) => x.rain_mm), backgroundColor: accent + "55", borderColor: accent, borderWidth: 1, borderRadius: 4, yAxisID: "rain" },
        { type: "line", label: "Max °C", data: days.map((x) => x.tmax), borderColor: accentInk, backgroundColor: accentInk, tension: .35, pointRadius: 3, yAxisID: "temp" },
        { type: "line", label: "Min °C", data: days.map((x) => x.tmin), borderColor: warm, backgroundColor: warm, tension: .35, pointRadius: 3, yAxisID: "temp" },
      ],
    },
    options: {
      maintainAspectRatio: false,
      animation: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? false : { duration: 400 },
      plugins: { legend: { labels: { boxWidth: 10, color: muted, font: { size: 11, family: "Inter" } } } },
      scales: {
        x: { grid: { display: false }, ticks: { color: muted, font: { size: 10 }, maxRotation: 0, autoSkip: true } },
        rain: { position: "left", beginAtZero: true, suggestedMax: 10, grid: { color: line }, ticks: { color: muted }, title: { display: true, text: "mm", color: muted } },
        temp: { position: "right", grid: { drawOnChartArea: false }, ticks: { color: muted }, title: { display: true, text: "°C", color: muted } },
      },
    },
  });
}

function segmented(name, value) {
  // Haan / Nahi / Pata nahi toggle; value: true | false | null
  const seg = el("div", { class: "seg", role: "group", "data-name": name });
  const opts = [["true", "Haan"], ["false", "Nahi"], ["", "Pata nahi"]];
  const current = value === true ? "true" : value === false ? "false" : "";
  for (const [v, t] of opts) {
    const b = el("button", { type: "button", "data-value": v, "aria-pressed": String(v === current) }, t);
    b.addEventListener("click", () => {
      for (const x of seg.querySelectorAll("button")) x.setAttribute("aria-pressed", String(x === b));
    });
    seg.appendChild(b);
  }
  return seg;
}

function profileForm(profile) {
  const f = el("form", { class: "profile-form" });
  for (const [key, label] of PROFILE_FIELDS) {
    const row = el("div", { class: "pf-row" });
    row.appendChild(el("span", { id: `pf-${key}` }, label));
    const seg = segmented(key, profile[key]);
    seg.setAttribute("aria-labelledby", `pf-${key}`);
    row.appendChild(seg);
    f.appendChild(row);
  }
  const acresRow = el("div", { class: "pf-row" });
  acresRow.appendChild(el("label", { for: "pf-land_acres" }, "Zameen (acre)"));
  const acres = el("input", { id: "pf-land_acres", name: "land_acres", type: "number", min: "0", step: "0.1", inputmode: "decimal" });
  if (profile.land_acres != null) acres.value = profile.land_acres;
  acresRow.appendChild(acres);
  f.appendChild(acresRow);
  f.appendChild(el("button", { type: "submit", class: "btn-primary" }, "Dobara jaanchein"));
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const p = {};
    for (const seg of f.querySelectorAll(".seg")) {
      const v = seg.querySelector('button[aria-pressed="true"]')?.dataset.value ?? "";
      p[seg.dataset.name] = v === "" ? null : v === "true";
    }
    p.land_acres = acres.value === "" ? null : Number(acres.value);
    p.district = profile.district ?? null;
    try {
      const res = await fetch("/api/eligibility", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile: p, session_id: sessionId, lang: lastLang }),
      });
      if (!res.ok) throw new Error(res.status);
      const data = await res.json();
      const bubble = addMessage("bot", data.reply_text);
      renderCards(data.cards || [], bubble);
    } catch (err) {
      addMessage("bot", "Jaanch nahi ho paayi, dobara koshish karein.", "error");
    }
  });
  return f;
}

function renderCards(cards, bubble) {
  if (!cards.length) return;
  const nodes = [];
  for (const card of cards) {
    const render = cardRenderers[card.type] || cardRenderers.default;
    try { nodes.push(render(card)); } catch (err) { console.error("card render failed", err); }
  }
  if (mobileMQ.matches && bubble) {
    const slot = bubble.parentElement.querySelector(".inline-cards");
    slot.replaceChildren(...nodes);
    scrollToBottom(slot);
  } else {
    cardsEl.replaceChildren(...nodes);
    cardsEl.scrollTop = 0;
  }
}

// ---------- replies ----------
function showReply(pending, data) {
  pending.classList.remove("pending");
  pending.textContent = data.reply_text;
  if (data.lang) lastLang = data.lang;
  if (data.audio_b64) {
    const btn = el("button", { class: "replay", type: "button", title: "Dobara sunein", "aria-label": "Jawab dobara sunein" });
    btn.appendChild(icon("i-volume"));
    btn.addEventListener("click", () => playAudio(data.audio_b64));
    pending.appendChild(btn);
    playAudio(data.audio_b64);
  }
  renderCards(data.cards || [], pending);
}
function showError(pending, msg) {
  pending.classList.remove("pending");
  pending.textContent = msg || "Maaf kijiye, abhi jawab nahi mil paaya. Dobara koshish karein.";
  pending.classList.add("error");
}
async function errorDetail(res) {
  try { const j = await res.json(); return typeof j.detail === "string" ? j.detail : null; } catch { return null; }
}
function setBusy(b) {
  busy = b;
  sendBtn.disabled = b;
  if (!recorder) micBtn.disabled = b || !micSupported;
  orbEl?.classList.toggle("thinking", b);
}

async function sendMessage(text) {
  if (busy) return;
  startChat();
  addMessage("user", text);
  const pending = addMessage("bot", "Soch raha hoon…", "pending");
  setBusy(true);
  try {
    const res = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session_id: sessionId }),
    });
    if (!res.ok) return showError(pending, await errorDetail(res));
    showReply(pending, await res.json());
  } catch (err) { showError(pending); console.error(err); }
  finally { setBusy(false); if (!mobileMQ.matches) input.focus({ preventScroll: true }); }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  sendMessage(text);
});

// ---------- suggestions + tabs ----------
let currentFilter = "all";
function renderSuggestions(filter) {
  currentFilter = filter;
  let items = SUGGESTIONS.filter((s) => filter === "all" || s.group === filter);
  if (filter === "all" && mobileMQ.matches) items = HOME_MOBILE.map((t) => SUGGESTIONS.find((s) => s.title === t));
  const nodes = items.map((s) => {
    const b = el("button", { type: "button", class: "chip", "data-q": s.q, "aria-label": s.q });
    b.appendChild(icon(s.icon));
    b.appendChild(el("span", { class: "chip-title" }, s.title));
    b.appendChild(el("span", { class: "chip-text" }, s.q));
    b.appendChild(el("span", { class: "chip-sub" }, s.q));
    b.appendChild(icon("i-arrow", "ic chip-arrow"));
    return b;
  });
  chipsEl.replaceChildren(...nodes);
}
chipsEl.addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (chip) sendMessage(chip.dataset.q);
});

function setFilter(filter, { showWelcome = false } = {}) {
  for (const t of document.querySelectorAll("[data-filter]")) t.classList.toggle("active", t.dataset.filter === filter);
  renderSuggestions(filter);
  if (mobileMQ.matches) {
    // Home brings the welcome screen back; module tabs show that module's suggestions
    if (showWelcome || filter !== "all") document.body.classList.remove("has-chat");
    window.scrollTo({ top: 0, behavior: "smooth" });
  } else {
    input.focus({ preventScroll: true });
  }
}
for (const t of document.querySelectorAll("[data-filter]")) {
  t.addEventListener("click", () => setFilter(t.dataset.filter, { showWelcome: t.dataset.filter === "all" }));
}
mobileMQ.addEventListener("change", () => renderSuggestions(currentFilter));

// ---------- mic recording ----------
const MAX_RECORD_MS = 25000;
const micSupported = !!(navigator.mediaDevices && window.MediaRecorder);
let recorder = null, stopTimer = null;

function pickMime() {
  const types = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
  return types.find((t) => MediaRecorder.isTypeSupported(t)) || "";
}
function setMicIcon(id) {
  micBtn.querySelector("use").setAttribute("href", `#${id}`);
}
async function startRecording() {
  if (busy) return;
  let stream;
  try { stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } }); }
  catch (err) {
    startChat();
    addMessage("bot", "Mic ki anumati nahi mili. Browser settings mein mic allow karein (HTTPS zaroori hai).", "error");
    return;
  }
  if (currentAudio) currentAudio.pause();
  const mimeType = pickMime();
  recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
  const chunks = [];
  recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  recorder.onstop = () => {
    stream.getTracks().forEach((t) => t.stop());
    clearTimeout(stopTimer);
    micBtn.classList.remove("recording");
    orbEl?.classList.remove("listening");
    setMicIcon("i-mic");
    micBtn.setAttribute("aria-label", "Bolkar poochhiye");
    const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
    recorder = null;
    sendVoice(blob);
  };
  recorder.start();
  micBtn.classList.add("recording");
  orbEl?.classList.add("listening");
  setMicIcon("i-stop");
  micBtn.setAttribute("aria-label", "Recording rokein");
  micBtn.title = "Rokne ke liye dabaayein";
  stopTimer = setTimeout(stopRecording, MAX_RECORD_MS);
}
function stopRecording() { if (recorder && recorder.state === "recording") recorder.stop(); }

async function sendVoice(blob) {
  if (blob.size < 1000) return; // accidental tap
  startChat();
  const userBubble = addMessage("user", "🎤 …");
  const pending = addMessage("bot", "Sun raha hoon…", "pending");
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

// ---------- init ----------
renderSuggestions("all");
addMessage("bot", "Johar! Namaste! Main Kisan Saathi hoon. Yojana, fasal ya mausam ke baare mein bolkar ya likhkar poochhiye.");

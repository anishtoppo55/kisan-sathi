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
let busy = false;

// ---------- UI language (toggle: हिंदी | Hinglish) ----------
// Every fixed string on the page lives here. index.html marks elements with data-i18n="key"
// (text) or data-i18n-attr="attr:key,attr:key" (attributes); tests check both languages have every key.
const STRINGS = {
  "hi-en": {
    "lang.label": "Bhasha chunein",
    "nav.home": "Home", "nav.yojana": "Yojana", "nav.fasal": "Fasal", "nav.mausam": "Mausam",
    "info.1": "Voice-first farm assistant\nfor Chhattisgarh farmers",
    "info.2": "Hindi · Hinglish\nbolkar ya likhkar",
    "info.3": "Yojana · Fasal · Mausam\nek jagah",
    "welcome.hello": "Johar! Namaste 🙏", "welcome.pre": "Aaj kya", "welcome.post": "karun?",
    "intro.label": "24/7 Kisan sahayata",
    "head.pre": "Aaj kis cheez mein", "head.em": "madad", "head.post": "karun?",
    "mic.label": "Bolkar poochhiye", "mic.stop": "Recording rokein", "mic.stopTitle": "Rokne ke liye dabaayein",
    "mic.unsupported": "Is browser mein mic support nahi hai",
    "mic.denied": "Mic ki anumati nahi mili. Browser settings mein mic allow karein (HTTPS zaroori hai).",
    "input.placeholder": "Apna sawal likhiye ya boliye…", "input.label": "Apna sawal",
    "send.label": "Bhejein",
    "panel.label": "Parinaam / Results",
    "step1.label": "01 / Boliye", "step1.sub": "Aapki aawaz ko text mein",
    "step2.label": "02 / Samjhe", "step2.sub": "Yojana, fasal, mausam ki jaankari",
    "step3.label": "03 / Suniye", "step3.sub": "Jawab aapki bhasha mein",
    "footer.disclaimer": "Yeh salah sirf jaankari ke liye hai. Yojana ki shartein aur dawa/khaad ki matra apne nazdeeki KVK, RAEO ya krishi vibhag se pushti karein.",
    "bot.welcome": "Johar! Namaste! Main Kisan Saathi hoon. Yojana, fasal ya mausam ke baare mein bolkar ya likhkar poochhiye.",
    "bot.thinking": "Soch raha hoon…", "bot.listening": "Sun raha hoon…",
    "bot.error": "Maaf kijiye, abhi jawab nahi mil paaya. Dobara koshish karein.",
    "bot.nothingHeard": "(kuch sunai nahi diya)",
    "replay.title": "Dobara sunein", "replay.label": "Jawab dobara sunein",
    "card.scheme": "Yojana", "card.docs": "Zaroori dastavez", "card.helpline": "Helpline",
    "card.elig": "Yojana paatrata jaanch", "card.eligTitle": "Aapke liye yojanaen", "card.eligDocs": "Dastavez",
    "card.eligEdit": "Apni jaankari sudharein", "card.recheck": "Dobara jaanchein",
    "card.recheckError": "Jaanch nahi ho paayi, dobara koshish karein.",
    "card.crop": "Fasal salah", "card.stage": "Avastha",
    "card.weather": "Mausam", "card.next7": "agle 7 din", "chart.rain": "Baarish (mm)",
    "card.web": "Web se", "card.webTitle": "Web sources", "card.webNote": "Web se mili jaankari; sarkari website par pushti karein.",
    "seg.yes": "Haan", "seg.no": "Nahi", "seg.unknown": "Pata nahi",
    "pf.owns_land": "Zameen apne naam par hai?", "pf.is_tenant": "Batai / kiraye par kheti?",
    "pf.income_tax_payer": "Income tax bharte hain?", "pf.govt_employee_or_pensioner_10k": "Sarkari naukri / ₹10,000+ pension?",
    "pf.registered_on_state_portal": "Kisan portal / samiti mein panjiyan?", "pf.grows_paddy": "Dhaan ugaate hain?",
    "pf.has_kcc": "KCC pehle se hai?", "pf.acres": "Zameen (acre)",
    "status.eligible": "Paatra", "status.already_enrolled": "Pehle se hai", "status.needs_check": "Jaankari chahiye", "status.not_eligible": "Paatra nahi",
  },
  "hi-IN": {
    "lang.label": "भाषा चुनें",
    "nav.home": "होम", "nav.yojana": "योजना", "nav.fasal": "फसल", "nav.mausam": "मौसम",
    "info.1": "किसानों के लिए आवाज़ से चलने वाला\nछत्तीसगढ़ कृषि सहायक",
    "info.2": "हिंदी · हिंग्लिश\nबोलकर या लिखकर",
    "info.3": "योजना · फसल · मौसम\nएक जगह",
    "welcome.hello": "जोहार! नमस्ते 🙏", "welcome.pre": "आज क्या", "welcome.post": "करूँ?",
    "intro.label": "24/7 किसान सहायता",
    "head.pre": "आज किस चीज़ में", "head.em": "मदद", "head.post": "करूँ?",
    "mic.label": "बोलकर पूछिए", "mic.stop": "रिकॉर्डिंग रोकें", "mic.stopTitle": "रोकने के लिए दबाएँ",
    "mic.unsupported": "इस ब्राउज़र में माइक काम नहीं करता",
    "mic.denied": "माइक की अनुमति नहीं मिली। ब्राउज़र सेटिंग में माइक की अनुमति दें (HTTPS ज़रूरी है)।",
    "input.placeholder": "अपना सवाल लिखिए या बोलिए…", "input.label": "अपना सवाल",
    "send.label": "भेजें",
    "panel.label": "परिणाम",
    "step1.label": "01 / बोलिए", "step1.sub": "आपकी आवाज़ को टेक्स्ट में",
    "step2.label": "02 / समझे", "step2.sub": "योजना, फसल, मौसम की जानकारी",
    "step3.label": "03 / सुनिए", "step3.sub": "जवाब आपकी भाषा में",
    "footer.disclaimer": "यह सलाह सिर्फ़ जानकारी के लिए है। योजना की शर्तें और दवा/खाद की मात्रा अपने नज़दीकी KVK, RAEO या कृषि विभाग से पुष्टि करें।",
    "bot.welcome": "जोहार! नमस्ते! मैं किसान साथी हूँ। योजना, फसल या मौसम के बारे में बोलकर या लिखकर पूछिए।",
    "bot.thinking": "सोच रहा हूँ…", "bot.listening": "सुन रहा हूँ…",
    "bot.error": "माफ़ कीजिए, अभी जवाब नहीं मिल पाया। दोबारा कोशिश करें।",
    "bot.nothingHeard": "(कुछ सुनाई नहीं दिया)",
    "replay.title": "दोबारा सुनें", "replay.label": "जवाब दोबारा सुनें",
    "card.scheme": "योजना", "card.docs": "ज़रूरी दस्तावेज़", "card.helpline": "हेल्पलाइन",
    "card.elig": "योजना पात्रता जाँच", "card.eligTitle": "आपके लिए योजनाएँ", "card.eligDocs": "दस्तावेज़",
    "card.eligEdit": "अपनी जानकारी सुधारें", "card.recheck": "दोबारा जाँचें",
    "card.recheckError": "जाँच नहीं हो पाई, दोबारा कोशिश करें।",
    "card.crop": "फसल सलाह", "card.stage": "अवस्था",
    "card.weather": "मौसम", "card.next7": "अगले 7 दिन", "chart.rain": "बारिश (मिमी)",
    "card.web": "वेब से", "card.webTitle": "वेब स्रोत", "card.webNote": "यह जानकारी वेब से है; सरकारी वेबसाइट पर पुष्टि करें।",
    "seg.yes": "हाँ", "seg.no": "नहीं", "seg.unknown": "पता नहीं",
    "pf.owns_land": "ज़मीन अपने नाम पर है?", "pf.is_tenant": "बटाई / किराये पर खेती?",
    "pf.income_tax_payer": "इनकम टैक्स भरते हैं?", "pf.govt_employee_or_pensioner_10k": "सरकारी नौकरी / ₹10,000+ पेंशन?",
    "pf.registered_on_state_portal": "किसान पोर्टल / समिति में पंजीयन?", "pf.grows_paddy": "धान उगाते हैं?",
    "pf.has_kcc": "KCC पहले से है?", "pf.acres": "ज़मीन (एकड़)",
    "status.eligible": "पात्र", "status.already_enrolled": "पहले से है", "status.needs_check": "जानकारी चाहिए", "status.not_eligible": "पात्र नहीं",
  },
};
const UI_LANGS = ["hi-IN", "hi-en"];
let uiLang = UI_LANGS.includes(localStorage.getItem("ks_lang")) ? localStorage.getItem("ks_lang") : "hi-en";
function t(key) { return STRINGS[uiLang][key] ?? STRINGS["hi-en"][key] ?? key; }

function applyStaticStrings() {
  document.documentElement.lang = uiLang === "hi-IN" ? "hi" : "hi-Latn";
  for (const node of document.querySelectorAll("[data-i18n]")) node.textContent = t(node.dataset.i18n);
  for (const node of document.querySelectorAll("[data-i18n-attr]")) {
    for (const pair of node.dataset.i18nAttr.split(",")) {
      const [attr, key] = pair.split(":");
      node.setAttribute(attr, t(key));
    }
  }
  for (const b of document.querySelectorAll("[data-ui-lang]")) b.setAttribute("aria-pressed", String(b.dataset.uiLang === uiLang));
}

// ---------- example questions (one list drives desktop chips and mobile cards) ----------
const SUGGESTIONS = [
  { id: "elig", group: "yojana", icon: "i-check",
    "hi-en": { title: "Yojana jaanch", q: "Main 2 acre ka kisan hoon, income tax nahi bharta, KCC nahi hai" },
    "hi-IN": { title: "योजना जाँच", q: "मैं 2 एकड़ का किसान हूँ, इनकम टैक्स नहीं भरता, KCC नहीं है" } },
  { id: "pmkisan", group: "yojana", icon: "i-scheme",
    "hi-en": { title: "PM-Kisan", q: "PM-Kisan ke liye kaun se documents chahiye?" },
    "hi-IN": { title: "पीएम किसान", q: "पीएम किसान के लिए कौन से दस्तावेज़ चाहिए?" } },
  { id: "token", group: "yojana", icon: "i-scheme",
    "hi-en": { title: "Dhaan token", q: "Dhaan bechne ke liye token kaise milega?" },
    "hi-IN": { title: "धान टोकन", q: "धान बेचने के लिए टोकन कैसे मिलेगा?" } },
  { id: "chana", group: "fasal", icon: "i-leaf",
    "hi-en": { title: "Fasal salah", q: "Chana mein phool aa rahe hain, sinchai karun?" },
    "hi-IN": { title: "फसल सलाह", q: "चने में फूल आ रहे हैं, सिंचाई करूँ?" } },
  { id: "bph", group: "fasal", icon: "i-leaf",
    "hi-en": { title: "Keet niyantran", q: "Dhaan mein bhura maahu lag gaya hai kya karun?" },
    "hi-IN": { title: "कीट नियंत्रण", q: "धान में भूरा माहू लग गया है, क्या करूँ?" } },
  { id: "mausam", group: "mausam", icon: "i-cloud",
    "hi-en": { title: "Mausam", q: "Bemetara mein agle hafte barish hogi? Gehun mein paani doon?" },
    "hi-IN": { title: "मौसम", q: "बेमेतरा में अगले हफ्ते बारिश होगी? गेहूँ में पानी दूँ?" } },
];
// Home on mobile shows one card per need (2×2); desktop Home shows all chips.
const HOME_MOBILE = ["elig", "pmkisan", "chana", "mausam"];

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
const PROFILE_FIELDS = ["owns_land", "is_tenant", "income_tax_payer", "govt_employee_or_pensioner_10k",
  "registered_on_state_portal", "grows_paddy", "has_kcc"];

const cardRenderers = {
  scheme(card) {
    const d = card.data;
    const { c } = cardShell("i-scheme", t("card.scheme"));
    c.appendChild(el("h3", {}, d.title));
    if (d.benefit) c.appendChild(el("p", { class: "benefit" }, d.benefit));
    if (d.documents?.length) {
      c.appendChild(el("h4", {}, t("card.docs")));
      const row = el("div", { class: "chiprow" });
      for (const doc of d.documents) row.appendChild(el("span", { class: "tag plain" }, doc));
      c.appendChild(row);
    }
    if (d.apply) c.appendChild(metaLine("i-check", d.apply));
    if (d.helpline) c.appendChild(metaLine("i-phone", `${t("card.helpline")}: ${d.helpline}`));
    if (d.link) c.appendChild(metaLine("i-link", safeLink(d.link)));
    return c;
  },

  eligibility(card) {
    const d = card.data;
    const { c } = cardShell("i-check", t("card.elig"));
    c.appendChild(el("h3", {}, t("card.eligTitle")));
    for (const r of d.results || []) {
      const row = el("div", { class: "elig-row" });
      const head = el("div", { class: "elig-head" });
      head.appendChild(el("span", { class: "name" }, r.name));
      head.appendChild(el("span", { class: `status ${r.status}` }, t(`status.${r.status}`)));
      row.appendChild(head);
      if (r.benefit) row.appendChild(el("p", { class: "muted" }, r.benefit));
      for (const reason of r.reasons || []) row.appendChild(el("p", { class: "why" }, `✗ ${reason}`));
      for (const q of r.questions || []) row.appendChild(el("p", { class: "ask" }, `? ${q}`));
      if (r.status === "eligible" && r.documents?.length) row.appendChild(el("p", {}, `${t("card.eligDocs")}: ${r.documents.join(", ")}`));
      for (const n of r.notes || []) row.appendChild(el("p", { class: "muted small" }, n));
      c.appendChild(row);
    }
    c.appendChild(el("h4", {}, t("card.eligEdit")));
    c.appendChild(profileForm(d.profile || {}));
    return c;
  },

  crop(card) {
    const d = card.data;
    const { c } = cardShell("i-leaf", t("card.crop"));
    const tags = el("div", { class: "chiprow" });
    if (d.crop) tags.appendChild(el("span", { class: "tag" }, d.crop));
    if (d.stage) tags.appendChild(el("span", { class: "tag plain" }, `${t("card.stage")}: ${d.stage}`));
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
    const { c } = cardShell("i-cloud", t("card.weather"));
    c.appendChild(el("h3", {}, `${d.place}: ${t("card.next7")}`));
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
    const { c } = cardShell("i-globe", t("card.web"));
    c.appendChild(el("h3", {}, uiLang === "hi-IN" ? t("card.webTitle") : (d.title || t("card.webTitle"))));
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
    c.appendChild(el("p", { class: "muted small" }, t("card.webNote")));
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
        { type: "bar", label: t("chart.rain"), data: days.map((x) => x.rain_mm), backgroundColor: accent + "55", borderColor: accent, borderWidth: 1, borderRadius: 4, yAxisID: "rain" },
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
  const opts = [["true", t("seg.yes")], ["false", t("seg.no")], ["", t("seg.unknown")]];
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
  for (const key of PROFILE_FIELDS) {
    const row = el("div", { class: "pf-row" });
    row.appendChild(el("span", { id: `pf-${key}` }, t(`pf.${key}`)));
    const seg = segmented(key, profile[key]);
    seg.setAttribute("aria-labelledby", `pf-${key}`);
    row.appendChild(seg);
    f.appendChild(row);
  }
  const acresRow = el("div", { class: "pf-row" });
  acresRow.appendChild(el("label", { for: "pf-land_acres" }, t("pf.acres")));
  const acres = el("input", { id: "pf-land_acres", name: "land_acres", type: "number", min: "0", step: "0.1", inputmode: "decimal" });
  if (profile.land_acres != null) acres.value = profile.land_acres;
  acresRow.appendChild(acres);
  f.appendChild(acresRow);
  f.appendChild(el("button", { type: "submit", class: "btn-primary" }, t("card.recheck")));
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
        body: JSON.stringify({ profile: p, session_id: sessionId, lang: uiLang }),
      });
      if (!res.ok) throw new Error(res.status);
      const data = await res.json();
      const bubble = addMessage("bot", data.reply_text);
      renderCards(data.cards || [], bubble);
    } catch (err) {
      addMessage("bot", t("card.recheckError"), "error");
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
  if (data.audio_b64) {
    const btn = el("button", { class: "replay", type: "button", title: t("replay.title"), "aria-label": t("replay.label") });
    btn.appendChild(icon("i-volume"));
    btn.addEventListener("click", () => playAudio(data.audio_b64));
    pending.appendChild(btn);
    playAudio(data.audio_b64);
  }
  renderCards(data.cards || [], pending);
}
function showError(pending, msg) {
  pending.classList.remove("pending");
  pending.textContent = msg || t("bot.error");
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
  const pending = addMessage("bot", t("bot.thinking"), "pending");
  setBusy(true);
  try {
    const res = await fetch("/api/chat", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session_id: sessionId, lang: uiLang }),
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
  if (filter === "all" && mobileMQ.matches) items = HOME_MOBILE.map((id) => SUGGESTIONS.find((s) => s.id === id));
  const nodes = items.map((s) => {
    const { title, q } = s[uiLang];
    const b = el("button", { type: "button", class: "chip", "data-q": q, "aria-label": q });
    b.appendChild(icon(s.icon));
    b.appendChild(el("span", { class: "chip-title" }, title));
    b.appendChild(el("span", { class: "chip-text" }, q));
    b.appendChild(el("span", { class: "chip-sub" }, q));
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
    addMessage("bot", t("mic.denied"), "error");
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
    micBtn.setAttribute("aria-label", t("mic.label"));
    const blob = new Blob(chunks, { type: recorder.mimeType || "audio/webm" });
    recorder = null;
    sendVoice(blob);
  };
  recorder.start();
  micBtn.classList.add("recording");
  orbEl?.classList.add("listening");
  setMicIcon("i-stop");
  micBtn.setAttribute("aria-label", t("mic.stop"));
  micBtn.title = t("mic.stopTitle");
  stopTimer = setTimeout(stopRecording, MAX_RECORD_MS);
}
function stopRecording() { if (recorder && recorder.state === "recording") recorder.stop(); }

async function sendVoice(blob) {
  if (blob.size < 1000) return; // accidental tap
  startChat();
  const userBubble = addMessage("user", "🎤 …");
  const pending = addMessage("bot", t("bot.listening"), "pending");
  setBusy(true);
  const ext = blob.type.includes("ogg") ? "ogg" : blob.type.includes("mp4") ? "m4a" : "webm";
  const fd = new FormData();
  fd.append("audio", blob, `recording.${ext}`);
  fd.append("session_id", sessionId);
  fd.append("lang", uiLang);
  try {
    const res = await fetch("/api/voice", { method: "POST", body: fd });
    if (!res.ok) { userBubble.textContent = "🎤"; return showError(pending, await errorDetail(res)); }
    const data = await res.json();
    userBubble.textContent = data.transcript ? `🎤 ${data.transcript}` : `🎤 ${t("bot.nothingHeard")}`;
    showReply(pending, data);
  } catch (err) { showError(pending); console.error(err); }
  finally { setBusy(false); micBtn.title = t("mic.label"); }
}

if (micSupported) {
  micBtn.disabled = false;
  micBtn.title = t("mic.label");
  micBtn.addEventListener("click", () => (recorder ? stopRecording() : startRecording()));
} else {
  micBtn.title = t("mic.unsupported");
}

// ---------- init ----------
// ---------- language toggle ----------
let welcomeBubble = null;
function setUiLang(lang) {
  if (!UI_LANGS.includes(lang)) return;
  uiLang = lang;
  localStorage.setItem("ks_lang", lang);
  applyStaticStrings();
  renderSuggestions(currentFilter);
  if (!recorder) micBtn.title = micSupported ? t("mic.label") : t("mic.unsupported");
  // The greeting follows the toggle until the conversation starts; later messages keep their language.
  if (welcomeBubble && !document.body.classList.contains("has-chat")) welcomeBubble.textContent = t("bot.welcome");
}
for (const b of document.querySelectorAll("[data-ui-lang]")) b.addEventListener("click", () => setUiLang(b.dataset.uiLang));

// ---------- init ----------
applyStaticStrings();
renderSuggestions("all");
welcomeBubble = addMessage("bot", t("bot.welcome"));
if (micSupported) micBtn.title = t("mic.label");

// Shared helpers: theme, guest session, API
const API = "";
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];

function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2600);
}

// ---- guest identity (no login) ----
let guestId = localStorage.getItem("bf_guest");
if (!guestId) {
  guestId = "Guest-" + Math.floor(1000 + Math.random() * 9000);
  localStorage.setItem("bf_guest", guestId);
}
let sessionId = localStorage.getItem("bf_session") || "";

async function api(path, opts = {}) {
  const r = await fetch(API + path, {
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
    body: opts.body && typeof opts.body !== "string" ? JSON.stringify(opts.body) : opts.body
  });
  if (!r.ok) throw new Error((await r.text()).slice(0, 200));
  return r.json();
}

async function joinSession() {
  try {
    const j = await api("/api/sessions/join", { method: "POST", body: { guest_id: guestId, theme: document.body.dataset.theme || "" } });
    sessionId = j.session_id;
    localStorage.setItem("bf_session", sessionId);
  } catch (e) { /* offline ok */ }
}
async function heartbeat(song) {
  if (!sessionId) return;
  try {
    await api("/api/sessions/heartbeat", { method: "POST", body: { session_id: sessionId, current_song: song || "", theme: document.body.dataset.theme || "" } });
  } catch {}
}
window.addEventListener("beforeunload", () => {
  if (sessionId) navigator.sendBeacon("/api/sessions/leave", JSON.stringify({ session_id: sessionId }));
});

// ---- theme ----
async function loadTheme() {
  try {
    const t = await api("/api/themes/active");
    const root = document.documentElement.style;
    if (t.primary_color) root.setProperty("--primary", t.primary_color);
    if (t.secondary_color) root.setProperty("--secondary", t.secondary_color);
    document.body.dataset.theme = t.name || "";
    document.body.classList.toggle("light", (t.background || "dark") === "light");
    if (t.site_name) $$(".site-name").forEach(el => el.textContent = t.site_name);
    if (t.font_family) document.body.style.fontFamily = t.font_family + ",system-ui,sans-serif";
  } catch {}
}

document.addEventListener("DOMContentLoaded", () => { loadTheme(); joinSession(); setInterval(() => heartbeat(window.__currentSong || ""), 15000); });

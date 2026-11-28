// Arka Trades · alerts (stage 37): the bell, the pop-ups, the sound and browser notifications, on every page.
// The worker writes the alerts on its own timer (8:45 orders, 9:16 stops and targets, stops and targets reached, the 3:00 and 3:15 reminders,
// the day summary and warnings) and, when it is set up, sends the same text to Telegram. This file only reads /api/alerts.
// Nothing here places an order. A member login sees the alerts without the desk's quantities and rupee amounts.
import { S, api, esc, toast } from "./app.js";

const SEEN = "arka_al_seen", SOUND = "arka_al_sound";
const ls = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, String(v)); } catch { /* private mode */ } return null; };
const A = { list: [], last: 0, first: true, open: false, ready: null, why: "", tg: false, busy: false, timer: 0, ctx: null, pops: [] };
const BELL = '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 8 3 8H3s3-1 3-8"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>';
const istNow = () => new Date(Date.now() + 330 * 60000), istHM = (iso) => { const t = new Date(Date.parse(iso) + 330 * 60000), h = t.getUTCHours(); return `${h % 12 || 12}:${String(t.getUTCMinutes()).padStart(2, "0")} ${h < 12 ? "am" : "pm"}`; };
const busyHours = () => { const t = istNow(), d = t.getUTCDay(), m = t.getUTCHours() * 60 + t.getUTCMinutes(); return d >= 1 && d <= 5 && m >= 520 && m <= 965; };      // 8:40 am to 4:05 pm
const seen = () => +ls(SEEN) || 0, soundOn = () => ls(SOUND) !== "off";
const unread = () => A.list.filter((a) => a.id > seen()).length;
const LV = { action: ["a", "Do now"], warn: ["r", "Check"], info: ["", "Info"] };
const KIND = { plan: "Orders", levels: "Levels", reenter: "Swing stops", stop: "Stop", target: "Target", remind: "Reminder", summary: "Summary", health: "Warning", test: "Test" };

export const bell = () => `<button class="ib al-bell" data-act="albell" aria-label="Alerts" title="Alerts"><svg viewBox="0 0 24 24">${BELL}</svg><b class="al-n"${unread() ? "" : " hidden"}>${Math.min(unread(), 99)}</b></button>`;
function badge() { const n = unread(); document.querySelectorAll(".al-n").forEach((b) => { b.hidden = !n; b.textContent = String(Math.min(n, 99)); }); }
function host() {
  if (!document.getElementById("alpanel")) document.body.insertAdjacentHTML("beforeend", '<div id="alpanel"></div><div id="alpop" aria-live="polite"></div>');
  return [document.getElementById("alpanel"), document.getElementById("alpop")];
}
const card = (a, pop) => { const [c, l] = LV[a.level] || LV.info;
  return `<div class="al-c ${a.level || "info"}${a.id > seen() && !pop ? " new" : ""}"><div class="al-h"><span class="chip ${c}">${esc(KIND[a.kind] || l)}</span><small>${esc(a.day === istNow().toISOString().slice(0, 10) ? "" : a.day + " · ")}${esc(istHM(a.ts))}</small>${pop ? `<button class="al-x" data-act="alpopx" data-v="${a.id}" aria-label="Close">×</button>` : ""}</div>
    <b>${esc(String(a.title || "").replace(/^ARKA · /, ""))}</b>${a.body ? `<pre>${esc(pop ? a.body.split("\n").slice(0, 7).join("\n") + (a.body.split("\n").length > 7 ? "\n…" : "") : a.body)}</pre>` : ""}${pop ? `<button class="al-more" data-act="albell">All alerts</button>` : ""}</div>`; };
function paintPanel() {
  const [pn] = host(); if (!A.open) { pn.innerHTML = ""; pn.className = ""; return; }
  const desk = S.me && S.me.role === "admin", perm = typeof Notification === "undefined" ? "none" : Notification.permission;
  const body = A.ready === false ? `<div class="al-e"><b>Alerts are not set up yet</b><small>${esc(A.why || "")}</small></div>` : !A.list.length ? `<div class="al-e"><b>No alerts yet</b><small>The first one comes at 8:45 am on the next trading day: the day's orders with quantities. Then the exact stops and targets at 9:16, each stop or target reached, reminders at 3:00 and 3:15 pm and a summary at about 3:45 pm.</small></div>` : A.list.map((a) => card(a)).join("");
  pn.className = "on";
  pn.innerHTML = `<div class="al-bg" data-act="alclose"></div><div class="al-p" role="dialog" aria-label="Alerts"><div class="al-ph"><b>Alerts</b><small>${A.tg ? "also sent to Telegram" : "website only · Telegram not set up"}</small><button class="ib" data-act="alclose" aria-label="Close"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></svg></button></div>
    <div class="al-tools"><button class="btn sm" data-act="alread">Mark all read</button><button class="btn sm" data-act="alsound">${soundOn() ? "Sound on" : "Sound off"}</button>${perm === "default" ? `<button class="btn sm" data-act="alnotif">Allow notifications</button>` : perm === "denied" ? `<small>Notifications are blocked in this browser</small>` : ""}${desk ? `<button class="btn sm" data-act="altest">Send a test</button>` : ""}</div>
    <div class="al-l">${body}</div><div class="al-f">Paper trading until a book passes its 60-session check. Orders are placed by you in your broker app. Not investment advice.</div></div>`;
}
function paintPops() { const [, pp] = host(); pp.innerHTML = A.pops.map((a) => card(a, true)).join(""); }
function beep(level) {                                      // two short tones for something to do, one for information; only after you have touched the page once
  if (!soundOn() || !A.ctx) return;
  try { const c = A.ctx, t0 = c.currentTime, tones = level === "info" ? [880] : level === "warn" ? [660, 440] : [740, 988];
    tones.forEach((f, i) => { const o = c.createOscillator(), g = c.createGain(); o.type = "sine"; o.frequency.value = f; g.gain.setValueAtTime(0.0001, t0 + i * 0.18); g.gain.exponentialRampToValueAtTime(0.18, t0 + i * 0.18 + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t0 + i * 0.18 + 0.16); o.connect(g).connect(c.destination); o.start(t0 + i * 0.18); o.stop(t0 + i * 0.18 + 0.17); });
  } catch { /* no audio */ }
}
function notify(a) {                                        // a system notification only when this tab is in the background and you allowed it
  try { if (typeof Notification === "undefined" || Notification.permission !== "granted" || !document.hidden) return; new Notification(String(a.title || "Arka alert").replace(/^ARKA · /, "Arka · "), { body: String(a.body || "").split("\n").slice(0, 4).join("\n"), tag: "arka-" + a.id, icon: "/icon-180.png" }); } catch { /* not supported here */ }
}
function pop(a) {
  A.pops = [a, ...A.pops.filter((x) => x.id !== a.id)].slice(0, 3); paintPops(); beep(a.level); notify(a);
  if (a.level === "info") setTimeout(() => { A.pops = A.pops.filter((x) => x.id !== a.id); paintPops(); }, 12000);
  else setTimeout(() => { A.pops = A.pops.filter((x) => x.id !== a.id); paintPops(); }, 300000);
}
export async function poll() {
  if (!S.me || A.busy) return; A.busy = true;
  try {
    const d = await api("/api/alerts" + (A.last ? "?since=" + A.last : ""));
    A.ready = d.ready !== false; A.why = d.why || ""; A.tg = !!d.telegram;
    const fresh = (d.alerts || []).filter((a) => a.id > A.last).sort((x, y) => x.id - y.id);
    if (fresh.length) { A.list = [...fresh.slice().reverse(), ...A.list].slice(0, 80); A.last = Math.max(A.last, ...fresh.map((a) => a.id)); if (!A.first) fresh.forEach(pop); }
    if (A.first && !seen() && A.list.length) { const today = istNow().toISOString().slice(0, 10), old = A.list.find((a) => a.day < today); ls(SEEN, old ? old.id : A.list[A.list.length - 1].id - 1); }      // first visit: today's alerts count as unread
    A.first = false; badge(); if (A.open) paintPanel();
  } catch { /* the next poll tries again */ }
  A.busy = false;
}
function schedule() { clearTimeout(A.timer); A.timer = setTimeout(async () => { await poll(); schedule(); }, busyHours() ? 30000 : 300000); }
export function start() {
  host(); poll(); schedule();
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  const unlock = () => { try { if (!A.ctx) { const C = window.AudioContext || window.webkitAudioContext; if (C) A.ctx = new C(); } if (A.ctx && A.ctx.state === "suspended") A.ctx.resume(); } catch { /* no audio */ } };
  document.addEventListener("pointerdown", unlock, { passive: true }); document.addEventListener("keydown", unlock);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && A.open) { A.open = false; paintPanel(); } });
}
export async function act(name, el) {
  if (name === "albell") { A.open = !A.open; A.pops = []; paintPops(); if (A.open) { const top = A.list[0]; if (top) ls(SEEN, Math.max(seen(), top.id)); paintPanel(); poll(); setTimeout(badge, 0); } else paintPanel(); return true; }
  if (name === "alclose") { A.open = false; paintPanel(); return true; }
  if (name === "alpopx") { A.pops = A.pops.filter((x) => String(x.id) !== String(el.dataset.v)); paintPops(); return true; }
  if (name === "alread") { if (A.list[0]) ls(SEEN, A.list[0].id); badge(); paintPanel(); return true; }
  if (name === "alsound") { ls(SOUND, soundOn() ? "off" : "on"); if (soundOn()) { if (!A.ctx) { try { const C = window.AudioContext || window.webkitAudioContext; if (C) A.ctx = new C(); } catch { /* no audio */ } } beep("action"); } paintPanel(); return true; }
  if (name === "alnotif") { try { await Notification.requestPermission(); } catch { /* not supported */ } paintPanel(); return true; }
  if (name === "altest") {
    try { const r = await api("/api/alerts?op=test", { method: "POST" }); toast(r.ok ? (r.telegram ? "Test sent to the website and Telegram" : "Test sent to the website (Telegram is not set up)") : r.why || "The test could not be stored"); await poll(); }
    catch (e) { toast(e.message || "The test could not be sent"); }
    return true;
  }
  return false;
}

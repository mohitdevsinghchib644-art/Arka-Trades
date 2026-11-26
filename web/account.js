// Arka Trades · Account (app.py _page_account): profile, display, the index the RS line uses, chart settings, data, this device,
// signing out, adding Arka to the home screen, contact. Everything here is kept on this device (the browser), nothing is sent anywhere.
import { S, api, paint, esc, toast } from "./app.js";
import * as SK from "./stock.js";

const PKEY = "arka_profile", LKEY = "arka_layout";
export const profile = () => { try { return { name: "", phone: "", email: "", photo: "", ...JSON.parse(localStorage.getItem(PKEY) || "{}") }; } catch { return { name: "", phone: "", email: "", photo: "" }; } };
export const displayName = () => profile().name || (S.me && S.me.user) || "Trader";
export const layoutPref = () => { try { return localStorage.getItem(LKEY) || "Auto"; } catch { return "Auto"; } };
export function applyLayout() {                                // "Desktop" on a phone: the page is laid out 1280 px wide and zoomed to fit, like "Request desktop site"
  const m = document.querySelector('meta[name="viewport"]'); if (!m) return;
  m.setAttribute("content", layoutPref() === "Desktop" ? "width=1280" : "width=device-width, initial-scale=1, viewport-fit=cover");
}
const railOn = () => { try { return localStorage.getItem("arka_news_rail") !== "off"; } catch { return true; } };
let tpl = null, tplBusy = false, photoMsg = "";
const sec = (t) => `<div class="ac-sec">${t}</div>`, kv = (k, v) => `<div class="ac-kv"><span>${k}</span><b>${v}</b></div>`;
function settingsFrame() {                                     // the chart's own settings panel (chart.html in settings mode), the same as the gear on a chart
  if (!tpl) { if (!tplBusy) { tplBusy = true; fetch("/chart.html").then((r) => r.text()).then((t) => { tpl = t; paint(); }).catch(() => { tplBusy = false; }); } return '<div class="note">Loading…</div>'; }
  const doc = tpl.replace("__LIB__", "").replace("__PAYLOAD__", "{}").replace("__SYM__", '""').replace("__MODE__", "settings").replace("__RANGE__", "6M");
  return `<iframe class="ac-set" title="Chart settings" srcdoc="${esc(doc)}"></iframe>`;
}
export function page() {
  const P = profile(), me = S.me || {}, role = me.role === "admin" ? "Admin" : "Member", w = innerWidth, phone = w < 900, lay = layoutPref();
  const until = me.exp ? new Date(me.exp + 330 * 60000) : null, MON = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");
  const stay = until ? `Yes, until ${until.getUTCDate()} ${MON[until.getUTCMonth()]} ${until.getUTCFullYear()}` : "Yes";
  const cur = SK.rsLabel(), known = Object.keys(SK.RS_INDICES), other = !SK.RS_INDICES[cur] || S.sk.rsOther;
  const left = `${sec("Profile")}<form id="acprof" class="card pad form" novalidate><div class="ac-ph">${P.photo ? `<img src="${esc(P.photo)}" alt="">` : `<i>${esc(displayName().slice(0, 1).toUpperCase())}</i>`}
      <label class="btn sm">Profile photo<input type="file" accept="image/png,image/jpeg" data-ac="photo" hidden></label>${P.photo ? '<button class="btn sm" type="button" data-act="acnophoto">Remove</button>' : ""}${photoMsg ? `<span class="mini">${esc(photoMsg)}</span>` : ""}</div>
      <div class="g2"><label class="f"><span>Name</span><input name="name" value="${esc(P.name)}" placeholder="${esc(me.user || "Trader")}" maxlength="60"></label><label class="f"><span>Phone</span><input name="phone" value="${esc(P.phone)}" inputmode="tel" maxlength="20"></label></div>
      <label class="f"><span>Email</span><input name="email" type="email" value="${esc(P.email)}" maxlength="120"></label><div class="err"></div><button class="btn pri" type="submit">Save profile</button>
      <div class="mini">Kept only on this device. It is not sent to the server or to any other service.</div></form>
    ${sec("Display")}<div class="card pad ac-col"><div><div class="ac-l">Layout</div><div class="seg">${["Auto", "Desktop"].map((x) => `<button class="${x === lay ? "on" : ""}" data-act="aclayout" data-v="${x}">${x === "Desktop" ? "Desktop layout" : "Auto"}</button>`).join("")}</div>
      <div class="mini">Auto uses the phone layout on screens narrower than 900 px. Desktop lays the page out at full width on a phone and zooms it to fit, like “Request desktop site”.</div></div>
      <label class="ac-tg"><input type="checkbox" data-ac="rail"${railOn() ? " checked" : ""}><span>Show the news column next to Home, Watchlist and stock pages (wide screens)</span></label></div>
    ${sec("Relative strength index")}<div class="card pad ac-col"><div class="mini">The RS line on every chart is the stock's price divided by this index. Pick one from the list, or type any index symbol (for example NIFTYMIDSML400).</div>
      <div class="ac-rs"><select data-ac="rsix">${known.map((n) => `<option${!other && n === cur ? " selected" : ""}>${n}</option>`).join("")}<option value="__other"${other ? " selected" : ""}>Another index (type its symbol)</option></select>
      ${other ? `<input data-ac="rsixc" value="${esc(SK.RS_INDICES[cur] ? "" : cur)}" placeholder="e.g. NIFTYMIDSML400, NIFTY_FIN_SERVICE.NS, ^CNXIT" autocomplete="off" spellcheck="false">` : ""}</div>
      <div class="mini">Now: <b>${esc(cur)}</b>${S.sk.rsMsg ? ` · ${esc(S.sk.rsMsg)}` : ""}</div></div>
    ${sec("Chart settings")}<div class="mini ac-cap">How every chart opens on this device: chart type, colours, moving averages, volume, RSI, MACD and levels. Changes save as you make them and apply the next time a chart opens. The gear on any chart opens the same settings.</div>
    <details class="card sc-det"${S.acSet ? " open" : ""} data-acd="1"><summary>Open chart settings</summary><div class="pad">${S.acSet ? settingsFrame() : ""}</div></details>
    ${sec("Data")}<div class="card pad ac-col"><div class="mini">Prices refresh every 30 seconds while NSE is open and when you come back to the app; company fundamentals are kept for 6 hours. The server keeps short shared copies for everyone, which refresh by themselves.</div>
      <div><button class="btn" data-act="acclear">Clear this device's cached data</button></div></div>`;
  const right = `${sec("This device")}<div class="card pad">${kv("Signed in as", `${esc(displayName())} · ${role}`)}${kv("User name", esc(me.user || ""))}${kv("Stay signed in", esc(stay))}${kv("Layout", `${phone ? "Phone" : "Desktop"} · ${w}px wide${lay === "Desktop" ? " · desktop layout chosen" : ""}`)}
      <button class="btn full red" data-act="logout">Sign out on this device</button><div class="mini">To sign out every device at once, change AUTH_SECRET in the Cloudflare secrets.</div></div>
    ${sec("Add Arka to your home screen")}<div class="card pad ac-p"><b>iPhone:</b> open this page in Safari, tap Share, then <i>Add to Home Screen</i>.<br><b>Android:</b> open it in Chrome, tap ⋮, then <i>Add to home screen</i>.<br>The Arka icon is used, and you stay signed in.</div>
    ${sec("Contact")}<div class="card pad ac-p">Questions or feedback: reach the Arka Trades desk through the configured support email.</div>
    <div class="mini ac-ver">Arka Trades · new app · stage 18</div>`;
  return `<div class="ac"><div class="hd"><div><h1>Account</h1><div class="sub">Profile, this device, display and data.</div></div></div><div class="ac-g"><div>${left}</div><div>${right}</div></div></div>`;
}
export async function submit(f) {
  const v = Object.fromEntries(new FormData(f)), P = profile();
  const em = String(v.email || "").trim(); if (em && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(em)) { const e = f.querySelector(".err"); if (e) e.textContent = "That email address does not look right."; return; }
  try { localStorage.setItem(PKEY, JSON.stringify({ ...P, name: String(v.name || "").trim().slice(0, 60), phone: String(v.phone || "").trim().slice(0, 20), email: em })); } catch { return toast("This browser does not allow saving here (private mode)."); }
  document.activeElement && document.activeElement.blur(); toast("Profile saved"); paint(); refreshWho();
}
export function refreshWho() { const el = document.querySelector(".who b"); if (el) el.textContent = displayName(); }
function shrink(file) {                                        // a profile photo is kept as a small 192 px square
  return new Promise((res, rej) => { const img = new Image(), u = URL.createObjectURL(file);
    img.onload = () => { const c = document.createElement("canvas"), n = 192, s = Math.min(img.width, img.height); c.width = c.height = n;
      c.getContext("2d").drawImage(img, (img.width - s) / 2, (img.height - s) / 2, s, s, 0, 0, n, n); URL.revokeObjectURL(u); res(c.toDataURL("image/jpeg", 0.85)); };
    img.onerror = () => { URL.revokeObjectURL(u); rej(new Error("That picture could not be read.")); }; img.src = u; });
}
export async function input(el, ev) {
  const k = el.dataset.ac;
  if (k === "photo" && el.files && el.files[0]) { try { const d = await shrink(el.files[0]); localStorage.setItem(PKEY, JSON.stringify({ ...profile(), photo: d })); photoMsg = "Photo saved"; } catch (e) { photoMsg = e.message; } return paint(); }
  if (k === "rail" && ev === "change") { try { localStorage.setItem("arka_news_rail", el.checked ? "on" : "off"); } catch { /* private mode */ } return; }
  if (k === "rsix" && ev === "change") { if (el.value === "__other") { S.sk.rsOther = true; return paint(); } S.sk.rsOther = false; await SK.setRsIndex(el.value); return paint(); }
  if (k === "rsixc" && ev === "change") { if (el.value.trim()) { await SK.setRsIndex(el.value.trim()); paint(); } return; }
}
export function act(a, el) {
  if (a === "aclayout") { try { localStorage.setItem(LKEY, el.dataset.v); } catch { /* private mode */ } applyLayout(); paint(); return true; }
  if (a === "acnophoto") { try { localStorage.setItem(PKEY, JSON.stringify({ ...profile(), photo: "" })); } catch { /* private mode */ } photoMsg = ""; paint(); return true; }
  if (a === "acclear") { toast("Cleared. Loading fresh data…"); setTimeout(() => location.reload(), 500); return true; }      // everything the app holds in memory is dropped and loaded again
  return false;
}
export function toggle(el) { if (el.dataset.acd) { const was = S.acSet; S.acSet = el.open; if (el.open && !was) paint(); } }

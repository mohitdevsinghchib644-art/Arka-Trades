// Arka Trades · the signal desk page (fno_page.py): Desk, Leaders, Swing, Index, Big players, Evidence. The drawing is in deskview.js.
import { S, api, paint, esc, todayStr } from "./app.js";
import * as V from "./deskview.js";
import * as O from "./omview.js";

const saved = () => { try { return JSON.parse(localStorage.getItem("arka_desk_size") || "{}"); } catch { return {}; } };
export const deskDefaults = () => { const z = saved(); return { tab: "Desk", C: null, chain: null, log: null, sectors: null, capL: z.capL ?? 200000, capS: z.capS ?? 100000, risk: z.risk ?? 1, chartL: "", chartS: "", busy: {}, om: null, omPick: "", omFlt: "All", omExp: {}, omSym: "", omS: {} }; };
export const sizing = () => { const D = S.fd; return { cap_l: Math.max(0, +D.capL || 0), cap_s: Math.max(0, +D.capS || 0), risk: Math.max(0.1, Math.min(5, +D.risk || 1)) / 100 }; };   // typed on the page, kept in this browser only
export async function load() {                               // the small files the Signals workflow writes every evening
  const D = S.fd, get = async (key, path, pick) => { if (D[key] != null || D.busy[key]) return; D.busy[key] = true; try { D[key] = pick(await api(path)); } catch { D[key] = pick({}); } D.busy[key] = false; paint(); };
  get("C", "/api/desk?f=charts", (d) => d || {});
  if (D.tab === "Index") get("chain", "/api/desk?f=chain", (d) => d.rows || []);
  if (D.tab === "Options map") { get("om", "/api/desk?f=om", (d) => (d && d.indexes ? d : { ok: false }));
    const s = D.omSym; if (s && !D.omS[s] && !D.busy["om_" + s]) { D.busy["om_" + s] = true; api("/api/desk?f=oms&s=" + encodeURIComponent(s)).then((d) => { D.omS[s] = d && d.levels ? d : { missing: true }; }).catch(() => { D.omS[s] = { missing: true }; }).then(() => { D.busy["om_" + s] = false; paint(); }); } }
  if (D.tab === "Evidence") get("log", "/api/desk?f=log", (d) => (d.rows || []).filter((r) => r.date));
  if (D.tab === "Leaders" && !D.sectors && !D.busy.sectors) { D.busy.sectors = true; fetch("/sectors.json").then((r) => (r.ok ? r.json() : {})).catch(() => ({})).then((j) => { D.sectors = j; D.busy.sectors = false; paint(); }); }
  if (S.nw.names === null && !D.busy.names) { D.busy.names = true; api("/api/market?f=names").then((n) => { S.nw.names = n || {}; }).catch(() => { S.nw.names = {}; }).then(() => { D.busy.names = false; paint(); }); }
}
const minus = (html) => html.replace(/>([^<]+)</g, (m, tx) => ">" + tx.replace(/(^|[\s(])-(?=\d)/g, "$1−") + "<");      // a real minus sign in the text, never inside a tag
const setup = '<b>The signal files are not in the repository yet.</b><br>1. Add <b>signal_engine.py</b> to the repository (next to app.py).<br>2. Add <b>.github/workflows/signals.yml</b>.<br>' +
  '3. In GitHub open Actions, choose "Signals", press Run workflow and wait for the green tick (about three minutes).<br>The desk fills in on its own after that, and refreshes every weekday evening.';
function sizeBox() {
  const D = S.fd, inp = (l, k, step, max) => `<label class="f"><span>${l}</span><input data-fd="${k}" type="number" min="0" max="${max}" step="${step}" value="${esc(D[k])}" inputmode="decimal"></label>`;
  return `<div class="card pad fd-size"><div class="g3">${inp("Money for the Leaders book (₹)", "capL", 10000, 1000000000)}${inp("Money for swing and index trades (₹)", "capS", 10000, 1000000000)}${inp("Risk on one swing trade (% of that money)", "risk", 0.1, 5)}</div>
    <div class="mini">Quantities on this page are worked out from these numbers. They stay in this browser and are not sent anywhere.</div></div>`;
}
function chartPicker(Sg, C, syms, key, marksFor) {           // candles for one stock from the book, with its entry and stop
  const D = S.fd, list = [...new Set(syms)].filter((s) => C && C.s && C.s[s]);
  if (!list.length) return "";
  const sel = list.includes(D[key]) ? D[key] : list[0], marks = marksFor(sel).filter((m) => V.ok(m[0]));
  return `<div class="fd-pick"><select data-fd="${key}" aria-label="Chart">${list.map((s) => `<option${s === sel ? " selected" : ""}>${esc(s)}</option>`).join("")}</select><a class="btn sm" href="#/s/${encodeURIComponent(sel)}">Open the stock page</a></div>` +
    V.grid(V.pan(sel, `${C.d.length} sessions · candles with the 50-day average`, V.candles(C.d, C.s[sel], marks), "c12 t12"));
}
function optionsMap() {                                      // fno_page._options_map: NIFTY 50, BANK NIFTY and the F&O stocks
  const D = S.fd, M = D.om, wrap = (b) => `<div class="fo">${minus(b)}</div>`;
  if (!M) return `<div class="fd">${V.empty("Reading the stored F&O history…")}</div>`;
  if (!M.ok || !M.indexes || !Object.keys(M.indexes).length) return `<div class="fd">${V.pan("Options map", "", V.empty("<b>The Options map file is not in the repository yet.</b><br>1. Add <b>fno_export.py</b> to the repository (next to app.py).<br>2. Replace <b>.github/workflows/fno-data.yml</b> with the new one.<br>3. In GitHub open Actions, choose “F&amp;O data”, press Run workflow and wait for the green tick.<br>This tab fills in on its own after that, every weekday evening."), "", true)}</div>`;
  const idx = Object.values(M.indexes), picks = idx.map((x) => x.name).concat(["F&O stocks"]), pick = picks.includes(D.omPick) ? D.omPick : idx[0].name;
  const seg = (act, opts, cur) => `<div class="seg fo-seg">${opts.map((o) => `<button class="${o === cur ? "on" : ""}" data-act="${act}" data-v="${esc(o)}">${esc(o)}</button>`).join("")}</div>`;
  let out = seg("fdom", picks, pick);
  if (pick === "F&O stocks") {
    const stocks = M.stocks || [], mine = new Set([...S.wl.rows.map((r) => r.sym), ...((S.wl.desk && S.wl.desk.syms) || [])]), flt = O.FILTERS.includes(D.omFlt) ? D.omFlt : "All";
    out += seg("fdomflt", O.FILTERS, flt) + wrap(O.stocksView(stocks, flt, mine));
    const syms = stocks.map((s) => s.sym).sort(), sel = syms.includes(D.omSym) ? D.omSym : "";
    out += `<div class="fd-pick"><select data-fd="omSym" aria-label="Open one stock"><option value="">Open one stock for its option chain and history</option>${syms.map((s) => `<option${s === sel ? " selected" : ""}>${esc(s)}</option>`).join("")}</select>${sel ? `<a class="btn sm" href="#/s/${encodeURIComponent(sel)}">Open the stock page</a>` : ""}</div>`;
    if (sel) { const d = D.omS[sel]; out += !d ? `<div class="fd">${V.empty("Loading " + esc(sel) + "…")}</div>` : d.missing ? `<div class="fd">${V.empty("No stored option data for " + esc(sel) + " yet.")}</div>` : wrap(O.stockView(sel, d)); }
    return out;
  }
  const x = idx.find((v) => v.name === pick), ch = x.chain || {}; let which = "m";
  if (ch.w) { which = D.omExp[x.sym] === "Weekly" ? "w" : "m"; out += seg("fdomexp", ["Monthly", "Weekly"], which === "w" ? "Weekly" : "Monthly"); }
  return out + wrap(O.indexView(x, which)) + `<div class="note">F&amp;O data to ${V.day(M.asof)}. End of day, from NSE's published files.</div>`;
}
export function page() {
  const D = S.fd, Sg = S.sig, head = (sub) => `<div class="hd"><div><h1>Signals</h1><div class="sub">${sub}</div></div></div>`;
  if (S.sigErr && !Sg) return `<div>${head("")}<div class="card"><div class="empty"><b>The signal file could not be read</b>${esc(S.sigErr)}</div></div></div>`;
  if (!Sg) return `<div>${head("Loading…")}<div class="card"><div class="note">Loading the desk…</div></div></div>`;
  if (Sg.v !== 3) return `<div>${head("")}<div class="fd">${V.pan("Signal desk", "", V.empty(setup), "", true)}</div></div>`;
  const tab = V.TABS.includes(D.tab) ? D.tab : "Desk", C = D.C || {}, names = S.nw.names || {}, sz = sizing(), ld = Sg.leaders, sw = Sg.swing;
  let body = "", extra = "";
  try {
    if (tab === "Desk") body = V.desk(Sg, C, sz, names);
    else if (tab === "Leaders") { body = V.leaders(Sg, C, sz, names, D.sectors);
      extra = chartPicker(Sg, C, ld.holdings.map((h) => h.sym).concat((ld.buy || []).map((r) => r.sym)), "chartL", (s) => [[(ld.holdings.find((h) => h.sym === s) || {}).entry ?? null, "Entry", "var(--tx2)"]]); }
    else if (tab === "Swing") { body = V.swing(Sg, C, sz, names);
      const stops = Object.fromEntries(sw.new.concat(sw.open).map((r) => [r.sym, r.stop])), ents = Object.fromEntries(sw.open.map((r) => [r.sym, r.entry ?? null]));
      extra = chartPicker(Sg, C, sw.new.map((r) => r.sym).concat(sw.open.map((r) => r.sym)), "chartS", (s) => [[ents[s] ?? null, "Entry", "var(--tx2)"], [stops[s] ?? null, "Stop", V.RED]]); }
    else if (tab === "Index") body = V.index(Sg, C, sz, D.chain || []);
    else if (tab === "Big players") body = V.players(Sg, names);
    else if (tab === "Options map") body = "";
    else body = V.evidence(Sg, names, D.log || []);
  } catch (er) { body = V.pan("This view could not be drawn", "", V.empty(`${V.e(er.name)}: ${V.e(String(er.message).slice(0, 200))}. The other tabs still work; running the Signals workflow again usually fixes a half-written file.`), "", true); }
  const tabs = `<div class="seg fd-tabs">${V.TABS.map((x) => `<button class="${x === tab ? "on" : ""}" data-act="fdtab" data-v="${x}">${x}</button>`).join("")}</div>`;
  return `<div class="fdp">${head("Orders for the next open, the books behind them and the evidence · end of day")}<div class="fd">${minus(V.ticker(Sg, todayStr()))}</div>${tabs}${["Desk", "Leaders", "Swing", "Index"].includes(tab) ? sizeBox() : ""}
    ${tab === "Options map" ? optionsMap() : ""}<div class="fd">${minus(body + extra)}<div class="fd-n" style="margin-top:4px">End-of-day signals from fixed rules tested on past prices. They can and do lose money. Not investment advice.</div></div></div>`;
}
export function input(el, ev) {                               // capital, risk or the chart choice changed
  const D = S.fd, k = el.dataset.fd;
  if (ev !== "change") return;
  D[k] = el.type === "number" ? el.value : el.value;
  if (k === "omSym") load();
  if (["capL", "capS", "risk"].includes(k)) { try { localStorage.setItem("arka_desk_size", JSON.stringify({ capL: +D.capL || 0, capS: +D.capS || 0, risk: +D.risk || 1 })); } catch { /* private mode */ } }
  paint();
}
export function act(name, el) {
  const D = S.fd, v = el.dataset.v;
  if (name === "fdtab") { D.tab = v; window.scrollTo(0, 0); paint(); return load(); }
  if (name === "fdom") { D.omPick = v; return paint(); }
  if (name === "fdomflt") { D.omFlt = v; return paint(); }
  if (name === "fdomexp") { const x = Object.values((D.om || {}).indexes || {}).find((i) => i.name === D.omPick) || Object.values((D.om || {}).indexes || {})[0]; if (x) D.omExp[x.sym] = v; return paint(); }
}
export function openOm(sym) { S.fd.omSym = sym; paint(); load(); setTimeout(() => { const el = document.querySelector(".fd-pick"); if (el) el.scrollIntoView({ behavior: "smooth", block: "start" }); }, 60); }

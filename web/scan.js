// Arka Trades · Screener (smart_scan_page.py): Scan, My setups, Backtest. The rules are checked in the browser on the price files the
// "Screener data" job publishes; the rule engine is scanengine.js and the scan / backtest logic is scanrun.js (both checked against the Python file).
import { S, api, paint, esc, toast, todayStr } from "./app.js";
import { fx } from "./rsengine.js";
import * as E from "./scanengine.js";
import * as R from "./scanrun.js";

const TOLS = Object.keys(E.TOLERANCES), tolKey = (t, dflt = "Small (5%)") => (TOLS.includes(E.tolLabel(t)) ? E.tolLabel(t) : dflt);
const blankDraft = () => ({ id: null, name: "", desc: "", pmin: 0, pmax: 99999, owner: null, min_pass: 0, tol: "Small (5%)", can_edit: true });
export const scanDefaults = () => ({ view: "Scan", setups: null, err: "", ai: false, role: "", hasOwner: true, sel: null, busy: {}, prog: null,
  D: { meta: null, raw: { h: {}, x: {} }, fr: {}, extra: {}, nifty: null, deliv: null, live: null, err: "" },
  band: "Setup's own range", cmin: 100, cmax: 1000, uni: "All NSE stocks", liq: {}, near: true, tolRun: {}, past: false, pastDate: "", skip: {}, showRef: false, img: {},
  res: null, tab: "match", sort: "Best fit", psize: 30, pg: 1, picks: {}, open: "", audit: null, aiN: 10, exp: "CSV",
  edit: "new", loaded: undefined, draft: blankDraft(), rules: [], notes: null, ans: {}, tpl: Object.keys(R.TEMPLATES)[0], addType: E.RULE_ORDER[0], newImg: null, exText: "", learn: null, tSym: "", tDate: "", test: null, confirmDel: false,
  bt: { uni: "NIFTY 500", n: 120, liq: 1, tol: {}, res: null } });

// ── small helpers ──
const f = (v, d = 2) => fx(v, d, true), fs = (v, d = 2) => fx(v, d, false, true).replace("-", "−"), pcs = (v, d = 1) => (v == null ? '<span class="mu">—</span>' : `<span class="${v >= 0 ? "up" : "dn"}">${fs(v, d)}%</span>`);
const MONS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");
const dmy = (d, yr = true) => { if (!d) return "—"; const [y, m, dd] = String(d).slice(0, 10).split("-"); return `${dd} ${MONS[+m - 1]}${yr ? " " + y : ""}`; };
const istNow = () => new Date(Date.now() + 330 * 60000), hhmm = (iso) => String(iso || "").slice(11, 16);
const pause = () => new Promise((r) => setTimeout(r, 0));
const calm = () => { const a = document.activeElement; if (a && a.blur && /INPUT|TEXTAREA|SELECT/.test(a.tagName)) a.blur(); };      // leave the box being typed in, so the page can be redrawn
const mimeOf = (b64) => (String(b64).startsWith("/9j/") ? "image/jpeg" : "image/png");
const phone = () => innerWidth < 900;
const sel = (key, opts, cur, lab = (x) => x, attr = "") => `<select data-sc="${key}" ${attr}>${opts.map((o) => `<option value="${esc(o)}"${String(o) === String(cur) ? " selected" : ""}>${esc(lab(o))}</option>`).join("")}</select>`;
const fld = (label, inner, cls = "") => `<label class="f ${cls}"><span>${esc(label)}</span>${inner}</label>`;
const numIn = (key, v, min, max, step, attr = "") => `<input data-sc="${key}" type="number" inputmode="decimal" min="${min}" max="${max}" step="${step}" value="${esc(v)}" ${attr}>`;
const setupsOf = () => S.sc.setups || [], setupById = (id) => setupsOf().find((s) => String(s.id) === String(id)) || null;
const curSetup = () => setupById(S.sc.sel) || setupsOf()[0] || null;
const bandOf = (setup) => { const C = S.sc, p = R.PRICE_PRESETS[C.band]; return p == null ? [Number(setup.price_min) || 0, Number(setup.price_max) || 99999] : p === "custom" ? [Number(C.cmin) || 0, Number(C.cmax) || 0] : p; };
const isList = (u) => !["All NSE stocks", "NIFTY 500"].includes(u);
const prog = (p, msg) => { S.sc.prog = { p: Math.max(0, Math.min(1, p)), msg }; const el = document.getElementById("sc-prog"); if (el) el.outerHTML = progBar(); };
const progBar = () => { const P = S.sc.prog; return P ? `<div id="sc-prog" class="sc-prog"><i style="width:${(P.p * 100).toFixed(0)}%"></i><span>${esc(P.msg)}</span></div>` : '<div id="sc-prog"></div>'; };

// ── data: setups, price files, live candle ──
export async function load(force) {
  const C = S.sc;
  if ((C.setups == null || force) && !C.busy.setups) { C.busy.setups = true;
    try { const d = await api("/api/setups"); C.setups = d.setups || []; C.ai = !!d.ai; C.role = d.role; C.hasOwner = d.has_owner; C.err = ""; if (!setupById(C.sel)) C.sel = C.setups[0] ? C.setups[0].id : null; }
    catch (e) { if (e.message !== "signed out") { C.err = e.message; C.setups = C.setups || []; } }
    C.busy.setups = false; paint(); }
  if (!C.D.meta && !C.busy.meta) { C.busy.meta = true; try { C.D.meta = await api("/api/scan?f=meta"); C.D.err = ""; } catch (e) { if (e.message !== "signed out") C.D.err = e.message; } C.busy.meta = false; paint(); }
}
async function getLive() {                                   // today's candle so far, when it is newer than the evening files
  const D = S.sc.D; try { const L = await api("/api/scan?f=live"); D.live = L && L.q && D.meta && L.at > D.meta.at && L.day >= D.meta.last ? L : null; } catch { D.live = null; }
  if ((D.liveKey || "") !== (D.live ? D.live.at : "")) { D.liveKey = D.live ? D.live.at : ""; D.fr = {}; D.niftyLive = null; }
}
async function getChunks(kind, idxs, lo, hi, label) {
  const D = S.sc.D, want = [...new Set(idxs)].filter((k) => !D.raw[kind][k]); let done = 0;
  for (let i = 0; i < want.length; i += 3) await Promise.all(want.slice(i, i + 3).map(async (k) => { D.raw[kind][k] = await api(`/api/scan?f=${kind}${k}`); done++; prog(lo + ((hi - lo) * done) / want.length, `${label} · file ${done} of ${want.length}`); }));
}
function frameOf(sym, long) {                                // candles for one stock: the last year (plus the year before when asked), with today's live candle
  const D = S.sc.D, key = (long ? "L:" : "S:") + sym; if (key in D.fr) return D.fr[key];
  let F = null; const pos = D.pos ? D.pos[sym] : undefined;
  if (pos != null) { const k = Math.floor(pos / D.meta.size), h = D.raw.h[k], x = long ? D.raw.x[k] : null;
    if (h && h.s[sym]) { F = R.unpack({ d: h.d, s: { [sym]: h.s[sym] } })[sym]; if (x && x.s[sym]) F = R.joinFrames(R.unpack({ d: x.d, s: { [sym]: x.s[sym] } })[sym], F); if (D.live && D.live.q[sym]) F = R.withLive(F, D.live.q[sym], D.live.day); } }
  else if (D.extra[sym]) F = D.extra[sym];
  return (D.fr[key] = F);
}
function liveFrac(F) {                                       // part of today's session done when the last candle is still forming (volume is then projected to a full day)
  const D = S.sc.D, last = F.d[F.d.length - 1]; let at = null;
  if (F.yahoo) { const t = istNow(); if (last === t.toISOString().slice(0, 10) && t.getUTCDay() >= 1 && t.getUTCDay() <= 5) at = t.getUTCHours() * 60 + t.getUTCMinutes(); }
  else if (D.live && last === D.live.day) at = +D.live.at.slice(11, 13) * 60 + +D.live.at.slice(14, 16);
  return at != null && at >= 555 && at <= 930 ? (at - 555) / 375 : null;
}
async function getExtra(syms, lo, hi) {                      // stocks that are not in the price files (ETFs, new listings): straight from Yahoo, six at a time
  const D = S.sc.D, want = [...new Set(syms)].filter((s) => !(s in D.extra)).slice(0, 150);
  for (let i = 0; i < want.length; i += 6) { const part = want.slice(i, i + 6);
    try { const d = await api("/api/history?range=2y&s=" + encodeURIComponent(part.join(","))); for (const h of d.history || []) D.extra[h.sym] = h.ok && h.daily !== false ? { d: h.d, o: h.o, h: h.h, l: h.l, c: h.c, v: h.v, yahoo: true } : null; }
    catch { /* the stock is counted as not priced */ }
    for (const s of part) if (!(s in D.extra)) D.extra[s] = null;
    prog(lo + ((hi - lo) * Math.min(want.length, i + 6)) / want.length, `Prices from Yahoo · ${Math.min(want.length, i + 6)} of ${want.length}`); }
}
async function getNifty() { const D = S.sc.D; if (!D.nifty) { try { const n = await api("/api/scan?f=nifty"); D.nifty = n && n.d ? n : null; } catch { D.nifty = null; } }
  if (!D.nifty) return null; if (D.niftyLive) return D.niftyLive; let N = { d: D.nifty.d, c: D.nifty.c };
  if (D.live && D.live.nifty > 0 && D.live.day >= N.d[N.d.length - 1]) { const cut = N.d[N.d.length - 1] === D.live.day ? N.d.length - 1 : N.d.length; N = { d: N.d.slice(0, cut).concat([D.live.day]), c: N.c.slice(0, cut).concat([D.live.nifty]) }; }
  return (D.niftyLive = N); }
async function getDeliv() { const D = S.sc.D; if (D.deliv === null) { try { const x = await api("/api/scan?f=deliv"); D.deliv = x && x.d ? { d: x.d, s: Object.fromEntries(Object.entries(x.s).map(([k, a]) => [k, a.map((v) => (v == null ? null : v / 100))])) } : false; } catch { D.deliv = false; } } return D.deliv || null; }
async function universe(kind) {                              // -> [symbols, source text]   (get_universe)
  const D = S.sc.D, M = D.meta;
  if (kind === "All NSE stocks") { const out = M.syms.concat(M.miss || []).filter((s) => !(M.only500 || []).includes(s)); return [out, M.all_src || `${out.length} NSE equities`]; }
  if (kind === "NIFTY 500") return [M.n500, M.n500_src || `${M.n500.length} stocks`];
  if (kind === "Your watchlist") { const s = [...new Set(S.wl.rows.map((r) => r.sym))]; return [s, `${s.length} stocks · your watchlist`]; }
  if (kind === "Arka watchlist") { if (!S.wl.desk) { try { S.wl.desk = await api("/api/watchlist?desk=1"); } catch { /* shown as empty */ } } const s = [...new Set((S.wl.desk && S.wl.desk.syms) || [])]; return [s, `${s.length} stocks · Arka watchlist`]; }
  const s = [...new Set(S.pf.lots.map((l) => String(l.sym || "").toUpperCase()).filter(Boolean))].sort(); return [s, `${s.length} stocks · your portfolio`];
}
async function prepare(syms, { long, nifty, deliv }, lo = 0.02, hi = 0.8) {      // everything a scan of these stocks needs -> { frames, nifty, deliv }
  const D = S.sc.D, M = D.meta; if (!D.pos) D.pos = Object.fromEntries(M.syms.map((s, i) => [s, i]));
  await getLive();
  const inFile = syms.filter((s) => D.pos[s] != null), idxs = inFile.map((s) => Math.floor(D.pos[s] / M.size)), mid = lo + (hi - lo) * (long ? 0.5 : 0.9);
  await getChunks("h", idxs, lo, mid, "Loading prices"); if (long) await getChunks("x", idxs, mid, lo + (hi - lo) * 0.9, "Loading older prices");
  const known = new Set(M.miss || []), other = syms.filter((s) => D.pos[s] == null && !known.has(s)); if (other.length) await getExtra(other, lo + (hi - lo) * 0.9, hi);
  return { frames: (s) => frameOf(s, long), nifty: nifty ? await getNifty() : null, deliv: deliv ? await getDeliv() : null };
}
const stamp = () => { const D = S.sc.D, M = D.meta; if (!M) return ""; return D.live ? `Live prices from ${hhmm(D.live.at)} IST today` : `Prices to the close of ${dmy(M.last)}`; };

// ── run a scan ──
async function runScan() {
  const C = S.sc, setup = curSetup(); if (!setup || C.busy.run) return;
  const rules = E.setupRules(setup).map((r, k) => ({ ...r, on: (r.on ?? true) && C.skip[setup.id + ":" + k] !== false })), [pmin, pmax] = bandOf(setup);
  if (pmin > pmax) return toast("The price band is reversed.");
  if (!C.D.meta) return toast(C.D.err || "The price files are still loading.");
  calm(); C.busy.run = true; prog(0.02, "Reading the stock list"); paint();
  try {
    const [uni, src] = await universe(C.uni);
    if (!uni.length) { toast(isList(C.uni) ? `${C.uni} is empty. Add stocks to it first.` : "No stocks to scan."); return; }
    const asof = C.past ? R.parseDate(C.pastDate) || null : null; if (C.past && !asof) { toast("Choose the past date first."); return; }
    const need = R.needs(rules), tol = E.TOLERANCES[C.tolRun[setup.id] || tolKey(E.setupTol(setup))], liq = Number(C.liq[isList(C.uni)] ?? (isList(C.uni) ? 0 : 1)) || 0, t0 = Date.now();
    const data = await prepare(uni, { long: need.long || asof != null, nifty: need.nifty, deliv: need.deliv });
    const res = await R.runScan({ universe: uni, frames: data.frames, rules, pmin, pmax, minTurnover: liq, wantNear: C.near, minPass: E.setupMinPass(setup), asof, tol, nifty: data.nifty, deliv: data.deliv, liveFrac,
      progress: (p, m) => prog(0.8 + 0.2 * p, m), pause });
    res.stats.secs = (Date.now() - t0) / 1000;
    Object.assign(res, { setup_id: setup.id, setup_name: setup.name, source: src, band: [pmin, pmax], rules: rules.filter((r) => r.on).map(E.describe), at: istNow().toISOString(), stamp: stamp(), need });
    C.res = res; C.audit = null; C.pg = 1; C.picks = {}; C.open = ""; C.tab = "match";
  } catch (e) { if (e.message !== "signed out") toast("The scan stopped: " + e.message); }
  finally { C.busy.run = false; C.prog = null; paint(); }
}

// ── drawing: chart, chips, results ──
export function svgChart(F, width = 324, height = 176, n = 60) {      // _svg_chart: candles, volume, EMA 20 and SMA 50
  if (!F || F.c.length < 5) return "";
  const N = F.c.length, a = Math.max(0, N - n), len = N - a, ema = new Array(N), sma = new Array(N).fill(NaN); let y0 = F.c[0], sum = 0;
  for (let i = 0; i < N; i++) { y0 = i ? (1 - 2 / 21) * y0 + (2 / 21) * F.c[i] : F.c[0]; ema[i] = y0; sum += F.c[i]; if (i >= 50) sum -= F.c[i - 50]; if (i >= 49) sma[i] = sum / 50; }
  let lo = Infinity, hi = -Infinity, vmax = 0; for (let i = a; i < N; i++) { lo = Math.min(lo, F.l[i], ema[i], isNaN(sma[i]) ? Infinity : sma[i]); hi = Math.max(hi, F.h[i], ema[i], isNaN(sma[i]) ? -Infinity : sma[i]); vmax = Math.max(vmax, F.v[i] || 0); }
  const px = 6, pw = width - 12, pTop = 6, pBot = height - 52, vTop = height - 44, vBot = height - 4, rng = hi - lo || 1, step = pw / len, bw = Math.max(2, Math.min(6, step * 0.62)), y = (p) => pTop + ((hi - p) / rng) * (pBot - pTop), out = [];
  out.push(`<svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" class="sc-ch">`);
  for (let k = 1; k < 4; k++) { const gy = pTop + ((pBot - pTop) * k) / 4; out.push(`<line x1="0" y1="${gy.toFixed(1)}" x2="${width}" y2="${gy.toFixed(1)}" class="g"/>`); }
  for (let i = a; i < N; i++) { const x = px + (i - a + 0.5) * step, o = F.o[i], c = F.c[i], col = c >= o ? "u" : "d", vv = ((F.v[i] || 0) / (vmax || 1)) * (vBot - vTop);
    out.push(`<line x1="${x.toFixed(1)}" y1="${y(F.h[i]).toFixed(1)}" x2="${x.toFixed(1)}" y2="${y(F.l[i]).toFixed(1)}" class="${col}"/><rect x="${(x - bw / 2).toFixed(1)}" y="${Math.min(y(o), y(c)).toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1.2, Math.abs(y(c) - y(o))).toFixed(1)}" class="${col}"/>` +
      `<rect x="${(x - bw / 2).toFixed(1)}" y="${(vBot - vv).toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1, vv).toFixed(1)}" class="${col} v"/>`); }
  for (const [arr, cl] of [[ema, "e"], [sma, "s"]]) { const pts = []; for (let i = a; i < N; i++) if (!isNaN(arr[i])) pts.push(`${(px + (i - a + 0.5) * step).toFixed(1)},${y(arr[i]).toFixed(1)}`); if (pts.length > 1) out.push(`<polyline points="${pts.join(" ")}" class="${cl}"/>`); }
  out.push(`<line x1="0" y1="${vTop - 4}" x2="${width}" y2="${vTop - 4}" class="g"/><text x="${width - 4}" y="14" text-anchor="end">${f(hi)}</text><text x="${width - 4}" y="${pBot - 3}" text-anchor="end">${f(lo)}</text></svg>`);
  return out.join("");
}
const fitHtml = (fit) => `<span class="sc-fit ${fit >= 90 ? "a" : fit >= 75 ? "b" : fit >= 55 ? "c" : "d"}" style="--w:${fit}%"><i></i>${fit}</span>`;
const chipHtml = (ck) => `<span class="sc-chip ${ck.close ? "cl" : ck.ok ? "ok" : "no"}" title="${esc(ck.label + ": " + ck.detail)}">${ck.close ? "≈" : ck.ok ? "✓" : "✗"} ${esc(ck.close && ck.chip.startsWith("≈ ") ? ck.chip.slice(2) : ck.chip)}</span>`;
const mineSet = () => new Set(S.wl.rows.map((r) => r.sym));
function detail(r, near) {                                   // the opened row: chart, every rule with its real value, links
  const rows = r.checks.map((ck) => `<tr class="${ck.close ? "cl" : ck.ok ? "ok" : "no"}"><td>${ck.close ? "≈" : ck.ok ? "✓" : "✗"}</td><td>${esc(ck.desc)}</td><td>${esc(ck.detail)}</td></tr>`).join("");
  return `<div class="sc-open"><div class="ch"><div class="cap"><b>${esc(r.symbol)}</b> · 60 candles · <span class="e">EMA 20</span> · <span class="s">SMA 50</span></div>${svgChart(r.chart, 520, 250)}</div>
    <div class="rl"><div class="tw"><table class="sc-t rules"><thead><tr><th></th><th>Rule</th><th>What the stock shows</th></tr></thead><tbody>${rows}</tbody></table></div>
    ${near && r.miss ? `<div class="mini">${esc(r.miss)}</div>` : ""}<div class="acts"><a class="btn sm" href="#/s/${encodeURIComponent(r.symbol)}">Open the stock page</a></div></div></div>`;
}
function resultsTable(rows, near, past, audit) {
  const C = S.sc, wl = mineSet(), head = ["", "#", "Stock", "Fit", past ? "Close" : "Price", "Day", "Turnover", "Vol ×", "RSI"].concat(past ? ["Since then"] : [], ["Rule checks"], near ? ["Missed rule"] : [], audit ? ["AI"] : []), right = new Set([4, 5, 6, 7, 8].concat(past ? [9] : []));
  const body = rows.map((r, i) => { const open = C.open === r.symbol, a = audit && audit[r.symbol];
    let chips = r.checks.map(chipHtml).join(""); if (r.total && (near || r.passed !== r.total)) chips = `<span class="sc-chip cl">${r.passed}/${r.total}</span>` + chips;
    const fl = r.fails[0], cells = [`<td class="ck">${wl.has(r.symbol) ? '<span class="in" title="In your watchlist">✓</span>' : `<input type="checkbox" data-sc="pick.${esc(r.symbol)}"${C.picks[r.symbol] ? " checked" : ""} aria-label="Select ${esc(r.symbol)}">`}</td>`,
      `<td class="mu">${(C.pg - 1) * C.psize + i + 1}</td>`, `<td><button class="sc-sym" data-act="scopen" data-s="${esc(r.symbol)}">${esc(r.symbol)}<span class="pop" data-pop="${esc(r.symbol)}"></span></button></td>`, `<td>${fitHtml(r.fit || 0)}</td>`,
      `<td class="r">₹${f(r.close)}</td>`, `<td class="r ${r.chg >= 0 ? "up" : "dn"}">${r.chg >= 0 ? "▲" : "▼"} ${f(Math.abs(r.chg))}%</td>`, `<td class="r">₹${f(r.turnover, 1)} Cr</td>`,
      `<td class="r">${r.vol_x == null ? "—" : f(r.vol_x) + "×"}${r.projected ? "*" : ""}</td>`, `<td class="r">${r.rsi == null ? "—" : f(r.rsi, 0)}</td>`];
    if (past) cells.push(r.since == null ? '<td class="r mu">—</td>' : `<td class="r ${r.since >= 0 ? "up" : "dn"}" title="${r.since_days || 0} sessions later">${fs(r.since, 1)}%</td>`);
    cells.push(`<td class="chips">${chips}</td>`);
    if (near) cells.push(fl ? `<td class="miss"><b>${esc(fl.label)}</b><br>${esc(fl.detail)}${r.miss ? `<br><span class="mu">${esc(r.miss)}</span>` : ""}</td>` : "<td></td>");
    if (audit) cells.push(a ? `<td><span class="sc-ai ${a.score >= 8 ? "up" : a.score >= 6 ? "cy" : "mu"}" title="${esc(a.caption)}">${a.score}/10</span></td>` : '<td class="mu">—</td>');
    return `<tr class="${open ? "on" : ""}">${cells.join("")}</tr>` + (open ? `<tr class="det"><td colspan="${head.length}">${detail(r, near)}</td></tr>` : ""); }).join("");
  return `<div class="card"><div class="tw"><table class="sc-t res"><thead><tr>${head.map((h, i) => `<th class="${right.has(i) ? "r" : ""}">${h}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div></div>`;
}
function resultsCards(rows, near, past) {                    // phone: one card per stock
  const C = S.sc, wl = mineSet();
  return `<div class="sc-cards">${rows.map((r) => { const open = C.open === r.symbol, fl = near && r.fails[0];
    return `<div class="card sc-rc${open ? " on" : ""}"><div class="top"><label class="ck">${wl.has(r.symbol) ? '<span class="in">✓</span>' : `<input type="checkbox" data-sc="pick.${esc(r.symbol)}"${C.picks[r.symbol] ? " checked" : ""} aria-label="Select ${esc(r.symbol)}">`}</label>
      <button class="mid" data-act="scopen" data-s="${esc(r.symbol)}"><b>${esc(r.symbol)}${wl.has(r.symbol) ? ' <small>in watchlist</small>' : ""}</b><span>Fit ${fitHtml(r.fit || 0)} · RSI ${r.rsi == null ? "—" : f(r.rsi, 0)} · ₹${f(r.turnover, 1)} Cr a day${past && r.since != null ? ` · since then <i class="${r.since >= 0 ? "up" : "dn"}">${fs(r.since, 1)}%</i>` : ""}</span></button>
      <div class="px">₹${f(r.close)}<small class="${r.chg >= 0 ? "up" : "dn"}">${fs(r.chg)}%</small></div></div>
      <div class="chips">${r.checks.map(chipHtml).join("")}</div>${fl ? `<div class="miss">Missed: <b>${esc(fl.label)}</b> · ${esc(fl.detail)}</div>` : ""}${open ? detail(r, near) : ""}</div>`; }).join("")}</div>`;
}
const kpi = (label, value, cls = "") => `<div class="sc-kpi ${cls}"><small>${esc(label)}</small><b>${value}</b></div>`;
const simpleTable = (head, rows, cls = "") => `<div class="tw"><table class="sc-t ${cls}"><thead><tr>${head.map((h, i) => `<th class="${i ? "r" : ""}">${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => "<tr>" + r.map((c, i) => `<td class="${i ? "r" : ""}">${c}</td>`).join("") + "</tr>").join("")}</tbody></table></div>`;

function results() {
  const C = S.sc, res = C.res; if (!res) return "";
  const St = res.stats, near = C.tab === "near", past = !!res.past, all = R.sortRows(near ? res.near : res.match, C.sort), pages = Math.max(1, Math.ceil(all.length / C.psize)), pg = Math.min(C.pg, pages), rows = all.slice((pg - 1) * C.psize, pg * C.psize);
  C.pg = pg;
  let out = `<h2>Results · ${esc(res.setup_name)} <span>${esc(res.source)} · ₹${f(res.band[0], 0)}–${f(res.band[1], 0)} · scanned ${dmy(res.at, false)} ${hhmm(res.at)} IST in ${f(St.secs, 1)}s</span></h2>
    <div class="sc-kpis">${kpi("Stocks in the list", f(St.universe, 0))}${kpi("With prices", f(St.priced, 0))}${kpi("In the price band", f(St.in_band, 0))}${kpi("Liquid and checked", f(St.liquid, 0))}
      ${kpi("Matches", `<span class="up">${St.match}</span>${res.tol ? `<i>${St.exact || 0} exact</i>` : ""}`, "g")}${kpi("Near matches", `<span class="am">${St.near}</span>`, "a")}</div>
    <div class="mini sc-line">${res.asof ? (past ? "Past-date scan as of the close of " : "Prices as of ") + dmy(res.asof) : ""}${!past && res.stamp ? " · " + esc(res.stamp) : ""}${res.live ? " · * volume projected to a full day (market open)" : ""}${St.no_data ? ` · ${St.no_data} stocks had too little price history (new listings or suspended) and were skipped` : ""}</div>`;
  if (res.deliv_missing) out += `<div class="card sc-warn">Delivery data is not in the price files yet, so the delivery rule failed for every stock. It appears after the “Market data” job and then the “Screener data” job have both run. Until then, untick that rule for this run.</div>`;
  const fun = res.funnel || [];
  if (fun.length && St.liquid) { const tight = fun.reduce((a, b) => (b.pass < a.pass ? b : a));
    out += `<details class="card sc-det"><summary>Which rule removes the most stocks <span>strictest: ${esc(tight.desc.slice(0, 70))} (${tight.pass} of ${St.liquid} pass)</span></summary>
      ${simpleTable(["Rule", "Stocks that pass", "% of checked", "Fail only this rule"], fun.map((x) => [esc(x.desc), f(x.pass, 0), f((x.pass / St.liquid) * 100, 0) + "%", `<span class="${x.only ? "am" : "mu"}">${f(x.only, 0)}</span>`]), "wrap1")}
      <div class="note">“Fail only this rule” = stocks you would gain by removing or widening that one rule. A rule that almost nothing passes is usually the number to look at first.</div></details>`; }
  out += `<div class="sc-bar"><div class="seg"><button class="${near ? "" : "on"}" data-act="sctab" data-v="match">Matches (${St.match})</button><button class="${near ? "on" : ""}" data-act="sctab" data-v="near">Near matches (${St.near})</button></div>
    <div class="r">${sel("k.sort", Object.keys(R.SORTS), C.sort, (x) => "Sort: " + x, 'class="sel" aria-label="Sort"')}${sel("k.psize", [30, 60, 100], C.psize, (x) => x + " per page", 'class="sel" aria-label="Rows per page"')}</div></div>`;
  if (!all.length) return out + `<div class="card"><div class="empty">${near ? "<b>No near matches</b>No stock misses by exactly one rule." : "<b>No stock passes every rule today</b>Look at the near matches, widen a range, or untick a rule for this run."}</div></div>`;
  out += phone() ? resultsCards(rows, near, past) : resultsTable(rows, near, past, C.audit && !near ? C.audit : null);
  if (pages > 1) out += `<div class="sc-pg"><button class="btn sm" data-act="scpg" data-v="${pg - 1}"${pg <= 1 ? " disabled" : ""}>Previous</button><span>Page ${pg} of ${pages}</span><button class="btn sm" data-act="scpg" data-v="${pg + 1}"${pg >= pages ? " disabled" : ""}>Next</button></div>`;
  out += `<div class="mini sc-line">Fit 100 = every rule passed exactly · ≈ = passed within the tolerance · tap a stock for its chart and the exact value of every rule${res.live ? " · Vol × * = projected to a full day" : ""}</div>`;
  const wl = mineSet(), fresh = all.map((r) => r.symbol).filter((s) => !wl.has(s)), chosen = fresh.filter((s) => C.picks[s]);
  out += `<div class="card pad sc-acts"><button class="btn pri" data-act="scadd"${chosen.length ? "" : " disabled"}>${chosen.length ? `Add ${chosen.length} to watchlist` : "Tick stocks to add them"}</button>
    <button class="btn" data-act="scaddall"${fresh.length ? "" : " disabled"}>Add all ${fresh.length} ${near ? "near " : ""}matches</button><span class="sp"></span>
    ${sel("k.exp", ["CSV", "TradingView list"], C.exp, (x) => x, 'class="sel" aria-label="Export format"')}<button class="btn" data-act="scexport">Download</button></div>`;
  if (!near) out += auditBox(all);
  return out;
}
function auditBox(all) {                                     // optional: compare the matches with the reference picture
  const C = S.sc, A = C.audit || {}, n = Math.max(1, Math.min(40, all.length, +C.aiN || 10));
  const cards = all.filter((r) => A[r.symbol]).sort((a, b) => A[b.symbol].score - A[a.symbol].score).map((r) => { const a = A[r.symbol];
    return `<div class="sc-au ${a.score >= 8 ? "g" : a.score >= 6 ? "c" : ""}"><div class="h"><b>${esc(r.symbol)}</b><span>${a.score}/10</span></div><div class="t">${esc(a.caption)}</div>${a.matches.map((m) => `<div class="up">+ ${esc(m)}</div>`).join("")}${a.mismatches.map((m) => `<div class="dn">− ${esc(m)}</div>`).join("")}</div>`; }).join("");
  return `<details class="card sc-det"${C.auditOpen ? " open" : ""} data-scd="auditOpen"><summary>AI chart check (optional) <span>compare the matches with your reference picture</span></summary><div class="pad">
    ${!C.ai ? '<div class="mini">Add the secret GEMINI_KEY in the Cloudflare project settings to use this. The rule scan above works without it.</div>' :
      `<div class="sc-row"><label class="f"><span>Check the top</span>${numIn("k.aiN", n, 1, Math.min(40, all.length), 1)}</label><button class="btn" data-act="scaudit"${C.busy.audit ? " disabled" : ""}>${C.busy.audit ? "Checking…" : `Run the AI check on ${n} stocks`}</button></div>
       <div class="mini">One AI call per stock, in the current order. Only the stock's public price chart, your setup description and your reference picture are sent. Results are kept until you close the page, so a stock is never checked twice. The AI score never overrides a failed rule.</div>${progBar()}`}
    ${cards ? `<div class="sc-aus">${cards}</div>` : ""}</div></details>`;
}

// ── page: Scan ──
function scanView() {
  const C = S.sc, list = setupsOf();
  if (!list.length) return `<div class="card"><div class="empty"><b>No setups yet</b>Open My setups, describe your setup, press Build rules and save it.</div></div>`;
  const setup = curSetup(), rules = E.setupRules(setup), desk = (s) => (s.owner || "admin") === "admin" && C.role !== "admin", lst = isList(C.uni);
  if (!rules.length) return `<div class="card pad">${fld("Setup", sel("k.sel", list.map((s) => s.id), setup.id, (id) => setupById(id).name + (desk(setupById(id)) ? "  ·  desk" : "")))}<div class="empty"><b>This setup has no rules yet</b>Open My setups and build them.</div></div>`;
  const [pmin, pmax] = bandOf(setup), tk = C.tolRun[setup.id] || tolKey(E.setupTol(setup)), img = C.showRef ? C.img[setup.id] : null, mp = E.setupMinPass(setup), nOn = rules.filter((r) => r.on ?? true).length;
  return `<div class="card pad sc-form">
      <div class="g2x">${fld("Setup", sel("k.sel", list.map((s) => s.id), setup.id, (id) => setupById(id).name + (desk(setupById(id)) ? "  ·  desk" : "")))}<label class="f ck"><input type="checkbox" data-sc="k.showRef"${C.showRef ? " checked" : ""}><span>Show the reference chart</span></label></div>
      ${C.showRef ? (img === undefined ? '<div class="mini">Loading the picture…</div>' : img ? `<img class="sc-ref" alt="Reference chart" src="data:image/png;base64,${img}">` : '<div class="mini">This setup has no reference picture.</div>') : ""}
      <div class="sc-rules"><div class="h"><b>${rules.length} rules</b><span>a stock must pass ${!mp || mp >= nOn ? "all of them" : `at least ${mp} of ${nOn}`} · price ₹${f(Number(setup.price_min) || 0, 0)}–${f(Number(setup.price_max) || 99999, 0)} · untick a rule to skip it for this run only</span></div>
        ${rules.map((r, k) => ((r.on ?? true) ? `<label class="rl"><input type="checkbox" data-sc="skip.${setup.id}:${k}"${C.skip[setup.id + ":" + k] !== false ? " checked" : ""}><i>${k + 1}</i><span>${esc(E.describe(r))}</span></label>` : `<div class="rl off"><i>${k + 1}</i><span>${esc(E.describe(r))} · off</span></div>`)).join("")}</div>
      <div class="g4x">${fld("Price band", sel("k.band", Object.keys(R.PRICE_PRESETS), C.band))}${fld("Search in", sel("k.uni", R.UNIVERSES, C.uni))}
        ${fld("Minimum turnover (₹ Cr a day)", numIn("liq." + lst, C.liq[lst] ?? (lst ? 0 : 1), 0, 500, 0.5, 'title="Average daily traded value over 20 days. Removes stocks that barely trade."'))}
        ${fld("Close enough", sel("tolrun." + setup.id, TOLS, tk, (x) => x, 'title="How far a number may miss and still pass (shown as ≈, ranked below exact matches)."'))}</div>
      ${C.band === "Custom" ? `<div class="g4x">${fld("From ₹", numIn("k.cmin", C.cmin, 0, 999999, 10))}${fld("To ₹", numIn("k.cmax", C.cmax, 0, 999999, 10))}</div>` : ""}
      <div class="g4x cks"><label class="f ck"><input type="checkbox" data-sc="k.near"${C.near ? " checked" : ""}><span>Show near matches <small>stocks that miss by exactly one rule</small></span></label>
        <label class="f ck"><input type="checkbox" data-sc="k.past"${C.past ? " checked" : ""}><span>Scan a past date <small>what the scan found then, and the move since</small></span></label>
        ${C.past ? fld("Date", `<input type="date" data-sc="k.pastDate" value="${esc(C.pastDate)}" max="${todayStr()}">`) : ""}</div>
      ${C.past ? '<div class="mini">Past-date scan: stocks are picked from a wider price band today (half to double) and then checked against your band on that date. Stocks delisted since then are not included.</div>' : ""}
      <div class="mini">Searching ₹${f(pmin, 0)} – ₹${f(pmax, 0)}. ${C.D.meta ? esc(stamp()) + ` · the evening files were built ${dmy(C.D.meta.at, false)} ${hhmm(C.D.meta.at)} IST.` : C.D.err ? `<span class="dn">${esc(C.D.err)}</span>` : "Loading the price files…"} The first scan downloads the prices (a few seconds); the next ones are instant.</div>
      <button class="btn pri full" data-act="scrun"${C.busy.run || !C.D.meta ? " disabled" : ""}>${C.busy.run ? "Scanning…" : "Run scan · " + esc(setup.name)}</button>${progBar()}</div>${results()}`;
}

// ── page: My setups ──
function loadDraft(setup) {                                  // put a setup (or a blank one) into the editor
  const C = S.sc;
  if (setup) { C.rules = E.setupRules(setup); C.draft = { id: setup.id, name: setup.name || "", desc: setup.visual_rules || "", pmin: Number(setup.price_min) || 0, pmax: Math.min(Number(setup.price_max) || 99999, 999999), owner: setup.owner, min_pass: E.setupMinPass(setup), tol: tolKey(E.setupTol(setup)), can_edit: setup.can_edit !== false }; }
  else { C.rules = []; C.draft = blankDraft(); }
  C.notes = null; C.learn = null; C.ans = {}; C.newImg = null; C.test = null; C.confirmDel = false;
}
function ruleEditor(r, i, n) {
  const meta = E.RULES[r.type], cell = ([name, label, kind, , a, b]) => { const key = `r.${r._id}.${name}`, v = r[name];
    return kind === "b" ? `<label class="f ck"><input type="checkbox" data-sc="${key}"${v ? " checked" : ""}><span>${esc(label)}</span></label>` : fld(label, kind === "s" ? sel(key, a, v) : numIn(key, v, a, b, kind === "i" ? 1 : b <= 5 ? 0.05 : 0.5)); };
  const used = [r.ago ? `${r.ago} candles ago` : "", r.tf === "weekly" ? "weekly" : "", (r.grp ?? "—") !== "—" ? `group ${r.grp}` : "", r.must ? "required" : ""].filter(Boolean);
  return `<div class="card sc-re${(r.on ?? true) ? "" : " off"}"><div class="h"><div><b>${i + 1}. ${esc(meta.label)}</b><span id="scd-${r._id}">${esc(E.describe(r))}</span></div>
      <div class="bt"><label class="sw"><input type="checkbox" data-sc="r.${r._id}.on"${(r.on ?? true) ? " checked" : ""}><i></i><span>On</span></label><button class="ib2" data-act="scrup" data-id="${r._id}" title="Move up"${i ? "" : " disabled"}>↑</button><button class="ib2" data-act="scrdn" data-id="${r._id}" title="Move down"${i < n - 1 ? "" : " disabled"}>↓</button><button class="ib2" data-act="scrdel" data-id="${r._id}" title="Remove this rule">✕</button></div></div>
    ${meta.fields.length ? `<div class="g4x">${meta.fields.map(cell).join("")}</div>` : ""}
    <details class="more"${S.sc.moreOpen && S.sc.moreOpen[r._id] ? " open" : ""} data-scm="${r._id}"><summary>More options: earlier candle · weekly · either-or · always required${used.length ? ` <b>(${esc(used.join(", "))})</b>` : ""}</summary><div class="g4x">${E.COMMON.map(cell).join("")}</div>
      <div class="mini">Either-or: rules with the same letter count as one rule, and one of them passing is enough. Always required only matters when the setup needs fewer than all rules to pass.</div></details></div>`;
}
function plainBox() { const C = S.sc, o = R.plainSummary(C.rules, E.TOLERANCES[C.draft.tol] ?? E.DEFAULT_TOL, +C.draft.min_pass || 0); return o ? `<div class="h"><b>How the scanner reads your setup</b><span>a stock must pass ${o.need}</span></div><ol>${o.items.map((x) => `<li>${esc(x)}</li>`).join("")}</ol><div class="mini">${esc(o.foot)}</div>` : ""; }
const fmtMeas = (v) => (v == null ? '<span class="mu">—</span>' : typeof v === "boolean" ? `<span class="${v ? "up" : "mu"}">${v ? "yes" : "no"}</span>` : Math.abs(v) < 10 ? f(v, 2) : f(v, 1));
function learnBox(editable) {
  const C = S.sc, L = C.learn; let out = "";
  if (L) {
    if (L.bad.length) out += `<div class="sc-warn in">Could not read: ${esc(L.bad.join(" · "))}. Write each line as the symbol and a date, for example TATAPOWER 12 Aug 2026.</div>`;
    if (L.missing.length) out += `<div class="sc-warn in">No price history on that date for: ${esc(L.missing.join(" · "))}</div>`;
    if (L.labels.length) {
      out += `<h3>What the scanner measured on each example</h3>${simpleTable(["Measure"].concat(L.labels), Object.entries(L.meas).map(([t, row]) => [esc(t)].concat(L.labels.map((lab) => fmtMeas(row[lab])))))}`;
      if (L.cur.length) out += `<h3>Your current rules on these examples</h3>${simpleTable(["Rule"].concat(L.labels), L.cur.map(([d, oks, whys]) => [esc(d)].concat(oks.map((ok, j) => `<span class="${ok ? "up" : "dn"}" title="${esc(whys[j])}">${ok ? "✓ pass" : "✗ fail"}</span>`))), "wrap1")}
        <div class="mini">A ✗ here means your current rule rejects one of your own examples. Hover it for the real value, then widen that rule.</div>`;
      out += `<h3>Proposed rules · ${L.rules.length} · every example passes all of them</h3><div class="sc-rules plain">${L.rules.map((r, i) => `<div class="rl"><i>${i + 1}</i><span>${esc(E.describe(r))}</span></div>`).join("")}</div>
        <div class="mini">Ranges are stretched a little beyond your examples. With few examples they can still be too narrow or too wide: remove rules that are not part of your idea, then check the setup in Backtest.</div>
        ${editable && L.rules.length ? `<button class="btn" data-act="sclearnuse">Use these ${L.rules.length} rules (replaces the rules below)</button>` : ""}`;
    }
  } else out = '<div class="mini">Price history goes back about two years. Three or more examples give much better ranges than one.</div>';
  return `<details class="card sc-det"${C.learnOpen || L ? " open" : ""} data-scd="learnOpen"><summary>Learn from examples <span>not sure about the numbers? show the scanner past cases of your setup</span></summary><div class="pad">
    <div class="mini">One per line, as symbol and date (the day you would have bought). The scanner measures each one and proposes rules whose ranges cover all of them.</div>
    <textarea data-sc="k.exText" rows="4" placeholder="TATAPOWER 12 Aug 2026&#10;BEL 3 Sep 2026&#10;HAL 2026-10-07">${esc(C.exText)}</textarea>
    <button class="btn pri" data-act="sclearn"${C.busy.learn ? " disabled" : ""}>${C.busy.learn ? "Measuring…" : "Measure these examples"}</button>${out}</div></details>`;
}
function testBox() {
  const C = S.sc, T = C.test; let out = "";
  if (T && T.err) out = `<div class="sc-warn in">${esc(T.err)}</div>`;
  else if (T) { const res = T.res, ok = res.status === "match", nfail = res.checks.filter((ck) => !ck.ok).length, pmin = +C.draft.pmin || 0, pmax = +C.draft.pmax || 0, inside = pmin <= res.close && res.close <= pmax;
    out = `<div class="sc-test"><b>${esc(res.symbol)}</b><span class="${ok ? "up" : res.status === "near" ? "am" : "dn"}">${ok ? "Match" : res.status === "near" ? "Near match" : `${nfail} rules failed`} · ${res.passed}/${res.total} passed · as of ${dmy(res.date)}</span>
      <span class="mu">₹${f(res.close)} · ${fs(res.chg)}% · ${inside ? "inside" : "outside"} the ₹${f(pmin, 0)}–${f(pmax, 0)} band · turnover ₹${f(res.turnover, 1)} Cr a day</span></div>
      <div class="tw"><table class="sc-t rules"><thead><tr><th></th><th>Rule</th><th>Result</th></tr></thead><tbody>${res.checks.map((ck) => `<tr class="${ck.close ? "cl" : ck.ok ? "ok" : "no"}"><td>${ck.close ? "≈" : ck.ok ? "✓" : "✗"}</td><td>${esc(ck.desc)}</td><td>${esc(ck.detail)}</td></tr>`).join("")}</tbody></table></div>`; }
  return `<h2>Check one stock against these rules <span>see exactly which rule a stock passes or fails, for example why it did not show up in a scan</span></h2>
    <div class="card pad"><div class="sc-row"><label class="f"><span>Symbol</span><input data-sc="k.tSym" value="${esc(C.tSym)}" placeholder="for example TATAPOWER" autocapitalize="characters" spellcheck="false"></label>
      <label class="f"><span>On date (blank = latest)</span><input data-sc="k.tDate" value="${esc(C.tDate)}" placeholder="for example 12 Aug 2026"></label><button class="btn" data-act="sctest"${C.busy.test ? " disabled" : ""}>${C.busy.test ? "Checking…" : "Test"}</button></div>${out}</div>`;
}
const SETUP_SQL = `-- Arka scanner · run once in Supabase → SQL Editor
create table if not exists setups (
  id bigint generated always as identity primary key,
  name text not null,
  price_min double precision default 0, price_max double precision default 99999,
  rsi_min double precision default 0, rsi_max double precision default 100,
  volume_multiplier double precision default 0,
  atr_pct_min double precision default 0, atr_pct_max double precision default 0,
  move_pct_min double precision default 0, move_pct_max double precision default 0,
  rule_config jsonb default '{}'::jsonb, visual_rules text, reference_image_b64 text,
  owner text default 'admin', created_at timestamptz default now());
alter table setups add column if not exists rule_config jsonb default '{}'::jsonb;
alter table setups add column if not exists owner text default 'admin';`;
function setupsView() {
  const C = S.sc, list = setupsOf(), picked = C.edit === "new" ? null : setupById(C.edit);
  if (C.loaded !== (picked ? picked.id : null)) { loadDraft(picked); C.loaded = picked ? picked.id : null; }
  const d = C.draft, editable = !picked || d.can_edit, dis = editable ? "" : " disabled", N = C.notes, nOn = C.rules.filter((r) => r.on ?? true).length, mp = +d.min_pass || 0;
  const saved = picked && C.img[picked.id], pic = C.newImg || saved || null;
  const notes = N && (N.assumptions.length || N.questions.length) ? `<div class="sc-notes"><div class="h"><b>How ${esc(N.src)} was read</b><span>check these before you save</span></div>${N.assumptions.map((x) => `<div class="as"><i>Assumed</i>${esc(x)}</div>`).join("")}
      ${N.questions.length && editable ? `<div class="qs"><b>A few questions so the rules match what you mean</b>${N.questions.map((q, i) => fld(q, `<input data-sc="ans.${i}" value="${esc(C.ans[i] || "")}" placeholder="Your answer">`)).join("")}<button class="btn pri" data-act="screbuild"${C.busy.build ? " disabled" : ""}>Rebuild with my answers</button></div>` : ""}</div>` : "";
  return `<div class="card pad sc-form"><div class="g2x">${fld("Edit setup", sel("k.edit", ["new"].concat(list.map((s) => s.id)), C.edit, (id) => (id === "new" ? "＋ New setup" : `${setupById(id).name}  ·  #${id}`)))}
        <div class="f"><span>&nbsp;</span><button class="btn full" data-act="scdup"${picked ? "" : " disabled"}>Duplicate</button></div></div>
      ${picked && !editable ? '<div class="sc-warn in">This is a desk setup: you can scan with it, or duplicate it to make your own copy.</div>' : ""}
      ${editable ? `<div class="g2x">${fld("Start from a ready-made setup", sel("k.tpl", Object.keys(R.TEMPLATES), C.tpl, (n) => "Template · " + n))}<div class="f"><span>&nbsp;</span><button class="btn full" data-act="sctpl" title="Replaces the rules below. Nothing is saved until you press Save.">Load template</button></div></div>` : ""}</div>
    <h2>Describe it, check how it was understood, save <span>write it the way you would explain it to a friend, including what is “ok”</span></h2>
    <div class="sc-two"><div class="card pad sc-form">
        ${fld("Setup name", `<input data-sc="d.name" value="${esc(d.name)}" maxlength="80" placeholder="for example Momentum pullback on dry volume"${dis}>`)}
        ${fld("Describe the setup in your own words", `<textarea data-sc="d.desc" rows="6" placeholder="for example: Stock gained 20%+ in the last 30 days. RSI above 50 and above its 14 SMA. Price above EMA 20 and SMA 50. Then a pullback of 3 candles on low volume (4 candles is also fine)."${dis}>${esc(d.desc)}</textarea>`)}
        <div class="sc-row"><button class="btn pri" data-act="scbuild"${dis}${C.busy.build ? " disabled" : ""}>${C.busy.build ? "Reading your setup…" : "Build rules from my description"}</button><span class="mini">${C.ai ? "Uses Arka AI (Gemini), and the built-in reader if the AI is busy." : "Uses the built-in reader (add the secret GEMINI_KEY in Cloudflare to use Arka AI)."} It replaces the rules below.</span></div>${notes}</div>
      <div class="card pad sc-form"><div class="g2">${fld("Price from (₹)", numIn("d.pmin", d.pmin, 0, 999999, 10, dis))}${fld("Price to (₹)", numIn("d.pmax", d.pmax, 0, 999999, 10, dis))}</div>
        ${fld("Close enough", sel("d.tol", TOLS, d.tol, (x) => x, dis + ' title="How far a number may miss and still pass. 5%: a 19% run-up passes “at least 20%”, RSI 48 passes “above 50”. Results show these as ≈ and rank exact matches first."'))}
        ${fld("Reference chart (optional, for the AI check)", `<input type="file" accept="image/png,image/jpeg" data-sc="file"${dis}>`)}
        ${pic ? `<img class="sc-ref" alt="Reference chart" src="data:image/png;base64,${pic}"><div class="mini">${C.newImg ? "New reference chart (saved with the setup)" : "Saved reference chart"}</div>` : picked && C.img[picked.id] === undefined ? `<button class="btn sm" data-act="scimg" data-id="${picked.id}">Show the saved reference chart</button>` : ""}
        <button class="btn full" data-act="scbuildpic"${editable && pic && C.ai && !C.busy.build ? "" : " disabled"} title="The AI looks at the right edge of your chart and proposes rules. Needs GEMINI_KEY and a picture. Replaces the rules below.">Build rules from the chart picture</button></div></div>
    ${learnBox(editable)}
    <div class="card pad sc-plain" id="sc-plain">${plainBox()}</div>
    <h2>Rules · ${C.rules.length} <span>a stock must pass ${!mp || mp >= nOn ? "all of them" : `at least ${mp} of ${nOn}`} · stocks that miss by exactly one rule are shown as near matches</span></h2>
    ${editable ? `<div class="card pad sc-form">${fld("A stock must pass at least this many rules (0 = all of them)", numIn("d.min_pass", d.min_pass, 0, 40, 1, 'title="Example: 7 rules and 5 here = any 5 of the 7 is a match. Mark a rule “Always required” under More options to protect it."'))}</div>` : ""}
    ${C.rules.length ? "" : '<div class="card"><div class="note">No rules yet: build them from the description, load a template, or add them below.</div></div>'}
    ${editable ? C.rules.map((r, i) => ruleEditor(r, i, C.rules.length)).join("") : `<div class="card pad sc-rules plain">${C.rules.map((r, i) => `<div class="rl"><i>${i + 1}</i><span>${esc(E.describe(r))}${(r.on ?? true) ? "" : " (off)"}</span></div>`).join("")}</div>`}
    ${editable ? `<div class="card pad sc-form"><div class="g2x">${fld("Add a rule", sel("k.addType", E.RULE_ORDER, C.addType, (t) => E.RULES[t].label))}<div class="f"><span>&nbsp;</span><button class="btn full" data-act="scradd">Add rule</button></div></div></div>` : ""}
    ${testBox()}
    <div class="sc-save">${editable ? `<button class="btn pri" data-act="scsave"${C.busy.save ? " disabled" : ""}>${C.busy.save ? "Saving…" : d.id ? "Save changes" : "Save setup"}</button>` : ""}${picked && editable ? (C.confirmDel ? `<span class="cf">Delete “${esc(picked.name)}” for good?</span><button class="btn red" data-act="scdelyes">Yes, delete</button><button class="btn" data-act="scdelno">Cancel</button>` : '<button class="btn" data-act="scdel">Delete setup</button>') : ""}</div>
    <details class="card sc-det"><summary>Storage (Supabase) <span>the same table the classic app uses, so both show the same setups</span></summary><div class="pad"><div class="mini">${C.hasOwner ? "Your table is ready. This is the SQL that creates it, for reference:" : "Run this once to store rule ranges and who owns each setup (members can then edit their own setups):"}</div><pre class="sc-sql">${esc(SETUP_SQL)}</pre></div></details>`;
}

// ── page: Backtest ──
function backtestView() {
  const C = S.sc, B = C.bt, list = setupsOf();
  if (!list.length) return `<div class="card"><div class="empty"><b>No setups yet</b>Open My setups and save one first.</div></div>`;
  const setup = curSetup(), rules = E.setupRules(setup).filter((r) => r.on ?? true), pmin = Number(setup.price_min) || 0, pmax = Number(setup.price_max) || 99999, tk = B.tol[setup.id] || tolKey(E.setupTol(setup), "Small (5%)");
  let out = `<div class="card pad sc-form"><div class="mini lead">Replays the setup on every past day for every stock. A signal is read at the close and bought at the next day's open. Use it to see whether the rules you wrote find what you meant, and whether it was worth finding.</div>
      <div class="g4x">${fld("Setup", sel("k.sel", list.map((s) => s.id), setup.id, (id) => setupById(id).name))}${fld("Stocks", sel("bt.uni", ["NIFTY 500", "All NSE stocks", "Your watchlist", "Arka watchlist", "Your portfolio"], B.uni))}
        ${fld("Look back", sel("bt.n", [60, 120, 200], B.n, (n) => n + " sessions"))}${fld("Minimum turnover (₹ Cr a day)", numIn("bt.liq", B.liq, 0, 500, 0.5))}${fld("Close enough", sel("bttol." + setup.id, TOLS, tk))}</div>
      ${rules.length ? `<div class="mini">${rules.length} rules · price band ₹${f(pmin, 0)}–${f(pmax, 0)} (checked on each past day) · a few seconds for 500 stocks, longer for every NSE stock and for setups with either-or groups.</div>
      <button class="btn pri full" data-act="scbt"${C.busy.bt || !C.D.meta ? " disabled" : ""}>${C.busy.bt ? "Replaying…" : "Run backtest · " + esc(setup.name)}</button>${progBar()}` : '<div class="sc-warn in">This setup has no rules yet.</div>'}</div>`;
  const bt = B.res; if (!bt) return out;
  const St = bt.stats, perMonth = (St.signals / Math.max(1, St.sessions)) * 21;
  out += `<h2>Result · ${esc(bt.setup_name)} <span>${esc(bt.source)} · ${f(St.stocks, 0)} stocks · last ${St.sessions} sessions · ${f(St.days, 0)} stock-days checked in ${f(St.secs, 1)}s</span></h2>
    <div class="sc-kpis four">${kpi("Signals", f(St.signals, 0), "a")}${kpi("Different stocks", f(St.names, 0))}${kpi("Signals per month", f(perMonth, 1))}${kpi("Average worst dip, 20 days", St.avg_worst == null ? "—" : `<span class="dn">${fs(St.avg_worst, 1)}%</span>`, "r")}</div>`;
  if (!St.signals) return out + `<div class="card"><div class="empty"><b>The setup never triggered in this period</b>It is too strict as written: run a scan and open “Which rule removes the most stocks”, or widen the ranges and try again.</div></div>`;
  const rows = R.HORIZONS.map((hz) => { const m = bt.summary[hz], base = bt.base[hz]; if (!m) return [`${hz} sessions later`, "0", "—", "—", "—", "—", "—", "—"]; const edge = base == null ? null : m.mean - base;
    return [`${hz} sessions later`, f(m.n, 0), f(m.win, 0) + "%", pcs(m.mean), pcs(m.median), pcs(base, 2), edge == null ? "—" : `<b class="${edge > 0 ? "up" : "dn"}">${fs(edge, 1)} pp</b>`, pcs(m.worst)]; });
  const n20 = (bt.summary[20] || {}).n || 0, show = bt.signals.slice(0, 150);
  out += `<div class="card">${simpleTable(["After the signal", "Signals", "Rose", "Average", "Middle (median)", "Any stock, any day", "Setup minus any day", "Worst"], rows)}
    <div class="note">“Any stock, any day” is the average result of buying every stock in the same list on every day of the same period: the setup is only useful if it beats that. ${n20 < 30 ? `Only ${n20} completed signals: too few to trust the percentages. ` : ""}Not included: brokerage, slippage, and stocks that were delisted. One period of one market: the next one can differ.${bt.deliv ? " Delivery data only covers recent sessions, so older days could not trigger." : ""}</div></div>
    <h2>Every signal, newest first <span>${bt.signals.length > show.length ? `showing the newest ${show.length} of ${bt.signals.length}; the download has all of them` : ""}</span></h2>
    <div class="card">${simpleTable(["Stock", "Signal date", "Close", "Entry (next open)", "+5", "+10", "+20", "Worst dip 20d", "Till now"], show.map((x) => [`<a class="sc-lk" href="#/s/${encodeURIComponent(x.symbol)}">${esc(x.symbol)}</a>`, dmy(x.date), "₹" + f(x.close), x.entry == null ? "waiting for next open" : "₹" + f(x.entry), pcs(x.r5), pcs(x.r10), pcs(x.r20), pcs(x.worst), pcs(x.now)]))}</div>
    <div class="sc-save"><button class="btn" data-act="scbtcsv">Download all signals (CSV)</button></div>`;
  return out;
}
async function runBacktest() {
  const C = S.sc, B = C.bt, setup = curSetup(); if (!setup || C.busy.bt || !C.D.meta) return;
  const rules = E.setupRules(setup).filter((r) => r.on ?? true), pmin = Number(setup.price_min) || 0, pmax = Number(setup.price_max) || 99999;
  calm(); C.busy.bt = true; prog(0.02, "Reading the stock list"); paint();
  try {
    const [uni, src] = await universe(B.uni); if (!uni.length) { toast(`${B.uni} is empty.`); return; }
    const need = R.needs(rules), tol = E.TOLERANCES[B.tol[setup.id] || tolKey(E.setupTol(setup))], t0 = Date.now(), liq = Number(B.liq) || 0;
    const data = await prepare(uni, { long: true, nifty: need.nifty, deliv: need.deliv }, 0.02, 0.5), { band } = R.candidates(uni, data.frames, pmin, pmax, liq, 2);
    const bt = await R.backtest({ syms: band.filter((s) => data.frames(s)), frames: data.frames, rules, pmin, pmax, minTurnover: liq, sessions: +B.n || 120, minPass: E.setupMinPass(setup), nifty: data.nifty, deliv: data.deliv, tol, progress: (p, m) => prog(0.5 + 0.5 * p, m), pause });
    bt.stats.secs = (Date.now() - t0) / 1000; Object.assign(bt, { setup_name: setup.name, source: src, band: [pmin, pmax], deliv: need.deliv }); B.res = bt;
  } catch (e) { if (e.message !== "signed out") toast("The backtest stopped: " + e.message); }
  finally { C.busy.bt = false; C.prog = null; paint(); }
}

// ── the page ──
export function page() {
  const C = S.sc, views = ["Scan", "My setups", "Backtest"], view = views.includes(C.view) ? C.view : "Scan";
  const head = `<div class="hd"><div><h1>Screener</h1><div class="sub">Describe your setup, scan the market, and see which stocks match and which almost do.</div></div></div>
    <div class="seg">${views.map((v) => `<button class="${v === view ? "on" : ""}" data-act="scview" data-v="${v}">${v}</button>`).join("")}</div>`;
  if (C.setups == null) return `<div class="sc">${head}<div class="card"><div class="note">Loading your setups…</div></div></div>`;
  let body; try { body = view === "Scan" ? scanView() : view === "Backtest" ? backtestView() : setupsView(); } catch (e) { body = `<div class="card"><div class="empty"><b>This view could not be drawn</b>${esc(e.message)}</div></div>`; }
  const setupMsg = !C.D.meta && C.D.err && /Not built yet|404/.test(C.D.err) ? `<div class="card sc-warn"><b>The price files are not there yet.</b><br>1. Add <b>scan_export.py</b> to the repository (next to app.py).<br>2. Add <b>.github/workflows/scan-data.yml</b>.<br>3. In GitHub open Actions, choose “Screener data”, press Run workflow and wait for the green tick (about six minutes).<br>After that the files refresh by themselves every evening, and every 15 minutes while the market is open.</div>` : "";
  return `<div class="sc">${head}${C.err ? `<div class="card sc-warn">${esc(C.err)}</div>` : ""}${setupMsg}${body}<div class="note">Technical scanner for research, not investment advice. Prices: Yahoo Finance, delayed. Past results do not promise future ones.</div></div>`;
}

// ── actions ──
function applyParsed(parsed, src) {                          // put the reader's rules into the editor -> false when nothing was read
  const C = S.sc; if (!parsed || !parsed.rules || !parsed.rules.length) return false;
  C.rules = parsed.rules; if (parsed.price_min != null) C.draft.pmin = parsed.price_min; if (parsed.price_max != null) C.draft.pmax = Math.min(parsed.price_max, 999999);
  C.notes = { src, assumptions: parsed.assumptions || [], questions: parsed.questions || [] }; C.ans = {};
  if (parsed.tol != null) C.draft.tol = tolKey(parsed.tol, "Medium (10%)");
  toast(`${parsed.rules.length} rules built: check the numbers below.`); return true;
}
async function askAi(prompt, images, temperature = 0.1) { const d = await api("/api/ai", { method: "POST", body: JSON.stringify({ prompt, images, temperature }) }); return d.data; }
async function build(text, src, image) {
  const C = S.sc; if (C.busy.build) return; calm(); C.busy.build = true; paint(); let parsed = null;
  try { if (C.ai && (text.trim() || image)) { try { parsed = R.cleanAi(await askAi(R.aiPrompt(text, !!image), image ? [{ mime: mimeOf(image), data: image }] : [])); if (parsed && !parsed.rules.length) parsed = image ? parsed : null; }
      catch (e) { if (e.message === "signed out") return; toast(image ? "The AI could not read the picture: " + e.message : "The AI could not read the description, so the built-in reader was used."); } }
    if (!parsed && !image) parsed = R.parseLocally(text);
    if (!applyParsed(parsed, src)) toast(image ? "No rules could be read from the picture." : "No rules could be read from that description. Add rules by hand below."); }
  finally { C.busy.build = false; paint(); }
}
async function histories(syms) { const out = {}; for (let i = 0; i < syms.length; i += 6) { const d = await api("/api/history?range=2y&s=" + encodeURIComponent(syms.slice(i, i + 6).join(","))); for (const h of d.history || []) if (h.ok && h.daily !== false) out[h.sym] = { d: h.d, o: h.o, h: h.h, l: h.l, c: h.c, v: h.v, yahoo: true }; } return out; }
async function learn() {
  const C = S.sc; if (C.busy.learn) return; const [ex, bad] = R.parseExamples(C.exText), L = { bad, missing: [], labels: [], meas: {}, rules: [], cur: [] };
  calm(); C.busy.learn = true; paint();
  try { if (ex.length) { const syms = [...new Set(ex.map((e) => e[0]))].slice(0, 30), hist = await histories(syms), nifty = await getNifty(), roots = {}, views = [];
      for (const s of syms) if (hist[s]) roots[s] = new E.Ctx(s, hist[s], null, nifty);
      for (const [sym, day] of ex) { const v = roots[sym] ? R.ctxOn(roots[sym], day) : null; if (!v) L.missing.push(`${sym} ${dmy(day)}` + (roots[sym] ? "" : " (symbol not found)")); else views.push([`${sym} · ${dmy(v.d[v.end - 1]).replace(/ \d\d(\d\d)$/, " $1")}`, v]); }
      if (views.length) { const [rules, meas] = R.learnRules(views), cur = C.rules.filter((r) => r.on ?? true); L.labels = views.map((x) => x[0]); L.meas = meas; L.rules = rules;
        L.cur = cur.map((r) => [E.describe(r), views.map(([, v]) => !!E.evaluateRule(v, r)[0]), views.map(([, v]) => E.evaluateRule(v, r)[2])]); } }
    C.learn = L; } catch (e) { if (e.message !== "signed out") toast("The examples could not be measured: " + e.message); }
  finally { C.busy.learn = false; paint(); }
}
async function testStock() {
  const C = S.sc, sym = C.tSym.trim().toUpperCase().replace(/^NSE:/, "").replace(/\.NS$/, ""); if (!sym || C.busy.test) return;
  const when = C.tDate.trim() ? R.parseDate(C.tDate) : null; if (C.tDate.trim() && !when) { C.test = { err: "That date could not be read. Write it like 12 Aug 2026 or 2026-08-12." }; return paint(); }
  calm(); C.busy.test = true; paint();
  try { const rules = C.rules.filter((r) => r.on ?? true), need = R.needs(rules), F = (await histories([sym]))[sym];
    if (!F) C.test = { err: `No price history found for ${sym}. Use the NSE symbol.` };
    else { const dl = need.deliv ? await getDeliv() : null, root = new E.Ctx(sym, F, when ? null : liveFrac(F), need.nifty ? await getNifty() : null, dl && dl.s[sym] ? { deliv: { d: dl.d, v: dl.s[sym] } } : {}), view = when ? R.ctxOn(root, when) : root;
      const res = view ? E.evaluateStock(sym, null, rules, { ctx: view, minPass: +C.draft.min_pass || 0, tol: E.TOLERANCES[C.draft.tol] ?? E.DEFAULT_TOL }) : null;
      C.test = res ? { res } : { err: when ? `${sym} has no candle near ${dmy(when)} in the stored history (${dmy(F.d[0])} – ${dmy(F.d[F.d.length - 1])}).` : `${sym} has too little price history to test.` }; } }
  catch (e) { if (e.message !== "signed out") C.test = { err: e.message }; }
  finally { C.busy.test = false; paint(); }
}
async function save() {
  const C = S.sc, d = C.draft, name = d.name.trim(), pmin = +d.pmin || 0, pmax = +d.pmax || 0;
  if (!name) return toast("Give the setup a name."); if (pmin > pmax) return toast("“Price from” is higher than “Price to”."); if (!C.rules.length) return toast("Add at least one rule.");
  calm(); C.busy.save = true; paint();
  try { const r = await api("/api/setups", { method: "POST", body: JSON.stringify({ id: d.id, name, desc: d.desc, pmin, pmax, min_pass: Math.trunc(+d.min_pass || 0), tol: E.TOLERANCES[d.tol] ?? E.DEFAULT_TOL, rules: C.rules, img_b64: C.newImg || undefined }) });
    if (C.newImg && r.id != null) C.img[r.id] = C.newImg; toast(`Setup “${name}” saved.`); C.busy.save = false; await load(true); C.sel = r.id; C.edit = setupById(r.id) ? r.id : "new"; C.loaded = undefined; }
  catch (e) { if (e.message !== "signed out") toast("Could not save: " + e.message); }
  finally { C.busy.save = false; paint(); }
}
async function addToWatchlist(syms) {
  const have = mineSet(), fresh = [...new Set(syms)].filter((s) => s && !have.has(s)); if (!fresh.length) return;
  const list = S.wl.list === "Arka desk" ? "Main" : S.wl.list;
  try { await api("/api/watchlist", { method: "POST", body: JSON.stringify({ syms: fresh, list }) }); for (const s of fresh) S.wl.rows.push({ sym: s, list, note: "", above: null, below: null }); S.wl.ver = (S.wl.ver || 0) + 1; S.sc.picks = {}; toast(`${fresh.length} stock${fresh.length > 1 ? "s" : ""} added to your watchlist (${list})`); }
  catch (e) { if (e.message !== "signed out") toast("Could not save: " + e.message); }
  paint();
}
function download(name, text, type) { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 4000); }
const csvCell = (v) => (v == null ? "" : /[",\n]/.test(String(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : String(v)), r2 = (v, d = 2) => (v == null ? "" : +Number(v).toFixed(d));
export function scanCsv(rows) { return [["symbol", "ltp", "chg_pct", "turnover_cr", "rsi", "missed", "checks"]].concat(rows.map((r) => [r.symbol, r2(r.close), r2(r.chg), r2(r.turnover), r.rsi == null ? "" : r2(r.rsi, 1), r.fails[0] ? r.fails[0].detail : "", r.checks.map((ck) => (ck.ok ? "OK " : "NO ") + ck.detail).join(" | ")])).map((r) => r.map(csvCell).join(",")).join("\n") + "\n"; }
export function btCsv(sig) { return [["symbol", "signal_date", "close", "entry_next_open", "ret_5d_pct", "ret_10d_pct", "ret_20d_pct", "worst_dip_20d_pct"]].concat(sig.map((x) => [x.symbol, x.date, r2(x.close), x.entry == null ? "" : r2(x.entry), r2(x.r5), r2(x.r10), r2(x.r20), r2(x.worst)])).map((r) => r.map(csvCell).join(",")).join("\n") + "\n"; }
function chartPng(sym, F, n = 60) {                          // the candidate's chart as a picture for the AI check (_chart_png)
  const W = 660, H = 462, cv = document.createElement("canvas"); cv.width = W; cv.height = H; const g = cv.getContext("2d"), N = F.c.length, a = Math.max(0, N - n), len = N - a;
  const ema = new Array(N), sma = new Array(N).fill(NaN); let y0 = F.c[0], sum = 0; for (let i = 0; i < N; i++) { y0 = i ? (1 - 2 / 21) * y0 + (2 / 21) * F.c[i] : F.c[0]; ema[i] = y0; sum += F.c[i]; if (i >= 50) sum -= F.c[i - 50]; if (i >= 49) sma[i] = sum / 50; }
  let lo = Infinity, hi = -Infinity, vmax = 1; for (let i = a; i < N; i++) { lo = Math.min(lo, F.l[i], ema[i], isNaN(sma[i]) ? Infinity : sma[i]); hi = Math.max(hi, F.h[i], ema[i], isNaN(sma[i]) ? -Infinity : sma[i]); vmax = Math.max(vmax, F.v[i] || 0); }
  const L = 46, Rr = 10, top = 30, pb = 318, vt = 340, vb = H - 16, step = (W - L - Rr) / len, bw = step * 0.6, y = (p) => top + ((hi - p) / (hi - lo || 1)) * (pb - top), x = (i) => L + (i - a + 0.5) * step;
  g.fillStyle = "#000"; g.fillRect(0, 0, W, H); g.strokeStyle = "#262626"; g.lineWidth = 1; g.strokeRect(L, top - 6, W - L - Rr, pb - top + 12); g.strokeRect(L, vt, W - L - Rr, vb - vt);
  g.fillStyle = "#E8E8E8"; g.font = "13px sans-serif"; g.fillText(`${sym} · ${n} candles · EMA20 / SMA50`, L, 18); g.fillStyle = "#8A8A8A"; g.font = "10px sans-serif";
  for (let k = 0; k <= 4; k++) { const p = lo + ((hi - lo) * k) / 4; g.fillText(p.toFixed(p < 100 ? 1 : 0), 4, y(p) + 3); }
  for (let i = a; i < N; i++) { const up = F.c[i] >= F.o[i]; g.strokeStyle = "#8A8A8A"; g.beginPath(); g.moveTo(x(i), y(F.h[i])); g.lineTo(x(i), y(F.l[i])); g.stroke(); g.fillStyle = up ? "#30D158" : "#FF453A";
    g.fillRect(x(i) - bw / 2, Math.min(y(F.o[i]), y(F.c[i])), bw, Math.max(1, Math.abs(y(F.c[i]) - y(F.o[i])))); g.globalAlpha = 0.7; const vh = ((F.v[i] || 0) / vmax) * (vb - vt - 4); g.fillRect(x(i) - bw / 2, vb - vh, bw, vh); g.globalAlpha = 1; }
  for (const [arr, col] of [[ema, "#5AC8FA"], [sma, "#FF9F0A"]]) { g.strokeStyle = col; g.lineWidth = 1.4; g.beginPath(); let st = false; for (let i = a; i < N; i++) if (!isNaN(arr[i])) { st ? g.lineTo(x(i), y(arr[i])) : g.moveTo(x(i), y(arr[i])); st = true; } g.stroke(); }
  return cv.toDataURL("image/png").split(",")[1];
}
async function refImage(id) { const C = S.sc; if (C.img[id] === undefined) { try { C.img[id] = (await api("/api/setups?img=" + encodeURIComponent(id))).b64 || null; } catch { C.img[id] = null; } } return C.img[id]; }
async function audit() {
  const C = S.sc, res = C.res; if (!res || C.busy.audit) return; const rows = R.sortRows(res.match, C.sort).slice(0, Math.max(1, Math.min(40, +C.aiN || 10))), out = { ...(C.audit || {}) };
  C.busy.audit = true; C.auditOpen = true; C.cache = C.cache || {}; paint();
  try { const ref = await refImage(res.setup_id), text = res.rules.join("\n"), todo = rows.filter((r) => { const k = `${res.setup_id}|${r.symbol}|${r.date}`; if (C.cache[k]) { out[r.symbol] = C.cache[k]; return false; } return true; }); let done = 0;
    const one = async (r) => { let a; try { a = R.cleanAudit(await askAi(R.AUDIT_PROMPT(text, !!ref), (ref ? [{ mime: mimeOf(ref), data: ref }] : []).concat([{ mime: "image/png", data: chartPng(r.symbol, r.chart) }]), 0.2)); C.cache[`${res.setup_id}|${r.symbol}|${r.date}`] = a; }
      catch (e) { a = { score: 0, matches: [], mismatches: ["The AI call failed: " + e.message], caption: "" }; } out[r.symbol] = a; done++; prog(done / todo.length, `Checked ${done} of ${todo.length}`); };
    for (let i = 0; i < todo.length; i += 3) await Promise.all(todo.slice(i, i + 3).map(one));
    C.audit = out; }
  finally { C.busy.audit = false; C.prog = null; paint(); }
}
function readImage(file) {                                   // a picture from the phone or computer -> PNG / JPEG text, made smaller when it is large
  return new Promise((ok, bad) => { if (!file) return bad(new Error("No file")); const fr = new FileReader(); fr.onerror = () => bad(new Error("The file could not be read."));
    fr.onload = () => { const im = new Image(); im.onerror = () => bad(new Error("That file is not a picture.")); im.onload = () => { const k = Math.min(1, 1400 / Math.max(im.width, im.height)), cv = document.createElement("canvas"); cv.width = Math.round(im.width * k); cv.height = Math.round(im.height * k);
        cv.getContext("2d").drawImage(im, 0, 0, cv.width, cv.height); let b = cv.toDataURL("image/png").split(",")[1]; if (b.length > 1_400_000) b = cv.toDataURL("image/jpeg", 0.82).split(",")[1]; ok(b); }; im.src = fr.result; };
    fr.readAsDataURL(file); });
}
const rule = (id) => S.sc.rules.find((r) => r._id === id);
export function input(el, ev) {                              // a control on the Screener changed
  const C = S.sc, key = el.dataset.sc, dot = key.indexOf("."), g = dot < 0 ? key : key.slice(0, dot), k = dot < 0 ? "" : key.slice(dot + 1), text = /^(INPUT|TEXTAREA)$/.test(el.tagName) && !/checkbox|file|date/.test(el.type), val = el.type === "checkbox" ? el.checked : el.value;
  if (g === "file") { if (ev !== "change") return; return readImage(el.files && el.files[0]).then((b) => { C.newImg = b; paint(); }).catch((e) => toast(e.message)); }
  if (text && ev === "change" && g !== "r" && g !== "d") return;                                            // text boxes were already read while typing
  if (!text && ev === "input") return;
  if (g === "r") { const [id, name] = k.split("."), r = rule(id); if (!r) return; const i = C.rules.indexOf(r);
    if (text && ev === "input") { const x = el.value === "" ? null : Number(el.value); if (x != null && !isNaN(x)) { C.rules[i] = E.cleanRule({ ...r, [name]: x }); const d = document.getElementById("scd-" + id); if (d) d.textContent = E.describe(C.rules[i]); const p = document.getElementById("sc-plain"); if (p) p.innerHTML = plainBox(); } return; }
    C.rules[i] = E.cleanRule({ ...r, [name]: text ? Number(el.value) : val });
    if (text) { for (const b of document.querySelectorAll(`[data-sc^="r.${id}."]`)) { const nm = b.dataset.sc.split(".")[2]; if (b.type === "number" && b !== document.activeElement) b.value = C.rules[i][nm]; }      // the checked numbers back into the boxes (a reversed range is swapped)
      el.value = C.rules[i][name]; const d = document.getElementById("scd-" + id); if (d) d.textContent = E.describe(C.rules[i]); const p = document.getElementById("sc-plain"); if (p) p.innerHTML = plainBox(); return; } return paint(); }
  if (g === "d") { if (text && ev === "change" && !["pmin", "pmax", "min_pass"].includes(k)) return; C.draft[k] = el.type === "number" ? (el.value === "" ? 0 : Number(el.value)) : val; if (k === "tol" || k === "min_pass") { const p = document.getElementById("sc-plain"); if (p) p.innerHTML = plainBox(); } if (!text) paint(); return; }
  if (g === "ans") { C.ans[k] = val; return; }
  if (g === "pick") { C.picks[k] = val; return paint(); }
  if (g === "skip") { C.skip[k] = val; return; }
  if (g === "liq") { C.liq[k] = val; return; }
  if (g === "tolrun") { C.tolRun[k] = val; return; }
  if (g === "bttol") { C.bt.tol[k] = val; return; }
  if (g === "bt") { C.bt[k] = k === "n" ? +val : val; return; }
  if (g === "k") { C[k] = k === "psize" ? +val : k === "sel" && val !== "new" ? (isNaN(+val) ? val : +val) : val;
    if (k === "edit") { C.edit = val === "new" ? "new" : setupById(val) ? setupById(val).id : "new"; C.loaded = undefined; }
    if (k === "sel") { const s = setupById(val); C.sel = s ? s.id : C.sel; }
    if (k === "showRef" && val && curSetup()) refImage(curSetup().id).then(paint);
    if (k === "sort" || k === "psize") C.pg = 1;
    if (text) return; return paint(); }
}
export function act(a, el) {
  const C = S.sc, v = el.dataset.v, id = el.dataset.id, idx = () => C.rules.findIndex((r) => r._id === id);
  if (a === "scview") { C.view = v; window.scrollTo(0, 0); if (v === "My setups" && C.sel != null && C.edit === "new" && !C.rules.length && !C.draft.name) { /* stay on the blank setup */ } return paint(); }
  if (a === "scrun") return runScan();
  if (a === "scbt") return runBacktest();
  if (a === "sctab") { C.tab = v; C.pg = 1; C.open = ""; return paint(); }
  if (a === "scpg") { C.pg = Math.max(1, +v); window.scrollTo(0, (document.querySelector(".sc-bar") || { offsetTop: 0 }).offsetTop - 70); return paint(); }
  if (a === "scopen") { C.open = C.open === el.dataset.s ? "" : el.dataset.s; return paint(); }
  if (a === "scadd") return addToWatchlist(Object.keys(C.picks).filter((s) => C.picks[s]));
  if (a === "scaddall") return addToWatchlist(R.sortRows(C.tab === "near" ? C.res.near : C.res.match, C.sort).map((r) => r.symbol));
  if (a === "scexport") { const rows = R.sortRows(C.tab === "near" ? C.res.near : C.res.match, C.sort), day = todayStr().replace(/-/g, ""); return C.exp === "CSV" ? download(`arka_scan_${day}.csv`, scanCsv(rows), "text/csv") : download(`arka_scan_${day}.txt`, rows.map((r) => "NSE:" + r.symbol).join("\n"), "text/plain"); }
  if (a === "scbtcsv") return download(`arka_backtest_${todayStr().replace(/-/g, "")}.csv`, btCsv(C.bt.res.signals), "text/csv");
  if (a === "scaudit") return audit();
  if (a === "scimg") return refImage(id).then(paint);
  if (a === "scdup") { C.draft = { ...C.draft, id: null, owner: null, can_edit: true, name: (C.draft.name + " (copy)").slice(0, 80) }; C.edit = "new"; C.loaded = null; C.confirmDel = false; return paint(); }
  if (a === "sctpl") { const [rules, desc] = R.templateRules(C.tpl); C.rules = rules; C.draft.min_pass = 0; if (!C.draft.desc.trim()) C.draft.desc = desc; if (!C.draft.name.trim()) C.draft.name = C.tpl; C.notes = null; toast(`Template loaded: ${rules.length} rules. Change any number, then save.`); return paint(); }
  if (a === "scbuild") { if (!C.draft.desc.trim()) return toast("Write the description first."); return build(C.draft.desc, "your description"); }
  if (a === "screbuild") { const ans = (C.notes ? C.notes.questions : []).map((q, i) => ((C.ans[i] || "").trim() ? `Q: ${q} A: ${C.ans[i].trim()}` : "")).filter(Boolean); if (!ans.length) return toast("Answer at least one question first."); return build((C.draft.desc + "\n\nClarifications from the trader:\n" + ans.join("\n")).trim(), "your description and answers"); }
  if (a === "scbuildpic") { const pic = C.newImg || (C.draft.id != null && C.img[C.draft.id]); if (!pic) return; return build(C.draft.desc, "your chart picture", pic); }
  if (a === "sclearn") return learn();
  if (a === "sclearnuse") { C.rules = C.learn.rules.map((r) => E.cleanRule({ ...r, _id: undefined })); C.notes = null; return paint(); }
  if (a === "scradd") { C.rules.push(E.newRule(C.addType)); return paint(); }
  if (a === "scrdel") { const i = idx(); if (i >= 0) C.rules.splice(i, 1); return paint(); }
  if (a === "scrup" || a === "scrdn") { const i = idx(), j = i + (a === "scrup" ? -1 : 1); if (i >= 0 && j >= 0 && j < C.rules.length) [C.rules[i], C.rules[j]] = [C.rules[j], C.rules[i]]; return paint(); }
  if (a === "sctest") return testStock();
  if (a === "scsave") return save();
  if (a === "scdel") { C.confirmDel = true; return paint(); }
  if (a === "scdelno") { C.confirmDel = false; return paint(); }
  if (a === "scdelyes") { const sid = C.draft.id; C.confirmDel = false; return api("/api/setups?id=" + encodeURIComponent(sid), { method: "DELETE" }).then(async () => { toast("Setup deleted."); C.edit = "new"; C.loaded = undefined; if (String(C.sel) === String(sid)) C.sel = null; await load(true); }).catch((e) => { if (e.message !== "signed out") toast(e.message); paint(); }); }
}
export function toggle(el) { const C = S.sc; if (el.dataset.scd) C[el.dataset.scd] = el.open; if (el.dataset.scm) { C.moreOpen = C.moreOpen || {}; C.moreOpen[el.dataset.scm] = el.open; } }
export function hover(e) {                                   // desktop: a small chart when the pointer rests on a symbol
  const p = e.target.closest && e.target.closest(".sc-sym"); if (!p) return; const pop = p.querySelector(".pop"); if (!pop || pop.dataset.done || !S.sc.res) return;
  const r = S.sc.res.match.concat(S.sc.res.near).find((x) => x.symbol === pop.dataset.pop); if (r) { pop.innerHTML = `<span class="cap"><b>${esc(r.symbol)}</b> · 60 candles · <i class="e">EMA 20</i> · <i class="s">SMA 50</i></span>${svgChart(r.chart)}`; pop.dataset.done = "1"; }
}

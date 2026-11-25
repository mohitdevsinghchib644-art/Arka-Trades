// Arka Trades · the Watchlist page: a port of watchlist_page.py (v7). The numbers come from wlengine.js.
import { S, api, paint, esc, num, toast, need, todayStr } from "./app.js";
import * as W from "./wlengine.js";
import * as NW from "./news.js";
import { fx } from "./rsengine.js";

export const wlDefaults = () => ({ view: "Today", flt: "All", how: "Your order", heat: "1D", q: "", group: false, rich: true, auto: true, hist: {}, histAt: {}, hver: 0, cals: {}, calAt: {}, dlv: {}, dlvAt: 0, sectors: null,
  desk: null, undo: null, manage: false, sel: {}, busy: "", addMsg: "", optOpen: false });
const HEAT = { "1D": ["pct", "today"], "1W": ["r1w", "1 week"], "1M": ["r1m", "1 month"], "3M": ["r3m", "3 months"], YTD: ["ytd", "this year"], "1Y": ["r1y", "1 year"] };
const NA = '<span class="mu">—</span>';
const cl = (v) => (v == null ? "mu" : v > 0 ? "up" : v < 0 ? "dn" : "");
const sg = (v, d = 2) => (v == null ? NA : fx(v, d, true, true).replace("-", "−"));
const pcs = (v, d = 2) => (v == null ? NA : `<span class="${cl(v)}">${fx(v, d, false, true).replace("-", "−")}%</span>`);
const isDesk = () => S.wl.list === W.DESK;
export const lists = () => [...new Set([...S.wl.rows.map((r) => r.list), W.DEFAULT_LIST, ...(S.wl.extra || [])])];
export const canEdit = () => (isDesk() ? !!(S.wl.desk && S.wl.desk.can_edit) : true);
export function symbols() { return isDesk() ? ((S.wl.desk && S.wl.desk.syms) || []) : S.wl.rows.filter((r) => r.list === S.wl.list).map((r) => r.sym); }
const metaOf = () => (isDesk() ? {} : Object.fromEntries(S.wl.rows.filter((r) => r.list === S.wl.list).map((r) => [r.sym, r])));

// ── data ──
async function pool(items, n, fn) { let i = 0; await Promise.all(Array.from({ length: Math.min(n, items.length) }, async () => { while (i < items.length) { const x = items[i++]; await fn(x); } })); }
const chunks = (a, n) => { const out = []; for (let i = 0; i < a.length; i += n) out.push(a.slice(i, i + n)); return out; };
export async function load(force) {                          // two years of daily candles, deliveries, calendars, names and sectors for the open list
  const L = S.wl, syms = symbols(), now = Date.now();
  if (isDesk() && (!L.desk || force)) { try { L.desk = await api("/api/watchlist?desk=1"); } catch { L.desk = L.desk || { syms: [], can_edit: false }; } paint(); return load(false); }
  need(syms);
  const todo = [...syms, "^NSEI"].filter((s) => force || !L.hist[s] || now - (L.histAt[s] || 0) > 6 * 3600e3);
  const jobs = [];
  if (todo.length) jobs.push(pool(chunks(todo, 6), 3, async (part) => {
    try { const d = await api("/api/history?range=2y&s=" + encodeURIComponent(part.join(","))); for (const h of d.history) { L.hist[h.sym] = h.ok && h.daily !== false ? h : { ok: false }; L.histAt[h.sym] = Date.now(); } }
    catch { for (const s of part) if (!L.hist[s]) { L.hist[s] = { ok: false }; L.histAt[s] = Date.now() - 6 * 3600e3 + 120000; } }
    L.hver++; paint();
  }));
  if (S.nw.names === null) jobs.push(api("/api/market?f=names").then((n) => { S.nw.names = n || {}; }).catch(() => { S.nw.names = {}; }));
  if (!L.sectors) jobs.push(fetch("/sectors.json").then((r) => (r.ok ? r.json() : {})).then((j) => { L.sectors = j; }).catch(() => { L.sectors = {}; }));
  const needDlv = syms.filter((s) => !(s in L.dlv)), stale = force || now - L.dlvAt > 3 * 3600e3;
  if (needDlv.length || stale) { if (stale) L.dlvAt = now; jobs.push(pool(chunks(stale ? syms : needDlv, 40), 2, async (part) => {
    try { const d = await api("/api/dlvlast?s=" + encodeURIComponent(part.join(","))); for (const s of part) L.dlv[s] = d.dlv[s] || null; } catch { /* NSE files not there yet */ } })); }
  if (L.rich) {
    const cs = syms.filter((s) => now - (L.calAt[s] || 0) > 12 * 3600e3);
    if (cs.length) jobs.push(pool(chunks(cs, 8), 2, async (part) => { for (const s of part) L.calAt[s] = Date.now();
      try { const d = await api("/api/cal?s=" + encodeURIComponent(part.join(","))); for (const s of part) if (d.cal[s]) L.cals[s] = d.cal[s]; L.hver++; paint(); } catch { /* later */ } }));
    NW.loadNews();
  }
  if (jobs.length) { await Promise.all(jobs); L.hver++; paint(); }
}
let memo = { key: "", v: null };
export function model() {                                    // rows for the open list, rebuilt only when something changed
  const L = S.wl, syms = [...new Set(symbols().filter(Boolean))], now = W.clock();
  const key = [L.list, syms.join(","), L.hver, S.qv, S.nw.ver, L.rich, L.group, S.wl.ver || 0, Math.floor(now.ms / 60000)].join("|");
  if (memo.key === key && memo.v) return memo.v;
  const frames = {}; for (const s of [...syms, "^NSEI"]) { const h = L.hist[s]; frames[s] = W.mergeLive(h && h.ok ? h : null, S.q[s], now); }
  const names = S.nw.names || {}, meta = metaOf(), news = L.rich ? NW.storiesFor(syms) : {};
  const rows = W.buildRows(syms, frames, names, L.dlv, L.rich ? L.cals : {}, news, meta, frames["^NSEI"], now);
  for (const r of rows) { r.loading = !L.hist[r.symbol]; if (!r.name) r.name = (S.q[r.symbol] || {}).name || ""; if (L.group) r.group = (L.sectors || {})[r.symbol] || "Unclassified"; }
  const v = { rows, nifty: W.compute("^NSEI", frames["^NSEI"], null, now), meta, now, syms };
  memo = { key, v }; return v;
}
export const rowOf = (sym) => { if (S.tab !== "watchlist" && !memo.v) return null; try { return model().rows.find((r) => r.symbol === sym) || null; } catch { return null; } };

// ── small pieces (watchlist_page cells) ──
function sparkDaily(vals, w = 96, h = 26) {
  const pts = (vals || []).filter((p) => p != null); if (pts.length < 2) return NA;
  const lo = Math.min(...pts), hi = Math.max(...pts), sp = hi - lo || 1;
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${pts.map((p, i) => `${((i / (pts.length - 1)) * w).toFixed(1)},${(h - 3 - ((p - lo) / sp) * (h - 6)).toFixed(1)}`).join(" ")}" fill="none" stroke="${pts[pts.length - 1] >= pts[0] ? W.GREEN : W.RED}" stroke-width="1.4"/></svg>`;
}
function sparkIntraday(pts, prev, w = 100, h = 28) {         // x = minutes since 9:15 across the full 375-minute session; the dashed line is the previous close
  if (!pts || pts.length < 2) return NA;
  const vals = pts.map((p) => p[1]).concat(prev ? [prev] : []), lo = Math.min(...vals), hi = Math.max(...vals), sp = hi - lo || 1, y = (p) => h - 3 - ((p - lo) / sp) * (h - 6);
  const col = prev == null || pts[pts.length - 1][1] >= prev ? W.GREEN : W.RED;
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${prev ? `<line x1="0" x2="${w}" y1="${y(prev).toFixed(1)}" y2="${y(prev).toFixed(1)}" stroke="#555" stroke-dasharray="2 2" stroke-width="1"/>` : ""}<polyline points="${
    pts.map(([m, p]) => `${((Math.max(0, Math.min(375, m)) / 375) * w).toFixed(1)},${y(p).toFixed(1)}`).join(" ")}" fill="none" stroke="${col}" stroke-width="1.4"/></svg>`;
}
const word = (t) => (!t ? NA : `<span class="wl-word" style="--c:${t[1]}"${t[2] ? ` title="${esc(t[2])}"` : ""}>${esc(t[0])}</span>`);
function chips(evs, cap = 2) {
  if (!evs.length) return "";
  const order = (e) => (String(e[0]).startsWith("Alert") ? 0 : W.isNewsEvent(e[0]) ? 1 : 2), s = evs.map((e, i) => [e, i]).sort((a, b) => order(a[0]) - order(b[0]) || a[1] - b[1]).map((x) => x[0]);
  return `<span class="wl-chips">${s.slice(0, cap).map(([lab, c, tip]) => `<span class="wl-chip" style="--c:${c}" title="${esc(tip)}">${esc(W.nice(lab))}</span>`).join("")}${s.length > cap ? `<span class="wl-more" title="Open the stock to see all">+${s.length - cap}</span>` : ""}</span>`;
}
const stCell = (r, meta, extra = "") => `<span class="wl-st"><b>${esc(r.symbol)}${(meta[r.symbol] || {}).note ? '<em title="Has a note">✎</em>' : ""}</b><small>${esc(r.name || (r.has_data ? "" : r.loading ? "loading…" : "no price data"))}</small>${extra ? `<small class="only-m">${extra}</small>` : ""}</span>`;
const pb = (r) => (!r.has_data ? `<span class="wl-pb only-m"><i class="mu">${r.loading ? "…" : "no data"}</i></span>` : `<span class="wl-pb only-m"><b>${W.fmtPrice(r.last)}</b>${r.pct != null ? `<i class="${cl(r.pct)}">${sg(r.chg)} (${fx(r.pct, 2, false, true).replace("-", "−")}%)</i>` : ""}</span>`);
const px = (r) => (!r.has_data ? `<span class="d mu">${r.loading ? "loading" : "no data"}</span>` : `<span class="d wl-px">${W.fmtPrice(r.last)}${r.live ? '<i class="wl-lv" title="Live"></i>' : ""}</span>`);
const numCell = (v, d = 2) => (v == null ? `<span class="d mu">—</span>` : `<span class="d ${cl(v)}">${fx(v, d, false, true).replace("-", "−")}%</span>`);
function rowCells(r, view, meta) {
  if (view === "Today") {
    const vx = r.vol_x, vol = r.has_data ? `${W.fmtQty(r.volume)}<small style="color:${vx && vx >= 2 ? W.YELLOW : "var(--tx3)"}">${vx ? fx(vx, 1) + "×" : ""}</small>` : NA;
    const ev = chips(r.events, 2), sp = r.has_data ? sparkIntraday((S.q[r.symbol] || {}).pts, r.prev) : "";
    return stCell(r, meta) + `<span class="wl-sp">${sp}</span>` + pb(r) + px(r) + `<span class="d ${cl(r.chg)}">${r.chg != null ? sg(r.chg) : "—"}</span>` + numCell(r.pct) + `<span class="d wide wl-vol">${vol}</span><span class="wl-ev${ev ? "" : " none"}">${ev || NA}</span>`;
  }
  if (view === "Returns") {
    const sub = r.has_data ? [["1W", "r1w"], ["1M", "r1m"], ["1Y", "r1y"]].map(([lab, k]) => `${lab} ${pcs(r[k], 1)}`).join(" · ") : "";
    return stCell(r, meta, sub) + pb(r) + px(r) + ["r1w", "r1m", "r3m", "ytd", "r1y"].map((k) => numCell(r[k], 1)).join("") + `<span class="d wide wl-sp">${r.has_data ? sparkDaily(r.spark) : ""}</span>`;
  }
  const tw = W.trendWord(r), rw = W.rsiWord(r.rsi), vx = r.vol_x, words = [tw ? word(tw) : "", rw ? word(["RSI " + rw[0], rw[1]]) : ""].filter(Boolean).join(" ");
  return stCell(r, meta) + pb(r) + px(r) + numCell(r.pct) + `<span class="d">${tw ? word(tw) : NA}</span><span class="d">${rw ? word(rw) : NA}</span>` + numCell(r.off_hi52, 1) +
    `<span class="d wide" style="color:${vx && vx >= 2 ? W.YELLOW : vx && vx >= 1.2 ? W.GREEN : "var(--tx2)"}">${vx ? fx(vx, 1) + "×" : "—"}</span><span class="wl-ev only-m${words ? "" : " none"}">${words}</span>`;
}
const HEADS = { Today: [["Stock", "l", ""], ["Chart today", "c", "Today in 5-minute steps; the dashed line is yesterday's close"], ["Price", "", "Last traded price (₹)"], ["Change", "", "Rupees up or down since yesterday's close"], ["Change %", "", ""],
    ["Volume", "wide", "Shares traded today, and how many times the usual day"], ["What's happening", "l", "News, results, your alerts and unusual price moves. Open the stock to read them"], ["", "", ""]],
  Returns: [["Stock", "l", ""], ["Price", "", ""], ["1 week", "", ""], ["1 month", "", ""], ["3 months", "", ""], ["This year", "", "Since 31 December"], ["1 year", "", ""], ["Last 3 months", "wide c", "Daily closes"], ["", "", ""]],
  Technicals: [["Stock", "l", ""], ["Price", "", ""], ["Change %", "", ""], ["Trend", "", "Price against its 20-day, 50-day and 200-day averages"], ["RSI (14 days)", "", "Momentum from 0 to 100: above 70 is stretched, below 30 is washed out"],
    ["From 52W high", "", "How far the price is below its highest price of the last year"], ["Volume", "wide", "Today against the usual day"], ["", "", ""]] };
function listHtml(rows, view, meta, group) {
  const vc = { Today: "v-today", Returns: "v-ret", Technicals: "v-tech" }[view], edit = canEdit();
  const head = HEADS[view].map(([h, c, tip]) => `<span class="${c}" title="${esc(tip)}">${esc(h)}</span>`).join("");
  let last = null; const body = [];
  for (const r of rows) {
    if (group && r.group !== last) { last = r.group; const mem = rows.filter((x) => x.group === last), pcts = mem.filter((x) => x.pct != null).map((x) => x.pct), avg = pcts.length ? pcts.reduce((a, b) => a + b, 0) / pcts.length : null;
      body.push(`<div class="wl-grp">${esc(last)}<span>${mem.length} stock${mem.length !== 1 ? "s" : ""}${avg != null ? ` · average <b class="${cl(avg)}">${fx(avg, 2, false, true).replace("-", "−")}%</b>` : ""}</span></div>`); }
    body.push(`<div class="wl-row ${vc}" data-sym="${esc(r.symbol)}" role="button" tabindex="0">${rowCells(r, view, meta)}${edit ? `<button class="d wl-x" data-act="wlrm" data-s="${esc(r.symbol)}" title="Remove ${esc(r.symbol)} from this list" aria-label="Remove ${esc(r.symbol)}">✕</button>` : '<span class="d"></span>'}</div>`);
  }
  return `<div class="card wl-list"><div class="wl-hd ${vc}">${head}</div>${body.join("")}</div>`;
}
function heatHtml(rows, field, group) {
  const tile = (r) => { const [bg, txt] = W.heatColour(r[field], field); return `<button class="wl-tile" data-sym="${esc(r.symbol)}" style="background:${bg}"><b>${esc(r.symbol)}</b><i>${W.fmtPrice(r.last)}</i><u>${txt.replace("-", "−")}</u></button>`; };
  const srt = rows.map((r, i) => [r, i]).sort((a, b) => (b[0][field] ?? -1e9) - (a[0][field] ?? -1e9) || a[1] - b[1]).map((x) => x[0]);
  if (!group) return `<div class="wl-heat">${srt.map(tile).join("")}</div>`;
  return [...new Set(rows.map((r) => r.group || "").sort())].map((g) => { const mem = srt.filter((r) => (r.group || "") === g); return `<div class="wl-gh">${esc(g)} · ${mem.length}</div><div class="wl-heat">${mem.map(tile).join("")}</div>`; }).join("");
}
function summaryHtml(rows, nifty) {
  const s = W.summary(rows), tot = Math.max(1, s.n), np = nifty && nifty.has_data ? nifty.pct : null;
  const extra = [s.news ? `${s.news} in the news` : "", s.alerts ? `${s.alerts} alert${s.alerts !== 1 ? "s" : ""} hit` : ""].filter(Boolean).join(" · ");
  const mv = (lab, r) => `<div><span>${lab}</span><b>${r ? esc(r.symbol) : NA}</b><small>${r ? pcs(r.pct) : ""}</small></div>`;
  return `<div class="card kp kp4 wl-sum"><div><span>Today</span><b><span class="up">${s.adv} up</span> <span class="mu">·</span> <span class="dn">${s.dec} down</span></b>
      <div class="wl-bb"><i style="width:${((s.adv / tot) * 100).toFixed(1)}%;background:${W.GREEN}"></i><i style="width:${(((s.n - s.adv - s.dec) / tot) * 100).toFixed(1)}%;background:#3A3A40"></i><i style="width:${((s.dec / tot) * 100).toFixed(1)}%;background:${W.RED}"></i></div></div>
    <div><span>Your list on average</span><b>${pcs(s.avg)}</b><small>NIFTY 50 ${np != null ? pcs(np) : "—"}${extra ? " · " + extra : ""}</small></div>${mv("Best today", s.best)}${mv("Worst today", s.worst)}</div>`;
}

// ── the page ──
export function page() {
  const L = S.wl, ls = lists(), edit = canEdit(), desk = isDesk();
  if (!desk && !ls.includes(L.list)) L.list = W.DEFAULT_LIST;
  const pills = `<div class="seg wl-lists">${[...ls, W.DESK].map((l) => `<button class="${l === L.list ? "on" : ""}" data-act="list" data-v="${esc(l)}">${esc(l)}</button>`).join("")}${L.lists === false ? "" : '<button class="ghost" data-act="newlist">+ New list</button>'}</div>`;
  if (!L.loaded) return `<div class="wl"><div class="hd"><div><h1>Watchlist</h1><div class="sub">Loading…</div></div></div><div class="card"><div class="note">Loading your watchlist…</div></div></div>`;
  if (L.err) return `<div class="wl"><div class="hd"><div><h1>Watchlist</h1></div></div><div class="card"><div class="empty"><b>Watchlist is not available</b>${esc(L.err)}</div></div></div>`;
  const syms = symbols(), sub = desk ? "Picked by the Arka desk" + (edit ? " · you can edit" : " · read-only") : `${syms.length} stock${syms.length !== 1 ? "s" : ""} · saved to your account`;
  const addBox = !edit ? "" : `<form id="wladd" class="wl-add"><input name="text" placeholder="${innerWidth < 700 ? "Add stocks: TCS, Reliance" : `Add stocks to ${esc(L.list)}: symbols or company names, separated by commas`}" autocomplete="off" spellcheck="false" aria-label="Add stocks"><button class="btn pri" type="submit"${L.busy ? " disabled" : ""}>${L.busy === "add" ? "Checking…" : "Add"}</button>
      <label class="btn" title="TradingView export, CSV or TXT. A column named symbol or ticker is used, otherwise the first column.">Import<input type="file" accept=".csv,.txt,text/csv,text/plain" data-wl="import" hidden></label></form>${L.addMsg ? `<div class="note wl-msg">${L.addMsg}</div>` : ""}`;
  const u = L.undo && L.undo.list === L.list && Date.now() - L.undo.t < 90000 ? `<div class="wl-undo">Removed <b>${esc(L.undo.sym)}</b> from ${esc(L.undo.list)}.<span class="sp"></span><button class="btn pri sm" data-act="wlundo">Undo</button><button class="btn sm" data-act="wlundox" aria-label="Dismiss">✕</button></div>` : "";
  const head = `<div class="hd"><div><h1>${esc(L.list)}</h1><div class="sub">${esc(sub)}</div></div></div>${pills}${addBox}${u}`;
  if (desk && !L.desk) return `<div class="wl">${head}<div class="card"><div class="note">Loading the Arka desk list…</div></div></div>`;
  if (!syms.length) return `<div class="wl">${head}<div class="card"><div class="empty">${edit ? "<b>This list is empty</b>Type a stock in the box above and press Add, for example TCS or Reliance. You can add several at once, separated by commas, or import a TradingView or CSV file." : "<b>The Arka desk list is empty</b>"}</div></div>${edit && !desk ? manage([]) : ""}</div>`;
  const M = model(), rows = M.rows, view = W.VIEWS6.includes(L.view) ? L.view : "Today";
  const counts = Object.fromEntries(W.FILTERS6.map(([lab, raw]) => [lab, W.filterRows(rows, raw).length])), opts = W.FILTERS6.map(([lab]) => lab).filter((lab) => lab === "All" || counts[lab]);
  if (!opts.includes(L.flt)) L.flt = "All";
  const fmt = (f) => (f === "All" ? "All" : `${f} · ${counts[f]}`), raw = Object.fromEntries(W.FILTERS6)[L.flt] || "All";
  let shown = W.filterRows(rows, raw, L.q); if (view !== "Heatmap") shown = W.sortRows(shown, W.SORTS6[L.how] || "List order", L.group);
  const sortSel = view === "Heatmap" ? `<select data-wl="heat" aria-label="Colour by">${Object.entries(HEAT).map(([k, v]) => `<option value="${k}"${L.heat === k ? " selected" : ""}>Colour: ${v[1]}</option>`).join("")}</select>`
    : `<select data-wl="how" aria-label="Sort">${Object.keys(W.SORTS6).map((k) => `<option value="${esc(k)}"${L.how === k ? " selected" : ""}>Sort: ${esc(k)}</option>`).join("")}</select>`;
  const controls = `<div class="wl-ctl"><input class="only-d" data-wl="q" value="${esc(L.q)}" placeholder="Find a stock in this list" autocomplete="off" spellcheck="false" aria-label="Find a stock in this list">
      <div class="seg wl-views">${W.VIEWS6.map((v) => `<button class="${v === view ? "on" : ""}" data-act="wlview" data-v="${v}">${v}</button>`).join("")}</div>
      <select class="only-m" data-wl="flt" aria-label="Show">${opts.map((f) => `<option value="${esc(f)}"${L.flt === f ? " selected" : ""}>Show: ${esc(fmt(f))}</option>`).join("")}</select>${sortSel}</div>
    <div class="wl-flt only-d">${opts.map((f) => `<button class="${L.flt === f ? "on" : ""}" data-act="wlflt" data-v="${esc(f)}">${esc(fmt(f))}</button>`).join("")}</div>`;
  const body = !shown.length ? '<div class="card"><div class="empty"><b>Nothing to show</b>No stock in this list matches the search or filter.</div></div>'
    : view === "Heatmap" ? heatHtml(shown, HEAT[L.heat][0], L.group) : listHtml(shown, view, M.meta, L.group);
  const asof = rows.filter((r) => r.has_data).map((r) => r.date).sort().pop(), d2 = (d) => `${W.dayMon(d)} ${d.slice(0, 4)}`;
  const foot = `<div class="note wl-foot">Showing ${shown.length} of ${rows.length} stocks${asof ? ` · prices as of ${d2(asof)}` : ""} · prices from Yahoo Finance, delivery from NSE · an amber dot means the day is still trading. Open a stock for its details, news, note and price alerts${edit ? "; to remove one, point at its row and click ✕, or open it and tap Remove" : ""}.</div>`;
  const tools = `<div class="wl-tools"><button class="btn sm" data-act="wlrefresh">Refresh</button><button class="btn sm" data-act="wlopt">Options</button><button class="btn sm only-d" data-act="wlcsv">CSV</button><button class="btn sm only-d" data-act="wltv" title="A file you can import into a TradingView watchlist">TradingView</button></div>
    ${L.optOpen ? `<div class="card pad wl-opt"><label class="f ck"><input type="checkbox" data-wl="group"${L.group ? " checked" : ""}><span>Group by sector</span></label><label class="f ck"><input type="checkbox" data-wl="rich"${L.rich ? " checked" : ""}><span>News, filings and results dates</span></label>
      <label class="f ck"><input type="checkbox" data-wl="auto"${L.auto ? " checked" : ""}><span>Live refresh while NSE is open</span></label></div>` : ""}`;
  return `<div class="wl">${head}${summaryHtml(rows, M.nifty)}${controls}${body}${foot}${tools}${edit ? manage(rows) : ""}</div>`;
}
function manage(rows) {                                      // watchlist_page._manage: remove several, remove the ones without data, rename
  const L = S.wl, dead = rows.filter((r) => !r.has_data && !r.loading).map((r) => r.symbol), nSel = rows.filter((r) => L.sel[r.symbol]).length, desk = isDesk();
  return `<details class="card pad wl-man"${L.manage ? " open" : ""}><summary>Manage list <span class="mu">· remove several, rename</span></summary>
    ${rows.length ? `<div class="wl-pick">${rows.map((r) => `<label><input type="checkbox" data-wl="sel" value="${esc(r.symbol)}"${L.sel[r.symbol] ? " checked" : ""}><span>${esc(r.symbol)}</span></label>`).join("")}</div>
      <div class="ctl"><button class="btn sm red" data-act="wlrmsel"${nSel ? "" : " disabled"}>Remove ${nSel} selected</button><button class="btn sm" data-act="wlrmdead"${dead.length ? "" : " disabled"} title="Symbols that return no price: usually typos, renamed or delisted stocks">Remove ${dead.length} without data</button></div>` : ""}
    ${!desk && L.lists !== false && L.list !== W.DEFAULT_LIST ? `<form id="wlren" class="form wl-ren"><label class="f"><span>Rename this list</span><input name="to" value="${esc(L.list)}" maxlength="24" autocomplete="off"></label><div class="err"></div><button class="btn sm" type="submit">Rename</button></form>` : ""}
    ${L.lists === false ? '<div class="mini">Several named lists, notes and price alerts need the extra watchlist columns. Open Watchlist in the classic app once: Manage list → Storage shows the SQL to run.</div>' : ""}</details>`;
}

// ── the stock sheet: the watchlist's numbers, what is happening today, and the last 24 hours of news and filings (panel_body_html) ──
function storyHtml(a) {
  const [clock, rel] = W.when(a.pub), [tag, tc] = W.storyTag(a), sc = Math.trunc(a.score || 0), link = a.link || "";
  const meta = [clock ? `<span class="t">${esc(clock)}</span>` : "", esc(rel), esc(a.source || "")].filter(Boolean).join('<span class="dot">·</span>');
  const tags = (tag ? `<span class="wl-k" style="--c:${tc}">${esc(tag)}</span>` : "") + (sc > 0 ? `<span class="wl-k" style="--c:${W.GREEN}">Positive</span>` : sc < 0 ? `<span class="wl-k" style="--c:${W.RED}">Negative</span>` : "");
  let summ = String(a.summary || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim(); if (summ.toLowerCase() === String(a.title || "").trim().toLowerCase()) summ = ""; if (summ.length > 320) summ = summ.slice(0, 320) + "…";
  const body = `<div class="wl-nm">${meta}${tags}</div><div class="wl-nt">${esc(String(a.title || "").slice(0, 240))}</div>${summ ? `<div class="wl-nd">${esc(summ)}</div>` : ""}${link ? `<div class="wl-go">${a.kind === "filing" ? "Open filing" : "Read full story"} ↗</div>` : ""}`;
  return link ? `<a class="wl-ns" href="${esc(link)}" target="_blank" rel="noopener noreferrer">${body}</a>` : `<div class="wl-ns">${body}</div>`;
}
export function sheetBlocks(sym) {                            // { stats, more } for the sheet of a stock on the open list, or null
  const r = rowOf(sym); if (!r) return null;
  if (!r.has_data) return { stats: r.loading ? '<div class="note">Loading numbers…</div>' : '<div class="note">Yahoo Finance returns no prices for this symbol. It may be misspelt, renamed or delisted: you can remove it from the list.</div>', more: "" };
  const d = S.wl.dlv[sym], tw = W.trendWord(r), rw = W.rsiWord(r.rsi), m = metaOf()[sym] || {}, vx = r.vol_x, stat = (l, v) => `<div><span>${l}</span><b>${v}</b></div>`;
  const alerts = [m.above > 0 ? `above ₹${W.fmtPrice(m.above)}` : "", m.below > 0 ? `below ₹${W.fmtPrice(m.below)}` : ""].filter(Boolean).join(" · ");
  const stats = `<h2>Key numbers</h2><div class="stats">${stat("Volume today", `${W.fmtQty(r.volume)}${vx ? ` <em>${fx(vx, 1)}× usual</em>` : ""}`)}${stat("Delivery", d ? `${fx(d.pct, 0)}%${d.avg ? ` <em>usual ${fx(d.avg, 0)}%</em>` : ""}` : "—")}
    ${stat("Trend", tw ? `<span style="color:${tw[1]}">${esc(tw[0])}</span>` : "—")}${stat("RSI (14 days)", rw ? `<span style="color:${rw[1]}">${esc(rw[0])}</span>` : "—")}${stat("1 month", pcs(r.r1m, 1))}${stat("1 year", pcs(r.r1y, 1))}
    ${stat("Open · prev. close", `${W.fmtPrice(r.open)} · ${W.fmtPrice(r.prev)}`)}${stat("Your alerts", alerts ? esc(alerts) : '<span class="mu">none set</span>')}</div>`;
  const sig = r.events.filter((e) => !W.isNewsEvent(e[0])), stories = r.stories || [];
  const more = `<h2>What's happening today <span>${sig.length || "nothing unusual"}</span></h2>${sig.length ? sig.map(([lab, c, tip]) => `<div class="wl-sig"><span class="wl-chip" style="--c:${c}">${esc(W.nice(lab))}</span><p>${esc(tip)}</p></div>`).join("") : '<div class="note">No unusual price moves today.</div>'}
    <h2>News and filings <span>last 24 hours</span></h2>${stories.length ? stories.slice(0, 8).map(storyHtml).join("") + (stories.length > 8 ? `<div class="note">+ ${stories.length - 8} older stories on the stock page (Research → CN).</div>` : "") : `<div class="note">${S.wl.rich ? "No news or filings about this stock in the last 24 hours." : "News is switched off under Options."}</div>`}`;
  return { stats, more };
}

// ── actions ──
async function resolveTokens(text) {                         // watchlist_page.resolve_tokens: NSE names list, then a price check, then a search by company name
  const pairs = new Map(); for (const t of W.splitTokens(text)) { const s = W.cleanSymbol(t); if (s && !pairs.has(s)) pairs.set(s, t.trim()); }
  if (!pairs.size) return [[], []];
  const names = S.nw.names || {}, check = [...pairs.keys()].filter((s) => !(s in names)), live = {};
  for (const part of chunks(check, 40)) { try { const d = await api("/api/quotes?s=" + encodeURIComponent(part.join(","))); for (const q of d.quotes) if (q.ok) { live[q.sym] = true; S.q[q.sym] = q; } } catch { /* next */ } }
  const valid = [], bad = [];
  for (const [s, rawText] of pairs) {
    if (s in names || live[s]) { valid.push(s); continue; }
    let hit = ""; try { const d = await api("/api/search?q=" + encodeURIComponent(rawText.slice(0, 40))); hit = (d.results && d.results[0] && d.results[0].sym) || ""; } catch { hit = ""; }
    if (hit) valid.push(W.cleanSymbol(hit)); else bad.push(s);
  }
  return [[...new Set(valid)], bad];
}
export async function addText(text) {
  const L = S.wl, list = L.list; if (!text || !text.trim() || L.busy) return;
  L.busy = "add"; L.addMsg = ""; paint();
  try {
    const [valid, bad] = await resolveTokens(text), msg = [];
    if (valid.length) {
      const d = isDesk() ? await api("/api/watchlist?desk=1", { method: "POST", body: JSON.stringify({ syms: valid }) }) : await api("/api/watchlist", { method: "POST", body: JSON.stringify({ syms: valid, list }) });
      const added = d.added || [];
      if (isDesk()) L.desk = { ...(L.desk || { can_edit: true }), syms: [...new Set([...((L.desk || {}).syms || []), ...added])] }; else for (const sym of added) L.rows.push({ sym, list, note: "", above: null, below: null });
      if (added.length) toast(`Added ${added.slice(0, 6).join(", ")}${added.length > 6 ? "…" : ""} to ${list}`); else msg.push("Already in this list.");
    }
    if (bad.length) msg.push("Not found on NSE: " + esc(bad.join(", ")) + ". Check the spelling or use the NSE ticker.");
    L.addMsg = msg.join(" ");
  } catch (e) { L.addMsg = "Could not add: " + esc(e.message); }
  L.busy = ""; S.wl.ver = (S.wl.ver || 0) + 1; paint(); load();
}
export async function importFile(file) {                      // TradingView export, CSV or TXT: a column named symbol / ticker, otherwise the first column
  if (!file) return;
  let text = (await file.text()).replace(/^﻿/, "");
  if (/\.csv$/i.test(file.name)) {
    const lines = text.split(/\r?\n/).filter((l) => l.trim()), cells = (l) => l.split(",").map((c) => c.trim().replace(/^"|"$/g, "")), head = cells(lines[0] || "");
    const k = head.findIndex((c) => ["symbol", "symbols", "ticker", "stock", "tradingsymbol"].includes(c.toLowerCase())), col = k >= 0 ? k : 0;
    text = lines.slice(1).map((l) => cells(l)[col]).filter(Boolean).join(",");                  // the first line is the header, as pandas reads it
  }
  return addText(text.replace(/\n/g, ","));
}
export async function remove(syms, undoable) {
  const L = S.wl, list = L.list, desk = isDesk(); syms = syms.filter(Boolean); if (!syms.length) return;
  const before = desk ? { ...L.desk } : L.rows.slice(), metas = Object.fromEntries(L.rows.filter((r) => r.list === list && syms.includes(r.sym)).map((r) => [r.sym, { note: r.note, above: r.above, below: r.below }]));
  if (desk) L.desk = { ...L.desk, syms: L.desk.syms.filter((s) => !syms.includes(s)) }; else L.rows = L.rows.filter((r) => !(r.list === list && syms.includes(r.sym)));
  for (const s of syms) delete L.sel[s];
  if (undoable && syms.length === 1) L.undo = { sym: syms[0], list, desk, t: Date.now(), meta: metas[syms[0]] || {} };
  S.wl.ver = (S.wl.ver || 0) + 1; paint();
  try { await api(desk ? `/api/watchlist?desk=1&s=${encodeURIComponent(syms.join(","))}` : `/api/watchlist?ss=${encodeURIComponent(syms.join(","))}&list=${encodeURIComponent(list)}`, { method: "DELETE" });
    if (!undoable) toast(`Removed ${syms.length} stock${syms.length !== 1 ? "s" : ""} from ${list}`); else setTimeout(() => { if (L.undo && Date.now() - L.undo.t >= 90000) { L.undo = null; paint(); } }, 90500); }
  catch (e) { if (desk) L.desk = before; else L.rows = before; L.undo = null; S.wl.ver++; paint(); toast("Could not remove. " + e.message); }
}
export async function undo() {                                // the stock comes back with its note and alerts (watchlist_page._undo)
  const L = S.wl, u = L.undo; if (!u) return; L.undo = null;
  try {
    if (u.desk) { await api("/api/watchlist?desk=1", { method: "POST", body: JSON.stringify({ syms: [u.sym] }) }); L.desk = { ...L.desk, syms: [...new Set([...L.desk.syms, u.sym])] }; }
    else { await api("/api/watchlist", { method: "POST", body: JSON.stringify({ syms: [u.sym], list: u.list, meta: { [u.sym]: u.meta } }) }); L.rows.push({ sym: u.sym, list: u.list, note: u.meta.note || "", above: u.meta.above ?? null, below: u.meta.below ?? null }); }
    toast(`${u.sym} is back in ${u.list}`);
  } catch (e) { toast("Could not bring it back. " + e.message); }
  S.wl.ver = (S.wl.ver || 0) + 1; paint(); load();
}
export async function rename(form) {
  const L = S.wl, to = String(new FormData(form).get("to") || "").replace(/[^\w &.-]/g, "").trim().slice(0, 24), err = form.querySelector(".err");
  if (!to || to === L.list) { err.textContent = "Type a different name."; return; }
  if (lists().includes(to) || to === W.DESK) { err.textContent = "A list with that name already exists."; return; }
  try { await api("/api/watchlist", { method: "PUT", body: JSON.stringify({ from: L.list, to }) }); for (const r of L.rows) if (r.list === L.list) r.list = to; L.extra = (L.extra || []).map((x) => (x === L.list ? to : x)); L.list = to; S.wl.ver = (S.wl.ver || 0) + 1; toast("List renamed to " + to); paint(); }
  catch (e) { err.textContent = e.message; }
}
function download(name, text, type) { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type })); a.download = name; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 2000); toast("Downloaded " + name); }
function shownRows() { const L = S.wl, M = model(), raw = Object.fromEntries(W.FILTERS6)[L.flt] || "All"; let s = W.filterRows(M.rows, raw, L.q); if (L.view !== "Heatmap") s = W.sortRows(s, W.SORTS6[L.how] || "List order", L.group); return s; }
export function act(name, el) {
  const L = S.wl;
  if (name === "wlview") { L.view = el.dataset.v; return paint(); }
  if (name === "wlflt") { L.flt = el.dataset.v; return paint(); }
  if (name === "wlopt") { L.optOpen = !L.optOpen; return paint(); }
  if (name === "wlrefresh") { S.at = {}; toast("Refreshing prices"); need(symbols()); return load(true); }
  if (name === "wlrm") return remove([el.dataset.s], true);
  if (name === "wlundo") return undo();
  if (name === "wlundox") { L.undo = null; return paint(); }
  if (name === "wlrmsel") { const s = symbols().filter((x) => L.sel[x]); if (s.length && confirm(`Remove ${s.length} stock${s.length !== 1 ? "s" : ""} from ${L.list}?`)) { L.manage = true; return remove(s, false); } return; }
  if (name === "wlrmdead") { const s = model().rows.filter((r) => !r.has_data && !r.loading).map((r) => r.symbol); if (s.length && confirm(`Remove ${s.length} stock${s.length !== 1 ? "s" : ""} without price data from ${L.list}?`)) { L.manage = true; return remove(s, false); } return; }
  if (name === "wlcsv") { const key = (isDesk() ? "desk" : "l_" + L.list.replace(/\W/g, "_")), c = (v) => (v == null ? "" : /[",\n]/.test(String(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : String(v));
    const cols = ["symbol", "name", "ltp", "chg", "pct_chg", "open", "high", "low", "prev_close", "volume", "vol_x_20d", "deliv_pct", "r1w", "r1m", "r3m", "ytd", "r1y", "rsi", "events"];
    const rows = shownRows().map((r) => [r.symbol, r.name, r.last, r.chg, r.pct, r.open, r.high, r.low, r.prev, r.volume, r.vol_x, r.deliv_pct, r.r1w, r.r1m, r.r3m, r.ytd, r.r1y, r.rsi, r.events.map((e) => W.nice(e[0])).join(" | ")]);
    return download(`arka_${key}_${todayStr().replace(/-/g, "")}.csv`, [cols, ...rows].map((r) => r.map(c).join(",")).join("\n"), "text/csv"); }
  if (name === "wltv") return download(`arka_${isDesk() ? "desk" : "l_" + L.list.replace(/\W/g, "_")}_tv.txt`, shownRows().map((r) => "NSE:" + r.symbol).join("\n"), "text/plain");
}
export function input(el, ev) {                               // a box or switch on the page changed
  const L = S.wl, k = el.dataset.wl;
  if (k === "import") { if (ev === "change") { importFile(el.files[0]); el.value = ""; } return; }
  if (k === "q") { if (ev !== "input") return; L.q = el.value; return paint(); }
  if (ev !== "change") return;
  if (k === "sel") { L.sel[el.value] = el.checked; L.manage = true; return paint(); }
  if (k === "group" || k === "rich" || k === "auto") { L[k] = el.checked; L.optOpen = true; if (k !== "auto") load(); return paint(); }
  L[k] = el.value; paint();
}

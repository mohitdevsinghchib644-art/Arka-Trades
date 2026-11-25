// Arka Trades · the signal desk, drawn: a line-by-line port of fno_page.py (Desk, Leaders, Swing, Index, Big players, Evidence).
// Pure functions that return HTML: no network and no page, so every tab is compared with the Python page's own output.
import { fx } from "./rsengine.js";

export const GREEN = "var(--green,#30D158)", RED = "var(--red,#FF453A)", AMBER = "var(--amber,#FF9F0A)", BLUE = "var(--cyan,#5AC8FA)", VIOLET = "var(--purple,#BF5AF2)";
export const STATUS = { on: ["Approved", "g"], trial: ["On trial", "a"], off: ["Switched off", "n"] };
export const TABS = ["Desk", "Leaders", "Swing", "Index", "Big players", "Options map", "Evidence"];
const FAMILY = {"Private Banks":"Financials","PSU Banks":"Financials","Small Finance Banks":"Financials","NBFC":"Financials","Housing Finance":"Financials","PSU Financiers":"Financials","Insurance":"Financials","Capital Markets":"Financials","Fintech":"Financials","Investment & Holding Cos":"Financials","Pharmaceuticals":"Healthcare","Healthcare Services":"Healthcare","IT Services":"Technology","Software Products":"Technology","Internet & E-commerce":"Technology","Electronics Manufacturing":"Technology","Telecom":"Technology","Automobiles":"Auto","Auto Components":"Auto","Tyres":"Auto","Electrical Equipment":"Industrials","Cables & Wires":"Industrials","Aerospace & Defence":"Industrials","Railways":"Industrials","Industrial Machinery":"Industrials","Industrial Products":"Industrials","Renewable Energy":"Industrials","Construction & Engineering":"Construction","Cement":"Construction","Building Products":"Construction","Paints":"Construction","Realty":"Construction","Steel":"Metals","Non-Ferrous & Mining":"Metals","Refineries & Lubricants":"Energy","Oil & Gas Exploration":"Energy","Gas Distribution":"Energy","Coal":"Energy","Power":"Energy","Water & Environment":"Energy","Specialty Chemicals":"Chemicals","Commodity Chemicals":"Chemicals","Fertilizers & Agrochemicals":"Chemicals","FMCG":"Consumer","Alcoholic Beverages":"Consumer","Sugar":"Consumer","Consumer Durables":"Consumer","Footwear & Luggage":"Consumer","Jewellery & Watches":"Consumer","Retail":"Consumer","Textiles & Apparel":"Consumer","Hotels & Travel":"Consumer","Restaurants (QSR)":"Consumer","Media & Entertainment":"Consumer","Logistics & Shipping":"Services","Airlines, Ports & Airports":"Services","Business Services":"Services","Packaging & Paper":"Services"};
const BROAD = ["Financials","Healthcare","Technology","Technology","Auto","Industrials","Construction","Construction","Construction","Metals","Energy","Energy","Energy","Chemicals","Consumer","Consumer","Consumer","Consumer","Consumer","Services","Services","Financials"];
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// ── formatting (fno_page.py) ──
export const e = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
export const ok = (x) => typeof x === "number" && isFinite(x);
const f = (v, d) => fx(v, d, false);                          // f"{v:.{d}f}"
export const num = (x, d = 0) => (ok(x) ? fx(x, d, true) : "—");
export const px = (x) => (!ok(x) ? "—" : Math.abs(x) >= 5000 ? fx(x, 0, true) : Math.abs(x) >= 100 ? fx(x, 1, true) : fx(x, 2, true));
export const pct = (x, d = 1, sign = true) => (!ok(x) ? "—" : fx(x * 100, d, false, sign) + "%");
export const cls = (x) => (!ok(x) || Math.abs(x) < 1e-9 ? "fl" : x > 0 ? "up" : "dn");
export const sg = (x, d = 1) => `<span class="${cls(x)}">${pct(x, d)}</span>`;
export function qty(x) {
  if (!ok(x)) return "—";
  const a = Math.abs(x), s = x < 0 ? "-" : "";
  return a >= 1e9 ? `${s}${fx(a / 1e7, 0, true)} Cr` : a >= 1e7 ? `${s}${f(a / 1e7, 2)} Cr` : a >= 1e5 ? `${s}${f(a / 1e5, 1)} L` : a >= 1e3 ? `${s}${f(a / 1e3, 0)} K` : `${s}${f(a, 0)}`;
}
export const rs = (x) => (!ok(x) ? "—" : Math.abs(x) >= 1e7 ? `₹${f(x / 1e7, 2)} Cr` : Math.abs(x) >= 1e5 ? `₹${f(x / 1e5, 2)} L` : `₹${fx(x, 0, true)}`);   // rupees the Indian way
const iso = (s) => { const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(s).slice(0, 10)); return m && +m[2] >= 1 && +m[2] <= 12 && +m[3] >= 1 && +m[3] <= 31 ? [+m[1], +m[2], +m[3]] : null; };
export const day = (s, year = true) => { const d = iso(s); return d ? `${d[2]} ${MON[d[1] - 1]}${year ? " " + d[0] : ""}` : "—"; };
export const dshort = (s) => { const d = iso(s); return d ? `${d[2]} ${MON[d[1] - 1]} ${String(d[0]).slice(2)}` : "—"; };
const pyRound = (v) => { const r = Math.round(v); return Math.abs(v % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r; };      // Python's round(): ties to even
const intc = (q) => fx(q, 0, true);                           // f"{q:,}"

// ── building blocks ──
export const pan = (title, meta, body, c = "", flush = false) => `<section class="fd-p ${c}"><header><b>${e(title)}</b><span>${meta}</span></header><div class="fd-b${flush ? " f" : ""}">${body}</div></section>`;
export const grid = (...panels) => `<div class="fd-g">${panels.join("")}</div>`;
const k = (label, val, sub = "") => `<div class="fd-k"><small>${e(label)}</small><b>${val}</b>${sub ? `<em>${sub}</em>` : ""}</div>`;
const ks = (cells, n = 8, inner = false) => `<div class="fd-ks${inner ? " in" : ""}" style="--n:${n}">${cells.join("")}</div>`;
export const pill = (status) => { const [name, c] = STATUS[status] || STATUS.off; return `<span class="fd-s ${c}"><i></i>${name}</span>`; };
const act = (word, kind = "") => `<span class="fd-a ${kind}">${e(word)}</span>`;
const bar = (frac, colour = "") => `<span class="fd-bar"><i style="width:${f(Math.max(0, Math.min(1, ok(frac) ? frac : 0)) * 100, 0)}%${colour ? `;background:${colour}` : ""}"></i></span>`;
const sym = (s, names, sub = "") => { const nm = sub || names[s] || ""; return `<b>${e(s)}</b>` + (nm ? `<small>${e(nm)}</small>` : ""); };
// head = [[label, classes]], rows = [[cell, …]]. Classes: l left · x hidden on phones · xx only on wide screens · b bold.
export function t(head, rows, scroll = true, c = "") {
  const th = head.map(([h, hc]) => `<th class="${hc}">${e(h)}</th>`).join("");
  const body = rows.map((r) => `<tr class="">` + r.map((cell, i) => `<td class="${head[i][1]}">${cell}</td>`).join("") + "</tr>").join("");
  const tb = `<table class="fd-t ${c}"><thead><tr>${th}</tr></thead><tbody>${body}</tbody></table>`;
  return scroll ? `<div class="fd-sc">${tb}</div>` : tb;
}
export const empty = (msg) => `<div class="fd-e">${msg}</div>`;
const legend = (items) => '<div class="fd-leg">' + items.map(([n, c, v]) => `<span><i style="background:${c}"></i>${e(n)}${v ? ` <b>${v}</b>` : ""}</span>`).join("") + "</div>";
function nice(lo, hi, n = 4) {
  const span = hi - lo || 1, raw = span / n, mag = 10 ** Math.floor(Math.log10(raw));
  let best = 1, bd = Infinity; for (const m of [1, 2, 2.5, 5, 10]) { const d = Math.abs(m * mag - raw); if (d < bd) { bd = d; best = m; } }
  const step = best * mag, out = []; let v = (Math.ceil(lo / step) + 0) * step;                // + 0: Python's ceil gives an integer, so there is no minus zero
  while (v <= hi + 1e-12) { out.push(v); v += step; }
  return out;
}
const uniqSorted = (a) => [...new Set(a)].sort((x, y) => x - y);
// Line chart. series = [[name, colour, values, last-value text]]. marks = [[value, label, colour]] drawn as dotted levels.
export function line(dates, series, h = 150, fmt = px, zero = false, marks = []) {
  const vals = series.flatMap(([, , ys]) => ys.filter(ok)).concat(marks.filter((m) => ok(m[0])).map((m) => m[0]));
  if (vals.length < 3) return empty("Not enough history to draw this yet.");
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
  const pad = (hi - lo) * 0.07 || Math.abs(hi) * 0.05 || 1; lo -= pad; hi += pad;
  const n = Math.max(...series.map(([, , ys]) => ys.length)), X = (i) => (i / Math.max(n - 1, 1)) * 1000, Y = (v) => ((hi - v) / (hi - lo)) * h, ticks = nice(lo, hi);
  const gridl = ticks.map((tk) => `<line x1="0" x2="1000" y1="${f(Y(tk), 1)}" y2="${f(Y(tk), 1)}" stroke="${zero && Math.abs(tk) < 1e-12 ? "var(--tx3)" : "var(--line)"}" stroke-width="1" vector-effect="non-scaling-stroke"/>`).join("");
  let paths = "", labels = "";
  for (const [, colr, ys] of series) { const pts = []; ys.forEach((v, i) => { if (ok(v)) pts.push(`${f(X(i), 1)},${f(Y(v), 1)}`); });
    paths += `<polyline points="${pts.join(" ")}" fill="none" stroke="${colr}" stroke-width="1.5" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>`; }
  for (const [v, lab, colr] of marks) if (ok(v)) {
    paths += `<line x1="0" x2="1000" y1="${f(Y(v), 1)}" y2="${f(Y(v), 1)}" stroke="${colr}" stroke-width="1" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"/>`;
    labels += `<span class="hl" style="top:${f((Y(v) / h) * 100, 1)}%;color:${colr}">${e(lab)}</span>`; }
  const yl = ticks.map((tk) => `<span class="yl" style="top:${f((Y(tk) / h) * 100, 1)}%">${fmt(tk)}</span>`).join("");
  let xl = "";
  if (dates && dates.length) { const idx = uniqSorted([0, 1, 2, 3, 4].map((q) => pyRound((q * (dates.length - 1)) / 4))); xl = '<div class="fd-xl">' + idx.map((i) => `<span>${dshort(dates[i])}</span>`).join("") + "</div>"; }
  const leg = legend(series.map(([nm, colr, , last]) => [nm, colr, last || ""]));
  return `${leg}<div class="fd-ch" style="height:${h}px"><svg viewBox="0 0 1000 ${h}" preserveAspectRatio="none">${gridl}${paths}</svg>${yl}${labels}</div>${xl}`;
}
// Bars across strikes. series = [[name, colour, values]]; mark = the price, drawn as a dashed line.
export function strikes(labels, series, h = 150, mark = null) {
  const n = labels.length, vals = series.flatMap(([, , ys]) => ys.filter(ok).map(Math.abs));
  if (n < 3 || !vals.length || Math.max(...vals) <= 0) return empty("No strikes stored for this expiry.");
  const m = Math.max(...vals), slot = 10, kk = series.length, W = n * slot, bw = 8 / kk;
  let rects = "";
  series.forEach(([, colr, ys], j) => ys.forEach((v, i) => { if (ok(v) && v > 0) { const hh = (v / m) * (h - 8);
    rects += `<rect x="${f(i * slot + 1 + j * bw, 2)}" y="${f(h - 1 - hh, 1)}" width="${f(bw * 0.92, 2)}" height="${f(hh, 1)}" fill="${colr}" opacity=".85"/>`; } }));
  let extra = "";
  if (ok(mark) && labels[0] < mark && mark < labels[n - 1]) for (let i = 0; i < n - 1; i++) if (labels[i] <= mark && mark <= labels[i + 1]) {
    const pos = (i + (mark - labels[i]) / (labels[i + 1] - labels[i] || 1)) * slot + slot / 2;
    extra = `<line x1="${f(pos, 1)}" x2="${f(pos, 1)}" y1="0" y2="${h}" stroke="var(--tx)" stroke-width="1.2" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"/>`; break; }
  const idx = uniqSorted([0, 1, 2, 3, 4, 5].map((q) => pyRound((q * (n - 1)) / 5))), xl = '<div class="fd-xl">' + idx.map((i) => `<span>${px(labels[i])}</span>`).join("") + "</div>";
  const items = series.map(([nm, colr]) => [nm, colr, ""]).concat(ok(mark) ? [[`Price ${px(mark)}`, "var(--tx)", ""]] : []);
  return `${legend(items)}<div class="fd-ch" style="height:${h}px"><svg viewBox="0 0 ${f(W, 0)} ${h}" preserveAspectRatio="none">${extra}${rects}</svg><span class="yl" style="top:6%">${qty(m)}</span><span class="yl" style="top:98%">0</span></div>${xl}`;
}
function vbars(labels, values, fmt = (v) => f(v * 100, 1), colour = null) {
  const vals = values.filter(ok); if (!vals.length) return "";
  const m = Math.max(...vals.map(Math.abs)) || 1;
  const cols = values.map((v, i) => `<div><span>${fmt(v)}</span><i style="height:${f(Math.max((Math.abs(v) / m) * 100, 1), 0)}%;background:${colour ? colour(i, v) : AMBER}"></i></div>`).join("");
  return `<div class="fd-vb">${cols}</div><div class="fd-xl" style="margin-right:0">` + labels.map((x) => `<span>${e(x)}</span>`).join("") + "</div>";
}
// Candlestick chart as plain SVG. Point at a candle for its numbers.
export function candles(d, s, marks = [], h = 260) {
  const n = d.length, vals = ["h", "l"].flatMap((kk) => s[kk].filter(ok)).concat(marks.filter((m) => ok(m[0])).map((m) => m[0]));
  if (n < 5 || !vals.length) return empty("No price history stored for this stock yet.");
  let lo = Math.min(...vals), hi = Math.max(...vals); const pad = (hi - lo) * 0.05 || 1; lo -= pad; hi += pad;
  const W = 1000, slot = 1000 / n, Y = (v) => ((hi - v) / (hi - lo)) * h, ticks = nice(lo, hi);
  let out = ticks.map((tk) => `<line x1="0" x2="${f(W, 1)}" y1="${f(Y(tk), 1)}" y2="${f(Y(tk), 1)}" stroke="var(--line)" stroke-width="1" vector-effect="non-scaling-stroke"/>`).join("");
  for (let i = 0; i < n; i++) {
    const o = s.o[i], hh = s.h[i], ll = s.l[i], c = s.c[i]; if (![o, hh, ll, c].every(ok)) continue;
    const colr = c >= o ? GREEN : RED, x = i * slot + slot / 2, top = Y(Math.max(o, c)), bot = Y(Math.min(o, c));
    const tip = `${day(d[i])} · open ${px(o)} · high ${px(hh)} · low ${px(ll)} · close ${px(c)}`;
    out += `<g><title>${tip}</title><line x1="${f(x, 1)}" x2="${f(x, 1)}" y1="${f(Y(hh), 1)}" y2="${f(Y(ll), 1)}" stroke="${colr}" stroke-width="1" vector-effect="non-scaling-stroke"/>` +
      `<rect x="${f(x - slot * 0.32, 1)}" y="${f(top, 1)}" width="${f(slot * 0.64, 1)}" height="${f(Math.max(bot - top, 0.8), 1)}" fill="${colr}"/></g>`;
  }
  const pts = []; (s.m || []).forEach((v, i) => { if (ok(v)) pts.push(`${f(i * slot + slot / 2, 1)},${f(Y(v), 1)}`); });
  if (pts.length) out += `<polyline points="${pts.join(" ")}" fill="none" stroke="${AMBER}" stroke-width="1.3" vector-effect="non-scaling-stroke"/>`;
  let labels = "";
  for (const [v, lab, colr] of marks) if (ok(v)) {
    out += `<line x1="0" x2="${f(W, 1)}" y1="${f(Y(v), 1)}" y2="${f(Y(v), 1)}" stroke="${colr}" stroke-width="1" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"/>`;
    labels += `<span class="hl" style="top:${f((Y(v) / h) * 100, 1)}%;color:${colr}">${e(lab)} ${px(v)}</span>`; }
  const yl = ticks.map((tk) => `<span class="yl" style="top:${f((Y(tk) / h) * 100, 1)}%">${px(tk)}</span>`).join("");
  const idx = uniqSorted([0, 1, 2, 3, 4, 5].map((q) => pyRound((q * (n - 1)) / 5))), xl = '<div class="fd-xl">' + idx.map((i) => `<span>${dshort(d[i])}</span>`).join("") + "</div>";
  return `<div class="fd-ch" style="height:${h}px"><svg viewBox="0 0 ${f(W, 0)} ${h}" preserveAspectRatio="none">${out}</svg>${yl}${labels}</div>${xl}`;
}

// ── sizing ──
export function slotQty(price, sz, slots = 20) {
  if (!ok(price) || price <= 0 || sz.cap_l <= 0) return "—";
  const q = Math.trunc(Math.floor(sz.cap_l / slots / price));
  return q > 0 ? intc(q) : "0";
}
// Shares so that the gap between entry and stop costs the chosen share of capital; never more than a quarter of capital in one trade.
export function riskQty(price, stop, sz) {
  if (!(ok(price) && ok(stop)) || price <= stop || sz.cap_s <= 0) return null;
  const q = Math.floor((sz.cap_s * sz.risk) / (price - stop));
  return Math.max(Math.min(q, Math.floor((sz.cap_s * 0.25) / price)), 0);
}

// ── orders: everything to do at the next open, across the books ──
export function orderRows(S, sz) {
  const out = [], ld = S.leaders, sw = S.swing;
  for (const r of ld.sell || []) out.push({ book: "Leaders", act: "Sell", k: "s", sym: r.sym, ref: r.price, stop: null, qty: "all", note: r.why || "", status: ld.status, pnl: r.pnl ?? null });
  for (const r of ld.buy || []) out.push({ book: "Leaders", act: "Buy", k: "b", sym: r.sym, ref: r.price, stop: null, qty: slotQty(r.price, sz, ld.slots ?? 20), note: "one twentieth of the book", status: ld.status, pnl: null });
  if (sw.status !== "off") {
    for (const r of sw.open || []) if (r.sell) out.push({ book: "Swing", act: "Sell", k: "s", sym: r.sym, ref: r.price, stop: null, qty: "all", note: r.sell, status: sw.status, pnl: r.pnl ?? null });
    for (const r of sw.new || []) { const q = riskQty(r.price, r.stop, sz);
      out.push({ book: "Swing", act: "Buy", k: "b", sym: r.sym, ref: r.price, stop: r.stop, qty: q != null ? intc(q) : "—", note: `stop ${pct(-(r.risk || 0), 1)} below entry`, status: sw.status, pnl: null }); }
  }
  for (const x of S.index || []) {
    if (x.status === "off") continue;
    if (x.action === "buy") out.push({ book: "Index", act: "Buy", k: "b", sym: x.name, ref: x.last, stop: null, qty: "futures or call spread", note: "dip in an uptrend", status: x.status, pnl: null });
    else if (x.action === "sell") out.push({ book: "Index", act: "Sell", k: "s", sym: x.name, ref: x.last, stop: null, qty: "all", note: x.why || "", status: x.status, pnl: (x.pos || {}).pnl ?? null });
  }
  return out;
}
function ordersTable(rows, names) {
  const head = [["Book", "l"], ["Action", "l"], ["Symbol", "l"], ["Last close", ""], ["Stop", ""], ["Quantity", ""], ["Open P&L", "x"], ["Why", "l xx"], ["Status", "l x"]];
  return t(head, rows.map((r) => [e(r.book), act(r.act, r.k), `<span class="s">${sym(r.sym, names)}</span>`, px(r.ref), ok(r.stop) ? px(r.stop) : '<span class="mu">none</span>', e(r.qty), ok(r.pnl) ? sg(r.pnl) : "", e(r.note), pill(r.status)]));
}
function orderCards(rows) {                                  // the same orders as stacked rows, for phones
  let out = "";
  for (const r of rows) {
    const bits = [r.book].concat(ok(r.stop) ? [`stop ${px(r.stop)}`] : [], r.qty !== "" && r.qty !== "—" ? [`qty ${r.qty}`] : [], ok(r.pnl) ? [pct(r.pnl, 1)] : [], r.k === "s" && r.note ? [r.note] : [], r.status !== "on" ? [STATUS[r.status][0].toLowerCase()] : []);
    out += `<div>${act(r.act, r.k)}<b>${e(r.sym)}</b><span class="v">${px(r.ref)}</span><small>${e(bits.join(" · "))}</small></div>`;
  }
  return `<div class="fd-oc">${out}</div>`;
}
function watchRows(S, names) {                               // what could turn into an order in the next few sessions
  const rows = [];
  for (const x of S.index || []) {
    if (x.status === "off") continue;
    if (x.action === "none" && x.above200 && ok(x.trigger) && ok(x.last)) rows.push(["Index", `<b style="color:var(--tx)">${e(x.name)}</b>`, "Buy trigger", px(x.trigger), sg(x.trigger / x.last - 1, 1), "a close at or below this level buys the dip"]);
    else if (x.action === "hold" && ok(x.ma5)) rows.push(["Index", `<b style="color:var(--tx)">${e(x.name)}</b>`, "Exit level", px(x.ma5), sg(x.ma5 / x.last - 1, 1), "a close above the 5-day average sells"]);
  }
  for (const r of (S.swing.open || []).slice(0, 6)) if (!r.sell && ok(r.stop) && ok(r.price)) rows.push(["Swing", `<span class="s">${sym(r.sym, names)}</span>`, "Stop", px(r.stop), sg(r.stop / r.price - 1, 1), `${r.left ?? 0} sessions left`]);
  for (const h of S.leaders.holdings) if (h.flag === "risk") rows.push(["Leaders", `<span class="s">${sym(h.sym, names)}</span>`, "Likely to go", px(h.price), "", `strength ${num((h.rank || 0) * 100, 0)}: sold at the next check unless it recovers`]);
  for (const r of (S.leaders.candidates || []).slice(0, 3)) rows.push(["Leaders", `<span class="s">${sym(r.sym, names)}</span>`, "Next in line", px(r.price), "", `strength ${num((r.rank || 0) * 100, 0)}: bought when a slot opens`]);
  return rows;
}
const nextCheck = (S) => (S.leaders.check_today ? "checked today" : "next check after the first session of next week");

// ── desk ──
export function ticker(S, today) {
  const m = S.market; let age = 0;
  const a = iso(S.asof), b = iso(today); if (a && b) age = Math.round((Date.UTC(b[0], b[1] - 1, b[2]) - Date.UTC(a[0], a[1] - 1, a[2])) / 864e5);
  const cell = (lab, val) => `<div><small>${lab}</small><b>${val}</b></div>`;
  return '<div class="fd-tk">' + cell(age > 4 ? `Prices to <span class="am">· ${age} days old</span>` : "Prices to", day(S.asof)) + cell("NIFTY 500", `${px(m.n500)} ${sg(m.n500_d1, 2)}`) +
    cell("Long-term trend", m.above200 ? '<span class="up">Above 200-day</span>' : '<span class="dn">Below 200-day</span>') + cell("Stocks above 50-day · 200-day", `${pct(m.b50, 0, false)} · ${pct(m.b200, 0, false)}`) +
    cell("52-week highs · lows", `<span class="up">${m.highs ?? 0}</span> · <span class="dn">${m.lows ?? 0}</span>`) + cell("India VIX", num(m.vix, 1)) + cell("Universe", `${m.universe ?? 0} most traded`) + cell("F&O data to", day(S.fno_date, false)) + "</div>";
}
function books(S) {
  const ld = S.leaders, sw = S.swing, w = Object.fromEntries(ld.record.windows.map((x) => [x.key, x])), a = w.all;
  const two = (top, sub) => `<span class="s2"><b>${top}</b><small>${sub}</small></span>`, n2 = (top, sub) => `<span class="s2" style="text-align:right"><b>${top}</b><small>${sub}</small></span>`;
  const rows = [[two("Leaders", "trend · 20 strongest stocks"), pill(ld.status), n2(`<span class="${cls(a.book.cagr)}">${pct(a.book.cagr)}</span> a year`, `NIFTY 500 ${pct(a.index.cagr)}`),
    n2(`${ld.holdings.length} held`, nextCheck(S).replace("after the first session of next week", "next week"))]];
  const s = sw.record.all;
  rows.push([two("Swing", "leader pullback · a few days"), pill(sw.status), n2(`<span class="${cls(s.exp)}">${pct(s.exp, 2)}</span> a trade`, `${pct(s.win, 0, false)} of ${s.n ?? 0} won`), n2(`${sw.open.length} open`, `${sw.new.length} new today`)]);
  for (const x of S.index || []) { const r = x.record.all, state = { buy: "Buy at open", sell: "Sell at open", hold: "In a trade", none: "No signal" }[x.action];
    rows.push([two(e(x.name), "dip in an uptrend"), pill(x.status), n2(`<span class="${cls(r.exp)}">${pct(r.exp, 2)}</span> a trade`, `${pct(r.win, 0, false)} of ${r.n ?? 0} won`), n2(state, `5 sessions ${pct(x.r5, 1)}`)]); }
  const foot = ks([k("Leaders from peak", sg(ld.record.dd_now, 1), "model book"), k("Stay-away list", `${(S.avoid || []).length} stocks`, "weakest tenth, below 200-day")], 2, true);
  return t([["Book", "l"], ["Status", "l"], ["Record", ""], ["Now", "x"]], rows, false) + `<div style="border-top:1px solid var(--line)">${foot}</div>`;
}
const flagOf = (h) => (h.flag === "sell" ? act("Sell", "s") : h.flag === "risk" ? act("Watch", "w") : act("Hold"));
function leadMini(S, names, limit = 10) {
  return t([["Symbol", "l"], ["Close", ""], ["Day", "x"], ["P&L", ""], ["Weight", "x"], ["", ""]], S.leaders.holdings.slice(0, limit).map((h) => [`<span class="s">${sym(h.sym, names)}</span>`, px(h.price), sg(h.d1, 1), sg(h.pnl, 0), pct(h.w, 0, false), flagOf(h)]), false);
}
function leadCurve(S, h = 150) {
  const c = S.leaders.record.curve, mult = (v) => `${fx(1 + v, 1, true)}×`, last = (a) => a[a.length - 1];
  return line(c.date, [["Leaders book", AMBER, c.book, mult(last(c.book))], ["NIFTY 500", "var(--tx2)", c.index, mult(last(c.index))], ["Average liquid stock", BLUE, c.avg, mult(last(c.avg))]], h, mult);
}
function swingMini(S, names) {
  const sw = S.swing, rows = [];
  for (const r of sw.new.slice(0, 6)) rows.push([`<span class="s"><b>${e(r.sym)}</b></span>`, act("Buy", "b"), px(r.stop), '<span class="mu">new</span>']);
  for (const r of sw.open.slice(0, 8)) rows.push([`<span class="s"><b>${e(r.sym)}</b></span>`, r.sell ? act("Sell", "s") : act("Hold"), px(r.stop), sg(r.pnl, 1)]);
  if (!rows.length) return empty("No swing signal and no open swing trade. Signals come in clusters, on sharp market sell-offs.");
  return t([["Symbol", "l"], ["", "l"], ["Stop", ""], ["P&L", ""]], rows, false);
}
function indexCard(x) {
  const [word, col] = { buy: ["Buy at the next open", "up"], sell: ["Sell at the next open", "dn"], hold: ["In a dip trade: hold", "am"], none: ["No signal", "fl"] }[x.action], p = x.pos || null, fo = x.fno || {};
  const sub = p && Object.keys(p).length ? `since ${day(p.since, false)} · ${sg(p.pnl, 2)} · ${p.left ?? 0} sessions left` : x.above200 ? `buys on a close at or below <b>${px(x.trigger)}</b>` : "below its 200-day average: the setup is off";
  const cells = [k("Last close", px(x.last), `5 sessions ${pct(x.r5)}`), k("200-day average", px(x.ma200), x.above200 ? "above" : "below"), k("Put-call ratio", num(fo.pcr, 2), `max pain ${px(fo.max_pain)}`),
    k("Put wall · call wall", `${px(fo.put_wall)} · ${px(fo.call_wall)}`, `expiry ${day(fo.exp, false)}`)];
  return `<div style="padding:9px 10px 8px"><div class="fd-big ${col}">${word}</div><div class="fd-n" style="margin-top:2px">${sub}</div></div>` + ks(cells, 2, true);
}
function fiiCard(S) {
  const P = S.participants; if (!P || !(P.rows || []).length) return empty("Participant positions are not stored yet.");
  return t([["Index futures", "l"], ["Long share", ""], ["Net, 5 sessions", ""]], P.rows.map((r) => [`<b style="color:var(--tx)">${e(r.who)}</b>`, pct(r.long_share, 0, false) + bar(r.long_share), `<span class="${cls(r.d5)}">${qty(r.d5)}</span>`]), false);
}
function footCard(S) {
  const F = S.footprints || {}, B = (S.buildups || {}).lists || {}, rows = [];
  for (const [lab, lst, colr] of [["Long build-up", B["Long build-up"], GREEN], ["Short build-up", B["Short build-up"], RED], ["Delivery accumulation", F.accumulation, GREEN], ["Delivery distribution", F.distribution, RED]])
    rows.push([`<i class="dot" style="background:${colr}"></i><span style="color:var(--tx)">${e(lab)}</span>`, (lst || []).slice(0, 4).map((r) => e(r.sym)).join(", ") || '<span class="mu">none</span>']);
  return t([["Footprint", "l"], ["Stocks", "l w"]], rows, false);
}
export function desk(S, C, sz, names) {
  const rows = orderRows(S, sz);
  let blot = rows.length ? `<div class="wide">${ordersTable(rows, names)}</div><div class="narrow">${orderCards(rows)}</div>` : empty(`<b>No orders for the next open.</b> Leaders: ${nextCheck(S)}. Swing and index signals appear on the day they trigger.`);
  const watch = watchRows(S, names);
  if (watch.length) blot += '<div style="border-top:1px solid var(--line)"><div style="padding:6px 10px 2px;font:500 10.5px var(--sans);color:var(--tx3)">On watch for the next sessions</div>' +
    t([["Book", "l"], ["", "l"], ["What", "l x"], ["Level", ""], ["Away", ""], ["", "l xx"]], watch.slice(0, 8), false) + "</div>";
  const nSw = rows.filter((r) => r.book === "Swing" && r.k === "b").length;
  if (nSw > 4) blot += `<div style="padding:7px 10px;border-top:1px solid var(--line)"><div class="fd-n"><b>${nSw} swing buys on one day are one bet.</b> They come from the same sell-off and move together. They are listed strongest first; the tested portfolio never held more than ten at a tenth of capital each.</div></div>`;
  const top = grid(pan("Orders for the next open", rows.length ? `${rows.length} to place · signals from ${day(S.asof, false)} close` : `signals from ${day(S.asof, false)} close`, blot, "c8 t12", true), pan("Books", "record on real prices, after costs", books(S), "c4 t12", true));
  const ld = S.leaders;
  const mid = grid(pan("Leaders: holdings", `top ${Math.min(10, ld.holdings.length)} of ${ld.holdings.length} by profit`, leadMini(S, names), "c5 t6", true),
    pan("Leaders against NIFTY 500", `growth of 1 since ${ld.record.start.slice(0, 4)}`, leadCurve(S), "c4 t6"), pan("Swing: signals and open trades", pill(S.swing.status), swingMini(S, names), "c3 t12", true));
  const idx = (S.index || []).map((x) => pan(x.name + " · dip in an uptrend", pill(x.status), indexCard(x), "c3 t6", true)).join(""), P = S.participants || {};
  const bot = grid(idx, pan("Who holds index futures", `${day(P.date, false)}`, fiiCard(S), "c3 t6", true), pan("Footprints today", "context, not signals", footCard(S), "c3 t6", true));
  return top + mid + bot;
}

// ── leaders ──
function leadKpis(S) {
  const ld = S.leaders, R = ld.record, w = Object.fromEntries(R.windows.map((x) => [x.key, x])), a = w.all, l3 = w.last3, tr = R.trades, rng = a.range || [null, null];
  return ks([k("Status", pill(ld.status), ld.status === "on" ? "beat both benchmarks" : "one period is weak"),
    k(`A year, since ${R.start.slice(0, 4)}`, `<span class="${cls(a.book.cagr)}">${pct(a.book.cagr)}</span>`, `NIFTY 500 ${pct(a.index.cagr)}`), k("Other check days", `${pct(rng[0], 0)} to ${pct(rng[1], 0)}`, "same rule, other check day"),
    k("Last 3 years", `<span class="${cls(l3.book.cagr)}">${pct(l3.book.cagr)}</span>`, `NIFTY 500 ${pct(l3.index.cagr)}`), k("Worst fall", `<span class="dn">${pct(a.book.dd, 0)}</span>`, `NIFTY 500 ${pct(a.index.dd, 0)}`),
    k("From its peak now", sg(R.dd_now, 1), "model book"), k("Trades won", pct(tr.win, 0, false), `avg ${pct(tr.avg_win, 0)} · lost ${pct(tr.avg_loss, 0)}`), k("Trades a year", num(tr.per_year, 0), `${tr.big ?? 0} gained 50% or more`)], 8);
}
function leadHoldings(S, names, sz) {
  const ld = S.leaders;
  const rows = ld.holdings.map((h) => { const fo = h.fno || null;
    return [`<span class="s">${sym(h.sym, names)}</span>`, flagOf(h), dshort(h.since), px(h.entry), px(h.price), sg(h.d1, 1), sg(h.pnl, 0), pct(h.w, 0, false), num((h.rank || 0) * 100, 0) + bar(h.rank), String(h.bars != null ? h.bars : "—"),
      slotQty(h.price, sz, ld.slots ?? 20), fo && Object.keys(fo).length ? `lot ${num(fo.lot)}` : '<span class="mu">cash only</span>']; });
  return t([["Symbol", "l"], ["", "l"], ["Since", "x"], ["Entry", "x"], ["Close", ""], ["Day", "x"], ["P&L", ""], ["Weight", "xx"], ["Strength", "x"], ["Sessions", "xx"], ["Qty for your slot", "xx"], ["F&O", "l xx"]], rows, false);
}
function leadChanges(S, names, sz) {
  const ld = S.leaders; let out = "";
  if ((ld.buy || []).length || (ld.sell || []).length) {
    let cards = "";
    for (const r of ld.sell) cards += `<div>${act("Sell", "s")}<b>${e(r.sym)}</b><span class="v">${px(r.price)}</span><small>${e(r.why || "")} · ${pct(r.pnl, 0)} since ${dshort(r.since)}</small></div>`;
    for (const r of ld.buy) cards += `<div>${act("Buy", "b")}<b>${e(r.sym)}</b><span class="v">${px(r.price)}</span><small>strength ${num((r.rank || 0) * 100, 0)} of 100 · ${slotQty(r.price, sz, ld.slots ?? 20)} shares for your slot</small></div>`;
    out += `<div class="fd-oc">${cards}</div>`;
  } else out += empty("<b>No change this session.</b> " + (ld.check_today ? "It was checked today." : "Next check: after the first session of next week; orders go in at the following open."));
  for (const nt of (ld.notes || []).slice(-3)) out += `<div style="padding:8px 10px;border-top:1px solid var(--line)"><div class="fd-n"><b class="am">${e(nt.sym ?? "")}, ${day(nt.date)}:</b> ${e(nt.text ?? "")} Result in the model book ${pct(nt.ret, 0)}.</div></div>`;
  const lc = ld.last_change;
  if (lc && Object.keys(lc).length) {
    const b = (lc.bought || []).map((x) => `${e(x.sym)} at ${px(x.px)}`).join(", ") || "nothing", s_ = (lc.sold || []).map((x) => `${e(x.sym)} at ${px(x.px)} (${pct(x.ret, 0)})`).join(", ") || "nothing";
    out += `<div style="padding:8px 10px;border-top:1px solid var(--line)"><div class="fd-n"><b>Last change, ${day(lc.date)} open.</b> Bought ${b}. Sold ${s_}.</div></div>`;
  }
  return out;
}
function yearsTable(R) {
  const rows = R.years.slice().reverse().map((y) => { const diff = ok(y.book) && ok(y.index) ? y.book - y.index : null;
    return [`<b style="color:var(--tx)">${y.y}</b>` + (y.part ? ' <span class="mu">part</span>' : ""), sg(y.book, 0), `${pct(y.lo, 0)} to ${pct(y.hi, 0)}`, sg(y.index, 0), sg(y.avg, 0), sg(diff, 0)]; });
  return t([["Year", "l"], ["Book", ""], ["Other check days", "x"], ["NIFTY 500", ""], ["Avg stock", "x"], ["Against index", ""]], rows);
}
function tradeList(trs, names, why = false) {
  const rows = trs.map((tr) => [`<span class="s">${sym(tr.sym, names)}</span>`, dshort(tr.in), dshort(tr.out), sg(tr.ret, Math.abs(tr.ret) >= 0.1 ? 0 : 1), String(tr.bars != null ? tr.bars : "—")].concat(why ? [e(tr.why || "")] : []));
  return t([["Symbol", "l"], ["In", ""], ["Out", "x"], ["Result", ""], ["Sessions", "x"]].concat(why ? [["Why it left", "l xx"]] : []), rows);
}
const family = (g) => (g in FAMILY ? FAMILY[g] : BROAD.find((fam) => g.startsWith(fam)) || "Other");
function sectorMix(S, sectors) {                             // share of the book by sector family (sector_map.classify → family)
  if (!sectors) return "";
  const counts = {}, order = [];
  for (const h of S.leaders.holdings) { const g = family(sectors[h.sym] || "Unclassified") || "Other"; if (!(g in counts)) { counts[g] = 0; order.push(g); } counts[g] += h.w || 0; }
  if (!order.length) return "";
  const top = order.map((g, i) => [g, counts[g], i]).sort((a, b) => b[1] - a[1] || a[2] - b[2]).slice(0, 8), m = Math.max(...top.map((x) => x[1])) || 1;
  return '<div class="fd-mix">' + top.map(([kk, v]) => `<div><span>${e(kk)}</span><u><i style="width:${f((v / m) * 100, 0)}%"></i></u><span>${f(v * 100, 0)}%</span></div>`).join("") + "</div>";
}
function rule(key, S) {
  const r = key === "leaders" ? S.leaders : key === "swing" ? S.swing : (S.index || [])[0] || {};
  return `<div class="fd-rule"><span>Enter</span><div>${e(r.rule ?? "")}</div><span>Exit</span><div>${e(r.exit ?? "")}</div><span>Size</span><div>${e(r.size ?? "")}</div><span>Fill</span><div>Signals are known after the close. Orders go in at the next session's open.</div></div>`;
}
export function leaders(S, C, sz, names, sectors) {
  const ld = S.leaders, R = ld.record, nSell = ld.holdings.filter((h) => h.flag === "sell").length, nRisk = ld.holdings.filter((h) => h.flag === "risk").length;
  const meta = `${ld.holdings.length} of ${ld.slots ?? 20} slots` + (nSell ? ` · ${nSell} to sell` : "") + (nRisk ? ` · ${nRisk} on watch` : ""), dd = R.dd_curve || {}, sm = sectorMix(S, sectors);
  const cand = (ld.candidates || []).map((r) => [`<span class="s">${sym(r.sym, names)}</span>`, px(r.price), sg(r.d1, 1), num((r.rank || 0) * 100, 0) + bar(r.rank)]);
  const nxt = pan("Next in line", "bought when a slot opens", cand.length ? t([["Symbol", "l"], ["Close", ""], ["Day", ""], ["Strength", ""]], cand, false) : empty("No candidates."), "", true);
  const left = pan("This week's changes", nextCheck(S), leadChanges(S, names, sz), "", true) + `<div class="wide">${nxt}</div>`;
  return leadKpis(S) + grid(`<div class="fd-col c4 t12">${left}</div>`, pan("Holdings", meta, leadHoldings(S, names, sz), "c8 t12", true), `<div class="narrow">${nxt}</div>`) +
    grid(pan("Growth of 1", `since ${day(R.start)} · after costs`, leadCurve(S, 190), "c7 t12"),
      pan("Fall from the peak", "how deep it has gone", line(dd.date || [], [["Leaders book", RED, dd.dd || [], pct(R.dd_now, 0)]], 190, (v) => `${f(v * 100, 0)}%`, true), "c5 t12")) +
    grid(pan("Year by year", "book · range on other check days · benchmarks", yearsTable(R), "c5 t12", true), pan("Biggest winners", `${R.trades.big ?? 0} trades gained 50% or more`, tradeList(R.winners, names), "c4 t6", true),
      pan("Sector mix", "share of the book", sm || empty("Sector map not available."), "c3 t6")) +
    grid(pan("Latest exits", "closed trades", tradeList(R.recent || [], names, true), "c7 t12", true),
      pan("The rule", "fixed, no judgement", rule("leaders", S) + '<div class="fd-n" style="margin-top:8px"><b>Starting today?</b> Buy the holdings in equal amounts, skip any marked Watch, then follow the weekly changes. <b>Why no stop:</b> a 4 ATR trail, a 20 to 25% stop and a 200-day exit were all tested; each lowered the return without reducing the worst fall.</div>', "c5 t12"));
}

// ── swing ──
function periodTable(rec, extra = null) {
  const rows = ["all", "early", "late", "last3"].map((kk) => { const r = rec[kk] || {}, ex = extra ? r[extra] || {} : {};
    return [`<b style="color:var(--tx)">${e(r.label ?? kk)}</b>`, String(r.n ?? 0), pct(r.win, 0, false), sg(r.exp, 2), pct(r.avg_win, 1), pct(r.avg_loss, 1), num(r.pf, 2), num(r.t, 1)].concat(extra ? [sg(ex.edge, 2), num(ex.t, 1)] : []); });
  let head = [["Period", "l"], ["Trades", ""], ["Won", ""], ["Per trade", ""], ["Avg win", "x"], ["Avg loss", "x"], ["Won ÷ lost", "x"], ["t", "xx"]];
  if (extra) head = head.concat([[extra === "pick" ? "Stock pick" : "Over holding", ""], ["t", "xx"]]);
  return t(head, rows, false);
}
export function swing(S, C, sz, names) {
  const sw = S.swing, a = sw.record.all, pick = a.pick || {}, l3 = sw.record.last3 || {}, cs = sw.curve_stats || {};
  const cells = [k("Status", pill(sw.status), "positive before and after 2022"), k("Trades tested", String(a.n ?? 0), `about ${num((a.n ?? 0) / 11.4, 0)} a year, in clusters`), k("Won", pct(a.win, 0, false), `last 3 years ${pct(l3.win, 0, false)}`),
    k("Per trade, after costs", sg(a.exp, 2), `last 3 years ${pct(l3.exp, 2)}`), k("Avg win · avg loss", `${pct(a.avg_win, 1)} · ${pct(a.avg_loss, 1)}`, `held ${num(a.bars, 1)} sessions`),
    k("Worst losing run", `${a.losing_run ?? 0} in a row`, `worst trade ${pct(a.worst, 0)}`), k("From stock picking", sg(pick.edge, 2), "the rest is the market's bounce"), k("10% a trade, 10 at once", pct(cs.cagr) + " a year", `worst fall ${pct(cs.dd, 0)}`)];
  const nw = sw.new.map((r) => { const q = riskQty(r.price, r.stop, sz), fo = r.fno || {}; let lots = "";
    if (Object.keys(fo).length && ok(fo.lot) && fo.lot > 0) lots = `lot ${num(fo.lot)} · risk ${rs(fo.lot * (r.price - r.stop))}`;
    return [`<span class="s">${sym(r.sym, names)}</span>`, px(r.price), sg(r.r5, 1), px(r.stop), pct(r.risk, 1, false), q != null ? intc(q) : "—", q ? rs(q * r.price) : "—", num((r.rank || 0) * 100, 0), lots || '<span class="mu">cash only</span>']; });
  const opn = sw.open.map((r) => [`<span class="s">${sym(r.sym, names)}</span>`, r.sell ? act("Sell", "s") : act("Hold"), dshort(r.since), px(r.entry), px(r.price), sg(r.pnl, 1), px(r.stop), e(r.sell || `${r.left ?? 0} sessions left`)]);
  const done = (sw.done || []).map((d) => `<div class="fd-n"><b>${e(d.sym)}</b> closed today at ${px(d.p1)}: ${pct(d.ret, 1)} · ${e(d.why)}</div>`).join("");
  const yrs = sw.record.years || [], ybars = vbars(yrs.map((y) => String(y.y).slice(2)), yrs.map((y) => y.exp), (v) => fx(v * 100, 1, false, true), (i, v) => (v >= 0 ? GREEN : RED)), cv = sw.curve || {}, cb = cv.book || [];
  return ks(cells, 8) +
    grid(pan("New signals: buy at the next open", `${nw.length} · stop 3 ATR below entry`, nw.length ? t([["Symbol", "l"], ["Close", ""], ["5 sessions", ""], ["Stop", ""], ["Stop gap", "x"], ["Quantity", ""], ["Value", "x"], ["Strength", "xx"], ["Futures", "l xx"]], nw)
        : empty("<b>No new signal.</b> A strong stock has to fall 3 ATR in 5 sessions; that mostly happens on market-wide sell-offs, so signals arrive in clusters."), "c7 t12", true),
      pan("Open trades", `${opn.length} open`, (opn.length ? t([["Symbol", "l"], ["", "l"], ["Since", "x"], ["Entry", "x"], ["Close", ""], ["P&L", ""], ["Stop", ""], ["Plan", "l x"]], opn) : empty("No open swing trade.")) +
        (done ? `<div style="padding:8px 10px;border-top:1px solid var(--line)">${done}</div>` : ""), "c5 t12", true)) +
    grid(pan("Record by period", "every signal, one trade per stock", periodTable(sw.record, "pick"), "c6 t12", true), pan("Average trade by year", "% after costs", ybars, "c3 t6"), pan("The rule", "fixed, no judgement", rule("swing", S), "c3 t6")) +
    grid(pan("Latest trades", "most recent first", tradeList(sw.recent || [], names, true), "c6 t12", true),
      pan("If you took them with a money limit", "a tenth of capital a trade, at most ten at once", line(cv.date || [], [["Swing book", AMBER, cb, pct(cb.length ? cb[cb.length - 1] : 0, 0)]], 170, (v) => `${f(v * 100, 0)}%`, true), "c6 t12"));
}

// ── index ──
export function chainOf(rows, s) {                           // fno_page._chain: the nearest expiry of one index, strikes in order. rows = [{sym, exp, strike, ce_oi, pe_oi}]
  const mine = (rows || []).filter((r) => r.sym === s); if (!mine.length) return null;
  const exp = mine.map((r) => r.exp).sort()[0], g = (v) => (v === "" || v == null ? 0 : Number(v));
  const r2 = mine.filter((r) => r.exp === exp).map((r, i) => [r, i]).sort((a, b) => g(a[0].strike) - g(b[0].strike) || a[1] - b[1]).map((x) => x[0]);
  return { exp, k: r2.map((r) => g(r.strike)), ce: r2.map((r) => g(r.ce_oi)), pe: r2.map((r) => g(r.pe_oi)) };
}
function indexCol(x, I, sz, chain) {
  const [word, col] = { buy: ["Buy at the next open", "up"], sell: ["Sell at the next open", "dn"], hold: ["In a dip trade: hold", "am"], none: ["No signal today", "fl"] }[x.action];
  const p = x.pos && Object.keys(x.pos).length ? x.pos : null, fo = x.fno || {}, a = x.record.all, oh = a.over_hold || {};
  const sub = p ? `Bought ${day(p.since, false)} at ${px(p.entry)} · now ${sg(p.pnl, 2)} · ${e(x.why || String(p.left ?? 0) + " sessions left")}`
    : x.above200 ? `Uptrend intact. A close at or below <b style="color:var(--tx)">${px(x.trigger)}</b> next session would trigger a buy.` : "Below its 200-day average: the setup only works in an uptrend, so it is switched off for now.";
  const head = `<div style="padding:10px"><div style="display:flex;justify-content:space-between;gap:10px;align-items:baseline;flex-wrap:wrap"><div class="fd-big ${col}">${word}</div>${pill(x.status)}</div><div class="fd-n" style="margin-top:3px">${sub}</div></div>` +
    ks([k("Last close", px(x.last), `to ${day(x.asof, false)}`), k("5 sessions", sg(x.r5, 2), "needs −2.5% or worse"), k("200-day average", px(x.ma200), x.above200 ? "above" : "below"), k("Trades tested", String(a.n ?? 0), `${pct(a.win, 0, false)} won`),
      k("Per trade", sg(a.exp, 2), `held ${num(a.bars, 1)} sessions`), k("More than just holding", sg(oh.edge, 2), `t ${num(oh.t, 1)}`), k("Worst trade", pct(a.worst, 1), `best ${pct(a.best, 1)}`), k("Worst losing run", `${a.losing_run ?? 0} in a row`, "no stop in the test")], 4, true);
  let out = pan(x.name + " · dip in an uptrend", `since 2012 · ${a.n ?? 0} trades`, head, "", true);
  const ch = I[x.sym] || null;
  if (ch && Object.keys(ch).length) {
    const marks = x.above200 && !p ? [[x.trigger, "Buy trigger", GREEN]] : p ? [[p.entry, "Entry", "var(--tx2)"]] : [];
    out += pan("One year", "close · 5-day and 200-day averages", line(ch.d, [["Close", "var(--tx)", ch.c, px(ch.c[ch.c.length - 1])], ["5-day", BLUE, ch.m5, ""], ["200-day", AMBER, ch.m200, ""]], 190, px, false, marks));
  }
  out += pan("Record by period", "after 0.05% a side", periodTable(x.record, "over_hold"), "", true);
  const lot = fo.lot, plan = x.plan || {};
  const cells = [k("Future", px(fo.fut), `lot ${num(lot)} · ${rs((lot || 0) * (fo.fut || 0))} a lot`), k("Expiry", day(fo.exp, false), `${num(fo.dte)} days`), k("Put-call ratio", num(fo.pcr, 2), "open interest, all strikes"),
    k("Implied volatility", pct(fo.iv, 1, false), `straddle ${pct(fo.straddle, 1, false)} of price`), k("Max pain", px(fo.max_pain), "this expiry"), k("Put wall", px(fo.put_wall), "most put open interest"), k("Call wall", px(fo.call_wall), "most call open interest"),
    k("A 1% move, one lot", rs((lot || 0) * (fo.fut || 0) * 0.01), "futures profit or loss")];
  let note = "";
  if (Object.keys(plan).length) note = plan.priced
    ? `<b>Call spread for a dip trade:</b> buy the ${px(plan.buy)} call, sell the ${px(plan.sell)} call, ${day(plan.exp, false)} expiry. Cost about ${px(plan.cost)} points (${rs(plan.cost * (lot || 0))} a lot), which is the most it can lose; best case ${px(plan.max_gain)} points; breaks even at ${px(plan.breakeven)}.`
    : `<b>Call spread for a dip trade:</b> buy the ${px(plan.buy)} call, sell the ${px(plan.sell)} call, in an expiry at least 7 days away. The stored chain expires in ${plan.days ?? 0} days, so price it at your broker; the cost is the most it can lose.`;
  out += pan("Futures and options levels", `F&O data to ${day(fo.date, false)}`, ks(cells, 4, true) + (note ? `<div style="padding:8px 10px;border-top:1px solid var(--line)"><div class="fd-n">${note}</div></div>` : ""), "", true);
  const c = chainOf(chain, x.sym);
  if (c && ok(fo.spot)) { const keep = c.k.map((kk, i) => i).filter((i) => Math.abs(c.k[i] / fo.spot - 1) <= 0.06);
    if (keep.length >= 8) out += pan("Open interest by strike", `${day(c.exp, false)} expiry`, strikes(keep.map((i) => c.k[i]), [["Calls", RED, keep.map((i) => c.ce[i])], ["Puts", GREEN, keep.map((i) => c.pe[i])]], 150, fo.spot)); }
  const rec = (x.recent || []).map((tr) => [dshort(tr.in), dshort(tr.out), px(tr.p0), px(tr.p1), sg(tr.ret, 2), String(tr.bars), e(tr.why)]);
  out += pan("Latest trades", "most recent first", t([["In", "l"], ["Out", "l"], ["Entry", "x"], ["Exit", "x"], ["Result", ""], ["Sessions", "x"], ["Why it left", "l xx"]], rec, false), "", true);
  return `<div class="fd-col c6 t12">${out}</div>`;
}
export function index(S, C, sz, chain) {
  const xs = S.index || [];
  if (!xs.length) return empty("Index history was not available on the last run.");
  return grid(...xs.map((x) => indexCol(x, C.i || {}, sz, chain))) + '<div class="fd-n">The index trade has no stop in its test: its worst trade is shown above. A call spread caps the loss at its cost; with futures, size for a fall of that worst trade. ' +
    'A "new 20-day high" rule was also tested on both indexes and made no more than simply holding, so it is not used.</div>';
}

// ── big players ──
const miniList = (rows, names, cols) => (!rows.length ? empty("Nothing unusual today.") : t([["Symbol", "l"]].concat(cols.map(([h, , c]) => [h, c])), rows.map((r) => [`<span class="s">${sym(r.sym, names)}</span>`].concat(cols.map(([, fn]) => fn(r)))), false));
export function players(S, names) {
  const P = S.participants || {}, pr = P.rows || [];
  const rows = pr.map((r) => [`<b style="color:var(--tx)">${e(r.who)}</b>`, pct(r.long_share, 0, false) + bar(r.long_share), `${pct(r.share_pct, 0, false)}`, qty(r.net), `<span class="${cls(r.d1)}">${qty(r.d1)}</span>`, `<span class="${cls(r.d5)}">${qty(r.d5)}</span>`, qty(r.stk),
    `<span class="${cls(r.stk_d5)}">${qty(r.stk_d5)}</span>`, qty(r.opt), `<span class="${cls(r.opt_d1)}">${qty(r.opt_d1)}</span>`]);
  let part = rows.length ? t([["Who", "l"], ["Index futures long", ""], ["Higher than, 1 yr", "x"], ["Net contracts", ""], ["1 session", "x"], ["5 sessions", ""], ["Stock futures net", "xx"], ["5 sessions", "xx"], ["Index options net", "xx"], ["1 session", "xx"]], rows, false)
    : empty("Participant positions are not stored yet. They come with the F&O data workflow.");
  const fii = pr.find((r) => r.who === "FIIs");
  if (fii) part += `<div style="padding:8px 10px;border-top:1px solid var(--line)"><div class="fd-n"><b>FIIs hold ${pct(fii.long_share, 0, false)} of their index futures long</b>, more than on ${pct(fii.share_pct, 0, false)} of days in the past year. Net contracts are longs less shorts; a positive options number means more calls than puts held on balance.</div>` +
    '<div class="fd-n">Tested on five years of this data: no participant reading gave a reliable buy or sell signal on its own, so these are context.</div></div>';
  const fh = P.fii || {}, sh = fh.share || [];
  const fchart = Object.keys(fh).length ? line(fh.date || [], [["FII long share of index futures", AMBER, sh, pct(sh.length ? sh[sh.length - 1] : 0, 0, false)]], 150, (v) => `${f(v * 100, 0)}%`) : empty("No history yet.");
  const B = (S.buildups || {}).lists || {}, bcols = [["Price", (r) => sg(r.p5, 1), ""], ["OI", (r) => sg(r.oi5, 0), ""]];
  const bu = ["Long build-up", "Short covering", "Short build-up", "Long unwinding"].map((kk) => pan(kk, "futures price and open interest · 5 sessions", miniList(B[kk] || [], names, bcols), "c3 t6", true)).join("");
  const F = S.footprints || {}, fcols = (lab) => [[lab, (r) => `${num(r.x, 1)}×`, ""], ["Day", (r) => sg(r.d1, 1), ""], ["10 sessions", (r) => sg(r.d10, 0), "x"], ["Delivered", (r) => pct(r.dshare, 0, false), "x"]];
  const fp = pan("Delivery accumulation", "10 sessions of delivery against normal · price up", miniList(F.accumulation || [], names, fcols("Delivery")), "c4 t6", true) +
    pan("Delivery distribution", "heavy delivery · price down", miniList(F.distribution || [], names, fcols("Delivery")), "c4 t6", true) + pan("Delivery spike today", "against the last 20 sessions", miniList(F.delivery_spike || [], names, fcols("Delivery")), "c4 t12", true) +
    pan("Big tickets", "average trade size against normal", miniList(F.big_tickets || [], names, fcols("Trade size")), "c6 t6", true) + pan("Volume surge", "against the last 50 sessions", miniList(F.volume || [], names, fcols("Volume")), "c6 t6", true);
  const deals = S.deals || [];
  const drows = deals.map((d, i) => [d, i]).sort((a, b) => (b[0].value_cr || 0) - (a[0].value_cr || 0) || a[1] - b[1]).slice(0, 40).map(([d]) => [e(d.kind ?? ""), `<span class="s">${sym(d.sym ?? "", names)}</span>`,
    String(d.side ?? "").toUpperCase().startsWith("B") ? act("Buy", "b") : act("Sell", "s"), e(d.who ?? ""), qty(d.qty), px(d.price), ok(d.value_cr) ? `₹${fx(d.value_cr, 1, true)} Cr` : "—"]);
  const dl = drows.length ? t([["Type", "l"], ["Symbol", "l"], ["Side", "l"], ["Who", "l w"], ["Shares", "x"], ["Price", "x"], ["Value", ""]], drows)
    : empty("No bulk or block deal was read on the last run. NSE publishes only the latest day, so they are collected from now on (arka_data/signals/deals.csv).");
  return grid(pan("Who holds what in futures and options", `NSE participant data · ${day(P.date)}`, part, "c8 t12", true), pan("FII positioning, 120 sessions", "share of their index futures that is long", fchart, "c4 t12")) + grid(bu) + grid(fp) +
    grid(pan("Bulk and block deals", "largest first", dl, "c12 t12", true)) + '<div class="fd-n"><b>How to read this tab.</b> These are footprints of large money, shown as context. Each was also tested as a buy signal on eleven years of prices: ' +
    "delivery accumulation, delivery spikes and heavy-volume days did not beat simply owning the average liquid stock, so none of them places an order on the desk. See Evidence.</div>";
}

// ── evidence ──
export function evidence(S, names, log = []) {
  const ld = S.leaders, sw = S.swing, w = Object.fromEntries(ld.record.windows.map((x) => [x.key, x])), a = w.all;
  const used = (st) => (st === "on" ? act("In use", "b") : st === "trial" ? act("On trial", "w") : act("Off")), sn = (v) => String(v ?? "—");
  const rows = [['<span class="s w"><b>Hold the 20 strongest stocks</b><small>12-month strength · weekly check</small></span>', used(ld.status), sn(ld.record.trades.n), pct(ld.record.trades.win, 0, false), `${pct(a.book.cagr)} a year`, `${pct((a.book.cagr || 0) - (a.avg.cagr || 0))} a year`, "—"]];
  const s = sw.record.all, pk = sw.record.all.pick || {};
  rows.push(['<span class="s w"><b>Leader pullback</b><small>strong stock, sharp 5-session fall</small></span>', used(sw.status), sn(s.n), pct(s.win, 0, false), `${pct(s.exp, 2)} a trade`, `${pct(pk.edge, 2)} a trade`, num(pk.t, 1)]);
  for (const x of S.index || []) { const r = x.record.all, oh = x.record.all.over_hold || {};
    rows.push([`<span class="s w"><b>${e(x.name)}: dip in an uptrend</b><small>fall of 2.5% above the 200-day</small></span>`, used(x.status), sn(r.n), pct(r.win, 0, false), `${pct(r.exp, 2)} a trade`, `${pct(oh.edge, 2)} over holding`, num(oh.t, 1)]); }
  for (const r of (S.evidence || {}).rejected || []) rows.push([`<span class="s w"><b>${e(r.name)}</b><small>${e(r.rule.split(".")[0])}</small></span>`, act("Rejected", "s"), sn(r.n), pct(r.win, 0, false), `${pct(r.exp, 2)} a trade`, `${pct(r.pick, 2)} a trade`, num(r.pick_t, 1)]);
  for (const x of S.index || []) { const h = x.high20 || {};
    rows.push([`<span class="s w"><b>${e(x.name)}: new 20-day high</b><small>hold 10 sessions</small></span>`, act("Rejected", "s"), sn(h.n), "—", `${pct(h.exp, 2)} a trade`, `${pct(h.edge, 2)} over holding`, num(h.t, 1)]); }
  const tested = t([["Idea", "l"], ["Verdict", "l"], ["Trades", "x"], ["Won", "x"], ["Result", ""], ["Edge", ""], ["t", "x"]], rows, false);
  const D = (S.evidence || {}).deciles || {}; let dec = "";
  if (D.all && D.all.length) dec = vbars(["weak", "", "", "", "", "", "", "", "", "strong"], D.all, (v) => f(v * 100, 1)) + `<div class="fd-n" style="margin-top:8px">Average move over the next 21 sessions for ten groups of stocks sorted by 12-month strength, ${D.all_n ?? 0} monthly snapshots. Last 3 years: weakest ${pct(D.last3[0], 1)}, strongest ${pct(D.last3[D.last3.length - 1], 1)}. This is the effect the Leaders book harvests, and why the weakest group is the stay-away list.</div>`;
  const bad = ld.record.years.filter((y) => !y.part && ok(y.book) && y.book < 0).map((y) => String(y.y));
  const badYears = bad.length > 1 ? bad.slice(0, -1).join(", ") + " and " + bad[bad.length - 1] : bad.length ? bad[0] : "no full year so far";
  const stat = ["Stops on the Leaders book (4 ATR trail, 20% and 25% from entry, exit below the 200-day): lower return, same worst fall.", "Selling everything when NIFTY 500 is below its 200-day: return fell from about 20% to 7 to 13% a year.",
    "Selling a holding as soon as it leaves the 350 most traded: same return, a quarter more trades, many of them one-week round trips. Holdings are now kept while among the 500 most traded.",
    'Breadth filters and a "recent breakouts are working" gauge on breakouts: no help in either half of the history.', "10 holdings instead of 20: much more luck, and worse in the first half of the history. 30 holdings: a little steadier.",
    "The earlier F&O factor model (19 inputs): no forecasting skill on five years of real data. It no longer places orders."];
  const limits = ["The price history leaves out companies that were later delisted, so old results look better than they were. The last three years are least affected.",
    "These rules were chosen in November 2026 after testing about thirty ideas on this same history. Expect less going forward than the record shows.",
    "Costs are 0.15% a side for stocks. Taxes, slippage on thin days and circuit limits are not in the numbers. Demergers and rights issues are not adjusted in the price files.",
    `The Leaders book has fallen ${pct(-(a.book.dd || 0), 0, false)} from a peak at its worst, and lost money in ${badYears}. Only use money you can leave alone through that.`,
    "NSE's own momentum indexes beat their parent indexes over 20 years, yet an index fund on one of them launched in late 2024 was still under water in mid-2026. Momentum has long bad stretches.",
    "End-of-day data only: no intraday signals, and fills are assumed at the next open."];
  const isDig = (v) => { const x = String(v).replace(".", ""); return x.length > 0 && /^\d+$/.test(x); }, fnum = (v) => (isDig(v) ? px(Number(v)) : "—"), capz = (v) => { const x = String(v ?? ""); return x.charAt(0).toUpperCase() + x.slice(1).toLowerCase(); };
  const lrows = log.slice(-40).reverse().map((r) => [dshort(r.date ?? ""), e(capz(r.book)), act(capz(r.action), r.action === "BUY" ? "b" : "s"), `<b style="color:var(--tx)">${e(r.sym ?? "")}</b>`, fnum(r.price ?? ""), fnum(r.stop ?? ""), e(r.note ?? "")]);
  const lg = lrows.length ? t([["Signal day", "l"], ["Book", "l"], ["", "l"], ["Symbol", "l"], ["Close", ""], ["Stop", "x"], ["Note", "l x"]], lrows)
    : empty(`<b>No order has been issued yet.</b> The live record starts on ${day(S.live_since)}; every order is written to arka_data/signals/log.csv on the day it is issued and is never edited.`);
  const health = ks([k("Prices to", day(S.asof), `built ${dshort(String(S.built ?? "").slice(0, 10))}`), k("History from", day(S.first), `${S.top ?? 350} most traded`), k("Live books since", day(S.live_since), "forward only"),
    k("Replay matches live", ld.drift ? "No" : "Yes", !ld.drift ? "history unchanged" : "history was revised")], 2, true);
  const li = (xs) => '<ul class="fd-list">' + xs.map((x) => `<li>${e(x)}</li>`).join("") + "</ul>";
  const avoid = (S.avoid || []).map((r) => [`<span class="s">${sym(r.sym, names)}</span>`, px(r.price), num((r.rank || 0) * 100, 0), sg(r.below200, 0), r.fno ? "F&O" : ""]);
  return grid(pan("What was tested, and what survived", "edge = beyond the average liquid stock, or beyond just holding the index", tested, "c8 t12", true), pan("Strength today, return next month", "% over the next 21 sessions", dec || empty("Not computed."), "c4 t12")) +
    grid(pan("Also tested, not used", "November 2026", li(stat), "c4 t6"), pan("Limits you should know", "read before using real money", li(limits), "c4 t6"),
      pan("Stay-away list", "weakest tenth by strength, below the 200-day", avoid.length ? t([["Symbol", "l"], ["Close", ""], ["Strength", ""], ["vs 200-day", ""], ["", "x"]], avoid) : empty("None."), "c4 t12", true)) +
    grid(pan("Live record: orders as they were issued", `${log.length} logged`, lg, "c8 t12", true), pan("Data health", "", health, "c4 t12", true));
}

// ── for other pages (fno_page.signal_for): [what the desk says about this stock, short detail] ──
export function signalFor(S, s) {
  if (!S || S.v !== 3) return ["", ""];
  s = String(s).toUpperCase(); const ld = S.leaders, sw = S.swing, str = (r) => `12-month strength ${num((r.rank || 0) * 100, 0)} of 100`;
  for (const r of ld.buy || []) if (r.sym === s) return ["Leaders book: buy at the next open", str(r)];
  for (const h of ld.holdings || []) if (h.sym === s) return [`Leaders book: ${h.flag === "sell" ? "sell at the next open" : h.flag === "risk" ? "held, on watch" : "held"}`, `since ${day(h.since)} · ${pct(h.pnl, 0)} · strength ${num((h.rank || 0) * 100, 0)} of 100`];
  for (const r of sw.new || []) if (r.sym === s) return ["Swing: buy at the next open", `stop about ${px(r.stop)}`];
  for (const r of sw.open || []) if (r.sym === s) return [r.sell ? "Swing: sell at the next open" : "Swing: open trade", `entry ${px(r.entry)} · stop ${px(r.stop)} · ${pct(r.pnl, 1)}`];
  for (const r of S.avoid || []) if (r.sym === s) return ["On the stay-away list", "weakest tenth by 12-month strength and below its 200-day average"];
  for (const r of ld.candidates || []) if (r.sym === s) return ["Leaders book: next in line", str(r)];
  return ["No signal on the desk", ""];
}

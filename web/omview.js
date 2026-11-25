// Arka Trades · the Options map, drawn: a line-by-line port of the views fno_page._options_map takes from fno_legacy.py
// (levels into expiry, option chain, dealer gamma and the smile, six months of history, the F&O stock map and table).
// Pure functions that return HTML; the numbers come from fno_engine.py through fno_export.py, so nothing is recomputed here.
import { fx } from "./rsengine.js";
import { e, ok, num, px, pct, cls, qty, day } from "./deskview.js";

export const GREEN = "var(--green,#22C55E)", RED = "var(--red,#EF4444)", AMBER = "var(--amber,#F59E0B)", BLUE = "var(--c,#3B82F6)";
export const FILTERS = ["All", "Top fifth", "Bottom fifth", "Long build-up", "Short build-up", "Short covering", "Long unwinding", "Options expensive", "Options cheap", "Open interest up", "Open interest down", "My watchlist"];
const f = (v, d) => fx(v, d, false);
const pyRound = (v) => { const r = Math.round(v); return Math.abs(v % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r; };
const uniqSorted = (a) => [...new Set(a)].sort((x, y) => x - y);
const col = (x, d = 1) => `<span class="${cls(x)}">${pct(x, d)}</span>`;
const rank = (p) => (!ok(p) ? "not enough history" : `higher than ${f(p * 100, 0)}% of the last year`);
export const card = (title, right, body, flush = false) => `<div class="fo-card"><div class="fo-ct"><b>${e(title)}</b><span>${right}</span></div><div class="${flush ? "" : "fo-cb"}">${body}</div></div>`;
const tile = (label, val, sub = "") => `<div class="fo-tile"><small>${e(label)}</small><b>${val}</b>${sub ? `<em>${sub}</em>` : ""}</div>`;
export const grid = (c, ...cards) => `<div class="fo-g ${c}">${cards.join("")}</div>`;
const legend = (items) => '<div class="fo-leg">' + items.map(([n, c, v]) => `<span><i style="background:${c}"></i>${e(n)}${v ? ` <b>${v}</b>` : ""}</span>`).join("") + "</div>";
function nice(lo, hi, n = 4) {
  const span = hi - lo || 1, raw = span / n, mag = 10 ** Math.floor(Math.log10(raw));
  let best = 1, bd = Infinity; for (const m of [1, 2, 2.5, 5, 10]) { const d = Math.abs(m * mag - raw); if (d < bd) { bd = d; best = m; } }
  const step = best * mag, out = []; let v = (Math.ceil(lo / step) + 0) * step;
  while (v <= hi + 1e-12) { out.push(v); v += step; }
  return out;
}
// Line chart. series = [[name, colour, values, last-value text or ""]].
function lines(dates, series, h = 170, fmt = px, zero = false, leg = true) {
  const vals = series.flatMap(([, , ys]) => ys.filter(ok));
  if (vals.length < 3) return '<div class="fo-empty">Not enough history to draw this yet.</div>';
  let lo = Math.min(...vals), hi = Math.max(...vals);
  if (zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
  const pad = (hi - lo) * 0.08 || Math.abs(hi) * 0.05 || 1; lo -= pad; hi += pad;
  const n = Math.max(...series.map(([, , ys]) => ys.length)), X = (i) => (i / Math.max(n - 1, 1)) * 1000, Y = (v) => ((hi - v) / (hi - lo)) * h, ticks = nice(lo, hi);
  const gl = ticks.map((tk) => `<line x1="0" x2="1000" y1="${f(Y(tk), 1)}" y2="${f(Y(tk), 1)}" stroke="${zero && Math.abs(tk) < 1e-12 ? "var(--tx3)" : "var(--line)"}" stroke-width="1" vector-effect="non-scaling-stroke"/>`).join("");
  let paths = "";
  for (const [, colr, ys] of series) { const pts = []; ys.forEach((v, i) => { if (ok(v)) pts.push(`${f(X(i), 1)},${f(Y(v), 1)}`); });
    paths += `<polyline points="${pts.join(" ")}" fill="none" stroke="${colr}" stroke-width="1.6" vector-effect="non-scaling-stroke" stroke-linejoin="round"/>`; }
  const yl = ticks.map((tk) => `<span class="yl" style="top:${f((Y(tk) / h) * 100, 1)}%">${fmt(tk)}</span>`).join("");
  let xl = "";
  if (dates && dates.length) { const idx = uniqSorted([0, 1, 2, 3, 4].map((q) => pyRound((q * (dates.length - 1)) / 4))); xl = '<div class="fo-xl">' + idx.map((i) => `<span>${day(dates[i], false)}</span>`).join("") + "</div>"; }
  const lg = leg ? legend(series.map(([nm, colr, , last]) => [nm, colr, last || ""])) : "";
  return `${lg}<div class="fo-ch" style="height:${h}px"><svg viewBox="0 0 1000 ${h}" preserveAspectRatio="none">${gl}${paths}</svg>${yl}</div>${xl}`;
}
// Bar chart across strikes. series = [[name, colour, values]]; mark = a price drawn as a vertical line.
function bars(labels, series, { h = 170, mark = null, diverge = false, fmt = qty, leg = true, markName = "Price", xlabels = null } = {}) {
  const n = labels.length, vals = series.flatMap(([, , ys]) => ys.filter(ok).map(Math.abs));
  if (n < 3 || !vals.length || Math.max(...vals) <= 0) return '<div class="fo-empty">Nothing stored for this expiry.</div>';
  const m = Math.max(...vals), slot = 10, kk = series.length, W = n * slot, zeroY = diverge ? h / 2 : h - 1, scale = diverge ? (h / 2 - 4) / m : (h - 8) / m, bw = 8 / kk;
  let rects = "";
  series.forEach(([, colr, ys], j) => ys.forEach((v, i) => { if (ok(v) && v !== 0) { const hh = Math.abs(v) * scale;
    rects += `<rect x="${f(i * slot + 1 + j * bw, 2)}" y="${f(v > 0 ? zeroY - hh : zeroY, 1)}" width="${f(bw * 0.92, 2)}" height="${f(hh, 1)}" fill="${colr}" opacity=".85"/>`; } }));
  let extra = `<line x1="0" x2="${f(W, 1)}" y1="${f(zeroY, 1)}" y2="${f(zeroY, 1)}" stroke="var(--line2)" stroke-width="1" vector-effect="non-scaling-stroke"/>`;
  if (ok(mark) && labels[0] < mark && mark < labels[n - 1]) { let pos = 0;
    for (let i = 0; i < n - 1; i++) if (labels[i] <= mark && mark <= labels[i + 1]) { pos = (i + (mark - labels[i]) / (labels[i + 1] - labels[i] || 1)) * slot + slot / 2; break; }
    extra += `<line x1="${f(pos, 1)}" x2="${f(pos, 1)}" y1="0" y2="${h}" stroke="var(--tx)" stroke-width="1.2" stroke-dasharray="4 3" vector-effect="non-scaling-stroke"/>`; }
  let yl = `<span class="yl" style="top:6%">${fmt(m)}</span><span class="yl" style="top:${f((zeroY / h) * 100, 0)}%">0</span>`;
  if (diverge) yl += `<span class="yl" style="top:94%">${fmt(-m)}</span>`;
  const idx = uniqSorted([0, 1, 2, 3, 4, 5].map((q) => pyRound((q * (n - 1)) / 5)));
  const xl = '<div class="fo-xl">' + (xlabels || idx.map((i) => px(labels[i]))).map((x) => `<span>${e(x)}</span>`).join("") + "</div>";
  const items = series.map(([nm, colr]) => [nm, colr, ""]).concat(ok(mark) ? [[`${markName} ${px(mark)}`, "var(--tx)", ""]] : []);
  return `${leg ? legend(items) : ""}<div class="fo-ch" style="height:${h}px"><svg viewBox="0 0 ${f(W, 0)} ${h}" preserveAspectRatio="none">${extra}${rects}</svg>${yl}</div>${xl}`;
}
export function levelBar(lv) {
  const fut = lv.fut, mv = lv.move10, marks = [[lv.put_wall, GREEN, "Put wall"], [lv.max_pain, AMBER, "Max pain"], [fut, "var(--tx)", "Price"], [lv.call_wall, RED, "Call wall"]];
  const good = marks.filter((m) => ok(m[0]) && m[0] > 0), pts = good.map((m) => m[0]);
  if (!ok(fut) || pts.length < 2) return "";
  let lo = Math.min(...pts.concat(ok(mv) ? [fut - mv] : [])), hi = Math.max(...pts.concat(ok(mv) ? [fut + mv] : []));
  const pad = (hi - lo) * 0.06 || fut * 0.01; lo -= pad; hi += pad;
  const pos = (v) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));
  const band = ok(mv) ? `<div class="bd" style="left:${f(pos(fut - mv), 1)}%;width:${f(pos(fut + mv) - pos(fut - mv), 1)}%"></div>` : "";
  const ticks = good.map(([v, c, n]) => `<i style="left:${f(pos(v), 1)}%;background:${c}" title="${e(n)} ${px(v)}"></i>`).join("");
  const items = good.map(([v, c, n]) => [`${n} ${px(v)}`, c, ""]);
  if (ok(mv)) items.push([`Priced 10-session range ±${px(mv)}`, "rgba(161,161,170,.35)", ""]);
  return `<div class="fo-bar"><div class="tr"></div>${band}${ticks}</div>${legend(items)}`;
}
export function histCharts(hist, withOi = true, four = true) {
  const d = hist.date || [], last = (kk, fn) => { const a = hist[kk] || []; let v = null; for (let i = a.length - 1; i >= 0; i--) if (ok(a[i])) { v = a[i]; break; } return fn(v); };
  const parts = [lines(d, [["Future price", "var(--tx)", hist.px || [], last("px", px)]], 130), lines(d, [["Put-call ratio", BLUE, hist.pcr || [], last("pcr", (v) => num(v, 2))]], 130, (v) => f(v, 2)),
    lines(d, [["Implied volatility", AMBER, hist.iv || [], last("iv", (v) => pct(v, 1, false))]], 130, (v) => `${f(v * 100, 0)}%`)];
  if (withOi) parts.push(lines(d, [["Futures open interest", "#A78BFA", hist.oi || [], last("oi", qty)]], 130, qty));
  return `<div class="fo-mini ${four ? "four" : ""}">` + parts.map((p) => `<div>${p}</div>`).join("") + "</div>";
}
export function chainCards(c, F) {
  if (!c) return grid("", card("Option chain", "", '<div class="fo-empty">Strike-by-strike data for this expiry is not stored yet. It arrives with the next run of the F&O data workflow.</div>', true));
  const K = c.strike; let keep = ok(F) ? K.map((_, i) => i).filter((i) => Math.abs(K[i] / F - 1) <= 0.07) : K.map((_, i) => i);
  if (keep.length < 8) keep = K.map((_, i) => i);
  const sub = (key) => keep.map((i) => c[key][i]), ksx = keep.map((i) => K[i]);
  const oi = bars(ksx, [["Call open interest", RED, sub("ce_oi")], ["Put open interest", GREEN, sub("pe_oi")]], { mark: F });
  const chg = bars(ksx, [["Calls added today", RED, sub("ce_chg")], ["Puts added today", GREEN, sub("pe_chg")]], { mark: F, diverge: true });
  const tiles = '<div class="fo-tiles two">' + tile("Max pain", px(c.max_pain), `expiry ${day(c.exp, false)} · ${c.dte} days`) + tile("Put wall · call wall", `${px(c.put_wall)} · ${px(c.call_wall)}`, "most open interest") +
    tile("Fresh writing today", `${px(c.put_add)} · ${px(c.call_add)}`, "puts · calls, biggest additions") + tile("Put-call ratio", num(c.pcr, 2), "this expiry, stored strikes") + "</div>";
  const right = [card("Open interest by strike", `${day(c.exp, false)} expiry`, oi + tiles), card("Positions added or closed today", "change in open interest", chg)];
  let third = "";
  if (c.gex && c.gex.length && c.gex.some((v) => Math.abs(v) > 0)) {
    const gex = bars(ksx, [["Estimated dealer gamma", BLUE, sub("gex")]], { mark: F, diverge: true }), sign = c.net_gex > 0 ? "positive" : "negative";
    const txt = c.net_gex > 0 ? "Positive gamma: dealers trade against moves, which tends to hold price in a range." : "Negative gamma: dealers trade with moves, which tends to stretch them.";
    const flip = ok(c.flip) ? ` It changes sign near <b style="color:var(--tx)">${px(c.flip)}</b>.` : "";
    const smile = lines([], [["Implied volatility by strike", AMBER, sub("iv"), ""]], 110, (v) => `${f(v * 100, 1)}%`);
    const xl = '<div class="fo-xl">' + uniqSorted([0, 1, 2, 3, 4, 5].map((q) => pyRound((q * (ksx.length - 1)) / 5))).map((i) => `<span>${px(ksx[i])}</span>`).join("") + "</div>";
    third = card("Dealer gamma and the volatility smile", `net gamma ${sign}`, gex + `<div class="fo-tx">${txt}${flip}</div><div style="height:12px"></div>` + smile + xl +
      '<div class="fo-note">Gamma is an estimate: it assumes dealers have sold the puts and bought the calls that customers hold. The true split is not published.</div>');
  }
  return grid(third ? "three" : "two", ...right, third);
}
export function levels(lv) {
  const fut = lv.fut, tl = [tile("Put-call ratio", num(lv.pcr, 2), rank(lv.pcr_pct)), tile("Implied volatility", pct(lv.iv, 1, false), rank(lv.iv_pct)), tile("Futures open interest", col(lv.oi5), "change over 5 sessions")];
  if (ok(lv.move5) && ok(fut)) tl.push(tile("Priced move, 5 sessions", `±${px(lv.move5)}`, `${px(fut - lv.move5)} to ${px(fut + lv.move5)}`));
  if (ok(lv.move_exp) && ok(fut)) tl.push(tile("Priced move to expiry", `±${px(lv.move_exp)}`, `${px(fut - lv.move_exp)} to ${px(fut + lv.move_exp)}`));
  if (ok(lv.roll)) tl.push(tile("Rolled to next month", pct(lv.roll, 0, false), "share of futures open interest"));
  if (ok(lv.cal)) tl.push(tile("Next-month premium", pct(lv.cal, 2), "next future against this one"));
  if (ok(lv.skew)) tl.push(tile("Put skew", `${fx(lv.skew * 100, 1, false, true)} pts`, "put volatility less call volatility"));
  if (lv.w_exp && ok(lv.w_max_pain)) tl.push(tile(`Weekly max pain · ${day(lv.w_exp, false)}`, px(lv.w_max_pain), `walls ${px(lv.w_put_wall)} · ${px(lv.w_call_wall)}`));
  return `<div class="fo-tiles">${tl.join("")}</div>`;
}
// ── stocks ──
const stable = (a, key) => a.map((x, i) => [x, i]).sort((p, q) => key(p[0]) - key(q[0]) || p[1] - q[1]).map((x) => x[0]);
export function pick(stocks, flt, mine = new Set()) {
  if (flt === "Top fifth") return stocks.filter((s) => s.level === 2);
  if (flt === "Bottom fifth") return stocks.filter((s) => s.level === -2).reverse();
  if (["Long build-up", "Short build-up", "Short covering", "Long unwinding"].includes(flt)) return stocks.filter((s) => s.buildup === flt);
  if (flt === "Options expensive") return stable(stocks.filter((s) => s.on.includes("iv_hi")), (s) => -(s.levels.iv_pct || 0));
  if (flt === "Options cheap") return stable(stocks.filter((s) => s.on.includes("iv_lo")), (s) => s.levels.iv_pct || 0);
  if (flt === "Open interest up") return stable(stocks.filter((s) => ok(s.levels.oi5)), (s) => -s.levels.oi5).slice(0, 40);
  if (flt === "Open interest down") return stable(stocks.filter((s) => ok(s.levels.oi5)), (s) => s.levels.oi5).slice(0, 40);
  if (flt === "My watchlist") return stocks.filter((s) => mine.has(s.sym));
  return stocks;
}
const fifth = (s) => `<span class="fo-pill ${s.level > 0 ? "g" : s.level < 0 ? "r" : "n"}">${e(s.verdict)}</span>`;
export function stockTable(rows) {
  if (!rows.length) return '<div class="fo-empty">No stock matches this filter today.</div>';
  const out = rows.map((s) => { const lv = s.levels, bu = s.buildup, bcl = bu === "Long build-up" || bu === "Short covering" ? "up" : bu ? "dn" : "mu", ivp = lv.iv_pct;
    return `<tr><td>${e(s.sym)}</td><td class="xw mu">${s.rank ?? ""}</td><td class="l">${fifth(s)}</td><td class="xw">${col(s.score, 2)}</td><td>${px(lv.fut)}</td><td>${col(lv.r1)}</td><td class="xw">${col(lv.r5)}</td><td class="xw">${col(lv.oi5)}</td>` +
      `<td class="l ${bcl}">${bu ? e(bu) : "—"}</td><td class="xw">${num(lv.pcr, 2)}</td><td class="xw">${pct(lv.iv, 0, false)}</td><td class="xw">${ok(ivp) ? f(ivp * 100, 0) : "—"}</td><td class="xw">${pct(lv.roll, 0, false)}</td><td class="xw">${px(lv.max_pain)}</td></tr>`; });
  const head = '<tr><th>Stock</th><th class="xw">Rank</th><th class="l">Model</th><th class="xw">Forecast against the rest</th><th>Future</th><th>Today</th><th class="xw">5 sessions</th><th class="xw">Open interest, 5 sessions</th><th class="l">Build-up</th><th class="xw">Put-call</th>' +
    '<th class="xw">Volatility</th><th class="xw">Vol rank</th><th class="xw">Rolled</th><th class="xw">Max pain</th></tr>';
  return `<div class="fo-tw"><table class="fo-t"><thead>${head}</thead><tbody>${out.join("")}</tbody></table></div>`;
}
export function quadrant(stocks) {
  const pts = stocks.map((s) => [s, s.levels.r5, s.levels.oi5]).filter(([, x, y]) => ok(x) && ok(y));
  if (pts.length < 10) return '<div class="fo-empty">Needs a week of stored data.</div>';
  const at = Math.trunc(pts.length * 0.97), mx = Math.max(0.04, pts.map((p) => Math.abs(p[1])).sort((a, b) => a - b)[at]), my = Math.max(0.05, pts.map((p) => Math.abs(p[2])).sort((a, b) => a - b)[at]);
  const pxx = (x) => 50 + Math.max(-1, Math.min(1, x / mx)) * 46, pyy = (y) => 50 - Math.max(-1, Math.min(1, y / my)) * 44;
  const far = new Set(stable(pts.map((p, i) => [p, i]), ([p]) => -((p[1] / mx) ** 2 + (p[2] / my) ** 2)).slice(0, 14).map(([, i]) => i));
  let dots = "";
  pts.forEach(([s, x, y], i) => { const colr = s.level > 0 ? "#22C55E" : s.level < 0 ? "#EF4444" : "#71717A";
    dots += `<i style="left:${f(pxx(x), 1)}%;top:${f(pyy(y), 1)}%;background:${colr}" title="${e(s.sym)} · price ${pct(x)} · open interest ${pct(y)} · ${e(s.verdict)}"></i>`;
    if (far.has(i)) dots += `<em style="left:${f(pxx(x), 1)}%;top:${f(pyy(y), 1)}%">${e(s.sym)}</em>`; });
  const q = '<span class="q" style="right:0;top:0;color:#22C55E">Long build-up</span><span class="q" style="left:0;top:0;color:#EF4444">Short build-up</span><span class="q" style="right:0;bottom:0">Short covering</span><span class="q" style="left:0;bottom:0">Long unwinding</span>';
  const tiles = '<div class="fo-tiles four">' + ["Long build-up", "Short build-up", "Short covering", "Long unwinding"].map((kk) => tile(kk, String(stocks.filter((s) => s.buildup === kk).length), "stocks today")).join("") + "</div>";
  return `<div class="fo-quad"><div class="h" style="top:50%"></div><div class="v" style="left:50%"></div>${q}${dots}</div><div class="fo-xl" style="margin-right:0"><span>price fell ${pct(-mx, 0)}</span><span>left to right: price over 5 sessions · bottom to top: open interest over 5 sessions</span>` +
    `<span>price rose ${pct(mx, 0)}</span></div>${legend([["Model: upper two fifths", "#22C55E", ""], ["Middle", "#71717A", ""], ["Lower two fifths", "#EF4444", ""]])}${tiles}`;
}
// ── the three views of fno_page._options_map ──
export const stocksView = (stocks, flt, mine) => { const rows = pick(stocks, flt, mine); return grid("two", card("Price and open interest map", `${stocks.length} stocks · last 5 sessions`, quadrant(stocks)), card("F&O stocks", `${rows.length} shown`, stockTable(rows), true)); };
export const indexView = (x, which) => { const lv = x.levels, ch = x.chain || {};
  return grid("", card("Levels into expiry", `expiry ${day(lv.m_exp, false)}`, levelBar(lv) + levels(lv))) + chainCards(ch[which], lv.fut) + grid("", card("Six months", "end of day", histCharts(x.hist || {}))); };
export function stockView(sel, d) {
  const lv = d.levels; let body = grid("", card(sel, `expiry ${day(lv.m_exp, false)}`, levelBar(lv) + levels(lv)));
  if (d.chain) body += chainCards(d.chain, lv.fut);
  if (d.hist && Object.keys(d.hist).length) body += grid("", card("Six months", "end of day", histCharts(d.hist)));
  return body;
}

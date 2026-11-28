// Arka Trades · research engine. A port of the classic Research terminal (research_page.py v16.1 + research_data.evidence):
// every screen is built here as a list of plain blocks (header, numbers, table, checklist, chart …) from the Screener page,
// Yahoo prices and the other public sources, with the same rules, thresholds and wording as Streamlit. No page code here,
// so the numbers can be checked against the Python in a test. stock.js draws the blocks.
const ordn = (v) => { const k = Math.round(v), t = k % 100; return k + (t >= 11 && t <= 13 ? "th" : ["th", "st", "nd", "rd"][k % 10] || "th"); };      // 1st, 2nd, 3rd, 11th, 21st

// ── number and text helpers (Python formatting) ──
const isNum = (v) => typeof v === "number" && isFinite(v);
export function fx(v, d = 2, comma = true, sign = false) {        // f"{v:,.2f}", f"{v:+.1f}" … (ties to even, as Python)
  if (!isNum(v)) return "nan";
  const a = Math.abs(v), x = a < 1e15 ? a.toFixed(Math.min(100, d + 40)) : "", dot = x.indexOf("."), tail = x.slice(dot + 1 + d);
  let s = a.toFixed(d);
  if (x && /^50*$/.test(tail)) {                                     // an exact tie: round to the even digit
    const keep = x.slice(0, dot + 1 + d).replace(/\.$/, ""), last = +keep.replace(".", "").slice(-1);
    if (last % 2 === 0) s = keep;
  }
  if (comma) { const [a, b] = s.split("."); s = a.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + (b != null ? "." + b : ""); }
  return (v < 0 || Object.is(v, -0) ? "-" : sign ? "+" : "") + s;
}
export const pct = (v, d = 1, sign = false) => (v == null || !isNum(v) ? "N/A" : fx(v, d, false, sign) + "%");     // _pct
export const rs = (v, d = 2) => (v == null || !isNum(v) ? "N/A" : "₹" + fx(v, d));                                  // _rs
export const qty = (v) => (v == null || !isNum(v) ? "—" : fx(v, 0));                                                // _qty
export function g(v) {                                           // f"{v:g}"
  if (!isNum(v)) return String(v);
  const p = Number(v.toPrecision(6)); return String(p);
}
export function f(v) {                                           // _f: '1,234' '23%' '₹45 Cr' → number
  if (v == null) return null;
  if (typeof v === "number") return isFinite(v) ? v : null;
  const s = String(v).replace(/,/g, "").replace(/%/g, "").replace(/₹/g, "").replace(/Cr/g, "").trim();
  if (["", "-", "—", "nan", "NaN", "None"].includes(s)) return null;
  if (!/^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(s) && !/^[+-]?(inf|infinity)$/i.test(s)) return null;
  const n = Number(s); return isFinite(n) ? n : null;
}
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const WD = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const dt = (s) => new Date(s + "T00:00:00Z");
export const dfmt = (s, k) => {                                  // strftime for "YYYY-MM-DD"
  const t = dt(s), dd = String(t.getUTCDate()).padStart(2, "0"), m = MON[t.getUTCMonth()], y = t.getUTCFullYear(), a = WD[t.getUTCDay()];
  return k === "a d b Y" ? `${a} ${dd} ${m} ${y}` : k === "d b Y" ? `${dd} ${m} ${y}` : k === "d b" ? `${dd} ${m}` : k === "b Y" ? `${m} ${y}` : s;
};
const addDays = (s, k) => new Date(dt(s).getTime() + k * 864e5).toISOString().slice(0, 10);
const sum = (a) => a.reduce((x, y) => x + y, 0);
const mean = (a) => (a.length ? sum(a) / a.length : NaN);
function median(a) { const b = [...a].sort((x, y) => x - y), n = b.length; return n ? (n % 2 ? b[(n - 1) / 2] : (b[n / 2 - 1] + b[n / 2]) / 2) : NaN; }
function std(a, ddof = 1) { const n = a.length; if (n - ddof <= 0) return NaN; const m = mean(a); return Math.sqrt(sum(a.map((x) => (x - m) ** 2)) / (n - ddof)); }
function cov(a, b) { const n = a.length; if (n < 2) return NaN; const ma = mean(a), mb = mean(b); let s = 0; for (let i = 0; i < n; i++) s += (a[i] - ma) * (b[i] - mb); return s / (n - 1); }
const corr = (a, b) => cov(a, b) / (std(a) * std(b));
function quantile(a, q) { const b = [...a].sort((x, y) => x - y), pos = (b.length - 1) * q, lo = Math.floor(pos), hi = Math.ceil(pos); return b[lo] + (b[hi] - b[lo]) * (pos - lo); }
function ewm(a, { span, alpha }) {                               // pandas ewm(adjust=False).mean(); NaN in the input carries the last value
  const al = alpha ?? 2 / (span + 1), out = []; let prev = null;
  for (const x of a) { if (x == null || !isFinite(x)) { out.push(prev); continue; } prev = prev == null ? x : al * x + (1 - al) * prev; out.push(prev); }
  return out;
}
function rolling(a, n, fn, minp = n) {                           // pandas rolling(n, min_periods).fn()
  const out = [];
  for (let i = 0; i < a.length; i++) { const w = a.slice(Math.max(0, i - n + 1), i + 1).filter((x) => x != null && isFinite(x)); out.push(i + 1 >= Math.min(n, minp) && w.length >= minp ? fn(w) : null); }
  return out;
}
function rma(values, n) {                                        // data_engine._rma (TradingView ta.rma)
  const out = values.map(() => NaN); if (values.length <= n) return out;
  out[n] = mean(values.slice(1, n + 1).filter((x) => !isNaN(x)));
  for (let i = n + 1; i < values.length; i++) out[i] = (out[i - 1] * (n - 1) + values[i]) / n;
  return out;
}
export function rsiWilder(c, n = 14) {                           // data_engine.rsi_wilder
  if (c.length <= n) return c.map(() => NaN);
  const gain = c.map((x, i) => (i === 0 ? NaN : Math.max(x - c[i - 1], 0))), loss = c.map((x, i) => (i === 0 ? NaN : Math.max(c[i - 1] - x, 0)));
  const ag = rma(gain, n), al = rma(loss, n);
  return ag.map((a, i) => (al[i] === 0 && !isNaN(a) ? 100 : 100 - 100 / (1 + a / al[i])));
}
const trueRange = (H) => H.c.map((_, i) => (i === 0 ? H.h[0] - H.l[0] : Math.max(H.h[i] - H.l[i], Math.abs(H.h[i] - H.c[i - 1]), Math.abs(H.l[i] - H.c[i - 1]))));
const atrOf = (H) => ewm(trueRange(H), { alpha: 1 / 14 }).at(-1);

// ── history ──
const PERIOD_DAYS = { "5d": 8, "1mo": 32, "3mo": 95, "6mo": 186, "1y": 368, "2y": 733, "3y": 1098, "5y": 1830 };
export function slice(H, a, b) { const o = { d: H.d.slice(a, b) }; for (const k of ["o", "h", "l", "c", "v"]) if (H[k]) o[k] = H[k].slice(a, b); return o; }
export function hist(H, period) {                                // get_history(sym, period): the 5-year download cut to a calendar window
  if (!H || !H.d || !H.d.length) return null;
  if (!PERIOD_DAYS[period]) return H;
  const cut = addDays(H.d.at(-1), -PERIOD_DAYS[period]), i = H.d.findIndex((x) => x >= cut);
  return slice(H, i < 0 ? H.d.length : i);
}
export function hist2(H, period, fallback = "2y") {             // _hist: at least 2 sessions or None
  let h = hist(H, period);
  if ((!h || h.d.length < 2) && fallback) h = hist(H, fallback);
  return h && h.d.length >= 2 ? h : null;
}
export const closeS = (h) => (h ? { d: h.d, v: h.c } : null);    // a date-indexed series {d, v}
export function join(a, b) {                                     // pd.concat([a, b], axis=1).dropna()
  const m = new Map(b.d.map((x, i) => [x, b.v[i]])), d = [], s = [], bb = [];
  a.d.forEach((x, i) => { const y = m.get(x); if (y != null && a.v[i] != null && isFinite(y) && isFinite(a.v[i])) { d.push(x); s.push(a.v[i]); bb.push(y); } });
  return { d, s, b: bb };
}
const asof = (S, day) => { let v = null; for (let i = 0; i < S.d.length && S.d[i] <= day; i++) if (S.v[i] != null) v = S.v[i]; return v; };
const retN = (v, n) => (v && v.length > n ? (v.at(-1) / v[v.length - 1 - n] - 1) * 100 : null);       // _ret
const pctChange = (v) => v.slice(1).map((x, i) => x / v[i] - 1);

// ── Screener record helpers ──
export function row(rec, ...needles) {
  if (!rec) return null;
  for (let n of needles) {
    n = n.toLowerCase();
    for (const r of rec.rows) if (r.label.toLowerCase().startsWith(n)) return r;
    for (const r of rec.rows) if (r.label.toLowerCase().includes(n)) return r;
  }
  return null;
}
export const nums = (r) => ((r || {}).values || []).map(f);
export function annualMap(rec, ...labels) {
  const r = row(rec, ...labels); if (!r || !rec) return {};
  const out = {};
  rec.periods.forEach((p, i) => { if (i >= r.values.length || String(p).toUpperCase().includes("TTM")) return; const x = f(r.values[i]); if (x != null) out[String(p)] = x; });
  return out;
}
export function debtEquity(B) {
  try {
    const last = (r) => { const a = nums(r).filter((x) => x != null); if (!a.length) throw 0; return a.at(-1); };
    const bs = B.balance_sheet, bv = last(row(bs, "borrowings")), rv = last(row(bs, "reserves")), ev = last(row(bs, "equity capital", "share capital"));
    const nw = rv + ev; return nw ? Math.round((bv / nw) * 100) / 100 : null;
  } catch { return null; }
}
export function ttmEps(B) { const e = nums(row(B.quarterly, "EPS")).slice(-4); return e.length === 4 && e.every((v) => v != null) ? sum(e) : null; }
export function quarterEnd(label) {                              // 'Mar 2024' → '2024-03-31'
  const m = String(label).trim().match(/^([A-Za-z]{3}) (\d{4})$/); if (!m) return null;
  const k = MON.findIndex((x) => x.toLowerCase() === m[1].toLowerCase()); if (k < 0) return null;
  return new Date(Date.UTC(+m[2], k + 1, 0)).toISOString().slice(0, 10);
}
export function peHistory(B, close) {                            // [(quarter end, ttm eps, P/E)] like _valuation / _pe_stats / _pe_history
  const q = B.quarterly, eps = nums(row(q, "EPS")), out = [];
  for (let i = 3; i < eps.length; i++) {
    const w = eps.slice(i - 3, i + 1);
    if (w.some((v) => v == null) || sum(w) <= 0 || i >= q.periods.length) continue;
    const d = quarterEnd(q.periods[i]); if (d == null || d < close.d[0]) continue;
    const px = asof(close, d); if (px != null) out.push([d, sum(w), px / sum(w)]);
  }
  return out;
}
export function peStats(B, close) {
  const ttm = ttmEps(B); if (!close || !close.d.length || !ttm || ttm <= 0) return [null, null];
  const pe = close.v.at(-1) / ttm, h = peHistory(B, close).map((x) => x[2]);
  return [pe, h.length >= 4 ? (h.filter((v) => v < pe).length / h.length) * 100 : null];
}
export const lin = (v, bad, good) => (v == null || !isFinite(v) || good === bad ? null : Math.max(0, Math.min(100, ((v - bad) / (good - bad)) * 100)));
export const word = (s) => (s >= 70 ? ["Strong", "g"] : s >= 55 ? ["Good", "g"] : s >= 40 ? ["Mixed", "a"] : ["Weak", "r"]);
function ttmGrowth(q, ...names) { const v = nums(row(q, ...names)); return v.length >= 8 && v.slice(-8).every((x) => x != null) && sum(v.slice(-8, -4)) > 0 ? (sum(v.slice(-4)) / sum(v.slice(-8, -4)) - 1) * 100 : null; }
function cagr3(y, ...names) { const m = annualMap(y, ...names), ks = ((y || {}).periods || []).filter((p) => p in m); return ks.length >= 4 && m[ks.at(-4)] > 0 && m[ks.at(-1)] > 0 ? ((m[ks.at(-1)] / m[ks.at(-4)]) ** (1 / 3) - 1) * 100 : null; }

// ── HC: Piotroski-style tests and the automatic flags ──
export function piotroski(B, liq = null) {
  const y = B.yearly, bs = B.balance_sheet, cf = B.cash_flow;
  const npf = annualMap(y, "Net Profit", "Profit for the period"), sales = annualMap(y, "Sales", "Revenue"), opm = annualMap(y, "OPM", "Financing Margin");
  const ta = annualMap(bs, "Total Assets", "Total Liabilities"), debt = annualMap(bs, "Borrowings"), eqc = annualMap(bs, "Equity Capital", "Share Capital");
  const cfo = annualMap(cf, "Cash from Operating", "Operating");
  const yrs = ((y || {}).periods || []).filter((p) => p in npf && p in ta);
  if (yrs.length < 2) return null;
  const t = yrs.at(-1), p = yrs.at(-2);
  const ratio = (a, b, k) => (k in a && b[k] ? a[k] / b[k] : null);
  const up = (a, b) => (a == null || b == null ? null : a > b);
  const arrow = (a, b, mult = 100, suf = "%", d = 1) => (a == null || b == null ? "" : `${fx(b * mult, d, false)}${suf} → ${fx(a * mult, d, false)}${suf}`);
  const roaT = ratio(npf, ta, t), roaP = ratio(npf, ta, p), levT = ratio(debt, ta, t), levP = ratio(debt, ta, p), atT = ratio(sales, ta, t), atP = ratio(sales, ta, p);
  const hasCfo = t in cfo;
  const tests = [
    ["Profitable (net profit > 0)", npf[t] > 0, `NP ₹${fx(npf[t], 0)} Cr`],
    ["Operating cash flow positive", hasCfo ? cfo[t] > 0 : null, hasCfo ? `CFO ₹${fx(cfo[t], 0)} Cr` : "cash flow not available"],
    ["Return on assets improving", up(roaT, roaP), arrow(roaT, roaP)],
    ["Cash flow exceeds profit (earnings quality)", hasCfo ? cfo[t] > npf[t] : null, hasCfo && npf[t] ? `CFO/NP ${fx(cfo[t] / npf[t], 2, false)}x` : ""],
    ["Leverage falling (debt / assets)", levT == null || levP == null ? null : levT <= levP, arrow(levT, levP)],
    ["Liquidity improving (current ratio)", !liq ? null : liq[0] > liq[1], liq ? `${fx(liq[1], 2, false)}x → ${fx(liq[0], 2, false)}x · Yahoo` : "current assets not available"],
    ["No share dilution (equity capital)", !(t in eqc) || !(p in eqc) ? null : eqc[t] <= eqc[p], "a split / bonus can show as a false fail"],
    ["Operating margin improving", up(opm[t] ?? null, opm[p] ?? null), arrow(opm[t] ?? null, opm[p] ?? null, 1)],
    ["Asset turnover improving", up(atT, atP), arrow(atT, atP, 1, "x", 2)],
  ];
  return [tests, t, p];
}
export function flags(B) {
  const out = [], y = B.yearly, bs = B.balance_sheet, cf = B.cash_flow, yp = (y || {}).periods || [];
  const pr = nums(row(B.shareholding, "Promoters")).filter((v) => v != null);
  if (pr.length >= 5) {
    const d = pr.at(-1) - pr.at(-5);
    if (d <= -2) out.push([`Promoter holding down ${fx(Math.abs(d), 1, false)} pp over 4 quarters`, false, `${fx(pr.at(-5), 1, false)}% → ${fx(pr.at(-1), 1, false)}%`]);
    else if (d >= 1) out.push([`Promoter holding up ${fx(d, 1, false)} pp over 4 quarters`, true, `${fx(pr.at(-5), 1, false)}% → ${fx(pr.at(-1), 1, false)}%`]);
  }
  const debt = annualMap(bs, "Borrowings"), dy = ((bs || {}).periods || []).filter((p) => p in debt);
  if (dy.length) {
    if (debt[dy.at(-1)] === 0) out.push(["Debt-free on latest balance sheet", true, dy.at(-1)]);
    else if (dy.length >= 4 && debt[dy.at(-4)] > 0 && debt[dy.at(-1)] / debt[dy.at(-4)] > 1.5)
      out.push([`Borrowings up ${fx((debt[dy.at(-1)] / debt[dy.at(-4)] - 1) * 100, 0, false)}% in 3 years`, false, `₹${fx(debt[dy.at(-4)], 0)} → ₹${fx(debt[dy.at(-1)], 0)} Cr`]);
  }
  const npf = annualMap(y, "Net Profit", "Profit for the period"), cfo = annualMap(cf, "Cash from Operating", "Operating");
  const common = yp.filter((p) => p in npf && p in cfo).slice(-5);
  if (common.length >= 3) {
    const snp = sum(common.map((p) => npf[p])), scfo = sum(common.map((p) => cfo[p]));
    if (snp > 0) { const conv = scfo / snp;
      if (conv < 0.5) out.push([`Weak cash conversion over ${common.length} years`, false, `CFO = ${fx(conv * 100, 0, false)}% of profit`]);
      else if (conv >= 0.9) out.push([`Strong cash conversion over ${common.length} years`, true, `CFO = ${fx(conv * 100, 0, false)}% of profit`]); }
  }
  const opm = annualMap(y, "OPM"), oy = yp.filter((p) => p in opm);
  if (oy.length >= 2) {
    const d = opm[oy.at(-1)] - opm[oy.at(-2)];
    if (d <= -3) out.push([`Operating margin fell ${fx(Math.abs(d), 1, false)} pp YoY`, false, `${fx(opm[oy.at(-2)], 0, false)}% → ${fx(opm[oy.at(-1)], 0, false)}%`]);
    else if (d >= 3) out.push([`Operating margin expanded ${fx(d, 1, false)} pp YoY`, true, `${fx(opm[oy.at(-2)], 0, false)}% → ${fx(opm[oy.at(-1)], 0, false)}%`]);
  }
  for (const [label, m] of [["Sales", annualMap(y, "Sales", "Revenue")], ["Net profit", npf]]) {
    const ys = yp.filter((p) => p in m); if (ys.length < 4) continue;
    const a = m[ys.at(-1)], b = m[ys.at(-4)];
    if (a > 0 && b > 0) { const c = ((a / b) ** (1 / 3) - 1) * 100;
      if (c < 0) out.push([`${label} shrinking`, false, `3-yr CAGR ${fx(c, 1, false, true)}%`]);
      else if (c >= 15) out.push([`${label} compounding fast`, true, `3-yr CAGR ${fx(c, 1, false, true)}%`]); }
    else if (b > 0 && 0 >= a) out.push([`${label} turned negative`, false, `${ys.at(-4)} → ${ys.at(-1)}`]);
  }
  return out;
}

// ── research_data.evidence: the fact sheet and the contradiction rules ──
export function evidence(B, close, peNow = null, pePct = null, dlv = null) {
  const facts = [], con = [], unk = [], add = (label, value, source) => facts.push({ label, value, source });
  const q = B.quarterly, y = B.yearly, sh = B.shareholding, cf = B.cash_flow;
  const vals = (rec, ...nd) => { for (const n of nd) for (const r of (rec || {}).rows || []) if (r.label.toLowerCase().startsWith(n.toLowerCase())) return r.values.map(f);
    for (const n of nd) for (const r of (rec || {}).rows || []) if (r.label.toLowerCase().includes(n.toLowerCase())) return r.values.map(f); return []; };
  const ttmPair = (v) => (v.length >= 8 && v.slice(-8).every((x) => x != null) ? [sum(v.slice(-4)), sum(v.slice(-8, -4))] : [null, null]);
  let ret1y = null, ret20 = null;
  if (close && close.v.length > 21) {
    const c = close.v.filter((x) => x != null);
    ret20 = (c.at(-1) / c.at(-21) - 1) * 100; add("Price change, 20 sessions", fx(ret20, 1, false, true) + "%", "Yahoo");
    if (c.length > 252) { ret1y = (c.at(-1) / c.at(-253) - 1) * 100; add("Price change, 1 year", fx(ret1y, 1, false, true) + "%", "Yahoo"); }
  } else unk.push("price history");
  const [sNow, sPrev] = ttmPair(vals(q, "Sales", "Revenue")), [pNow, pPrev] = ttmPair(vals(q, "Net Profit", "Profit for the period"));
  const salesG = sNow != null && sPrev && sPrev > 0 ? (sNow / sPrev - 1) * 100 : null, profG = pNow != null && pPrev && pPrev > 0 ? (pNow / pPrev - 1) * 100 : null;
  if (salesG != null) add("Sales, last 4 quarters vs previous 4", fx(salesG, 1, false, true) + "%", "Screener");
  if (profG != null) add("Net profit, last 4 quarters vs previous 4", fx(profG, 1, false, true) + "%", "Screener");
  else if (pNow != null && pPrev != null) add("Net profit, last 4 quarters vs previous 4", `₹${fx(pPrev, 0)} Cr → ₹${fx(pNow, 0)} Cr`, "Screener");
  if (salesG == null && profG == null) unk.push("8 quarters of results");
  const opm = vals(q, "OPM", "Financing Margin").filter((v) => v != null), opmD = opm.length >= 5 ? opm.at(-1) - opm.at(-5) : null;
  if (opmD != null) add("Operating margin, latest quarter vs a year ago", `${fx(opm.at(-5), 0, false)}% → ${fx(opm.at(-1), 0, false)}% (${fx(opmD, 1, false, true)} pp)`, "Screener");
  if (peNow) add("P/E (TTM)", `${fx(peNow, 1, false)}x` + (pePct != null ? `, ${ordn(pePct)} percentile of its own history` : ""), "Screener EPS + Yahoo price");
  else unk.push("P/E (needs positive TTM EPS)");
  const pr = vals(sh, "Promoters").filter((v) => v != null), promD = pr.length >= 5 ? pr.at(-1) - pr.at(-5) : null;
  if (pr.length) add("Promoter holding", `${fx(pr.at(-1), 2, false)}%` + (promD != null ? ` (${fx(promD, 2, false, true)} pp in 4 quarters)` : ""), "Screener");
  else unk.push("shareholding pattern");
  for (const [lab, nd] of [["FII holding", "FII"], ["DII holding", "DII"]]) {
    const v = vals(sh, nd).filter((x) => x != null);
    if (v.length) add(lab, `${fx(v.at(-1), 2, false)}%` + (v.length >= 5 ? ` (${fx(v.at(-1) - v.at(-5), 2, false, true)} pp in 4 quarters)` : ""), "Screener");
  }
  const npy = {}, cfo = {}, yv = vals(y, "Net Profit", "Profit for the period"), cv = vals(cf, "Cash from Operating", "Operating");
  ((y || {}).periods || []).forEach((p, i) => { if (i < yv.length && yv[i] != null && !String(p).toUpperCase().includes("TTM")) npy[p] = yv[i]; });
  ((cf || {}).periods || []).forEach((p, i) => { if (i < cv.length && cv[i] != null) cfo[p] = cv[i]; });
  const common = Object.keys(npy).filter((p) => p in cfo).slice(-3); let conv = null;
  if (common.length === 3 && sum(common.map((p) => npy[p])) > 0) { conv = (sum(common.map((p) => cfo[p])) / sum(common.map((p) => npy[p]))) * 100; add("Operating cash flow as % of net profit, 3 years", fx(conv, 0, false) + "%", "Screener"); }
  else unk.push("3 years of cash flow");
  let dlNow = null, dlBase = null;
  const dp = dlv ? dlv.pct.filter((x) => x != null && isFinite(x)) : [];
  if (dlv && dlv.d.length >= 40 && dp.length >= 40) { dlNow = mean(dp.slice(-10)); dlBase = mean(dp.slice(0, -10)); add("Delivery share of traded quantity, last 10 sessions vs before", `${fx(dlBase, 0, false)}% → ${fx(dlNow, 0, false)}%`, "NSE bhavcopy"); }
  else unk.push("delivery data");
  const c_ = (name, numbers) => con.push({ name, numbers });
  if (pePct != null && pePct >= 80 && profG != null && profG < 0) c_("Valuation high, profit falling", `P/E at the ${ordn(pePct)} percentile of its own history while 4-quarter profit is ${fx(profG, 1, false, true)}%`);
  if (ret1y != null && ret1y > 20 && profG != null && profG < 0) c_("Price up, profit down", `price ${fx(ret1y, 1, false, true)}% in a year while 4-quarter profit is ${fx(profG, 1, false, true)}%`);
  if (ret1y != null && ret1y < -20 && profG != null && profG > 15) c_("Price down, profit up", `price ${fx(ret1y, 1, false, true)}% in a year while 4-quarter profit is ${fx(profG, 1, false, true)}%`);
  if (salesG != null && salesG > 10 && opmD != null && opmD <= -2) c_("Sales growing, margin shrinking", `sales ${fx(salesG, 1, false, true)}% while operating margin moved ${fx(opmD, 1, false, true)} pp`);
  if (conv != null && conv < 50 && profG != null && profG > 0) c_("Profit growing, cash not following", `operating cash flow is ${fx(conv, 0, false)}% of profit over 3 years while profit is ${fx(profG, 1, false, true)}%`);
  if (promD != null && promD <= -1 && ret1y != null && ret1y > 0) c_("Price up, promoter holding down", `promoters ${fx(promD, 2, false, true)} pp in 4 quarters while price is ${fx(ret1y, 1, false, true)}% in a year`);
  if (dlNow != null && ret20 != null && ret20 < -5 && dlNow >= dlBase * 1.25) c_("Price falling, delivery share rising", `price ${fx(ret20, 1, false, true)}% in 20 sessions while delivery share rose from ${fx(dlBase, 0, false)}% to ${fx(dlNow, 0, false)}%`);
  unk.push("analyst estimate revisions", "who bought or sold", "management guidance");
  return { facts, contradictions: con, unknown: unk };
}

// ── SC: the five-pillar scorecard ──
export function scorecard(B, close, bench, dlv = null) {
  const s = B.summary || {}, q = B.quarterly, y = B.yearly, cf = B.cash_flow, sh = B.shareholding;
  const P = { Quality: [], Growth: [], Valuation: [], Momentum: [], Ownership: [] };
  const add = (p, label, text, score, rule) => { if (score != null) P[p].push({ label, text, score: +score, rule }); };
  const res = piotroski(B);
  if (res) { const av = res[0].filter((x) => x[1] != null); if (av.length) { const k = av.filter((x) => x[1]).length; add("Quality", "Quality tests passed", `${k} of ${av.length}`, (k / av.length) * 100, "share of the HC tests passed"); } }
  const roe = f(s.roe), roce = f(s.roce);
  if (roe != null) add("Quality", "Return on equity", `${fx(roe, 1, false)}%`, lin(roe, 5, 22), "0 at 5%, 100 at 22%");
  if (roce != null) add("Quality", "Return on capital employed", `${fx(roce, 1, false)}%`, lin(roce, 6, 25), "0 at 6%, 100 at 25%");
  const de = debtEquity(B);
  if (de != null && row(y, "Financing Profit", "Financing Margin") == null) add("Quality", "Debt / equity", `${fx(de, 2, false)}x`, lin(de, 2.0, 0.0), "0 at 2x, 100 at no debt (not scored for lenders)");
  const npm = annualMap(y, "Net Profit", "Profit for the period"), cfo = annualMap(cf, "Cash from Operating", "Operating");
  const cm = ((y || {}).periods || []).filter((p) => p in npm && p in cfo).slice(-3);
  if (cm.length === 3 && sum(cm.map((p) => npm[p])) > 0) { const conv = (sum(cm.map((p) => cfo[p])) / sum(cm.map((p) => npm[p]))) * 100; add("Quality", "Cash flow as % of profit, 3 years", `${fx(conv, 0, false)}%`, lin(conv, 30, 100), "0 at 30%, 100 at 100%"); }
  const sg = ttmGrowth(q, "Sales", "Revenue"), pg = ttmGrowth(q, "Net Profit", "Profit for the period");
  if (sg != null) add("Growth", "Sales, last 4 quarters", `${fx(sg, 1, false, true)}%`, lin(sg, -5, 25), "0 at -5%, 100 at +25%");
  if (pg != null) add("Growth", "Net profit, last 4 quarters", `${fx(pg, 1, false, true)}%`, lin(pg, -10, 30), "0 at -10%, 100 at +30%");
  for (const [lab, names] of [["Sales, 3-year growth a year", ["Sales", "Revenue"]], ["Net profit, 3-year growth a year", ["Net Profit", "Profit for the period"]]]) {
    const c3 = cagr3(y, ...names); if (c3 != null) add("Growth", lab, `${fx(c3, 1, false, true)}%`, lin(c3, 0, 20), "0 at 0%, 100 at +20%");
  }
  const opm = nums(row(q, "OPM", "Financing Margin")).filter((v) => v != null);
  if (opm.length >= 5) add("Growth", "Operating margin vs a year ago", `${fx(opm.at(-1) - opm.at(-5), 1, false, true)} pp`, lin(opm.at(-1) - opm.at(-5), -4, 4), "0 at -4 pp, 100 at +4 pp");
  const [pe, pp] = close ? peStats(B, close) : [null, null];
  if (pe && pp != null) add("Valuation", "P/E against its own history", `${fx(pe, 1, false)}x · ${ordn(pp)} percentile`, 100 - pp, "100 at its cheapest, 0 at its dearest");
  if (pe) {
    add("Valuation", "Earnings yield", `${fx(100 / pe, 1, false)}%`, lin(100 / pe, 1.5, 8), "0 at 1.5%, 100 at 8%");
    if (pg != null && pg > 0) add("Valuation", "P/E ÷ profit growth", fx(pe / pg, 2, false), lin(pe / pg, 3, 0.7), "0 at 3, 100 at 0.7");
  }
  const price = close && close.v.length ? close.v.at(-1) : null, bv = f(s.book_value);
  if (price && bv && bv > 0 && roe != null && roe > 0) add("Valuation", "P/B ÷ return on equity", fx((price / bv / roe) * 100, 1, false), lin((price / bv / roe) * 100, 40, 8), "0 at 40, 100 at 8 (price paid per unit of return)");
  if (close && close.v.length > 70) {
    for (const [lab, n] of [["3 months vs the index", 63], ["6 months vs the index", 126], ["12 months vs the index", 252]]) {
      if (bench) { const J = join(close, bench); if (J.d.length > n) { const d = (J.s.at(-1) / J.s.at(-1 - n) - J.b.at(-1) / J.b.at(-1 - n)) * 100; add("Momentum", lab, `${fx(d, 1, false, true)} pp`, lin(d, -15, 15), "0 at -15 pp, 100 at +15 pp"); } }
    }
    if (close.v.length >= 200) { const x = (close.v.at(-1) / mean(close.v.slice(-200)) - 1) * 100; add("Momentum", "Price vs 200-day average", `${fx(x, 1, false, true)}%`, lin(x, -10, 15), "0 at -10%, 100 at +15%"); }
    const hi = Math.max(...close.v.slice(-252)), dd = (close.v.at(-1) / hi - 1) * 100;
    add("Momentum", "Distance from 52-week high", `${fx(dd, 1, false, true)}%`, lin(dd, -35, -3), "0 at -35%, 100 at -3%");
  }
  const prm = nums(row(sh, "Promoters")).filter((v) => v != null);
  if (prm.length >= 5) add("Ownership", "Promoter holding, 4 quarters", `${fx(prm.at(-1) - prm.at(-5), 2, false, true)} pp`, lin(prm.at(-1) - prm.at(-5), -3, 1), "0 at -3 pp, 100 at +1 pp");
  let inst = null;
  for (const nd of ["FII", "DII"]) { const v = nums(row(sh, nd)).filter((x) => x != null); if (v.length >= 5) inst = (inst || 0) + v.at(-1) - v.at(-5); }
  if (inst != null) add("Ownership", "FII + DII holding, 4 quarters", `${fx(inst, 2, false, true)} pp`, lin(inst, -2, 2), "0 at -2 pp, 100 at +2 pp");
  if (dlv && dlv.d.length >= 40) { const dd = dlv.pct.filter((x) => x != null && isFinite(x));
    if (dd.length >= 40) { const now = mean(dd.slice(-10)), base = mean(dd.slice(0, -10)); if (base > 0) add("Ownership", "Delivery share, last 10 sessions vs before", `${fx(base, 0, false)}% → ${fx(now, 0, false)}%`, lin(now / base, 0.8, 1.3), "0 at 0.8x, 100 at 1.3x of its own normal"); } }
  const pillars = Object.entries(P).filter(([, v]) => v.length).map(([name, items]) => ({ name, score: sum(items.map((i) => i.score)) / items.length, items }));
  return { pillars, overall: pillars.length ? sum(pillars.map((p) => p.score)) / pillars.length : null, missing: Object.keys(P).filter((k) => !P[k].length) };
}

// ── DCF and the value range ──
export function dcfValue(cf, g_, r, tg, years = 10) {
  let pv = 0, cur = cf; const half = Math.floor(years / 2);
  for (let t = 1; t <= years; t++) { const gt = t <= half ? g_ : g_ + ((tg - g_) * (t - half)) / (years - half); cur *= 1 + gt; pv += cur / (1 + r) ** t; }
  return pv + (cur * (1 + tg)) / (r - tg) / (1 + r) ** years;
}
export function valueRows(B, close, info = {}, targets = {}) {
  const s = B.summary || {}, price = close.v.at(-1), ttm = ttmEps(B), bv = f(s.book_value), rows = [];
  const pes = peHistory(B, close).map((x) => x[2]).sort((a, b) => a - b), mid = Math.floor(pes.length / 2);
  if (ttm && ttm > 0 && pes.length >= 4) rows.push(["Its own P/E range", `today's EPS at its lowest, median and highest P/E (${fx(pes[0], 0, false)}x · ${fx(pes[mid], 0, false)}x · ${fx(pes.at(-1), 0, false)}x)`, ttm * pes[0], ttm * pes[mid], ttm * pes.at(-1)]);
  if (ttm && bv && ttm > 0 && bv > 0) { const gg = Math.sqrt(22.5 * ttm * bv); rows.push(["Graham number", "√(22.5 × EPS × book value), a conservative yardstick", gg, gg, gg]); }
  const mcap = f(s.market_cap), npq = nums(row(B.quarterly, "Net Profit", "Profit for the period"));
  const np4 = npq.length >= 4 && npq.slice(-4).every((v) => v != null) ? sum(npq.slice(-4)) : null, past = cagr3(B.yearly, "Net Profit", "Profit for the period");
  if (mcap && np4 && np4 > 0 && past != null) {
    const gg = Math.max(0, Math.min(0.3, past / 100)), v = [0.14, 0.12, 0.1].map((r) => (dcfValue(np4, gg, r, 0.05) / mcap) * price);
    rows.push(["Cash-flow value at past growth", `profit keeps growing ${fx(gg * 100, 0, false)}% a year for 5 years, then fades; discount rate 14% · 12% · 10%`, v[0], v[1], v[2]]);
  }
  const lo = targets.low || info.targetLowPrice, md = targets.mean || info.targetMeanPrice, hi = targets.high || info.targetHighPrice;
  if ([lo, md, hi].every((x) => isNum(x) && x > 0)) rows.push(["Analyst targets", "lowest, average and highest published target (Yahoo)", lo, md, hi]);
  const w = close.v.slice(-252);
  rows.push(["52-week trading range", "where it actually traded, for scale", Math.min(...w), median(w), Math.max(...w)]);
  return rows;
}

// ── SWG: swing setup ──
export function swingStats(h, bench = null) {
  if (!h || h.d.length < 210) return null;
  const c = h.c, hi = h.h, lo = h.l, vol = h.v, px = c.at(-1), ma = {};
  for (const n of [20, 50, 150, 200]) ma[n] = mean(c.slice(-n));
  const ma200prev = c.length >= 221 ? mean(c.slice(-221, -21)) : null, h52 = Math.max(...hi.slice(-252)), l52 = Math.min(...lo.slice(-252)), atr = atrOf(h);
  let rs6 = null;
  if (bench) { const J = join({ d: h.d, v: c }, bench); if (J.d.length > 126) rs6 = (J.s.at(-1) / J.s.at(-127) - J.b.at(-1) / J.b.at(-127)) * 100; }
  const checks = [["Price above the 150-day and 200-day averages", px > ma[150] && px > ma[200], `${rs(ma[150], 0)} · ${rs(ma[200], 0)}`],
    ["150-day average above the 200-day", ma[150] > ma[200], ""], ["200-day average rising for a month", ma200prev == null ? null : ma[200] > ma200prev, ""],
    ["50-day average above the 150-day and 200-day", ma[50] > ma[150] && ma[50] > ma[200], rs(ma[50], 0)],
    ["Price above the 50-day average", px > ma[50], `${fx((px / ma[50] - 1) * 100, 1, false, true)}%`],
    ["At least 30% above the 52-week low", px >= l52 * 1.3, `${fx((px / l52 - 1) * 100, 0, false, true)}% above ${rs(l52, 0)}`],
    ["Within 25% of the 52-week high", px >= h52 * 0.75, `${fx((px / h52 - 1) * 100, 0, false, true)}% from ${rs(h52, 0)}`],
    ["Beating the index over 6 months", rs6 == null ? null : rs6 > 0, rs6 != null ? `${fx(rs6, 1, false, true)} pp` : "index not available"]];
  const r = pctChange(c).slice(-252), w5 = c.slice(5).map((x, i) => x / c[i] - 1).slice(-252), y = c.slice(-252);
  let peak = -Infinity; const dd = Math.min(...y.map((x) => { peak = Math.max(peak, x); return x / peak - 1; })) * 100;
  const v10 = mean(vol.slice(-10)), v50 = mean(vol.slice(-50));
  return { px, ma, atr, atr_pct: (atr / px) * 100, checks, h52, l52, hi20: Math.max(...hi.slice(-21, -1)), lo10: Math.min(...lo.slice(-10)), lo20: Math.min(...lo.slice(-20)),
    ext50: (px / ma[50] - 1) * 100, vol_dry: v50 ? v10 / v50 : null, var95: r.length > 60 ? quantile(r, 0.05) * 100 : null, worst5: w5.length ? Math.min(...w5) * 100 : null,
    dd, rs6, tight: ((Math.max(...hi.slice(-10)) - Math.min(...lo.slice(-10))) / px) * 100 };
}

// ── PB: price behaviour ──
function streaks(fl) {
  let bu = 0, bd = 0, cur = 0, last = null;
  for (const x of fl) { if (x == null) { cur = 0; last = null; continue; } cur = x === last ? cur + 1 : 1; last = x; if (x) bu = Math.max(bu, cur); else bd = Math.max(bd, cur); }
  return [bu, bd, cur, last];
}
export function behaviourStats(h, n, mode = "day", splits = null, bench = null, deliv = null) {
  if (!h || h.d.length < 3) return null;
  const idx = []; h.d.forEach((d, i) => { if (h.c[i] != null && h.c[i] > 0) idx.push(i); });
  const D = idx.map((i) => ({ d: h.d[i], o: h.o[i], h: h.h[i], l: h.l[i], c: h.c[i], v: h.v[i] }));
  D.forEach((x, i) => { const p = i ? D[i - 1].c : NaN; x.chg = (x.c / p - 1) * 100; x.body = (x.c / x.o - 1) * 100; x.gap = (x.o / p - 1) * 100; x.range = ((x.h - x.l) / p) * 100;
    x.tr = i ? Math.max(x.h - x.l, Math.abs(x.h - p), Math.abs(x.l - p)) : x.h - x.l; });
  const atr = ewm(D.map((x) => x.tr), { alpha: 1 / 14 }), vols = D.map((x) => x.v);
  D.forEach((x, i) => { const w = vols.slice(Math.max(0, i - 50), i); x.vavg50 = w.length >= 10 ? mean(w) : NaN; });
  const sd = new Set((splits || []).map((x) => x[0]));
  D.forEach((x) => { x.bad = Math.abs(x.chg) > 45 || sd.has(x.d); });
  const W = D.slice(-Math.trunc(n)); if (W.length < 3) return null;
  W.forEach((x) => { x.move = mode === "candle" ? x.body : x.chg;
    x.up = mode === "candle" ? (x.c > x.o ? true : x.c < x.o ? false : null) : x.chg > 0 ? true : x.chg < 0 ? false : null;
    if (x.bad || !isFinite(x.move)) x.up = null; });
  const good = W.filter((x) => !x.bad && isFinite(x.move)), U = good.filter((x) => x.up === true), Dn = good.filter((x) => x.up === false);
  const su = sum(U.map((x) => x.v)), sdn = sum(Dn.map((x) => x.v)), [bu, bd, cur, curUp] = streaks(W.map((x) => x.up));
  const firstPrev = D.length > W.length ? D[D.length - W.length - 1].c : null;
  const argmax = (a, k, dir) => a.reduce((b, x) => (b == null || (dir > 0 ? x[k] > b[k] : x[k] < b[k]) ? x : b), null);
  const best = argmax(good, "move", 1), worst = argmax(good, "move", -1);
  const out = { n: W.length, used: good.length, excluded: W.filter((x) => x.bad).length, mode, first: W[0].d, last: W.at(-1).d,
    up: U.length, down: Dn.length, flat: good.length - U.length - Dn.length, up_pct: good.length ? (U.length / good.length) * 100 : null,
    avg_up: U.length ? mean(U.map((x) => x.move)) : null, avg_dn: Dn.length ? mean(Dn.map((x) => x.move)) : null,
    med_up: U.length ? median(U.map((x) => x.move)) : null, med_dn: Dn.length ? median(Dn.map((x) => x.move)) : null,
    best: best ? [best.d, best.move] : null, worst: worst ? [worst.d, worst.move] : null, vol_ratio: sdn > 0 ? su / sdn : null,
    avg_vol_up: U.length ? mean(U.map((x) => x.v)) : null, avg_vol_dn: Dn.length ? mean(Dn.map((x) => x.v)) : null,
    streak_up: bu, streak_dn: bd, streak_now: cur, streak_now_up: curUp, ret: firstPrev != null ? (W.at(-1).c / firstPrev - 1) * 100 : null,
    atr_pct: (atr.at(-1) / D.at(-1).c) * 100, avg_range: good.length ? mean(good.map((x) => x.range)) : null, frame: W };
  if (out.avg_up != null && out.avg_dn) out.payoff = out.avg_up / Math.abs(out.avg_dn);
  const gu = good.filter((x) => x.gap >= 1), gd = good.filter((x) => x.gap <= -1);
  Object.assign(out, { gap_up: gu.length, gap_up_held: gu.filter((x) => x.c > x.o).length, gap_dn: gd.length, gap_dn_held: gd.filter((x) => x.c < x.o).length });
  out.buckets = [[0, 1], [1, 2], [2, 3], [3, 5], [5, 1e9]].map(([lo, hi]) => [lo, hi, U.filter((x) => x.move >= lo && x.move < hi).length, Dn.filter((x) => -x.move >= lo && -x.move < hi).length]);
  if (bench && bench.d.length > 3) {
    const bm = new Map(), bp = new Map(); bench.d.forEach((d, i) => { bm.set(d, bench.v[i]); if (i) bp.set(d, bench.v[i - 1]); });
    const bw = W.map((x) => (bm.has(x.d) && bp.has(x.d) ? (bm.get(x.d) / bp.get(x.d) - 1) * 100 : null)).filter((x) => x != null && isFinite(x));
    if (bw.length >= 3) {
      const bu_ = bw.filter((x) => x > 0), bd_ = bw.filter((x) => x < 0), inW = W.filter((x) => bm.has(x.d)), inP = W.filter((x) => bp.has(x.d));
      out.bench = { up_pct: (bu_.length / bw.length) * 100, avg_up: bu_.length ? mean(bu_) : null, avg_dn: bd_.length ? mean(bd_) : null,
        ret: (bm.get(inW.at(-1).d) / bp.get(inP[0].d) - 1) * 100 };
    }
  }
  if (deliv && deliv.d.length) {
    const dm = new Map(deliv.d.map((d, i) => [d, [deliv.q[i], deliv.pct[i]]])), J = good.filter((x) => dm.has(x.d)).map((x) => ({ ...x, dq: dm.get(x.d)[0], dp: dm.get(x.d)[1] }));
    const ju = J.filter((x) => x.up === true), jd = J.filter((x) => x.up === false), sq = (a) => sum(a.map((x) => x.dq).filter((v) => v != null && isFinite(v)));
    const mp = (a) => mean(a.map((x) => x.dp).filter((v) => v != null && isFinite(v)));
    if (ju.length >= 3 && jd.length >= 3 && sq(ju) > 0 && sq(jd) > 0) out.deliv = { days: J.length, up_days: ju.length, dn_days: jd.length, qty_ratio: sq(ju) / sq(jd), pct_up: mp(ju), pct_dn: mp(jd) };
  }
  return out;
}
export function pbReading(S, sym) {
  const vr = S.vol_ratio, po = S.payoff; if (vr == null || po == null) return "";
  const tone = vr >= 1.25 && po >= 1 ? "buyers have had the upper hand: up days were bigger and carried more volume."
    : vr <= 0.8 && po <= 1 ? "sellers have had the upper hand: down days were bigger and carried more volume."
    : vr >= 1.25 ? "volume favoured up days even though down days moved further: quiet demand under choppy price."
    : vr <= 0.8 ? "volume favoured down days even though up days moved further: rallies came on thin volume." : "neither side has clearly dominated.";
  return `Over the last ${S.n} sessions ${sym} ${S.mode === "day" ? "closed higher" : "printed a green candle"} on <b>${S.up} days (${fx(S.up_pct, 0, false)}%)</b>. Up days averaged <b class='pos'>${fx(S.avg_up, 2, false, true)}%</b>, down days <b class='neg'>${fx(S.avg_dn, 2, false, true)}%</b>, and up days carried <b>${fx(vr, 2, false)}×</b> the volume of down days, so ${tone}`;
}

// ── DLV: delivery aggregation (research_data.aggregate_delivery) ──
export function aggregateDelivery(dl, freq = "D") {
  if (!dl || !dl.d.length) return [];
  const n = dl.d.length, ok = (i) => dl.q[i] != null && dl.v[i] != null;
  let out;
  if (freq === "D") out = dl.d.map((d, i) => ({ label: dfmt(d, "d b Y"), deliv_qty: ok(i) ? dl.q[i] : null, volume: dl.v[i], close: dl.c[i], deliv_pct: dl.pct[i] }));
  else {
    const key = (d) => { if (freq === "M") return d.slice(0, 7); return addDays(d, -dt(d).getUTCDay()); };   // weeks run Sunday to Saturday (W-SAT)
    const G = new Map();
    dl.d.forEach((d, i) => { const k = key(d); if (!G.has(k)) G.set(k, []); G.get(k).push(i); });
    out = [...G.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1)).map(([k, ix]) => {
      const sm = (a) => (a.length ? sum(a) : null), dq = sm(ix.filter(ok).map((i) => dl.q[i])), vq = sm(ix.filter(ok).map((i) => dl.v[i]));
      const vol = sm(ix.filter((i) => dl.v[i] != null).map((i) => dl.v[i])), cl = ix.map((i) => dl.c[i]).filter((x) => x != null);
      return { label: freq === "W" ? `${dfmt(dl.d[ix[0]], "d b")} - ${dfmt(dl.d[ix.at(-1)], "d b")}` : dfmt(k + "-01", "b Y"), deliv_qty: dq, volume: vol, close: cl.length ? cl.at(-1) : null,
        deliv_pct: dq != null && vq != null && vq > 0 ? (dq / vq) * 100 : null };
    });
  }
  out.forEach((r, i) => { r.chg_pct = i && r.close != null && out[i - 1].close != null ? (r.close / out[i - 1].close - 1) * 100 : null; });
  if (freq === "D") out.forEach((r, i) => { r.chg_pct = dl.p[i] > 0 && dl.c[i] != null ? (dl.c[i] / dl.p[i] - 1) * 100 : null; });
  return out;
}
export { mean, median, std, cov, corr, quantile, ewm, rolling, sum, addDays, asof, retN, pctChange, atrOf, trueRange };

// Arka Trades · portfolio maths. A line-by-line port of the Streamlit app's portfolio_page.py so both apps show the
// same numbers. Pure functions, no network. Dates are "YYYY-MM-DD" strings (they compare correctly as text).
export const TAX_DEFAULTS = { stcg: 20, ltcg: 12.5, exempt: 125000, cess: 4 };
const DAY = 86400000;
const ts = (d) => Date.UTC(+d.slice(0, 4), +d.slice(5, 7) - 1, +d.slice(8, 10));
export const daysBetween = (a, b) => Math.round((ts(b) - ts(a)) / DAY);
export function addMonths(d, n) {
  let y = +d.slice(0, 4), m = +d.slice(5, 7) - 1 + n; const day = +d.slice(8, 10);
  y += Math.floor(m / 12); m = ((m % 12) + 12) % 12;
  const last = [31, y % 4 === 0 && (y % 100 !== 0 || y % 400 === 0) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m];
  return `${y}-${String(m + 1).padStart(2, "0")}-${String(Math.min(day, last)).padStart(2, "0")}`;
}
export function fy(d) { const y = +d.slice(5, 7) >= 4 ? +d.slice(0, 4) : +d.slice(0, 4) - 1; return `FY${y}-${String((y + 1) % 100).padStart(2, "0")}`; }
const sum = (a) => a.reduce((x, y) => x + y, 0);
const mean = (a) => (a.length ? sum(a) / a.length : NaN);
const cmp = (a, b) => (a < b ? -1 : a > b ? 1 : 0);

// ── buys, sells, splits ──
export function splitFactor(splits, since) {              // today's shares per share held at `since`
  let f = 1; if (since == null) return f;
  for (const [d, r] of splits || []) if (d > since) f *= r;
  return f;
}
export const MAIN = "Main", ALL = "All books";
export function engine(lots, sells, splits = {}) {         // split-adjust, then match sells to the oldest buys (FIFO) inside each book
  const groups = {};
  for (const l of lots) {
    const sym = String(l.sym).toUpperCase(), f = splitFactor(splits[sym], l.date || null), q = +l.qty, p = +l.price;
    const book = l.book || MAIN, k = book + "|" + sym;
    (groups[k] = groups[k] || []).push({ id: l.id, sym, book, date: l.date || null, qty: q * f, price: f ? p / f : p, rawQty: q, rawPrice: p, factor: f, note: l.note || "", open: q * f, consumed: [] });
  }
  for (const g of Object.values(groups)) g.sort((a, b) => cmp(a.date || "", b.date || "") || cmp(String(a.id), String(b.id)));
  const realised = [], problems = [], adjSells = [];
  const ordered = sells.slice().sort((a, b) => cmp(a.date || "", b.date || "") || cmp(String(a.id), String(b.id)));
  for (const s of ordered) {
    const sd = s.date || null, sym = String(s.sym).toUpperCase(), f = splitFactor(splits[sym], sd), q = s.qty * f, p = s.price / f;
    const book = s.book || MAIN;
    adjSells.push({ ...s, sym, book, date: sd, qty: q, price: p, rawQty: s.qty, rawPrice: s.price, factor: f });
    const pool = (groups[book + "|" + sym] || []).filter((l) => l.date == null || sd == null || l.date <= sd);
    const avail = sum(pool.map((l) => l.open));
    if (q > avail + 1e-6) problems.push(`Sell of ${+s.qty.toFixed(4)} ${sym} on ${s.date} is more than the ${+(f ? avail / f : avail).toFixed(4)} shares held then. Check the sell or add the missing buy.`);
    let rem = Math.min(q, avail);
    for (const l of pool) {
      if (rem <= 1e-9) break;
      const take = Math.min(l.open, rem);
      if (take <= 1e-9) continue;
      l.open -= take; l.consumed.push([sd, take]); rem -= take;
      const longTerm = Boolean(l.date && sd && sd > addMonths(l.date, 12));
      realised.push({ sym, book, sellId: s.id, sellDate: sd, buyDate: l.date, qty: take, buyPrice: l.price, sellPrice: p, cost: take * l.price, proceeds: take * p, gain: take * (p - l.price),
                      days: l.date && sd ? daysBetween(l.date, sd) : null, term: longTerm ? "LT" : "ST", fy: sd ? fy(sd) : "—" });
    }
  }
  return { alots: Object.values(groups).flat(), realised, problems, adjSells };
}
// quotes: {SYM: {price, prev}}   plans: {"Book|SYM": {stop, target}}   sectors: {SYM: name}   techs: {SYM: tech()}
export function aggregate(alots, quotes, plans = {}, sectors = {}, techs = {}, bookFilter = ALL) {
  const by = {};
  for (const l of alots) if (l.open > 1e-9) (by[l.sym] = by[l.sym] || []).push(l);
  const out = [];
  for (const [sym, ls] of Object.entries(by)) {
    const qty = sum(ls.map((l) => l.open)), invested = sum(ls.map((l) => l.open * l.price)), q = quotes[sym] || {};
    const ltp = q.price ?? null, prev = q.prev ?? null, value = ltp != null ? qty * ltp : null;
    const books = [...new Set(ls.map((l) => l.book))].sort(), planBook = books.length === 1 ? books[0] : bookFilter !== ALL ? bookFilter : books[0], plan = plans[planBook + "|" + sym] || {};
    const sl = plan.stop ?? null, tgt = plan.target ?? null, avg = qty ? invested / qty : 0;
    const dates = ls.map((l) => l.date).filter(Boolean).sort();
    out.push({ sym, books, planBook, name: q.name || "", sector: sectors[sym] || "Unclassified", qty, avg, invested, ltp, value, chg: ltp != null && prev ? (ltp / prev - 1) * 100 : null,
      pnl: value != null ? value - invested : null, pnlPct: value != null && invested ? (value / invested - 1) * 100 : null, dayPnl: ltp != null && prev ? qty * (ltp - prev) : null,
      lots: ls.slice().sort((a, b) => cmp(a.date || "", b.date || "")), splitAdj: ls.some((l) => l.factor !== 1), sl, target: tgt,
      risk: sl != null && ltp != null && ltp > sl ? qty * (ltp - sl) : sl != null && ltp != null ? 0 : null, reward: tgt != null && ltp != null && tgt > ltp ? qty * (tgt - ltp) : null,
      rMult: sl != null && ltp != null && avg > sl ? (ltp - avg) / (avg - sl) : null, slHit: sl != null && ltp != null && ltp <= sl, tech: techs[sym] || null, firstDate: dates[0] || null });
  }
  const total = sum(out.filter((h) => h.value != null).map((h) => h.value));
  for (const h of out) h.weight = h.value != null && total ? (h.value / total) * 100 : null;
  return out.sort((a, b) => (a.value == null) - (b.value == null) || (b.value || 0) - (a.value || 0));
}
export function totals(holds, alots) {
  const valid = holds.filter((h) => h.value != null), inv = sum(holds.map((h) => h.invested)), invV = sum(valid.map((h) => h.invested)), val = sum(valid.map((h) => h.value));
  const day = sum(valid.filter((h) => h.dayPnl != null).map((h) => h.dayPnl));
  const den = sum(holds.filter((h) => h.value).map((h) => h.value)), anyStop = holds.some((h) => h.sl != null);
  return { inv, val, pnl: val - invV, pnlPct: invV ? (val / invV - 1) * 100 : null, day, dayPct: val - day ? (day / (val - day)) * 100 : null, n: holds.length, nValid: valid.length,
           nLots: alots.filter((l) => l.open > 1e-9).length, heat: den && anyStop ? (sum(holds.filter((h) => h.sl != null).map((h) => h.risk || 0)) / den) * 100 : null };
}
export function dividends(alots, divs, today) {            // gross dividends received per buy, from the dividend history
  const rows = [];
  for (const l of alots) {
    if (l.date == null) continue;
    for (const [d, amt] of divs[l.sym] || []) {
      if (d <= l.date || d > today) continue;
      const held = l.qty - sum(l.consumed.filter(([sd]) => sd != null && sd < d).map(([, q]) => q));
      if (held > 1e-9) rows.push({ sym: l.sym, exDate: d, perShare: amt, qty: held, amount: held * amt, fy: fy(d) });
    }
  }
  return rows;
}

// ── performance ──
export function xirrOf(flows) {                            // flows: [[date, amount]]  money out negative, money in positive
  flows = flows.filter(([, a]) => a);
  if (flows.length < 2 || !flows.some(([, a]) => a > 0) || !flows.some(([, a]) => a < 0)) return null;
  const t0 = flows.map(([d]) => d).sort()[0], npv = (r) => sum(flows.map(([d, a]) => a / Math.pow(1 + r, daysBetween(t0, d) / 365)));
  let lo = -0.99, hi = 20, fLo = npv(lo); const fHi = npv(hi);
  if (!isFinite(fLo) || !isFinite(fHi) || fLo * fHi > 0) return null;
  for (let i = 0; i < 140; i++) { const mid = (lo + hi) / 2, fMid = npv(mid); if (fLo * fMid <= 0) hi = mid; else { lo = mid; fLo = fMid; } }
  return (lo + hi) / 2;
}
export function portfolioXirr(alots, adjSells, holds, today) {
  const flows = alots.filter((l) => l.date).map((l) => [l.date, -l.qty * l.price]).concat(adjSells.filter((s) => s.date).map((s) => [s.date, s.qty * s.price]));
  if (!flows.length) return { xirr: null, days: 0 };
  const span = daysBetween(flows.map(([d]) => d).sort()[0], today);
  if (span < 60) return { xirr: null, days: span };
  flows.push([today, sum(holds.filter((h) => h.value != null).map((h) => h.value))]);
  return { xirr: xirrOf(flows), days: span };
}
// closes: {SYM: {d: [dates], c: [closes]}}   bench: {d, c} or null
// -> {d, value, flow, net, r, bench} : daily holdings value, money put in, and an index bought and sold with the same cash
export function timeline(alots, adjSells, closes, bench) {
  const dated = alots.filter((l) => l.date != null && closes[l.sym]);
  if (!dated.length) return null;
  const start = dated.map((l) => l.date).sort()[0], syms = [...new Set(dated.map((l) => l.sym))].sort();
  const pre = new Date(ts(start) - 7 * DAY).toISOString().slice(0, 10);
  const all = [...new Set(syms.flatMap((s) => closes[s].d))].sort().filter((d) => d >= pre), idx = all.filter((d) => d >= start);
  if (idx.length < 2) return null;
  const px = {};                                           // forward-fill, then back-fill, on the window that starts a week early
  for (const s of syms) {
    const m = new Map(closes[s].d.map((d, i) => [d, closes[s].c[i]])), col = all.map((d) => (m.has(d) && m.get(d) != null ? m.get(d) : null));
    for (let i = 1; i < col.length; i++) if (col[i] == null) col[i] = col[i - 1];
    for (let i = col.length - 2; i >= 0; i--) if (col[i] == null) col[i] = col[i + 1];
    px[s] = col.slice(all.length - idx.length);
  }
  const n = idx.length, qty = Object.fromEntries(syms.map((s) => [s, new Array(n).fill(0)])), flow = new Array(n).fill(0);
  const snap = (d) => { let lo = 0, hi = n; while (lo < hi) { const mid = (lo + hi) >> 1; if (idx[mid] < d) lo = mid + 1; else hi = mid; } return lo < n ? lo : -1; };
  for (const l of dated) { const t = snap(l.date); if (t < 0) continue; for (let i = t; i < n; i++) qty[l.sym][i] += l.qty; flow[t] += l.qty * l.price; }
  for (const s of adjSells) {
    if (s.date == null || !syms.includes(s.sym)) continue;
    const t = snap(s.date); if (t < 0) continue;
    const q = Math.min(s.qty, Math.max(0, qty[s.sym][t]));
    for (let i = t; i < n; i++) qty[s.sym][i] -= q; flow[t] -= q * s.price;
  }
  const value = idx.map((_, i) => sum(syms.map((s) => qty[s][i] * (px[s][i] ?? 0)))), net = [], r = [];
  let run = 0;
  for (let i = 0; i < n; i++) {
    run += flow[i]; net.push(run);
    const prev = i ? value[i - 1] : 0, denom = prev + Math.max(flow[i], 0);
    r.push(denom ? Math.max(-0.5, Math.min(0.5, (value[i] - prev - flow[i]) / denom)) : 0);
  }
  let benchV = null;
  if (bench && bench.d.length) {
    const b = new Array(n).fill(null); let j = 0, last = null;   // align: last close on or before each day, back-filled at the start
    for (let i = 0; i < n; i++) { while (j < bench.d.length && bench.d[j] <= idx[i]) { if (bench.c[j] != null) last = bench.c[j]; j++; } b[i] = last; }
    for (let i = n - 2; i >= 0; i--) if (b[i] == null) b[i] = b[i + 1];
    let units = 0; benchV = [];
    for (let i = 0; i < n; i++) { units += b[i] ? flow[i] / b[i] : 0; benchV.push(Math.max(0, units) * (b[i] || 0)); }
  }
  return { d: idx, value, flow, net, r, bench: benchV };
}
export function monthlyReturns(tl) {                       // {"2026-11": percent}
  const out = {};
  tl.d.forEach((d, i) => { const k = d.slice(0, 7); out[k] = (out[k] ?? 1) * (1 + tl.r[i]); });
  for (const k of Object.keys(out)) out[k] = (out[k] - 1) * 100;
  return out;
}

// ── daily-candle facts a swing trader checks ──
function ewm(a, alpha) { const out = []; let v = null; for (const x of a) { if (x == null || isNaN(x)) { out.push(v); continue; } v = v == null ? x : alpha * x + (1 - alpha) * v; out.push(v); } return out; }
export function tech(h) {                                  // h: {d, c, h, l, v}
  if (!h || !h.c) return null;
  const keep = h.c.map((x, i) => (x != null ? i : -1)).filter((i) => i >= 0);
  if (keep.length < 30) return null;
  const c = keep.map((i) => h.c[i]), hi = keep.map((i) => h.h[i] ?? h.c[i]), lo = keep.map((i) => h.l[i] ?? h.c[i]), d = keep.map((i) => h.d[i]), n = c.length;
  const tr = c.map((_, i) => (i ? Math.max(hi[i] - lo[i], Math.abs(hi[i] - c[i - 1]), Math.abs(lo[i] - c[i - 1])) : hi[i] - lo[i]));
  const diff = c.map((x, i) => (i ? x - c[i - 1] : null));
  const up = ewm(diff.map((x) => (x == null ? null : Math.max(x, 0))), 1 / 14).at(-1), dn = ewm(diff.map((x) => (x == null ? null : Math.max(-x, 0))), 1 / 14).at(-1);
  const out = { close: c[n - 1], atr: ewm(tr, 1 / 14).at(-1), rsi: dn === 0 ? 100 : 100 - 100 / (1 + up / dn), ema10: ewm(c, 2 / 11).at(-1), ema21: ewm(c, 2 / 22).at(-1),
                sma50: n >= 50 ? mean(c.slice(-50)) : null, sma200: n >= 200 ? mean(c.slice(-200)) : null, lo10: Math.min(...lo.slice(-10)), hi20: Math.max(...hi.slice(-20)),
                dates: d.slice(-260), highs: hi.slice(-260), relVol: null };
  if (h.v && n >= 21) { const v = keep.map((i) => h.v[i] || 0), avg = mean(v.slice(-21, -1)); out.relVol = avg ? v[n - 1] / avg : null; }
  return out;
}
export function chandelier(t, since, mult = 3) {            // highest high since the buy, minus 3 x ATR
  if (!t || !t.atr) return null;
  const highs = t.highs.filter((_, i) => since == null || t.dates[i] >= since);
  return highs.length ? Math.max(...highs) - mult * t.atr : null;
}
export function positionSize(capital, riskPct, entry, stop) {
  const per = entry - stop;
  if (!(capital > 0) || !(riskPct > 0) || !(entry > 0) || !(per > 0)) return null;
  const byRisk = Math.floor((capital * riskPct / 100) / per), byCap = Math.floor(capital / entry), qty = Math.max(0, Math.min(byRisk, byCap));
  return { qty, capped: byCap < byRisk, per, value: qty * entry, loss: qty * per, r1: entry + per, r2: entry + 2 * per, r3: entry + 3 * per };
}

// ── shape of the portfolio ──
export function concentration(holds) {
  const w = holds.map((h) => h.weight).filter(Boolean).sort((a, b) => b - a);
  if (!w.length) return null;
  const hhi = sum(w.map((x) => (x / 100) ** 2));
  return { top1: w[0], top3: sum(w.slice(0, 3)), top5: sum(w.slice(0, 5)), hhi, effN: hhi ? 1 / hhi : null, n: w.length };
}
export function groupBy(holds, key) {
  const agg = {};
  for (const h of holds) { const k = h[key] || "Unclassified", a = (agg[k] = agg[k] || { name: k, n: 0, syms: [], invested: 0, investedV: 0, value: 0, day: 0 });
    a.n++; a.syms.push(h.sym); a.invested += h.invested; if (h.value != null) { a.value += h.value; a.investedV += h.invested; } if (h.dayPnl != null) a.day += h.dayPnl; }
  const total = sum(Object.values(agg).map((a) => a.value));
  return Object.values(agg).map((a) => ({ ...a, pnl: a.value - a.investedV, pnlPct: a.investedV ? (a.value / a.investedV - 1) * 100 : null, weight: total ? (a.value / total) * 100 : 0 })).sort((a, b) => b.value - a.value);
}
const variance = (a) => { const m = mean(a); return sum(a.map((x) => (x - m) ** 2)) / (a.length - 1); };
const cov = (a, b) => { const ma = mean(a), mb = mean(b); return sum(a.map((x, i) => (x - ma) * (b[i] - mb))) / (a.length - 1); };
function percentile(a, p) { const s = a.slice().sort((x, y) => x - y), k = ((s.length - 1) * p) / 100, f = Math.floor(k), c = Math.ceil(k); return s[f] + (s[c] - s[f]) * (k - f); }
export function riskMetrics(holds, closes, nifty) {
  const syms = holds.filter((h) => h.value && closes[h.sym]).map((h) => h.sym);
  if (!syms.length) return null;
  let dates = [...new Set(syms.flatMap((s) => closes[s].d))].sort();
  const px = {};
  for (const s of syms) { const m = new Map(closes[s].d.map((d, i) => [d, closes[s].c[i]])); let last = null; px[s] = dates.map((d) => { const v = m.get(d); if (v != null) last = v; return last; }); }
  const cut = Math.max(0, dates.length - 300); dates = dates.slice(cut);
  for (const s of syms) px[s] = px[s].slice(cut);
  let rd = dates.slice(1); const rets = {};
  for (const s of syms) rets[s] = rd.map((_, i) => (px[s][i] != null && px[s][i + 1] != null && px[s][i] !== 0 ? px[s][i + 1] / px[s][i] - 1 : null));
  const c2 = Math.max(0, rd.length - 252); rd = rd.slice(c2); for (const s of syms) rets[s] = rets[s].slice(c2);
  if (rd.length < 60) return null;
  const wRaw = Object.fromEntries(holds.filter((h) => syms.includes(h.sym)).map((h) => [h.sym, h.value])), wTot = sum(Object.values(wRaw));
  const port = [], pd = [];
  rd.forEach((d, i) => { let ws = 0, v = 0; for (const s of syms) { const r = rets[s][i]; if (r != null) { ws += wRaw[s] / wTot; v += r * (wRaw[s] / wTot); } } if (ws > 0) { port.push(v / ws); pd.push(d); } });
  if (port.length < 60) return null;
  const totalVal = sum(holds.filter((h) => h.value).map((h) => h.value));
  let cum = 1, peak = 0, mdd = 0; for (const r of port) { cum *= 1 + r; peak = Math.max(peak, cum); mdd = Math.min(mdd, cum / peak - 1); }
  const out = { nDays: port.length, vol: Math.sqrt(variance(port)) * Math.sqrt(252) * 100, mdd: mdd * 100, ret1y: (cum - 1) * 100, var95: -percentile(port, 5) * totalVal,
                beta: null, benchRet: null, avgCorr: null, topPair: null, betas: {}, corr: null };
  if (nifty && nifty.d.length > 1) {
    const nr = new Map(); for (let i = 1; i < nifty.d.length; i++) if (nifty.c[i] != null && nifty.c[i - 1]) nr.set(nifty.d[i], nifty.c[i] / nifty.c[i - 1] - 1);
    const join = (ds, vs) => { const a = [], b = []; ds.forEach((d, i) => { if (vs[i] != null && nr.has(d)) { a.push(vs[i]); b.push(nr.get(d)); } }); return [a, b]; };
    const [p, m] = join(pd, port); if (p.length >= 60 && variance(m) > 0) out.beta = cov(p, m) / variance(m);
    for (const s of syms) { const [a, b] = join(rd, rets[s]); if (a.length >= 60 && variance(b) > 0) out.betas[s] = cov(a, b) / variance(b); }
    const win = nifty.d.map((d, i) => [d, nifty.c[i]]).filter(([d, c]) => d >= pd[0] && d <= pd[pd.length - 1] && c != null);
    if (win.length > 10) out.benchRet = (win[win.length - 1][1] / win[0][1] - 1) * 100;
  }
  const top = holds.filter((h) => h.value && rets[h.sym]).sort((a, b) => b.value - a.value).slice(0, 12).map((h) => h.sym);
  if (top.length >= 2) {
    const pairs = [], mat = top.map((_, i) => top.map((_, j) => (i === j ? 1 : null)));
    for (let i = 0; i < top.length; i++) for (let j = i + 1; j < top.length; j++) {
      const a = [], b = []; rd.forEach((_, k) => { const x = rets[top[i]][k], y = rets[top[j]][k]; if (x != null && y != null) { a.push(x); b.push(y); } });
      if (a.length >= 60 && variance(a) > 0 && variance(b) > 0) { const c = cov(a, b) / Math.sqrt(variance(a) * variance(b)); pairs.push([top[i], top[j], c]); mat[i][j] = mat[j][i] = c; }
    }
    out.corr = { syms: top, m: mat };
    if (pairs.length) { out.avgCorr = mean(pairs.map((x) => x[2])); out.topPair = pairs.reduce((m, x) => (x[2] > m[2] ? x : m)); }
  }
  return out;
}
export function diversificationScore(conc, sectors, risk) {
  if (!conc) return null;
  const known = sectors.filter((s) => s.name !== "Unclassified");
  let pen = Math.min(30, Math.max(0, conc.top1 - 10) * 1.2);
  if (known.length) pen += Math.min(30, Math.max(0, known[0].weight - 25) * 1.0);
  pen += Math.min(25, Math.max(0, 10 - (conc.effN || 1)) * 3);
  if (risk && risk.avgCorr != null) pen += Math.min(15, Math.max(0, risk.avgCorr - 0.5) * 60);
  return Math.round(Math.max(0, 100 - pen));               // note: Python rounds halves to even; a x.5 score can differ by one
}

// ── tax and the trade journal ──
export function taxSummary(realised, rates = TAX_DEFAULTS) { // Indian listed-equity capital gains by financial year (no surcharge, no carry-forward)
  const by = {};
  for (const r of realised) {
    const a = (by[r.fy] = by[r.fy] || { stGain: 0, stLoss: 0, ltGain: 0, ltLoss: 0, proceeds: 0, n: 0 });
    a.n++; a.proceeds += r.proceeds;
    const k = r.term === "ST" ? "st" : "lt";
    if (r.gain >= 0) a[k + "Gain"] += r.gain; else a[k + "Loss"] += -r.gain;
  }
  const out = {};
  for (const f of Object.keys(by).sort()) {
    const a = by[f], stSet = Math.min(a.stGain, a.stLoss), leftStl = a.stLoss - stSet, ltNet = a.ltGain - a.ltLoss - leftStl, stNet = Math.max(a.stGain - stSet, 0);
    const ltTaxable = ltNet > 0 ? Math.max(0, ltNet - rates.exempt) : 0;
    out[f] = { ...a, stNet, ltNet, ltTaxable, tax: ((stNet * rates.stcg + ltTaxable * rates.ltcg) / 100) * (1 + rates.cess / 100), exemptLeft: Math.max(0, rates.exempt - Math.max(ltNet, 0)), carryLoss: Math.max(0, -ltNet) };
  }
  return out;
}
export function journal(realised) {                        // each matched buy is one trade
  const tr = realised.filter((x) => x.cost);
  if (!tr.length) return null;
  const rets = tr.map((x) => (x.gain / x.cost) * 100), wins = tr.map((x, i) => [x, rets[i]]).filter(([x]) => x.gain > 0), loss = tr.map((x, i) => [x, rets[i]]).filter(([x]) => x.gain <= 0);
  const gw = sum(wins.map(([x]) => x.gain)), gl = -sum(loss.map(([x]) => x.gain)), aw = wins.length ? mean(wins.map(([, r]) => r)) : 0, al = loss.length ? mean(loss.map(([, r]) => r)) : 0;
  const days = (rows) => { const d = rows.map(([x]) => x.days).filter((v) => v != null); return rows.length ? sum(d) / Math.max(1, d.length) : null; };
  let streak = 0, worst = 0;
  for (const x of tr.slice().sort((a, b) => cmp(a.sellDate || "", b.sellDate || ""))) { streak = x.gain <= 0 ? streak + 1 : 0; worst = Math.max(worst, streak); }
  const months = {};
  for (const x of tr) if (x.sellDate) { const k = x.sellDate.slice(0, 7), m = (months[k] = months[k] || [0, 0, 0]); m[0]++; m[1] += x.gain > 0 ? 1 : 0; m[2] += x.gain; }
  return { n: tr.length, win: (wins.length / tr.length) * 100, avgWin: aw, avgLoss: al, payoff: al ? aw / Math.abs(al) : null, exp: mean(rets), pf: gl > 0 ? gw / gl : null,
           daysWin: days(wins), daysLoss: days(loss), best: Math.max(...rets), worst: Math.min(...rets), streak: worst, net: gw - gl, months };
}

// ── what needs attention (the checks that need no fundamentals or news) ──
export function flags(holds, T, conc, sectors, risk, problems) {
  const out = [], add = (level, title, detail) => out.push({ level, title, detail }), n = T.n, weighted = holds.filter((h) => h.weight);
  if (conc && weighted.length) {
    const top = weighted.reduce((m, h) => (h.weight > m.weight ? h : m)), t = `${top.sym} is ${top.weight.toFixed(1)}% of the portfolio`;
    if (conc.top1 > 25) add("alert", "Single-stock concentration", t + ". One stock this large can dominate your results. Check this is intentional.");
    else if (conc.top1 > 15) add("watch", "Large top position", t + ". Comfortable for high-conviction ideas, but worth a periodic review.");
    else add("ok", "Position sizing looks balanced", t + ", so no single stock dominates.");
    if (conc.top5 > 70 && n > 5) add("watch", "Top-5 heavy", `Your five largest positions make up ${conc.top5.toFixed(0)}% of the value.`);
    if (n >= 5 && conc.effN && conc.effN < 0.5 * n) add("watch", "Lopsided weights", `You hold ${n} stocks but they behave like about ${conc.effN.toFixed(1)} equal-sized positions.`);
  }
  if (n < 5) add("alert", "Very few holdings", `Only ${n} stock${n !== 1 ? "s" : ""}. Company-specific events will move the whole portfolio.`);
  else if (n < 10) add("watch", "Compact portfolio", `${n} holdings. Fine if deliberate; spread across sectors matters more at this size.`);
  else if (n > 30) add("info", "Many holdings", `${n} stocks can be hard to track closely; small positions barely move results.`);
  const known = sectors.filter((s) => s.name !== "Unclassified");
  if (known.length) {
    const s0 = known[0], t = `${s0.name} is ${s0.weight.toFixed(1)}% of the portfolio (${s0.n} stock${s0.n !== 1 ? "s" : ""})`;
    if (s0.weight > 40) add("alert", "Sector concentration", t + ". A sector-wide event would hit most of your money at once.");
    else if (s0.weight > 30) add("watch", "Sector tilt", t + ". Not necessarily wrong, just a deliberate bet to be aware of.");
    else add("ok", "Sector spread looks healthy", t + ", your largest sector.");
  }
  const unk = holds.filter((h) => h.sector === "Unclassified").map((h) => h.sym);
  if (unk.length) add("info", "Sector not known", "Could not classify: " + unk.slice(0, 8).join(", ") + (unk.length > 8 ? " …" : "") + ".");
  const hit = holds.filter((h) => h.slHit).map((h) => h.sym);
  if (hit.length) add("alert", "Stop-loss breached", "Price is at or below your stop-loss for: " + hit.join(", ") + ".");
  const totV = T.val || 0, uncovered = sum(holds.filter((h) => h.value && h.sl == null).map((h) => h.value));
  if (totV && uncovered / totV > 0.5 && holds.some((h) => h.sl != null)) add("info", "Stops on part of the portfolio", `${((uncovered / totV) * 100).toFixed(0)}% of value has no stop-loss set.`);
  if (risk) {
    if (risk.topPair && risk.topPair[2] > 0.8) add("watch", "Highly correlated pair", `${risk.topPair[0]} and ${risk.topPair[1]} have moved almost together (correlation ${risk.topPair[2].toFixed(2)}).`);
    if (risk.avgCorr != null && risk.avgCorr > 0.6) add("watch", "Holdings move together", `Average correlation between your top holdings is ${risk.avgCorr.toFixed(2)}.`);
    if (risk.mdd < -25) add("watch", "Deep drawdown", `At today's weights the portfolio fell ${Math.abs(risk.mdd).toFixed(0)}% peak-to-trough in the last year.`);
    if (risk.beta != null && risk.beta > 1.3) add("watch", "High market sensitivity", `Beta vs NIFTY 50 is ${risk.beta.toFixed(2)}: expect larger swings than the index.`);
    else if (risk.beta != null && risk.beta < 0.7) add("info", "Defensive profile", `Beta vs NIFTY 50 is ${risk.beta.toFixed(2)}: the portfolio moves less than the index.`);
  }
  const losers = holds.filter((h) => h.pnlPct != null && h.pnlPct <= -20).sort((a, b) => a.pnlPct - b.pnlPct);
  if (losers.length) add("watch", "Deep losers", `${losers.length} position${losers.length !== 1 ? "s are" : " is"} down 20% or more: ` + losers.slice(0, 5).map((h) => `${h.sym} (${h.pnlPct.toFixed(0)}%)`).join(", ") + ".");
  if (T.nValid < n) add("watch", "Missing prices", `${n - T.nValid} holding(s) have no live price and are excluded from totals.`);
  if (problems && problems.length) add("alert", "Transaction data needs attention", problems[0] + (problems.length > 1 ? ` (+${problems.length - 1} more, see Transactions)` : ""));
  const adj = holds.filter((h) => h.splitAdj).map((h) => h.sym);
  if (adj.length) add("info", "Adjusted for splits / bonus", "Quantities and buy prices adjusted automatically for: " + adj.slice(0, 8).join(", ") + ".");
  const order = { alert: 0, watch: 1, info: 2, ok: 3 };
  return out.sort((a, b) => order[a.level] - order[b.level]);
}

// ── rebalance, tax preview, stress ──
export const REB_DEFAULTS = { maxStock: 15, maxSector: 30, redeploy: true, cash: 0 };
export function rebalancePlan(holds, rules) {              // trim what is above the caps; optionally put the freed cash into holdings with room
  const H = holds.filter((h) => h.value && h.ltp);
  if (!H.length) return { rows: [], left: 0 };
  const cashNew = Math.max(0, +rules.cash || 0), total = sum(H.map((h) => h.value)) + cashNew, capS = rules.maxStock / 100, capSec = rules.maxSector / 100;
  const tgt = Object.fromEntries(H.map((h) => [h.sym, h.value])), sec = Object.fromEntries(H.map((h) => [h.sym, h.sector])), U = "Unclassified";
  for (let i = 0; i < 60; i++) {
    let changed = false;
    for (const s in tgt) if (tgt[s] > capS * total + 1e-6) { tgt[s] = capS * total; changed = true; }
    const sums = {};
    for (const s in tgt) if (sec[s] !== U) sums[sec[s]] = (sums[sec[s]] || 0) + tgt[s];
    for (const name in sums) if (sums[name] > capSec * total + 1e-6) { const k = (capSec * total) / sums[name]; for (const s in tgt) if (sec[s] === name) tgt[s] *= k; changed = true; }
    if (!changed) break;
  }
  let pool = total - sum(Object.values(tgt));
  if (rules.redeploy) for (let i = 0; i < 60; i++) {
    if (pool <= 1) break;
    const sums = {}, room = {};
    for (const s in tgt) sums[sec[s]] = (sums[sec[s]] || 0) + tgt[s];
    for (const s in tgt) { const r1 = capS * total - tgt[s], r2 = sec[s] !== U ? capSec * total - sums[sec[s]] : r1, r = Math.min(r1, r2); if (r > 1) room[s] = r; }
    if (!Object.keys(room).length) break;
    const base = sum(Object.keys(room).map((s) => tgt[s])) || 1; let given = 0;
    for (const s in room) { const add = Math.min(room[s], (pool * tgt[s]) / base); tgt[s] += add; given += add; }
    pool -= given;
    if (given < 1) break;
  }
  const rows = [];
  for (const h of H) {
    const dv = tgt[h.sym] - h.value; let q = dv > 0 ? Math.floor(Math.abs(dv) / h.ltp) : Math.ceil(Math.abs(dv) / h.ltp);
    if (dv < 0) q = Math.min(q, Math.floor(h.qty));
    if (q >= 1 && Math.abs(dv) >= Math.max(500, 0.002 * total))
      rows.push({ sym: h.sym, sector: h.sector, nowW: (h.value / total) * 100, newW: ((h.value + (dv > 0 ? q : -q) * h.ltp) / total) * 100, action: dv > 0 ? "BUY" : "SELL", qty: q, amount: q * h.ltp, ltp: h.ltp, h });
  }
  return { rows: rows.sort((a, b) => (a.action !== "SELL") - (b.action !== "SELL") || b.amount - a.amount), left: pool };
}
export function fifoPreview(h, qty, price, today) {         // gain, split short and long term, if qty shares were sold today
  let rem = qty, st = 0, lt = 0;
  for (const l of h.lots) { if (rem <= 1e-9) break; const take = Math.min(l.open, rem), g = take * (price - l.price); if (l.date && today > addMonths(l.date, 12)) lt += g; else st += g; rem -= take; }
  return { st, lt };
}
export function unrealisedByTerm(alots, quotes, today) {
  let st = 0, lt = 0; const soon = [];
  for (const l of alots) {
    if (l.open <= 1e-9) continue;
    const ltp = (quotes[l.sym] || {}).price; if (ltp == null) continue;
    const g = l.open * (ltp - l.price), on = l.date ? addMonths(l.date, 12) : null;
    if (on && today > on) lt += g; else { st += g; if (on && daysBetween(today, on) <= 60 && g > 0) soon.push({ on, lot: l, gain: g, days: daysBetween(today, on) }); }
  }
  return { st, lt, soon: soon.sort((a, b) => cmp(a.on, b.on)) };
}
export function stress(holds, betas, mkt, sector, shock) {  // linear estimate from each stock's beta to NIFTY 50
  const rows = [];
  for (const h of holds) { if (!h.value) continue; const b = betas[h.sym] ?? 1, move = b * mkt + (sector && h.sector === sector ? shock : 0); rows.push({ h, beta: b, move, impact: (h.value * move) / 100 }); }
  return { rows: rows.sort((a, b) => a.impact - b.impact), total: sum(rows.map((r) => r.impact)), noBeta: holds.filter((h) => h.value && betas[h.sym] == null).length };
}
export function worstMoves(nifty) {                        // worst NIFTY day (last 1,250 sessions) and worst calendar month (last 60)
  if (!nifty || nifty.d.length < 30) return null;
  const d = [], c = []; nifty.d.forEach((x, i) => { if (nifty.c[i] != null) { d.push(x); c.push(nifty.c[i]); } });
  let day = null;
  for (let i = Math.max(1, d.length - 1250); i < d.length; i++) { const r = c[i] / c[i - 1] - 1; if (!day || r < day.r) day = { r, when: d[i] }; }
  const ends = []; d.forEach((x, i) => { const k = x.slice(0, 7); if (ends.length && ends[ends.length - 1].k === k) ends[ends.length - 1].c = c[i]; else ends.push({ k, c: c[i] }); });
  let month = null;
  for (let i = Math.max(1, ends.length - 60); i < ends.length; i++) { const r = ends[i].c / ends[i - 1].c - 1; if (!month || r < month.r) month = { r, when: ends[i].k }; }
  return { day, month };
}
export function twrStats(tl, benchClose) {                  // time-weighted return of the portfolio and the price return of the index over the same days
  const n = tl.d.length, twr = (tl.r.reduce((a, r) => a * (1 + r), 1) - 1) * 100; let b = null, idx = null, bIdx = null;
  idx = []; let v = 100; for (const r of tl.r) { v *= 1 + r; idx.push(v); }
  if (benchClose && benchClose.d.length) {
    const al = []; let j = 0, last = null;
    for (let i = 0; i < n; i++) { while (j < benchClose.d.length && benchClose.d[j] <= tl.d[i]) { if (benchClose.c[j] != null) last = benchClose.c[j]; j++; } al.push(last); }
    for (let i = n - 2; i >= 0; i--) if (al[i] == null) al[i] = al[i + 1];
    if (al[0]) { b = (al[n - 1] / al[0] - 1) * 100; bIdx = al.map((x) => (x / al[0]) * 100); }
  }
  return { days: n, twr, ann: n > 250 ? (Math.pow(1 + twr / 100, 252 / n) - 1) * 100 : null, bench: b, idx, bIdx, benchMonthly: bIdx ? monthlyFromIndex(tl.d, bIdx) : null };
}
function monthlyFromIndex(d, idx) { const out = {}; d.forEach((x, i) => { const k = x.slice(0, 7), r = i ? idx[i] / idx[i - 1] : 1; out[k] = (out[k] ?? 1) * r; }); for (const k in out) out[k] = (out[k] - 1) * 100; return out; }
export function contributions(holds, realised, divs) {      // unrealised + realised + dividends, per stock
  const c = {};
  for (const h of holds) c[h.sym] = (c[h.sym] || 0) + (h.pnl || 0);
  for (const x of realised) c[x.sym] = (c[x.sym] || 0) + x.gain;
  for (const d of divs) c[d.sym] = (c[d.sym] || 0) + d.amount;
  return Object.entries(c).sort((a, b) => b[1] - a[1]);
}

// Arka Trades · Arka AI engine: a port of the deterministic part of arka_ai.py (sections 4 and 6): indicators, swing points,
// support / resistance, supply and demand zones, trendlines, events, candle signals, structure, the walk-forward track record,
// the engine plan and score, what to draw, and the checks that make the AI's reply exact (validate_ai).
// Pure functions on plain arrays, no page code, so every number can be checked against the Python on the same candles.
// A frame is { d: ["YYYY-MM-DD", …], o, h, l, c, v } (oldest first), prices as traded: split-adjusted, not dividend-adjusted.
import { rollMean, ewm, rsiOf, atrOf, npSum } from "./btengine.js";
import { fx } from "./rsengine.js";

export const DEFAULT_STYLE = { pivot_left: 3, pivot_right: 3, base_max: 4, base_range_atr: 1.0, impulse_atr: 1.6, impulse_window: 3,
  max_zone_atr: 2.2, min_quality: 40, sr_tol_atr: 0.6, sr_min_touches: 2, lookback: 220, fresh_only: false };
export const STYLE_HELP = {
  base_max: "Maximum candles in a base (consolidation) before the move",
  base_range_atr: "Largest candle range allowed inside a base, in ATR",
  impulse_atr: "How far price must leave the base, in ATR, to count as an impulse",
  max_zone_atr: "Thickest zone you accept, in ATR (thinner = tighter risk)",
  min_quality: "Hide zones scoring below this quality (0-100)",
  sr_tol_atr: "Prices within this many ATR are treated as one support/resistance level",
  sr_min_touches: "Touches needed before a level is drawn",
};
export const SYMBOL_MAP = { NIFTY50: "^NSEI", NIFTY: "^NSEI", BANKNIFTY: "^NSEBANK", NIFTYBANK: "^NSEBANK", SENSEX: "^BSESN", FINNIFTY: "NIFTY_FIN_SERVICE.NS", NIFTYIT: "^CNXIT" };

// ── Python numbers ──
const fin = (v) => typeof v === "number" && isFinite(v);
export const pr = (v, d = 2) => (fin(v) ? Number(fx(v, d, false)) : v);                  // Python round(x, d): the exact value, ties to even
export function nr(v, d = 2) {                                                            // numpy round(x, d): rint(x * 10^d) / 10^d
  if (!fin(v)) return v; const f = 10 ** d, y = v * f, r = Math.round(y); return (Math.abs(y % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r) / f;
}
export const pyRound = (v) => { const r = Math.round(v); return Math.abs(v % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r; };   // round(x) → int, ties to even
export const _f = (x, nd = 2) => (x == null || typeof x === "boolean" ? (x == null ? null : pr(+x, nd)) : fin(+x) ? pr(+x, nd) : null);
export function pyFloorDiv(a, b) {                                                        // float // float, as CPython computes it
  const mod = a % b; let div = (a - mod) / b; if (mod && (b < 0) !== (mod < 0)) div -= 1;
  let fl = Math.floor(div); if (div - fl > 0.5) fl += 1; return fl;
}
export function pyRepr(v) {                                                               // str(float): 2.0 → "2.0", 0.1 → "0.1"
  if (typeof v !== "number") return String(v);
  if (!isFinite(v)) return v !== v ? "nan" : v > 0 ? "inf" : "-inf";
  if (Number.isInteger(v) && Math.abs(v) < 1e16) return v.toFixed(1);
  const s = String(v); return /e/.test(s) ? s.replace(/e([+-])(\d)$/, "e$10$2") : s;
}
const median = (a) => { const s = Float64Array.from(a).sort(), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
export function percentile(a, q) {                                                        // numpy percentile, method "linear" (numpy's own lerp)
  const s = Float64Array.from(a).sort(), n = s.length, vi = (n - 1) * (q / 100), lo = Math.floor(vi), hi = Math.min(lo + 1, n - 1), t = vi - lo;
  const A = s[lo], B = s[hi], diff = B - A; return t >= 0.5 ? B - diff * (1 - t) : A + diff * t;
}
export function pySum(a) {                                 // sum() of floats in Python 3.12+: compensated (Neumaier)
  let f = 0.0, c = 0.0;
  for (const x of a) { const t = f + x; if (Math.abs(f) >= Math.abs(x)) c += (f - t) + x; else c += (x - t) + f; f = t; }
  return c && isFinite(c) ? f + c : f;
}
const npMean = (a) => npSum(Float64Array.from(a)) / a.length;
const argMax = (x, a, b) => { let k = a; for (let i = a + 1; i <= b; i++) if (x[i] > x[k]) k = i; return k; };
const argMin = (x, a, b) => { let k = a; for (let i = a + 1; i <= b; i++) if (x[i] < x[k]) k = i; return k; };
const mx = (x, a, b) => x[argMax(x, a, b)], mn = (x, a, b) => x[argMin(x, a, b)];
export const title = (s) => String(s).toLowerCase().replace(/(^|[^a-z])([a-z])/g, (m, p, ch) => p + ch.toUpperCase());       // str.title()
export const cap = (s) => { s = String(s); return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase(); };                    // str.capitalize()

// ── market data helpers ──
export function yahooSymbols(ticker) {
  const t = String(ticker || "").toUpperCase().trim().replace(/ /g, "");
  if (SYMBOL_MAP[t]) return [SYMBOL_MAP[t]];
  if (t.startsWith("^") || t.endsWith(".NS") || t.endsWith(".BO")) return [t];
  return [t + ".NS", t + ".BO"];
}
export const isIndex = (ticker) => { const t = String(ticker || "").toUpperCase().replace(/ /g, ""); return Boolean(SYMBOL_MAP[t]) || t.startsWith("^"); };
export function cleanFrame(h) {                                                           // _clean_ohlc: one row per day, rows with a missing price dropped
  if (!h || !h.ok) return null;
  const o = { d: [], o: [], h: [], l: [], c: [], v: [] };
  for (let k = 0; k < h.d.length; k++) { const r = [h.o[k], h.h[k], h.l[k], h.c[k]]; if (r.some((x) => x == null || !isFinite(x))) continue;
    o.d.push(h.d[k]); o.o.push(+r[0]); o.h.push(+r[1]); o.l.push(+r[2]); o.c.push(+r[3]); o.v.push(h.v[k] == null ? 0 : +h.v[k]); }
  return o;
}
export function weeklyFromDaily(df) {                                                     // resample("W-FRI"): weeks end on Friday
  const out = { d: [], o: [], h: [], l: [], c: [], v: [] }; let cur = null;
  for (let i = 0; i < df.d.length; i++) {
    const t = new Date(df.d[i] + "T00:00:00Z"), wd = (t.getUTCDay() + 6) % 7, fri = new Date(t.getTime() + ((4 - wd + 7) % 7) * 864e5).toISOString().slice(0, 10);
    if (cur !== fri) { cur = fri; out.d.push(fri); out.o.push(df.o[i]); out.h.push(df.h[i]); out.l.push(df.l[i]); out.c.push(df.c[i]); out.v.push(df.v[i]); continue; }
    const k = out.d.length - 1; if (df.h[i] > out.h[k]) out.h[k] = df.h[i]; if (df.l[i] < out.l[k]) out.l[k] = df.l[i]; out.c[k] = df.c[i]; out.v[k] += df.v[i];
  }
  return out;
}
export function liveFraction(lastDate, now = new Date()) {                                // share of today's session already traded, when the last candle is today's
  const t = new Date(now.getTime() + 330 * 60000), mins = t.getUTCHours() * 60 + t.getUTCMinutes(), wd = (t.getUTCDay() + 6) % 7;
  if (wd >= 5 || String(lastDate).slice(0, 10) !== t.toISOString().slice(0, 10) || !(555 <= mins && mins < 930)) return null;
  return Math.max(0.15, Math.min(1.0, (mins - 555) / 375.0));
}

// ── indicators ──
export function prepare(df) {
  const n = df.c.length, c = Float64Array.from(df.c), v = Float64Array.from(df.v);
  return { n, atr: atrOf(Float64Array.from(df.h), Float64Array.from(df.l), c), rsi: rsiOf(c), sma20: rollMean(c, 20, 20), sma50: rollMean(c, 50, 50), sma200: rollMean(c, 200, 200), vavg: rollMean(v, 20, 5) };
}
export function findPivots(df, left = 3, right = 3) {
  const h = df.h, l = df.l, n = h.length, ph = [], pl = [];
  for (let i = left; i < n - right; i++) {
    if (argMax(h, i - left, i + right) === i) ph.push([i, h[i]]);             // h[i] is the window's first maximum
    if (argMin(l, i - left, i + right) === i) pl.push([i, l[i]]);
  }
  return [ph, pl];
}

// ── support / resistance ──
export function buildLevels(df, ph, pl, atrNow, style) {
  const n = df.c.length, price = df.c[n - 1], tol = Math.max(style.sr_tol_atr * atrNow, price * 0.003);
  const pts = [...ph.map(([i, p]) => [p, i, "H"]), ...pl.map(([i, p]) => [p, i, "L"])].sort((a, b) => a[0] - b[0] || a[1] - b[1] || (a[2] < b[2] ? -1 : a[2] > b[2] ? 1 : 0));
  const clusters = [];
  for (const x of pts) { const last = clusters[clusters.length - 1];
    if (last && Math.abs(x[0] - npMean(last.map((y) => y[0]))) <= tol) last.push(x); else clusters.push([x]); }
  const raw = [];
  for (const cl of clusters) {
    if (cl.length < style.sr_min_touches) continue;
    const prices = cl.map((x) => x[0]), idxs = cl.map((x) => x[1]), lvl = median(prices);
    const zipped = idxs.map((i, k) => [i, prices[k]]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const score = pySum(idxs.map((i) => 0.5 + 0.5 * (i / Math.max(1, n - 1))));
    raw.push({ price: pr(lvl), touches: cl.length, first: Math.min(...idxs), last: Math.max(...idxs), touch_idx: idxs.slice().sort((a, b) => a - b), touch_px: zipped.map((x) => pr(x[1])),
      flip: new Set(cl.map((x) => x[2])).size === 2, score: pr(score), dist_pct: pr(((lvl - price) / price) * 100), dist_atr: atrNow ? pr((lvl - price) / atrNow) : null });
  }
  const pick = (side, key) => { const near = side.slice().sort((a, b) => key(a) - key(b)); const keep = [...near.slice(0, 3), ...near.slice(3).sort((a, b) => b.score - a.score).slice(0, 2)]; return keep.sort((a, b) => key(a) - key(b)); };
  const above = pick(raw.filter((x) => x.price > price), (x) => x.price), below = pick(raw.filter((x) => x.price <= price), (x) => -x.price);
  above.forEach((x, k) => Object.assign(x, { id: "R" + (k + 1), kind: "resistance" })); below.forEach((x, k) => Object.assign(x, { id: "S" + (k + 1), kind: "support" }));
  return [...below, ...above];
}

// ── supply and demand zones ──
export function zoneQuality(dep, impa, baseN, thickAtr, tests, vr, bos, aligned) {
  let pts = 30 * Math.min(1.0, dep / (impa * 2.5));
  pts += { 1: 15, 2: 12, 3: 8 }[baseN] ?? 5;
  pts += tests === 0 ? 20 : tests === 1 ? 10 : tests === 2 ? 4 : 0;
  if (vr != null && isFinite(vr)) pts += vr >= 2 ? 10 : vr >= 1.5 ? 7 : vr >= 1.2 ? 4 : 0;
  pts += thickAtr <= 0.8 ? 10 : thickAtr <= 1.4 ? 7 : thickAtr <= 2.0 ? 4 : 1;
  pts += bos ? 8 : 0; pts += aligned ? 7 : 0;
  return pyRound(Math.min(100, pts));
}
// One base → impulse candidate at candle k (shared by detectZones and zoneHistory). Returns null when there is no base.
function baseAt(df, P, k, bmax, brng) {
  const { o, h, l, c } = df, e = k - 1, a = P.atr[e];
  if (!isFinite(a) || a <= 0 || Math.abs(c[k] - o[k]) < 0.55 * a) return null;
  let m = 0, j = e; while (j >= 0 && m < bmax && h[j] - l[j] <= brng * a) { m++; j--; }
  if (m === 0) return null;
  return { e, a, m, s: e - m + 1, up: c[k] > o[k] };
}
const bodyMax = (df, s, e) => { let r = -Infinity; for (let i = s; i <= e; i++) r = Math.max(r, Math.max(df.o[i], df.c[i])); return r; };
const bodyMin = (df, s, e) => { let r = Infinity; for (let i = s; i <= e; i++) r = Math.min(r, Math.min(df.o[i], df.c[i])); return r; };
export function detectZones(df, P, style) {
  const n = df.c.length; if (n < 40) return [];
  const { h, l, c, v } = df, atr = P.atr, vavg = P.vavg, sma50 = P.sma50;
  const bmax = Math.trunc(style.base_max), brng = +style.base_range_atr, impa = +style.impulse_atr, W = Math.trunc(style.impulse_window), raw = [];
  for (let k = Math.max(5, n - Math.trunc(style.lookback), bmax + 1); k < n; k++) {
    const B = baseAt(df, P, k, bmax, brng); if (!B) continue;
    const { e, a, m, s, up } = B, wEnd = Math.min(n - 1, k + W - 1);
    let dep, distal, proximal, impEnd;
    if (up) { dep = (mx(c, k, wEnd) - mx(h, s, e)) / a; distal = mn(l, s, e); proximal = bodyMax(df, s, e); proximal = Math.max(proximal, distal + 0.1 * a); impEnd = argMax(c, k, wEnd); }
    else { dep = (mn(l, s, e) - mn(c, k, wEnd)) / a; distal = mx(h, s, e); proximal = bodyMin(df, s, e); proximal = Math.min(proximal, distal - 0.1 * a); impEnd = argMin(c, k, wEnd); }
    const thick = Math.abs(proximal - distal);
    if (dep < impa || thick > +style.max_zone_atr * a) continue;
    const lin = s >= 4 ? (c[s - 1] - c[s - 4]) / a : 0.0;
    const pattern = up ? (lin > 0.8 ? "RBR" : lin < -0.8 ? "DBR" : "BR") : (lin > 0.8 ? "RBD" : lin < -0.8 ? "DBD" : "BD");
    const vr = isFinite(vavg[e]) && vavg[e] > 0 ? mx(v, k, wEnd) / vavg[e] : null;
    const p0 = Math.max(0, s - 15);
    const bos = s > 0 && ((up && c[impEnd] > mx(h, p0, s - 1)) || (!up && c[impEnd] < mn(l, p0, s - 1)));
    const aligned = s > 0 && isFinite(sma50[s - 1]) && ((up && c[s - 1] > sma50[s - 1]) || (!up && c[s - 1] < sma50[s - 1]));
    let tests = 0, inTouch = false, broken = false;
    for (let t = impEnd + 1; t < n; t++) {
      let inside;
      if (up) { if (c[t] < distal) { broken = true; break; } inside = l[t] <= proximal; }
      else { if (c[t] > distal) { broken = true; break; } inside = h[t] >= proximal; }
      if (inside && !inTouch) tests++; inTouch = inside;
    }
    if (broken) continue;
    const testsPrior = inTouch ? tests - 1 : tests, status = inTouch ? "IN ZONE" : tests === 0 ? "FRESH" : `TESTED ${tests}x`;
    const q = zoneQuality(dep, impa, m, thick / a, testsPrior, vr, bos, aligned), top = Math.max(proximal, distal), bottom = Math.min(proximal, distal);
    raw.push({ type: up ? "demand" : "supply", pattern, s, e, imp: impEnd, proximal: pr(proximal), distal: pr(distal), top: pr(top), bottom: pr(bottom), thickness_atr: nr(thick / a),
      base_n: m, dep_atr: pr(dep), vol_ratio: _f(vr), bos, aligned, tests: testsPrior, status, in_zone: inTouch, quality: q, formed: df.d[e], age: n - 1 - impEnd });
  }
  const pass = raw.filter((z) => z.quality >= style.min_quality && !(style.fresh_only && z.tests > 0)), kept = [];
  for (const z of pass.slice().sort((a, b) => b.quality - a.quality)) {          // overlapping duplicates: the strongest wins
    const dup = kept.some((k) => k.type === z.type && Math.min(k.top, z.top) - Math.max(k.bottom, z.bottom) > 0.5 * Math.min(k.top - k.bottom, z.top - z.bottom));
    if (!dup) kept.push(z);
  }
  const price = c[n - 1], aNow = atr[n - 1];
  for (const z of kept) {
    const d = z.in_zone || (z.bottom <= price && price <= z.top) ? 0.0 : z.type === "demand" ? price - z.top : z.bottom - price;
    z.dist_pct = pr((d / price) * 100); z.dist_atr = aNow ? pr(d / aNow) : null;
  }
  const out = [];
  for (const [zt, prefix] of [["demand", "DZ"], ["supply", "SZ"]]) {
    kept.filter((z) => z.type === zt).sort((a, b) => (a.dist_atr ?? 99) - (b.dist_atr ?? 99) || b.quality - a.quality).forEach((z, k) => { z.id = prefix + (k + 1); out.push(z); });
  }
  return out;
}

// ── trendlines ──
export function detectTrendlines(df, ph, pl, atrNow) {
  const n = df.c.length, c = df.c, tol = 0.45 * atrNow, out = [];
  for (const [kind, piv] of [["support", pl], ["resistance", ph]]) {
    const cand = piv.slice(-7); let best = null;
    for (let ai = 0; ai < cand.length - 1; ai++) for (let bi = ai + 1; bi < cand.length; bi++) {
      const [i0, p0] = cand[ai], [i1, p1] = cand[bi]; if (i1 - i0 < 8) continue;
      const slope = (p1 - p0) / (i1 - i0);
      if ((kind === "support" && slope <= 0) || (kind === "resistance" && slope >= 0)) continue;
      let bad = false; for (let t = i0; t < n; t++) { const y = p0 + slope * (t - i0); if (kind === "support" ? c[t] < y - tol : c[t] > y + tol) { bad = true; break; } }
      if (bad) continue;
      const tp = cand.filter(([i, p]) => i >= i0 && Math.abs(p - (p0 + slope * (i - i0))) <= tol).map(([i, p]) => [i, pr(p)]);
      if (tp.length < 3) continue;
      const score = tp.length * 2 + (i1 / n) * 3; if (!best || score > best[0]) best = [score, i0, p0, slope, tp.length, tp];
    }
    if (best) { const [, i0, p0, slope, touches, tp] = best, now = p0 + slope * (n - 1 - i0), last = c[n - 1];
      out.push({ kind, i0, p0: pr(p0), i1: n - 1, p1: pr(now), touches, touch_idx: tp.map((x) => x[0]), touch_px: tp.map((x) => x[1]), slope_pct_per_bar: pr((slope / last) * 100, 3), dist_pct: pr(((last - now) / last) * 100) }); }
  }
  out.forEach((t, k) => (t.id = "TL" + (k + 1)));
  return out;
}

// ── events, candle signals, structure ──
export function detectEvents(df, P, levels, zones, atrNow) {
  const n = df.c.length, { c, l, v } = df, vavg = P.vavg, ev = [];
  for (const lv of levels) { const p = lv.price;
    for (let t = Math.max(1, n - 6); t < n; t++) {
      const up = c[t - 1] <= p && p < c[t], dn = c[t - 1] >= p && p > c[t]; if (!(up || dn)) continue;
      const vr = isFinite(vavg[t - 1]) && vavg[t - 1] > 0 ? v[t] / vavg[t - 1] : null;
      let retest = null;
      if (up) { if (t < n - 1) { retest = false; for (let u = t + 1; u < n; u++) if (l[u] <= p + 0.5 * atrNow && c[u] >= p) { retest = true; break; } } }
      ev.push({ type: up ? "BREAKOUT" : "BREAKDOWN", level_id: lv.id, price: p, i: t, date: df.d[t], vol_ratio: _f(vr), retest_held: up ? retest : null });
    }
  }
  for (const z of zones) if (z.in_zone) ev.push({ type: "PRICE_IN_" + z.type.toUpperCase() + "_ZONE", zone_id: z.id, i: n - 1, date: df.d[n - 1] });
  return ev;
}
export function candleSignals(df) {
  const { o, h, l, c } = df, n = c.length, out = [];
  for (let i = Math.max(1, n - 3); i < n; i++) {
    const body = Math.abs(c[i] - o[i]), rng = h[i] - l[i]; if (rng <= 0) continue;
    const lw = Math.min(o[i], c[i]) - l[i], uw = h[i] - Math.max(o[i], c[i]);
    if (c[i - 1] < o[i - 1] && c[i] > o[i] && o[i] <= c[i - 1] && c[i] >= o[i - 1]) out.push({ type: "BULLISH_ENGULFING", i, bias: "bull" });
    if (c[i - 1] > o[i - 1] && c[i] < o[i] && o[i] >= c[i - 1] && c[i] <= o[i - 1]) out.push({ type: "BEARISH_ENGULFING", i, bias: "bear" });
    if (lw >= 2 * Math.max(body, 1e-9) && uw <= 0.35 * rng && body / rng < 0.4) out.push({ type: "HAMMER_REJECTION", i, bias: "bull" });
    if (uw >= 2 * Math.max(body, 1e-9) && lw <= 0.35 * rng && body / rng < 0.4) out.push({ type: "SHOOTING_STAR_REJECTION", i, bias: "bear" });
    if (h[i] < h[i - 1] && l[i] > l[i - 1]) out.push({ type: "INSIDE_BAR", i, bias: "neutral" });
  }
  for (const s of out) s.date = df.d[s.i];
  return out;
}
export function marketStructure(ph, pl) {
  if (ph.length >= 2 && pl.length >= 2) {
    const hh = ph[ph.length - 1][1] > ph[ph.length - 2][1], hl = pl[pl.length - 1][1] > pl[pl.length - 2][1];
    if (hh && hl) return "UPTREND (higher highs, higher lows)";
    if (!hh && !hl) return "DOWNTREND (lower highs, lower lows)";
    return "RANGE / TRANSITION";
  }
  return "UNDEFINED";
}
export function analyzeMarket(df, style, liveFrac = null) {
  const P = prepare(df), n = P.n, c = df.c, price = c[n - 1], atrNow = isFinite(P.atr[n - 1]) ? P.atr[n - 1] : c[n - 1] * 0.02;
  const [ph, pl] = findPivots(df, Math.trunc(style.pivot_left), Math.trunc(style.pivot_right));
  const levels = buildLevels(df, ph, pl, atrNow, style), zones = detectZones(df, P, style), lines = detectTrendlines(df, ph, pl, atrNow);
  const vavgPrev = n > 1 && isFinite(P.vavg[n - 2]) ? P.vavg[n - 2] : NaN, volNow = liveFrac ? df.v[n - 1] / liveFrac : df.v[n - 1];
  const vr20 = isFinite(vavgPrev) && vavgPrev > 0 ? volNow / vavgPrev : null;
  const sma = { 20: _f(P.sma20[n - 1]), 50: _f(P.sma50[n - 1]), 200: _f(P.sma200[n - 1]) };
  const s50 = P.sma50[n - 1], s50p = n > 6 ? P.sma50[n - 6] : NaN, lastVavg = P.vavg[n - 1];
  const tail = (x, k) => x.slice(Math.max(0, n - k)), maxOf = (a) => a.reduce((m, x) => (x > m ? x : m), -Infinity), minOf = (a) => a.reduce((m, x) => (x < m ? x : m), Infinity);
  const t5 = tail(df.v, 5); let s5 = 0; for (const x of t5) s5 += x;
  const det = {
    n, asof: df.d[n - 1], close: pr(price), atr: pr(atrNow), atr_pct: pr((atrNow / price) * 100),
    day_chg_pct: n > 1 ? pr((price / c[n - 2] - 1) * 100) : 0.0, structure: marketStructure(ph, pl), rsi: _f(P.rsi[n - 1], 1), sma,
    ma_state: sma[20] && sma[50] && price > sma[20] && price > sma[50] ? "ABOVE 20/50" : sma[20] && sma[50] && price < sma[20] && price < sma[50] ? "BELOW 20/50" : "MIXED",
    sma50_slope_atr: isFinite(s50) && isFinite(s50p) ? _f((s50 - s50p) / atrNow) : null,
    volume: { last: Math.trunc(df.v[n - 1]), ratio20: _f(vr20), ratio5: isFinite(lastVavg) && lastVavg > 0 ? _f(s5 / t5.length / lastVavg) : null },
    roc5_pct: n > 6 ? _f((price / c[n - 6] - 1) * 100) : null, roc20_pct: n > 21 ? _f((price / c[n - 21] - 1) * 100) : null,
    refs: { pdh: n > 1 ? _f(df.h[n - 2]) : null, pdl: n > 1 ? _f(df.l[n - 2]) : null, hi52: _f(maxOf(tail(df.h, 252))), lo52: _f(minOf(tail(df.l, 252))), hi20: _f(maxOf(tail(df.h, 20))), lo20: _f(minOf(tail(df.l, 20))) },
    swings: { highs: ph.slice(-4).map(([i, p]) => ({ i, date: df.d[i], price: pr(p) })), lows: pl.slice(-4).map(([i, p]) => ({ i, date: df.d[i], price: pr(p) })) },
    zones, levels, trendlines: lines, events: detectEvents(df, P, levels, zones, atrNow), candles: candleSignals(df), live: Boolean(liveFrac),
  };
  if (liveFrac) { det.volume.projected = true; for (const x of [...det.events, ...det.candles]) if (x.i === n - 1) x.provisional = true; }
  return det;
}
export function addHtfConfluence(d, w) {
  for (const z of d.zones) { z.htf = false; if (!w) continue; for (const x of w.zones) if (x.type === z.type && Math.min(z.top, x.top) - Math.max(z.bottom, x.bottom) > 0) { z.htf = true; break; } }
}

// ── learning a zone style from labelled zones ──
const FEATURE_KEYS = ["type", "pattern", "thickness_atr", "base_n", "dep_atr", "vol_ratio", "quality", "tests", "bos", "aligned", "age"];
export const zoneFeatures = (z) => Object.fromEntries(FEATURE_KEYS.map((k) => [k, z[k] === undefined ? null : z[k]]));
const clip = (x, a, b) => Math.min(b, Math.max(a, x));
export function calibrateStyle(approved, rejected) {
  if (approved.length < 3) return [null, ["Approve at least 3 zones so Arka has something to learn from."]];
  const arr = (key, src) => src.filter((x) => x[key] != null).map((x) => +x[key]), notes = [], params = {};
  const th = arr("thickness_atr", approved), bn = arr("base_n", approved), dp = arr("dep_atr", approved), ql = arr("quality", approved);
  if (th.length) { params.max_zone_atr = clip(nr(percentile(th, 80) * 1.15, 2), 0.8, 3.5);
    notes.push(`Your zones are typically ${fx(median(th), 2, false)} ATR thick (80th percentile ${fx(percentile(th, 80), 2, false)}) -> max zone thickness ${pyRepr(params.max_zone_atr)} ATR.`); }
  if (bn.length) { params.base_max = Math.trunc(clip(Math.ceil(percentile(bn, 80)), 1, 6));
    notes.push(`Bases of ${Math.trunc(median(bn))} candle(s) are typical for you -> allow up to ${params.base_max}.`); }
  if (dp.length) { params.impulse_atr = clip(nr(percentile(dp, 20) * 0.9, 2), 1.0, 4.0);
    notes.push(`You favour moves of ${fx(median(dp), 1, false)} ATR away from the base -> minimum impulse ${pyRepr(params.impulse_atr)} ATR.`); }
  if (ql.length) { params.min_quality = Math.trunc(clip(pyRound(percentile(ql, 20)) - 5, 25, 70));
    notes.push(`Approved zones scored ${Math.trunc(percentile(ql, 20))}-${Math.trunc(percentile(ql, 80))} -> hide anything below ${params.min_quality}.`); }
  const fresh = npMean(approved.map((x) => ((x.tests === undefined ? 0 : x.tests) === 0 ? 1.0 : 0.0)));
  if (approved.length >= 5 && fresh >= 0.8) { params.fresh_only = true; notes.push(`${fx(fresh * 100, 0, false)}% of your approved zones were untested -> showing fresh zones only.`); }
  if (rejected.length) {
    const rth = arr("thickness_atr", rejected);
    if (rth.length && th.length && median(rth) > median(th) * 1.3) {
      const capv = clip(nr((percentile(th, 80) + median(rth)) / 2, 2), 0.8, 3.5); if (capv < (params.max_zone_atr ?? 9)) params.max_zone_atr = capv;
      notes.push(`You rejected thicker zones (median ${fx(median(rth), 2, false)} ATR) than you approved (${fx(median(th), 2, false)}) -> max zone thickness tightened to ${pyRepr(params.max_zone_atr)} ATR.`);
    }
    const rTested = npMean(rejected.map((x) => ((x.tests === undefined ? 0 : x.tests) > 0 ? 1.0 : 0.0)));
    if (rejected.length >= 4 && rTested >= 0.75 && fresh >= 0.6 && !params.fresh_only) {
      params.fresh_only = true; notes.push(`${fx(rTested * 100, 0, false)}% of the zones you rejected had already been tested, and most you approved were untested -> showing fresh zones only.`); }
    const rdp = arr("dep_atr", rejected);
    if (rdp.length && dp.length && median(rdp) < median(dp) * 0.75) {
      const lift = clip(nr((median(rdp) + percentile(dp, 20)) / 2, 2), 1.0, 4.0); if (lift > (params.impulse_atr ?? 0)) params.impulse_atr = lift;
      notes.push(`You rejected zones with weaker moves away (median ${fx(median(rdp), 1, false)} ATR vs ${fx(median(dp), 1, false)}) -> minimum impulse raised to ${pyRepr(params.impulse_atr)} ATR.`);
    }
    const rq = arr("quality", rejected);
    if (rq.length && ql.length && median(rq) < median(ql) - 10) notes.push("Rejected zones scored clearly lower than approved ones: the quality score matches your taste.");
  }
  return [params, notes];
}

// ── track record: every past zone of this style, replayed with no hindsight ──
export function zoneHistory(df, style, maxWait = 120) {
  const P = prepare(df), n = P.n; if (n < 60) return [];
  const { o, h, l, c, v } = df, vavg = P.vavg, sma50 = P.sma50;
  const bmax = Math.trunc(style.base_max), brng = +style.base_range_atr, impa = +style.impulse_atr, W = Math.trunc(style.impulse_window), out = [], last = { demand: null, supply: null };
  for (let k = Math.max(30, bmax + 1); k < n; k++) {
    const B = baseAt(df, P, k, bmax, brng); if (!B) continue;
    const { e, a, m, s, up } = B, wEnd = Math.min(n - 1, k + W - 1), baseHi = mx(h, s, e), baseLo = mn(l, s, e);
    let confirm = null, dep = 0.0;
    for (let j = k; j <= wEnd; j++) { dep = up ? (c[j] - baseHi) / a : (baseLo - c[j]) / a; if (dep >= impa) { confirm = j; break; } }
    if (confirm == null) continue;
    let distal, proximal;
    if (up) { distal = baseLo; proximal = Math.max(bodyMax(df, s, e), baseLo + 0.1 * a); } else { distal = baseHi; proximal = Math.min(bodyMin(df, s, e), baseHi - 0.1 * a); }
    const thick = Math.abs(proximal - distal); if (thick > +style.max_zone_atr * a) continue;
    const zt = up ? "demand" : "supply", vr = isFinite(vavg[e]) && vavg[e] > 0 ? mx(v, k, confirm) / vavg[e] : null, p0 = Math.max(0, s - 15);
    const bos = s > 0 && ((up && c[confirm] > mx(h, p0, s - 1)) || (!up && c[confirm] < mn(l, p0, s - 1)));
    const aligned = s > 0 && isFinite(sma50[s - 1]) && ((up && c[s - 1] > sma50[s - 1]) || (!up && c[s - 1] < sma50[s - 1]));
    const q0 = zoneQuality(dep, impa, m, thick / a, 0, vr, bos, aligned); if (q0 < style.min_quality) continue;
    const top = Math.max(proximal, distal), bottom = Math.min(proximal, distal), pv = last[zt];
    if (pv && confirm - pv.confirm <= 10 && Math.min(pv.top, top) - Math.max(pv.bottom, bottom) > 0.5 * Math.min(pv.top - pv.bottom, top - bottom)) continue;      // the same area formed again a few candles later
    const stop = up ? distal - 0.25 * a : distal + 0.25 * a;
    const row = { type: zt, quality: q0, aligned, confirm, formed: df.d[e], top: pr(top), bottom: pr(bottom), proximal: pr(proximal), stop: pr(stop), touch: null, touch_date: null, outcome: "WAITING", hit1: null, hit2: null, wait: null };
    last[zt] = row;
    let touch = null; for (let t = confirm + 1; t < Math.min(n, confirm + 1 + maxWait); t++) if ((up && l[t] <= proximal) || (!up && h[t] >= proximal)) { touch = t; break; }
    if (touch == null) { row.outcome = confirm + maxWait < n - 1 ? "NEVER TESTED" : "WAITING"; out.push(row); continue; }
    const entry = up ? Math.min(proximal, o[touch]) : Math.max(proximal, o[touch]), risk = up ? entry - stop : stop - entry;
    Object.assign(row, { touch, touch_date: df.d[touch], wait: touch - confirm, entry: pr(entry) });
    if (risk <= 0) { Object.assign(row, { outcome: "STOP", hit1: false, hit2: false }); out.push(row); continue; }      // gapped through the whole zone
    const t1 = up ? entry + risk : entry - risk, t2 = up ? entry + 2 * risk : entry - 2 * risk;
    let hit1 = false, outcome = "OPEN";
    for (let t = touch; t < n; t++) {
      if ((up && l[t] <= stop) || (!up && h[t] >= stop)) { outcome = "STOP"; break; }
      if (t > touch) { if ((up && h[t] >= t1) || (!up && l[t] <= t1)) hit1 = true; if ((up && h[t] >= t2) || (!up && l[t] <= t2)) { outcome = "2R"; break; } }
    }
    Object.assign(row, { outcome, hit1: hit1 || outcome === "STOP" ? Boolean(hit1) : null, hit2: outcome === "2R" || outcome === "STOP" ? outcome === "2R" : null });
    out.push(row);
  }
  return out;
}
export function trackStats(rows) {
  const r1 = rows.filter((x) => x.hit1 != null).map((x) => x.hit1), r2 = rows.filter((x) => x.hit2 != null).map((x) => x.hit2);
  const sum = (a) => a.reduce((s, x) => s + (x ? 1 : 0), 0), p2 = r2.length ? sum(r2) / r2.length : null;
  return { formed: rows.length, tested: rows.filter((x) => x.touch != null).length, n1: r1.length, p1: r1.length ? pr((sum(r1) / r1.length) * 100, 1) : null,
    n2: r2.length, p2: r2.length ? pr(p2 * 100, 1) : null, exp2: r2.length ? pr(p2 * 2 - (1 - p2), 2) : null };
}
export const TRACK_GROUPS = ["All zones", "Demand", "Supply", "With trend", "Against trend", "Q 70+", "Q 55-69", "Q below 55"];
export function trackRecord(rows, zone = null) {
  const band = (q) => (q >= 70 ? "Q 70+" : q >= 55 ? "Q 55-69" : "Q below 55");
  const groups = { "All zones": rows, Demand: rows.filter((x) => x.type === "demand"), Supply: rows.filter((x) => x.type === "supply"), "With trend": rows.filter((x) => x.aligned), "Against trend": rows.filter((x) => !x.aligned) };
  for (const b of ["Q 70+", "Q 55-69", "Q below 55"]) groups[b] = rows.filter((x) => band(x.quality) === b);
  const out = { groups: Object.fromEntries(Object.entries(groups).map(([k, v]) => [k, trackStats(v)])), like: null, like_label: null, recent: rows.filter((x) => x.touch != null).slice(-8).reverse() };
  if (zone) { out.like = trackStats(rows.filter((x) => x.type === zone.type && x.aligned === Boolean(zone.aligned)));
    out.like_label = `${title(zone.type)} zones ${zone.aligned ? "with" : "against"} the trend`; }
  return out;
}
export function evaluateCall(p, df) {
  try {
    const long = p.direction === "LONG", ref = +p.entry_ref, stop = +p.stop, t1 = p.targets && p.targets.length ? +p.targets[0].price : null;
    if ([ref, stop].some((x) => !isFinite(x)) || (t1 != null && !isFinite(t1))) throw new Error("ValueError");
    let risk = Math.abs(ref - stop);
    const idx = []; for (let i = 0; i < df.d.length; i++) if (df.d[i] > String(p.asof).slice(0, 10)) idx.push(i);
    const base = { status: "WAITING", r: null, days: idx.length, entry: null, last: df.c.length ? df.c[df.c.length - 1] : null };
    if (risk <= 0 || t1 == null) return { ...base, status: "INVALID" };
    const cal = p.close_at_log != null ? +p.close_at_log : ref, inZone = +p.entry_low <= cal && cal <= +p.entry_high;
    let entry = inZone ? ref : null, start = inZone ? 0 : null;
    const O = idx.map((i) => df.o[i]), H = idx.map((i) => df.h[i]), L = idx.map((i) => df.l[i]), C = idx.map((i) => df.c[i]);
    if (entry == null) for (let t = 0; t < idx.length; t++) {
      if ((long && L[t] <= ref) || (!long && H[t] >= ref)) { entry = long ? Math.min(ref, O[t]) : Math.max(ref, O[t]); start = t; break; }
      if ((long && H[t] >= t1) || (!long && L[t] <= t1)) return { ...base, status: "MISSED (ran to target without entry)" };
    }
    if (entry == null) return base;
    risk = Math.abs(entry - stop) || risk;
    for (let t = start; t < idx.length; t++) {
      if ((long && L[t] <= stop) || (!long && H[t] >= stop)) return { ...base, status: "STOPPED", r: -1.0, entry: pr(entry), exit_date: df.d[idx[t]] };
      if ((t > start || inZone) && ((long && H[t] >= t1) || (!long && L[t] <= t1))) return { ...base, status: "TARGET 1 HIT", r: pr(Math.abs(t1 - entry) / risk), entry: pr(entry), exit_date: df.d[idx[t]] };
    }
    const now = idx.length ? C[C.length - 1] : cal;
    return { ...base, status: "OPEN", r: pr(((long ? now - entry : entry - now)) / risk), entry: pr(entry) };
  } catch (e) { return { status: `ERROR (${e.message === "ValueError" ? "ValueError" : "TypeError"})`, r: null, days: 0, entry: null, last: null }; }
}
export function marketContext(stock, nifty) {
  if (!nifty || nifty.c.length < 80) return null;
  const P = prepare(nifty), [ph, pl] = findPivots(nifty, 3, 3), roc = (d) => (d.c.length > 64 ? (d.c[d.c.length - 1] / d.c[d.c.length - 64] - 1) * 100 : null);
  const sr = roc(stock), nr_ = roc(nifty), s50 = P.sma50[P.n - 1], last = nifty.c[P.n - 1];
  return { nifty_structure: marketStructure(ph, pl), nifty_close: pr(last), nifty_vs_sma50: !isFinite(s50) ? null : last > s50 ? "ABOVE" : "BELOW",
    stock_3m_pct: _f(sr), nifty_3m_pct: _f(nr_), rs_3m_pp: sr != null && nr_ != null ? _f(sr - nr_) : null };
}

// ── the engine's own plan and score (works with no AI) ──
function targetCands(det, ref, risk, long) {
  const cands = [];
  for (const lv of det.levels) if ((long && lv.price > ref + risk) || (!long && lv.price < ref - risk)) cands.push([lv.price, lv.id, long ? "resistance" : "support"]);
  for (const oz of det.zones) { if (long && oz.type === "supply" && oz.bottom > ref + risk) cands.push([oz.bottom, oz.id, "supply zone"]); if (!long && oz.type === "demand" && oz.top < ref - risk) cands.push([oz.top, oz.id, "demand zone"]); }
  cands.sort((a, b) => (long ? a[0] - b[0] : b[0] - a[0]));
  const out = cands.slice(0, 3).map(([p, r, k]) => ({ price: pr(p), ref: r, reason: `Next ${k}`, rr: pr(Math.abs(p - ref) / risk) }));
  for (const mult of [2, 3]) if (out.length < 2) { const p = long ? ref + mult * risk : ref - mult * risk; out.push({ price: pr(p), ref: `${mult}R`, reason: `${mult}R projection (no level found)`, rr: mult }); }
  return out;
}
export function deterministicPlan(det) {
  const price = det.close, atr = det.atr, zones = det.zones, trendUp = det.structure.startsWith("UPTREND"), trendDn = det.structure.startsWith("DOWNTREND");
  const pick = (zt) => { const c = zones.filter((z) => z.type === zt && z.dist_atr != null && z.dist_atr <= 2.5); if (!c.length) return null;
    const bonus = (z) => z.quality - 6 * z.dist_atr + ((zt === "demand" && trendUp) || (zt === "supply" && trendDn) ? 8 : 0) + (z.htf ? 6 : 0);
    let best = c[0]; for (const z of c) if (bonus(z) > bonus(best)) best = z; return best; };
  const zl = pick("demand"), zs = pick("supply"); if (!zl && !zs) return null;
  const long = Boolean(zl && (!zs || zl.quality - 6 * zl.dist_atr + (trendUp ? 8 : 0) >= zs.quality - 6 * zs.dist_atr + (trendDn ? 8 : 0))), z = long ? zl : zs;
  const ref = z.in_zone ? Math.min(Math.max(price, z.bottom), z.top) : long ? z.top : z.bottom;
  const stop = long ? pr(z.distal - 0.25 * atr) : pr(z.distal + 0.25 * atr), risk = Math.abs(ref - stop); if (risk <= 0) return null;
  const plan = { direction: long ? "LONG" : "SHORT", ref_zone: z.id, entry_low: z.bottom, entry_high: z.top, entry_ref: pr(ref), stop,
    stop_reason: `Beyond ${z.id} distal line (${fx(z.distal, 2, false)}) ${long ? "minus" : "plus"} 0.25 ATR`, targets: targetCands(det, ref, risk, long).slice(0, 3),
    counter_trend: Boolean((long && trendDn) || (!long && trendUp)), source: "engine" };
  Object.assign(plan, planRoom(det, plan)); return plan;
}
export function planRoom(det, plan) {
  const long = plan.direction === "LONG", ref = plan.entry_ref, risk = Math.abs(ref - plan.stop);
  if (risk <= 0) return { room_r: null, obstacle: null, blocked: false };
  const obs = [];
  for (const lv of det.levels) if ((long && lv.price > ref) || (!long && lv.price < ref)) obs.push([Math.abs(lv.price - ref), lv.id, lv.price]);
  for (const oz of det.zones) { if (oz.id === plan.ref_zone) continue;
    if (long && oz.type === "supply" && oz.bottom > ref) obs.push([oz.bottom - ref, oz.id, oz.bottom]);
    if (!long && oz.type === "demand" && oz.top < ref) obs.push([ref - oz.top, oz.id, oz.top]); }
  if (!obs.length) return { room_r: null, obstacle: null, blocked: false };
  const [d, oid, px] = obs.reduce((m, x) => (x[0] < m[0] || (x[0] === m[0] && (x[1] < m[1] || (x[1] === m[1] && x[2] < m[2]))) ? x : m));      // min() of tuples
  const flips = Boolean((long && px < det.close) || (!long && px > det.close));      // a level between price and the entry has to break first, then it is in the way
  return { room_r: pr(d / risk), obstacle: oid, obstacle_price: pr(px), blocked: Boolean(d / risk < 1.0), obstacle_flips: flips };
}
export function targetsFor(det, ref, risk, long) { return targetCands(det, ref, risk, long).slice(0, 3).sort((a, b) => a.rr - b.rr); }
export function engineScore(det, plan) {
  if (!plan) return det.structure === "RANGE / TRANSITION" ? 20 : 28;
  const z = det.zones.find((x) => x.id === plan.ref_zone); if (!z) return 25;
  const long = plan.direction === "LONG", proximity = Math.max(0.0, 100 - (z.dist_atr || 0) * 35);
  const trend = plan.counter_trend ? 15 : det.structure.startsWith(long ? "UPTREND" : "DOWNTREND") ? 100 : 55, htf = z.htf ? 100 : 40, want = long ? "bull" : "bear";
  const vol = (det.volume.ratio20 || 0) >= 1.2, sig = det.candles.some((s) => s.bias === want && s.i >= det.n - 2 && !s.provisional);
  const confirm = sig && vol ? 100 : sig ? 65 : vol ? 40 : 20;
  let score = 0.40 * z.quality + 0.20 * proximity + 0.15 * trend + 0.10 * htf + 0.15 * confirm; if (plan.blocked) score -= 15;      // less than 1R of room
  return pyRound(Math.max(0, score));
}
export const gradeFor = (score) => (score >= 85 ? "A+" : score >= 72 ? "A" : score >= 58 ? "B" : score >= 45 ? "C" : "D");
export function positionSize(plan, riskAmount) {
  if (!plan || !(riskAmount > 0)) return {};
  const per = Math.abs(plan.entry_ref - plan.stop); if (per <= 0) return {};
  const qty = pyFloorDiv(riskAmount, per); return { risk_amount: riskAmount, risk_per_share: pr(per), qty, capital: pr(qty * plan.entry_ref) };
}
export function buildSpec(det, draw, plan) {
  const zid = new Set((draw || {}).zones || []), lid = new Set((draw || {}).levels || []), tid = new Set((draw || {}).trendlines || []);
  const isZ = (s) => /^(DZ|SZ)/.test(String(s));
  if (plan) { zid.add(plan.ref_zone); const ob = String(plan.obstacle || ""); (isZ(ob) ? zid : lid).add(ob);
    for (const t of plan.targets || []) (isZ(t.ref ?? "") ? zid : lid).add(t.ref); }
  if (!zid.size) for (const zt of ["demand", "supply"]) det.zones.filter((z) => z.type === zt).sort((a, b) => (a.dist_atr || 99) - (b.dist_atr || 99)).slice(0, 3).forEach((z) => zid.add(z.id));
  if (!lid.size) det.levels.slice().sort((a, b) => Math.abs(a.dist_atr || 99) - Math.abs(b.dist_atr || 99)).slice(0, 5).forEach((x) => lid.add(x.id));
  if (!tid.size) det.trendlines.forEach((x) => tid.add(x.id));
  const markers = det.events.filter((e) => e.type === "BREAKOUT" || e.type === "BREAKDOWN").map((e) => ({ i: e.i, price: e.price, dir: e.type === "BREAKOUT" ? "up" : "down", label: title(e.type) }));
  return { zones: det.zones.filter((z) => zid.has(z.id)), levels: det.levels.filter((x) => lid.has(x.id)), lines: det.trendlines.filter((x) => tid.has(x.id)), markers, candles: det.candles, plan, live: Boolean(det.live) };
}

// ── AI: prompt, and the checks that make its reply exact ──
export const SYSTEM_PROMPT = `You are Arka AI, the personal trading analyst of ONE trader. Your only source of strategy is that trader's PLAYBOOK
(setups, lessons about how they draw supply and demand zones and lines, and zones they approved or rejected).

RULES YOU MUST FOLLOW
1. Never invent a strategy. Do not use indicators, patterns or criteria the playbook does not use. You may add clearly separated
   "context" observations, but they must not change the setup verdict.
2. All numbers come from the DETECTIONS and MARKET FACTS supplied by Python. Never invent prices, dates, candle indices or volumes.
3. Never output pixel coordinates. To draw something, return the ID of a supplied detection (for example DZ1, SZ2, L3, TL1).
4. If the evidence cannot decide a rule, say UNCLEAR. Do not guess.
5. A score is how well the evidence matches the playbook. It is NOT a probability of profit.
6. Be direct and specific: exact prices, exact dates, exact detection IDs. Plain language, no hype.
7. The scorecard must contain EVERY rule id of EVERY active setup exactly once, using the ids exactly as written in the playbook.
8. A candle or event marked "provisional" belongs to today's unfinished candle. It cannot confirm a trigger; say so.
9. Reply with ONE JSON object and nothing else.`;
export const ANALYSIS_SCHEMA = `Return ONLY this JSON object (fill every field; use null / [] when unknown):
{
 "verdict": "MATCH | PARTIAL | NO_MATCH | AVOID",
 "score": 0,
 "grade": "A+ | A | B | C | D",
 "bias": "BULLISH | BEARISH | NEUTRAL",
 "setup_state": "WATCHING | FORMING | READY | TRIGGERED | EXTENDED | INVALIDATED | NOT_APPLICABLE",
 "headline": "one sentence a trader can act on",
 "setups": [ {"setup_id": "...", "name": "...", "match_pct": 0, "state": "...",
              "checks": [ {"rule_id": "R01", "rule_name": "...", "status": "PRESENT | NOT_PRESENT | UNCLEAR",
                           "confidence": 0, "reason": "exact dates / prices / detection IDs", "refs": ["DZ1"]} ] } ],
 "my_zones": [ {"id": "DZ1", "role": "ENTRY_ZONE | TARGET | SUPPORT | RESISTANCE | CONTEXT", "matches_my_style": true, "comment": "..."} ],
 "plan": { "direction": "LONG | SHORT | NONE", "ref_zone": "the ID of ONE detected zone (demand for LONG, supply for SHORT)", "stop": 0, "stop_reason": "...",
           "targets": [ {"ref": "ID of a detected level or opposing zone, e.g. R1 or SZ1", "reason": "..."} ], "invalidation": "...", "confirmation_needed": ["..."] },
 "supports": ["things that agree with MY playbook"],
 "contradicts": ["things that disagree with MY playbook"],
 "watch_next": ["what to watch before acting"],
 "scenarios": { "bull": {"trigger": "...", "path": "...", "level": 0}, "bear": {"trigger": "...", "path": "...", "level": 0},
                "neutral": {"trigger": "...", "path": "...", "level": 0} },
 "context": ["optional observations NOT used for the verdict"],
 "draw": { "zones": ["DZ1"], "levels": ["R1"], "trendlines": ["TL1"] },
 "narrative": "a clear 150-250 word analysis in plain language"
}
The entry zone is drawn by Python from ref_zone (its exact top and bottom) and each target from its ref ID, so give IDs, not your own prices.
Python recomputes match_pct, score and grade from your rule statuses, so spend your effort on getting each status and its evidence right.`;
export const PDF_PROMPT = `Read this trading-strategy PDF as a TRAINING document for a personal trading analyst.
Understand everything: prose, screenshots, diagrams, marked-up charts, tables and examples. Do not merely summarise.
Convert it into a structured, testable trading setup. Keep the author's own terminology. Do not invent conditions;
anything ambiguous goes in "ambiguities". Capture how the author defines and draws SUPPLY and DEMAND zones and any lines.

Return ONLY this JSON:
{
 "setup_name": "...", "summary": "...", "market_context": "...",
 "conditions": [ {"id": "R01", "name": "...", "type": "structure|zone|price|volume|indicator|context|other", "required": true,
                  "condition": "testable condition", "evidence_hint": "what proves it"} ],
 "trigger": {"description": "...", "type": "..."},
 "confirmation": [ {"id": "C01", "name": "...", "description": "...", "required": true} ],
 "invalidation": [ {"id": "I01", "description": "...", "price_logic": "..."} ],
 "zone_definitions": [ {"type": "demand|supply", "how_identified": "...", "how_drawn": "...", "quality_markers": ["..."], "avoid": ["..."]} ],
 "line_definitions": [ {"type": "support|resistance|trendline|other", "how_drawn": "...", "use": "..."} ],
 "entry_logic": "...", "stop_logic": "...", "target_logic": "...",
 "exclusions": ["..."], "examples": [ {"label": "VALID|INVALID|UNCLEAR", "description": "...", "source_page": 1} ],
 "ambiguities": ["..."], "confidence": "HIGH|MEDIUM|LOW"
}
Prefer atomic, individually testable conditions. A generic indicator is NOT a rule unless the document actually uses it.`;
export const IMAGE_PROMPT = `These images are charts the trader marked up. Study the drawings (boxes, zones, lines, arrows, labels, entries, stops, targets)
together with the trader's explanation. Learn HOW THIS TRADER reads a chart: what a good supply zone and demand zone look like to them
(base size, impulse strength, location, freshness), how they draw lines, what they wait for before entering, where the stop and targets go.
Do not describe the stock. Extract rules of style.

Return ONLY this JSON:
{
 "lesson_title": "...", "summary": "...",
 "zone_definitions": [ {"type": "demand|supply", "how_identified": "...", "how_drawn": "...", "quality_markers": ["..."], "avoid": ["..."]} ],
 "line_definitions": [ {"type": "support|resistance|trendline|other", "how_drawn": "...", "use": "..."} ],
 "entry_logic": "...", "stop_logic": "...", "target_logic": "...",
 "good_setup_signs": ["..."], "bad_setup_signs": ["..."], "ambiguities": ["..."]
}`;
// json.dumps(obj, ensure_ascii=False): ", " and ": " separators, floats as Python writes them
export function pyDumps(x) {
  if (x === null || x === undefined) return "null";
  if (typeof x === "boolean") return x ? "true" : "false";
  if (typeof x === "number") return Object.is(x, -0) ? "-0.0" : isFinite(x) ? String(x) : x !== x ? "NaN" : x > 0 ? "Infinity" : "-Infinity";
  if (typeof x === "string") return JSON.stringify(x);
  if (Array.isArray(x)) return "[" + x.map(pyDumps).join(", ") + "]";
  return "{" + Object.keys(x).filter((k) => x[k] !== undefined).map((k) => JSON.stringify(k) + ": " + pyDumps(x[k])).join(", ") + "}";
}
const SLIM_Z = ["id", "type", "pattern", "bottom", "top", "proximal", "distal", "thickness_atr", "base_n", "dep_atr", "vol_ratio", "bos", "aligned", "tests", "status", "quality", "formed", "age", "dist_pct", "dist_atr", "htf", "in_zone"];
export const slimZone = (z) => Object.fromEntries(SLIM_Z.map((k) => [k, z[k] === undefined ? null : z[k]]));
export const slimLevels = (ls) => ls.map((x) => Object.fromEntries(["id", "kind", "price", "touches", "flip", "dist_pct", "dist_atr"].map((k) => [k, x[k] === undefined ? null : x[k]])));
export function feedbackSummary(items) {
  const ap = [], rj = [];
  for (const it of items) for (const lab of (it.payload || {}).labels || []) (lab.approved ? ap : rj).push(lab.features || {});
  const med = (rows, k) => (rows.some((r) => r[k] != null) ? _f(median(rows.filter((r) => r[k] != null).map((r) => +r[k]))) : null);
  const keys = ["thickness_atr", "base_n", "dep_atr", "quality"];
  return { approved: ap.length, rejected: rj.length, approved_median: Object.fromEntries(keys.map((k) => [k, med(ap, k)])), rejected_median: Object.fromEntries(keys.map((k) => [k, med(rj, k)])),
    approved_patterns: [...new Set(ap.map((r) => r.pattern).filter(Boolean))].sort() };
}
export function feedbackReasons(items, capN = 14) {
  const out = [];
  for (const it of items) for (const lab of (it.payload || {}).labels || []) { const why = String(lab.reason || "").trim();
    if (why) { const f = lab.features || {}; out.push({ verdict: lab.approved ? "MINE" : "NOT MINE", why: why.slice(0, 200), zone: f.type ?? null, pattern: f.pattern ?? null, thickness_atr: f.thickness_atr ?? null, tests: f.tests ?? null, quality: f.quality ?? null }); } }
  return out.slice(0, capN);
}
export function buildPrompt(ticker, det, detW, style, setups, lessons, feedback, note, df, extra = {}) {
  const setupJson = setups.map((it) => { const p = { ...it.payload }; delete p.source_name; return { setup_id: it.item_id, name: it.name, ...p }; });
  const lessonJson = lessons.map((it) => ({ name: it.name, ...it.payload }));
  const market = Object.fromEntries(["asof", "close", "atr", "atr_pct", "day_chg_pct", "structure", "rsi", "sma", "ma_state", "sma50_slope_atr", "volume", "roc5_pct", "roc20_pct", "refs", "swings"].map((k) => [k, det[k]]));
  const detections = { zones: det.zones.map(slimZone), levels: slimLevels(det.levels), trendlines: det.trendlines, events: det.events, candle_signals: det.candles };
  const weekly = detW ? { structure: detW.structure, ma_state: detW.ma_state, rsi: detW.rsi, close: detW.close, zones: detW.zones.slice(0, 4).map(slimZone), levels: slimLevels(detW.levels.slice(0, 6)) } : null;
  const n = df.c.length, from = Math.max(0, n - 70), rows = [];
  for (let i = from; i < n; i++) rows.push(`${i},${df.d[i]},${fx(df.o[i], 2, false)},${fx(df.h[i], 2, false)},${fx(df.l[i], 2, false)},${fx(df.c[i], 2, false)},${Math.trunc(df.v[i])}`);
  const ep = extra.engine_plan, reasons = feedbackReasons(feedback);
  const parts = [
    "TICKER: " + ticker,
    "== PLAYBOOK: ACTIVE SETUPS (evaluate EACH one separately; rule ids are inside each setup) ==\n" + (setupJson.length ? pyDumps(setupJson) : "NONE TAUGHT YET"),
    "== PLAYBOOK: LESSONS (how this trader defines and draws supply / demand zones, lines, entries, stops, targets) ==\n" + (lessonJson.length ? pyDumps(lessonJson) : "NONE"),
    "== PLAYBOOK: ZONES THIS TRADER APPROVED OR REJECTED (summary) ==\n" + pyDumps(feedbackSummary(feedback)),
    "== PLAYBOOK: THE TRADER'S OWN REASONS FOR APPROVING / REJECTING ZONES ==\n" + (reasons.length ? pyDumps(reasons) : "NONE GIVEN"),
    "== ENGINE STYLE PARAMETERS (how detections below were produced) ==\n" + pyDumps(style),
    "== MARKET FACTS (daily, computed by Python) ==\n" + pyDumps(market),
    "== DETECTIONS (daily). Choose ONLY from these IDs ==\n" + pyDumps(detections),
    "== WEEKLY CONTEXT ==\n" + (weekly ? pyDumps(weekly) : "UNAVAILABLE"),
    "== DAILY OHLC (last 70 candles; i is the candle index used by detections) ==\ni,date,o,h,l,c,v\n" + rows.join("\n"),
    '== CONTEXT (not part of the playbook; use only under "context" unless a playbook rule asks for it) ==\n' + pyDumps({
      last_candle_unfinished: Boolean(det.live), market: extra.market ?? null, next_results_date: extra.results ?? null,
      engine_plan: ep ? Object.fromEntries(["direction", "ref_zone", "entry_low", "entry_high", "stop", "room_r", "obstacle", "blocked"].map((k) => [k, ep[k] ?? null])) : null,
      zone_track_record_this_stock: extra.track ?? null }),
    "== TRADER NOTE ==\n" + (note || "None"),
    "TASK: Judge this stock ONLY against the trader's playbook. 1) Check every rule of every active setup against the detections and facts. " +
      "2) Say which detected zones are the trader's kind of zone (use the lessons and the approved / rejected summary) and what role each plays. " +
      "3) Build a trade plan using ONLY detected zones and levels: name the zone to trade from (ref_zone), a stop beyond its distal line, and targets as the IDs of the next opposing zone or level; " +
      "use direction NONE if nothing qualifies. 4) List what supports and what contradicts the playbook, and what to watch next. " +
      "5) Give bull / bear / neutral scenarios with exact trigger prices. 6) Choose which detection IDs to draw. The attached image shows the detections. " +
      "If no setup is taught, judge against the lessons and approved zones and say so in the headline.",
    ANALYSIS_SCHEMA,
  ];
  return parts.join("\n\n");
}
export function parseJsonLoose(raw) {                     // strict → outermost {...} → trailing-comma repair
  const extract = (t) => { let text = String(t || "").trim();
    if (text.startsWith("```")) { const parts = text.split("```"); if (parts.length >= 2) text = parts[1].trim(); if (text.toLowerCase().startsWith("json")) text = text.slice(4).trim(); }
    const a = text.indexOf("{"), b = text.lastIndexOf("}"); return a >= 0 && b > a ? text.slice(a, b + 1) : text; };
  for (const cand of [raw || "", extract(raw)]) { try { const o = JSON.parse(cand); if (o && typeof o === "object" && !Array.isArray(o)) return o; } catch { /* next */ } }
  const o = JSON.parse(extract(raw).replace(/,\s*([}\]])/g, "$1"));
  if (!o || typeof o !== "object" || Array.isArray(o)) throw new Error("the AI did not return a JSON object");
  return o;
}
const cleanList = (x, capN = 6) => (Array.isArray(x) ? x.slice(0, capN) : []).map((i) => pyStr(i)).filter((s) => s.trim()).map((s) => s.slice(0, 400));
export function pyStr(v) {                                 // str(x) for what JSON can hold
  if (v == null) return "None"; if (v === true) return "True"; if (v === false) return "False"; if (typeof v === "number") return Number.isInteger(v) ? String(v) : pyRepr(v);
  if (typeof v === "string") return v; return pyDumps(v).replace(/\btrue\b/g, "True").replace(/\bfalse\b/g, "False").replace(/\bnull\b/g, "None");
}
export function playbookRules(item) {
  const p = item.payload || {}, out = [], seen = new Set();
  for (const [group, key, prefix] of [["rule", "conditions", "R"], ["confirmation", "confirmation", "C"]]) {
    (Array.isArray(p[key]) ? p[key] : []).forEach((c0, k) => {
      const c = c0 && typeof c0 === "object" && !Array.isArray(c0) ? c0 : { name: pyStr(c0) }, dflt = prefix + String(k + 1).padStart(2, "0");
      let rid = String(c.id || dflt).trim() || dflt; while (seen.has(rid.toUpperCase())) rid += "'"; seen.add(rid.toUpperCase());
      out.push({ id: rid, name: pyStr(c.name || c.condition || c.description || rid).slice(0, 140), required: c.required === undefined ? true : Boolean(c.required), group });
    });
  }
  return out;
}
const G = (o, k, d) => (o && Object.prototype.hasOwnProperty.call(o, k) ? o[k] : d);            // dict.get(k, d)
const truthy = (v) => !(v == null || v === false || v === 0 || v === "" || (Array.isArray(v) && !v.length) || (typeof v === "object" && !Array.isArray(v) && !Object.keys(v).length));
function pyFloat(v) {                                      // float(v), or null where Python raises
  if (typeof v === "boolean") return v ? 1 : 0; if (typeof v === "number") return v;
  if (typeof v === "string") { const t = v.trim().replace(/_/g, "").toLowerCase(); if (/^[+-]?(nan|inf|infinity)$/.test(t)) return t.includes("nan") ? NaN : t.startsWith("-") ? -Infinity : Infinity;
    return t && /^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/.test(t) ? Number(t) : null; }
  return null;
}
function check(c, ids) {
  const st = ["PRESENT", "NOT_PRESENT", "UNCLEAR"].includes(c.status) ? c.status : "UNCLEAR";
  let conf = 0; const f = pyFloat(G(c, "confidence", 0));
  if (f != null) { const lo = f < 100 ? f : 100, v = lo > 0 ? lo : 0; conf = isFinite(v) ? Math.trunc(v) : 0; }      // int(max(0, min(100, f)))
  const refs = G(c, "refs", []);
  return { status: st, confidence: conf, reason: pyStr(G(c, "reason", "")).slice(0, 500), refs: Array.isArray(refs) ? refs.filter((r) => ids.has(r)) : [] };
}
export function snapPrice(det, price, tol) {
  let best = null; const lt = (a, b) => a[0] < b[0] || (a[0] === b[0] && (a[1] < b[1] || (a[1] === b[1] && a[2] < b[2])));
  const consider = (x) => { if (!best || lt(x, best)) best = x; };
  for (const lv of det.levels) consider([Math.abs(lv.price - price), lv.price, lv.id]);
  for (const z of det.zones) for (const edge of [z.top, z.bottom]) consider([Math.abs(edge - price), edge, z.id]);
  return best && best[0] <= tol ? [best[1], best[2]] : null;
}
const toFloat = (v) => pyFloat(v);
const VERDICT_WORD = (v) => v.replace(/_/g, " ");
export function validateAi(ai0, det, price, setups = null) {
  const zid = new Set(det.zones.map((z) => z.id)), lid = new Set(det.levels.map((x) => x.id)), tid = new Set(det.trendlines.map((x) => x.id));
  const ids = new Set([...zid, ...lid, ...tid]), fix = [], out = {}, atr = det.atr, ai = ai0 && typeof ai0 === "object" && !Array.isArray(ai0) ? ai0 : {};
  const aiVerdict = ["MATCH", "PARTIAL", "NO_MATCH", "AVOID"].includes(ai.verdict) ? ai.verdict : null;
  out.bias = ["BULLISH", "BEARISH", "NEUTRAL"].includes(ai.bias) ? ai.bias : "NEUTRAL";
  out.setup_state = pyStr(G(ai, "setup_state", "WATCHING")).slice(0, 30); out.headline = pyStr(G(ai, "headline", "")).slice(0, 400);
  const isObj = (x) => x && typeof x === "object" && !Array.isArray(x);
  const aiSetups = (Array.isArray(ai.setups) ? ai.setups : []).filter(isObj), result = [];
  if (setups && setups.length) {
    const used = new Set();
    const find = (item, pos) => {
      for (let k = 0; k < aiSetups.length; k++) if (!used.has(k) && pyStr(G(aiSetups[k], "setup_id", "")) === String(item.item_id)) return k;
      for (let k = 0; k < aiSetups.length; k++) if (!used.has(k) && pyStr(G(aiSetups[k], "name", "")).trim().toLowerCase() === String(item.name).trim().toLowerCase()) return k;
      return aiSetups.length === setups.length && !used.has(pos) ? pos : null;
    };
    setups.forEach((item, pos) => {
      const k = find(item, pos), a = k != null ? aiSetups[k] : {};
      if (k != null) used.add(k); else fix.push(`The AI did not evaluate the setup "${item.name}"; its rules are shown as UNCLEAR.`);
      const raw = (Array.isArray(a.checks) ? a.checks : []).filter(isObj), byId = new Map(), byName = new Map();
      for (const c of raw) byId.set(pyStr(G(c, "rule_id", "")).trim().toUpperCase(), c);
      for (const c of raw) byName.set(pyStr(G(c, "rule_name", "")).trim().toLowerCase(), c);
      const rules = playbookRules(item); let checks = [], skipped = 0;
      for (const r of rules) {
        let c = null; const key = r.id.toUpperCase(); if (byId.has(key)) { c = byId.get(key); byId.delete(key); }
        if (!truthy(c)) c = byName.has(r.name.trim().toLowerCase()) ? byName.get(r.name.trim().toLowerCase()) : null;
        if (c == null) { skipped++; c = { status: "UNCLEAR", confidence: 0, reason: "The AI did not evaluate this rule." }; }
        checks.push({ rule_id: r.id, rule_name: r.name, required: r.required, group: r.group, ...check(c, ids) });
      }
      if (!rules.length) checks = raw.map((c) => ({ rule_id: pyStr(G(c, "rule_id", "")).slice(0, 12), rule_name: pyStr(G(c, "rule_name", "")).slice(0, 140), required: true, group: "rule", ...check(c, ids) }));
      else { const extraN = raw.filter((c) => byId.has(pyStr(G(c, "rule_id", "")).trim().toUpperCase())).length;
        if (skipped) fix.push(`${item.name}: ${skipped} rule(s) were not evaluated by the AI and are shown as UNCLEAR.`);
        if (extraN) fix.push(`${item.name}: ${extraN} check(s) that are not rules of this setup were removed.`); }
      const reqd = checks.filter((c) => c.required), req = reqd.length ? reqd : checks, present = req.filter((c) => c.status === "PRESENT").length;
      result.push({ setup_id: String(item.item_id), name: String(item.name).slice(0, 120), match_pct: req.length ? pyRound((100 * present) / req.length) : 0, present, required: req.length,
        unclear: req.filter((c) => c.status === "UNCLEAR").length, failed: req.filter((c) => c.status === "NOT_PRESENT").length, state: pyStr(G(a, "state", "")).slice(0, 30), checks });
    });
  }
  out.setups = result;
  if (result.length) {
    let best = result[0]; for (const x of result) if (x.match_pct > best.match_pct) best = x;
    out.score = best.match_pct;
    const derived = best.required && best.present === best.required ? "MATCH" : best.match_pct >= 50 ? "PARTIAL" : "NO_MATCH";
    out.verdict = aiVerdict === "AVOID" ? "AVOID" : derived;
    if (aiVerdict && aiVerdict !== "AVOID" && aiVerdict !== derived) fix.push(`Verdict set to ${VERDICT_WORD(derived)} from the rule statuses (${best.present} of ${best.required} required rules present); the AI had said ${VERDICT_WORD(aiVerdict)}.`);
  } else { out.score = null; out.verdict = aiVerdict || "PARTIAL"; }
  out.grade = out.score != null ? gradeFor(out.score) : null;
  out.my_zones = Array.isArray(ai.my_zones) ? ai.my_zones.filter((z) => isObj(z) && (zid.has(z.id) || lid.has(z.id))).map((z) => ({ id: z.id, role: pyStr(G(z, "role", "CONTEXT")).slice(0, 20), matches_my_style: truthy(z.matches_my_style), comment: pyStr(G(z, "comment", "")).slice(0, 300) })) : [];
  for (const k of ["supports", "contradicts", "watch_next", "context"]) out[k] = cleanList(ai[k]);
  const sc = isObj(ai.scenarios) ? ai.scenarios : {}; out.scenarios = {};
  for (const k of ["bull", "bear", "neutral"]) {
    const one = isObj(sc[k]) ? sc[k] : {}; let lvl = toFloat(one.level), ref = null;
    if (lvl != null) { if (!(0.4 * price < lvl && lvl < 2.5 * price)) lvl = null; else { const snap = snapPrice(det, lvl, 0.35 * atr); if (snap) [lvl, ref] = snap; } }
    out.scenarios[k] = { trigger: one.trigger ?? null, path: one.path ?? null, level: lvl, ref };
  }
  const dr = isObj(ai.draw) ? ai.draw : {}, lst = (v) => (Array.isArray(v) ? v : []);
  out.draw = { zones: lst(dr.zones).filter((i) => zid.has(i)), levels: lst(dr.levels).filter((i) => lid.has(i)), trendlines: lst(dr.trendlines).filter((i) => tid.has(i)) };
  out.narrative = pyStr(G(ai, "narrative", "")).slice(0, 3000);
  const p = isObj(ai.plan) ? ai.plan : {}; let plan = null; const direction = p.direction;
  out.no_trade = direction === "NONE";
  if (direction === "LONG" || direction === "SHORT") {
    const long = direction === "LONG", want = long ? "demand" : "supply";
    let z = det.zones.find((x) => x.id === p.ref_zone) || null;
    if (!z) { const lo = toFloat(p.entry_low), hi = toFloat(p.entry_high);
      if (lo != null && hi != null) { const mid = (lo + hi) / 2, near = det.zones.filter((x) => x.type === want && x.bottom - 0.25 * atr <= mid && mid <= x.top + 0.25 * atr);
        z = near.length ? near.reduce((m, x) => (Math.abs((x.top + x.bottom) / 2 - mid) < Math.abs((m.top + m.bottom) / 2 - mid) ? x : m)) : null;
        if (z) fix.push(`The plan did not name a zone; it was matched to ${z.id} from its prices.`); } }
    if (z && z.type !== want) { fix.push(`The AI planned a ${direction} from ${z.id}, which is a ${z.type} zone. That plan was rejected.`); z = null; }
    if (!z) fix.push("The AI's plan did not map to a detected zone, so it is not drawn. The engine plan is shown instead when one exists.");
    else {
      const lo = z.bottom, hi = z.top, distal = z.distal, ref = z.in_zone ? Math.min(Math.max(price, lo), hi) : long ? hi : lo;
      const dflt = long ? pr(distal - 0.25 * atr) : pr(distal + 0.25 * atr);
      let stop = dflt, reason = `Beyond ${z.id} distal line (${fx(distal, 2, false)}) ${long ? "minus" : "plus"} 0.25 ATR`;
      const sAi = toFloat(p.stop);
      if (sAi != null) { const beyond = long ? sAi < distal : sAi > distal;
        if (beyond && Math.abs(sAi - distal) <= 1.5 * atr) { stop = pr(sAi); reason = pyStr(truthy(p.stop_reason) ? p.stop_reason : reason).slice(0, 300); }
        else if (Math.abs(sAi - dflt) > 0.02 * atr) fix.push(`Stop ${fx(sAi, 2)} was not a sensible distance beyond ${z.id}'s distal line (${fx(distal, 2)}); set to ${fx(dflt, 2)}.`); }
      const risk = Math.abs(ref - stop), targets = [], seen = new Set(); let dropped = 0;
      for (const t of (Array.isArray(p.targets) ? p.targets : []).slice(0, 4)) {
        if (!isObj(t)) continue;
        let rid = pyStr(G(t, "ref", "")).trim(), px = null;
        const lv = det.levels.find((x) => x.id === rid), oz = det.zones.find((x) => x.id === rid && x.id !== z.id);
        if (lv) px = lv.price; else if (oz) px = long ? oz.bottom : oz.top;
        else { const tp = toFloat(t.price); if (tp != null) { const snap = snapPrice(det, tp, 0.35 * atr); if (snap) [px, rid] = snap; } }
        if (px == null || seen.has(rid) || !((long && px > ref) || (!long && px < ref))) { dropped++; continue; }
        seen.add(rid); targets.push({ price: pr(px), ref: rid, reason: pyStr(G(t, "reason", "")).slice(0, 200), rr: risk ? pr(Math.abs(px - ref) / risk) : 0.0 });
      }
      if (dropped) fix.push(`${dropped} target(s) did not map to a detected level or zone on the right side of the entry and were removed.`);
      let tg = targets;
      if (!tg.length && risk > 0) { tg = targetsFor(det, ref, risk, long); fix.push("No usable target came from the AI; targets were taken from the next detected levels / zones."); }
      if (risk > 0 && tg.length) {
        const st = det.structure;
        plan = { direction, ref_zone: z.id, entry_low: lo, entry_high: hi, entry_ref: pr(ref), stop, stop_reason: reason, targets: tg.slice().sort((a, b) => a.rr - b.rr).slice(0, 3),
          invalidation: pyStr(G(p, "invalidation", "")).slice(0, 400), confirmation_needed: cleanList(p.confirmation_needed, 5),
          counter_trend: Boolean((long && st.startsWith("DOWNTREND")) || (!long && st.startsWith("UPTREND"))), source: "ai" };
        Object.assign(plan, planRoom(det, plan));
      }
    }
  }
  out.plan = plan; out.corrections = fix;
  return out;
}
export const VERDICTS = { MATCH: ["Matches your setup", "g"], PARTIAL: ["Partial match", "a"], NO_MATCH: ["Not your setup", "m"], AVOID: ["Avoid", "r"] };
export function verdictText(r) {
  if (r.ai) return VERDICTS[r.ai.verdict];
  const s = r.score; return s >= 72 ? ["Strong zone setup", "g"] : s >= 50 ? ["Developing", "a"] : ["No clear setup", "m"];
}
export function engineHeadline(det) {
  const zs = det.zones.filter((z) => z.dist_atr != null);
  if (!zs.length) return `No supply or demand zones detected with your current style. Structure: ${det.structure.toLowerCase()}.`;
  let z = zs[0]; for (const x of zs) if (x.dist_atr < z.dist_atr) z = x;
  const where = z.in_zone ? "price is inside it now" : `${fx(Math.abs(z.dist_pct), 1, false)}% (${fx(z.dist_atr, 1, false)} ATR) away`;
  return `${det.zones.length} zones detected. Nearest: ${z.id} (${z.type}, quality ${z.quality}, ${z.status.toLowerCase()}); ${where}. Structure: ${det.structure.toLowerCase()}.`;
}
export function manualSetup(name, summary, conditions, trigger, invalidation) {
  const rows = conditions.split(/\r?\n/).filter((x) => x.trim()).map((ln, i) => ({ id: "R" + String(i + 1).padStart(2, "0"), name: ln.trim().slice(0, 80), type: "other", required: true, condition: ln.trim(), evidence_hint: "Use the supplied detections and market facts." }));
  return { setup_name: name.trim(), summary: summary.trim(), conditions: rows, trigger: { description: trigger.trim(), type: "manual" }, confirmation: [],
    invalidation: invalidation.trim() ? [{ id: "I01", description: invalidation.trim() }] : [], exclusions: [], examples: [], confidence: "HIGH", source_name: "manual" };
}
// radar_scan for one stock: the row of the readiness table, and its zone history when asked for
export function radarRow(sym, df, style, liveFrac = null) {
  const det = analyzeMarket(df, style, liveFrac), plan = deterministicPlan(det), z = plan ? det.zones.find((x) => x.id === plan.ref_zone) || null : null;
  let near = null; for (const x of det.zones) if (x.dist_atr != null && (!near || x.dist_atr < near.dist_atr)) near = x;
  const w = z || near || {};
  return { symbol: sym, close: det.close, chg: det.day_chg_pct, structure: det.structure.split(" ")[0], rsi: det.rsi, zone: w.id ?? null, ztype: w.type ?? null, quality: w.quality ?? null,
    status: w.status ?? null, dist_pct: w.dist_pct ?? null, dist_atr: w.dist_atr ?? null, direction: plan ? plan.direction : null, rr: plan && plan.targets.length ? plan.targets[0].rr : null,
    room_r: plan ? plan.room_r ?? null : null, blocked: Boolean(plan && plan.blocked), score: engineScore(det, plan), n_zones: det.zones.length };
}

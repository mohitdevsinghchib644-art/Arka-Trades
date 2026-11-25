// Arka Trades · Backtest lab engine: a port of backtest_engine.py + quant_data.py (indicators, panel) used by backtest_page.py.
// Prices are kept as 32-bit floats exactly like the Python panel (numpy float32), so every rule is decided at the same precision,
// and the indicators follow pandas' own arithmetic. Monte Carlo uses numpy's random generator (PCG64, seed 11) so its numbers match.
// Pure functions: checked against the Python files on the same candles (see the test in bttest/).

export const COST_DEFAULT = 0.35;       // % per round trip: ~0.25% statutory (STT 0.1% x2, stamp, exchange, GST, DP) + ~0.1% slippage
export const WARMUP = 252;
export const STATES = ["Confirmed uptrend", "Under pressure", "Rally attempt", "Correction"];
const F = Math.fround, NaNf = () => NaN;

// ── column helpers (one stock = one column, length T) ──
const f32 = (T) => new Float32Array(T).fill(NaN), f64 = (T) => new Float64Array(T).fill(NaN);
export function shift(x, k = 1) { const T = x.length, o = new x.constructor(T).fill(NaN); if (k > 0) o.set(x.subarray(0, T - k), k); else if (k < 0) o.set(x.subarray(-k), 0); else o.set(x); return o; }
const mp = (n) => (n <= 5 ? n : Math.max(5, Math.trunc(n * 0.9)));          // quant_data._mp: a window needs 90% of its days
export function ema(x, n) {                                   // quant_data.ema: seeded with the first value; a gap keeps the previous value
  const a = 2.0 / (n + 1.0), o = f64(x.length); let prev = NaN;
  for (let t = 0; t < x.length; t++) { const xt = x[t]; if (xt === xt) { prev = prev !== prev ? xt : prev + a * (xt - prev); o[t] = prev; } }
  return o;
}
export function rollMean(x, n, minp = n) {                    // pandas rolling(n, min_periods).mean(), the same running sums
  const T = x.length, o = f64(T); let sum = 0, nobs = 0, neg = 0, ca = 0, cr = 0, same = 0, prev = T ? x[0] : NaN;
  for (let i = 0; i < T; i++) {
    if (i >= n) { const v = x[i - n]; if (v === v) { nobs--; const y = -v - cr, t = sum + y; cr = t - sum - y; sum = t; if (v < 0 || Object.is(v, -0)) neg--; } }
    const v = x[i]; if (v === v) { nobs++; const y = v - ca, t = sum + y; ca = t - sum - y; sum = t; if (v < 0 || Object.is(v, -0)) neg++; if (v === prev) same++; else same = 1; prev = v; }
    if (nobs >= minp && nobs > 0) { let r = sum / nobs; if (same >= nobs) r = prev; else if (neg === 0 && r < 0) r = 0; else if (neg === nobs && r > 0) r = 0; o[i] = r; }
  }
  return o;
}
function rollExt(x, n, minp, isMax) {                         // pandas rolling max / min (NaN ignored, min_periods counts numbers)
  const T = x.length, o = f64(T), dq = []; let cnt = 0, h = 0;
  for (let i = 0; i < T; i++) {
    const v = x[i]; if (v === v) { cnt++; while (dq.length > h && (isMax ? x[dq[dq.length - 1]] <= v : x[dq[dq.length - 1]] >= v)) dq.pop(); dq.push(i); }
    if (i >= n) { const u = x[i - n]; if (u === u) cnt--; }
    while (dq.length > h && dq[h] <= i - n) h++;
    if (cnt >= minp && cnt > 0 && dq.length > h) o[i] = x[dq[h]];
  }
  return o;
}
export const sma = (x, n) => rollMean(x, n, mp(n)), rollMax = (x, n, minp = mp(n)) => rollExt(x, n, minp, true), rollMin = (x, n, minp = mp(n)) => rollExt(x, n, minp, false);
// pandas 3 ewm(alpha, adjust=False, min_periods).mean(), gaps and all. After a missing value pandas 3 gives the old value the weight
// (1-α)^gap and renormalises, except at α = 0.5 (RSI 2), where the new value simply gets the rest; both checked against pandas 3.0.5.
export function ewm(x, alpha, minp) {
  const T = x.length, o = f64(T); if (!T) return o; const owf = 1 - alpha; let w = x[0], nobs = w === w ? 1 : 0, old = 1;
  o[0] = nobs >= minp ? w : NaN;
  for (let i = 1; i < T; i++) { const cur = x[i], obs = cur === cur; nobs += obs;
    if (w === w) { old *= owf; if (obs) { if (w !== cur) w = alpha === 0.5 ? old * w + (1 - old) * cur : (old * w + alpha * cur) / (old + alpha); old = 1; } } else if (obs) w = cur;
    o[i] = nobs >= minp ? w : NaN; }
  return o;
}
export function rsiOf(c, n = 14) {                            // quant_data.rsi
  const T = c.length, up = f64(T), dn = f64(T); for (let i = 1; i < T; i++) { const d = c[i] - c[i - 1]; if (d === d) { up[i] = d > 0 ? d : 0; dn[i] = d < 0 ? -d : 0; } }
  const U = ewm(up, 1 / n, n), D = ewm(dn, 1 / n, n), o = f64(T);
  for (let i = 0; i < T; i++) { if (U[i] !== U[i]) continue; const r = D[i] === 0 ? NaN : 100 - 100 / (1 + U[i] / D[i]); o[i] = r === r ? r : 100; }
  return o;
}
export function atrOf(h, l, c, n = 14) {                      // quant_data.atr
  const T = c.length, tr = f64(T);
  for (let i = 0; i < T; i++) { if (c[i] !== c[i]) continue; const pc = i ? c[i - 1] : NaN; let m = NaN; for (const v of [h[i] - l[i], Math.abs(h[i] - pc), Math.abs(l[i] - pc)]) if (v === v && !(v <= m)) m = v; tr[i] = m; }
  return ewm(tr, 1 / n, n);
}
const asF32 = (a) => Float32Array.from(a), asF64 = (a) => Float64Array.from(a);
const pyRound = (v) => { const r = Math.round(v); return Math.abs(v % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r; };
export function rsRating(C) {                                 // quant_data.rs_rating on the whole panel (columns), ranked 1-99 across stocks each day
  const N = C.length, T = N ? C[0].length : 0, S = C.map((c) => { const r = (k) => (i) => (i >= k ? c[i] / c[i - k] - 1.0 : NaN), a = r(63), b = r(126), d = r(189), e = r(252), o = f64(T);
    for (let i = 0; i < T; i++) { let s = 0.4 * a(i) + 0.2 * b(i) + 0.2 * d(i) + 0.2 * e(i); if (s !== s) s = 0.4 * a(i) + 0.6 * b(i); o[i] = s; } return o; });
  const out = C.map(() => f32(T));
  for (let i = 0; i < T; i++) { const xs = []; for (let j = 0; j < N; j++) if (S[j][i] === S[j][i]) xs.push([S[j][i], j]); xs.sort((p, q) => p[0] - q[0]); const m = xs.length;
    for (let k = 0; k < m;) { let e = k; while (e + 1 < m && xs[e + 1][0] === xs[k][0]) e++; const rk = (k + e + 2) / 2 / m; for (let q = k; q <= e; q++) out[xs[q][1]][i] = Math.min(99, Math.max(1, pyRound(rk * 98 + 1))); k = e + 1; } }
  return out;
}

// ── the panel: every instrument aligned to NIFTY 50's trading days (quant_data.build_panel + add_indicators) ──
// frames: {label: {d, o, h, l, c, v}} (adjusted daily candles), nifty: the same for NIFTY 50
export function buildPanel(frames, labels, nifty, years) {
  if (!nifty || nifty.d.length < 300) throw new Error("NIFTY 50 history is unavailable, so the trading calendar cannot be built.");
  const dates = nifty.d, T = dates.length, pos = new Map(dates.map((d, i) => [d, i])), syms = labels.filter((s) => frames[s]);
  const p = { dates, T, N: syms.length, symbols: syms, industry: syms.map(() => ""), years, cache: {} };
  for (const k of ["o", "h", "l", "c", "v"]) p[k] = syms.map(() => f32(T));
  syms.forEach((s, j) => { const f = frames[s]; for (let r = 0; r < f.d.length; r++) { const i = pos.get(f.d[r]); if (i === undefined) continue; for (const k of ["o", "h", "l", "c", "v"]) { const v = f[k][r]; p[k][j][i] = v == null ? NaN : v; } } });
  p.idx_c = f32(T); for (let i = 0; i < T; i++) p.idx_c[i] = nifty.c[i] == null ? NaN : nifty.c[i];
  return p;
}
export function addIndicators(p) {
  const C = p.c.map(asF64), H = p.h.map(asF64), L = p.l.map(asF64), V = p.v.map(asF64), col = (fn) => C.map((_, j) => asF32(fn(j)));
  p.ema21 = col((j) => ema(C[j], 21)); p.atr14 = col((j) => atrOf(H[j], L[j], C[j], 14)); p.atr50 = col((j) => atrOf(H[j], L[j], C[j], 50));
  p.vavg50 = col((j) => shift(sma(V[j], 50), 1));
  p.turn_cr = col((j) => { const cv = C[j].map((x, i) => x * V[j][i]), m = sma(cv, 20); return m.map((x) => x / 1e7); });
  p.hh52 = col((j) => rollMax(H[j], 252)); p.ll52 = col((j) => rollMin(L[j], 252)); p.rs = rsRating(C);
  p.cache["ema21"] = p.ema21;
  return p;
}

// ── indicators on demand (backtest_engine) ──
const cached = (p, key, fn) => (p.cache[key] || (p.cache[key] = fn()));
export const ma = (p, kind, n) => cached(p, `${kind === "EMA" ? "ema" : "sma"}${Math.trunc(n)}`, () => p.c.map((c) => asF32(kind === "EMA" ? ema(asF64(c), Math.trunc(n)) : sma(asF64(c), Math.trunc(n)))));
const priorHigh = (p, n) => cached(p, "ph" + n, () => p.h.map((h) => asF32(shift(rollMax(asF64(h), n), 1))));
const priorLow = (p, n) => cached(p, "pl" + Math.trunc(n), () => p.l.map((l) => asF32(shift(rollMin(asF64(l), Math.trunc(n)), 1))));
const highN = (p, n) => cached(p, "hn" + Math.trunc(n), () => p.h.map((h) => asF32(rollMax(asF64(h), Math.trunc(n)))));
export const lowN = (p, n) => cached(p, "ln" + Math.trunc(n), () => p.l.map((l) => asF32(rollMin(asF64(l), Math.trunc(n), 1))));
const rsi = (p, n) => cached(p, "rsi" + Math.trunc(n), () => p.c.map((c) => asF32(rsiOf(asF64(c), Math.trunc(n)))));
const idxMa = (p, kind, n) => cached(p, `idx_${kind}${Math.trunc(n)}`, () => asF32(kind === "EMA" ? ema(asF64(p.idx_c), Math.trunc(n)) : sma(asF64(p.idx_c), Math.trunc(n))));
const volAvg = (p, n) => cached(p, "vavg" + Math.trunc(n), () => p.v.map((v) => asF32(shift(sma(asF64(v), Math.trunc(n)), 1))));
const macd = (p) => cached(p, "x_macd", () => p.c.map((c) => { const x = asF64(c), a = ema(x, 12), b = ema(x, 26), line = a.map((v, i) => v - b[i]); return [line, ema(line, 9)]; }));

// a rule = for each stock column j a function of the day i -> true / false. Arithmetic on 32-bit columns is rounded to 32 bits at every step,
// as numpy does; a plain Python number next to a 32-bit column is first made 32-bit (numpy 2).
const per = (fn) => (p, a, s) => { const out = []; for (let j = 0; j < p.N; j++) { const f = fn(p, a, j), m = new Uint8Array(p.T); for (let i = 0; i < p.T; i++) m[i] = f(i) ? 1 : 0; out.push(m); } return out; };
const S1 = (x, i, k = 1) => (i >= k ? x[i - k] : NaN);
const K = ["kind", "Average", "EMA", ["EMA", "SMA"]];
export const RULES = {
  close_above_ma: ["Trend", "Close above a moving average", [K, ["n", "Length", 50, 2, 400, 1]], per((p, a, j) => { const c = p.c[j], m = ma(p, a.kind, a.n)[j]; return (i) => c[i] > m[i]; })],
  ma_above_ma: ["Trend", "Fast average above slow average", [K, ["fast", "Fast", 21, 2, 400, 1], ["slow", "Slow", 50, 2, 400, 1]], per((p, a, j) => { const x = ma(p, a.kind, a.fast)[j], y = ma(p, a.kind, a.slow)[j]; return (i) => x[i] > y[i]; })],
  ma_rising: ["Trend", "Moving average is rising", [K, ["n", "Length", 50, 2, 400, 1], ["days", "Higher than N days ago", 20, 1, 120, 1]], per((p, a, j) => { const m = ma(p, a.kind, a.n)[j], d = Math.trunc(a.days); return (i) => m[i] > S1(m, i, d); })],
  near_52w_high: ["Trend", "Within % of the 52-week high", [["pct", "Within %", 15.0, 0.0, 80.0, 1.0]], per((p, a, j) => { const c = p.c[j], h = p.hh52[j], k = F(1 - a.pct / 100); return (i) => c[i] >= F(h[i] * k); })],
  above_52w_low: ["Trend", "At least % above the 52-week low", [["pct", "At least %", 30.0, 0.0, 500.0, 5.0]], per((p, a, j) => { const c = p.c[j], l = p.ll52[j], k = F(1 + a.pct / 100); return (i) => c[i] >= F(l[i] * k); })],
  breakout_n: ["Breakout", "Close above the highest high of the previous N days", [["n", "N days", 20, 2, 260, 1]], per((p, a, j) => { const c = p.c[j], h = priorHigh(p, Math.trunc(a.n))[j]; return (i) => c[i] > h[i]; })],
  new_52w_high: ["Breakout", "New 52-week high today", [], per((p, a, j) => { const h = p.h[j], ph = priorHigh(p, 252)[j]; return (i) => h[i] >= ph[i]; })],
  close_above_prev_high: ["Breakout", "Close above the previous day's high", [], per((p, a, j) => { const c = p.c[j], h = p.h[j]; return (i) => c[i] > S1(h, i); })],
  gap_up: ["Breakout", "Opened with a gap up of at least %", [["pct", "Gap %", 1.0, 0.1, 20.0, 0.25]], per((p, a, j) => { const o = p.o[j], c = p.c[j], k = F(1 + a.pct / 100); return (i) => o[i] >= F(S1(c, i) * k); })],
  rs_min: ["Momentum", "RS rating at least", [["x", "RS rating", 80, 1, 99, 1]], per((p, a, j) => { const r = p.rs[j], x = F(a.x); return (i) => r[i] >= x; })],
  ret_n: ["Momentum", "Return over N days at least %", [["n", "N days", 63, 1, 260, 1], ["pct", "Return %", 20.0, -90.0, 500.0, 1.0]], per((p, a, j) => { const c = p.c[j], n = Math.trunc(a.n), x = F(a.pct); return (i) => F(F(F(c[i] / S1(c, i, n)) - 1) * 100) >= x; })],
  day_change: ["Momentum", "Today's change between % and %", [["lo", "From %", 2.0, -20.0, 20.0, 0.25], ["hi", "To %", 6.0, -20.0, 20.0, 0.25]], per((p, a, j) => { const c = p.c[j], lo = F(a.lo), hi = F(a.hi); return (i) => { const v = F(F(F(c[i] / S1(c, i)) - 1) * 100); return v >= lo && v <= hi; }; })],
  rsi_above: ["Momentum", "RSI above", [["n", "RSI length", 14, 2, 50, 1], ["x", "Above", 55.0, 1.0, 99.0, 1.0]], per((p, a, j) => { const r = rsi(p, a.n)[j], x = F(a.x); return (i) => r[i] > x; })],
  rsi_below: ["Momentum", "RSI below", [["n", "RSI length", 14, 2, 50, 1], ["x", "Below", 75.0, 1.0, 99.0, 1.0]], per((p, a, j) => { const r = rsi(p, a.n)[j], x = F(a.x); return (i) => r[i] < x; })],
  touch_ma: ["Pullback", "Low came within % of a moving average in the last N days", [K, ["n", "Length", 21, 2, 400, 1], ["pct", "Within %", 1.5, 0.0, 10.0, 0.25], ["days", "In the last N days", 3, 1, 20, 1]],
    per((p, a, j) => { const l = p.l[j], m = ma(p, a.kind, a.n)[j], q = f64(p.T); for (let i = 0; i < p.T; i++) q[i] = F(l[i] / m[i]); const r = rollMin(q, Math.trunc(a.days)), x = 1 + a.pct / 100; return (i) => r[i] <= x; })],
  pullback_depth: ["Pullback", "Pulled back between % and % from the N-day high", [["n", "N days", 20, 3, 260, 1], ["lo", "At least %", 3.0, 0.0, 60.0, 0.5], ["hi", "At most %", 12.0, 0.0, 80.0, 0.5]],
    per((p, a, j) => { const c = p.c[j], h = highN(p, a.n)[j], lo = F(a.lo), hi = F(a.hi); return (i) => { const v = F(F(1 - F(c[i] / h[i])) * 100); return v >= lo && v <= hi; }; })],
  green: ["Candle", "Green candle (close above open)", [], per((p, a, j) => { const c = p.c[j], o = p.o[j]; return (i) => c[i] > o[i]; })],
  close_top: ["Candle", "Close in the top % of the day's range", [["pct", "Top %", 30.0, 1.0, 100.0, 5.0]], per((p, a, j) => { const c = p.c[j], l = p.l[j], h = p.h[j], k = F(1 - a.pct / 100); return (i) => F(c[i] - l[i]) >= F(k * F(h[i] - l[i])); })],
  inside_day: ["Candle", "Inside day (range within the previous day's)", [], per((p, a, j) => { const h = p.h[j], l = p.l[j]; return (i) => h[i] <= S1(h, i) && l[i] >= S1(l, i); })],
  nr7: ["Candle", "Narrowest range of the last 7 days (NR7)", [], per((p, a, j) => { const h = p.h[j], l = p.l[j], rg = f64(p.T); for (let i = 0; i < p.T; i++) rg[i] = F(h[i] - l[i]); const m = rollMin(rg, 7); return (i) => rg[i] <= m[i]; })],
  vol_mult: ["Volume", "Volume at least × the 50-day average", [["x", "Times average", 1.5, 0.1, 20.0, 0.1]], per((p, a, j) => { const v = p.v[j], av = p.vavg50[j], x = F(a.x); return (i) => v[i] >= F(x * av[i]); })],
  vol_dry: ["Volume", "Volume dried up: at most × average on each of the N days before today", [["x", "Times average", 0.8, 0.1, 5.0, 0.05], ["days", "N days", 3, 1, 20, 1]],
    per((p, a, j) => { const v = p.v[j], av = p.vavg50[j], q = f64(p.T); for (let i = 0; i < p.T; i++) q[i] = F(v[i] / av[i]); const r = shift(rollMax(q, Math.trunc(a.days)), 1); return (i) => r[i] <= a.x; })],
  turnover_min: ["Volume", "Trades at least ₹ crore a day (20-day average)", [["x", "₹ crore", 5.0, 0.0, 5000.0, 1.0]], per((p, a, j) => { const t = p.turn_cr[j], x = F(a.x); return (i) => t[i] >= x; })],
  price_min: ["Volume", "Price at least ₹", [["x", "₹", 20.0, 0.0, 100000.0, 5.0]], per((p, a, j) => { const c = p.c[j], x = F(a.x); return (i) => c[i] >= x; })],
  atr_contract: ["Volatility", "Volatility contracted: ATR 14 ÷ ATR 50 at most (before today)", [["x", "Ratio", 0.80, 0.2, 2.0, 0.05]], per((p, a, j) => { const a1 = p.atr14[j], a2 = p.atr50[j]; return (i) => (i >= 1 ? F(a1[i - 1] / a2[i - 1]) : NaN) <= a.x; })],
  range_tight: ["Volatility", "Tight base: range of the previous N days at most % of price", [["n", "N days", 15, 3, 120, 1], ["pct", "Range %", 12.0, 1.0, 80.0, 0.5]],
    per((p, a, j) => { const ph = priorHigh(p, Math.trunc(a.n))[j], pl = priorLow(p, a.n)[j], c = p.c[j], k = F(a.pct / 100); return (i) => F(ph[i] - pl[i]) <= F(k * c[i]); })],
  not_extended: ["Volatility", "Not extended: at most % above a moving average", [K, ["n", "Length", 21, 2, 400, 1], ["pct", "At most %", 8.0, 0.0, 100.0, 0.5]],
    per((p, a, j) => { const c = p.c[j], m = ma(p, a.kind, a.n)[j], x = F(a.pct); return (i) => F(F(F(c[i] / m[i]) - 1) * 100) <= x; })],
  nifty_above_ma: ["Market", "NIFTY 50 above its moving average", [K, ["n", "Length", 50, 2, 400, 1]], per((p, a, j) => { const c = p.idx_c, m = idxMa(p, a.kind, a.n); return (i) => c[i] > m[i]; })],
  regime_in: ["Market", "Market regime is one of", [["states", "States", ["Confirmed uptrend", "Under pressure"], STATES]], (p, a, s) => p.c.map((_, j) => { const m = new Uint8Array(p.T); for (let i = 0; i < p.T; i++) m[i] = s ? (a.states.includes(s[i]) ? 1 : 0) : 1; return m; })],
  // extra conditions the plain-words reader can produce (setup_language.EXTRA)
  x_macd: ["Momentum", "MACD", [], per((p, a, j) => { const [m, g] = macd(p)[j]; if (a.how === "zero") return (i) => m[i] > 0; if (a.how === "above") return (i) => m[i] > g[i]; return (i) => m[i] > g[i] && S1(m, i) <= S1(g, i); })],
  x_close_below_ma: ["Trend", "Close below a moving average", [], per((p, a, j) => { const c = p.c[j], m = ma(p, a.kind, a.n)[j]; return (i) => c[i] < m[i]; })],
  x_cross_above_ma: ["Trend", "Close crosses above a moving average", [], per((p, a, j) => { const c = p.c[j], m = ma(p, a.kind, a.n)[j]; return (i) => c[i] > m[i] && S1(c, i) <= S1(m, i); })],
  x_ma_above_ma: ["Trend", "One average above another", [], per((p, a, j) => { const x = ma(p, a.k1, a.n1)[j], y = ma(p, a.k2, a.n2)[j]; return (i) => x[i] > y[i]; })],
  x_ma_cross_up: ["Trend", "One average crosses above another", [], per((p, a, j) => { const x = ma(p, a.k1, a.n1)[j], y = ma(p, a.k2, a.n2)[j]; return (i) => x[i] > y[i] && S1(x, i) <= S1(y, i); })],
  x_beat_nifty: ["Momentum", "Return beats NIFTY 50", [], per((p, a, j) => { const c = p.c[j], ic = p.idx_c, n = Math.trunc(a.n); return (i) => F(F(c[i] / S1(c, i, n)) - 1) > ic[i] / S1(ic, i, n) - 1; })],
  x_red: ["Candle", "Red candle", [], per((p, a, j) => { const c = p.c[j], o = p.o[j]; return (i) => c[i] < o[i]; })],
  x_vol_mult_n: ["Volume", "Volume against its N-day average", [], per((p, a, j) => { const v = p.v[j], av = volAvg(p, a.n)[j], x = F(a.x); return (i) => v[i] >= F(x * av[i]); })],
  x_vol_gt_prev: ["Volume", "Volume above the previous day", [], per((p, a, j) => { const v = p.v[j]; return (i) => v[i] > S1(v, i); })],
  x_rsi_cross_up: ["Momentum", "RSI crosses above a level", [], per((p, a, j) => { const r = rsi(p, a.n)[j], x = F(a.x); return (i) => r[i] > x && S1(r, i) <= x; })],
  x_rsi_between: ["Momentum", "RSI between two levels", [], per((p, a, j) => { const r = rsi(p, a.n)[j], lo = F(a.lo), hi = F(a.hi); return (i) => r[i] >= lo && r[i] <= hi; })],
  x_streak: ["Candle", "Closes lower or higher several days running", [], per((p, a, j) => { const c = p.c[j], hit = f64(p.T); for (let i = 0; i < p.T; i++) hit[i] = (a.dir === "down" ? c[i] < S1(c, i) : c[i] > S1(c, i)) ? 1 : 0; const m = rollMin(hit, Math.trunc(a.n)); return (i) => m[i] >= 1; })],
  x_close_above_prev_close: ["Candle", "Close above the previous close", [], per((p, a, j) => { const c = p.c[j]; return (i) => c[i] > S1(c, i); })],
  x_price_max: ["Volume", "Price at most", [], per((p, a, j) => { const c = p.c[j], x = F(a.x); return (i) => c[i] <= x; })],
};
export const ruleMask = (p, rule, state = null) => RULES[rule.type][3](p, rule, state);
export function signals(p, rules, state = null, cols = null) {   // for each stock: true where every condition is true at that day's close
  const masks = rules.map((r) => ruleMask(p, r, state)), keep = cols ? new Set(cols) : null;
  return p.c.map((c, j) => { const s = new Uint8Array(p.T); if (keep && !keep.has(j)) return s; for (let i = WARMUP; i < p.T; i++) { if (c[i] !== c[i]) continue; let ok = 1; for (const m of masks) if (!m[j][i]) { ok = 0; break; } s[i] = ok; } return s; });
}

// ── trades: every signal on its own (make_trades) ──
export const EXIT_DEFAULT = { entry: "Next day's open", stop: "Signal-day low", stop_n: 5, stop_atr: 1.5, stop_pct: 7.0, max_risk: 10.0, trail_kind: "EMA", trail_n: 21, target_r: 0.0, max_days: 40, breakeven_r: 0.0, gap_max: 4.0 };
export const ENTRIES = ["Next day's open", "Next day, only above the signal day's high"];
export const STOPS = ["Signal-day low", "Lowest low of the last N days", "ATR multiple", "Percent"];
const SUSPECT_DOWN = -0.35, SUSPECT_UP = 0.5;
const ffill = (a) => { const o = asF64(a); for (let i = 1; i < o.length; i++) if (!isFinite(o[i])) o[i] = o[i - 1]; return o; };
export function makeTrades(p, sig, ex, costPct = COST_DEFAULT) {
  const T = p.T, trail = Math.trunc(ex.trail_n || 0), emaT = trail ? ma(p, ex.trail_kind || "EMA", trail) : null, cost = costPct / 100.0, gapMax = (ex.gap_max ?? 4.0) / 100.0;
  const maxDays = Math.trunc(ex.max_days || 60), tgtR = Number(ex.target_r || 0), beR = Number(ex.breakeven_r || 0), maxRisk = (ex.max_risk ?? 10.0) / 100.0, buyStop = (ex.entry || ENTRIES[0]) === ENTRIES[1];
  const stopLow = ex.stop === STOPS[1] ? lowN(p, ex.stop_n ?? 5) : null, rows = []; let dropped = 0;
  for (let j = 0; j < p.N; j++) {
    const sj = sig[j]; let any = false; for (let i = 0; i < T; i++) if (sj[i]) { any = true; break; } if (!any) continue;
    const o = ffill(p.o[j]), h = ffill(p.h[j]), l = ffill(p.l[j]), c = ffill(p.c[j]), O = p.o[j], sus = new Int32Array(T);
    for (let i = 1; i < T; i++) { const rr = c[i] / c[i - 1] - 1; sus[i] = sus[i - 1] + (rr < SUSPECT_DOWN || rr > SUSPECT_UP ? 1 : 0); }
    const et = trail ? asF64(emaT[j]) : null; let busyUntil = -1;
    for (let t = 0; t < T; t++) { if (!sj[t]) continue;
      const ei = t + 1; if (ei >= T || ei <= busyUntil || !isFinite(O[ei])) continue;
      if (o[ei] > c[t] * (1 + gapMax)) continue;                              // chasing a big gap: skip
      let entry; if (buyStop) { const trig = h[t]; if (!(h[ei] > trig)) continue; entry = Math.max(o[ei], trig); } else entry = o[ei];
      let stop = ex.stop === STOPS[0] ? l[t] : ex.stop === STOPS[1] ? stopLow[j][t] : ex.stop === STOPS[2] ? entry - (ex.stop_atr ?? 1.5) * p.atr14[j][t] : entry * (1 - (ex.stop_pct ?? 7.0) / 100);
      if (!isFinite(stop) || stop >= entry * 0.998) continue;                 // opened at / below the stop: no trade
      stop = Math.max(stop, entry * (1 - maxRisk)); const risk = entry - stop, end = Math.min(T - 1, ei + maxDays - 1), n = end - ei + 1;
      const lo = (k) => l[ei + k], hi = (k) => h[ei + k], op = (k) => o[ei + k], cl = (k) => c[ei + k], cand = [];
      let kb = n; if (beR > 0) { kb = n; for (let k = 0; k < n; k++) if (hi(k) >= entry + beR * risk) { kb = k; break; } }
      let s1 = n; for (let k = 0; k < n; k++) if (lo(k) <= stop) { s1 = k; break; }
      if (s1 <= kb) { if (s1 < n) cand.push([s1, 1, s1 > 0 ? Math.min(op(s1), stop) : stop, "Stop"]); }
      else { for (let k = kb + 1; k < n; k++) if (lo(k) <= entry) { cand.push([k, 1, Math.min(op(k), entry), "Breakeven stop"]); break; } }
      if (tgtR > 0) for (let k = 0; k < n; k++) if (hi(k) >= entry + tgtR * risk) { cand.push([k, 2, k > 0 ? Math.max(op(k), entry + tgtR * risk) : entry + tgtR * risk, "Target"]); break; }
      if (trail) for (let k = 0; k < n; k++) if (cl(k) < et[ei + k]) { const nm = `Close below ${trail} ${ex.trail_kind || "EMA"}`; if (ei + k + 1 < T) cand.push([k + 1, 0, o[ei + k + 1], nm]); else cand.push([k, 3, cl(k), nm]); break; }
      cand.push([n - 1, 4, cl(n - 1), end < T - 1 ? "Time stop" : "Open at end"]);
      let best = cand[0]; for (const x of cand) if (x[0] < best[0] || (x[0] === best[0] && x[1] < best[1])) best = x;
      const [k, pri, px, why] = best, xi = ei + k;
      if (sus[xi] - sus[t] > 0) { dropped++; busyUntil = xi; continue; }      // a suspect candle inside the trade: do not trust it
      const last = pri === 0 && xi > ei ? xi - 1 : xi, rG = (px - entry) / risk;   // exited at the open: that day's range never happened to us
      let mae = Infinity, mfe = -Infinity; for (let i = ei; i <= last; i++) { if (l[i] < mae) mae = l[i]; if (h[i] > mfe) mfe = h[i]; }
      mae = last >= ei ? (mae - entry) / risk : Math.min(0, rG); mfe = last >= ei ? (mfe - entry) / risk : Math.max(0, rG);
      if (why.includes("top")) mae = Math.max(mae, rG);
      mae = Math.min(mae, rG, 0.0); mfe = Math.max(mfe, rG, 0.0);
      const C = p.c[j][t], e21 = p.ema21[j][t];
      rows.push({ j, sig_i: t, entry_i: ei, exit_i: xi, entry, stop, exit: px, reason: why, r_gross: rG, r: (px - entry - cost * entry) / risk, ret_pct: (px / entry - 1 - cost) * 100, days: xi - ei + 1, mae, mfe,
        rs: p.rs[j][t], ext21: F(F(F(C / e21) - 1) * 100), risk_pct: (risk / entry) * 100, exit_open: pri === 0,
        symbol: p.symbols[j], sector: p.industry[j], entry_date: p.dates[ei], exit_date: p.dates[xi], open_at_end: why === "Open at end" });
      busyUntil = xi; }
  }
  rows.dropped = dropped;
  return rows;
}

// ── the account (portfolio): capital, risk per trade, positions, cash ──
const rsDesc = (x, y) => (x !== x ? (y !== y ? 0 : 1) : y !== y ? -1 : y - x);      // highest RS first; no rating (NaN) last, as pandas sorts
export const EXPOSURE = { "Confirmed uptrend": 1.0, "Under pressure": 0.5, "Rally attempt": 0.25, Correction: 0.0 };
export const book = (o = {}) => ({ capital: 1_000_000.0, risk_pct: 1.0, max_pos: 8, max_pos_pct: 25.0, regime_mode: "Off", ...o });
// -> { curve: {d, v}, taken }. Inside a day: exits at the open, then new entries, then exits during the day or at the close.
export function portfolio(p, trades, bk, startI, endI = null, regime = null) {
  const T = p.T; endI = endI == null ? T - 1 : endI; const C = p.c; let cash = Number(bk.capital); const taken = [], lastPx = new Map(), eq = f64(T); let open = [];
  const tr = trades.filter((t) => t.entry_i >= startI && t.entry_i <= endI).map((t, k) => [t, k]).sort((a, b) => a[0].entry_i - b[0].entry_i || rsDesc(a[0].rs, b[0].rs) || a[1] - b[1]).map((x) => x[0]);
  const byDay = new Map(); for (const t of tr) { if (!byDay.has(t.entry_i)) byDay.set(t.entry_i, []); byDay.get(t.entry_i).push(t); }
  let equity = cash;
  const closeOut = (ps) => { const fee = ps.qty * ps.entry * ps.cost; cash += ps.qty * ps.exit - fee; ps.pnl = ps.qty * (ps.exit - ps.entry) - fee; taken.push(ps); };
  // Python quirk kept on purpose: the value of an open position is a numpy float32, and in numpy 2 a plain Python number next to a float32
  // becomes float32, so while a position is open the account value (and the sizing that uses it) is a 32-bit number. eq32 tracks that.
  let eq32 = false;
  for (let d = startI; d <= endI; d++) {
    open = open.filter((ps) => (ps.exit_i === d && ps.exit_open ? (closeOut(ps), false) : true));         // 1. exits at this morning's open
    const g = byDay.get(d);
    if (g) { let scale = 1.0;                                                                               // 2. new entries
      if (regime && bk.regime_mode !== "Off") { const st = d - 1 >= 0 ? regime[d - 1] : null; scale = bk.regime_mode === "Skip corrections" ? (st === "Correction" ? 0 : 1) : st ? EXPOSURE[st] ?? 1 : 1; }
      let slots = bk.max_pos - open.length; const held = new Set(open.map((ps) => ps.j));
      for (const r of g) { if (slots <= 0 || scale <= 0) break; if (held.has(r.j)) continue;
        const rk = eq32 ? F(F(F(equity * F(bk.risk_pct)) / 100) * F(scale)) : ((equity * bk.risk_pct) / 100) * scale, room = eq32 ? F(F(equity * F(bk.max_pos_pct)) / 100) : (equity * bk.max_pos_pct) / 100;
        let qty = Math.floor(eq32 ? F(rk / F(Math.max(1e-9, r.entry - r.stop))) : rk / Math.max(1e-9, r.entry - r.stop));
        qty = Math.min(qty, Math.floor(eq32 ? F(room / F(r.entry)) : room / r.entry), Math.floor(cash / r.entry)); if (qty < 1) continue;
        const ps = { ...r, qty, cost: ((r.r_gross - r.r) * (r.entry - r.stop)) / r.entry, value: qty * r.entry, equity_at_entry: equity };
        cash -= qty * r.entry; open.push(ps); held.add(r.j); slots--; } }
    open = open.filter((ps) => (ps.exit_i === d && (!ps.exit_open || ps.entry_i === d) ? (closeOut(ps), false) : true));   // 3. stops, targets, time stops
    let mtm = 0.0, m32 = false;
    for (const ps of open) { const px = C[ps.j][d]; if (isFinite(px)) lastPx.set(ps.j, px); const has = lastPx.has(ps.j), term = has ? F(ps.qty * lastPx.get(ps.j)) : ps.qty * ps.entry;
      if (m32 || has) { mtm = F(mtm + F(term)); m32 = true; } else mtm += term; }
    equity = m32 ? F(F(cash) + mtm) : cash + mtm; eq32 = m32; eq[d] = equity;
  }
  for (const ps of open) { const px = lastPx.has(ps.j) ? lastPx.get(ps.j) : ps.entry; ps.pnl = ps.qty * (px - ps.entry); ps.exit = px; ps.reason = "Open at end"; taken.push(ps); }
  const cv = Array.from(eq.subarray(startI, endI + 1)); for (let i = 1; i < cv.length; i++) if (cv[i] !== cv[i]) cv[i] = cv[i - 1];
  for (const t of taken) t.pnl_pct_of_equity = (t.pnl / t.equity_at_entry) * 100;
  const tk = taken.map((t, k) => [t, k]).sort((a, b) => a[0].entry_i - b[0].entry_i || a[1] - b[1]).map((x) => x[0]);
  return { curve: { d: p.dates.slice(startI, endI + 1), v: cv }, taken: tk };
}

// ── statistics ──
function npSum(a, lo = 0, n = a.length) {                    // numpy's pairwise sum (what pandas Series.sum / mean use)
  if (n < 8) { let r = 0.0; for (let i = lo; i < lo + n; i++) r += a[i]; return r; }
  if (n <= 128) { const r = [a[lo], a[lo + 1], a[lo + 2], a[lo + 3], a[lo + 4], a[lo + 5], a[lo + 6], a[lo + 7]]; let i = 8;
    for (; i < n - (n % 8); i += 8) for (let k = 0; k < 8; k++) r[k] += a[lo + i + k];
    let res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7])); for (; i < n; i++) res += a[lo + i]; return res; }
  let n2 = Math.floor(n / 2); n2 -= n2 % 8; return npSum(a, lo, n2) + npSum(a, lo + n2, n - n2);
}
const npMean = (a) => (a.length ? npSum(a) / a.length : NaN);
const kSum = (a) => { let s = 0, c = 0; for (const x of a) { const y = x - c, t = s + y; c = t - s - y; s = t; } return s; };      // pandas groupby sum / mean (compensated)
const median = (a) => { const s = a.slice().sort((x, y) => x - y), m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const std1 = (a) => { const n = a.length; if (n < 2) return NaN; const m = npSum(a) / n; return Math.sqrt(npSum(a.map((x) => (m - x) ** 2)) / (n - 1)); };      // pandas std(ddof=1)
export function tradeStats(r, days = null) {
  r = r.filter((x) => x != null && x === x); const n = r.length; if (!n) return { n: 0 };
  const w = r.filter((x) => x > 0), ls = r.filter((x) => x <= 0), sd = n > 1 ? std1(r) : NaN; let streak = 0, best = 0; for (const x of r) { streak = x <= 0 ? streak + 1 : 0; best = Math.max(best, streak); }
  const lsum = npSum(ls), e = npMean(r);
  const out = { n, win: (w.length / n) * 100, avg_win: w.length ? npMean(w) : 0.0, avg_loss: ls.length ? npMean(ls) : 0.0, exp: e, median: median(r), pf: lsum < 0 ? npSum(w) / Math.abs(lsum) : Infinity,
    sqn: sd && sd > 0 ? (Math.sqrt(Math.min(n, 100)) * e) / sd : NaN, t: sd && sd > 0 ? (Math.sqrt(n) * e) / sd : NaN, total_r: npSum(r), best: Math.max(...r), worst: Math.min(...r), max_losing_streak: best };
  if (days) out.avg_days = npMean(days);
  return out;
}
const dayNum = (d) => Date.parse(d + "T00:00:00Z") / 864e5;
export function curveStats(curve) {                           // curve: {d, v}; NaN days dropped
  const d = [], v = []; curve.d.forEach((x, i) => { if (curve.v[i] === curve.v[i] && curve.v[i] != null) { d.push(x); v.push(curve.v[i]); } }); if (v.length < 2) return null;
  const yrs = Math.max(1e-9, (dayNum(d[d.length - 1]) - dayNum(d[0])) / 365.25), tot = v[v.length - 1] / v[0] - 1;
  let peak = -Infinity; const dd = v.map((x) => { peak = Math.max(peak, x); return x / peak - 1; });
  const rets = []; for (let i = 1; i < v.length; i++) rets.push(v[i] / v[i - 1] - 1);
  const cagr = tot > -1 ? Math.pow(1 + tot, 1 / yrs) - 1 : -1; let ti = 0; for (let i = 1; i < dd.length; i++) if (dd[i] < dd[ti]) ti = i; let pi = 0; for (let i = 1; i <= ti; i++) if (v[i] > v[pi]) pi = i;
  const sdv = std1(rets), mdd = dd[ti];
  return { total: tot * 100, cagr: cagr * 100, maxdd: mdd * 100, years: yrs, calmar: mdd < 0 ? cagr / Math.abs(mdd) : NaN, sharpe: sdv > 0 ? (npMean(rets) / sdv) * Math.sqrt(252) : NaN, dd_peak: d[pi], dd_trough: d[ti], dd: { d, v: dd } };
}

// numpy's Generator(PCG64(SeedSequence(seed))) and Generator.choice(..., replace=True), so the shuffles are the same as Python's
const M32 = 0xffffffffn, M64 = (1n << 64n) - 1n, M128 = (1n << 128n) - 1n, PCG_MULT = 0x2360ED051FC65DA44385DF649FCCF645n;
function seedSequence(seed, nWords64) {
  const INIT_A = 0x43b0d7e5, MULT_A = 0x931e8875, INIT_B = 0x8b51f9dd, MULT_B = 0x58f38ded, ML = 0xca01f9dd, MR = 0x4973f715, mul = (a, b) => Math.imul(a, b) >>> 0;
  let hc = INIT_A; const hashmix = (v) => { v = (v ^ hc) >>> 0; hc = mul(hc, MULT_A); v = mul(v, hc); return (v ^ (v >>> 16)) >>> 0; };
  const mix = (x, y) => { let r = (mul(ML, x) - mul(MR, y)) >>> 0; return (r ^ (r >>> 16)) >>> 0; };
  const ent = []; let s = BigInt(seed); do { ent.push(Number(s & M32)); s >>= 32n; } while (s > 0n);
  const pool = [0, 0, 0, 0]; for (let i = 0; i < 4; i++) pool[i] = hashmix(i < ent.length ? ent[i] : 0);
  for (let a = 0; a < 4; a++) for (let b = 0; b < 4; b++) if (a !== b) pool[b] = mix(pool[b], hashmix(pool[a]));
  for (let a = 4; a < ent.length; a++) for (let b = 0; b < 4; b++) pool[b] = mix(pool[b], hashmix(ent[a]));
  let hb = INIT_B; const w32 = []; for (let i = 0; i < nWords64 * 2; i++) { let v = (pool[i % 4] ^ hb) >>> 0; hb = mul(hb, MULT_B); v = mul(v, hb); w32.push((v ^ (v >>> 16)) >>> 0); }
  const out = []; for (let i = 0; i < nWords64; i++) out.push((BigInt(w32[2 * i + 1]) << 32n) | BigInt(w32[2 * i])); return out;
}
export class PCG64 {
  constructor(seed) { const v = seedSequence(seed, 4), initstate = (v[0] << 64n) | v[1], initseq = (v[2] << 64n) | v[3]; this.inc = ((initseq << 1n) | 1n) & M128; this.state = 0n; this.step(); this.state = (this.state + initstate) & M128; this.step(); this.has32 = false; this.buf = 0; }
  step() { this.state = (this.state * PCG_MULT + this.inc) & M128; }
  next64() { this.step(); const s = this.state, rot = Number(s >> 122n), x = ((s >> 64n) ^ s) & M64; return rot ? ((x >> BigInt(rot)) | (x << BigInt(64 - rot))) & M64 : x; }
  next32() { if (this.has32) { this.has32 = false; return this.buf; } const x = this.next64(); this.has32 = true; this.buf = Number(x >> 32n); return Number(x & M32); }
  bounded(rng) {                                              // integers in [0, rng] (Lemire, numpy's buffered_bounded_lemire_uint32)
    if (rng === 0) return 0; if (rng === 0xffffffff) return this.next32(); const ex = rng + 1, mulHi = (x) => { const m = BigInt(x) * BigInt(ex); return [Number(m >> 32n), Number(m & M32)]; };
    let [hiw, left] = mulHi(this.next32()); if (left < ex) { const thr = ((0xffffffff - rng) >>> 0) % ex; while (left < thr) [hiw, left] = mulHi(this.next32()); } return hiw;
  }
}
const pct = (a, q) => { const s = Float64Array.from(a).sort(), x = (q / 100) * (s.length - 1), lo = Math.floor(x), hi = Math.ceil(x); return s[lo] + (s[hi] - s[lo]) * (x - lo); };    // numpy percentile, linear
export function monteCarlo(r, riskPct, nTrades = null, sims = 3000, seed = 11) {   // the same trades in a different order, many times
  r = r.filter((x) => x != null && isFinite(x)); if (r.length < 10) return null;
  const n = nTrades || r.length, rng = new PCG64(seed), f = riskPct / 100, mdd = new Float64Array(sims), fin = new Float64Array(sims), streak = new Float64Array(sims);
  for (let s = 0; s < sims; s++) { let e = 1, pk = 1, m = 0, cur = 0, best = 0;
    for (let k = 0; k < n; k++) { const x = r[rng.bounded(r.length - 1)]; e *= 1 + f * x; if (e > pk) pk = e; const dd = e / pk - 1; if (dd < m) m = dd; cur = x <= 0 ? cur + 1 : 0; if (cur > best) best = cur; }
    mdd[s] = m * 100; fin[s] = (e - 1) * 100; streak[s] = best; }
  let loss = 0; for (const x of fin) if (x < 0) loss++;
  return { n, sims, dd_med: pct(mdd, 50), dd_p90: pct(mdd, 10), dd_p95: pct(mdd, 5), ret_med: pct(fin, 50), ret_p05: pct(fin, 5), ret_p95: pct(fin, 95), p_loss: (loss / sims) * 100, streak_med: pct(streak, 50), streak_p90: pct(streak, 90), mdd_hist: mdd };
}
export function halves(tr) {                                  // first half against second half of the test period
  if (!tr || tr.length < 20) return null; let a = Infinity, b = -Infinity; for (const t of tr) { a = Math.min(a, t.entry_i); b = Math.max(b, t.entry_i); } const mid = Math.floor((a + b) / 2);
  const A = tr.filter((t) => t.entry_i <= mid).map((t) => t.r), B = tr.filter((t) => t.entry_i > mid).map((t) => t.r);
  return { a_n: A.length, a_exp: A.length ? npMean(A) : NaN, b_n: B.length, b_exp: B.length ? npMean(B) : NaN, mid };
}
export function breakdown(tr, by) {                           // -> [[key, {trades, win, exp, total}]] in sorted key order (pandas groupby)
  const g = new Map(); for (const t of tr) { const k = t[by]; if (!g.has(k)) g.set(k, []); g.get(k).push(t.r); }
  return [...g.keys()].sort((x, y) => (x < y ? -1 : x > y ? 1 : 0)).map((k) => { const a = g.get(k); return [k, { trades: a.length, win: (a.filter((x) => x > 0).length / a.length) * 100, exp: kSum(a) / a.length, total: kSum(a), best: Math.max(...a), worst: Math.min(...a) }]; });
}
export const perStock = (tr) => breakdown(tr, "symbol").map((x, i) => [x, i]).sort((a, b) => b[0][1].total - a[0][1].total || a[1] - b[1]).map((x) => x[0]);
export { npSum, npMean };
export function verdict(res) {                                // (tone, headline, explanation) in plain words
  const tr = res.trades, st = tr && tr.length ? tradeStats(tr.map((t) => t.r)) : { n: 0 }, n = st.n || 0;
  if (n < 30) return ["warn", "Not enough trades to judge", `Only ${n} trades. You need roughly 100 before win rate and expectancy settle, so treat every number below as a rough hint. Test more stocks or more years, or relax a condition.`];
  const e = st.exp, t = st.t ?? 0, wf = res.mode === "walk", wfe = res.wfe ?? NaN, small = n < 100 ? " With under 100 trades the estimate is still loose." : "", f2 = (v) => fxs(v, 2), f1 = (v) => fx1(v);
  if (e > 0.15 && t >= 2 && (!wf || (isFinite(wfe) && wfe >= 0.5)))
    return ["good", "Edge holds" + (wf ? " on unseen data" : " in this test"), `${f2(e)}R per trade over ${n} trades (t = ${f1(t)})` + (wf ? `, walk-forward efficiency ${wfe.toFixed(2)}.` : ".") + " Statistically this is unlikely to be luck." + small + " Check the first-half / second-half and nearby-settings tables before trusting it."];
  if (e > 0 && t >= 1) return ["warn", "Small or uncertain edge", `${f2(e)}R per trade over ${n} trades (t = ${f1(t)})` + (wf && isFinite(wfe) ? `, walk-forward efficiency ${wfe.toFixed(2)}` : "") + ". Positive but not convincing." + small + " Check the regime split: it may work only in uptrends."];
  return ["bad", "No reliable edge", `${f2(e)}R per trade over ${n} trades after costs. Trading these rules as they stand would most likely lose money or tread water.`];
}
import { fx } from "./rsengine.js";
const fxs = (v, d) => fx(v, d, false, true), fx1 = (v) => fx(v, 1, false);

// ── a run: your exact rules, no tuning (run_single) ──
export function runSingle(p, rules, ex, bk, costPct, state = null, cols = null, startYear = null) {
  const sig = signals(p, rules, state, cols), all = makeTrades(p, sig, ex, costPct), dropped = all.dropped || 0;
  let s0 = WARMUP; if (startYear) { const want = `${startYear}-01-01`; let k = 0; while (k < p.T && p.dates[k] < want) k++; s0 = Math.max(s0, k); }
  const tr = all.filter((t) => t.entry_i >= s0), { curve, taken } = portfolio(p, tr, bk, s0, null, state);
  let n = 0; for (const s of sig) for (let i = s0; i < p.T; i++) n += s[i];
  return { mode: "single", signals: n, trades: tr, curve, taken, start_i: s0, variants: 1, dropped };
}

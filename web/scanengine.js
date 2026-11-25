// Arka Trades · the setup scanner's rule engine: a port of smart_scan_page.py (rule catalogue, indicators, 26 rule types,
// "close enough" tolerance, either-or groups, fit score). Pure functions, checked against the Python file on the same candles.
import { fx, rsiWilder } from "./rsengine.js";

// field: [name, label, kind, default, a, b]   kind f = number (min, max) · i = whole number (min, max) · s = choice (options) · b = yes / no
export const MA_KINDS = ["EMA", "SMA"];
export const CANDLES = ["hammer", "bullish engulfing", "bearish engulfing", "doji", "strong close", "weak close", "green candle", "red candle"];
export const RULES = {
  pullback: { label: "Pullback on low volume", fields: [["nmin", "Min candles", "i", 2, 1, 10], ["nmax", "Max candles", "i", 4, 1, 10], ["max_up", "Up candles allowed", "i", 0, 0, 5], ["small_up", "Ignore up candles smaller than %", "f", 4.0, 0.0, 15.0],
    ["vol", "Avg volume ≤ × 20-day avg", "f", 0.8, 0.1, 2.0], ["each", "Every candle below avg volume", "b", false, null, null], ["dmin", "Depth min %", "f", 0.0, 0.0, 50.0], ["dmax", "Depth max % (0 = any)", "f", 0.0, 0.0, 60.0]] },
  lowvol: { label: "Consecutive low-volume candles", fields: [["nmin", "Min candles", "i", 3, 1, 15], ["nmax", "Max candles (0 = any)", "i", 0, 0, 30], ["x", "Volume below × 20-day avg", "f", 1.0, 0.1, 2.0]] },
  runup: { label: "Prior run-up (momentum)", fields: [["days", "Look-back candles", "i", 30, 5, 250], ["lo", "Rise at least %", "f", 20.0, 0.0, 1000.0], ["hi", "Rise at most % (0 = any)", "f", 0.0, 0.0, 5000.0]] },
  rsi: { label: "RSI in range", fields: [["lo", "Min", "f", 50.0, 0.0, 100.0], ["hi", "Max", "f", 100.0, 0.0, 100.0], ["n", "Period", "i", 14, 2, 50]] },
  rsi_avg: { label: "RSI vs its average", fields: [["dir", "RSI is", "s", "above", ["above", "below"], null], ["n", "Average length", "i", 14, 2, 50]] },
  ma_pos: { label: "Price vs moving average", fields: [["kind", "Type", "s", "EMA", MA_KINDS, null], ["len", "Length", "i", 20, 2, 300], ["dir", "Price is", "s", "above", ["above", "below", "near"], null],
    ["dmin", "Distance min %", "f", 0.0, 0.0, 100.0], ["dmax", "Distance max % (0 = any)", "f", 0.0, 0.0, 200.0]] },
  ma_stack: { label: "Moving-average alignment", fields: [["k1", "Fast type", "s", "EMA", MA_KINDS, null], ["n1", "Fast length", "i", 20, 2, 300], ["k2", "Slow type", "s", "EMA", MA_KINDS, null], ["n2", "Slow length", "i", 50, 2, 300]] },
  ma_cross: { label: "Fresh MA crossover", fields: [["k1", "Fast type", "s", "EMA", MA_KINDS, null], ["n1", "Fast length", "i", 20, 2, 300], ["k2", "Slow type", "s", "EMA", MA_KINDS, null], ["n2", "Slow length", "i", 50, 2, 300], ["within", "Crossed within candles", "i", 5, 1, 60]] },
  vol_spike: { label: "Volume vs 20-day average", fields: [["lo", "At least ×", "f", 1.5, 0.0, 50.0], ["hi", "At most × (0 = any)", "f", 0.0, 0.0, 100.0]] },
  atr: { label: "ATR % (typical daily range)", fields: [["lo", "Min %", "f", 1.0, 0.0, 50.0], ["hi", "Max % (0 = any)", "f", 0.0, 0.0, 50.0], ["n", "Period", "i", 14, 2, 50]] },
  day_move: { label: "Today's move %", fields: [["mode", "Measure", "s", "size", ["size", "up", "down"], null], ["lo", "Min %", "f", 2.0, 0.0, 50.0], ["hi", "Max % (0 = any)", "f", 0.0, 0.0, 50.0]] },
  change: { label: "Price change over N candles", fields: [["days", "Candles", "i", 5, 1, 250], ["lo", "Min %", "f", 0.0, -100.0, 1000.0], ["hi", "Max %", "f", 100.0, -100.0, 5000.0]] },
  near_high: { label: "Distance below N-day high", fields: [["days", "High of last candles", "i", 252, 5, 260], ["lo", "Min % below", "f", 0.0, 0.0, 100.0], ["hi", "Max % below", "f", 15.0, 0.0, 100.0]] },
  breakout: { label: "Breakout above N-day high", fields: [["days", "Previous candles", "i", 20, 3, 260], ["amin", "Happened at least N candles ago", "i", 0, 0, 60], ["amax", "…and at most N candles ago", "i", 0, 0, 60]] },
  tight: { label: "Tight range (consolidation)", fields: [["n", "Last candles", "i", 5, 2, 60], ["max", "High–low range at most %", "f", 6.0, 0.5, 50.0]] },
  nr: { label: "Narrow-range candle (NR-N)", fields: [["n", "Narrowest of last", "i", 7, 3, 30]] },
  inside: { label: "Inside bar (today inside yesterday)", fields: [] },
  gap: { label: "Gap at today's open %", fields: [["lo", "Min %", "f", 1.0, -50.0, 50.0], ["hi", "Max %", "f", 20.0, -50.0, 50.0]] },
  rs: { label: "Relative strength vs NIFTY 50", fields: [["days", "Over candles", "i", 63, 5, 250], ["lo", "Beats NIFTY by at least % pts", "f", 0.0, -100.0, 500.0]] },
  macd: { label: "MACD (12, 26, 9)", fields: [["mode", "Condition", "s", "above signal", ["above signal", "above zero", "histogram rising"], null]] },
  adx: { label: "ADX trend strength", fields: [["lo", "Min", "f", 20.0, 0.0, 100.0], ["hi", "Max (0 = any)", "f", 0.0, 0.0, 100.0], ["n", "Period", "i", 14, 2, 50]] },
  candle: { label: "Candle shape", fields: [["pat", "Shape", "s", "hammer", CANDLES, null]] },
  ma_touch: { label: "Touched a moving average and held", fields: [["kind", "Type", "s", "EMA", MA_KINDS, null], ["len", "Length", "i", 20, 2, 300], ["within", "Touched within last candles", "i", 1, 1, 10], ["tol", "Touch tolerance %", "f", 0.5, 0.0, 5.0]] },
  contract: { label: "Shrinking ranges (VCP style)", fields: [["legs", "Number of legs", "i", 3, 2, 4], ["n", "Candles per leg", "i", 5, 3, 20]] },
  vol_fall: { label: "Volume falling candle after candle", fields: [["n", "Last candles", "i", 3, 2, 10], ["skip", "Exceptions allowed", "i", 0, 0, 3]] },
  delivery: { label: "Delivery % (NSE)", fields: [["n", "Average of last sessions", "i", 5, 1, 20], ["lo", "At least %", "f", 40.0, 0.0, 100.0], ["x", "At least × its earlier average (0 = skip)", "f", 0.0, 0.0, 5.0]] },
};
export const RULE_ORDER = ["pullback", "lowvol", "vol_fall", "runup", "rsi", "rsi_avg", "ma_pos", "ma_touch", "ma_stack", "ma_cross", "vol_spike", "delivery", "atr", "day_move", "change", "near_high", "breakout", "tight", "contract", "nr", "inside", "candle", "gap", "rs", "macd", "adx"];
// options every rule has: when it is measured, on which candles, either-or groups, always required
export const COMMON = [["ago", "Measured N candles ago (0 = latest)", "i", 0, 0, 60], ["tf", "Candles", "s", "daily", ["daily", "weekly"], null], ["grp", "Either-or group", "s", "—", ["—", "A", "B", "C"], null], ["must", "Always required", "b", false, null, null]];
export const allFields = (t) => RULES[t].fields.concat(COMMON);

// ── Python number formats ──
const pyRound = (v) => { const r = Math.round(v); return Math.abs(v % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r; };
export function g(v) {                                       // f"{v:g}"
  if (v === 0 || !isFinite(v)) return v === 0 ? "0" : String(v);
  const [m, ex] = v.toExponential(5).split("e"), x = +ex, strip = (s) => (s.includes(".") ? s.replace(/0+$/, "").replace(/\.$/, "") : s);
  if (x < -4 || x >= 6) return strip(m) + "e" + (x < 0 ? "-" : "+") + String(Math.abs(x)).padStart(2, "0");
  return strip((+v.toPrecision(6)).toFixed(Math.max(0, 5 - x)));
}
const f = (v, d) => fx(v, d, false), fs = (v, d) => fx(v, d, false, true), fc = (v, d) => fx(v, d, true);
const uid = () => Math.random().toString(16).slice(2, 10).padEnd(8, "0");

const pyFloat = (v) => {                                      // Python float(): numbers, yes / no and number words; anything else is refused
  if (typeof v === "number") return v; if (typeof v === "boolean") return v ? 1 : 0;
  if (typeof v === "string") { const x = v.trim().toLowerCase().replace(/^\+/, ""); if (/^-?(nan|inf|infinity)$/.test(x)) return x.includes("nan") ? NaN : x[0] === "-" ? -Infinity : Infinity; if (/^-?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/.test(x)) return Number(x); }
  throw new Error("ValueError");
};
const pyBool = (v) => !(v == null || v === false || v === 0 || v === "" || (typeof v === "number" && isNaN(v) && false) || (Array.isArray(v) && !v.length) || (typeof v === "object" && v && !Array.isArray(v) && !Object.keys(v).length));
const pyStr = (v) => (v === true ? "True" : v === false ? "False" : v == null ? "None" : typeof v === "object" ? JSON.stringify(v) : String(v));
export function cleanRule(r) {                                // validate one rule against the catalogue (AI output and saved setups)
  const t = r && r.type; if (!RULES[t]) return null;
  const out = { type: t, on: "on" in r ? pyBool(r.on) : true, _id: r._id || uid() };
  for (const [name, , kind, def, a, b] of allFields(t)) {
    let v = name in r ? r[name] : def;
    try {
      if (kind === "i") { const x = pyFloat(v); if (!isFinite(x)) throw new Error("ValueError"); v = Math.max(a, Math.min(b, pyRound(x))); }
      else if (kind === "f") { const x = pyFloat(v); v = isFinite(x) ? Math.max(a, Math.min(b, x)) : def; }
      else if (kind === "s") { v = pyStr(v); if (!a.includes(v)) v = { signed: "size", abs: "size", above_signal: "above signal", above_zero: "above zero", hist_rising: "histogram rising" }[v] || def; if (!a.includes(v)) v = def; }
      else if (kind === "b") v = pyBool(v);
    } catch { v = def; }
    out[name] = v;
  }
  if ((t === "pullback" || t === "lowvol") && out.nmax && out.nmax < out.nmin) out.nmax = out.nmin;
  if ((t === "rsi" || t === "change" || t === "gap") && out.hi < out.lo) [out.lo, out.hi] = [out.hi, out.lo];
  if (t === "breakout" && out.amax < out.amin) out.amax = out.amin;
  return out;
}
export function newRule(t, vals = {}) { const r = { type: t, on: true, _id: uid() }; for (const [name, , , def] of allFields(t)) r[name] = def; for (const [kk, v] of Object.entries(vals)) if (kk in r) r[kk] = v; return cleanRule(r); }
const R_ = (lo, hi, unit = "", zeroAny = true) => (zeroAny && !hi ? `≥ ${g(lo)}${unit}` : !lo && zeroAny ? `≤ ${g(hi)}${unit}` : `${g(lo)}–${g(hi)}${unit}`);
export function describe(r) {
  let s = describe0(r);
  if (r.tf === "weekly") s += " · on weekly candles";
  if (r.ago) s += ` · measured ${r.ago} candle${r.ago !== 1 ? "s" : ""} ago`;
  if ((r.grp ?? "—") !== "—") s += ` · either-or group ${r.grp}`;
  if (r.must) s += " · always required";
  return s;
}
function describe0(r) {
  const t = r.type;
  if (t === "pullback") { let s = `Pullback of ${r.nmax !== r.nmin ? `${r.nmin}–${r.nmax}` : `${r.nmin}`} candles on avg volume ≤ ${g(r.vol)}× the 20-day average`;
    if (r.max_up) s += `, up to ${r.max_up} up candle${r.max_up > 1 ? "s" : ""} allowed`;
    if (r.small_up) s += `, green candles under ${g(r.small_up)}% are fine`;
    if (r.each) s += ", every candle below average volume";
    if (r.dmin || r.dmax) s += `, depth ${R_(r.dmin, r.dmax, "%")}`;
    return s; }
  if (t === "lowvol") return `${!r.nmax ? `at least ${r.nmin}` : r.nmax !== r.nmin ? `${r.nmin}–${r.nmax}` : `${r.nmin}`} consecutive candles with volume below ${g(r.x)}× the 20-day average`;
  if (t === "runup") return `Rose ${R_(r.lo, r.hi, "%")} from a low to a later high in the last ${r.days} candles`;
  if (t === "rsi") return `RSI(${r.n}) between ${g(r.lo)} and ${g(r.hi)}`;
  if (t === "rsi_avg") return `RSI ${r.dir} its ${r.n}-period average`;
  if (t === "ma_pos") { const ma = `${r.kind} ${r.len}`; if (r.dir === "near") return `Price within ${g(r.dmax || 2)}% of ${ma} (either side)`; return `Price ${r.dir} ${ma}${r.dmin || r.dmax ? ` by ${R_(r.dmin, r.dmax, "%")}` : ""}`; }
  if (t === "ma_stack") return `${r.k1} ${r.n1} above ${r.k2} ${r.n2}`;
  if (t === "ma_cross") return `${r.k1} ${r.n1} crossed above ${r.k2} ${r.n2} in the last ${r.within} candles`;
  if (t === "vol_spike") return `Today's volume ${R_(r.lo, r.hi, "×")} the 20-day average`;
  if (t === "atr") return `ATR(${r.n}) ${R_(r.lo, r.hi, "%")} of price`;
  if (t === "day_move") return `${{ size: "Today's move (up or down)", up: "Today's gain", down: "Today's fall" }[r.mode]} ${R_(r.lo, r.hi, "%")}`;
  if (t === "change") return `Price change over ${r.days} candles between ${g(r.lo)}% and ${g(r.hi)}%`;
  if (t === "near_high") return `${g(r.lo)}–${g(r.hi)}% below the ${r.days}-candle high`;
  if (t === "breakout") return `Close above the highest high of the previous ${r.days} candles${!(r.amin || r.amax) ? "" : r.amax !== r.amin ? `, ${r.amin}–${r.amax} candles ago` : `, ${r.amin} candles ago`}`;
  if (t === "tight") return `Last ${r.n} candles inside a ${g(r.max)}% range`;
  if (t === "nr") return `Today's range is the narrowest of the last ${r.n} candles (NR${r.n})`;
  if (t === "inside") return "Inside bar: today's high / low inside yesterday's";
  if (t === "gap") return `Opened ${g(r.lo)}% to ${g(r.hi)}% away from yesterday's close`;
  if (t === "rs") return `Beats NIFTY 50 by ≥ ${g(r.lo)} % points over ${r.days} candles`;
  if (t === "macd") return `MACD ${r.mode}`;
  if (t === "adx") return `ADX(${r.n}) ${R_(r.lo, r.hi)}`;
  if (t === "candle") return `Latest candle is a ${r.pat}`;
  if (t === "ma_touch") return `Low touched ${r.kind} ${r.len} (within ${g(r.tol)}%) in the last ${r.within} candle${r.within > 1 ? "s" : ""} and the close is above it`;
  if (t === "contract") return `${r.legs} legs of ${r.n} candles, each with a smaller high–low range than the one before`;
  if (t === "vol_fall") return `Volume lower than the previous candle on each of the last ${r.n} candles` + (r.skip ? `, ${r.skip} exception${r.skip > 1 ? "s" : ""} allowed` : "");
  if (t === "delivery") return `Delivery averages ≥ ${g(r.lo)}% over the last ${r.n} sessions` + (r.x ? ` and ≥ ${g(r.x)}× its earlier average` : "");
  return t;
}

// ── indicators (pandas semantics) ──
function ewm(a, alpha, minp = 0) {                            // ewm(adjust=False).mean(), started at the first number; blank until minp numbers were seen
  const out = new Float64Array(a.length).fill(NaN); let y = NaN, seen = 0;
  for (let i = 0; i < a.length; i++) { const x = a[i]; if (!isNaN(x)) { y = isNaN(y) ? x : (1 - alpha) * y + alpha * x; seen++; } if (seen >= Math.max(minp, 1) && !isNaN(y)) out[i] = y; }
  return out;
}
const rma = (a, n) => ewm(a, 1 / n, n);
function rollMean(a, n) {                                    // pandas rolling(n).mean(): the same compensated running sum, so the last digit agrees
  const out = new Float64Array(a.length).fill(NaN); let sum = 0, nobs = 0, ca = 0, cr = 0;
  for (let i = 0; i < a.length; i++) {
    if (i >= n) { const x = a[i - n]; if (!isNaN(x)) { const y = -x - cr, t = sum + y; cr = t - sum - y; sum = t; nobs--; } }
    const x = a[i]; if (!isNaN(x)) { const y = x - ca, t = sum + y; ca = t - sum - y; sum = t; nobs++; }
    if (i >= n - 1 && nobs >= n) out[i] = sum / nobs;
  }
  return out;
}
const last = (a) => { const v = a[a.length - 1]; return a.length && typeof v === "number" && isFinite(v) ? v : null; };
const amax = (a) => { let m = -Infinity; for (const x of a) if (x > m) m = x; return m; }, amin = (a) => { let m = Infinity; for (const x of a) if (x < m) m = x; return m; };
const amean = (a) => { let s = 0; for (const x of a) s += x; return s / a.length; };
const tail = (a, n) => (n <= 0 ? [] : a.subarray ? a.subarray(Math.max(0, a.length - n)) : a.slice(Math.max(0, a.length - n)));
const F64 = (a) => (a instanceof Float64Array ? a : Float64Array.from(a));
const friday = (d) => { const t = new Date(d + "T00:00:00Z"), add = (5 - t.getUTCDay() + 7) % 7; return new Date(t.getTime() + add * 864e5).toISOString().slice(0, 10); };

export class Ctx {                                            // one stock's candles and its indicators; today's volume is projected to a full day in market hours
  constructor(sym, F, liveFrac = null, nifty = null, extra = null) {
    const num = (x) => (x == null || x === "" || isNaN(Number(x)) ? NaN : Number(x)), keep = []; for (let i = 0; i < F.c.length; i++) if (num(F.c[i]) > 0) keep.push(i);
    const n = keep.length, col = (a, fb) => { const o = new Float64Array(n); for (let j = 0; j < n; j++) { const x = a ? num(a[keep[j]]) : NaN; o[j] = isNaN(x) ? fb(j) : x; } return o; };
    this.sym = sym; this.nifty = nifty; this.extra = extra || {}; this.d = n === F.c.length ? F.d : keep.map((i) => F.d[i]); this.c = col(F.c, () => NaN);
    for (const kk of ["o", "h", "l"]) this[kk] = col(F[kk], (j) => this.c[j]);
    this.rv = col(F.v, () => 0); this.v = this.rv.slice(); this.projected = false;
    if (liveFrac != null && n) { this.v[n - 1] /= Math.max(0.15, Math.min(1, liveFrac)); this.projected = true; }
    this.memo = {}; this.root = this; this.end = n;
  }
  at(end) {                                                   // this stock as of candle number `end` (exclusive): indicators are cut, not recomputed
    const root = this.root; end = Math.max(0, Math.min(Math.trunc(end), root.end));
    if (end === root.end) return root;
    const kk = "view|" + end;
    if (!root.memo[kk]) { const x = Object.create(Ctx.prototype); Object.assign(x, { sym: root.sym, nifty: root.nifty, extra: root.extra, root, end, memo: {}, projected: false });
      x.d = root.d; for (const a of ["c", "o", "h", "l", "v", "rv"]) x[a] = root[a].subarray(0, end); root.memo[kk] = x; }
    return root.memo[kk];
  }
  ind(key, fn) { const root = this.root; if (!root.memo[key]) root.memo[key] = F64(fn(root)); const full = root.memo[key]; return this === root ? full : full.subarray(0, this.end); }
  ma(kind, n) { return this.ind(`ma|${kind}|${n}`, (r) => (kind === "EMA" ? ewm(r.c, 2 / (n + 1), n) : rollMean(r.c, n))); }
  rsi(n = 14) { return this.ind("rsi|" + n, (r) => rsiWilder(r.c, n)); }
  avgVol() { return this.ind("avgv", (r) => { const m = rollMean(r.v, 20), o = new Float64Array(m.length); o[0] = NaN; o.set(m.subarray(0, Math.max(0, m.length - 1)), 1); return m.length ? o : m; }); }                    // 20-day average volume ending YESTERDAY
  atr(n = 14) { return this.ind("atr|" + n, (r) => rma(Array.from(r.c, (_, i) => (i === 0 ? r.h[0] - r.l[0] : Math.max(r.h[i] - r.l[i], Math.abs(r.h[i] - r.c[i - 1]), Math.abs(r.l[i] - r.c[i - 1])))), n)); }
  macd() { const m = this.ind("macd", (r) => { const a = ewm(r.c, 2 / 13), b = ewm(r.c, 2 / 27); return a.map((x, i) => x - b[i]); }); const sg = this.ind("macds", (r) => { const a = ewm(r.c, 2 / 13), b = ewm(r.c, 2 / 27); return ewm(a.map((x, i) => x - b[i]), 2 / 10); }); return [m, sg]; }
  adx(n = 14) { return this.ind("adx|" + n, (r) => {
    const N = r.c.length, pdm = new Array(N).fill(0), mdm = new Array(N).fill(0);
    for (let i = 1; i < N; i++) { const up = r.h[i] - r.h[i - 1], dn = -(r.l[i] - r.l[i - 1]); if (up > dn && up > 0) pdm[i] = up; if (dn > up && dn > 0) mdm[i] = dn; }
    const tr = r.atr(n), p = rma(pdm, n), m = rma(mdm, n);
    const dx = Array.from(p, (_, i) => { const pdi = (100 * p[i]) / tr[i], mdi = (100 * m[i]) / tr[i], x = (100 * Math.abs(pdi - mdi)) / (pdi + mdi); return isFinite(x) ? x : 0; });
    return rma(dx, n); }); }
  weekly() {                                                  // the same stock on weekly candles (weeks end on Friday; the unfinished week is the last candle)
    if (this.memo.wk) return this.memo.wk;
    const W = { d: [], o: [], h: [], l: [], c: [], v: [] }; let cur = null;
    for (let i = 0; i < this.c.length; i++) { const kk = friday(this.d[i]);
      if (kk !== cur) { cur = kk; W.d.push(kk); W.o.push(this.o[i]); W.h.push(this.h[i]); W.l.push(this.l[i]); W.c.push(this.c[i]); W.v.push(this.rv[i]); }
      else { const j = W.d.length - 1; W.h[j] = Math.max(W.h[j], this.h[i]); W.l[j] = Math.min(W.l[j], this.l[i]); W.c[j] = this.c[i]; W.v[j] += this.rv[i]; } }
    const x = new Ctx(this.sym, W, null, this.nifty, this.extra); this.memo.wk = x; return x;
  }
  turnoverCr() { const n = this.c.length, a = n > 21 ? n - 21 : 0, b = n > 21 ? n - 1 : n; if (b <= a) return 0; let s = 0; for (let i = a; i < b; i++) s += this.c[i] * this.rv[i]; return s / (b - a) / 1e7; }
}
function countUps(closes, smallUp = 0) { let k = 0; for (let i = 1; i < closes.length; i++) if (((closes[i] - closes[i - 1]) / closes[i - 1]) * 100 > (smallUp || 0)) k++; return k; }
function findPullback(ctx, r) {
  const { c, h, l, v } = ctx, N = c.length;
  for (let n = Math.trunc(r.nmax); n >= Math.trunc(r.nmin); n--) {
    const piv = N - n - 1; if (piv < 21) continue;
    const seg = (a) => a.slice(N - n);
    if (h[piv] < amax(seg(h))) continue;                        // the pullback must start from the recent high …
    if (h[piv] < amax(h.slice(Math.max(0, piv - Math.max(5, Math.trunc(r.nmax))), piv))) continue;      // … and that candle must be a swing high
    const ups = countUps(c.slice(piv), r.small_up ?? 0);
    if (ups > r.max_up || c[N - 1] >= c[piv]) continue;
    const base = amean(v.slice(piv - 19, piv + 1)); if (base <= 0) continue;
    const sv = seg(v), ratio = amean(sv) / base;
    if (ratio > r.vol || (r.each && sv.some((x) => x >= base))) continue;
    const depth = ((h[piv] - amin(seg(l))) / h[piv]) * 100;
    if (depth < r.dmin || (r.dmax && depth > r.dmax)) continue;
    return { n, ratio, depth, ups };
  }
  return null;
}
// -> [ok, chip, detail, metrics]; handles the options every rule has: weekly candles and "N candles ago"
export function evaluateRule(ctx, r) {
  let tag = "";
  if (r.tf === "weekly") { ctx = ctx.weekly(); tag += " W"; if (ctx.c.length < 12) return [false, `${r.type.toUpperCase()} W ?`, "not enough weekly history", {}]; }
  const ago = Math.trunc(r.ago || 0);
  if (ago) { if (ctx.end - ago < 12) return [false, `${r.type.toUpperCase()} ?`, `not enough history ${ago} candles ago`, {}]; ctx = ctx.at(ctx.end - ago); tag += ` −${ago}c`; }
  let [ok, chip, detail, m] = evalRule(ctx, r);
  if (tag) { chip += tag; detail += (r.tf === "weekly" ? " · weekly candles" : "") + (ago ? ` · ${ago} candles ago` : ""); }
  return [ok, chip, detail, m];
}
function evalRule(ctx, r) {                                    // one rule on the latest candle of ctx; not ok when there is too little data
  const t = r.type, c = ctx.c, N = c.length, cur = last(c), sl = Number(r._slack || 0);
  try {
    if (N < 2 && ["day_move", "inside", "gap", "candle"].includes(t)) throw new Error("IndexError");
    if (t === "pullback") {
      const p = findPullback(ctx, r);
      if (p) return [true, `PB ${p.n}c · ${f(p.ratio, 2)}×`, `${p.n}-candle pullback, ${f(p.depth, 1)}% deep, volume ${f(p.ratio, 2)}× average`, { pb_n: p.n, pb_vol: p.ratio, pb_depth: p.depth, pb_ups: p.ups }];
      const hh = tail(ctx.h, 15); let am = 0; for (let i = 1; i < hh.length; i++) if (hh[i] > hh[am]) am = i;      // diagnostics: measure from the most recent swing high
      const kk = hh.length - 1 - am; let why;
      if (kk === 0) why = "no pullback yet — today is the high";
      else { const piv = N - kk - 1, base = amean(ctx.v.slice(Math.max(0, piv - 19), piv + 1)), vr = base ? amean(tail(ctx.v, kk)) / base : NaN, ups = countUps(tail(c, kk + 1), r.small_up ?? 0);
        why = `high was ${kk} candle${kk !== 1 ? "s" : ""} ago, ${ups} up candle${ups !== 1 ? "s" : ""}, volume ${isNaN(vr) ? "nan" : f(vr, 2)}× avg`; }
      return [false, "PB ✗", `needs a ${r.nmin}–${r.nmax} candle pullback on ≤ ${g(r.vol)}× volume (${why})`, {}];
    }
    if (t === "lowvol") { const av = ctx.avgVol(); let kk = 0; for (let i = N - 1; i >= 0; i--) { if (!(ctx.v[i] < r.x * av[i])) break; kk++; }
      return [kk >= r.nmin && (!r.nmax || kk <= r.nmax), `LOWVOL ${kk}c`, `${kk} low-volume candles in a row (needs ${r.nmin}${r.nmax ? "–" + r.nmax : "+"})`, { lowvol: kk }]; }
    if (t === "runup") { const w = c.slice(Math.max(0, N - Math.trunc(r.days) - 1), N - 1); if (w.length < 5) return [false, "RUN ?", "not enough history", {}];
      let i = 0; for (let j = 1; j < w.length; j++) if (w[j] < w[i]) i = j;
      const rise = w[i] > 0 ? (amax(w.slice(i)) / w[i] - 1) * 100 : 0;
      return [rise >= r.lo && (!r.hi || rise <= r.hi), `RUN ${fs(rise, 0)}%`, `rose ${f(rise, 1)}% in ${r.days} candles (needs ${R_(r.lo, r.hi, "%")})`, { runup: rise }]; }
    if (t === "rsi") { const v = last(ctx.rsi(r.n)); if (v == null) return [false, "RSI ?", "not enough history", {}]; return [r.lo <= v && v <= r.hi, `RSI ${f(v, 0)}`, `RSI ${f(v, 1)} (needs ${g(r.lo)}–${g(r.hi)})`, { rsi: v }]; }
    if (t === "rsi_avg") { const rsv = ctx.rsi(14), v = last(rsv); let a = null; if (N >= r.n) { let sm = 0; for (let i = N - r.n; i < N; i++) sm += rsv[i]; a = isFinite(sm) ? sm / r.n : null; } if (v == null || a == null) return [false, "RSI/AVG ?", "not enough history", {}];
      return [r.dir === "above" ? v > a - sl : v < a + sl, `RSI ${v > a ? ">" : "<"} AVG`, `RSI ${f(v, 1)} vs its ${r.n}-avg ${f(a, 1)} (needs ${r.dir})`, {}]; }
    if (t === "ma_pos") { const m = last(ctx.ma(r.kind, r.len)); if (m == null || cur == null) return [false, `${r.kind}${r.len} ?`, `not enough history for ${r.kind} ${r.len}`, {}];
      const dist = (cur / m - 1) * 100; let ok;
      if (r.dir === "near") ok = r.dmin <= Math.abs(dist) && Math.abs(dist) <= (r.dmax || 2);
      else { ok = r.dir === "above" ? dist > -sl : dist < sl; ok = ok && (Math.abs(dist) >= r.dmin || sl > 0) && (!r.dmax || Math.abs(dist) <= r.dmax); }
      return [ok, `${r.kind}${r.len} ${fs(dist, 1)}%`, `price ${fs(dist, 2)}% vs ${r.kind} ${r.len} (${describe(r).toLowerCase()})`, { ma_dist: dist }]; }
    if (t === "ma_stack" || t === "ma_cross") { const F1 = ctx.ma(r.k1, r.n1), S1 = ctx.ma(r.k2, r.n2), a = last(F1), b = last(S1); if (a == null || b == null) return [false, "MA ?", "not enough history", {}];
      if (t === "ma_stack") return [a > b * (1 - sl / 100), `${r.k1}${r.n1}${a > b ? ">" : "<"}${r.k2}${r.n2}`, `${r.k1} ${r.n1} ${fc(a, 1)} vs ${r.k2} ${r.n2} ${fc(b, 1)}`, {}];
      const dd = []; for (let i = N - 1; i >= 0 && dd.length < Math.trunc(r.within) + 1; i--) { const x = F1[i] - S1[i]; if (!isNaN(x)) dd.unshift(x); }
      const w = dd; let crossed = false; for (let i = 1; i < w.length; i++) if (w[i - 1] <= 0 && w[i] > 0) crossed = true;
      crossed = crossed && dd[dd.length - 1] > 0;
      return [crossed, crossed ? "X-OVER ✓" : "X-OVER ✗", `${crossed ? "crossed above" : "no cross above"} in last ${r.within} candles`, {}]; }
    if (t === "vol_spike") { const av = last(ctx.avgVol()); if (!av) return [false, "VOL ?", "no volume history", {}]; const x = ctx.v[N - 1] / av;
      return [x >= r.lo && (!r.hi || x <= r.hi), `VOL ${f(x, 1)}×`, `volume ${f(x, 2)}× the 20-day average${ctx.projected ? " (projected)" : ""}`, { vol_x: x }]; }
    if (t === "atr") { const a = last(ctx.atr(r.n)); if (a == null || !cur) return [false, "ATR ?", "not enough history", {}]; const p = (a / cur) * 100;
      return [p >= r.lo && (!r.hi || p <= r.hi), `ATR ${f(p, 1)}%`, `ATR ${f(p, 2)}% of price (needs ${R_(r.lo, r.hi, "%")})`, { atr: p }]; }
    if (t === "day_move") { const ch = (cur / c[N - 2] - 1) * 100, val = r.mode === "size" ? Math.abs(ch) : r.mode === "up" ? ch : -ch;
      return [val >= r.lo && (!r.hi || val <= r.hi), `DAY ${fs(ch, 1)}%`, `today ${fs(ch, 2)}% (needs ${describe(r).toLowerCase()})`, {}]; }
    if (t === "change") { if (N <= r.days) return [false, "CHG ?", "not enough history", {}]; const ch = (cur / c[N - 1 - Math.trunc(r.days)] - 1) * 100;
      return [r.lo <= ch && ch <= r.hi, `${r.days}D ${fs(ch, 0)}%`, `${fs(ch, 1)}% over ${r.days} candles (needs ${g(r.lo)}–${g(r.hi)}%)`, { change: ch }]; }
    if (t === "near_high") { const hh = amax(tail(ctx.h, Math.trunc(r.days))), dd = hh ? (1 - cur / hh) * 100 : 0;
      return [r.lo <= dd && dd <= r.hi, `${f(dd, 1)}% < HIGH`, `${f(dd, 1)}% below the ${r.days}-candle high ${fc(hh, 2)}`, { off_high: dd }]; }
    if (t === "breakout") { const days = Math.trunc(r.days), a0 = Math.trunc(r.amin || 0), a1 = Math.trunc(r.amax || 0), hv = ctx.h;
      if (N < days + a1 + 1) return [false, "BO ?", "not enough history", {}];
      for (let kk = a0; kk <= a1; kk++) { const e = N - kk, lvl = amax(hv.slice(e - 1 - days, e - 1));
        if (c[e - 1] > lvl * (1 - sl / 100)) return [true, `BO ✓ ${fc(lvl, 0)}` + (kk === 0 ? "" : ` −${kk}c`), `closed above the ${days}-candle high ${fc(lvl, 2)} ${kk === 0 ? "today" : `${kk} candle${kk !== 1 ? "s" : ""} ago`}`, { bo_ago: kk }]; }
      const lvl = amax(hv.slice(N - 1 - days, N - 1));
      return [false, `BO ✗ ${fc(lvl, 0)}`, `close ${fc(cur, 2)} vs ${days}-candle high ${fc(lvl, 2)}${!a1 ? "" : ` (needs a breakout ${a0}–${a1} candles ago)`}`, {}]; }
    if (t === "tight") { const w = Math.trunc(r.n), rng = ((amax(tail(ctx.h, w)) - amin(tail(ctx.l, w))) / cur) * 100; return [rng <= r.max, `RANGE ${f(rng, 1)}%`, `last ${w} candles span ${f(rng, 1)}% (needs ≤ ${g(r.max)}%)`, { tight: rng }]; }
    if (t === "nr") { const th = tail(ctx.h, Math.trunc(r.n)), tl = tail(ctx.l, Math.trunc(r.n)), rg = Array.from(th, (x, i) => x - tl[i]), ok = rg[rg.length - 1] <= amin(rg) + 1e-9; return [ok, `NR${r.n} ${ok ? "✓" : "✗"}`, `today's range ${ok ? "is" : "is not"} the narrowest of ${r.n}`, {}]; }
    if (t === "inside") { const ok = ctx.h[N - 1] <= ctx.h[N - 2] && ctx.l[N - 1] >= ctx.l[N - 2]; return [ok, `INSIDE ${ok ? "✓" : "✗"}`, ok ? "inside bar" : "not an inside bar", {}]; }
    if (t === "gap") { const gp = (ctx.o[N - 1] / c[N - 2] - 1) * 100; return [r.lo <= gp && gp <= r.hi, `GAP ${fs(gp, 1)}%`, `opened ${fs(gp, 2)}% vs yesterday (needs ${g(r.lo)}–${g(r.hi)}%)`, {}]; }
    if (t === "rs") { if (!ctx.nifty || N <= r.days) return [false, "RS ?", "NIFTY data unavailable", {}];
      const d0 = ctx.d[N - 1 - Math.trunc(r.days)], d1 = ctx.d[N - 1], nf = ctx.nifty, at = (dd) => { let lo = 0, hi = nf.d.length; while (lo < hi) { const mid = (lo + hi) >> 1; if (nf.d[mid] <= dd) lo = mid + 1; else hi = mid; } return lo ? nf.c[lo - 1] : null; }, n0 = at(d0), n1 = at(d1);
      if (n0 == null || n1 == null) return [false, "RS ?", "NIFTY data unavailable", {}];
      const sr = (cur / c[N - 1 - Math.trunc(r.days)] - 1) * 100, nr = (n1 / n0 - 1) * 100, ex = sr - nr;
      return [ex >= r.lo, `RS ${fs(ex, 0)}`, `stock ${fs(sr, 1)}% vs NIFTY ${fs(nr, 1)}% over ${r.days} candles`, { rs: ex }]; }
    if (t === "macd") { const [m, sg] = ctx.macd(), hist = { [N - 1]: m[N - 1] - sg[N - 1], [N - 2]: m[N - 2] - sg[N - 2] }; if (r.mode === "histogram rising" && N < 2) throw new Error("IndexError");
      const ok = r.mode === "above signal" ? m[N - 1] > sg[N - 1] : r.mode === "above zero" ? m[N - 1] > 0 : hist[N - 1] > hist[N - 2];
      return [ok, `MACD ${ok ? "✓" : "✗"}`, `MACD ${f(m[N - 1], 2)}, signal ${f(sg[N - 1], 2)}`, {}]; }
    if (t === "adx") { const a = last(ctx.adx(Math.trunc(r.n))); if (a == null) return [false, "ADX ?", "not enough history", {}]; return [a >= r.lo && (!r.hi || a <= r.hi), `ADX ${f(a, 0)}`, `ADX ${f(a, 1)} (needs ${R_(r.lo, r.hi)})`, { adx: a }]; }
    if (t === "candle") { const o = ctx.o[N - 1], h = ctx.h[N - 1], l = ctx.l[N - 1], po = ctx.o[N - 2], pc = c[N - 2], rng = h - l;
      if (rng <= 0) return [false, "CANDLE ?", "the candle has no range", {}];
      const body = Math.abs(cur - o), top = h - Math.max(cur, o), bot = Math.min(cur, o) - l, pos = (cur - l) / rng;      // pos: where the close sits in the candle, 0 = low, 1 = high
      const shapes = { hammer: bot >= 2 * body && top <= 0.25 * rng && body <= 0.4 * rng, "bullish engulfing": pc < po && cur > o && cur >= po && o <= pc, "bearish engulfing": pc > po && cur < o && cur <= po && o >= pc,
        doji: body <= 0.1 * rng, "strong close": pos >= 0.75, "weak close": pos <= 0.25, "green candle": cur > o, "red candle": cur < o }, ok = !!shapes[r.pat];
      return [ok, `${r.pat.toUpperCase()} ${ok ? "✓" : "✗"}`, `body ${f((body / rng) * 100, 0)}% of the range, close ${f(pos * 100, 0)}% of the way up (${ok ? "is" : "not"} a ${r.pat})`, { close_pos: pos * 100 }]; }
    if (t === "ma_touch") { const ma = ctx.ma(r.kind, r.len), m = last(ma); let have = 0; for (let i = N - 1; i >= 0 && have < r.within; i--) if (!isNaN(ma[i])) have++;
      if (m == null || have < r.within) return [false, "TOUCH ?", `not enough history for ${r.kind} ${r.len}`, {}];
      const w = Math.trunc(r.within), tm = tail(ma, w), gaps = Array.from(tail(ctx.l, w), (x, i) => (x / tm[i] - 1) * 100).filter((x) => !isNaN(x)), best = amin(gaps), ok = best <= r.tol && cur > m;      // how far each low stayed above the average, %
      return [ok, `TOUCH ${r.kind}${r.len} ${ok ? "✓" : "✗"}`, `closest low was ${fs(best, 2)}% from ${r.kind} ${r.len} in the last ${w} candle${w > 1 ? "s" : ""}, close ${fs((cur / m - 1) * 100, 2)}% vs it`, { touch: best }]; }
    if (t === "contract") { const legs = Math.trunc(r.legs), n = Math.trunc(r.n); if (N < legs * n + 1) return [false, "VCP ?", "not enough history", {}];
      const hv = tail(ctx.h, legs * n), lv = tail(ctx.l, legs * n), rg = []; for (let i = 0; i < legs; i++) rg.push(((amax(hv.slice(i * n, (i + 1) * n)) - amin(lv.slice(i * n, (i + 1) * n))) / cur) * 100);
      const ok = rg.every((x, i) => i === 0 || x < rg[i - 1]);
      return [ok, `VCP ${ok ? "✓" : "✗"}`, "leg ranges " + rg.map((x) => `${f(x, 1)}%`).join(" → ") + (ok ? "" : " (not shrinking every time)"), { vcp_last: rg[rg.length - 1] }]; }
    if (t === "vol_fall") { const n = Math.trunc(r.n), vv = tail(ctx.v, n + 1); if (vv.length < n + 1) return [false, "VOL↓ ?", "not enough history", {}];
      let rises = 0; for (let i = 1; i < vv.length; i++) if (vv[i] - vv[i - 1] >= 0) rises++;
      return [rises <= r.skip, `VOL↓ ${n - rises}/${n}`, `volume fell on ${n - rises} of the last ${n} candles (needs ${n - Math.trunc(r.skip)})`, { vol_fall: n - rises }]; }
    if (t === "delivery") { const D = (ctx.extra || {}).deliv; if (D == null) return [false, "DELIV ?", "NSE delivery data is not available on this server", {}];
      const dd = []; for (let i = 0; i < D.d.length; i++) if (D.d[i] <= ctx.d[N - 1] && D.v[i] != null && !isNaN(D.v[i])) dd.push(D.v[i]);
      const n = Math.trunc(r.n); if (dd.length < n) return [false, "DELIV ?", `delivery data covers only ${dd.length} sessions up to this date`, {}];
      const now = amean(tail(dd, n)); let ok = now >= r.lo, txt = `delivery ${f(now, 1)}% over ${n} sessions (needs ≥ ${g(r.lo)}%)`;
      if (r.x) { const prev = tail(dd.slice(0, dd.length - n), 20); if (prev.length < 5) return [false, "DELIV ?", "not enough earlier delivery sessions to compare with", {}];
        const pm = amean(prev), ratio = pm ? now / pm : NaN; ok = ok && ratio >= r.x; txt += `, ${isNaN(ratio) ? "nan" : f(ratio, 2)}× its earlier ${prev.length}-session average (needs ≥ ${g(r.x)}×)`; }
      return [ok, `DELIV ${f(now, 0)}%`, txt, { deliv: now }]; }
  } catch (er) { return [false, `${t.toUpperCase()} ?`, `could not compute (${er && er.message === "IndexError" ? "IndexError" : (er && er.name) || "Error"})`, {}]; }
  return [false, t, "unknown rule", {}];
}

// ── tolerance: "close enough" ──
export const TOLERANCES = { Exact: 0.0, "Small (5%)": 0.05, "Medium (10%)": 0.1, "Loose (20%)": 0.2 };
export const DEFAULT_TOL = 0.05;
// how each number may be relaxed: min = a lower bound (relax down) · max = an upper bound (relax up) · max0 = upper bound where 0 = any ·
// cmin / cmax = candle counts (one candle either way) · smin / smax = signed percentages · rmin / rmax = RSI / ADX points
const TOL_FIELDS = { pullback: [["nmin", "cmin"], ["nmax", "cmax"], ["max_up", "cmax"], ["vol", "max"], ["dmin", "min"], ["dmax", "max0"], ["small_up", "max"]], lowvol: [["nmin", "cmin"], ["nmax", "cmax0"], ["x", "max"]],
  runup: [["lo", "min"], ["hi", "max0"]], rsi: [["lo", "rmin"], ["hi", "rmax"]], ma_pos: [["dmin", "min"], ["dmax", "max0"]], vol_spike: [["lo", "min"], ["hi", "max0"]], atr: [["lo", "min"], ["hi", "max0"]], day_move: [["lo", "min"], ["hi", "max0"]],
  change: [["lo", "smin"], ["hi", "smax"]], near_high: [["lo", "min"], ["hi", "max"]], tight: [["max", "max"]], gap: [["lo", "smin"], ["hi", "smax"]], rs: [["lo", "smin"]], adx: [["lo", "rmin"], ["hi", "rmax0"]],
  ma_touch: [["tol", "max"], ["within", "cmax"]], vol_fall: [["skip", "cmax"]], delivery: [["lo", "min"], ["x", "min"]] };
const TOL_TYPES = new Set([...Object.keys(TOL_FIELDS), "ma_pos", "ma_stack", "rsi_avg", "breakout"]);
export function relaxRule(r, t) {                             // a copy of the rule with every number widened by tolerance t (0.05 = 5%); used only when the exact rule fails
  if (!t) return r;
  const x = { ...r };
  for (const [name, how] of TOL_FIELDS[r.type] || []) { const v = x[name]; if (v == null) continue;
    if (how === "min" && v > 0) x[name] = v * (1 - t);
    else if ((how === "max" || how === "max0") && v > 0) x[name] = v * (1 + t);
    else if (how === "cmin") x[name] = Math.max(1, v - 1);
    else if (how === "cmax" || (how === "cmax0" && v)) x[name] = v + 1;
    else if (how === "smin") x[name] = v - t * Math.max(Math.abs(v), 10);
    else if (how === "smax") x[name] = v + t * Math.max(Math.abs(v), 10);
    else if (how === "rmin" && v > 0) x[name] = Math.max(0, v - 50 * t);
    else if ((how === "rmax" || how === "rmax0") && (v || how === "rmax") && v < 100) x[name] = Math.min(100, v + 50 * t); }
  if (["ma_pos", "ma_stack", "rsi_avg", "breakout"].includes(r.type)) x._slack = 20 * t;      // 5% tolerance: price may sit up to 1% on the wrong side of the average or level
  return x;
}
export function evaluateRuleTol(ctx, r, t) {                  // -> [ok, close, chip, detail, metrics]; close = failed exactly but passes within tolerance t
  const [ok, chip, detail, m] = evaluateRule(ctx, r);
  if (ok || !t || !TOL_TYPES.has(r.type)) return [ok, false, chip, detail, m];
  const [ok2, chip2, detail2, m2] = evaluateRule(ctx, relaxRule(r, t));
  if (ok2) return [true, true, "≈ " + chip2, `${detail2} · passes within the ${g(t * 100)}% tolerance (exact rule: ${detail})`, m2];
  return [false, false, chip, detail, m];
}
const cfgOf = (setup) => { let cfg = setup.rule_config; if (typeof cfg === "string") { try { cfg = JSON.parse(cfg); } catch { cfg = null; } } return cfg && typeof cfg === "object" && !Array.isArray(cfg) ? cfg : null; };
export function setupTol(setup) { const cfg = cfgOf(setup), v = cfg ? cfg.tol : null; if (v == null) return DEFAULT_TOL; const x = Number(v); return isNaN(x) || typeof v === "boolean" && false ? DEFAULT_TOL : Math.max(0, Math.min(0.3, x)); }
export const tolLabel = (t) => { for (const [kk, v] of Object.entries(TOLERANCES)) if (Math.abs(v - t) < 1e-9) return kk; return `${g(t * 100)}%`; };
export function verdict(checks, minPass = 0) {                // either-or groups count as one rule
  const units = [], groups = {};
  for (const ck of checks) { const gr = ck.grp ?? "—"; let u;
    if (gr !== "—") { if (!groups[gr]) { groups[gr] = { ok: false, must: false, checks: [] }; units.push(groups[gr]); } u = groups[gr]; } else { u = { ok: false, must: false, checks: [] }; units.push(u); }
    u.checks.push(ck); u.ok = u.ok || ck.ok; u.must = u.must || !!ck.must; }
  const total = units.length, need = !minPass ? total : Math.max(1, Math.min(Math.trunc(minPass), total)), passed = units.filter((u) => u.ok).length, failed = units.filter((u) => !u.ok), reqFail = failed.filter((u) => u.must), short = Math.max(0, need - passed);
  const status = !short && !reqFail.length ? "match" : Math.max(short, reqFail.length) === 1 && total >= 2 ? "near" : "no";
  const fails = reqFail.concat(failed.filter((u) => !u.must)).flatMap((u) => u.checks);
  const miss = status !== "match" && need < total ? `passes ${passed} of ${total}, needs ${need}` + (reqFail.length ? " · a required rule failed" : "") : "";
  return { passed, total, need, status, fails, miss };
}
// all enabled rules on one stock -> result (or null when there is too little data). Pass a Ctx (or a view from Ctx.at) to evaluate a past date.
export function evaluateStock(sym, F, rules, { liveFrac = null, nifty = null, ctx = null, minPass = 0, extra = null, tol = 0 } = {}) {
  if (!ctx) { if (!F || !F.c || F.c.length < 30) return null; ctx = new Ctx(sym, F, liveFrac, nifty, extra); }
  const N = ctx.c.length; if (N < 30) return null;
  const cur = ctx.c[N - 1], prev = ctx.c[N - 2], checks = [], metrics = {};
  for (const r of rules) { if (!(r.on ?? true)) continue;
    const [ok, close, chip, detail, m] = evaluateRuleTol(ctx, r, tol);
    checks.push({ ok: !!ok, close: !!close, chip, detail, label: RULES[r.type].label, desc: describe(r), grp: r.grp ?? "—", must: !!r.must, type: r.type }); Object.assign(metrics, m); }
  const av = last(ctx.avgVol()), v = verdict(checks, minPass), nChk = checks.length || 1;
  const fit = pyRound((100 * checks.reduce((s, ck) => s + (ck.ok && !ck.close ? 1 : ck.ok ? 0.6 : 0), 0)) / nChk);
  return { symbol: sym, close: cur, chg: prev ? (cur / prev - 1) * 100 : 0, turnover: ctx.turnoverCr(), vol_x: av ? ctx.v[N - 1] / av : null, rsi: last(ctx.rsi(14)), checks, fails: v.fails, status: v.status, passed: v.passed, total: v.total, miss: v.miss, fit,
    close_n: checks.filter((ck) => ck.close).length, m: metrics, date: ctx.d[N - 1], projected: ctx.projected };
}

// ── saved setups -> rules (legacy_rules, setup_rules, setup_min_pass) ──
function legacyCfgFromText(d) {
  d = d.toLowerCase(); const cfg = {};
  const m = /(?:more than|over|above|greater than|at least|>)\s*(\d+(?:\.\d+)?)\s*%/.exec(d);
  if (m && ["momentum", "previous 30", "last 30", "30 days", "30d"].some((kk) => d.includes(kk))) { cfg.require_momentum_30 = true; cfg.momentum_30_min = parseFloat(m[1]); }
  if (d.includes("rsi") && ["above 50", "> 50", ">50"].some((kk) => d.includes(kk))) cfg.require_rsi_above_50 = true;
  if (d.includes("rsi") && /(?:above|>)\s*(?:its\s*)?(?:rsi\s*)?(?:14\s*)?sma\s*14|above (?:its )?14[- ](?:period )?sma/.test(d)) cfg.require_rsi_above_sma14 = true;
  if (/above (?:the |its )?(?:50[- ]?(?:day )?(?:ma|sma)|sma ?50)/.test(d)) cfg.require_sma50 = true;
  const mm = /(\d+)\s*(?:to|-|–)\s*(\d+)\s*candles?/.exec(d);
  if (mm && d.includes("pullback")) { cfg.require_pullback = true; cfg.pullback_min_candles = parseInt(mm[1], 10); cfg.pullback_max_candles = Math.max(parseInt(mm[2], 10), parseInt(mm[1], 10)); }
  return cfg;
}
export function legacyRules(setup) {                          // old setups (number columns and rule_config v1) -> the same behaviour as rules
  const rules = []; let cfg = cfgOf(setup) || {}; if (!Object.keys(cfg).length) cfg = legacyCfgFromText(setup.visual_rules || "");
  const num = (v) => Number(v || 0) || 0; let rmin = num(setup.rsi_min); const rmax = setup.rsi_max ? Number(setup.rsi_max) : 100;
  if (cfg.require_rsi_above_50) rmin = Math.max(rmin, 50);
  if (rmin > 0 || rmax < 100) rules.push(newRule("rsi", { lo: rmin, hi: rmax || 100 }));
  if (cfg.require_momentum_30) rules.push(newRule("runup", { days: 30, lo: Number(cfg.momentum_30_min ?? 20) }));
  if (cfg.require_rsi_above_sma14) rules.push(newRule("rsi_avg", { dir: "above", n: 14 }));
  if (cfg.require_sma50) rules.push(newRule("ma_pos", { kind: "SMA", len: 50, dir: "above" }));
  if (cfg.require_pullback) rules.push(newRule("pullback", { nmin: Math.trunc(cfg.pullback_min_candles ?? 2), nmax: Math.trunc(cfg.pullback_max_candles ?? 4), vol: Number(cfg.volume_dryup_ratio_max ?? 0.8), max_up: 0 }));
  if (num(setup.volume_multiplier) > 0) rules.push(newRule("vol_spike", { lo: Number(setup.volume_multiplier) }));
  if (num(setup.atr_pct_max) > 0) rules.push(newRule("atr", { lo: num(setup.atr_pct_min), hi: Number(setup.atr_pct_max) }));
  if (num(setup.move_pct_max) > 0) rules.push(newRule("day_move", { mode: "size", lo: num(setup.move_pct_min), hi: Number(setup.move_pct_max) }));
  return rules;
}
export function setupMinPass(setup) { const cfg = cfgOf(setup); if (!cfg) return 0; const v = Number(cfg.min_pass || 0); return isNaN(v) ? 0 : Math.max(0, Math.trunc(v)); }
export function setupRules(setup) { const cfg = cfgOf(setup); if (cfg && cfg.version === 2) return (cfg.rules || []).map(cleanRule).filter(Boolean); return legacyRules(setup); }

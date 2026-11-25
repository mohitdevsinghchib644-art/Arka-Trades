// Arka Trades · Screener: reading a description into rules, ready-made setups, learning from examples, the scan itself and the backtest.
// A port of smart_scan_page.py (parse_locally, TEMPLATES, learn_rules, run_scan, backtest). Pure functions, checked against the Python file.
import { RULES, Ctx, newRule, cleanRule, describe, evaluateRule, evaluateRuleTol, evaluateStock, g, TOLERANCES, tolLabel } from "./scanengine.js";

export const PRICE_PRESETS = { "Setup's own range": null, "₹20 – 100": [20, 100], "₹100 – 250": [100, 250], "₹250 – 500": [250, 500], "₹500 – 1,000": [500, 1000], "₹1,000 – 2,000": [1000, 2000], "₹2,000 – 5,000": [2000, 5000], "Any price": [0, 999999], Custom: "custom" };
export const UNIVERSES = ["All NSE stocks", "NIFTY 500", "Your watchlist", "Arka watchlist", "Your portfolio"];
export const HORIZONS = [5, 10, 20];

// ── plain English -> rules without AI (parse_locally) ──
export function parseLocally(text) {
  const d = " " + String(text).toLowerCase().replace(/–/g, "-").replace(/—/g, "-") + " ", rules = [], out = { price_min: null, price_max: null };
  let m = /(?:rs\.?|₹|price)\s*(\d+(?:\.\d+)?)\s*(?:-|to)\s*(?:rs\.?|₹)?\s*(\d+(?:\.\d+)?)/.exec(d);
  if (m) { out.price_min = parseFloat(m[1]); out.price_max = parseFloat(m[2]); }
  m = /(?:gain(?:ed)?|rose|up|rall(?:y|ied)|momentum|run[- ]?up)[^.]{0,40}?(\d+(?:\.\d+)?)\s*%[^.]{0,30}?(\d+)\s*(?:days|d\b|candles|sessions)/.exec(d) || /(\d+(?:\.\d+)?)\s*%[^.]{0,40}?(?:in|over|within)\s*(?:the\s*)?(?:last|previous|past)?\s*(\d+)\s*(?:days|candles|sessions)/.exec(d);
  if (m) rules.push(newRule("runup", { lo: parseFloat(m[1]), days: parseInt(m[2], 10) }));
  m = /rsi[^.]{0,20}?between\s*(\d+)\s*(?:and|-|to)\s*(\d+)/.exec(d);
  if (m) rules.push(newRule("rsi", { lo: parseFloat(m[1]), hi: parseFloat(m[2]) }));
  else { m = /rsi[^.]{0,15}?(?:above|>|over)\s*(\d+)/.exec(d); if (m) rules.push(newRule("rsi", { lo: parseFloat(m[1]), hi: 100 })); }
  if (/rsi[^.]{0,40}?(?:above|>)\s*(?:its\s*)?(?:\d+\s*)?(?:sma|ma|average)/.test(d)) rules.push(newRule("rsi_avg", { dir: "above", n: 14 }));
  for (const mm of d.matchAll(/\b(ema|sma|dma|ma)\s*-?\s*(\d{1,3})\b|\b(\d{1,3})\s*-?\s*(?:day\s*)?(ema|sma|dma|ma)\b/g)) {
    const before = d.slice(Math.max(0, mm.index - 40), mm.index).split(/[.;\n]/).pop();                      // same sentence only
    if (before.includes("rsi") || !["above", "over", "below", "under", "near", "close to", "and", ","].some((w) => before.includes(w))) continue;      // "RSI above its 14 SMA" is the RSI-average rule, not a price average
    const kind = mm[1] || mm[4], n = parseInt(mm[2] || mm[3], 10), dir = /(below|under)[^.]*$/.test(before) ? "below" : /(near|close to)[^.]*$/.test(before) ? "near" : "above";
    if (!rules.some((r) => r.type === "ma_pos" && r.len === n)) rules.push(newRule("ma_pos", { kind: kind === "ema" ? "EMA" : "SMA", len: n, dir, dmax: dir === "near" ? 2 : 0 }));
  }
  m = /(\d+)\s*(?:-|to)\s*(\d+)\s*(?:low[- ]volume\s*)?candles?/.exec(d);
  const m2 = /(\d+)\s*(?:low[- ]volume\s*)?candles?[^.]{0,40}?(\d+)\s*(?:low[- ]volume\s*)?(?:candles?\s*)?(?:is\s*|are\s*)?(?:also\s*)?(?:fine|ok|okay|acceptable|allowed)/.exec(d);
  const lohi = m ? [parseInt(m[1], 10), parseInt(m[2], 10)] : m2 ? [Math.min(+m2[1], +m2[2]), Math.max(+m2[1], +m2[2])] : null;
  if (d.includes("pullback") || d.includes("pull back") || d.includes("retrace")) {
    const [a, b] = lohi || [2, 4], vol = ["dry", "low volume", "low-volume", "decreas", "declin"].some((kk) => d.includes(kk)) ? 0.8 : 1.0;
    const mu = /(\d+(?:\.\d+)?)\s*(?:-|to)?\s*(\d+(?:\.\d+)?)?\s*%\s*(?:up|green|gain|rise)[^.]{0,30}?(?:ok|okay|fine|allowed|acceptable)/.exec(d), small = mu ? parseFloat(mu[2] || mu[1]) : 4.0;
    rules.push(newRule("pullback", { nmin: a, nmax: b, vol, max_up: d.includes("mostly") ? 1 : 0, small_up: small }));
  } else if (lohi && d.includes("volume")) rules.push(newRule("lowvol", { nmin: lohi[0], nmax: lohi[1], x: 1.0 }));
  m = /volume[^.]{0,25}?(\d+(?:\.\d+)?)\s*(?:x|times)/.exec(d); if (m) rules.push(newRule("vol_spike", { lo: parseFloat(m[1]) }));
  if (/inside\s*(?:bar|candle)/.test(d)) rules.push(newRule("inside"));
  m = /\bnr\s*(\d+)/.exec(d); if (m) rules.push(newRule("nr", { n: parseInt(m[1], 10) }));
  m = /(\d+)[- ]?(?:day|candle)?\s*(?:high\s*)?breakout|breakout\s*(?:above|of)\s*(?:the\s*)?(\d+)/.exec(d); if (m) rules.push(newRule("breakout", { days: parseInt(m[1] || m[2], 10) }));
  if (/hammer/.test(d)) rules.push(newRule("candle", { pat: "hammer" }));
  if (/bullish engulfing/.test(d)) rules.push(newRule("candle", { pat: "bullish engulfing" }));
  if (/volume[^.]{0,30}?(?:decreas|fall|declin|lower)\w*\s*(?:on\s*)?(?:each|every)/.test(d)) rules.push(newRule("vol_fall", { n: 3 }));
  m = /deliver\w*[^.]{0,30}?(\d+(?:\.\d+)?)\s*%/.exec(d); if (m) rules.push(newRule("delivery", { lo: parseFloat(m[1]) }));
  out.rules = rules;
  if (/\b(exact|exactly|strict)\b/.test(d)) out.tol = 0.0;
  else if (/\b(loose|loosely|give or take)\b/.test(d)) out.tol = 0.2;
  else if (/\b(roughly|around|about|approximately|approx)\b|\d\s*%?\s*(?:off|less|short)[^.]{0,20}?(?:ok|okay|fine)/.test(d)) out.tol = 0.1;
  out.assumptions = rules.length ? ["Built-in reader used (no AI): it only understands common phrasings — check every rule below."] : [];
  out.questions = [];
  return out;
}

// ── the AI reader: the prompt goes to the server with the description, the reply is checked here (parse_with_ai) ──
const AI_PROMPT = `You turn a trader's {source} into scanner rules. Reply with ONLY JSON:
{"price_min": number or null, "price_max": number or null, "tolerance": 0 | 5 | 10 | 20, "rules": [ ... ],
  "assumptions": [short sentences: every number you had to choose because the trader did not state it],
  "questions": [at most 3 short questions about things that are genuinely unclear and would change the rules]}

Use ONLY these rule objects (fields exactly as shown; numbers are examples):
{"type":"pullback","nmin":2,"nmax":4,"max_up":0,"small_up":4,"vol":0.8,"each":false,"dmin":0,"dmax":0}   last nmin–nmax candles pull back from the recent high; average volume of those candles ≤ vol × 20-day average; max_up = bigger up candles tolerated; small_up = an up candle that closes up by less than this % is still part of the pullback ("a 3-4% green candle is ok" -> 4); each = every candle below average volume; dmin/dmax = pullback depth % (0 = any)
{"type":"lowvol","nmin":3,"nmax":0,"x":1.0}   at least nmin (to nmax, 0 = no max) consecutive latest candles with volume below x × 20-day average
{"type":"runup","days":30,"lo":20,"hi":0}   rose at least lo % from a low to a later high in the last \`days\` candles (prior momentum)
{"type":"rsi","lo":50,"hi":100,"n":14}
{"type":"rsi_avg","dir":"above","n":14}   RSI above / below its own moving average
{"type":"ma_pos","kind":"EMA","len":20,"dir":"above","dmin":0,"dmax":0}   dir above / below / near; dmax = max distance % (0 = any); near = within dmax % either side
{"type":"ma_stack","k1":"EMA","n1":20,"k2":"EMA","n2":50}   first MA above second
{"type":"ma_cross","k1":"EMA","n1":20,"k2":"EMA","n2":50,"within":5}
{"type":"vol_spike","lo":1.5,"hi":0}   today's volume ≥ lo × 20-day average
{"type":"atr","lo":1,"hi":4,"n":14}   typical daily range % of price
{"type":"day_move","mode":"size","lo":2,"hi":3}   mode size (up or down) / up / down, today's % move
{"type":"change","days":5,"lo":-5,"hi":10}
{"type":"near_high","days":252,"lo":0,"hi":15}   % below the N-candle high
{"type":"breakout","days":20,"amin":0,"amax":0}   close above the previous \`days\`-candle high; amin–amax = how many candles ago it happened (0,0 = today)
{"type":"tight","n":5,"max":6}
{"type":"nr","n":7}
{"type":"inside"}
{"type":"gap","lo":1,"hi":10}
{"type":"rs","days":63,"lo":0}   beats NIFTY 50 by lo % points
{"type":"macd","mode":"above signal"}   or "above zero" / "histogram rising"
{"type":"adx","lo":20,"hi":0,"n":14}
{"type":"candle","pat":"hammer"}   pat: hammer / bullish engulfing / bearish engulfing / doji / strong close / weak close / green candle / red candle
{"type":"ma_touch","kind":"EMA","len":20,"within":1,"tol":0.5}   a low came within tol % of the average in the last \`within\` candles and the close is above it ("bounced off / tested / took support at the 20 EMA")
{"type":"contract","legs":3,"n":5}   \`legs\` blocks of n candles, each with a smaller high-low range than the one before (volatility contraction, VCP)
{"type":"vol_fall","n":3,"skip":0}   volume lower than the previous candle on each of the last n candles ("volume decreasing every candle")
{"type":"delivery","n":5,"lo":40,"x":0}   average delivery % of the last n sessions ≥ lo; x = also ≥ x times its earlier average (0 = skip)

Optional fields allowed on ANY rule (leave them out when not needed):
"ago": N       measure the rule N candles ago instead of on the latest candle
"tf": "weekly" measure on weekly candles instead of daily
"grp": "A"     rules sharing the same letter are either-or: one of them passing is enough ("RSI above 60 OR a volume spike")
"must": true   this rule is always required even when the trader says "most of these"

Rules:
- Flexible numbers become ranges: "3 low-volume candles, 4 is also fine" -> nmin 3, nmax 4.
- "tolerance" = how far a number may miss and still count, in %: 0 when the trader wants exact numbers, 5 by default, 10 when they say
  "roughly / around / about / approximately / 3-4% off is ok", 20 when they say "loosely / give or take a lot". Small up candles inside a pullback
  ("a green candle of 3-4% is fine") go into the pullback's small_up, not into the tolerance.
- Read the trader's intent, not just the words: a "pullback" is price easing back from a recent swing high; "momentum" before it is a run-up;
  "above the 20" means price above the 20 EMA unless SMA is said; "dry volume" means volume clearly below average.
- A pullback "with volume dry-up / decreasing volume" -> pullback rule (vol 0.8 unless stated).
- "low volume candles" without a pullback -> lowvol.
- Only include what the trader describes{or_shows}. Never invent rules or prices. price_min/max null when not given.
- Vague words get the usual meaning and go into "assumptions": "low volume" = below 0.8× the 20-day average; "near" = within 2 %; "strong trend" = ADX ≥ 25.
{extra}
Trader's setup:
"""{text}"""
`;
const AI_PICTURE = `- A chart picture is attached. Read the setup AT THE RIGHT EDGE of the chart (the latest candles): the trend before it, where price sits
  against any moving averages drawn, the last few candles (pullback / tight range / breakout / candle shape) and the volume bars under them.
- You cannot read exact indicator values from a picture: use sensible ranges, and list every one in "assumptions".
`;
export const aiPrompt = (text, image) => AI_PROMPT.replace("{text}", String(text).trim() || "(no description — use the picture)").replace("{source}", image ? "chart picture and notes" : "plain-English technical setup")
  .replace("{or_shows}", image ? " or the picture clearly shows" : "").replace("{extra}", image ? AI_PICTURE : "");
export function cleanAi(data) {                               // the AI's reply -> the same shape parseLocally gives; every rule is checked against the catalogue
  if (!data || typeof data !== "object") return null;
  const rules = (Array.isArray(data.rules) ? data.rules : []).filter((r) => r && typeof r === "object" && !Array.isArray(r)).map((r) => cleanRule({ ...r })).filter(Boolean);
  const pm = (v) => (typeof v === "number" && v >= 0 ? v : null), notes = (kk) => (Array.isArray(data[kk]) ? data[kk] : []).filter((x) => typeof x === "string" && x.trim()).map((x) => x.slice(0, 220)).slice(0, 6);
  const tol = typeof data.tolerance === "number" ? Math.max(0, Math.min(30, data.tolerance)) / 100 : null;
  return { price_min: pm(data.price_min), price_max: pm(data.price_max), rules, tol, assumptions: notes("assumptions"), questions: notes("questions").slice(0, 3) };
}
export const AUDIT_PROMPT = (rules, hasRef) => `You are a skeptical technical-analysis reviewer. Compare the CANDIDATE chart with the trader's
REFERENCE chart${hasRef ? "" : " (no reference picture: judge against the description)"}. Trader's description of the setup:
"""${rules || "(none)"}"""
The candidate already passed every hard rule; you only judge visual similarity and must not re-score the rules.
Score 0-10: 0-2 no resemblance · 3-5 different structure · 6-7 same broad pattern with real differences · 8-10 the same setup.
Reply with ONLY JSON: {"score": int, "matches": [max 3 short phrases], "mismatches": [max 3 short phrases], "caption": "one sentence"}`;
export function cleanAudit(d) { const sc = d && d.score; return { score: Math.max(0, Math.min(10, typeof sc === "number" ? Math.trunc(sc) : 0)), matches: (Array.isArray(d && d.matches) ? d.matches : []).slice(0, 3).map(String), mismatches: (Array.isArray(d && d.mismatches) ? d.mismatches : []).slice(0, 3).map(String), caption: String((d && d.caption) || "").slice(0, 240) }; }

// ── ready-made setups ──
const T = (desc, ...rules) => ({ rules, desc });
export const TEMPLATES = {
  "Momentum pullback on dry volume": T("Stock ran 20%+ in 30 candles and is still above EMA 20, then pulls back 2–4 candles on low volume.",
    ["runup", { days: 30, lo: 20 }], ["rsi", { lo: 50, hi: 100 }], ["ma_pos", { kind: "EMA", len: 20, dir: "above" }], ["ma_stack", { k1: "EMA", n1: 20, k2: "SMA", n2: 50 }], ["pullback", { nmin: 2, nmax: 4, vol: 0.8, max_up: 1 }]),
  "Pullback to EMA 20 that held": T("Uptrend (EMA 20 above SMA 50, price above SMA 200); a low touched EMA 20 in the last 2 candles and today closed near its high.",
    ["ma_stack", { k1: "EMA", n1: 20, k2: "SMA", n2: 50 }], ["ma_pos", { kind: "SMA", len: 200, dir: "above" }], ["ma_touch", { kind: "EMA", len: 20, within: 2, tol: 0.5 }], ["candle", { pat: "strong close" }], ["rsi", { lo: 45, hi: 70 }]),
  "Breakout near 52-week high on volume": T("Closes above the 20-candle high within 5% of the 52-week high, on 1.5× volume, beating NIFTY over 3 months.",
    ["breakout", { days: 20 }], ["near_high", { days: 252, lo: 0, hi: 5 }], ["vol_spike", { lo: 1.5 }], ["candle", { pat: "strong close" }], ["rs", { days: 63, lo: 0 }]),
  "Tight base after a run (flag)": T("A 25%+ run in 40 candles, then 7 candles inside an 8% range on below-average volume, still near the high.",
    ["runup", { days: 40, lo: 25 }], ["tight", { n: 7, max: 8 }], ["lowvol", { nmin: 3, nmax: 0, x: 1.0 }], ["ma_pos", { kind: "EMA", len: 20, dir: "above" }], ["near_high", { days: 60, lo: 0, hi: 12 }]),
  "Volatility contraction (VCP)": T("Three 7-candle legs, each tighter than the last, volume drying up, within 20% of the 52-week high in a long-term uptrend.",
    ["contract", { legs: 3, n: 7 }], ["vol_fall", { n: 3, skip: 1 }], ["near_high", { days: 252, lo: 0, hi: 20 }], ["ma_stack", { k1: "SMA", n1: 50, k2: "SMA", n2: 200 }], ["ma_pos", { kind: "SMA", len: 50, dir: "above" }]),
  "NR7 + inside bar (coiled)": T("Narrowest candle of 7 that is also an inside bar, above EMA 20 in a trending stock.",
    ["nr", { n: 7 }], ["inside", {}], ["ma_pos", { kind: "EMA", len: 20, dir: "above" }], ["adx", { lo: 20 }]),
  "Breakout 3–8 candles ago, now retesting": T("Broke the 30-candle high 3–8 candles ago and is now pulling back on lighter volume above EMA 20.",
    ["breakout", { days: 30, amin: 3, amax: 8 }], ["pullback", { nmin: 2, nmax: 5, vol: 0.9, max_up: 1 }], ["ma_pos", { kind: "EMA", len: 20, dir: "above" }], ["rsi", { lo: 50, hi: 75 }]),
  "Weekly uptrend, daily oversold bounce": T("Above the 20-week EMA with weekly RSI over 50; daily RSI was 30–45 yesterday and today is a green candle closing near its high.",
    ["ma_pos", { kind: "EMA", len: 20, dir: "above", tf: "weekly" }], ["rsi", { lo: 50, hi: 100, tf: "weekly" }], ["rsi", { lo: 30, hi: 45, ago: 1 }], ["candle", { pat: "green candle" }], ["candle", { pat: "strong close" }]),
  "High delivery accumulation": T("Delivery above 50% and 1.2× its earlier average for 5 sessions while price holds a 10% range above SMA 50. Needs NSE delivery data.",
    ["delivery", { n: 5, lo: 50, x: 1.2 }], ["ma_pos", { kind: "SMA", len: 50, dir: "above" }], ["tight", { n: 10, max: 10 }], ["rs", { days: 63, lo: 0 }]),
};
export const templateRules = (name) => [TEMPLATES[name].rules.map(([t, v]) => newRule(t, v)), TEMPLATES[name].desc || ""];

// ── dates and examples ──
const MON = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, sept: 9, oct: 10, nov: 11, dec: 12, january: 1, february: 2, march: 3, april: 4, june: 6, july: 7, august: 8, september: 9, october: 10, november: 11, december: 12 };
export function parseDate(text, today = null) {               // "12 Aug 2026", "12/08/2026" (day first), "2026-08-12", "Aug 12, 2026", "12 Aug" -> "2026-08-12" or null
  const s = String(text || "").trim().toLowerCase().replace(/(\d)(st|nd|rd|th)\b/g, "$1").replace(/,/g, " ").replace(/\s+/g, " "); if (!s) return null;
  const yr = today ? +String(today).slice(0, 4) : new Date().getUTCFullYear(), y4 = (y) => (y == null ? yr : +y < 100 ? (+y < 69 ? 2000 + +y : 1900 + +y) : +y);
  const mk = (y, mo, dd) => { const t = new Date(Date.UTC(y, mo - 1, dd)); return mo >= 1 && mo <= 12 && dd >= 1 && t.getUTCDate() === dd && y > 1900 && y < 2200 ? t.toISOString().slice(0, 10) : null; };
  let m = /^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/.exec(s); if (m) return mk(+m[1], +m[2], +m[3]);
  m = /^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2}|\d{4})$/.exec(s); if (m) return mk(y4(m[3]), +m[2], +m[1]) || mk(y4(m[3]), +m[1], +m[2]);
  m = /^(\d{1,2})[- ]?([a-z]+)[- ]?(\d{2}|\d{4})?$/.exec(s); if (m && MON[m[2]]) return mk(y4(m[3]), MON[m[2]], +m[1]);
  m = /^([a-z]+)[- ]?(\d{1,2})(?: (\d{2}|\d{4}))?$/.exec(s); if (m && MON[m[1]]) return mk(y4(m[3]), MON[m[1]], +m[2]);
  return null;
}
export function parseExamples(text, today = null) {           // "TATAPOWER 12 Aug 2026" / "BEL, 2026-09-03" per line -> [[[SYM, date]], [lines that could not be read]]
  const out = [], bad = [];
  for (let line of String(text).replace(/;/g, "\n").split(/\r?\n/)) { line = line.trim(); if (!line) continue;
    const m = /^(?:NSE:)?([A-Za-z0-9&\-]+)[\s,:|]+(.+)$/.exec(line), d = m ? parseDate(m[2], today) : null;
    if (!m || !d) bad.push(line); else out.push([m[1].toUpperCase(), d]); }
  return [out, bad];
}
const dayGap = (a, b) => Math.round((Date.parse(a + "T00:00:00Z") - Date.parse(b + "T00:00:00Z")) / 864e5);
export function ctxOn(root, day) {                            // the stock on `day` (its last candle on or before it), or null when history does not reach that far
  let lo = 0, hi = root.end; while (lo < hi) { const mid = (lo + hi) >> 1; if (root.d[mid] <= day) lo = mid + 1; else hi = mid; }
  if (lo < 30) return null; const v = root.at(lo); return dayGap(day, v.d[v.end - 1]) <= 5 ? v : null;
}
const PB = { nmin: 1, nmax: 8, max_up: 3, vol: 5.0 };
const PROBES = [["Run-up 30c %", ["runup", { days: 30, lo: 0 }], "runup"], ["RSI", ["rsi", { lo: 0, hi: 100 }], "rsi"], ["vs EMA20 %", ["ma_pos", { kind: "EMA", len: 20, dir: "above" }], "ma_dist"], ["vs SMA50 %", ["ma_pos", { kind: "SMA", len: 50, dir: "above" }], "ma_dist"],
  ["vs SMA200 %", ["ma_pos", { kind: "SMA", len: 200, dir: "above" }], "ma_dist"], ["Pullback candles", ["pullback", PB], "pb_n"], ["Pullback vol ×", ["pullback", PB], "pb_vol"], ["Pullback depth %", ["pullback", PB], "pb_depth"], ["Pullback up candles", ["pullback", PB], "pb_ups"],
  ["Low-vol candles", ["lowvol", { nmin: 1, x: 1.0 }], "lowvol"], ["Volume ×", ["vol_spike", { lo: 0 }], "vol_x"], ["ATR %", ["atr", { lo: 0 }], "atr"], ["Below 52w high %", ["near_high", { days: 252, lo: 0, hi: 100 }], "off_high"],
  ["5-candle range %", ["tight", { n: 5, max: 50 }], "tight"], ["vs NIFTY 63c", ["rs", { days: 63, lo: -100 }], "rs"], ["ADX", ["adx", { lo: 0 }], "adx"], ["Close position %", ["candle", { pat: "strong close" }], "close_pos"]];
const FLAGS = [["RSI > its avg", ["rsi_avg", { dir: "above", n: 14 }]], ["EMA20 > SMA50", ["ma_stack", { k1: "EMA", n1: 20, k2: "SMA", n2: 50 }]], ["20c breakout", ["breakout", { days: 20 }]], ["Inside bar", ["inside", {}]], ["NR7", ["nr", { n: 7 }]],
  ["Touched EMA20", ["ma_touch", { kind: "EMA", len: 20, within: 2, tol: 0.5 }]], ["MACD > signal", ["macd", { mode: "above signal" }]]];
// views: [[label, the stock on the example date]]. Measures every example and proposes rules whose ranges cover ALL of them -> [rules, measurements {title: {label: value}}]
export function learnRules(views) {
  const meas = {}, n = views.length, mn = (x) => Math.min(...x), mx = (x) => Math.max(...x);
  for (const [title, [rt, kw], key] of PROBES) { const rule = newRule(rt, kw); meas[title] = {}; for (const [lab, v] of views) meas[title][lab] = evaluateRule(v, rule)[3][key] ?? null; }
  for (const [title, [rt, kw]] of FLAGS) { const rule = newRule(rt, kw); meas[title] = {}; for (const [lab, v] of views) meas[title][lab] = !!evaluateRule(v, rule)[0]; }
  const vals = (title) => { const x = Object.values(meas[title]).filter((v) => v != null); return x.length === n ? x : null; };      // a rule is only proposed when every example has the value
  const allflag = (title) => Object.values(meas[title]).every(Boolean), rules = [];
  let x = vals("Run-up 30c %"); if (x && x.length && mn(x) >= 8) rules.push(newRule("runup", { days: 30, lo: Math.floor(mn(x) * 0.9) }));
  x = vals("RSI"); if (x && x.length) rules.push(newRule("rsi", { lo: Math.max(0, Math.floor(mn(x) - 3)), hi: Math.min(100, Math.ceil(mx(x) + 3)) }));
  if (allflag("RSI > its avg")) rules.push(newRule("rsi_avg", { dir: "above", n: 14 }));
  for (const [title, kind, ln] of [["vs EMA20 %", "EMA", 20], ["vs SMA50 %", "SMA", 50], ["vs SMA200 %", "SMA", 200]]) { x = vals(title); if (!x || !x.length) continue; const am = mx(x.map(Math.abs));
    if (am <= 3 && mn(x) < 0 && 0 < mx(x)) rules.push(newRule("ma_pos", { kind, len: ln, dir: "near", dmax: Math.ceil(am * 1.25 * 2) / 2 }));
    else if (mn(x) > 0) rules.push(newRule("ma_pos", { kind, len: ln, dir: "above", dmax: ln === 20 ? Math.ceil(mx(x) * 1.3) : 0 }));
    else if (mx(x) < 0) rules.push(newRule("ma_pos", { kind, len: ln, dir: "below" })); }
  if (allflag("EMA20 > SMA50")) rules.push(newRule("ma_stack", { k1: "EMA", n1: 20, k2: "SMA", n2: 50 }));
  const pn = vals("Pullback candles"), pv = vals("Pullback vol ×"), pd = vals("Pullback depth %"), pu = vals("Pullback up candles"), full = (a) => a && a.length;
  if (full(pn) && full(pv) && full(pd) && full(pu)) rules.push(newRule("pullback", { nmin: Math.trunc(mn(pn)), nmax: Math.trunc(mx(pn)), max_up: Math.trunc(mx(pu)), vol: Math.ceil(mx(pv) * 1.1 * 20) / 20, dmax: Math.ceil(mx(pd) * 1.3) }));
  else { x = vals("Low-vol candles"); if (full(x) && mn(x) >= 2) rules.push(newRule("lowvol", { nmin: Math.trunc(mn(x)), nmax: 0, x: 1.0 })); }
  x = vals("Volume ×"); if (full(x) && mn(x) >= 1.3) rules.push(newRule("vol_spike", { lo: Math.floor(mn(x) * 0.9 * 10) / 10 }));
  x = vals("Below 52w high %"); if (full(x) && mx(x) <= 25) rules.push(newRule("near_high", { days: 252, lo: 0, hi: Math.ceil(mx(x) * 1.25) }));
  x = vals("5-candle range %"); if (full(x) && mx(x) <= 10) rules.push(newRule("tight", { n: 5, max: Math.ceil(mx(x) * 1.2 * 2) / 2 }));
  x = vals("vs NIFTY 63c"); if (full(x) && mn(x) > 0) rules.push(newRule("rs", { days: 63, lo: 0 }));
  x = vals("ADX"); if (full(x) && mn(x) >= 20) rules.push(newRule("adx", { lo: Math.floor(mn(x) * 0.9) }));
  x = vals("ATR %"); if (full(x)) rules.push(newRule("atr", { lo: Math.floor(mn(x) * 0.75 * 10) / 10, hi: Math.ceil(mx(x) * 1.35 * 10) / 10 }));
  x = vals("Close position %"); if (full(x) && mn(x) >= 75) rules.push(newRule("candle", { pat: "strong close" }));
  for (const [title, [rt, kw]] of FLAGS.slice(2, 6)) if (allflag(title)) rules.push(newRule(rt, kw));
  return [rules, meas];
}

// ── the scan ──
export function needs(rules) {                                // what a set of rules needs besides one year of prices
  const on = rules.filter((r) => r.on ?? true);
  return { nifty: on.some((r) => r.type === "rs"), deliv: on.some((r) => r.type === "delivery"),
    long: on.some((r) => r.tf === "weekly" || (["ma_pos", "ma_stack", "ma_cross", "ma_touch"].includes(r.type) && Math.max(r.len || 0, r.n1 || 0, r.n2 || 0) > 150)) };
}
// first stage (price band and rough liquidity on the last 5 candles); widen > 1 loosens it for a past-date scan or a backtest
export function candidates(universe, frames, pmin, pmax, minTurnover, widen = 1) {
  const priced = [], band = [];
  for (const sym of universe) { const F = frames(sym); if (!F || !F.c.length) continue; priced.push(sym);
    const n = F.c.length, a = Math.max(0, n - 5), cl = []; for (let i = a; i < n; i++) if (F.c[i] != null && !isNaN(F.c[i])) cl.push(F.c[i]);
    if (!cl.length) continue; const lastC = cl[cl.length - 1]; if (!(pmin / widen <= lastC && lastC <= pmax * widen)) continue;
    if (minTurnover > 0) { let sum = 0, k = 0; const b = n - a > 1 ? n - 1 : n; for (let i = a; i < b; i++) { const x = F.c[i] * F.v[i]; if (F.v[i] != null && !isNaN(x)) { sum += x; k++; } }
      if (k && sum / k / 1e7 < (minTurnover * 0.5) / widen) continue; }                                    // lenient here; the exact check comes later
    band.push(sym); }
  return { priced, band };
}
const extraFor = (sym, deliv) => (deliv && deliv.s[sym] ? { deliv: { d: deliv.d, v: deliv.s[sym] } } : {});
// asof: a past date -> the scan as it looked at that day's close, plus what the price did since. frames(sym) -> candles; liveFrac(F) -> part of the session done, or null
export async function runScan({ universe, frames, rules, pmin, pmax, minTurnover = 0, wantNear = true, minPass = 0, asof = null, tol = 0, nifty = null, deliv = null, liveFrac = () => null, progress = null, pause = null }) {
  const t0 = Date.now(), on = rules.filter((r) => r.on ?? true), need = needs(on), stats = { universe: universe.length };
  const { priced, band } = candidates(universe, frames, pmin, pmax, minTurnover, asof != null ? 2 : 1);
  stats.priced = priced.length; stats.in_band = band.length;
  const nf = need.nifty ? nifty : null, dl = need.deliv ? deliv : null, match = [], near = [], noData = [], funnel = on.map((r) => ({ desc: describe(r), pass: 0, only: 0 })); let liquid = 0;
  for (let i = 0; i < band.length; i++) { const sym = band[i], F = frames(sym);
    if (i % 100 === 0) { if (progress) progress((i + 1) / Math.max(1, band.length), `Checking rules · ${i + 1}/${band.length}`); if (pause) await pause(); }
    if (!F || F.c.length < 30) { noData.push(sym); continue; }
    let res;
    if (asof == null) res = evaluateStock(sym, F, on, { liveFrac: liveFrac(F), nifty: nf, minPass, extra: extraFor(sym, dl), tol });
    else { const root = new Ctx(sym, F, null, nf, extraFor(sym, dl)), view = ctxOn(root, asof); res = view ? evaluateStock(sym, null, on, { ctx: view, minPass, tol }) : null;
      if (res) { if (!(pmin <= res.close && res.close <= pmax)) continue; res.since = (root.c[root.end - 1] / res.close - 1) * 100; res.since_days = root.end - view.end; } }
    if (!res) { noData.push(sym); continue; }
    if (res.turnover < minTurnover) continue;
    liquid++; const bad = []; res.checks.forEach((ck, kk) => { if (ck.ok) funnel[kk].pass++; else bad.push(kk); }); if (bad.length === 1) funnel[bad[0]].only++;
    if (asof == null) res.chart = F; else { let n = F.d.length; while (n && F.d[n - 1] > res.date) n--; res.chart = { d: F.d.slice(0, n), o: F.o.slice(0, n), h: F.h.slice(0, n), l: F.l.slice(0, n), c: F.c.slice(0, n), v: F.v.slice(0, n) }; }      // the chart as it looked on that date
    if (res.status === "match") match.push(res); else if (res.status === "near" && wantNear) near.push(res); }
  const byFit = (a, b) => b.fit - a.fit; match.sort(byFit); near.sort(byFit);
  Object.assign(stats, { liquid, no_data: noData.length, match: match.length, near: near.length, secs: (Date.now() - t0) / 1000, exact: match.filter((r) => !r.close_n).length });
  let asofD = null; for (const r of match.concat(near)) if (asofD == null || r.date > asofD) asofD = r.date;
  return { match, near, stats, no_data: noData.slice(0, 200), asof: asofD, funnel, past: asof != null, deliv_missing: need.deliv && !deliv, live: match.concat(near).some((r) => r.projected), tol };
}

// ── backtest: what happened after past signals ──
// Replays the setup on every one of the last `sessions` candles of every stock. A signal is read at the close; entry is the NEXT candle's open.
// The same stock is not counted again for `gap` candles.
export async function backtest({ syms, frames, rules, pmin, pmax, minTurnover = 0, sessions = 120, minPass = 0, nifty = null, deliv = null, gap = 5, tol = 0, progress = null, pause = null }) {
  const t0 = Date.now(), on = rules.filter((r) => r.on ?? true), simple = !minPass && on.every((r) => (r.grp ?? "—") === "—");
  const quick = on.map((r, i) => [r, i]).sort((a, b) => (a[0].tf === "weekly") - (b[0].tf === "weekly") || a[1] - b[1]).map((x) => x[0]);      // cheap daily rules first
  const signals = [], baseSum = {}, baseN = {}; for (const hz of HORIZONS) { baseSum[hz] = 0; baseN[hz] = 0; }
  let days = 0, stocks = 0;
  for (let si = 0; si < syms.length; si++) { const sym = syms[si], F = frames(sym);
    if (si % 20 === 0) { if (progress) progress((si + 1) / Math.max(1, syms.length), `Replaying ${si + 1}/${syms.length} stocks · ${signals.length} signals so far`); if (pause) await pause(); }
    if (!F || F.c.length < 80) continue;
    const root = new Ctx(sym, F, null, nifty, extraFor(sym, deliv)), N = root.end, { o, c, l, h } = root, first = Math.max(60, N - sessions);
    stocks++;
    for (const hz of HORIZONS) for (let t = first; t <= N - hz; t++) if (c[t - 1] >= pmin && c[t - 1] <= pmax) { baseSum[hz] += (c[t + hz - 1] / o[t] - 1) * 100; baseN[hz]++; }      // baseline: buy any day in the window at the next open
    let lastSig = -1e9;
    for (let end = first; end <= N; end++) { const px = c[end - 1];                 // the stock as of candle end-1
      if (!(pmin <= px && px <= pmax) || end - 1 - lastSig < gap) continue;
      days++; const view = root.at(end);
      if (simple && !quick.every((r) => evaluateRuleTol(view, r, tol)[0])) continue;
      const res = evaluateStock(sym, null, on, { ctx: view, minPass, tol });
      if (!res || res.status !== "match" || res.turnover < minTurnover) continue;
      lastSig = end - 1; const sig = { symbol: sym, date: root.d[end - 1], close: px, entry: null, open: end >= N };
      if (end < N) { const e = o[end]; sig.entry = e; for (const hz of HORIZONS) sig["r" + hz] = end + hz - 1 < N ? (c[end + hz - 1] / e - 1) * 100 : null;
        let lo = Infinity, hi = -Infinity; for (let i = end; i < Math.min(N, end + 20); i++) { if (l[i] < lo) lo = l[i]; if (h[i] > hi) hi = h[i]; }
        sig.worst = (lo / e - 1) * 100; sig.best = (hi / e - 1) * 100; sig.now = (c[N - 1] / e - 1) * 100; }
      signals.push(sig); } }
  const summary = {};
  for (const hz of HORIZONS) { const x = signals.map((s) => s["r" + hz]).filter((v) => v != null); if (!x.length) { summary[hz] = null; continue; }
    const srt = x.slice().sort((a, b) => a - b), mid = srt.length >> 1;
    summary[hz] = { n: x.length, win: (x.filter((v) => v > 0).length / x.length) * 100, mean: x.reduce((a, b) => a + b, 0) / x.length, median: srt.length % 2 ? srt[mid] : (srt[mid - 1] + srt[mid]) / 2, worst: srt[0], best: srt[srt.length - 1] }; }
  const worst = signals.map((s) => s.worst).filter((v) => v != null), base = {}; for (const hz of HORIZONS) base[hz] = baseN[hz] ? baseSum[hz] / baseN[hz] : null;
  signals.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
  return { signals, summary, base, stats: { stocks, days, signals: signals.length, names: new Set(signals.map((s) => s.symbol)).size, avg_worst: worst.length ? worst.reduce((a, b) => a + b, 0) / worst.length : null, secs: (Date.now() - t0) / 1000, sessions } };
}

// ── result order ──
const M = (r, kk, d) => (r.m[kk] ?? d);
export const SORTS = { "Best fit": (r) => [-(r.fit || 0), -r.turnover], "Liquidity (turnover)": (r) => [-r.turnover], "Day % (best first)": (r) => [-r.chg], "Lowest pullback volume": (r) => [M(r, "pb_vol", 9)], "Strongest run-up": (r) => [-M(r, "runup", -1e9)],
  "Closest to moving average": (r) => [Math.abs(M(r, "ma_dist", 1e9))], "RSI (high first)": (r) => [-(r.rsi || 0)], "Relative strength vs NIFTY": (r) => [-M(r, "rs", -1e9)], "Most rules passed": (r) => [-(r.passed || 0)],
  "Move since the scan date": (r) => [-(r.since || -1e9)], "A–Z": (r) => [r.symbol] };
export function sortRows(rows, by) { const key = SORTS[by] || SORTS["Best fit"]; return rows.map((r, i) => [key(r), i, r]).sort((a, b) => { for (let j = 0; j < a[0].length; j++) { if (a[0][j] < b[0][j]) return -1; if (a[0][j] > b[0][j]) return 1; } return a[1] - b[1]; }).map((x) => x[2]); }
export function plainSummary(rules, tol, minPass = 0) {       // the setup read back in plain words -> { need, items, foot } or null
  const on = rules.filter((r) => r.on ?? true); if (!on.length) return null;
  const pb = on.find((r) => r.type === "pullback");
  return { need: !minPass || minPass >= on.length ? "all of these" : `at least ${minPass} of these ${on.length}`, items: on.map(describe),
    foot: (!tol ? "Numbers must be met exactly." : `Close enough counts: a number that misses by less than ${g(tol * 100)}% still passes (shown as ≈), so a stock is not dropped for a tiny miss.`)
      + (pb && pb.small_up ? ` Inside the pullback, green candles that close up by less than ${g(pb.small_up)}% are treated as part of the pullback.` : "") };
}
export { TOLERANCES, tolLabel, RULES };

// ── nightly price files ──
// A chunk holds many stocks: { d: [dates], s: { SYM: [first date number, close ×100, open − close, high − close, low − close, volume] } } with whole numbers (paise).
// A stock that missed some sessions carries its own list of date numbers in a 7th slot.
export function unpack(chunk, into = {}) {
  for (const [sym, a] of Object.entries(chunk.s)) { const [i0, c, o, h, l, v, own] = a, n = c.length;
    into[sym] = { d: own ? own.map((i) => chunk.d[i]) : chunk.d.slice(i0, i0 + n), c: c.map((x) => x / 100), o: c.map((x, i) => (x + o[i]) / 100), h: c.map((x, i) => (x + h[i]) / 100), l: c.map((x, i) => (x + l[i]) / 100), v }; }
  return into;
}
export function joinFrames(older, newer) {                    // older candles + the last year; where both have a day the newer file wins
  if (!older || !older.d.length) return newer; if (!newer || !newer.d.length) return older;
  let n = older.d.length; while (n && older.d[n - 1] >= newer.d[0]) n--;
  const o = {}; for (const kk of ["d", "o", "h", "l", "c", "v"]) o[kk] = older[kk].slice(0, n).concat(newer[kk]); return o;
}
export function withLive(F, q, day) {                         // add today's candle (or replace it) from the intraday file: q = [open, high, low, close, volume]
  if (!F || !q || !(q[3] > 0)) return F; const n = F.d.length; if (n && F.d[n - 1] > day) return F;
  const cut = n && F.d[n - 1] === day ? n - 1 : n, o = {}; for (const [kk, x] of [["d", day], ["o", q[0]], ["h", q[1]], ["l", q[2]], ["c", q[3]], ["v", q[4]]]) o[kk] = F[kk].slice(0, cut).concat([x]); return o;
}

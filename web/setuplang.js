// Arka Trades · reads a trading setup written in plain words and turns it into exact, testable rules (a port of setup_language.py).
// A fixed set of patterns, not an AI model: the same sentence always gives the same rules, and the page shows the reading before anything runs.
// parse(text) -> { rules, ex, book, read: [[your words, meaning, note, "entry"|"exit"]], missed: [[your words, why]], assumed: [text] }
import { EXIT_DEFAULT, ENTRIES, STOPS } from "./btengine.js";
import { fx } from "./rsengine.js";

const R = (src, flags = "") => new RegExp(src, flags);
const NUM = String.raw`(\d+(?:\.\d+)?)`, MA = String.raw`(\d+)\s*(ema|sma)`;
const CMP_UP = String.raw`(?:above|over|>=|>|greater than|more than|higher than|at least|atleast|crossed above|crosses above|cross above|exceeds?)`;
const CMP_DN = String.raw`(?:below|under|<=|<|less than|lower than|at most|atmost)`;
export const NO_TIME_STOP = 100000, ASSUMED_HOLD = 60;
const pyNum = (x) => { const f = parseFloat(x); return Number.isInteger(f) ? String(f) : pyFloat(f); };
const pyFloat = (f) => { if (Number.isInteger(f)) return f.toFixed(1); const s = String(f); return /e-/.test(s) ? s.replace(/e-(\d)$/, "e-0$1") : s; };
export const _n = (x) => pyNum(x);
const search = (src, s) => R(src).exec(s);
const has = (src, s) => R(src).test(s);

export function normalise(text) {
  let t = " " + String(text ?? "").toLowerCase().replace(/’/g, "'").replace(/×/g, "x").replace(/₹/g, " rs ").replace(/–/g, "-").replace(/—/g, "-") + " ";
  const sub = (src, rep) => { t = t.replace(R(src, "g"), rep); };
  sub(String.raw`\bper ?cent\b|\bpercent\b|\bpct\b`, "%");
  sub(String.raw`\bstoploss\b|\bstop-loss\b`, "stop loss");
  sub(String.raw`\btake profit\b|\bbook profits?\b|\bprofit target\b|\btgt\b|\btp\b`, "target");
  sub(String.raw`\byesterday'?s?\b|\bprior day'?s?\b|\bprevious day'?s?\b|\bprev\.? day'?s?\b|\blast day'?s?\b`, "previous day");
  sub(String.raw`\b52[- ]?(?:w|wk|wks|weeks?)\b|\bone year\b|\b1 year\b|\byearly\b`, "52 week");
  sub(String.raw`\ball[- ]time high\b|\bath\b`, "all time high");
  sub(String.raw`\bexponential moving averages?\b|\bexponential averages?\b|\bemas\b`, "ema");
  sub(String.raw`\bsimple moving averages?\b|\bmoving averages?\b|\bdmas?\b|\bsmas\b|\bday averages?\b`, "sma");
  sub(String.raw`\b(ema|sma)\s*\(?\s*(\d+)\s*\)?`, "$2 $1 ");                                     // ema21, ema(21) -> 21 ema
  sub(String.raw`\b(\d+)\s*-?\s*(?:day|days|d|period|periods)\s*-?\s*(ema|sma)\b`, "$1 $2");
  sub(String.raw`\b(\d+)\s*-?\s*(?:week|weeks|wk)\s*-?\s*(ema|sma)\b`, (m, a, b) => `${parseInt(a, 10) * 5} ${b}`);
  sub(String.raw`\b(\d+)\s*ma\b`, "$1 sma");                                                     // 20ma, 20 ma
  sub(String.raw`\b(\d+)(ema|sma)\b`, "$1 $2");
  sub(String.raw`\b(\d+)\s*-?\s*(?:week|weeks|wk|wks)\b(?!\s*(?:high|low))`, (m, a) => `${parseInt(a, 10) * 5} day`);
  sub(String.raw`\b(\d+)\s*-?\s*(?:month|months|mo|mth)\b`, (m, a) => `${parseInt(a, 10) * 21} day`);
  sub(String.raw`\b(?!52)(\d+)\s*-?\s*(?:week|weeks|wk|wks)\b(?=\s*(?:high|low))`, (m, a) => `${parseInt(a, 10) * 5} day`);
  sub(String.raw`\b(\d+)\s*-?\s*(?:days|day|d|sessions?|bars?|candles)\b`, "$1 day");
  sub(String.raw`\b(\d+(?:\.\d+)?)\s*(?:x|times)\b`, "$1 x");
  sub(String.raw`\b1\s*:\s*(\d+(?:\.\d+)?)\b`, "target $1 r");                                    // 1:2 risk reward
  sub(String.raw`\b(\d+(?:\.\d+)?)\s*r\b`, "$1 r");
  sub(String.raw`\bbetween\s+` + NUM + String.raw`\s*%?\s*(?:and|to|-)\s*` + NUM, "between $1 to $2");
  sub(String.raw`\b` + NUM + String.raw`\s*(?:and|&|,|/)\s*` + NUM + String.raw`\s*(ema|sma)\b`, "$1 $3 and $2 $3");   // 50 and 200 sma
  sub(String.raw`(\d)\s*%`, "$1 %");
  return t.replace(/[ \t]+/g, " ");
}
const STRIP = " \t:;-.,!?()[]\"'*•";
const strip = (s, ch = STRIP) => { let a = 0, b = s.length; while (a < b && ch.includes(s[a])) a++; while (b > a && ch.includes(s[b - 1])) b--; return s.slice(a, b); };
const pyStrip = (s) => s.replace(/^[\s]+|[\s]+$/g, "");
export function clauses(text) {                              // [[line number, clause]]: new lines, full stops, commas, semicolons, "and", "with", "then"
  const out = [];
  text.split(/[\n\r]+/).forEach((line, ln) => { for (const part of line.split(R(String.raw`;|\.(?!\d)|,(?!\d)|\band\b|&|\+|\bthen\b|\bwith\b|\bwhile\b|\bbut\b|\balso\b|\bplus\b|\bon\b(?=\s+\w+\s+vol)`))) { const c = strip(part); if (c) out.push([ln, c]); } });
  return out;
}
const maOf = (m, i = 1) => [m[i + 1] === "ema" ? "EMA" : "SMA", parseInt(m[i], 10)];
const maTxt = (kind, n) => `${n}-day ${kind === "EMA" ? "EMA" : "average (SMA)"}`;
const rule = (type, kw = {}) => ({ type, ...kw });
const EXIT_WORDS = R(String.raw`\b(stop loss|stop|sl|target|exit|sell|trail|trailing|square off|book|hold|holding|time stop|breakeven|break even|risk reward|risk|rr|skip|avoid)\b`);
const f2 = (v) => fx(v, 2, false);

function entry(c) {                                          // -> [rule, meaning, note] or ["miss", why] or null
  let s = pyStrip(c.replace(R(String.raw`^(?:buy|entry|enter|go long|long|signal)\s*(?:when|if|on|:|-)?\s*`), ""));
  s = pyStrip(s.replace(R(String.raw`^(?:when|if|the|stock|share|it|should be|must be|is|has|have|a|an)\s+`), ""));
  const mkt = has(String.raw`\b(nifty|market|index|sensex)\b`, s); let m;
  // relative strength / market
  m = search(String.raw`(?:outperform\w*|beat\w*|stronger than|strength (?:vs|versus|against|over)|rs (?:vs|versus|against))\s*(?:the\s*)?(?:nifty|market|index)(?:.*?(\d+) day)?`, s);
  if (m) { const n = m[1] ? parseInt(m[1], 10) : 63; return [rule("x_beat_nifty", { n }), `Return over the last ${n} sessions is higher than NIFTY 50's`, m[1] ? null : "No period given, so I used 63 sessions (about 3 months)."]; }
  if (has(String.raw`\brs\s*(?:rating|rank)\b|\brelative strength (?:rating|rank)\b`, s)) return ["miss", "RS rating ranks a stock against a whole index, which this test does not load. Write \"outperforming Nifty over 3 months\" instead."];
  if (mkt) {
    m = search(String.raw`(?:nifty|market|index)\D*?` + CMP_UP + String.raw`\s*(?:its|the|their)?\s*` + MA, s);
    if (m) { const [k, n] = maOf(m); return [rule("nifty_above_ma", { kind: k, n }), `NIFTY 50 closes above its ${maTxt(k, n)}`, null]; }
    if (has(String.raw`(?:uptrend|bullish|positive|strong)`, s)) return [rule("nifty_above_ma", { kind: "EMA", n: 50 }), "NIFTY 50 closes above its 50-day EMA", "\"Market in uptrend\" has no exact meaning, so I used NIFTY above its 50-day EMA."];
    return null;
  }
  // moving averages
  if (has("golden cross", s)) return [rule("x_ma_cross_up", { k1: "SMA", n1: 50, k2: "SMA", n2: 200 }), "50-day average crosses above the 200-day average today", null];
  m = search(MA + String.raw`\s*(?:has |have |just )?(?:cross\w*|crossover)\s*(?:above|over|up through)?\s*(?:the\s*)?` + MA, s);
  if (m) { const [k1, n1] = maOf(m, 1), [k2, n2] = maOf(m, 3); return [rule("x_ma_cross_up", { k1, n1, k2, n2 }), `${maTxt(k1, n1)} crosses above the ${maTxt(k2, n2)} today`, null]; }
  m = search(MA + String.raw`\s*(?:is |stays |remains )?` + CMP_UP + String.raw`\s*(?:the\s*)?` + MA, s);
  if (m) { const [k1, n1] = maOf(m, 1), [k2, n2] = maOf(m, 3); return [rule("x_ma_above_ma", { k1, n1, k2, n2 }), `${maTxt(k1, n1)} is above the ${maTxt(k2, n2)}`, null]; }
  m = search(MA + String.raw`\s*(?:is |are )?(?:rising|sloping up\w*|slop\w* up|trending up|pointing up|going up|upward\w*|turning up)(?:.*?(\d+) day)?`, s);
  if (m) { const [k, n] = maOf(m), d = m[3] ? parseInt(m[3], 10) : 20; return [rule("ma_rising", { kind: k, n, days: d }), `${maTxt(k, n)} is higher than it was ${d} sessions ago`, m[3] ? null : "\"Rising\" needs a look-back, so I compare with 20 sessions ago."]; }
  m = search(String.raw`(?:cross\w*|crossover|reclaim\w*|moves? back)\s*(?:above|over|up through)?\s*(?:the\s*)?` + MA, s);
  if (m) { const [k, n] = maOf(m); return [rule("x_cross_above_ma", { kind: k, n }), `Close crosses above the ${maTxt(k, n)} today (it closed at or below it the day before)`, null]; }
  m = search(String.raw`(?:not more than|no more than|at most|within|less than|max(?:imum)?)\s*` + NUM + String.raw`\s*%\s*(?:above|over|from|of|away from)\s*(?:the\s*)?` + MA, s);
  if (m) { const [k, n] = maOf(m, 2); return [rule("not_extended", { kind: k, n, pct: parseFloat(m[1]) }), `Close is at most ${_n(m[1])}% above the ${maTxt(k, n)}`, null]; }
  m = search(String.raw`(?:pull\w*\s*back|pullback|dip\w*|retrace\w*|retest\w*|touch\w*|tests?|tag\w*|bounce\w*|support|comes? (?:back )?(?:down )?to|near|at)\s*(?:to|from|off|at|on|of|near|the|its|\s)*` + MA, s);
  if (m) { const [k, n] = maOf(m); return [rule("touch_ma", { kind: k, n, pct: 1.5, days: 3 }), `The low came within 1.5% of the ${maTxt(k, n)} in the last 3 sessions`, "\"Pullback to\" needs numbers, so I used: low within 1.5% of the average, any of the last 3 sessions."]; }
  m = search(CMP_UP + String.raw`\s*(?:the|its|all|both)?\s*` + MA, s);
  if (m) { const [k, n] = maOf(m); return [rule("close_above_ma", { kind: k, n }), `Close is above the ${maTxt(k, n)}`, null]; }
  m = search(CMP_DN + String.raw`\s*(?:the|its)?\s*` + MA, s);
  if (m) { const [k, n] = maOf(m); return [rule("x_close_below_ma", { kind: k, n }), `Close is below the ${maTxt(k, n)}`, null]; }
  // 52-week levels
  if (has("all time high", s)) return ["miss", "All-time high needs the full price history, which is not loaded. Write \"new 52 week high\" instead."];
  m = search(String.raw`(?:within|inside|less than|not more than|at most)\s*` + NUM + String.raw`\s*%\s*(?:of|from|below|off)?\s*(?:the|its)?\s*52 week high`, s);
  if (m) return [rule("near_52w_high", { pct: parseFloat(m[1]) }), `Close is within ${_n(m[1])}% of the 52-week high`, null];
  m = search(NUM + String.raw`\s*%\s*(?:or more\s*)?(?:above|over|off|from|up from)\s*(?:the|its)?\s*52 week low`, s);
  if (m) return [rule("above_52w_low", { pct: parseFloat(m[1]) }), `Close is at least ${_n(m[1])}% above the 52-week low`, null];
  if (has(String.raw`(?:new|fresh|makes?|making|hits?|hitting|at|breaks?|breakout\w*|above|cross\w*)\s*(?:a|the|its|of|to|out)?\s*(?:new\s*)?52 week high`, s)) return [rule("new_52w_high"), "Makes a new 52-week high today", null];
  if (has(String.raw`(?:near|close to|around)\s*(?:the|its)?\s*52 week high`, s)) return [rule("near_52w_high", { pct: 10.0 }), "Close is within 10% of the 52-week high", "\"Near\" has no exact meaning, so I used within 10%."];
  // breakouts
  if (has(String.raw`(?:above|over|>|cross\w*|break\w*|clos\w* above)\s*(?:the\s*)?previous day\s*(?:'s)?\s*high`, s)) return [rule("close_above_prev_high"), "Close is above the previous day's high", null];
  if (has(String.raw`(?:above|over|>|higher than)\s*(?:the\s*)?previous day\s*(?:'s)?\s*close`, s) || has(String.raw`\bhigher close\b`, s)) return [rule("x_close_above_prev_close"), "Close is above the previous day's close", null];
  m = search(String.raw`(\d+) day\s*(?:high|breakout|closing high|range breakout)|(?:highest|high|breakout|break)\s*(?:close|high)?\s*(?:of|in|over|above|out of)?\s*(?:the\s*)?(?:last|past|previous|prior)?\s*(\d+) day`, s);
  if (m && has("high|break", s)) { const n = parseInt(m[1] || m[2], 10); return [rule("breakout_n", { n }), `Close is above the highest high of the previous ${n} sessions`, null]; }
  if (has(String.raw`\bbreak\s*out\b|\bbreakout\b|\bbreaks out\b`, s)) return [rule("breakout_n", { n: 20 }), "Close is above the highest high of the previous 20 sessions", "No period given for the breakout, so I used a 20-day high."];
  // consolidation / contraction
  m = R(String.raw`(?:tight|narrow|base|consolidat\w*|range|sideways)\D*?(\d+) day\D*?` + NUM + String.raw`\s*%`, "d").exec(s) || R(String.raw`(?:tight|narrow|base|consolidat\w*|range|sideways)\D*?` + NUM + String.raw`\s*%\D*?(\d+) day`, "d").exec(s);
  if (m) { const a = parseFloat(m[1]), b = parseFloat(m[2]), [n, pct] = s.slice(m.indices[1][0], m.indices[2][0]).includes("day") ? [Math.trunc(a), b] : [Math.trunc(b), a];
    return [rule("range_tight", { n, pct }), `High-to-low range of the previous ${n} sessions is at most ${_n(pct)}% of the price`, null]; }
  if (has(String.raw`\bvcp\b|volatility contract\w*|contraction`, s)) return [rule("atr_contract", { x: 0.8 }), "Volatility has contracted: 14-day ATR is at most 0.8 times the 50-day ATR", "\"Contraction\" needs a number, so I used ATR 14 ÷ ATR 50 at most 0.8."];
  m = search(String.raw`(?:pull\w*\s*back|pullback|correct\w*|dip\w*|down|off)\D*?between ` + NUM + " to " + NUM + String.raw`\s*%\D*?(?:(\d+) day)?`, s);
  if (m) { const n = m[3] ? parseInt(m[3], 10) : 20; return [rule("pullback_depth", { n, lo: parseFloat(m[1]), hi: parseFloat(m[2]) }), `Close is ${_n(m[1])}% to ${_n(m[2])}% below the ${n}-day high`, m[3] ? null : "No period given, so I measured from the 20-day high."]; }
  // volume
  if (has(String.raw`\bvol(?:ume)?\b`, s)) {
    m = search(NUM + String.raw` x\D*?(\d+) (?:day|sma)`, s) || search(String.raw`(\d+) (?:day|sma)\D*?` + NUM + " x", s);
    if (m) { const a = m[1], b = m[2], [x, n] = has(NUM + String.raw` x\D*?(\d+) (?:day|sma)`, s) ? [parseFloat(a), parseInt(b, 10)] : [parseFloat(b), parseInt(a, 10)]; return [rule("x_vol_mult_n", { x, n }), `Volume is at least ${_n(x)} times its ${n}-day average`, null]; }
    m = search(NUM + " x", s); if (m) return [rule("vol_mult", { x: parseFloat(m[1]) }), `Volume is at least ${_n(m[1])} times its 50-day average`, null];
    m = search(NUM + String.raw`\s*%\s*(?:above|over|more than|higher than)`, s);
    if (m && has("avg|average", s)) { const x = 1 + parseFloat(m[1]) / 100; return [rule("vol_mult", { x }), `Volume is at least ${f2(x)} times its 50-day average`, null]; }
    if (has(String.raw`dry|dries|dried|low|light|thin|contract\w*|shrink\w*|declin\w*|falling`, s)) return [rule("vol_dry", { x: 0.8, days: 3 }), "Volume was at most 0.8 times its 50-day average on each of the 3 sessions before today", "\"Volume dry-up\" needs numbers, so I used 0.8 times average for 3 sessions."];
    if (has(String.raw`(?:above|over|more than|higher than|greater than|>)\s*(?:the\s*)?previous day`, s)) return [rule("x_vol_gt_prev"), "Volume is higher than the previous day's", null];
    m = search(String.raw`(?:above|over|more than|higher than|greater than|>)\s*(?:the|its)?\s*(?:(\d+) day\s*)?(?:avg|average|sma)`, s) || search(String.raw`(?:above|over)\s*(?:the|its)?\s*(\d+) sma`, s);
    if (m) { const n = m[1] ? parseInt(m[1], 10) : 50; return [rule("x_vol_mult_n", { x: 1.0, n }), `Volume is above its ${n}-day average`, null]; }
    if (has(String.raw`spike|surge|burst|explos\w*|huge|heavy`, s)) return [rule("vol_mult", { x: 2.0 }), "Volume is at least 2 times its 50-day average", "\"Volume spike\" needs a number, so I used 2 times the 50-day average."];
    if (has(String.raw`high|strong|good|big|above|expansion|increase\w*|rising|pick\w* up`, s)) return [rule("vol_mult", { x: 1.5 }), "Volume is at least 1.5 times its 50-day average", "\"High volume\" needs a number, so I used 1.5 times the 50-day average."];
    return null;
  }
  m = search(String.raw`turnover\D*?` + NUM + String.raw`\s*(?:cr|crore)`, s);
  if (m) return [rule("turnover_min", { x: parseFloat(m[1]) }), `Trades at least ₹${_n(m[1])} crore a day (20-day average)`, null];
  // MACD (12, 26, 9)
  if (has(String.raw`\bmacd\b`, s)) {
    if (has(String.raw`cross\w*|crossover|buy|bullish|turns? (?:up|positive)`, s) && !has(String.raw`zero|\b0\b`, s)) return [rule("x_macd", { how: "cross" }), "MACD (12, 26, 9) crosses above its signal line today", null];
    if (has(String.raw`zero|\b0\b|positive`, s)) return [rule("x_macd", { how: "zero" }), "MACD (12, 26, 9) is above zero", null];
    if (has("above|over|>", s)) return [rule("x_macd", { how: "above" }), "MACD (12, 26, 9) is above its signal line", null];
    return null;
  }
  // RSI
  if (has(String.raw`\brsi\b`, s)) {
    let n = 14, rest = s; const ml = search(String.raw`rsi\s*\(?\s*(\d+)\s*\)?\s*(?:is|has|day|>|<|above|below|over|under|cross|between|greater|less|more)`, s);
    if (ml) { const from = s.slice(ml.index); if (parseInt(ml[1], 10) <= 30 && has(String.raw`rsi\s*\(?\s*\d+\s*\)?.*\d`, from.replace(ml[1], ""))) { n = parseInt(ml[1], 10); rest = s.slice(0, ml.index) + "rsi " + from.slice(from.indexOf(ml[1]) + ml[1].length); } }
    m = search("between " + NUM + " to " + NUM, rest); if (m) return [rule("x_rsi_between", { n, lo: parseFloat(m[1]), hi: parseFloat(m[2]) }), `RSI ${n} is between ${_n(m[1])} and ${_n(m[2])}`, null];
    m = search(String.raw`cross\w*\s*(?:above|over)?\s*` + NUM, rest); if (m) return [rule("x_rsi_cross_up", { n, x: parseFloat(m[1]) }), `RSI ${n} crosses above ${_n(m[1])} today`, null];
    m = search(CMP_UP + String.raw`\s*` + NUM, rest); if (m) return [rule("rsi_above", { n, x: parseFloat(m[1]) }), `RSI ${n} is above ${_n(m[1])}`, null];
    m = search(CMP_DN + String.raw`\s*` + NUM, rest); if (m) return [rule("rsi_below", { n, x: parseFloat(m[1]) }), `RSI ${n} is below ${_n(m[1])}`, null];
    if (has("oversold", rest)) return [rule("rsi_below", { n, x: 30.0 }), `RSI ${n} is below 30`, "\"Oversold\" is usually RSI below 30, so I used that."];
    return null;
  }
  // candles
  m = search(String.raw`(\d+)\s*(?:day\s*)?(?:consecutive|straight|in a row)?\s*(red|down|lower|green|up|higher)\s*(?:day|candle|close)?s?\s*(?:in a row|consecutive|straight|running)?`, s);
  if (m && has("in a row|consecutive|straight|running", s)) { const d = ["red", "down", "lower"].includes(m[2]) ? "down" : "up"; return [rule("x_streak", { dir: d, n: parseInt(m[1], 10) }), `Closed ${d === "down" ? "lower" : "higher"} than the day before on each of the last ${m[1]} sessions`, null]; }
  if (has(String.raw`inside (?:day|bar|candle)`, s)) return [rule("inside_day"), "Inside day: today's range sits within the previous day's range", null];
  if (has(String.raw`\bnr ?7\b|narrowest range`, s)) return [rule("nr7"), "Today's range is the narrowest of the last 7 sessions (NR7)", null];
  m = search(String.raw`gap\w*\s*up(?:\D*?` + NUM + String.raw`\s*%)?`, s);
  if (m) { const x = m[1] ? parseFloat(m[1]) : 1.0; return [rule("gap_up", { pct: x }), `Opens at least ${_n(x)}% above the previous close`, m[1] ? null : "No size given for the gap, so I used 1%."]; }
  m = search(String.raw`clos\w*\s*(?:in|within|at)?\s*(?:the\s*)?(?:top|upper)\s*` + NUM + String.raw`\s*%`, s);
  if (m) return [rule("close_top", { pct: parseFloat(m[1]) }), `Close is in the top ${_n(m[1])}% of the day's range`, null];
  if (has(String.raw`clos\w*\s*(?:near|at|close to)\s*(?:the|its|day'?s?)?\s*(?:day\s*)?high|strong close`, s)) return [rule("close_top", { pct: 25.0 }), "Close is in the top 25% of the day's range", "\"Closes near the high\" needs a number, so I used the top 25% of the range."];
  m = search("between " + NUM + " to " + NUM + String.raw`\s*%`, s);
  if (m && has("today|day|change|move|up|gain|rise", s)) return [rule("day_change", { lo: parseFloat(m[1]), hi: parseFloat(m[2]) }), `Today's change is between ${_n(m[1])}% and ${_n(m[2])}%`, null];
  m = search(String.raw`(?:up|gain\w*|rise\w*|rose|rall\w*|return\w*|moved?|advance\w*)\D*?` + NUM + String.raw`\s*%\D*?(\d+) day`, s);
  if (m) return [rule("ret_n", { n: parseInt(m[2], 10), pct: parseFloat(m[1]) }), `Up at least ${_n(m[1])}% over the last ${m[2]} sessions`, null];
  m = search(String.raw`(\d+) day\s*(?:return|gain|change|performance)\D*?` + NUM + String.raw`\s*%`, s);
  if (m) return [rule("ret_n", { n: parseInt(m[1], 10), pct: parseFloat(m[2]) }), `Up at least ${_n(m[2])}% over the last ${m[1]} sessions`, null];
  m = search(String.raw`(?:up|gain\w*|rise\w*|rose|move\w*|change\w*|closes? up)\D*?` + NUM + String.raw`\s*%`, s);
  if (m) return [rule("day_change", { lo: parseFloat(m[1]), hi: 100.0 }), `Today's close is at least ${_n(m[1])}% above the previous close`, null];
  if (has(String.raw`green|bullish candle|up day|positive (?:day|close|candle)|close\w* above (?:the\s*)?open`, s)) return [rule("green"), "Green candle: close above the open", null];
  if (has(String.raw`red|bearish candle|down day|negative (?:day|close|candle)|close\w* below (?:the\s*)?open`, s)) return [rule("x_red"), "Red candle: close below the open", null];
  // price
  m = search(String.raw`(?:price|close|cmp|ltp)?\s*` + CMP_UP + String.raw`\s*(?:rs\.?\s*)?` + NUM + String.raw`\s*(?:rs|rupees)?\s*$`, s);
  if (m && has("price|close|cmp|ltp|rs|rupee", s)) return [rule("price_min", { x: parseFloat(m[1]) }), `Price is at least ₹${_n(m[1])}`, null];
  m = search(String.raw`(?:price|close|cmp|ltp)?\s*` + CMP_DN + String.raw`\s*(?:rs\.?\s*)?` + NUM + String.raw`\s*(?:rs|rupees)?\s*$`, s);
  if (m && has("price|close|cmp|ltp|rs|rupee", s)) return [rule("x_price_max", { x: parseFloat(m[1]) }), `Price is at most ₹${_n(m[1])}`, null];
  if (has(String.raw`\b(?:uptrend|up trend|trending up)\b`, s)) return [rule("x_ma_above_ma", { k1: "EMA", n1: 50, k2: "SMA", n2: 200 }), "50-day EMA is above the 200-day average", "\"Uptrend\" has no exact meaning, so I used 50-day EMA above the 200-day average. Write your own definition to replace it."];
  return null;
}

function exit(s, ex, book) {                                 // updates ex / book; -> [meaning, note] or ["miss", why] or null
  let m;
  if (has(String.raw`\b(?:buy|entry|enter)\b`, s) && !has(String.raw`\b(?:stop loss|sl|target|exit|sell)\b`, s)) {          // how to enter
    if (has(String.raw`(?:above|over|break\w*|cross\w*)\s*(?:of\s*)?(?:the\s*)?(?:signal|breakout|setup|trigger|previous day|that|entry)?\s*(?:day|candle|bar)?(?:'s)?\s*high`, s) || has("buy stop", s)) { ex.entry = ENTRIES[1]; return ["Buy the next day only if price trades above the signal day's high", null]; }
    if (has(String.raw`next\s*(?:day|session|morning)?(?:'s)?\s*open|at (?:the )?open|market open`, s)) { ex.entry = ENTRIES[0]; return ["Buy at the next day's open", null]; }
  }
  m = search(String.raw`(?:skip|avoid|ignore|no trade|don'?t (?:buy|trade))\D*gap\D*?` + NUM + String.raw`\s*%`, s);
  if (m) { ex.gap_max = parseFloat(m[1]); return [`Skip the trade if it opens more than ${_n(m[1])}% above the signal day's close`, null]; }
  m = search(String.raw`risk\s*(?:per trade)?\s*(?:of|is|=|:)?\s*` + NUM + String.raw`\s*%(?!\s*(?:above|below))`, s);
  if (m && !has("reward|stop", s)) { book.risk_pct = parseFloat(m[1]); return [`Risk ${_n(m[1])}% of capital on each trade`, null]; }
  m = search(String.raw`(?:breakeven|break even|stop to (?:entry|cost|buy price))\D*?` + NUM + " r", s) || search(NUM + String.raw` r\D*?(?:breakeven|break even|stop to (?:entry|cost))`, s);
  if (m) { ex.breakeven_r = parseFloat(m[1]); return [`Move the stop to the entry price once the trade is up ${_n(m[1])}R`, null]; }
  m = search(String.raw`(?:exit|sell|trail\w*|square off|close (?:the )?(?:trade|position)|get out|book)\D*?` + MA, s);
  if (m && !has(String.raw`\babove\b`, s)) { const [k, n] = maOf(m); ex.trail_kind = k; ex.trail_n = n; return [`Exit at the next open after a close below the ${maTxt(k, n)}`, null]; }
  m = search(String.raw`stop\w*(?: loss)?\D*?(?:below|under|at)\s*(?:the\s*)?` + MA, s);
  if (m) { const [k, n] = maOf(m); ex.trail_kind = k; ex.trail_n = n; return [`Exit at the next open after a close below the ${maTxt(k, n)}`, "A stop \"below an average\" moves every day, so it is tested as an exit on a close below it. The first stop is still needed: see below."]; }
  if (has(String.raw`\bstop loss\b|\bstop\b|\bsl\b`, s) && !has("time stop", s)) {                                       // stop loss
    m = search(NUM + String.raw`\s*(?:x\s*)?atr|atr\D*?` + NUM, s);
    if (m || has(String.raw`\batr\b`, s)) { const x = m ? parseFloat(m[1] || m[2]) : 1.5; ex.stop = STOPS[2]; ex.stop_atr = x; return [`Stop loss ${_n(x)} × ATR(14) below the entry price`, m ? null : "No ATR multiple given, so I used 1.5."]; }
    m = search(NUM + String.raw`\s*%`, s); if (m) { ex.stop = STOPS[3]; ex.stop_pct = parseFloat(m[1]); return [`Stop loss ${_n(m[1])}% below the entry price`, null]; }
    m = search(String.raw`(?:low(?:est low)? of (?:the )?(?:last|past|previous|prior)?\s*(\d+) day|(\d+) day low)`, s);
    if (m) { const n = parseInt(m[1] || m[2], 10); ex.stop = STOPS[1]; ex.stop_n = n; return [`Stop loss at the lowest low of the last ${n} sessions (signal day included)`, null]; }
    if (has("swing low|recent low|pivot low", s)) { ex.stop = STOPS[1]; ex.stop_n = 10; return ["Stop loss at the lowest low of the last 10 sessions", "\"Swing low\" needs a look-back, so I used the lowest low of 10 sessions."]; }
    if (has("low", s)) { ex.stop = STOPS[0]; return ["Stop loss at the low of the signal day", null]; }
    return ["miss", "I could not read the stop. Write it as \"stop loss 5%\", \"stop at signal candle low\", \"stop at 10 day low\" or \"stop 2 ATR\"."];
  }
  if (has(String.raw`\btarget\b|\brisk reward\b|\brr\b`, s)) {                                                             // target
    m = search(NUM + String.raw` r\b`, s) || search(String.raw`(?:risk reward|rr)\D*?` + NUM, s);
    if (m) { ex.target_r = parseFloat(m[1]); return [`Take profit at ${_n(m[1])}R (that many times the risk)`, null]; }
    m = search(NUM + String.raw`\s*%`, s); if (m) { ex._target_pct = parseFloat(m[1]); return [`Take profit ${_n(m[1])}% above the entry price`, null]; }
    return ["miss", "I could not read the target. Write it as \"target 2R\", \"1:3\" or \"target 10%\"."];
  }
  m = search(String.raw`(?:exit|sell|close|square off|hold|holding|time stop|max\w*)\D*?(\d+) day`, s);                    // time
  if (m) { ex.max_days = parseInt(m[1], 10); return [`Exit after ${m[1]} sessions if nothing else has closed the trade`, null]; }
  if (has(String.raw`\b(?:short|sell short|shorting)\b`, s)) return ["miss", "Only long (buy) trades are tested."];
  return null;
}

export const EXAMPLES = {
  Breakout: "Close above 50 EMA and 200 SMA\nBreakout of 20 day high with volume 1.5x average\nClose in top 30% of the day's range\nStop loss at signal candle low\nExit when close below 21 EMA",
  Pullback: "21 EMA above 50 EMA, close above 200 SMA\nPullback to 21 EMA\nGreen candle, close above previous day high\nStop loss at 5 day low\nTarget 3R, exit after 30 days",
  "52-week high": "New 52 week high\nVolume 1.5x average\nOutperforming Nifty over 3 months\nStop loss 7%\nExit when close below 21 EMA",
  "Average crossover": "21 EMA crosses above 50 EMA\nClose above 200 SMA\nStop loss 2 ATR\nExit when close below 50 EMA",
  "RSI dip": "Close above 200 SMA\nRSI below 35\n3 down days in a row\nStop loss 6%\nTarget 2R, exit after 15 days",
};
export const VOCAB = [
  ["Trend", ["close above 50 EMA", "21 EMA above 50 EMA", "50 EMA rising", "close crosses above 20 SMA", "21 EMA crosses above 50 EMA", "golden cross", "within 10% of 52 week high", "30% above 52 week low", "not more than 8% above 21 EMA"]],
  ["Breakout", ["breakout of 20 day high", "new 52 week high", "close above previous day high", "gap up 2%", "tight range 15 days within 12%"]],
  ["Pullback", ["pullback to 21 EMA", "pullback between 3 and 12% from 20 day high", "3 down days in a row", "RSI below 35"]],
  ["Momentum", ["up 20% in 3 months", "outperforming Nifty over 3 months", "RSI above 60", "RSI crosses above 50", "MACD bullish crossover", "MACD above zero", "up 3% today"]],
  ["Volume", ["volume 1.5x average", "volume 2x 20 day average", "volume above previous day", "volume dry up", "turnover above 5 crore"]],
  ["Candle", ["green candle", "close in top 25% of range", "inside day", "NR7"]],
  ["Market", ["Nifty above 50 EMA"]],
  ["Buying", ["buy at next day open", "buy above signal candle high", "skip if gap up more than 4%", "risk 1% per trade"]],
  ["Stop loss", ["stop loss 5%", "stop loss at signal candle low", "stop loss at 10 day low", "stop loss 2 ATR", "move stop to breakeven at 1R"]],
  ["Exit", ["exit when close below 21 EMA", "target 2R", "target 10%", "exit after 20 days"]],
];
const sameRule = (a, b) => { const ka = Object.keys(a).sort(), kb = Object.keys(b).sort(); return ka.length === kb.length && ka.every((k, i) => k === kb[i] && a[k] === b[k]); };
const pyRound3 = (x) => Number(fx(x, 3, false));
export function parse(text) {
  const ex = { ...EXIT_DEFAULT, entry: ENTRIES[0], stop: null, trail_n: 0, target_r: 0.0, breakeven_r: 0.0, max_days: null, gap_max: 100.0, max_risk: 100.0 };
  const book = {}, rules = [], read = [], missed = [], assumed = [], norm = normalise(text); let exitLine = null, last = null;
  for (const [ln, c] of clauses(norm)) {
    const isExit = EXIT_WORDS.test(c) || (has(String.raw`\b(?:buy|entry|enter)\b`, c) && has("open|high|buy stop", c) && !has(MA, c));
    if (has(String.raw`\b(?:buy|entry|enter|long)\b\s*(?:when|if|:)`, c)) exitLine = null;
    const inExit = isExit || exitLine === ln;
    if (has(String.raw`\b(?:short|shorting|sell short)\b`, c)) { missed.push([c, "Only long (buy) trades are tested."]); continue; }
    let got = null;
    if (inExit) { got = exit(c, ex, book);
      if (got !== null) { exitLine = has(String.raw`\b(exit|sell|square off)\b`, c) ? ln : exitLine; if (got[0] === "miss") missed.push([c, got[1]]); else read.push([c, got[0], got[1], "exit"]); last = null; continue; } }
    got = entry(c);
    if (got === null && last !== null) {                      // "…above 50 EMA and 200 SMA": the second half borrows the verb
      const m = R(String.raw`^(?:(?:the\s*)?` + MA + ")$").exec(pyStrip(c));
      if (m && ["close_above_ma", "x_close_below_ma", "x_cross_above_ma", "touch_ma"].includes(last.type)) { const [k, n] = maOf(m), r2 = { ...last, kind: k, n };
        const verb = { close_above_ma: "Close is above the", x_close_below_ma: "Close is below the", x_cross_above_ma: "Close crosses above the", touch_ma: "The low came within 1.5% of the" }[r2.type];
        got = [r2, `${verb} ${maTxt(k, n)}`, null]; }
    }
    if (got === null) {
      if (inExit) missed.push([c, "I could not read this exit rule."]);
      else if (c.replace(/[^a-z0-9]/g, "").length >= 3 && !R(String.raw`^(?:(?:buy|entry|enter|long|setup|rules?|my setup|when|if|exit|sell)\W*)$`).test(c)) missed.push([c, "I do not know this phrase yet. See \"Words I understand\"."]);
      continue;
    }
    if (got[0] === "miss") { missed.push([c, got[1]]); continue; }
    const [r, meaning, note] = got;
    if (rules.some((x) => sameRule(x, r))) continue;
    rules.push(r); read.push([c, meaning, note, "entry"]); last = r;
  }
  // defaults, stated openly
  if (ex.stop === null) { ex.stop = STOPS[0]; assumed.push("No stop loss written, so the stop is the low of the signal day. Write e.g. \"stop loss 5%\" to change it."); }
  const tp = ex._target_pct; delete ex._target_pct;
  if (tp) {
    if (ex.stop === STOPS[3] && ex.stop_pct) { ex.target_r = pyRound3(tp / ex.stop_pct); ex.target_pct = tp; }
    else { missed.push([`target ${_n(tp)} %`, "A target in % can only be tested together with a stop in % (e.g. \"stop loss 5%\"). Use \"target 2R\" with other stops."]);
      const keep = read.filter((x) => !x[1].startsWith("Take profit") || x[1].includes("R (")); read.length = 0; read.push(...keep); }
  }
  if (ex.max_days === null) {
    if (ex.trail_n || ex.target_r) ex.max_days = NO_TIME_STOP;
    else { ex.max_days = ASSUMED_HOLD; assumed.push(`No exit written, so a trade is closed after ${ASSUMED_HOLD} sessions unless the stop is hit first. Write e.g. "exit when close below 21 EMA" or "target 2R".`); }
  }
  return { rules, ex, book, read, missed, assumed };
}
export function exitSummary(ex) {                            // plain sentences: how a trade is taken and closed
  const out = [ex.entry === ENTRIES[0] ? "Buy at the next day's open" : "Buy the next day only if price trades above the signal day's high"];
  out.push({ [STOPS[0]]: "Stop loss at the low of the signal day", [STOPS[1]]: `Stop loss at the lowest low of the last ${ex.stop_n} sessions`, [STOPS[2]]: `Stop loss ${_n(ex.stop_atr)} × ATR(14) below entry`, [STOPS[3]]: `Stop loss ${_n(ex.stop_pct)}% below entry` }[ex.stop]);
  if (ex.breakeven_r) out.push(`Stop moves to entry at +${_n(ex.breakeven_r)}R`);
  if (ex.target_r) out.push(ex.target_pct ? `Take profit ${_n(ex.target_pct)}% above entry (${_n(ex.target_r)}R)` : `Take profit at ${_n(ex.target_r)}R`);
  if (ex.trail_n) out.push(`Exit after a close below the ${maTxt(ex.trail_kind, ex.trail_n)}`);
  if (ex.max_days && ex.max_days < NO_TIME_STOP) out.push(`Exit after ${ex.max_days} sessions`);
  if ((ex.gap_max ?? 100) < 100) out.push(`Skip if it opens more than ${_n(ex.gap_max)}% above the signal close`);
  return out;
}

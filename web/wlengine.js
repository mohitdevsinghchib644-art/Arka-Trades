// Arka Trades · Watchlist maths: a port of watchlist_page.py (compute, price_events, add_reference_events, filters, sorting, words).
// Pure functions, no network and no page: tested against the Python file with the same candles.
import { fx, rsiWilder } from "./rsengine.js";

export const TX2 = "#A3A3A3", AMBER = "#FF9F0A", GREEN = "#30D158", RED = "#FF453A", CYAN = "#5AC8FA", PURPLE = "#BF5AF2", YELLOW = "#FFD60A", BLUE = "#4FA3FF";
export const DEFAULT_LIST = "Main", DESK = "Arka desk";
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const dayMon = (d) => `${d.slice(8, 10)} ${MON[+d.slice(5, 7) - 1]}`;                        // "%d %b"
const dayNo = (d) => Math.round(Date.parse(d + "T00:00:00Z") / 864e5);
const ok = (v) => v != null && isFinite(v);
const mean = (a) => a.reduce((x, y) => x + y, 0) / a.length;
const max = (a) => a.reduce((x, y) => (y > x ? y : x), -Infinity), min = (a) => a.reduce((x, y) => (y < x ? y : x), Infinity);

// ── the clock (IST): now = { day: "YYYY-MM-DD", mins: minutes since midnight, wd: 0 Monday … 6 Sunday } ──
export function clock(ms = Date.now()) {
  const t = new Date(ms + 330 * 60000);
  return { day: t.toISOString().slice(0, 10), mins: t.getUTCHours() * 60 + t.getUTCMinutes(), wd: (t.getUTCDay() + 6) % 7, ms };
}
export const marketOpen = (now) => now.wd < 5 && now.mins >= 555 && now.mins < 930;
export function liveFraction(lastDate, now) {                // share of today's session already traded (0.1–1), else null
  if (!marketOpen(now) || lastDate !== now.day) return null;
  return Math.max(0.1, Math.min(1, (now.mins - 555) / 375));
}
const ret = (c, n) => (c.length > n && c[c.length - 1 - n] > 0 ? (c[c.length - 1] / c[c.length - 1 - n] - 1) * 100 : null);
function ewmLast(a, alpha) { let v = a[0]; for (let i = 1; i < a.length; i++) v = alpha * a[i] + (1 - alpha) * v; return v; }   // pandas ewm(adjust=False).mean().iloc[-1]

// History up to the day before the live candle, then the live candle (it wins on the same day): watchlist_page.merge_live for one day.
export function mergeLive(F, q, now) {
  if (!q || !q.ok || !(q.price > 0)) return F;
  const day = q.t ? clock(q.t * 1000).day : now.day;
  const live = { o: q.open ?? q.price, h: Math.max(q.high ?? q.price, q.price), l: Math.min(q.low ?? q.price, q.price), c: q.price, v: q.vol ?? 0 };
  if (!F || !F.d || !F.d.length) return { d: [day], o: [live.o], h: [live.h], l: [live.l], c: [live.c], v: [live.v] };
  if (day < F.d[F.d.length - 1]) return F;                  // a quote older than the history: the history already has that day
  const keep = F.d.findIndex((x) => x >= day), n = keep < 0 ? F.d.length : keep, cut = (k) => F[k].slice(0, n);
  const same = keep >= 0 && F.d[keep] === day;              // the same day in the history: keep its open, widen the range, take the fresh last price and volume
  if (same) { live.o = F.o[keep] ?? live.o; live.h = Math.max(live.h, q.high == null ? F.h[keep] : live.h); live.l = Math.min(live.l, q.low == null ? F.l[keep] : live.l); live.v = Math.max(live.v, q.vol == null ? F.v[keep] : live.v); }
  return { d: [...cut("d"), day], o: [...cut("o"), live.o], h: [...cut("h"), live.h], l: [...cut("l"), live.l], c: [...cut("c"), live.c], v: [...cut("v"), live.v] };
}

// ── every number and event for one stock from its daily candles (watchlist_page.compute) ──
export function compute(sym, F, nifty = null, now = clock()) {
  const row = { symbol: sym, has_data: false, events: [] };
  if (!F || !F.c || F.c.length < 2) return row;
  const idx = F.c.map((x, i) => i).filter((i) => F.c[i] > 0);                                  // _norm: rows with a close; missing open, high, low take the close
  const c = idx.map((i) => F.c[i]), o = idx.map((i) => F.o[i] ?? F.c[i]), h = idx.map((i) => F.h[i] ?? F.c[i]), l = idx.map((i) => F.l[i] ?? F.c[i]), v = idx.map((i) => F.v[i] || 0), d = idx.map((i) => F.d[i]);
  const n = c.length; if (n < 2) return row;
  const last = c[n - 1], prev = c[n - 2], frac = liveFraction(d[n - 1], now);
  const avg20 = n >= 6 ? mean(v.slice(Math.max(0, n - 21), n - 1)) : null;
  const volProj = frac ? v[n - 1] / frac : v[n - 1];
  const hi52 = max(h.slice(Math.max(0, n - 252))), lo52 = min(l.slice(Math.max(0, n - 252)));
  const priorHi = n > 20 ? max(h.slice(Math.max(0, n - 253), n - 1)) : null, priorLo = n > 20 ? min(l.slice(Math.max(0, n - 253), n - 1)) : null;
  let rsi = null; if (n > 15) { const r = rsiWilder(c); rsi = ok(r[n - 1]) ? r[n - 1] : null; }
  const ema20 = n >= 20 ? ewmLast(c, 2 / 21) : null;
  const sma50 = n >= 50 ? mean(c.slice(-50)) : null, sma200 = n >= 200 ? mean(c.slice(-200)) : null, sma200Prev = n >= 201 ? mean(c.slice(-201, -1)) : null;
  const tr = []; for (let i = 1; i < n; i++) tr.push(Math.max(h[i] - l[i], Math.abs(h[i] - c[i - 1]), Math.abs(l[i] - c[i - 1])));
  const atr = tr.length >= 14 ? ewmLast(tr, 1 / 14) : null;
  const yr = d[n - 1].slice(0, 4); let yb = -1; for (let i = n - 1; i >= 0; i--) if (d[i].slice(0, 4) < yr) { yb = i; break; }
  const ytd = yb >= 0 ? (last / c[yb] - 1) * 100 : null;
  const r3 = ret(c, 63);
  let n3 = null; if (nifty && nifty.c && nifty.c.length > 64) n3 = ret(nifty.c.filter((x) => x > 0), 63);
  Object.assign(row, { has_data: true, last, prev, chg: last - prev, pct: prev ? (last / prev - 1) * 100 : null,
    open: o[n - 1], high: h[n - 1], low: l[n - 1], volume: v[n - 1], avg20, live: !!frac,
    vol_x: avg20 ? volProj / avg20 : null, turnover_cr: (last * v[n - 1]) / 1e7,
    pdh: h[n - 2], pdl: l[n - 2], hi52, lo52, pos52: hi52 > lo52 ? ((last - lo52) / (hi52 - lo52)) * 100 : null,
    off_hi52: hi52 ? (last / hi52 - 1) * 100 : null, day_pos: h[n - 1] > l[n - 1] ? ((last - l[n - 1]) / (h[n - 1] - l[n - 1])) * 100 : null,
    r1w: ret(c, 5), r1m: ret(c, 21), r3m: r3, r6m: ret(c, 126), r1y: ret(c, 252), ytd,
    vs_nifty_3m: r3 != null && n3 != null ? r3 - n3 : null,
    rsi, vs_ema20: ema20 ? (last / ema20 - 1) * 100 : null, vs_sma50: sma50 ? (last / sma50 - 1) * 100 : null,
    vs_sma200: sma200 ? (last / sma200 - 1) * 100 : null, atr_pct: atr ? (atr / last) * 100 : null,
    spark: c.slice(-63), date: d[n - 1] });
  row.events = priceEvents(row, o, h, l, c, sma200Prev, priorHi, priorLo);
  return row;
}
// [label, colour, explanation, sort weight]: everything is read from today's (last) candle (watchlist_page.price_events)
export function priceEvents(r, o, h, l, c, sma200Prev, priorHi, priorLo) {
  const ev = [], n = c.length, O = o[n - 1], H = h[n - 1], L = l[n - 1], C = c[n - 1], tol = Math.max(0.01, 0.0002 * O), rng = H - L;
  if (rng > 0 && Math.abs(O - H) <= tol) ev.push(["Open=High", RED, "Opened at the day's high: sellers in control since the open", 8]);
  else if (rng > 0 && Math.abs(O - L) <= tol) ev.push(["Open=Low", GREEN, "Opened at the day's low: buyers in control since the open", 8]);
  const gap = (O / c[n - 2] - 1) * 100;
  if (Math.abs(gap) >= 1) ev.push([`Gap ${gap > 0 ? "up" : "down"} ${fx(Math.abs(gap), 1)}%`, gap > 0 ? GREEN : RED, `Opened ${fx(gap, 2, false, true)}% from the previous close`, 7]);
  if (priorHi != null && H > priorHi) ev.push(["52W high", GREEN, `New 52-week high (previous ${fx(priorHi, 2, true)})`, 9]);
  else if (priorLo != null && L < priorLo) ev.push(["52W low", RED, `New 52-week low (previous ${fx(priorLo, 2, true)})`, 9]);
  if (C > h[n - 2]) ev.push(["PDH break", GREEN, `Trading above yesterday's high ${fx(h[n - 2], 2, true)}`, 5]);
  else if (C < l[n - 2]) ev.push(["PDL break", RED, `Trading below yesterday's low ${fx(l[n - 2], 2, true)}`, 5]);
  if (r.vol_x != null && r.vol_x >= 2) ev.push([`Vol ${fx(r.vol_x, 1)}×`, YELLOW, "Volume vs the 20-day average" + (r.live ? " (projected to a full day)" : ""), 7]);
  if (n >= 7 && rng > 0) { let mn = Infinity; for (let i = n - 7; i < n; i++) mn = Math.min(mn, h[i] - l[i]); if (rng <= mn + 1e-9) ev.push(["NR7", CYAN, "Narrowest range of the last 7 sessions: a move often follows", 3]); }
  if (n >= 2 && H < h[n - 2] && L > l[n - 2]) ev.push(["Inside", CYAN, "Inside bar: today's range is inside yesterday's", 2]);
  const s200 = r.vs_sma200;
  if (s200 != null && sma200Prev) {
    if (c[n - 2] < sma200Prev && s200 > 0) ev.push(["↑200 DMA", GREEN, "Crossed above the 200-day average today", 6]);
    else if (c[n - 2] > sma200Prev && s200 < 0) ev.push(["↓200 DMA", RED, "Crossed below the 200-day average today", 6]);
  }
  if (r.rsi != null && r.rsi >= 70) ev.push([`RSI ${fx(r.rsi, 0)}`, AMBER, "RSI 14 at or above 70 (overbought)", 3]);
  else if (r.rsi != null && r.rsi <= 30) ev.push([`RSI ${fx(r.rsi, 0)}`, PURPLE, "RSI 14 at or below 30 (oversold)", 3]);
  return ev;
}
// results and ex-dividend dates, today's news and filings, and the user's price alerts (watchlist_page.add_reference_events)
export function addReferenceEvents(r, cal, stories, meta, today) {
  const ev = r.events || (r.events = []); cal = cal || {};
  const rd = cal.results, xd = cal.exdiv, full = (d) => `${dayMon(d)} ${d.slice(0, 4)}`;
  if (rd) { const days = dayNo(rd) - dayNo(today); if (days >= 0 && days <= 14) ev.push([days === 0 ? "Results today" : `Results ${dayMon(rd)}`, PURPLE, `Expected results date ${full(rd)} (Yahoo calendar)`, days <= 2 ? 10 : 6]); }
  if (xd) { const days = dayNo(xd) - dayNo(today); if (days >= 0 && days <= 14) ev.push([`Ex-div ${dayMon(xd)}`, BLUE, `Ex-dividend date ${full(xd)}: buy before this date to receive the dividend`, 4]); }
  stories = stories || [];
  if (stories.length) {
    const filings = stories.filter((a) => a.kind === "filing"), news = stories.filter((a) => a.kind !== "filing");
    if (stories.some((a) => a.event === "EARNINGS")) ev.push(["Results out", PURPLE, "Results / earnings story or filing in the last 24 hours: " + String(stories[0].title).slice(0, 140), 10]);
    if (news.length) { const tone = news.reduce((s, a) => s + Math.trunc(a.score || 0), 0);
      ev.push([`News ${news.length}`, tone > 0 ? GREEN : tone < 0 ? RED : BLUE, news.slice(0, 3).map((a) => String(a.title).slice(0, 110)).join(" | "), 6]); }
    if (filings.length) ev.push([`Filing${filings.length > 1 ? "s" : ""} ${filings.length}`, CYAN, filings.slice(0, 3).map((a) => String(a.title).slice(0, 110)).join(" | "), 5]);
    r.stories = stories;
  }
  meta = meta || {};
  const last = r.last, pos = (x) => (x == null || x === "" || !isFinite(+x) ? null : +x), above = pos(meta.above), below = pos(meta.below);
  r.alert_hit = false;
  if (last != null && above != null && last >= above) { ev.push([`Alert ▲ ${fx(above, 2, true)}`, AMBER, `Your alert: price at or above ${fx(above, 2, true)}`, 11]); r.alert_hit = true; }
  if (last != null && below != null && last <= below) { ev.push([`Alert ▼ ${fx(below, 2, true)}`, AMBER, `Your alert: price at or below ${fx(below, 2, true)}`, 11]); r.alert_hit = true; }
  const w = ev.map((e, i) => [e, i]).sort((a, b) => b[0][3] - a[0][3] || a[1] - b[1]).map((x) => x[0]);       // stable, heaviest first
  ev.length = 0; ev.push(...w);
}
export function buildRows(symbols, frames, names, deliv, cals, news, meta, nifty, now) {       // watchlist_page.build_rows
  return symbols.map((s) => { const r = compute(s, frames[s], nifty, now);
    addReferenceEvents(r, cals[s], news[s], meta[s], now.day);
    r.name = names[s] || ""; r.deliv_pct = (deliv[s] || {}).pct ?? null; r.n_events = r.events.length; return r; });
}

// ── filter and sort (watchlist_page.filter_rows / sort_rows) ──
export const SORTS = { "List order": [null, false], "% Chg ↓": ["pct", true], "% Chg ↑": ["pct", false], "Volume × ↓": ["vol_x", true], "Events first": ["n_events", true], "1W ↓": ["r1w", true], "1M ↓": ["r1m", true],
  "3M ↓": ["r3m", true], "YTD ↓": ["ytd", true], "1Y ↓": ["r1y", true], "vs NIFTY 3M ↓": ["vs_nifty_3m", true], "Near 52W high": ["off_hi52", true], "RSI ↓": ["rsi", true], "RSI ↑": ["rsi", false],
  "Turnover ↓": ["turnover_cr", true], "Delivery % ↓": ["deliv_pct", true], "Symbol A–Z": ["symbol", false] };
export const FILTERS6 = [["All", "All"], ["Up today", "Gainers"], ["Down today", "Losers"], ["In the news", "News today"], ["Alert hit", "Alerts hit"], ["Volume spike", "Volume spike"], ["52-week high", "52W high"],
  ["Opened at high", "Open = high"], ["Opened at low", "Open = low"], ["Above yesterday's high", "Above PDH"], ["Below yesterday's low", "Below PDL"], ["No price data", "No data"]];
export const SORTS6 = { "Your order": "List order", "Biggest gainers": "% Chg ↓", "Biggest losers": "% Chg ↑", "Busiest (volume)": "Volume × ↓", "Most events": "Events first", "Best 1 month": "1M ↓", "Best this year": "YTD ↓",
  "Best 1 year": "1Y ↓", "Nearest 52-week high": "Near 52W high", "Highest RSI": "RSI ↓", "Lowest RSI": "RSI ↑", "Name A–Z": "Symbol A–Z" };
export const VIEWS6 = ["Today", "Returns", "Technicals", "Heatmap"];
export function filterRows(rows, flt, query = "") {
  const q = (query || "").trim().toUpperCase(), has = (r, lab) => r.events.some((e) => e[0].startsWith(lab));
  const out = rows.filter((r) => !q || r.symbol.includes(q) || (r.name || "").toUpperCase().includes(q));
  const tests = { Gainers: (r) => (r.pct || 0) > 0, Losers: (r) => (r.pct || 0) < 0, "With events": (r) => r.events.length > 0, "Open = high": (r) => has(r, "Open=High"), "Open = low": (r) => has(r, "Open=Low"),
    "52W high": (r) => has(r, "52W high"), "Volume spike": (r) => (r.vol_x || 0) >= 2, "Above PDH": (r) => has(r, "PDH break"), "Below PDL": (r) => has(r, "PDL break"),
    "News today": (r) => !!(r.stories && r.stories.length), "Alerts hit": (r) => !!r.alert_hit, "No data": (r) => !r.has_data };
  return tests[flt] ? out.filter(tests[flt]) : out;
}
const stable = (a, cmp) => a.map((x, i) => [x, i]).sort((p, q) => cmp(p[0], q[0]) || p[1] - q[1]).map((x) => x[0]);
export function sortRows(rows, how, group = false) {
  const [key, rev] = SORTS[how] || [null, false]; let out = rows.slice();
  if (key === "symbol") out = stable(out, (a, b) => (a.symbol < b.symbol ? -1 : a.symbol > b.symbol ? 1 : 0));
  else if (key) { const have = out.filter((r) => r[key] != null), miss = out.filter((r) => r[key] == null);
    out = stable(have, (a, b) => (rev ? b[key] - a[key] : a[key] - b[key])).concat(miss); }                    // missing values always last
  if (group) out = stable(out, (a, b) => (a.group === "Unclassified") - (b.group === "Unclassified") || ((a.group || "") < (b.group || "") ? -1 : (a.group || "") > (b.group || "") ? 1 : 0));
  return out;
}

// ── words (watchlist_page.trend_word, rsi_word, _nice, _KIND) ──
export function trendWord(r) {                               // ["Strong uptrend", colour, detail] from the price against its 20-day, 50-day and 200-day averages
  const a = r.vs_ema20, b = r.vs_sma50, c = r.vs_sma200;
  if (c == null && b == null) return null;
  const up = (x) => x != null && x >= 0, dn = (x) => x != null && x < 0;
  const detail = [["20-day", a], ["50-day", b], ["200-day", c]].filter(([, v]) => v != null).map(([lab, v]) => `${lab} ${v >= 0 ? "above" : "below"}`).join(" · ");
  const word = c == null ? (up(b) ? ["Uptrend", GREEN] : ["Downtrend", RED]) : up(a) && up(b) && up(c) ? ["Strong uptrend", GREEN] : up(b) && up(c) ? ["Uptrend", GREEN]
    : dn(a) && dn(b) && dn(c) ? ["Strong downtrend", RED] : dn(b) && dn(c) ? ["Downtrend", RED] : ["Sideways", AMBER];
  return [word[0], word[1], "Price vs its averages: " + detail];
}
export function rsiWord(v) {
  if (!ok(v)) return null;
  const t = fx(v, 0);
  return v >= 70 ? [`${t} · overbought`, AMBER] : v <= 30 ? [`${t} · oversold`, PURPLE] : v >= 55 ? [`${t} · strong`, GREEN] : v <= 45 ? [`${t} · weak`, "#FF8A80"] : [`${t} · neutral`, TX2];
}
const NICE = { "Open=High": "Opened at high", "Open=Low": "Opened at low", "PDH break": "Above yesterday's high", "PDL break": "Below yesterday's low", NR7: "Quiet day", Inside: "Inside day", "52W high": "52-week high",
  "52W low": "52-week low", "↑200 DMA": "Crossed above 200-day", "↓200 DMA": "Crossed below 200-day" };
export function nice(label) {                                // chip text a first-time reader understands; filters keep the raw labels
  const lab = String(label); if (NICE[lab]) return NICE[lab];
  let m = lab.match(/^News (\d+)$/); if (m) return `${m[1]} news`;
  m = lab.match(/^Filings? (\d+)$/); if (m) return `${m[1]} filing${m[1] !== "1" ? "s" : ""}`;
  m = lab.match(/^Vol ([\d.]+)×$/); if (m) return `Volume ${m[1]}×`;
  return lab;
}
export const isNewsEvent = (label) => /^(News |Filing|Results out)/.test(String(label));
export const KIND = { EARNINGS: ["Results", PURPLE], RESULTS: ["Results", PURPLE], ORDER: ["Order win", GREEN], DIVIDEND: ["Dividend", BLUE], BONUS: ["Bonus", BLUE], SPLIT: ["Split", BLUE], BUYBACK: ["Buyback", GREEN],
  RATING: ["Rating", YELLOW], UPGRADE: ["Upgrade", GREEN], DOWNGRADE: ["Downgrade", RED], MERGER: ["M&A", CYAN], ACQUISITION: ["M&A", CYAN], MGMT: ["Management", YELLOW], BLOCK: ["Block deal", CYAN], BULK: ["Bulk deal", CYAN],
  PLEDGE: ["Pledge", RED], FUNDRAISE: ["Fund raise", YELLOW], REGULATORY: ["Regulatory", RED], LEGAL: ["Legal", RED] };
export function storyTag(a) {                                // [text, colour] of the kind-of-story pill
  if (a.kind === "filing") return ["Exchange filing", CYAN];
  const ev = String(a.event || "").toUpperCase();
  if (KIND[ev]) return KIND[ev];
  if (ev && ev !== "NEWS") { const t = ev.replace(/_/g, " ").toLowerCase(); return [t.charAt(0).toUpperCase() + t.slice(1), BLUE]; }
  return ["", BLUE];
}
export function when(pubMs, nowMs = Date.now()) {            // ["14:32", "2 h ago"]; the clock shows the date too when it is not today
  const mins = Math.max(0, (nowMs - pubMs) / 60000), a = new Date(pubMs + 330 * 60000), b = clock(nowMs), day = a.toISOString().slice(0, 10), hm = a.toISOString().slice(11, 16);
  const rel = mins < 1 ? "just now" : mins < 60 ? `${Math.trunc(mins)} min ago` : mins < 1440 ? `${Math.trunc(mins / 60)} h ago` : `${Math.trunc(mins / 1440)} d ago`;
  return [day === b.day ? hm : `${dayMon(day)}, ${hm}`, rel];
}
export function fmtQty(v) {                                  // Indian compact volume: 12,345 / 5.94 L / 2.34 Cr
  if (!ok(v)) return "—";
  return v >= 1e7 ? fx(v / 1e7, 2, true) + " Cr" : v >= 1e5 ? fx(v / 1e5, 2, true) + " L" : fx(v, 0, true);
}
export const fmtPrice = (v, d = 2) => (ok(v) ? fx(v, d, true) : "—");
// 'NSE:tcs' / 'TCS.NS' / ' tcs ' → 'TCS' (user_data.clean_symbol); and the pasted text split on commas, semicolons, new lines and tabs
export function cleanSymbol(raw) { let s = String(raw || "").trim().toUpperCase(); if (s.includes(":")) s = s.split(":").pop(); for (const suf of [".NS", ".BO", ".NSE", ".BSE"]) if (s.endsWith(suf)) { s = s.slice(0, -suf.length); break; } return s.replace(/[^A-Z0-9&\-_]/g, ""); }
export const splitTokens = (text) => String(text || "").split(/[,;\n\t]+/).map((t) => t.trim()).filter(Boolean);
export function heatColour(v, field) {                       // watchlist_page.render_heat7
  if (v == null) return ["#161618", "no data"];
  const scale = { pct: 4, r1w: 8, r1m: 15, r3m: 25, ytd: 40, r1y: 60 }[field] || 10;
  return [`rgba(${v >= 0 ? "34,160,70" : "215,50,45"},${Math.min(0.85, 0.16 + (Math.abs(v) / scale) * 0.7).toFixed(3)})`, fx(v, 2, false, true) + "%"];
}
export function summary(rows) {                              // the numbers behind watchlist_page.render_summary7
  const okr = rows.filter((r) => r.has_data && r.pct != null), adv = okr.filter((r) => r.pct > 0).length, dec = okr.filter((r) => r.pct < 0).length;
  const movers = okr.slice().sort((a, b) => a.pct - b.pct);
  return { n: okr.length, adv, dec, avg: okr.length ? mean(okr.map((r) => r.pct)) : null, best: movers[movers.length - 1] || null, worst: movers[0] || null,
    news: rows.filter((r) => r.stories && r.stories.length).length, alerts: rows.filter((r) => r.alert_hit).length };
}

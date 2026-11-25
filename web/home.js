// Arka Trades · Home (market overview). Same content as the classic Home (home_page.py v4.2), most important first:
//   status and one-line brief · your money · NIFTY 50 and market health · benchmarks · signal desk · your book
//   · leadership (sectors, movers, rotation, NIFTY 50 heatmap) · world and macro · calendar.
// Numbers follow home_page.py exactly (checks, pivots, breadth, VIX words, stop tags). On a phone, a bar of section
// buttons stays at the top so any part of the page is one tap away.
import { S, api, paint, esc, num, nice, todayStr, need, ordersOf } from "./app.js";
import { lineChart, resetCharts } from "./charts.js";
import * as PFV from "./portfolio.js";

const GREEN = "#30D158", RED = "#FF453A", AMBER = "#FF9F0A", CYAN = "#5AC8FA", YELLOW = "#FFD60A", LIME = "#84CC16", T2 = "#A3A3A3";
// ── NSE calendar (update once a year from NSE's holiday circular; 2027 is published in late December) ──
const HOLIDAYS = { "2026-01-15": "Municipal elections (Maharashtra)", "2026-01-26": "Republic Day", "2026-03-03": "Holi", "2026-03-26": "Shri Ram Navami", "2026-03-31": "Shri Mahavir Jayanti",
  "2026-04-03": "Good Friday", "2026-04-14": "Dr. Baba Saheb Ambedkar Jayanti", "2026-05-01": "Maharashtra Day", "2026-05-28": "Bakri Id", "2026-06-26": "Muharram", "2026-09-14": "Ganesh Chaturthi",
  "2026-10-02": "Mahatma Gandhi Jayanti", "2026-10-20": "Dussehra", "2026-11-10": "Diwali Balipratipada", "2026-11-24": "Guru Nanak Jayanti", "2026-12-25": "Christmas" };
const SPECIAL = { "2026-11-08": "Muhurat trading (Diwali) · timing per NSE circular" };
const PRE_OPEN = 540, OPEN = 555, CLOSE = 930;
// ── instruments: [group, label, tickers (first that answers wins), kind] ──
const WEI = [["India", "NIFTY 50", ["^NSEI"], "idx"], ["India", "SENSEX", ["^BSESN"], "idx"], ["India", "BANK NIFTY", ["^NSEBANK"], "idx"], ["India", "FIN NIFTY", ["NIFTY_FIN_SERVICE.NS"], "idx"],
  ["India", "NIFTY NEXT 50", ["^NSMIDCP"], "idx"], ["India", "NIFTY MIDCAP 100", ["^CRSMID", "NIFTY_MIDCAP_100.NS"], "idx"], ["India", "NIFTY SMALLCAP 100", ["^CNXSC", "NIFTY_SMLCAP_100.NS"], "idx"], ["India", "NIFTY 500", ["^CRSLDX"], "idx"],
  ["Americas", "S&P 500", ["^GSPC"], "idx"], ["Americas", "Nasdaq Comp", ["^IXIC"], "idx"], ["Americas", "Dow Jones", ["^DJI"], "idx"], ["Americas", "Russell 2000", ["^RUT"], "idx"],
  ["Europe", "FTSE 100", ["^FTSE"], "idx"], ["Europe", "DAX", ["^GDAXI"], "idx"], ["Europe", "CAC 40", ["^FCHI"], "idx"], ["Europe", "Euro Stoxx 50", ["^STOXX50E"], "idx"],
  ["Asia-Pacific", "Nikkei 225", ["^N225"], "idx"], ["Asia-Pacific", "Hang Seng", ["^HSI"], "idx"], ["Asia-Pacific", "Shanghai Comp", ["000001.SS"], "idx"], ["Asia-Pacific", "KOSPI", ["^KS11"], "idx"],
  ["Asia-Pacific", "TAIEX", ["^TWII"], "idx"], ["Asia-Pacific", "ASX 200", ["^AXJO"], "idx"]];
const XASSET = [["Commodities · USD", "Gold", ["GC=F"], "usd"], ["Commodities · USD", "Silver", ["SI=F"], "usd"], ["Commodities · USD", "Brent Crude", ["BZ=F"], "usd"], ["Commodities · USD", "WTI Crude", ["CL=F"], "usd"],
  ["Commodities · USD", "Natural Gas", ["NG=F"], "usd"], ["Commodities · USD", "Copper", ["HG=F"], "usd"], ["Currencies", "USD / INR", ["INR=X"], "fx"], ["Currencies", "EUR / INR", ["EURINR=X"], "fx"],
  ["Currencies", "GBP / INR", ["GBPINR=X"], "fx"], ["Currencies", "JPY / INR", ["JPYINR=X"], "fx4"], ["Currencies", "Dollar index", ["DX-Y.NYB"], "fx"], ["Rates · yield %", "US 10Y", ["^TNX"], "yld"],
  ["Rates · yield %", "US 30Y", ["^TYX"], "yld"], ["Volatility", "India VIX", ["^INDIAVIX"], "vol"], ["Volatility", "CBOE VIX", ["^VIX"], "vol"], ["Crypto · USD", "Bitcoin", ["BTC-USD"], "cry"], ["Crypto · USD", "Ethereum", ["ETH-USD"], "cry"]];
const SECT = [["Bank", ["^NSEBANK"]], ["Private bank", ["NIFTY_PVT_BANK.NS"]], ["PSU bank", ["^CNXPSUBANK"]], ["Fin services", ["NIFTY_FIN_SERVICE.NS"]], ["IT", ["^CNXIT"]], ["Auto", ["^CNXAUTO"]], ["FMCG", ["^CNXFMCG"]],
  ["Pharma", ["^CNXPHARMA"]], ["Metal", ["^CNXMETAL"]], ["Energy", ["^CNXENERGY"]], ["Realty", ["^CNXREALTY"]], ["Media", ["^CNXMEDIA"]], ["Infra", ["^CNXINFRA"]], ["PSE", ["^CNXPSE"]],
  ["Commodities", ["^CNXCMDT"]], ["Consumption", ["^CNXCONSUM"]], ["MNC", ["^CNXMNC"]], ["Services", ["^CNXSERVICE"]]];
const PULSE = [["NIFTY 50", "NIFTY 50"], ["NIFTY MIDCAP 100", "Midcap 100"], ["NIFTY SMALLCAP 100", "Smallcap 100"], ["BANK NIFTY", "Bank Nifty"], ["India VIX", "India VIX"]];
const TAPE = ["NIFTY 50", "SENSEX", "BANK NIFTY", "India VIX", "USD / INR", "Gold", "Brent Crude", "US 10Y", "Bitcoin", "S&P 500", "Nasdaq Comp", "Dow Jones", "FTSE 100", "DAX", "Nikkei 225", "Hang Seng", "Dollar index", "Silver"];
const BENCH = ["SENSEX", "BANK NIFTY", "FIN NIFTY", "NIFTY MIDCAP 100", "NIFTY NEXT 50", "NIFTY 500"];
// NIFTY 50 as of 30 Sep 2026 (BSE in, Wipro out), grouped as on the classic Home
const NIFTY50 = { Financials: ["HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "BAJFINANCE", "BAJAJFINSV", "JIOFIN", "SHRIRAMFIN", "HDFCLIFE", "SBILIFE", "BSE"], IT: ["TCS", "INFY", "HCLTECH", "TECHM"],
  "Energy & power": ["RELIANCE", "ONGC", "COALINDIA", "NTPC", "POWERGRID"], Auto: ["M&M", "MARUTI", "TMPV", "BAJAJ-AUTO", "EICHERMOT"], FMCG: ["ITC", "HINDUNILVR", "NESTLEIND", "TATACONSUM"],
  Healthcare: ["SUNPHARMA", "CIPLA", "DRREDDY", "APOLLOHOSP", "MAXHEALTH"], Metals: ["TATASTEEL", "JSWSTEEL", "HINDALCO", "ADANIENT"], Consumer: ["TITAN", "ASIANPAINT", "TRENT", "ETERNAL"],
  Industrials: ["LT", "BEL", "ADANIPORTS", "INDIGO"], Materials: ["ULTRACEMCO", "GRASIM"], Telecom: ["BHARTIARTL"] };
export const N50 = Object.values(NIFTY50).flat();
const WORLD = [["Tokyo", "Asia/Tokyo", 540, 930], ["Hong Kong", "Asia/Hong_Kong", 570, 960], ["Shanghai", "Asia/Shanghai", 570, 900], ["Mumbai · NSE", "Asia/Kolkata", 555, 930],
  ["Frankfurt", "Europe/Berlin", 540, 1050], ["London", "Europe/London", 480, 990], ["New York", "America/New_York", 570, 960]];
const SECTIONS = [["hm-today", "Today"], ["hm-book", "My book"], ["hm-lead", "Leaders"], ["hm-world", "World"], ["hm-cal", "Calendar"]];

export const homeDefaults = () => ({ ov: {}, ovAt: 0, ovBusy: false, mmi: null, mmiAt: 0, meta: null, gift: null, giftAt: 0 });
const ALL = [...WEI, ...XASSET].map(([, l, t, k]) => [l, t, k]).concat(SECT.map(([l, t]) => ["SEC::" + l, t, "idx"]));
export const allTickers = () => [...new Set(ALL.flatMap(([, t]) => t))];

// ── data ──
export async function loadHome(force) {
  const H = S.hm, now = Date.now();
  if (!H.ovBusy && (force || now - H.ovAt > 55000)) {
    H.ovBusy = true; H.ovAt = now;
    const t = allTickers(), jobs = [];
    for (let i = 0; i < t.length; i += 15) jobs.push(api("/api/overview?full=^NSEI,^INDIAVIX&s=" + encodeURIComponent(t.slice(i, i + 15).join(",")))
      .then((d) => { for (const o of d.overview) H.ov[o.sym] = o; paint(); }).catch(() => {}));
    Promise.all(jobs).then(() => { H.ovBusy = false; paint(); });
  }
  if (now - H.giftAt > 60000) { H.giftAt = now; api("/api/gift").then((g) => { H.gift = g; paint(); }).catch(() => { H.gift = H.gift || { ok: false }; }); }
  if (now - H.mmiAt > 15 * 60000) { H.mmiAt = now; api("/api/mmi").then((m) => { H.mmi = m; paint(); }).catch(() => { H.mmi = { status: "unavailable" }; }); }
  if (!H.meta) { H.meta = {}; api("/api/market?f=meta").then((m) => { H.meta = m; paint(); }).catch(() => {}); }
  need([...N50, ...S.pf.lots.map((l) => l.sym)]);
  if (S.pf.loaded && S.pf.lots.length) PFV.loadHistory();
}
function metrics() {                                         // {label: metrics} like home_page.market_data()
  const M = {};
  for (const [label, chain, kind] of ALL) for (const t of chain) { const o = S.hm.ov[t]; if (o && o.ok) { M[label] = { ...o, kind }; break; } }
  return M;
}

// ── small helpers (home_page.py formats) ──
const ok = (v) => v != null && isFinite(v);
const cl = (v, eps = 0.005) => (!ok(v) ? "mu" : v > eps ? "up" : v < -eps ? "dn" : "mu");
const arrow = (v) => (ok(v) && v > 0.005 ? "▲" : ok(v) && v < -0.005 ? "▼" : "■");
const pct = (v, d = 2, sign = true) => { if (!ok(v)) return "—"; if (+v.toFixed(d) === 0) return (0).toFixed(d) + "%"; return (sign && v > 0 ? "+" : v < 0 ? "−" : "") + num(Math.abs(v), d) + "%"; };
const pts = (v, d = 2) => (!ok(v) ? "—" : (v > 0 ? "+" : v < 0 ? "−" : "") + num(Math.abs(v), d));
const px = (v, kind = "idx") => (!ok(v) ? "—" : kind === "fx4" ? num(v, 4) : kind === "fx" || kind === "vol" ? num(v, 2) : kind === "yld" ? num(v, 3) + "%" : kind === "cry" ? "$" + num(v, 0) : kind === "usd" ? "$" + num(v, v < 1000 ? 2 : 1) : num(v, 2));
const bp = (p) => (!ok(p) ? "—" : (p > 0 ? "+" : p < 0 ? "−" : "") + num(Math.abs(p * 100), 1) + " bp");
const ptsOf = (m, pc) => (ok(pc) ? m.price - m.price / (1 + pc / 100) : null);       // the price change behind a % change (for yields, in points)
const inr = (x, signed) => { if (!ok(x)) return "—"; const a = Math.abs(x), s = x <= -0.5 ? "−" : signed && x >= 0.5 ? "+" : ""; return s + "₹" + (a >= 1e7 ? num(a / 1e7, 2) + " Cr" : a >= 1e5 ? num(a / 1e5, 2) + " L" : num(a, 0)); };
const heat = (v, lim = 3) => { if (!ok(v)) return "#1a1a1a"; const k = Math.min(1, Math.abs(v) / lim); if (Math.abs(v) < 0.05) return "#262626"; return v > 0 ? `rgba(48,209,88,${(0.16 + 0.62 * k).toFixed(2)})` : `rgba(255,69,58,${(0.16 + 0.62 * k).toFixed(2)})`; };
function spark(vals, w = 92, h = 24, color) {
  vals = (vals || []).filter(ok); if (vals.length < 3) return "";
  color = color || (vals[vals.length - 1] >= vals[0] ? GREEN : RED);
  const lo = Math.min(...vals), rng = Math.max(...vals) - lo || 1;
  return `<svg class="sp" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${vals.map((v, i) => `${((i / (vals.length - 1)) * w).toFixed(1)},${(h - 2 - ((v - lo) / rng) * (h - 4)).toFixed(1)}`).join(" ")}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}
const card = (title, right, body, cls = "", flush = false) => `<div class="hm-c ${cls}"><div class="hm-ch"><b>${title}</b>${right ? `<span>${right}</span>` : ""}</div><div class="${flush ? "hm-flush" : "hm-pad"}">${body}</div></div>`;
const sec = (id, title, sub = "") => `<h2 class="hm-sec" id="${id}">${title}${sub ? ` <span>${sub}</span>` : ""}</h2>`;
const kv = (k, v) => `<div class="hm-kv"><span>${k}</span><b>${v}</b></div>`;
const empty = (t) => `<div class="hm-empty">${t}</div>`;

// ── NSE session, expiries, holidays (home_page.py) ──
const iso = (t) => t.toISOString().slice(0, 10);
const istNow = () => { const n = new Date(); return new Date(n.getTime() + n.getTimezoneOffset() * 60000 + 330 * 60000); };
const dateOf = (s) => new Date(s + "T00:00:00");
const addDays = (s, k) => { const t = dateOf(s); t.setDate(t.getDate() + k); return iso2(t); };
const iso2 = (t) => `${t.getFullYear()}-${String(t.getMonth() + 1).padStart(2, "0")}-${String(t.getDate()).padStart(2, "0")}`;
const wday = (s) => dateOf(s).getDay();                                       // 0 Sunday … 6 Saturday
export const isTradingDay = (s) => wday(s) >= 1 && wday(s) <= 5 && !HOLIDAYS[s];
const nextTradingDay = (s) => { let n = addDays(s, 1); for (let i = 0; i < 15 && !isTradingDay(n); i++) n = addDays(n, 1); return n; };
// Sessions the nightly "Market data" job should already have published: a trading day counts once its evening runs are done (22:00 IST).
export function dataBehind(asof, now = istNow()) {
  if (!asof) return { n: 0, due: null };
  let due = iso2(now); if (now.getHours() < 22) due = addDays(due, -1);
  due = prevTradingOnOrBefore(due); let n = 0;
  for (let d = addDays(asof, 1), i = 0; d <= due && i < 60; d = addDays(d, 1), i++) if (isTradingDay(d)) n++;
  return { n, due };
}
const prevTradingOnOrBefore = (s) => { for (let i = 0; i < 15 && !isTradingDay(s); i++) s = addDays(s, -1); return s; };
const fmtDay = (s, o = { weekday: "short", day: "2-digit", month: "short" }) => dateOf(s).toLocaleDateString("en-IN", o);
export function session(now = istNow()) {
  const d = iso2(now), t = now.getHours() * 60 + now.getMinutes(), nxt = nextTradingDay(d), nxtTxt = "next session " + fmtDay(nxt), sp = SPECIAL[d];
  if (HOLIDAYS[d]) return { cls: "closed", short: "Holiday", detail: `${HOLIDAYS[d]} · ${nxtTxt}`, progress: null };
  if (wday(d) === 0 || wday(d) === 6) return { cls: "closed", short: "Closed", detail: (sp ? sp + " · " : "") + "Weekend · " + nxtTxt, progress: null };
  if (t >= PRE_OPEN && t < OPEN) return { cls: "pre", short: "Pre-open", detail: "Order matching · opens 09:15", progress: 0 };
  if (t >= OPEN && t <= CLOSE) { const left = CLOSE - t; return { cls: "live", short: "Open", detail: `Closes 15:30 · ${Math.floor(left / 60)}h ${String(left % 60).padStart(2, "0")}m left`, progress: (t - OPEN) / (CLOSE - OPEN) }; }
  if (t < PRE_OPEN) return { cls: "closed", short: "Closed", detail: "Pre-open 09:00 · opens 09:15", progress: null };
  return { cls: "closed", short: "Closed", detail: "Closed 15:30 · " + nxtTxt, progress: 1 };
}
function sessionChip(Ss, now = istNow()) {
  const d = iso2(now), t = now.getHours() * 60 + now.getMinutes();
  if (Ss.cls === "live") { const l = Math.max(0, CLOSE - t); return ["live", "Market open", l >= 60 ? `closes in ${Math.floor(l / 60)}h ${String(l % 60).padStart(2, "0")}m` : `closes in ${l} min`]; }
  if (Ss.cls === "pre") return ["pre", "Pre-open", `opens 09:15, in ${Math.max(0, OPEN - t)} min`];
  if (isTradingDay(d) && t < PRE_OPEN) { const k = OPEN - t; return ["", "Market closed", `opens 09:15, in ${Math.floor(k / 60)}h ${String(k % 60).padStart(2, "0")}m`]; }
  const nxt = nextTradingDay(d), when = Math.round((dateOf(nxt) - dateOf(d)) / 864e5) === 1 ? "tomorrow" : fmtDay(nxt);
  return ["", HOLIDAYS[d] ? "Market holiday" : "Market closed", `opens ${when}, 09:15`];
}
export function nextExpiry(now = istNow(), weekday = 2, monthly = false) {  // weekday as JS getDay(): Tuesday 2 (NSE), Thursday 4 (BSE)
  const today = iso2(now), after = now.getHours() * 60 + now.getMinutes() > CLOSE, okDay = (e) => e > today || (e === today && !after);
  if (monthly) {
    let y = now.getFullYear(), m = now.getMonth(), e = null;
    for (let k = 0; k < 3; k++) { const last = new Date(y, m + 1, 0); while (last.getDay() !== weekday) last.setDate(last.getDate() - 1); e = prevTradingOnOrBefore(iso2(last)); if (okDay(e)) return e; m++; if (m > 11) { m = 0; y++; } }
    return e;
  }
  let d = today, e = null;
  for (let k = 0; k < 3; k++) { const c = addDays(d, (weekday - wday(d) + 7) % 7); e = prevTradingOnOrBefore(c); if (okDay(e)) return e; d = addDays(c, 1); }
  return e;
}
const tradingDaysLeft = (s) => { const end = s.slice(0, 4) + "-12-31"; let n = 0, d = addDays(s, 1); while (d <= end) { n += isTradingDay(d) ? 1 : 0; d = addDays(d, 1); } return n; };
const upcomingHolidays = (n, s) => Object.entries(HOLIDAYS).map(([d, nm]) => [d, nm, "HOLIDAY"]).concat(Object.entries(SPECIAL).map(([d, nm]) => [d, nm, "SPECIAL"])).filter(([d]) => d >= s).sort().slice(0, n);
const vixWord = (x) => (x < 13 ? "calm" : x < 18 ? "normal" : x < 24 ? "elevated" : "stressed");

// ── sections ──
function pulse(M) {                                          // the top strip: GIFT Nifty and the four numbers that say how the Indian market is doing. Nothing moves.
  const g = S.hm.gift, tile = (label, v, c, sub, symb) => `<${symb ? `button data-sym="${esc(symb)}"` : "div"} class="hm-pl"><span>${label}</span><b>${v}</b><em class="${c}">${sub}</em></${symb ? "button" : "div"}>`;
  const gift = !g ? tile("GIFT Nifty", '<i class="sk">00,000</i>', "mu", "loading") : g.ok ? tile("GIFT Nifty", num(g.price, 1), cl(g.chg), `${arrow(g.chg)} ${pts(g.pts, 1)} (${pct(g.chg)})`) : tile("GIFT Nifty", "—", "mu", "not available now");
  return `<div class="hm-pulse" aria-label="Market at a glance">${gift}${PULSE.map(([l, short]) => { const m = M[l], t = (ALL.find((a) => a[0] === l) || [, []])[1].find((x) => S.hm.ov[x] && S.hm.ov[x].ok);
    return !m ? tile(short, '<i class="sk">00,000</i>', "mu", "loading") : tile(short, px(m.price, m.kind), cl(m.chg), `${arrow(m.chg)} ${pts(m.pts)} (${pct(m.chg)})`, t); }).join("")}</div>`;
}
function tape(M) {
  const items = TAPE.map((l) => [l, M[l]]).filter(([, m]) => m);
  if (!items.length) return '<div class="hm-tape"><div class="hm-tt"><span class="sk">Loading prices…</span></div></div>';
  const one = items.map(([l, m]) => `<span><b>${esc(l)}</b>${px(m.price, m.kind)} <em class="${cl(m.kind === "yld" ? m.pts : m.chg)}">${m.kind === "yld" ? bp(m.pts) : arrow(m.chg) + " " + pct(m.chg)}</em></span>`).join("");
  return `<div class="hm-tape" aria-label="Market ticker"><div class="hm-tt">${one}${one}</div></div>`;
}
function breadth(stocks) {
  const vals = stocks.map((s) => S.q[s]).filter((q) => q && q.ok && ok(q.pct)).map((q) => q.pct);
  const adv = vals.filter((v) => v > 0.05).length, dec = vals.filter((v) => v < -0.05).length;
  return { adv, dec, flat: vals.length - adv - dec, n: vals.length, avg: vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null };
}
function header(M, B, Ss) {
  const now = istNow(), n = M["NIFTY 50"], bits = [];
  if (n && ok(n.chg)) { const word = n.chg > 0.05 ? "up" : n.chg < -0.05 ? "down" : "flat"; bits.push(`NIFTY 50 is <b class="${cl(n.chg)}">${word}${word === "flat" ? "" : " " + pct(Math.abs(n.chg), 2, false)}</b> at <b>${num(n.price, 0)}</b>.`); }
  if (B.n) bits.push(`<b>${B.adv}</b> of ${B.n} NIFTY stocks are up` + (B.n >= 45 ? "." : " (quotes for the rest have not arrived yet)."));
  const secs = SECT.map(([l]) => [l, M["SEC::" + l]]).filter(([, m]) => m && ok(m.chg)).sort((a, b) => a[1].chg - b[1].chg);
  if (secs.length >= 4) { const [wl, wv] = secs[0], [bl, bv] = secs[secs.length - 1]; bits.push(`${esc(bl)} leads (<b class="${cl(bv.chg)}">${pct(bv.chg, 1)}</b>), ${esc(wl)} lags (<b class="${cl(wv.chg)}">${pct(wv.chg, 1)}</b>).`); }
  const v = M["India VIX"]; if (v) bits.push(`India VIX <b>${num(v.price, 1)}</b>, ${vixWord(v.price)}.`);
  const d = iso2(now), t = now.getHours() * 60 + now.getMinutes();               // before the bell: global cues
  if (Ss.cls !== "live" && isTradingDay(d) && t < OPEN) {
    const cues = [["S&P 500", "S&P 500"], ["Nasdaq Comp", "Nasdaq"], ["Nikkei 225", "Nikkei"], ["Hang Seng", "Hang Seng"]].map(([l, nm]) => [nm, M[l]])
      .filter(([, m]) => m && ok(m.chg) && Math.round((dateOf(d) - dateOf(m.d)) / 864e5) <= 4).map(([nm, m]) => `${esc(nm)} <b class="${cl(m.chg)}">${pct(m.chg, 1)}</b>`);
    if (cues.length) bits.push("Global cues: " + cues.join(", ") + ".");
  }
  const [c, lab, sub] = sessionChip(Ss, now), asof = !n ? "Waiting for prices" : Ss.cls === "live" ? `Updated ${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}` : `Closing prices · ${nice(n.d).replace(/ \d{4}$/, "")}`;
  const rg = S.hm.meta && S.hm.meta.regime && S.hm.meta.regime.now && S.hm.meta.regime.now["Nifty 500"];
  const rgc = rg ? { "Confirmed uptrend": GREEN, "Under pressure": YELLOW, "Rally attempt": "#4FA3FF", Correction: RED }[rg.state] || T2 : null;
  const chips = `<span class="hm-chip ${c}"><i></i><b>${lab}</b> · ${esc(sub)}</span>${rg ? `<a class="hm-chip k" href="#/regime" style="--c:${rgc}"><i></i>Regime: ${esc(rg.state)} · ${num(rg.score, 0)}</a>` : ""}<span class="hm-chip">${asof}</span>`;
  const day = Ss.cls === "live" && Ss.progress != null ? `<div class="hm-day" title="Trading day, 09:15 to 15:30"><i style="width:${(Math.max(0, Math.min(1, Ss.progress)) * 100).toFixed(1)}%"></i></div>` : "";
  return `<div class="hm-hd"><div><div class="o">${now.toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric" })}</div><h1>Market overview</h1></div><div class="hm-chips">${chips}</div></div>
    <div class="hm-brief">${bits.join(" ") || "Market data is loading."}</div>${day}`;
}
const stopState = (h) => { const ltp = h.ltp, sl = h.sl, tg = h.target; if (!ok(ltp)) return ""; if (ok(sl) && sl > 0) { if (ltp <= sl) return "hit"; if (ltp <= sl * 1.03) return "near"; } if (ok(tg) && tg > 0 && ltp >= tg) return "target"; return ""; };
const STOP_TAG = { hit: ["Stop hit", RED], near: ["Near stop", AMBER], target: ["At target", GREEN] };
const tag = (h) => { const t = STOP_TAG[stopState(h)]; return t ? ` <span class="hm-tag" style="--c:${t[1]}">${t[0]}</span>` : ""; };
function bookBar(holds) {
  const valid = holds.filter((h) => h.value != null); if (!valid.length) return "";
  const val = valid.reduce((a, h) => a + h.value, 0), inv = valid.reduce((a, h) => a + h.invested, 0), day = valid.reduce((a, h) => a + (ok(h.dayPnl) ? h.dayPnl : 0), 0), pnl = val - inv;
  const dp = val - day ? (day / (val - day)) * 100 : null, pp = inv ? (pnl / inv) * 100 : null, st = valid.map(stopState), withStop = valid.filter((h) => ok(h.sl) && h.sl > 0).length;
  const hit = st.filter((x) => x === "hit").length, near = st.filter((x) => x === "near").length;
  const stop = !withStop ? '<div><small>Stops</small><b class="mu">Not set</b><em>Add stops in Portfolio</em></div>'
    : hit || near ? `<div><small>Stops</small><b style="color:${hit ? RED : AMBER}">${[hit ? hit + " hit" : "", near ? near + " near" : ""].filter(Boolean).join(" · ")}</b><em>${withStop} of ${valid.length} have a stop</em></div>`
    : `<div><small>Stops</small><b class="up">All clear</b><em>${withStop} of ${valid.length} have a stop</em></div>`;
  const moved = valid.filter((h) => ok(h.chg)), best = moved.length ? moved.reduce((a, b) => (b.chg > a.chg ? b : a)) : null, worst = moved.length ? moved.reduce((a, b) => (b.chg < a.chg ? b : a)) : null;
  const mover = (h, title, up) => (!h || (up && h.chg <= 0) || (!up && h.chg >= 0) ? `<div><small>${title}</small><b class="mu">—</b><em>${up ? "No gainers today" : "No losers today"}</em></div>`
    : `<button data-sym="${esc(h.sym)}"><small>${title}</small><b>${esc(h.sym)}</b><em class="${cl(h.chg)}">${pct(h.chg)} · ${inr(h.dayPnl, true)}</em></button>`);
  return `<a class="hm-bk" href="#/portfolio" aria-label="Your portfolio"><div><small>Portfolio value</small><b>${inr(val)}</b><em>${holds.length} holding${holds.length === 1 ? "" : "s"}</em></div>
    <div><small>Today</small><b class="${cl(day)}">${inr(day, true)}</b><em class="${cl(dp)}">${pct(dp)}</em></div><div><small>Open profit</small><b class="${cl(pnl)}">${inr(pnl, true)}</b><em class="${cl(pp)}">${pct(pp)}</em></div>
    ${stop}${mover(best, "Best today", true)}${mover(worst, "Worst today", false)}</a>`;
}
function pivots(m, Ss) {                                     // classic floor pivots, for today during the session, else for the next one
  const live = Ss.cls === "live" && m.d === todayStr(), prev = m.price - m.pts;
  const [H, L, C] = live ? [m.pdh, m.pdl, prev] : [m.high, m.low, m.price];
  if (!(ok(H) && ok(L) && ok(C)) || H < L) return { live };
  const p = (H + L + C) / 3;
  return { p, r1: 2 * p - L, s1: 2 * p - H, live };
}
const avg = (a, k) => (a && a.length >= k ? a.slice(-k).reduce((x, y) => x + y, 0) / k : null);
function hero(M, Ss) {
  const m = M["NIFTY 50"];
  if (!m) return card("NIFTY 50", "", S.hm.ovBusy || !S.hm.ovAt ? '<div class="sk" style="height:300px;border-radius:12px"></div>' : empty("NIFTY 50 data is unavailable right now. It retries automatically."), "hm-hero");
  const c = m.series ? m.series.c : null, price = m.price, prev = m.price - m.pts, col = (m.chg || 0) >= 0 ? GREEN : RED;
  const vs = (lv) => (!ok(lv) || !lv ? ["", ""] : [pct((price / lv - 1) * 100, 1), cl(price / lv - 1)]);
  const P = pivots(m, Ss), op = m.open, gap = ok(op) && ok(prev) && prev ? (op / prev - 1) * 100 : null;
  const cell = (l, v, sub = "", c2 = "") => `<div><small>${l}</small><b>${ok(v) ? num(v, 0) : "—"}</b><em class="${c2}">${sub || "&nbsp;"}</em></div>`;
  const cells = [cell("Open", op, gap != null ? "Gap " + pct(gap) : "", cl(gap)), cell("Prev close", prev), cell("Day high", m.high), cell("Day low", m.low), cell("52W high", m.hi52, ...vs(m.hi52)), cell("52W low", m.lo52, ...vs(m.lo52)),
    cell("Pivot", P.p, ...vs(P.p)), cell("Resistance", P.r1, ...vs(P.r1)), cell("Support", P.s1, ...vs(P.s1)), ...[20, 50, 200].map((k) => cell(k + "-day avg", avg(c, k), ...vs(avg(c, k))))].join("");
  let chart = "";
  if (c && c.length > 30) {                                  // 6 months of daily closes with the 20- and 50-day averages
    const n = c.length, from = Math.max(0, n - 126), ma = (k) => c.map((_, i) => (i + 1 >= k ? c.slice(i + 1 - k, i + 1).reduce((a, b) => a + b, 0) / k : null)).slice(from);
    chart = lineChart(m.series.d.slice(from), [{ n: "NIFTY 50", c: col, v: c.slice(from), w: 2, fill: true }, { n: "20-day", c: AMBER, v: ma(20), w: 1.1 }, { n: "50-day", c: "#BF5AF2", v: ma(50), w: 1.1 }], { h: 170, fmt: (v) => num(v, 0) });
  }
  const per = [["1W", m.w1], ["1M", m.m1], ["YTD", m.ytd]].map(([k, v]) => `<span>${k}<b class="${cl(v)}">${pct(v, 1)}</b></span>`).join("");
  return card("NIFTY 50", `NSE · ${nice(m.d).replace(/ \d{4}$/, "")} · 6 months, daily`, `<div class="hm-htop"><button data-sym="^NSEI"><div class="hm-hv">${num(price, 2)}</div><div class="hm-hc ${cl(m.chg)}">${arrow(m.chg)} ${pts(m.pts)} (${pct(m.chg)}) today</div></button><div class="hm-per">${per}</div></div>
    ${chart}<div class="hm-nt">Pivot, resistance and support are for ${P.live ? "today" : "the next session"} (classic floor pivots from ${P.live ? "yesterday" : "the last session"}); the small percentages show how far NIFTY is from each level.</div><div class="hm-st">${cells}</div>`, "hm-hero");
}
function checks(M, B) {                                      // six yes/no checks on trend, breadth and volatility
  const n = M["NIFTY 50"], v = M["India VIX"], out = [], s = n && n.series ? n.series.c : null;
  if (s && s.length >= 200) { const d50 = avg(s, 50), d200 = avg(s, 200); out.push(["Above 50-day average", n.price > d50], ["Above 200-day average", n.price > d200], ["50-day above 200-day", d50 > d200]); }
  if (n && ok(n.hi52)) out.push(["Within 10% of 52W high", n.price >= 0.9 * n.hi52]);
  if (B.n >= 30) out.push([`More stocks up (${B.adv} vs ${B.dec})`, B.adv > B.dec]);
  const vs = v && v.series ? v.series.c : null;
  if (vs && vs.length >= 100) { const so = [...vs].sort((a, b) => a - b), med = so.length % 2 ? so[(so.length - 1) / 2] : (so[so.length / 2 - 1] + so[so.length / 2]) / 2; out.push([`VIX below 1Y median (${num(med, 1)})`, v.price < med]); }
  return out;
}
function rsi(c, n = 14) {                                    // Wilder RSI on daily closes, as home_page._rsi
  if (!c || c.length <= n) return null;
  let up = 0, dn = 0; const a = 1 / n;
  for (let i = 1; i < c.length; i++) { const d = c[i] - c[i - 1], u = Math.max(d, 0), w = Math.max(-d, 0); if (i === 1) { up = u; dn = w; } else { up = a * u + (1 - a) * up; dn = a * w + (1 - a) * dn; } }
  return dn === 0 ? 100 : 100 - 100 / (1 + up / dn);
}
function health(M, B) {
  const ck = checks(M, B), mmi = S.hm.mmi || {}, v = M["India VIX"], n = M["NIFTY 50"];
  let top = empty("The trend checks need NIFTY 50 history. They retry automatically."), rows = "";
  if (ck.length) {
    const score = ck.filter(([, x]) => x).length, r = score / ck.length;
    const [lab, c] = r >= 0.83 ? ["Risk-on", GREEN] : r >= 0.66 ? ["Constructive", LIME] : r >= 0.5 ? ["Neutral", YELLOW] : r >= 0.33 ? ["Cautious", AMBER] : ["Risk-off", RED];
    top = `<div class="hm-vd"><b style="color:${c}">${lab}</b><span>${score} of ${ck.length} checks pass</span></div><div class="hm-meter">${ck.map((_, i) => `<i style="background:${i < score ? c : ""}"></i>`).join("")}</div>`;
    rows = `<div class="hm-cks">${ck.map(([t, x]) => `<div class="hm-ck"><i class="${x ? "y" : "n"}">${x ? "✓" : "✕"}</i><span>${esc(t)}</span></div>`).join("")}</div>`;
  }
  const g = [], kvs = [];
  g.push(B.n ? `<div class="hm-gz"><small>Breadth</small><b><span class="up">${B.adv}</span> <span class="mu">/</span> <span class="dn">${B.dec}</span></b><em>Average ${pct(B.avg, 1)}</em>
    <div class="ad"><i style="width:${((B.adv / B.n) * 100).toFixed(1)}%;background:${GREEN}"></i><i style="width:${((B.flat / B.n) * 100).toFixed(1)}%;background:#3a3a3e"></i><i style="width:${((B.dec / B.n) * 100).toFixed(1)}%;background:${RED}"></i></div></div>`
    : '<div class="hm-gz"><small>Breadth</small><b class="mu">—</b><em>Waiting for quotes</em></div>');
  if (v) {
    const vs = v.series ? v.series.c : null, pctl = vs && vs.length >= 60 ? (vs.filter((x) => x < v.price).length / vs.length) * 100 : null, word = vixWord(v.price);
    const vc = { calm: GREEN, normal: LIME, elevated: AMBER, stressed: RED }[word];
    g.push(`<div class="hm-gz"><small>India VIX</small><b>${num(v.price, 2)}</b><em>${word[0].toUpperCase() + word.slice(1)} · <span class="${cl(-v.chg)}">${pct(v.chg, 1)}</span></em><div class="lv"><i style="width:${Math.max(4, Math.min(100, pctl ?? 50)).toFixed(0)}%;background:${vc}"></i></div></div>`);
    if (pctl != null) kvs.push(kv("VIX against its last year", `higher than ${num(pctl, 0)}% of days`));
  } else g.push('<div class="hm-gz"><small>India VIX</small><b class="mu">—</b><em>Unavailable</em></div>');
  if (mmi.status === "live" || mmi.status === "stale") {
    const z = mmi.zone, zc = z === "Extreme Fear" ? RED : z === "Fear" ? AMBER : z === "Greed" ? LIME : GREEN;
    g.push(`<div class="hm-gz"><small>Market mood</small><b style="color:${zc}">${num(mmi.score, 0)}</b><em>${esc(z)}${mmi.status === "stale" ? " · cached" : ""}</em><div class="gg"><i style="left:${Math.max(0, Math.min(100, mmi.score)).toFixed(1)}%"></i></div></div>`);
  } else g.push(`<div class="hm-gz"><small>Market mood</small><b class="mu">—</b><em>${S.hm.mmi ? "Unavailable right now" : "Loading…"}</em></div>`);
  const r = n && n.series ? rsi(n.series.c) : null;
  if (r != null) kvs.unshift(kv("NIFTY RSI (14)", `${num(r, 0)} · ${r >= 70 ? "overbought" : r >= 55 ? "strong" : r > 45 ? "neutral" : r > 30 ? "weak" : "oversold"}`));
  if (n && v && ok(v.price)) { const mv = v.price / Math.sqrt(252); kvs.splice(r != null ? 1 : 0, 0, kv("Expected daily move, from VIX", `±${num((n.price * mv) / 100, 0)} pts · ±${num(mv, 2)}%`)); }
  const sc = SECT.map(([l]) => M["SEC::" + l]).filter((x) => x && ok(x.chg)), up = sc.filter((x) => x.chg > 0.05).length;
  if (sc.length) kvs.push(kv("Sectors advancing", `<span class="${up * 2 > sc.length ? "up" : up * 2 < sc.length ? "dn" : ""}">${up}</span> of ${sc.length}`));
  const fx = M["USD / INR"]; if (fx && ok(fx.chg)) kvs.push(kv("USD / INR", `${num(fx.price, 2)} <span class="${cl(-fx.chg)}">${pct(fx.chg, 2)}</span>`));
  return card("Market health", "Trend, breadth and volatility", top + `<div class="hm-g3">${g.join("")}</div>` + rows + `<div class="hm-kvs">${kvs.join("")}</div>`);
}
function bench(M) {
  const out = BENCH.map((l) => [l, M[l]]).filter(([, m]) => m).map(([l, m]) => `<button class="hm-ix" data-sym="${esc((ALL.find((a) => a[0] === l) || [, [""]])[1].find((t) => S.hm.ov[t] && S.hm.ov[t].ok) || "")}"><div class="n">${esc(l)}</div>
    <div class="r"><div class="v">${num(m.price, 2)}</div>${spark((m.spark || []).slice(-22), 58, 24)}</div><div class="c ${cl(m.chg)}">${pts(m.pts)} (${pct(m.chg)})</div>
    <div class="w"><span>1W<b class="${cl(m.w1)}">${pct(m.w1, 1)}</b></span><span>1M<b class="${cl(m.m1)}">${pct(m.m1, 1)}</b></span></div></button>`).join("");
  return out ? `<div class="hm-ixs">${out}</div>` : "";
}
function desk() {                                            // the signal desk in brief (fno_page.home_desk)
  const d = S.sig; if (!d || !d.leaders) return "";
  const word = { on: "approved", trial: "on trial", off: "switched off" }, f = (v, k = 1) => (ok(v) ? (v > 0 ? "+" : v < 0 ? "−" : "") + num(Math.abs(v * 100), k) + "%" : "—");
  const pxs = (v) => (!ok(v) ? "—" : Math.abs(v) >= 5000 ? num(v, 0) : Math.abs(v) >= 100 ? num(v, 1) : num(v, 2));
  const L = d.leaders, W = d.swing, od = ordersOf(d), w = Object.fromEntries((L.record && L.record.windows || []).map((x) => [x.key, x])), all = w.all || { book: {}, index: {} };
  const b1 = od.length ? `<div class="hm-vd"><b>${od.length}</b><span>to place at the next open · last close shown</span></div><div class="hm-kvs">${od.slice(0, 6).map((o) => kv(`${esc(o.book)} · ${o.act.toLowerCase()}${ok(o.stop) ? " · stop " + pxs(o.stop) : ""}`, `<span class="${o.act === "Buy" ? "up" : "dn"}">${esc(o.sym)} ${pxs(o.ref)}</span>`)).join("")}${od.length > 6 ? kv(`and ${od.length - 6} more in Signals`, "") : ""}</div>`
    : `<div class="hm-vd"><b class="mu">No orders</b><span>for the next open</span></div><div class="hm-kvs">${kv("Leaders book", L.check_today ? "checked today, no change" : "next check next week")}${kv("Swing trades open", (W.open || []).length)}${kv("Swing record", `${num(((W.record && W.record.all && W.record.all.win) || 0) * 100, 0)}% won · ${f(W.record && W.record.all && W.record.all.exp, 2)} a trade`)}</div>`;
  const cg = all.book.cagr, dd = L.record && L.record.dd_now, tops = (L.holdings || []).slice(0, 2).map((t) => `${esc(t.sym)} ${f(t.pnl, 0)}`).join(", ") || "—";
  const b2 = `<div class="hm-vd"><b style="color:${ok(cg) && cg > 0 ? GREEN : RED}">${f(cg)}</b><span>a year in the test, after costs</span></div><div class="hm-kvs">${kv("NIFTY 500, same period", f(all.index.cagr) + " a year")}${kv("Status", word[L.status] || "—")}${kv("Holding", (L.holdings || []).length + " stocks")}${kv("From its peak now", `<span class="${ok(dd) && dd < -0.001 ? "dn" : ""}">${f(dd)}</span>`)}${kv("Best holdings", tops)}</div>`;
  const act = { buy: ["Buy the dip at the open", GREEN], sell: ["Sell at the open", RED], hold: ["In a dip trade", AMBER], none: ["No signal", T2] };
  let head = null, rows = "";
  for (const x of d.index || []) {
    const [wd, c] = act[x.action] || act.none, fo = x.fno || {};
    if (x.action !== "none" && !head) head = [`${x.name}: ${wd.toLowerCase()}`, c];
    const note = x.action !== "none" ? wd : x.above200 ? "buys at or below " + pxs(x.trigger) : "below its 200-day average";
    rows += kv(`${esc(x.name)} · ${word[x.status] || ""}`, `<span style="color:${x.action !== "none" ? c : "inherit"}">${esc(note)}</span>`) + kv("Put-call ratio · max pain", `${ok(fo.pcr) ? num(fo.pcr, 2) : "—"} · ${ok(fo.max_pain) ? num(fo.max_pain, 0) : "—"}`) + kv("Put wall · call wall", `${ok(fo.put_wall) ? num(fo.put_wall, 0) : "—"} · ${ok(fo.call_wall) ? num(fo.call_wall, 0) : "—"}`);
  }
  head = head || ["No index signal", T2];
  const b3 = `<div class="hm-vd"><b style="color:${head[1]}">${esc(head[0])}</b><span>dip in an uptrend</span></div><div class="hm-kvs">${rows}</div>`;
  const asof = d.asof ? `Signals from ${nice(d.asof).replace(/ \d{4}$/, "")} close` : "";
  return sec("hm-desk", "Signal desk", "fixed rules tested on real prices · records, stops and sizes in Signals") +
    `<div class="hm-g c3">${card("Orders for the next open", asof, b1)}${card("Leaders book · 20 strongest stocks", "trend", b2)}${card("NIFTY 50 and BANK NIFTY", "index", b3)}</div>`;
}
function holdingsCard(holds, realised) {
  const right = realised ? `Realised ${inr(realised, true)} all time` : "By value";
  if (!holds.length) return card("Holdings", "", empty("<b>No holdings yet.</b> Add your buys in Portfolio to see value, today's profit and stops here."), "", true);
  const top = holds.slice(0, 6), more = holds.length > 6 ? `<a class="hm-more" href="#/portfolio">+ ${holds.length - 6} more in Portfolio</a>` : "";
  const phone = top.map((h) => !ok(h.ltp) ? `<button class="hm-li" data-sym="${esc(h.sym)}" style="opacity:.6"><span class="a"><b>${esc(h.sym)}</b><small>Price not available right now</small></span><span class="z"></span></button>`
    : `<button class="hm-li" data-sym="${esc(h.sym)}"><span class="a"><b>${esc(h.sym)}${tag(h)}</b><small>₹${num(h.ltp, 2)} · ${pct(h.weight, 1, false)} of book</small></span><span class="z"><b class="${cl(h.chg)}">${pct(h.chg)}</b><small class="${cl(h.pnlPct)}">${pct(h.pnlPct, 1)} overall</small></span></button>`).join("");
  const desk2 = `<table class="hm-t"><thead><tr><th class="l">Stock</th><th>Price</th><th>Today</th><th>Today ₹</th><th>Overall</th><th>Weight</th></tr></thead><tbody>${top.map((h) => !ok(h.ltp)
    ? `<tr data-sym="${esc(h.sym)}" class="st"><td class="l">${esc(h.sym)}</td><td colspan="5" class="l">Price not available right now</td></tr>`
    : `<tr data-sym="${esc(h.sym)}"><td class="l">${esc(h.sym)}${tag(h)}</td><td>₹${num(h.ltp, 2)}</td><td class="${cl(h.chg)}">${pct(h.chg)}</td><td class="${cl(h.dayPnl, 0.5)}">${inr(h.dayPnl, true)}</td>
      <td class="${cl(h.pnlPct)}" title="${inr(h.pnl, true)}">${pct(h.pnlPct, 1)}</td><td>${ok(h.weight) ? `<span class="hm-wt"><i style="width:${Math.max(0, Math.min(100, h.weight)).toFixed(0)}%"></i></span>${num(h.weight, 0)}%` : "—"}</td></tr>`).join("")}</tbody></table>`;
  return card("Holdings", right, `<div class="only-m">${phone}</div><div class="only-d hm-tw">${desk2}</div>${more}`, "", true);
}
function watchCard() {
  const wl = S.wl.rows.filter((r) => r.list === S.wl.list).map((r) => r.sym);
  if (!S.wl.loaded) return card("Watchlist", "", empty("Loading your watchlist…"), "", true);
  if (!wl.length) return card("Watchlist", "", empty("<b>Your watchlist is empty.</b> Add the stocks you are stalking in Watchlist and they show up here."), "", true);
  const rows = wl.slice(0, 7).map((s) => { const q = S.q[s] || {};
    return `<button class="hm-li" data-sym="${esc(s)}"><span class="a"><b>${esc(s)}</b></span><span class="z hm-row2"><b>${q.ok ? "₹" + num(q.price, 2) : "—"}</b><small class="${cl(q.pct)}">${pct(q.pct)}</small></span></button>`; }).join("");
  return card(esc(S.wl.list === "Main" ? "Watchlist" : S.wl.list), wl.length > 7 ? `<a href="#/watchlist">${wl.length} stocks</a>` : "Today", rows, "", true);
}
const sectorRows = (M) => SECT.map(([l]) => [l, M["SEC::" + l]]).filter(([, m]) => m).sort((a, b) => (ok(b[1].chg) ? b[1].chg : -99) - (ok(a[1].chg) ? a[1].chg : -99));
function sectors(M) {
  const rows = sectorRows(M); if (!rows.length) return empty("Sector indices are unavailable right now.");
  const tsym = (l) => SECT.find((x) => x[0] === l)[1][0];
  const table = `<table class="hm-t hm-hm"><thead><tr><th></th><th class="l">Sector</th><th>Last</th><th>1D</th><th>1W</th><th>1M</th><th>YTD</th><th>1M trend</th></tr></thead><tbody>${rows.map(([l, m], i) =>
    `<tr data-sym="${esc(tsym(l))}"><td class="rk">${i + 1}</td><td class="l">${esc(l)}</td><td>${num(m.price, 0)}</td>${[[m.chg, 2.5], [m.w1, 5], [m.m1, 9], [m.ytd, 25]].map(([v, lim]) => `<td class="c" style="background:${heat(v, lim)}">${pct(v, 1)}</td>`).join("")}<td>${spark(m.spark, 70, 20)}</td></tr>`).join("")}</tbody></table>`;
  const tiles = `<div class="hm-stiles">${rows.map(([l, m]) => `<button class="hm-stile" data-sym="${esc(tsym(l))}" style="background:${heat(m.chg, 2.5)}"><b>${esc(l)}</b><span>${pct(m.chg, 2)}</span><small>1W ${pct(m.w1, 1)} · 1M ${pct(m.m1, 1)}</small></button>`).join("")}</div>`;
  return `<div class="only-d hm-tw">${table}</div><div class="only-m">${tiles}</div>`;
}
function movers(k = 5) {
  const ranked = N50.map((s) => [s, S.q[s]]).filter(([, q]) => q && q.ok && ok(q.pct)).sort((a, b) => b[1].pct - a[1].pct);
  if (!ranked.length) return empty("NIFTY 50 quotes are loading.");
  const col = (items, title) => `<div><div class="hm-lg">${title}</div>${items.length ? items.map(([s, q]) => `<button class="hm-li" data-sym="${esc(s)}"><span class="a"><b>${esc(s)}</b><small>₹${num(q.price, 2)}</small></span><span class="z"><b class="${cl(q.pct)}">${pct(q.pct)}</b></span></button>`).join("") : empty("None today.")}</div>`;
  return `<div class="hm-mv">${col(ranked.slice(0, k).filter(([, q]) => q.pct > 0), "Top gainers")}${col(ranked.slice().reverse().slice(0, k).filter(([, q]) => q.pct < 0), "Top losers")}</div>`;
}
function rotation(M) {                                       // Money flow's ranking when the nightly file is there, else 1-month return against NIFTY 50
  const fm = S.hm.meta && S.hm.meta.flow;
  if (fm && fm.top && fm.top.length) {
    const c = { "Rotate in": GREEN, Hold: "#8AD66B" };
    return [`From Money flow · ${nice(fm.asof).replace(/ \d{4}$/, "")} close`, fm.top.slice(0, 6).map((x) => `<a class="hm-li" href="#/flow"><span class="a"><b>${esc(x.sector)}</b><small>Model portfolio · market ${esc(fm.regime || "")}</small></span><span class="z"><span class="hm-pill" style="--c:${c[x.action] || T2}">${esc(x.action)}</span></span></a>`).join("")];
  }
  const n = M["NIFTY 50"], rows = SECT.map(([l]) => [l, M["SEC::" + l]]).filter(([, m]) => m && ok(m.m1));
  if (!n || !ok(n.m1) || rows.length < 6) return ["", empty("Sector rotation appears once the sector indices load.")];
  const rel = rows.map(([l, m]) => [l, m.m1 - n.m1, m]).sort((a, b) => b[1] - a[1]);
  const row = ([l, d, m], lead) => { const w1 = ok(m.w1) && ok(n.w1) ? m.w1 - n.w1 : null;
    const [t, c] = lead ? (w1 == null || w1 >= 0 ? ["Leading", GREEN] : ["Cooling", YELLOW]) : (w1 == null || w1 <= 0 ? ["Lagging", RED] : ["Turning up", CYAN]);
    return `<div class="hm-li"><span class="a"><b>${esc(l)}</b><small>vs NIFTY 1M <span class="${cl(d)}">${pct(d, 1)}</span> · 1W <span class="${cl(w1)}">${pct(w1, 1)}</span></small></span><span class="z"><span class="hm-pill" style="--c:${c}">${t}</span></span></div>`; };
  return ["1-month return against NIFTY 50", '<div class="hm-lg">Stronger than NIFTY</div>' + rel.slice(0, 3).filter((x) => x[1] > 0).map((x) => row(x, true)).join("") + '<div class="hm-lg">Weaker than NIFTY</div>' + rel.slice().reverse().slice(0, 3).filter((x) => x[1] < 0).map((x) => row(x, false)).join("")];
}
function heatmap() {
  return `<div class="hm-imap">${Object.entries(NIFTY50).map(([g, syms]) => `<div class="hm-grp"><small>${esc(g)}</small><div class="hm-tiles">${syms.map((s) => { const q = S.q[s] || {};
    return `<button class="hm-tile${q.ok ? "" : " nq"}" data-sym="${esc(s)}" style="background:${heat(q.pct)}" title="${esc(s)} · ${q.ok ? "₹" + num(q.price, 2) : "no price"}"><b>${esc(s)}</b><span>${pct(q.pct, 1)}</span></button>`; }).join("")}</div></div>`).join("")}</div>
    <div class="hm-leg"><span>−3%</span><i></i><span>+3%</span><span style="margin-left:auto">Tap a stock to open it</span></div>`;
}
function qtable(spec, M, cols) {                             // global indices and cross-asset table (desktop)
  const rows = spec.map(([g, l, , k]) => [g, l, k, M[l]]).filter((x) => x[3]); if (!rows.length) return empty("Quotes are loading.");
  const newest = rows.map((x) => x[3].d).sort().pop(); let last = null;
  const cell = (m, v, yl) => (yl ? `<td class="${cl(ptsOf(m, v))}">${ok(v) ? bp(ptsOf(m, v)).replace(".0 bp", " bp") : "—"}</td>` : `<td class="${cl(v)}">${pct(v, 1)}</td>`);
  return `<table class="hm-t"><thead><tr>${cols.map((c, i) => `<th class="${i ? "" : "l"}">${c}</th>`).join("")}</tr></thead><tbody>${rows.map(([g, l, k, m]) => {
    const yl = k === "yld", head = g !== last ? `<tr class="g"><td colspan="${cols.length}">${esc(g)}</td></tr>` : ""; last = g;
    const stale = Math.round((dateOf(newest) - dateOf(m.d)) / 864e5) > 4, sym = spec.find((x) => x[1] === l)[2].find((t) => S.hm.ov[t] && S.hm.ov[t].ok);
    return head + `<tr class="${stale ? "st" : ""}" data-sym="${esc(sym)}"><td class="l" title="Last trade ${nice(m.d)}">${esc(l)}</td><td>${px(m.price, k)}</td>${yl ? `<td class="${cl(m.pts)}">${bp(m.pts)}</td>` : cell(m, m.chg)}${cols.includes("1W") ? cell(m, m.w1, yl) : ""}${cell(m, m.m1, yl)}${cell(m, m.ytd, yl)}${cols.includes("1M trend") ? `<td>${spark(m.spark, 72, 20)}</td>` : ""}</tr>`; }).join("")}</tbody></table>`;
}
function qlist(spec, M, skip = []) {                         // the same quotes as a list (phone)
  let last = null;
  return spec.map(([g, l, t, k]) => [g, l, t, k, M[l]]).filter((x) => x[4] && !skip.includes(x[0])).map(([g, l, t, k, m]) => {
    const head = g !== last ? `<div class="hm-lg">${esc(g)}</div>` : ""; last = g;
    const sym = t.find((x) => S.hm.ov[x] && S.hm.ov[x].ok);
    return head + `<button class="hm-li" data-sym="${esc(sym)}"><span class="a"><b>${esc(l)}</b><small>1M ${pct(m.m1, 1)} · ${nice(m.d).replace(/ \d{4}$/, "")}</small></span><span class="z"><b>${px(m.price, k)}</b><small class="${cl(k === "yld" ? m.pts : m.chg)}">${k === "yld" ? bp(m.pts) : pct(m.chg)}</small></span></button>`; }).join("") || empty("Quotes are loading.");
}
function sessionCard(Ss) {
  const now = istNow(), c = Ss.cls === "live" ? GREEN : Ss.cls === "pre" ? AMBER : T2, today = iso2(now), sp = SPECIAL[today];
  const dd = (d) => { const k = Math.round((dateOf(d) - dateOf(today)) / 864e5); return fmtDay(d) + (k === 0 ? " · today" : k === 1 ? " · tomorrow" : ` · in ${k} days`); };
  const prog = Ss.progress != null ? `<div class="hm-prog"><i style="width:${(Math.max(0, Math.min(1, Ss.progress)) * 100).toFixed(1)}%"></i></div><div class="hm-ticks"><span>09:15</span><span>12:00</span><span>15:30</span></div>` : "";
  return card("NSE session", now.toLocaleDateString("en-IN", { weekday: "short", day: "2-digit", month: "short" }) + ` · ${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")} IST`,
    `<div class="hm-big" style="color:${c}">${esc(Ss.short)}</div><div class="hm-cap">${esc(Ss.detail)}</div>${prog}${sp ? `<div class="hm-cap" style="color:${AMBER}">${esc(sp)}</div>` : ""}
    <div class="hm-kvs">${kv("NIFTY weekly expiry", dd(nextExpiry(now, 2)))}${kv("Monthly F&amp;O expiry", dd(nextExpiry(now, 2, true)))}${kv("SENSEX weekly expiry", dd(nextExpiry(now, 4)))}</div>`);
}
function holidayCard() {
  const today = iso2(istNow()), h = upcomingHolidays(5, today);
  const rows = h.map(([d, nm, kind]) => { const k = Math.round((dateOf(d) - dateOf(today)) / 864e5); return kv(esc(nm.split(" · ")[0]) + (kind === "SPECIAL" ? " (special session)" : ""), `${fmtDay(d)} · ${k === 0 ? "today" : k === 1 ? "tomorrow" : "in " + k + " days"}`); }).join("");
  return card("Market holidays", "NSE equity segment", (rows ? `<div class="hm-kvs first">${rows}</div>` : empty("No more holidays listed this year. NSE publishes next year's list in late December.")) + `<div class="hm-kvs">${kv("Trading days left in " + today.slice(0, 4), tradingDaysLeft(today))}</div>`);
}
function worldClock() {
  return card("World markets", "Local time", WORLD.map(([name, tz, o, c]) => {
    let lt; try { lt = new Date(new Date().toLocaleString("en-US", { timeZone: tz })); } catch { return ""; }
    const t = lt.getHours() * 60 + lt.getMinutes(), open = tz === "Asia/Kolkata" ? session().cls === "live" : lt.getDay() >= 1 && lt.getDay() <= 5 && t >= o && t < c;
    return `<div class="hm-clk"><span>${esc(name)}</span><b>${lt.toLocaleDateString("en-IN", { weekday: "short" })} ${String(lt.getHours()).padStart(2, "0")}:${String(lt.getMinutes()).padStart(2, "0")}</b><span class="hm-pill" style="--c:${open ? GREEN : "#6B6B6B"}">${open ? "Open" : "Closed"}</span></div>`; }).join(""), "", true);
}

// ── the page ──
export function page() {
  resetCharts();
  const M = metrics(), Ss = session(), B = breadth(N50);
  let holds = [], realised = 0;
  if (S.pf.loaded && S.pf.lots.length && (S.pf.histState === "ready" || !S.pf.lots.length)) { try { const A = PFV.calc(); holds = A.holds; realised = A.realisedTotal; } catch { holds = []; } }
  const bookWait = S.pf.loaded && S.pf.lots.length && S.pf.histState !== "ready";
  const upN = SECT.map(([l]) => M["SEC::" + l]).filter((m) => m && ok(m.chg)), up = upN.filter((m) => m.chg > 0.05).length;
  const [rotR, rotB] = rotation(M), hasBook = holds.length || S.wl.rows.length || bookWait;
  const book = bookWait ? `<div class="hm-g c75">${card("Holdings", "", '<div class="sk" style="height:180px;border-radius:12px"></div>', "", true)}${watchCard()}</div>`
    : hasBook ? `<div class="hm-g c75">${holdingsCard(holds, realised)}${watchCard()}</div>`
    : `<div class="hm-c"><div class="hm-empty" style="padding:16px"><b>Make this page yours.</b> Add your buys in Portfolio and the stocks you are stalking in Watchlist: your profit for the day, stops and prices then sit at the top of this page.</div></div>`;
  const nav = `<nav class="hm-nav only-m" aria-label="Home sections">${SECTIONS.map(([id, t]) => `<button class="${id === (S.hm.sec || SECTIONS[0][0]) ? "on" : ""}" data-act="hmgo" data-v="${id}">${t}</button>`).join("")}</nav>`;
  return `<div class="hm">${pulse(M)}${nav}<div id="hm-today">${header(M, B, Ss)}${bookBar(holds)}</div>
    <div class="hm-g c75">${hero(M, Ss)}${health(M, B)}</div>${bench(M)}${desk()}
    ${sec("hm-book", "Your book", "holdings and the stocks you are watching")}${book}
    ${sec("hm-lead", "Leadership", "sectors and stocks moving the market today")}
    <div class="hm-g c75">${card("Sectors", upN.length ? `${up} of ${upN.length} up · ranked by today` : "", sectors(M), "", true)}<div class="hm-stack">${card("Top movers", "NIFTY 50 · today", movers(), "", true)}${card("Sector rotation", rotR, rotB, "", true)}</div></div>
    ${card("NIFTY 50 heatmap", B.n ? `${B.adv} up · ${B.flat} flat · ${B.dec} down` : "", heatmap(), "hm-wide", true)}
    ${sec("hm-world", "World and macro", "global indices, commodities, currencies and rates")}
    <div class="hm-g c75">${card("Global indices", "Local currency", `<div class="only-d hm-tw">${qtable(WEI, M, ["Index", "Last", "1D", "1W", "1M", "YTD", "1M trend"])}</div><div class="only-m">${qlist(WEI, M, ["India"])}</div>`, "", true)}
      ${card("Commodities, currencies and rates", "", `<div class="only-d hm-tw">${qtable(XASSET, M, ["Instrument", "Last", "1D", "1M", "YTD"])}</div><div class="only-m">${qlist(XASSET, M)}</div><div class="hm-empty hm-fine">Yields move in basis points (100 bp = 1%). For rupee pairs a rise means a weaker rupee.</div>`, "", true)}</div>
    ${sec("hm-cal", "Calendar", "session, expiries, holidays and world market hours")}
    <div class="hm-g c3">${sessionCard(Ss)}${holidayCard()}${worldClock()}</div>
    <div class="note" style="padding:16px 2px">Prices from Yahoo Finance, delayed; they can trail the exchange by a few minutes. Market mood from Tickertape. Information, not investment advice.</div></div>`;
}
export function go(id) {                                     // phone section bar: jump to a section
  const el = document.getElementById(id); if (!el) return;
  const nav = document.querySelector(".hm-nav"), off = (nav ? nav.offsetHeight : 0) + 64;
  S.hm.sec = id; if (nav) nav.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === id));
  window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - off, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
}
export function spy() {                                      // light up the section in view
  const nav = document.querySelector(".hm-nav"); if (!nav || nav.offsetParent === null) return;
  let cur = SECTIONS[0][0];
  for (const [id] of SECTIONS) { const el = document.getElementById(id); if (el && el.getBoundingClientRect().top < 160) cur = id; }
  S.hm.sec = cur;
  nav.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === cur));
}

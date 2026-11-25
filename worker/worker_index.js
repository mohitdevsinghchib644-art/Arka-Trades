// Arka Trades · API (stage 8: adds the stock page and research sources: Screener, Yahoo company details, BSE filings, NSE deliveries, notes). Runs on Cloudflare for every request to /api/*.
// Secrets are read by NAME from the Cloudflare project settings and never sent to the browser:
//   ADMIN_USER, ADMIN_PASS, MEMBER_USER, MEMBER_PASS, AUTH_SECRET   sign-in (same names and values as in Streamlit)
//   SUPABASE_URL, SUPABASE_KEY                                      watchlist storage (same as in Streamlit)
// Optional plain variable: GITHUB_REPO (default below) for the signal files written by the "Signals" workflow.
// Only public market data leaves this worker (Yahoo Finance). Nothing here touches a broker account.

const REPO = "mohitdevsinghchib644-art/Arka-Trades";
const COOKIE = "arka_s";
const DAYS = 30;
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36";
const enc = new TextEncoder();

const json = (data, status = 200, headers = {}) =>
  new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", ...headers } });
const fail = (status, message) => json({ error: message }, status);

// ── sign-in: a signed cookie, nothing stored on the server ──
const b64u = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const unb64u = (s) => Uint8Array.from(atob(s.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
async function hmac(secret, text) {
  const key = await crypto.subtle.importKey("raw", enc.encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  return b64u(await crypto.subtle.sign("HMAC", key, enc.encode(text)));
}
function same(a, b) {                                   // comparison that does not stop at the first difference
  a = String(a); b = String(b);
  let diff = a.length ^ b.length;
  for (let i = 0; i < Math.max(a.length, b.length); i++) diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  return diff === 0;
}
async function makeToken(env, role, user) {
  const body = b64u(enc.encode(JSON.stringify({ r: role, u: user, x: Date.now() + DAYS * 864e5 })));
  return body + "." + (await hmac(env.AUTH_SECRET, body));
}
async function readToken(env, request) {
  const m = (request.headers.get("cookie") || "").match(new RegExp("(?:^|;\\s*)" + COOKIE + "=([^;]+)"));
  if (!m || !env.AUTH_SECRET) return null;
  const [body, sig] = m[1].split(".");
  if (!body || !sig || !same(sig, await hmac(env.AUTH_SECRET, body))) return null;
  try {
    const p = JSON.parse(new TextDecoder().decode(unb64u(body)));
    return p.x > Date.now() && (p.r === "admin" || p.r === "member") ? { role: p.r, user: p.u } : null;
  } catch { return null; }
}
const cookie = (value, maxAge) => `${COOKIE}=${value}; Max-Age=${maxAge}; Path=/; HttpOnly; Secure; SameSite=Lax`;

async function login(env, request) {
  if (!env.AUTH_SECRET || String(env.AUTH_SECRET).length < 16) return fail(503, "Sign-in is not set up yet: add AUTH_SECRET in the Cloudflare project settings.");
  if (!(env.ADMIN_PASS || env.MEMBER_PASS)) return fail(503, "Sign-in is not set up yet: add ADMIN_USER and ADMIN_PASS in the Cloudflare project settings.");
  let b = {};
  try { b = await request.json(); } catch { /* empty body */ }
  const u = String(b.user || "").trim(), p = String(b.pass || "").trim();
  let role = null;
  if (env.ADMIN_USER && env.ADMIN_PASS && same(u, env.ADMIN_USER) && same(p, env.ADMIN_PASS)) role = "admin";
  else if (env.MEMBER_USER && env.MEMBER_PASS && same(u.toLowerCase(), String(env.MEMBER_USER).toLowerCase()) && same(p.toLowerCase(), String(env.MEMBER_PASS).toLowerCase())) role = "member";
  if (!role) return fail(401, "That user name or password is not right.");
  return json({ role, user: u }, 200, { "set-cookie": cookie(await makeToken(env, role, u), DAYS * 86400) });
}

// ── market data (Yahoo Finance, public) ──
const SYM_OK = /^[A-Z0-9^][A-Z0-9&._=\-^]{0,24}$/;
const ySym = (s) => (/[\^=.]|-USD$/.test(s) ? s : s + ".NS");          // plain names are NSE stocks; indices and currencies pass through
function thin(arr, n) {                                                 // at most n points, always keeping the last one
  const a = arr.filter((v) => v != null);
  if (a.length <= n) return a.map((v) => +v.toFixed(2));
  const out = [];
  for (let i = 0; i < n; i++) out.push(+a[Math.round((i * (a.length - 1)) / (n - 1))].toFixed(2));
  return out;
}
async function yahoo(path, ctx, ttl) {
  const url = "https://query1.finance.yahoo.com" + path;
  const cache = typeof caches !== "undefined" && caches.default;
  const key = new Request(url);
  if (cache) { const hit = await cache.match(key); if (hit) return hit.json(); }
  const r = await fetch(url, { headers: { "user-agent": UA, accept: "application/json" } });
  if (!r.ok) throw new Error("Yahoo answered " + r.status);
  const data = await r.json();
  if (cache) {
    const put = cache.put(key, new Response(JSON.stringify(data), { headers: { "content-type": "application/json", "cache-control": `max-age=${ttl}` } }));
    ctx && ctx.waitUntil ? ctx.waitUntil(put) : await put;
  }
  return data;
}
export function quoteFrom(sym, data) {
  const res = data && data.chart && data.chart.result && data.chart.result[0];
  if (!res || !res.meta) return { sym, ok: false };
  const m = res.meta, q0 = ((res.indicators || {}).quote || [{}])[0], closes = q0.close || [];
  const price = m.regularMarketPrice, prev = m.chartPreviousClose ?? m.previousClose;
  if (price == null) return { sym, ok: false };
  // today in 5-minute steps as [minutes since 09:15 IST, price] (the watchlist's chart column), every second bar and always the last one
  const ts = res.timestamp || [], off = m.gmtoffset ?? 19800, all = [];
  for (let i = 0; i < ts.length; i++) if (closes[i] != null) all.push([Math.floor((((ts[i] + off) % 86400) + 86400) % 86400 / 60) - 555, +closes[i].toFixed(2)]);
  const pts = all.filter((_, i) => i % 2 === 0 || i === all.length - 1), open = (q0.open || []).find((v) => v != null);
  return { sym, ok: true, name: m.longName || m.shortName || "", price, prev, chg: prev ? price - prev : null, pct: prev ? (price / prev - 1) * 100 : null,
           high: m.regularMarketDayHigh ?? null, low: m.regularMarketDayLow ?? null, vol: m.regularMarketVolume ?? null,
           hi52: m.fiftyTwoWeekHigh ?? null, lo52: m.fiftyTwoWeekLow ?? null, t: m.regularMarketTime ?? null, cur: m.currency || "", spark: thin(closes, 36), open: open != null ? +open.toFixed(2) : null, pts };
}
async function quotes(url, ctx) {
  const list = [...new Set((url.searchParams.get("s") || "").toUpperCase().split(",").map((s) => s.trim()).filter((s) => SYM_OK.test(s)))].slice(0, 40);
  const out = await Promise.all(list.map(async (sym) => {
    try { return quoteFrom(sym, await yahoo(`/v8/finance/chart/${encodeURIComponent(ySym(sym))}?range=1d&interval=5m&includePrePost=false`, ctx, 45)); }
    catch { return { sym, ok: false }; }
  }));
  return json({ quotes: out, at: Date.now() });
}
const ret = (c, n) => (c.length > n && c[c.length - 1 - n] ? (c[c.length - 1] / c[c.length - 1 - n] - 1) * 100 : null);
export function stockFrom(sym, day, hist) {
  const q = quoteFrom(sym, day);
  const res = hist && hist.chart && hist.chart.result && hist.chart.result[0];
  const c = res ? (((res.indicators || {}).quote || [{}])[0].close || []).filter((v) => v != null) : [];
  const avg = (n) => (c.length >= n ? c.slice(-n).reduce((a, b) => a + b, 0) / n : null);
  const a50 = avg(50), a200 = avg(200);
  return { ...q, r1w: ret(c, 5), r1m: ret(c, 21), r3m: ret(c, 63), r6m: ret(c, 126), r1y: ret(c, 252), a50, a200,
           trend: a50 == null || !q.ok ? null : q.price > a50 && (a200 == null || q.price > a200) ? "up" : q.price < a50 && (a200 == null || q.price < a200) ? "down" : "side",
           year: thin(c, 120) };
}
async function stock(url, ctx) {
  const sym = (url.searchParams.get("s") || "").toUpperCase().trim();
  if (!SYM_OK.test(sym)) return fail(400, "Unknown symbol.");
  const y = encodeURIComponent(ySym(sym));
  try {
    const [day, hist] = await Promise.all([yahoo(`/v8/finance/chart/${y}?range=1d&interval=5m&includePrePost=false`, ctx, 45),
                                           yahoo(`/v8/finance/chart/${y}?range=1y&interval=1d`, ctx, 900)]);
    return json(stockFrom(sym, day, hist));
  } catch (e) { return fail(502, "Prices are not available right now. " + e.message); }
}
// Daily candles with split and dividend history: what the Portfolio maths needs (growth chart, XIRR, risk, trailing stops, dividends).
const dayOf = (t, off) => new Date((t + off) * 1000).toISOString().slice(0, 10);
export function historyFrom(sym, data) {
  const res = data && data.chart && data.chart.result && data.chart.result[0];
  if (!res || !res.timestamp) return { sym, ok: false };
  const off = (res.meta && res.meta.gmtoffset) || 19800, q = ((res.indicators || {}).quote || [{}])[0], r2 = (v) => (v == null ? null : +v.toFixed(2));
  const rows = new Map();                               // one row per day, the last one wins; days with no close are dropped
  res.timestamp.forEach((t, i) => { const c = q.close && q.close[i]; if (c != null) rows.set(dayOf(t, off), [r2(c), r2(q.high && q.high[i]), r2(q.low && q.low[i]), (q.volume && q.volume[i]) || 0, r2(q.open && q.open[i])]); });
  const d = [...rows.keys()].sort(), col = (k) => d.map((x) => rows.get(x)[k]);
  const ev = res.events || {}, list = (o, f) => Object.values(o || {}).map(f).filter((x) => x[1] > 0).sort((a, b) => (a[0] < b[0] ? -1 : 1));
  return { sym, ok: d.length > 0, d, c: col(0), h: col(1), l: col(2), v: col(3), o: col(4),
           splits: list(ev.splits, (x) => [dayOf(x.date, off), x.denominator ? x.numerator / x.denominator : 0]),
           divs: list(ev.dividends, (x) => [dayOf(x.date, off), +Number(x.amount).toFixed(4)]) };
}
export function isDaily(d) {                              // true when the dates are trading days, not weeks or months (median gap of the last 60 rows)
  if (!d || d.length < 8) return true;
  const t = d.slice(-60).map((x) => Date.parse(x)), g = t.slice(1).map((x, i) => (x - t[i]) / 864e5).sort((a, b) => a - b);
  return g[Math.floor(g.length / 2)] <= 4;
}
export function giftFrom(j) {                              // NSE marketStatus → GIFT Nifty last price and change
  const g = j && (j.giftnifty || j.giftNifty); if (!g) return null;
  const n = (v) => { const x = Number(String(v ?? "").replace(/,/g, "")); return isFinite(x) ? x : null; }, price = n(g.LASTPRICE ?? g.lastPrice);
  if (!(price > 0)) return null;
  return { price, pts: n(g.DAYCHANGE ?? g.dayChange), chg: n(g.PERCHANGE ?? g.perChange), expiry: g.EXPIRYDATE || g.expiryDate || "", at: g.TIMESTMP || g.timestamp || "" };
}
async function gift(ctx) {                                 // GIFT Nifty futures (NSE IX), as NSE's own site shows it
  return json(await cached("gift", 60, async () => {
    const h = { "user-agent": UA, accept: "application/json,text/plain,*/*", "accept-language": "en-US,en;q=0.9", referer: "https://www.nseindia.com/" };
    let r = await fetch("https://www.nseindia.com/api/marketStatus", { headers: h });
    if (!r.ok) {                                           // NSE wants its cookies first
      const home = await fetch("https://www.nseindia.com/", { headers: { ...h, accept: "text/html" } });
      const ck = (home.headers.get("set-cookie") || "").split(/,(?=\s*[A-Za-z0-9_]+=)/).map((c) => c.split(";")[0].trim()).filter(Boolean).join("; ");
      r = await fetch("https://www.nseindia.com/api/marketStatus", { headers: { ...h, cookie: ck } });
    }
    if (!r.ok) throw new Error("NSE answered " + r.status);
    const g = giftFrom(await r.json()); if (!g) throw new Error("NSE sent no GIFT Nifty price");
    return { ok: true, ...g };
  }, ctx, 86400).catch((e) => ({ ok: false, err: e.message })));
}
async function history(url, ctx) {
  const list = [...new Set((url.searchParams.get("s") || "").toUpperCase().split(",").map((s) => s.trim()).filter((s) => SYM_OK.test(s)))].slice(0, 6);     // few per call: a free Worker has little CPU time per request
  const range = ["1y", "2y", "5y", "10y", "max"].includes(url.searchParams.get("range")) ? url.searchParams.get("range") : "5y";
  const out = await Promise.all(list.map(async (sym) => {
    // Yahoo answers range=max with one candle a month, so the whole history is asked for by dates; and any answer that is not daily is asked for again by dates
    const base = `/v8/finance/chart/${encodeURIComponent(ySym(sym))}?interval=1d&events=div%2Csplits&`, yrs = { "1y": 1, "2y": 2, "5y": 5, "10y": 10 }[range];
    const byDate = (from) => base + `period1=${from}&period2=9999999999`, start = yrs ? Math.floor(Date.now() / 864e5 - yrs * 365.25) * 86400 : 0;
    try {
      let h = historyFrom(sym, await yahoo(yrs ? base + "range=" + range : byDate(0), ctx, 1800));
      if (h.ok && !isDaily(h.d)) { const again = historyFrom(sym, await yahoo(byDate(start), ctx, 1800)); if (again.ok && (isDaily(again.d) || again.d.length > h.d.length)) h = again; }
      return { ...h, daily: h.ok ? isDaily(h.d) : false };
    } catch { return { sym, ok: false }; }
  }));
  return json({ history: out, at: Date.now() }, 200, { "cache-control": "private, max-age=600" });
}
// Home page: for each index, currency or commodity, the numbers the classic Home shows (home_page._metrics): last price,
// change, open, day range, yesterday's high and low, 1-week / 1-month / year-to-date change, 52-week range, a 1-month sparkline,
// and for the symbols in full= the year of closes (NIFTY chart, 200-day average, VIX percentile). One year of daily candles each.
export function overviewFrom(sym, data, full) {
  const res = data && data.chart && data.chart.result && data.chart.result[0];
  if (!res || !res.timestamp) return { sym, ok: false };
  const off = (res.meta && res.meta.gmtoffset) || 19800, q = ((res.indicators || {}).quote || [{}])[0], byDay = new Map();
  res.timestamp.forEach((t, i) => { const c = q.close && q.close[i]; if (c != null && c > 0) byDay.set(dayOf(t, off), { c, o: q.open && q.open[i], h: q.high && q.high[i], l: q.low && q.low[i] }); });
  const d = [...byDay.keys()].sort(), r = d.map((x) => byDay.get(x)), n = d.length;
  if (n < 2) return { sym, ok: false };
  const pos = (v) => (v != null && isFinite(v) && v > 0 ? v : null), last = r[n - 1], prv = r[n - 2], price = last.c, prev = prv.c, day = d[n - 1];
  const back = (days) => { const cut = new Date(Date.parse(day) - days * 864e5).toISOString().slice(0, 10); let v = null; for (let i = 0; i < n && d[i] <= cut; i++) v = r[i].c; return v; };
  const y0 = (() => { let v = null; for (let i = 0; i < n && d[i] < day.slice(0, 4) + "-01-01"; i++) v = r[i].c; return v; })();
  const yr = new Date(Date.parse(day) - 365 * 864e5).toISOString().slice(0, 10), tail = r.filter((_, i) => d[i] > yr).map((x) => x.c);
  const chg = (ref) => (ref ? (price / ref - 1) * 100 : null), w1 = back(7), m1 = back(30);
  const out = { sym, ok: true, d: day, price, prev, pts: price - prev, chg: chg(prev), open: pos(last.o), high: pos(last.h), low: pos(last.l), pdh: pos(prv.h), pdl: pos(prv.l),
    w1: chg(w1), m1: chg(m1), ytd: chg(y0), hi52: Math.max(...tail), lo52: Math.min(...tail), spark: r.slice(-23).map((x) => +x.c.toFixed(4)) };
  if (full) out.series = { d, c: r.map((x) => +x.c.toFixed(4)) };
  return out;
}
const marketHours = () => { const t = new Date(Date.now() + 330 * 60000), m = t.getUTCHours() * 60 + t.getUTCMinutes(); return t.getUTCDay() >= 1 && t.getUTCDay() <= 5 && m >= 540 && m <= 945; };
async function overview(url, ctx) {
  const pick = (k) => [...new Set((url.searchParams.get(k) || "").toUpperCase().split(",").map((x) => x.trim()).filter((x) => SYM_OK.test(x)))];
  const list = pick("s").slice(0, 15), full = new Set(pick("full")), ttl = marketHours() ? 60 : 900;
  const cache = typeof caches !== "undefined" && caches.default;
  const out = await Promise.all(list.map(async (sym) => {
    const key = new Request(`https://cache.arka/ov/${encodeURIComponent(sym)}?f=${full.has(sym) ? 1 : 0}`);
    if (cache) { const hit = await cache.match(key); if (hit) return hit.json(); }
    let o;
    try { o = overviewFrom(sym, await yahoo(`/v8/finance/chart/${encodeURIComponent(ySym(sym))}?range=1y&interval=1d&includePrePost=false`, ctx, ttl), full.has(sym)); }
    catch { o = { sym, ok: false }; }
    if (cache && o.ok) { const put = cache.put(key, new Response(JSON.stringify(o), { headers: { "cache-control": `max-age=${ttl}` } })); ctx && ctx.waitUntil ? ctx.waitUntil(put) : await put; }
    return o;
  }));
  return json({ overview: out, at: Date.now() });
}
// Market mood index from Tickertape (public page), as the classic Home reads it; the last good reading is kept for a week.
const MMI_ZONES = [[0, 30, "Extreme Fear"], [30, 50, "Fear"], [50, 70, "Greed"], [70, 100.0001, "Extreme Greed"]];
export function mmiFrom(html) {
  for (const m of String(html || "").matchAll(/(\d{1,3}\.\d{1,2})\s*(?:<[^>]+>\s*)*Updated/g)) {
    const v = parseFloat(m[1]);
    if (v >= 0 && v <= 100) return { score: Math.round(v * 100) / 100, zone: MMI_ZONES.find(([lo, hi]) => v >= lo && v < hi)[2] };
  }
  return null;
}
async function mmi(ctx) {
  const cache = typeof caches !== "undefined" && caches.default, liveKey = new Request("https://cache.arka/mmi/live"), lastKey = new Request("https://cache.arka/mmi/last");
  if (cache) { const hit = await cache.match(liveKey); if (hit) return json(await hit.json()); }
  let got = null;
  try { const r = await fetch("https://www.tickertape.in/market-mood-index", { headers: { "user-agent": UA, accept: "text/html" } }); if (r.ok) got = mmiFrom(await r.text()); } catch { /* unreachable */ }
  if (got) {
    const v = { status: "live", ...got, at: Date.now() };
    if (cache) { const a = cache.put(liveKey, new Response(JSON.stringify(v), { headers: { "cache-control": "max-age=900" } })), b = cache.put(lastKey, new Response(JSON.stringify(v), { headers: { "cache-control": "max-age=604800" } }));
      ctx && ctx.waitUntil ? ctx.waitUntil(Promise.all([a, b])) : await Promise.all([a, b]); }
    return json(v);
  }
  if (cache) { const old = await cache.match(lastKey); if (old) return json({ ...(await old.json()), status: "stale" }); }
  return json({ status: "unavailable" });
}
// ── news: one source per request (the browser asks for them in parallel and does the tagging and scoring) ──
// The sources of the classic news desk (news_feed.py v12.1). Only these addresses can be fetched.
export const WIRE = [["Economic Times", "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"], ["Economic Times", "https://economictimes.indiatimes.com/markets/stocks/news/rssfeeds/2146842.cms"],
  ["Business Standard", "https://www.business-standard.com/rss/markets-106.rss"], ["Business Standard", "https://www.business-standard.com/rss/companies-101.rss"],
  ["Mint", "https://www.livemint.com/rss/markets"], ["Mint", "https://www.livemint.com/rss/companies"], ["Moneycontrol", "https://www.moneycontrol.com/rss/marketreports.xml"],
  ["Moneycontrol", "https://www.moneycontrol.com/rss/business.xml"], ["BusinessLine", "https://www.thehindubusinessline.com/markets/feeder/default.rss"], ["NDTV Profit", "https://feeds.feedburner.com/ndtvprofit-latest"]];
export const NSE_FEEDS = [["Announcements", "https://nsearchives.nseindia.com/content/RSS/Online_announcements.xml"], ["Financial results", "https://nsearchives.nseindia.com/content/RSS/Financial_Results.xml"],
  ["Board meetings", "https://nsearchives.nseindia.com/content/RSS/Board_Meetings.xml"], ["Corporate actions", "https://nsearchives.nseindia.com/content/RSS/Corporate_action.xml"],
  ["Insider trading", "https://nsearchives.nseindia.com/content/RSS/Insider_Trading.xml"]];
const BORING = /trading window|74\s?\(5\)|loss of share|duplicate share|newspaper (publication|advertisement)|compliance certificate|closure of trading|investor (meet|presentation) intimation|book closure|change in (address|registrar)|copy of newspaper|postal ballot|esop|esos|analysts?.{0,25}meet|investors?.{0,12}meet|certificate under sebi|record of proceedings/i;
const ENT = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ", rsquo: "’", lsquo: "‘", rdquo: "”", ldquo: "“", ndash: "–", mdash: "—", hellip: "…", rupee: "₹" };
const unent = (s) => String(s || "").replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (m, e) => (e[0] === "#" ? String.fromCodePoint(e[1] === "x" || e[1] === "X" ? parseInt(e.slice(2), 16) : +e.slice(1)) : ENT[e.toLowerCase()] ?? m));
export function cleanHtml(s, n = 260) {                     // news_feed._clean_html
  let t = String(s || "").replace(/<[^>]+>/g, " ");
  t = unent(t.replace(/\s+/g, " ")).replace(/\s+/g, " ").trim();
  return t.length > n ? t.slice(0, n) + "…" : t;
}
const MON = { jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5, jul: 6, aug: 7, sep: 8, oct: 9, nov: 10, dec: 11 };
export function parseDate(raw) {                            // RFC 822 dates, ISO dates, and NSE's "22-Nov-2026 18:30:00" (Indian time)
  if (!raw) return null;
  const s = String(raw).trim(), m = s.match(/^(\d{1,2})-([A-Za-z]{3})-(\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$/);
  if (m && MON[m[2].toLowerCase()] != null) return Date.UTC(+m[3], MON[m[2].toLowerCase()], +m[1], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0)) - 330 * 60000;
  const t = Date.parse(s.replace(/\bIST\b/, "+0530"));
  return isNaN(t) ? null : t;
}
const tagText = (xml, tag) => { const m = xml.match(new RegExp(`<${tag}(?:\\s[^>]*)?>([\\s\\S]*?)</${tag}>`, "i")); return m ? m[1].replace(/^\s*<!\[CDATA\[([\s\S]*?)\]\]>\s*$/, "$1") : ""; };
export function parseFeed(xml, max = 40) {                  // RSS 2.0 and Atom, the fields the classic desk reads
  const out = [], items = String(xml || "").match(/<item[\s>][\s\S]*?<\/item>|<entry[\s>][\s\S]*?<\/entry>/gi) || [];
  for (const it of items.slice(0, max)) {
    let link = tagText(it, "link").trim();
    if (!link) { const a = it.match(/<link[^>]*href="([^"]+)"/i); link = a ? a[1] : ""; }
    const src = tagText(it, "source");
    out.push({ title: unent(tagText(it, "title").replace(/<[^>]+>/g, "")).trim(), link: unent(link), summary: cleanHtml(unent(tagText(it, "description") || tagText(it, "summary") || tagText(it, "content"))),
      dt: parseDate(tagText(it, "pubDate") || tagText(it, "published") || tagText(it, "updated") || tagText(it, "dc:date")), src: src ? unent(src).trim() : null });
  }
  return out;
}
export function nseRows(xml, label) {                       // one NSE filing feed (news_feed._nse_cached)
  const out = [];
  for (const it of (String(xml || "").match(/<item[\s>][\s\S]*?<\/item>/gi) || []).slice(0, 150)) {
    const title = cleanHtml(unent(tagText(it, "title")), 300), desc = cleanHtml(unent(tagText(it, "description")), 300);
    let name, head;
    if (title.includes(" - ")) { const i = title.indexOf(" - "); name = title.slice(0, i).trim(); head = title.slice(i + 3).trim(); } else { name = title.trim(); head = desc; }
    if (!name || BORING.test(`${head} ${desc}`)) continue;
    const dt = parseDate(tagText(it, "pubDate") || tagText(it, "published")); if (dt == null) continue;
    out.push({ title: `${name}: ${head || label}`.slice(0, 240), link: unent(tagText(it, "link").trim()) || "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
      summary: desc && desc !== head ? desc : label, dt, src: "NSE filing", company: name });
  }
  return out;
}
export function bseRows(data) {                             // BSE corporate announcements (news_feed._bse_cached)
  const out = [];
  for (const x of ((data || {}).Table || []).slice(0, 200)) {
    const name = String(x.SLONGNAME || "").trim(), head = String(x.HEADLINE || x.NEWSSUB || "").trim();
    if (!name || !head || BORING.test(head) || BORING.test(String(x.SUBCATNAME || ""))) continue;
    const raw = String(x.DT_TM || x.NEWS_DT || ""), dt = Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(raw) ? raw : raw + "+05:30"); if (isNaN(dt)) continue;
    const att = String(x.ATTACHMENTNAME || "").trim(), title = name.toLowerCase().replace(/(^|[^a-z0-9])([a-z])/g, (m, a, b) => a + b.toUpperCase());
    out.push({ title: `${title}: ${head}`.slice(0, 240), link: att ? `https://www.bseindia.com/xml-data/corpfiling/AttachLive/${att}` : "https://www.bseindia.com/corporates/ann.html",
      summary: String(x.CATEGORYNAME || x.SUBCATNAME || ""), dt, src: "BSE filing", company: name });
  }
  return out;
}
const gnewsUrl = (q, days) => `https://news.google.com/rss/search?q=${encodeURIComponent(`${q} when:${Math.max(1, days | 0)}d`).replace(/%20/g, "+")}&hl=en-IN&gl=IN&ceid=IN:en`;
async function feed(url, ctx) {
  const id = url.searchParams.get("id") || "", days = Math.max(1, Math.min(30, +url.searchParams.get("days") || 1));
  let target, kind = "rss", label = "", headers = { "user-agent": UA, accept: "application/rss+xml,application/xml,text/xml,*/*" };
  const [k, v] = [id.split(":")[0], id.slice(id.indexOf(":") + 1)];
  if (k === "wire" && WIRE[+v]) target = WIRE[+v][1];
  else if (k === "nse" && NSE_FEEDS[+v]) { target = NSE_FEEDS[+v][1]; kind = "nse"; label = NSE_FEEDS[+v][0]; headers.referer = "https://www.nseindia.com/"; }
  else if (k === "gnews") { const q = (url.searchParams.get("q") || "").slice(0, 300); if (!q) return fail(400, "Which query?"); target = gnewsUrl(q, days); }
  else if (k === "bse") { const t = new Date(Date.now() + 330 * 60000), f = new Date(t.getTime() - Math.min(days, 7) * 864e5), ymd = (d) => d.toISOString().slice(0, 10).replace(/-/g, "");
    target = `https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?pageno=1&strCat=-1&strPrevDate=${ymd(f)}&strScrip=&strSearch=P&strToDate=${ymd(t)}&strType=C&subcategory=-1`;
    kind = "bse"; headers = { "user-agent": UA, referer: "https://www.bseindia.com/", origin: "https://www.bseindia.com", accept: "application/json, text/plain, */*" }; }
  else return fail(400, "Unknown source.");
  const cache = typeof caches !== "undefined" && caches.default, key = new Request("https://cache.arka/feed/" + encodeURIComponent(target)), last = new Request("https://cache.arka/feedlast/" + encodeURIComponent(target));
  if (cache) { const hit = await cache.match(key); if (hit) return json(await hit.json()); }
  try {
    const r = await fetch(target, { headers, signal: AbortSignal.timeout ? AbortSignal.timeout(7000) : undefined });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const rows = kind === "bse" ? bseRows(await r.json()) : kind === "nse" ? nseRows(await r.text(), label) : parseFeed(await r.text());
    if (!rows.length) throw new Error(kind === "rss" ? "empty feed" : "no filings");
    const v2 = { ok: true, rows, at: Date.now() };
    if (cache) { const a = cache.put(key, new Response(JSON.stringify(v2), { headers: { "cache-control": "max-age=300" } })), b = cache.put(last, new Response(JSON.stringify(v2), { headers: { "cache-control": "max-age=21600" } }));
      ctx && ctx.waitUntil ? ctx.waitUntil(Promise.all([a, b])) : await Promise.all([a, b]); }
    return json(v2);
  } catch (e) {                                            // a failed source: say so, and hand back its last good copy (up to 6 hours old)
    const old = cache ? await cache.match(last) : null, prev = old ? await old.json() : null;
    return json({ ok: false, err: String(e && e.name === "TimeoutError" ? "Timeout" : (e && e.message) || "failed").slice(0, 60), rows: prev ? prev.rows : [], at: prev ? prev.at : null });
  }
}
// ── research: the sources the classic Research terminal reads (research_page.py, screener_scraper.py, research_data.py) ──
// The pages are passed to the browser almost as they come; the browser parses them (cheap here, and the Worker stays fast).
const SCREENER = "https://www.screener.in";
const HTML_HEAD = { "user-agent": UA, accept: "text/html,application/xhtml+xml" };
async function cached(key, ttl, make, ctx, lastTtl = 3 * 86400) {      // a fresh copy for ttl, the last good copy for lastTtl when the source fails
  const cache = typeof caches !== "undefined" && caches.default, k = new Request("https://cache.arka/" + key), l = new Request("https://cache.arka/last/" + key);
  if (cache) { const hit = await cache.match(k); if (hit) return { ...(await hit.json()), cached: true }; }
  try {
    const v = await make();
    if (cache) { const body = JSON.stringify(v), a = cache.put(k, new Response(body, { headers: { "cache-control": `max-age=${ttl}` } })), b = cache.put(l, new Response(body, { headers: { "cache-control": `max-age=${lastTtl}` } }));
      ctx && ctx.waitUntil ? ctx.waitUntil(Promise.all([a, b])) : await Promise.all([a, b]); }
    return v;
  } catch (e) {
    const old = cache ? await cache.match(l) : null;
    if (old) return { ...(await old.json()), stale: true, err: String(e.message || e).slice(0, 80) };
    throw e;
  }
}
const slim = (html) => String(html).replace(/<script[\s\S]*?<\/script>/gi, "").replace(/<style[\s\S]*?<\/style>/gi, "").replace(/<svg[\s\S]*?<\/svg>/gi, "").replace(/<!--[\s\S]*?-->/g, "");
async function screener(url, ctx) {                     // screener_scraper.resolve_symbol + one page fetch
  const sym = cleanSym(url.searchParams.get("s"));
  if (!sym) return fail(400, "Which stock?");
  try {
    const v = await cached("screener/" + sym, 6 * 3600, async () => {
      const get = async (u) => { const r = await fetch(u, { headers: HTML_HEAD, redirect: "follow" }); return r.ok ? r.text() : null; };
      for (const path of [`/company/${encodeURIComponent(sym)}/consolidated/`, `/company/${encodeURIComponent(sym)}/`]) {
        const html = await get(SCREENER + path);
        if (html && html.includes("Screener")) return { ok: true, url: SCREENER + path, html: slim(html), at: Date.now() };
      }
      const r = await fetch(`${SCREENER}/api/company/search/?q=${encodeURIComponent(sym)}`, { headers: { ...HTML_HEAD, accept: "application/json" } });
      const first = r.ok ? ((await r.json()) || [])[0] : null;
      if (first && first.url) { let u = SCREENER + first.url; if (!u.endsWith("/")) u += "/"; const html = await get(u); if (html) return { ok: true, url: u, html: slim(html), at: Date.now() }; }
      throw new Error("not found on Screener");
    }, ctx);
    return json(v);
  } catch (e) { return json({ ok: false, err: "Screener unavailable right now (" + String(e.message || e).slice(0, 60) + ")" }); }
}
async function screenerPeers(url, ctx) {                // data_engine._peers_cached: Screener's peer table for one company id
  const id = String(url.searchParams.get("id") || "");
  if (!/^\d{1,9}$/.test(id)) return fail(400, "Which company?");
  try {
    return json(await cached("peers/" + id, 6 * 3600, async () => {
      const r = await fetch(`${SCREENER}/api/company/${id}/peers/`, { headers: { ...HTML_HEAD, "x-requested-with": "XMLHttpRequest", referer: SCREENER + "/" } });
      const html = r.ok ? await r.text() : "";
      if (!/<table/i.test(html)) throw new Error("no peer table");
      return { ok: true, html };
    }, ctx));
  } catch (e) { return json({ ok: false, err: String(e.message || e).slice(0, 80) }); }
}
// Yahoo company details: profile, analyst targets and ratings, rating changes, next results date, and the yearly statement lines
// the classic page reads through yfinance (info, analyst_price_targets, recommendations, upgrades_downgrades, balance_sheet, cashflow).
async function yahooAuth(ctx) {
  const cache = typeof caches !== "undefined" && caches.default, key = new Request("https://cache.arka/yahoo-crumb");
  if (cache) { const hit = await cache.match(key); if (hit) return hit.json(); }
  const r = await fetch("https://fc.yahoo.com/", { headers: { "user-agent": UA }, redirect: "manual" });
  const ck = (r.headers.get("set-cookie") || "").split(/,(?=\s*[A-Za-z0-9_]+=)/).map((c) => c.split(";")[0].trim()).filter(Boolean).join("; ");
  const c = await fetch("https://query2.finance.yahoo.com/v1/test/getcrumb", { headers: { "user-agent": UA, cookie: ck } });
  const crumb = c.ok ? (await c.text()).trim() : "";
  if (!crumb || crumb.length > 40 || /</.test(crumb)) throw new Error("Yahoo sign-in token not given");
  const v = { ck, crumb };
  if (cache) { const put = cache.put(key, new Response(JSON.stringify(v), { headers: { "cache-control": "max-age=21600" } })); ctx && ctx.waitUntil ? ctx.waitUntil(put) : await put; }
  return v;
}
const rawv = (x) => (x && typeof x === "object" && "raw" in x ? x.raw : x && typeof x === "object" && "fmt" in x ? x.fmt : x);
export function yinfoFrom(qs, ts) {
  const R = ((qs || {}).quoteSummary || {}).result || [], m = R[0] || {}, ap = m.assetProfile || {}, fd = m.financialData || {}, ce = m.calendarEvents || {}, sd = m.summaryDetail || {};
  const info = { longBusinessSummary: ap.longBusinessSummary || null, fullTimeEmployees: rawv(ap.fullTimeEmployees) ?? null, sector: ap.sector || null, industry: ap.industry || null,
    companyOfficers: (ap.companyOfficers || []).map((o) => ({ name: o.name, title: o.title, age: rawv(o.age) ?? null, totalPay: rawv(o.totalPay) ?? null })),
    targetMeanPrice: rawv(fd.targetMeanPrice) ?? null, targetHighPrice: rawv(fd.targetHighPrice) ?? null, targetLowPrice: rawv(fd.targetLowPrice) ?? null, targetMedianPrice: rawv(fd.targetMedianPrice) ?? null,
    currentPrice: rawv(fd.currentPrice) ?? null, numberOfAnalystOpinions: rawv(fd.numberOfAnalystOpinions) ?? null, recommendationKey: fd.recommendationKey || null,
    totalDebt: rawv(fd.totalDebt) ?? null, totalCash: rawv(fd.totalCash) ?? null, ebitda: rawv(fd.ebitda) ?? null, dividendYield: rawv(sd.dividendYield) ?? null };
  const targets = info.targetMeanPrice != null ? { current: info.currentPrice, high: info.targetHighPrice, low: info.targetLowPrice, mean: info.targetMeanPrice, median: info.targetMedianPrice } : {};
  const rec = ((m.recommendationTrend || {}).trend || []).map((t) => ({ period: t.period, strongBuy: t.strongBuy, buy: t.buy, hold: t.hold, sell: t.sell, strongSell: t.strongSell }));
  const ud = ((m.upgradeDowngradeHistory || {}).history || []).map((h) => ({ date: new Date((rawv(h.epochGradeDate) || 0) * 1000).toISOString().slice(0, 10), firm: h.firm || "", to: h.toGrade || "", from: h.fromGrade || "", action: h.action || "" }))
    .sort((a, b) => (a.date < b.date ? 1 : -1));
  const ed = ((ce.earnings || {}).earningsDate || []).map(rawv).filter(Boolean).map((t) => new Date(t * 1000).toISOString().slice(0, 10)).sort();
  const today = new Date(Date.now() + 330 * 60000).toISOString().slice(0, 10), next = ed.find((d) => d >= today) || null;
  // yearly statement lines (fundamentals timeseries), newest year first, as yfinance's frames
  const S = {}; for (const x of ((ts || {}).timeseries || {}).result || []) { const t = (x.meta || {}).type && x.meta.type[0]; if (!t || !x[t]) continue;
    S[t] = x[t].filter(Boolean).map((p) => [p.asOfDate, rawv(p.reportedValue)]).sort((a, b) => (a[0] < b[0] ? 1 : -1)); }
  const col = (t) => (S[t] || []).map((p) => p[1]);
  const stmt = {};
  const frame = (pairs) => { const cols = [...new Set(pairs.flatMap(([, t]) => (S[t] || []).map((p) => p[0])))].sort().reverse();
    const rows = pairs.filter(([, t]) => S[t] && S[t].length).map(([lab, t]) => [lab, cols.map((c) => { const hit = S[t].find((p) => p[0] === c); return hit ? hit[1] : null; })]);
    return rows.length ? { cols, rows } : null; };
  stmt.income = frame([["Total Revenue", "annualTotalRevenue"], ["EBITDA", "annualEBITDA"], ["Net Income", "annualNetIncome"], ["Diluted EPS", "annualDilutedEPS"]]);
  stmt.qincome = frame([["Total Revenue", "quarterlyTotalRevenue"], ["EBITDA", "quarterlyEBITDA"], ["Net Income", "quarterlyNetIncome"], ["Diluted EPS", "quarterlyDilutedEPS"]]);
  stmt.balance = frame([["Total Assets", "annualTotalAssets"], ["Current Assets", "annualCurrentAssets"], ["Current Liabilities", "annualCurrentLiabilities"], ["Total Debt", "annualTotalDebt"], ["Stockholders Equity", "annualStockholdersEquity"]]);
  stmt.cashflow = frame([["Operating Cash Flow", "annualOperatingCashFlow"], ["Capital Expenditure", "annualCapitalExpenditure"], ["Free Cash Flow", "annualFreeCashFlow"]]);
  const dOf = (x) => { const v = rawv(x); return typeof v === "number" ? new Date(v * 1000).toISOString().slice(0, 10) : null; };
  const cal = { earnings: ed, exdiv: dOf(ce.exDividendDate) || dOf(sd.exDividendDate), paid: dOf(ce.dividendDate) };
  return { ok: Boolean(R.length), info, targets, rec, ud, earnings: next, cal, bs: { ca: col("annualCurrentAssets"), cl: col("annualCurrentLiabilities") }, cf: { fcf: col("annualFreeCashFlow") }, stmt };
}
const TS_TYPES = ["annualTotalRevenue", "annualEBITDA", "annualNetIncome", "annualDilutedEPS", "quarterlyTotalRevenue", "quarterlyEBITDA", "quarterlyNetIncome", "quarterlyDilutedEPS", "annualTotalAssets",
  "annualCurrentAssets", "annualCurrentLiabilities", "annualTotalDebt", "annualStockholdersEquity", "annualOperatingCashFlow", "annualCapitalExpenditure", "annualFreeCashFlow"];
async function yinfo(url, ctx) {
  const sym = cleanSym(url.searchParams.get("s"));
  if (!sym) return fail(400, "Which stock?");
  try {
    return json(await cached("yinfo/" + sym, 6 * 3600, async () => {
      const a = await yahooAuth(ctx), y = encodeURIComponent(sym + ".NS"), h = { "user-agent": UA, cookie: a.ck, accept: "application/json" };
      const mods = "assetProfile,financialData,recommendationTrend,upgradeDowngradeHistory,calendarEvents,summaryDetail";
      const now = Math.floor(Date.now() / 1000);
      const [q, t] = await Promise.all([
        fetch(`https://query2.finance.yahoo.com/v10/finance/quoteSummary/${y}?modules=${mods}&crumb=${encodeURIComponent(a.crumb)}`, { headers: h }).then((r) => (r.ok ? r.json() : null)),
        fetch(`https://query2.finance.yahoo.com/ws/fundamentals-timeseries/v1/finance/timeseries/${y}?type=${TS_TYPES.join(",")}&period1=${now - 6 * 366 * 86400}&period2=${now}&crumb=${encodeURIComponent(a.crumb)}`, { headers: h }).then((r) => (r.ok ? r.json() : null)).catch(() => null)]);
      if (!q) throw new Error("Yahoo company details unavailable");
      return yinfoFrom(q, t);
    }, ctx));
  } catch (e) { return json({ ok: false, err: String(e.message || e).slice(0, 80) }); }
}
// BSE corporate filings for one company (research_data._bse_ann_cached): up to 3 pages of 40
export function bseFilings(pages) {
  const out = [];
  for (const data of pages) for (const x of (data || {}).Table || []) {
    const head = String(x.HEADLINE || x.NEWSSUB || "").trim(); if (!head) continue;
    const att = String(x.ATTACHMENTNAME || "").trim();
    out.push({ dt: String(x.DT_TM || x.NEWS_DT || "").slice(0, 16).replace("T", " "), title: head.slice(0, 300), category: String(x.CATEGORYNAME || "").trim() || "Other", sub: String(x.SUBCATNAME || "").trim(),
      url: att ? `https://www.bseindia.com/xml-data/corpfiling/AttachLive/${att}` : "https://www.bseindia.com/corporates/ann.html" });
  }
  return out;
}
async function bse(url, ctx) {
  const code = String(url.searchParams.get("code") || ""), days = Math.max(1, Math.min(365, +url.searchParams.get("days") || 365));
  if (!/^\d{6}$/.test(code)) return fail(400, "Which BSE code?");
  try {
    return json(await cached(`bse/${code}/${days}`, 900, async () => {
      const t = new Date(Date.now() + 330 * 60000), f = new Date(t.getTime() - days * 864e5), ymd = (d) => d.toISOString().slice(0, 10).replace(/-/g, "");
      const pages = [];
      for (const page of [1, 2, 3]) {
        const r = await fetch(`https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?pageno=${page}&strCat=-1&strPrevDate=${ymd(f)}&strScrip=${code}&strSearch=P&strToDate=${ymd(t)}&strType=C&subcategory=-1`,
          { headers: { "user-agent": UA, referer: "https://www.bseindia.com/", origin: "https://www.bseindia.com", accept: "application/json, text/plain, */*" } });
        if (!r.ok) throw new Error("BSE answered " + r.status);
        const d = await r.json(); pages.push(d);
        if (((d || {}).Table || []).length < 40) break;
      }
      return { ok: true, rows: bseFilings(pages) };
    }, ctx, 86400));
  } catch (e) { return json({ ok: false, err: String(e.message || e).slice(0, 80), rows: [] }); }
}
// 15-minute closes for the last 5 sessions (news_feed._intraday): how far a stock has moved since a story came out
async function intraday(url, ctx) {
  const sym = cleanSym(url.searchParams.get("s")); if (!sym) return fail(400, "Which stock?");
  try {
    const d = await yahoo(`/v8/finance/chart/${encodeURIComponent(ySym(sym))}?range=5d&interval=15m`, ctx, 300), res = d.chart.result[0], c = res.indicators.quote[0].close;
    return json({ ok: true, pts: res.timestamp.map((t, i) => [t * 1000, c[i]]).filter((p) => p[1] != null) });
  } catch { return json({ ok: false, pts: [] }); }
}
// NSDL fortnightly FPI investment by sector, one report per call (money_flow._nsdl_fetch): used by Money flow when the nightly job could not reach NSDL
const NSDL = "https://www.fpi.nsdl.co.in/web/StaticReports/Fortnightly_Sector_wise_FII_Investment_Data/";
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
export function nsdlUrls(day) {
  const [y, m, d] = day.split("-").map(Number), full = MONTHS[m - 1], ab = full.slice(0, 3), v = [full, ab, ab.toUpperCase(), full.toUpperCase()];
  if (m === 9) v.push("Sept", "SEPT");
  const dd = String(d).padStart(2, "0"), cdsl = "https://www.cdslindia.com/publications/FII/FortnightlySecWisePages/";
  return [...[...new Set(v)].map((x) => `${NSDL}FIIInvestSector_${x}${dd}${y}.html`),            // NSDL, as the classic page; then CDSL's copy of the same report
    `${cdsl}${encodeURIComponent(`${full} ${dd} ${y}`)}.html`, `${cdsl}${encodeURIComponent(`${full} ${dd}, ${y}`)}.html`];
}
async function fpiReport(url, ctx) {
  const day = String(url.searchParams.get("d") || "");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return fail(400, "Which fortnight?");
  try {
    return json(await cached("nsdl/" + day, 30 * 86400, async () => {
      for (const u of nsdlUrls(day)) {
        const r = await fetch(u, { headers: { "user-agent": UA, accept: "text/html" } }).catch(() => null);
        if (r && r.ok) { const t = await r.text(); if (t.includes("Sector")) return { ok: true, html: slim(t) }; }
      }
      throw new Error("no report for " + day);
    }, ctx, 60 * 86400));
  } catch (e) { return json({ ok: false, err: String(e.message || e).slice(0, 80) }); }
}
// NSE deliveries for one stock, saved each evening by the "Market data" job (market_export.py delivery)
async function dlv(env, ctx, url) {
  const sym = cleanSym(url.searchParams.get("s"));
  if (!sym) return fail(400, "Which stock?");
  const text = await repoFile(env, ctx, `dlv/${sym.replace(/&/g, "%26")}.json`, 3600, "market-data");
  return text == null ? json({ ok: false, err: "No delivery file for this stock yet" }) : raw(text);
}
export function dlvLast(j) {                              // watchlist_page._delivery_cached: the latest delivery % and the average of the 5 sessions before it
  if (!j || !j.d) return null;
  const rows = j.d.map((d, i) => [d, j.q[i] != null && j.v[i] > 0 ? (j.q[i] / j.v[i]) * 100 : null]).filter((r) => r[1] != null).slice(-6);
  if (!rows.length) return null;
  const prior = rows.slice(0, -1).map((r) => r[1]);
  return { pct: rows[rows.length - 1][1], date: rows[rows.length - 1][0], avg: prior.length ? prior.reduce((a, b) => a + b, 0) / prior.length : null };
}
async function dlvMany(env, ctx, url) {
  const list = [...new Set((url.searchParams.get("s") || "").split(",").map(cleanSym).filter(Boolean))].slice(0, 40), out = {};
  await Promise.all(list.map(async (sym) => { try { const t = await repoFile(env, ctx, `dlv/${sym.replace(/&/g, "%26")}.json`, 3600, "market-data"); const v = t == null ? null : dlvLast(JSON.parse(t)); if (v) out[sym] = v; } catch { /* no file */ } }));
  return json({ dlv: out });
}
export function calFrom(q, today) {                        // next results date and ex-dividend date (watchlist_page._calendar_one)
  const m = (((q || {}).quoteSummary || {}).result || [])[0] || {}, ce = m.calendarEvents || {}, sd = m.summaryDetail || {};
  const day = (v) => (typeof v === "number" ? new Date(v * 1000).toISOString().slice(0, 10) : null);
  const ed = ((ce.earnings || {}).earningsDate || []).map(rawv).map(day).filter(Boolean).sort();
  return { results: ed.find((d) => d >= today) || ed[0] || null, exdiv: day(rawv(ce.exDividendDate)) || day(rawv(sd.exDividendDate)) };
}
async function calMany(url, ctx) {
  const list = [...new Set((url.searchParams.get("s") || "").split(",").map(cleanSym).filter(Boolean))].slice(0, 8), out = {};
  let a = null; try { a = await yahooAuth(ctx); } catch { return json({ cal: out, err: "Yahoo calendar unavailable" }); }
  await Promise.all(list.map(async (sym) => {
    try { out[sym] = await cached("cal/" + sym, 12 * 3600, async () => {
      const r = await fetch(`https://query2.finance.yahoo.com/v10/finance/quoteSummary/${encodeURIComponent(sym + ".NS")}?modules=calendarEvents,summaryDetail&crumb=${encodeURIComponent(a.crumb)}`, { headers: { "user-agent": UA, cookie: a.ck, accept: "application/json" } });
      if (!r.ok) throw new Error("Yahoo answered " + r.status);
      return calFrom(await r.json(), today());
    }, ctx); } catch { /* filled on a later refresh */ }
  }));
  return json({ cal: out });
}
// thesis notes: the research_notes table the classic page uses (user "default", so both apps share the notes)
async function notes(env, request, url) {
  try {
    if (request.method === "GET") {
      const sym = cleanSym(url.searchParams.get("s")); if (!sym) return fail(400, "Which stock?");
      const rows = await sb(env, `research_notes?select=payload,updated_at&user_id=eq.default&symbol=eq.${encodeURIComponent(sym)}&limit=1`);
      return json({ note: rows && rows[0] ? rows[0] : null });
    }
    if (request.method === "POST") {
      let b = {}; try { b = await request.json(); } catch { /* empty */ }
      const sym = cleanSym(b.sym); if (!sym) return fail(400, "Which stock?");
      const p = b.payload || {}, payload = { stance: ["—", "Bullish", "Neutral", "Bearish"].includes(p.stance) ? p.stance : "—", tp: Math.max(0, +p.tp || 0), conv: Math.max(1, Math.min(5, Math.round(+p.conv || 3))) };
      for (const k of ["bull", "bear", "cat", "risk"]) payload[k] = String(p[k] || "").slice(0, 4000);
      await sb(env, "research_notes?on_conflict=user_id,symbol", { method: "POST", headers: { prefer: "resolution=merge-duplicates,return=minimal" },
        body: JSON.stringify([{ user_id: "default", symbol: sym, payload, updated_at: new Date().toISOString() }]) });
      return json({ ok: true, at: new Date().toISOString() });
    }
    return fail(405, "Not allowed.");
  } catch (e) { return fail(e.status === 503 ? 503 : 502, e.status === 503 ? e.message : "Notes could not be saved: check that the research_notes table exists (supabase_setup.sql)."); }
}

async function search(url, ctx) {
  const q = (url.searchParams.get("q") || "").trim().slice(0, 40);
  if (q.length < 2) return json({ results: [] });
  try {
    const d = await yahoo(`/v1/finance/search?q=${encodeURIComponent(q)}&quotesCount=12&newsCount=0&listsCount=0`, ctx, 3600);
    const results = (d.quotes || []).filter((x) => x.quoteType === "EQUITY" && /\.NS$/.test(x.symbol || ""))
      .map((x) => ({ sym: x.symbol.replace(/\.NS$/, ""), name: x.longname || x.shortname || "" })).slice(0, 8);
    return json({ results });
  } catch { return json({ results: [], error: "Search is not available right now." }); }
}

// ── watchlist (Supabase, the same table the Streamlit app uses) ──
const cleanSym = (s) => String(s || "").toUpperCase().replace(/\.NS$/, "").replace(/[^A-Z0-9&._-]/g, "").slice(0, 24);
const cleanList = (s) => String(s || "Main").replace(/[^\w &.-]/g, "").trim().slice(0, 24) || "Main";
async function sb(env, path, init = {}) {
  if (!env.SUPABASE_URL || !env.SUPABASE_KEY) throw Object.assign(new Error("Storage is not connected: add SUPABASE_URL and SUPABASE_KEY in the Cloudflare project settings."), { status: 503 });
  const r = await fetch(String(env.SUPABASE_URL).replace(/\/+$/, "") + "/rest/v1/" + path, {
    ...init, headers: { apikey: env.SUPABASE_KEY, authorization: "Bearer " + env.SUPABASE_KEY, "content-type": "application/json", prefer: "return=minimal", ...(init.headers || {}) } });
  const text = await r.text();
  if (!r.ok) throw Object.assign(new Error(text.slice(0, 200) || "Storage error " + r.status), { status: r.status, body: text });
  return text ? JSON.parse(text) : null;
}
async function deskList(env, request, url, me) {            // the Arka desk list (table admin_watchlist): everyone reads, only the admin changes it
  try {
    if (request.method === "GET") {
      let rows = []; try { rows = await sb(env, "admin_watchlist?select=symbol&order=id"); } catch (e) { if (e.status === 503) throw e; rows = []; }
      return json({ syms: [...new Set((rows || []).map((r) => cleanSym(r.symbol)).filter(Boolean))], can_edit: me.role === "admin" });
    }
    if (me.role !== "admin") return fail(403, "Only the Arka desk can change this list.");
    const b = request.method === "POST" ? await request.json().catch(() => ({})) : {};
    const syms = [...new Set((request.method === "POST" ? b.syms || [b.sym] : (url.searchParams.get("s") || "").split(",")).map(cleanSym).filter(Boolean))].slice(0, 200);
    if (!syms.length) return fail(400, "Which stock?");
    if (request.method === "POST") {
      const have = new Set(((await sb(env, "admin_watchlist?select=symbol")) || []).map((r) => cleanSym(r.symbol))), fresh = syms.filter((x) => !have.has(x));
      if (fresh.length) await sb(env, "admin_watchlist", { method: "POST", body: JSON.stringify(fresh.map((symbol) => ({ symbol }))) });
      return json({ ok: true, added: fresh });
    }
    if (request.method === "DELETE") { await sb(env, `admin_watchlist?symbol=in.(${syms.map((x) => `"${x}"`).map(encodeURIComponent).join(",")})`, { method: "DELETE" }); return json({ ok: true }); }
    return fail(405, "Not supported.");
  } catch (e) { return fail(e.status === 503 ? 503 : 502, e.message); }
}
async function watchlist(env, request, url, me) {
  if (url.searchParams.get("desk")) return deskList(env, request, url, me);
  try {
    if (request.method === "PUT") {                     // rename a list (watchlist_page.rename_list)
      const b = await request.json().catch(() => ({})), from = cleanList(b.from), to = cleanList(b.to);
      if (!b.from || !b.to || from === to) return fail(400, "Give the list a new name.");
      await sb(env, `watchlist?list_name=eq.${encodeURIComponent(from)}`, { method: "PATCH", body: JSON.stringify({ list_name: to }) });
      return json({ ok: true, to });
    }
    if (request.method === "POST" || request.method === "DELETE") {       // several stocks in one call (add box, import, remove selected)
      const body = request.method === "POST" ? await request.clone().json().catch(() => ({})) : {};
      const many = request.method === "POST" ? body.syms : url.searchParams.get("ss") ? url.searchParams.get("ss").split(",") : null;
      if (Array.isArray(many)) {
        const list = cleanList(request.method === "POST" ? body.list : url.searchParams.get("list")), syms = [...new Set(many.map(cleanSym).filter(Boolean))].slice(0, 300);
        if (!syms.length) return fail(400, "Which stocks?");
        const inq = `symbol=in.(${syms.map((x) => encodeURIComponent(`"${x}"`)).join(",")})`;
        if (request.method === "DELETE") {
          try { await sb(env, `watchlist?${inq}&list_name=eq.${encodeURIComponent(list)}`, { method: "DELETE" }); }
          catch (e) { if (e.status === 503) throw e; await sb(env, `watchlist?${inq}`, { method: "DELETE" }); }
          return json({ ok: true });
        }
        let have = []; try { have = await sb(env, `watchlist?select=symbol&list_name=eq.${encodeURIComponent(list)}`); } catch (e) { if (e.status === 503) throw e; have = await sb(env, "watchlist?select=symbol"); }
        const got = new Set((have || []).map((r) => cleanSym(r.symbol))), fresh = syms.filter((x) => !got.has(x)), meta = body.meta || {}, pos = (v) => (v == null || v === "" || !(Number(v) > 0) ? null : Number(v));
        if (fresh.length) {
          const row = (symbol) => { const m = meta[symbol] || {}; return { symbol, list_name: list, note: String(m.note || "").trim().slice(0, 200) || null, alert_above: pos(m.above), alert_below: pos(m.below) }; };
          try { await sb(env, "watchlist", { method: "POST", body: JSON.stringify(fresh.map(row)) }); }
          catch (e) { if (e.status === 503) throw e; await sb(env, "watchlist", { method: "POST", body: JSON.stringify(fresh.map((symbol) => ({ symbol }))) }); }
        }
        return json({ ok: true, added: fresh, list });
      }
    }
    if (request.method === "GET") {
      let rows, lists = true;
      try { rows = await sb(env, "watchlist?select=symbol,list_name,note,alert_above,alert_below&order=id"); }
      catch (e) {                                       // table without the list columns: one plain list
        if (e.status === 503) throw e;
        rows = await sb(env, "watchlist?select=symbol&order=id"); lists = false;
      }
      const seen = new Set(), out = [];
      for (const r of rows || []) {
        const sym = cleanSym(r.symbol), list = cleanList(r.list_name);
        if (sym && !seen.has(list + "|" + sym)) { seen.add(list + "|" + sym); out.push({ sym, list, note: r.note || "", above: r.alert_above ?? null, below: r.alert_below ?? null }); }
      }
      return json({ rows: out, lists });
    }
    const hasBody = request.method === "POST" || request.method === "PATCH";
    const b = hasBody ? await request.json().catch(() => ({})) : {};
    const sym = cleanSym(hasBody ? b.sym : url.searchParams.get("s"));
    const list = cleanList(hasBody ? b.list : url.searchParams.get("list"));
    if (!sym) return fail(400, "Which stock?");
    if (request.method === "POST") {
      try { await sb(env, "watchlist", { method: "POST", body: JSON.stringify({ symbol: sym, list_name: list }) }); }
      catch (e) { if (e.status === 503) throw e; await sb(env, "watchlist", { method: "POST", body: JSON.stringify({ symbol: sym }) }); }
      return json({ ok: true, sym, list });
    }
    if (request.method === "DELETE") {
      try { await sb(env, `watchlist?symbol=eq.${encodeURIComponent(sym)}&list_name=eq.${encodeURIComponent(list)}`, { method: "DELETE" }); }
      catch (e) { if (e.status === 503) throw e; await sb(env, `watchlist?symbol=eq.${encodeURIComponent(sym)}`, { method: "DELETE" }); }
      return json({ ok: true });
    }
    if (request.method === "PATCH") {                   // note, price alerts, or move to another list
      const pos = (v) => (v == null || v === "" || !(Number(v) > 0) ? null : Number(v));
      const fields = { note: String(b.note || "").trim().slice(0, 200) || null, alert_above: pos(b.above), alert_below: pos(b.below) };
      if (b.to && cleanList(b.to) !== list) fields.list_name = cleanList(b.to);
      await sb(env, `watchlist?symbol=eq.${encodeURIComponent(sym)}&list_name=eq.${encodeURIComponent(list)}`, { method: "PATCH", body: JSON.stringify(fields) });
      return json({ ok: true });
    }
    return fail(405, "Not supported.");
  } catch (e) { return fail(e.status === 503 ? 503 : 502, e.message); }
}

// ── portfolio (the same Supabase tables the Streamlit app uses: portfolio, portfolio_sells, portfolio_plan) ──
const missingTable = (e) => e.status === 404 || /PGRST205|does not exist|could not find the table|42P01/i.test(String(e.body || e.message || ""));
const isDate = (s) => /^\d{4}-\d{2}-\d{2}$/.test(String(s || ""));
const today = () => new Date(Date.now() + 330 * 60000).toISOString().slice(0, 10);          // the date in India
async function optional(env, path) { try { return { rows: (await sb(env, path)) || [], ok: true }; } catch (e) { if (missingTable(e)) return { rows: [], ok: false }; throw e; } }
async function portfolio(env, request, url, sub) {
  try {
    const m = request.method, b = m === "POST" ? await request.json().catch(() => ({})) : {};
    if (sub === "" && m === "GET") {
      const [lots, sells, plans, meta, snaps, sets] = await Promise.all([optional(env, "portfolio?select=id,symbol,name,quantity,buy_price,buy_date,note&order=buy_date.asc.nullsfirst,id.asc"),
        optional(env, "portfolio_sells?select=id,book,symbol,quantity,sell_price,sell_date,note&order=sell_date.asc,id.asc"), optional(env, "portfolio_plan?select=book,symbol,stop_loss,target"),
        optional(env, "portfolio_lot_meta?select=lot_id,book"), optional(env, "portfolio_snapshots?select=snap_date,book,invested,value,realised,dividends&order=snap_date.asc&limit=2000"),
        optional(env, "portfolio_settings?select=key,value")]);
      const plan = {}, bookOf = {};                      // plans are kept per book, as "Book|SYMBOL"
      for (const r of plans.rows) plan[(r.book || "Main") + "|" + cleanSym(r.symbol)] = { stop: r.stop_loss == null ? null : Number(r.stop_loss), target: r.target == null ? null : Number(r.target) };
      for (const r of meta.rows) if (r.lot_id != null) bookOf[String(r.lot_id)] = r.book || "Main";
      const settings = {}; for (const r of sets.rows) settings[r.key] = r.value;
      return json({ table: lots.ok, can_sell: sells.ok, can_plan: plans.ok, can_book: meta.ok, can_snap: snaps.ok, can_set: sets.ok, plan, settings,
        snaps: snaps.rows.map((r) => ({ d: String(r.snap_date).slice(0, 10), book: r.book, inv: Number(r.invested), val: Number(r.value), real: Number(r.realised), div: Number(r.dividends) })),
        lots: lots.rows.map((r) => ({ id: r.id, book: bookOf[String(r.id)] || "Main", sym: cleanSym(r.symbol), name: r.name || "", qty: Number(r.quantity), price: Number(r.buy_price), date: r.buy_date || null, note: r.note || "" })).filter((r) => r.sym && r.qty > 0),
        sells: sells.rows.map((r) => ({ id: r.id, book: r.book || "Main", sym: cleanSym(r.symbol), qty: Number(r.quantity), price: Number(r.sell_price), date: r.sell_date || null, note: r.note || "" })).filter((r) => r.sym && r.qty > 0) });
    }
    if (sub === "" && m === "POST") {
      const sym = cleanSym(b.sym), qty = Number(b.qty), price = Number(b.price), date = isDate(b.date) ? b.date : today();
      if (!sym || !(qty > 0) || !(price > 0)) return fail(400, "Give the stock, a quantity above zero and a buy price above zero.");
      if (date > today()) return fail(400, "The buy date cannot be in the future.");
      const book = cleanList(b.book);
      const made = await sb(env, "portfolio", { method: "POST", headers: { prefer: "return=representation" }, body: JSON.stringify({ symbol: sym, quantity: qty, buy_price: price, buy_date: date, note: String(b.note || "").slice(0, 200) || null }) });
      let booked = true;                                 // a buy outside "Main" is tagged with its book, the way the classic app does it
      if (book !== "Main") {
        const id = made && made[0] && made[0].id;
        try { if (id == null) throw new Error("no id"); await sb(env, "portfolio_lot_meta?on_conflict=lot_id", { method: "POST", headers: { prefer: "resolution=merge-duplicates,return=minimal" }, body: JSON.stringify({ lot_id: String(id), book }) }); }
        catch { booked = false; }
      }
      return json({ ok: true, booked });
    }
    if (sub === "" && m === "DELETE") {
      const id = url.searchParams.get("id");
      if (!/^\d+$/.test(id || "")) return fail(400, "Which buy?");
      await sb(env, "portfolio?id=eq." + id, { method: "DELETE" });
      try { await sb(env, "portfolio_lot_meta?lot_id=eq." + id, { method: "DELETE" }); } catch { /* no book tag to remove */ }
      return json({ ok: true });
    }
    if (sub === "/sell" && m === "DELETE") {
      const id = url.searchParams.get("id");
      if (!/^\d+$/.test(id || "")) return fail(400, "Which sell?");
      await sb(env, "portfolio_sells?id=eq." + id, { method: "DELETE" });
      return json({ ok: true });
    }
    if (sub === "/sell" && m === "POST") {
      const sym = cleanSym(b.sym), qty = Number(b.qty), price = Number(b.price), date = isDate(b.date) ? b.date : today();
      if (!sym || !(qty > 0) || !(price > 0)) return fail(400, "Give the stock, a quantity above zero and a sell price above zero.");
      if (date > today()) return fail(400, "The sell date cannot be in the future.");
      // How many shares are open is checked in the browser, which knows about splits and books; here only the tables are checked.
      const book = cleanList(b.book);
      try { await sb(env, "portfolio_sells", { method: "POST", body: JSON.stringify({ book, symbol: sym, quantity: qty, sell_price: price, sell_date: date, note: String(b.note || "").slice(0, 200) }) }); }
      catch (e) { if (missingTable(e)) return fail(409, "Selling needs the extra portfolio tables. Open Portfolio in the classic app once and run the SQL it shows."); throw e; }
      return json({ ok: true });
    }
    if (sub === "/move" && m === "POST") {                // move one buy to another book
      if (!/^\d+$/.test(String(b.id || ""))) return fail(400, "Which buy?");
      try { await sb(env, "portfolio_lot_meta?on_conflict=lot_id", { method: "POST", headers: { prefer: "resolution=merge-duplicates,return=minimal" }, body: JSON.stringify({ lot_id: String(b.id), book: cleanList(b.book) }) }); }
      catch (e) { if (missingTable(e)) return fail(409, "Books need the extra portfolio tables. Open Portfolio in the classic app once and run the SQL it shows."); throw e; }
      return json({ ok: true });
    }
    if (sub === "/snapshot" && m === "POST") {           // one row a day per book (portfolio_page._snapshot)
      const n = (v) => (isFinite(+v) ? Math.round(+v * 100) / 100 : null), book = b.book === "ALL" ? "ALL" : cleanList(b.book);
      try { await sb(env, "portfolio_snapshots?on_conflict=snap_date,book", { method: "POST", headers: { prefer: "resolution=merge-duplicates,return=minimal" },
              body: JSON.stringify({ snap_date: today(), book, invested: n(b.inv), value: n(b.val), realised: n(b.real), dividends: n(b.div) }) }); }
      catch (e) { if (missingTable(e)) return json({ ok: false, missing: true }); throw e; }
      return json({ ok: true });
    }
    if (sub === "/settings" && m === "POST") {           // the editable tax assumptions (portfolio_settings, key "tax")
      const r = b.tax || {}, c = (v, lo, hi) => Math.max(lo, Math.min(hi, +v || 0)), tax = { stcg: c(r.stcg, 0, 50), ltcg: c(r.ltcg, 0, 50), exempt: c(r.exempt, 0, 1e8), cess: c(r.cess, 0, 10) };
      try { await sb(env, "portfolio_settings?on_conflict=key", { method: "POST", headers: { prefer: "resolution=merge-duplicates,return=minimal" }, body: JSON.stringify({ key: "tax", value: tax }) }); }
      catch (e) { if (missingTable(e)) return fail(409, "Saving needs the portfolio_settings table. Open Portfolio in the classic app once and run the SQL it shows."); throw e; }
      return json({ ok: true, tax });
    }
    if (sub === "/plan" && m === "POST") {
      const sym = cleanSym(b.sym), pos = (v) => (v == null || v === "" || !(Number(v) > 0) ? null : Number(v));
      if (!sym) return fail(400, "Which stock?");
      try { await sb(env, "portfolio_plan?on_conflict=book,symbol", { method: "POST", headers: { prefer: "resolution=merge-duplicates,return=minimal" },
              body: JSON.stringify({ book: cleanList(b.book), symbol: sym, stop_loss: pos(b.stop), target: pos(b.target) }) }); }
      catch (e) { if (missingTable(e)) return fail(409, "Stops and targets need the extra portfolio tables. Open Portfolio in the classic app once and run the SQL it shows."); throw e; }
      return json({ ok: true });
    }
    return fail(405, "Not supported.");
  } catch (e) { return fail(e.status === 503 ? 503 : 502, e.message); }
}

// ── files written into the repo by the workflows (public): signals, deals, the daily brief ──
async function repoFile(env, ctx, path, ttl = 600, branch = "main") {      // -> text, or null when the file is not there
  const url = `https://raw.githubusercontent.com/${env.GITHUB_REPO || REPO}/${branch}/${path}`;
  const cache = typeof caches !== "undefined" && caches.default, key = new Request(url);
  if (cache) { const hit = await cache.match(key); if (hit) return hit.text(); }
  const r = await fetch(url, { headers: { "user-agent": UA } });
  if (!r.ok) return null;
  const text = await r.text();
  if (cache) { const put = cache.put(key, new Response(text, { headers: { "cache-control": `max-age=${ttl}` } })); ctx && ctx.waitUntil ? ctx.waitUntil(put) : await put; }
  return text;
}
const raw = (text) => new Response(text, { headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" } });
async function signals(env, ctx) {
  const text = await repoFile(env, ctx, "arka_data/signals/signals.json");
  return text == null ? fail(502, "The signal file could not be read.") : raw(text);
}
export function parseCsv(text) {                           // small CSV reader (handles quoted names with commas)
  const rows = [];
  for (const line of String(text || "").split(/\r?\n/)) {
    if (!line.trim()) continue;
    const out = []; let cur = "", q = false;
    for (let i = 0; i < line.length; i++) {
      const c = line[i];
      if (q) { if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; } else if (c === '"') q = false; else cur += c; }
      else if (c === '"') q = true; else if (c === ",") { out.push(cur); cur = ""; } else cur += c;
    }
    out.push(cur); rows.push(out);
  }
  return rows;
}
async function deals(env, ctx) {
  const text = await repoFile(env, ctx, "arka_data/signals/deals.csv");
  if (text == null) return json({ deals: [] });
  const [head, ...rows] = parseCsv(text), ix = Object.fromEntries((head || []).map((h, i) => [h.trim(), i]));
  const out = rows.map((r) => ({ kind: r[ix.kind], date: r[ix.date], sym: cleanSym(r[ix.sym]), who: (r[ix.who] || "").trim(), side: (r[ix.side] || "").toUpperCase(), qty: Number(r[ix.qty]), price: Number(r[ix.price]), cr: Number(r[ix.value_cr]) }))
    .filter((d) => d.sym && d.cr > 0);
  const stamp = (d) => { const t = Date.parse(d.date); return isNaN(t) ? 0 : t; };
  out.sort((a, b) => stamp(b) - stamp(a) || b.cr - a.cr);
  return json({ deals: out.slice(0, 60) });
}
// results of the nightly "Market data" job (the Streamlit engines run in Python on GitHub); only known file names are served
// They live on the "market-data" branch (replaced each night, so the main branch and Cloudflare builds stay small); older runs wrote to main.
const MARKET_FILES = /^(regime_nifty(500|200|100)|meta|flow|flow_sectors|flow_fpi|names)$/;
async function market(env, ctx, url) {
  const f = url.searchParams.get("f") || "";
  if (!MARKET_FILES.test(f)) return fail(400, "Unknown market file.");
  const text = (await repoFile(env, ctx, `${f}.json`, 900, "market-data")) ?? (await repoFile(env, ctx, `arka_data/market/${f}.json`, 900));
  return text == null ? fail(404, "Not built yet: run the “Market data” job once in GitHub Actions.") : raw(text);
}
async function brief(env, ctx) {
  if (env.SUPABASE_URL && env.SUPABASE_KEY) {              // written here when the morning job has the Supabase secrets
    try {
      const r = await sb(env, "arka_briefs?select=day,payload&kind=eq.am&order=day.desc&limit=1");
      if (r && r[0]) return json({ brief: typeof r[0].payload === "string" ? JSON.parse(r[0].payload) : r[0].payload });
    } catch { /* no such table: try the repo file */ }
  }
  const text = await repoFile(env, ctx, "arka_data/brief/latest.json", 300);
  if (text) { try { return json({ brief: JSON.parse(text) }); } catch { /* fall through */ } }
  return json({ brief: null });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url), p = url.pathname;
    if (!p.startsWith("/api/")) return env.ASSETS ? env.ASSETS.fetch(request) : fail(404, "Not found.");
    try {
      if (p === "/api/health") {                        // which settings exist (true/false only, never a value)
        const has = (n) => Boolean(env[n] && String(env[n]).trim());
        return json({ sign_in: has("AUTH_SECRET") && (has("ADMIN_PASS") || has("MEMBER_PASS")), storage: has("SUPABASE_URL") && has("SUPABASE_KEY") });
      }
      if (p === "/api/login" && request.method === "POST") return login(env, request);
      if (p === "/api/logout" && request.method === "POST") return json({ ok: true }, 200, { "set-cookie": cookie("", 0) });
      const me = await readToken(env, request);
      if (!me) return fail(401, "Please sign in.");
      if (p === "/api/me") return json(me);
      if (p === "/api/quotes") return quotes(url, ctx);
      if (p === "/api/stock") return stock(url, ctx);
      if (p === "/api/history") return history(url, ctx);
      if (p === "/api/overview") return overview(url, ctx);
      if (p === "/api/mmi") return mmi(ctx);
      if (p === "/api/gift") return gift(ctx);
      if (p === "/api/feed") return feed(url, ctx);
      if (p === "/api/search") return search(url, ctx);
      if (p === "/api/screener") return screener(url, ctx);
      if (p === "/api/screener/peers") return screenerPeers(url, ctx);
      if (p === "/api/yinfo") return yinfo(url, ctx);
      if (p === "/api/bse") return bse(url, ctx);
      if (p === "/api/dlv") return dlv(env, ctx, url);
      if (p === "/api/intraday") return intraday(url, ctx);
      if (p === "/api/fpi") return fpiReport(url, ctx);
      if (p === "/api/note") return notes(env, request, url);
      if (p === "/api/watchlist") return watchlist(env, request, url, me);
      if (p === "/api/dlvlast") return dlvMany(env, ctx, url);
      if (p === "/api/cal") return calMany(url, ctx);
      if (p === "/api/signals") return signals(env, ctx);
      if (p === "/api/deals") return deals(env, ctx);
      if (p === "/api/brief") return brief(env, ctx);
      if (p === "/api/market") return market(env, ctx, url);
      if (["/api/portfolio", "/api/portfolio/sell", "/api/portfolio/plan", "/api/portfolio/move", "/api/portfolio/snapshot", "/api/portfolio/settings"].includes(p)) return portfolio(env, request, url, p.slice("/api/portfolio".length));
      return fail(404, "Not found.");
    } catch (e) {
      return fail(500, "Something went wrong on the server.");
    }
  },
};

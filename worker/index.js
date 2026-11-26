// Arka Trades · API (stage 18: the full Daily brief with past editions and writing it from the website; the Account page). Runs on Cloudflare for every request to /api/*.
// Secrets are read by NAME from the Cloudflare project settings and never sent to the browser:
//   ADMIN_USER, ADMIN_PASS, MEMBER_USER, MEMBER_PASS, AUTH_SECRET   sign-in (same names and values as in Streamlit)
//   SUPABASE_URL, SUPABASE_KEY                                      watchlist storage (same as in Streamlit)
//   GEMINI_KEY                                                      Screener and Arka AI (Google Gemini)
//   PINECONE_KEY, PINECONE_HOST (optional: found by name when missing) Arka AI playbook storage, the same Pinecone index Streamlit uses
//   GH_ACTIONS_TOKEN (optional)                                     Daily brief: "Write now / again" starts the morning job on GitHub
//   ARKA_GEMINI_MODEL (optional)                                    a Gemini model to try first for Arka AI, as in Streamlit
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
    return p.x > Date.now() && (p.r === "admin" || p.r === "member") ? { role: p.r, user: p.u, exp: p.x } : null;
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
export function historyFrom(sym, data, full = false) {
  const res = data && data.chart && data.chart.result && data.chart.result[0];
  if (!res || !res.timestamp) return { sym, ok: false };
  const off = (res.meta && res.meta.gmtoffset) || 19800, q = ((res.indicators || {}).quote || [{}])[0], r2 = (v) => (v == null ? null : +v.toFixed(2));
  const rows = new Map();                               // one row per day, the last one wins; days with no close are dropped
  const adj = (((res.indicators || {}).adjclose || [])[0] || {}).adjclose || null, raw = (v) => (v == null ? null : +v);
  res.timestamp.forEach((t, i) => { const c = q.close && q.close[i]; if (c != null) rows.set(dayOf(t, off), [r2(c), r2(q.high && q.high[i]), r2(q.low && q.low[i]), (q.volume && q.volume[i]) || 0, r2(q.open && q.open[i]),
    full ? [raw(q.open && q.open[i]), raw(q.high && q.high[i]), raw(q.low && q.low[i]), raw(c), adj ? raw(adj[i]) : null] : null]); });
  const d = [...rows.keys()].sort(), col = (k) => d.map((x) => rows.get(x)[k]);
  const ev = res.events || {}, list = (o, f) => Object.values(o || {}).map(f).filter((x) => x[1] > 0).sort((a, b) => (a[0] < b[0] ? -1 : 1));
  if (full) { const f = d.map((x) => rows.get(x)[5]);                // full precision, and Yahoo's adjusted close (the Backtest lab adjusts like yfinance auto_adjust)
    return { sym, ok: d.length > 0, d, o: f.map((x) => x[0]), h: f.map((x) => x[1]), l: f.map((x) => x[2]), c: f.map((x) => x[3]), a: f.map((x) => x[4]), v: col(3), full: true }; }
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
  const from = /^\d{4}-\d{2}-\d{2}$/.test(url.searchParams.get("from") || "") ? url.searchParams.get("from") : null, full = url.searchParams.get("full") === "1";
  const out = await Promise.all(list.map(async (sym) => {
    // Yahoo answers range=max with one candle a month, so the whole history is asked for by dates; and any answer that is not daily is asked for again by dates
    const base = `/v8/finance/chart/${encodeURIComponent(ySym(sym))}?interval=1d&events=div%2Csplits&`, yrs = { "1y": 1, "2y": 2, "5y": 5, "10y": 10 }[range];
    const byDate = (from) => base + `period1=${from}&period2=9999999999`, start = yrs ? Math.floor(Date.now() / 864e5 - yrs * 365.25) * 86400 : 0;
    try {
      if (from) { const h = historyFrom(sym, await yahoo(byDate(Math.floor(Date.parse(from + "T00:00:00+05:30") / 1000)), ctx, 1800), full); return { ...h, daily: h.ok ? isDaily(h.d) : false }; }      // from a date (midnight in India), as yfinance start=
      const ttl = full ? 300 : 1800;                    // Arka AI (range + full) reads today's candle: 5 minutes, as Streamlit's cache
      let h = historyFrom(sym, await yahoo(yrs ? base + "range=" + range + (full ? "&includeAdjustedClose=true" : "") : byDate(0), ctx, ttl), full);      // its own cache entry, kept 5 minutes
      if (h.ok && !isDaily(h.d)) { const again = historyFrom(sym, await yahoo(byDate(start), ctx, ttl), full); if (again.ok && (isDaily(again.d) || again.d.length > h.d.length)) h = again; }
      return { ...h, daily: h.ok ? isDaily(h.d) : false };
    } catch { return { sym, ok: false }; }
  }));
  return json({ history: out, at: Date.now() }, 200, { "cache-control": `private, max-age=${full && !from ? 240 : 600}` });
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
// Named holders over 1% (Screener's "investors" breakdown of the shareholding table, from company filings): who the funds are, quarter by quarter.
const HOLDER_KINDS = new Set(["domestic_institutions", "foreign_institutions", "promoters", "public", "government"]);
async function screenerHolders(url, ctx) {
  const id = String(url.searchParams.get("id") || ""), c = String(url.searchParams.get("c") || ""), p = String(url.searchParams.get("p") || "quarterly");
  if (!/^\d{1,9}$/.test(id) || !HOLDER_KINDS.has(c) || !/^(quarterly|yearly)$/.test(p)) return fail(400, "Which holders?");
  try {
    return json(await cached(`holders/${id}/${c}/${p}`, 6 * 3600, async () => {
      const r = await fetch(`${SCREENER}/api/3/${id}/investors/${c}/${p}/`, { headers: { "user-agent": UA, accept: "application/json, text/plain, */*", "x-requested-with": "XMLHttpRequest", referer: SCREENER + "/" } });
      if (!r.ok) throw new Error("Screener answered " + r.status);
      const t = await r.text(); let d; try { d = JSON.parse(t); } catch { throw new Error("Screener did not send a holder list"); }
      if (!d || typeof d !== "object" || Array.isArray(d)) throw new Error("Screener did not send a holder list");
      return { ok: true, data: d };
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
  const names = [...new Set(v)].map((x) => `FIIInvestSector_${x}${dd}${y}.html`);
  return [...names.map((x) => NSDL + x), ...names.slice(0, 2).map((x) => "https://pilot.fpi.nsdl.co.in/StaticReports/Fortnightly_Sector_wise_FII_Investment_Data/" + x),      // NSDL, its pilot site, then CDSL's copy
    `${cdsl}${encodeURIComponent(`${full} ${dd} ${y}`)}.html`, `${cdsl}${encodeURIComponent(`${full} ${dd}, ${y}`)}.html`];
}
async function fpiReport(url, ctx) {
  const day = String(url.searchParams.get("d") || "");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return fail(400, "Which fortnight?");
  try {
    return json(await cached("nsdl/" + day, 30 * 86400, async () => {
      for (const u of nsdlUrls(day)) {
        const r = await fetch(u, { headers: { "user-agent": UA, accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "accept-language": "en-IN,en;q=0.9", referer: "https://www.fpi.nsdl.co.in/web/Reports/FPI_Fortnightly_Selection.aspx" } }).catch(() => null);
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
// the desk's other files: candles for the chart picker, the index option chain (NIFTY and BANKNIFTY rows only) and the live order log
async function deskFile(env, ctx, url) {
  const f = url.searchParams.get("f");
  if (f === "charts") { const t = await repoFile(env, ctx, "arka_data/signals/charts.json"); return t == null ? json({}) : raw(t); }
  if (f === "chain") { const t = await repoFile(env, ctx, "arka_data/fno/chain_latest.csv", 1800); return json({ rows: t == null ? [] : chainRows(t) }); }
  if (f === "log") { const t = await repoFile(env, ctx, "arka_data/signals/log.csv"); if (t == null) return json({ rows: [] });
    const [head, ...rows] = parseCsv(t); return json({ rows: rows.map((r) => Object.fromEntries((head || []).map((h, i) => [h.trim(), r[i] ?? ""]))) }); }
  if (f === "om") { const t = await repoFile(env, ctx, "arka_data/fno/om.json", 1800); return t == null ? json({ ok: false, missing: true }) : raw(t); }       // the Options map (fno_export.py)
  if (f === "oms") { const sym = cleanSym(url.searchParams.get("s")); if (!sym) return fail(400, "Which stock?");
    const t = await repoFile(env, ctx, `arka_data/fno/om/${sym.replace(/&/g, "%26")}.json`, 1800); return t == null ? json({ ok: false, missing: true }) : raw(t); }
  return fail(400, "Which file?");
}
export function chainRows(text) {                          // chain_latest.csv → [{sym, exp, strike, ce_oi, pe_oi}] for the two indexes (a line scan: the file has every F&O stock)
  const lines = String(text).split("\n"), h = (lines[0] || "").trim().split(","), ix = Object.fromEntries(h.map((k, i) => [k, i])), out = [];
  for (let i = 1; i < lines.length; i++) { const l = lines[i]; if (!(l.startsWith("NIFTY,") || l.startsWith("BANKNIFTY,"))) continue;
    const c = l.trim().split(","); out.push({ sym: c[ix.sym], exp: c[ix.exp], strike: c[ix.strike], ce_oi: c[ix.ce_oi], pe_oi: c[ix.pe_oi] }); }
  return out;
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
const MARKET_FILES = /^(regime_nifty(500|200|100)|meta|flow|flow_sectors|flow_fpi|flow_index|names|flow(_sectors)?@[a-z0-9]+-[a-z0-9]+-\dy-\d+(\.\d+)?|flow_models@[a-z0-9]+-[a-z0-9]+-\dy-\d+(\.\d+)?@\d+(\.\d+)?)$/;      // the Money flow data choices: flow@arka-n50-5y-0.5
async function market(env, ctx, url) {
  const f = url.searchParams.get("f") || "";
  if (!MARKET_FILES.test(f)) return fail(400, "Unknown market file.");
  const text = (await repoFile(env, ctx, `${f}.json`, 900, "market-data")) ?? (await repoFile(env, ctx, `arka_data/market/${f}.json`, 900));
  return text == null ? fail(404, "Not built yet: run the “Market data” job once in GitHub Actions.") : raw(text);
}
// ── Daily brief (daily_brief.render_brief): the last 7 editions, from the Supabase table arka_briefs (what Streamlit and the morning job
// share) and the files the morning job saves in the repo (arka_data/brief/<day>.json, index.json, latest.json). Supabase wins for a day
// in both, as in daily_brief.Store. "Write now / again" starts the morning job on GitHub (write only, no Telegram): secret GH_ACTIONS_TOKEN.
const BRIEF_WF = "daily-brief.yml";
async function briefText(env, ctx, path, fresh) {             // fresh: straight from GitHub (right after the job ran), else the 5-minute copy
  if (fresh && env.GH_ACTIONS_TOKEN) {
    const r = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO || REPO}/contents/${path}?ref=main`, { headers: { authorization: `Bearer ${env.GH_ACTIONS_TOKEN}`, accept: "application/vnd.github.raw+json", "user-agent": UA, "x-github-api-version": "2022-11-28" } });
    return r.ok ? r.text() : null;
  }
  return repoFile(env, ctx, path, fresh ? 30 : 300);
}
async function brief(env, ctx, url, request, me) {
  if (request.method === "POST") return briefWrite(env, request, me);
  if (url.searchParams.get("op") === "run") return briefRun(env, ctx);
  const fresh = url.searchParams.get("fresh") === "1", want = /^\d{4}-\d{2}-\d{2}$/.test(url.searchParams.get("day") || "") ? url.searchParams.get("day") : "";
  let sbDays = [], table = null;                            // table: true = arka_briefs is there, false = it is not, null = no Supabase
  if (env.SUPABASE_URL && env.SUPABASE_KEY) {
    try { sbDays = ((await sb(env, "arka_briefs?select=day&kind=eq.am&order=day.desc&limit=7")) || []).map((r) => String(r.day).slice(0, 10)); table = true; }
    catch (e) { table = e.status === 503 ? null : false; }
  }
  let repoDays = [], latest = null;
  const idx = await briefText(env, ctx, "arka_data/brief/index.json", fresh); if (idx) { try { repoDays = (JSON.parse(idx).days || []).filter((d) => /^\d{4}-\d{2}-\d{2}$/.test(d)); } catch { /* no list */ } }
  const lt = await briefText(env, ctx, "arka_data/brief/latest.json", fresh); if (lt) { try { latest = JSON.parse(lt); if (latest && latest.day && !repoDays.includes(latest.day)) repoDays.push(latest.day); } catch { latest = null; } }
  const days = [...new Set([...sbDays, ...repoDays])].sort().reverse().slice(0, 7), pick = days.includes(want) ? want : days[0];
  let out = null;
  if (pick && sbDays.includes(pick)) { try { const r = await sb(env, `arka_briefs?select=payload&kind=eq.am&day=eq.${pick}&limit=1`); if (r && r[0]) out = typeof r[0].payload === "string" ? JSON.parse(r[0].payload) : r[0].payload; } catch { out = null; } }
  if (!out && pick) { if (latest && latest.day === pick) out = latest; else { const t = await briefText(env, ctx, `arka_data/brief/${pick}.json`, fresh); if (t) { try { out = JSON.parse(t); } catch { out = null; } } } }
  return json({ brief: out, days, table, can_write: Boolean(env.GH_ACTIONS_TOKEN), admin: me.role === "admin" });
}
async function briefWrite(env, request, me) {
  if (!env.GH_ACTIONS_TOKEN) return fail(503, "Writing from the website is not set up: add the secret GH_ACTIONS_TOKEN (a GitHub token that may start Actions in this repository) in Cloudflare.");
  const b = await request.json().catch(() => ({})), again = b.again === true;
  if (again && me.role !== "admin") return fail(403, "Only the desk can write the brief again.");
  const r = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO || REPO}/actions/workflows/${BRIEF_WF}/dispatches`, { method: "POST",
    headers: { authorization: `Bearer ${env.GH_ACTIONS_TOKEN}`, accept: "application/vnd.github+json", "user-agent": UA, "x-github-api-version": "2022-11-28", "content-type": "application/json" },
    body: JSON.stringify({ ref: "main", inputs: { app: again ? "again" : "now" } }) });
  if (r.status !== 204) return fail(502, `GitHub did not start the job (${r.status}). Check that GH_ACTIONS_TOKEN may run Actions in this repository.`);
  return json({ ok: true, at: Date.now() });
}
async function briefRun(env, ctx) {                         // the latest website-started run: queued, running, done or failed
  if (!env.GH_ACTIONS_TOKEN) return json({ run: null });
  const r = await fetch(`https://api.github.com/repos/${env.GITHUB_REPO || REPO}/actions/workflows/${BRIEF_WF}/runs?event=workflow_dispatch&per_page=1`, { headers: { authorization: `Bearer ${env.GH_ACTIONS_TOKEN}`, accept: "application/vnd.github+json", "user-agent": UA, "x-github-api-version": "2022-11-28" } });
  if (!r.ok) return json({ run: null });
  const run = ((await r.json()).workflow_runs || [])[0]; if (!run) return json({ run: null });
  let status = null; if (run.status === "completed") { const t = await briefText(env, ctx, "arka_data/brief/status.json", true); if (t) { try { status = JSON.parse(t); } catch { status = null; } } }
  return json({ run: { status: run.status, conclusion: run.conclusion, created: Date.parse(run.created_at), url: run.html_url }, status });
}

// ── Screener (smart_scan_page.py): price files from the "Screener data" job, saved setups, and the AI reader ──
// Price files are passed through without being read here (they are big; the browser unpacks them).
async function repoStream(env, ctx, path, ttl, branch) {
  const url = `https://raw.githubusercontent.com/${env.GITHUB_REPO || REPO}/${branch}/${path}`, cache = typeof caches !== "undefined" && caches.default, key = new Request(url);
  let r = cache ? await cache.match(key) : null;
  if (!r) { const got = await fetch(url, { headers: { "user-agent": UA } }); if (!got.ok) return null;
    r = new Response(got.body, { headers: { "content-type": "application/json; charset=utf-8", "cache-control": `max-age=${ttl}` } });
    if (cache) { const put = cache.put(key, r.clone()); ctx && ctx.waitUntil ? ctx.waitUntil(put) : await put; } }
  return new Response(r.body, { headers: { "content-type": "application/json; charset=utf-8", "cache-control": `private, max-age=${Math.min(ttl, 300)}` } });
}
async function scanFile(env, ctx, url) {
  const f = url.searchParams.get("f") || "";
  if (f === "live") return (await repoStream(env, ctx, "live.json", 120, "scan-live")) || json({ missing: true });
  if (!/^(meta|nifty|deliv|[hx]\d{1,2})$/.test(f)) return fail(400, "Unknown file.");
  return (await repoStream(env, ctx, `${f}.json`, f === "meta" ? 300 : 900, "scan-data")) || (f === "meta" || /^[hx]/.test(f) ? fail(404, "Not built yet: run the “Screener data” job once in GitHub Actions.") : json({ missing: true }));
}
// saved setups: the same Supabase table the Streamlit Screener uses (setups), so both apps show the same setups
const SETUP_COLS = "id,name,price_min,price_max,rsi_min,rsi_max,volume_multiplier,atr_pct_min,atr_pct_max,move_pct_min,move_pct_max,rule_config,visual_rules,created_at";
const canEditSetup = (row, me, hasOwner) => me.role === "admin" || (hasOwner && (row.owner || "admin") === "member");
async function setups(env, request, url, me) {
  try {
    const hasOwner = async () => { try { await sb(env, "setups?select=owner&limit=1"); return true; } catch (e) { if (e.status === 503) throw e; return false; } };
    if (request.method === "GET") {
      const img = url.searchParams.get("img");
      if (img) { if (!/^\d{1,18}$/.test(img)) return fail(400, "Which setup?"); const rows = await sb(env, `setups?select=reference_image_b64&id=eq.${encodeURIComponent(img)}&limit=1`); return json({ b64: (rows && rows[0] && rows[0].reference_image_b64) || null }); }
      let rows = null, owner = false, err = null;
      for (const cols of [SETUP_COLS + ",owner", SETUP_COLS]) { try { rows = await sb(env, `setups?select=${cols}&order=created_at.desc`); owner = cols.endsWith(",owner"); err = null; break; } catch (e) { if (e.status === 503) throw e; err = e; } }
      if (err) return fail(502, missingTable(err) ? "The setups table is not in Supabase yet: run the SQL shown under My setups → Storage once." : "Setups could not be loaded: " + String(err.message).slice(0, 120));
      return json({ setups: (rows || []).map((r) => ({ ...r, can_edit: canEditSetup(r, me, owner) })), has_owner: owner, role: me.role, ai: Boolean(env.GEMINI_KEY && String(env.GEMINI_KEY).trim()) });
    }
    if (request.method === "POST") {
      const b = await request.json().catch(() => ({})), name = String(b.name || "").trim().slice(0, 80), pmin = Number(b.pmin), pmax = Number(b.pmax), rules = Array.isArray(b.rules) ? b.rules.slice(0, 40) : [];
      if (!name) return fail(400, "Give the setup a name.");
      if (!(pmin >= 0) || !(pmax >= 0) || pmin > pmax) return fail(400, "The price band is not right.");
      if (!rules.length) return fail(400, "Add at least one rule.");
      const tol = Number(b.tol), row = { name, price_min: pmin, price_max: pmax, rsi_min: 0, rsi_max: 100, volume_multiplier: 0, atr_pct_min: 0, atr_pct_max: 0, move_pct_min: 0, move_pct_max: 0, visual_rules: String(b.desc || "").slice(0, 6000),
        rule_config: { version: 2, min_pass: Math.max(0, Math.trunc(Number(b.min_pass) || 0)), tol: isFinite(tol) ? Math.max(0, Math.min(0.3, tol)) : 0.05, rules: rules.map((r) => { const o = { ...r }; delete o._id; return o; }) } };
      if (b.img_b64) { if (String(b.img_b64).length > 2_000_000) return fail(400, "That picture is too large (1.5 MB at most)."); row.reference_image_b64 = String(b.img_b64); }
      const owner = await hasOwner(), id = b.id == null || b.id === "" ? null : String(b.id);
      if (id && !/^\d{1,18}$/.test(id)) return fail(400, "Which setup?");
      const send = async (payload) => (id ? sb(env, `setups?id=eq.${encodeURIComponent(id)}`, { method: "PATCH", headers: { prefer: "return=representation" }, body: JSON.stringify(payload) })
        : sb(env, "setups", { method: "POST", headers: { prefer: "return=representation" }, body: JSON.stringify([payload]) }));
      if (id) { const cur = await sb(env, `setups?select=${owner ? "id,owner" : "id"}&id=eq.${encodeURIComponent(id)}&limit=1`); if (!cur || !cur[0]) return fail(404, "That setup is no longer there.");
        if (!canEditSetup(cur[0], me, owner)) return fail(403, "This is a desk setup: duplicate it to make your own copy."); }
      try { const out = await send(!id && owner ? { ...row, owner: me.role } : row); return json({ ok: true, id: out && out[0] ? out[0].id : id }); }
      catch (e) { if (/rule_config/.test(String(e.body || e.message))) return fail(400, "Your setups table has no rule_config column: run the SQL shown under My setups → Storage once, then save again."); throw e; }
    }
    if (request.method === "DELETE") {
      const id = url.searchParams.get("id"); if (!id || !/^\d{1,18}$/.test(id)) return fail(400, "Which setup?");
      const owner = await hasOwner(), cur = await sb(env, `setups?select=${owner ? "id,owner" : "id"}&id=eq.${encodeURIComponent(id)}&limit=1`);
      if (!cur || !cur[0]) return json({ ok: true });
      if (!canEditSetup(cur[0], me, owner)) return fail(403, "This is a desk setup: only the desk can delete it.");
      await sb(env, `setups?id=eq.${encodeURIComponent(id)}`, { method: "DELETE" }); return json({ ok: true });
    }
    return fail(405, "Not supported.");
  } catch (e) { return fail(e.status === 503 ? 503 : 502, e.status === 503 ? e.message : "Setups: " + String(e.message || e).slice(0, 160)); }
}
// Gemini (secret GEMINI_KEY, the same key Arka AI uses in Streamlit). Only what the page sends goes out: a setup description, the rules as text,
// a reference chart picture and a chart of a public stock. Never holdings or the watchlist.
const GEMINI = "https://generativelanguage.googleapis.com/v1beta";
export function pickGeminiModels(names, prefer = "gemini-2.5-flash") {     // smart_scan_page.gemini_model: the newest stable "flash" this key can use, then the others as spares
  const ok = names.filter((n) => n.startsWith("gemini-") && n.includes("flash") && !["lite", "preview", "exp", "image", "tts", "live", "audio", "embedding", "thinking"].some((x) => n.includes(x)));
  const ver = (n) => parseFloat((n.match(/\d+(?:\.\d+)?/) || ["0"])[0]), sorted = ok.slice().sort((a, b) => ver(b) - ver(a) || a.length - b.length);
  const first = ok.includes(prefer) || !sorted.length ? prefer : sorted[0];
  return [...new Set([first, ...sorted, prefer, "gemini-flash-latest"])].slice(0, 4);
}
async function geminiNames(env, ctx) {
  try { const d = await cached("gemini-models", 6 * 3600, async () => { const r = await fetch(`${GEMINI}/models?pageSize=200`, { headers: { "x-goog-api-key": env.GEMINI_KEY } }); if (!r.ok) throw new Error("models " + r.status);
      const j = await r.json(); return { names: (j.models || []).filter((m) => (m.supportedGenerationMethods || ["generateContent"]).includes("generateContent")).map((m) => String(m.name || "").split("/").pop()) }; }, ctx, 7 * 86400);
    return d.names || []; } catch { return []; }
}
async function geminiModels(env, ctx) { return pickGeminiModels(await geminiNames(env, ctx)); }
async function ai(env, ctx, request) {
  if (request.method !== "POST") return fail(405, "Not supported.");
  if (!env.GEMINI_KEY || !String(env.GEMINI_KEY).trim()) return fail(503, "Arka AI is not connected: add the secret GEMINI_KEY in the Cloudflare project settings.");
  const b = await request.json().catch(() => ({})), prompt = String(b.prompt || "").slice(0, 24000), images = (Array.isArray(b.images) ? b.images : []).slice(0, 2);
  if (!prompt.trim()) return fail(400, "Nothing to read.");
  const parts = [{ text: prompt }];
  for (const im of images) { const data = String((im && im.data) || ""), mime = /^image\/(png|jpeg|webp)$/.test((im && im.mime) || "") ? im.mime : "image/png"; if (!data || data.length > 2_800_000) return fail(400, "That picture is too large (2 MB at most)."); parts.push({ inline_data: { mime_type: mime, data } }); }
  const temp = Math.max(0, Math.min(1, Number(b.temperature) || 0.1)), models = await geminiModels(env, ctx); let last = "";
  for (const model of models) for (const think of b.think === false ? [false] : [true, false]) {
    const cfg = { responseMimeType: "application/json", temperature: temp }; if (think) cfg.thinkingConfig = { thinkingBudget: 1024 };
    let r; try { r = await fetch(`${GEMINI}/models/${model}:generateContent`, { method: "POST", headers: { "x-goog-api-key": env.GEMINI_KEY, "content-type": "application/json" }, body: JSON.stringify({ contents: [{ role: "user", parts }], generationConfig: cfg }) }); }
    catch (e) { last = "network"; break; }
    const text = await r.text();
    if (r.ok) { try { const j = JSON.parse(text), out = (((j.candidates || [])[0] || {}).content || {}).parts || [], txt = out.filter((x) => !x.thought).map((x) => x.text || "").join("").trim().replace(/^```(?:json)?/i, "").replace(/```$/, "").trim(); return json({ ok: true, model, data: JSON.parse(txt) }); }
      catch { last = "The AI reply could not be read."; break; } }
    last = `${r.status}`; if (think && /think/i.test(text)) continue;                  // this model has no thinking setting: ask again without it
    break;                                                                           // busy (429 / 503) or not available: try the next model
  }
  return fail(502, /^(429|503)$/.test(last) ? "Arka AI is busy right now. Try again in a minute, or use the built-in reader." : "Arka AI could not answer (" + last + ").");
}

// ── Arka AI (arka_ai.py) ──
// Gemini exactly as Streamlit calls it: the Arka system prompt, temperature 0, the thinking budget of the chosen speed (Fast 0, Balanced 1024,
// Deep = the model decides), a busy model is tried once more after 2.5 s and then the next one, a retired name moves on, at most 4 models.
// Only what the page sends goes out: public prices and detections, the playbook text, a chart picture, a PDF or pictures the trader uploads.
const ARKA_MODEL_RX = /^gemini-(\d+(?:\.\d+)?)-flash$/;
export function rankArkaModels(names, prefer = "") {           // arka_ai._rank_models: stable gemini-<version>-flash, newest first, then safe fallbacks
  const stable = [...new Set(names.filter((n) => ARKA_MODEL_RX.test(n)))].sort((a, b) => parseFloat(b.match(ARKA_MODEL_RX)[1]) - parseFloat(a.match(ARKA_MODEL_RX)[1]));
  return [...new Set([prefer, ...stable, "gemini-2.5-flash", "gemini-flash-latest"].filter(Boolean))];
}
const busyText = (t) => /503|unavailable|high demand|overloaded|429|resource_exhausted|resource exhausted|deadline|timed out|timeout|500 internal/i.test(t);
const goneText = (t) => /not found|404|is not supported|deprecated|no longer available|unknown model|not_found/i.test(t);
let arkaModelOk = "", arkaNoThinking = false;                   // per running copy of the worker, like Streamlit's session values
async function arkaAi(env, ctx, request) {
  if (request.method !== "POST") return fail(405, "Not supported.");
  if (!env.GEMINI_KEY || !String(env.GEMINI_KEY).trim()) return fail(503, "Gemini is not configured: add the secret GEMINI_KEY in the Cloudflare project settings.");
  const b = await request.json().catch(() => ({})), prompt = String(b.prompt || "").slice(0, 600000);
  if (!prompt.trim()) return fail(400, "Nothing to send.");
  const parts = [{ text: prompt }];
  for (const f of (Array.isArray(b.files) ? b.files : []).slice(0, 8)) { const uri = String((f && f.uri) || ""), mime = /^(application\/pdf|image\/(png|jpeg|webp))$/.test((f && f.mime) || "") ? f.mime : "";
    if (!/^https:\/\/generativelanguage\.googleapis\.com\//.test(uri) || !mime) return fail(400, "That upload is not usable."); parts.push({ file_data: { mime_type: mime, file_uri: uri } }); }
  for (const im of (Array.isArray(b.images) ? b.images : []).slice(0, 2)) { const data = String((im && im.data) || ""); if (!data || data.length > 2_800_000) return fail(400, "That picture is too large."); parts.push({ inline_data: { mime_type: "image/png", data } }); }
  const wantJson = b.json !== false, budget = b.budget === null || b.budget === undefined ? null : Math.max(0, Math.min(24576, Math.trunc(Number(b.budget) || 0)));
  const ranked = rankArkaModels(await geminiNames(env, ctx), String(env.ARKA_GEMINI_MODEL || "").trim()), models = [...new Set([arkaModelOk, ...ranked].filter(Boolean))].slice(0, 4);
  let last = "", busy = false;
  const call = async (model, think) => {
    const cfg = { temperature: 0.0 }; if (wantJson) cfg.responseMimeType = "application/json"; if (think) cfg.thinkingConfig = { thinkingBudget: budget };
    const body = { contents: [{ role: "user", parts }], generationConfig: cfg }; if (b.system !== false) body.systemInstruction = { parts: [{ text: String(b.system_text || "").slice(0, 8000) }] };
    const r = await fetch(`${GEMINI}/models/${model}:generateContent`, { method: "POST", headers: { "x-goog-api-key": env.GEMINI_KEY, "content-type": "application/json" }, body: JSON.stringify(body) });
    return [r.ok, r.status, await r.text()];
  };
  for (const model of models) {
    for (let attempt = 0; attempt < 2; attempt++) {
      let ok, status, text;
      try { const think = budget !== null && !arkaNoThinking; [ok, status, text] = await call(model, think);
        if (!ok && think && /think/i.test(text)) { arkaNoThinking = true; [ok, status, text] = await call(model, false); } }        // this model takes no budget: run without it
      catch (e) { ok = false; status = 0; text = "network: " + String(e.message || e); }
      if (ok) { let out = ""; try { const j = JSON.parse(text), c = (j.candidates || [])[0] || {}; out = ((c.content || {}).parts || []).filter((x) => !x.thought).map((x) => x.text || "").join("").trim(); } catch { out = ""; }
        arkaModelOk = model; return json({ ok: true, model, text: out }); }
      last = `${status} ${text.slice(0, 300)}`;
      if (busyText(last)) { busy = true; if (attempt === 0) { await new Promise((r) => setTimeout(r, 2500)); continue; } break; }
      if (goneText(last)) break;
      return fail(502, "Gemini call failed: " + last.slice(0, 240));
    }
  }
  if (busy) return fail(503, "Google's AI service is busy right now (tried " + models.join(", ") + "). Nothing is wrong with your key or the app: wait a minute and press the button again.");
  return fail(502, "Gemini call failed: " + last.slice(0, 240));
}
// A PDF or a picture for Arka AI: streamed to Gemini's file store (kept by Google for 48 hours), so this worker never holds the bytes.
async function aiUpload(env, request) {
  if (request.method !== "POST") return fail(405, "Not supported.");
  if (!env.GEMINI_KEY || !String(env.GEMINI_KEY).trim()) return fail(503, "Gemini is not configured: add the secret GEMINI_KEY in the Cloudflare project settings.");
  const mime = request.headers.get("x-file-type") || "", n = Number(request.headers.get("content-length") || 0);
  if (!/^(application\/pdf|image\/(png|jpeg|webp))$/.test(mime)) return fail(400, "Only PDF, PNG and JPG files.");
  if (!(n > 0) || !request.body) return fail(400, "The file is empty.");
  if (n > 18 * 1024 * 1024) return fail(400, mime === "application/pdf" ? "This PDF is larger than 18 MB. Split it or export a smaller copy, then upload again." : "That picture is larger than 18 MB.");
  const start = await fetch("https://generativelanguage.googleapis.com/upload/v1beta/files", { method: "POST", headers: { "x-goog-api-key": env.GEMINI_KEY, "X-Goog-Upload-Protocol": "resumable", "X-Goog-Upload-Command": "start",
    "X-Goog-Upload-Header-Content-Length": String(n), "X-Goog-Upload-Header-Content-Type": mime, "content-type": "application/json" }, body: JSON.stringify({ file: { display_name: "arka-upload" } }) });
  const to = start.headers.get("x-goog-upload-url"); if (!start.ok || !to) return fail(502, "Gemini did not accept the upload (" + start.status + ").");
  let body = request.body; if (typeof FixedLengthStream !== "undefined") { const fl = new FixedLengthStream(n); request.body.pipeTo(fl.writable).catch(() => {}); body = fl.readable; }
  const up = await fetch(to, { method: "POST", headers: { "Content-Length": String(n), "X-Goog-Upload-Offset": "0", "X-Goog-Upload-Command": "upload, finalize" }, body });
  if (!up.ok) return fail(502, "The upload to Gemini failed (" + up.status + ").");
  let f = ((await up.json().catch(() => ({}))).file) || {};
  for (let i = 0; i < 20 && f.state === "PROCESSING" && f.name; i++) { await new Promise((r) => setTimeout(r, 1500));
    const g = await fetch(`${GEMINI}/${f.name}`, { headers: { "x-goog-api-key": env.GEMINI_KEY } }); if (g.ok) f = await g.json(); }
  if (!f.uri || f.state === "FAILED") return fail(502, "Gemini could not read this file.");
  return json({ ok: true, uri: f.uri, mime: f.mimeType || mime });
}
// The playbook: the Pinecone index "arka-trading-rules" that Streamlit's Arka AI uses (setups, lessons, labelled zones, zone styles, logged plans).
// Items are read back by id, the vectors are placeholders. The list goes to the page unread (no work here), the page decodes it.
const PC_INDEX = "arka-trading-rules", PC_VER = "2025-01";
const pcHead = (env) => ({ "Api-Key": String(env.PINECONE_KEY).trim(), "X-Pinecone-API-Version": PC_VER, "content-type": "application/json" });
let pcInfo = null;                                              // { host, dim }
async function pinecone(env) {
  if (pcInfo) return pcInfo;
  let host = String(env.PINECONE_HOST || "").trim().replace(/^https?:\/\//, "").replace(/\/+$/, ""), dim = 0;
  if (!host) {                                                  // find the index by name, and create it when it is not there (as Streamlit does)
    let r = await fetch(`https://api.pinecone.io/indexes/${PC_INDEX}`, { headers: pcHead(env) });
    if (r.status === 404) { const c = await fetch("https://api.pinecone.io/indexes", { method: "POST", headers: pcHead(env), body: JSON.stringify({ name: PC_INDEX, dimension: 768, metric: "cosine", spec: { serverless: { cloud: "aws", region: "us-east-1" } } }) });
      if (!c.ok && c.status !== 409) throw new Error("Pinecone could not create the index (" + c.status + ")."); r = await fetch(`https://api.pinecone.io/indexes/${PC_INDEX}`, { headers: pcHead(env) }); }
    if (!r.ok) throw new Error("Pinecone answered " + r.status + "."); const j = await r.json(); host = j.host; dim = Number(j.dimension) || 0;
    if (!host || (j.status && j.status.ready === false)) throw new Error("The Pinecone index is still being created. Try again in a minute.");
  }
  if (!dim) { try { const s = await fetch(`https://${host}/describe_index_stats`, { method: "POST", headers: pcHead(env), body: "{}" }); if (s.ok) dim = Number((await s.json()).dimension) || 0; } catch { /* default below */ } }
  pcInfo = { host, dim: dim || 768 }; return pcInfo;
}
const pcCall = async (env, info, path, body) => { const r = await fetch(`https://${info.host}${path}`, { method: "POST", headers: pcHead(env), body: JSON.stringify(body) }); if (!r.ok) throw new Error("Pinecone answered " + r.status + ": " + (await r.text()).slice(0, 160)); return r; };
async function arkaStore(env, ctx, request, url) {
  const cloud = Boolean(env.PINECONE_KEY && String(env.PINECONE_KEY).trim()), ai = Boolean(env.GEMINI_KEY && String(env.GEMINI_KEY).trim());
  if (request.method === "GET" && url.searchParams.get("op") === "status") return json({ cloud, ai, model: arkaModelOk || "" });
  if (!cloud) return fail(503, "Playbook storage is not connected: add the secrets PINECONE_KEY (and PINECONE_HOST) in the Cloudflare project settings.");
  try {
    const info = await pinecone(env), vec = () => new Array(info.dim).fill(0.001);
    if (request.method === "GET") {                             // every item: one query with the placeholder vector returns them all (up to 1,000), metadata only
      const r = await pcCall(env, info, "/query", { vector: vec(), topK: 1000, includeMetadata: true, includeValues: false, namespace: "" });
      return new Response(r.body, { headers: { "content-type": "application/json; charset=utf-8", "cache-control": "no-store", "x-arka-ai": ai ? "1" : "0" } });
    }
    if (request.method !== "POST") return fail(405, "Not supported.");
    const b = await request.json().catch(() => ({}));
    if (b.op === "save") {
      const kinds = ["setup", "lesson", "feedback", "style", "call"], kind = String(b.kind || ""); if (!kinds.includes(kind)) return fail(400, "Unknown item kind.");
      const compact = JSON.stringify(b.payload ?? {}); if ([...compact].length > 34000) return fail(400, "This item is too large to store (over 34 KB). Trim it or split the document.");
      const label = { setup: "Setup", lesson: "Lesson", feedback: "Labelled Zones", style: "Zone Style", call: "Logged Plan" }[kind];
      const name = String(b.name || "").trim().slice(0, 200) || label, rand = [...crypto.getRandomValues(new Uint8Array(2))].map((x) => x.toString(16).padStart(2, "0")).join("");
      const id = `${kind}_${Date.now()}_${rand}`, savedAt = new Date().toISOString().replace("Z", "000+00:00");
      const meta = { memory_kind: kind, item_id: id, name, active: Boolean(b.active), saved_at: savedAt, source_name: String(b.source_name || "").slice(0, 300), source_text: String(b.source_text || "").slice(0, 3000), version: "arka-v10" };
      if (kind === "setup") Object.assign(meta, { setup_id: id, setup_name: name, setup_json: compact }); else meta.item_json = compact;
      await pcCall(env, info, "/vectors/upsert", { vectors: [{ id, values: vec(), metadata: meta }], namespace: "" });
      for (const other of (Array.isArray(b.deactivate) ? b.deactivate : []).slice(0, 50)) await pcCall(env, info, "/vectors/update", { id: String(other), setMetadata: { active: false }, namespace: "" });    // one active zone style at a time
      return json({ ok: true, item: { item_id: id, kind, name, active: Boolean(b.active), saved_at: savedAt, source: meta.source_name, payload: b.payload ?? {} } });
    }
    if (b.op === "active") { await pcCall(env, info, "/vectors/update", { id: String(b.id || ""), setMetadata: { active: Boolean(b.active) }, namespace: "" }); return json({ ok: true }); }
    if (b.op === "delete") { await pcCall(env, info, "/vectors/delete", { ids: [String(b.id || "")], namespace: "" }); return json({ ok: true }); }
    return fail(400, "Unknown request.");
  } catch (e) { pcInfo = null; return fail(502, String(e.message || e).slice(0, 200)); }
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url), p = url.pathname;
    if (!p.startsWith("/api/")) return env.ASSETS ? env.ASSETS.fetch(request) : fail(404, "Not found.");
    try {
      if (p === "/api/health") {                        // which settings exist (true/false only, never a value)
        const has = (n) => Boolean(env[n] && String(env[n]).trim());
        return json({ sign_in: has("AUTH_SECRET") && (has("ADMIN_PASS") || has("MEMBER_PASS")), storage: has("SUPABASE_URL") && has("SUPABASE_KEY"), ai: has("GEMINI_KEY"), playbook: has("PINECONE_KEY") });
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
      if (p === "/api/screener/holders") return screenerHolders(url, ctx);
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
      if (p === "/api/desk") return deskFile(env, ctx, url);
      if (p === "/api/deals") return deals(env, ctx);
      if (p === "/api/brief") return brief(env, ctx, url, request, me);
      if (p === "/api/market") return market(env, ctx, url);
      if (p === "/api/scan") return scanFile(env, ctx, url);
      if (p === "/api/setups") return setups(env, request, url, me);
      if (p === "/api/ai") return ai(env, ctx, request);
      if (p === "/api/ai/arka") return arkaAi(env, ctx, request);
      if (p === "/api/ai/upload") return aiUpload(env, request);
      if (p === "/api/arka") return arkaStore(env, ctx, request, url);
      if (["/api/portfolio", "/api/portfolio/sell", "/api/portfolio/plan", "/api/portfolio/move", "/api/portfolio/snapshot", "/api/portfolio/settings"].includes(p)) return portfolio(env, request, url, p.slice("/api/portfolio".length));
      return fail(404, "Not found.");
    } catch (e) {
      return fail(500, "Something went wrong on the server.");
    }
  },
};

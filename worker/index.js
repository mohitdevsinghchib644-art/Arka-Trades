// Arka Trades · API (stage 4: adds the nightly market files, starting with the Regime page). Runs on Cloudflare for every request to /api/*.
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
  const m = res.meta, closes = ((res.indicators || {}).quote || [{}])[0].close || [];
  const price = m.regularMarketPrice, prev = m.chartPreviousClose ?? m.previousClose;
  if (price == null) return { sym, ok: false };
  return { sym, ok: true, name: m.longName || m.shortName || "", price, prev, chg: prev ? price - prev : null, pct: prev ? (price / prev - 1) * 100 : null,
           high: m.regularMarketDayHigh ?? null, low: m.regularMarketDayLow ?? null, vol: m.regularMarketVolume ?? null,
           hi52: m.fiftyTwoWeekHigh ?? null, lo52: m.fiftyTwoWeekLow ?? null, t: m.regularMarketTime ?? null, cur: m.currency || "", spark: thin(closes, 36) };
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
  res.timestamp.forEach((t, i) => { const c = q.close && q.close[i]; if (c != null) rows.set(dayOf(t, off), [r2(c), r2(q.high && q.high[i]), r2(q.low && q.low[i]), (q.volume && q.volume[i]) || 0]); });
  const d = [...rows.keys()].sort(), col = (k) => d.map((x) => rows.get(x)[k]);
  const ev = res.events || {}, list = (o, f) => Object.values(o || {}).map(f).filter((x) => x[1] > 0).sort((a, b) => (a[0] < b[0] ? -1 : 1));
  return { sym, ok: d.length > 0, d, c: col(0), h: col(1), l: col(2), v: col(3),
           splits: list(ev.splits, (x) => [dayOf(x.date, off), x.denominator ? x.numerator / x.denominator : 0]),
           divs: list(ev.dividends, (x) => [dayOf(x.date, off), +Number(x.amount).toFixed(4)]) };
}
async function history(url, ctx) {
  const list = [...new Set((url.searchParams.get("s") || "").toUpperCase().split(",").map((s) => s.trim()).filter((s) => SYM_OK.test(s)))].slice(0, 6);     // few per call: a free Worker has little CPU time per request
  const range = ["1y", "2y", "5y", "10y"].includes(url.searchParams.get("range")) ? url.searchParams.get("range") : "5y";
  const out = await Promise.all(list.map(async (sym) => {
    try { return historyFrom(sym, await yahoo(`/v8/finance/chart/${encodeURIComponent(ySym(sym))}?range=${range}&interval=1d&events=div%2Csplits`, ctx, 1800)); }
    catch { return { sym, ok: false }; }
  }));
  return json({ history: out, at: Date.now() }, 200, { "cache-control": "private, max-age=600" });
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
async function watchlist(env, request, url) {
  try {
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
      const [lots, sells, plans, meta] = await Promise.all([optional(env, "portfolio?select=id,symbol,name,quantity,buy_price,buy_date,note&order=buy_date.asc.nullsfirst,id.asc"),
        optional(env, "portfolio_sells?select=id,book,symbol,quantity,sell_price,sell_date,note&order=sell_date.asc,id.asc"), optional(env, "portfolio_plan?select=book,symbol,stop_loss,target"),
        optional(env, "portfolio_lot_meta?select=lot_id,book")]);
      const plan = {}, bookOf = {};                      // plans are kept per book, as "Book|SYMBOL"
      for (const r of plans.rows) plan[(r.book || "Main") + "|" + cleanSym(r.symbol)] = { stop: r.stop_loss == null ? null : Number(r.stop_loss), target: r.target == null ? null : Number(r.target) };
      for (const r of meta.rows) if (r.lot_id != null) bookOf[String(r.lot_id)] = r.book || "Main";
      return json({ table: lots.ok, can_sell: sells.ok, can_plan: plans.ok, can_book: meta.ok, plan,
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
async function repoFile(env, ctx, path, ttl = 600) {      // -> text, or null when the file is not there
  const url = `https://raw.githubusercontent.com/${env.GITHUB_REPO || REPO}/main/${path}`;
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
const MARKET_FILES = /^(regime_nifty(500|200|100)|meta)$/;
async function market(env, ctx, url) {
  const f = url.searchParams.get("f") || "";
  if (!MARKET_FILES.test(f)) return fail(400, "Unknown market file.");
  const text = await repoFile(env, ctx, `arka_data/market/${f}.json`, 900);
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
      if (p === "/api/search") return search(url, ctx);
      if (p === "/api/watchlist") return watchlist(env, request, url);
      if (p === "/api/signals") return signals(env, ctx);
      if (p === "/api/deals") return deals(env, ctx);
      if (p === "/api/brief") return brief(env, ctx);
      if (p === "/api/market") return market(env, ctx, url);
      if (p === "/api/portfolio" || p === "/api/portfolio/sell" || p === "/api/portfolio/plan" || p === "/api/portfolio/move") return portfolio(env, request, url, p.slice("/api/portfolio".length));
      return fail(404, "Not found.");
    } catch (e) {
      return fail(500, "Something went wrong on the server.");
    }
  },
};

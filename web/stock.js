// Arka Trades · Stock page. The classic security workspace (app.py _render_security_workspace) and the Research terminal
// (research_page.py v16.1) on one page: quote header, actions, session / fundamentals / trend check / key levels, the pro chart,
// about, then the 39 research screens. The maths lives in research.js and rsengine.js; this file loads the data and draws.
import { S, api, paint, esc, num, sgn, pc, cls, nice, toast, need } from "./app.js";
import * as R from "./research.js";
import * as E from "./rsengine.js";
import { parseScreener, parsePeers, empty } from "./screener.js";
import { lineChart } from "./charts.js";
import * as NW from "./news.js";

const COL = { g: "#30D158", r: "#FF453A", a: "#FF9F0A", c: "#5AC8FA", p: "#BF5AF2", y: "#FFD60A", t2: "#A3A3A3", t3: "#6B6B6B", w: "#EDEDED" };
const LIB = '<script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>\n'
  + "<script>if(!window.LightweightCharts){document.write('<script src=\"https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js\"><\\/script>');}</script>";
export const stockDefaults = () => ({ sym: null, code: "OV", data: {}, o: {}, ix: null, ixAt: 0, tpl: null, csv: {}, grp: null });

// ── loading ──
const istToday = () => new Date(Date.now() + 330 * 60000).toISOString().slice(0, 10);
const marketOpenNow = () => { const t = new Date(Date.now() + 330 * 60000), m = t.getUTCHours() * 60 + t.getUTCMinutes(), d = t.getUTCDay(); return d > 0 && d < 6 && m >= 555 && m <= 930; };
function dataOf(sym) { return (S.sk.data[sym] = S.sk.data[sym] || { err: {}, busy: {}, at: 0 }); }
async function step(D, key, fn) {
  D.busy[key] = true;
  try { await fn(); D.err[key] = ""; } catch (e) { D.err[key] = e.message || "failed"; }
  D.busy[key] = false; paint();
}
export async function open(sym, code) {
  const K = S.sk; sym = String(sym || "").toUpperCase();
  if (K.sym !== sym) { K.o = {}; K.grp = null; }
  K.sym = sym; if (code && R.FN[code]) K.code = code; remember(sym);
  const D = dataOf(sym);
  need([sym]);
  if (D.at && Date.now() - D.at < 10 * 60000) return;
  D.at = Date.now();
  if (!K.ix || Date.now() - K.ixAt > 30 * 60000) { K.ixAt = Date.now();
    api("/api/history?s=%5ENSEI,%5ECRSLDX&range=5y").then((d) => { const ix = {}; for (const h of d.history || []) if (h.ok) ix[h.sym === "^NSEI" ? "NIFTY 50" : "NIFTY 500"] = h; K.ix = ix; paint(); }).catch(() => { K.ixAt = 0; }); }
  if (!S.sig) api("/api/signals").then((d) => { S.sig = d; paint(); }).catch(() => {});
  step(D, "H", async () => { const d = await api(`/api/history?s=${encodeURIComponent(sym)}&range=max`), h = (d.history || [])[0];
    if (!h || !h.ok) throw new Error("Yahoo Finance returned no price history");
    if (h.daily === false) throw new Error("Yahoo Finance sent weekly or monthly prices instead of daily ones");       // never compute 10-day or 50-day numbers from monthly candles
    D.Hfull = h; D.H = E.hist(h, "5y"); D.divs = h.divs || []; D.splits = h.splits || []; });
  step(D, "B", async () => {
    const d = await api("/api/screener?s=" + encodeURIComponent(sym));
    D.B = d.ok ? parseScreener(d.html, d.url, sym) : empty(sym, d.err); D.Bstale = !!d.stale;
    if (D.B.bse) step(D, "anns", async () => { const a = await api(`/api/bse?code=${D.B.bse}&days=365`); D.anns = a.rows || []; if (!a.ok) throw new Error(a.err || "BSE filings unavailable"); });
    else D.anns = [];
    for (const id of D.B.peer_ids || []) { try { const p = await api("/api/screener/peers?id=" + id); if (p.ok) { const rec = parsePeers(p.html); if (rec) { D.peers = rec; break; } } } catch { /* next */ } }
  });
  step(D, "Y", async () => { D.Y = await api("/api/yinfo?s=" + encodeURIComponent(sym)); if (!D.Y.ok) throw new Error(D.Y.err || "Yahoo company details unavailable"); });
  step(D, "dlv", async () => { const d = await api("/api/dlv?s=" + encodeURIComponent(sym));
    if (!d.d) { D.dlv = null; D.dlvErr = d.err; return; }
    D.dlv = { d: d.d, c: d.c, p: d.p, v: d.v, q: d.q, pct: d.q.map((q, i) => (q != null && d.v[i] > 0 ? (q / d.v[i]) * 100 : null)) }; });
}
function lazy(D, sym, code) {                                // sources only some screens need
  if (code === "CN" && !D.news && !D.busy.news) step(D, "news", async () => {
    const [items, ip] = await Promise.all([NW.securityNews(sym), api("/api/intraday?s=" + encodeURIComponent(sym)).catch(() => ({ pts: [] }))]); D.news = items; D.pts = ip.pts || []; });
  if (code === "NOTE" && D.note === undefined && !D.busy.note) step(D, "note", async () => { D.note = null; const d = await api("/api/note?s=" + encodeURIComponent(sym)); D.note = d.note || {}; });
  if (code === "COMP") {
    const want = compNames(sym).filter((n) => n !== sym && !R.IDX[n] && !(D.cmp && n in D.cmp));
    if (want.length && !D.busy.cmp) step(D, "cmp", async () => { D.cmp = D.cmp || {}; const d = await api(`/api/history?s=${want.map(encodeURIComponent).join(",")}&range=2y`);
      for (const n of want) { const h = (d.history || []).find((x) => x.sym === n); D.cmp[n] = h && h.ok ? { d: h.d, v: h.c } : null; } });
  }
  if (code === "RV" && !D.peers && R.SECTOR_PEERS[sym]) need(R.SECTOR_PEERS[sym]);
  if (code === "MF" && D.B && D.B.resolved) {                // named holders over 1%, from Screener's shareholding breakdown
    const o = S.sk.o, c = o.mfc === "Foreign institutions" ? "foreign_institutions" : "domestic_institutions", p = (o.mfp || "Quarterly").toLowerCase(), key = c + "/" + p;
    D.hold = D.hold || {};
    if (!(key in D.hold) && !D.busy["hold:" + key]) step(D, "hold:" + key, async () => {
      let last = "Screener's company id is not on its page";
      for (const id of D.B.peer_ids || []) {
        const d = await api(`/api/screener/holders?id=${id}&c=${c}&p=${p}`).catch((e) => ({ ok: false, err: e.message }));
        if (d.ok) { D.hold[key] = d.data; return; }
        last = d.err || last;
      }
      D.hold[key] = null; throw new Error(last);
    });
  }
}
const compNames = (sym) => String(S.sk.o.compq ?? defaultComp(sym)).split(",").map((t) => t.trim().toUpperCase()).filter(Boolean).slice(0, 6);
const defaultComp = (sym) => (R.SECTOR_PEERS[sym] || []).filter((p) => p !== sym).join(", ") || "NIFTY 500, NIFTY 50";

// ── the signal desk for one stock (fno_page.signal_for) ──
const px_ = (x) => (x == null || !isFinite(x) ? "—" : Math.abs(x) >= 5000 ? E.fx(x, 0) : Math.abs(x) >= 100 ? E.fx(x, 1) : E.fx(x, 2));
const pct_ = (x, d = 1) => (x == null || !isFinite(x) ? "—" : E.fx(x * 100, d, false, true) + "%");
const day_ = (iso) => { const t = new Date(String(iso).slice(0, 10) + "T00:00:00Z"); return isNaN(t) ? "—" : `${t.getUTCDate()} ${t.toLocaleString("en-GB", { month: "short", timeZone: "UTC" })} ${t.getUTCFullYear()}`; };
export function signalFor(sym) {                             // what the Arka Model says about this stock tonight
  const d = S.md && S.md.d; if (!d) return ["", ""];
  for (const [k, word] of [["long", "buy"], ["short", "sell short"]]) { const B = d.books[k], t = B.status === "live" ? "" : " (in test)", r = B.ranked.find((x) => x.sym === sym), p = B.positions.find((x) => x.sym === sym);
    if (B.picks.includes(sym) && r) return [`Arka Model: ${word} at the next open` + t, `rank ${r.rank} · win chance ${r.prob ?? "—"}% · stop ${px_(r.stop)} · target ${px_(r.target)}`];
    if (p) return [`Arka Model: open ${k} trade` + t, `entry ${px_(p.entry)} · stop ${px_(p.stop)} · target ${px_(p.target)} · ${p.left} sessions left`]; }
  const dec = (d.decisions || {})[sym], rl = d.books.long.ranked.find((x) => x.sym === sym), rs = d.books.short.ranked.find((x) => x.sym === sym);
  if (dec === "AVOID") return ["Arka Model: avoid for longs tonight", "in the lowest long ranks"];
  if (rl) return [`Arka Model: long rank ${rl.rank} tonight`, dec === "WATCH" ? "on watch, not a buy tonight" : "in the top 25"];
  if (rs) return [`Arka Model: short rank ${rs.rank} tonight`, dec === "WATCH SHORT" ? "on watch, not a short tonight" : "in the short top 25"];
  return ["No model signal", ""];
}

// ── the context the research screens read ──
function ctx(sym) {
  const K = S.sk, D = dataOf(sym), ix2 = {};
  for (const [k, h] of Object.entries(K.ix || {})) { const c = E.hist(h, "2y"); ix2[k] = { d: c.d, v: c.c }; }
  const desk = signalFor(sym), q = {};
  for (const p of R.SECTOR_PEERS[sym] || []) { const x = S.q[p]; if (x && x.ok) q[p] = { price: x.price, pct: x.pct }; }
  return { sym, B: D.B || empty(sym, D.busy.B ? "Loading Screener…" : D.err.B || undefined), H: D.H || null, IX: ix2, Y: D.Y && D.Y.ok ? D.Y : null, divs: D.divs || [], splits: D.splits || [],
    dlv: D.dlv || null, dlvInfo: { error: D.dlvErr || D.err.dlv }, anns: D.anns || [], today: istToday(), open: marketOpenNow(), o: { ...K.o, comp: compNames(sym) }, peers: D.peers || null,
    quotes: q, news: D.news || [], cmp: D.cmp || {}, note: D.note, hold: D.hold || {}, holdErr: Object.fromEntries(Object.entries(D.err).filter(([k]) => k.startsWith("hold:")).map(([k, v]) => [k.slice(5), v])), holdBusy: Object.keys(D.busy).filter((k) => k.startsWith("hold:") && D.busy[k]).map((k) => k.slice(5)), fno: desk[0] && !desk[0].startsWith("No model signal") && !!desk[0] ? [["Signal", desk[0]], ...(desk[1] ? [["Detail", desk[1]]] : [])] : null,
    fnoWhy: "Positioning (who holds what, option walls) is on the Signals page. " + (desk[0] ? desk[0] + (desk[1] ? " · " + desk[1] : "") + "." : ""),
    status: { notes: S.me && S.me.role ? "configured" : "on demand" } };
}

export function newsFor(sym) { const D = dataOf(sym); lazy(D, sym, "CN"); return D.news || null; }       // for the news panel's stock view

// ── page ──
const cell = (k, v, c = "") => `<div class="sw-st"><small>${k}</small><b class="${c}">${v}</b></div>`;
const r2 = (v) => (v == null ? "—" : "₹" + num(v, 2));
const rng = (lo, hi, px, l, r, mid) => (lo == null || hi == null || px == null || hi <= lo ? "" : (() => { const p = Math.max(0, Math.min(100, ((px - lo) / (hi - lo)) * 100));
  return `<div class="sw-rg"><div class="t"><span>${l}</span><span class="m">${mid.replace("{pos}", num(p, 0))}</span><span>${r}</span></div><div class="b"><u style="width:${p.toFixed(1)}%"></u><i style="left:${p.toFixed(1)}%"></i></div></div>`; })());
const fmtNum = (v, pre, suf) => { const x = E.f(v); return x == null ? "—" : pre + num(x, x % 1 ? 2 : 0) + suf; };
function parts() {
  const K = S.sk, sym = K.sym;
  const D = dataOf(sym), B = D.B || {}, s = B.summary || {}, sec = B.sector || {}, H = D.H, m = H ? R.swMetrics(H) : {}, q = S.q[sym] && S.q[sym].ok ? S.q[sym] : null;
  const price = q ? q.price : m.price ?? E.f(s.current_price), chg = q ? q.pct : m.chg, pts = q ? q.chg : m.chg_abs;
  const hi52 = m.hi52 ?? E.f(s.year_high), lo52 = m.lo52 ?? E.f(s.year_low), name = B.name || (q && q.name) || sym;
  const tags = [...new Set(["NSE", sec.Sector || sec["Broad Sector"] || "", sec.Industry || sec["Broad Industry"] || ""])].filter((t) => t && t !== "—").map((t) => `<span class="sw-tag">${esc(t)}</span>`).join("");
  const rets = Object.entries(m.rets || {}).map(([k, v]) => `<div class="sw-rt"><small>${k}</small><b class="${cls(v)}">${pc(v, 1)}</b></div>`).join("");
  const asof = m.date ? `${marketOpenNow() ? "Last price" : "Closing price"} · ${m.date}` : "";
  const inWl = S.wl.rows.some((r) => r.sym === sym), held = S.pf.lots.some((l) => l.sym === sym);
  const loading = D.busy.H && !H ? '<div class="note">Loading prices…</div>' : D.err.H ? `<div class="note">Price history could not be loaded right now (${esc(D.err.H)}), so ranges and trend are missing. Reopen the stock in a minute to retry.</div>` : "";
  const bNote = D.err.B || (B.resolved === false && !D.busy.B) ? `<div class="note">Fundamentals from Screener could not be loaded right now; the chart and technicals still work. ${esc(B.reason || "")}</div>` : D.Bstale ? '<div class="note">Screener did not answer; showing its last saved copy.</div>' : "";
  const hero = `<div class="sw-hero"><div class="sw-top"><div class="sw-id"><div class="sw-av">${esc(sym.slice(0, 2))}</div><div class="sw-nm"><div class="s">${esc(sym)}</div><div class="n">${esc(name)}</div><div class="sw-tags">${tags}</div></div></div>
    <div class="sw-px"><div class="v">${price != null ? r2(price) : '<span class="sk">0000.00</span>'}</div>${chg != null ? `<div class="sw-chg ${cls(chg)}"><span>${sgn(pts)}</span><b>${chg >= 0 ? "▲" : "▼"} ${num(Math.abs(chg), 2)}%</b></div>` : ""}<div class="a">${esc(asof)}</div></div></div>
    ${rets ? `<div class="sw-rets">${rets}</div>` : ""}</div>`;
  const acts = `<div class="sw-act"><button class="btn ${inWl ? "" : "pri"}" data-act="${inWl ? "remove" : "add"}" data-s="${esc(sym)}">${inWl ? "✓ In watchlist" : "+ Add to watchlist"}</button>
    <button class="btn" data-act="buyform" data-s="${esc(sym)}">${held ? "Add another buy" : "Add to portfolio"}</button>
    <button class="btn" data-act="sksheet" data-s="${esc(sym)}">${inWl || held ? "Note, alerts and position" : "Quick view"}</button>
    <a class="btn" href="#/arka/${encodeURIComponent(sym)}">Arka AI analysis</a>
    <button class="btn" data-act="skgo" data-v="research">Full research ↓</button></div>`;
  const rv = m.rel_vol;
  const session = H ? `<div class="sw-card"><div class="sw-h">Session<span>${esc(m.date || "")}</span></div><div class="sw-grid">${cell("Open", r2(m.open))}${cell("Previous close", r2(m.prev))}${cell("Day high", r2(m.day_hi))}${cell("Day low", r2(m.day_lo))}
    ${cell("Volume", swQty(m.vol))}${cell("Volume vs 20-day average", rv != null ? num(rv, 2) + "×" : "—", rv != null && rv >= 1.5 ? "up" : "")}</div>
    ${rng(m.day_lo, m.day_hi, price, "Low " + r2(m.day_lo), "High " + r2(m.day_hi), "Today's range")}${rng(lo52, hi52, price, "Low " + r2(lo52), "High " + r2(hi52), "52-week range · {pos}%")}</div>` : "";
  const funda = `<div class="sw-card"><div class="sw-h">Fundamentals<span>Screener.in</span></div><div class="sw-grid">${cell("Market cap", esc(fmtNum(s.market_cap, "₹", " Cr")))}${cell("P/E", esc(fmtNum(s.pe_ratio, "", "×")))}
    ${cell("ROE", esc(fmtNum(s.roe, "", "%")))}${cell("ROCE", esc(fmtNum(s.roce, "", "%")))}${cell("Book value", esc(fmtNum(s.book_value, "₹", "")))}${cell("Dividend yield", esc(fmtNum(s.dividend_yield, "", "%")))}</div>${D.busy.B ? '<div class="note">Loading Screener…</div>' : ""}</div>`;
  const checks = [], f2 = (v) => r2(v);
  const above = (lab, k) => { const v = m[k]; if (v == null || price == null) return; const d = v ? (price / v - 1) * 100 : null; checks.push([price >= v, `Price ${price >= v ? "above" : "below"} ${lab}`, `${f2(v)} · ${pc(d, 1)}`]); };
  above("10-day EMA", "ema10"); above("21-day EMA", "ema21"); above("50-day average", "sma50"); above("200-day average", "sma200");
  if (m.sma50 != null && m.sma200 != null) { const ok = m.sma50 >= m.sma200; checks.push([ok, `50-day average ${ok ? "above" : "below"} 200-day`, pc((m.sma50 / m.sma200 - 1) * 100, 1)]); }
  if (price != null && hi52) { const off = (price / hi52 - 1) * 100; checks.push([off >= -15, off >= -15 ? "Within 15% of 52-week high" : "More than 15% below 52-week high", pc(off, 1)]); }
  const passed = checks.filter((c) => c[0]).length, tone = passed >= checks.length - 1 ? "up" : passed <= 1 ? "dn" : "mid";
  const extra = (m.rsi != null ? cell("RSI 14", num(m.rsi, 0), m.rsi >= 75 ? "dn" : "") : "") + (m.atr_pct != null ? cell("Average daily range (ATR 14)", `${num(m.atr_pct, 1)}% · ${f2(m.atr)}`) : "");
  const trend = checks.length ? `<div class="sw-card"><div class="sw-h">Trend check<span class="sw-score ${tone}">${passed} of ${checks.length}</span></div>${checks.map(([ok, t, v]) => `<div class="sw-ck ${ok ? "y" : "n"}"><i>${ok ? "✓" : "✕"}</i><span>${esc(t)}</span><b>${esc(v)}</b></div>`).join("")}
    ${extra ? `<div class="sw-grid two">${extra}</div>` : ""}<div class="sw-note">Facts from daily closing prices. Not a buy or sell signal.</div></div>` : "";
  const lv = [["52-week high", hi52], ["20-day high", m.hi20], ["Previous day high", m.pdh], ["10-day EMA", m.ema10], ["21-day EMA", m.ema21], ["50-day average", m.sma50], ["200-day average", m.sma200],
    ["Previous day low", m.pdl], ["20-day low", m.lo20], ["52-week low", lo52]].filter(([, v]) => v != null).sort((a, b) => b[1] - a[1]);
  let ladder = "";
  if (lv.length && price != null) { const out = []; let placed = false;
    for (const [k, v] of lv) { if (!placed && v < price) { out.push(`<div class="sw-lv cur"><span>Price now</span><b>${f2(price)}</b><em></em></div>`); placed = true; }
      out.push(`<div class="sw-lv ${v >= price ? "r" : "s"}"><span>${k}</span><b>${f2(v)}</b><em>${pc((v / price - 1) * 100, 1)}</em></div>`); }
    if (!placed) out.push(`<div class="sw-lv cur"><span>Price now</span><b>${f2(price)}</b><em></em></div>`);
    ladder = `<div class="sw-card"><div class="sw-h">Key levels<span>distance from price</span></div>${out.join("")}</div>`; }
  const about = (B.about || "").trim(), sectorTxt = sec.Sector || sec["Broad Sector"] || "";
  const desc = about ? about.slice(0, 520) + (about.length > 520 ? "…" : "") : `${name} is listed on NSE${sectorTxt ? ", classified under " + sectorTxt : ""}.`;
  const aboutC = `<div class="sw-card"><div class="sw-h">About</div><div class="sw-about">${esc(desc)}</div></div>`;
  const phone = innerWidth < 900;
  return { phone, a: `<button class="sw-back" data-act="skback">← Back</button>${hero}${acts}${loading}${bNote}`, b1: phone ? "" : `<div class="sw-two">${session}${funda}</div>`,
    b2: phone ? `<div class="sw-stack">${trend}${ladder}${session}${funda}${aboutC}</div>` : `<div class="sw-stack side">${trend}${ladder}</div>`, b3: phone ? "" : `<div class="sw-stack">${aboutC}</div>`,
    chart: proChart(sym, phone ? 480 : 640), r: research(sym, D) };
}
// ── the Research landing page (#/research): find a stock, or reopen a recent one ──
const recent = () => { try { return JSON.parse(localStorage.getItem("arka_recent") || "[]"); } catch { return []; } };
function remember(sym) { try { localStorage.setItem("arka_recent", JSON.stringify([sym, ...recent().filter((x) => x !== sym)].slice(0, 12))); } catch { /* private mode */ } }
let findTimer = 0, findSeq = 0;
export function landing() {
  const K = S.sk, chip = (s) => `<button class="rs-pick" data-sym="${esc(s)}" data-fromsearch="1"><b>${esc(s)}</b>${S.q[s] && S.q[s].ok ? `<i class="${cls(S.q[s].pct)}">${pc(S.q[s].pct, 1)}</i>` : ""}</button>`;
  const rec = recent(), mine = [...new Set([...S.pf.lots.map((l) => l.sym), ...S.wl.rows.map((r) => r.sym)])].filter((x) => !/[\^=.]/.test(x));
  const res = K.found == null ? "" : K.found.length ? `<div class="rs-res">${K.found.map((x) => `<button data-sym="${esc(x.sym)}" data-fromsearch="1"><b>${esc(x.sym)}</b><small>${esc(x.name)}</small></button>`).join("")}</div>` : '<div class="note">No NSE stock found. Try the ticker, for example HDFCBANK.</div>';
  return `<div class="rs-land"><div class="hd"><div><h1>Research</h1><div class="sub">Price chart, financials, valuation, quality, ownership, filings and news for any NSE stock: 39 screens, the same as the classic Research page.</div></div></div>
    <div class="rs-find"><input data-rs="find" value="${esc(K.find || "")}" placeholder="Type a stock: TCS, Reliance, HDFC…" autocomplete="off" spellcheck="false" aria-label="Find a stock"></div>${res}
    ${rec.length ? `<h2>Recently opened</h2><div class="rs-picks">${rec.map(chip).join("")}</div>` : ""}
    ${mine.length ? `<h2>Your stocks <span>watchlist and holdings</span></h2><div class="rs-picks">${mine.slice(0, 40).map(chip).join("")}</div>` : ""}
    <div class="note" style="padding:14px 2px">You can also open a stock from the search at the top of any page, or from the “Open the stock page” button when you tap a stock in a list.</div></div>`;
}
function find(text) {
  const K = S.sk; K.find = text; clearTimeout(findTimer);
  if (text.trim().length < 2) { K.found = null; return paint(); }
  findTimer = setTimeout(async () => { const n = ++findSeq; try { const d = await api("/api/search?q=" + encodeURIComponent(text.trim())); if (n === findSeq) { K.found = d.results || []; paint(); } } catch { /* keep the old list */ } }, 250);
}

export function page() {                                     // the first paint of the page (later paints update the parts, see paintInto)
  const K = S.sk; if (!K.sym) return '<div class="card"><div class="note">Pick a stock from the search.</div></div>';
  const P = parts();
  return `<div class="stk" data-sym="${esc(K.sym)}" data-w="${P.phone ? "p" : "d"}" data-ck="${esc(dataOf(K.sym).chartKey || "")}"><div id="sk-a">${P.a}</div><div id="sk-b1">${P.b1}</div>
    <div class="sw-main"><div><div class="sw-sec">Price chart</div><div id="sk-chart">${P.chart}</div></div><div id="sk-b2">${P.b2}</div></div><div id="sk-b3">${P.b3}</div>
    <div class="sw-sec" id="sk-research" style="margin-top:18px">Research</div><div id="sk-r">${P.r}</div></div>`;
}
// Repaint without touching the chart frame (a frame reloads whenever it is redrawn) and without wiping a note being typed.
export function paintInto(main) {
  const K = S.sk, box = main.querySelector(".stk");
  if (!K.sym || !box || box.dataset.sym !== K.sym || box.dataset.w !== (innerWidth < 900 ? "p" : "d")) { main.innerHTML = page(); return; }
  const ae = document.activeElement, rsBox = main.querySelector("#sk-r"), inR = ae && rsBox && rsBox.contains(ae) && /INPUT|TEXTAREA|SELECT/.test(ae.tagName);
  const keep = inR && ae.dataset.rs ? [ae.dataset.rs, ae.selectionStart, ae.selectionEnd] : null;
  const P = parts();
  for (const id of ["a", "b1", "b2", "b3"]) { const el = main.querySelector("#sk-" + id); if (el.innerHTML !== P[id]) el.innerHTML = P[id]; }
  const ck = dataOf(K.sym).chartKey || "";
  if (box.dataset.ck !== ck || !main.querySelector("#sk-chart iframe")) { box.dataset.ck = ck; main.querySelector("#sk-chart").innerHTML = P.chart; }
  if (inR && !keep && !K.force) return;                       // typing in the note: leave the research part alone
  K.force = false; rsBox.innerHTML = P.r;
  if (keep) { const el = rsBox.querySelector(`[data-rs="${keep[0]}"]`); if (el) { el.focus(); try { el.setSelectionRange(keep[1], keep[2]); } catch { /* not a text box */ } } }
}
const swQty = (v) => { if (v == null || !isFinite(v)) return "—"; for (const [d, s] of [[1e7, " Cr"], [1e5, " L"], [1e3, " K"]]) if (Math.abs(v) >= d) return E.fx(v / d, 2) + s; return E.fx(v, 0); };

// ── the pro chart (chart_engine.py, drawn with TradingView's Lightweight Charts in a frame) ──
// ── the index the RS line is measured against (chart_engine.RS_INDICES, rs_candidates, render_rs_index_setting) ──
export const RS_INDICES = { "NIFTY 50": ["^NSEI"], "NIFTY 500": ["^CRSLDX"], "NIFTY NEXT 50": ["^NSMIDCP"], "NIFTY MIDCAP 100": ["NIFTY_MIDCAP_100.NS", "^CRSMID"], "NIFTY SMALLCAP 100": ["^CNXSC"], "BANK NIFTY": ["^NSEBANK"],
  "NIFTY IT": ["^CNXIT"], "NIFTY AUTO": ["^CNXAUTO"], "NIFTY PHARMA": ["^CNXPHARMA"], "NIFTY FMCG": ["^CNXFMCG"], "NIFTY METAL": ["^CNXMETAL"], "NIFTY ENERGY": ["^CNXENERGY"],
  "NIFTY MIDSMALLCAP 400": ["NIFTYMIDSML400.NS", "NIFTY_MIDSML_400.NS"], "NIFTY PSU BANK": ["^CNXPSUBANK"], "NIFTY REALTY": ["^CNXREALTY"] };
export function rsCandidates(name) {                         // Yahoo tickers to try for an index name: a known label, or what was typed (NSE:NIFTYMIDSML400, ^CNXIT, NIFTY_FIN_SERVICE.NS …)
  const raw = String(name || "").trim(); if (RS_INDICES[raw.toUpperCase()]) return RS_INDICES[raw.toUpperCase()];
  const t = raw.toUpperCase().replace("NSE:", "").replace("BSE:", "").trim();
  if (!t || !/^[\^A-Z0-9_.&\- ]{1,40}$/.test(t)) return [];
  if (t.startsWith("^") || t.endsWith(".NS") || t.endsWith(".BO")) return [t];
  const compact = t.replace(/ /g, ""), under = t.replace(/ /g, "_");
  return [...new Set([compact + ".NS", under + ".NS", "^" + compact, "^CNX" + compact.replace("NIFTY", "")])];
}
export const rsLabel = () => { let v = ""; try { v = (localStorage.getItem("arka_rs_index") || "").trim(); } catch { v = ""; } v = v || "NIFTY 50"; return RS_INDICES[v.toUpperCase()] ? v.toUpperCase() : v; };
async function loadRsIndex(label) {                          // the first ticker that answers with enough history wins
  const K = S.sk; K.rsIx = K.rsIx || {};
  if (K.rsIx[label] && (K.rsIx[label].h || Date.now() - K.rsIx[label].at < 600000)) return K.rsIx[label].h;
  K.rsIx[label] = { h: null, at: Date.now(), busy: true };
  for (const t of rsCandidates(label)) { try { const d = await api("/api/history?range=5y&s=" + encodeURIComponent(t)), h = (d.history || [])[0]; if (h && h.ok && h.d.length >= 60) { K.rsIx[label] = { h, at: Date.now() }; break; } } catch { /* next ticker */ } }
  K.rsIx[label].busy = false; return K.rsIx[label].h;
}
export async function setRsIndex(name) {                     // pick an index; it is kept only if its history loads
  const K = S.sk, raw = String(name || "").trim(); if (!raw) return;
  const label = RS_INDICES[raw.toUpperCase()] ? raw.toUpperCase() : raw, cur = rsLabel();
  K.rsMsg = "Loading " + label + "…"; K.rsOther = !RS_INDICES[label]; rsBar();
  const h = await loadRsIndex(label);
  if (!h) { K.rsMsg = `No price history found for “${label}”. Check the symbol; the RS line keeps using ${cur}.`; return rsBar(); }
  try { localStorage.setItem("arka_rs_index", label); } catch { /* private mode */ }
  K.rsMsg = ""; paint();
}
function rsBarHtml() {
  const K = S.sk, cur = rsLabel(), known = Object.keys(RS_INDICES), other = K.rsOther || !RS_INDICES[cur];
  return `<span>RS line compares with</span><select data-rs="rsix" aria-label="Index for the RS line">${known.map((n) => `<option${!other && n === cur ? " selected" : ""}>${n}</option>`).join("")}<option value="__other"${other ? " selected" : ""}>Another index (type its symbol)</option></select>` +
    (other ? `<input data-rs="rsixc" value="${esc(RS_INDICES[cur] ? "" : cur)}" placeholder="e.g. NIFTYMIDSML400, NIFTY_FIN_SERVICE.NS, ^CNXIT" autocomplete="off" spellcheck="false" aria-label="Index symbol">` : "") + (K.rsMsg ? `<em>${esc(K.rsMsg)}</em>` : "");
}
function rsBar() { const el = document.querySelector(".pc-rs"); if (el) el.innerHTML = rsBarHtml(); }
function proChart(sym, h) {
  const K = S.sk, D = dataOf(sym);
  if (!D.Hfull) return `<div class="card pc-wait" style="height:${Math.min(h, 360)}px"><div class="note">${D.err.H ? "No chart: Yahoo Finance returned no price history. Try again in a minute." : "Loading the chart…"}</div></div>`;
  if (!K.tpl) { if (!K.tplBusy) { K.tplBusy = true; fetch("/chart.html").then((r) => r.text()).then((t) => { K.tpl = t; paint(); }).catch(() => { K.tplBusy = false; }); } return `<div class="card" style="height:${Math.min(h, 360)}px"><div class="note">Loading the chart…</div></div>`; }
  const label = rsLabel(), rx = (K.rsIx || {})[label];
  if (!rx) loadRsIndex(label).then(() => paint());
  const key = `${sym}|${D.Hfull.d.length}|${label}|${rx && rx.h ? 1 : 0}`;
  if (D.chartKey !== key) { D.chartKey = key; D.chartDoc = chartDoc(sym, D.Hfull); }
  return `<div class="pc-rs">${rsBarHtml()}</div><iframe class="pc" title="${esc(sym)} chart" style="height:${h}px" srcdoc="${esc(D.chartDoc)}" data-key="${esc(key)}"></iframe>`;
}
function group(rows, by) {                                   // chart_engine._group: weekly (to Friday) or monthly candles, dated on the last traded day
  const out = [], key = (d) => { if (by === "M") return d.slice(0, 7); const t = new Date(d + "T00:00:00Z"), k = (5 - t.getUTCDay() + 7) % 7; return new Date(t.getTime() + k * 864e5).toISOString().slice(0, 10); };
  let cur = null;
  for (const r of rows) { const k = key(r[0]);
    if (!cur || cur.k !== k) { cur = { k, r: [r[0], r[1], r[2], r[3], r[4], r[5]] }; out.push(cur); }
    else { cur.r[0] = r[0]; cur.r[2] = Math.max(cur.r[2], r[2]); cur.r[3] = Math.min(cur.r[3], r[3]); cur.r[4] = r[4]; cur.r[5] += r[5]; } }
  return out.map((x) => x.r);
}
export function chartPayload(Hf, ixH, label) {               // chart_engine.build_payload
  const h = E.hist(Hf, "5y"), rows = [];
  h.d.forEach((d, i) => { const o = h.o ? h.o[i] : null, c = h.c[i], hh = h.h[i], l = h.l[i]; if ([o, hh, l, c].some((v) => v == null) || !(c > 0) || !(hh > 0)) return; rows.push([d, +o.toFixed(2), +hh.toFixed(2), +l.toFixed(2), +c.toFixed(2), h.v[i] || 0]); });
  if (rows.length < 5) return null;
  const y = rows.slice(-252), P = { D: rows, W: group(rows, "W"), M: group(rows, "M"), hi52: Math.max(...y.map((r) => r[2])), lo52: Math.min(...y.map((r) => r[3])), pdh: null, pdl: null, ix: null };
  if (rows.length >= 2) { P.pdh = rows.at(-2)[2]; P.pdl = rows.at(-2)[3]; }
  if (ixH) { const m = new Map(ixH.d.map((d, i) => [d, ixH.c[i]])), all = [...new Set([...ixH.d, ...rows.map((r) => r[0])])].sort(); let last = null; const ff = new Map();
    for (const d of all) { if (m.has(d) && m.get(d) != null) last = m.get(d); ff.set(d, last); }
    const v = rows.map((r) => (ff.get(r[0]) == null ? null : +ff.get(r[0]).toFixed(2))); P.ix = { label, v: v.filter((x) => x != null).length < 60 ? null : v }; }
  else P.ix = { label, v: null };
  return P;
}
function chartDoc(sym, Hf) {
  const label = rsLabel(), ixH = ((S.sk.rsIx || {})[label] || {}).h || null, P = chartPayload(Hf, ixH, label);
  return S.sk.tpl.replace("__LIB__", LIB).replace("__PAYLOAD__", JSON.stringify(P || {}).replace(/<\//g, "<\\/")).replace("__SYM__", JSON.stringify(sym).replace(/<\//g, "<\\/")).replace("__MODE__", "chart").replace("__RANGE__", "6M");
}

// ── research ──
function research(sym, D) {
  const K = S.sk, code = K.code, fn = R.FN[code], grp = K.grp || fn.grp;
  lazy(D, sym, code);
  const groups = `<div class="seg rs-grp">${R.GROUPS.map(([g]) => `<button class="${g === grp ? "on" : ""}" data-act="rsgrp" data-v="${esc(g)}">${esc(g)}</button>`).join("")}</div>`;
  const items = R.GROUPS.find(([g]) => g === grp)[1];
  const pills = `<div class="rs-fns">${items.map(([c, n]) => `<button class="rs-fn ${c === code ? "on" : ""}" data-act="rsfn" data-v="${c}"><b>${c}</b>${esc(n)}</button>`).join("")}</div>`;
  const cmd = `<div class="rs-cmd"><input data-rs="cmd" placeholder="Type a code and press Enter: VAL, PB, DCF…" autocomplete="off" spellcheck="false" aria-label="Function code"><span>${R.GROUPS.flatMap(([, it]) => it).length} screens</span></div>`;
  const same = sameName(sym);
  const head = `<div class="rs-title"><b>${code}</b><span>${esc(fn.name)}</span><em>${esc(fn.desc)}</em></div>`;
  const waiting = ["H", "B", "Y"].filter((k) => D.busy[k]).map((k) => ({ H: "prices", B: "Screener", Y: "Yahoo" }[k]));
  const C = ctx(sym), blocks = R.build(code, C);
  K.csv = {};
  return `<div class="rs">${cmd}${groups}${pills}${same}${head}${controls(code, C)}${waiting.length ? `<div class="note">Still loading: ${waiting.join(", ")}…</div>` : ""}<div class="rs-body">${draw(blocks, C)}</div></div>`;
}
function sameName(sym) {                                    // research_page.same_group: other companies whose name starts with the same word
  const names = S.nw.names || {}, n = names[sym] || "", w = n ? n.trim().toUpperCase().split(/[\s\-.&]+/)[0] : "";
  const GENERIC = new Set(["THE", "INDIA", "INDIAN", "NATIONAL", "STATE", "UNITED", "GENERAL", "SHREE", "SHRI", "SRI", "NEW", "ORIENTAL", "HINDUSTAN", "BHARAT", "BANK", "STEEL", "POWER", "GLOBAL",
    "STANDARD", "UNIVERSAL", "SUPREME", "ROYAL", "ASIAN", "CENTRAL", "FIRST", "MAHARASHTRA", "GUJARAT", "KERALA", "TAMILNADU"]);
  if (w.length < 3 || GENERIC.has(w)) return "";
  const out = Object.entries(names).filter(([s, x]) => s !== sym && String(x).trim().toUpperCase().split(/[\s\-.&]+/)[0] === w).map(([s]) => s).sort((a, b) => a.length - b.length).slice(0, 8);
  return out.length ? `<div class="rs-same"><span>Same name</span>${out.map((s) => `<button class="nd-sym" data-sym="${esc(s)}">${esc(s)}</button>`).join("")}</div>` : "";
}
const seg = (k, opts, cur, lab = (x) => x) => `<div class="seg">${opts.map((v) => `<button class="${String(v) === String(cur) ? "on" : ""}" data-act="rsopt" data-k="${k}" data-v="${esc(v)}">${esc(lab(v))}</button>`).join("")}</div>`;
function controls(code, C) {
  const o = S.sk.o;
  if (code === "FA") return seg("fin", ["Quarterly", "Yearly"], o.fin || "Quarterly");
  if (code === "HDS") return seg("sh", ["Quarterly", "Yearly"], o.sh || "Quarterly");
  if (code === "MF") return `<div class="rs-ctl">${seg("mfc", ["Mutual funds", "All domestic institutions", "Foreign institutions"], o.mfc || "Mutual funds")}${seg("mfp", ["Quarterly", "Yearly"], o.mfp || "Quarterly")}</div>`;
  if (code === "DLV") return `<div class="rs-ctl">${seg("dlv", ["Daily", "Weekly", "Monthly"], o.dlv || "Weekly")}${seg("dlvn", [60, 120, 250], o.dlvn || 60, (v) => `Last ${v} sessions`)}</div>`;
  if (code === "HP") return seg("hp", ["1M", "3M", "6M", "1Y", "5Y"], o.hp || "3M");
  if (code === "COMP") return `<div class="rs-ctl"><input class="rs-in" data-rs="compq" value="${esc(o.compq ?? defaultComp(C.sym))}" placeholder="Symbols or indices, comma separated: TCS, INFY, NIFTY 500, NIFTY IT">${seg("comprng", ["3M", "6M", "1Y", "2Y"], o.comprng || "1Y")}</div>`;
  if (code === "BETA") return `<div class="rs-ctl">${seg("betaix", ["NIFTY 500", "NIFTY 50"], o.betaix || "NIFTY 500")}${seg("betawin", ["1Y", "2Y"], o.betawin || "1Y")}</div>`;
  if (code === "CN") return `<input class="rs-in" data-rs="newsq" value="${esc(o.newsq || "")}" placeholder="Filter headlines… e.g. results, order, merger">`;
  return "";
}

// ── drawing the blocks ──
const TAG = { live: ["Live", COL.g], stale: ["Cached", COL.a], unavailable: ["Not available", COL.t3] };
const ACR = new Set(["P/E", "P/B", "EPS", "ROE", "ROCE", "TTM", "CSV", "SMA", "EMA", "RSI", "ATR", "NIFTY", "MF", "BSE", "NSE", "FII", "DII", "FIIS", "DIIS", "MACD", "CAGR", "DMA", "R²", "AI", "IT", "BANK", "₹", "PE", "PB", "ID", "AGM", "EGM", "EBITDA", "ISIN"]);
const ACR_FIX = { YOY: "YoY", QOQ: "QoQ", FIIS: "FIIs", DIIS: "DIIs" };
function hd(s, sym) {                                       // research_page._hd: sentence case for an ALL-CAPS heading
  s = String(s); if (s !== s.toUpperCase() || s === s.toLowerCase()) return s;
  const t = s.split(" ").map((w) => ACR_FIX[w] || (ACR.has(w) || w === sym || /\d/.test(w) ? w : w.toLowerCase())).join(" ");
  return t.charAt(0).toUpperCase() + t.slice(1);
}
const STATE = new Set(["ABOVE", "BELOW", "OUTPERFORM", "UNDERPERFORM", "PASS", "FAIL", "UP", "DOWN", "UPTREND", "DOWNTREND", "MIXED", "BULLISH", "BEARISH", "GOLDEN CROSS", "DEATH CROSS", "OVERBOUGHT", "OVERSOLD",
  "NEUTRAL", "STRONG", "AVERAGE", "WEAK", "MARGIN", "ASSET TURNOVER", "LEVERAGE", "NEXT RESULTS", "DIVIDEND", "SPLIT / BONUS"]);
const POS = new Set(["ABOVE", "OUTPERFORM", "PASS", "UP", "UPTREND", "BULLISH", "GOLDEN CROSS", "OVERSOLD"]), NEG = new Set(["BELOW", "UNDERPERFORM", "FAIL", "DOWN", "DOWNTREND", "BEARISH", "DEATH CROSS", "OVERBOUGHT"]);
const val = (v) => { const s = String(v); return STATE.has(s) ? s[0] + s.slice(1).toLowerCase() : s; };
const vcls = (v) => { const s = String(v).trim(); return /^\+\s?[\d₹.]/.test(s) ? "pos" : /^[-−]\s?[\d₹.]/.test(s) ? "neg" : POS.has(s.toUpperCase()) ? "pos" : NEG.has(s.toUpperCase()) ? "neg" : ""; };
const colr = (c) => (c ? COL[c] || c : "");
function draw(blocks, C) { return blocks.map((b) => one(b, C)).join(""); }
function one(b, C) {
  const k = b[0];
  if (k === "h") { const t = b[2] ? TAG[b[2]] : null; return `<div class="rp-h"><b>${esc(b[1])}</b>${t ? `<span class="rp-tag" style="color:${t[1]}">${t[0]}</span>` : ""}</div>${b[3] ? `<div class="rp-note">${esc(b[3])}</div>` : ""}`; }
  if (k === "kpis") return `<div class="rp-kg" style="--n:${Math.max(1, Math.min(b[2], b[1].length || 1))}">${b[1].map(([l, v, s, c]) => `<div class="rp-k"><div class="l">${esc(l)}</div><div class="v" style="${c ? "color:" + colr(c) : ""}">${esc(val(v))}</div><div class="s">${esc(s || "")}</div></div>`).join("")}</div>`;
  if (k === "table") {
    const [, heads, rows, hl, csv] = b; if (!rows.length) return na("No rows returned", "Selected dataset");
    let btn = ""; if (csv) { S.sk.csv[csv] = [["METRIC", ...heads], ...rows.map((r) => [r.label, ...[...r.values, ...Array(heads.length).fill("")].slice(0, heads.length)])]; btn = `<button class="btn sm rp-dl" data-act="rscsv" data-v="${esc(csv)}">Download CSV</button>`; }
    return `<div class="rp-tw"><table class="rp-t"><thead><tr><th class="l">&nbsp;</th>${heads.map((h) => `<th>${esc(hd(h, C.sym))}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => {
      const h = (hl || []).some((x) => x && String(r.label).toLowerCase().includes(String(x).toLowerCase()));
      return `<tr class="${h ? "hl" : ""}"><td class="l">${esc(r.label)}</td>${r.values.map((v) => `<td class="n ${vcls(v)}">${esc(val(v))}</td>`).join("")}</tr>`; }).join("")}</tbody></table></div>${btn}`;
  }
  if (k === "check") return panel(b[1], check(b[2], b[3] || ["Pass", "Fail", "N/A"]));
  if (k === "mini") return `<div class="rp-panel"><div class="t">${esc(b[1])}${b[3] ? `<em>${esc(b[3])}</em>` : ""}</div><div class="b tight">${b[2].map(([l, v, c]) => `<div class="rp-mr"><span>${esc(l)}</span><b class="${c ? "" : vcls(v)}" style="${c ? "color:" + colr(c) : ""}">${esc(val(v))}</b></div>`).join("")}</div></div>`;
  if (k === "na") return na(b[1], b[2], b[3]);
  if (k === "note") return `<div class="rp-cap">${esc(b[1])}</div>`;
  if (k === "text") return panel(b[1], `<div class="rp-p">${esc(b[2] || "")}</div>`, b[3]);
  if (k === "chart") return chart(b[1]);
  if (k === "cols") return `<div class="rp-two">${b[1].map((col) => `<div>${draw(col, C)}</div>`).join("")}</div>`;
  if (k === "row3") return `<div class="rp-three">${b[1].map((x) => `<div>${one(x, C)}</div>`).join("")}</div>`;
  if (k === "row2") return `<div class="rp-two">${b[1].map((x) => `<div>${one(x, C)}</div>`).join("")}</div>`;
  if (k === "stack") return b[1].map((x) => one(x, C)).join("");
  if (k === "dash") { const h = C.H ? E.hist(C.H, "1y") : null;
    return `<div class="rp-dash"><div>${h ? panel("Price · 1 year", lineChart(h.d, [{ n: C.sym, c: h.c.at(-1) >= h.c[0] ? COL.g : COL.r, v: h.c, w: 1.8, fill: true }], { h: 300, fmt: (v) => num(v, 2) })) : ""}</div><div>${draw(b[1], C)}</div></div>`; }
  if (k === "boxes") return `<div class="rp-boxes">${b[1].map(([t, rows]) => panel(t, Object.entries(rows).map(([a, v]) => `<div class="rp-mr"><span>${esc(a)}</span><b>${esc(v)}</b></div>`).join(""), false, true)).join("")}</div>`;
  if (k === "proscons") return `<div class="rp-two">${panel("Pros (Screener)", b[1].map((x) => `<div class="rp-li"><i style="background:${COL.g}"></i><span>${esc(x)}</span></div>`).join("") || "—", false, true)}${panel("Cons (Screener)", b[2].map((x) => `<div class="rp-li"><i style="background:${COL.r}"></i><span>${esc(x)}</span></div>`).join("") || "—", false, true)}</div>`;
  if (k === "scorestrip") return scoreStrip(b[1]);
  if (k === "standout") { const li = (rows, c) => rows.map(([, l, t]) => `<div class="rp-li"><i style="background:${c}"></i><span><b>${esc(l)}</b> ${esc(t)}</span></div>`).join("") || '<div class="rp-li"><span>Nothing crossed the threshold.</span></div>';
    return `<div class="rp-two">${panel("What stands out · strengths", li(b[1], COL.g), false, true)}${panel("What stands out · watch-outs", li(b[2], COL.r), false, true)}</div>`; }
  if (k === "reading") return `<div class="rp-read">${b[1]}</div>`;
  if (k === "html") return pictures(b[1], b[2], C);
  if (k === "filings") return filings(b[1], b[2]);
  if (k === "filingsmini") return panel("Latest exchange filings", b[1].length ? b[1].map((x) => `<div class="rp-fil"><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.title.slice(0, 110))}</a><div>${esc(x.dt)} · ${esc(x.category)}</div></div>`).join("") : "No BSE filings returned. Open CACS for details.", false, true);
  if (k === "events") return events(b[1], b[2], b[3]);
  if (k === "news") return news(b[1], C);
  if (k === "dcfin") return `<div class="rs-ctl rs-num">${numIn("dcfcf", "Starting cash flow ₹ Cr (default: last 4 quarters' net profit)", b[1].base)}${numIn("dcfr", "Discount rate %", b[1].r)}${numIn("dcftg", "Growth after year 10 %", b[1].tg)}</div>`;
  if (k === "pbin") return `<div class="rs-ctl">${seg("pbwin", ["20", "60", "90", "120", "250", "Custom"], b[1].pick, (v) => (v === "Custom" ? "Custom" : v + " days"))}${b[1].pick === "Custom" ? numIn("pbn", "Sessions", b[1].n) : ""}${seg("pbmode", ["Close vs previous close", "Candle colour"], b[1].mode)}</div>`;
  if (k === "sizer") return `<div class="rs-ctl rs-num">${numIn("cap", "Capital ₹", b[1].cap)}${numIn("risk", "Risk per trade %", b[1].risk)}</div>`;
  if (k === "csvprices") { const d = b[1]; S.sk.csv[b[2]] = [["Date", "Open", "High", "Low", "Close", "Volume"], ...d.d.map((x, i) => [x, d.o[i], d.h[i], d.l[i], d.c[i], d.v[i]])];
    return `<button class="btn sm rp-dl" data-act="rscsv" data-v="${esc(b[2])}">Download CSV · full range</button>`; }
  if (k === "prochart") return panel("Chart", `<div class="rp-p">The full pro chart is at the top of this page: candles, weekly and monthly bars, moving averages, RSI, MACD, the RS line, drawing tools and full screen.</div><button class="btn sm" data-act="skchart" style="margin-top:10px">Go to the chart ↑</button>`, false);
  if (k === "synth") return panel("AI synthesis", `<div class="rp-p">The AI write-up of this evidence sheet arrives with the Arka AI section. It will read only the facts above (public numbers), never your holdings or watchlist.</div>`, true);
  if (k === "thesis") return thesis(C);
  if (k === "fno") return "";
  return "";
}
const panel = (t, body, dashed = false, tight = false) => `<div class="rp-panel${dashed ? " dash" : ""}"><div class="t">${esc(t)}</div><div class="b${tight ? " tight" : ""}">${body}</div></div>`;
const na = (t, req, d) => panel(t, `<b class="rp-na">Not available.</b> Needs ${esc(req)}.<div style="margin-top:4px">${esc(d || "No verified dataset returned this field.")}</div>`, true);
const check = (items, labels) => items.map(([t, ok, d]) => { const [m, c] = ok === true ? [labels[0], COL.g] : ok === false ? [labels[1], COL.r] : [labels[2], COL.t3];
  return `<div class="rp-cl"><b style="color:${c}">${esc(m)}</b><span>${esc(t)}</span><i>${esc(d || "")}</i></div>`; }).join("");
const numIn = (k, label, v) => `<label class="rs-f"><span>${esc(label)}</span><input type="number" step="any" inputmode="decimal" data-rs="${k}" value="${esc(v ?? "")}"></label>`;
function scoreStrip(SC) {
  if (!SC || SC.overall == null) return "";
  const tile = (name, score, sub, all) => { const [w, c] = E.word(score); return `<div class="c${all ? " all" : ""}"><div class="l">${esc(name)}</div><div class="v"><b>${E.fx(score, 0, false)}</b><i style="color:${COL[c]}">${w}</i></div>
    <div class="t"><u style="width:${E.fx(score, 0, false)}%;background:${COL[c]}"></u></div><div class="s">${esc(sub)}</div></div>`; };
  return `<div class="rp-sc">${tile("Arka score", SC.overall, `average of ${SC.pillars.length} pillars · out of 100`, true)}${SC.pillars.map((p) => { const best = p.items.reduce((a, i) => (Math.abs(i.score - 50) > Math.abs(a.score - 50) ? i : a));
    return tile(p.name, p.score, `${best.label}: ${best.text}`); }).join("")}</div>`;
}
function chart(s) {
  if (s.kind === "line") {
    const ser = s.series.map((x) => ({ n: x.name, c: colr(x.color), v: x.y, w: x.width || 1.6, dash: x.dash }));
    return `<div class="rp-ch">${lineChart(s.x, ser, { h: s.h ? Math.min(s.h, 320) : 260, fmt: (v) => num(v, Math.abs(v) >= 100 ? 0 : 2), hlines: (s.hlines || []).map(([y]) => [y, COL.a]) })}${legendOf(ser)}</div>`;
  }
  if (s.kind === "bars") return `<div class="rp-ch">${bars(s.x, s.series, s.h || 260)}${legendOf(s.series.map((x) => ({ n: x.name, c: colr(x.color) })))}</div>`;
  if (s.kind === "scatter") return `<div class="rp-ch">${scatter(s)}</div>`;
  return "";
}
const legendOf = (ser) => (ser.length > 1 ? `<div class="lgd">${ser.map((x) => `<span><i style="background:${x.c}"></i>${esc(x.n)}</span>`).join("")}</div>` : "");
function bars(x, series, h) {                               // grouped bars with a zero line; the value shows on hover
  const W = 1000, H = Math.min(h, 280), all = series.flatMap((s) => s.y).filter((v) => v != null && isFinite(v)); if (!all.length) return "";
  let lo = Math.min(0, ...all), hi = Math.max(0, ...all); if (hi === lo) hi = lo + 1; const pad = (hi - lo) * 0.06; lo -= lo < 0 ? pad : 0; hi += pad;
  const n = x.length, gw = W / n, bw = (gw * 0.72) / series.length, Y = (v) => H - ((v - lo) / (hi - lo)) * H;
  let out = `<line x1="0" x2="${W}" y1="${Y(0).toFixed(1)}" y2="${Y(0).toFixed(1)}" stroke="#333" vector-effect="non-scaling-stroke"/>`;
  x.forEach((lab, i) => series.forEach((s, j) => { const v = s.y[i]; if (v == null || !isFinite(v)) return;
    out += `<rect x="${(i * gw + gw * 0.14 + j * bw).toFixed(1)}" y="${Math.min(Y(v), Y(0)).toFixed(1)}" width="${Math.max(1, bw - 1).toFixed(1)}" height="${Math.max(0.8, Math.abs(Y(v) - Y(0))).toFixed(1)}" rx="1.5" fill="${colr(s.color)}"><title>${esc(lab)} · ${esc(s.name)}: ${num(v, Math.abs(v) >= 100 ? 0 : 2)}</title></rect>`; }));
  const lbl = n <= 14 ? x : x.filter((_, i) => i % Math.ceil(n / 12) === 0);
  return `<div class="rp-bars"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:${H}px">${out}</svg><div class="xl">${lbl.map((l) => `<span>${esc(l)}</span>`).join("")}</div></div>`;
}
function scatter(s) {
  const W = 1000, H = 280, xs = s.x, ys = s.y, xl = Math.min(...xs), xh = Math.max(...xs), yl = Math.min(...ys), yh = Math.max(...ys);
  const X = (v) => ((v - xl) / (xh - xl || 1)) * W, Y = (v) => H - ((v - yl) / (yh - yl || 1)) * H;
  const dots = xs.map((x, i) => `<circle cx="${X(x).toFixed(1)}" cy="${Y(ys[i]).toFixed(1)}" r="3" fill="${COL.c}" opacity=".5" vector-effect="non-scaling-stroke"/>`).join("");
  const [a, b] = s.fit;
  return `<svg class="rp-sct" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:${H}px">${dots}<line x1="0" x2="${W}" y1="${Y(a + b * xl).toFixed(1)}" y2="${Y(a + b * xh).toFixed(1)}" stroke="${COL.a}" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>
    <div class="lgd"><span><i style="background:${COL.c}"></i>Daily returns % (index across, stock up)</span><span><i style="background:${COL.a}"></i>Fit · beta ${E.fx(b, 2, false)}</span></div>`;
}
function pictures(kind, d, C) {
  if (kind === "valuefield") {
    const { rows, price } = d; let lo = Math.min(...rows.map((r) => r[2]), price), hi = Math.max(...rows.map((r) => r[4]), price); const pad = (hi - lo) * 0.06 || price * 0.05; lo -= pad; hi += pad;
    const pos = (v) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100)), pal = [COL.c, COL.p, COL.a, COL.g, "#6B6B73"];
    return `<div class="rp-panel"><div class="b"><div class="rp-vf">${rows.map(([name, note, a, m, b], i) => { const col = name === "52-week trading range" ? pal[4] : pal[Math.min(i, 4)], vs = (m / price - 1) * 100;
      return `<div class="r"><span>${esc(name)}<small>${esc(note)}</small></span><div class="tk"><u style="left:${pos(a).toFixed(1)}%;width:${Math.max(1.2, pos(b) - pos(a)).toFixed(1)}%;background:${col}"></u>${b > a ? `<s style="left:${pos(m).toFixed(1)}%"></s>` : ""}<em style="left:${pos(price).toFixed(1)}%" title="Price ${E.rs(price)}"></em></div>
        <b>${a === b ? E.rs(a, 0) : `${E.rs(a, 0)} – ${E.rs(b, 0)}`}<br><span style="color:${vs > 0 ? COL.g : COL.r};font-weight:500">${a === b ? "" : "middle "}${E.fx(vs, 0, false, true)}% vs price</span></b></div>`; }).join("")}</div></div></div>`;
  }
  if (kind === "heat") {
    const M = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const c = (v) => (v == null ? "transparent" : v >= 0 ? `rgba(48,209,88,${Math.min(0.85, 0.12 + Math.abs(v) / 12)})` : `rgba(255,69,58,${Math.min(0.85, 0.12 + Math.abs(v) / 12)})`);
    return `<div class="rp-tw"><table class="rp-heat"><thead><tr><th></th>${M.map((m) => `<th>${m}</th>`).join("")}</tr></thead><tbody>${d.years.map((y, i) => `<tr><td class="l">${y}</td>${d.Z[i].map((v) => `<td style="background:${c(v)}">${v == null ? "" : E.fx(v, 1, false, true)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  }
  if (kind === "dlvbars") {
    const mx = Math.max(1, ...d.map((r) => r.volume || 0));
    return `<div class="rp-panel"><div class="b"><div class="rp-dlv">${d.map((r) => `<div class="r"><span>${esc(r.label)}</span><div class="t"><i style="width:${(((r.volume || 0) / mx) * 100).toFixed(1)}%"></i><u style="width:${(((r.deliv_qty || 0) / mx) * 100).toFixed(1)}%"></u></div><b>${r.deliv_pct == null ? "—" : E.fx(r.deliv_pct, 1, false) + "%"}</b></div>`).join("")}</div>
      <div class="lgd"><span><i style="background:#2A3F5F"></i>Traded qty</span><span><i style="background:${COL.c}"></i>Delivered qty</span></div></div></div>`;
  }
  if (kind === "moves") {
    const n = d.length; if (n < 2) return "";
    const W = 1000, top = 170, gap = 10, vh = 46, mx = Math.max(1e-9, ...d.map((x) => Math.abs(x[1])).filter(isFinite)), vmx = Math.max(1e-9, ...d.map((x) => x[2]).filter(isFinite)), bw = W / n, mid = top / 2;
    let p = `<line x1="0" x2="${W}" y1="${mid}" y2="${mid}" stroke="#2A2A30"/>`;
    d.forEach(([dt, m, v, u], i) => { if (!isFinite(m)) return; const col = u ? COL.g : u === false ? COL.r : "#5A5A60", hh = (Math.abs(m) / mx) * (mid - 6), y = m >= 0 ? mid - hh : mid, x = i * bw + bw * 0.14, w = Math.max(0.8, bw * 0.72);
      const tip = `${E.dfmt(dt, "a d b Y")} · ${E.fx(m, 2, false, true)}%` + (isFinite(v) ? ` · vol ${E.qty(v)}` : "");
      p += `<rect x="${x.toFixed(2)}" y="${y.toFixed(2)}" width="${w.toFixed(2)}" height="${Math.max(0.8, hh).toFixed(2)}" rx="1.2" fill="${col}"><title>${esc(tip)}</title></rect>`;
      if (isFinite(v)) { const h2 = (v / vmx) * vh; p += `<rect x="${x.toFixed(2)}" y="${(top + gap + vh - h2).toFixed(2)}" width="${w.toFixed(2)}" height="${Math.max(0.6, h2).toFixed(2)}" rx="1" fill="${col}" opacity=".38"><title>${esc(tip)}</title></rect>`; } });
    const Ht = top + gap + vh;
    return panel("Every session in the window", `<svg viewBox="0 0 ${W} ${Ht}" preserveAspectRatio="none" style="display:block;width:100%;height:${Ht}px">${p}</svg><div class="rp-axis"><span>${E.dfmt(d[0][0], "d b Y")}</span><span>daily move · volume underneath · hover a bar</span><span>${E.dfmt(d[n - 1][0], "d b Y")}</span></div>`);
  }
  return "";
}
function filings(rows, cats) {
  const o = S.sk.o;
  return `<div class="rs-ctl"><select class="rs-in" data-rs="cat">${cats.map((c) => `<option ${c === (o.cat || "All") ? "selected" : ""}>${esc(c)}</option>`).join("")}</select>
    <input class="rs-in" data-rs="filq" value="${esc(o.filq || "")}" placeholder="Filter… e.g. result, dividend, order, board meeting"></div>
    <div class="rp-panel"><div class="b tight">${rows.map((a) => `<div class="rp-fil"><a href="${esc(a.url)}" target="_blank" rel="noopener noreferrer">${esc(a.title)}</a><div>${esc(a.dt)} · <span style="color:${COL.c}">${esc(a.category)}</span>${a.sub ? " · " + esc(a.sub) : ""} · Open document ↗</div></div>`).join("") || '<div class="rp-p">No filings match.</div>'}</div></div>`;
}
function events(ev, kinds, today) {
  const o = S.sk.o;
  return `<select class="rs-in" data-rs="evk">${kinds.map((k) => `<option ${k === (o.evk || "All") ? "selected" : ""}>${esc(k)}</option>`).join("")}</select>
    <div class="rp-panel"><div class="b tight">${ev.map(([d, kind, text, url]) => `<div class="rp-fil">${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>` : `<a>${esc(text)}</a>`}<div><span style="color:${d >= today ? COL.a : COL.c}">${esc(d)}</span> · ${esc(kind)}</div></div>`).join("")}</div></div>`;
}
function news(items, C) {
  const D = dataOf(C.sym);
  if (D.busy.news) return '<div class="note">Gathering this company\'s news from the wire, exchange filings and Google News…</div>';
  const moves = {}; for (const s of new Set(items.flatMap((a) => a.symbols))) { const q = S.q[s]; if (q && q.ok) moves[s] = q.pct; }
  return `<div class="nd-list">${items.map((a) => NW.story(a, moves, a.kind !== "macro" ? NW.reactPill(C.sym, NW.reaction(D.pts, a.pub)) : "")).join("")}</div>`;
}
function thesis(C) {
  const D = dataOf(C.sym); if (D.note === undefined || D.busy.note) return '<div class="note">Loading your note…</div>';
  const p = (D.note && D.note.payload) || {}, at = D.note && D.note.updated_at ? String(D.note.updated_at).slice(0, 16).replace("T", " ") : "not yet";
  const ta = (k, l) => `<label class="rs-f wide"><span>${l}</span><textarea name="${k}" rows="5">${esc(p[k] || "")}</textarea></label>`;
  return `<form id="thesis" class="rs-note" data-s="${esc(C.sym)}"><div class="rs-ctl rs-num"><label class="rs-f"><span>Stance</span><select name="stance">${["—", "Bullish", "Neutral", "Bearish"].map((x) => `<option ${x === (p.stance || "—") ? "selected" : ""}>${x}</option>`).join("")}</select></label>
    <label class="rs-f"><span>Target price (₹)</span><input name="tp" type="number" min="0" step="1" value="${esc(p.tp ?? 0)}"></label><label class="rs-f"><span>Conviction (1 to 5)</span><input name="conv" type="number" min="1" max="5" step="1" value="${esc(p.conv ?? 3)}"></label></div>
    <div class="rp-two">${ta("bull", "Bull case")}${ta("bear", "Bear case")}${ta("cat", "Catalysts")}${ta("risk", "Risks")}</div>
    <div class="rs-ctl"><button class="btn pri" type="submit">Save note</button><button class="btn" type="button" data-act="rsnotetxt">Download note (.txt)</button></div>
    <div class="rp-cap">${D.err.note ? esc(D.err.note) : `Notes are saved in Supabase (the research_notes table the classic app uses). Last saved: ${esc(at)}.`}</div></form>`;
}

// ── events from app.js ──
export function act(name, el) {
  const K = S.sk;
  if (name === "rsfn") { K.code = el.dataset.v; K.grp = null; history.replaceState(null, "", `#/s/${encodeURIComponent(K.sym)}/${K.code}`); paint(); return true; }
  if (name === "rsgrp") { K.grp = el.dataset.v; const first = R.GROUPS.find(([g]) => g === K.grp)[1][0][0]; K.code = first; history.replaceState(null, "", `#/s/${encodeURIComponent(K.sym)}/${K.code}`); paint(); return true; }
  if (name === "rsopt") { const k = el.dataset.k, v = el.dataset.v; K.o[k] = ["dlvn"].includes(k) ? +v : v; paint(); return true; }
  if (name === "rscsv") { const rows = K.csv[el.dataset.v]; if (!rows) return true;
    const text = rows.map((r) => r.map((c) => (/[",\n]/.test(String(c)) ? `"${String(c).replace(/"/g, '""')}"` : String(c ?? ""))).join(",")).join("\n");
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([text], { type: "text/csv" })); a.download = el.dataset.v + ".csv"; a.click(); return true; }
  if (name === "skchart") { const t = document.getElementById("sk-chart"); if (t) t.scrollIntoView({ behavior: "auto", block: "center" }); return true; }
  if (name === "skgo") { const t = document.getElementById("sk-research"); if (t) t.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" }); return true; }
  if (name === "skback") { if (history.length > 1) history.back(); else location.hash = "#/home"; return true; }
  if (name === "rsnotetxt") { const f = document.getElementById("thesis"); if (!f) return true; const v = Object.fromEntries(new FormData(f)), sym = K.sym;
    const t = new Date(Date.now() + 330 * 60000).toISOString().slice(0, 16).replace("T", " ");
    const txt = `${sym} RESEARCH NOTE · ${t} IST\nStance: ${v.stance} · Target: ₹${num(+v.tp || 0, 2)} · Conviction: ${v.conv}/5\n` + [["Bull case", "bull"], ["Bear case", "bear"], ["Catalysts", "cat"], ["Risks", "risk"]].map(([l, k]) => `${l.toUpperCase()}\n${v[k] || ""}`).join("\n\n");
    const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([txt], { type: "text/plain" })); a.download = `${sym}_research_note.txt`; a.click(); return true; }
  return false;
}
let inTimer = 0;
export function input(el, ev) {                              // a box on the research screen changed
  const k = el.dataset.rs, K = S.sk;
  if (k === "find") { if (ev === "input") find(el.value); return; }
  if (k === "rsix") { if (ev !== "change") return; if (el.value === "__other") { K.rsOther = true; K.rsMsg = ""; return rsBar(); } K.rsOther = false; return setRsIndex(el.value); }
  if (k === "rsixc") { if (ev === "change" || ev === "enter") setRsIndex(el.value); return; }
  if (k === "cmd") { if (ev === "enter") { const c = R.resolveCode(el.value); if (c) { K.code = c; K.grp = null; el.value = ""; history.replaceState(null, "", `#/s/${encodeURIComponent(K.sym)}/${c}`); paint(); } else toast("No screen called " + el.value.trim().toUpperCase() + ". Try VAL, PB, DCF or SC."); } return; }
  const text = el.tagName === "TEXTAREA" || (el.tagName === "INPUT" && el.type !== "number");
  if (el.type === "number") { if (ev !== "change") return; const n = el.value === "" ? null : +el.value; if (n == null || !isFinite(n)) return; K.o[k] = n; K.force = true; return paint(); }
  if (text && ev === "input") { K.o[k] = el.value; clearTimeout(inTimer); inTimer = setTimeout(paint, 250); return; }
  if (!text && ev === "change") { K.o[k] = el.value; K.force = true; paint(); }
}
export async function saveNote(f) {
  const v = Object.fromEntries(new FormData(f)), sym = f.dataset.s, D = dataOf(sym), b = f.querySelector("button[type=submit]"); b.disabled = true;
  try { const r = await api("/api/note", { method: "POST", body: JSON.stringify({ sym, payload: v }) }); D.note = { payload: { ...v, tp: +v.tp || 0, conv: +v.conv || 3 }, updated_at: r.at }; D.err.note = ""; toast("Note saved"); }
  catch (e) { D.err.note = e.message; toast(e.message); }
  b.disabled = false; paint();
}

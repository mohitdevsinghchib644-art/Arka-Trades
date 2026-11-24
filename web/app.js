// Arka Trades · new app (stage 3): Home, Watchlist, the full Portfolio, Signals, Markets, Daily brief, More. Plain JavaScript, no build step.
import * as PFV from "./portfolio.js";
import * as RG from "./regime.js";
import * as HM from "./home.js";
import * as MF from "./flow.js";
import * as NW from "./news.js";
import { chartMove } from "./charts.js";
const CLASSIC = "https://arka-trades-1.streamlit.app";       // the Streamlit app, for sections that have not moved yet
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ── what the pages show ──
const INDICES = [["NIFTY 50", "^NSEI"], ["SENSEX", "^BSESN"], ["BANK NIFTY", "^NSEBANK"], ["NIFTY 500", "^CRSLDX"]];
const SECTORS = [["Bank", "^NSEBANK"], ["Private bank", "NIFTY_PVT_BANK.NS"], ["PSU bank", "^CNXPSUBANK"], ["Financial services", "NIFTY_FIN_SERVICE.NS"], ["IT", "^CNXIT"],
  ["Auto", "^CNXAUTO"], ["FMCG", "^CNXFMCG"], ["Pharma", "^CNXPHARMA"], ["Metal", "^CNXMETAL"], ["Energy", "^CNXENERGY"], ["Realty", "^CNXREALTY"], ["Media", "^CNXMEDIA"],
  ["Infra", "^CNXINFRA"], ["PSE", "^CNXPSE"], ["Commodities", "^CNXCMDT"], ["Consumption", "^CNXCONSUM"], ["MNC", "^CNXMNC"], ["Services", "^CNXSERVICE"]];
const MARKETS = {
  India: [["NIFTY 50", "^NSEI"], ["SENSEX", "^BSESN"], ["BANK NIFTY", "^NSEBANK"], ["NIFTY 500", "^CRSLDX"], ["NIFTY Next 50", "^NSMIDCP"], ["Financial services", "NIFTY_FIN_SERVICE.NS"], ["India VIX", "^INDIAVIX"]],
  World: [["S&P 500", "^GSPC"], ["Nasdaq", "^IXIC"], ["Dow Jones", "^DJI"], ["FTSE 100", "^FTSE"], ["DAX", "^GDAXI"], ["CAC 40", "^FCHI"], ["Nikkei 225", "^N225"], ["Hang Seng", "^HSI"],
          ["Shanghai", "000001.SS"], ["KOSPI", "^KS11"], ["ASX 200", "^AXJO"]],
  Commodities: [["Gold", "GC=F"], ["Silver", "SI=F"], ["Brent crude", "BZ=F"], ["WTI crude", "CL=F"], ["Natural gas", "NG=F"], ["Copper", "HG=F"]],
  "Currencies and rates": [["USD / INR", "INR=X"], ["EUR / INR", "EURINR=X"], ["GBP / INR", "GBPINR=X"], ["JPY / INR", "JPYINR=X"], ["Dollar index", "DX-Y.NYB"], ["US 10-year yield", "^TNX"], ["CBOE VIX", "^VIX"]],
  Crypto: [["Bitcoin", "BTC-USD"], ["Ethereum", "ETH-USD"]],
};
const NAMES = Object.fromEntries([...INDICES, ...SECTORS, ...Object.values(MARKETS).flat()].map(([n, s]) => [s, n]));
// [key, name, icon, shown in the phone's bottom bar]. Markets and the brief sit under More on a phone.
const TABS = [["home", "Home", '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/>', true], ["watchlist", "Watchlist", '<path d="M6 3h12v18l-6-4.5L6 21z"/>', true],
  ["portfolio", "Portfolio", '<path d="M3 7h18v13H3z"/><path d="M8 7V4h8v3M3 12h18"/>', true], ["signals", "Signals", '<path d="M3 17l5-6 4 4 8-10"/><path d="M15 5h5v5"/>', true],
  ["flow", "Money flow", '<path d="M4 17c3-6 5 2 8-4s5-1 8-6"/><path d="M4 21h16"/>', false],
  ["news", "News", '<path d="M4 5h13v14H6a2 2 0 0 1-2-2z"/><path d="M17 9h3v8a2 2 0 0 1-2 2"/><path d="M8 9h5M8 13h5"/>', false],
  ["regime", "Regime", '<path d="M3 20h18"/><path d="M6 16l4-5 3 3 5-7"/><circle cx="18" cy="7" r="1.6"/>', false],
  ["markets", "Markets", '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.2 3 14.8 0 18M12 3c-3 3.2-3 14.8 0 18"/>', false],
  ["brief", "Daily brief", '<path d="M5 3h11l3 3v15H5z"/><path d="M8 9h8M8 13h8M8 17h5"/>', false],
  ["more", "More", '<circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/>', true]];
const MORE = [["Stock research", "Financials, charts, filings and peers for any stock."], ["Arka AI", "Chart reading with your own playbook."],
  
  ["Screener", "Describe a setup and scan the market."], ["Backtest", "Test a rule on past prices."]];

const S = { me: null, tab: "home", q: {}, at: {}, wl: { rows: [], list: "Main", loaded: false, err: "" }, sig: null, sigErr: "", sigTab: "Leaders", mkTab: "India", sort: "order",
            pf: PFV.pfDefaults(), rg: RG.rgDefaults(), qv: 0, hm: HM.homeDefaults(), mf: MF.flowDefaults(), nw: NW.newsDefaults(), deals: null, brief: undefined,
            sheet: null, results: null, searching: false };

// ── small helpers ──
const num = (v, d = 2) => (v == null || isNaN(v) ? "—" : Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d }));
const sgn = (v, d = 2) => (v == null || isNaN(v) ? "—" : (v > 0 ? "+" : v < 0 ? "−" : "") + num(Math.abs(v), d));
const pc = (v, d = 2) => (v == null || isNaN(v) ? "—" : sgn(v, d) + "%");
const cls = (v) => (v == null || isNaN(v) || v === 0 ? "mu" : v > 0 ? "up" : "dn");
const qty = (v) => (v == null ? "—" : v >= 1e7 ? num(v / 1e7, 2) + " Cr" : v >= 1e5 ? num(v / 1e5, 2) + " L" : num(v, 0));
const frac = (v, d = 1) => pc(v == null ? null : v * 100, d);
const rs = (v, d = 0) => (v == null || isNaN(v) ? "—" : "₹" + num(v, d));
const srs = (v, d = 0) => (v == null || isNaN(v) ? "—" : (v > 0 ? "+" : v < 0 ? "−" : "") + "₹" + num(Math.abs(v), d));
const todayStr = () => { const t = ist(); return t.getFullYear() + "-" + String(t.getMonth() + 1).padStart(2, "0") + "-" + String(t.getDate()).padStart(2, "0"); };
const nice = (d) => { if (!d) return "no date"; const t = new Date(d + "T00:00:00"); return isNaN(t) ? d : t.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" }); };
function ist() { const n = new Date(); return new Date(n.getTime() + n.getTimezoneOffset() * 60000 + 330 * 60000); }
function marketOpen() { const t = ist(), m = t.getHours() * 60 + t.getMinutes(); return t.getDay() >= 1 && t.getDay() <= 5 && m >= 555 && m <= 930; }
function spark(pts, prev, w = 68, h = 26, fill = false) {
  const a = (pts || []).filter((v) => v != null);
  if (a.length < 2) return `<svg viewBox="0 0 ${w} ${h}"></svg>`;
  const all = prev != null ? a.concat(prev) : a, lo = Math.min(...all), hi = Math.max(...all), sp = hi - lo || 1;
  const y = (v) => (h - 3 - ((v - lo) / sp) * (h - 6)).toFixed(1), x = (i) => ((i / (a.length - 1)) * w).toFixed(1);
  const up = prev != null ? a[a.length - 1] >= prev : a[a.length - 1] >= a[0], col = up ? "var(--up)" : "var(--dn)";
  const line = a.map((v, i) => `${x(i)},${y(v)}`).join(" ");
  const base = prev != null ? `<line x1="0" x2="${w}" y1="${y(prev)}" y2="${y(prev)}" stroke="#4A4A52" stroke-dasharray="2 3" stroke-width="1"/>` : "";
  const area = fill ? `<polygon points="0,${h} ${line} ${w},${h}" fill="${col}" opacity=".10"/>` : "";
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">${base}${area}<polyline points="${line}" fill="none" stroke="${col}" stroke-width="1.6" stroke-linejoin="round" vector-effect="non-scaling-stroke"/></svg>`;
}
const icon = (d) => `<svg viewBox="0 0 24 24">${d}</svg>`;
const SEARCH_I = '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>';

async function api(path, init) {
  const r = await fetch(path, { credentials: "same-origin", ...init, headers: { "content-type": "application/json", ...((init || {}).headers || {}) } });
  let data = null;
  try { data = await r.json(); } catch { /* not JSON */ }
  if (r.status === 401 && path !== "/api/login") { S.me = null; renderLogin(); throw new Error("signed out"); }
  if (!r.ok) throw new Error((data && data.error) || "Something went wrong (" + r.status + ").");
  return data;
}

// ── data ──
async function need(symbols) {                             // fetch quotes that are older than 25 seconds, 40 at a time
  const now = Date.now(), want = [...new Set(symbols)].filter((s) => s && now - (S.at[s] || 0) > 25000);
  if (!want.length) return;
  want.forEach((s) => (S.at[s] = now));
  for (let i = 0; i < want.length; i += 40) {
    try {
      const d = await api("/api/quotes?s=" + encodeURIComponent(want.slice(i, i + 40).join(",")));
      for (const q of d.quotes) S.q[q.sym] = q;
      S.qv++;
    } catch (e) { want.slice(i, i + 40).forEach((s) => { if (!S.q[s]) S.q[s] = { sym: s, ok: false }; }); }
    paint();
  }
}
async function loadWatchlist() {
  try { const d = await api("/api/watchlist"); S.wl.rows = d.rows; S.wl.err = ""; if (!lists().includes(S.wl.list)) S.wl.list = lists()[0] || "Main"; }
  catch (e) { if (e.message !== "signed out") S.wl.err = e.message; }
  S.wl.loaded = true; paint(); refresh();
}
async function loadSignals() {
  try { S.sig = await api("/api/signals"); S.sigErr = ""; } catch (e) { if (e.message !== "signed out") S.sigErr = e.message; }
  paint();
}
async function loadPortfolio() {
  try { const d = await api("/api/portfolio"); Object.assign(S.pf, d, { err: "" }); S.pf.ver++; }
  catch (e) { if (e.message !== "signed out") S.pf.err = e.message; }
  S.pf.loaded = true; paint(); refresh(); PFV.loadHistory();
}
async function loadDeals() { if (S.deals) return; try { S.deals = (await api("/api/deals")).deals; } catch { S.deals = []; } paint(); }
async function loadBrief() { try { S.brief = (await api("/api/brief")).brief; } catch (e) { if (e.message !== "signed out") S.brief = null; } paint(); }
const lists = () => [...new Set([...S.wl.rows.map((r) => r.list), ...(S.wl.extra || [])])];
const mine = () => S.wl.rows.filter((r) => r.list === S.wl.list).map((r) => r.sym);
const inList = (sym) => S.wl.rows.some((r) => r.sym === sym && r.list === S.wl.list);
function pageSymbols() {
  const wl = mine();
  if (S.tab === "home") return [...HM.N50, ...S.pf.lots.map((l) => l.sym), ...wl.slice(0, 7)];
  if (S.tab === "watchlist") return wl;
  if (S.tab === "portfolio") return [...new Set(S.pf.lots.map((l) => l.sym)), ...wl.slice(0, 12)];
  if (S.tab === "news") return [...new Set([...S.wl.rows.map((r) => r.sym), ...S.pf.lots.map((l) => l.sym)])].slice(0, 120);
  if (S.tab === "markets") return [...MARKETS[S.mkTab].map((x) => x[1]), ...wl.slice(0, 12)];
  return wl.slice(0, 12);
}
const refresh = () => { need(pageSymbols()); if (S.tab === "home") HM.loadHome(); };

// ── pieces ──
const qOf = (s) => S.q[s];
function stockRow(sym, opts = {}) {
  const q = qOf(sym), name = opts.name || (q && q.name) || NAMES[sym] || "";
  const z = !q ? '<b class="sk">0000.00</b><small class="sk">+0.00%</small>' : !q.ok ? '<b class="mu">—</b><small class="mu">no price</small>'
    : `<b>${num(q.price)}</b><small class="${cls(q.pct)}">${opts.wide ? sgn(q.chg) + " (" + pc(q.pct) + ")" : pc(q.pct)}</small>`;
  const x = opts.remove ? `<span class="x" data-act="remove" data-s="${esc(sym)}" title="Remove from this list">✕</span>` : "";
  const w = opts.remove ? S.wl.rows.find((r) => r.sym === sym && r.list === S.wl.list) : null;
  const hit = w && q && q.ok && ((w.above && q.price >= w.above) || (w.below && q.price <= w.below));
  const badge = (hit ? ' <span class="chip a">Alert hit</span>' : "") + (w && w.note ? ' <span class="pen" title="' + esc(w.note) + '">✎</span>' : "");
  return `<button class="row ${opts.remove ? "wl" : ""}" data-sym="${esc(sym)}"><span class="a"><b>${esc(opts.title || sym)}${badge}</b>${name && name !== (opts.title || sym) ? `<small>${esc(name)}</small>` : ""}</span>
    ${spark(q && q.spark, q && q.prev)}<span class="z">${z}</span>${x}</button>`;
}
function rail() {                                           // desktop: the watchlist stays next to every page
  const wl = mine();
  const body = !S.wl.loaded ? '<div class="note">Loading your watchlist…</div>' : S.wl.err ? `<div class="note">${esc(S.wl.err)}</div>`
    : !wl.length ? '<div class="empty"><b>No stocks yet</b>Search a stock above and add it to your watchlist.</div>' : wl.slice(0, 12).map((s) => stockRow(s)).join("");
  return `<aside class="rail-box only-d"><h2>Watchlist <a href="#/watchlist">Open</a></h2><div class="card">${body}</div></aside>`;
}

// ── pages ──
function orders() {
  const d = S.sig; if (!d) return null;
  const o = [];
  for (const r of (d.leaders && d.leaders.sell) || []) o.push(["Sell", "r", r.sym, "Leaders · " + (r.why || "leaves the book")]);
  for (const r of (d.leaders && d.leaders.buy) || []) o.push(["Buy", "g", r.sym, "Leaders · joins the 20 strongest"]);
  for (const r of (d.swing && d.swing.new) || []) o.push(["Buy", "g", r.sym, "Swing · leader pullback" + (r.stop ? " · stop " + num(r.stop) : "")]);
  for (const r of (d.swing && d.swing.open) || []) if (r.sell) o.push(["Sell", "r", r.sym, "Swing · " + r.sell]);
  for (const x of d.index || []) if (x.action && x.action !== "none") o.push([x.action === "buy" ? "Buy" : "Sell", x.action === "buy" ? "g" : "r", x.name, "Index · " + (x.why || x.setup) + (x.status === "trial" ? " · on trial" : "")]);
  return o;
}
function ordersOf(d) {                                     // everything to do at the next open, across the books (fno_page.order_rows)
  const out = [], L = d.leaders || {}, W = d.swing || {};
  for (const r of L.sell || []) out.push({ book: "Leaders", act: "Sell", sym: r.sym, ref: r.price, stop: null });
  for (const r of L.buy || []) out.push({ book: "Leaders", act: "Buy", sym: r.sym, ref: r.price, stop: null });
  if (W.status !== "off") { for (const r of W.open || []) if (r.sell) out.push({ book: "Swing", act: "Sell", sym: r.sym, ref: r.price, stop: null }); for (const r of W.new || []) out.push({ book: "Swing", act: "Buy", sym: r.sym, ref: r.price, stop: r.stop }); }
  for (const x of d.index || []) { if (x.status === "off") continue; if (x.action === "buy") out.push({ book: "Index", act: "Buy", sym: x.name, ref: x.last, stop: null }); else if (x.action === "sell") out.push({ book: "Index", act: "Sell", sym: x.name, ref: x.last, stop: null }); }
  return out;
}
function ordersCard(short) {
  const o = orders();
  if (!o) return `<div class="card"><div class="note">${S.sigErr ? esc(S.sigErr) : "Loading…"}</div></div>`;
  const body = o.length ? o.slice(0, short ? 4 : 99).map(([a, c, sym, why]) => `<div class="order"><span class="chip ${c}">${a}</span><div><b>${esc(sym)}</b><small>${esc(why)}</small></div></div>`).join("")
    + (short && o.length > 4 ? `<div class="note">and ${o.length - 4} more</div>` : "")
    : `<div class="empty"><b>No orders for the next open</b>Signals from the close of ${esc(S.sig.asof)}. Leaders are checked once a week; swing and index signals appear on the day they trigger.</div>`;
  return `<div class="card">${body}</div>`;
}
function watchlist() {
  const ls = lists().length ? lists() : ["Main"], wl = mine();
  let rows = wl.slice();
  if (S.sort === "pct") rows.sort((a, b) => ((qOf(b) || {}).pct ?? -1e9) - ((qOf(a) || {}).pct ?? -1e9));
  if (S.sort === "az") rows.sort();
  const ok = wl.map(qOf).filter((q) => q && q.ok && q.pct != null), up = ok.filter((q) => q.pct > 0).length;
  const body = !S.wl.loaded ? '<div class="note">Loading your watchlist…</div>' : S.wl.err ? `<div class="empty"><b>Watchlist is not available</b>${esc(S.wl.err)}</div>`
    : !rows.length ? '<div class="empty"><b>This list is empty</b>Search a stock, open it and tap “Add to watchlist”.</div>' : rows.map((s) => stockRow(s, { remove: true, wide: true })).join("");
  return `<div><div class="hd"><div><h1>Watchlist</h1><div class="sub">${wl.length} stock${wl.length === 1 ? "" : "s"}${ok.length ? ` · <span class="up">${up} up</span> · <span class="dn">${ok.length - up} down</span>` : ""}</div></div>
      <select class="sel" data-act="sort"><option value="order"${S.sort === "order" ? " selected" : ""}>Your order</option><option value="pct"${S.sort === "pct" ? " selected" : ""}>Biggest gainers</option><option value="az"${S.sort === "az" ? " selected" : ""}>Name A–Z</option></select></div>
    <div class="seg">${ls.map((l) => `<button class="${l === S.wl.list ? "on" : ""}" data-act="list" data-v="${esc(l)}">${esc(l)}</button>`).join("")}<button class="ghost" data-act="newlist">+ New list</button></div>
    <div class="card">${body}</div>
    <div class="note" style="padding:14px 2px">Tap a stock for its chart, a note and price alerts. To add one, search it at the top.</div></div>`;
}
function signals() {
  const d = S.sig;
  if (!d) return `<div><div class="hd"><div><h1>Signals</h1></div></div><div class="card"><div class="note">${S.sigErr ? esc(S.sigErr) : "Loading…"}</div></div></div>`;
  const tabs = ["Leaders", "Swing", "Index", "Big players", "Avoid"];
  let body = "";
  if (S.sigTab === "Leaders") {
    const L = d.leaders || {}, w = ((L.record || {}).windows || [])[0];
    const rec = w ? `<div class="verdict">In the test since ${esc((L.record.start || "").slice(0, 4))} this book made <b>${frac(w.book.cagr)}</b> a year against <b>${frac(w.index.cagr)}</b> for NIFTY 500, with a worst fall of <b>${frac(w.book.dd, 0)}</b>. A past test is not a promise.</div>` : "";
    const rows = (L.holdings || []).map((h) => `<button class="row two" data-sym="${esc(h.sym)}"><span class="a"><b>${esc(h.sym)}${h.flag === "sell" ? ' <span class="chip r">Sell</span>' : h.flag === "risk" ? ' <span class="chip a">At risk</span>' : ""}</b>
      <small>Since ${esc(h.since)} · bought at ${num(h.entry)}</small></span><span class="z"><b>${num(h.price)}</b><small class="${cls(h.pnl)}">${frac(h.pnl)}</small></span></button>`).join("");
    body = `<div class="card">${rec}<div class="note" style="border-bottom:1px solid var(--line)">${esc(L.rule || "")}</div>${rows || '<div class="empty">No holdings.</div>'}</div>`;
  } else if (S.sigTab === "Swing") {
    const W = d.swing || {}, a = (W.record || {}).all;
    const rec = a ? `<div class="verdict">In the test: <b>${a.n}</b> trades, <b>${num(a.win * 100, 0)}%</b> made money, average result <b>${frac(a.exp, 2)}</b> a trade. A past test is not a promise.</div>` : "";
    const open = (W.open || []).map((r) => `<button class="row two" data-sym="${esc(r.sym)}"><span class="a"><b>${esc(r.sym)} <span class="chip a">Open</span></b><small>${r.stop ? "Stop " + num(r.stop) : ""}</small></span><span class="z"><b>${num(r.price)}</b></span></button>`).join("");
    const recent = (W.recent || []).slice(0, 12).map((r) => `<button class="row two" data-sym="${esc(r.sym)}"><span class="a"><b>${esc(r.sym)}</b><small>${esc(r.in)} to ${esc(r.out)} · ${esc(r.why || "")}</small></span>
      <span class="z"><b class="${cls(r.ret)}">${frac(r.ret)}</b><small class="mu">${r.bars} days</small></span></button>`).join("");
    body = `<div class="card">${rec}<div class="note" style="border-bottom:1px solid var(--line)">${esc(W.rule || "")}</div>${open}${recent || '<div class="empty">No closed trades yet.</div>'}</div>`;
  } else if (S.sigTab === "Big players") {
    const F = d.footprints || {};
    const block = (title, sub, rows, col) => !rows || !rows.length ? "" : `<h2>${title} <span>${sub}</span></h2><div class="card">${rows.slice(0, 8).map((r) =>
      `<button class="row two" data-sym="${esc(r.sym)}"><span class="a"><b>${esc(r.sym)}</b><small>${num(r.x, 1)}× the usual · ${num(r.dshare * 100, 0)}% taken for delivery</small></span>
       <span class="z"><b>${num(r.price)}</b><small class="${cls(r.d10)}">${frac(r.d10)} in 10 days</small></span></button>`).join("")}</div>`;
    const dl = S.deals == null ? '<div class="note">Loading deals…</div>' : !S.deals.length ? '<div class="empty">No bulk or block deals collected yet.</div>' : S.deals.slice(0, 25).map((x) =>
      `<button class="row two" data-sym="${esc(x.sym)}"><span class="a"><b>${esc(x.sym)} <span class="chip ${x.side === "BUY" ? "g" : "r"}">${x.side === "BUY" ? "Bought" : "Sold"}</span></b><small>${esc(x.who)} · ${esc(x.date)} · ${esc(x.kind)} deal</small></span>
       <span class="z"><b>₹${num(x.cr, 1)} Cr</b><small class="mu">at ${num(x.price)}</small></span></button>`).join("");
    body = block("Quiet buying", "heavy delivery, price holding up", F.accumulation) + block("Quiet selling", "heavy delivery, price falling", F.distribution)
      + block("Delivery spikes", "far more shares taken home than usual", F.delivery_spike) + block("Big tickets", "unusually large trades", F.big_tickets)
      + `<h2>Bulk and block deals <span>from NSE, latest first</span></h2><div class="card">${dl}</div>`;
  } else if (S.sigTab === "Avoid") {
    body = `<div class="card"><div class="note" style="border-bottom:1px solid var(--line)">The weakest liquid stocks by 12-month strength, below their 200-day average. Not short calls: a list of what the rules stay away from.</div>${(d.avoid || []).map((r) =>
      `<button class="row two" data-sym="${esc(r.sym)}"><span class="a"><b>${esc(r.sym)}</b><small>${frac(r.below200, 0)} against its 200-day average</small></span>
       <span class="z"><b>${num(r.price)}</b><small class="${cls(r.d1)}">${frac(r.d1)}</small></span></button>`).join("") || '<div class="empty">Nothing here today.</div>'}</div>`;
  } else {
    body = (d.index || []).map((x) => { const a = (x.record || {}).all;
      return `<div class="card" style="margin-bottom:12px"><div class="verdict"><b>${esc(x.name)}</b> · ${esc(x.setup || "")} ${x.status === "trial" ? '<span class="chip a">On trial</span>' : '<span class="chip g">Approved</span>'}</div>
        <div class="stats"><div><span>Last close</span><b>${num(x.last)}</b></div><div><span>Buys at or below</span><b>${num(x.trigger)}</b></div>
        <div><span>200-day average</span><b class="${x.above200 ? "up" : "dn"}">${x.above200 ? "Above" : "Below"}</b></div><div><span>In the test</span><b>${a ? a.n + " trades · " + num(a.win * 100, 0) + "% won" : "—"}</b></div></div>
        <div class="note">${esc(x.rule || "")}</div></div>`; }).join("") || '<div class="card"><div class="empty">No index setups.</div></div>';
  }
  return `<div><div class="hd"><div><h1>Signals</h1><div class="sub">Fixed rules tested on real prices · from the close of ${esc(d.asof)}</div></div></div>
    <h2 style="margin-top:6px">Orders for the next open</h2>${ordersCard(false)}
    <h2>The books</h2><div class="seg">${tabs.map((t) => `<button class="${t === S.sigTab ? "on" : ""}" data-act="sigtab" data-v="${t}">${t}</button>`).join("")}</div>${body}
    <div class="note" style="padding:14px 2px">Signals are rules applied to past and present prices. They can lose money, sometimes for long stretches. This is information, not investment advice. The options map and the full evidence tables are in the <a href="${CLASSIC}" target="_blank" rel="noopener" style="color:var(--amber)">classic app</a>.</div></div>`;
}
function markets() {
  const rows = MARKETS[S.mkTab].map(([n, s]) => stockRow(s, { title: n, name: " " })).join("");
  return `<div><div class="hd"><div><h1>Markets</h1><div class="sub">Indices, commodities, currencies and rates</div></div></div>
    <div class="seg">${Object.keys(MARKETS).map((t) => `<button class="${t === S.mkTab ? "on" : ""}" data-act="mktab" data-v="${esc(t)}">${esc(t)}</button>`).join("")}</div>
    <div class="card">${rows}</div><div class="note" style="padding:14px 2px">Changes are from the previous close. For rupee pairs a rise means a weaker rupee.</div></div>`;
}
function brief() {
  const B = S.brief, head = (sub) => `<div class="hd"><div><h1>Daily brief</h1><div class="sub">${sub}</div></div></div>`;
  if (B === undefined) return `<div>${head("What happened overnight and what can move Indian stocks today")}<div class="card"><div class="note">Loading…</div></div></div>`;
  if (!B) return `<div>${head("What happened overnight and what can move Indian stocks today")}<div class="card"><div class="empty"><b>No brief here yet</b>The morning job writes it at about 8 am on weekdays and it appears here after the next run.</div></div></div>`;
  const src = (a) => (Array.isArray(a) && a.length ? `<div class="srcs">${a.filter((x) => x && x.url).map((x) => `<a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.name || "Source")}</a>`).join("")}</div>` : "");
  const para = (t, x) => (x ? `<div class="bp"><b>${t}</b>${esc(x)}</div>` : "");
  const g = B.global || {};
  const deep = (B.deep || []).map((d) => `<div class="card pad story"><div class="chip">${esc(d.region || "")}</div><h3>${esc(d.title)}</h3>${d.update && d.changed ? `<div class="bp"><b>What changed</b>${esc(d.changed)}</div>` : ""}
    ${para("What happened", d.what)}${para("Why it matters", d.why)}${para("What to watch", d.watch)}${src(d.src)}</div>`).join("");
  const line = (r) => `<div class="order"><div><b style="font-weight:500">${esc(r.line)}</b>${r.tag ? `<small style="color:var(--amber)">${esc(r.tag)}</small>` : ""}${src(r.src)}</div></div>`;
  return `<div>${head(nice(B.day) + (B.model ? "" : ""))}
    <div class="card pad">${(B.bottom_line || []).map((x, i) => `<div class="bl"><i>${i + 1}</i><span>${esc(x)}</span></div>`).join("")}</div>
    <h2>Overnight</h2><div class="card pad">${para("United States", g.us)}${para("Europe", g.europe)}${para("Asia this morning", g.asia)}${para("India", B.india)}${para("Commodities, currencies and rates", B.commodities)}</div>
    ${deep ? "<h2>Stories</h2>" + deep : ""}
    ${(B.radar || []).length ? `<h2>On the radar</h2><div class="card">${B.radar.map(line).join("")}</div>` : ""}
    ${(B.rapid || []).length ? `<h2>Rapid fire</h2><div class="card">${B.rapid.map(line).join("")}</div>` : ""}
    ${(B.today || []).length ? `<h2>On the calendar</h2><div class="card pad">${B.today.map((x) => `<div class="bp">${esc(x)}</div>`).join("")}</div>` : ""}
    <div class="note" style="padding:14px 2px">Written by an AI from public headlines and prices; every item links to its sources. Information, not investment advice.</div></div>`;
}
function more() {
  return `<div><div class="hd"><div><h1>More</h1></div></div>
    <div class="links only-m" style="margin-bottom:10px"><a class="link" href="#/news"><b>News desk</b><small>Market wire, your stocks and exchange filings.</small></a><a class="link" href="#/flow"><b>Money flow</b><small>Sector rotation: where to be, and when.</small></a><a class="link" href="#/regime"><b>Market regime</b><small>Trend, breadth and volatility: how hard to push today.</small></a><a class="link" href="#/markets"><b>Markets</b><small>Indices, commodities, currencies and rates.</small></a>
      <a class="link" href="#/brief"><b>Daily brief</b><small>What happened overnight and what can move stocks today.</small></a></div>
    <h2 style="margin-top:8px">Still in the classic app <span>opens in a new tab</span></h2>
    <div class="links">${MORE.map(([t, d]) => `<a class="link" href="${CLASSIC}" target="_blank" rel="noopener"><b>${t}</b><small>${d}</small></a>`).join("")}</div>
    <h2>Account</h2><div class="card pad"><div style="margin-bottom:12px">Signed in as <b>${esc(S.me.user)}</b> <span class="chip">${esc(S.me.role)}</span></div><button class="btn" data-act="logout">Sign out</button></div></div>`;
}
const PAGES = { home: HM.page, watchlist, portfolio: PFV.page, regime: RG.page, flow: MF.page, news: NW.page, signals, markets, brief, more };

// ── painting ──
function renderShell() {
  const open = marketOpen();
  $("#app").innerHTML = `<div class="shell">
    <header class="top"><a class="brand" href="#/home"><img src="/icon-180.png" alt="">ARKA <i>TRADES</i></a>
      <div class="field only-d"><svg viewBox="0 0 24 24">${SEARCH_I}</svg><input id="q" placeholder="Search a stock, for example TCS or Reliance" autocomplete="off" spellcheck="false"><div id="drop"></div></div>
      <span class="sp"></span><span class="mk ${open ? "on" : ""}"><b></b>${open ? "Market open" : "Market closed"}</span>
      <button class="ib only-m" data-act="search" aria-label="Search">${icon(SEARCH_I)}</button></header>
    <div id="msearch"></div><main id="main"></main>
    <nav class="nav"><a class="brand only-d" href="#/home"><img src="/icon-180.png" alt="">ARKA <i>TRADES</i></a>
      ${TABS.map(([k, n, d, mob]) => `<a href="#/${k}" data-tab="${k}"${mob ? "" : ' class="only-d"'}>${icon(d)}<span>${n}</span></a>`).join("")}
      <div class="who">Signed in as <b>${esc(S.me.user)}</b><br>New app · stage 7</div></nav></div>`;
}
function paint() {
  const main = $("#main"); if (!main || !S.me) return;
  const phoneTab = TABS.some(([k, , , mob]) => k === S.tab && mob) ? S.tab : "more";      // Markets and the brief light up More on a phone
  document.querySelectorAll(".nav a[data-tab]").forEach((a) => { a.classList.toggle("on", a.dataset.tab === S.tab); a.classList.toggle("onm", a.dataset.tab === phoneTab); });
  const withRail = !["home", "watchlist", "more", "brief", "portfolio", "regime", "flow", "news"].includes(S.tab);
  const ae = document.activeElement;                       // typing in a Portfolio box: leave the page alone so the cursor stays put
  if (ae && main.contains(ae) && ae.dataset.pf && ae.tagName === "INPUT" && !/range|checkbox/.test(ae.type)) { if (S.sheet && !typingInSheet()) paintSheet(); return; }
  const keep = ae && main.contains(ae) && ae.dataset && ae.dataset.nw ? [ae.dataset.nw, ae.selectionStart, ae.selectionEnd] : null;
  main.className = withRail ? "rail" : ["home", "portfolio", "regime", "flow", "news"].includes(S.tab) ? "wide" : "";
  const y = window.scrollY;
  main.innerHTML = PAGES[S.tab]() + (withRail ? rail() : "");
  window.scrollTo(0, y);
  if (keep) { const el = main.querySelector(`[data-nw="${keep[0]}"]`); if (el) { el.focus(); try { el.setSelectionRange(keep[1], keep[2]); } catch { /* not a text box */ } } }
  if (S.sheet && !typingInSheet()) paintSheet();
}
const typingInSheet = () => { const a = document.activeElement; return !!(a && $("#sheet").contains(a) && /INPUT|SELECT|TEXTAREA/.test(a.tagName)); };
function route() {
  const t = (location.hash.replace(/^#\/?/, "") || "home").split("/")[0];
  S.tab = PAGES[t] ? t : "home";
  window.scrollTo(0, 0); paint(); refresh();
  if (S.tab === "brief" && S.brief === undefined) loadBrief();
  if (S.tab === "regime") RG.loadRegime();
  if (S.tab === "flow") MF.loadFlow();
  if (S.tab === "news") NW.loadNews();
  if (S.tab === "signals" && S.sigTab === "Big players") loadDeals();
}

// ── stock sheet ──
async function openSheet(sym) {
  S.sheet = { sym, d: null, range: "day", err: "" }; paintSheet();
  if (S.pf.lots.some((l) => l.sym === sym)) need(S.pf.lots.map((l) => l.sym));          // a holding: its weight needs every holding's price
  try { const d = await api("/api/stock?s=" + encodeURIComponent(sym)); if (S.sheet && S.sheet.sym === sym) { S.sheet.d = d; if (d.ok) S.q[sym] = { ...(S.q[sym] || {}), ...d }; } }
  catch (e) { if (S.sheet && S.sheet.sym === sym) S.sheet.err = e.message; }
  paintSheet();
}
function closeSheet() { S.sheet = null; $("#sheet").innerHTML = ""; }
function field(label, name, value, attrs = "") { return `<label class="f"><span>${label}</span><input name="${name}" value="${esc(value ?? "")}" ${attrs}></label>`; }
function buyForm() {
  $("#sheet").innerHTML = `<div class="scrim" data-act="close"></div><section class="sheet" role="dialog" aria-label="Add a buy"><div class="grab"></div>
    <div class="hd"><div><h1>Add a buy</h1><small>A purchase you made, so the app can track its profit.</small></div><button class="ib" data-act="close" aria-label="Close">${icon('<path d="M6 6l12 12M18 6 6 18"/>')}</button></div>
    <form id="buy" class="form">${field("Stock (NSE ticker)", "sym", S.sheet.sym || "", 'placeholder="TCS" autocapitalize="characters" autocomplete="off" required')}
      <div class="g2">${field("Shares", "qty", "", 'inputmode="decimal" placeholder="10" required')}${field("Price paid (₹)", "price", "", 'inputmode="decimal" placeholder="3000" required')}</div>
      ${field("Buy date", "date", todayStr(), 'type="date" max="' + todayStr() + '"')}${field("Note (optional)", "note", "", 'placeholder="Why you bought it" maxlength="200"')}
      ${S.pf.can_book ? `<label class="f"><span>Book (optional)</span><input name="book" list="buy-books" value="${esc(S.pf.book !== "All books" ? S.pf.book : "Main")}" maxlength="24"><datalist id="buy-books">${PFV.books().map((b) => `<option>${esc(b)}</option>`).join("")}</datalist></label>` : ""}
      <div class="err"></div><button class="btn pri full" type="submit">Save buy</button></form></section>`;
}
function watchBlock(sym) {                                 // note, price alerts and moving between lists, for a stock in the open list
  const w = S.wl.rows.find((r) => r.sym === sym && r.list === S.wl.list); if (!w) return "";
  const others = lists().filter((l) => l !== S.wl.list);
  return `<h2>Note and price alerts</h2><form id="wmeta" class="form" data-s="${esc(sym)}">${field("Note", "note", w.note || "", 'placeholder="Why it is on your list" maxlength="200"')}
    <div class="g2">${field("Alert above (₹)", "above", w.above ?? "", 'inputmode="decimal" placeholder="none"')}${field("Alert below (₹)", "below", w.below ?? "", 'inputmode="decimal" placeholder="none"')}</div>
    ${others.length ? `<label class="f"><span>Move to another list</span><select name="to"><option value="">Keep in ${esc(S.wl.list)}</option>${others.map((l) => `<option>${esc(l)}</option>`).join("")}</select></label>` : ""}
    <div class="err"></div><button class="btn full" type="submit">Save</button></form>`;
}
function paintSheet() {
  const sh = S.sheet; if (!sh) return;
  if (sh.form === "buy") return buyForm();
  const q = (sh.d && sh.d.ok ? sh.d : qOf(sh.sym)) || {}, d = sh.d || {};
  const isStock = !/[\^=.]|-USD$/.test(sh.sym);
  const title = NAMES[sh.sym] || sh.sym, name = NAMES[sh.sym] ? "" : q.name || "";
  const band = (lo, hi, v, lab) => (lo == null || hi == null || v == null || hi <= lo ? "" :
    `<div class="rg"><div class="l"><span>${lab}</span><b>${num(lo)} to ${num(hi)}</b></div><div class="t"><i style="left:${Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100)).toFixed(1)}%"></i></div></div>`);
  const chart = sh.range === "day" ? spark(q.spark, q.prev, 400, 150, true) : spark(d.year, null, 400, 150, true);
  const trend = d.trend === "up" ? '<b class="up">Uptrend</b>' : d.trend === "down" ? '<b class="dn">Downtrend</b>' : d.trend === "side" ? "<b>Sideways</b>" : "<b>—</b>";
  const stat = (l, v) => `<div><span>${l}</span>${v}</div>`;
  const stats = sh.d ? `<div class="stats">${stat("1 week", `<b class="${cls(d.r1w)}">${pc(d.r1w, 1)}</b>`)}${stat("1 month", `<b class="${cls(d.r1m)}">${pc(d.r1m, 1)}</b>`)}${stat("3 months", `<b class="${cls(d.r3m)}">${pc(d.r3m, 1)}</b>`)}
      ${stat("1 year", `<b class="${cls(d.r1y)}">${pc(d.r1y, 1)}</b>`)}${stat("Trend", trend)}${stat("Volume today", `<b>${qty(q.vol)}</b>`)}</div>`
    : sh.err ? `<div class="note">${esc(sh.err)}</div>` : '<div class="note">Loading numbers…</div>';
  const has = inList(sh.sym);
  const acts = isStock ? `<div class="acts"><button class="btn ${has ? "red" : "pri"}" data-act="${has ? "remove" : "add"}" data-s="${esc(sh.sym)}">${has ? "Remove" : "Add to watchlist"}</button>
      <button class="btn" data-act="buyform" data-s="${esc(sh.sym)}">Add a buy</button></div>` : "";
  $("#sheet").innerHTML = `<div class="scrim" data-act="close"></div><section class="sheet" role="dialog" aria-label="${esc(title)}"><div class="grab"></div>
    <div class="hd"><div><h1>${esc(title)}</h1><small>${esc(name)}</small></div><button class="ib" data-act="close" aria-label="Close">${icon('<path d="M6 6l12 12M18 6 6 18"/>')}</button></div>
    <div class="px">${q.price != null ? num(q.price) : '<span class="sk">0000.00</span>'}<small class="${cls(q.pct)}">${q.price != null ? sgn(q.chg) + " (" + pc(q.pct) + ")" : ""}</small></div>
    <div class="chart">${chart}</div>
    <div class="seg"><button class="${sh.range === "day" ? "on" : ""}" data-act="range" data-v="day">Today</button><button class="${sh.range === "year" ? "on" : ""}" data-act="range" data-v="year">1 year</button></div>
    ${band(q.low, q.high, q.price, "Today's range")}${band(q.lo52, q.hi52, q.price, "52-week range")}${stats}${acts}${isStock ? PFV.positionBlock(sh.sym) + watchBlock(sh.sym) : ""}</section>`;
  if (sh.keep != null) { $(".sheet").scrollTop = sh.keep; sh.keep = null; }
}

// ── search ──
let searchTimer = 0, searchSeq = 0;
function paintDrop() {
  const el = $("#drop") || $("#mdrop"); if (!el) return;
  const r = S.results;
  el.innerHTML = r == null ? "" : `<div class="drop">${r.length ? r.map((x) => `<button data-sym="${esc(x.sym)}" data-fromsearch="1"><b>${esc(x.sym)}</b><small>${esc(x.name)}</small></button>`).join("")
    : '<div class="mu">No NSE stock found. Try the ticker, for example HDFCBANK.</div>'}</div>`;
}
function onSearch(v) {
  clearTimeout(searchTimer);
  if (v.trim().length < 2) { S.results = null; paintDrop(); return; }
  const seq = ++searchSeq;
  searchTimer = setTimeout(async () => {
    try { const d = await api("/api/search?q=" + encodeURIComponent(v.trim())); if (seq === searchSeq) { S.results = d.results || []; paintDrop(); } } catch { /* ignore */ }
  }, 220);
}
function toggleMobileSearch(on) {
  S.searching = on; S.results = null;
  $("#msearch").innerHTML = on ? `<div style="padding:4px 16px 8px"><div class="field"><svg viewBox="0 0 24 24">${SEARCH_I}</svg><input id="mq" placeholder="Search a stock, for example TCS" autocomplete="off" spellcheck="false"><div id="mdrop"></div></div></div>` : "";
  if (on) $("#mq").focus();
}
function endSearch() { S.results = null; paintDrop(); const q = $("#q"); if (q) q.value = ""; if (S.searching) toggleMobileSearch(false); }

// ── watchlist changes ──
let toastTimer = 0;
function toast(text, undo) {
  clearTimeout(toastTimer);
  $("#toast").innerHTML = `<div class="toast"><span>${esc(text)}</span>${undo ? '<button data-act="undo">Undo</button>' : ""}</div>`;
  S.undo = undo || null; toastTimer = setTimeout(() => ($("#toast").innerHTML = ""), undo ? 7000 : 2600);
}
async function addStock(sym, quiet) {
  if (inList(sym)) return;
  S.wl.rows.push({ sym, list: S.wl.list, note: "" }); paint(); need([sym]);
  try { await api("/api/watchlist", { method: "POST", body: JSON.stringify({ sym, list: S.wl.list }) }); if (!quiet) toast(sym + " added to " + S.wl.list); }
  catch (e) { S.wl.rows = S.wl.rows.filter((r) => !(r.sym === sym && r.list === S.wl.list)); paint(); toast("Could not add " + sym + ". " + e.message); }
}
async function removeStock(sym) {
  const list = S.wl.list, before = S.wl.rows.slice();
  S.wl.rows = S.wl.rows.filter((r) => !(r.sym === sym && r.list === list)); paint();
  try { await api(`/api/watchlist?s=${encodeURIComponent(sym)}&list=${encodeURIComponent(list)}`, { method: "DELETE" }); toast(sym + " removed", () => addStock(sym, true)); }
  catch (e) { S.wl.rows = before; paint(); toast("Could not remove " + sym + ". " + e.message); }
}

// ── sign-in ──
function renderLogin(err = "") {
  $("#sheet").innerHTML = ""; $("#toast").innerHTML = "";
  $("#app").innerHTML = `<div class="login"><form id="login"><div class="brand"><img src="/icon-180.png" alt="">ARKA <i>TRADES</i></div><h1>Sign in</h1><p>The market terminal for Indian traders</p>
    <input name="user" placeholder="User name" autocomplete="username" autocapitalize="none" required><input name="pass" type="password" placeholder="Password" autocomplete="current-password" required>
    <div class="err">${esc(err)}</div><button class="btn pri full" type="submit">Sign in</button></form></div>`;
}
async function start() {
  renderShell(); route(); loadWatchlist(); loadSignals(); loadPortfolio();
}
async function boot() {
  try { S.me = await api("/api/me"); start(); } catch { /* the sign-in screen is already showing */ }
}

// ── events ──
const formErr = (form, msg) => { const el = form.querySelector(".err"); if (el) el.textContent = msg; const b = form.querySelector("button[type=submit]"); if (b) b.disabled = false; };
async function sendForm(form, path, body, method = "POST") {
  const b = form.querySelector("button[type=submit]"); if (b) b.disabled = true;
  try { await api(path, { method, body: JSON.stringify(body) }); return true; } catch (er) { formErr(form, er.message); return false; }
}
document.addEventListener("submit", async (e) => {
  const f = e.target, id = f.getAttribute("id");       // not f.id: a field named "id" would hide it
  if (["buy", "sell", "plan", "wmeta", "move"].includes(id)) {
    e.preventDefault();
    const v = Object.fromEntries(new FormData(f)), sym = (f.dataset.s || v.sym || "").toUpperCase().trim().replace(/\.NS$/, "");
    const keep = () => { if (S.sheet) S.sheet.keep = ($(".sheet") || {}).scrollTop || 0; };
    if (id === "buy") {
      if (!/^[A-Z0-9&._-]{1,24}$/.test(sym)) return formErr(f, "Type the NSE ticker, for example TCS.");
      if (!(Number(v.qty) > 0) || !(Number(v.price) > 0)) return formErr(f, "Shares and price must be numbers above zero.");
      const b = f.querySelector("button[type=submit]"); b.disabled = true;
      try { const d = await api("/api/quotes?s=" + encodeURIComponent(sym)); if (!d.quotes[0] || !d.quotes[0].ok) return formErr(f, sym + " was not found on NSE. Check the ticker."); S.q[sym] = d.quotes[0]; }
      catch (er) { return formErr(f, er.message); }
      const book = (v.book || "Main").replace(/[^\w &.-]/g, "").trim().slice(0, 24) || "Main";
      const b2 = f.querySelector("button[type=submit]"); b2.disabled = true;
      try { const r = await api("/api/portfolio", { method: "POST", body: JSON.stringify({ sym, qty: Number(v.qty), price: Number(v.price), date: v.date, note: v.note, book }) });
        closeSheet(); toast(r.booked === false ? "Buy saved in Main: the book could not be saved" : "Buy saved: " + sym); await loadPortfolio(); }
      catch (er) { formErr(f, er.message); }
    } else if (id === "sell") {
      if (!(Number(v.qty) > 0) || !(Number(v.price) > 0)) return formErr(f, "Shares and price must be numbers above zero.");
      const x = { sym, qty: Number(v.qty), price: Number(v.price), date: v.date || todayStr(), book: v.book || "Main" }, why = PFV.sellProblem(x);
      if (why) return formErr(f, why);
      if (await sendForm(f, "/api/portfolio/sell", x)) { toast("Sell recorded: " + sym); keep(); document.activeElement && document.activeElement.blur(); await loadPortfolio(); paintSheet(); }
    } else if (id === "plan") {
      if (Number(v.stop) > 0 && Number(v.target) > 0 && Number(v.target) <= Number(v.stop)) return formErr(f, "The target must be above the stop-loss.");
      if (await sendForm(f, "/api/portfolio/plan", { sym, stop: v.stop, target: v.target, book: f.dataset.book || "Main" })) { toast("Stop and target saved"); keep(); document.activeElement && document.activeElement.blur(); await loadPortfolio(); paintSheet(); }
    } else if (id === "move") {
      if (await sendForm(f, "/api/portfolio/move", { id: v.lot, book: v.book })) { toast("Moved to " + v.book); keep(); document.activeElement && document.activeElement.blur(); await loadPortfolio(); paintSheet(); }
    } else {
      if (await sendForm(f, "/api/watchlist", { sym, list: S.wl.list, note: v.note, above: v.above, below: v.below, to: v.to || "" }, "PATCH")) {
        toast(v.to ? sym + " moved to " + v.to : "Saved"); keep(); await loadWatchlist(); document.activeElement && document.activeElement.blur(); paintSheet(); }
    }
    return;
  }
  if (e.target.id !== "login") return;
  e.preventDefault();
  const fd = new FormData(e.target), btn = e.target.querySelector("button");
  btn.disabled = true; btn.textContent = "Signing in…";
  try { S.me = await api("/api/login", { method: "POST", body: JSON.stringify({ user: fd.get("user"), pass: fd.get("pass") }) }); start(); }
  catch (er) { renderLogin(er.message); }
});
function pfInput(el, ev) {                                // a control on the Portfolio page changed
  const P = S.pf, k = el.dataset.pf, [g, f] = k.split("."), text = el.tagName === "INPUT" && !/range|checkbox/.test(el.type);
  if (text !== (ev === "input") && !(el.type === "range" && ev === "input")) return;       // text boxes react as you type, the rest when they change
  if (f) P[g][f] = el.type === "checkbox" ? el.checked : el.value; else P[k] = el.value;
  const part = PFV.partial[f ? g : k];
  if (part) { const box = document.getElementById(part[0]); if (box) box.innerHTML = part[1](PFV.calc()); return; }
  if (k === "bench") PFV.loadHistory();
  paint();
}
let nwTimer = 0;
document.addEventListener("input", (e) => { if (e.target.id === "q" || e.target.id === "mq") onSearch(e.target.value); else if (e.target.dataset.pf) pfInput(e.target, "input");
  else if (e.target.dataset.nw) { S.nw[e.target.dataset.nw] = e.target.value; clearTimeout(nwTimer); nwTimer = setTimeout(paint, 180); } });
document.addEventListener("change", (e) => { if (e.target.dataset.mf === "pick") { S.mf.pick = e.target.value; paint(); return; } if (e.target.dataset.act === "sort") { S.sort = e.target.value; paint(); } else if (e.target.dataset.pf) pfInput(e.target, "change"); });
document.addEventListener("pointermove", chartMove, { passive: true });
document.addEventListener("pointerdown", chartMove, { passive: true });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") { if (S.sheet) closeSheet(); else endSearch(); } });
document.addEventListener("click", async (e) => {
  const a = e.target.closest("[data-act]"), act = a && a.dataset.act;
  if (act) {
    if (act === "remove") { e.preventDefault(); e.stopPropagation(); await removeStock(a.dataset.s); if (S.sheet) paintSheet(); return; }
    if (act === "add") { await addStock(a.dataset.s); if (S.sheet) paintSheet(); return; }
    if (act === "undo") { const u = S.undo; $("#toast").innerHTML = ""; if (u) u(); return; }
    if (act === "close") return closeSheet();
    if (act === "range") { S.sheet.range = a.dataset.v; return paintSheet(); }
    if (act === "list") { S.wl.list = a.dataset.v; paint(); return refresh(); }
    if (act === "sigtab") { S.sigTab = a.dataset.v; if (S.sigTab === "Big players") loadDeals(); return paint(); }
    if (act === "buyform") { S.sheet = { form: "buy", sym: a.dataset.s || "" }; paintSheet(); const i = $("#buy input[name=" + (a.dataset.s ? "qty" : "sym") + "]"); if (i) i.focus(); return; }
    if (act === "dellot") { if (!confirm("Delete this buy? This cannot be undone.")) return; try { await api("/api/portfolio?id=" + a.dataset.id, { method: "DELETE" }); toast("Buy deleted"); } catch (er) { toast(er.message); }
      await loadPortfolio(); return paintSheet(); }
    if (act === "hmgo") return HM.go(a.dataset.v);
    if (act === "nwrefresh") { S.nw.at = 0; NW.loadNews(true); return paint(); }
    if (act === "nwdays") { S.nw.days = +a.dataset.v; S.nw.at = 0; NW.loadNews(true); return paint(); }
    if (act === "nwsort") { S.nw.sort = a.dataset.v; return paint(); }
    if (act === "nwmode") { S.nw.mode = a.dataset.v; return paint(); }
    if (act === "nwpart") { S.nw.part = a.dataset.v; return paint(); }
    if (act === "mfview") { S.mf.view = a.dataset.v; window.scrollTo(0, 0); return paint(); }
    if (act === "mfsec") { e.preventDefault(); S.mf.pick = a.dataset.v; S.mf.view = "Sector"; window.scrollTo(0, 0); return paint(); }
    if (act.startsWith("mfset-")) { const k = act.slice(6); S.mf[k] = k === "regime" ? a.dataset.v === "1" : +a.dataset.v; return paint(); }
    if (act === "mfshow") { S.mf.show = a.dataset.v; return paint(); }
    if (act === "mftail") { S.mf.tail = +a.dataset.v; return paint(); }
    if (act === "mffpi") { S.mf.fpiN = +a.dataset.v; return paint(); }
    if (act === "mfsort") { const id = a.dataset.id, k = a.dataset.v, cur = S.mf.sort[id]; S.mf.sort[id] = [k, cur && cur[0] === k ? -cur[1] : -1]; return paint(); }
    if (act === "mfcsv") return MF.csv();
    if (act === "rgset") { S.rg[a.dataset.k] = a.dataset.v; if (a.dataset.k === "uni") RG.loadRegime(); return paint(); }
    if (act === "pfview") { S.pf.view = a.dataset.v; window.scrollTo(0, 0); return paint(); }
    if (act === "pfcsv") return PFV.csv(a.dataset.v);
    if (act === "pfrefresh") { S.at = {}; toast("Refreshing prices and history"); refresh(); return PFV.loadHistory(true); }
    if (act === "delsell") { if (!confirm("Delete this sell? This cannot be undone.")) return; try { await api("/api/portfolio/sell?id=" + a.dataset.id, { method: "DELETE" }); toast("Sell deleted"); } catch (er) { toast(er.message); } return loadPortfolio(); }
    if (act === "newbook") { const n = (prompt("Name for the new book (for example Trading or Long term)") || "").replace(/[^\w &.-]/g, "").trim().slice(0, 24); if (!n) return;
      S.pf.extraBooks = [...new Set([...(S.pf.extraBooks || []), n])]; paint(); return toast("Book “" + n + "” is ready. Choose it when you add a buy."); }
    if (act === "newlist") { const n = (prompt("Name for the new list (for example Swing or Long term)") || "").replace(/[^\w &.-]/g, "").trim().slice(0, 24); if (!n) return;
      S.wl.extra = [...new Set([...(S.wl.extra || []), n])]; S.wl.list = n; paint(); return toast("List “" + n + "” is ready. It is saved when you add its first stock."); }
    if (act === "mktab") { S.mkTab = a.dataset.v; paint(); return refresh(); }
    if (act === "search") return toggleMobileSearch(!S.searching);
    if (act === "logout") { try { await api("/api/logout", { method: "POST" }); } catch { /* ignore */ } S.me = null; return renderLogin(); }
  }
  const s = e.target.closest("[data-sym]");
  if (s) { if (s.dataset.fromsearch) endSearch(); return openSheet(s.dataset.sym); }
  if (S.results && !e.target.closest(".field")) { S.results = null; paintDrop(); }
});
window.addEventListener("hashchange", route);
window.addEventListener("scroll", () => { if (S.tab === "home") HM.spy(); }, { passive: true });
document.addEventListener("visibilitychange", () => { if (!document.hidden && S.me) refresh(); });
setInterval(() => { if (S.me && !document.hidden && marketOpen()) refresh(); if (S.me && !document.hidden && S.tab === "news") { NW.loadNews(); paint(); } }, 30000);
boot();
export { S, api, paint, esc, num, sgn, pc, cls, rs, nice, todayStr, field, toast, need, ordersOf };

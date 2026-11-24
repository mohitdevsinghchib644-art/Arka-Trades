// Arka Trades · new app (stage 1): Home, Watchlist, Signals, Markets, More. Plain JavaScript, no build step.
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
const TABS = [["home", "Home", '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/>'], ["watchlist", "Watchlist", '<path d="M6 3h12v18l-6-4.5L6 21z"/>'],
  ["signals", "Signals", '<path d="M3 17l5-6 4 4 8-10"/><path d="M15 5h5v5"/>'], ["markets", "Markets", '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3.2 3 14.8 0 18M12 3c-3 3.2-3 14.8 0 18"/>'],
  ["more", "More", '<circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/>']];
const MORE = [["Stock research", "Financials, charts, filings and peers for any stock."], ["Portfolio", "Holdings, profit and loss, stops and tax."], ["Arka AI", "Chart reading with your own playbook."],
  ["Daily brief", "What happened overnight and what can move stocks today."], ["Money flow", "Where money is moving across sectors."], ["Market regime", "Trend, breadth and volatility of the market."],
  ["Screener", "Describe a setup and scan the market."], ["Backtest", "Test a rule on past prices."], ["News", "Market wire and exchange filings."]];

const S = { me: null, tab: "home", q: {}, at: {}, wl: { rows: [], list: "Main", loaded: false, err: "" }, sig: null, sigErr: "", sigTab: "Leaders", mkTab: "India", sort: "order",
            sheet: null, results: null, searching: false };

// ── small helpers ──
const num = (v, d = 2) => (v == null || isNaN(v) ? "—" : Number(v).toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d }));
const sgn = (v, d = 2) => (v == null || isNaN(v) ? "—" : (v > 0 ? "+" : v < 0 ? "−" : "") + num(Math.abs(v), d));
const pc = (v, d = 2) => (v == null || isNaN(v) ? "—" : sgn(v, d) + "%");
const cls = (v) => (v == null || isNaN(v) || v === 0 ? "mu" : v > 0 ? "up" : "dn");
const qty = (v) => (v == null ? "—" : v >= 1e7 ? num(v / 1e7, 2) + " Cr" : v >= 1e5 ? num(v / 1e5, 2) + " L" : num(v, 0));
const frac = (v, d = 1) => pc(v == null ? null : v * 100, d);
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
const lists = () => [...new Set(S.wl.rows.map((r) => r.list))];
const mine = () => S.wl.rows.filter((r) => r.list === S.wl.list).map((r) => r.sym);
const inList = (sym) => S.wl.rows.some((r) => r.sym === sym && r.list === S.wl.list);
function pageSymbols() {
  const wl = mine();
  if (S.tab === "home") return [...INDICES.map((x) => x[1]), ...SECTORS.map((x) => x[1]), ...wl.slice(0, 12)];
  if (S.tab === "watchlist") return wl;
  if (S.tab === "markets") return [...MARKETS[S.mkTab].map((x) => x[1]), ...wl.slice(0, 12)];
  return wl.slice(0, 12);
}
const refresh = () => need(pageSymbols());

// ── pieces ──
const qOf = (s) => S.q[s];
function stockRow(sym, opts = {}) {
  const q = qOf(sym), name = opts.name || (q && q.name) || NAMES[sym] || "";
  const z = !q ? '<b class="sk">0000.00</b><small class="sk">+0.00%</small>' : !q.ok ? '<b class="mu">—</b><small class="mu">no price</small>'
    : `<b>${num(q.price)}</b><small class="${cls(q.pct)}">${opts.wide ? sgn(q.chg) + " (" + pc(q.pct) + ")" : pc(q.pct)}</small>`;
  const x = opts.remove ? `<span class="x" data-act="remove" data-s="${esc(sym)}" title="Remove from this list">✕</span>` : "";
  return `<button class="row ${opts.remove ? "wl" : ""}" data-sym="${esc(sym)}"><span class="a"><b>${esc(opts.title || sym)}</b>${name && name !== (opts.title || sym) ? `<small>${esc(name)}</small>` : ""}</span>
    ${spark(q && q.spark, q && q.prev)}<span class="z">${z}</span>${x}</button>`;
}
function rail() {                                           // desktop: the watchlist stays next to every page
  const wl = mine();
  const body = !S.wl.loaded ? '<div class="note">Loading your watchlist…</div>' : S.wl.err ? `<div class="note">${esc(S.wl.err)}</div>`
    : !wl.length ? '<div class="empty"><b>No stocks yet</b>Search a stock above and add it to your watchlist.</div>' : wl.slice(0, 12).map((s) => stockRow(s)).join("");
  return `<aside class="rail-box only-d"><h2>Watchlist <a href="#/watchlist">Open</a></h2><div class="card">${body}</div></aside>`;
}

// ── pages ──
function home() {
  const t = ist(), hr = t.getHours(), hello = hr < 12 ? "Good morning" : hr < 17 ? "Good afternoon" : "Good evening";
  const tiles = INDICES.map(([n, s]) => { const q = qOf(s);
    return `<button class="tile" data-sym="${esc(s)}"><div class="n">${n}</div><div class="p">${q && q.ok ? num(q.price) : '<span class="' + (q ? "mu" : "sk") + '">—</span>'}</div>
      <div class="c ${cls(q && q.pct)}">${q && q.ok ? sgn(q.chg) + " (" + pc(q.pct) + ")" : "&nbsp;"}</div>${spark(q && q.spark, q && q.prev, 120, 30, true)}</button>`; }).join("");
  const secs = SECTORS.map(([n, s]) => ({ n, q: qOf(s) })).filter((x) => x.q && x.q.ok && x.q.pct != null).sort((a, b) => b.q.pct - a.q.pct);
  const mx = Math.max(1, ...secs.map((x) => Math.abs(x.q.pct)));
  const secHtml = secs.length ? secs.map((x) => { const w = (Math.abs(x.q.pct) / mx) * 50;
    return `<div class="sec"><span>${x.n}</span><span class="t"><i style="${x.q.pct >= 0 ? "left:50%" : "right:50%"};width:${w.toFixed(1)}%;background:var(${x.q.pct >= 0 ? "--up" : "--dn"})"></i></span><b class="${cls(x.q.pct)}">${pc(x.q.pct)}</b></div>`; }).join("")
    : '<div class="note">Loading sectors…</div>';
  const upN = secs.filter((x) => x.q.pct > 0).length;
  const wl = mine();
  const wlHtml = !S.wl.loaded ? "" : `<div class="only-m"><h2>Watchlist <a href="#/watchlist">See all</a></h2><div class="card">${wl.length ? wl.slice(0, 5).map((s) => stockRow(s)).join("")
    : '<div class="empty"><b>No stocks yet</b>Tap the search icon and add the stocks you follow.</div>'}</div></div>`;
  return `<div><div class="hd"><div><h1>${hello}</h1><div class="sub">${t.toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long" })}</div></div></div>
    <div class="tiles">${tiles}</div>
    <div class="two-col"><div><h2>Market health <span>${S.sig ? "close of " + esc(S.sig.asof) : ""}</span></h2>${health()}</div>
      <div><h2>Signal desk <a href="#/signals">Open</a></h2>${ordersCard(true)}</div></div>
    ${wlHtml}
    <h2>Sectors today <span>${secs.length ? upN + " of " + secs.length + " up" : ""}</span></h2><div class="card" style="padding:6px 0">${secHtml}</div>
    <div class="note" style="padding:14px 2px">Prices from Yahoo Finance and can trail the exchange by a few minutes. Information, not investment advice.</div></div>`;
}
function health() {
  const m = S.sig && S.sig.market;
  if (!m) return `<div class="card"><div class="note">${S.sigErr ? esc(S.sigErr) : "Loading…"}</div></div>`;
  const good = (m.above200 ? 1 : 0) + (m.b50 >= 0.5 ? 1 : 0) + (m.b200 >= 0.5 ? 1 : 0) + (m.highs > m.lows ? 1 : 0);
  const word = good >= 3 ? ["Healthy", "up"] : good === 2 ? ["Mixed", "mu"] : ["Weak", "dn"];
  return `<div class="card"><div class="verdict"><b class="${word[1]}">${word[0]}.</b> NIFTY 500 is ${m.above200 ? "above" : "below"} its 200-day average, and ${num(m.b200 * 100, 0)}% of stocks are above theirs.</div>
    <div class="stats"><div><span>Above 50-day average</span><b>${num(m.b50 * 100, 0)}%</b></div><div><span>Above 200-day average</span><b>${num(m.b200 * 100, 0)}%</b></div>
    <div><span>52-week highs · lows</span><b><span class="up">${m.highs}</span> · <span class="dn">${m.lows}</span></b></div><div><span>India VIX</span><b>${num(m.vix, 1)}</b></div></div></div>`;
}
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
function ordersCard(short) {
  const o = orders();
  if (!o) return `<div class="card"><div class="note">${S.sigErr ? esc(S.sigErr) : "Loading…"}</div></div>`;
  const body = o.length ? o.slice(0, short ? 4 : 99).map(([a, c, sym, why]) => `<div class="order"><span class="chip ${c}">${a}</span><div><b>${esc(sym)}</b><small>${esc(why)}</small></div></div>`).join("")
    + (short && o.length > 4 ? `<div class="note">and ${o.length - 4} more</div>` : "")
    : `<div class="empty"><b>No orders for the next open</b>Signals from the close of ${esc(S.sig.asof)}. Leaders are checked once a week; swing and index signals appear on the day they trigger.</div>`;
  return `<div class="card">${body}</div>`;
}
function watchlist() {
  const ls = lists(), wl = mine();
  let rows = wl.slice();
  if (S.sort === "pct") rows.sort((a, b) => ((qOf(b) || {}).pct ?? -1e9) - ((qOf(a) || {}).pct ?? -1e9));
  if (S.sort === "az") rows.sort();
  const ok = wl.map(qOf).filter((q) => q && q.ok && q.pct != null), up = ok.filter((q) => q.pct > 0).length;
  const body = !S.wl.loaded ? '<div class="note">Loading your watchlist…</div>' : S.wl.err ? `<div class="empty"><b>Watchlist is not available</b>${esc(S.wl.err)}</div>`
    : !rows.length ? '<div class="empty"><b>This list is empty</b>Search a stock, open it and tap “Add to watchlist”.</div>' : rows.map((s) => stockRow(s, { remove: true, wide: true })).join("");
  return `<div><div class="hd"><div><h1>Watchlist</h1><div class="sub">${wl.length} stock${wl.length === 1 ? "" : "s"}${ok.length ? ` · <span class="up">${up} up</span> · <span class="dn">${ok.length - up} down</span>` : ""}</div></div>
      <select class="sel" data-act="sort"><option value="order"${S.sort === "order" ? " selected" : ""}>Your order</option><option value="pct"${S.sort === "pct" ? " selected" : ""}>Biggest gainers</option><option value="az"${S.sort === "az" ? " selected" : ""}>Name A–Z</option></select></div>
    ${ls.length > 1 ? `<div class="seg">${ls.map((l) => `<button class="${l === S.wl.list ? "on" : ""}" data-act="list" data-v="${esc(l)}">${esc(l)}</button>`).join("")}</div>` : ""}
    <div class="card">${body}</div>
    <div class="note" style="padding:14px 2px">Tap a stock for its chart and numbers. To add one, search it at the top. Notes, price alerts and new lists are still in the <a href="${CLASSIC}" target="_blank" rel="noopener" style="color:var(--amber)">classic app</a>.</div></div>`;
}
function signals() {
  const d = S.sig;
  if (!d) return `<div><div class="hd"><div><h1>Signals</h1></div></div><div class="card"><div class="note">${S.sigErr ? esc(S.sigErr) : "Loading…"}</div></div></div>`;
  const tabs = ["Leaders", "Swing", "Index"];
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
    <div class="note" style="padding:14px 2px">Signals are rules applied to past and present prices. They can lose money, sometimes for long stretches. This is information, not investment advice. Big players, the options map and the full evidence are in the <a href="${CLASSIC}" target="_blank" rel="noopener" style="color:var(--amber)">classic app</a>.</div></div>`;
}
function markets() {
  const rows = MARKETS[S.mkTab].map(([n, s]) => stockRow(s, { title: n, name: " " })).join("");
  return `<div><div class="hd"><div><h1>Markets</h1><div class="sub">Indices, commodities, currencies and rates</div></div></div>
    <div class="seg">${Object.keys(MARKETS).map((t) => `<button class="${t === S.mkTab ? "on" : ""}" data-act="mktab" data-v="${esc(t)}">${esc(t)}</button>`).join("")}</div>
    <div class="card">${rows}</div><div class="note" style="padding:14px 2px">Changes are from the previous close. For rupee pairs a rise means a weaker rupee.</div></div>`;
}
function more() {
  return `<div><div class="hd"><div><h1>More</h1><div class="sub">These sections open in the classic app until they move here</div></div></div>
    <div class="links">${MORE.map(([t, d]) => `<a class="link" href="${CLASSIC}" target="_blank" rel="noopener"><b>${t}</b><small>${d}</small></a>`).join("")}</div>
    <h2>Account</h2><div class="card pad"><div style="margin-bottom:12px">Signed in as <b>${esc(S.me.user)}</b> <span class="chip">${esc(S.me.role)}</span></div><button class="btn" data-act="logout">Sign out</button></div></div>`;
}
const PAGES = { home, watchlist, signals, markets, more };

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
      ${TABS.map(([k, n, d]) => `<a href="#/${k}" data-tab="${k}">${icon(d)}<span>${n}</span></a>`).join("")}
      <div class="who">Signed in as <b>${esc(S.me.user)}</b><br>New app · stage 1</div></nav></div>`;
}
function paint() {
  const main = $("#main"); if (!main || !S.me) return;
  document.querySelectorAll(".nav a[data-tab]").forEach((a) => a.classList.toggle("on", a.dataset.tab === S.tab));
  const withRail = S.tab !== "watchlist" && S.tab !== "more";
  main.className = withRail ? "rail" : "";
  const y = window.scrollY;
  main.innerHTML = PAGES[S.tab]() + (withRail ? rail() : "");
  window.scrollTo(0, y);
  if (S.sheet) paintSheet();
}
function route() {
  const t = (location.hash.replace(/^#\/?/, "") || "home").split("/")[0];
  S.tab = PAGES[t] ? t : "home";
  window.scrollTo(0, 0); paint(); refresh();
}

// ── stock sheet ──
async function openSheet(sym) {
  S.sheet = { sym, d: null, range: "day", err: "" }; paintSheet();
  try { const d = await api("/api/stock?s=" + encodeURIComponent(sym)); if (S.sheet && S.sheet.sym === sym) { S.sheet.d = d; if (d.ok) S.q[sym] = { ...(S.q[sym] || {}), ...d }; } }
  catch (e) { if (S.sheet && S.sheet.sym === sym) S.sheet.err = e.message; }
  paintSheet();
}
function closeSheet() { S.sheet = null; $("#sheet").innerHTML = ""; }
function paintSheet() {
  const sh = S.sheet; if (!sh) return;
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
      <a class="btn" href="${CLASSIC}" target="_blank" rel="noopener">Full research</a></div>` : "";
  $("#sheet").innerHTML = `<div class="scrim" data-act="close"></div><section class="sheet" role="dialog" aria-label="${esc(title)}"><div class="grab"></div>
    <div class="hd"><div><h1>${esc(title)}</h1><small>${esc(name)}</small></div><button class="ib" data-act="close" aria-label="Close">${icon('<path d="M6 6l12 12M18 6 6 18"/>')}</button></div>
    <div class="px">${q.price != null ? num(q.price) : '<span class="sk">0000.00</span>'}<small class="${cls(q.pct)}">${q.price != null ? sgn(q.chg) + " (" + pc(q.pct) + ")" : ""}</small></div>
    <div class="chart">${chart}</div>
    <div class="seg"><button class="${sh.range === "day" ? "on" : ""}" data-act="range" data-v="day">Today</button><button class="${sh.range === "year" ? "on" : ""}" data-act="range" data-v="year">1 year</button></div>
    ${band(q.low, q.high, q.price, "Today's range")}${band(q.lo52, q.hi52, q.price, "52-week range")}${stats}${acts}</section>`;
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
  renderShell(); route(); loadWatchlist(); loadSignals();
}
async function boot() {
  try { S.me = await api("/api/me"); start(); } catch { /* the sign-in screen is already showing */ }
}

// ── events ──
document.addEventListener("submit", async (e) => {
  if (e.target.id !== "login") return;
  e.preventDefault();
  const f = new FormData(e.target), btn = e.target.querySelector("button");
  btn.disabled = true; btn.textContent = "Signing in…";
  try { S.me = await api("/api/login", { method: "POST", body: JSON.stringify({ user: f.get("user"), pass: f.get("pass") }) }); start(); }
  catch (er) { renderLogin(er.message); }
});
document.addEventListener("input", (e) => { if (e.target.id === "q" || e.target.id === "mq") onSearch(e.target.value); });
document.addEventListener("change", (e) => { if (e.target.dataset.act === "sort") { S.sort = e.target.value; paint(); } });
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
    if (act === "sigtab") { S.sigTab = a.dataset.v; return paint(); }
    if (act === "mktab") { S.mkTab = a.dataset.v; paint(); return refresh(); }
    if (act === "search") return toggleMobileSearch(!S.searching);
    if (act === "logout") { try { await api("/api/logout", { method: "POST" }); } catch { /* ignore */ } S.me = null; return renderLogin(); }
  }
  const s = e.target.closest("[data-sym]");
  if (s) { if (s.dataset.fromsearch) endSearch(); return openSheet(s.dataset.sym); }
  if (S.results && !e.target.closest(".field")) { S.results = null; paintDrop(); }
});
window.addEventListener("hashchange", route);
document.addEventListener("visibilitychange", () => { if (!document.hidden && S.me) refresh(); });
setInterval(() => { if (S.me && !document.hidden && marketOpen()) refresh(); }, 30000);
boot();

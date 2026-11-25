// Arka Trades · the Portfolio page. Every number here comes from pf.js, which is a port of the Streamlit app's portfolio maths.
import { S, api, paint, esc, num, sgn, pc, cls, rs, nice, todayStr, field, toast } from "./app.js";
import * as M from "./pf.js";
import { lineChart, resetCharts } from "./charts.js";
import { parseScreener } from "./screener.js";
import { piotroski } from "./rsengine.js";
import { stockNews } from "./news.js";
import { loadFlow } from "./flow.js";

export const VIEWS = ["Dashboard", "Swing desk", "Holdings", "Analysis", "Performance", "Risk", "Rebalance", "Income", "Tax", "Transactions", "News"];
const NDAYS = { "24 hours": 1, "3 days": 3, "7 days": 7 };
const QUAD_C = { Leading: "#30D158", Weakening: "#FFD60A", Lagging: "#FF453A", Improving: "#5AC8FA" };
const BENCH = { "NIFTY 50": "^NSEI", "NIFTY 500": "^CRSLDX", "Midcap 100": "NIFTY_MIDCAP_100.NS" };
const PAL = ["#FF9F0A", "#5AC8FA", "#BF5AF2", "#30D158", "#FF6482", "#5E8CFF", "#FFD60A", "#64D2FF", "#AC8E68", "#FF8A5B", "#7BD88F", "#C7A6FF"];
const GREY = "#55555C", UP = "var(--up)", DN = "var(--dn)", AMB = "var(--amber)", CY = "#5AC8FA", PU = "#BF5AF2";
export const pfDefaults = () => ({ lots: [], sells: [], plan: {}, can_sell: true, can_plan: true, can_book: true, loaded: false, err: "", view: "Dashboard", book: M.ALL, bench: "NIFTY 50", sort: "value", secF: "", find: "", funda: {}, fver: 0, fDone: 0, fTodo: 0, news: {}, nver: 0, nDays: "24 hours", nKey: "", nAt: 0, nState: "idle",
  snaps: [], settings: {}, can_snap: true, can_set: true, snapDone: {}, events: null, evState: "idle",
  hist: {}, benchData: {}, sectors: null, histState: "idle", ver: 0, hver: 0, ps: { cap: "", risk: "1", entry: "", stop: "" }, st: { mkt: "-10", sec: "", shock: "-15" }, reb: { maxStock: "15", maxSector: "30", cash: "0", redeploy: true } });

// ── formatting ──
const sh = (v, signed) => {                               // short rupees: ₹1.25 L, ₹2.10 Cr
  if (v == null || isNaN(v)) return "—";
  const a = Math.abs(v), t = a >= 1e7 ? num(a / 1e7, 2) + " Cr" : a >= 1e5 ? num(a / 1e5, 2) + " L" : num(a, 0);
  return (v < 0 ? "−" : signed && v > 0 ? "+" : "") + "₹" + t;
};
const shs = (v) => sh(v, true);
const q4 = (v) => (v == null ? "—" : num(v, Math.abs(v % 1) > 1e-9 ? 2 : 0));
const tone = (v) => (v == null || isNaN(v) || v === 0 ? "" : v > 0 ? "up" : "dn");
const sym = (s) => `<button class="lnk" data-sym="${esc(s)}">${esc(s)}</button>`;
const sub = (t) => `<small>${t}</small>`;
const kpi = (l, v, s = "", c = "") => `<div><span>${l}</span><b class="${c}">${v}</b><small>${s}</small></div>`;
const kpis = (cards, n = 4) => `<div class="card kp kp${n}">${cards.join("")}</div>`;
const note = (t) => `<div class="card"><div class="note">${t}</div></div>`;
const sec = (t, s = "") => `<h2>${t}${s ? ` <span>${s}</span>` : ""}</h2>`;
function tbl(head, rows, right = [], cls2 = "") {
  if (!rows.length) return "";
  const r = new Set(right);
  return `<div class="card tw ${cls2}"><table class="tb"><thead><tr>${head.map((h, i) => `<th${r.has(i) ? ' class="r"' : ""}>${h}</th>`).join("")}</tr></thead><tbody>${
    rows.map((row) => `<tr>${row.map((c, i) => `<td${r.has(i) ? ' class="r"' : ""}>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}
const col = (v) => `<span class="${cls(v)}">`;

// ── charts ──
function gauge(score) {
  const L = 267, c = score >= 75 ? "#2FD067" : score >= 50 ? "#FF9F0A" : "#FF5247", lab = score >= 75 ? "Well diversified" : score >= 50 ? "Moderate" : "Concentrated";
  return `<div class="gauge"><svg viewBox="0 0 200 112"><path d="M15 100A85 85 0 0 1 185 100" fill="none" stroke="rgba(255,255,255,.08)" stroke-width="14" stroke-linecap="round"/>
    <path d="M15 100A85 85 0 0 1 185 100" fill="none" stroke="${c}" stroke-width="14" stroke-linecap="round" stroke-dasharray="${((L * score) / 100).toFixed(1)} ${L}"/></svg><b>${score}</b><span style="color:${c}">${lab}</span></div>`;
}
function donut(items, centre) {                           // items: [{n, w, c}]
  const C = 2 * Math.PI * 40; let acc = 0;
  const arcs = items.map((x) => { const len = (C * x.w) / 100, a = `<circle cx="50" cy="50" r="40" fill="none" stroke="${x.c}" stroke-width="13" stroke-dasharray="${len.toFixed(2)} ${(C - len).toFixed(2)}" stroke-dashoffset="${(-acc).toFixed(2)}"/>`; acc += len; return a; }).join("");
  return `<div class="donut"><div class="dn-c"><svg viewBox="0 0 100 100" style="transform:rotate(-90deg)">${arcs}</svg><b>${centre}</b></div><div class="lg">${
    items.slice(0, 8).map((x) => `<div><i style="background:${x.c}"></i><span>${esc(x.n)}</span><b>${num(x.w, 1)}%</b></div>`).join("")}${items.length > 8 ? `<div><i></i><span>${items.length - 8} more</span><b></b></div>` : ""}</div></div>`;
}
function hbars(items, fmt = (v) => num(v, 1) + "%") {       // items: [{l, v, c}]; negative values grow to the left of a centre line
  if (!items.length) return '<div class="note">Nothing to show yet.</div>';
  const mx = Math.max(1e-9, ...items.map((x) => Math.abs(x.v))), neg = items.some((x) => x.v < 0);
  return `<div class="hb">${items.map((x) => { const w = (Math.abs(x.v) / mx) * (neg ? 50 : 100);
    return `<div><span>${x.l}</span><span class="t"><i style="${neg ? (x.v >= 0 ? "left:50%" : "right:50%") : "left:0"};width:${w.toFixed(1)}%;background:${x.c || AMB}"></i></span><b class="${neg ? tone(x.v) : ""}">${fmt(x.v)}</b></div>`; }).join("")}</div>`;
}
function monthHeat(m) {                                     // {"2026-11": pct}
  const years = [...new Set(Object.keys(m).map((k) => k.slice(0, 4)))].sort().reverse(), MN = "JFMAMJJASOND".split("");
  const cell = (v) => (v == null ? "<td></td>" : `<td style="background:rgba(${v >= 0 ? "47,208,103" : "255,82,71"},${Math.min(0.55, 0.08 + Math.abs(v) / 22).toFixed(2)})">${sgn(v, 1)}</td>`);
  return `<div class="card tw"><table class="tb heat"><thead><tr><th></th>${MN.map((x) => `<th>${x}</th>`).join("")}</tr></thead><tbody>${
    years.map((y) => `<tr><th>${y}</th>${MN.map((_, i) => cell(m[y + "-" + String(i + 1).padStart(2, "0")])).join("")}</tr>`).join("")}</tbody></table></div>`;
}
function corrTable(c) {
  const bg = (v) => (v == null ? "" : `background:rgba(${v >= 0 ? "255,159,10" : "90,200,250"},${Math.min(0.6, Math.abs(v) * 0.6).toFixed(2)})`);
  return `<div class="card tw"><table class="tb heat"><thead><tr><th></th>${c.syms.map((s) => `<th>${esc(s.slice(0, 6))}</th>`).join("")}</tr></thead><tbody>${
    c.syms.map((s, i) => `<tr><th>${esc(s)}</th>${c.m[i].map((v) => `<td style="${bg(v)}">${v == null ? "" : (Math.abs(v) < 0.005 ? 0 : v).toFixed(2)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
}
const flagsHtml = (fl, limit) => `<div class="flags">${(limit ? fl.slice(0, limit) : fl).map((f) => `<div class="fl ${f.level}"><b>${esc(f.title)}</b><span>${esc(f.detail)}</span></div>`).join("")}</div>`;

// ── data ──
export const books = () => [...new Set([M.MAIN, ...S.pf.lots.map((l) => l.book || M.MAIN), ...S.pf.sells.map((x) => x.book || M.MAIN), ...(S.pf.extraBooks || [])])];
export async function loadHistory(force) {                // 5 years of daily candles per stock, with splits and dividends; and the index
  const P = S.pf, syms = [...new Set([...P.lots, ...P.sells].map((x) => x.sym))], todo = syms.filter((s) => force || !P.hist[s] || !P.hist[s].ok), jobs = [];
  if (!P.sectors) jobs.push(fetch("/sectors.json").then((r) => (r.ok ? r.json() : {})).then((j) => { P.sectors = j; }).catch(() => { P.sectors = {}; }));
  for (let i = 0; i < todo.length; i += 6)
    jobs.push(api("/api/history?range=5y&s=" + encodeURIComponent(todo.slice(i, i + 6).join(","))).then((d) => { for (const h of d.history) P.hist[h.sym] = h.ok ? { ...h, tech: M.tech(h) } : { ok: false }; })
      .catch(() => { todo.slice(i, i + 6).forEach((s) => { if (!P.hist[s]) P.hist[s] = { ok: false }; }); }));
  const bs = [...new Set(["^NSEI", BENCH[P.bench]])].filter((s) => force || !P.benchData[s]);
  if (bs.length && syms.length) jobs.push(api("/api/history?range=10y&s=" + encodeURIComponent(bs.join(","))).then((d) => { for (const h of d.history) if (h.ok) P.benchData[h.sym] = { d: h.d, c: h.c }; }).catch(() => {}));
  if (force) loadExtras(true);
  if (!jobs.length) return;
  P.histState = "loading"; paint();
  await Promise.all(jobs);
  P.histState = "ready"; P.hver++; paint();
}
const numF = (x) => {                                       // portfolio_page._f: "1,234.5", "₹ 12 Cr", "3.2%" → number
  if (x == null) return null; if (typeof x === "number") return isFinite(x) ? x : null;
  const t = String(x).replace(/[₹,%\s]|Cr\.?/g, ""); if (!t || !/^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i.test(t)) return null; return Number(t);
};
function fundaOf(B) {                                        // the fundamentals and quality score portfolio_page._fetch_info keeps per stock
  const m = B.summary || {}; let fs = null;
  try { const res = piotroski(B); if (res) { const av = res[0].filter((t) => t[1] != null); fs = av.length ? [av.filter((t) => t[1]).length, av.length] : null; } } catch { fs = null; }
  return { mcap: numF(m.market_cap), pe: numF(m.pe_ratio), roe: numF(m.roe), roce: numF(m.roce), dy: numF(m.dividend_yield), fscore: fs, name: B.name || "" };
}
const openSyms = () => [...new Set(S.pf.lots.map((l) => l.sym))].filter((s) => calc().E.alots.some((l) => l.sym === s && l.open > 1e-9));
async function pool(items, n, fn) { let i = 0; await Promise.all(Array.from({ length: Math.min(n, items.length) }, async () => { while (i < items.length) { const x = items[i++]; await fn(x); } })); }
export async function loadFunda(force) {                     // Screener page per holding: market cap, P/E, ROE, ROCE, dividend yield and the quality score
  const P = S.pf, now = Date.now(), todo = openSyms().filter((s) => force || !P.funda[s] || (P.funda[s].fail && now - P.funda[s].at > 120000));
  if (P._fRun || !todo.length) return;
  P._fRun = true; P.fDone = 0; P.fTodo = todo.length;
  await pool(todo, 4, async (s) => {
    try { const d = await api("/api/screener?s=" + encodeURIComponent(s)), B = d.ok ? parseScreener(d.html, d.url, s) : null; P.funda[s] = B && B.resolved ? fundaOf(B) : { fail: true, at: Date.now() }; }
    catch { P.funda[s] = { fail: true, at: Date.now() }; }
    P.fDone++; P.fver++; if (P.fDone % 4 === 0) paint();
  });
  P._fRun = false; P.fver++; paint();
}
export async function loadPfNews(force) {                    // portfolio_page.holding_news: each holding's headlines over the chosen window, kept 10 minutes
  const P = S.pf, syms = openSyms(), days = NDAYS[P.nDays] || 1, key = syms.join(",") + "|" + days;
  if (P._nRun || !syms.length || (!force && P.nKey === key && Date.now() - P.nAt < 600000)) return;
  P._nRun = true; P.nState = "loading"; if (P.nKey.split("|")[1] !== String(days)) P.news = {};
  const names = (S.nw && S.nw.names) || {}, out = {};
  await pool(syms, 4, async (s) => { const l = P.lots.find((x) => x.sym === s && x.name), nm = (l && l.name) || ((P.funda[s] || {}).name) || names[s] || null;
    try { out[s] = await stockNews(s, days, nm); } catch { out[s] = []; } });
  P.news = out; P.nKey = key; P.nAt = Date.now(); P.nver++; P._nRun = false; P.nState = "ready"; paint();
}
export function loadExtras(force) { loadFunda(force); loadPfNews(force); if (!S.mf.d) loadFlow("main"); }
const normSec = (s) => String(s || "").toLowerCase().replace(/[^a-z]/g, "");
function stageMap() {                                        // {sector: quadrant} from Money flow's nightly file (portfolio_page._rotation_map)
  const d = S.mf && S.mf.d; if (!d || !d.sectors) return {};
  return Object.fromEntries(d.sectors.filter((r) => r.quadrant).map((r) => [normSec(r.sector), r.quadrant]));
}
export const bookKey = () => (S.pf.book === M.ALL ? "ALL" : S.pf.book);
function snapshot(A) {                                       // portfolio_page._snapshot: one row a day per book, saved when the page is open
  const P = S.pf, key = bookKey(), flag = A.today + "|" + key, T = A.T;
  if (!P.can_snap || P.snapDone[flag] || A.waiting || !A.histReady || !(T.val > 0)) return;
  P.snapDone[flag] = true;
  const row = { d: A.today, book: key, inv: T.inv, val: T.val, real: A.realisedTotal, div: A.divTotal };
  api("/api/portfolio/snapshot", { method: "POST", body: JSON.stringify({ book: key, inv: row.inv, val: row.val, real: row.real, div: row.div }) })
    .then((r) => { if (r && r.missing) P.can_snap = false; }).catch(() => {});
  P.snaps = P.snaps.filter((x) => !(x.d === row.d && x.book === key)).concat([row]);
}
let memo = { key: "", A: null };
export function calc() {                                    // everything the page shows, recomputed only when the inputs change
  const P = S.pf, key = [P.ver, P.hver, S.qv, P.book, P.bench, P.fver, P.nver, S.mf && S.mf.d ? 1 : 0, JSON.stringify(P.settings && P.settings.tax || null)].join("|");
  if (memo.key === key && memo.A) return memo.A;
  const today = todayStr(), splits = {}, divs = {}, closes = {}, techs = {};
  for (const [s, h] of Object.entries(P.hist)) if (h.ok) { splits[s] = h.splits; divs[s] = h.divs; closes[s] = { d: h.d, c: h.c }; techs[s] = h.tech; }
  const E = M.engine(P.lots, P.sells, splits), inb = (x) => P.book === M.ALL || x.book === P.book;
  const alots = E.alots.filter(inb), realised = E.realised.filter(inb), adjSells = E.adjSells.filter(inb), quotes = {};
  for (const l of alots) { const q = S.q[l.sym]; if (q && q.ok) quotes[l.sym] = { price: q.price, prev: q.prev, name: q.name }; }
  const holds = M.aggregate(alots, quotes, P.plan, P.sectors || {}, techs, P.book), rmap = stageMap();
  for (const h of holds) { const f = P.funda[h.sym] || {}, arts = P.news[h.sym] || [];
    Object.assign(h, { mcap: f.mcap ?? null, pe: f.pe ?? null, roe: f.roe ?? null, roce: f.roce ?? null, dy: f.dy ?? null, fscore: f.fscore || null, arts, newsN: arts.length, tone: M.newsTone(arts), stage: rmap[normSec(h.sector)] || null });
    if (!h.name && f.name) h.name = f.name; }
  const T = M.totals(holds, alots), sectors = M.groupBy(holds, "sector"), conc = M.concentration(holds), rates = { ...M.TAX_DEFAULTS, ...((P.settings || {}).tax || {}) };
  const nifty = P.benchData["^NSEI"] || null, bench = P.benchData[BENCH[P.bench]] || nifty, benchName = P.benchData[BENCH[P.bench]] ? P.bench : "NIFTY 50";
  const risk = M.riskMetrics(holds, closes, nifty), dv = M.dividends(alots, divs, today), tax = M.taxSummary(realised, rates), x = M.portfolioXirr(alots, adjSells, holds, today);
  let tl = null; try { tl = M.timeline(alots, adjSells, closes, bench); } catch { tl = null; }
  const scol = {}; sectors.forEach((s, i) => { scol[s.name] = s.name === "Unclassified" ? GREY : PAL[i % PAL.length]; });
  const A = { today, E, alots, realised, adjSells, holds, T, sectors, scol, conc, risk, divs: dv, divTotal: dv.reduce((a, d) => a + d.amount, 0), tax, xirr: x.xirr, xirrDays: x.days, tl, bench, benchName, nifty, quotes,
    realisedTotal: realised.reduce((a, r) => a + r.gain, 0), realisedN: new Set(realised.map((r) => r.sellId)).size, score: M.diversificationScore(conc, sectors, risk), journal: M.journal(realised),
    waiting: holds.some((h) => !S.q[h.sym]), histReady: P.histState === "ready", rates, mcap: M.mcapSplit(holds), fund: M.weightedFundamentals(holds) };
  A.flags = M.flags(holds, T, conc, sectors, risk, E.problems, { mcap: A.mcap, daysLabel: P.nDays });
  A.cur = tax[M.fy(today)] || null; A.headroom = A.cur ? A.cur.exemptLeft : rates.exempt;
  memo = { key, A };
  return A;
}
export function sellProblem(x) {                            // "" when the sell fits what is held in that book on that date (split-aware)
  const P = S.pf, splits = {}; for (const [s, h] of Object.entries(P.hist)) if (h.ok) splits[s] = h.splits;
  const before = M.engine(P.lots, P.sells, splits).problems.length, after = M.engine(P.lots, [...P.sells, { id: "new", ...x }], splits).problems;
  return after.length > before ? after[after.length - 1].replace(/ Check the sell.*$/, "") : "";
}

// ── views ──
const waitHist = (A) => (A.histReady ? "" : '<div class="note">Loading five years of price history for your stocks. Charts, XIRR, risk and dividends fill in when it arrives.</div>');
function growth(A, h = 230) {
  if (!A.tl) return note(A.histReady ? "The growth chart needs buys with a date, and price history." : "Loading price history…");
  const tl = A.tl, n = tl.d.length - 1, s = [{ n: "Your holdings", c: "#FF9F0A", v: tl.value, fill: true, w: 2 }, { n: "Money put in", c: "#8E8E93", v: tl.net, dash: true }];
  if (tl.bench) s.push({ n: A.benchName + " with the same cash", c: CY, v: tl.bench });
  const diff = tl.bench ? tl.value[n] - tl.bench[n] : null;
  return `<div class="card pad">${lineChart(tl.d, s, { h, fmt: (v) => sh(v) })}${diff == null ? "" : `<div class="mini">Against ${esc(A.benchName)} bought and sold on the same days: <b class="${cls(diff)}">${shs(diff)}</b></div>`}</div>`;
}
function dashboard(A) {
  const T = A.T, x = A.xirr, holds = A.holds;
  const k = [kpi("Invested (open)", sh(T.inv), `${T.n} holdings · ${T.nLots} buys`), kpi("Current value", sh(T.val), "last prices"),
    kpi("Unrealised P&amp;L", shs(T.pnl), pc(T.pnlPct), tone(T.pnl)), kpi("Day P&amp;L", shs(T.day), pc(T.dayPct), tone(T.day)),
    kpi("Realised P&amp;L", shs(A.realisedTotal), `${A.realisedN} matched sells`, tone(A.realisedTotal)),
    kpi("XIRR", x != null ? pc(x * 100, 1) : "—", x != null ? `money-weighted · ${A.xirrDays} days` : A.histReady ? "needs 60+ days of history" : "loading history…", x != null ? tone(x) : ""),
    kpi("Dividends (est.)", sh(A.divTotal), "gross, since you bought", A.divTotal ? "up" : ""),
    kpi("Portfolio heat", T.heat != null ? num(T.heat, 1) + "%" : "—", T.heat != null ? "₹ at risk to stops ÷ value" : "set stop-losses to see it", (T.heat || 0) > 6 ? "am" : "")];
  const movers = (() => { const r = holds.filter((h) => h.chg != null).sort((a, b) => b.chg - a.chg); if (!r.length) return '<div class="note">No live price changes yet.</div>';
    const up = r.slice(0, 3), dn = r.slice().reverse().slice(0, 3).filter((h) => !up.includes(h));
    return [...up, ...dn].map((h) => `<button class="row two" data-sym="${esc(h.sym)}"><span class="a"><b>${esc(h.sym)}</b><small>${shs(h.dayPnl)} today</small></span><span class="z"><b class="${cls(h.chg)}">${pc(h.chg)}</b></span></button>`).join(""); })();
  return kpis(k) + (T.nValid < T.n && !A.waiting ? '<div class="note">Some holdings have no live price right now and are left out of the totals.</div>' : "") +
    `<div class="pf-2 wide"><div>${sec("Growth of your money", "value, money put in, and the index with identical buys and sells")}${growth(A)}</div>
      <div>${sec("Portfolio health", "diversification score")}<div class="card pad">${A.score != null ? gauge(A.score) : ""}${flagsHtml(A.flags, 4)}</div></div></div>
    <div class="pf-3"><div>${sec("Sector allocation")}<div class="card pad">${donut(A.sectors.filter((s) => s.weight > 0).map((s) => ({ n: s.name, w: s.weight, c: A.scol[s.name] })), sh(T.val))}</div></div>
      <div>${sec("Top positions", "% of portfolio")}<div class="card pad">${hbars(holds.filter((h) => h.weight).slice(0, 10).map((h) => ({ l: esc(h.sym), v: h.weight, c: A.scol[h.sector] })))}</div></div>
      <div>${sec("Today's movers")}<div class="card">${movers}</div></div></div>`;
}
function swing(A) {
  const holds = A.holds, tot = A.T.val || 0, rows = []; let nStop = 0, nBelow = 0, attention = 0;
  for (const h of holds) {
    const t = h.tech || {}, ltp = h.ltp, held = h.firstDate ? M.daysBetween(h.firstDate, A.today) : null, notes = [];
    if (h.slHit) notes.push(["Stop hit", "r"]); else if (h.sl == null) notes.push(["No stop set", "a"]); else if (ltp != null && t.atr && ltp - h.sl < t.atr) notes.push(["Stop within one day's range", "a"]);
    if (ltp != null && t.ema21 && ltp < t.ema21) { nBelow++; notes.push(["Below 21 EMA", "a"]); }
    if (ltp != null && t.sma50 && ltp < t.sma50) notes.push(["Below 50-day", "r"]);
    if (h.rMult != null && held != null && held >= 15 && h.rMult < 0.5) notes.push([`Stalled: ${held} days, under 0.5R`, ""]);
    if (h.target != null && ltp != null && ltp >= h.target) notes.push(["Target reached", "g"]);
    if (h.sl != null) nStop++;
    if (notes.length && notes[0][1] && notes[0][1] !== "g") attention++;
    let trend = "—";
    if (ltp != null && t.ema21) { const ok = [t.ema10, t.ema21, t.sma50].filter(Boolean).map((v) => ltp >= v);
      trend = (ok.every(Boolean) ? '<span class="up">Above all</span>' : !ok.some(Boolean) ? '<span class="dn">Below all</span>' : '<span class="am">Mixed</span>') + sub("10 / 21 / 50: " + ok.map((v) => (v ? "✓" : "✕")).join(" ")); }
    const lv = (v) => (v == null ? "—" : rs(v, 2) + sub(ltp ? pc((v / ltp - 1) * 100, 1) : ""));
    rows.push([sym(h.sym) + sub(held != null ? held + " days" : "no buy date"), `${col(h.pnlPct)}${pc(h.pnlPct, 1)}</span>${sub(shs(h.pnl))}`,
      h.rMult != null ? `${col(h.rMult)}${sgn(h.rMult, 2)}R</span>` : h.sl != null && h.avg <= h.sl ? '<span class="up">Profit locked</span>' + sub("stop above cost") : '<span class="mu">needs a stop</span>',
      h.sl != null ? lv(h.sl) : '<span class="mu">not set</span>', trend, lv(t.ema21), lv(t.lo10), lv(M.chandelier(h.tech, h.firstDate)), t.atr && ltp ? num((t.atr / ltp) * 100, 1) + "%" + sub(rs(t.atr, 2)) : "—",
      t.rsi != null ? num(t.rsi, 0) : "—", notes.map(([n, c]) => `<span class="chip ${c}">${esc(n)}</span>`).join(" ") || '<span class="up">In order</span>']);
  }
  const riskSum = holds.filter((h) => h.sl != null).reduce((a, h) => a + (h.risk || 0), 0), J = A.journal;
  let out = kpis([kpi("Open risk to stops", nStop ? sh(riskSum) : "—", tot && nStop ? num((riskSum / tot) * 100, 1) + "% of value" : "set stops to see it", tot && riskSum / tot > 0.06 ? "dn" : ""),
    kpi("Positions with a stop", `${nStop} of ${holds.length}`, "every swing trade should have one", nStop === holds.length ? "" : "am"),
    kpi("Need attention", String(attention), "stop hit, no stop, or trend broken", attention ? "dn" : "up"), kpi("Below the 21 EMA", `${nBelow} of ${holds.length}`, "last price against the 21-day EMA", nBelow ? "am" : "")]);
  out += !holds.length ? note("No open positions. The position size calculator and your trade journal are below.") : sec("Open trades", "trend, risk and the levels traders trail a stop with") + waitHist(A) +
    tbl(["Stock", "P&amp;L", "Open R", "Your stop", "Trend", "21 EMA", "10-day low", "3 × ATR trail", "ATR", "RSI", "Check"], rows, [1, 2, 3, 5, 6, 7, 8, 9], "stick") +
    '<div class="note">Open R is the profit in units of the risk you took (buy price minus stop). The percentage under each level is its distance from the last price. The 3 × ATR trail is the highest high since you bought minus three times the 14-day ATR. Reference levels from daily candles, not instructions. Set your own stop by tapping a stock.</div>';
  const P = S.pf.ps, inp = (l, k, ph) => `<label class="f"><span>${l}</span><input data-pf="ps.${k}" value="${esc(P[k])}" inputmode="decimal" placeholder="${ph}" autocomplete="off"></label>`;
  out += sec("Position size calculator", "how many shares to buy so a stop-out costs exactly the risk you choose") +
    `<div class="card pad"><div class="g4">${inp("Trading capital (₹)", "cap", String(Math.round(tot / 1000) * 1000 || 500000))}${inp("Risk per trade (%)", "risk", "1")}${inp("Entry price (₹)", "entry", "0.00")}${inp("Stop-loss (₹)", "stop", "0.00")}</div><div id="pf-ps">${psOut(A)}</div></div>`;
  out += sec("Trade journal", "from the sells you recorded; each matched buy counts as one trade");
  if (!J) return out + note("No closed trades yet. Record sells and this fills in: win rate, average win and loss, expectancy and how long you hold winners against losers.");
  out += kpis([kpi("Closed trades", String(J.n), num(J.win, 0) + "% won"), kpi("Expectancy", pc(J.exp, 2), "average result per trade", tone(J.exp)),
    kpi("Average win / loss", `<span class="up">${pc(J.avgWin, 1)}</span> / <span class="dn">${pc(J.avgLoss, 1)}</span>`, J.payoff ? "payoff " + num(J.payoff, 2) : "no losing trades yet"),
    kpi("Profit factor", J.pf == null ? "∞" : num(J.pf, 2), "money won ÷ money lost"), kpi("Holding time", J.daysWin != null && J.daysLoss != null ? `${num(J.daysWin, 0)} / ${num(J.daysLoss, 0)} days` : "—", "winners / losers"),
    kpi("Net realised", shs(J.net), "longest losing run " + J.streak, tone(J.net))], 3);
  const tips = [];
  if (J.n < 20) tips.push(`Only ${J.n} closed trades: these numbers will move a lot until you have 30 or more.`);
  if (J.payoff && J.win && J.exp < 0) tips.push(`With a payoff of ${num(J.payoff, 2)} you need to win more than ${num(100 / (1 + J.payoff), 0)}% of trades to make money; you win ${num(J.win, 0)}%.`);
  if (J.daysWin != null && J.daysLoss != null && J.daysLoss > J.daysWin * 1.3) tips.push(`You hold losers longer (${num(J.daysLoss, 0)} days) than winners (${num(J.daysWin, 0)} days). Cutting losers sooner usually raises expectancy.`);
  if (tips.length) out += note(tips.map(esc).join("<br>"));
  const mk = Object.keys(J.months).sort().reverse().slice(0, 12);
  return out + tbl(["Month", "Trades closed", "Won", "Realised"], mk.map((k2) => { const v = J.months[k2]; return [new Date(k2 + "-01T00:00:00").toLocaleDateString("en-IN", { month: "short", year: "numeric" }), v[0], num((v[1] / v[0]) * 100, 0) + "%", `${col(v[2])}${shs(v[2])}</span>`]; }), [1, 2, 3]) +
    '<div class="note">Percentages are on the buy cost of each matched buy, before brokerage and taxes.</div>';
}
function psOut(A) {
  const P = S.pf.ps, tot = A.T.val || 0, cap = +P.cap || Math.round(tot / 1000) * 1000 || 500000, rp = +P.risk || 1, entry = +P.entry, stop = +P.stop;
  if (!(entry > 0) || !(stop > 0)) return '<div class="note">Enter an entry price and a stop-loss to see the size.</div>';
  if (stop >= entry) return '<div class="note">The stop-loss must be below the entry price for a long trade.</div>';
  const z = M.positionSize(cap, rp, entry, stop); if (!z) return "";
  return `<div class="kp kp4 in">${kpi("Shares to buy", num(z.qty, 0), z.capped ? "limited by capital" : "risk " + rs(z.per, 2) + " per share", "am")}${kpi("Position value", sh(z.value), num((z.value / cap) * 100, 0) + "% of capital")}${
    kpi("Loss if stopped", sh(z.loss), num((z.loss / cap) * 100, 2) + "% of capital", "dn")}${kpi("Stop distance", num((z.per / entry) * 100, 1) + "%", "below entry")}</div>
    <div class="lv">${[1, 2, 3, 5].map((k) => `<div><span>${k}R</span><b>${rs(entry + k * z.per, 2)}</b><em>${pc(((k * z.per) / entry) * 100, 1)} · ${sh(z.qty * k * z.per)}</em></div>`).join("")}</div>${
    z.qty === 0 ? '<div class="note">The risk amount is smaller than the risk on a single share. Raise the risk, or use a tighter stop.</div>' : ""}`;
}
const SORTS = { value: ["Value, high to low", (h) => -(h.value || 0)], pnlD: ["Profit ₹, high to low", (h) => -(h.pnl || 0)], pnlA: ["Profit ₹, low to high", (h) => h.pnl || 0], pctD: ["Profit %, high to low", (h) => -(h.pnlPct || 0)],
  pctA: ["Profit %, low to high", (h) => h.pnlPct || 0], dayD: ["Today %, high to low", (h) => -(h.chg || 0)], dayA: ["Today %, low to high", (h) => h.chg || 0], risk: ["Risk to stop, high to low", (h) => -(h.risk || 0)], az: ["A to Z", null] };
const stagePill = (st) => (st ? `<span class="chip" style="color:${QUAD_C[st] || "inherit"}">${esc(st)}</span>` : '<span class="mu">—</span>');
const toneHtml = (h) => (h.tone == null ? '<span class="mu">—</span>' : `<span class="${h.tone > 0.3 ? "up" : h.tone < -0.3 ? "dn" : ""}">${h.newsN} · ${h.tone >= 0 ? "+" : "−"}${Math.abs(h.tone).toFixed(1)}</span>`);
const fsHtml = (fs) => (fs ? `<span class="${fs[0] / fs[1] >= 0.75 ? "up" : fs[0] / fs[1] >= 0.45 ? "am" : "dn"}">${fs[0]}/${fs[1]}</span>` : '<span class="mu">—</span>');
const newsDot = (h) => (h.newsN ? ` <i class="pf-ndot" title="${esc(h.newsN + " headline" + (h.newsN > 1 ? "s" : "") + " · latest: " + (h.arts[0] || {}).title)}"></i>` : "");
function extrasState() {                                     // what is still loading for the extra columns
  const P = S.pf, bits = [];
  if (P._fRun) bits.push(`Reading fundamentals from Screener: ${P.fDone} of ${P.fTodo}`);
  if (P._nRun) bits.push("Checking headlines for your holdings");
  if (!S.mf.d && !S.mf.err) bits.push("Loading sector stages from Money flow");
  return bits.length ? `<div class="note">${bits.join(" · ")}…</div>` : "";
}
function holdList(A) {
  const P = S.pf, f = P.find.trim().toUpperCase(), rows = A.holds.filter((h) => (!P.secF || h.sector === P.secF) && (!f || h.sym.includes(f)));
  const key = SORTS[P.sort] || SORTS.value; rows.sort(key[1] ? (a, b) => key[1](a) - key[1](b) : (a, b) => (a.sym < b.sym ? -1 : 1));
  if (!rows.length) return note("No holdings match.");
  const chips = (h) => `${h.splitAdj ? ' <span class="chip">Adjusted</span>' : ""}${h.slHit ? ' <span class="chip r">Stop hit</span>' : h.target != null && h.ltp != null && h.ltp >= h.target ? ' <span class="chip g">At target</span>' : ""}`;
  const phone = rows.map((h) => `<button class="row two" data-sym="${esc(h.sym)}"><span class="a"><b>${esc(h.sym)}${newsDot(h)}${chips(h)}</b><small>${q4(h.qty)} shares · avg ${num(h.avg)} · ${h.weight != null ? num(h.weight, 1) + "%" : "—"}${h.sl != null ? " · stop " + num(h.sl) : ""}${h.stage ? " · " + esc(h.stage) : ""}${h.fscore ? " · quality " + h.fscore[0] + "/" + h.fscore[1] : ""}</small></span>
    <span class="z"><b>${h.value == null ? '<span class="mu">—</span>' : rs(h.value)}</b><small class="${cls(h.pnl)}">${h.pnl == null ? "no price" : shs(h.pnl) + " (" + pc(h.pnlPct, 1) + ")"}</small></span></button>`).join("");
  const desk = tbl(["Stock", "Qty", "Avg cost", "Last · today", "Invested", "Value", "P&amp;L", "Weight", "Stop-loss · target", "Stage · news · quality"], rows.map((h) => [
    sym(h.sym) + newsDot(h) + chips(h) + sub(`<i class="dot" style="background:${A.scol[h.sector] || GREY}"></i>${esc(h.sector)}${P.book === M.ALL && h.books.length && (h.books.length > 1 || h.books[0] !== M.MAIN) ? " · " + esc(h.books.join(", ")) : ""}`),
    q4(h.qty), num(h.avg), (h.ltp == null ? "—" : num(h.ltp)) + sub(`${col(h.chg)}${pc(h.chg)}</span>`), rs(h.invested), rs(h.value), `${col(h.pnl)}${rs(h.pnl)}</span>${sub(`${col(h.pnl)}${pc(h.pnlPct)}</span>`)}`,
    h.weight == null ? "—" : `<span class="wb"><i style="width:${Math.min(100, h.weight * 2).toFixed(0)}%;background:${A.scol[h.sector] || AMB}"></i></span>${num(h.weight, 1)}%`,
    (h.sl == null ? '<span class="mu">no stop</span>' : `<span class="${h.slHit ? "dn" : ""}">${num(h.sl)}</span>`) + (h.target == null ? "" : ` · ${num(h.target)}`) +
      sub(h.sl == null ? (h.target == null ? "" : h.reward ? "to target +" + sh(h.reward) : "target reached") : (h.slHit ? "stop hit" : "risk " + sh(h.risk)) + (h.rMult != null ? " · R " + num(h.rMult, 1) : "") + (h.target != null ? (h.reward ? " · +" + sh(h.reward) : " · target reached") : "")),
    `<span class="pf-ctx">${stagePill(h.stage)}</span>${sub(`news ${toneHtml(h)} · quality ${fsHtml(h.fscore)}`)}`]), [1, 2, 3, 4, 5, 6], "stick");
  return `<div class="only-m card">${phone}</div><div class="only-d">${desk}</div>`;
}
function holdingsView(A) {
  const P = S.pf, secs = [...new Set(A.holds.map((h) => h.sector))].sort();
  return `<div class="ctl"><select data-pf="sort" aria-label="Sort">${Object.entries(SORTS).map(([k, v]) => `<option value="${k}"${P.sort === k ? " selected" : ""}>${v[0]}</option>`).join("")}</select>
    <select data-pf="secF" aria-label="Sector"><option value="">All sectors</option>${secs.map((s) => `<option${P.secF === s ? " selected" : ""}>${esc(s)}</option>`).join("")}</select>
    <input data-pf="find" value="${esc(P.find)}" placeholder="Find a stock" autocomplete="off" spellcheck="false"><button class="btn sm" data-act="pfcsv" data-v="holdings">Export CSV</button></div>
    <div id="pf-hl">${holdList(A)}</div>${extrasState()}<div class="note">Tap a stock for its buys, stop-loss, target, selling and moving between books. Amber dot: fresh headlines in the last ${esc(S.pf.nDays)} (see News). Sector stage is the sector's quadrant in Money flow. Quality is the Piotroski-style score from Research → HC. “Adjusted” means split or bonus adjusted. P&amp;L excludes charges.</div>`;
}
const MCAP_C = { "Large cap": CY, "Mid cap": "#FF9F0A", "Small cap": PU, Unknown: GREY };
function valuation(A) {                                      // portfolio_page._view_analysis: Valuation & quality
  const F = A.fund, cov = (t) => num(t[1], 0) + "% of value covered", n1 = (v, d, suf) => (v == null ? "—" : num(v, d) + suf);
  const rows = A.holds.map((h) => [sym(h.sym) + sub(esc(h.sector)), n1(h.pe, 1, "x"), n1(h.roe, 1, "%"), n1(h.roce, 1, "%"), n1(h.dy, 2, "%"), h.mcap == null ? "—" : "₹" + num(h.mcap, 0) + " Cr", fsHtml(h.fscore), h.weight == null ? "—" : num(h.weight, 1) + "%"]);
  return sec("Valuation &amp; quality", "value-weighted across holdings with data") + kpis([kpi("Weighted P/E", n1(F.pe[0], 1, "x"), cov(F.pe)), kpi("Weighted ROE", n1(F.roe[0], 1, "%"), cov(F.roe)),
      kpi("Weighted ROCE", n1(F.roce[0], 1, "%"), cov(F.roce)), kpi("Dividend yield", n1(F.dy[0], 2, "%"), cov(F.dy)), kpi("Average quality", F.fscore == null ? "—" : num(F.fscore, 0) + "%", "Piotroski-style, value-weighted")], 5) +
    (S.pf._fRun ? `<div class="note">Reading fundamentals from Screener: ${S.pf.fDone} of ${S.pf.fTodo}…</div>` : "") +
    tbl(["Stock", "P/E", "ROE", "ROCE", "Div yield", "Market cap", "Quality", "Weight"], rows, [1, 2, 3, 4, 5, 6, 7], "stick") +
    '<div class="note">P/E is blended as a harmonic mean (the correct way to average P/E); loss-making companies are left out of it. Fundamentals come from Screener.in.</div>';
}
function analysis(A) {
  const c = A.conc, holds = A.holds;
  const secRows = A.sectors.map((s) => [`<i class="dot" style="background:${A.scol[s.name]}"></i>${esc(s.name)}`, s.n, sh(s.value), num(s.weight, 1) + "%", `${col(s.pnl)}${shs(s.pnl)}</span>${sub(pc(s.pnlPct, 1))}`, stagePill(s.stage)]);
  const pv = holds.filter((h) => h.pnl != null).sort((a, b) => b.pnl - a.pnl).slice(0, 15);
  const rng = holds.map((h) => { const q = S.q[h.sym] || {}, ok = h.ltp != null && q.hi52 && q.lo52 && q.hi52 > q.lo52, p = ok ? Math.max(0, Math.min(1, (h.ltp - q.lo52) / (q.hi52 - q.lo52))) : null, t = h.tech || {};
    return [sym(h.sym) + sub(esc(h.sector)), ok ? num(q.lo52) : "—", ok ? num(q.hi52) : "—", p == null ? "—" : `<span class="rng"><i style="left:${(p * 100).toFixed(0)}%"></i></span> ${num(p * 100, 0)}%`,
      t.sma200 && h.ltp ? `${col(h.ltp - t.sma200)}${pc((h.ltp / t.sma200 - 1) * 100, 1)}</span>` : "—", A.risk && A.risk.betas[h.sym] != null ? num(A.risk.betas[h.sym], 2) : "—", h.weight == null ? "—" : num(h.weight, 1) + "%"]; });
  return sec("Sector exposure", "value, weight, profit and Money flow stage by sector") + `<div class="pf-2"><div>${tbl(["Sector", "Stocks", "Value", "Weight", "P&amp;L", "Stage"], secRows, [1, 2, 3, 4])}</div>
      <div><div class="card pad">${hbars(A.sectors.filter((s) => s.weight > 0).map((s) => ({ l: esc(s.name), v: s.weight, c: A.scol[s.name] })))}</div></div></div>` +
    sec("Composition") + (c ? kpis([kpi("Top 1", num(c.top1, 1) + "%", "largest position"), kpi("Top 3", num(c.top3, 1) + "%", "of portfolio"), kpi("Top 5", num(c.top5, 1) + "%", "of portfolio"), kpi("Effective N", num(c.effN, 1), `behaves like · of ${c.n}`)]) : "") +
    `<div class="pf-2"><div>${sec("Market-cap mix", "approximate AMFI buckets")}<div class="card pad">${A.mcap.some((m) => m.name !== "Unknown" && m.value > 0) ? hbars(A.mcap.filter((m) => m.value > 0).map((m) => ({ l: m.name, v: m.weight, c: MCAP_C[m.name] }))) : '<div class="note">' + (S.pf._fRun ? "Reading market caps from Screener…" : "Market caps are not available right now.") + "</div>"}</div></div>
      <div>${sec("Unrealised P&amp;L by holding")}<div class="card pad">${hbars(pv.map((h) => ({ l: esc(h.sym), v: h.pnl, c: h.pnl >= 0 ? UP : DN })), (v) => shs(v))}</div></div></div>` + valuation(A) +
    sec("Price position", "where each stock sits in its 52-week range, against its 200-day average, and its beta to NIFTY 50") + tbl(["Stock", "52W low", "52W high", "Position in range", "vs 200-day", "Beta", "Weight"], rng, [1, 2, 4, 5, 6], "stick") +
    sec("Health check", "automatic review") + `<div class="card pad">${flagsHtml(A.flags)}</div>` +
    '<div class="note">The diversification score starts at 100 and loses points for: largest position above 10% (up to 30), largest sector above 25% (up to 30), fewer than 10 effective equal-weight stocks (up to 25) and average correlation above 0.5 (up to 15). Rules of thumb, not recommendations.</div>';
}
function performance(A) {
  if (!A.tl) return note(A.histReady ? "Performance needs buys with a date, and price history." : "Loading price history…");
  const tl = A.tl, t = M.twrStats(tl, A.bench), x = A.xirr, m = M.monthlyReturns(tl), months = Object.keys(m);
  const beat = t.benchMonthly ? months.filter((k) => m[k] - (t.benchMonthly[k] ?? 0) > 0).length : null;
  const con = M.contributions(A.holds, A.realised, A.divs), show = con.slice(0, 8).concat(con.slice(-6).filter((x2) => !con.slice(0, 8).includes(x2)));
  const bySec = {}; for (const [s, v] of con) { const k = (S.pf.sectors || {})[s] || "Unclassified"; bySec[k] = (bySec[k] || 0) + v; }
  return kpis([kpi("Time-weighted return", pc(t.twr, 1), "since " + nice(tl.d[0]), tone(t.twr)), kpi("Annualised (TWR)", t.ann != null ? pc(t.ann, 1) : "—", t.ann == null ? "needs 1+ year" : "per year", tone(t.ann)),
      kpi(esc(A.benchName) + " same period", t.bench != null ? pc(t.bench, 1) : "—", "price index"), kpi("Excess over the index", t.bench != null ? pc(t.twr - t.bench, 1) : "—", "time-weighted", t.bench != null ? tone(t.twr - t.bench) : ""),
      kpi("XIRR (money-weighted)", x != null ? pc(x * 100, 1) : "—", "your actual cash flows", x != null ? tone(x) : "")], 5) +
    `<div class="pf-2"><div>${sec("Growth of your money", "against the index bought and sold with the same cash")}${growth(A, 220)}</div>
      <div>${sec("Time-weighted index (start = 100)", "removes the effect of when you added money")}<div class="card pad">${lineChart(tl.d, [{ n: "You", c: "#FF9F0A", v: t.idx, w: 2 }].concat(t.bIdx ? [{ n: A.benchName, c: CY, v: t.bIdx }] : []), { h: 220, fmt: (v) => num(v, 1) })}</div></div></div>` +
    sec("Monthly returns", "time-weighted, %") + monthHeat(m) + (beat != null ? `<div class="note">Beat ${esc(A.benchName)} in ${beat} of ${months.length} months (partial first and current months included).</div>` : "") +
    sec("What drove your returns", "unrealised + realised + estimated dividends") + `<div class="pf-2"><div class="card pad">${hbars(show.map(([s, v]) => ({ l: esc(s), v, c: v >= 0 ? UP : DN })), (v) => shs(v))}</div>
      <div class="card pad">${hbars(Object.entries(bySec).sort((a, b) => b[1] - a[1]).map(([s, v]) => ({ l: esc(s), v, c: v >= 0 ? UP : DN })), (v) => shs(v))}</div></div>` + recorded();
}
function recorded() {                                         // portfolio_page._view_performance: Recorded history
  const P = S.pf, key = bookKey(), sn = P.snaps.filter((x) => x.book === key).sort((a, b) => (a.d < b.d ? -1 : 1));
  const head = sec("Recorded history", "a snapshot is saved each day you open this page");
  if (sn.length < 2) return head + note(`${sn.length} snapshot${sn.length === 1 ? "" : "s"} recorded so far. The curve appears from the second day you open the portfolio${P.can_snap ? "." : ". Saving needs the portfolio_snapshots table: open Portfolio in the classic app once and run the SQL it shows."}`);
  return head + `<div class="card pad">${lineChart(sn.map((x) => x.d), [{ n: "Value", c: "#FF9F0A", v: sn.map((x) => x.val), w: 2 }, { n: "Invested (open)", c: "#8E8E93", v: sn.map((x) => x.inv), dash: true }, { n: "Realised P&L", c: PU, v: sn.map((x) => x.real) }], { h: 220, fmt: (v) => sh(v) })}</div>`;
}
function riskView(A) {
  const R = A.risk, holds = A.holds, tot = A.T.val || 0;
  let out = sec("Risk", "today's weights over the last year of daily returns");
  if (!R) return out + note(A.histReady ? "Risk numbers need about 3 months of price history for your holdings." : "Loading price history…");
  out += kpis([kpi("Volatility", num(R.vol, 1) + "%", "annualised"), kpi("Max drawdown", num(R.mdd, 1) + "%", "peak to trough", "dn"), kpi("1-day VaR (95%)", sh(R.var95), "historical, worst 1-in-20 day"),
    kpi("Beta", R.beta == null ? "—" : num(R.beta, 2), "against NIFTY 50"), kpi("1Y basket return", pc(R.ret1y, 1), R.benchRet != null ? "NIFTY 50 " + pc(R.benchRet, 1) : "", tone(R.ret1y))], 5);
  out += sec("Stop-loss risk", "what you lose if every stop is hit");
  const withSl = holds.filter((h) => h.sl != null && h.value);
  if (withSl.length) {
    const rsum = withSl.reduce((a, h) => a + (h.risk || 0), 0), cover = tot ? (withSl.reduce((a, h) => a + h.value, 0) / tot) * 100 : 0, hit = holds.filter((h) => h.slHit).length;
    out += kpis([kpi("₹ at risk to stops", sh(rsum), tot ? num((rsum / tot) * 100, 1) + "% of portfolio" : "", "dn"), kpi("Value covered by stops", num(cover, 0) + "%", `${withSl.length} of ${holds.length} holdings`), kpi("Stops breached", String(hit), "price at or below stop", hit ? "dn" : "")], 3) +
      tbl(["Stock", "Last", "Stop", "Distance", "₹ at risk", "Open R", "Target", "Reward : risk"], withSl.sort((a, b) => (b.risk || 0) - (a.risk || 0)).map((h) => [sym(h.sym), num(h.ltp), num(h.sl), pc((h.sl / h.ltp - 1) * 100, 1), `<span class="dn">${sh(h.risk)}</span>`,
        h.rMult == null ? "—" : num(h.rMult, 2), h.target ? num(h.target) : "—", h.target && h.ltp > h.sl ? num((h.target - h.ltp) / (h.ltp - h.sl), 2) : "—"]), [1, 2, 3, 4, 5, 6, 7], "stick");
  } else out += note("No stop-losses set. Tap a stock in Holdings and save a stop-loss.");
  const st = S.pf.st, secs = [...new Set(holds.map((h) => h.sector).filter((s) => s !== "Unclassified"))].sort();
  out += sec("Stress test", "estimated using each stock's beta to NIFTY 50 over the last year") + `<div class="card pad"><div class="g3"><label class="f"><span>NIFTY 50 move (%)</span><input type="range" min="-30" max="20" step="1" data-pf="st.mkt" value="${esc(st.mkt)}"></label>
      <label class="f"><span>Extra sector shock</span><select data-pf="st.sec"><option value="">None</option>${secs.map((s) => `<option${st.sec === s ? " selected" : ""}>${esc(s)}</option>`).join("")}</select></label>
      <label class="f"><span>Sector move (%)</span><input type="range" min="-40" max="20" step="1" data-pf="st.shock" value="${esc(st.shock)}"></label></div><div id="pf-st">${stOut(A)}</div></div>`;
  const w = M.worstMoves(A.nifty);
  if (w && w.day && w.month) { const est = (mv) => holds.filter((h) => h.value).reduce((a, h) => a + (h.value * (R.betas[h.sym] ?? 1) * mv) / 100, 0);
    out += sec("Historical replay", "the worst NIFTY 50 day and month of the last 5 years, applied to today's portfolio") + tbl(["Scenario", "When", "NIFTY 50", "Your estimated impact", "% of portfolio"],
      [["Worst day", nice(w.day.when), w.day.r * 100], ["Worst month", new Date(w.month.when + "-01T00:00:00").toLocaleDateString("en-IN", { month: "short", year: "numeric" }), w.month.r * 100]].map(([l, when, mv]) => { const e = est(mv);
        return [l, when, `<span class="dn">${pc(mv, 1)}</span>`, `${col(e)}${shs(e)}</span>`, pc(tot ? (e / tot) * 100 : 0, 1)]; }), [2, 3, 4]); }
  if (R.corr && R.corr.syms.length >= 2) out += sec("Correlation of top holdings", "closer to 1.00 means they move together") + corrTable(R.corr) +
    (R.topPair ? `<div class="note">Most correlated pair: <b>${esc(R.topPair[0])}</b> and <b>${esc(R.topPair[1])}</b> (${R.topPair[2].toFixed(2)}) · average ${R.avgCorr.toFixed(2)}</div>` : "");
  return out + '<div class="note">Beta-based scenarios are straight-line estimates; real crashes are often worse for small caps and better for defensives.</div>';
}
function stOut(A) {
  const st = S.pf.st, tot = A.T.val || 0, z = M.stress(A.holds, (A.risk || {}).betas || {}, +st.mkt, st.sec, +st.shock);
  return `<div class="mini">NIFTY 50 <b>${sgn(+st.mkt, 0)}%</b>${st.sec ? ` · ${esc(st.sec)} a further <b>${sgn(+st.shock, 0)}%</b>` : ""}</div>
    <div class="kp kp3 in">${kpi("Scenario impact", shs(z.total), pc(tot ? (z.total / tot) * 100 : 0, 1), tone(z.total))}${kpi("Portfolio after", sh(tot + z.total), "estimated value")}${kpi("Stocks without beta", String(z.noBeta), "assumed beta 1.0")}</div>` +
    tbl(["Stock", "Beta", "Estimated move", "Impact"], z.rows.slice(0, 15).map((r) => [sym(r.h.sym) + sub(esc(r.h.sector)), num(r.beta, 2), `${col(r.move)}${pc(r.move, 1)}</span>`, `${col(r.impact)}${shs(r.impact)}</span>`]), [1, 2, 3]);
}
function rebalance(A) {
  const r = S.pf.reb, inp = (l, k) => `<label class="f"><span>${l}</span><input data-pf="reb.${k}" value="${esc(r[k])}" inputmode="decimal" autocomplete="off"></label>`;
  return sec("Rebalance assistant", "your rules, turned into exact quantities to get back inside them") + `<div class="card pad"><div class="g4">${inp("Max per stock (%)", "maxStock")}${inp("Max per sector (%)", "maxSector")}${inp("New cash to invest (₹)", "cash")}
      <label class="f ck"><input type="checkbox" data-pf="reb.redeploy"${r.redeploy ? " checked" : ""}><span>Redeploy freed cash into holdings under the caps</span></label></div></div><div id="pf-rb">${rebOut(A)}</div>`;
}
function rebOut(A) {
  const r = S.pf.reb, rules = { maxStock: Math.min(100, Math.max(2, +r.maxStock || 15)), maxSector: Math.min(100, Math.max(5, +r.maxSector || 30)), cash: Math.max(0, +r.cash || 0), redeploy: !!r.redeploy }, p = M.rebalancePlan(A.holds, rules);
  if (!p.rows.length) return note(`Everything is already inside the rules (max ${num(rules.maxStock, 1)}% per stock, ${num(rules.maxSector, 0)}% per sector). Nothing to do.`);
  let stT = 0, ltT = 0; const rows = p.rows.map((x) => { let tax = "—";
    if (x.action === "SELL") { const f = M.fifoPreview(x.h, x.qty, x.ltp, A.today); stT += f.st; ltT += f.lt; tax = `${col(f.st + f.lt)}${shs(f.st + f.lt)}</span>${sub(`short ${shs(f.st)} · long ${shs(f.lt)}`)}`; }
    return [sym(x.sym) + sub(esc(x.sector)), `<span class="chip ${x.action === "SELL" ? "r" : "g"}">${x.action === "SELL" ? "Sell" : "Buy"}</span>`, q4(x.qty), num(x.ltp), sh(x.amount), num(x.nowW, 1) + "%", num(x.newW, 1) + "%", tax]; });
  const sells = p.rows.filter((x) => x.action === "SELL"), buys = p.rows.filter((x) => x.action === "BUY"), T = A.rates;
  const estTax = ((Math.max(0, stT) * T.stcg + Math.max(0, ltT - A.headroom) * T.ltcg) / 100) * (1 + T.cess / 100);
  return kpis([kpi("Sell", sh(sells.reduce((a, x) => a + x.amount, 0)), sells.length + " trades", "dn"), kpi("Buy", sh(buys.reduce((a, x) => a + x.amount, 0)), buys.length + " trades", "up"), kpi("Cash left over", sh(p.left), "after the plan"),
    kpi("Gains realised by trims", shs(stT + ltT), `short ${shs(stT)} · long ${shs(ltT)}`, tone(stT + ltT)), kpi("Estimated tax on trims", sh(estTax), "after this year's LTCG headroom", "am")], 5) +
    tbl(["Stock", "Action", "Qty", "Price", "Amount", "Weight now", "Weight after", "Gain realised (FIFO)"], rows, [2, 3, 4, 5, 6], "stick") +
    '<div class="note">Quantities are whole shares at the last price. Sells use your oldest buys first, matching how gains are taxed. A planning aid, not advice: consider brokerage, STT and whether the reason you own each stock still holds.</div>';
}
function income(A) {
  const divs = A.divs, byFy = {}, per = {}, cur = M.fy(A.today), yearAgo = new Date(Date.parse(A.today) - 365 * 864e5).toISOString().slice(0, 10);
  for (const d of divs) { byFy[d.fy] = (byFy[d.fy] || 0) + d.amount; per[d.sym] = (per[d.sym] || 0) + d.amount; }
  const ttm = divs.filter((d) => d.exDate >= yearAgo).reduce((a, d) => a + d.amount, 0), inv = A.T.inv || 0;
  let out = sec("Dividend income", "estimated from the dividend history and the dates you held each buy (gross, before TDS)") + waitHist(A) +
    kpis([kpi("Total received (est.)", sh(A.divTotal), divs.length + " payouts", A.divTotal ? "up" : ""), kpi("Last 12 months", sh(ttm), "gross"), kpi(`This year (${cur})`, sh(byFy[cur] || 0), "financial year"), kpi("Yield on cost (12M)", inv ? num((ttm / inv) * 100, 2) + "%" : "—", "on open invested amount")]);
  if (!divs.length) return out + (A.histReady ? note("No dividends found for the periods you held these stocks.") : "");
  const last = (s) => divs.filter((d) => d.sym === s).map((d) => d.exDate).sort().pop();
  return out + `<div class="pf-2"><div>${sec("By financial year")}<div class="card pad">${hbars(Object.keys(byFy).sort().map((k) => ({ l: k, v: byFy[k], c: UP })), (v) => sh(v))}</div></div>
    <div>${sec("By stock")}${tbl(["Stock", "Received (est.)", "Total yield on cost", "Last ex-date"], Object.entries(per).sort((a, b) => b[1] - a[1]).map(([s, amt]) => { const h = A.holds.find((x) => x.sym === s);
      return [sym(s), sh(amt), h && h.invested ? num((amt / h.invested) * 100, 1) + "%" : "sold", nice(last(s))]; }), [1, 2])}</div></div>` + events(A);
}
export async function loadEvents() {                          // results and dividend dates Yahoo publishes for each holding
  const P = S.pf, A = calc(); if (P.evState === "loading") return;
  P.evState = "loading"; paint();
  const out = [], to = new Date(Date.parse(A.today) + 120 * 864e5).toISOString().slice(0, 10);
  await pool(A.holds.map((h) => h.sym), 4, async (s) => { try { const d = await api("/api/yinfo?s=" + encodeURIComponent(s)), c = d.cal || {};
    for (const [v, lab] of [[c.earnings, "Results"], [c.exdiv, "Ex-dividend"], [c.paid, "Dividend paid"]]) for (const x of [].concat(v || [])) if (x && x >= A.today && x <= to) out.push([x, s, lab]); } catch { /* skip */ } });
  P.events = out.sort((a, b) => (a[0] + a[1] + a[2] < b[0] + b[1] + b[2] ? -1 : 1)); P.evState = "ready"; paint();
}
function events(A) {
  const P = S.pf, head = sec("Upcoming events", "results and ex-dividend dates published by Yahoo (often incomplete for NSE)");
  if (P.evState === "idle") return head + `<div class="card pad"><button class="btn sm" data-act="pfevents">Load event calendar</button></div>`;
  if (P.evState === "loading") return head + note("Checking calendars…");
  if (!P.events.length) return head + note("No upcoming results or dividend dates were published for your holdings in the next 120 days.");
  const day = (d) => new Date(d + "T00:00:00").toLocaleDateString("en-IN", { weekday: "short", day: "2-digit", month: "short", year: "numeric" });
  return head + tbl(["Date", "Stock", "Event", "In"], P.events.map(([d, s, l]) => [day(d), sym(s), l, Math.round((Date.parse(d) - Date.parse(A.today)) / 864e5) + " days"]), [3]);
}
function taxView(A) {
  const T = A.rates, cur = M.fy(A.today), c = A.cur || { stNet: 0, ltNet: 0, tax: 0, exemptLeft: T.exempt }, u = M.unrealisedByTerm(A.alots, A.quotes, A.today);
  let out = taxForm(T) + sec("This financial year · " + cur) + kpis([kpi("Realised P&amp;L (total)", shs(A.realisedTotal), `${A.realisedN} matched sells, all years`, tone(A.realisedTotal)), kpi("Net STCG this year", shs(c.stNet), "after set-off", tone(c.stNet)),
    kpi("Net LTCG this year", shs(c.ltNet), "after set-off", tone(c.ltNet)), kpi("Tax-free LTCG left", sh(c.exemptLeft), "of the annual exemption", "up"), kpi("Estimated tax this year", sh(c.tax), "including cess", "am")], 5);
  out += tbl(["Financial year", "Sale value", "Net STCG", "Net LTCG", "Taxable LTCG", "Est. tax", "Loss to carry forward"], Object.entries(A.tax).map(([f, v]) => [f, sh(v.proceeds), `${col(v.stNet)}${shs(v.stNet)}</span>`, `${col(v.ltNet)}${shs(v.ltNet)}</span>`, sh(v.ltTaxable), sh(v.tax), v.carryLoss ? sh(v.carryLoss) : "—"]), [1, 2, 3, 4, 5, 6]);
  out += sec("Unrealised gains by tax term", "what selling today would realise") + kpis([kpi("Unrealised short-term", shs(u.st), "held 12 months or less", tone(u.st)), kpi("Unrealised long-term", shs(u.lt), "held more than 12 months", tone(u.lt)),
    kpi("LTCG you could book tax-free", sh(Math.min(Math.max(u.lt, 0), c.exemptLeft)), "this year (" + cur + ")", "up")], 3);
  if (u.soon.length) out += note("<b>Turning long-term soon.</b> Waiting could cut the tax rate on these gains from the short-term rate to the long-term rate.") +
    tbl(["Stock", "Bought", "Long-term from", "Days left", "Open gain"], u.soon.map((x) => [sym(x.lot.sym), nice(x.lot.date), nice(x.on), x.days, shs(x.gain)]), [3, 4]);
  out += sec("Realised trades", "each sell matched to your oldest buys");
  if (!A.realised.length) return out + note("No sells recorded yet. Record them from a stock's sheet or under Transactions.") + taxNote(T);
  out += `<div class="ctl"><span class="sp"></span><button class="btn sm" data-act="pfcsv" data-v="realised">Download realised P&amp;L (CSV)</button></div>` +
    tbl(["Sold", "Stock", "Qty", "Bought", "Buy", "Sell", "Gain", "Held", "Term", "Year"], A.realised.slice().sort((a, b) => (a.sellDate < b.sellDate ? 1 : -1)).map((x) => [nice(x.sellDate), sym(x.sym) + (x.book !== M.MAIN ? sub(esc(x.book)) : ""), q4(x.qty), nice(x.buyDate), num(x.buyPrice), num(x.sellPrice),
      `${col(x.gain)}${rs(x.gain)}</span>`, x.days == null ? "—" : x.days + "d", `<span class="chip ${x.term === "LT" ? "" : "a"}">${x.term === "LT" ? "Long-term" : "Short-term"}</span>`, x.fy]), [2, 4, 5, 6, 7], "stick");
  return out + taxNote(T);
}
function taxForm(T) {                                        // portfolio_page._view_tax: Tax assumptions (editable), saved in portfolio_settings
  const P = S.pf, f = (l, k, step, max) => `<label class="f"><span>${l}</span><input name="${k}" type="number" min="0"${max ? ` max="${max}"` : ""} step="${step}" value="${esc(T[k])}" inputmode="decimal"></label>`;
  return `<details class="card pad pf-tax"${P.taxOpen ? " open" : ""}><summary>Tax assumptions <span class="mu">· editable</span></summary><form id="pftax" class="form"><div class="g4">${f("Short-term gains rate %", "stcg", 0.5, 50)}${f("Long-term gains rate %", "ltcg", 0.5, 50)}${f("Tax-free long-term gains ₹ a year", "exempt", 5000)}${f("Cess %", "cess", 0.5, 10)}</div>
    <div class="err"></div><div class="ctl"><button class="btn pri sm" type="submit"${P.can_set ? "" : " disabled"}>Save</button><button class="btn sm" type="button" data-act="pftaxreset">Use the defaults</button></div>
    <div class="mini">Defaults: listed equity with STT. Short-term 20%, long-term 12.5% above ₹1.25 lakh a year, long-term means held more than 12 months (rates from 23 Jul 2024, unchanged in Budget 2026), 4% cess.${P.can_set ? "" : " Saving needs the portfolio_settings table: open Portfolio in the classic app once and run the SQL it shows."}</div></form></details>`;
}
export async function saveTax(form, rates) {
  const P = S.pf, err = form.querySelector(".err"), b = form.querySelector("button[type=submit]");
  const r = rates || Object.fromEntries([...new FormData(form)].map(([k, v]) => [k, Number(v)]));
  if (Object.values(r).some((v) => !isFinite(v) || v < 0)) { if (err) err.textContent = "Every rate must be a number, zero or more."; return; }
  if (b) b.disabled = true;
  try { const d = await api("/api/portfolio/settings", { method: "POST", body: JSON.stringify({ tax: r }) }); P.settings = { ...(P.settings || {}), tax: d.tax }; P.taxOpen = true; toast("Tax assumptions saved"); paint(); }
  catch (e) { if (err) err.textContent = e.message; if (b) b.disabled = false; }
}
const taxNote = (T) => `<div class="note">Assumptions: listed equity with STT, short-term gains ${T.stcg}%, long-term gains ${T.ltcg}% above ₹${num(T.exempt, 0)} a year, long-term means held more than 12 months, ${T.cess}% cess. Surcharge, carried-forward losses and pre-2018 grandfathering are not modelled. An estimate to help planning, not tax advice: use your broker's capital-gains statement or AIS for filing.</div>`;
function transactions(A) {
  const P = S.pf, open = A.holds, bk = books();
  const led = P.lots.map((l) => ({ d: l.date, t: "BUY", ...l })).concat(P.sells.map((x) => ({ d: x.date, t: "SELL", ...x }))).filter((x) => P.book === M.ALL || (x.book || M.MAIN) === P.book).sort((a, b) => ((a.d || "") < (b.d || "") ? 1 : -1));
  const bookSel = (name, v) => (bk.length > 1 ? `<label class="f"><span>Book</span><select name="${name}">${bk.map((b) => `<option${b === v ? " selected" : ""}>${esc(b)}</option>`).join("")}</select></label>` : "");
  const sellForm = !open.length ? note("Nothing to sell yet.") : !P.can_sell ? note("Selling needs the extra portfolio tables. Open Portfolio in the classic app once and run the SQL it shows.") :
    `<form id="sell" class="card pad form"><label class="f"><span>Stock</span><select name="sym">${open.map((h) => `<option value="${esc(h.sym)}">${esc(h.sym)} · ${q4(h.qty)} held</option>`).join("")}</select></label>
      <div class="g2">${field("Shares", "qty", "", 'inputmode="decimal" required')}${field("Sell price (₹)", "price", "", 'inputmode="decimal" required')}</div>
      <div class="g2">${field("Sell date", "date", A.today, 'type="date" max="' + A.today + '"')}${bookSel("book", P.book === M.ALL ? M.MAIN : P.book)}</div><div class="err"></div><button class="btn red full" type="submit">Record sell</button></form>`;
  return `<div class="pf-2"><div>${sec("Add a buy", "the same stock at another price is another buy")}<div class="card pad"><button class="btn pri full" data-act="buyform">Add a buy</button><div class="mini">Pick the book in the form. A new book name creates the book.</div></div>
      ${sec("Books", "separate portfolios such as Long term, Trading, Family")}<div class="card pad">${bk.map((b) => `<span class="chip a">${esc(b)}</span>`).join(" ")} <button class="btn sm" data-act="newbook">New book</button></div></div>
      <div>${sec("Record a sell", "matched against your oldest buys in that book")}${sellForm}</div></div>` +
    (A.E.problems.length ? sec("Data checks") + `<div class="card pad">${flagsHtml(A.E.problems.map((p) => ({ level: "alert", title: "Sell larger than holding", detail: p })))}</div>` : "") +
    sec("Ledger", "every buy and sell, newest first") + (led.length ? tbl(["Date", "Type", "Stock", "Book", "Qty", "Price", "Amount", "Note", ""], led.map((x) => [nice(x.d), `<span class="chip ${x.t === "BUY" ? "g" : "r"}">${x.t === "BUY" ? "Buy" : "Sell"}</span>`, sym(x.sym), esc(x.book || M.MAIN),
      q4(x.qty), num(x.price), rs(x.qty * x.price), esc(x.note || ""), `<button class="del" data-act="${x.t === "BUY" ? "dellot" : "delsell"}" data-id="${x.id}">Delete</button>`]), [4, 5, 6], "stick") : note("No buys or sells yet.")) +
    `<div class="note">The ledger shows quantities and prices exactly as entered; the analysis uses split and bonus adjusted figures. ${P.can_sell && P.can_plan && P.can_book ? "All portfolio data is saved in Supabase, the same tables the classic app uses." :
      "Some extra portfolio tables are missing, so sells, stops or books cannot be saved yet. Open Portfolio in the classic app once and run the SQL it shows under Transactions → Storage."}</div>`;
}
const VIEW = { Dashboard: dashboard, "Swing desk": swing, Holdings: holdingsView, Analysis: analysis, Performance: performance, Risk: riskView, Rebalance: rebalance, Income: income, Tax: taxView, Transactions: transactions, News: newsView };
function newsView(A) {                                      // portfolio_page._view_news
  const P = S.pf, days = Object.keys(NDAYS), withNews = A.holds.filter((h) => h.newsN).sort((a, b) => (b.value || 0) - (a.value || 0));
  let out = `<div class="ctl"><select data-pf="nDays" aria-label="News window">${days.map((d) => `<option${P.nDays === d ? " selected" : ""}>${d}</option>`).join("")}</select>${P._nRun ? '<span class="mu">Checking headlines…</span>' : ""}<span class="sp"></span><button class="btn sm" data-act="pfnews">Refresh headlines</button></div>`;
  if (!withNews.length) return out + note(P._nRun || P.nState === "idle" ? "Checking headlines for your holdings…" : `No relevant headlines for your holdings in the last ${esc(P.nDays)}. An amber dot appears next to a stock in Holdings when news arrives.`);
  for (const h of withNews) {
    const t = h.tone == null ? "" : ` · tone <span class="${h.tone > 0.3 ? "up" : h.tone < -0.3 ? "dn" : ""}">${h.tone >= 0 ? "+" : "−"}${Math.abs(h.tone).toFixed(1)}</span>`;
    out += `<div class="pf-nh"><i class="pf-ndot"></i><button class="lnk" data-sym="${esc(h.sym)}">${esc(h.sym)}</button><span>${h.newsN} headline${h.newsN > 1 ? "s" : ""} · ${h.weight == null ? "—" : num(h.weight, 1) + "%"} of portfolio${t}</span></div><div class="card">${
      h.arts.slice(0, 6).map((a) => `<a class="pf-art ${/positive/.test(a.sentiment) ? "pos" : /negative/.test(a.sentiment) ? "neg" : ""}" href="${esc(a.link || "#")}" target="_blank" rel="noopener"><b>${esc(a.title)}</b><small>${esc(a.source || "")} · ${esc(a.time_str || "")} · ${esc(a.event === "NEWS" ? "News" : (a.event || "News").toLowerCase().replace(/^./, (c) => c.toUpperCase()))}</small></a>`).join("")}</div>`;
  }
  return out + '<div class="note">Headlines from a Google News search for each company. Tone is the average headline sentiment from −3 to +3; it reads words, not meaning, so open the story before acting on it.</div>';
}
// ── the monthly review: a printable page built in the browser (portfolio_page._report_html) ──
export function report() {
  const P = S.pf, A = calc(), T = A.T, cl = (v) => ((v || 0) >= 0 ? "pos" : "neg"), k = (l, v, c = "") => `<div class="k"><span class="s">${l}</span><b class="${c}">${v}</b></div>`;
  const css = "body{font-family:Inter,Arial,sans-serif;color:#111;margin:32px;}h1{margin:0;font-size:22px}h2{font-size:14px;margin:26px 0 8px;border-bottom:2px solid #FF9F0A;padding-bottom:4px}" +
    "table{border-collapse:collapse;width:100%;font-size:11px}th,td{padding:5px 6px;border-bottom:1px solid #ddd;text-align:left}td.r,th.r{text-align:right}" +
    ".k{display:inline-block;margin:0 18px 8px 0}.k b{display:block;font-size:16px}.s{color:#666;font-size:11px}.pos{color:#0a7a2f}.neg{color:#b3261e}";
  const rows = A.holds.map((h) => `<tr><td>${esc(h.sym)}</td><td>${esc(h.sector)}</td><td class="r">${q4(h.qty)}</td><td class="r">${num(h.avg)}</td><td class="r">${h.ltp == null ? "—" : num(h.ltp)}</td><td class="r">${h.value == null ? "—" : num(h.value, 0)}</td>` +
    `<td class="r ${cl(h.pnl)}">${h.pnl == null ? "—" : num(h.pnl, 0) + " (" + pc(h.pnlPct, 1) + ")"}</td><td class="r">${h.weight == null ? "—" : num(h.weight, 1) + "%"}</td></tr>`).join("");
  const secs = A.sectors.map((s) => `<tr><td>${esc(s.name)}</td><td class="r">${s.n}</td><td class="r">${sh(s.value)}</td><td class="r">${num(s.weight, 1)}%</td><td class="r ${cl(s.pnlPct)}">${pc(s.pnlPct, 1)}</td><td>${esc(s.stage || "")}</td></tr>`).join("");
  const fl = A.flags.filter((f) => f.level === "alert" || f.level === "watch").map((f) => `<li><b>${esc(f.title)}</b>: ${esc(f.detail)}</li>`).join("");
  const taxr = Object.entries(A.tax).map(([f, v]) => `<tr><td>${f}</td><td class="r">${shs(v.stNet)}</td><td class="r">${shs(v.ltNet)}</td><td class="r">${sh(v.tax)}</td></tr>`).join("");
  const now = new Date().toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }), x = A.xirr;
  const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>Arka portfolio review</title><style>${css}</style></head><body>` +
    `<h1>Portfolio review · ${esc(P.book)}</h1><div class="s">Arka Trades · ${esc(now)} IST · prices delayed · not investment advice</div>` +
    `<h2>Summary</h2>${k("Invested (open)", sh(T.inv))}${k("Value", sh(T.val))}${k("Unrealised P&amp;L", shs(T.pnl) + " (" + pc(T.pnlPct, 1) + ")", cl(T.pnl))}${k("Realised P&amp;L", shs(A.realisedTotal), cl(A.realisedTotal))}` +
    `${k("Dividends (est.)", sh(A.divTotal))}${k("XIRR", x != null ? pc(x * 100, 1) : "—", cl(x))}${k("Diversification score", A.score != null ? String(A.score) : "—")}` +
    `<h2>Holdings</h2><table><tr><th>Stock</th><th>Sector</th><th class="r">Qty</th><th class="r">Avg</th><th class="r">Last</th><th class="r">Value</th><th class="r">P&amp;L</th><th class="r">Weight</th></tr>${rows}</table>` +
    `<h2>Sectors</h2><table><tr><th>Sector</th><th class="r">Stocks</th><th class="r">Value</th><th class="r">Weight</th><th class="r">P&amp;L</th><th>Money flow stage</th></tr>${secs}</table>` +
    `<h2>Points to review</h2><ul>${fl || "<li>No warnings.</li>"}</ul>` +
    `<h2>Capital gains (estimate)</h2><table><tr><th>Financial year</th><th class="r">Net STCG</th><th class="r">Net LTCG</th><th class="r">Est. tax</th></tr>${taxr || '<tr><td colspan="4">No sells.</td></tr>'}</table>` +
    `<p class="s">Benchmark used for performance: ${esc(A.benchName)}. Print this page or save it as PDF from your browser.</p></body></html>`;
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([html], { type: "text/html" }));
  a.download = `arka_portfolio_review_${A.today.slice(0, 4)}_${A.today.slice(5, 7)}.html`; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  toast("Downloaded " + a.download);
}
export const partial = { ps: ["pf-ps", psOut], st: ["pf-st", stOut], reb: ["pf-rb", rebOut], find: ["pf-hl", holdList] };

export function page() {
  const P = S.pf; resetCharts();
  const head = (subt, extra = "") => `<div class="hd"><div><h1>Portfolio</h1><div class="sub">${subt}</div></div><button class="btn pri" data-act="buyform">Add a buy</button></div>${extra}`;
  if (!P.loaded) return `<div>${head("Loading…")}<div class="card"><div class="note">Loading your portfolio…</div></div></div>`;
  if (P.err) return `<div>${head("")}<div class="card"><div class="empty"><b>Portfolio is not available</b>${esc(P.err)}</div></div></div>`;
  if (!P.lots.length) return `<div>${head("No holdings yet")}<div class="card"><div class="empty"><b>No holdings yet</b>Tap “Add a buy” and enter the stock, how many shares and the price you paid. Everything else on this page builds itself from your buys and sells.</div></div></div>`;
  const A = calc(), T = A.T, bk = books(), view = VIEW[P.view] ? P.view : "Dashboard";
  queueMicrotask(() => { loadExtras(); snapshot(A); });
  const hero = `<div class="card hero"><div><span>Portfolio value${P.book !== M.ALL ? " · " + esc(P.book) : ""}</span><b>${A.waiting ? '<i class="sk">₹00,00,000</i>' : sh(T.val)}</b>
      <small><span class="${cls(T.pnl)}">${shs(T.pnl)} (${pc(T.pnlPct)}) unrealised</span> · <span class="${cls(T.day)}">${shs(T.day)} today</span></small></div>
    <div class="ctl">${bk.length > 1 ? `<select data-pf="book" aria-label="Book">${[M.ALL, ...bk].map((b) => `<option${P.book === b ? " selected" : ""}>${esc(b)}</option>`).join("")}</select>` : ""}
      <select data-pf="bench" aria-label="Benchmark">${Object.keys(BENCH).map((b) => `<option${P.bench === b ? " selected" : ""}>${b}</option>`).join("")}</select><button class="btn sm" data-act="pfrefresh">Refresh</button><button class="btn sm" data-act="pfreport" title="A printable page: open it and use Print, then Save as PDF">Monthly review</button></div></div>`;
  const pills = `<div class="seg">${VIEWS.map((v) => `<button class="${v === view ? "on" : ""}" data-act="pfview" data-v="${v}">${v}</button>`).join("")}</div>`;
  const empty = !A.holds.length && !["Transactions", "Tax", "Performance", "Swing desk"].includes(view);
  return `<div class="pf">${head(`${T.n} holding${T.n === 1 ? "" : "s"} · ${A.sectors.filter((s) => s.name !== "Unclassified").length} sectors · against ${esc(A.benchName)}`, hero + pills)}${
    empty ? note(`No open positions${P.book !== M.ALL ? " in " + esc(P.book) : ""}. Realised trades and tax are under Tax; add buys under Transactions.`) : VIEW[view](A)}
    <div class="note" style="padding:16px 2px">Prices and history from Yahoo Finance; they can trail the exchange. Sectors use the Arka sector map. P&amp;L excludes brokerage, taxes and charges. Information, not investment advice.</div></div>`;
}

// ── the holding inside the stock sheet: numbers, levels, stop and target, sell, the buys ──
export function positionBlock(s) {
  const P = S.pf; if (!P.loaded || !P.lots.some((l) => l.sym === s)) return "";
  const A = calc(), h = A.holds.find((x) => x.sym === s); if (!h) return "";
  const t = h.tech || {}, ltp = h.ltp, bk = books(), ch = M.chandelier(h.tech, h.firstDate), lv = (v) => (v == null ? "—" : num(v) + (ltp ? ` <em class="${cls(v - ltp)}">${pc((v / ltp - 1) * 100, 1)}</em>` : ""));
  const lots = h.lots.map((l) => { const lp = ltp != null ? (ltp - l.price) * l.open : null, on = l.date ? M.addMonths(l.date, 12) : null, term = !on ? "" : A.today > on ? "long-term" : "long-term from " + nice(on);
    return `<div class="lot"><span><b>${nice(l.date)}</b> · ${q4(l.open)}${l.open < l.qty - 1e-9 ? " of " + q4(l.qty) : ""} at ${num(l.price)}${l.factor !== 1 ? ` <em>entered ${q4(l.rawQty)} at ${num(l.rawPrice)}, ×${+l.factor.toFixed(4)}</em>` : ""}
      <em>${l.book !== M.MAIN || bk.length > 1 ? esc(l.book) + " · " : ""}<span class="${cls(lp)}">${lp == null ? "" : shs(lp)}</span>${term ? " · " + term : ""}${l.note ? " · " + esc(l.note) : ""}</em></span><button data-act="dellot" data-id="${l.id}">Delete</button></div>`; }).join("");
  const rr = h.target && h.sl != null && ltp > h.sl ? (h.target - ltp) / (ltp - h.sl) : null;
  return `<h2>Your position${h.splitAdj ? " <span>split or bonus adjusted</span>" : ""}</h2><div class="stats" style="border-radius:14px;overflow:hidden"><div><span>Shares · average price</span><b>${q4(h.qty)} · ${num(h.avg)}</b></div>
      <div><span>Profit or loss${h.pnl == null ? "" : " · " + pc(h.pnlPct, 1)}</span><b class="${cls(h.pnl)}">${h.pnl == null ? "—" : shs(h.pnl)}</b></div>
      <div><span>Value · weight</span><b>${sh(h.value)} · ${h.weight == null ? "—" : num(h.weight, 1) + "%"}</b></div><div><span>Open R · reward : risk</span><b>${h.rMult == null ? "—" : sgn(h.rMult, 2) + "R"} · ${rr == null ? "—" : num(rr, 2)}</b></div></div>
    ${h.tech ? `<div class="lvl"><div><span>21 EMA</span><b>${lv(t.ema21)}</b></div><div><span>10-day low</span><b>${lv(t.lo10)}</b></div><div><span>3 × ATR trail</span><b>${lv(ch)}</b></div><div><span>ATR · RSI</span><b>${t.atr && ltp ? num((t.atr / ltp) * 100, 1) + "%" : "—"} · ${t.rsi == null ? "—" : num(t.rsi, 0)}</b></div></div>` : ""}
    ${P.can_plan ? `<form id="plan" class="form" data-s="${esc(s)}" data-book="${esc(h.planBook)}"><div class="g2">${field("Stop-loss (₹)", "stop", h.sl ?? "", 'inputmode="decimal" placeholder="none"')}${field("Target (₹)", "target", h.target ?? "", 'inputmode="decimal" placeholder="none"')}</div>
      ${h.sl != null && ltp != null ? `<div class="mini">Distance to stop <b>${pc((h.sl / ltp - 1) * 100, 1)}</b> · at risk <b>${sh(h.risk)}</b></div>` : ""}<div class="err"></div><button class="btn full" type="submit">Save stop and target</button></form>` : ""}
    ${P.can_sell ? `<form id="sell" class="form" data-s="${esc(s)}"><div class="g2">${field("Sell shares", "qty", "", 'inputmode="decimal" placeholder="' + q4(h.qty) + '" required')}${field("Sell price (₹)", "price", ltp ?? "", 'inputmode="decimal" required')}</div>
      <div class="g2">${field("Sell date", "date", A.today, 'type="date" max="' + A.today + '"')}${h.books.length > 1 ? `<label class="f"><span>Book</span><select name="book">${h.books.map((b) => `<option>${esc(b)}</option>`).join("")}</select></label>` : `<input type="hidden" name="book" value="${esc(h.books[0])}">`}</div>
      <div class="err"></div><button class="btn red full" type="submit">Record sell</button></form>` : ""}
    <h2>Your buys</h2><div class="card">${lots}</div>
    ${P.can_book ? `<form id="move" class="form" data-s="${esc(s)}"><div class="g2"><label class="f"><span>Move a buy</span><select name="lot">${h.lots.map((l) => `<option value="${l.id}">${nice(l.date)} · ${q4(l.open)} at ${num(l.price)}</option>`).join("")}</select></label>
      <label class="f"><span>To book</span><input name="book" list="pf-books" placeholder="Trading" maxlength="24" required><datalist id="pf-books">${bk.map((b) => `<option>${esc(b)}</option>`).join("")}</datalist></label></div><div class="err"></div><button class="btn full" type="submit">Move to book</button></form>` : ""}`;
}
export function csv(kind) {                                 // a download built in the browser; nothing is sent anywhere
  const A = calc(), r2 = (v) => (v == null ? "" : Math.round(v * 100) / 100), cell = (v) => (/[",\n]/.test(String(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : String(v ?? ""));
  const rows = kind === "realised" ? [["Sell date", "Symbol", "Book", "Quantity", "Buy date", "Buy price", "Sell price", "Cost", "Proceeds", "Gain", "Days held", "Term", "Financial year"],
      ...A.realised.map((x) => [x.sellDate, x.sym, x.book, r2(x.qty), x.buyDate, r2(x.buyPrice), r2(x.sellPrice), r2(x.cost), r2(x.proceeds), r2(x.gain), x.days, x.term, x.fy])]
    : [["Symbol", "Name", "Books", "Sector", "Quantity", "Avg cost", "Last price", "Invested", "Value", "P&L", "P&L %", "Weight %", "Stop-loss", "Target", "Risk to stop", "Sector stage", "P/E", "ROE %", "ROCE %", "Quality"],
      ...A.holds.map((h) => [h.sym, h.name, h.books.join(" / "), h.sector, r2(h.qty), r2(h.avg), h.ltp, r2(h.invested), r2(h.value), r2(h.pnl), r2(h.pnlPct), r2(h.weight), h.sl, h.target, r2(h.risk), h.stage || "", h.pe ?? "", h.roe ?? "", h.roce ?? "", h.fscore ? h.fscore[0] + "/" + h.fscore[1] : ""])];
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([rows.map((r) => r.map(cell).join(",")).join("\n")], { type: "text/csv" }));
  a.download = kind === "realised" ? "arka_realised_pnl.csv" : "arka_portfolio.csv"; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  toast("Downloaded " + a.download);
}

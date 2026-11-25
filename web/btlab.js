// Arka Trades · Backtest lab (backtest_page.py): write a setup in plain words, pick stocks or indices, run it on years of daily prices.
// The reading is setuplang.js (fixed patterns, not an AI model); the simulation is btengine.js (signals at the close, entry the next
// day, Indian costs). Both were checked against the Python files. Prices: Yahoo daily candles adjusted for splits and dividends.
import { S, api, paint, esc, toast } from "./app.js";
import { fx } from "./rsengine.js";
import { lineChart, legend } from "./charts.js";
import * as B from "./btengine.js";
import * as L from "./setuplang.js";

export const INDICES = { "NIFTY 50": "^NSEI", "BANK NIFTY": "^NSEBANK", SENSEX: "^BSESN", "NIFTY NEXT 50": "^NSMIDCP", "NIFTY 500": "^CRSLDX", "FIN NIFTY": "NIFTY_FIN_SERVICE.NS", "NIFTY IT": "^CNXIT", "NIFTY AUTO": "^CNXAUTO",
  "NIFTY PHARMA": "^CNXPHARMA", "NIFTY FMCG": "^CNXFMCG", "NIFTY METAL": "^CNXMETAL", "NIFTY REALTY": "^CNXREALTY", "NIFTY ENERGY": "^CNXENERGY", "NIFTY PSU BANK": "^CNXPSUBANK", "NIFTY MEDIA": "^CNXMEDIA", "NIFTY INFRA": "^CNXINFRA" };
const ALIAS = { NIFTY: "NIFTY 50", NIFTY50: "NIFTY 50", BANKNIFTY: "BANK NIFTY", "NIFTY BANK": "BANK NIFTY", FINNIFTY: "FIN NIFTY", NIFTYIT: "NIFTY IT" };
const YEARS = [5, 10, 3, 8], MAX_ITEMS = 25;
const KNOWN = "RELIANCE HDFCBANK ICICIBANK INFY TCS BHARTIARTL ITC LT SBIN AXISBANK KOTAKBANK HINDUNILVR BAJFINANCE M&M SUNPHARMA HCLTECH MARUTI NTPC TITAN ULTRACEMCO POWERGRID TATASTEEL ASIANPAINT ONGC NESTLEIND ADANIPORTS BAJAJFINSV JSWSTEEL TECHM WIPRO COALINDIA HINDALCO GRASIM CIPLA DRREDDY BAJAJ-AUTO EICHERMOT HEROMOTOCO BRITANNIA APOLLOHOSP SBILIFE HDFCLIFE TATACONSUM ADANIENT SHRIRAMFIN TRENT BEL INDUSINDBK ETERNAL JIOFIN HAL DLF VEDL PIDILITIND SIEMENS GODREJCP DABUR AMBUJACEM BANKBARODA PNB CANBK GAIL IOC BPCL TATAPOWER ADANIGREEN ADANIPOWER DMART LICI HAVELLS CHOLAFIN ICICIGI ICICIPRULI TVSMOTOR SHREECEM TORNTPHARM ZYDUSLIFE DIVISLAB LTIM NAUKRI INDIGO MOTHERSON BOSCHLTD ABB CGPOWER VBL UNITDSPR IRFC RECLTD PFC BAJAJHLDNG JSWENERGY INDHOTEL LODHA POLYCAB PERSISTENT COFORGE MUTHOOTFIN BHEL HINDZINC TATAMOTORS TMPV".split(" ");   // quant_data.FALLBACK
export const bxDefaults = () => ({ items: [], q: "", bad: [], text: "", years: 5, capital: 1000000, risk: 1.0, cost: B.COST_DEFAULT, more: false, vocab: false, busy: false, prog: "", res: null, key: "", sym: "", trust: false, err: "" });

export function resolve(name) {                               // what was typed -> [label, Yahoo ticker, is index] or null
  let s = String(name || "").toUpperCase().trim().replace(/\s+/g, " "); if (!s) return null;
  s = ALIAS[s] || s; if (INDICES[s]) return [s, INDICES[s], true]; if (s.startsWith("^")) return [s, s, true];
  s = s.replace(".NS", "").replace(/ /g, ""); if (!/^[A-Z0-9&\-_.]{1,25}$/.test(s)) return null; return [s, s + ".NS", false];
}
const options = () => [...new Set([...Object.keys(INDICES), ...S.wl.rows.map((r) => r.sym), ...((S.wl.desk && S.wl.desk.syms) || []), ...KNOWN])];
const f = (v, d = 2) => (v == null || !isFinite(v) ? "—" : fx(v, d, true)), fs = (v, d = 2) => (v == null || !isFinite(v) ? "—" : fx(v, d, false, true).replace(/^-/, "−"));
const R = (v) => (v == null || !isFinite(v) ? "—" : fs(v, 2) + "R"), cls = (v) => (v > 0 ? "up" : v < 0 ? "dn" : "");
const MON = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" "), dmy = (d) => { const [y, m, dd] = d.split("-"); return `${dd} ${MON[+m - 1]} ${y}`; }, my = (d) => { const [y, m] = d.split("-"); return `${MON[+m - 1]} ${y}`; };
const istNow = () => new Date(Date.now() + 330 * 60000), istDay = (t) => t.toISOString().slice(0, 10);
const reading = () => L.parse(S.bx.text || "");
const cfgOf = () => { const X = S.bx, r = reading(); return { items: X.items, text: X.text.trim(), rules: r.rules, ex: r.ex, years: +X.years, capital: +X.capital, risk: r.book.risk_pct ? +r.book.risk_pct : +X.risk, cost: +X.cost }; };
const keyOf = (c) => JSON.stringify(c);

// ── prices: exactly the instruments asked for, plus NIFTY 50 for the calendar (backtest_page._panel) ──
async function panel(items, years) {
  const now = istNow(), start = istDay(new Date(now.getTime() - (Math.trunc(years * 365.25) + 420) * 864e5)), cut = now.getUTCHours() < 16 ? istDay(now) : null;   // before 16:00 IST today's candle is still forming
  const want = [...new Set(["^NSEI", ...items.map((x) => x[1])])], got = {};
  for (let i = 0; i < want.length; i += 6) { S.bx.prog = `Downloading prices · ${Math.min(i + 6, want.length)} of ${want.length}`; paintProg();
    const tick = want.slice(i, i + 6), q = tick.map((t) => (t.endsWith(".NS") && !t.includes("_") ? t.slice(0, -3) : t));
    const d = await api(`/api/history?full=1&from=${start}&s=${encodeURIComponent(q.join(","))}`); (d.history || []).forEach((h, k) => { got[tick[q.indexOf(h.sym)] || tick[k]] = h; }); }
  const frame = (h) => { if (!h || !h.ok || h.daily === false) return null; const o = { d: [], o: [], h: [], l: [], c: [], v: [] };       // yfinance auto_adjust: close = adjusted close, open / high / low scaled the same way
    for (let k = 0; k < h.d.length; k++) { const c = h.c[k], a = h.a ? h.a[k] : c; if (c == null || a == null || !(a > 0) || h.d[k] < start || (cut && h.d[k] >= cut)) continue; const r = a / c;
      o.d.push(h.d[k]); o.o.push(h.o[k] == null ? null : h.o[k] * r); o.h.push(h.h[k] == null ? null : h.h[k] * r); o.l.push(h.l[k] == null ? null : h.l[k] * r); o.c.push(a); o.v.push(h.v[k] == null ? null : h.v[k]); }
    return o.d.length >= 60 ? o : null; };
  const nifty = frame(got["^NSEI"]); if (!nifty) throw new Error("Yahoo Finance did not return NIFTY 50 history, which sets the trading calendar. Try again in a minute.");
  const frames = {}, labels = [], missing = [];
  for (const [label, t] of items) { const fr = frame(got[t]); if (fr) { frames[label] = fr; labels.push(label); } else missing.push(label); }
  if (!labels.length) throw new Error("No price history came back for: " + missing.join(", ") + ". Check the NSE symbols.");
  const p = B.buildPanel(frames, labels, nifty, years), noVol = [];
  p.symbols.forEach((label, j) => { const v = p.v[j]; let s = 0; for (let i = Math.max(0, p.T - 250); i < p.T; i++) if (v[i] === v[i]) s += v[i]; if (!(s > 0)) { v.fill(NaN); noVol.push(label); } });   // an index has no traded volume: volume rules can then never be true
  B.addIndicators(p); p.missing = missing; p.no_vol = noVol; return p;
}
const paintProg = () => { const el = document.getElementById("bx-prog"); if (el) el.textContent = S.bx.prog; };
async function run() {
  const X = S.bx, cfg = cfgOf(); if (X.busy || !cfg.items.length || !cfg.rules.length) return;
  const a = document.activeElement; if (a && a.blur) a.blur();
  X.busy = true; X.err = ""; X.prog = "Downloading prices and testing…"; paint();
  try {
    const p = await panel(cfg.items, cfg.years); X.prog = "Testing…"; paintProg(); await new Promise((r) => setTimeout(r, 0));
    const n = p.symbols.length, bk = B.book({ capital: cfg.capital, risk_pct: cfg.risk, max_pos: Math.max(1, n), max_pos_pct: 100.0 / Math.max(1, Math.min(n, 4)), regime_mode: "Off" });
    const res = B.runSingle(p, cfg.rules, cfg.ex, bk, cfg.cost), s0 = res.start_i;
    if (res.curve.v.length > 1) { const pos = new Map(p.dates.map((d, i) => [d, i])), ic = res.curve.d.map((d) => p.idx_c[pos.get(d)]); for (let i = 1; i < ic.length; i++) if (ic[i] !== ic[i]) ic[i] = ic[i - 1];
      res.bench_curve = ic.map((x) => (x / ic[0]) * 100); res.bench_stats = B.curveStats({ d: res.curve.d, v: ic }); }
    const tk = cfg.ex.trail_n ? B.ma(p, cfg.ex.trail_kind, cfg.ex.trail_n) : null;
    Object.assign(res, { cfg, reading: reading(), symbols: p.symbols.slice(), missing: p.missing, no_vol: p.no_vol, first: p.dates[s0], last: p.dates[p.T - 1],
      px: Object.fromEntries(p.symbols.map((s, j) => [s, { d: p.dates.slice(s0), c: Array.from(p.c[j].subarray(s0)), trail: tk ? Array.from(tk[j].subarray(s0)) : null }])) });
    X.res = res; X.key = keyOf(cfg); X.sym = p.symbols[0];
  } catch (e) { if (e.message !== "signed out") X.err = e.message || "The test could not run."; }
  finally { X.busy = false; X.prog = ""; paint(); }
}

// ── the page ──
function readingHtml(r) {
  const entry = r.read.filter((x) => x[3] === "entry"), n = entry.length, out = [];
  out.push(`<div class="bx-rt">How I read your setup<span>${esc(`${n} buy condition${n !== 1 ? "s" : ""} understood` + (r.missed.length ? ` · ${r.missed.length} not understood` : ""))}</span></div>`);
  out.push(`<div class="bx-sec">Buy signal: every line below must be true at the day's close</div>`);
  out.push(entry.length ? entry.map(([w, m, note]) => `<div class="bx-it ok"><i>✓</i><div>${esc(m)}<small>from <q>${esc(w)}</q></small>${note ? `<small class="w">${esc(note)}</small>` : ""}</div></div>`).join("")
    : '<div class="bx-empty">No buy condition yet. Write at least one, for example "close above 50 EMA" or "breakout of 20 day high".</div>');
  out.push('<div class="bx-sec">How the trade is taken and closed</div>');
  const notes = r.read.filter((x) => x[3] === "exit" && x[2]).map((x) => [x[1], x[2]]);
  for (const line of L.exitSummary(r.ex)) { const nt = (notes.find(([m]) => m.slice(0, 22) === line.slice(0, 22)) || [])[1]; out.push(`<div class="bx-it ex"><i>→</i><div>${esc(line)}${nt ? `<small class="w">${esc(nt)}</small>` : ""}</div></div>`); }
  if (r.book.risk_pct) out.push(`<div class="bx-it ex"><i>→</i><div>Risk ${L._n(r.book.risk_pct)}% of capital on each trade</div></div>`);
  if (r.assumed.length) out.push('<div class="bx-sec">Filled in because your setup did not say</div>' + r.assumed.map((a) => `<div class="bx-it as"><i>!</i><div>${esc(a)}</div></div>`).join(""));
  if (r.missed.length) out.push('<div class="bx-sec">Not understood, so NOT tested</div>' + r.missed.map(([w, why]) => `<div class="bx-it no"><i>✕</i><div><span>${esc(w)}</span><small>${esc(why)}</small></div></div>`).join(""));
  return out.join("");
}
function runBar() {
  const X = S.bx, r = reading(), ready = X.items.length && r.rules.length, need = [!X.items.length ? "add a stock or index" : "", !r.rules.length ? "write at least one buy condition I can read" : ""].filter(Boolean).join(" and ");
  return `<button class="btn pri full" data-act="bxrun"${ready && !X.busy ? "" : " disabled"}>${X.busy ? "Testing…" : "Run backtest"}</button>${X.busy ? `<div class="mini" id="bx-prog">${esc(X.prog)}</div>` : !ready ? `<div class="mini">To run: ${esc(need)}.</div>` : ""}`;
}
function controls() {
  const X = S.bx, r = reading(), opts = options();
  const chips = X.items.map(([lab]) => `<span class="bx-chip">${esc(lab)}<button data-act="bxdel" data-v="${esc(lab)}" aria-label="Remove ${esc(lab)}">✕</button></span>`).join("");
  return `<div class="card pad bx-card"><div class="bx-h"><em>1</em>What do you want to test on?</div><div class="bx-d">Type a stock symbol or an index name and press Enter. You can add more than one (up to ${MAX_ITEMS}).</div>
      <div class="bx-pick">${chips}<input data-bx="q" list="bx-opts" value="${esc(X.q)}" placeholder="${X.items.length ? "Add another…" : "Search: RELIANCE, TCS, NIFTY 50, BANK NIFTY …"}" autocapitalize="characters" spellcheck="false" autocomplete="off"${X.items.length >= MAX_ITEMS ? " disabled" : ""}><button class="btn sm" data-act="bxadd">Add</button></div>
      <datalist id="bx-opts">${opts.map((o) => `<option value="${esc(o)}">`).join("")}</datalist>
      ${X.bad.length ? `<div class="mini am">Not a symbol I can use: ${esc(X.bad.join(", "))}. Use the NSE symbol, for example TMPV or M&amp;M.</div>` : ""}</div>
    <div class="card pad bx-card"><div class="bx-h"><em>2</em>Write your setup</div><div class="bx-d">In your own words, one idea per line or separated by commas: when to buy, where the stop goes, when to exit.</div>
      <textarea data-bx="text" rows="7" placeholder="Example:&#10;Close above 50 EMA and 200 SMA&#10;Breakout of 20 day high with volume 1.5x average&#10;Stop loss at signal candle low&#10;Exit when close below 21 EMA">${esc(X.text)}</textarea>
      <div class="bx-ex"><span>Show me an example</span>${Object.keys(L.EXAMPLES).map((n) => `<button class="btn sm" data-act="bxex" data-v="${esc(n)}">${esc(n)}</button>`).join("")}</div>
      <details class="bx-voc"${X.vocab ? " open" : ""} data-bxd="vocab"><summary>Words I understand</summary>${L.VOCAB.map(([g, xs]) => `<b>${esc(g)}</b>${xs.map((x) => `<span>${esc(x)}</span>`).join("")}`).join("")}
        <b>Good to know</b><div class="mini">Numbers can be anything: "close above 30 EMA", "breakout of 55 day high", "stop loss 4.5%". Weeks and months are converted to trading days (1 week = 5, 1 month = 21). Long trades on daily candles only. If a line shows in red below, it was not understood and is left out of the test.</div></details></div>
    <div class="card pad bx-read" id="bx-read"${(X.text || "").trim() ? "" : " hidden"}>${(X.text || "").trim() ? readingHtml(r) : ""}</div>
    <details class="card sc-det"${X.more ? " open" : ""} data-bxd="more"><summary>Capital, risk, costs and history <span>${X.years} years · ₹${f(+X.capital, 0)} · risk ${L._n(r.book.risk_pct || X.risk)}% · costs ${f(+X.cost)}%</span></summary><div class="pad"><div class="g4x">
      <label class="f"><span>History</span><select data-bx="years">${YEARS.map((y) => `<option value="${y}"${+X.years === y ? " selected" : ""}>${y} years</option>`).join("")}</select></label>
      <label class="f"><span>Capital (₹)</span><input data-bx="capital" type="number" min="10000" max="10000000000" step="50000" value="${esc(X.capital)}" inputmode="decimal"></label>
      <label class="f"><span>Risk per trade (%)</span><input data-bx="risk" type="number" min="0.1" max="10" step="0.25" value="${esc(X.risk)}" inputmode="decimal" title="How much of the capital is lost when a trade hits its stop. Your setup text overrides this if it says &quot;risk 1% per trade&quot;."></label>
      <label class="f"><span>Costs per round trip (%)</span><input data-bx="cost" type="number" min="0" max="3" step="0.05" value="${esc(X.cost)}" inputmode="decimal" title="Brokerage, STT, stamp duty, exchange and GST charges plus slippage, for buying and selling once."></label></div>
      ${r.book.risk_pct ? `<div class="mini">Your setup says risk ${L._n(r.book.risk_pct)}% per trade, so that is used instead of the box.</div>` : ""}</div></details>
    <div class="bx-run" id="bx-run">${runBar()}</div>`;
}
const kpi = (label, value, sub = "", c = "") => `<div class="sc-kpi bx-kpi"><small>${esc(label)}</small><b class="${c}">${value}</b>${sub ? `<em>${sub}</em>` : ""}</div>`;
const table = (head, rows, left = 1) => `<div class="tw"><table class="sc-t bx-t"><thead><tr>${head.map((h, i) => `<th class="${i >= left ? "r" : ""}">${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c, i) => `<td class="${i >= left ? "r" : ""}">${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
function stockChart(res, sym) {
  const P = res.px[sym], tr = res.trades.filter((t) => t.symbol === sym), s0 = res.start_i, ex = res.cfg.ex, st = tr.length ? B.tradeStats(tr.map((t) => t.r)) : { n: 0 };
  const series = [{ n: "Close", c: "#F2F2F5", v: P.c, w: 1.4 }].concat(P.trail ? [{ n: `${ex.trail_n}-day ${ex.trail_kind}`, c: "var(--amber)", v: P.trail, w: 1.1 }] : []);
  const marks = tr.map((t) => [t.entry_i - s0, t.entry, "up"]).concat(tr.filter((t) => t.reason !== "Open at end").map((t) => [t.exit_i - s0, t.exit, "dn"]));
  const sub = st.n ? `${st.n} trades · ${f(st.win, 0)}% won · ${fs(st.exp)}R per trade · total ${fs(st.total_r, 1)}R` : "no trades";
  return `<div class="card pad"><div class="bx-ch">${esc(sym)} with your trades<em>${sub}</em></div>${lineChart(P.d, series, { h: innerWidth < 900 ? 220 : 300, log: true, marks, fmt: (v) => f(v) })}
    ${legend([["Close (adjusted for splits and dividends)", "#F2F2F5"]].concat(P.trail ? [[`${ex.trail_n}-day ${ex.trail_kind}`, "var(--amber)"]] : []).concat([["Buy ▲", "var(--up)", true], ["Sell ▼", "var(--dn)", true]]))}
    ${INDICES[sym] || sym.startsWith("^") ? "" : `<div class="bx-act"><a class="btn sm" href="#/s/${encodeURIComponent(sym)}">Open ${esc(sym)} in the terminal</a></div>`}</div>`;
}
function results(res) {
  const X = S.bx, cfg = res.cfg, tr = res.trades, names = res.symbols.slice(0, 6).join(", ") + (res.symbols.length > 6 ? ` and ${res.symbols.length - 6} more` : ""); let out = "";
  if (res.missing.length) out += `<div class="card sc-warn">No price history for: ${esc(res.missing.join(", "))}. Left out; check the NSE symbols.</div>`;
  if (res.no_vol.length && cfg.rules.some((r) => ["vol_mult", "vol_dry", "turnover_min", "x_vol_mult_n", "x_vol_gt_prev"].includes(r.type)))
    out += `<div class="card sc-warn">${esc(res.no_vol.join(", "))} has no traded volume (an index), so your volume condition can never be true for it and it gives no trades. Remove the volume line to test an index.</div>`;
  if (!tr.length) return out + `<div class="card bx-verdict" style="--c:#FFD60A"><b>No trades</b><p>Your buy conditions were never all true on the same day for ${esc(names)} between ${res.first} and ${res.last}, or every signal was skipped at the entry. Loosen or remove one condition and run again.</p></div>`;
  const [tone, head, body] = B.verdict(res), col = { good: "var(--up)", warn: "#FFD60A", bad: "var(--dn)" }[tone];
  out += `<div class="card bx-verdict" style="--c:${col}"><b>${esc(head)}</b><p>${esc(body)}</p><p class="mu">${esc(names)} · ${res.first} to ${res.last} · ${cfg.rules.length} buy conditions · costs ${f(cfg.cost)}% per round trip · buys the day after the signal</p></div>`;
  const s = B.tradeStats(tr.map((t) => t.r), tr.map((t) => t.days)), cs = B.curveStats(res.curve), pf = isFinite(s.pf) ? f(s.pf) : "∞", retMean = B.npMean(tr.map((t) => t.ret_pct));
  const kp = [kpi("Trades", `${s.n}`, `${f(s.win, 0)}% won`), kpi("Average result per trade", R(s.exp), `${fs(retMean)}% of the buy price`, cls(s.exp)),
    kpi("Average win / loss", `<span class="up">${fs(s.avg_win)}R</span> / <span class="dn">${f(s.avg_loss)}R</span>`, "after costs"), kpi("Profit factor", pf, "money won ÷ money lost"),
    kpi("Average holding", `${f(s.avg_days, 0)} sessions`, `longest losing run ${s.max_losing_streak} trades`), kpi("Best / worst trade", `<span class="up">${fs(s.best, 1)}R</span> / <span class="dn">${f(s.worst, 1)}R</span>`, "R = one unit of risk")];
  if (cs) { kp.push(kpi("Account growth a year", `${fs(cs.cagr, 1)}%`, `₹${f(cfg.capital, 0)} → ₹${f(res.curve.v[res.curve.v.length - 1], 0)}`, cls(cs.cagr)), kpi("Deepest fall", `${f(cs.maxdd, 1)}%`, `${my(cs.dd_peak)} to ${my(cs.dd_trough)}`, "dn"));
    if (res.bench_stats) kp.push(kpi("NIFTY 50, buy and hold", `${fs(res.bench_stats.cagr, 1)}% a year`, `deepest fall ${f(res.bench_stats.maxdd, 1)}%`)); }
  out += `<div class="sc-kpis bx-kpis">${kp.join("")}</div><div class="mini bx-line">R is your risk on a trade: the distance from the buy price to the stop. +2R means the trade made twice what it risked; −1R is a full stop-out.</div>`;
  if (cs && res.curve.v.length > 2) {
    const base = res.curve.v.map((v) => (v / res.curve.v[0]) * 100), series = [{ n: "Your setup", c: "var(--amber)", v: base, w: 1.8, fill: true }].concat(res.bench_curve ? [{ n: "NIFTY 50", c: "#9A9AA0", v: res.bench_curve, w: 1.2 }] : []);
    const idle = res.symbols.length <= 5 ? " With one or a few instruments the money sits in cash between trades, so judge the setup by the result per trade more than by account growth." : "";
    out += `<div class="card pad"><div class="bx-ch">Account value<em>start 100 · risk ${L._n(cfg.risk)}% per trade · log scale</em></div>${lineChart(res.curve.d, series, { h: innerWidth < 900 ? 200 : 260, log: true, fmt: (v) => f(v, 1) })}
      ${legend([["Your setup", "var(--amber)"], ["NIFTY 50 buy and hold", "#9A9AA0"]])}<div class="bx-ch" style="margin-top:14px">Fall from the previous peak<em>%</em></div>
      ${lineChart(cs.dd.d, [{ n: "Fall from peak", c: "var(--dn)", v: cs.dd.v.map((x) => x * 100), w: 1.2, fill: true }], { h: 110, fmt: (v) => f(v, 1) + "%" })}
      <div class="mini">${tr.length} trades simulated, ${res.taken.length} taken by the account.${idle}</div></div>`;
  }
  const syms = res.symbols, sym = syms.includes(X.sym) ? X.sym : syms[0];
  if (syms.length > 1) out += `<div class="seg bx-seg">${syms.map((x) => `<button class="${x === sym ? "on" : ""}" data-act="bxsym" data-v="${esc(x)}">${esc(x)}</button>`).join("")}</div>`;
  out += stockChart(res, sym);
  const two = [], yr = B.breakdown(tr.map((t) => ({ ...t, year: +t.entry_date.slice(0, 4) })), "year");
  if (yr.length) two.push(`<div class="card pad"><div class="bx-ch">Year by year<em>does it work every year, or only in one?</em></div>${table(["Year", "Trades", "Won", "Per trade", "Total"], yr.map(([y, v]) => [String(y), String(v.trades), f(v.win, 0) + "%", `<span class="${cls(v.exp)}">${fs(v.exp)}R</span>`, `<span class="${cls(v.total)}">${fs(v.total, 1)}R</span>`]))}</div>`);
  const br = B.breakdown(tr, "reason").map((x, i) => [x, i]).sort((a, b) => b[0][1].trades - a[0][1].trades || a[1] - b[1]).map((x) => x[0]);
  if (br.length) two.push(`<div class="card pad"><div class="bx-ch">How trades ended<em>which exit makes or loses the money</em></div>${table(["Exit", "Trades", "Won", "Per trade"], br.map(([k, v]) => [esc(k), String(v.trades), f(v.win, 0) + "%", `<span class="${cls(v.exp)}">${fs(v.exp)}R</span>`]))}</div>`);
  if (two.length) out += `<div class="bx-two">${two.join("")}</div>`;
  if (syms.length > 1) out += `<div class="card pad"><div class="bx-ch">By instrument</div>${table(["Instrument", "Trades", "Won", "Per trade", "Total"], B.perStock(tr).map(([k, v]) => [`<a class="sc-lk" data-act="bxsym" data-v="${esc(k)}">${esc(k)}</a>`, String(v.trades), f(v.win, 0) + "%", `<span class="${cls(v.exp)}">${fs(v.exp)}R</span>`, `<span class="${cls(v.total)}">${fs(v.total, 1)}R</span>`]))}</div>`;
  const hv = X.trust ? B.halves(tr) : null, mc = X.trust ? (X.mc && X.mc.key === X.key ? X.mc.v : (X.mc = { key: X.key, v: B.monteCarlo(tr.map((t) => t.r), cfg.risk) }).v) : null;
  out += `<details class="card sc-det"${X.trust ? " open" : ""} data-bxd="trust"><summary>Can I trust it? <span>first half against second half, and how bad a losing run can get</span></summary><div class="pad">${!X.trust ? "" :
    (hv ? `<div class="bx-sub"><b>First half against second half.</b> First half: <b>${R(hv.a_exp)}</b> per trade over ${hv.a_n} trades. Second half: <b>${R(hv.b_exp)}</b> over ${hv.b_n} trades. An edge that shows up in only one half is fragile.</div>` : "")
    + (mc ? `<div class="bx-sub"><b>If the same trades came in a different order</b> (${f(mc.sims, 0)} shuffles). Typical deepest fall <b>${f(mc.dd_med, 1)}%</b>; one time in ten it is worse than <b>${f(mc.dd_p90, 1)}%</b>, one in twenty worse than <b>${f(mc.dd_p95, 1)}%</b>. Typical longest losing run ${f(mc.streak_med, 0)} trades (one in ten: ${f(mc.streak_p90, 0)}). Chance of ending below the starting capital: <b>${f(mc.p_loss, 0)}%</b>.</div>` : "")
    || '<div class="mini">Needs at least 20 trades.</div>'}</div></details>`;
  const show = tr.map((t, i) => [t, i]).sort((a, b) => b[0].entry_i - a[0].entry_i || a[1] - b[1]).map((x) => x[0]);
  out += `<div class="card pad"><div class="bx-ch">Every trade<em>newest first${show.length > 300 ? " · first 300 shown" : ""}</em></div>${table(["Instrument", "Bought", "Buy price", "Stop", "Sold", "Sell price", "Why it ended", "Sessions", "Result", "In R"],
    show.slice(0, 300).map((t) => [esc(t.symbol), dmy(t.entry_date), f(t.entry), f(t.stop), dmy(t.exit_date), f(t.exit), `<span class="l">${esc(t.reason)}</span>`, String(t.days), `<span class="${cls(t.ret_pct)}">${fs(t.ret_pct)}%</span>`, `<span class="${cls(t.r)}">${fs(t.r)}R</span>`]), 1)}</div>
    <div class="bx-act"><button class="btn" data-act="bxcsv">Download all trades (CSV)</button></div>
    <div class="note">Past results do not promise future results. Prices are Yahoo Finance daily candles adjusted for splits and dividends. A signal is read at the close and bought the next session, so the test never uses information it could not have had. Index results are for the index level itself, which cannot be bought directly. Information only, not investment advice.</div>`;
  return out;
}
export function page() {
  const X = S.bx;
  let body = controls();
  if (X.err) body += `<div class="card bx-verdict" style="--c:var(--dn)"><b>Could not run</b><p>${esc(X.err)}</p></div>`;
  if (X.res) { if (X.key !== keyOf(cfgOf())) body += '<div class="mini am bx-line">You changed something since this result. Press Run backtest to update it.</div>';
    try { body += results(X.res); } catch (e) { body += `<div class="card"><div class="empty"><b>The result could not be drawn</b>${esc(e.message)}</div></div>`; } }
  return `<div class="sc bx"><div class="hd"><div><h1>Backtest lab</h1><div class="sub">Write your setup in plain words, pick a stock or an index, and see how it would have traded.</div></div></div>${body}</div>`;
}

// ── actions ──
const repaint = (refocus = null) => { const a = document.activeElement; if (a && a.blur) a.blur(); paint(); if (refocus) { const i = document.querySelector(refocus); if (i) i.focus(); } };      // the app does not redraw under a box being typed in
function addTyped(text) {
  const X = S.bx, bad = [];
  for (const nm of String(text).split(/[,;\n]+/).map((x) => x.trim()).filter(Boolean)) { const got = resolve(nm); if (!got) bad.push(nm); else if (!X.items.some((x) => x[1] === got[1]) && X.items.length < MAX_ITEMS) X.items.push(got); }
  X.bad = bad; X.q = "";
}
const csvCell = (v) => (v == null ? "" : /[",\n]/.test(String(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : String(v));
export function tradesCsv(tr) {
  const show = tr.map((t, i) => [t, i]).sort((a, b) => b[0].entry_i - a[0].entry_i || a[1] - b[1]).map((x) => x[0]);
  return [["Instrument", "Bought", "Buy price", "Stop", "Sold", "Sell price", "Why it ended", "Sessions", "Result %", "Result R"]].concat(show.map((t) => [t.symbol, t.entry_date, t.entry, t.stop, t.exit_date, t.exit, t.reason, t.days, t.ret_pct, t.r])).map((r) => r.map(csvCell).join(",")).join("\n") + "\n";
}
export function act(a, el) {
  const X = S.bx, v = el.dataset.v;
  if (a === "bxrun") return run();
  if (a === "bxadd") { addTyped(X.q); return repaint('[data-bx="q"]'); }
  if (a === "bxdel") { X.items = X.items.filter((x) => x[0] !== v); return repaint(); }
  if (a === "bxex") { X.text = L.EXAMPLES[v]; return repaint(); }
  if (a === "bxsym") { X.sym = v; return repaint(); }
  if (a === "bxcsv") { const b = new Blob([tradesCsv(X.res.trades)], { type: "text/csv" }), l = document.createElement("a"); l.href = URL.createObjectURL(b); l.download = "arka_backtest_trades.csv"; document.body.appendChild(l); l.click(); l.remove(); setTimeout(() => URL.revokeObjectURL(l.href), 4000); return; }
}
export function input(el, ev) {
  const X = S.bx, k = el.dataset.bx;
  if (k === "q") { X.q = el.value;                                                                           // a comma, Enter, or a picked suggestion adds it
    if ((ev === "input" && /[,;]/.test(el.value)) || (ev === "change" && el.value.trim())) { addTyped(el.value); repaint(ev === "input" ? '[data-bx="q"]' : null); } return; }
  if (k === "text") { if (ev !== "input") return; X.text = el.value; const r = reading(), box = document.getElementById("bx-read"); if (box) { box.hidden = !X.text.trim(); box.innerHTML = X.text.trim() ? readingHtml(r) : ""; } const rb = document.getElementById("bx-run"); if (rb) rb.innerHTML = runBar(); return; }
  if (["years", "capital", "risk", "cost"].includes(k)) { if (el.tagName === "INPUT" && ev === "input") { X[k] = el.value === "" ? X[k] : Number(el.value); return; } X[k] = Number(el.value); return repaint(); }
}
export function enter(el) { if (el.dataset.bx === "q") { addTyped(el.value); repaint('[data-bx="q"]'); return true; } return false; }
export function toggle(el) { if (el.dataset.bxd) { const was = S.bx[el.dataset.bxd]; S.bx[el.dataset.bxd] = el.open; if (el.dataset.bxd === "trust" && el.open && !was) paint(); } }

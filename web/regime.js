// Arka Trades · Market regime. Shows the file the nightly "Market data" job writes with the Streamlit regime engine
// (regime_engine.py), so the state, score, components and history are the same numbers as the classic app.
import { S, api, paint, esc, num, sgn, nice, todayStr } from "./app.js";
import { lineChart, resetCharts, bandsFrom, legend } from "./charts.js";

const GREEN = "#30D158", RED = "#FF453A", AMBER = "#FF9F0A", BLUE = "#4FA3FF", PURPLE = "#BF5AF2", CYAN = "#5AC8FA", YELLOW = "#FFD60A";
const STATE_COLOR = { "Confirmed uptrend": GREEN, "Under pressure": YELLOW, "Rally attempt": BLUE, Correction: RED };
const TONE = { good: GREEN, warn: YELLOW, bad: RED, info: BLUE };
const COMP = { trend: ["Trend", "NIFTY 50 vs its 21 / 50 EMA and 200 DMA, and the 50 EMA's slope"], breadth: ["Breadth", "Share of stocks above their 50 EMA and 200 DMA"],
  highs: ["New highs vs lows", "Share of new 52-week highs among highs + lows, 10 days"], mcclellan: ["McClellan", "Momentum of advancers vs decliners"],
  distribution: ["Distribution", "Institutional selling days in the last 25 sessions"], volatility: ["Volatility", "India VIX vs its past year, and how fast it is rising"],
  appetite: ["Risk appetite", "Average stock vs NIFTY 50"] };
const UNIS = { "Nifty 500": "regime_nifty500", "Nifty 200": "regime_nifty200", "Nifty 100": "regime_nifty100" };
export const rgDefaults = () => ({ uni: "Nifty 500", span: "1 year", hz: "20", data: {}, err: {}, loading: {} });

export async function loadRegime() {
  const R = S.rg, f = UNIS[R.uni];
  if (R.data[f] || R.loading[f]) return;
  R.loading[f] = true; R.err[f] = "";
  try { R.data[f] = await api("/api/market?f=" + f); } catch (e) { if (e.message !== "signed out") R.err[f] = e.message; }
  R.loading[f] = false; paint();
}
const col = (v) => (v == null || v === 0 ? "" : v > 0 ? "up" : "dn");
const pill = (s) => `<span class="pill" style="--c:${STATE_COLOR[s]}">${esc(s)}</span>`;
const seg = (key, opts, cur, label) => `<div class="seg" role="group" aria-label="${label}">${opts.map(([v, t]) => `<button class="${String(v) === String(cur) ? "on" : ""}" data-act="rgset" data-k="${key}" data-v="${v}">${t}</button>`).join("")}</div>`;
const days = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / 864e5);

function forwardCard(d, hz) {
  const ft = (d.forward[hz] || []).filter((r) => r.days > 0);
  if (!ft.length) return "";
  const rows = ft.map((r) => `<tr><td>${pill(r.state)}</td><td class="r">${num(r.share, 0)}%</td><td class="r ${col(r.nifty_avg)}">${sgn(r.nifty_avg, 2)}%</td><td class="r">${num(r.nifty_up, 0)}%</td>
    <td class="r ${col(r.ew_avg)}">${sgn(r.ew_avg, 2)}%</td><td class="r">${num(r.ew_up, 0)}%</td><td class="r dn">${sgn(r.worst_avg, 1)}%</td></tr>`).join("");
  const by = Object.fromEntries(ft.map((r) => [r.state, r])), a = by["Confirmed uptrend"], b = by.Correction;
  const line = a && b ? `<div class="note">Over the past ${num(d.years, 0)} years, after a Confirmed uptrend reading the average stock moved <b class="${col(a.ew_avg)}">${sgn(a.ew_avg, 1)}%</b> over the next ${hz} sessions and rose ${num(a.ew_up, 0)}% of the time.
    After a Correction reading it moved <b class="${col(b.ew_avg)}">${sgn(b.ew_avg, 1)}%</b> and rose ${num(b.ew_up, 0)}% of the time.
    ${a.ew_avg > b.ew_avg + 1 ? "The states separate good and bad times to buy, so the reading is worth following." : "The gap between states is small in this universe, so treat the reading as context, not a hard rule."}</div>` : "";
  return `<div class="card tw"><table class="tb"><thead><tr><th>State</th><th class="r">Time in state</th><th class="r">NIFTY 50 avg</th><th class="r">NIFTY up</th><th class="r">Average stock</th><th class="r">Average stock up</th><th class="r">Avg worst dip</th></tr></thead><tbody>${rows}</tbody></table></div>${line}`;
}

export function page() {
  const R = S.rg, f = UNIS[R.uni], d = R.data[f];
  resetCharts();
  const ctl = `<div class="rg-ctl">${seg("uni", Object.keys(UNIS).map((u) => [u, u]), R.uni, "Breadth universe")}${seg("span", [["1 year", "1 year"], ["3 years", "3 years"], ["All", "All"]], R.span, "Chart period")}</div>`;
  const head = `<div class="hd"><div><h1>Market regime</h1><div class="sub">Should you be buying breakouts aggressively, carefully, or not at all?</div></div></div>`;
  if (!d) return `<div>${head}${ctl}<div class="card"><div class="${R.err[f] ? "empty" : "note"}">${R.err[f] ? `<b>Regime is not available yet</b>${esc(R.err[f])}` : "Loading the regime…"}</div></div></div>`;
  const r = d.row, state = d.state, c = STATE_COLOR[state], s = d.series, n = s.d.length;
  const age = days(d.asof, todayStr()), warn = [];
  if (age > 4) warn.push(`The latest candle is from ${nice(d.asof)} (${age} days ago). The nightly job may have failed: check the “Market data” run in GitHub Actions, and do not act on this reading until it updates.`);
  if (d.n < 80 && R.uni !== "Nifty 100") warn.push(`Only ${d.n} stocks loaded, so breadth is thin. The NSE list or Yahoo may have failed on the last run.`);
  const hero = `<div class="card rg-hero" style="--c:${c}"><div><span class="rg-st"><i></i>Market regime · ${new Date(d.asof + "T00:00:00").toLocaleDateString("en-IN", { weekday: "short", day: "2-digit", month: "short", year: "numeric" })}</span>
      <h2>${esc(state)}</h2><p>${esc(d.guide)}</p>
      <p class="mu">In this state since ${nice(d.since)} (${d.days_in} session${d.days_in === 1 ? "" : "s"}). NIFTY 50 ${num(r.close, 0)} <span class="${col(r.ret)}">${sgn(r.ret * 100, 2)}%</span>.</p></div>
    <div><div class="rg-big">${num(r.score, 0)}<small> / 100 regime score</small></div><div class="rg-gauge"><i style="left:${Math.max(0, Math.min(100, r.score)).toFixed(1)}%"></i></div>
      <div class="rg-gl"><span>Correction</span><span>Under pressure</span><span>Uptrend</span></div><div class="mini">Suggested exposure <b>${d.exposure}%</b> of your normal size</div></div></div>`;
  const kp = (l, v, sub, cl = "") => `<div><span>${l}</span><b class="${cl}">${v}</b><small>${sub}</small></div>`;
  const kpis = `<div class="card kp kp6">${[
    kp("Above 50 EMA", num(r.pct50, 0) + "%", `200 DMA ${num(r.pct200, 0)}% · 21 EMA ${num(r.pct20, 0)}%`),
    kp("New highs / lows", `<span class="up">${num(r.nh, 0)}</span> / <span class="dn">${num(r.nl, 0)}</span>`, "52-week, today"),
    kp("Distribution days", num(r.dd_count, 0), "last 25 sessions", r.dd_count <= 2 ? "up" : r.dd_count <= 4 ? "yl" : "dn"),
    kp("India VIX", r.vix == null ? "—" : num(r.vix, 1), r.vix_pct == null ? "" : `${num(r.vix_pct, 0)}th percentile of 1 year`),
    kp("Advancers / decliners", `<span class="up">${num(r.adv, 0)}</span> / <span class="dn">${num(r.dec, 0)}</span>`, `McClellan ${sgn(r.mcclellan, 0)}`),
    kp("From 3-month high", sgn(r.dd_from_hi * 100, 1) + "%", "NIFTY 50")].join("")}</div>`;
  const why = d.reasons.map(([t, x]) => `<div class="rg-rs" style="--c:${TONE[t]}"><i></i><span>${esc(x)}</span></div>`).join("");
  const comps = Object.entries(d.weights).map(([k, w]) => { const v = r["c_" + k], cc = v >= 65 ? GREEN : v >= 40 ? YELLOW : RED;
    return `<div class="rg-cp"><div class="n"><span>${COMP[k][0]}</span><em>${num(v, 0)} · ${Math.round(w * 100)}%</em></div><div class="rg-bar"><i style="width:${Math.max(0, Math.min(100, v)).toFixed(0)}%;background:${cc}"></i></div><div class="d">${COMP[k][1]}</div></div>`; }).join("");
  // charts over the chosen period
  const take = R.span === "1 year" ? 252 : R.span === "3 years" ? 756 : n, a0 = Math.max(0, n - take), sl = (k) => s[k].slice(a0), dates = s.d.slice(a0), st = s.state.slice(a0);
  const bands = bandsFrom(st, (i) => STATE_COLOR[d.states[i]]), stName = (i) => d.states[st[i]];
  const extra = (i) => `<span>${pill(stName(i))}</span>`;
  const nifty = lineChart(dates, [{ n: "NIFTY 50", c: "#F2F2F5", v: sl("close"), w: 1.6 }, { n: "50 EMA", c: AMBER, v: sl("ema50"), w: 1.1 }, { n: "200 DMA", c: PURPLE, v: sl("sma200"), w: 1.1 }],
    { h: 280, bands, log: R.span === "All", fmt: (v) => num(v, 0), extra });
  const br = lineChart(dates, [{ n: "Above 50 EMA", c: BLUE, v: sl("pct50"), w: 1.5 }, { n: "Above 200 DMA", c: GREEN, v: sl("pct200"), w: 1.3 }], { h: 190, hlines: [[70, GREEN], [30, RED]], fmt: (v) => num(v, 0) + "%" });
  const sc = lineChart(dates, [{ n: "Regime score", c: AMBER, v: sl("score"), w: 1.5 }], { h: 190, bands, hlines: [[65, GREEN], [45, RED]], fmt: (v) => num(v, 0), extra });
  const nh = sl("nh"), nl = sl("nl"), diff = nh.map((x, i) => (x == null || nl[i] == null ? null : x - nl[i]));
  const hl = lineChart(dates, [{ n: "Highs minus lows", c: GREEN, v: diff }], { h: 150, bars: true, barColor: (v) => (v > 0 ? GREEN : RED), fmt: (v) => num(v, 0) });
  const mc = lineChart(dates, [{ n: "McClellan", c: CYAN, v: sl("mcclellan"), w: 1.3 }], { h: 150, hlines: [[0, "#777"]], fmt: (v) => num(v, 0) });
  const eps = d.episodes.map(([st2, a, b, nd, mv]) => `<tr><td>${pill(st2)}</td><td>${nice(a)}</td><td>${b === d.asof ? "today" : nice(b)}</td><td class="r">${nd}</td><td class="r ${col(mv)}">${sgn(mv, 1)}%</td></tr>`).join("");
  const card = (t, sub, body) => `<div><h2>${t}${sub ? ` <span>${sub}</span>` : ""}</h2><div class="card pad">${body}</div></div>`;
  return `<div class="rg">${head}${ctl}${warn.map((w) => `<div class="card warn"><div class="note">${esc(w)}</div></div>`).join("")}${hero}${kpis}
    <div class="pf-2">${card("Why", "today's reading in plain words", why)}${card("Components", "score · weight", `<div class="rg-comp">${comps}</div>`)}</div>
    ${card("NIFTY 50 coloured by regime", R.span.toLowerCase(), nifty + legend([["NIFTY 50", "#F2F2F5"], ["50 EMA", AMBER], ["200 DMA", PURPLE], ...d.states.map((x) => [x, STATE_COLOR[x], true])]))}
    <div class="pf-2">${card("Breadth", "share of stocks in uptrends", br)}${card("Regime score", "65+ uptrend · under 45 weak", sc)}</div>
    <div class="pf-2">${card("New 52-week highs minus lows", "daily", hl)}${card("McClellan oscillator", "above 0 = advancers gaining", mc)}</div>
    <h2>Does it work? What happened next <span>after each reading</span></h2>${seg("hz", [["20", "Next 20 sessions"], ["10", "Next 10 sessions"], ["60", "Next 60 sessions"]], R.hz, "Horizon")}${forwardCard(d, R.hz)}
    <div class="pf-2"><div><h2>Recent state changes <span>NIFTY 50 move during each</span></h2><div class="card tw"><table class="tb"><thead><tr><th>State</th><th>From</th><th>To</th><th class="r">Sessions</th><th class="r">NIFTY</th></tr></thead><tbody>${eps}</tbody></table></div></div>
      ${card("Distribution and follow-through", "", `<div class="rg-txt"><b>Distribution days in the last 25 sessions:</b> ${d.dd_days.map((x) => nice(x).replace(/ \d{4}$/, "")).join(", ") || "none"}.<br>
        A distribution day is NIFTY 50 falling 0.2% or more on higher volume than the day before: big funds selling. Five or six in 25 sessions has preceded most corrections.<br><br>
        <b>Recent follow-through days:</b> ${d.ftd.map(nice).join(", ") || "none"}.<br>A follow-through day is a strong up day (+1.2% or more) on higher volume on day 4 or later of a rally attempt. It is how a new uptrend gets confirmed; most big rallies started with one, but not every one works.</div>`)}</div>
    <div class="note" style="padding:16px 2px">Breadth from ${d.n} stocks (${esc(d.note)}${d.missing ? `, ${d.missing} returned no data` : ""}). Prices: Yahoo Finance daily, adjusted for splits and bonuses; volume for distribution days is the universe's total turnover because Yahoo's index volume is unreliable.
      Built ${esc(String(d.built).slice(0, 16).replace("T", " "))} IST by the nightly “Market data” job with the same engine as the classic app. Every reading uses only data available at that day's close. A reading aid, not investment advice.</div></div>`;
}

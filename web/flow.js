// Arka Trades · Money flow. Shows the files the nightly "Market data" job writes with the classic app's rotation engine
// (money_flow.py + arka_flow), so ranks, scores, the model portfolio and its test are the same numbers as Streamlit.
// Eight views as on the classic page. Data options (sectors, benchmark, history, liquidity floor) and model settings (sectors to hold,
// review every, regime filter, round-trip cost) switch instantly: every combination the classic page offers is computed each night.
import { S, api, paint, esc, num, nice } from "./app.js";
import { lineChart, resetCharts, legend } from "./charts.js";
import * as FPI from "./fpi.js";
import { dataBehind } from "./home.js";

const GREEN = "#30D158", RED = "#FF453A", AMBER = "#FF9F0A", CYAN = "#5AC8FA", YELLOW = "#FFD60A", PURPLE = "#BF5AF2", T2 = "#A3A3A3", T3 = "#6B6B6B";
const QUAD_C = { Leading: GREEN, Weakening: YELLOW, Lagging: RED, Improving: CYAN, "": T3 };
const ACT_C = { "Rotate in": GREEN, Hold: "#8AD66B", "Rotate out": RED, "Watch: improving": CYAN, "": T3 };
const REG_C = { "Risk-on": GREEN, Neutral: YELLOW, "Risk-off": RED, Stress: RED, Unknown: T3 };
const STATE_C = { LEADERSHIP: GREEN, "MATURE LEADERSHIP": YELLOW, "PARTICIPATION CONFIRMATION": "#8AD66B", "EARLY IMPROVEMENT": CYAN, RECOVERY: PURPLE, DETERIORATION: RED };
const BOARD = ["EARLY IMPROVEMENT", "PARTICIPATION CONFIRMATION", "LEADERSHIP", "MATURE LEADERSHIP", "RECOVERY", "DETERIORATION"];
const STATE_HELP = { "EARLY IMPROVEMENT": "Behind over 60 sessions; relative strength has turned up.", "PARTICIPATION CONFIRMATION": "Turning up with more stocks joining.",
  LEADERSHIP: "Ahead over 60 sessions, above its 50-EMA.", "MATURE LEADERSHIP": "Still leading, but extended or slowing.", RECOVERY: "Price trend damaged; relative strength stabilising.",
  DETERIORATION: "Relative strength falling, confirmed by breadth or price." };
const REG_TXT = { "Risk-on": "Benchmark above its 200-session average and most stocks above theirs. All model slots are filled.",
  Neutral: "Mixed: benchmark and market breadth disagree. All slots are filled, trend filter still applies.",
  "Risk-off": "Benchmark below its 200-session average and fewer than 40% of stocks above theirs. Half the slots stay in cash.",
  Stress: "Benchmark 10%+ below its 1-year high with volatility in the top 20% of the year: the 'panic' state where momentum strategies historically lose most (Daniel & Moskowitz). Half the slots stay in cash.",
  Unknown: "Not enough history to judge the market trend yet." };
const ACT_WHY = { "Rotate in": "it is in the top slots today and was not held", "Rotate out": "it fell below the rank buffer or failed the trend filter",
  Hold: "it is still inside the rank buffer and above trend", "Watch: improving": "it is in the Improving quadrant and climbed the ranking, but is not in the top slots yet" };
const IC_MEANING = { score: "the full rotation score", momentum: "risk-adjusted relative momentum", rotation: "RS-Momentum (rotation speed)", breadth: "breadth level and change",
  consistency: "steady vs jumpy relative gains", activity: "up-day turnover share", momentum_only: "baseline: plain 6-month relative return" };
const SOURCES = [["Moskowitz & Grinblatt (1999), Do Industries Explain Momentum?, Journal of Finance", "https://doi.org/10.1111/0022-1082.00146"],
  ["NSE Indices, Nifty200 Momentum 30: Normalised Momentum Score (6 and 12-month return / volatility)", "https://niftyindices.com/indices/equity/strategy-indices/nifty200-momentum-30"],
  ["de Kempenaer, Relative Rotation Graphs (JdK RS-Ratio / RS-Momentum; Bloomberg RRG <GO>)", "https://chartschool.stockcharts.com/table-of-contents/chart-analysis/chart-types/relative-rotation-graphs-rrg-charts"],
  ["Da, Gurun & Warachka (2014), Frog in the Pan: Continuous Information and Momentum, Review of Financial Studies", "https://doi.org/10.1093/rfs/hhu003"],
  ["Faber (2007), A Quantitative Approach to Tactical Asset Allocation (10-month trend rule)", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=962461"],
  ["Moskowitz, Ooi & Pedersen (2012), Time Series Momentum, Journal of Financial Economics", "https://www.aqr.com/Insights/Datasets/Time-Series-Momentum-Original-Paper-Data"],
  ["Daniel & Moskowitz (2016), Momentum Crashes, Journal of Financial Economics", "https://www.nber.org/papers/w20439"]];
export const VIEWS = ["Where to be", "Rotation map", "Sector", "Does it work?", "Breadth & activity", "Rule states", "FPI flows", "Method & data"];
export const flowDefaults = () => ({ view: "Where to be", d: null, err: "", sec: null, secErr: "", fpi: null, fpiErr: "", n: null, every: 21, regime: true, pick: null, show: "top", tail: 10, fpiN: 24, sort: {},
  key: "", cost: null, idx: null, byKey: {}, secByKey: {}, mods: {}, modErr: "" });

export async function loadFlow(what = "main") {
  const F = S.mf;
  const get = async (f, k, ek) => { if (F[k] || F["_" + k]) return; F["_" + k] = true; try { F[k] = await api("/api/market?f=" + f); } catch (e) { if (e.message !== "signed out") F[ek] = e.message; } F["_" + k] = false; paint(); };
  const sfx = F.key ? "@" + F.key : "";                      // a data choice other than the default: its own files (flow@<key>.json)
  if (what === "main") {
    if (!F._idx) { F._idx = true; api("/api/market?f=flow_index").then((x) => { F.idx = x; paint(); }).catch(() => { /* an older job: the default choice only */ }); }
    await get("flow" + sfx, "d", "err"); if (F.d) F.byKey[F.key] = F.d; return;
  }
  if (what === "sectors") { await get("flow_sectors" + sfx, "sec", "secErr"); if (F.sec) F.secByKey[F.key] = F.sec; return; }
  if (what === "models") {                                   // the model grid at another round-trip cost (flow_models@<key>@<cost>.json)
    const k = (F.key || (F.idx && F.idx.default)) + "@" + F.cost; if (F.mods[k] || F["_m" + k]) return; F["_m" + k] = true; F.modErr = "";
    try { F.mods[k] = await api("/api/market?f=" + encodeURIComponent("flow_models@" + k)); } catch (e) { if (e.message !== "signed out") F.modErr = e.message; }
    F["_m" + k] = false; paint(); return;
  }
  if (what === "fpi") {                                      // the nightly file first; if the job could not reach NSDL, read the reports live (4 at a time)
    if (F._fpiRun) return; F._fpiRun = true;
    await get("flow_fpi", "fpi", "fpiErr");
    if (F.fpi) { F._fpiRun = false; return; }
    F._live = true; F.fpiErr = ""; F.fpiDone = 0; paint();
    const x = await FPI.loadLive(api, 48, () => { F.fpiDone++; if (F.fpiDone % 6 === 0) paint(); });
    F.fpi = x; if (!x) F.fpiErr = "no NSDL report could be read (0 of 48), from the nightly job or live"; F._live = false; F._fpiRun = false; paint();
  }
}
// ── formats (money_flow.py) ──
const ok = (v) => v != null && isFinite(v);
const f = (v, d = 1, sign = true, suf = "") => (!ok(v) ? "n/a" : (sign && v > 0 ? "+" : v < 0 ? "−" : "") + num(Math.abs(v), d) + suf);
const pc = (v, d = 1, suf = "%") => (!ok(v) ? '<span class="mu">n/a</span>' : `<span class="${v > 0 ? "up" : v < 0 ? "dn" : ""}">${f(v, d, true, suf)}</span>`);
const pill = (t, c) => `<span class="mf-pill" style="--c:${c}">${esc(t)}</span>`;
const bar = (v) => { if (!ok(v)) return '<span class="mu">n/a</span>'; const w = Math.max(0, Math.min(100, v)), c = w >= 70 ? GREEN : w >= 55 ? "#8AD66B" : w >= 40 ? AMBER : RED;
  return `<span class="mf-bar" style="--w:${w.toFixed(0)}%;--c:${c}"><i></i>${num(v, 0)}</span>`; };
const h = (t, sub = "") => `<h2>${esc(t)}${sub ? ` <span>${esc(sub)}</span>` : ""}</h2>`;
const note = (t) => `<div class="card"><div class="note">${t}</div></div>`;
const tbl = (head, rows, cls = "") => `<div class="card tw"><table class="tb ${cls}"><thead><tr>${head.map(([t, r]) => `<th class="${r ? "r" : ""}">${t}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
const kpis = (items, n = 3) => `<div class="card kp kp${n}">${items.map(([a, b, c, cl = ""]) => `<div><span>${a}</span><b class="${cl}">${b}</b><small>${c}</small></div>`).join("")}</div>`;
const seg = (act, opts, cur) => `<div class="seg">${opts.map(([v, t]) => `<button class="${String(v) === String(cur) ? "on" : ""}" data-act="${act}" data-v="${esc(v)}">${esc(t)}</button>`).join("")}</div>`;

const costOf = (d) => (S.mf.cost == null ? d.cost : S.mf.cost);
function modelsOf(d) {                                       // the model grid at the chosen cost (the main file holds the default cost)
  const F = S.mf, c = costOf(d); if (Math.abs(c - d.cost) < 1e-9) return d.models;
  const k = (F.key || (F.idx && F.idx.default)) + "@" + c; if (!F.mods[k]) loadFlow("models"); return F.mods[k] || null;
}
function model(d) {                                          // the model portfolio for the chosen settings
  const F = S.mf, n = F.n || d.default.n, ms = modelsOf(d) || {};
  return { n, every: F.every, regime: F.regime, cost: costOf(d), m: ms[`${n}|${F.every}|${F.regime ? 1 : 0}`], other: ms[`${n}|${F.every}|${F.regime ? 0 : 1}`] };
}
// ── data choices (money_flow.render_money_flow: Sectors, Compare against, History, Min value / day): each is built nightly as its own files ──
const KIND_HELP = { "Arka groups": "Arka's ~50 rotation groups (sector_map.py), equal-weight baskets of today's members. Breadth available for every group.",
  "NSE indices": "Official NSE sector indices. No survivorship bias, but fewer sectors; breadth needs each index's constituent file in data/." };
const choiceOf = (d) => d.choice || { kind: d.kind, bench: d.bench, period: d.period, min_turn: d.min_turn, key: "" };
export function setData(field, v) {                         // pick the built choice that keeps as much of the current one as possible
  const F = S.mf, I = F.idx, d = F.d; if (!I || !d) return;
  const cur = { ...choiceOf(d), [field]: field === "min_turn" ? +v : v }, fields = ["kind", "bench", "period", "min_turn"];
  let best = null, bs = -1;
  for (const c of I.choices) { if (String(c[field]) !== String(cur[field])) continue; const sc = fields.filter((k) => String(c[k]) === String(cur[k])).length; if (sc > bs) { bs = sc; best = c; } }
  if (!best) return;
  const key = best.key === I.default ? "" : best.key; if (key === F.key && F.d) return paint();
  F.key = key; F.d = F.byKey[key] || null; F.sec = F.secByKey[key] || null; F.err = ""; F.secErr = ""; F.n = null;
  if (F.cost != null && !best.costs.some((c) => Math.abs(c - F.cost) < 1e-9)) F.cost = null;
  paint(); loadFlow("main");
}
function dataCard(d) {
  const F = S.mf, I = F.idx, ch = choiceOf(d), turnTxt = (t) => `₹${String(t).replace(/\.0$/, "")} Cr`;
  const sum = `${esc(ch.kind)} · vs ${esc(ch.bench)} · ${esc(ch.period)} · min ${turnTxt(ch.min_turn)}/day`;
  if (!I) return `<details class="card mf-set"><summary>Data options <span>${sum}</span></summary><div class="pad"><div class="note">The other data choices appear here after the next nightly “Market data” run.</div></div></details>`;
  const has = (field, v) => I.choices.some((c) => String(c[field]) === String(v)), row = (label, field, opts, fmt = (x) => x) =>
    `<div class="mf-row"><span>${label}</span><div class="seg">${opts.map((v) => `<button class="${String(v) === String(ch[field]) ? "on" : ""}" data-act="mfdata-${field}" data-v="${esc(v)}"${has(field, v) ? "" : ' disabled title="Not built in the last run"'}>${esc(fmt(v))}</button>`).join("")}</div></div>`;
  const miss = (I.failed || []).length ? `<div class="note">Not built in the last run: ${I.failed.map((x) => esc(x[0])).join(", ")}.</div>` : "";
  return `<details class="card mf-set"${F.dataOpen ? " open" : ""} data-mfd="1"><summary>Data options <span>${sum}</span></summary><div class="pad">
    ${row("Sectors", "kind", I.kinds)}<div class="note" style="padding:0 0 8px">${esc(KIND_HELP[ch.kind] || "")}</div>${row("Compare against", "bench", I.benches)}${row("History", "period", I.periods)}${row("Min value / day", "min_turn", I.min_turn, turnTxt)}
    <div class="note" style="padding:6px 0 0">Every sector, history length and liquidity floor the classic page offers is built each night. A different sector set or benchmark uses 5 years and ₹0.5 Cr; a different history or floor uses Arka groups vs NIFTY 500. 5 years of history gives the model test a far better sample.</div>${miss}</div></details>`;
}
function snapOf(d, M) {                                      // the classic snapshot: model-independent columns + this model's actions
  const act = {}; for (const [a, list] of Object.entries(M.m.actions || {})) for (const s of list) act[s] = a;
  const held = new Set(M.m.held || []), target = new Set(M.m.target || []);
  return d.sectors.map((r) => ({ ...r, action: act[r.sector] || "", held: held.has(r.sector), target: target.has(r.sector) }));
}
const slotsNow = (d, M) => (M.regime ? Math.max(1, Math.round(M.n * (d.regime.slots_share[d.regime.label] ?? 1))) : M.n);

function status(d, M) {
  const rg = d.regime.label, q = d.quality;
  const chips = [`As of <b>${esc(nice(d.asof))}</b> close`, `<b>${d.n_sectors}</b> sectors · ${esc(d.kind)}`, `vs <b>${esc(d.bench)}</b>`, `${esc(d.period)} history`,
    `<i style="background:${REG_C[rg] || T3}"></i>Market <b>${esc(rg)}</b>`, `Model: top <b>${M.n}</b>, review every <b>${M.every}</b> sessions`, `<b>${q.used}</b> stocks used`];
  let out = `<div class="mf-status">${chips.map((c) => `<span class="mf-chip">${c}</span>`).join("")}</div>`;
  const lag = dataBehind(String(d.asof).slice(0, 10));      // checked now, against the NSE holiday list (the file's own check knows weekends only)
  if (lag.n) out += `<div class="card warn"><div class="note">Latest data is ${lag.n} session(s) behind ${esc(nice(lag.due))}, the last session that should be published by now. Check the “Market data” run in GitHub Actions.</div></div>`;
  if (q.n_failures) out += `<div class="note">${q.n_failures} ticker(s) returned no data from Yahoo; affected sectors are marked in Method & data.</div>`;
  return out;
}
function settings(d, M) {
  const F = S.mf, I = F.idx, ch = I ? I.choices.find((c) => c.key === (F.key || I.default)) : null, costs = ch ? ch.costs : [d.cost];
  return `<details class="card mf-set"${F.setOpen ? " open" : ""} data-mfs="1"><summary>Model settings <span>top ${M.n} · every ${M.every} sessions · regime filter ${M.regime ? "on" : "off"} · costs ${num(M.cost, 2)}%</span></summary><div class="pad">
    <div class="mf-row"><span>Round-trip cost</span>${seg("mfset-cost", costs.map((x) => [x, num(x, 2) + "%" + (Math.abs(x - d.cost) < 1e-9 ? " (default)" : "")]), M.cost)}</div>
    <div class="mf-row"><span>Sectors to hold</span>${seg("mfset-n", d.options.n.map((x) => [x, x]), M.n)}</div>
    <div class="mf-row"><span>Review every</span>${seg("mfset-every", d.options.every.map((x) => [x, x + " sessions" + (x === 21 ? " (monthly)" : x === 10 ? " (fortnightly)" : "")]), F.every)}</div>
    <div class="mf-row"><span>Regime filter</span>${seg("mfset-regime", [[1, "On"], [0, "Off"]], F.regime ? 1 : 0)}</div>
    <div class="note" style="padding:6px 0 0">Defaults: review monthly (the holding period used in the industry-momentum research) and hold roughly the top tenth of sectors. Changing a setting changes both the model and its test on Does it work?. Round-trip cost is brokerage + STT + stamp + slippage, both sides: check your contract notes.</div></div></details>`;
}
function card(r, bench) {
  const why = `Relative to ${esc(bench)}: ${pc(r.rel_63)} in 3 months, ${pc(r.rel_126)} in 6. ` + (ok(r.pct50) ? `${f(r.pct50, 0, false)}% of members above 50-EMA (${f(r.d50, 0)} pts in 20 sessions).` : "");
  const rc = r.rank_change_20, move = !ok(rc) || rc === 0 ? "" : rc > 0 ? ` · up ${rc}` : ` · down ${-rc}`;
  return `<button class="mf-card${r.held ? " held" : ""}" data-act="mfsec" data-v="${esc(r.sector)}"><div class="mf-top"><div><div class="nm">${esc(r.sector)}</div><div class="rk">Rank ${r.rank}${move} in 20 sessions</div></div><div class="sc">${num(r.rank_pct, 0)}<small>score</small></div></div>
    <div class="why">${why}</div><div class="chips">${pill(r.action || "—", ACT_C[r.action] || T3)}${pill(r.quadrant || "n/a", QUAD_C[r.quadrant] || T3)}${pill(r.trend_ok === 1 ? "Trend pass" : r.trend_ok === 0 ? "Trend fail" : "Trend n/a", r.trend_ok === 1 ? GREEN : r.trend_ok === 0 ? RED : T3)}</div></button>`;
}
function rankTable(snap) {
  return tbl([["#", 1], ["Sector"], ["Score"], ["Rotation map"], ["vs bench 1M", 1], ["3M", 1], ["6M", 1], ["Above 50-EMA", 1], ["Δ 20 sessions", 1], ["Trend"], ["Model"]], snap.map((r) => {
    const tr = r.trend_ok === 1 ? "Pass" : r.trend_ok === 0 ? "Fail" : "n/a";
    return `<tr class="${r.held ? "mf-held" : ""}" data-act="mfsec" data-v="${esc(r.sector)}"><td class="r">${ok(r.rank) ? r.rank : "—"}</td><td><b>${esc(r.sector)}</b></td><td>${bar(r.rank_pct)}</td><td>${pill(r.quadrant || "n/a", QUAD_C[r.quadrant] || T3)}</td>
      <td class="r">${pc(r.rel_21)}</td><td class="r">${pc(r.rel_63)}</td><td class="r">${pc(r.rel_126)}</td><td class="r">${f(r.pct50, 0, false, "%")}</td><td class="r">${pc(r.d50, 0, "")}</td>
      <td class="${tr === "Pass" ? "up" : tr === "Fail" ? "dn" : ""}">${tr}</td><td>${r.action ? pill(r.action, ACT_C[r.action]) : ""}</td></tr>`; }), "stick mf-rank");
}
function regimeCard(d, M) {
  const r = d.regime, rg = r.label;
  const facts = [["Benchmark vs 200-session avg", f(r.bench_vs_sma, 1, true, "%"), r.above === 1 ? "above" : r.above === 0 ? "below" : "n/a"], ["Stocks above 200-EMA", f(r.breadth200, 0, false, "%"), "50%+ healthy · under 40% weak"],
    ["Volatility, 20 sessions", f(r.vol20, 0, false, "%"), `${f(r.vol_pct, 0, false)}th percentile of its year`], ["From 1-year high", f(r.drawdown, 1, true, "%"), "stress if 10%+ down and volatile"]];
  return `<div class="card mf-regime" style="--c:${REG_C[rg] || T3}"><div><span class="k">Market regime</span><div class="v">${esc(rg)}</div><div class="d">${esc(REG_TXT[rg] || "")}<br><b>Model slots now: ${slotsNow(d, M)} of ${M.n}</b>${M.regime ? "" : " (regime filter switched off)"}</div></div>
    <div class="mf-facts">${facts.map(([a, b, c]) => `<div><small>${a}</small><b>${b}</b><em>${c}</em></div>`).join("")}</div></div>`;
}
function viewNow(d, M, snap) {
  const m = M.m, target = snap.filter((r) => r.target);
  let out = regimeCard(d, M) + h("Model portfolio", `top ${M.n} by rotation score that pass the trend filter · equal weight · ` + (m.last_review ? "last review " + nice(m.last_review) : "no review yet"));
  out += target.length ? `<div class="mf-cards">${target.map((r) => card(r, d.bench)).join("")}</div>` : note("No sector passes the trend filter today: the model would hold cash.");
  const by = (a) => snap.filter((r) => r.action === a);
  const cols = [["Rotate in", GREEN, "New to the top slots, above trend.", by("Rotate in")], ["Rotate out", RED, "Fell below the rank buffer or the trend filter.", by("Rotate out")],
    ["Watch: improving", CYAN, "Improving quadrant and climbing the ranking, not in the slots yet. The early part of a rotation.", by("Watch: improving")]];
  if (cols.some((c) => c[3].length)) out += h("If the model were reviewed today", "the next scheduled review uses the same rules") + `<div class="mf-board">${cols.map(([t, c, p, list]) => `<div class="mf-col" style="--c:${c}"><h4>${t} · ${list.length}</h4><p>${p}</p>${
    list.slice(0, 6).map((r) => `<button class="it" data-act="mfsec" data-v="${esc(r.sector)}"><b>${esc(r.sector)}</b> · rank ${r.rank} · ${esc(r.quadrant)} · 3M ${pc(r.rel_63)}</button>`).join("") || '<div class="it">None.</div>'}</div>`).join("")}</div>`;
  if (m.ok && m.metrics) { const ic = ((d.ic[String(M.every)] || {}).ic || []).find((x) => x.factor === "score");
    out += note(`<b>On this history</b> (${m.sessions} sessions, walk-forward, costs ${num(M.cost, 2)}% round trip): the model returned <b>${f(m.metrics.cagr, 1, true, "%")}</b> a year vs <b>${f(m.bench_metrics.cagr, 1, true, "%")}</b> for ${esc(d.bench)}, worst fall ${f(m.metrics.max_dd, 1, false, "%")} vs ${f(m.bench_metrics.max_dd, 1, false, "%")}. Score IC ${f(ic && ic.mean_ic, 3)} (above +0.05 is useful, near 0 is no edge). Open <b>Does it work?</b> for the full test before acting on any of this.`); }
  out += h("All sectors", `ranked by rotation score · green edge = held by the model · vs ${d.bench}`) + `<div class="only-d">${rankTable(snap)}</div><div class="only-m mf-cards">${snap.slice(0, 20).map((r) => card(r, d.bench)).join("")}</div>
    <div class="only-m note">Top 20 shown on a phone. The full table is on the desktop layout and in the download.</div><div class="ctl"><span class="sp"></span><button class="btn sm" data-act="mfcsv">Download ranking (CSV)</button></div>`;
  return out;
}
function rrg(d, snap) {                                       // Relative Rotation Graph with tails
  const F = S.mf, tail = +F.tail, secs = F.show === "top" ? snap.slice(0, 15).map((r) => r.sector) : F.show === "model" ? (snap.filter((r) => r.target || r.action === "Watch: improving").map((r) => r.sector).length ? snap.filter((r) => r.target || r.action === "Watch: improving").map((r) => r.sector) : snap.slice(0, 8).map((r) => r.sector)) : snap.map((r) => r.sector);
  const pts = secs.map((s) => { const [x, y] = d.rrg.s[s] || [[], []]; const p = x.map((v, i) => [v, y[i]]).slice(-tail).filter(([a, b]) => ok(a) && ok(b)); return [s, p]; }).filter(([, p]) => p.length);
  const all = pts.flatMap(([, p]) => p); if (!all.length) return note("The rotation map needs more history.");
  const dx = Math.max(Math.max(...all.map(([x]) => Math.abs(x - 100))), 1.5) * 1.15, dy = Math.max(Math.max(...all.map(([, y]) => Math.abs(y - 100))), 1) * 1.15;
  const W = 1000, H = 640, X = (x) => ((x - (100 - dx)) / (2 * dx)) * W, Y = (y) => H - ((y - (100 - dy)) / (2 * dy)) * H;
  const quad = [[X(100), 0, W - X(100), Y(100), GREEN, "Leading", "end", 22], [X(100), Y(100), W - X(100), H - Y(100), YELLOW, "Weakening", "end", H - 12], [0, Y(100), X(100), H - Y(100), RED, "Lagging", "start", H - 12], [0, 0, X(100), Y(100), CYAN, "Improving", "start", 22]];
  const svg = `<svg viewBox="0 0 ${W} ${H}" class="mf-rrg" role="img" aria-label="Relative rotation graph">${quad.map(([x, y, w, hh, c, t, a, ty]) => `<rect x="${x}" y="${y}" width="${w}" height="${hh}" fill="${c}" opacity=".05"/><text x="${a === "end" ? W - 12 : 12}" y="${ty}" text-anchor="${a}" fill="${c}" font-size="20">${t}</text>`).join("")}
    <line x1="${X(100)}" x2="${X(100)}" y1="0" y2="${H}" stroke="#3a3a3e"/><line x1="0" x2="${W}" y1="${Y(100)}" y2="${Y(100)}" stroke="#3a3a3e"/>
    ${pts.map(([s, p]) => { const r = snap.find((x) => x.sector === s), c = QUAD_C[r.quadrant] || T3, [lx, ly] = p[p.length - 1];
      return `<g class="mf-dot" data-act="mfsec" data-v="${esc(s)}"><polyline points="${p.map(([x, y]) => `${X(x).toFixed(1)},${Y(y).toFixed(1)}`).join(" ")}" fill="none" stroke="${c}" stroke-width="2" opacity=".55"/>
        <circle cx="${X(lx).toFixed(1)}" cy="${Y(ly).toFixed(1)}" r="8" fill="${c}" stroke="#0B0B0C" stroke-width="2"><title>${esc(s)} · RS-Ratio ${num(lx, 2)} · RS-Momentum ${num(ly, 2)}</title></circle>
        <text x="${X(lx).toFixed(1)}" y="${(Y(ly) - 14).toFixed(1)}" text-anchor="middle" fill="#E4E4E7" font-size="13" stroke="#0B0B0C" stroke-width="4" paint-order="stroke">${esc(s.length > 22 ? s.slice(0, 21) + "…" : s)}</text></g>`; }).join("")}</svg>`;
  return `<div class="card pad">${svg}<div class="mf-ax"><span>← weaker than ${esc(d.bench)}</span><span>RS-Ratio (relative strength) →</span></div></div>`;
}
function viewMap(d, snap) {
  const F = S.mf;
  let out = `<div class="rg-ctl">${seg("mfshow", [["top", "Top 15 by score"], ["model", "Model holdings + watch"], ["all", "All sectors"]], F.show)}${seg("mftail", [[5, "last 5 sessions"], [10, "last 10"], [20, "last 20"]], F.tail)}</div>`;
  out += note("<b>How to read it.</b> Right of 100 = outperforming the benchmark (RS-Ratio). Above 100 = that outperformance is accelerating (RS-Momentum). Sectors usually turn clockwise: <b style='color:#5AC8FA'>Improving</b> → <b style='color:#30D158'>Leading</b> → <b style='color:#FFD60A'>Weakening</b> → <b style='color:#FF453A'>Lagging</b>. The best time to be in a sector is the move from Improving into Leading; the time to leave is when it rolls into Weakening and its rank falls. The map shows direction; the score and trend filter decide. Tap a dot to open that sector.") + rrg(d, snap);
  const moved = snap.filter((r) => r.quadrant !== r.quadrant_10_ago && r.quadrant_10_ago && r.quadrant);
  if (moved.length) out += h("Quadrant changes in the last 10 sessions") + tbl([["Rank", 1], ["Sector"], ["From"], ["To"], ["Score"]], moved.map((r) => `<tr data-act="mfsec" data-v="${esc(r.sector)}"><td class="r">${r.rank}</td><td><b>${esc(r.sector)}</b></td><td>${pill(r.quadrant_10_ago, QUAD_C[r.quadrant_10_ago] || T3)}</td><td>${pill(r.quadrant, QUAD_C[r.quadrant] || T3)}</td><td>${bar(r.rank_pct)}</td></tr>`));
  return out;
}
function viewSector(d, snap) {
  const F = S.mf, names = snap.map((r) => r.sector), pick = names.includes(F.pick) ? F.pick : names[0], r = snap.find((x) => x.sector === pick);
  const why = [...r.why]; if (r.action) why.push(`Model: ${r.action.toUpperCase()}, because ${ACT_WHY[r.action]} (market regime: ${d.regime.label}).`);
  const contrib = Object.keys(d.weights).map((k) => { const z = r[k], v = r.contrib[k], lab = d.labels[k];
    if (!ok(z)) return `<div class="r"><span>${esc(lab)}</span><div class="tr"><s></s></div><b class="mu">n/a</b></div>`;
    const w = Math.min(50, (Math.abs(v) / 1.5) * 50); return `<div class="r"><span>${esc(lab)} <em>· z ${f(z, 2)}</em></span><div class="tr"><s></s><i style="${v >= 0 ? "left:50%" : "right:50%"};width:${w.toFixed(0)}%;background:${v >= 0 ? GREEN : RED}"></i></div><b>${f(v, 2)}</b></div>`; }).join("");
  let out = `<label class="f mf-pick"><span>Sector</span><select data-mf="pick">${snap.map((x) => `<option value="${esc(x.sector)}"${x.sector === pick ? " selected" : ""}>${esc(x.sector)} · rank ${x.rank}</option>`).join("")}</select></label>
    <div class="pf-2"><div>${card(r, d.bench)}${h("Why it ranks here", "every sentence is computed from the numbers")}<div class="card pad"><ul class="mf-why">${why.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div></div>
    <div>${h("Score breakdown", `weight × z-score · ${r.n_parts || 0} of 5 components available`)}<div class="card pad"><div class="mf-contrib">${contrib}</div><div class="note" style="padding:10px 0 0">Each bar is that component's contribution to the final score (weights: momentum 35%, rotation 20%, breadth 20%, consistency 15%, activity 10%). z = how many standard deviations the sector is above (+) or below (−) the average sector today.</div></div></div></div>`;
  const sd = S.mf.sec;
  if (!sd) { loadFlow("sectors"); return out + note(S.mf.secErr ? esc(S.mf.secErr) : "Loading this sector's history and members…"); }
  const x = sd.s[pick]; if (!x) return out + note("No history for this sector.");
  const c = (t, sub, body) => `<div>${h(t, sub)}<div class="card pad">${body}</div></div>`;
  out += `<div class="pf-2">${c("Price vs benchmark", "both rebased to 100 one year ago", lineChart(sd.d252, [{ n: pick, c: AMBER, v: x.lv, w: 1.8 }, { n: d.bench, c: "#A1A1AA", v: x.bv, w: 1.3 }, { n: "200-session average", c: "#52525B", v: x.tv, w: 1.2 }], { h: 260, hlines: [[100, "#555"]], fmt: (v) => num(v, 1) }))}
    ${c("Score rank over time", "percentile among all sectors (100 = best)", lineChart(sd.d252, [{ n: "Score percentile", c: CYAN, v: x.rank, w: 1.5 }], { h: 260, hlines: [[50, "#555"]], fmt: (v) => num(v, 0) }))}</div>
    <div class="pf-2">${c("Rotation path", "RS-Ratio and RS-Momentum", lineChart(sd.d180, [{ n: "RS-Ratio", c: AMBER, v: x.rr, w: 1.5 }, { n: "RS-Momentum", c: CYAN, v: x.rm, w: 1.5 }], { h: 240, hlines: [[100, "#555"]], fmt: (v) => num(v, 1) }))}
    ${c("Breadth", "% of members above their 20 / 50 / 200-EMA", x.p50.length ? lineChart(sd.d250, [{ n: "20-EMA", c: CYAN, v: x.p20, w: 1.3 }, { n: "50-EMA", c: GREEN, v: x.p50, w: 1.4 }, { n: "200-EMA", c: PURPLE, v: x.p200, w: 1.3 }], { h: 240, fmt: (v) => num(v, 0) + "%" }) : '<div class="note">Breadth is not available for this sector.</div>')}</div>`;
  out += h("Strongest stocks inside it", `${x.n_members} members · sorted by 60-session return vs benchmark`);
  if (!x.stocks.length) return out + note("Constituents are NOT AVAILABLE for this sector.");
  const yn = (v) => (v === true ? '<span class="up">Yes</span>' : v === false ? '<span class="dn">No</span>' : '<span class="mu">n/a</span>');
  out += tbl([["Symbol"], ["Price ₹", 1], ["Return 20d %", 1], ["Return 60d %", 1], ["vs benchmark 60d pp", 1], ["vs sector 60d pp", 1], ["Above 50-EMA"], ["Above 200-EMA"], ["From 52w high %", 1], ["Volume × 20d", 1], ["Avg value ₹Cr", 1], ["Chart (descriptive)"]],
    x.stocks.map((s) => `<tr data-sym="${esc(s.symbol)}"><td><b>${esc(s.symbol)}</b></td><td class="r">${ok(s.price) ? num(s.price, 1) : "n/a"}</td><td class="r">${pc(s.ret_20)}</td><td class="r">${pc(s.ret_60)}</td><td class="r">${pc(s.rel_bench_60, 1, "")}</td><td class="r">${pc(s.rel_sector_60, 1, "")}</td>
      <td>${yn(s.above_50)}</td><td>${yn(s.above_200)}</td><td class="r">${ok(s.from_52w_high) ? f(s.from_52w_high, 1) : "n/a"}</td><td class="r">${ok(s.rel_volume) ? num(s.rel_volume, 1) : "n/a"}</td><td class="r">${ok(s.avg_value_cr) ? num(s.avg_value_cr, 1) : "n/a"}</td><td class="mf-wrap">${esc(s.setup || "")}</td></tr>`), "stick");
  return out + '<div class="note">A strong sector does not make every member a good trade: the leaders inside it are usually where the sector\'s move is concentrated. Tap a stock to open it.</div>';
}
function viewProof(d, M) {
  const m = M.m;
  let out = `<div class="card warn"><div class="note">${d.kind === "Arka groups" ? "Arka groups use TODAY's member lists for the whole history, so stocks that failed or were delisted are missing and past results look better than they were. " : ""}Sector baskets cannot be bought directly. This is evidence about the rules, not a promise about the future.</div></div>`;
  if (!m.ok) return out + note(`Not enough history to test the model (${esc(m.why || "")}).`);
  const mm = m.metrics, bm = m.bench_metrics, em = m.ew_metrics, yrs = m.sessions / 252;
  out += kpis([["Model, per year", f(mm.cagr, 1, true, "%"), `${f(mm.excess_cagr, 1)} pts vs ${esc(d.bench)}`, mm.cagr > 0 ? "up" : "dn"], ["Benchmark, per year", f(bm.cagr, 1, true, "%"), `all sectors equal-weight ${f(em.cagr, 1, true, "%")}`],
    ["Worst fall", f(mm.max_dd, 1, false, "%"), `benchmark ${f(bm.max_dd, 1, false, "%")}`, "dn"], ["Sharpe (no risk-free)", f(mm.sharpe, 2, false), `benchmark ${f(bm.sharpe, 2, false)}`],
    ["Months beating benchmark", f(mm.months_beat, 0, false, "%"), `information ratio ${f(mm.info_ratio, 2, false)}`], ["Turnover", f(m.turnover, 1, false, "×/yr"), `${m.reviews} reviews over ${num(yrs, 1)} years`]]);
  const c = m.curve, series = [{ n: "Model", c: AMBER, v: c.model, w: 2 }, { n: d.bench, c: "#D4D4D8", v: c.bench, w: 1.3 }, { n: "All sectors equal-weight", c: "#71717A", v: c.ew, w: 1.2 }];
  if (c.base) series.push({ n: "Plain 6-month momentum", c: CYAN, v: c.base, w: 1.2 });
  out += h("Growth of ₹100", `walk-forward · signal at the close, trade next close · ${num(M.cost, 2)}% round-trip cost on every change`) + `<div class="card pad">${lineChart(c.d, series, { h: 340, hlines: [[100, "#555"]], fmt: (v) => num(v, 0) })}</div>`;
  const alt = [["Model (these settings)", mm], ["Plain 6-month momentum, no filters", m.base_metrics], [`Model with regime filter ${M.regime ? "off" : "on"}`, M.other && M.other.ok ? M.other.metrics : null]].filter(([, x]) => x);
  out += tbl([["Variant"], ["Per year", 1], ["vs benchmark", 1], ["Worst fall", 1], ["Sharpe", 1], ["Months beating", 1]], alt.map(([n, x]) => `<tr><td><b>${esc(n)}</b></td><td class="r">${pc(x.cagr)}</td><td class="r">${pc(x.excess_cagr, 1, " pts")}</td><td class="r">${f(x.max_dd, 1, false, "%")}</td><td class="r">${f(x.sharpe, 2, false)}</td><td class="r">${f(x.months_beat, 0, false, "%")}</td></tr>`))
    + '<div class="note">If the plain 6-month ranking does as well as the model, the extra components add nothing on this data and the simpler rule is the better one.</div>';
  const t = d.ic[String(M.every)] || {}, hz = M.every, lab = (k) => d.labels[k] || (k === "score" ? "Rotation score" : "6-month momentum");
  if ((t.ic || []).length) out += h("Does the score predict the next move?", `rank correlation (IC) between each date's score and the NEXT ${hz} sessions' return vs benchmark`) + tbl([["Component"], ["What it measures"], ["Mean IC", 1], ["t-stat", 1], ["IC above 0", 1], ["Dates", 1]],
    t.ic.map((x) => `<tr><td><b>${esc(lab(x.factor))}</b></td><td class="mf-wrap">${esc(IC_MEANING[x.factor] || "")}</td><td class="r">${pc(x.mean_ic, 3, "")}</td><td class="r">${f(x.t_stat, 1, false)}</td><td class="r">${f(x.pct_positive, 0, false, "%")}</td><td class="r">${x.dates}</td></tr>`))
    + '<div class="note">IC +0.05 or more with a t-stat above 2 is a useful signal; around 0 means no measurable edge on this history. Sectors move together, so the t-stat overstates certainty a little.</div>';
  const q = t.quintiles || [];
  if (q.some((x) => ok(x.mean_excess))) { const mx = Math.max(0.01, ...q.map((x) => Math.abs(x.mean_excess || 0))), cols = [RED, "#E07A5F", "#71717A", "#8AD66B", GREEN];
    out += h("Top fifth vs bottom fifth", `average return vs benchmark over the next ${hz} sessions, by score quintile`) + `<div class="card pad"><div class="mf-q">${q.map((x, i) => `<div><b class="${(x.mean_excess || 0) >= 0 ? "up" : "dn"}">${f(x.mean_excess, 2, true, " pp")}</b><div class="col"><i style="height:${((Math.abs(x.mean_excess || 0) / mx) * 50).toFixed(1)}%;${(x.mean_excess || 0) >= 0 ? "bottom:50%" : "top:50%"};background:${cols[i]}"></i></div><span>${esc(x.quintile)}</span></div>`).join("")}</div></div>`
      + `<div class="note">Top minus bottom: ${f(t.spread_mean, 2)} pp per ${hz} sessions, positive on ${f(t.spread_beat, 0, false)}% of ${t.dates} dates. A useful score shows the bars rising from bottom to top.</div>`; }
  if (t.cut && m.stability) { const cut = new Date(t.cut + "T00:00:00").toLocaleDateString("en-IN", { month: "short", year: "numeric" });
    out += h("Stability", "the same test on the first 60% and the last 40% of the history") + tbl([["Period"], ["Per year", 1], ["vs benchmark", 1], ["Worst fall", 1], ["Score IC", 1]],
      [[`First 60% (to ${cut})`, m.stability[0], t.half_ic[0]], [`Last 40% (from ${cut})`, m.stability[1], t.half_ic[1]]].map(([l, x, ic]) => `<tr><td><b>${l}</b></td><td class="r">${pc(x && x.cagr)}</td><td class="r">${pc(x && x.excess_cagr, 1, " pts")}</td><td class="r">${f(x && x.max_dd, 1, false, "%")}</td><td class="r">${pc(ic, 3, "")}</td></tr>`))
      + '<div class="note">Nothing was tuned on either half. A result that flips sign between halves is not reliable.</div>'; }
  out += h("Review log", "the last 12 reviews: what the model held and what it changed") + tbl([["Review"], ["Regime"], ["Slots", 1], ["In"], ["Out"], ["Held after"]], m.log.map((x) => `<tr><td><b>${nice(x.date)}${x.pending ? " (pending)" : ""}</b></td><td>${esc(x.regime || "—")}</td><td class="r">${x.slots}</td><td class="up mf-wrap">${esc(x.in.join(", ") || "—")}</td><td class="dn mf-wrap">${esc(x.out.join(", ") || "—")}</td><td class="mf-wrap">${esc(x.holdings.join(", ") || "cash")}</td></tr>`));
  if (m.missing_bar_days) out += `<div class="note">${m.missing_bar_days} holding-days had no price bar and were counted as 0% for that day.</div>`;
  return out;
}
const COLS = { sector: "Sector", state: "State", n_bar: "Stocks", pct20: "% above 20-EMA", d20_stocks_10: "Stocks above 20-EMA, Δ 10d", pct50: "% above 50-EMA", d50_stocks_20: "Stocks above 50-EMA, Δ 20d", pct200: "% above 200-EMA",
  adv: "Advancing", dec: "Declining", new_high20: "New 20d highs", new_low20: "New 20d lows", breakout20: "Breakouts", coverage_status: "Data", traded_value_cr: "Traded value ₹Cr", rel_activity: "Activity × 20d",
  turnover_accel: "Turnover 5d / 20d", up_share5: "Up-close turnover 5d %", down_share5: "Down-close turnover 5d %", abs_5: "Return 5d %", rel_20: "vs bench 20d pp" };
function sortTable(d, keys, id, def) {                         // sortable table: tap a heading to sort by it
  const st = S.mf.sort[id] || [def, -1], [k, dir] = st;
  const rows = d.table.slice().sort((a, b) => { const x = a[k], y = b[k]; if (typeof x === "string" || typeof y === "string") return String(x || "").localeCompare(String(y || "")) * dir; return ((ok(x) ? x : -1e18) - (ok(y) ? y : -1e18)) * dir; });
  return `<div class="card tw"><table class="tb stick"><thead><tr>${keys.map((c) => `<th class="${c === "sector" || c === "state" || c === "coverage_status" ? "" : "r"} mf-sort" data-act="mfsort" data-id="${id}" data-v="${c}">${COLS[c]}${k === c ? (dir < 0 ? " ↓" : " ↑") : ""}</th>`).join("")}</tr></thead><tbody>${
    rows.map((r) => `<tr data-act="mfsec" data-v="${esc(r.sector)}">${keys.map((c) => { const v = r[c]; return typeof v === "string" || v == null ? `<td>${c === "sector" ? `<b>${esc(v || "")}</b>` : esc(v ?? "n/a")}</td>` : `<td class="r">${num(v, 1)}</td>`; }).join("")}</tr>`).join("")}</tbody></table></div>`;
}
function viewBreadth(d) {
  return h("Participation by sector", "level and direction are separate columns · tap a heading to sort") + sortTable(d, ["sector", "n_bar", "pct20", "d20_stocks_10", "pct50", "d50_stocks_20", "pct200", "adv", "dec", "new_high20", "new_low20", "breakout20", "coverage_status"], "br", "pct50")
    + h("Whole market", "% of all loaded stocks above their 20 / 50 / 200-EMA") + `<div class="card pad">${lineChart(d.market.d, [{ n: "20-EMA", c: CYAN, v: d.market.p20, w: 1.3 }, { n: "50-EMA", c: GREEN, v: d.market.p50, w: 1.4 }, { n: "200-EMA", c: PURPLE, v: d.market.p200, w: 1.3 }], { h: 260, hlines: [[50, "#555"]], fmt: (v) => num(v, 0) + "%" })}</div>`
    + h("Trading activity", `traded value basis: ${d.value_basis}`) + sortTable(d, ["sector", "traded_value_cr", "rel_activity", "turnover_accel", "up_share5", "down_share5", "abs_5", "rel_20"], "act", "rel_activity")
    + '<div class="note">Activity shows how much traded and on what kind of day. It does not show who bought or sold.</div>';
}
function viewStates(d) {
  const cols = BOARD.map((s) => { const rows = d.table.filter((r) => r.state === s).sort((a, b) => (b.pctile_20 ?? -1) - (a.pctile_20 ?? -1));
    return `<div class="mf-col" style="--c:${STATE_C[s]}"><h4>${s[0] + s.slice(1).toLowerCase()} · ${rows.length}</h4><p>${STATE_HELP[s]}</p>${rows.slice(0, 8).map((r) => `<button class="it" data-act="mfsec" data-v="${esc(r.sector)}"><b>${esc(r.sector)}</b> · RS 60d ${pc(r.rel_60, 1, " pp")} · ${f(r.pct50, 0, false)}% above 50-EMA · ${esc(r.confidence || "")}</button>`).join("") || '<div class="it">None.</div>'}</div>`; });
  return note("These are the <b>rule states</b>: a description of each sector from written rules (arka_flow/state.py). They are kept for reference. The rotation model on <b>Where to be</b> is what ranks sectors and is tested on <b>Does it work?</b>.")
    + `<div class="mf-board six">${cols.join("")}</div><details class="card mf-set"><summary>How states are decided</summary><pre class="mf-pre">${esc(d.states_doc)}</pre></details>`;
}
export function fpiRows(x, n) {                             // the classic FPI table: last fortnight, last 6 and 12, % of holdings, streak
  const k = Math.min(n, x.dates.length);
  return Object.keys(x.net).map((s) => { const v = x.net[s].slice(-k).filter(ok); let streak = 0;
    for (const z of [...v].reverse()) { if (streak >= 0 && z > 0) streak++; else if (streak <= 0 && z < 0) streak--; else break; }
    const a = (x.auc[s] || []).slice(-k).filter(ok), aum = a.length ? a[a.length - 1] : null, sum = (m) => v.slice(-m).reduce((p, q) => p + q, 0);
    return { s, last: v.length ? v[v.length - 1] : null, s6: sum(6), s12: sum(12), pct: aum && aum > 0 ? (sum(6) / aum) * 100 : null, streak, aum }; }).sort((a, b) => (b.pct ?? -1e9) - (a.pct ?? -1e9));
}
function viewFpi() {
  const F = S.mf, x = F.fpi;
  let out = note("NSDL's fortnightly report of <b>foreign portfolio investor</b> net investment by sector: recorded money, but FPIs only, NSDL's own sector list, and published after the fortnight ends. Shown as context; <b>not used in the score or the test</b>.");
  if (!x) { if (!F._fpiRun && !F.fpiErr) loadFlow("fpi"); return out + note(F._live ? `Reading NSDL's fortnightly reports… ${F.fpiDone || 0} of 48` : F.fpiErr ? `FPI data NOT AVAILABLE: ${esc(F.fpiErr)}` : "Loading NSDL fortnights…"); }
  out += `<div class="rg-ctl">${seg("mffpi", [[12, "12 fortnights"], [24, "24"], [48, "48"]], F.fpiN)}</div>`;
  const n = +F.fpiN, last = x.dates[x.dates.length - 1], age = Math.round((Date.now() - Date.parse(last)) / 864e5);
  const rows = fpiRows(x, n);
  const cr = (v) => (ok(v) ? `<span class="${v > 0 ? "up" : v < 0 ? "dn" : ""}">${f(v, 0)}</span>` : "n/a");
  return out + `<div class="note">${x.ok} of ${x.tried} reports loaded${x.live ? " just now (the nightly job could not reach NSDL)" : ""} · latest fortnight ended ${nice(last)} (${age} days ago) · ₹ crore, equity.</div>` + tbl([["NSDL sector"], ["Last fortnight ₹Cr", 1], ["Last 6 ₹Cr", 1], ["Last 12 ₹Cr", 1], ["Last 6 as % of FPI holdings", 1], ["Fortnights in a row (+ in / − out)", 1], ["FPI holdings ₹Cr", 1]],
    rows.map((r) => `<tr><td><b>${esc(r.s)}</b></td><td class="r">${cr(r.last)}</td><td class="r">${cr(r.s6)}</td><td class="r">${cr(r.s12)}</td><td class="r">${ok(r.pct) ? pc(r.pct, 2, "") : "n/a"}</td><td class="r">${r.streak > 0 ? "+" + r.streak : r.streak}</td><td class="r">${ok(r.aum) ? num(r.aum, 0) : "n/a"}</td></tr>`), "stick");
}
function viewMethod(d) {
  const q = d.quality;
  const rows = [["Relative momentum", "35%", "Sector ÷ benchmark change over 3, 6 and 12 months, each divided by its volatility", "Industry momentum is one of the most replicated effects in equities; NSE's own momentum indices rank on 6 and 12-month return ÷ volatility"],
    ["Rotation speed", "20%", "RS-Momentum from the Relative Rotation Graph", "Catches relative strength that is accelerating, the early part of a rotation"], ["Breadth", "20%", "% of members above their 50-EMA and its 20-session change", "A move carried by many stocks lasts longer than one carried by a few"],
    ["Trend consistency", "15%", "Up days minus down days in relative return, 6 months", "Steady gains persist longer than jumpy ones (“frog in the pan”)"], ["Up-day turnover", "10%", "Share of traded value in stocks that closed up, 5 sessions", "Where the day's money was traded"]];
  const cov = d.table.slice().sort((a, b) => (a.coverage ?? 0) - (b.coverage ?? 0));
  return h("How the rotation model works") + `<div class="card pad mf-txt"><p><b>1 · Score every sector against every other sector, every day.</b> Five components, each a z-score across sectors on that date (so a sector is only ever compared with its peers on the same day), combined with weights fixed in advance:</p></div>`
    + tbl([["Component"], ["Weight", 1], ["What it is"], ["Why it is there"]], rows.map((r) => `<tr><td><b>${r[0]}</b></td><td class="r">${r[1]}</td><td class="mf-wrap">${r[2]}</td><td class="mf-wrap">${r[3]}</td></tr>`))
    + `<div class="card pad mf-txt"><p><b>2 · Filter.</b> A sector below its 200-session average gets no slot, however high it ranks (trend rule). When the market is Risk-off or in Stress, only half the slots are filled (momentum's worst losses happen in panic markets).</p>
      <p><b>3 · Hold the top N, change slowly.</b> Reviewed every K sessions. A held sector stays while its rank is within N + N (the buffer), so a sector hovering around #5 is not traded every review.</p>
      <p><b>4 · Prove it.</b> <i>Does it work?</i> replays all of the above walk-forward on the loaded history, with costs, against the benchmark, all sectors equal-weight, and plain 6-month momentum, and reports the information coefficient and quintile spread for every component.</p></div>`
    + h("Sources") + `<div class="card pad mf-txt">${SOURCES.map(([t, u]) => `<a href="${u}" target="_blank" rel="noopener">${esc(t)}</a>`).join("<br>")}</div>`
    + `<details class="card mf-set"><summary>Full rules (arka_flow/rotation.py)</summary><pre class="mf-pre">${esc(d.rules_doc)}</pre></details>`
    + h("What was loaded") + `<div class="card pad mf-txt"><ul class="mf-why"><li>Source: <b>${esc(q.source)}</b> · prices ${esc(q.adjusted)} · history ${esc(q.period)} · loaded ${esc(String(q.loaded_at).slice(0, 16).replace("T", " "))}</li>
      <li>Universe: <code>${esc(q.universe)}</code> · requested <b>${q.requested}</b>, received <b>${q.loaded}</b>, used <b>${q.used}</b></li><li>Data up to <b>${esc(nice(d.asof))}</b> · checked against the NSE 2026 holiday list in the app</li>
      <li>Excluded as illiquid (median value under ₹${d.min_turn} Cr/day): <b>${q.illiquid}</b> · stale tickers: <b>${q.stale}</b></li><li>Groups with fewer than 3 priced members: ${esc(q.small_groups.join(", ") || "none")} · NSE index series missing: ${esc(q.index_missing.join(", ") || "none")}</li>
      <li>Model versions: rotation ${esc(q.rot_version)} · engine ${esc(q.model_version)} · built ${esc(String(d.built).slice(0, 16).replace("T", " "))} IST by the nightly “Market data” job</li></ul></div>`
    + tbl([["Sector"], ["Members", 1], ["With prices", 1], ["Coverage %", 1], ["Status"]], cov.map((r) => `<tr><td><b>${esc(r.sector)}</b></td><td class="r">${r.members ?? "n/a"}</td><td class="r">${r.with_prices ?? "n/a"}</td><td class="r">${ok(r.coverage) ? num(r.coverage * 100, 0) : "n/a"}</td><td>${esc(r.coverage_status || "")}</td></tr>`), "stick")
    + `<div class="note">A sector with under ${num(q.min_coverage * 100, 0)}% of members priced is marked INCOMPLETE.</div>`
    + (q.failures.length ? `<details class="card mf-set"><summary>Tickers with no data · ${q.n_failures}</summary><div class="pad mf-txt">${q.failures.map(([k, v]) => `${esc(k)}: ${esc(v)}`).join("<br>")}</div></details>` : "")
    + (q.jumps.length ? `<details class="card mf-set"><summary>One-session moves above 25% · ${q.jumps.length} (check for unadjusted splits)</summary><div class="pad mf-txt">${q.jumps.map((j) => `${esc(j.symbol)} · ${esc(j.date)} · ${f(j.move_pct, 1, true, "%")}`).join("<br>")}</div></details>` : "");
}
export function page() {
  const F = S.mf, d = F.d; resetCharts();
  const head = `<div class="hd"><div><h1>Money flow</h1><div class="sub">Sector rotation: where to be, and when</div></div></div>`;
  const pills = `<div class="seg">${VIEWS.map((v) => `<button class="${v === F.view ? "on" : ""}" data-act="mfview" data-v="${v}">${v}</button>`).join("")}</div>`;
  if (F.view === "FPI flows") return `<div class="mf">${head}${pills}${viewFpi()}</div>`;
  if (!d) { if (!F._d && !F.err) loadFlow("main"); return `<div class="mf">${head}${pills}<div class="card"><div class="${F.err ? "empty" : "note"}">${F.err ? `<b>Money flow is not available yet</b>${esc(F.err)}` : "Loading sector rotation…"}</div></div></div>`; }
  const M = model(d);
  if (!M.m) { const wait = !modelsOf(d) && !F.modErr; return `<div class="mf">${head}${pills}${dataCard(d)}${settings(d, M)}${note(wait ? `Loading the model at ${num(M.cost, 2)}% cost…` : F.modErr ? esc(F.modErr) : "This model setting was not computed in the last run.")}</div>`; }
  const snap = snapOf(d, M);
  const body = F.view === "Where to be" ? viewNow(d, M, snap) : F.view === "Rotation map" ? viewMap(d, snap) : F.view === "Sector" ? viewSector(d, snap) : F.view === "Does it work?" ? viewProof(d, M)
    : F.view === "Breadth & activity" ? viewBreadth(d) : F.view === "Rule states" ? viewStates(d) : viewMethod(d);
  return `<div class="mf">${head}${pills}${status(d, M)}${dataCard(d)}${settings(d, M)}${body}<div class="note" style="padding:16px 2px">Research tool built on daily price, breadth and traded value. Rankings and tests describe the past; they are not forecasts or investment advice. Built nightly by the “Market data” job with the same engine as the classic app (Arka groups, against ${esc(d.bench)}, ${esc(d.period)} of history, stocks trading at least ₹${d.min_turn} Cr a day).</div></div>`;
}
export function csv() {
  const d = S.mf.d; if (!d) return;
  const snap = snapOf(d, model(d)), keys = ["sector", "score", "rank", "rank_20_ago", "rank_pct", "momentum", "rotation", "breadth", "consistency", "activity", "n_parts", "rs_ratio", "rs_mom", "quadrant", "quadrant_10_ago", "trend_ok", "dist_trend", "rel_63", "rel_126", "rel_21", "pct50", "d50", "consistency_raw", "vol", "rank_change_20", "action"];
  const cell = (v) => (v == null ? "" : /[",\n]/.test(String(v)) ? '"' + String(v).replace(/"/g, '""') + '"' : String(v));
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([[keys.join(","), ...snap.map((r) => keys.map((k) => cell(r[k])).join(","))].join("\n")], { type: "text/csv" }));
  a.download = "arka_rotation_ranking.csv"; document.body.appendChild(a); a.click(); a.remove();
}

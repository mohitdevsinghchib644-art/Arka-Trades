// Arka Trades · News desk. The classic news desk (news_feed.py v12.1) in the new app: the same sources, tags, merging,
// scoring and eleven views. The server fetches each source separately; the browser does the rest (newsengine.js).
import { S, api, paint, esc, num } from "./app.js";
import * as N from "./newsengine.js";

const GREEN = "#30D158", RED = "#FF453A", AMBER = "#FF9F0A", CYAN = "#5AC8FA", PURPLE = "#BF5AF2", YELLOW = "#FFD60A", TX3 = "#6B6B6B";
const WIRE = [["Economic Times", 0], ["Economic Times", 1], ["Business Standard", 2], ["Business Standard", 3], ["Mint", 4], ["Mint", 5], ["Moneycontrol", 6], ["Moneycontrol", 7], ["BusinessLine", 8], ["NDTV Profit", 9]];
const NSE = ["Announcements", "Financial results", "Board meetings", "Corporate actions", "Insider trading"];
const EVENT_LABEL = { REGULATORY: "Regulatory", FILING: "Filing", ORDER: "Order win", STAKE: "Stake / M&A", PROMOTER: "Promoter", EARNINGS: "Results", "CORP ACTION": "Corporate action", IPO: "IPO", RATING: "Rating",
  CAPEX: "Capex", MANAGEMENT: "Management", MACRO: "Macro", NEWS: "News" };
const EVENT_COLORS = { REGULATORY: RED, FILING: CYAN, ORDER: GREEN, STAKE: AMBER, PROMOTER: AMBER, EARNINGS: PURPLE, "CORP ACTION": GREEN, IPO: YELLOW, RATING: CYAN, CAPEX: CYAN, MANAGEMENT: YELLOW, MACRO: AMBER, NEWS: TX3 };
const MODE_LABEL = { TOP: "Top", LIVE: "Latest", "MY STOCKS": "My stocks", MOVERS: "Movers", DEVELOPING: "Developing", CATALYSTS: "Catalysts", RISK: "Risk", EARNINGS: "Results", "CORP ACTIONS": "Corporate actions", FILINGS: "Filings", MACRO: "Macro" };
const TAG_LABEL = { "BANKS & FIN": "Banks & finance", IT: "IT", AUTO: "Auto", PHARMA: "Pharma & health", METALS: "Metals", ENERGY: "Oil & gas", FMCG: "FMCG", REALTY: "Realty", POWER: "Power", "CAP GOODS": "Capital goods",
  TELECOM: "Telecom", CEMENT: "Cement", CHEMICALS: "Chemicals", RBI: "RBI", FED: "Fed", INFLATION: "Inflation", CRUDE: "Crude", RUPEE: "Rupee", FII: "FII / DII flows", GDP: "GDP", TRADE: "Trade & tariffs", BUDGET: "Budget & GST", GLOBAL: "Global markets" };
const TONE_WORD = { strong_positive: "Positive", mild_positive: "Slightly positive", neutral: "Neutral", mild_negative: "Slightly negative", strong_negative: "Negative" };
const TONE_COLOR = { strong_positive: GREEN, mild_positive: "#6FD98F", neutral: TX3, mild_negative: "#FF8A80", strong_negative: RED };
const WINDOWS = [["24 h", 1], ["3 days", 3], ["7 days", 7], ["30 days", 30]];
const tagLabel = (t) => TAG_LABEL[t] || t[0] + t.slice(1).toLowerCase();
export const newsDefaults = () => ({ days: 1, q: "", sort: "Priority", mode: "TOP", part: "Stories", res: {}, items: null, at: 0, busy: false, names: null, seen: null, ver: 0, built: -1 });

// ── data ──
const universe = () => [...new Set([...S.wl.rows.map((r) => r.sym), ...S.pf.lots.map((l) => l.sym)])].slice(0, 120);
const coKey = (n) => String(n || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").replace(/\s+/g, " ").trim().replace(/\s+(limited|ltd)$/, "");
let nameIdx = null;
function seenSet() { if (S.nw.seen) return S.nw.seen; try { S.nw.seen = new Set(JSON.parse(localStorage.getItem("arka_news_seen") || "[]")); } catch { S.nw.seen = new Set(); } return S.nw.seen; }
function saveSeen() { try { localStorage.setItem("arka_news_seen", JSON.stringify([...S.nw.seen].slice(-1500))); } catch { /* private mode */ } }
export async function loadNews(force) {
  const W = S.nw;
  if (W.busy || (!force && Date.now() - W.at < 110000)) return;
  W.busy = true; W.at = Date.now();
  if (W.names === null) { W.names = {}; try { W.names = await api("/api/market?f=names"); } catch { /* names are optional */ } nameIdx = Object.fromEntries(Object.entries(W.names).map(([s, n]) => [coKey(n), s])); }
  const days = W.days, U = universe(), bucket = Math.floor(Date.now() / 300000), jobs = [];
  WIRE.forEach(([nm, i]) => jobs.push({ id: "wire:" + i, kind: "wire", source: nm, health: nm }));
  N.MACRO_QUERIES.forEach((q, i) => jobs.push({ id: "gnews:m" + i, q, kind: "macro", source: "Google News", health: "Google News" }));
  for (const s of N.rotation(U, "", 10, bucket)) jobs.push({ id: "gnews:" + s, q: N.companyQuery(s, W.names[s]), kind: "company", source: "Google News", sym: s, health: "Google News" });
  NSE.forEach((_, i) => jobs.push({ id: "nse:" + i, kind: "filing", source: "NSE filing", health: "NSE filings" }));
  jobs.push({ id: "bse:", kind: "filing", source: "BSE filing", health: "BSE filings" });
  let pending = jobs.length, timer = 0;
  const done = () => { clearTimeout(timer); timer = setTimeout(() => { W.ver++; paint(); }, 250); };
  await Promise.all(jobs.map(async (j) => {
    try {
      const d = await api(`/api/feed?id=${encodeURIComponent(j.id)}&days=${days}` + (j.q ? "&q=" + encodeURIComponent(j.q) : ""));
      const rows = (d.rows || []).map((r) => ({ ...r, sym: r.company && nameIdx ? nameIdx[coKey(r.company)] || null : null }));
      W.res[j.id + "|" + days] = { ...j, rows, ok: d.ok, err: d.err || "", n: rows.length };
    } catch (e) { W.res[j.id + "|" + days] = { ...j, rows: (W.res[j.id + "|" + days] || {}).rows || [], ok: false, err: e.message.slice(0, 40), n: 0 }; }
    pending--; done();
  }));
  W.busy = false; W.ver++; paint();
}
// One company's stories for the stock page (news_feed.get_security_news): the wire, exchange filings and a Google News search
// for the company, 7 days, kept only when the story is about it. pts = 15-minute prices for the move since each story.
export async function securityNews(sym, pts) {
  const W = S.nw;
  if (W.names === null) { W.names = {}; try { W.names = await api("/api/market?f=names"); } catch { /* optional */ } nameIdx = Object.fromEntries(Object.entries(W.names).map(([s, n]) => [coKey(n), s])); }
  const jobs = [...WIRE.map(([nm, i]) => ({ id: "wire:" + i, kind: "wire", source: nm })), { id: "gnews:" + sym, q: N.companyQuery(sym, W.names[sym]), kind: "company", source: "Google News", sym },
    ...NSE.map((_, i) => ({ id: "nse:" + i, kind: "filing", source: "NSE filing" })), { id: "bse:", kind: "filing", source: "BSE filing" }];
  const results = await Promise.all(jobs.map(async (j) => {
    try { const d = await api(`/api/feed?id=${encodeURIComponent(j.id)}&days=7` + (j.q ? "&q=" + encodeURIComponent(j.q) : ""));
      return { ...j, rows: (d.rows || []).map((r) => ({ ...r, sym: r.company && nameIdx ? nameIdx[coKey(r.company)] || null : null })), ok: d.ok }; }
    catch { return { ...j, rows: [], ok: false }; }
  }));
  return N.assemble(results, [sym], W.names || {}, 7, Date.now(), sym).filter((a) => a.symbols.includes(sym));
}
export function reaction(pts, dt) {                          // news_feed._reaction: last price vs the first 15-minute close at or after the story
  if (!pts || !pts.length) return null;
  const after = pts.filter((p) => p[0] >= dt).map((p) => p[1]);
  return after.length && after[0] > 0 ? (pts[pts.length - 1][1] / after[0] - 1) * 100 : null;
}
export const reactPill = (sym, r) => (r == null ? "" : pill(`${sym} ${r >= 0 ? "+" : ""}${num(r, 2)}% since`, r >= 0 ? GREEN : RED));

function build() {                                            // assemble once per new batch of results, then only refresh ages
  const W = S.nw, U = universe(), key = W.ver + "|" + W.days + "|" + U.join(",");
  const results = Object.entries(W.res).filter(([k]) => k.endsWith("|" + W.days)).map(([, v]) => v);
  if (W.built !== key) {
    W.items = N.assemble(results, U, W.names || {}, W.days, Date.now()); W.built = key;
    const seen = seenSet(), first = !seen.size; for (const a of W.items) a.is_new = !first && !seen.has(a.id);
    for (const a of W.items.slice(0, 200)) seen.add(a.id); saveSeen();
  } else N.score(W.items, new Set(U), "", Date.now());
  const health = {}; for (const r of results) { const h = (health[r.health] = health[r.health] || { name: r.health, ok: 0, fail: 0, n: 0, err: "" }); if (r.ok) h.ok++; else { h.fail++; h.err = r.err; } h.n += r.n; }
  return { items: W.items || [], U, health: Object.values(health).sort((a, b) => (a.fail > 0 && a.ok === 0) - (b.fail > 0 && b.ok === 0) || a.name.localeCompare(b.name)) };
}
// ── drawing ──
const clock = (dt) => { const t = new Date(dt + 330 * 60000), n = new Date(Date.now() + 330 * 60000), hm = String(t.getUTCHours()).padStart(2, "0") + ":" + String(t.getUTCMinutes()).padStart(2, "0");
  return t.getUTCDate() === n.getUTCDate() && t.getUTCMonth() === n.getUTCMonth() ? hm : t.toLocaleDateString("en-IN", { day: "2-digit", month: "short", timeZone: "UTC" }) + ", " + hm; };
const pill = (t, c, solid) => `<span class="nd-pill${solid ? " solid" : ""}" style="--c:${c}">${esc(t)}</span>`;
const href = (u) => (/^https?:\/\//i.test(u || "") ? esc(u) : "#");
export function story(a, moves, react = null) {
  const p = [];
  if (a.is_breaking) p.push(pill("Breaking", RED, true)); else if (a.priority >= 70) p.push(pill("Important", AMBER));
  if (a.hot) p.push(pill(`Trending · ${a.source_count} outlets`, YELLOW)); if (a.is_new) p.push(pill("New", CYAN));
  if (a.event !== "NEWS") p.push(pill(EVENT_LABEL[a.event] || a.event, EVENT_COLORS[a.event] || TX3));
  const ago = a.time_str + (a.time_str === "now" || !/[mh]$/.test(a.time_str) ? "" : " ago"), src = esc(a.source) + (a.source_count > 1 ? ` and ${a.source_count - 1} more` : "");
  const mv = (s) => { const c = moves[s]; return c == null ? "" : `<i class="${c > 0.005 ? "up" : c < -0.005 ? "dn" : "mu"}">${c > 0 ? "+" : c < 0 ? "−" : ""}${num(Math.abs(c), 1)}%</i>`; };
  const syms = a.symbols.slice(0, 4).map((s) => `<button class="nd-sym" data-sym="${esc(s)}">${esc(s)}${mv(s)}</button>`).join("") || `<span class="nd-sym">${a.kind === "macro" || a.macro.length ? "Macro" : "Market"}</span>`;
  const extra = [...a.sectors.slice(0, 2), ...a.macro.slice(0, 2)].map(tagLabel).map(esc).join(" · ");
  return `<article class="nd-s"><div class="nd-m"><span class="t">${clock(a.pub)}</span><span>·</span><span>${esc(ago)}</span><span>·</span><span>${src}</span>${p.join("")}</div>
    <a class="nd-h" href="${href(a.link)}" target="_blank" rel="noopener noreferrer">${esc(a.title)}</a>${a.summary ? `<div class="nd-x">${esc(a.summary)}</div>` : ""}
    <div class="nd-f">${syms}${react || ""}<span><i class="nd-dot" style="--c:${TONE_COLOR[a.sentiment]}"></i>${TONE_WORD[a.sentiment]}</span>${a.amount_cr ? `<span class="${a.score >= 0 ? "up" : "dn"}" style="font-weight:600">₹${num(a.amount_cr, 0)} Cr</span>` : ""}${extra ? `<span>${extra}</span>` : ""}</div></article>`;
}
function side(items, U, health) {
  const pl = N.pulse(items, U), live = pl.filter((r) => r.stories > 0), quiet = pl.length - live.length;
  const you = `<div class="card nd-card"><h3>Your stocks in the news<em>${U.length} tracked</em></h3>${live.length ? live.slice(0, 14).map((r) => { const c = r.tone > 0.2 ? GREEN : r.tone < -0.2 ? RED : TX3, w = r.tone > 0.2 ? "positive" : r.tone < -0.2 ? "negative" : "mixed";
    return `<a class="nd-pl" href="${href(r.link)}" target="_blank" rel="noopener noreferrer"><span class="s">${esc(r.symbol)}</span><span class="n" style="color:${c}">${r.stories} ${r.stories === 1 ? "story" : "stories"} · ${w}</span><span class="h">${esc(r.latest)}${r.age ? " · " + esc(r.age) : ""}</span></a>`; }).join("")
    : '<div class="note">No stories name your stocks in this window.</div>'}${quiet && live.length ? `<div class="note">${quiet} tracked stocks had no news.</div>` : ""}</div>`;
  const st = N.sectorTone(items).slice(0, 10), sect = st.length ? `<div class="card nd-card"><h3>Sector tone<em>stories · average tone</em></h3>${st.map(([n, c, t]) => { const w = Math.min(50, (Math.abs(t) / 3) * 50), col = t >= 0 ? GREEN : RED;
    return `<div class="nd-bar"><span>${esc(tagLabel(n))}</span><div class="t"><i style="${t >= 0 ? "left:50%" : `left:${(50 - w).toFixed(0)}%`};width:${w.toFixed(0)}%;background:${col}"></i><s></s></div><span style="color:${col}">${c} · ${t >= 0 ? "+" : "−"}${num(Math.abs(t), 1)}</span></div>`; }).join("")}</div>` : "";
  const mc = {}; for (const a of items) for (const t of a.macro) mc[t] = (mc[t] || 0) + 1;
  const mac = Object.keys(mc).length ? `<div class="card nd-card"><h3>Macro themes</h3><div class="nd-chips">${Object.entries(mc).sort((a, b) => b[1] - a[1]).slice(0, 8).map(([k, v]) => pill(`${tagLabel(k)} · ${v}`, AMBER)).join("")}</div></div>` : "";
  const liveN = health.filter((h) => h.ok).length;
  const src = `<div class="card nd-card"><h3>Sources<em>${liveN} of ${health.length} live</em></h3>${health.map((h) => `<div class="nd-src"><span><i class="nd-dot" style="--c:${h.ok ? GREEN : RED}"></i>${esc(h.name)}</span><span class="${h.ok ? "mu" : "dn"}">${h.ok ? h.n + " items" + (h.fail ? ` · ${h.fail} failed` : "") : `not reachable (${esc(h.err || "failed")})`}</span></div>`).join("")}</div>`;
  return { you, sect: sect + mac, src };
}
export function page() {
  const W = S.nw, { items, U, health } = build(), moves = {};
  for (const s of U) { const q = S.q[s]; if (q && q.ok && q.pct != null) moves[s] = q.pct; }
  const t = new Date(Date.now() + 330 * 60000), stamp = t.toLocaleDateString("en-IN", { weekday: "short", day: "2-digit", month: "short", timeZone: "UTC" }) + ", " + String(t.getUTCHours()).padStart(2, "0") + ":" + String(t.getUTCMinutes()).padStart(2, "0");
  const head = `<div class="hd"><div><h1>News desk</h1><div class="sub">${stamp} IST · updates by itself every 2 minutes</div></div><button class="btn sm" data-act="nwrefresh">Refresh</button></div>
    <div class="nd-ctl"><div class="seg">${WINDOWS.map(([l, d]) => `<button class="${W.days === d ? "on" : ""}" data-act="nwdays" data-v="${d}">${l}</button>`).join("")}</div>
      <input data-nw="q" value="${esc(W.q)}" placeholder="Search headlines, stocks, sectors, sources: RBI, order, HDFCBANK, pharma…" autocomplete="off" spellcheck="false">
      <div class="seg">${["Priority", "Newest"].map((s) => `<button class="${W.sort === s ? "on" : ""}" data-act="nwsort" data-v="${s}">${s}</button>`).join("")}</div></div>`;
  if (!items.length) return `<div class="nd">${head}<div class="card"><div class="note">${W.busy || !W.at ? "Gathering the news…" : "No stories yet; some sources may be slow. Press Refresh in a few seconds."}</div></div></div>`;
  const tone = N.newsTone(items), pos = items.filter((a) => a.score > 0).length, neg = items.filter((a) => a.score < 0).length;
  const brk = items.filter((a) => a.is_breaking || (a.priority >= 70 && a.age_min <= 180)).slice(0, 12);
  const tape = brk.length ? `<div class="nd-tape"><span class="lab">Breaking</span><div class="run"><div>${[...brk, ...brk].map((a) => `<b>${esc(a.symbols.slice(0, 2).join(", ") || EVENT_LABEL[a.event] || "News")}</b>${esc(a.title)}`).join("")}</div></div></div>` : "";
  const kp = (l, v, s) => `<div><span>${l}</span><b>${v}</b><small>${s}</small></div>`, liveN = health.filter((h) => h.ok).length;
  const kpis = `<div class="card kp kp6 nd-k">${kp("Stories", items.length, `${items.filter((a) => a.is_new).length} new since you last looked`)}${kp("Your stocks", items.filter((a) => a.mine).length, `stories across ${U.length} tracked stocks`)}
    ${kp("News tone", `<span class="${tone > 0 ? "up" : tone < 0 ? "dn" : ""}">${tone > 0 ? "+" : tone < 0 ? "−" : ""}${Math.abs(tone)}</span>`, `<span class="up">${pos} positive</span> · <span class="dn">${neg} negative</span>`)}
    ${kp("Breaking", items.filter((a) => a.is_breaking).length, `${items.filter((a) => a.hot).length} trending (3+ outlets)`)}${kp("Filings", items.filter((a) => a.kind === "filing").length, "NSE and BSE exchange filings")}${kp("Sources live", `${liveN} of ${health.length}`, "details in Sources")}</div>`;
  let shown = N.view(items, W.mode, W.q, "", moves); if (W.sort === "Newest") shown = shown.slice().sort((a, b) => b.pub - a.pub);
  const empty = W.mode === "MOVERS" ? "None of your stocks with news moved 2% or more today." : W.mode === "DEVELOPING" ? "No story is carried by three or more outlets in this window." : "Nothing in this view for the selected window.";
  const wire = `<div class="seg nd-modes">${N.MODES.map((m) => `<button class="${W.mode === m ? "on" : ""}" data-act="nwmode" data-v="${m}">${MODE_LABEL[m]}</button>`).join("")}</div>
    <h2>${esc(MODE_LABEL[W.mode])} <span>${shown.length} ${shown.length === 1 ? "story" : "stories"}</span></h2>${shown.length ? `<div class="nd-list">${shown.slice(0, 45).map((a) => story(a, moves)).join("")}</div>` : `<div class="card"><div class="note">${esc(empty)}</div></div>`}`;
  const sd = side(items, U, health);
  const parts = ["Stories", "Your stocks", "Sectors & macro", "Sources"];
  const phone = `<div class="only-m"><div class="seg">${parts.map((p) => `<button class="${W.part === p ? "on" : ""}" data-act="nwpart" data-v="${p}">${p}</button>`).join("")}</div>${W.part === "Stories" ? wire : W.part === "Your stocks" ? sd.you : W.part === "Sources" ? sd.src : sd.sect || '<div class="card"><div class="note">Not enough sector-tagged stories yet.</div></div>'}</div>`;
  const desk = `<div class="only-d nd-cols"><div>${wire}</div><aside>${sd.you}${sd.sect}${sd.src}</aside></div>`;
  return `<div class="nd">${head}${tape}${kpis}${phone}${desk}<div class="note" style="padding:16px 2px">Tone and story types are tagged automatically from the headline wording. Treat them as a reading aid, not a recommendation. Tap a headline to read it at the source; tap a stock to open it.</div></div>`;
}

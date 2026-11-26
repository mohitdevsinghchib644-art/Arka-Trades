// Arka Trades · Daily brief (daily_brief.render_brief + brief_html): the morning edition with the price tiles of every region, the
// stories, the radar, rapid fire and the calendar; the last 7 editions; "In your book today" (worked out here from your holdings and
// lists, never sent anywhere); and writing today's brief from the website (it starts the morning job on GitHub, write only, no Telegram).
import { S, api, paint, esc, toast } from "./app.js";
import { fx } from "./rsengine.js";

export const briefDefaults = () => ({ data: undefined, err: "", day: "", busy: false, job: null, valid: null, validAsked: false });
const KINDS = { company: "Company", sector: "Sector", macro: "Macro and policy", plumbing: "Market structure" };
const TYPES = { earnings: "Earnings", deal: "Deal", ipo: "IPO", flows: "Flows", rating: "Rating", other: "Also" };
const KIND_C = { company: "#5AC8FA", sector: "#BF5AF2", macro: "#FFD60A", plumbing: "#FF9F0A" };
const IMP = [[/bearish|headwind|negative|pressure|risk|weak|drag|downside|caution/, "#FF453A"], [/bullish|supportive|tailwind|positive|boost|upside|strong|relief/, "#30D158"],
  [/^watch|confirms|awaits|due /, "#FF9F0A"], [/read-through|read through|signals|implies/, "#5AC8FA"], [/noise/, "#71717A"]];
const SQL_SETUP = `create table if not exists public.arka_briefs (
  day date not null,
  kind text not null default 'am',
  payload jsonb not null,
  created_at timestamptz not null default now(),
  primary key (day, kind)
);`;
const DAYS_L = "Sunday Monday Tuesday Wednesday Thursday Friday Saturday".split(" "), MON_L = "January February March April May June July August September October November December".split(" ");
const parseDay = (d) => (/^\d{4}-\d{2}-\d{2}$/.test(d || "") ? new Date(d + "T00:00:00Z") : null);
const longDay = (d) => { const t = parseDay(d); return t ? `${DAYS_L[t.getUTCDay()]} ${String(t.getUTCDate()).padStart(2, "0")} ${MON_L[t.getUTCMonth()]} ${t.getUTCFullYear()}` : String(d || ""); };
const shortDay = (d) => { const t = parseDay(d); return t ? `${DAYS_L[t.getUTCDay()].slice(0, 3)} ${String(t.getUTCDate()).padStart(2, "0")} ${MON_L[t.getUTCMonth()].slice(0, 3)}` : String(d || ""); };
const istNow = () => new Date(Date.now() + 330 * 60000), today = () => istNow().toISOString().slice(0, 10);
const impColor = (tag) => { const t = String(tag).toLowerCase(); for (const [rx, c] of IMP) if (rx.test(t)) return c; return "#A1A1AA"; };
const href = (u) => (/^https?:\/\//i.test(String(u || "")) ? esc(u) : "");

// ── numbers (_f_last, _f_chg, _move, _cls) ──
export function fLast(r) {
  const v = r.last, k = r.kind; if (v == null) return "—";
  if (k === "yld") return fx(v, 2, false) + "%"; if (k === "fx" || k === "vol") return fx(v, 2);
  if (k === "cry") return "$" + fx(v, 0); if (k === "usd") return "$" + fx(v, 2);
  return v >= 1000 ? fx(v, 0) : fx(v, 2);
}
export function fChg(r) {
  if (r.kind === "yld") return r.pts == null ? "—" : fx(r.pts * 100, 0, false, true) + " bp";
  const c = r.chg; return c == null ? "—" : Number(fx(c, 2, false)) === 0 ? "0.00%" : fx(c, 2, false, true) + "%";
}
const move = (r) => (r.kind === "yld" ? r.pts : r.chg), cls = (v) => (v == null || Math.abs(v) < 0.005 ? "db-fl" : v > 0 ? "db-up" : "db-dn");
const stamp = (day, ref) => { if (!day) return ""; if (day === ref) return "Today, live"; const t = parseDay(day); return t ? "Close of " + shortDay(day) : ""; };
function groupDay(rows, group) {                               // the date most instruments of a group last traded on
  const n = {}; for (const r of rows) if (r.group === group && r.date) n[r.date] = (n[r.date] || 0) + 1;
  let best = ""; for (const [d, k] of Object.entries(n)) if (!best || k > n[best] || (k === n[best] && d > best)) best = d; return best;
}
function nums(rows, group, ref) {
  const common = groupDay(rows, group), out = rows.filter((r) => r.group === group).map((r) => { const odd = r.date && r.date !== common ? stamp(r.date, ref) : "";
    return `<div class="db-n"><small>${esc(r.name)}</small><b>${esc(fLast(r))}</b><em class="${cls(move(r))}">${esc(fChg(r))}</em>${odd ? `<u>${esc(odd)}</u>` : ""}</div>`; });
  return out.length ? `<div class="db-nums">${out.join("")}</div>` : "";
}
const srcHtml = (src) => (src || []).slice(0, 3).map((s) => { const u = href(s.url); return u ? `<a href="${u}" target="_blank" rel="noopener noreferrer" title="${esc(s.title || "")}">${esc(s.name)}</a>` : esc(s.name); }).join(" · ");
const symHtml = (syms, mine, valid) => (syms || []).filter((s) => !valid || valid.has(s)).map((s) => `<a class="db-sym${mine.has(s) ? " mine" : ""}" href="#/s/${encodeURIComponent(s)}">${esc(s)}</a>`).join("");
const panel = (title, right, body, span = "", pad = true) => `<div class="db-p ${span}"><div class="db-pt"><b>${title}</b><span>${right}</span></div>${pad ? `<div class="db-pb">${body}</div>` : body}</div>`;

export function briefHtml(b, mine = new Set(), valid = null) {
  const rows = b.snapshot || [], day = b.day || "", st = b.stats || {};
  let written = ""; try { const w = new Date(b.written); if (!isNaN(w)) { const t = new Date(w.getTime() + 330 * 60000); written = `written ${String(t.getUTCHours()).padStart(2, "0")}:${String(t.getUTCMinutes()).padStart(2, "0")} IST`; } } catch { written = ""; }
  const chips = `<span class="db-chip"><b>${st.headlines || 0}</b> headlines</span><span class="db-chip"><b>${st.sources || 0}</b> sources</span>${written ? `<span class="db-chip">${esc(written)}</span>` : ""}`;
  let out = `<div class="db-ed"><div><div class="o">Morning edition · before the open</div><div class="t">${esc(longDay(day))}</div></div><div class="db-chips">${chips}</div></div>`;
  const hits = new Map();                                       // which of your stocks are named today (worked out here, never sent to the AI)
  for (const sec of ["deep", "radar", "rapid"]) for (const it of b[sec] || []) for (const s of it.nse || []) if (mine.has(s)) hits.set(s, (hits.get(s) || 0) + 1);
  if (hits.size) out += `<div class="db-mine"><b>In your book today</b>${[...hits].sort((a, c) => c[1] - a[1]).map(([s, n]) => `<a class="db-sym mine" href="#/s/${encodeURIComponent(s)}">${esc(s)} · ${n}</a>`).join("")}<span>Tap a stock to open it.</span></div>`;
  const bl = (b.bottom_line || []).map((x, i) => `<li><i>${i + 1}</i><span>${esc(x)}</span></li>`).join("");
  out += `<div class="db-g">${bl ? `<div class="db-p db-bl c12"><div class="db-pt"><b>Bottom line</b><span>What matters today</span></div><ol>${bl}</ol></div>` : ""}</div>`;
  const g = b.global || {}; let regs = "";
  for (const [title, group, key] of [["United States", "United States", "us"], ["Europe", "Europe", "europe"], ["Asia", "Asia", "asia"]]) {
    const body = nums(rows, group, day) + (g[key] ? `<div class="db-say">${esc(g[key])}</div>` : "");
    if (body) regs += `<div class="db-reg"><div class="db-h4"><b>${title}</b><span>${esc(stamp(groupDay(rows, group), day))}</span></div>${body}</div>`;
  }
  const wrap = regs ? panel("Global session wrap", "Overnight and this morning", regs, "c7", false) : "";
  const india = nums(rows, "India", day) + (b.india ? `<div class="db-say">${esc(b.india)}</div>` : "");
  const comm = nums(rows, "Commodities", day) + nums(rows, "Currencies and rates", day) + (b.commodities ? `<div class="db-say">${esc(b.commodities)}</div>` : "");
  const side = `<div class="c5 db-side">${india ? panel("India setup", esc(stamp(groupDay(rows, "India"), day)), india) : ""}${comm ? panel("Commodities, currencies and rates", esc(stamp(groupDay(rows, "Commodities"), day)), comm) : ""}</div>`;
  out += `<div class="db-g">${wrap}${side}</div>`;
  const deep = b.deep || [], stories = deep.map((d, k) => {
    const i = k + 1, span = i === 1 && deep.length % 2 === 1 ? "c12" : "c6";      // an odd count: the lead story takes the full width
    const tags = `<span class="db-num">${String(i).padStart(2, "0")}</span><span class="db-tag" style="--c:${KIND_C[d.kind] || "#A1A1AA"}">${KINDS[d.kind] || "Macro and policy"}</span><span class="db-tag">${esc(d.region || "")}</span>${d.update ? '<span class="db-tag" style="--c:#FF9F0A">Update</span>' : ""}`;
    let parts = d.update && d.changed ? `<div class="db-part chg"><small>What changed</small><div class="tx">${esc(d.changed)}</div></div>` : "";
    parts += `<div class="db-part"><small>What happened</small><div class="tx">${esc(d.what)}</div></div><div class="db-part"><small>Why it matters</small><div class="tx">${esc(d.why)}</div></div>`;
    if (d.watch) parts += `<div class="db-part"><small>Watch next</small><div class="tx">${esc(d.watch)}</div></div>`;
    return `<div class="db-p ${span}"><div class="db-st"><div class="db-tags">${tags}</div><div class="db-h3">${esc(d.title)}</div>${parts}<div class="db-foot">${symHtml(d.nse, mine, valid)}<span>Sources: ${srcHtml(d.src)}</span></div></div></div>`;
  });
  const radar = (b.radar || []).map((r) => `<div class="db-li"><div class="tx">${esc(r.line)} ${symHtml(r.nse, mine, valid)}<small>${srcHtml((r.src || []).slice(0, 1))}</small></div><span class="db-imp" style="--c:${impColor(r.tag)}">${esc(r.tag)}</span></div>`).join("");
  const rapid = (b.rapid || []).map((r) => `<div class="db-rf"><span class="db-tag">${TYPES[r.type] || "Also"}</span><div class="tx">${esc(r.line)} ${symHtml(r.nse, mine, valid)}<small>${srcHtml((r.src || []).slice(0, 1))}</small></div></div>`).join("");
  const cal = (b.today || []).map((x) => `<div class="db-kv">${esc(x)}</div>`).join("");
  if (stories.length) out += `<div class="db-sec"><b>Stories</b><span>Developing situations: what happened, why it matters, what to watch next</span></div><div class="db-g">${stories.join("")}</div>`;
  if (radar) out += `<div class="db-sec"><b>On the radar</b><span>One line each, with what it implies</span></div><div class="db-g"><div class="db-p c12">${radar}</div></div>`;
  if (rapid || cal) out += `<div class="db-sec"><b>Rapid fire</b><span>Earnings, deals, IPOs, flows and rating changes</span></div><div class="db-g">${rapid ? `<div class="db-p ${cal ? "c8" : "c12"}">${rapid}</div>` : ""}${cal ? panel("On the calendar", "From the news", cal, rapid ? "c4" : "c12", false) : ""}</div>`;
  const web = (b.web || []).filter((w) => href(w.url)), webHtml = web.length ? " Web pages consulted: " + web.map((w) => `<a href="${href(w.url)}" target="_blank" rel="noopener noreferrer">${esc(w.title)}</a>`).join(" · ") + "." : "";
  out += `<div class="db-note">Prices come from Yahoo Finance and can trail the exchange. The text is written by AI (${esc(b.model || "")}) from the price table and ${st.headlines || 0} headlines (${st.feeds_ok || 0} of ${st.feeds || 0} news feeds answered); every story links to its sources, and items that cited no source were removed. Check the source before acting on any item. Information, not advice. ${esc((b.notes || []).join(" "))}${webHtml}</div>`;
  return `<div class="db">${out}</div>`;
}

// ── data ──
export async function load(day = "", fresh = false) {
  const B = S.br; B.busy = true; if (B.data === undefined) paint();
  try { const d = await api(`/api/brief?${day ? "day=" + encodeURIComponent(day) + "&" : ""}${fresh ? "fresh=1" : ""}`); B.data = d; B.day = d.brief ? d.brief.day : ""; B.err = ""; B.at = Date.now(); }
  catch (e) { if (e.message === "signed out") return; B.err = e.message; if (B.data === undefined) B.data = null; }
  B.busy = false; paint(); extras();
}
async function extras() {                                       // NSE names for the stock chips, and the desk list for "In your book"
  const B = S.br;
  if (!B.validAsked) { B.validAsked = true; try { const m = await api("/api/scan?f=meta"), all = [...(m.syms || []), ...(m.miss || [])]; if (all.length > 500) { B.valid = new Set(all); paint(); } } catch { /* chips are shown unfiltered */ } }
  if (!S.wl.desk) { try { S.wl.desk = await api("/api/watchlist?desk=1"); paint(); } catch { /* not needed */ } }
}
const mine = () => new Set([...S.wl.rows.map((r) => r.sym), ...((S.wl.desk && S.wl.desk.syms) || []), ...S.pf.lots.map((l) => l.sym)].map((s) => String(s).toUpperCase()));
async function write(again) {
  const B = S.br; if (B.job && !B.job.done) return;
  try { const d = await api("/api/brief", { method: "POST", body: JSON.stringify({ again }) }); B.job = { at: d.at, again, phase: "starting", done: false, err: "", url: "" }; paint(); poll(); }
  catch (e) { toast(e.message); }
}
async function poll() {
  const B = S.br, J = B.job; if (!J || J.done) return;
  await new Promise((r) => setTimeout(r, 12000));
  try {
    const d = await api("/api/brief?op=run"), run = d.run;
    if (run && run.created >= J.at - 120000) { J.url = run.url; J.phase = run.status;
      if (run.status === "completed") { J.done = true;
        const st = d.status, failed = run.conclusion !== "success" || (st && st.state === "failed");
        if (failed) J.err = (st && st.reason) || "The job did not finish (" + (run.conclusion || "unknown") + ").";
        else { toast("Today's brief is ready"); await load(today(), true); B.job = null; return; } } }
  } catch { /* try again */ }
  if (!J.done && Date.now() - J.at > 20 * 60000) { J.done = true; J.err = "No answer from the job after 20 minutes."; }
  paint(); if (!J.done) poll();
}

// ── page ──
export function page() {
  const B = S.br, head = `<div class="hd"><div><h1>Daily brief</h1><div class="sub">What happened overnight and what can move Indian stocks today</div></div></div>`;
  if (B.data === undefined) { if (!B.busy) setTimeout(() => load(), 0); return `<div>${head}<div class="card"><div class="note">Loading…</div></div></div>`; }
  const D = B.data || {}, days = D.days || [], t = today(), have = days.includes(t), admin = D.admin || (S.me && S.me.role === "admin");
  let o = head;
  if (B.err) o += `<div class="card pad"><div class="note">${esc(B.err)}</div></div>`;
  const pills = days.length > 1 ? `<div class="seg db-eds">${days.map((d) => `<button class="${d === B.day ? "on" : ""}" data-act="brday" data-v="${d}">${d === t ? "Today" : esc(shortDay(d))}</button>`).join("")}</div>` : "<div></div>";
  const again = have && admin ? `<button class="btn" data-act="brwrite" data-v="again" title="Collects fresh headlines and rewrites today's brief. Uses the AI key once.">Write it again</button>` : "";
  o += `<div class="db-top">${pills}${again}</div>`;
  const J = B.job;
  if (J) o += J.done ? `<div class="card pad db-job r"><b>The brief could not be written</b><span>${esc(J.err)}${J.url ? ` <a href="${esc(J.url)}" target="_blank" rel="noopener">Open the job on GitHub</a>` : ""}</span><button class="btn sm" data-act="brjobx">Close</button></div>`
    : `<div class="card pad db-job"><b><i class="aa-spin"></i>${J.again ? "Writing today's brief again" : "Writing today's brief"} on GitHub</b><span>${J.phase === "in_progress" ? "The job is running: collecting headlines and asking the AI." : "Waiting for GitHub to start the job."} It usually takes 2 to 5 minutes; this page updates by itself.${J.url ? ` <a href="${esc(J.url)}" target="_blank" rel="noopener">Follow it on GitHub</a>` : ""}</span></div>`;
  if (!have && !J) {
    const late = istNow().getUTCHours() >= 6;
    o += `<div class="db"><div class="db-empty"><b>${late ? "Today's brief has not been written yet." : "Today's brief is written after 6 am IST."}</b><span>${late ? "If the morning schedule is set up it arrives by about 8:30 am IST. You can also write it now; it takes a few minutes." : "You can still write one now from the latest headlines; it takes a few minutes."}</span>
      <button class="btn pri" data-act="brwrite" data-v="now"${D.can_write === false ? ' title="Needs the secret GH_ACTIONS_TOKEN in Cloudflare"' : ""}>Write today's brief now</button></div></div>`;
  }
  if (D.brief) { if (!have) o += '<div class="note db-cap">Showing the most recent brief.</div>'; o += briefHtml(D.brief, mine(), B.valid); }
  else if (!days.length) o += '<div class="card"><div class="empty"><b>No brief here yet</b>The morning job writes it at about 8 am on weekdays and it appears here after the next run.</div></div>';
  if (admin && D.table === false) o += `<details class="card sc-det"><summary>Share one brief between the app and Telegram (admin)</summary><div class="pad"><div class="mini">The table for briefs was not found in Supabase, so the morning job only saves its copy in the repository. To keep every edition in one place for both apps, run this once in the Supabase SQL editor:</div><pre class="sc-sql">${esc(SQL_SETUP)}</pre></div></details>`;
  return `<div class="brp">${o}</div>`;
}
export function act(a, el) {
  const B = S.br;
  if (a === "brday") { if (el.dataset.v !== B.day) { B.day = el.dataset.v; paint(); load(el.dataset.v); } return true; }
  if (a === "brwrite") { write(el.dataset.v === "again"); return true; }
  if (a === "brjobx") { B.job = null; paint(); return true; }
  return false;
}

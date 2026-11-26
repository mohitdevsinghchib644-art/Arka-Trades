// Arka Trades · Arka AI (arka_ai.py): teach Arka how you read a chart, then ask about any stock. The engine (arkaengine.js, checked
// against the Python on the same candles) finds every supply and demand zone, level and trendline with exact prices; Gemini then judges
// them against your playbook, and every reply is checked back against the detections before it is shown. Modes: Analyze, Radar,
// Journal, Teach, Playbook. The playbook is the same Pinecone index Streamlit uses. Only public prices, the detections and your
// playbook text go to the AI, never your holdings or watchlist.
import { S, api, paint, esc, toast, addStock } from "./app.js";
import { fx } from "./rsengine.js";
import * as E from "./arkaengine.js";

export const MODES = [["ANALYZE", "Analyze"], ["RADAR", "Radar"], ["JOURNAL", "Journal"], ["TEACH", "Teach"], ["PLAYBOOK", "Playbook"]];
const SPEEDS = { Fast: 0, Balanced: 1024, Deep: null };
const LAYERS = [["zones", "Zones"], ["levels", "Levels"], ["lines", "Trendlines"], ["plan", "Trade plan"], ["mas", "Moving averages"], ["markers", "Signals"]];
const TEACH = ["Import PDF", "Label zones", "From screenshot", "Quick rules", "Zone style"];
const KIND_LABEL = { setup: "SETUP", lesson: "LESSON", feedback: "LABELLED ZONES", style: "ZONE STYLE", call: "LOGGED PLAN" };
const G = "#30D158", R = "#FF453A", AM = "#FF9F0A", CY = "#5AC8FA", PU = "#BF5AF2", T2 = "#A3A3A3", T3 = "#6B6B6B", IV = "#ECECEC";
export const aaDefaults = () => ({ mode: "ANALYZE", items: null, itemsErr: "", loading: false, cloud: null, ai: null, model: "", speed: "Balanced", ticker: "", window: 120, risk: 1000, note: "", useAi: true,
  recents: [], report: null, busy: false, steps: [], t0: 0, err: "", show: Object.fromEntries(LAYERS.map(([k]) => [k, true])), qa: [], ask: "", asking: false,
  radar: { src: "My watchlist", txt: "", track: true, res: null, busy: false, err: "", prog: "" }, trackUniv: null, journal: { key: "", res: null, busy: false, pick: "", sure: false },
  teach: { sec: "Import PDF", pdf: null, pend: null, pendBusy: false, lab: { t: "", busy: false, data: null, pick: {}, why: {}, err: "" }, imgs: [], why: "", lesson: null, imgBusy: false,
    m: { name: "", sum: "", conds: "", trig: "", inv: "" }, l: { t: "", x: "" }, learned: null, hand: null }, sure: {}, open: {} });

// ── small helpers ──
const f = (p, nd = 2) => (typeof p === "number" && isFinite(p) ? fx(p, nd) : "—");                       // _fmt
const pill = (t, c) => `<span class="aa-pill" style="--c:${c}">${esc(t)}</span>`;
const chip = (t, cl = "") => `<span class="aa-chip ${cl}"><i></i>${esc(t)}</span>`;
const sec = (t, sub = "") => `<div class="aa-sec"><b>${esc(t)}</b>${sub ? `<span>${esc(sub)}</span>` : ""}</div>`;
const nice = (x) => E.cap(String(x).replace(/_/g, " "));
const phone = () => innerWidth < 760;
const MON = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(" ");
const istNow = () => new Date(Date.now() + 330 * 60000), today = () => istNow().toISOString().slice(0, 10);
const dmy = (t = istNow()) => `${String(t.getUTCDate()).padStart(2, "0")} ${MON[t.getUTCMonth()]} ${t.getUTCFullYear()}`;
const ranAt = () => { const t = istNow(); return `${dmy(t)}, ${String(t.getUTCHours()).padStart(2, "0")}:${String(t.getUTCMinutes()).padStart(2, "0")}`; };
const tick = () => new Promise((r) => setTimeout(r, 0));
const withTimeout = (p, ms, dflt) => Promise.race([p.catch(() => dflt), new Promise((r) => setTimeout(() => r(dflt), ms))]);
const repaint = () => { const a = document.activeElement; if (a && a.blur && a.closest && a.closest(".aa")) a.blur(); paint(); };
const download = (name, blob) => { const l = document.createElement("a"); l.href = URL.createObjectURL(blob); l.download = name; document.body.appendChild(l); l.click(); l.remove(); setTimeout(() => URL.revokeObjectURL(l.href), 4000); };
const pf = (v) => (v == null ? "None" : E.pyRepr(v));                                 // str() of a Python float
const tx = (v) => (v == null || v === "" || v === false || v === 0 ? "—" : E.pyStr(v));            // x or "—"
const pretty = (o) => `<pre class="aa-json">${esc(JSON.stringify(o, null, 2))}</pre>`;

// ── the playbook (Pinecone through the worker; this browser tab only when it is not connected) ──
const localKey = "arka_ai_local_items";
const localItems = () => { try { return JSON.parse(sessionStorage.getItem(localKey) || "[]"); } catch { return []; } };
const saveLocal = (list) => { try { sessionStorage.setItem(localKey, JSON.stringify(list)); } catch { /* private mode */ } };
function decode(vid, meta) {                                   // _decode: v9 items and legacy v8 setups
  const kind = meta.memory_kind; let payload, name;
  try {
    if (kind === "setup" && "setup_json" in meta) { payload = JSON.parse(meta.setup_json); name = meta.setup_name || payload.setup_name || "Unnamed setup"; }
    else if (KIND_LABEL[kind] && "item_json" in meta) { payload = JSON.parse(meta.item_json); name = meta.name || kind; }
    else return null;
  } catch { return null; }
  return { item_id: meta.setup_id || meta.item_id || vid, kind, name, active: Boolean(meta.active), saved_at: meta.saved_at || "", source: meta.source_name || "", payload: payload || {}, _local: false };
}
let itemsJob = null;
export function loadItems(force = false) {                      // one load at a time: a second caller waits for the same one
  const A = S.aa; if (A.items && !force) return Promise.resolve(); if (itemsJob) return itemsJob;
  itemsJob = loadItemsNow().finally(() => { itemsJob = null; }); return itemsJob;
}
async function loadItemsNow() {
  const A = S.aa; A.loading = true;
  let items = [];
  try { const st = await api("/api/arka?op=status"); A.cloud = st.cloud; A.ai = st.ai; if (st.model && !A.model) A.model = st.model;
    if (A.cloud) { const d = await api("/api/arka"); items = (d.matches || []).map((m) => decode(m.id, m.metadata || {})).filter(Boolean); }
    A.itemsErr = ""; }
  catch (e) { if (e.message === "signed out") { A.loading = false; return; } A.itemsErr = e.message; }
  items = items.concat(localItems()).sort((a, b) => (a.saved_at < b.saved_at ? 1 : a.saved_at > b.saved_at ? -1 : 0));
  A.items = items; A.loading = false; paint();
}
const items = () => S.aa.items || [];
const active = (kind) => items().filter((i) => i.kind === kind && i.active);
export function getStyle() { const st = { ...E.DEFAULT_STYLE }; const s = active("style")[0]; if (s) for (const [k, v] of Object.entries((s.payload || {}).params || {})) if (k in E.DEFAULT_STYLE) st[k] = v; return st; }
const styleSource = () => (active("style")[0] || {}).name || "DEFAULT";
async function saveItem(kind, name, payload, activate = true, sourceName = "", sourceText = "") {
  const A = S.aa; name = String(name || "").trim() || E.title(KIND_LABEL[kind] || kind);
  const others = activate && kind === "style" ? items().filter((i) => i.kind === "style" && i.active) : [];
  if (!A.cloud) {
    const id = `${kind}_${Date.now()}_${Math.floor(Math.random() * 65536).toString(16).padStart(4, "0")}`;
    const it = { item_id: id, kind, name, active: activate, saved_at: new Date().toISOString().replace("Z", "000+00:00"), source: sourceName, payload, _local: true };
    const list = localItems().map((x) => (others.some((o) => o.item_id === x.item_id) ? { ...x, active: false } : x)); list.unshift(it); saveLocal(list);
    others.forEach((o) => (o.active = false)); A.items = [it, ...items().filter((x) => x.item_id !== id)]; return true;
  }
  try {
    const d = await api("/api/arka", { method: "POST", body: JSON.stringify({ op: "save", kind, name, payload, active: activate, source_name: sourceName, source_text: sourceText, deactivate: others.filter((o) => !o._local).map((o) => o.item_id) }) });
    others.forEach((o) => (o.active = false)); A.items = [{ ...d.item, _local: false }, ...items().filter((x) => x.item_id !== d.item.item_id)]; return true;
  } catch (e) { toast("Could not save to your playbook: " + e.message); return false; }
}
async function setActive(id, on) {
  const it = items().find((x) => x.item_id === id); if (!it) return false;
  if (it._local) { saveLocal(localItems().map((x) => (x.item_id === id ? { ...x, active: on } : x))); it.active = on; return true; }
  try { await api("/api/arka", { method: "POST", body: JSON.stringify({ op: "active", id, active: on }) }); it.active = on; return true; } catch (e) { toast(e.message); return false; }
}
async function deleteItem(id) {
  const it = items().find((x) => x.item_id === id); if (!it) return false;
  if (!it._local) { try { await api("/api/arka", { method: "POST", body: JSON.stringify({ op: "delete", id }) }); } catch (e) { toast(e.message); return false; } }
  else saveLocal(localItems().filter((x) => x.item_id !== id));
  S.aa.items = items().filter((x) => x.item_id !== id); return true;
}

// ── prices: two years of daily candles as traded (split-adjusted, not dividend-adjusted) ──
const dfCache = new Map();
async function fetchOhlc(t) {
  const hit = dfCache.get(t); if (hit && Date.now() - hit.at < 300000) return hit.df;
  let last = "";
  for (const sym of E.yahooSymbols(t)) {
    try { const d = await api(`/api/history?range=2y&full=1&s=${encodeURIComponent(sym)}`), h = (d.history || [])[0];
      if (h && h.ok && h.d.length >= 40) { const df = E.cleanFrame(h); dfCache.set(t, { at: Date.now(), df }); return df; } }
    catch (e) { if (e.message === "signed out") throw e; last = e.message; }
  }
  throw new Error(`No usable 1d price history for ${t}` + (last ? ` (${last})` : ""));
}
async function fetchMany(symbols, onProg) {                   // fetch_many: NSE only, at least 60 candles
  const out = {}, batches = []; for (let i = 0; i < symbols.length; i += 6) batches.push(symbols.slice(i, i + 6));
  let done = 0;
  const one = async (b) => { try { const d = await api(`/api/history?range=2y&full=1&s=${encodeURIComponent(b.map((s) => s + ".NS").join(","))}`);
      for (const h of d.history || []) { const df = E.cleanFrame(h); if (df && df.c.length >= 60) out[String(h.sym).replace(/\.NS$/, "")] = df; } } catch (e) { if (e.message === "signed out") throw e; }
    done += b.length; if (onProg) onProg(Math.min(done, symbols.length)); };
  for (let i = 0; i < batches.length; i += 3) await Promise.all(batches.slice(i, i + 3).map(one));
  return Object.fromEntries(symbols.filter((s) => out[s]).map((s) => [s, out[s]]));
}
async function resultsDate(t) {
  if (E.isIndex(t)) return null; const sym = t.replace(/\.(NS|BO)$/, "");
  try { const d = await api("/api/cal?s=" + encodeURIComponent(sym)), r = d.cal && d.cal[sym] && d.cal[sym].results; return r && r >= today() ? r : null; } catch { return null; }
}

// ── Gemini (through the worker, with Arka's system prompt and the chosen speed) ──
async function gen(prompt, { json = true, images = [], files = [] } = {}) {
  const d = await api("/api/ai/arka", { method: "POST", body: JSON.stringify({ prompt, system_text: E.SYSTEM_PROMPT, json, budget: S.aa.speed in SPEEDS ? SPEEDS[S.aa.speed] : 1024, images, files }) });
  if (d.model) S.aa.model = d.model; return d.text || "";
}
async function callJson(prompt, opts = {}) {                    // one automatic retry when the reply is not valid JSON
  const raw = await gen(prompt, opts);
  try { return E.parseJsonLoose(raw); } catch { return E.parseJsonLoose(await gen(prompt + "\n\nYour previous reply was not valid JSON. Reply again with ONE valid JSON object only.", opts)); }
}
async function upload(file) {
  const r = await fetch("/api/ai/upload", { method: "POST", credentials: "same-origin", headers: { "x-file-type": file.type || "application/octet-stream" }, body: file });
  const d = await r.json().catch(() => ({})); if (!r.ok) throw new Error(d.error || "Upload failed (" + r.status + ")."); return { uri: d.uri, mime: d.mime };
}

// ── analysis (run_analysis) ──
async function runAnalysis(t) {
  const A = S.aa; t = String(t || "").toUpperCase().trim(); if (!t || A.busy) return;
  A.busy = true; A.err = ""; A.steps = []; A.reqT = t; A.t0 = performance.now(); A.ticker = t; repaint();
  const say = (m) => { A.steps.push(m); paintProg(); };
  try {
    await loadItems();
    const fN = E.isIndex(t) ? null : fetchOhlc("NIFTY50").catch(() => null), fR = resultsDate(t);
    say(`Loading ${t} prices`);
    const daily = await fetchOhlc(t), weekly = E.weeklyFromDaily(daily);
    say("Detecting zones, levels and trendlines"); await tick();
    const style = getStyle(), det = E.analyzeMarket(daily, style, E.liveFraction(daily.d[daily.d.length - 1]));
    const detW = weekly.c.length >= 60 ? E.analyzeMarket(weekly, { ...style, pivot_left: 2, pivot_right: 2, lookback: 140, base_max: Math.min(3, Math.trunc(style.base_max)) }) : null;
    E.addHtfConfluence(det, detW);
    const eplan = E.deterministicPlan(det), escore = E.engineScore(det, eplan);
    let hist = []; try { hist = E.zoneHistory(daily, style); } catch { hist = []; }
    let market = null; if (fN) { const nf = await withTimeout(fN, 12000, null); try { market = nf ? E.marketContext(daily, nf) : null; } catch { market = null; } }
    const results = await withTimeout(fR, 4000, null), setups = active("setup");
    const rep = { ticker: t, asof: det.asof, det, det_w: detW, style, engine_plan: eplan, engine_score: escore, ai: null, ai_error: "", plan: eplan, score: escore, note: A.note, window: A.window, market, results,
      zone_rows: hist, n_setups: setups.length, n_lessons: active("lesson").length, n_labelled: active("feedback").length, ran_at: ranAt(), df: daily, model: "" };
    if (A.useAi && A.ai) {
      try {
        say("Judging against your playbook (Arka AI)"); await tick();
        const preview = pngBase64(daily, E.buildSpec(det, null, eplan), 110, `${t} · detections (preview for AI)`, 72);
        const zref = eplan ? det.zones.find((z) => z.id === eplan.ref_zone) || null : null, tr = E.trackRecord(hist, zref);
        const extra = { market, results, engine_plan: eplan, track: { all: tr.groups["All zones"], similar_to_plan_zone: tr.like, note: "walk-forward replay on this stock; small samples are weak evidence" } };
        const prompt = E.buildPrompt(t, det, detW, style, setups, active("lesson"), active("feedback"), A.note, daily, extra), tA = performance.now();
        const ai = E.validateAi(await callJson(prompt, { images: [{ data: preview }] }), det, det.close, setups);
        rep.ai_secs = Math.round((performance.now() - tA) / 100) / 10; rep.ai = ai; rep.model = A.model; rep.score = ai.score != null ? ai.score : escore;
        ai.grade = ai.grade || E.gradeFor(rep.score); rep.plan = ai.no_trade ? null : ai.plan || eplan;
      } catch (e) { if (e.message === "signed out") throw e; rep.ai_error = e.message; }
    }
    const zref = rep.plan ? det.zones.find((z) => z.id === rep.plan.ref_zone) || null : null;
    rep.track = E.trackRecord(hist, zref); rep.spec = E.buildSpec(det, (rep.ai || {}).draw, rep.plan); rep.secs = Math.round((performance.now() - A.t0) / 100) / 10;
    A.report = rep; A.recents = [t, ...A.recents.filter((x) => x !== t)].slice(0, 8);
  } catch (e) { if (e.message !== "signed out") { A.report = null; A.err = `Could not analyse ${t}: ${e.message}`; } }
  A.busy = false; repaint();
}
const paintProg = () => { const el = document.getElementById("aa-prog"); if (el) el.innerHTML = progHtml(); };
const progHtml = () => { const A = S.aa; return `<div class="aa-prog"><b><i class="aa-spin"></i>${esc((A.steps[A.steps.length - 1] || "Analysing " + A.reqT) + "…")}</b>${A.steps.slice(0, -1).map((s) => `<span>✓ ${esc(s)}</span>`).join("")}</div>`; };

// ── the interactive chart (fig_plotly) ──
function chartSvg(df, spec, window, show, h0) {
  const P = E.prepare(df), n = df.c.length, start = Math.max(0, n - window), m = n - start, ext = Math.max(8, Math.trunc(m * 0.14)), xr = n + ext;
  const W = phone() ? Math.max(340, Math.min(innerWidth - 24, 720)) : 1180, H = h0, L = phone() ? 40 : 54, Rm = phone() ? 118 : 140, top = 8, bot = 22, gap = 8;
  const plotH = H - top - bot - 2 * gap, hP = plotH * 0.68, hV = plotH * 0.13, hR = plotH * 0.19, yV0 = top + hP + gap, yR0 = yV0 + hV + gap;
  const X = (i) => L + ((i - (start - 1)) / (xr - (start - 1))) * (W - L - Rm), bw = Math.max(1, ((W - L - Rm) / (xr - start + 1)) * 0.62);
  let lo = Infinity, hi = -Infinity; for (let i = start; i < n; i++) { lo = Math.min(lo, df.l[i]); hi = Math.max(hi, df.h[i]); }
  const ys = [lo, hi], p = spec.plan;
  if (show.zones) for (const z of spec.zones) if (z.top >= ys[0] * 0.8 && z.bottom <= ys[1] * 1.2) ys.push(z.top, z.bottom);
  if (show.levels) for (const lv of spec.levels) ys.push(lv.price);
  if (show.plan && p) { ys.push(p.stop); for (const t of p.targets) ys.push(t.price); }
  const yMin = Math.min(...ys), yMax = Math.max(...ys), pad = (yMax - yMin) * 0.06 || yMax * 0.01, y0 = yMin - pad, y1 = yMax + pad;
  const Y = (v) => top + ((y1 - v) / (y1 - y0)) * hP, out = [], txt = [], fs = phone() ? 9 : 10, clipId = "aaclip" + (++clipSeq);
  out.push(`<defs><clipPath id="${clipId}"><rect x="0" y="${top}" width="${W}" height="${hP.toFixed(1)}"/></clipPath></defs><g clip-path="url(#${clipId})">`);
  const rgba = (hex, a) => `rgba(${parseInt(hex.slice(1, 3), 16)},${parseInt(hex.slice(3, 5), 16)},${parseInt(hex.slice(5, 7), 16)},${a})`;
  // grid and price axis (right)
  const step = niceStep((y1 - y0) / 6); for (let v = Math.ceil(y0 / step) * step; v <= y1; v += step) { out.push(`<line x1="${L}" x2="${W - Rm}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" class="g"/>`); txt.push(`<text x="${L - 5}" y="${(Y(v) + 3).toFixed(1)}" class="ax" text-anchor="end">${fx(v, v >= 1000 ? 0 : 2)}</text>`); }
  // zones
  if (show.zones) for (const z of spec.zones) { const c = z.type === "demand" ? G : R, x0 = X(Math.max(z.s, start)), xe = X(xr);
    out.push(`<rect x="${x0.toFixed(1)}" y="${Y(z.top).toFixed(1)}" width="${(xe - x0).toFixed(1)}" height="${Math.max(1, Y(z.bottom) - Y(z.top)).toFixed(1)}" fill="${rgba(c, 0.15)}" stroke="${rgba(c, 0.9)}" stroke-width="1"><title>${esc(`${z.id} ${z.pattern} · Q${z.quality} · ${z.status} · ${f(z.bottom)}–${f(z.top)}`)}</title></rect>`);
    out.push(`<line x1="${x0.toFixed(1)}" x2="${xe.toFixed(1)}" y1="${Y(z.distal).toFixed(1)}" y2="${Y(z.distal).toFixed(1)}" stroke="${rgba(c, 0.9)}" stroke-dasharray="5 4"/>`);
    txt.push(`<text x="${(x0 + 3).toFixed(1)}" y="${(z.type === "supply" ? Y(z.top) - 4 : Y(z.bottom) + fs + 2).toFixed(1)}" fill="${c}" font-size="${fs}"><tspan font-weight="700">${z.id}</tspan> ${esc(z.pattern)} · Q${z.quality} · ${esc(z.status)} · ${f(z.bottom)}–${f(z.top)}</text>`); }
  // levels
  if (show.levels) for (const lv of spec.levels) { const c = lv.kind === "support" ? G : R, x0 = X(Math.max(start, lv.first ?? start));
    out.push(`<line x1="${x0.toFixed(1)}" x2="${X(xr).toFixed(1)}" y1="${Y(lv.price).toFixed(1)}" y2="${Y(lv.price).toFixed(1)}" stroke="${rgba(c, 0.75)}" stroke-dasharray="2 3"/>`);
    (lv.touch_idx || []).forEach((i, k) => { if (i >= start) out.push(`<circle cx="${X(i).toFixed(1)}" cy="${Y(lv.touch_px[k]).toFixed(1)}" r="4.5" fill="none" stroke="${c}" stroke-width="1.5"><title>${esc(`${lv.id} touch · ${f(lv.touch_px[k])}`)}</title></circle>`); });
    txt.push(`<text x="${(x0 + 3).toFixed(1)}" y="${(Y(lv.price) - 3).toFixed(1)}" fill="${c}" font-size="${fs - 1}">${lv.id} ${f(lv.price)} (${lv.touches}x)</text>`); }
  // moving averages
  if (show.mas) for (const [k, c] of [["sma20", AM], ["sma50", CY], ["sma200", PU]]) { const pts = []; for (let i = start; i < n; i++) if (isFinite(P[k][i])) pts.push(`${X(i).toFixed(1)},${Y(P[k][i]).toFixed(1)}`); if (pts.length > 1) out.push(`<polyline points="${pts.join(" ")}" fill="none" stroke="${c}" stroke-width="1.1" opacity=".8"/>`); }
  // candles
  for (let i = start; i < n; i++) { const up = df.c[i] >= df.o[i], c = up ? G : R, x = X(i), t = Y(Math.max(df.o[i], df.c[i])), b = Y(Math.min(df.o[i], df.c[i]));
    out.push(`<line x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="${Y(df.h[i]).toFixed(1)}" y2="${Y(df.l[i]).toFixed(1)}" stroke="${c}"/><rect x="${(x - bw / 2).toFixed(1)}" y="${t.toFixed(1)}" width="${bw.toFixed(1)}" height="${Math.max(1, b - t).toFixed(1)}" fill="${c}"/>`); }
  // trendlines
  if (show.lines) for (const t of spec.lines) { const c = t.kind === "support" ? G : R, xe = n - 1 + Math.trunc(ext / 2), slope = (t.p1 - t.p0) / Math.max(1, t.i1 - t.i0);
    out.push(`<line x1="${X(t.i0).toFixed(1)}" x2="${X(xe).toFixed(1)}" y1="${Y(t.p0).toFixed(1)}" y2="${Y(t.p0 + slope * (xe - t.i0)).toFixed(1)}" stroke="${c}" stroke-width="1.6" stroke-dasharray="6 4"><title>${t.id} trendline</title></line>`);
    (t.touch_idx || []).forEach((i, k) => { if (i >= start) { const cx = X(i), cy = Y(t.touch_px[k]); out.push(`<path d="M${cx.toFixed(1)} ${(cy - 5).toFixed(1)}l5 5l-5 5l-5 -5z" fill="none" stroke="${c}" stroke-width="1.5"><title>${esc(`${t.id} touch · ${f(t.touch_px[k])}`)}</title></path>`); } }); }
  // the trade plan
  if (show.plan && p) { const ref = p.entry_ref, stop = p.stop, xa = X(n - 1), xb = X(n - 1 + Math.trunc(ext * 0.8)), t1 = p.targets.length ? p.targets[0].price : ref, box = (a, b, fill, st = "") => out.push(`<rect x="${xa.toFixed(1)}" y="${Y(Math.max(a, b)).toFixed(1)}" width="${(xb - xa).toFixed(1)}" height="${Math.max(1, Math.abs(Y(a) - Y(b))).toFixed(1)}" fill="${fill}" ${st}/>`);
    box(ref, stop, rgba(R, 0.1)); box(ref, t1, rgba(G, 0.1)); box(p.entry_low, p.entry_high, rgba(CY, 0.18), `stroke="${CY}" stroke-width="1"`);
    const tags = [[(p.entry_low + p.entry_high) / 2, `<tspan font-weight="700">ENTRY</tspan> ${f(p.entry_low)}-${f(p.entry_high)}`, CY], [stop, `<tspan font-weight="700">STOP</tspan> ${f(stop)}`, R], ...p.targets.map((t, k) => [t.price, `<tspan font-weight="700">T${k + 1} · ${E.pyRepr(t.rr ?? 0).replace(/\.0$/, "")}R</tspan> ${f(t.price)}`, G])];
    for (const [v, , c] of tags.slice(1)) out.push(`<line x1="${xa.toFixed(1)}" x2="${xb.toFixed(1)}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" stroke="${c}" stroke-width="1.4"/>`);
    let prev = null; for (const [v, s, c] of tags.slice().sort((a, b) => b[0] - a[0])) { let y = Y(v) + 3; if (prev != null && y < prev + fs + 2) y = prev + fs + 2; prev = y; txt.push(`<text x="${(xb + 4).toFixed(1)}" y="${y.toFixed(1)}" fill="${c}" font-size="${fs}">${s}</text>`); } }
  // breakouts and candle signals
  if (show.markers) { for (const mk of spec.markers) if (mk.i >= start) { const x = X(mk.i), y = Y(mk.price), up = mk.dir === "up";
      out.push(`<path d="M${x.toFixed(1)} ${(up ? y - 6 : y + 6).toFixed(1)}l6 ${up ? 10 : -10}h-12z" fill="${up ? G : R}"><title>${esc(mk.label)}</title></path>`); txt.push(`<text x="${x.toFixed(1)}" y="${(up ? y - 9 : y + 18).toFixed(1)}" fill="${IV}" font-size="${fs - 1}" text-anchor="middle">${esc(mk.label)}</text>`); }
    for (const c of spec.candles || []) if (c.i >= start && c.type !== "INSIDE_BAR") { const x = X(c.i), y = c.bias === "bull" ? Y(df.l[c.i] * 0.995) : Y(df.h[c.i] * 1.005);
      out.push(`<path d="M${x.toFixed(1)} ${(y - 4).toFixed(1)}l4 4l-4 4l-4 -4z" fill="${c.bias === "bull" ? CY : AM}"><title>${esc(E.title(c.type.replace(/_/g, " ")))}${c.provisional ? " (today, provisional)" : ""}</title></path>`); } }
  out.push("</g>");
  if (spec.live) txt.push(`<text x="${X(n - 1).toFixed(1)}" y="${(Y(df.h[n - 1]) - 6).toFixed(1)}" fill="${AM}" font-size="${fs - 1}" font-weight="700" text-anchor="middle">LIVE</text>`);
  // volume
  let vmax = 0; for (let i = start; i < n; i++) vmax = Math.max(vmax, df.v[i]);
  for (let i = start; i < n; i++) { const hgt = vmax ? (df.v[i] / vmax) * hV : 0; out.push(`<rect x="${(X(i) - bw / 2).toFixed(1)}" y="${(yV0 + hV - hgt).toFixed(1)}" width="${bw.toFixed(1)}" height="${hgt.toFixed(1)}" fill="${rgba(df.c[i] >= df.o[i] ? G : R, 0.45)}"/>`); }
  // RSI
  const RY = (v) => yR0 + ((100 - v) / 100) * hR; for (const lv of [30, 70]) out.push(`<line x1="${L}" x2="${X(xr).toFixed(1)}" y1="${RY(lv).toFixed(1)}" y2="${RY(lv).toFixed(1)}" stroke="#3a3a3a" stroke-dasharray="2 3"/>`);
  for (const v of [30, 50, 70]) txt.push(`<text x="${L - 5}" y="${(RY(v) + 3).toFixed(1)}" class="ax" text-anchor="end">${v}</text>`);
  const rp = []; for (let i = start; i < n; i++) if (isFinite(P.rsi[i])) rp.push(`${X(i).toFixed(1)},${RY(P.rsi[i]).toFixed(1)}`); if (rp.length > 1) out.push(`<polyline points="${rp.join(" ")}" fill="none" stroke="${AM}" stroke-width="1.3"/>`);
  txt.push(`<text x="${L - 5}" y="${yR0 + 10}" class="ax" text-anchor="end">RSI</text><text x="${L - 5}" y="${yV0 + 10}" class="ax" text-anchor="end">Vol</text>`);
  // dates (_date_ticks)
  const dstep = Math.max(1, Math.trunc((n - start) / (phone() ? 5 : 8)));
  for (let i = start + Math.ceil(dstep / 3); i < n; i += dstep) txt.push(`<text x="${X(i).toFixed(1)}" y="${H - 6}" class="ax" text-anchor="middle">${n - start < 70 ? df.d[i].slice(5) : df.d[i].slice(2)}</text>`);
  const legend = show.mas ? `<div class="aa-leg"><span style="--c:${AM}">SMA 20</span><span style="--c:${CY}">SMA 50</span><span style="--c:${PU}">SMA 200</span></div>` : "";
  const data = { s: start, n, L, W: W - L - Rm, xr, d: df.d.slice(start), o: df.o.slice(start), h: df.h.slice(start), l: df.l.slice(start), c: df.c.slice(start), v: df.v.slice(start), r: Array.from(P.rsi.slice(start)) };
  hoverData = data;
  return `${legend}<div class="aa-chw"><svg class="aa-ch" viewBox="0 0 ${W} ${H}" width="100%" preserveAspectRatio="xMidYMid meet" data-aach="1">${out.join("")}${txt.join("")}<line class="aa-cx" x1="0" x2="0" y1="${top}" y2="${H - bot}" visibility="hidden"/></svg><div class="aa-tip" hidden></div></div>`;
}
function niceStep(x) { const p = 10 ** Math.floor(Math.log10(x || 1)), r = x / p; return (r < 1.5 ? 1 : r < 3 ? 2 : r < 7 ? 5 : 10) * p; }
let hoverData = null, clipSeq = 0;
document.addEventListener("pointermove", (e) => {
  const svg = e.target.closest && e.target.closest("svg[data-aach]"); if (!svg || !hoverData) return;
  const D = hoverData, box = svg.getBoundingClientRect(), vb = svg.viewBox.baseVal, sx = vb.width / box.width, px = (e.clientX - box.left) * sx;
  const i = Math.round((px - D.L) / D.W * (D.xr - (D.s - 1)) + (D.s - 1)) - D.s, tip = svg.parentNode.querySelector(".aa-tip"), cx = svg.querySelector(".aa-cx");
  if (i < 0 || i >= D.d.length) { tip.hidden = true; cx.setAttribute("visibility", "hidden"); return; }
  const x = D.L + ((i + D.s - (D.s - 1)) / (D.xr - (D.s - 1))) * D.W; cx.setAttribute("x1", x); cx.setAttribute("x2", x); cx.setAttribute("visibility", "visible");
  const chg = i ? (D.c[i] / D.c[i - 1] - 1) * 100 : null;
  tip.innerHTML = `<b>${D.d[i]}</b><span>O ${f(D.o[i])} · H ${f(D.h[i])}</span><span>L ${f(D.l[i])} · C ${f(D.c[i])}${chg != null ? ` <i class="${chg >= 0 ? "up" : "dn"}">${chg >= 0 ? "+" : "−"}${fx(Math.abs(chg), 2)}%</i>` : ""}</span><span>Vol ${fx(D.v[i], 0)} · RSI ${isFinite(D.r[i]) ? fx(D.r[i], 0) : "—"}</span>`;
  tip.hidden = false; const left = (x / sx) + 14, w = tip.offsetWidth; tip.style.left = (left + w > box.width ? left - w - 28 : left) + "px";
}, { passive: true });

// ── the annotated picture (render_png): what the AI looks at, and the download ──
export function renderPng(df, spec, window = 110, ttl = "", dpi = 120) {
  const P = E.prepare(df), n = df.c.length, start = Math.max(0, n - window), m = n - start, ext = Math.max(8, Math.trunc(m * 0.16));
  const W = Math.round(14.5 * dpi), H = Math.round(7.4 * dpi), pt = (v) => (v * dpi) / 72, cv = document.createElement("canvas"); cv.width = W; cv.height = H;
  const g = cv.getContext("2d"), font = (sz, b = "") => `${b} ${pt(sz)}px Inter, system-ui, sans-serif`;
  g.fillStyle = "#050505"; g.fillRect(0, 0, W, H);
  const ax = { l: 0.125 * W, r: 0.9 * W, t: 0.12 * H, b: 0.12 * H + 0.62 * H }, av = { t: ax.b + 0.012 * H, b: 0.89 * H };
  const x0 = -1, x1 = m + ext + 14, X = (i) => ax.l + ((i - x0) / (x1 - x0)) * (ax.r - ax.l);
  let lo = Infinity, hi = -Infinity; for (let i = start; i < n; i++) { lo = Math.min(lo, df.l[i]); hi = Math.max(hi, df.h[i]); }
  const ys = [lo, hi], p = spec.plan; for (const z of spec.zones) ys.push(z.top, z.bottom); for (const lv of spec.levels) ys.push(lv.price);
  if (p) { ys.push(p.stop); for (const t of p.targets) ys.push(t.price); }
  const ymn = Math.min(...ys), ymx = Math.max(...ys), pad = (ymx - ymn) * 0.06 || 1, Y = (v) => ax.t + ((ymx + pad - v) / (ymx - ymn + 2 * pad)) * (ax.b - ax.t);
  const line = (xa, ya, xb, yb, c, w = 1, dash = []) => { g.strokeStyle = c; g.lineWidth = w; g.setLineDash(dash); g.beginPath(); g.moveTo(xa, ya); g.lineTo(xb, yb); g.stroke(); g.setLineDash([]); };
  const text = (s, x, y, c, sz = 8, align = "left", base = "middle", b = "") => { g.fillStyle = c; g.font = font(sz, b); g.textAlign = align; g.textBaseline = base; g.fillText(s, x, y); };
  const alpha = (hex, a) => `rgba(${parseInt(hex.slice(1, 3), 16)},${parseInt(hex.slice(3, 5), 16)},${parseInt(hex.slice(5, 7), 16)},${a})`;
  g.strokeStyle = "#2B2B2B"; g.lineWidth = 1; g.strokeRect(ax.l, ax.t, ax.r - ax.l, ax.b - ax.t); g.strokeRect(ax.l, av.t, ax.r - ax.l, av.b - av.t);
  const st = niceStep((ymx - ymn + 2 * pad) / 6); for (let v = Math.ceil((ymn - pad) / st) * st; v <= ymx + pad; v += st) { line(ax.l, Y(v), ax.r, Y(v), "#151515", 0.6 * dpi / 72); text(fx(v, v >= 1000 ? 0 : 2, false), ax.l - pt(4), Y(v), T2, 8, "right"); }
  g.save(); g.beginPath(); g.rect(ax.l, ax.t, ax.r - ax.l, ax.b - ax.t); g.clip();
  for (const z of spec.zones) { const c = z.type === "demand" ? G : R, xa = X(Math.max(z.s, start) - start), xb = X(m + ext);
    g.fillStyle = alpha(c, 0.16); g.fillRect(xa, Y(z.top), xb - xa, Y(z.bottom) - Y(z.top)); g.strokeStyle = c; g.lineWidth = 1; g.strokeRect(xa, Y(z.top), xb - xa, Y(z.bottom) - Y(z.top));
    line(xa, Y(z.distal), xb, Y(z.distal), c, 1.1 * dpi / 72, [pt(4), pt(2)]);
    text(`${z.id} ${z.pattern} Q${z.quality} ${z.status}  ${f(z.bottom)}-${f(z.top)}`, xa + pt(2), z.type === "supply" ? Y(z.top) - pt(3) : Y(z.bottom) + pt(3), c, 8, "left", z.type === "supply" ? "bottom" : "top"); }
  for (const lv of spec.levels) { const c = lv.kind === "support" ? G : R, xa = X(Math.max(0, (lv.first ?? start) - start));
    line(xa, Y(lv.price), X(m + ext), Y(lv.price), alpha(c, 0.8), dpi / 72, [pt(1), pt(2)]);
    (lv.touch_idx || []).forEach((i, k) => { if (i >= start) { g.strokeStyle = c; g.lineWidth = 1.2 * dpi / 72; g.beginPath(); g.arc(X(i - start), Y(lv.touch_px[k]), pt(3.4), 0, 7); g.stroke(); } });
    text(`${lv.id} ${f(lv.price)} (${lv.touches}x)`, xa + pt(2), Y(lv.price) - pt(2), c, 8, "left", "bottom"); }
  for (let i = start; i < n; i++) { const c = df.c[i] >= df.o[i] ? G : R, x = X(i - start), bwid = ((ax.r - ax.l) / (x1 - x0)) * 0.62, body = Math.max(Math.abs(df.c[i] - df.o[i]), (hi - lo) * 0.0012);
    line(x, Y(df.h[i]), x, Y(df.l[i]), c, dpi / 72); g.fillStyle = c; g.fillRect(x - bwid / 2, Y(Math.min(df.o[i], df.c[i]) + body), bwid, Y(Math.min(df.o[i], df.c[i])) - Y(Math.min(df.o[i], df.c[i]) + body)); }
  for (const [k, c] of [["sma20", AM], ["sma50", CY]]) { g.strokeStyle = alpha(c, 0.85); g.lineWidth = dpi / 72; g.beginPath(); let on = false; for (let i = start; i < n; i++) { if (!isFinite(P[k][i])) { on = false; continue; } const x = X(i - start), y = Y(P[k][i]); if (on) g.lineTo(x, y); else g.moveTo(x, y); on = true; } g.stroke(); }
  for (const t of spec.lines) { const c = t.kind === "support" ? G : R, slope = (t.p1 - t.p0) / Math.max(1, t.i1 - t.i0), xe = n - 1 + Math.trunc(ext / 2);
    line(X(t.i0 - start), Y(t.p0), X(xe - start), Y(t.p0 + slope * (xe - t.i0)), c, 1.4 * dpi / 72, [pt(4), pt(2)]);
    (t.touch_idx || []).forEach((i, k) => { if (i >= start) { const x = X(i - start), y = Y(t.touch_px[k]), r = pt(3.2); g.strokeStyle = c; g.lineWidth = 1.2 * dpi / 72; g.beginPath(); g.moveTo(x, y - r); g.lineTo(x + r, y); g.lineTo(x, y + r); g.lineTo(x - r, y); g.closePath(); g.stroke(); } }); }
  g.restore();
  if (p) { const xa = X(m - 1), xb = X(m - 1 + Math.trunc(ext * 0.85)), ref = p.entry_ref, stop = p.stop, t1 = p.targets.length ? p.targets[0].price : ref;
    const rect = (a, b, c, al, edge) => { g.fillStyle = alpha(c, al); g.fillRect(xa, Y(Math.max(a, b)), xb - xa, Math.abs(Y(a) - Y(b))); if (edge) { g.strokeStyle = c; g.lineWidth = 1; g.strokeRect(xa, Y(Math.max(a, b)), xb - xa, Math.abs(Y(a) - Y(b))); } };
    rect(ref, stop, R, 0.12); rect(ref, t1, G, 0.12); rect(p.entry_low, p.entry_high, CY, 0.22, true);
    const tags = [[(p.entry_low + p.entry_high) / 2, `ENTRY ${f(p.entry_low)}-${f(p.entry_high)}`, CY]];
    for (const [v, lab, c] of [[stop, "STOP", R], ...p.targets.map((t, k) => [t.price, `T${k + 1} ${E.pyRepr(t.rr ?? 0).replace(/\.0$/, "")}R`, G])]) { line(xa, Y(v), xb, Y(v), c, 1.4 * dpi / 72); tags.push([v, `${lab} ${f(v)}`, c]); }
    const gp = (ymx - ymn) * 0.028; let prev = null; for (const [v, s, c] of tags.sort((a, b) => a[0] - b[0] || (a[1] < b[1] ? -1 : 1))) { const ty = prev == null ? v : Math.max(v, prev + gp); text(s, xb + pt(2), Y(ty), c, 8); prev = ty; } }
  for (const mk of spec.markers) if (mk.i >= start) { const x = X(mk.i - start), y = Y(mk.price), r = pt(4.5), up = mk.dir === "up"; g.fillStyle = up ? G : R; g.beginPath(); g.moveTo(x, up ? y - r : y + r); g.lineTo(x + r, up ? y + r : y - r); g.lineTo(x - r, up ? y + r : y - r); g.closePath(); g.fill(); }
  if (spec.live) text("LIVE", X(m - 1), Y(df.h[n - 1] + (ymx - ymn) * 0.02), AM, 7, "center", "middle", "700");
  let vmax = 0; for (let i = start; i < n; i++) vmax = Math.max(vmax, df.v[i]);
  for (let i = start; i < n; i++) { const hgt = vmax ? (df.v[i] / vmax) * (av.b - av.t) * 0.95 : 0, x = X(i - start), bwid = ((ax.r - ax.l) / (x1 - x0)) * 0.62; g.fillStyle = alpha(df.c[i] >= df.o[i] ? G : R, 0.5); g.fillRect(x - bwid / 2, av.b - hgt, bwid, hgt); }
  for (let k = 0; k < m; k += Math.max(1, Math.trunc(m / 9))) text(df.d[start + k].slice(2), X(k), av.b + pt(8), T2, 8, "center");
  text(ttl, 0.012 * W, 0.035 * H, IV, 12, "left", "middle", "700");
  text("Arka AI · research tool, not advice · prices as traded (not dividend-adjusted)", 0.988 * W, 0.035 * H, T3, 8, "right");
  return cv;
}
const pngBase64 = (df, spec, window, ttl, dpi) => renderPng(df, spec, window, ttl, dpi).toDataURL("image/png").split(",")[1];

// ── report blocks (html_hero, html_plan, html_scorecard …) ──
const VCOL = { g: G, a: AM, m: T3, r: R };
function ring(score, color) {
  const c = 2 * Math.PI * 34;
  return `<svg width="92" height="92" viewBox="0 0 92 92"><circle cx="46" cy="46" r="34" fill="none" stroke="#232326" stroke-width="6"/><circle cx="46" cy="46" r="34" fill="none" stroke="${color}" stroke-width="6" stroke-linecap="round" stroke-dasharray="${fx((Math.max(0, Math.min(100, score)) / 100) * c, 1, false)} ${fx(c, 1, false)}" transform="rotate(-90 46 46)"/>
    <text x="46" y="51" text-anchor="middle" font-size="24" font-weight="600" fill="#EDEDEF">${score}</text><text x="46" y="66" text-anchor="middle" font-size="9" fill="#71717A">score</text></svg>`;
}
function hero(r) {
  const det = r.det, ai = r.ai || {}, [label, vc] = E.verdictText(r), color = VCOL[vc], score = r.score, grade = ai.grade || E.gradeFor(score), chg = det.day_chg_pct, struct = det.structure;
  const bias = ai.bias || (struct.startsWith("UPTREND") ? "BULLISH" : struct.startsWith("DOWNTREND") ? "BEARISH" : "NEUTRAL"), state = ai.setup_state || (det.zones.some((z) => z.in_zone) ? "PRICE IN ZONE" : "WATCHING");
  let chips = chip(`Grade ${grade}`, ["A+", "A"].includes(grade) ? "ok" : ["B", "C"].includes(grade) ? "warn" : "bad") + chip(nice(bias), bias === "BULLISH" ? "ok" : bias === "BEARISH" ? "bad" : "") + chip(nice(state)) + chip(r.ai ? "AI + engine" : "Engine only", r.ai ? "ok" : "warn");
  const mk = r.market;
  if (mk) { const ns = mk.nifty_structure.split(" ")[0]; chips += chip(`NIFTY ${ns.toLowerCase()}`, ns === "UPTREND" ? "ok" : ns === "DOWNTREND" ? "bad" : "");
    if (mk.rs_3m_pp != null) chips += chip(`vs NIFTY 3M ${fx(mk.rs_3m_pp, 1, false, true)} pp`, mk.rs_3m_pp > 0 ? "ok" : "bad"); }
  if (r.results) { const days = Math.round((Date.parse(r.results) - Date.parse(today())) / 864e5); if (days >= 0 && days <= 14) chips += chip(days ? `Results in ${days} day${days !== 1 ? "s" : ""}` : "Results today", "warn"); }
  if (det.live) chips += chip("Today's candle unfinished", "warn");
  const p = r.plan, t1 = p && p.targets.length ? p.targets[0] : null;
  const mini = p ? `<div class="aa-mini"><div><span>${E.cap(p.direction)} entry</span><b>${f(p.entry_ref)}</b></div><div><span>Stop</span><b class="dn">${f(p.stop)}</b></div><div><span>Target 1</span><b class="up">${t1 ? f(t1.price) : "—"}</b></div><div><span>Reward : risk</span><b>${t1 ? E.pyRepr(t1.rr) + " R" : "—"}</b></div></div>`
    : '<div class="aa-empty" style="padding:14px">No trade plan right now.<br>Waiting for price to reach one of your zones.</div>';
  const head = ai.headline || E.engineHeadline(det), timing = r.secs != null ? ` · analysed in ${r.secs} s` : "";
  return `<div class="aa-hero" style="--vc:${color}"><div><div class="aa-sym">${esc(r.ticker)} · daily</div><div class="aa-px">₹${f(det.close)}<em class="${chg >= 0 ? "up" : "dn"}">${chg >= 0 ? "+" : "−"}${fx(Math.abs(chg), 2, false)}%</em></div>
    <div class="aa-sub">As of ${esc(det.asof)} · ATR ${E.pyRepr(det.atr_pct)}% · RSI ${f(det.rsi, 0)}</div><div class="aa-hm"><span class="aa-verdict" style="--vc:${color}">${esc(label)}</span><div class="aa-chips">${chips}</div></div></div>
    <div class="aa-ring">${ring(score, color)}</div><div>${mini}</div></div><div class="aa-head">${esc(head)}<span>${esc(r.ran_at)}${timing}${r.ai_secs != null ? ` · AI ${r.ai_secs} s` : ""}${r.model ? " · " + esc(r.model) : ""}</span></div>`;
}
function planCard(r, risk) {
  const p = r.plan, ai = r.ai || {};
  if (!p) { const why = ai.no_trade ? "The AI found no entry that fits your playbook right now." : "No zone is close enough to price for a clean plan.", e = r.engine_plan;
    const alt = ai.no_trade && e ? `<div class="aa-note">Engine alternative (not matched to your playbook): ${e.direction} from ${esc(e.ref_zone)}, entry ${f(e.entry_low)}-${f(e.entry_high)}, stop ${f(e.stop)}.</div>` : "";
    return `<div class="aa-card"><div class="aa-ct">Trade plan<span>none</span></div><div class="aa-empty in">${esc(why)}</div>${alt}</div>`; }
  const d = p.direction === "LONG" ? "up" : "dn", kv = (k, v) => `<div class="aa-kv"><span>${k}</span>${v}</div>`;
  const rows = [kv("Direction", `<b class="${d}">${E.cap(p.direction)}${p.counter_trend ? " · counter-trend" : ""}</b>`), kv("Based on", `<b>${esc(p.ref_zone || "-")}</b>`), kv("Entry zone", `<b class="cy">${f(p.entry_low)} – ${f(p.entry_high)}</b>`),
    kv("Entry reference", `<b>${f(p.entry_ref)}</b>`), kv("Stop", `<div><b class="dn">${f(p.stop)}</b><em>${esc(p.stop_reason || "")}</em></div>`)];
  p.targets.forEach((t, i) => rows.push(kv(`Target ${i + 1} · ${esc(t.ref ?? "")}`, `<div><b class="up">${f(t.price)}</b><em>${t.rr != null ? E.pyRepr(t.rr) + " R" : ""} · ${esc(t.reason || "")}</em></div>`)));
  if (p.room_r != null) rows.push(kv("Room before first obstacle", `<div><b class="${p.blocked ? "dn" : p.room_r >= 2 ? "up" : ""}">${E.pyRepr(p.room_r).replace(/\.0$/, "")} R</b><em>${esc(p.obstacle || "")} at ${f(p.obstacle_price)}${p.obstacle_flips ? " · price must break this level to reach the entry, after which it is in the way" : ""}${p.blocked ? " · less than 1R: the trade risks more than it has room to make" : ""}</em></div>`));
  const sz = E.positionSize(p, risk);
  if (sz.qty != null) rows.push(kv(`Position size @ ₹${fx(risk, 0)} risk`, `<div><b>${fx(sz.qty, 0)} shares</b><em>₹${E.pyRepr(sz.risk_per_share)} risk per share · about ₹${fx(sz.capital, 0)} deployed</em></div>`));
  let extra = ""; if (p.invalidation) extra += `<div class="aa-note"><b>Invalidation:</b> ${esc(p.invalidation)}</div>`;
  if ((p.confirmation_needed || []).length) extra += `<div class="aa-note"><b>Wait for:</b> ${p.confirmation_needed.map(esc).join(" · ")}</div>`;
  return `<div class="aa-card"><div class="aa-ct">Trade plan<span>${p.source === "ai" ? "from Arka AI" : "from the engine"}</span></div>${rows.join("")}${extra}</div>`;
}
function scorecard(r) {
  const ai = r.ai;
  if (!ai) { const msg = !r.ai_error ? "AI is offline, so your written rules cannot be checked. Zones, levels and the plan above come from the engine." : `AI analysis failed: ${r.ai_error}`;
    return `<div class="aa-card"><div class="aa-ct">Setup scorecard<span>engine only</span></div><div class="aa-empty in">${esc(msg)}</div></div>`; }
  if (!ai.setups.length) return '<div class="aa-card"><div class="aa-ct">Setup scorecard<span>no setup taught</span></div><div class="aa-empty in">No active setup in your playbook.<br>Open Teach to import a PDF or write your rules.</div></div>';
  const col = { PRESENT: G, NOT_PRESENT: R, UNCLEAR: AM }, lab = { PRESENT: "Present", NOT_PRESENT: "Not present", UNCLEAR: "Unclear" }, out = ['<div class="aa-card"><div class="aa-ct">Setup scorecard<span>your rules, one by one</span></div>'];
  for (const s of ai.setups) {
    out.push(`<div class="aa-sh"><div>${esc(s.name)}</div><span>${s.match_pct}% · ${s.present || 0} of ${s.required || 0} required present${s.unclear ? ` · ${s.unclear} unclear` : ""}</span></div>`);
    for (const c of s.checks) { const kind = (c.required === false ? '<span class="aa-ref o">optional</span>' : "") + (c.group === "confirmation" ? '<span class="aa-ref p">confirmation</span>' : "");
      out.push(`<div class="aa-ck" style="--c:${col[c.status]}"><div class="h"><div><i>${esc(c.rule_id || "")}</i>${esc(c.rule_name)}${kind}${c.refs.map((x) => `<span class="aa-ref">${esc(x)}</span>`).join("")}</div><span>${lab[c.status]}</span></div>
        <div class="r">${esc(c.reason)}</div><div class="b"><i style="width:${c.confidence}%"></i></div></div>`); }
  }
  return out.join("") + "</div>";
}
function readCard(ai) {
  if (!ai) return '<div class="aa-card"><div class="aa-ct">The read<span>AI offline</span></div><div class="aa-empty in">Supports, contradictions and what to watch appear here when the AI is online.</div></div>';
  const block = (t, xs, c) => `<div class="aa-sh"><div>${t}</div></div><ul class="aa-list" style="--c:${c}">${xs.map((x) => `<li>${esc(x)}</li>`).join("") || '<li class="mu">Nothing to report.</li>'}</ul>`;
  return `<div class="aa-card"><div class="aa-ct">The read<span>against your playbook</span></div>${block("What supports your setup", ai.supports, G)}${block("What contradicts it", ai.contradicts, R)}${block("Watch before acting", ai.watch_next, AM)}</div>`;
}
const qbar = (q, c) => `<span class="aa-q" style="--c:${c};--w:${q}%"><i></i>${q}</span>`;
function zonesTable(r) {
  const ai = r.ai || {}, det = r.det, mine = Object.fromEntries((ai.my_zones || []).map((z) => [z.id, z]));
  const rows = det.zones.slice().sort((a, b) => (a.dist_atr ?? 99) - (b.dist_atr ?? 99)).map((z) => {
    const c = z.type === "demand" ? G : R, qc = z.quality >= 70 ? G : z.quality >= 50 ? AM : R, m = mine[z.id], flags = [];
    if (z.htf) flags.push("Weekly confluence"); if (z.bos) flags.push("Broke structure"); if (z.aligned) flags.push("With trend"); if (m && m.matches_my_style) flags.push("Your style");
    return `<tr><td><b>${z.id}</b></td><td>${pill(E.cap(z.type), c)}</td><td>${esc(z.pattern)}</td><td class="r">${f(z.bottom)} – ${f(z.top)}</td><td class="r">${pf(z.thickness_atr)} ATR</td><td>${qbar(z.quality, qc)}</td><td>${esc(z.status)}</td>
      <td class="r">${f(z.dist_pct)}% · ${pf(z.dist_atr)} ATR</td><td class="r">${esc(z.formed)}</td><td>${m ? esc(m.role.replace(/_/g, " ")) : "—"}</td><td>${flags.map((x) => pill(x, CY)).join(" ") || "—"}</td></tr>`; });
  if (!rows.length) return '<div class="aa-empty">No zones detected with your current zone style.</div>';
  return table(["ID", "Type", "Pattern", "Zone", "Thickness", "Quality", "Status", "Distance", "Formed", "Role", "Flags"], rows);
}
function levelsTable(r) {
  const det = r.det, rows = det.levels.slice().sort((a, b) => b.price - a.price).map((lv) => `<tr><td><b>${lv.id}</b></td><td>${pill(E.cap(lv.kind), lv.kind === "support" ? G : R)}</td><td class="r">${f(lv.price)}</td><td class="r">${lv.touches}</td><td class="r">${f(lv.dist_pct)}%</td><td>${lv.flip ? "role reversal" : "—"}</td></tr>`);
  for (const t of det.trendlines) rows.push(`<tr><td><b>${t.id}</b></td><td>${pill("Trendline · " + t.kind, t.kind === "support" ? G : R)}</td><td class="r">${f(t.p1)}</td><td class="r">${t.touches}</td><td class="r">${f(t.dist_pct)}%</td><td>slope ${E.pyRepr(t.slope_pct_per_bar)}%/bar</td></tr>`);
  if (!rows.length) return '<div class="aa-empty">No support / resistance levels with enough touches.</div>';
  return table(["ID", "Kind", "Price", "Touches", "Distance", "Note"], rows);
}
const table = (head, rows, cl = "") => `<div class="aa-tw"><table class="sc-t aa-t ${cl}"><thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
function scenarios(ai) {
  if (!ai) return "";
  return `<div class="aa-scn">${[["bull", "Bull case", G], ["bear", "Bear case", R], ["neutral", "Neutral / range", AM]].map(([k, t, c]) => { const s = ai.scenarios[k];
    const lvl = typeof s.level === "number" ? f(s.level) + (s.ref ? ` · ${esc(s.ref)}` : "") : "—";
    return `<div class="aa-sc" style="--c:${c}"><div class="t">${t}</div><div class="l">${lvl}</div><div class="d"><b>Trigger:</b> ${esc(tx(s.trigger))}<br><b>Path:</b> ${esc(tx(s.path))}</div></div>`; }).join("")}</div>`;
}
const pctCell = (n, p, base = null) => (!n || p == null ? '<td class="r mu">—</td>' : `<td class="r" style="color:${base == null ? "#D4D4D8" : p > base ? G : p < base - 5 ? R : "#D4D4D8"}">${fx(p, 0, false)}% <span class="mu">of ${n}</span></td>`);
function trackTable(tr, universe = null) {
  const g = tr.groups; if (!g["All zones"].formed) return '<div class="aa-empty">No zone of your style formed on this stock in the last two years.</div>';
  const rows = [], line = (label, x, bold) => rows.push(`<tr><td>${bold ? `<b>${esc(label)}</b>` : esc(label)}</td><td class="r">${x.formed}</td><td class="r">${x.tested}</td>${pctCell(x.n1, x.p1, 50)}${pctCell(x.n2, x.p2, 33.3)}<td class="r">${x.exp2 == null ? "—" : `<span style="color:${x.exp2 > 0 ? G : x.exp2 < 0 ? R : T2}">${fx(x.exp2 || 0, 2, false, true)} R</span>`}</td></tr>`);
  if (tr.like && tr.like_label) line("Like the plan's zone · " + tr.like_label, tr.like, true);
  for (const k of E.TRACK_GROUPS) if (g[k].formed) line(k, g[k]);
  if (universe && universe.groups && universe.groups["All zones"] && universe.groups["All zones"].formed) line(`Your last radar scan · ${universe.n_stocks || 0} stocks · all zones`, universe.groups["All zones"], true);
  const col = { "2R": G, STOP: R, OPEN: AM };
  const recent = tr.recent && tr.recent.length ? `<div class="aa-note">Latest tested zones: ${tr.recent.slice(0, 6).map((x) => `${esc(x.touch_date)} ${x.type} Q${x.quality} <b style="color:${col[x.outcome] || T2}">${x.outcome === "2R" ? "reached 2R" : x.outcome === "STOP" ? "stopped" + (x.hit1 ? " after 1R" : "") : "still open"}</b>`).join(" · ")}</div>` : "";
  return table(["Zones of your style", "Formed", "Tested", "Reached 1R before stop", "Reached 2R before stop", "Average result aiming for 2R"], rows, "wrap1") + recent;
}
function statusChips() {
  const A = S.aa, nSet = active("setup").length, nLes = active("lesson").length, nLab = active("feedback").length, nCalls = items().filter((i) => i.kind === "call").length, src = styleSource(), pl = (n, w) => `${n} ${w}${n !== 1 ? "s" : ""}`;
  const chips = [chip(A.ai ? "AI online" + (A.model ? " · " + A.model : "") : A.ai === false ? "AI offline · engine only" : "Checking AI…", A.ai ? "ok" : "warn"),
    chip(A.cloud ? "Playbook saved to cloud" : A.cloud === false ? "Playbook: this browser tab only" : "Loading playbook…", A.cloud ? "ok" : "warn"),
    chip(pl(nSet, "active setup"), nSet ? "ok" : "bad"), chip(pl(nLes, "lesson"), nLes ? "ok" : ""), chip(pl(nLab, "labelled set"), nLab ? "ok" : ""),
    chip("Zone style: " + (src === "DEFAULT" ? "default" : src), src !== "DEFAULT" ? "ok" : ""), chip(`${pl(nCalls, "plan")} in journal`, nCalls ? "ok" : "")];
  if (A.itemsErr) chips.push(chip("Playbook could not load: " + A.itemsErr, "bad"));
  return `<div class="aa-chips">${chips.join("")}</div>`;
}
const gettingStarted = () => `<div class="aa-intro"><b>Teach once. Ask about any stock.</b> Arka learns how <b>you</b> read a chart (your setups, your supply and demand zones, your lines) and then analyses any stock the same way, marking everything on the chart.</div>
  <div class="aa-steps"><div class="aa-step"><div class="n">Step 1 · Teach</div><div class="t">Show Arka your style</div><div class="d">Import your strategy PDF, tick the zones you like and cross the ones you don't, or upload charts you marked up.</div></div>
  <div class="aa-step"><div class="n">Step 2 · Analyze</div><div class="t">Type a stock</div><div class="d">Every zone, level and trendline is detected with exact prices, then judged against your playbook.</div></div>
  <div class="aa-step"><div class="n">Step 3 · Act</div><div class="t">Get the marked-up chart</div><div class="d">Verdict, scorecard, entry zone, stop, targets, position size and scenarios, ready to download.</div></div></div>`;
function report(r) {
  const A = S.aa, ai = r.ai, d = r.det, fixes = (ai || {}).corrections || [];
  let o = hero(r);
  if (r.ai_error) o += `<div class="aa-warn">The AI step failed, so this is the engine-only view. Reason: ${esc(r.ai_error)}</div>`;
  o += `<div class="seg aa-layers">${LAYERS.map(([k, t]) => `<button class="${A.show[k] ? "on" : ""}" data-act="aalayer" data-v="${k}">${t}</button>`).join("")}</div>`;
  o += `<div class="card aa-chart">${chartSvg(r.df, r.spec, A.window, A.show, phone() ? 470 : 700)}</div>`;
  o += `<div class="aa-three">${planCard(r, A.risk)}${scorecard(r)}${readCard(ai)}</div>`;
  if (fixes.length) o += `<details class="card sc-det"><summary>Engine check of the AI · ${fixes.length} correction${fixes.length !== 1 ? "s" : ""} applied</summary><div class="pad"><div class="mini">Everything the AI says is matched back to your playbook rules and to the detected zones and levels. These are the points where its reply did not match and what was done.</div><ul class="aa-ul">${fixes.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div></details>`;
  if (ai) o += sec("Scenarios", "what would change the picture") + scenarios(ai);
  if (r.track) o += sec("Track record of your zone style", `every zone of this style on ${r.ticker} in the last 2 years, replayed with no hindsight`) + trackTable(r.track, A.trackUniv)
    + `<div class="aa-cap">How to read it: a zone is 'tested' the first time price returns to it. Entry at the proximal line, stop 0.25 ATR beyond the distal line. Reaching 2R before the stop one time in three is break-even before costs; on random price data this replay gives about 30%, so a real edge has to sit clearly above that. Fewer than 20 tested zones is too small a sample to trust. Daily candles only: if one candle touches both stop and target the stop is counted, and brokerage and slippage are not included.</div>`;
  o += sec("Supply & demand zones found", "every zone the engine detects, with your style applied") + zonesTable(r) + sec("Support, resistance and trendlines") + levelsTable(r);
  if (ai && ai.narrative) o += `<details class="card sc-det"><summary>Full analysis</summary><div class="pad"><div class="aa-narr">${esc(ai.narrative)}</div>${ai.context.length ? `<b>Context (not used for the verdict)</b><ul class="aa-ul">${ai.context.map((c) => `<li>${esc(c)}</li>`).join("")}</ul>` : ""}</div></details>`;
  o += `<details class="card sc-det"><summary>Engine evidence <span>the exact numbers the AI was given</span></summary><div class="pad">${pretty({ market: r.market, next_results_date: r.results, ai_model: r.model || null, structure: d.structure, ma_state: d.ma_state, sma: d.sma, volume: d.volume, refs: d.refs, swings: d.swings, events: d.events, candle_signals: d.candles, weekly: r.det_w ? { structure: r.det_w.structure, ma_state: r.det_w.ma_state } : null })}</div></details>`;
  if (ai) { const qa = A.qa.filter((x) => x[0] === r.ticker).slice(-4).reverse();
    o += sec("Ask Arka about this chart", "follow-up questions use the same data") + `<div class="aa-ask"><input data-aa="ask" value="${esc(A.ask)}" placeholder="e.g. Why is DZ1 better than DZ2? Where would you trail the stop?" autocomplete="off"><button class="btn pri" data-act="aaask"${A.asking ? " disabled" : ""}>${A.asking ? "Thinking…" : "Ask Arka"}</button></div>`
      + qa.map(([, q, a]) => `<div class="aa-qa"><b class="mu">You</b>${esc(q)}</div><div class="aa-qa a"><b>Arka</b>${esc(a)}</div>`).join(""); }
  const p = r.plan;
  o += sec("Export") + `<div class="aa-exp"><button class="btn" data-act="aapng">Download chart (PNG)</button><button class="btn" data-act="aajson">Download report (JSON)</button><button class="btn" data-act="aawl">Add to watchlist</button><button class="btn pri" data-act="aalog"${p ? "" : " disabled"} title="Saves the plan to your Journal. Arka then checks every day whether it triggered, hit target 1 or was stopped.">Log this plan</button></div>`;
  return o;
}

// ── modes ──
function analyzeView() {
  const A = S.aa, dis = A.ai === false ? " disabled" : "", ph = phone();
  const tickerBox = `<label class="f aa-tk"><span>Stock</span><input data-aa="ticker" value="${esc(A.ticker)}" placeholder="RELIANCE, TCS, HDFCBANK, NIFTY50…" autocapitalize="characters" autocomplete="off" spellcheck="false"></label>`;
  const opts = `<label class="f"><span>Candles shown</span><select data-aa="window">${[80, 120, 180, 250].map((x) => `<option${x === A.window ? " selected" : ""}>${x}</option>`).join("")}</select></label>
    <label class="f"><span>Risk per trade (₹)</span><input data-aa="risk" type="number" min="0" step="500" value="${esc(A.risk)}" inputmode="decimal"></label>
    <label class="f" title="How long Arka AI may think. Fast is quickest, Deep is most thorough (slowest)."><span>Speed</span><select data-aa="speed">${Object.keys(SPEEDS).map((x) => `<option${x === A.speed ? " selected" : ""}>${x}</option>`).join("")}</select></label>`;
  const note = `<label class="f aa-note-in"><span>Note for Arka (optional)</span><input data-aa="note" value="${esc(A.note)}" placeholder="e.g. I'm only interested in long setups from fresh demand zones" autocomplete="off"></label>`;
  const toggle = `<label class="aa-tg"><input type="checkbox" data-aa="useAi"${A.useAi ? " checked" : ""}${dis}><span>Use Arka AI</span></label>`, go = `<button class="btn pri" data-act="aago"${A.busy ? " disabled" : ""}>Analyze</button>`;
  const form = ph ? `<div class="aa-form">${tickerBox}${go}<details class="aa-opt"${A.optOpen ? " open" : ""} data-aad="optOpen"><summary>Options</summary><div class="aa-g2">${opts}</div>${note}${toggle}</details></div>`
    : `<div class="aa-form"><div class="aa-row1">${tickerBox}${opts}</div><div class="aa-row2">${note}${toggle}${go}</div></div>`;
  const rec = A.recents.length ? `<div class="aa-rec">${A.recents.slice(0, 8).map((s) => `<button data-act="aarun" data-v="${esc(s)}">${esc(s)}</button>`).join("")}</div>` : "";
  let body = "";
  if (A.busy) body = `<div class="card pad" id="aa-prog">${progHtml()}</div>`;
  else if (A.err) body = `<div class="aa-warn r">${esc(A.err)}</div>`;
  if (A.report && !A.busy) body += report(A.report);
  else if (!A.busy && !A.err) body += !(active("setup").length || active("lesson").length || active("feedback").length) ? gettingStarted() : '<div class="aa-empty">Type a stock above and press <b>Analyze</b>.<br>Your playbook is loaded and ready.</div>';
  return form + rec + body;
}
async function radarScan() {
  const A = S.aa, X = A.radar, syms = radarSymbols().slice(0, 40); if (!syms.length || X.busy) return;
  X.busy = true; X.err = ""; X.prog = `Scanning ${syms.length} stocks…`; repaint();
  try {
    const frames = await fetchMany(syms, (k) => { X.prog = `Downloading prices · ${k} of ${syms.length}`; const el = document.getElementById("aa-rprog"); if (el) el.textContent = X.prog; });
    const style = getStyle(), rows = [], hist = [];
    for (const [sym, df] of Object.entries(frames)) { try { if (X.track) hist.push(...E.zoneHistory(df, style)); rows.push(E.radarRow(sym, df, style, E.liveFraction(df.d[df.d.length - 1]))); } catch { /* skip that stock */ } await tick(); }
    rows.sort((a, b) => b.score - a.score);
    let tr = null; if (X.track) { tr = E.trackRecord(hist); tr.n_stocks = Object.keys(frames).length; A.trackUniv = tr; }
    X.res = { rows, at: `${String(istNow().getUTCDate()).padStart(2, "0")} ${MON[istNow().getUTCMonth()]} ${String(istNow().getUTCHours()).padStart(2, "0")}:${String(istNow().getUTCMinutes()).padStart(2, "0")} IST`, track: tr };
  } catch (e) { if (e.message !== "signed out") X.err = "Scan failed: " + e.message; }
  X.busy = false; repaint();
}
function radarSymbols() {
  const X = S.aa.radar; let s;
  if (X.src === "My watchlist") { const list = S.wl.list === "Arka desk" ? "Main" : S.wl.list; s = S.wl.rows.filter((r) => r.list === list).map((r) => r.sym); }
  else if (X.src === "Arka desk list") s = (S.wl.desk && S.wl.desk.syms) || [];
  else s = X.txt.split(/[,\n;\s]+/).map((x) => x.trim().toUpperCase()).filter(Boolean);
  return [...new Set(s)];
}
function radarView() {
  const A = S.aa, X = A.radar, syms = radarSymbols();
  if (X.src === "Arka desk list" && !S.wl.desk) { S.wl.desk = { syms: [], loading: true }; api("/api/watchlist?desk=1").then((d) => { S.wl.desk = d; paint(); }).catch(() => { S.wl.desk = { syms: [] }; paint(); }); }
  let o = sec("Radar", "scan a list with the same zone engine: which stocks are sitting at your zones right now");
  o += `<div class="seg">${["My watchlist", "Arka desk list", "Type symbols"].map((x) => `<button class="${x === X.src ? "on" : ""}" data-act="aarsrc" data-v="${x}">${x}</button>`).join("")}</div>`;
  if (X.src === "Type symbols") o += `<label class="f"><span>Symbols (comma or line separated)</span><textarea data-aa="rtxt" rows="3" placeholder="RELIANCE, TCS, INFY, HDFCBANK">${esc(X.txt)}</textarea></label>`;
  o += `<div class="aa-cap">${syms.length > 40 ? `Scanning the first 40 of ${syms.length} symbols. ` : ""}${Math.min(40, syms.length)} symbol(s) · uses your zone style (${esc(styleSource())}) · no AI cost</div>
    <label class="aa-tg"><input type="checkbox" data-aa="rtrack"${X.track ? " checked" : ""}><span>Also replay the track record of my zone style on these stocks (2 years, a few seconds more)</span></label>
    <div class="aa-exp"><button class="btn pri" data-act="aarscan"${!syms.length || X.busy ? " disabled" : ""}>${X.busy ? "Scanning…" : "Scan for my zones"}</button>${X.busy ? `<span class="mini" id="aa-rprog">${esc(X.prog)}</span>` : ""}</div>`;
  if (X.err) o += `<div class="aa-warn r">${esc(X.err)}</div>`;
  const res = X.res;
  if (!res) return o + '<div class="aa-empty">Press <b>Scan for my zones</b> to rank your list by how ready each stock is.</div>';
  if (!res.rows.length) return o + '<div class="aa-warn">No usable price history came back for these symbols.</div>';
  const rows = res.rows.map((x) => { const sc = x.score, scol = sc >= 72 ? G : sc >= 50 ? AM : T3, zc = x.ztype === "demand" ? G : x.ztype === "supply" ? R : T3;
    return `<tr><td><a class="sc-lk" data-act="aarun" data-v="${esc(x.symbol)}">${esc(x.symbol)}</a></td><td class="r">${f(x.close)}</td><td class="r" style="color:${x.chg >= 0 ? G : R}">${x.chg >= 0 ? "▲" : "▼"} ${fx(Math.abs(x.chg), 2, false)}%</td><td>${esc(x.structure)}</td>
      <td class="r">${f(x.rsi, 0)}</td><td>${x.zone ? pill(E.cap(x.ztype), zc) + " " + esc(x.zone) : "—"}</td><td class="r">${x.quality ?? "—"}</td><td>${esc(x.status || "—")}</td><td class="r">${x.dist_pct != null ? f(x.dist_pct) + "%" : "—"}</td>
      <td>${esc(x.direction || "—")}</td><td class="r">${x.rr ? E.pyRepr(x.rr) + " R" : "—"}</td><td class="r" style="color:${x.blocked ? R : "#CFCFCF"}">${x.room_r != null ? E.pyRepr(x.room_r).replace(/\.0$/, "") + " R" : "—"}</td><td>${qbar(sc, scol)}</td></tr>`; });
  o += table(["Symbol", "Price", "1D", "Trend", "RSI", "Nearest zone", "Quality", "Status", "Distance", "Plan", "T1 R:R", "Room", "Readiness"], rows)
    + `<div class="aa-cap">Scanned ${esc(res.at)}. Readiness = zone quality, proximity, trend, weekly confluence and confirmation. Tap a symbol for the full AI read.</div>`;
  if (res.track) o += sec("Track record of your zone style on this list", `${res.track.n_stocks || 0} stocks · 2 years · replayed with no hindsight`) + trackTable(res.track)
    + '<div class="aa-cap">This is the evidence for your ZONE STYLE settings: change a setting in Teach → Zone style, scan again, and compare. Break-even is 33% reaching 2R first; random price data gives about 30%. Costs are not included.</div>';
  return o;
}
async function journalLoad(force = false) {
  const A = S.aa, J = A.journal, calls = items().filter((i) => i.kind === "call").slice(0, 60), key = calls.map((c) => c.item_id).join(",");
  if (J.busy || (!force && J.key === key && J.res)) return; J.busy = true; J.key = key; paint();
  try {
    const tick_ = [...new Set(calls.map((c) => String(c.payload.ticker || "").toUpperCase()).filter(Boolean))].sort(), stocks = tick_.filter((x) => !E.isIndex(x));
    let frames = {}; if (stocks.length) { try { frames = await fetchMany(stocks); } catch { frames = {}; } }
    const rows = [];
    for (const it of calls) { const p = it.payload; let ev;
      try { const df = frames[String(p.ticker).toUpperCase()] || (await fetchOhlc(p.ticker)); ev = E.evaluateCall(p, df); }
      catch { ev = { status: "NO DATA (ValueError)", r: null, days: 0, entry: null, last: null }; }
      rows.push([it, p, ev]); }
    J.res = rows;
  } catch (e) { J.res = []; }
  J.busy = false; repaint();
}
function journalView() {
  const A = S.aa, J = A.journal, calls = items().filter((i) => i.kind === "call");
  let o = '<div class="aa-intro"><b>Your logged plans, checked against what the market did.</b> Press <b>Log this plan</b> under any analysis. Each plan is then followed on daily candles: did price reach the entry, then target 1 or the stop first. This is the real, forward record of the plans you chose to take, which no backtest can give you.</div>';
  if (!A.items) return o + '<div class="aa-empty">Loading your playbook…</div>';
  if (!calls.length) return o + '<div class="aa-empty">No plans logged yet.<br>Analyze a stock and press <b>Log this plan</b>.</div>';
  const key = calls.slice(0, 60).map((c) => c.item_id).join(","); if (J.key !== key && !J.busy) setTimeout(() => journalLoad(), 0);
  if (!J.res || J.busy) return o + '<div class="card pad"><div class="aa-prog"><b><i class="aa-spin"></i>Checking each plan against the latest prices…</b></div></div>';
  const rows = J.res, closed = rows.filter(([, , ev]) => ["STOPPED", "TARGET 1 HIT"].includes(ev.status)).map(([, , ev]) => ev.r), wins = closed.filter((x) => x > 0).length;
  const openR = rows.filter(([, , ev]) => ev.status === "OPEN" && ev.r != null).map(([, , ev]) => ev.r), sum = (a) => a.reduce((s, x) => s + x, 0), kp = (l, v) => `<div><span>${l}</span><b>${v}</b></div>`;
  o += `<div class="aa-card mini0"><div class="aa-mini j">${kp("Plans logged", rows.length)}${kp("Closed", closed.length)}${kp("Hit target 1", closed.length ? `${wins} of ${closed.length}` : "—")}${kp("Result of closed plans", closed.length ? fx(sum(closed), 1, false, true) + " R" : "—")}${kp("Open plans now", openR.length ? fx(sum(openR), 1, false, true) + " R" : "—")}</div></div>`;
  const colors = { "TARGET 1 HIT": G, STOPPED: R, OPEN: AM, WAITING: T2 };
  o += table(["Stock", "Logged", "Side", "Entry zone", "Stop", "Target 1", "Score", "Status", "Filled at", "Last price", "Result", "Sessions", ""], rows.map(([it, p, ev]) => {
    const t1 = (p.targets || []).length ? p.targets[0].price : null, rr = ev.r == null ? "—" : `<span style="color:${ev.r > 0 ? G : ev.r < 0 ? R : T2}">${fx(ev.r, 2, false, true)} R</span>`;
    return `<tr><td><a class="sc-lk" data-act="aarun" data-v="${esc(p.ticker)}">${esc(p.ticker)}</a></td><td>${esc(p.asof)}</td><td>${pill(E.cap(p.direction || ""), p.direction === "LONG" ? G : R)}</td><td class="r">${f(p.entry_low)} – ${f(p.entry_high)}</td><td class="r">${f(p.stop)}</td><td class="r">${f(t1)}</td>
      <td class="r">${p.score ?? "—"}</td><td>${pill(E.cap(ev.status), colors[ev.status] || T3)}</td><td class="r">${f(ev.entry)}</td><td class="r">${f(ev.last)}</td><td class="r">${rr}</td><td class="r">${ev.days || 0}</td>
      <td>${A.sure[it.item_id] ? `<button class="btn sm red" data-act="aadel" data-v="${esc(it.item_id)}">Delete</button><button class="btn sm" data-act="aasure" data-v="${esc(it.item_id)}">Keep</button>` : `<button class="btn sm" data-act="aasure" data-v="${esc(it.item_id)}">Delete…</button>`}</td></tr>`; }))
    + '<div class="aa-cap">WAITING = price has not reached the entry yet · OPEN = filled, result shown at the last close · a candle that touches stop and target counts as stopped · results are in R (1R = the distance from entry to stop). Daily candles; costs not included.</div>';
  return o;
}
const setupSummary = (s) => `<div class="aa-item" style="--c:${AM}"><div class="h"><b>${esc(s.setup_name || "Unnamed setup")}</b>${pill("Confidence " + E.pyStr(s.confidence ?? "?").toLowerCase(), AM)}</div><div class="d">${esc(s.summary || "")}<br>
  ${(s.conditions || []).length} conditions · ${(s.confirmation || []).length} confirmations · ${(s.invalidation || []).length} invalidations · ${(s.zone_definitions || []).length} zone definitions · ${(s.line_definitions || []).length} line definitions · ${(s.examples || []).length} examples</div></div>`;
function teachView() {
  const A = S.aa, T = A.teach, aiOff = A.ai === false;
  let o = `<div class="seg">${TEACH.map((x) => `<button class="${x === T.sec ? "on" : ""}" data-act="aatsec" data-v="${x}">${x}</button>`).join("")}</div>`;
  const off = aiOff ? '<div class="aa-warn">Gemini is not configured (add the secret GEMINI_KEY in Cloudflare), so this needs the AI and is switched off.</div>' : "";
  if (T.sec === "Import PDF") {
    o += '<div class="aa-intro">Upload your strategy PDF. Arka reads text, diagrams and marked-up examples, turns them into a structured setup (rules, trigger, confirmation, invalidation, how you draw supply / demand zones and lines) and shows you what it understood. <b>You approve it before it is used.</b></div>' + off
      + `<div class="aa-exp"><label class="btn aa-file">${T.pdf ? esc(T.pdf.name) : "Choose a PDF"}<input type="file" accept="application/pdf" data-aa="pdf" hidden></label><button class="btn pri" data-act="aapdf"${!T.pdf || T.pendBusy || aiOff ? " disabled" : ""}>${T.pendBusy ? "Arka is reading the whole PDF…" : "Read my PDF"}</button></div>`;
    const p = T.pend; if (p) { if (p.error) o += `<div class="aa-warn r">${esc(p.error)}</div>`;
      else { o += setupSummary(p); if ((p.ambiguities || []).length) o += `<div class="aa-warn">Arka was unsure about: ${esc(p.ambiguities.slice(0, 6).map(E.pyStr).join(" | "))}</div>`;
        o += `<details class="card sc-det" open><summary>What Arka understood <span>review before approving</span></summary><div class="pad">${pretty(p)}</div></details><div class="aa-exp"><button class="btn pri" data-act="aapdfok">Approve and activate this setup</button></div>`; } }
  } else if (T.sec === "Label zones") {
    const L = T.lab;
    o += '<div class="aa-intro"><b>The fastest way to teach your zones.</b> Arka detects every supply and demand zone on a chart. Mark the ones that are <b>your kind of zone</b> and the ones that are not. After a few charts it learns your style: how thick, how fresh, how strong the move away. No drawing needed.</div>'
      + `<div class="aa-form"><div class="aa-row2"><label class="f aa-tk"><span>Stock to learn from</span><input data-aa="labt" value="${esc(L.t)}" placeholder="Choose a chart where you can see both good and bad zones" autocapitalize="characters" autocomplete="off"></label><button class="btn pri" data-act="aalab"${L.busy ? " disabled" : ""}>${L.busy ? "Detecting…" : "Detect zones"}</button></div></div>`;
    if (L.err) o += `<div class="aa-warn r">${esc(L.err)}</div>`;
    const D = L.data; if (D) { const zones = D.det.zones.slice().sort((a, b) => b.quality - a.quality).slice(0, 14);
      if (!zones.length) o += '<div class="aa-warn">No zones detected on this chart. Try a more volatile stock.</div>';
      else { const spec = { zones, levels: [], lines: [], markers: [], candles: [], plan: null };
        o += `<div class="card aa-chart">${chartSvg(D.df, spec, 160, { zones: true, levels: true, lines: true, plan: false, mas: false, markers: false }, phone() ? 420 : 560)}</div>` + sec("Which of these are YOUR kind of zone?", `${D.ticker} · strongest ${zones.length} shown`);
        o += zones.map((z) => { const k = `${D.ticker}_${z.id}`, v = L.pick[k] || "Skip";
          return `<div class="aa-lab"><div class="z"><b>${z.id}</b>${pill(E.cap(z.type), z.type === "demand" ? G : R)} ${esc(z.pattern)} · ${f(z.bottom)}–${f(z.top)} · ${E.pyRepr(z.thickness_atr)} ATR thick · move away ${E.pyRepr(z.dep_atr)} ATR · Q${z.quality} · ${esc(z.status)}</div>
            <div class="seg sm">${["Skip", "✔ Mine", "✘ Not mine"].map((x) => `<button class="${x === v ? "on" : ""}" data-act="aalpick" data-k="${esc(k)}" data-v="${x}">${x}</button>`).join("")}</div>
            <input data-aa="why" data-k="${esc(k)}" value="${esc(L.why[k] || "")}" placeholder="Why? e.g. too wide, already tested, no real impulse" autocomplete="off"></div>`; }).join("");
        const nOk = zones.filter((z) => L.pick[`${D.ticker}_${z.id}`] === "✔ Mine").length, nNo = zones.filter((z) => L.pick[`${D.ticker}_${z.id}`] === "✘ Not mine").length;
        o += `<div class="aa-exp"><button class="btn pri" data-act="aalabsave"${nOk + nNo === 0 ? " disabled" : ""}>Save my labels (${nOk} mine · ${nNo} not mine)</button></div>`; } }
  } else if (T.sec === "From screenshot") {
    o += '<div class="aa-intro">Upload charts <b>you</b> marked up (zones, lines, entries, stops, targets) and explain what you did. Arka studies the drawings and writes down how you read a chart, which it then applies to every stock you analyse.</div>' + off
      + `<div class="aa-exp"><label class="btn aa-file">${T.imgs.length ? `${T.imgs.length} chart${T.imgs.length !== 1 ? "s" : ""} chosen` : "Choose marked-up charts (PNG / JPG)"}<input type="file" accept="image/png,image/jpeg" multiple data-aa="imgs" hidden></label></div>
      <label class="f"><span>Explain your markings</span><textarea data-aa="imgwhy" rows="4" placeholder="e.g. The green boxes are my demand zones: a tight base of 1-3 candles, then a strong move away. I only buy fresh zones in an uptrend. Stop below the zone, first target at the previous swing high.">${esc(T.why)}</textarea></label>
      <div class="aa-exp"><button class="btn pri" data-act="aaimg"${!T.imgs.length || T.imgBusy || aiOff ? " disabled" : ""}>${T.imgBusy ? "Studying your charts…" : "Learn from these charts"}</button></div>`;
    const p = T.lesson; if (p) { if (p.error) o += `<div class="aa-warn r">${esc(p.error)}</div>`;
      else o += `<div class="aa-item" style="--c:${CY}"><div class="h"><b>${esc(p.lesson_title || "Lesson")}</b>${pill("New lesson", CY)}</div><div class="d">${esc(p.summary || "")}</div></div>
        <details class="card sc-det" open><summary>What Arka learned <span>review</span></summary><div class="pad">${pretty(p)}</div></details><div class="aa-exp"><button class="btn pri" data-act="aaimgok">Save lesson to my playbook</button></div>`; }
  } else if (T.sec === "Quick rules") {
    const M = T.m, Lx = T.l;
    o += '<div class="aa-intro">Prefer typing? Write a setup as plain rules, or teach a lesson in your own words.</div>' + sec("Write a setup", "one rule per line")
      + `<div class="aa-stack"><label class="f"><span>Setup name</span><input data-aa="m.name" value="${esc(M.name)}" placeholder="Fresh demand pullback"></label><label class="f"><span>What is this setup?</span><textarea data-aa="m.sum" rows="2">${esc(M.sum)}</textarea></label>
      <label class="f"><span>Rules (one per line)</span><textarea data-aa="m.conds" rows="6" placeholder="Price is in an uptrend (higher highs, higher lows)&#10;Price returns to a fresh demand zone&#10;The demand zone is thin (base of 1-3 candles)&#10;A rejection candle forms inside the zone">${esc(M.conds)}</textarea></label>
      <label class="f"><span>Trigger</span><input data-aa="m.trig" value="${esc(M.trig)}" placeholder="Bullish close back above the zone's proximal line"></label><label class="f"><span>Invalidation</span><input data-aa="m.inv" value="${esc(M.inv)}" placeholder="Close below the zone's distal line"></label>
      <div><button class="btn pri" data-act="aamsave">Save and activate setup</button></div></div>` + sec("Teach a lesson", "how you define zones and lines, in your words")
      + `<div class="aa-stack"><label class="f"><span>Lesson title</span><input data-aa="l.t" value="${esc(Lx.t)}" placeholder="How I draw demand zones"></label><label class="f"><span>Lesson</span><textarea data-aa="l.x" rows="5" placeholder="A demand zone starts at the lowest wick of the base and ends at the top of the base bodies. I ignore zones thicker than 2 ATR...">${esc(Lx.x)}</textarea></label><div><button class="btn" data-act="aalsave">Save lesson</button></div></div>`;
  } else {
    const style = getStyle(), [ap, rj] = allLabels(), H = T.hand || (T.hand = { ...style });
    o += `<div class="aa-intro">Your zone style tells the engine what counts as a zone. Current source: <b>${esc(styleSource())}</b>. You have labelled <b>${ap.length}</b> zone(s) as yours and <b>${rj.length}</b> as not yours.</div>` + sec("Learn from my labels")
      + `<div class="aa-exp"><button class="btn pri" data-act="aalearn"${ap.length < 3 ? " disabled" : ""}>Learn my zone style</button>${ap.length < 3 ? '<span class="mini">Approve at least 3 zones in Label zones first.</span>' : ""}</div>`;
    const Ln = T.learned; if (Ln && Ln.params) o += `<ul class="aa-ul">${Ln.notes.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>${pretty(Ln.params)}<div class="aa-exp"><button class="btn pri" data-act="aalearnsave">Save as my zone style</button></div>`;
    const num = (k, lab, lo, hi, st) => `<label class="f" title="${esc(E.STYLE_HELP[k] || "")}"><span>${lab}</span><input type="number" data-aa="h.${k}" min="${lo}" max="${hi}" step="${st}" value="${esc(H[k])}" inputmode="decimal"></label>`;
    o += sec("Or set it by hand") + `<div class="aa-g4">${num("base_max", "Max base candles", 1, 8, 1)}${num("base_range_atr", "Base candle range (ATR)", 0.3, 2.5, 0.1)}${num("impulse_atr", "Min impulse (ATR)", 0.8, 6.0, 0.1)}${num("max_zone_atr", "Max zone thickness (ATR)", 0.5, 5.0, 0.1)}
      ${num("min_quality", "Min quality", 0, 90, 5)}${num("sr_tol_atr", "S/R tolerance (ATR)", 0.2, 2.0, 0.1)}${num("sr_min_touches", "S/R min touches", 2, 6, 1)}<label class="aa-tg"><input type="checkbox" data-aa="h.fresh_only"${H.fresh_only ? " checked" : ""}><span>Fresh zones only</span></label></div>
      <div class="aa-exp"><button class="btn" data-act="aahsave">Save these settings</button><button class="btn" data-act="aareset">Reset to Arka defaults</button></div>`;
  }
  return o;
}
function allLabels() { const ap = [], rj = []; for (const it of active("feedback")) for (const lab of (it.payload || {}).labels || []) (lab.approved ? ap : rj).push(lab.features || {}); return [ap, rj]; }
function playbookView() {
  const A = S.aa, list = items();
  let o = `<div class="aa-pbh"><div class="aa-intro">Everything you have taught Arka. <b>Active</b> items are used in every analysis; switch items off to test a different approach without deleting anything.</div><button class="btn" data-act="aareload">Reload</button></div>`;
  if (!A.items) return o + '<div class="aa-empty">Loading your playbook…</div>';
  if (!list.length) return o + gettingStarted();
  const colors = { setup: AM, lesson: CY, feedback: G, style: PU };
  for (const kind of ["setup", "lesson", "feedback", "style"]) {
    const group = list.filter((i) => i.kind === kind); if (!group.length) continue;
    o += sec({ setup: "Setups", lesson: "Lessons", feedback: "Labelled zones", style: "Zone styles" }[kind], `${group.length} saved`);
    for (const it of group) { const p = it.payload || {}, desc = p.summary || p.setup_name || "";
      const meta = { setup: `${(p.conditions || []).length} rules · ${(p.zone_definitions || []).length} zone definitions`, lesson: `${(p.zone_definitions || []).length} zone definitions · ${(p.line_definitions || []).length} line definitions`,
        feedback: `${(p.labels || []).filter((x) => x.approved).length} mine · ${(p.labels || []).filter((x) => !x.approved).length} not mine`, style: Object.entries(p.params || {}).slice(0, 4).map(([k, v]) => `${k} ${E.pyStr(v)}`).join(", ") }[kind];
      const open = A.open[it.item_id];
      o += `<div class="aa-item" style="--c:${it.active ? colors[kind] : "#2B2B2B"}"><div class="h"><b>${esc(it.name)}</b>${pill(it.active ? "Active" : "Off", it.active ? G : T3)}</div><div class="d">${esc(E.pyStr(desc).slice(0, 260))}<br>${esc(meta)} · saved ${esc(String(it.saved_at).slice(0, 10))}${it._local ? " · this browser tab only" : ""}</div>
        <div class="aa-ia"><label class="aa-tg"><input type="checkbox" data-aa="act" data-k="${esc(it.item_id)}"${it.active ? " checked" : ""}><span>Active</span></label><button class="btn sm" data-act="aaview" data-v="${esc(it.item_id)}">${open ? "Hide details" : "View details"}</button>
        ${A.sure[it.item_id] ? `<button class="btn sm red" data-act="aadel" data-v="${esc(it.item_id)}">Delete for good</button><button class="btn sm" data-act="aasure" data-v="${esc(it.item_id)}">Keep</button>` : `<button class="btn sm" data-act="aasure" data-v="${esc(it.item_id)}">Delete…</button>`}</div>${open ? pretty(p) : ""}</div>`; }
  }
  return o;
}
export function page() {
  const A = S.aa; if (!A.items && !A.loading) setTimeout(() => loadItems(), 0);
  const body = { ANALYZE: analyzeView, RADAR: radarView, JOURNAL: journalView, TEACH: teachView, PLAYBOOK: playbookView }[A.mode] || analyzeView;
  let inner; try { inner = body(); } catch (e) { inner = `<div class="aa-warn r">This view could not be drawn: ${esc(e.message)}</div>`; }
  return `<div class="sc aa"><div class="hd"><div><h1>Arka AI</h1><div class="sub">Supply and demand zones, levels and a trade plan checked against your own playbook.</div></div></div>${statusChips()}
    <div class="seg aa-modes">${MODES.map(([k, t]) => `<button class="${k === A.mode ? "on" : ""}" data-act="aamode" data-v="${k}">${t}</button>`).join("")}</div>${inner}
    <div class="note aa-foot">Arka AI is a research tool. Zones and levels are rule-based approximations of discretionary chart reading; scores measure how well the evidence matches your playbook, not the probability of profit. Not investment advice.</div></div>`;
}
export function open(sym) {                                  // #/arka/SYM: open Analyze on a stock and start (the "Arka AI analysis" buttons)
  const A = S.aa;
  if (sym) { A.mode = "ANALYZE"; A.ticker = sym; if (!(A.report && A.report.ticker === sym) && !A.busy) setTimeout(() => runAnalysis(sym), 0); }
}

// ── actions ──
export async function act(a, el) {
  const A = S.aa, v = el.dataset.v, T = A.teach;
  if (a === "aamode") { A.mode = v; window.scrollTo(0, 0); if (v === "JOURNAL") journalLoad(); return repaint(); }
  if (a === "aago") { const i = document.querySelector('[data-aa="ticker"]'); if (i) A.ticker = i.value; return runAnalysis(A.ticker); }
  if (a === "aarun") { A.mode = "ANALYZE"; A.ticker = v; window.scrollTo(0, 0); return runAnalysis(v); }
  if (a === "aalayer") { A.show[v] = !A.show[v]; return repaint(); }
  if (a === "aaask") { const i = document.querySelector('[data-aa="ask"]'); if (i) A.ask = i.value; const q = A.ask.trim(), r = A.report; if (!q || !r || A.asking) return;
    A.asking = true; repaint(); let ans;
    try { const det = r.det, ai = r.ai || {}, ctx = { ticker: r.ticker, asof: r.asof, close: det.close, structure: det.structure, plan: r.plan, zones: det.zones.map(E.slimZone), levels: E.slimLevels(det.levels), headline: ai.headline ?? null, narrative: ai.narrative ?? null, supports: ai.supports ?? null, contradicts: ai.contradicts ?? null };
      ans = await gen("You are Arka AI. Answer the trader's follow-up question about this analysis. Use ONLY the supplied data; if it is not there, say so. Be concise and specific (prices, IDs). No hype, no guarantees.\n\nANALYSIS DATA:\n" + E.pyDumps(ctx) + "\n\nQUESTION: " + q, { json: false }); }
    catch (e) { ans = "Could not answer: " + e.message; }
    A.qa.push([r.ticker, q, ans]); A.ask = ""; A.asking = false; return repaint(); }
  if (a === "aapng") { const r = A.report; if (!r) return; const [label] = E.verdictText(r);
    renderPng(r.df, r.spec, A.window, `${r.ticker}  ·  Daily  ·  ${r.asof}  ·  ${label}  ·  score ${r.score}`, 120).toBlob((b) => b && download(`${r.ticker}_arka_${r.asof}.png`, b), "image/png"); return; }
  if (a === "aajson") { const r = A.report; if (!r) return; const pay = Object.fromEntries(["ticker", "asof", "score", "plan", "ai", "engine_plan", "market", "results", "model"].map((k) => [k, r[k] ?? null]));
    Object.assign(pay, { zones: r.det.zones, levels: r.det.levels, trendlines: r.det.trendlines, track_record: (r.track || {}).groups || null });
    return download(`${r.ticker}_arka_${r.asof}.json`, new Blob([JSON.stringify(pay, null, 2)], { type: "application/json" })); }
  if (a === "aawl") { const r = A.report; if (!r) return; if (E.isIndex(r.ticker)) return toast("Indices cannot be added to the watchlist."); if (S.wl.rows.some((x) => x.sym === r.ticker)) return toast(r.ticker + " is already in your watchlist"); return addStock(r.ticker); }
  if (a === "aalog") { const r = A.report, p = r && r.plan; if (!p) return; const [label] = E.verdictText(r);
    const call = Object.fromEntries(["direction", "ref_zone", "entry_low", "entry_high", "entry_ref", "stop", "targets", "source", "room_r"].map((k) => [k, p[k] ?? null]));
    Object.assign(call, { ticker: r.ticker, asof: r.asof, close_at_log: r.det.close, score: r.score, verdict: label, summary: `${p.direction} ${r.ticker} from ${p.ref_zone} · entry ${fx(p.entry_ref, 2)} · stop ${fx(p.stop, 2)}` });
    if (await saveItem("call", `${r.ticker} · ${p.direction} · ${r.asof}`, call, true)) toast("Plan logged. Open Journal to follow it."); A.journal.key = ""; return repaint(); }
  if (a === "aarsrc") { A.radar.src = v; return repaint(); }
  if (a === "aarscan") { const i = document.querySelector('[data-aa="rtxt"]'); if (i) A.radar.txt = i.value; return radarScan(); }
  if (a === "aatsec") { T.sec = v; return repaint(); }
  if (a === "aareload") { A.items = null; A.journal.key = ""; await loadItems(true); return; }
  if (a === "aaview") { A.open[v] = !A.open[v]; return repaint(); }
  if (a === "aasure") { A.sure[v] = !A.sure[v]; return repaint(); }
  if (a === "aadel") { if (await deleteItem(v)) { toast("Deleted"); A.journal.key = ""; } delete A.sure[v]; return repaint(); }
  if (a === "aapdf") { if (!T.pdf) return; T.pendBusy = true; T.pend = null; repaint();
    try { if (T.pdf.size > 18 * 1024 * 1024) throw new Error("This PDF is larger than 18 MB. Split it or export a smaller copy, then upload again.");
      const up = await upload(T.pdf), res = await callJson(E.PDF_PROMPT, { files: [up] }); res.source_name = T.pdf.name; T.pend = res; }
    catch (e) { T.pend = { error: e.message }; }
    T.pendBusy = false; return repaint(); }
  if (a === "aapdfok") { const p = T.pend; if (p && (await saveItem("setup", p.setup_name || "PDF setup", p, true, p.source_name || "", ""))) { T.pend = null; toast("Setup saved and active. Analyze now checks stocks against it."); } return repaint(); }
  if (a === "aalab") { const i = document.querySelector('[data-aa="labt"]'); if (i) T.lab.t = i.value; const t = T.lab.t.trim().toUpperCase(); if (!t) return; T.lab.busy = true; T.lab.err = ""; repaint();
    try { const df = await fetchOhlc(t); T.lab.data = { ticker: t, df, det: E.analyzeMarket(df, { ...getStyle(), min_quality: 20, fresh_only: false }) }; } catch (e) { T.lab.err = e.message; }
    T.lab.busy = false; return repaint(); }
  if (a === "aalpick") { T.lab.pick[el.dataset.k] = v; return repaint(); }
  if (a === "aalabsave") { const D = T.lab.data; if (!D) return; const zones = D.det.zones.slice().sort((x, y) => y.quality - x.quality).slice(0, 14);
    const labels = zones.filter((z) => (T.lab.pick[`${D.ticker}_${z.id}`] || "Skip") !== "Skip").map((z) => ({ zone_id: z.id, zone_type: z.type, approved: T.lab.pick[`${D.ticker}_${z.id}`] === "✔ Mine", reason: T.lab.why[`${D.ticker}_${z.id}`] || "", features: E.zoneFeatures(z) }));
    const nOk = labels.filter((x) => x.approved).length, nNo = labels.length - nOk;
    if (await saveItem("feedback", `${D.ticker} · ${nOk} mine / ${nNo} not · ${D.det.asof}`, { ticker: D.ticker, asof: D.det.asof, labels, summary: `${nOk} approved and ${nNo} rejected zones on ${D.ticker}` }, true)) toast("Saved. Open Zone style and press Learn to turn your labels into engine settings.");
    return repaint(); }
  if (a === "aaimg") { const i = document.querySelector('[data-aa="imgwhy"]'); if (i) T.why = i.value; T.imgBusy = true; T.lesson = null; repaint();
    try { const ups = []; for (const file of T.imgs.slice(0, 6)) ups.push(await upload(file)); T.lesson = await callJson(E.IMAGE_PROMPT + "\n\nTRADER'S EXPLANATION:\n" + (T.why || "None given."), { files: ups }); }
    catch (e) { T.lesson = { error: e.message }; }
    T.imgBusy = false; return repaint(); }
  if (a === "aaimgok") { const p = T.lesson; if (p && (await saveItem("lesson", p.lesson_title || "Chart lesson", p, true, "screenshot"))) { T.lesson = null; toast("Lesson saved and active."); } return repaint(); }
  if (a === "aamsave") { readBoxes(); const M = T.m; if (!M.name.trim() || !M.conds.trim()) return toast("Give the setup a name and at least one rule.");
    if (await saveItem("setup", M.name, E.manualSetup(M.name, M.sum, M.conds, M.trig, M.inv), true, "manual")) { T.m = { name: "", sum: "", conds: "", trig: "", inv: "" }; toast("Setup saved and active."); } return repaint(); }
  if (a === "aalsave") { readBoxes(); const L = T.l; if (!L.t.trim() || !L.x.trim()) return toast("Add a title and the lesson text.");
    if (await saveItem("lesson", L.t, { lesson_title: L.t.trim(), summary: L.x.trim(), entry_logic: "", stop_logic: "", target_logic: "" }, true, "manual")) { T.l = { t: "", x: "" }; toast("Lesson saved and active."); } return repaint(); }
  if (a === "aalearn") { const [ap, rj] = allLabels(), [params, notes] = E.calibrateStyle(ap, rj); T.learned = { params, notes }; return repaint(); }
  if (a === "aalearnsave") { const Ln = T.learned; if (Ln && (await saveItem("style", "Learned style · " + dmy(), { params: Ln.params, notes: Ln.notes, summary: "Learned from labelled zones" }, true))) { T.learned = null; T.hand = null; toast("Saved. Every analysis now uses your style."); } return repaint(); }
  if (a === "aahsave") { readBoxes(); const H = T.hand, cl = (x, lo, hi) => Math.max(lo, Math.min(hi, Number(x)));
    const vals = { base_max: Math.trunc(cl(H.base_max, 1, 8)), base_range_atr: cl(H.base_range_atr, 0.3, 2.5), impulse_atr: cl(H.impulse_atr, 0.8, 6.0), max_zone_atr: cl(H.max_zone_atr, 0.5, 5.0),
      min_quality: Math.trunc(cl(H.min_quality, 0, 90)), sr_tol_atr: cl(H.sr_tol_atr, 0.2, 2.0), sr_min_touches: Math.trunc(cl(H.sr_min_touches, 2, 6)), fresh_only: Boolean(H.fresh_only) };
    if (Object.values(vals).some((x) => typeof x === "number" && !isFinite(x))) return toast("Every setting needs a number.");
    if (await saveItem("style", "Manual style · " + dmy(), { params: vals, summary: "Set by hand" }, true)) { T.hand = null; toast("Saved."); } return repaint(); }
  if (a === "aareset") { for (const it of active("style")) await setActive(it.item_id, false); T.hand = null; toast("Back to Arka defaults."); return repaint(); }
}
function readBoxes() { document.querySelectorAll(".aa [data-aa]").forEach((el) => { if (/INPUT|TEXTAREA/.test(el.tagName) && el.type !== "checkbox" && el.type !== "file") input(el, "input"); }); }
export function input(el, ev) {
  const A = S.aa, k = el.dataset.aa, T = A.teach;
  if (k === "ticker") { A.ticker = el.value.toUpperCase(); return; }
  if (k === "note") { A.note = el.value; return; }
  if (k === "ask") { A.ask = el.value; return; }
  if (k === "risk") { if (el.value !== "" && isFinite(+el.value)) A.risk = Math.max(0, +el.value); if (ev === "change") repaint(); return; }
  if (k === "window") { A.window = +el.value; return repaint(); }
  if (k === "speed") { A.speed = el.value; return; }
  if (k === "useAi") { A.useAi = el.checked; return; }
  if (k === "rtxt") { A.radar.txt = el.value; if (ev === "change") repaint(); return; }
  if (k === "rtrack") { A.radar.track = el.checked; return; }
  if (k === "labt") { T.lab.t = el.value; return; }
  if (k === "why") { T.lab.why[el.dataset.k] = el.value; return; }
  if (k === "imgwhy") { T.why = el.value; return; }
  if (k === "pdf") { T.pdf = el.files && el.files[0] ? el.files[0] : null; T.pend = null; return repaint(); }
  if (k === "imgs") { T.imgs = [...(el.files || [])].slice(0, 6); T.lesson = null; return repaint(); }
  if (k.startsWith("m.")) { T.m[k.slice(2)] = el.value; return; }
  if (k.startsWith("l.")) { T.l[k.slice(2)] = el.value; return; }
  if (k.startsWith("h.")) { const key = k.slice(2); if (!T.hand) T.hand = { ...getStyle() }; T.hand[key] = el.type === "checkbox" ? el.checked : el.value === "" ? T.hand[key] : Number(el.value); return; }
  if (k === "act" && ev === "change") { setActive(el.dataset.k, el.checked).then(() => repaint()); return; }
}
export function enter(el) { const k = el.dataset.aa; if (k === "ticker") { A_ticker(el); return true; } if (k === "ask") { act("aaask", el); return true; } if (k === "labt") { act("aalab", el); return true; } return false; }
const A_ticker = (el) => { S.aa.ticker = el.value.toUpperCase(); runAnalysis(S.aa.ticker); };
export function toggle(el) { if (el.dataset.aad) S.aa[el.dataset.aad] = el.open; }

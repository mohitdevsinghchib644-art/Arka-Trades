// Arka Trades · NSDL fortnightly FPI flows by sector, read in the browser (arka_flow/fpi.py + money_flow._view_fpi).
// Used when the nightly job could not reach NSDL: the Worker fetches each fortnight's report, this file reads it the way
// fpi.parse_nsdl_sector_html does (straight from the HTML, the four header rows NSDL uses since Aug 2024), and builds the same object as flow_fpi.json.
import { fx } from "./rsengine.js";

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const iso = (y, m, d) => `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
export function reportDates(n = 24, today = new Date()) {         // fpi.nsdl_report_dates: the 15th and month ends, newest first, at least 3 days old
  const t = Date.UTC(today.getFullYear(), today.getMonth(), today.getDate()), out = [];
  let y = today.getFullYear(), m = today.getMonth();
  while (out.length < n) {
    for (const d of [new Date(Date.UTC(y, m + 1, 0)).getUTCDate(), 15]) { const v = Date.UTC(y, m, d); if (v <= t - 3 * 864e5 && out.length < n) out.push(iso(y, m, d)); }
    m--; if (m < 0) { m = 11; y--; }
  }
  return out;
}
const MON3 = Object.fromEntries(MONTHS.map((n, i) => [n.slice(0, 3).toLowerCase(), i]));
function mdy(mon, day, year) {                                    // fpi._parse_mdy
  const k = mon.trim().replace(/\.$/, "").toLowerCase(), i = MONTHS.findIndex((n) => n.toLowerCase() === k), j = i >= 0 ? i : MON3[k.slice(0, 3)];
  if (j == null || (i < 0 && k.length !== 3 && k !== "sept")) return null;
  return iso(+year, j, +day);
}
const cellText = (inner) => decode(inner.replace(/<[^>]+>/g, " ")).replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
function decode(t) { return t.replace(/&nbsp;/gi, " ").replace(/&amp;/gi, "&").replace(/&lt;/gi, "<").replace(/&gt;/gi, ">").replace(/&quot;/gi, '"').replace(/&#39;|&apos;/gi, "'").replace(/&#(\d+);/g, (_, n) => String.fromCharCode(+n)); }
const span = (attrs, name) => { const m = attrs.match(new RegExp(name + "\\s*=\\s*['\"]?(\\d+)", "i")); return m ? Math.max(1, +m[1]) : 1; };
export function grid(tableHtml) {                                  // fpi._grid: rows with colspan / rowspan expanded
  const out = [], carry = new Map();
  for (const tr of tableHtml.match(/<tr[^>]*>[\s\S]*?<\/tr>/gi) || []) {
    const cells = [...tr.matchAll(/<t([dh])([^>]*)>([\s\S]*?)<\/t[dh]>/gi)].map((m) => [cellText(m[3]), span(m[2], "colspan"), span(m[2], "rowspan")]), row = [];
    let col = 0, k = 0;
    while (k < cells.length || carry.has(col)) {
      if (carry.has(col)) { const [t, left] = carry.get(col); row.push(t); if (left <= 1) carry.delete(col); else carry.set(col, [t, left - 1]); col++; continue; }
      const [t, cs, rs] = cells[k++];
      for (let c = 0; c < cs; c++) { row.push(t); if (rs > 1) carry.set(col, [t, rs - 1]); col++; }
    }
    out.push(row);
  }
  return out;
}
const NOT_EQUITY = /mutual|alternative|\baif|hybrid|debt|total|solution|other/i;
const value = (raw) => { let t = String(raw).replace(/,/g, "").replace(/\u00a0/g, "").trim(); const neg = t.startsWith("(") && t.endsWith(")"); t = t.replace(/^\(|\)$/g, "").trim(); if (t === "" || isNaN(Number(t))) return null; return neg ? -Number(t) : Number(t); };
export function parseNsdl(html) {                                 // fpi.parse_nsdl_sector_html → [{date, sector, kind, value}] (FPI equity, ₹ crore)
  const recs = [];
  for (const m of String(html || "").matchAll(/<table[^>]*>([\s\S]*?)<\/table>/gi)) {
    const g = grid(m[1]), isSec = (c) => ["sector", "sectors"].includes(c.toLowerCase()), top = g.findIndex((r) => r.some(isSec));
    if (top < 0) continue;
    const sc = g[top].findIndex(isSec); let hdr = top;                 // the header ends at the row above the first sector row with a number
    while (hdr + 1 < g.length && (sc >= g[hdr + 1].length || isSec(g[hdr + 1][sc]) || g[hdr + 1][sc] === "" || !g[hdr + 1].slice(sc + 1).some((c) => value(c) != null))) hdr++;
    const head = g.slice(0, hdr + 1), n = Math.max(...g.map((r) => r.length)), parts = Array.from({ length: n }, (_, i) => head.map((r) => r[i]).filter((x) => x));
    const sec = parts.findIndex((p) => p.some(isSec)); if (sec < 0) continue;
    const cols = [];
    parts.forEach((p, i) => {
      if (!p.length || p[p.length - 1].toLowerCase() !== "equity" || !p.some((x) => x.toLowerCase().includes("inr")) || p.slice(0, -1).some((x) => x.toLowerCase() !== "equity" && NOT_EQUITY.test(x))) return;
      let mm = p[0].match(/Net\s+Investment\s+([A-Za-z]+)\.?\s+\d+\s*-\s*(\d+)\s*,?\s*(\d{4})/i), kind = "net";
      if (!mm) { mm = p[0].match(/AUC\s+as\s+on\s+([A-Za-z]+)\.?\s+(\d+)\s*,?\s*(\d{4})/i); kind = "auc"; }
      if (!mm) return; const date = mdy(mm[1], mm[2], mm[3]); if (date) cols.push([i, date, kind]);
    });
    for (const r of g.slice(hdr + 1)) { const s = String(r[sec] ?? "").trim(); if (!s || s.toLowerCase() === "nan") continue;
      for (const [i, date, kind] of cols) { const v = i < r.length ? value(r[i]) : null; if (v != null) recs.push({ date, sector: s, kind, value: v }); } }
    if (recs.length) break;
  }
  const m = new Map(); for (const r of recs) { const k = r.date + "|" + r.sector + "|" + r.kind; if (!m.has(k)) m.set(k, r); } return [...m.values()];
}
export function panels(parsed) {                                  // fpi.nsdl_panels, as flow_fpi.json stores it
  const seen = new Set(), rows = [];
  for (const p of parsed) for (const r of p || []) { const k = r.date + "|" + r.sector + "|" + r.kind; if (seen.has(k)) continue; seen.add(k); if (!/total|sovereign|others/i.test(r.sector)) rows.push(r); }
  const pivot = (kind) => { const R = rows.filter((r) => r.kind === kind), dates = [...new Set(R.map((r) => r.date))].sort(), secs = [...new Set(R.map((r) => r.sector))].sort();
    const m = new Map(R.map((r) => [r.date + "|" + r.sector, r.value]));
    return { dates, cols: Object.fromEntries(secs.map((s) => [s, dates.map((d) => (m.has(d + "|" + s) ? +fx(m.get(d + "|" + s), 1, false) : null))])) }; };
  const net = pivot("net"), auc = pivot("auc");
  return { dates: net.dates, net: net.cols, auc: auc.cols };
}
export async function loadLive(api, n = 48, onProgress) {              // the Worker fetches one report per call; 4 at a time, as the classic page
  const dates = reportDates(n), got = new Array(dates.length).fill(null); let i = 0;
  const worker = async () => { while (i < dates.length) { const k = i++; try { const d = await api("/api/fpi?d=" + dates[k]); if (d.ok) { const p = parseNsdl(d.html); got[k] = p.length ? p : null; } } catch { /* one report missing */ } if (onProgress) onProgress(); } };
  await Promise.all([worker(), worker(), worker(), worker()]);
  const P = panels(got);
  return P.dates.length ? { ...P, tried: dates.length, ok: got.filter(Boolean).length, live: true } : null;
}

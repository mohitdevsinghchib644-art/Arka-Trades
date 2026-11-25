// Arka Trades · NSDL fortnightly FPI flows by sector, read in the browser (arka_flow/fpi.py + money_flow._view_fpi).
// Used when the nightly job could not reach NSDL: the Worker fetches each fortnight's report, this file reads it the way
// pandas.read_html + parse_nsdl_sector_html do, and builds the same object as flow_fpi.json.
import { tables } from "./screener.js";
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
export function parseNsdl(html) {                                 // fpi.parse_nsdl_sector_html → [{date, sector, kind, value}]
  for (const headerRows of [0, 3]) {
    const recs = [];
    for (const t of tables(html, headerRows)) {
      const cols = t.parts.map((p) => p.join(" | ")), sec = cols.findIndex((c) => c.toLowerCase().includes("sector"));
      if (sec < 0) continue;
      t.parts.forEach((parts, ci) => {
        parts = parts.map((p) => p.trim());
        if (parts[parts.length - 1].toLowerCase() !== "equity" || !parts.some((p) => p.toLowerCase().includes("inr"))) return;
        const head = parts[0];
        let mm = head.match(/Net\s+Investment\s+([A-Za-z]+)\.?\s+\d+\s*-\s*(\d+)\s*,?\s*(\d{4})/i), kind = "net";
        if (!mm) { mm = head.match(/AUC\s+as\s+on\s+([A-Za-z]+)\.?\s+(\d+)\s*,?\s*(\d{4})/i); kind = "auc"; }
        if (!mm) return;
        const date = mdy(mm[1], mm[2], mm[3]); if (!date) return;
        for (const r of t.rows) {
          const s = String(r[sec] ?? "").trim(), raw = String(r[ci] ?? "").replace(/,/g, "").trim(), v = raw === "" || isNaN(Number(raw)) ? null : Number(raw);
          if (s && s.toLowerCase() !== "nan" && v != null) recs.push({ date, sector: s, kind, value: v });
        }
      });
    }
    if (recs.length) { const m = new Map(); for (const r of recs) m.set(r.date + "|" + r.sector + "|" + r.kind, r); return [...m.values()]; }
  }
  return [];
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

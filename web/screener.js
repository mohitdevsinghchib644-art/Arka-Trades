// Arka Trades · reads a Screener.in company page into the same "bundle" the classic app builds (data_engine._screener_raw +
// screener_scraper): summary numbers, sector, quarterly / yearly results, shareholding, balance sheet, cash flow, ratios,
// growth boxes, pros / cons, about, peer table ids and the BSE code. Tables are read the way pandas.read_html reads them.
const WS = /[\r\n]+|\s{2,}/g;
const clean = (t) => String(t || "").replace(WS, " ").trim();
function grid(table) {                                     // rows of cell texts with colspan / rowspan expanded (pandas _expand_colspan_rowspan)
  const own = (el) => el.closest("table") === table;
  const head = [...table.querySelectorAll("thead tr")].filter(own), all = [...table.querySelectorAll("tr")].filter(own);
  let body = all.filter((tr) => !head.includes(tr) && !(tr.parentElement && tr.parentElement.tagName === "TFOOT"));
  const foot = all.filter((tr) => tr.parentElement && tr.parentElement.tagName === "TFOOT");
  let hdr = head;
  if (!hdr.length) { hdr = []; while (body.length && [...body[0].children].every((c) => c.tagName === "TH")) hdr.push(body.shift()); }
  const expand = (rows) => {
    const out = [], carry = [];                             // carry[col] = [text, rows left]
    for (const tr of rows) {
      const r = []; let col = 0;
      const fill = () => { while (carry[col] && carry[col][1] > 0) { r.push(carry[col][0]); carry[col][1]--; col++; } };
      for (const td of [...tr.children].filter((c) => c.tagName === "TD" || c.tagName === "TH")) {
        fill();
        const t = clean(td.textContent), cs = Math.max(1, +td.getAttribute("colspan") || 1), rs = Math.max(1, +td.getAttribute("rowspan") || 1);
        for (let k = 0; k < cs; k++) { r.push(t); if (rs > 1) carry[col] = [t, rs - 1]; col++; }
      }
      fill();
      out.push(r);
    }
    return out;
  };
  return { head: expand(hdr), body: expand(body).concat(expand(foot)) };
}
function frames(doc) {
  return [...doc.querySelectorAll("table")].map((t) => {
    const g = grid(t), n = Math.max(0, ...g.head.map((r) => r.length), ...g.body.map((r) => r.length));
    const cols = Array.from({ length: n }, (_, i) => { const parts = g.head.map((r) => r[i] ?? ""); const last = parts.length ? parts[parts.length - 1] : ""; return last || (g.head.length ? `Unnamed: ${i}` : String(i)); });
    const rows = g.body.filter((r) => r.length).map((r) => Array.from({ length: n }, (_, i) => r[i] ?? ""));
    return { cols, rows, all: g.head.flat().concat(g.body.flat()) };
  });
}
function match(tables, must) {                             // screener_scraper._find_tables_by_header
  return tables.filter((t) => {
    const blob = (t.rows.flat().join(" ") + " " + t.cols.join(" ")).toLowerCase().replace(/\s+/g, " ");
    return must.every((c) => (Array.isArray(c) ? c.some((x) => blob.includes(x.toLowerCase())) : blob.includes(c.toLowerCase())));
  });
}
function records(t) {                                      // screener_scraper._df_to_records
  if (!t) return null;
  const periods = t.cols.slice(1), rows = [];
  for (const r of t.rows) {
    const label = String(r[0] || "").trim().replace(/\+/g, "").trim();
    if (!label || ["nan", "none"].includes(label.toLowerCase())) continue;
    rows.push({ label, values: r.slice(1).map((v) => { v = String(v).trim(); return !v || ["nan", "none"].includes(v.toLowerCase()) ? "—" : v; }) });
  }
  return rows.length ? { periods, rows } : null;
}
const M_PL = [["sales", "revenue", "interest", "financing margin"], ["net profit", "profit for the period"]];
const M_SH = ["promoters", ["fiis", "fii"], ["diis", "dii"]];
const M_BS = [["equity capital", "share capital"], ["reserves"], ["borrowings", "total liabilities"]];
const M_CF = [["cash from operating", "cash flow from operating", "operating activities"], ["cash from investing", "investing activities", "capital expenditure"]];
const M_RAT = [["debtor days"]];
const SUMMARY = { market_cap: /Market Cap[\s\S]*?<span class="number">([\d,.]+)<\/span>/i, current_price: /Current Price[\s\S]*?<span class="number">([\d,.]+)<\/span>/i,
  pe_ratio: /Stock P\/E[\s\S]*?<span class="number">([\d,.]+)<\/span>/i, book_value: /Book Value[\s\S]*?<span class="number">([\d,.]+)<\/span>/i,
  dividend_yield: /Dividend Yield[\s\S]*?<span class="number">([\d,.]+)<\/span>/i, roce: /ROCE[\s\S]*?<span class="number">([\d,.]+)<\/span>/i,
  roe: /ROE[\s\S]*?<span class="number">([\d,.]+)<\/span>/i, face_value: /Face Value[\s\S]*?<span class="number">([\d,.]+)<\/span>/i,
  high_low: /High\s*\/\s*Low[\s\S]*?<span class="number">([\d,.]+)\s*\/\s*([\d,.]+)<\/span>/i };
export function parseScreener(html, url, sym) {
  const doc = new DOMParser().parseFromString(html, "text/html"), T = frames(doc);
  const pl = match(T, M_PL), sh = match(T, M_SH), bs = match(T, M_BS), cf = match(T, M_CF), rat = match(T, M_RAT);
  const summary = {};
  for (const [k, re] of Object.entries(SUMMARY)) { const m = html.match(re); if (!m) continue; if (k === "high_low") { summary.year_high = m[1].trim(); summary.year_low = m[2].trim(); } else summary[k] = m[1].trim(); }
  const sector = {};
  for (const m of html.matchAll(/<a[^>]*title="(Broad Sector|Sector|Broad Industry|Industry)"[^>]*>([^<]+)<\/a>/gi)) sector[m[1]] = clean(m[2].replace(/&amp;/g, "&"));
  const text = (el) => clean([...el.childNodes].map((n) => n.textContent).join(" "));
  const li = (sel) => [...doc.querySelectorAll(sel)].map((x) => clean(x.textContent)).filter(Boolean).slice(0, 8);
  const ab = doc.querySelector("div.company-profile .about") || doc.querySelector("div.about");
  const growth = {};
  for (const t of T) {
    if (t.cols.length !== 2) continue;
    const cols = t.cols.join(" ").toLowerCase();
    for (const [needle, label] of [["compounded sales growth", "Sales Growth"], ["compounded profit growth", "Profit Growth"], ["stock price cagr", "Stock Price CAGR"], ["return on equity", "Return on Equity"]])
      if (cols.includes(needle)) { const rows = {}; for (const r of t.rows) { const k = String(r[0]).replace(/:/g, "").trim(); if (k) rows[k] = String(r[1]).trim(); } if (Object.keys(rows).length) growth[label] = rows; }
  }
  const ids = [...new Set([...html.matchAll(/\/api\/company\/(\d+)\/peers\//g)].map((m) => m[1]).concat([...html.matchAll(/data-(?:warehouse|company)-id="(\d+)"/g)].map((m) => m[1])))].slice(0, 4);
  let bse = null;                                           // research_data.bse_code_from_html
  for (const re of [/bseindia\.com\/[^"'<> ]*?\/(\d{6})\/?["']/i, /BSE:\s*(?:<\/?[a-z][^>]*>\s*)*(\d{6})/i]) { const m = html.match(re); if (m) { bse = m[1]; break; } }
  const h1 = html.match(/<h1[^>]*>\s*([^<]+?)\s*<\/h1>/);
  const B = { resolved: true, symbol: sym, name: h1 ? clean(h1[1].replace(/&amp;/g, "&")) : sym, url, summary, sector,
    quarterly: records(pl[0]), yearly: pl.length > 1 ? records(pl[1]) : null, shareholding: records(sh[0]), shareholding_yearly: sh.length > 1 ? records(sh[1]) : null,
    balance_sheet: records(bs[0]), cash_flow: records(cf[0]), ratios: records(rat[0]), growth,
    pros: li("div.pros li"), cons: li("div.cons li"), about: ab ? text(ab).slice(0, 1200) : "", peer_ids: ids, bse };
  if (!(B.quarterly || B.yearly || Object.keys(summary).length)) return { ...empty(sym), reason: "Screener returned no usable data" };
  return B;
}
export function parsePeers(html) {                         // data_engine._peers_cached
  const doc = new DOMParser().parseFromString(html, "text/html"), t = frames(doc)[0];
  if (!t) return null;
  const tt = /^s\.no/i.test(t.cols[0]) ? { cols: t.cols.slice(1), rows: t.rows.map((r) => r.slice(1)) } : t;
  return records(tt);
}
export const empty = (sym, reason = "Screener unavailable right now") => ({ resolved: false, symbol: sym, reason, summary: {}, sector: {}, growth: {}, pros: [], cons: [], about: "", peer_ids: [] });

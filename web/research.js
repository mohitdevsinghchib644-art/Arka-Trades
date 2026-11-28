// Arka Trades · Research screens. Each function is one screen of the classic Research terminal (research_page.py v16.1),
// written as a list of blocks: ["h", title, status, note] header · ["kpis", items, cols] · ["table", heads, rows, highlights, csv]
// · ["check", title, items, labels, dashed] · ["mini", title, rows, code] · ["na", title, needs, detail] · ["note", text]
// · ["chart", spec] · ["html", key, data] for the few custom pictures. Colours are names: g r a c p t2 t3.
// Inputs (period, window …) come in C.o with the classic defaults. Pure: no page code, so it can be tested against the Python.
import * as E from "./rsengine.js";
const ordn = (v) => { const k = Math.round(v), t = k % 100; return k + (t >= 11 && t <= 13 ? "th" : ["th", "st", "nd", "rd"][k % 10] || "th"); };      // 1st, 2nd, 3rd, 11th, 21st
const { fx, pct, rs, qty, f, g, row, nums, annualMap, debtEquity, ttmEps, hist, hist2, closeS, join, mean, median, std, cov, corr, ewm, sum, retN, pctChange, atrOf, trueRange, dfmt, asof } = E;

export const BENCH = ["^NSEI", "NIFTY 50"];
export const IDX = { "NIFTY 500": "^CRSLDX", "NIFTY 50": "^NSEI", "NIFTY BANK": "^NSEBANK", "NIFTY IT": "^CNXIT", "NIFTY MIDCAP 100": "NIFTY_MIDCAP_100.NS" };
export const SECTOR_PEERS = { RELIANCE: ["RELIANCE", "ONGC", "IOC", "BPCL"], HDFCBANK: ["HDFCBANK", "ICICIBANK", "AXISBANK", "KOTAKBANK"], ICICIBANK: ["ICICIBANK", "HDFCBANK", "AXISBANK", "KOTAKBANK"],
  AXISBANK: ["AXISBANK", "HDFCBANK", "ICICIBANK", "KOTAKBANK"], KOTAKBANK: ["KOTAKBANK", "HDFCBANK", "ICICIBANK", "AXISBANK"], SBIN: ["SBIN", "HDFCBANK", "ICICIBANK", "BANKBARODA"],
  TCS: ["TCS", "INFY", "WIPRO", "HCLTECH"], INFY: ["INFY", "TCS", "WIPRO", "HCLTECH"], WIPRO: ["WIPRO", "TCS", "INFY", "HCLTECH"], HCLTECH: ["HCLTECH", "TCS", "INFY", "WIPRO"],
  TATAMOTORS: ["TATAMOTORS", "M&M", "MARUTI", "BAJAJ-AUTO"], MARUTI: ["MARUTI", "TATAMOTORS", "M&M", "BAJAJ-AUTO"], SUNPHARMA: ["SUNPHARMA", "DRREDDY", "CIPLA", "DIVISLAB"],
  ITC: ["ITC", "HINDUNILVR", "NESTLEIND", "BRITANNIA"], HINDUNILVR: ["HINDUNILVR", "ITC", "NESTLEIND", "BRITANNIA"] };

export const GROUPS = [
  ["Overview", [["OV", "Dashboard", "Scorecard, chart, performance, valuation, quality, earnings, ownership, contradictions and filings on one screen"],
    ["SC", "Scorecard", "Five pillars out of 100 with every input and rule shown, plus what stands out"], ["DES", "Description", "Company profile, key fundamentals, management, growth snapshot, pros and cons"],
    ["SYN", "Research intel", "Evidence sheet, contradiction detection and AI synthesis"]]],
  ["Financials", [["FA", "Income statement", "Quarterly / yearly results with QoQ and YoY growth"], ["BS", "Balance sheet", "Assets, liabilities, borrowings and reserves"],
    ["CF", "Cash flow", "Operating, investing and financing cash flows"], ["RAT", "Ratios", "Efficiency and return ratios"],
    ["GRW", "Growth", "1 / 3 / 5 / 10-year compound growth and common-size statement"], ["ERN", "Earnings trend", "Each quarter against the same quarter a year earlier"]]],
  ["Quality", [["HC", "Health check", "Piotroski-style quality score and automatic red / green flags"], ["FH", "Financial health", "Leverage, coverage, liquidity, free cash flow and cash conversion"],
    ["DUP", "DuPont", "Return on equity split into margin, asset turnover and leverage"]]],
  ["Valuation", [["VAL", "Valuation", "P/E, P/B, earnings yield, Graham number, P/E history and P/E band on price"], ["DCF", "Reverse DCF", "The growth rate today's price already assumes, with a sensitivity grid"],
    ["RV", "Peers", "Relative value versus sector peers"], ["ANR", "Analysts", "Price targets, rating distribution and rating changes"]]],
  ["Price", [["GP", "Chart", "Full-size price chart"], ["SWG", "Swing setup", "Trend checklist, breakout level, ATR stop, support, downside numbers and position size"],
    ["PB", "Price behaviour", "Up vs down days, average moves, biggest days, volume and delivery on up vs down days, streaks and gaps"],
    ["HP", "Price history", "Daily open / high / low / close / volume table with CSV export"], ["TECH", "Technicals", "Trend, momentum, moving averages and pivot levels"],
    ["SEAS", "Seasonality", "Monthly return heatmap, average month and win rate"], ["TRA", "Total return", "Price return plus dividends, per-year return"]]],
  ["Risk & quant", [["QR", "Quant", "Relative strength vs NIFTY 500 / 50, momentum, volatility, beta, drawdown"], ["COMP", "Compare", "Rebased performance against any stocks or indices, with correlation"],
    ["BETA", "Beta", "Regression against the index: beta, alpha, R-squared, rolling beta"], ["HVG", "Volatility", "Realised volatility 20 / 60 / 120 sessions, percentile, ATR"],
    ["PERF", "vs NIFTY 50", "Relative performance, alpha, beta, volatility, drawdown"]]],
  ["Ownership", [["HDS", "Shareholding", "Promoter / FII / DII / public holding trend"], ["DLV", "Deliveries", "Delivered vs traded quantity and delivery %, daily / weekly / monthly (NSE)"],
    ["FNO", "F&O positioning", "Futures and options: model rank, build-up, option chain, days like today and signal status"], ["MF", "MF holdings", "Funds and institutions holding over 1%, quarter by quarter, from company filings"]]],
  ["Events & news", [["EVTS", "Events", "Timeline of results, board meetings, dividends, splits and material filings"], ["CN", "News", "Company news timeline"],
    ["CACS", "Filings", "Exchange filings from BSE, by category"], ["DVD", "Dividends", "Dividend history, splits and bonus issues"]]],
  ["Workspace", [["NOTE", "Thesis notes", "Your bull / bear case, catalysts, risks and target"], ["SRC", "Sources", "Where every number on this screen comes from"]]],
];
export const FN = Object.fromEntries(GROUPS.flatMap(([grp, items]) => items.map(([c, n, d]) => [c, { name: n, desc: d, grp }])));
export const ALIASES = { SCORE: "SC", SCORECARD: "SC", RATING: "SC", SWING: "SWG", SETUP: "SWG", TREND: "SWG", FO: "FNO", OI: "FNO", OPTIONS: "FNO", FUTURES: "FNO",
  OVERVIEW: "OV", DASH: "OV", DASHBOARD: "OV", PROFILE: "DES", MGMT: "DES", HEALTH: "HC", PIOTROSKI: "HC", VALUATION: "VAL", PE: "VAL", FINANCIALS: "FA", RESULTS: "FA", PL: "FA", IS: "FA",
  BALANCE: "BS", CASH: "CF", CASHFLOW: "CF", RATIOS: "RAT", SHAREHOLDING: "HDS", OWN: "HDS", HOLDING: "HDS", MFH: "MF", PEERS: "RV", COMPARE: "COMP", PERFORMANCE: "PERF",
  CHART: "GP", G: "GP", PRICES: "HP", HIST: "HP", VOL: "HVG", HV: "HVG", TR: "TRA", TOTALRETURN: "TRA", RDCF: "DCF", DUPONT: "DUP", GROWTH: "GRW", CAGR: "GRW", EVENTS: "EVTS", EVT: "EVTS",
  CALENDAR: "EVTS", TECHNICALS: "TECH", SEASONALITY: "SEAS", SEAG: "SEAS", ANALYST: "ANR", TARGET: "ANR", DIVIDENDS: "DVD", NEWS: "CN", ANNOUNCEMENTS: "CACS", FILINGS: "CACS",
  THESIS: "NOTE", NOTES: "NOTE", SOURCES: "SRC", DELIVERY: "DLV", DELIVERIES: "DLV", DEL: "DLV", FINHEALTH: "FH", EARNINGS: "ERN", EE: "ERN", QUANT: "QR", BEHAVIOUR: "PB",
  BEHAVIOR: "PB", CANDLES: "PB", UPDOWN: "PB", RS: "QR", MOMENTUM: "QR", INTEL: "SYN", AI: "SYN", SYNTHESIS: "SYN", EVIDENCE: "SYN" };
export const resolveCode = (t) => { t = String(t || "").trim().toUpperCase().replace(/<GO>$/, "").trim(); return FN[t] ? t : ALIASES[t] || null; };

// ── shared pieces ──
const H_ = (title, status = null, note = null) => ["h", title, status, note];
const K = (items, cols = 4) => ["kpis", items, cols];
const T = (heads, rows, hl = [], csv = null) => ["table", heads, rows, hl, csv];
const NA = (title, needs, detail = null) => ["na", title, needs, detail || "No verified dataset returned this field."];
const NOTE = (t) => ["note", t];
const recT = (rec, hl = [], lastN = null, csv = null) => {
  if (!rec || !rec.rows || !rec.rows.length) return [NA("Table", "Screener.in company page")];
  let p = rec.periods, rows = rec.rows;
  if (lastN && p.length > lastN) { p = p.slice(-lastN); rows = rows.map((r) => ({ label: r.label, values: r.values.slice(-lastN) })); }
  return [T(p, rows, hl, csv)];
};
const S5 = (C) => E.hist(C.H, "5y");
const closeOf = (C, period = "5y", fb = "2y") => { const h = hist2(C.H, period, fb); return h ? { d: h.d, v: h.c } : null; };
const bench = (C, name = "NIFTY 500") => { const s = C.IX && C.IX[name]; return s && s.d.length > 30 ? s : null; };
const idxHist = (C, name) => { const s = C.IX && C.IX[name]; return s && s.d.length ? s : null; };
const info = (C) => (C.Y && C.Y.info) || {};
const pyf = (v) => (Number.isInteger(v) ? v.toFixed(1) : String(v));      // str() of a Python float: 0.0, 1.25
function yahooStmt(C, kind, title, hl, rows = 40, cols = 6) {             // the classic page's Yahoo fallback when Screener has no table
  const st = C.Y && C.Y.stmt && C.Y.stmt[kind];
  if (!st || !st.rows.length) return [H_(title, "unavailable"), NA("Table", "Yahoo Finance dataset")];
  return [H_(title, "stale"), T(st.cols.slice(0, cols).map((c) => String(c).slice(0, 10)), st.rows.slice(0, rows).map(([l, v]) => ({ label: l, values: v.slice(0, cols).map((x) => (x == null ? "—" : fx(x, 2))) })), hl)];
}

function growthRows(rec, labels, lags, lagNames) {
  const out = [];
  for (const lab of labels) {
    const r = row(rec, lab); if (!r) continue;
    const n = nums(r), vals = [r.values.length ? r.values.at(-1) : "—"];
    for (const lag of lags) {
      if (n.length > lag && n.at(-1) != null && n[n.length - 1 - lag] != null) { const b = n[n.length - 1 - lag]; vals.push(b > 0 ? `${fx((n.at(-1) / b - 1) * 100, 1, false, true)}%` : "n/m"); }
      else vals.push("—");
    }
    out.push({ label: r.label, values: vals });
  }
  return [["LATEST", ...lagNames], out];
}
function barChart(rec, labels, colors, n = 12) {
  if (!rec) return [];
  const seen = new Set(), series = [];
  labels.forEach((lab, i) => { const r = row(rec, lab); if (!r || seen.has(r.label)) return; seen.add(r.label); series.push({ name: r.label, color: colors[i], y: nums(r).slice(-n) }); });
  return series.length ? [["chart", { kind: "bars", x: rec.periods.slice(-n), series, h: 290 }]] : [];
}
function liquidity(C) {
  const bs = (C.Y && C.Y.bs) || {}, ca = bs.ca || [], cl = bs.cl || [];
  const crs = [0, 1].map((i) => [ca[i] ?? null, cl[i] ?? null]);
  return crs.every(([a, b]) => a != null && b) ? [crs[0][0] / crs[0][1], crs[1][0] / crs[1][1]] : null;
}
const lastNum = (v) => { const a = v.filter((x) => x != null); return a.length ? a.at(-1) : null; };
const fmtDtCell = (v) => (v == null || (typeof v === "number" && !isFinite(v)) ? "—" : typeof v === "number" ? (Number.isInteger(v) ? fx(v, 0) : fx(v, 2)) : String(v));

// ── DES: description ──
function DES(C) {
  const B = C.B, s = B.summary || {}, I = info(C), out = [H_("Key fundamentals", B.resolved ? "live" : "unavailable", B.url || null)];
  const ed = (C.Y && C.Y.earnings) || null, hh = hist(C.H, "1y");
  const hl = hh && hh.d.length > 20 ? [`₹${fx(Math.max(...hh.h), 2)} / ₹${fx(Math.min(...hh.l), 2)}`, "Yahoo, last 1 year"]
    : s.year_high ? [`₹${s.year_high} / ₹${s.year_low}`, "Screener"] : ["N/A", "Screener"];
  const sc = B.sector || {}, sector = sc.Sector || sc["Broad Sector"] || I.sector || "N/A", industry = sc.Industry || sc["Broad Industry"] || I.industry || "";
  out.push(K([["Market Cap", s.market_cap ? `₹${s.market_cap} Cr` : "N/A", "Screener"], ["Stock P/E", s.pe_ratio ? `${s.pe_ratio}x` : "N/A", "Screener"],
    ["Book Value", s.book_value ? `₹${s.book_value}` : "N/A", "Screener"], ["Face Value", s.face_value ? `₹${s.face_value}` : "N/A", "Screener"], ["52W High / Low", hl[0], hl[1]],
    ["Sector", String(sector), String(industry) || (Object.keys(sc).length ? "Screener" : "Yahoo")],
    ["Employees", Number.isInteger(I.fullTimeEmployees) ? fx(I.fullTimeEmployees, 0) : "N/A", "Yahoo"], ["Next earnings", ed || "N/A", ed ? "Yahoo calendar" : "Not published"]], 4));
  const growth = B.growth || {};
  if (Object.keys(growth).length) { out.push(H_("Growth snapshot", "live")); out.push(["boxes", Object.entries(growth)]); }
  const desc = I.longBusinessSummary || B.about;
  if (desc) out.push(["text", "Business", desc.slice(0, 1400)]);
  out.push(...management(C));
  if ((B.pros || []).length || (B.cons || []).length) out.push(["proscons", B.pros || [], B.cons || []]);
  if (!B.resolved) out.push(NA("Screener fundamentals", "Screener.in company page", B.reason));
  return out;
}
function management(C) {
  const off = (info(C).companyOfficers || []).filter((o) => o && o.name); if (!off.length) return [];
  return [H_("Management · Yahoo"), T(["ROLE", "AGE", "PAY"], off.slice(0, 12).map((o) => ({ label: String(o.name), values: [String(o.title || "—").slice(0, 60), String(o.age || "—"),
    typeof o.totalPay === "number" && o.totalPay ? `₹${fx(o.totalPay / 1e7, 2)} Cr` : "—"] })))];
}

// ── HC: health check ──
function HC(C) {
  const B = C.B; if (!B.resolved) return [NA("Health check", "Screener yearly P&L, balance sheet and cash flow", B.reason)];
  const res = E.piotroski(B, liquidity(C)), out = [H_("Quality score · Piotroski-style, computed from Screener annual data", res ? "live" : "unavailable")];
  if (!res) out.push(NA("Quality score", "At least two fiscal years of P&L and balance sheet"));
  else {
    const [tests, t, p] = res, av = tests.filter((x) => x[1] != null), score = av.filter((x) => x[1]).length, r = av.length ? score / av.length : 0;
    const [grade, gc] = r >= 0.75 ? ["STRONG", "g"] : r >= 0.45 ? ["AVERAGE", "a"] : ["WEAK", "r"];
    out.push(K([["Score", `${score} / ${av.length}`, `${tests.length - av.length} test(s) not computable`, gc], ["Reading", grade, `Comparing ${p} → ${t}`, gc]], 2));
    out.push(["check", "The 9 tests", tests, ["Pass", "Fail", "N/A"]]);
  }
  const fl = E.flags(B);
  out.push(H_("Automatic flags", fl.length ? "live" : null));
  out.push(fl.length ? ["check", `${fl.filter((x) => x[1] === false).length} warning(s) · ${fl.filter((x) => x[1]).length} positive(s)`, fl, ["Good", "Warning", "—"]]
    : ["text", "No flags triggered", "None of the rule thresholds were crossed, or the data needed was not available."]);
  out.push(NOTE("Rule-based screen for research only — not investment advice. Thresholds: promoter ±2/1 pp, debt +50% in 3y, CFO/NP <50% or ≥90%, OPM ±3 pp, 3-yr CAGR <0% or ≥15%."));
  return out;
}

// ── VAL: valuation ──
function VAL(C) {
  const B = C.B, s = B.summary || {}, h = hist2(C.H, "5y"); if (!h) return [NA("Valuation", "Yahoo daily price history")];
  const close = { d: h.d, v: h.c }, price = close.v.at(-1), ttm = ttmEps(B), bv = f(s.book_value);
  const pe = ttm && ttm > 0 ? price / ttm : null, pb = bv && bv > 0 ? price / bv : null, gr = ttm && bv && ttm > 0 && bv > 0 ? Math.sqrt(22.5 * ttm * bv) : null;
  const out = [H_("Valuation · computed from Screener EPS / book value + Yahoo price", ttm ? "live" : "unavailable"),
    K([["TTM EPS", rs(ttm), "Sum of last 4 quarters"], ["P/E (TTM)", pe ? `${fx(pe, 1, false)}x` : ttm != null ? "n/m (loss)" : "N/A", "Price ÷ TTM EPS"],
      ["P/B", pb ? `${fx(pb, 2, false)}x` : "N/A", "Price ÷ book value"], ["Earnings yield", pct(ttm ? (ttm / price) * 100 : null, 2), "TTM EPS ÷ price"],
      ["Graham number", rs(gr), "√(22.5 × EPS × BVPS)"], ["Price vs Graham", gr ? pct((price / gr - 1) * 100, 1, true) : "N/A", "Negative = below Graham value", gr ? (price < gr ? "g" : "r") : null]], 3)];
  out.push(...valueField(C, close));
  const pts = E.peHistory(B, close).map((x) => [x[0], x[2]]);
  out.push(H_("Historical P/E band", pts.length >= 4 ? "live" : "unavailable", "Quarter-end price ÷ trailing-4-quarter EPS. Limited by how many quarters Screener shows."));
  if (pts.length < 4) { out.push(NA("P/E band", "≥ 7 quarters of positive EPS plus matching price history")); return out; }
  const v = pts.map((x) => x[1]), med = median(v);
  out.push(["chart", { kind: "line", x: pts.map((x) => x[0]), series: [{ name: "P/E (TTM)", color: "c", y: v, markers: true }], hlines: [[med, `median ${fx(med, 1, false)}x`]], now: pe ? [close.d.at(-1), pe] : null, h: 300 }]);
  const iMin = v.indexOf(Math.min(...v)), iMax = v.indexOf(Math.max(...v)), pctile = pe ? (v.filter((x) => x < pe).length / v.length) * 100 : null;
  out.push(K([["Min P/E", `${fx(v[iMin], 1, false)}x`, pts[iMin][0].slice(0, 7)], ["Median P/E", `${fx(med, 1, false)}x`, `${v.length} quarters`], ["Max P/E", `${fx(v[iMax], 1, false)}x`, pts[iMax][0].slice(0, 7)],
    ["Current vs history", pctile != null ? `${ordn(pctile)} pct` : "N/A", ttm && ttm > 0 ? `Price at median P/E: ${rs(med * ttm)}` : ""]], 4));
  out.push(...peBand(B, close));
  return out;
}
function valueField(C, close) {
  const rows = E.valueRows(C.B, close, info(C), (C.Y && C.Y.targets) || {}); if (rows.length < 2) return [];
  const price = close.v.at(-1);
  return [H_("Value range · what the price would be under each method", "live", `The white line is today's price, ${rs(price)}. The dark tick inside a bar is that method's middle value.`),
    ["html", "valuefield", { rows, price }],
    NOTE("Arithmetic on reported numbers, not price targets. The methods disagree because they assume different things; a price inside most bars is ordinary, a price above all of them is paying for growth that has not shown up yet.")];
}
function peBand(B, close) {
  const pts = E.peHistory(B, close); if (pts.length < 4) return [];
  const pes = pts.map((x) => x[2]).sort((a, b) => a - b), lo = pes[0], med = pes[Math.floor(pes.length / 2)], hi = pes.at(-1);
  const x = [], e = [], c = []; let k = -1;                       // EPS carried forward from each quarter end, on every trading day after the first one
  close.d.forEach((d, i) => { while (k + 1 < pts.length && pts[k + 1][0] <= d) k++; if (k >= 0) { x.push(d); e.push(pts[k][1]); c.push(close.v[i]); } });
  return [H_("P/E band on price · where the price would be at its own low, median and high P/E"),
    ["chart", { kind: "line", x, series: [{ name: `${fx(hi, 0, false)}x (highest)`, color: "r", y: e.map((v) => v * hi), dash: true }, { name: `${fx(med, 0, false)}x (median)`, color: "a", y: e.map((v) => v * med), dash: true },
      { name: `${fx(lo, 0, false)}x (lowest)`, color: "g", y: e.map((v) => v * lo), dash: true }, { name: "Price", color: "w", y: c, width: 2 }], h: 320 }]];
}

// ── FA / BS / CF / RAT / HDS / RV ──
function FA(C) {
  const mode = C.o.fin || "Quarterly", rec = mode === "Quarterly" ? C.B.quarterly : C.B.yearly;
  if (!rec) return yahooStmt(C, mode === "Quarterly" ? "qincome" : "income", `${mode} income statement · Yahoo fallback`, ["Total Revenue", "Net Income", "EBITDA", "Diluted EPS"], 40, 8);
  const out = [H_(`${mode} results · Screener`, "live"), ...barChart(rec, ["Sales", "Revenue", "Net Profit"], ["c", "c", "g"])];
  const [lags, names] = mode === "Quarterly" ? [[1, 4], ["QoQ", "YoY"]] : [[1, 3], ["YoY", "3Y TOTAL"]];
  const [heads, grows] = growthRows(rec, ["Sales", "Revenue", "Operating Profit", "Financing Profit", "Net Profit", "EPS"], lags, names);
  if (grows.length) out.push(H_("Growth (latest vs earlier)"), T(heads, grows, ["Sales", "Revenue", "Net Profit"]));
  out.push(H_("Full table"), ...recT(rec, ["Sales", "Revenue", "Operating Profit", "Net Profit", "EPS", "OPM"], null, `${C.sym}_results_${mode.toLowerCase()}`));
  return out;
}
function BS(C) {
  const rec = C.B.balance_sheet;
  if (!rec) return yahooStmt(C, "balance", "Balance sheet · Yahoo fallback", ["Total Assets", "Total Debt", "Stockholders Equity"], 50, 6);
  return [H_("Balance sheet · Screener", "live"), ...barChart(rec, ["Borrowings", "Reserves", "Total Assets"], ["r", "g", "c"], 10),
    ...recT(rec, ["Total Assets", "Total Liabilities", "Borrowings", "Reserves"], null, `${C.sym}_balance_sheet`)];
}
function CF(C) {
  const rec = C.B.cash_flow;
  if (!rec) return yahooStmt(C, "cashflow", "Cash flow · Yahoo fallback", ["Operating Cash Flow", "Free Cash Flow", "Capital Expenditure"], 40, 6);
  return [H_("Cash flow · Screener", "live"), ...barChart(rec, ["Cash from Operating", "Cash from Investing", "Cash from Financing"], ["g", "r", "a"], 10),
    ...recT(rec, ["Operating", "Investing", "Financing", "Net Cash"], null, `${C.sym}_cash_flow`)];
}
function RAT(C) {
  const s = C.B.summary || {}, de = debtEquity(C.B), rec = C.B.ratios;
  return [K([["Debt / Equity", de != null ? `${pyf(de)}x` : "N/A", "Borrowings ÷ (Equity + Reserves)"], ["ROCE", s.roce ? `${s.roce}%` : "N/A", "Screener"],
    ["ROE", s.roe ? `${s.roe}%` : "N/A", "Screener"], ["Face Value", s.face_value ? `₹${s.face_value}` : "N/A", "Screener"]], 4),
    H_("Efficiency ratios · Screener", rec ? "live" : "unavailable"),
    ...(rec ? recT(rec, ["ROCE", "Cash Conversion"], null, `${C.sym}_ratios`) : [NA("Efficiency ratios", "Screener 'Ratios' table (not published for banks / NBFCs)")])];
}
function HDS(C) {
  const mode = C.o.sh || "Quarterly", rec = mode === "Quarterly" ? C.B.shareholding : C.B.shareholding_yearly;
  const out = [H_(`Shareholding pattern (${mode.toLowerCase()}) · Screener`, rec ? "live" : "unavailable")];
  if (!rec) { out.push(NA("Shareholding", "Screener or Yahoo holders data")); return out; }
  const colors = [["promoters", "a"], ["fiis", "c"], ["diis", "g"], ["government", "p"], ["public", "r"]], latest = [], series = [];
  for (const r of rec.rows) {
    const nm = r.label.toLowerCase(), c = (colors.find(([k]) => nm.startsWith(k)) || [])[1]; if (!c) continue;
    const vals = nums(r); series.push({ name: r.label, color: c, y: vals, markers: true });
    const clean = vals.filter((v) => v != null);
    if (clean.length) { const d = clean.length >= 2 ? clean.at(-1) - clean.at(-2) : null;
      latest.push([r.label, `${fx(clean.at(-1), 2, false)}%`, d != null ? `${fx(d, 2, false, true)} pp vs prev` : "", d != null ? (d > 0 ? "g" : d < 0 ? "r" : null) : null]); }
  }
  if (latest.length) out.push(K(latest, Math.min(latest.length, 5)));
  if (series.length) out.push(["chart", { kind: "line", x: rec.periods, series, h: 320 }]);
  out.push(...recT(rec, ["Promoters", "FIIs", "DIIs"], 14, `${C.sym}_shareholding_${mode.toLowerCase()}`));
  return out;
}
function RV(C) {
  const rec = C.peers;
  if (rec) return [H_("Peer comparison · Screener", "live"), T(rec.periods, rec.rows, [C.B.name || C.sym, C.sym], `${C.sym}_peers`)];
  const out = [H_("Peer comparison · Screener", "unavailable")], peers = (SECTOR_PEERS[C.sym] || []).filter((p) => p !== C.sym), q = C.quotes || {};
  if (peers.length) {
    const rows = [C.sym, ...peers].filter((p) => q[p]).map((p) => ({ label: p + (p === C.sym ? " ◀" : ""), values: [`₹${fx(q[p].price, 2)}`, `${fx(q[p].pct, 2, false, true)}%`] }));
    if (rows.length) return [...out, ["text", "Note", "Screener peer table did not load; showing curated peers with live prices only."], T(["PRICE", "CHG"], rows, [C.sym])];
  }
  return [...out, NA("Peer set", "Screener peer table or a curated peer map")];
}

// ── PERF / TECH / SEAS / ANR / DVD ──
function PERF(C) {
  const h = hist2(C.H, "2y", "1y"); if (!h) return [NA("Performance", "Yahoo daily price history")];
  const ix = idxHist(C, "NIFTY 50"); if (!ix) return [NA(`${BENCH[1]} comparison`, `Yahoo history for ${BENCH[0]}`)];
  const J = join({ d: h.d, v: h.c }, ix); if (J.d.length < 60) return [NA("Performance", "≥ 60 overlapping trading days")];
  const w = { d: J.d.slice(-252), s: J.s.slice(-252), b: J.b.slice(-252) };
  const out = [H_(`Rebased performance · ${C.sym} vs ${BENCH[1]} (1Y = 100)`, "live"),
    ["chart", { kind: "line", x: w.d, series: [{ name: C.sym, color: "a", y: w.s.map((x) => (x / w.s[0]) * 100), width: 2 }, { name: BENCH[1], color: "t2", y: w.b.map((x) => (x / w.b[0]) * 100) }], h: 300 }]];
  const rows = [];
  for (const [lab, n] of [["1 month", 21], ["3 months", 63], ["6 months", 126], ["1 year", 252], ["2 years", 504]]) if (J.d.length > n) {
    const a = (J.s.at(-1) / J.s.at(-1 - n) - 1) * 100, b = (J.b.at(-1) / J.b.at(-1 - n) - 1) * 100;
    rows.push({ label: lab, values: [`${fx(a, 2, false, true)}%`, `${fx(b, 2, false, true)}%`, `${fx(a - b, 2, false, true)} pp`, a > b ? "OUTPERFORM" : "UNDERPERFORM"] });
  }
  out.push(H_("Returns vs benchmark"), T([C.sym, BENCH[1], "ALPHA", "STATE"], rows, ["1 year"]));
  const rs_ = pctChange(w.s), rb = pctChange(w.b), vb = std(rb) ** 2, beta = vb ? cov(rs_, rb) / vb : null, cr = corr(rs_, rb);
  const vs = std(rs_) * Math.sqrt(252) * 100, vbb = std(rb) * Math.sqrt(252) * 100; let pk = -Infinity;
  const mdd = Math.min(...w.s.map((x) => { pk = Math.max(pk, x); return x / pk - 1; })) * 100, r1 = (w.s.at(-1) / w.s[0] - 1) * 100;
  out.push(H_("Risk (1 year, daily returns)"), K([["Beta", beta != null ? fx(beta, 2, false) : "N/A", ">1 moves more than the index"], ["Correlation", fx(cr, 2, false), `with ${BENCH[1]}`],
    ["Volatility (ann.)", `${fx(vs, 1, false)}%`, `Index ${fx(vbb, 1, false)}%`], ["Max drawdown", `${fx(mdd, 1, false)}%`, "Peak-to-trough, 1Y", "r"],
    ["Return / volatility", vs ? fx(r1 / vs, 2, false) : "N/A", "1Y return ÷ ann. vol (no risk-free rate)"]], 5));
  return out;
}
function TECH(C) {
  const h = hist(C.H, "2y"); if (!h || h.d.length < 30) return [NA("Technicals", "Yahoo Finance daily history")];
  const c = h.c, last = c.at(-1), rsi = E.rsiWilder(c).at(-1), atr = atrOf(h), v = h.v;
  const avgv = v.length > 21 ? mean(v.slice(-21, -1)) : NaN, buzz = avgv && isFinite(avgv) ? v.at(-1) / avgv : NaN;
  const e12 = ewm(c, { span: 12 }), e26 = ewm(c, { span: 26 }), macd = e12.map((x, i) => x - e26[i]), sig = ewm(macd, { span: 9 }), hv = macd.at(-1) - sig.at(-1);
  const sma50 = c.length >= 50 ? mean(c.slice(-50)) : null, sma200 = c.length >= 200 ? mean(c.slice(-200)) : null;
  let trend, regime;
  if (sma50 && sma200) { trend = last > sma50 && last > sma200 ? ["UPTREND", "g"] : last < sma50 && last < sma200 ? ["DOWNTREND", "r"] : ["MIXED", "a"]; regime = sma50 > sma200 ? ["GOLDEN CROSS", "g"] : ["DEATH CROSS", "r"]; }
  else trend = regime = ["N/A", "t3"];
  const rz = rsi >= 70 ? ["OVERBOUGHT", "r"] : rsi <= 30 ? ["OVERSOLD", "g"] : ["NEUTRAL", "t2"], mom = hv > 0 ? ["BULLISH", "g"] : ["BEARISH", "r"];
  const h252 = Math.max(...h.h.slice(-252)), l252 = Math.min(...h.l.slice(-252));
  const out = [H_("Signal summary · rule-based, not advice", "live"), K([["Trend (vs SMA 50/200)", trend[0], "Price above both = uptrend", trend[1]], ["MA regime", regime[0], "SMA 50 vs SMA 200", regime[1]],
    ["RSI zone", rz[0], `RSI 14 = ${fx(rsi, 1, false)}`, rz[1]], ["MACD momentum", mom[0], `Histogram ${fx(hv, 2, false, true)}`, mom[1]]], 4),
    H_("Momentum & volatility · computed from Yahoo daily data", "live"), K([["Close", `₹${fx(last, 2)}`, ""], ["ATR 14", `₹${fx(atr, 2)}`, `${fx((atr / last) * 100, 2, false)}% of price`],
      ["Volume buzz", isFinite(buzz) ? `${fx(buzz, 2, false)}x` : "N/A", "Today vs 20-day avg"], ["MACD / Signal", fx(macd.at(-1), 2, false), `Signal ${fx(sig.at(-1), 2, false)}`],
      ["52W high", `₹${fx(h252, 2)}`, `${fx((last / h252 - 1) * 100, 1, false, true)}% from high`], ["52W low", `₹${fx(l252, 2)}`, `${fx((last / l252 - 1) * 100, 1, false, true)}% above low`]], 3)];
  const ma = [];
  for (const n of [10, 20, 50, 100, 200]) if (c.length >= n) { const m = mean(c.slice(-n)); ma.push({ label: `SMA ${n}`, values: [`₹${fx(m, 2)}`, `${fx((last / m - 1) * 100, 2, false, true)}%`, last >= m ? "ABOVE" : "BELOW"] }); }
  for (const n of [20, 50]) { const m = ewm(c, { span: n }).at(-1); ma.push({ label: `EMA ${n}`, values: [`₹${fx(m, 2)}`, `${fx((last / m - 1) * 100, 2, false, true)}%`, last >= m ? "ABOVE" : "BELOW"] }); }
  const i = C.open && h.d.length >= 2 ? h.d.length - 2 : h.d.length - 1, Hh = h.h[i], L = h.l[i], Cc = h.c[i], P = (Hh + L + Cc) / 3;
  const piv = [["R3", Hh + 2 * (P - L)], ["R2", P + (Hh - L)], ["R1", 2 * P - L], ["PIVOT", P], ["S1", 2 * P - Hh], ["S2", P - (Hh - L)], ["S3", L - 2 * (Hh - P)]];
  out.push(["cols", [[H_("Moving averages"), T(["VALUE", "VS PRICE", "STATE"], ma, ["SMA 50", "SMA 200"])],
    [H_("Pivot levels (classic)", null, `From session ${h.d[i]}`), T(["LEVEL", "VS PRICE"], piv.map(([n, v]) => ({ label: n, values: [`₹${fx(v, 2)}`, `${fx((v / last - 1) * 100, 2, false, true)}%`] })), ["PIVOT"])]]]);
  const rr = [["1 week", 5], ["1 month", 21], ["3 months", 63], ["6 months", 126], ["1 year", 252]].filter(([, n]) => c.length > n).map(([lab, n]) => ({ label: lab, values: [`${fx((last / c[c.length - 1 - n] - 1) * 100, 2, false, true)}%`] }));
  const bm = mean(c.slice(-20)), bs = std(c.slice(-20));
  out.push(["cols", [[H_("Returns"), T(["RETURN"], rr)], [H_("Bollinger (20, 2)"), T(["LOWER", "MID", "UPPER"], [{ label: "Band", values: [`₹${fx(bm - 2 * bs, 2)}`, `₹${fx(bm, 2)}`, `₹${fx(bm + 2 * bs, 2)}`] }])]]]);
  return out;
}
const MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
function SEAS(C) {
  const h = hist2(C.H, "5y"); if (!h || h.d.length < 260) return [NA("Seasonality", "≥ 1 year of Yahoo daily history")];
  const me = new Map(); h.d.forEach((d, i) => me.set(d.slice(0, 7), h.c[i]));     // month-end close
  const ks = [...me.keys()], tbl = new Map();
  for (let i = 1; i < ks.length; i++) { const y = +ks[i].slice(0, 4), m = +ks[i].slice(5, 7); if (!tbl.has(y)) tbl.set(y, Array(12).fill(null)); tbl.get(y)[m - 1] = (me.get(ks[i]) / me.get(ks[i - 1]) - 1) * 100; }
  const years = [...tbl.keys()].sort((a, b) => a - b), Z = years.map((y) => tbl.get(y));
  const col = (m) => Z.map((r) => r[m]).filter((v) => v != null);
  return [H_(`Monthly returns heatmap · ${years.length} years`, "live"), ["html", "heat", { years, Z }],
    T(MONTHS, [{ label: "Average", values: MONTHS.map((_, m) => (col(m).length ? `${fx(mean(col(m)), 2, false, true)}%` : "—")) },
      { label: "Win rate", values: MONTHS.map((_, m) => (col(m).length ? `${fx((col(m).filter((v) => v > 0).length / col(m).length) * 100, 0, false)}%` : "—")) },
      { label: "Years", values: MONTHS.map((_, m) => String(col(m).length)) }], ["Average"]),
    NOTE("Past seasonality is descriptive only; small samples (≤ 5 years) are noisy.")];
}
function ANR(C) {
  const I = info(C), tg = (C.Y && C.Y.targets) || {}, has = Object.keys(tg).length || I.targetMeanPrice, out = [H_("Analyst targets · Yahoo Finance", has ? "live" : "unavailable")];
  if (has) {
    const gv = (k, ik) => tg[k] || I[ik], fm = (v) => (typeof v === "number" ? `₹${fx(v, 2)}` : "N/A"), mn = gv("mean", "targetMeanPrice"), cur = gv("current", "currentPrice");
    const up = typeof mn === "number" && typeof cur === "number" && cur ? (mn / cur - 1) * 100 : null;
    out.push(K([["Mean target", fm(mn), up != null ? pct(up, 1, true) + " upside" : "", up != null ? (up && up > 0 ? "g" : "r") : null], ["High", fm(gv("high", "targetHighPrice")), ""],
      ["Low", fm(gv("low", "targetLowPrice")), ""], ["Analysts", String(I.numberOfAnalystOpinions ?? "N/A"), String(I.recommendationKey || "").toUpperCase()]], 4));
  } else out.push(NA("Price targets", "Yahoo analyst coverage (thin for small/mid caps)"));
  const rec = (C.Y && C.Y.rec) || [];
  if (rec.length) { const keys = ["strongBuy", "buy", "hold", "sell", "strongSell"];
    out.push(H_("Rating distribution", "live"), T(rec.slice(0, 6).map((r) => r.period), keys.map((k) => ({ label: k, values: rec.slice(0, 6).map((r) => fmtDtCell(r[k])) })))); }
  const ud = (C.Y && C.Y.ud) || [];
  if (ud.length) out.push(H_("Recent rating changes", "live"), T(["FIRM", "TOGRADE", "FROMGRADE", "ACTION"], ud.slice(0, 25).map((x) => ({ label: x.date.slice(0, 10), values: [x.firm, x.to, x.from, x.action].map(String) }))));
  return out;
}
function DVD(C) {
  const d = C.divs || [], out = [H_("Dividend history · Yahoo Finance", d.length ? "live" : "unavailable")];
  if (!d.length) out.push(NA("Dividends", "Yahoo dividend history"));
  else {
    const lastD = d.at(-1)[0], cut = E.addDays(lastD, -365), ttm = sum(d.filter((x) => x[0] >= cut).map((x) => x[1])), h = hist(C.H, "1y"), px = h && h.d.length ? h.c.at(-1) : null;
    out.push(K([["Last dividend", `₹${fx(d.at(-1)[1], 2)}`, lastD], ["Trailing 12M", `₹${fx(ttm, 2)}`, "Sum of payouts"], ["TTM yield", pct(px ? (ttm / px) * 100 : null, 2), "TTM payout ÷ price"],
      ["Payouts on record", String(d.length), ""]], 4));
    const yr = new Map(); d.forEach(([dd, a]) => yr.set(dd.slice(0, 4), (yr.get(dd.slice(0, 4)) || 0) + a));
    if (yr.size >= 2) out.push(["chart", { kind: "bars", x: [...yr.keys()], series: [{ name: "₹ / share", color: "g", y: [...yr.values()] }], h: 220 }]);
    out.push(T(["₹ PER SHARE"], [...d].reverse().slice(0, 25).map(([dd, a]) => ({ label: dd, values: [fx(a, 2)] }))));
  }
  const sp = C.splits || [];
  if (sp.length) out.push(H_("Splits / bonus", "live"), T(["RATIO"], [...sp].reverse().slice(0, 15).map(([dd, r]) => ({ label: dd, values: [`${g(r)} : 1`] }))));
  return out;
}
function CN(C) {
  let news = C.news || [];
  if (!news.length) return [NA("Security news", "Arka News Intelligence or Yahoo news")];
  const q = (C.o.newsq || "").toLowerCase(); if (q) news = news.filter((n) => String(n.title || "").toLowerCase().includes(q));
  return [H_(`Security news timeline · ${news.length} item(s)`, "live"), ["news", news.slice(0, 30)]];
}

// ── DLV / FH / ERN / QR ──
function DLV(C) {
  const mode = C.o.dlv || "Weekly", dl = C.dlv, ok = dl && dl.d.length;
  const out = [H_("Deliveries · NSE security-wise bhavcopy", ok ? "live" : "unavailable", "Delivered quantity = shares that changed hands for delivery (not squared off intraday). NSE only.")];
  if (!ok) { out.push(NA("Delivery data", "NSE daily bhavcopy with delivery (sec_bhavdata_full)", `${(C.dlvInfo && C.dlvInfo.error) || "No file could be read"}. The nightly "Market data" job saves these files; until its first run there is nothing to show.`)); return out; }
  const n = C.o.dlvn || 60;
  const w = sliceDl(dl, n), d = w.pct.map((x, i) => [w.d[i], x]).filter((x) => x[1] != null && isFinite(x[1]));
  const lastI = d.length ? d.at(-1)[0] : null, avg20 = d.length >= 5 ? mean(d.slice(-20).map((x) => x[1])) : null, avgAll = d.length ? mean(d.map((x) => x[1])) : null, lastV = d.length ? d.at(-1)[1] : null;
  out.push(K([["Latest delivery %", pct(lastV), lastI || "", lastV != null && avgAll && lastV > avgAll ? "g" : null], ["20-session average", pct(avg20), "delivered ÷ traded quantity"],
    [`${d.length}-session average`, pct(avgAll), "the stock's own normal"], ["Sessions loaded", `${w.d.length} / ${n}`, `${w.d.length} stored · 0 no file · 0 failed`]], 4));
  const agg = E.aggregateDelivery(w, { Daily: "D", Weekly: "W", Monthly: "M" }[mode]), show = agg.slice(-{ Daily: 30, Weekly: 16, Monthly: 12 }[mode]).reverse();
  out.push(["html", "dlvbars", show]);
  out.push(T(["DELIVERED QTY", "TRADED QTY", "DELIVERY %", "CLOSE", "CHANGE"], show.map((r) => ({ label: r.label, values: [qty(r.deliv_qty), qty(r.volume), pct(r.deliv_pct), rs(r.close), pct(r.chg_pct, 1, true)] })), [], `${C.sym}_delivery_${mode.toLowerCase()}`));
  const top = w.d.map((dd, i) => ({ d: dd, p: w.pct[i], q: w.q[i], c: w.c[i], pc: w.p[i] })).filter((x) => x.p != null && isFinite(x.p)).sort((a, b) => b.p - a.p).slice(0, 5);
  if (top.length) out.push(H_("Highest delivery days in this window"), T(["DELIVERY %", "DELIVERED QTY", "CLOSE", "DAY CHANGE"], top.map((x) => ({ label: x.d, values: [pct(x.p), qty(x.q), rs(x.c), pct(x.pc ? (x.c / x.pc - 1) * 100 : null, 1, true)] }))));
  out.push(NOTE("High delivery means more of the day's trading was taken for delivery. It does not show who bought or sold, or whether they were buying or selling. Compare a day with the stock's own average, not with other stocks."));
  return out;
}
export function sliceDl(dl, n) { const k = Math.max(0, dl.d.length - n), o = {}; for (const key of ["d", "c", "p", "v", "q", "pct"]) o[key] = dl[key].slice(k); return o; }
function FH(C) {
  const B = C.B, y = B.yearly, bs = B.balance_sheet, cf = B.cash_flow, rt = B.ratios, I = info(C), Yb = (C.Y && C.Y.bs) || {}, Yc = (C.Y && C.Y.cf) || {};
  const de = debtEquity(B), lender = row(y, "Financing Profit", "Financing Margin") != null, op = lastNum(nums(row(y, "Operating Profit"))), it = lastNum(nums(row(y, "Interest")));
  const cv = !lender && op != null && it && it > 0 ? op / it : null, ca = (Yb.ca || [])[0] ?? null, cl = (Yb.cl || [])[0] ?? null, cr = ca != null && cl ? ca / cl : null, fcf = (Yc.fcf || [])[0] ?? null;
  const td = I.totalDebt, cash = I.totalCash, eb = I.ebitda, nde = [td, cash, eb].every((v) => typeof v === "number") && eb && eb > 0 ? (td - cash) / eb : null;
  const npm = annualMap(y, "Net Profit", "Profit for the period"), cfo = annualMap(cf, "Cash from Operating", "Operating"), common = ((y || {}).periods || []).filter((p) => p in npm && p in cfo).slice(-3);
  const conv = common.length === 3 && sum(common.map((p) => npm[p])) > 0 ? (sum(common.map((p) => cfo[p])) / sum(common.map((p) => npm[p]))) * 100 : null;
  const out = [H_("Financial health · balance-sheet strength and cash quality", B.resolved ? "live" : "unavailable"),
    K([["Debt / equity", de != null ? `${pyf(de)}x` : "N/A", "Borrowings ÷ (equity + reserves) · Screener"], ["Interest coverage", cv != null ? `${fx(cv, 1, false)}x` : "N/A", lender ? "Not meaningful for lenders" : "Operating profit ÷ interest, latest year · Screener"],
      ["Current ratio", cr != null ? `${fx(cr, 2, false)}x` : "N/A", "Current assets ÷ current liabilities · Yahoo"], ["Net debt / EBITDA", nde != null ? `${fx(nde, 1, false)}x` : "N/A", "(Debt − cash) ÷ EBITDA · Yahoo"],
      ["Free cash flow", fcf != null ? `₹${fx(fcf / 1e7, 0)} Cr` : "N/A", "Latest year · Yahoo"], ["Cash conversion, 3 yrs", pct(conv, 0), "Operating cash flow ÷ net profit · Screener"]], 3)];
  const extra = [];
  for (const lab of ["Debtor Days", "Inventory Days", "Days Payable", "Cash Conversion Cycle", "Working Capital Days", "ROCE"]) {
    const r = row(rt, lab), v = ((r || {}).values || []).filter((x) => x !== "—" && x !== ""); if (r && v.length >= 2) extra.push({ label: r.label, values: [v.at(-2), v.at(-1)] });
  }
  if (extra.length) out.push(H_("Working capital · previous year → latest"), T(["PREVIOUS", "LATEST"], extra, ["Cash Conversion", "ROCE"]));
  if (bs && bs.rows && bs.rows.length) {
    const seen = new Set(), rows = [];
    for (const lab of ["Borrowings", "Reserves", "Total Assets"]) { const r = row(bs, lab); if (r && !seen.has(r.label)) { seen.add(r.label); rows.push({ label: r.label, values: r.values.slice(-6) }); } }
    out.push(H_("Balance sheet trend · ₹ Cr"), T(bs.periods.slice(-6), rows, ["Borrowings"]));
  }
  out.push(NOTE("Ratios are shown without a verdict: what is healthy differs by industry. Compare with peers (RV) and with the company's own past."));
  return out;
}
function ERN(C) {
  const q = C.B.quarterly; if (!q || !q.rows || !q.rows.length) return [NA("Earnings trend", "Screener quarterly results", C.B.reason)];
  const per = q.periods, yoy = (v) => v.map((x, i) => (i >= 4 && x != null && v[i - 4] && v[i - 4] > 0 ? (x / v[i - 4] - 1) * 100 : null));
  const sales = nums(row(q, "Sales", "Revenue")), npf = nums(row(q, "Net Profit", "Profit for the period")), eps = nums(row(q, "EPS")), opm = nums(row(q, "OPM", "Financing Margin"));
  const ys = yoy(sales), yn = yoy(npf), ye = yoy(eps), n = Math.min(8, per.length);
  const cut = (a) => [...a, ...Array(per.length).fill(null)].slice(0, per.length).slice(-n);
  const f0 = (a) => cut(a).map((v) => (v == null ? "—" : fx(v, 0))), f2 = (a) => cut(a).map((v) => (v == null ? "—" : fx(v, 2))), fp = (a) => cut(a).map((v) => (v == null ? "—" : `${fx(v, 1, false, true)}%`));
  const wins = cut(yn).filter((v) => v != null), ttm = (a) => (a.length >= 8 && a.slice(-8).every((v) => v != null) ? [sum(a.slice(-4)), sum(a.slice(-8, -4))] : [null, null]);
  const [s1, s0] = ttm(sales), [p1, p0] = ttm(npf), o = opm.filter((v) => v != null);
  return [H_("Earnings trend · last quarters vs the same quarter a year earlier", "live"),
    K([["Sales, last 4 quarters", pct(s1 != null && s0 && s0 > 0 ? (s1 / s0 - 1) * 100 : null, 1, true), "vs the 4 quarters before"],
      ["Net profit, last 4 quarters", pct(p1 != null && p0 && p0 > 0 ? (p1 / p0 - 1) * 100 : null, 1, true), "vs the 4 quarters before"],
      ["Profit up year-on-year", wins.length ? `${wins.filter((v) => v > 0).length} of ${wins.length}` : "N/A", "quarters shown"],
      ["Operating margin", o.length ? `${fx(o.at(-1), 0, false)}%` : "N/A", o.length >= 5 ? `${fx(o.at(-5), 0, false)}% a year ago` : ""]], 4),
    ["chart", { kind: "bars", x: per.slice(-n), series: [{ name: "Sales YoY %", color: "c", y: cut(ys) }, { name: "Net profit YoY %", color: "g", y: cut(yn) }], h: 280 }],
    T(per.slice(-n), [{ label: "Sales ₹ Cr", values: f0(sales) }, { label: "Sales YoY", values: fp(ys) }, { label: "Net profit ₹ Cr", values: f0(npf) }, { label: "Net profit YoY", values: fp(yn) },
      { label: "EPS ₹", values: f2(eps) }, { label: "EPS YoY", values: fp(ye) }, { label: "Operating margin %", values: f0(opm) }], ["YoY"], `${C.sym}_earnings_trend`),
    NOTE("YoY compares each quarter with the same quarter a year earlier, which removes seasonality. Result dates and the price reaction to each result are not available from this source.")];
}
function QR(C) {
  const h = hist2(C.H, "2y", "1y"); if (!h) return [NA("Quant", "Yahoo daily price history")];
  const s = { d: h.d, v: h.c }, bn = {};
  for (const name of ["NIFTY 500", "NIFTY 50"]) { const ix = idxHist(C, name); if (ix && ix.d.length > 60) bn[name] = ix; }
  if (!Object.keys(bn).length) return [NA("Benchmarks", "Yahoo history for NIFTY 500 / NIFTY 50")];
  const main = bn["NIFTY 500"] ? "NIFTY 500" : "NIFTY 50", rows = [];
  for (const [lab, k] of [["5 sessions", 5], ["20 sessions", 20], ["60 sessions", 60], ["120 sessions", 120], ["252 sessions", 252]]) {
    const vals = [];
    for (const name of ["NIFTY 500", "NIFTY 50"]) {
      if (!bn[name]) { vals.push("N/A"); continue; }
      const J = join(s, bn[name]); if (J.d.length <= k) { vals.push("—"); continue; }
      const a = (J.s.at(-1) / J.s.at(-1 - k) - 1) * 100, b = (J.b.at(-1) / J.b.at(-1 - k) - 1) * 100;
      if (!vals.length) vals.unshift(`${fx(a, 1, false, true)}%`);
      vals.push(`${fx(a - b, 1, false, true)} pp`);
    }
    while (vals.length < 3) vals.unshift("—");
    rows.push({ label: lab, values: vals.slice(0, 3) });
  }
  const out = [H_("Relative strength · stock return minus index return", "live"), T([C.sym, "vs NIFTY 500", "vs NIFTY 50"], rows, ["60 sessions"])];
  const J = join(s, bn[main]), ratio = J.s.map((x, i) => x / J.b[i]), sma = E.rolling(ratio, 50, mean);
  let trend = "N/A";
  if (sma.filter((x) => x != null).length > 21) { const up = ratio.at(-1) > sma.at(-1), rising = sma.at(-1) > sma.at(-21); trend = up && rising ? "UP" : !up && !rising ? "DOWN" : "MIXED"; }
  const w = { s: J.s.slice(-252), b: J.b.slice(-252) }, r = { s: pctChange(w.s), b: pctChange(w.b) };
  const vol20 = r.s.length >= 20 ? std(r.s.slice(-20)) * Math.sqrt(252) * 100 : null, vol60 = r.s.length >= 60 ? std(r.s.slice(-60)) * Math.sqrt(252) * 100 : null;
  const vb = std(r.b) ** 2, beta = r.s.length >= 60 && vb ? cov(r.s, r.b) / vb : null;
  let pk = -Infinity; const dd = w.s.map((x) => { pk = Math.max(pk, x); return x / pk - 1; });
  const mom = s.v.length > 253 ? (s.v.at(-22) / s.v.at(-253) - 1) * 100 : null, upd = r.s.length >= 60 ? (r.s.slice(-60).filter((x) => x > 0).length / 60) * 100 : null;
  const base = ratio[ratio.length - Math.min(252, ratio.length)], x = J.d.slice(-252);
  out.push(H_(`Relative-strength line · ${C.sym} ÷ ${main}`, null, "Rising line = the stock is beating the index. Trend uses the 50-session average, as in Money Flow."),
    ["chart", { kind: "line", x, series: [{ name: "RS line (100 = a year ago)", color: "a", y: ratio.slice(-252).map((v) => (v / base) * 100), width: 2 }, { name: "50-session average", color: "c", y: sma.slice(-252).map((v) => (v == null ? null : (v / base) * 100)) }], h: 280 }],
    K([["RS trend", trend, `vs ${main}`, trend === "UP" ? "g" : trend === "DOWN" ? "r" : null], ["Momentum 12-1", pct(mom, 1, true), "12 months ago → 1 month ago"],
      ["Volatility 20 / 60", vol20 != null && vol60 != null ? `${fx(vol20, 0, false)}% / ${fx(vol60, 0, false)}%` : "N/A", "annualised, daily returns"], ["Beta", beta != null ? fx(beta, 2, false) : "N/A", `vs ${main}, 1 year`],
      ["Drawdown now / max 1Y", `${fx(dd.at(-1) * 100, 1, false)}% / ${fx(Math.min(...dd) * 100, 1, false)}%`, "from the 1-year high", "r"], ["Up days, last 60", pct(upd, 0), "share of sessions that closed higher"]], 3),
    NOTE("Descriptive statistics from daily closes. Momentum 12-1 skips the latest month, the standard academic definition."));
  return out;
}

// ── CACS / SYN / MF / NOTE / SRC ──
function CACS(C) {
  const from = E.addDays(C.today, -180); let a = (C.anns || []).filter((x) => x.dt.slice(0, 10) >= from);
  if (!a.length) return [H_("Exchange announcements · ScanX (optional source)", "unavailable"), NA("Announcements", "BSE corporate filings for this company", "BSE did not return filings for this company, or its BSE code is not on its Screener page.")];
  const cats = ["All", ...[...new Set(a.map((x) => x.category))].sort()], cat = C.o.cat || "All", q = (C.o.filq || "").toLowerCase(), all = a.length;
  if (cat !== "All") a = a.filter((x) => x.category === cat);
  if (q) a = a.filter((x) => (x.title + " " + x.sub).toLowerCase().includes(q));
  return [H_(`Exchange filings · BSE (scrip ${C.B.bse || "—"}) · ${all} in 180 days`, "live"), ["filings", a.slice(0, 80), cats]];
}
function SYN(C) {
  const h = hist2(C.H, "5y", "2y"), close = h ? { d: h.d, v: h.c } : null, [pe, pp] = close ? E.peStats(C.B, close) : [null, null], Ev = E.evidence(C.B, close, pe, pp, C.dlv), con = Ev.contradictions;
  return [H_(`Contradictions · ${con.length} found`, Ev.facts.length ? "live" : "unavailable", "Places where two measurements point in different directions. Rules are fixed and shown; nothing here is a forecast."),
    con.length ? ["check", "Where the evidence disagrees", con.map((c) => [c.name, false, c.numbers]), ["", "⚠", ""]]
      : ["text", "No contradiction rule triggered", "Checked: valuation vs profit, price vs profit, sales vs margin, profit vs cash flow, price vs promoter holding, price vs delivery share."],
    H_("Evidence sheet · every fact with its source"), T(["VALUE", "SOURCE"], Ev.facts.map((x) => ({ label: x.label, values: [x.value, x.source] })), [], `${C.sym}_evidence`),
    ["text", "Not available", Ev.unknown.join("; ") + ".", true],
    H_("AI synthesis · Gemini reads the evidence sheet above, nothing else"), ["synth", { company: C.B.name || C.sym, symbol: C.sym, facts: Ev.facts, contradictions: con, unknown: Ev.unknown }]];
}
// MF: named holders over 1% (Screener's investors breakdown of the shareholding table, from company filings)
const MON = { Jan: 1, Feb: 2, Mar: 3, Apr: 4, May: 5, Jun: 6, Jul: 7, Aug: 8, Sep: 9, Oct: 10, Nov: 11, Dec: 12 };
const perKey = (p) => { const m = /^([A-Z][a-z]{2})\s+(\d{4})$/.exec(String(p).trim()); return m && MON[m[1]] ? +m[2] * 100 + MON[m[1]] : null; };
const holdNum = (v) => { if (v == null || v === "") return null; const x = parseFloat(String(v).replace(/[%,\s]/g, "")); return isFinite(x) ? x : null; };
export function parseHolders(d) {                          // {holder: {"Sep 2026": "1.23", ...}} -> {periods (oldest first), rows: [{name, v: []}]}
  const per = new Set(), rows = [];
  for (const [name, o] of Object.entries(d || {})) {
    if (!o || typeof o !== "object" || Array.isArray(o)) continue;
    const vals = {}; for (const [k, v] of Object.entries(o)) if (perKey(k) != null) { per.add(k.trim()); vals[k.trim()] = holdNum(v); }
    if (Object.keys(vals).length) rows.push({ name: String(name).replace(/\s+/g, " ").trim(), vals });
  }
  const periods = [...per].sort((x, y) => perKey(x) - perKey(y));
  return { periods, rows: rows.map((r) => ({ name: r.name, v: periods.map((p) => (r.vals[p] ?? null)) })) };
}
const IS_MF1 = /mutual fund|\bmf\b|trustee|\ba\/c\b/i, IS_INS = /insurance|assurance|\blic\b|life insurance/i, IS_MF = /\bfunds?\b|\bscheme\b|\betf\b/i, NOT_MF = /pension|provident|gratuity|superannuation|\bnps\b|employees/i;
export const holderKind = (n) => (IS_MF1.test(n) && !NOT_MF.test(n) ? "Mutual fund" : IS_INS.test(n) ? "Insurance" : IS_MF.test(n) && !NOT_MF.test(n) ? "Mutual fund" : "Other institution");
function MF(C) {
  const o = C.o, view = o.mfc || "Mutual funds", foreign = view === "Foreign institutions", mode = o.mfp || "Quarterly", key = (foreign ? "foreign_institutions" : "domestic_institutions") + "/" + mode.toLowerCase();
  const title = `${view} holding over 1% (${mode.toLowerCase()}) · Screener, from company filings`;
  if (!C.B.resolved) return [H_(title, "unavailable"), NA("Named holders", "the company's Screener page", C.B.reason)];
  const raw = C.hold[key];
  if (raw === undefined) return [H_(title), ["note", C.holdErr[key] ? "" : "Loading the holder list from Screener…"]].concat(C.holdErr[key] ? [NA("Named holders", "Screener's shareholding breakdown", C.holdErr[key])] : []);
  if (raw === null) return [H_(title, "unavailable"), NA("Named holders", "Screener's shareholding breakdown", C.holdErr[key] || "Screener did not return a holder list for this company.")];
  const P = parseHolders(raw), n = P.periods.length;
  let rows = P.rows.map((r) => ({ ...r, kind: foreign ? "Foreign institution" : holderKind(r.name) }));
  if (view === "Mutual funds") rows = rows.filter((r) => r.kind === "Mutual fund");
  const show = Math.min(n, 8), lastAt = (r) => { for (let i = n - 1; i >= 0; i--) if (r.v[i] > 0) return i; return -1; };
  rows = rows.filter((r) => lastAt(r) >= n - show);           // Screener sends every holder back to its first filing: keep those named inside the window shown
  const out = [H_(title, "live", "Companies must name every holder with more than 1% of the shares in their shareholding filing. Smaller positions are not named, so this list is a floor, not the full fund ownership.")];
  if (!n) return [...out, NA("Named holders", "Screener's shareholding breakdown", "Screener returned no dated holdings.")];
  if (!rows.length) return [...out, ["text", "No named holder", `No ${view === "Mutual funds" ? "mutual fund" : view.toLowerCase().replace(/s$/, "")} held more than 1% of ${C.B.name || C.sym} in any of the last ${show} ${mode === "Quarterly" ? "quarters" : "years"} shown. Funds can still own it below 1%.`]];
  const cur = (r) => r.v[n - 1], prev = (r) => (n >= 2 ? r.v[n - 2] : null);
  const status = (r) => { const a = prev(r), b = cur(r); if (b == null || b <= 0) return a > 0 ? "Below 1% or sold" : `Not named since ${P.periods[lastAt(r)]}`; if (a == null || a <= 0) return n >= 2 ? "New" : "Holds"; const d = b - a; return d >= 0.05 ? "Added" : d <= -0.05 ? "Cut" : "Held"; };
  const sumAt = (i) => rows.reduce((p, r) => p + (r.v[i] > 0 ? r.v[i] : 0), 0), cnt = (i) => rows.filter((r) => r.v[i] > 0).length;
  const tNow = sumAt(n - 1), tPrev = n >= 2 ? sumAt(n - 2) : null, st = rows.map(status);
  const nNew = st.filter((x) => x === "New").length, nOut = st.filter((x) => x === "Below 1% or sold").length, nAdd = st.filter((x) => x === "Added").length, nCut = st.filter((x) => x === "Cut").length;
  const catRow = ((C.o.mfp === "Yearly" ? C.B.shareholding_yearly : C.B.shareholding) || { rows: [] }).rows.find((r) => r.label.toLowerCase().startsWith(foreign ? "fiis" : "diis"));
  const catRec = (C.o.mfp === "Yearly" ? C.B.shareholding_yearly : C.B.shareholding) || null, catAt = (p) => { if (!catRow || !catRec) return null; const i = catRec.periods.indexOf(p); return i < 0 ? null : nums(catRow)[i]; };
  const catNow = catAt(P.periods[n - 1]), catPrev = n >= 2 ? catAt(P.periods[n - 2]) : null, lp = P.periods[n - 1], pp = n >= 2 ? P.periods[n - 2] : null;
  out.push(K([[`Named ${view === "Mutual funds" ? "funds" : "holders"} · ${lp}`, String(cnt(n - 1)), pp ? `${cnt(n - 2)} in ${pp}` : ""],
    ["Their combined stake", `${fx(tNow, 2, false)}%`, tPrev != null ? `${fx(tNow - tPrev, 2, false, true)} pp vs ${pp}` : "", tPrev != null ? (tNow > tPrev ? "g" : tNow < tPrev ? "r" : null) : null],
    [foreign ? "All FIIs (filing total)" : "All DIIs (filing total)", catNow != null ? `${fx(catNow, 2, false)}%` : "N/A", catNow != null && catPrev != null ? `${fx(catNow - catPrev, 2, false, true)} pp vs ${pp}` : "Screener shareholding table", catNow != null && catPrev != null ? (catNow > catPrev ? "g" : catNow < catPrev ? "r" : null) : null],
    [`Changes since ${pp || "—"}`, pp ? `${nNew} new · ${nAdd} added` : "—", pp ? `${nCut} cut · ${nOut} below 1% or sold` : "", pp ? (nNew + nAdd > nCut + nOut ? "g" : nNew + nAdd < nCut + nOut ? "r" : null) : null]], 4));
  if (n >= 2) { const ser = [{ name: `Named ${view === "Mutual funds" ? "funds" : "holders"} over 1%`, color: "a", y: P.periods.map((_, i) => sumAt(i)), width: 2, markers: true }];
    if (catRow) ser.push({ name: foreign ? "All FIIs" : "All DIIs", color: "t2", y: P.periods.map(catAt), markers: true });
    out.push(["chart", { kind: "line", x: P.periods, series: ser, h: 280 }]); }
  const heads = [...P.periods.slice(-show), "Change pp", "Status", ...(view === "All domestic institutions" ? ["Type"] : [])];
  const order = rows.map((r, i) => [r, st[i]]).sort((x, y) => (cur(y[0]) ?? -1) - (cur(x[0]) ?? -1) || lastAt(y[0]) - lastAt(x[0]) || (y[0].v[lastAt(y[0])] ?? 0) - (x[0].v[lastAt(x[0])] ?? 0));
  out.push(T(heads, order.map(([r, s]) => { const a = prev(r), b = cur(r), d = a > 0 || b > 0 ? (b || 0) - (a || 0) : null;
    return { label: r.name, values: [...r.v.slice(-show).map((v) => (v == null ? "—" : fx(v, 2, false))), n >= 2 && d != null ? fx(d, 2, false, true) : "—", s, ...(view === "All domestic institutions" ? [r.kind] : [])] }; }), [], `${C.sym}_holders_${view.replace(/\s+/g, "_").toLowerCase()}_${mode.toLowerCase()}`));
  if (view === "Mutual funds") out.push(["text", "How funds are picked out", "A holder counts as a mutual fund when its filed name says mutual fund, fund, scheme or ETF and is not an insurer or a pension or provident fund. Switch to All domestic institutions to see every named holder with its type."]);
  out.push(["text", "Limits", `Quarterly filings, so the newest figure is up to a quarter old. "Below 1% or sold" means the holder was named last quarter but not now: it may have sold everything or just dropped under 1%. "Not named since" is the same, further back. Only holders named in the ${show} ${mode === "Quarterly" ? "quarters" : "years"} shown are listed. Monthly fund portfolios are not used here.`, true]);
  return out;
}
function NOTEFN(C) { return [["thesis", C.note || {}]]; }
function SRC(C) {
  const st = C.status || {};
  return [H_("Data provenance"), T(["SOURCE", "STATUS"], [
    { label: "Fundamentals (financials, ratios, holding)", values: ["Screener.in", C.B.resolved ? "live" : "unavailable"] }, { label: "Prices / charts / technicals / seasonality", values: ["Yahoo Finance", C.H ? "live" : "unavailable"] },
    { label: `Benchmark (${BENCH[1]})`, values: [`Yahoo ${BENCH[0]}`, idxHist(C, "NIFTY 50") ? "live" : "on demand"] }, { label: "Health check, valuation, P/E band", values: ["Computed from the above", "on demand"] },
    { label: "Analyst / dividends / fallbacks", values: ["Yahoo Finance", C.Y ? (C.Y.ok ? "live" : "unavailable") : "on demand"] }, { label: "News", values: ["News desk / Google News", "on demand"] },
    { label: "Delivery (DLV)", values: ["NSE daily bhavcopy, saved nightly", C.dlv && C.dlv.d.length ? "live" : "on demand"] }, { label: "Announcements (CACS)", values: ["BSE API", C.anns ? (C.anns.length ? "live" : "no filings returned") : "on demand"] },
    { label: "MF holdings (holders over 1%)", values: ["Screener, from company filings", Object.values(C.hold || {}).some(Boolean) ? "live" : "on demand"] }, { label: "Storage for notes", values: ["Supabase", st.notes || "on demand"] }, { label: "AI synthesis (SYN)", values: ["Gemini", st.ai || "set up with Arka AI"] }]),
    ["text", "Integrity rule", "Values are shown only when a source returns them or when they are computed directly from returned values (formula shown on screen). Missing data says DATA UNAVAILABLE; nothing is estimated or invented."]];
}

// ── OV: dashboard ──
function OV(C) {
  const B = C.B, s = B.summary || {}, close = closeOf(C, "2y", "1y"), b500 = bench(C, "NIFTY 500") || bench(C, "NIFTY 50"), c5 = closeOf(C);
  const [pe, pp] = close ? E.peStats(B, c5) : [null, null], Ev = E.evidence(B, close, pe, pp, C.dlv), F = Object.fromEntries(Ev.facts.map((x) => [x.label, x.value]));
  const out = [["scorestrip", E.scorecard(B, c5, b500, C.dlv)]], side = [], perf = [];
  for (const [lab, n] of [["1 week", 5], ["1 month", 21], ["3 months", 63], ["6 months", 126], ["1 year", 252]]) {
    const r = retN(close && close.v, n); if (r == null) continue;
    let rb = null; if (b500) { const J = join(close, b500); rb = J.d.length > n ? retN(J.b, n) : null; }
    perf.push([lab, `${fx(r, 1, false, true)}%` + (rb != null ? `  ·  ${fx(r - rb, 1, false, true)} pp` : "")]);
  }
  side.push(["mini", "Performance · vs NIFTY 500", perf.length ? perf : [["Price history", "N/A"]], "QR"]);
  const bv = f(s.book_value), price = close ? close.v.at(-1) : null;
  side.push(["mini", "Valuation", [["P/E (TTM)", pe ? `${fx(pe, 1, false)}x` : "N/A"], ["vs own history", pp != null ? `${ordn(pp)} percentile` : "N/A"], ["P/B", price && bv ? `${fx(price / bv, 2, false)}x` : "N/A"],
    ["Earnings yield", pe ? `${fx(100 / pe, 2, false)}%` : "N/A"], ["Dividend yield", s.dividend_yield ? `${s.dividend_yield}%` : "N/A"]], "VAL"]);
  const hh = hist2(C.H, "2y", "1y");
  if (hh && hh.d.length > 60) {
    const c = hh.c, v = hh.v, av = mean(v.slice(-20)), trade = [["Volume vs 20-session average", av ? `${fx(v.at(-1) / av, 2, false)}x` : "N/A"],
      ["Volatility, 20 sessions", `${fx(std(pctChange(c).slice(-20)) * Math.sqrt(252) * 100, 0, false)}% a year`], ["RSI 14", fx(E.rsiWilder(c, 14).at(-1), 0, false)]];
    for (const n of [50, 200]) if (c.length >= n) trade.push([`vs ${n}-day average`, `${fx((c.at(-1) / mean(c.slice(-n)) - 1) * 100, 1, false, true)}%`]);
    side.push(["mini", "Trading", trade, "TECH"]);
  }
  const pb = pbMini(C); if (pb) side.push(pb);
  if (C.fno) side.push(["mini", "F&O positioning", C.fno, "FNO"]);
  out.push(["dash", side]);
  const res = E.piotroski(B); let sc = "N/A"; if (res) { const av = res[0].filter((x) => x[1] != null); sc = `${av.filter((x) => x[1]).length} / ${av.length}`; }
  const de = debtEquity(B);
  out.push(["row3", [["mini", "Quality", [["Piotroski-style score", sc], ["ROE", s.roe ? `${s.roe}%` : "N/A"], ["ROCE", s.roce ? `${s.roce}%` : "N/A"], ["Debt / equity", de != null ? `${pyf(de)}x` : "N/A"],
      ["Cash flow ÷ profit, 3 yrs", F["Operating cash flow as % of net profit, 3 years"] || "N/A"]], "HC"],
    ["mini", "Earnings", [["Sales, last 4 quarters", F["Sales, last 4 quarters vs previous 4"] || "N/A"], ["Net profit, last 4 quarters", F["Net profit, last 4 quarters vs previous 4"] || "N/A"],
      ["Operating margin", F["Operating margin, latest quarter vs a year ago"] || "N/A"], ["Price, 1 year", F["Price change, 1 year"] || "N/A"]], "ERN"],
    ["mini", "Ownership", [["Promoters", F["Promoter holding"] || "N/A"], ["FIIs", F["FII holding"] || "N/A"], ["DIIs", F["DII holding"] || "N/A"],
      ["Delivery share", F["Delivery share of traded quantity, last 10 sessions vs before"] || "open DLV to load"]], "HDS"]]]);
  const con = Ev.contradictions;
  out.push(["row2", [con.length ? ["check", `Contradictions · ${con.length}`, con.map((x) => [x.name, false, x.numbers]), ["", "⚠", ""]]
    : ["text", `Contradictions · 0`, "No contradiction rule triggered. Checked: valuation vs profit, price vs profit, sales vs margin, profit vs cash, price vs promoters, price vs delivery."],
    ["filingsmini", (C.anns || []).filter((a) => a.dt >= E.addDays(C.today, -90)).slice(0, 6)]]]);
  return out;
}
function pbMini(C) {
  const h = hist2(C.H, "1y"), S = h ? E.behaviourStats(h, 60, "day", C.splits) : null; if (!S || !S.used) return null;
  const vr = S.vol_ratio;
  return ["mini", "Price behaviour · 60 sessions", [["Up days", `${S.up} of ${S.used} (${fx(S.up_pct, 0, false)}%)`], ["Average up / down day", `${pct(S.avg_up, 2, true)} / ${pct(S.avg_dn, 2, true)}`],
    ["Up / down volume", vr ? `${fx(vr, 2, false)}×` : "N/A", vr && vr >= 1.25 ? "g" : vr && vr <= 0.8 ? "r" : null], ["Longest up / down streak", `${S.streak_up} / ${S.streak_dn}`]], "PB"];
}

// ── GP / HP / COMP / BETA / HVG / TRA / DCF / DUP / GRW / EVTS ──
function GP() { return [["prochart", 640]]; }
function HP(C) {
  const h = hist2(C.H, "5y", "2y"); if (!h) return [NA("Historical prices", "Yahoo daily price history")];
  const rng = C.o.hp || "3M", n = { "1M": 21, "3M": 63, "6M": 126, "1Y": 252, "5Y": h.d.length }[rng], d = E.slice(h, Math.max(0, h.d.length - n));
  const chg = d.c.map((x, i) => (i ? (x / d.c[i - 1] - 1) * 100 : null));
  const iH = d.h.indexOf(Math.max(...d.h)), iL = d.l.indexOf(Math.min(...d.l));
  const rows = d.d.map((dd, i) => ({ label: dd, values: [fx(d.o[i], 2), fx(d.h[i], 2), fx(d.l[i], 2), fx(d.c[i], 2), pct(chg[i], 2, true), qty(d.v[i])] })).reverse().slice(0, 90);
  return [H_(`Historical prices · ${d.d.length} sessions · Yahoo, unadjusted`, "live"),
    K([["High", rs(d.h[iH]), d.d[iH]], ["Low", rs(d.l[iL]), d.d[iL]], ["Average close", rs(mean(d.c)), `${rng} window`],
      ["Change", pct((d.c.at(-1) / d.c[0] - 1) * 100, 1, true), "first → last close", d.c.at(-1) >= d.c[0] ? "g" : "r"], ["Average volume", qty(mean(d.v)), "shares per session"]], 5),
    T(["OPEN", "HIGH", "LOW", "CLOSE", "CHANGE", "VOLUME"], rows), ["csvprices", d, `${C.sym}_prices_${rng}`]];
}
function COMP(C) {
  const n = { "3M": 63, "6M": 126, "1Y": 252, "2Y": 504 }[C.o.comprng || "1Y"], names = [...new Set([C.sym, ...(C.o.comp || []).slice(0, 6)])], series = {}, missing = [];
  for (const nm of names) { const s = nm === C.sym ? closeOf(C, "2y", "1y") : IDX[nm] ? bench(C, nm) : C.cmp && C.cmp[nm]; if (!s || s.d.length < 30) missing.push(nm); else series[nm] = s; }
  if (!series[C.sym] || Object.keys(series).length < 2) return [NA("Comparison", "Price history for the stock and at least one other name", `No data for: ${missing.join(", ") || "other names"}`)];
  const ks = Object.keys(series), maps = ks.map((k) => new Map(series[k].d.map((d, i) => [d, series[k].v[i]])));
  let days = series[ks[0]].d.filter((d) => maps.every((m) => m.get(d) != null && isFinite(m.get(d))));
  days = days.slice(-(n + 1));
  const cols = ks.map((k, j) => days.map((d) => maps[j].get(d))), pal = ["a", "c", "g", "p", "y", "r", "t2"];
  const out = [H_(`Rebased performance · ${C.o.comprng || "1Y"} = 100 · ${days.length - 1} common sessions`, "live", missing.length ? `No data for: ${missing.join(", ")}` : null),
    ["chart", { kind: "line", x: days, series: ks.map((k, j) => ({ name: k, color: pal[j % pal.length], y: cols[j].map((v) => (v / cols[j][0]) * 100), width: k === C.sym ? 2.4 : 1.4 })), h: 340 }]];
  const r = cols.map(pctChange), si = ks.indexOf(C.sym), tot = (j) => (cols[j].at(-1) / cols[j][0] - 1) * 100;
  out.push(T(["RETURN", `VS ${C.sym}`, "VOLATILITY", "MAX DRAWDOWN", `CORRELATION WITH ${C.sym}`], ks.map((k, j) => { let pk = -Infinity;
    const mdd = Math.min(...cols[j].map((x) => { pk = Math.max(pk, x); return x / pk - 1; })) * 100;
    return { label: k, values: [`${fx(tot(j), 1, false, true)}%`, k !== C.sym ? `${fx(tot(j) - tot(si), 1, false, true)} pp` : "—", `${fx(std(r[j]) * Math.sqrt(252) * 100, 0, false)}%`, `${fx(mdd, 1, false)}%`, k !== C.sym ? fx(corr(r[j], r[si]), 2, false) : "1.00"] }; }), [C.sym]),
    NOTE("Index names accepted: " + Object.keys(IDX).join(", ") + ". Prices are unadjusted closes; dividends are not included (see TRA)."));
  return out;
}
function BETA(C) {
  const bname = C.o.betaix || "NIFTY 500", win = C.o.betawin || "1Y", s = closeOf(C, "2y", "1y"), b = bench(C, bname);
  if (!s || !b) return [NA("Beta", `Price history for ${C.sym} and ${bname}`)];
  const J0 = join(s, b), k = { "1Y": 253, "2Y": 505 }[win], J = { d: J0.d.slice(-k), s: J0.s.slice(-k), b: J0.b.slice(-k) };
  const y = pctChange(J.s).map((v) => v * 100), x = pctChange(J.b).map((v) => v * 100), dd = J.d.slice(1);
  if (y.length < 60) return [NA("Beta", "At least 60 common sessions")];
  const mx = mean(x), my = mean(y), beta = sum(x.map((v, i) => (v - mx) * (y[i] - my))) / sum(x.map((v) => (v - mx) ** 2)), alpha = my - beta * mx, cr = corr(y, x);
  const resid = y.map((v, i) => v - (alpha + beta * x[i])), se = std(resid, 2) / (std(x, 1) * Math.sqrt(x.length));
  const roll = []; for (let i = 59; i < x.length; i++) { const xs = x.slice(i - 59, i + 1), ys = y.slice(i - 59, i + 1); roll.push([dd[i], cov(ys, xs) / std(xs) ** 2]); }
  return [H_(`Regression of daily returns · ${C.sym} on ${bname} · ${y.length} sessions`, "live"),
    K([["Beta", fx(beta, 2, false), `± ${fx(1.96 * se, 2, false)} (95% range)`], ["Alpha, annualised", pct(alpha * 252, 1, true), "return not explained by the index"], ["R²", fx(cr * cr, 2, false), "share of moves explained by the index"],
      ["Correlation", fx(cr, 2, false), ""], ["Stock-specific volatility", `${fx(std(resid, 0) * Math.sqrt(252), 0, false)}%`, "annualised, after removing the index"]], 5),
    ["cols", [[["chart", { kind: "scatter", x, y, fit: [alpha, beta], name: "Daily returns %", h: 300 }]], [["chart", { kind: "line", x: roll.map((r) => r[0]), series: [{ name: "Rolling 60-session beta", color: "a", y: roll.map((r) => r[1]) }], h: 300 }]]]],
    NOTE("Beta above 1 means the stock has moved more than the index. It describes the past window only.")];
}
function HVG(C) {
  const h = hist2(C.H, "5y", "2y"); if (!h || h.d.length < 130) return [NA("Volatility", "At least 130 sessions of price history")];
  const c = h.c, r = c.map((x, i) => (i ? x / c[i - 1] - 1 : null)), v = {};
  for (const n of [20, 60, 120]) v[n] = r.map((_, i) => (i >= n ? std(r.slice(i - n + 1, i + 1)) * Math.sqrt(252) * 100 : null));
  const cur = v[20].at(-1), h2 = v[20].filter((x) => x != null).slice(-504), pctl = (h2.filter((x) => x < cur).length / h2.length) * 100, atr = atrOf(h);
  const big = r.slice(-252).map((x) => Math.abs(x)), bi = big.indexOf(Math.max(...big)), bd = h.d.slice(-252)[bi];
  return [H_("Realised volatility · annualised, from daily closes", "live"),
    K([["20-session", `${fx(cur, 0, false)}%`, `${ordn(pctl)} percentile of the last 2 years`], ["60-session", `${fx(v[60].at(-1), 0, false)}%`, ""], ["120-session", `${fx(v[120].at(-1), 0, false)}%`, ""],
      ["ATR 14", rs(atr), `${fx((atr / c.at(-1)) * 100, 2, false)}% of price`], ["Days moving 3%+", String(big.filter((x) => x >= 0.03).length), "in the last 252 sessions"],
      ["Largest 1-day move", pct(r.slice(-252)[bi] * 100, 1, true), bd]], 3),
    ["chart", { kind: "line", x: h.d.filter((_, i) => v[20][i] != null).slice(-504), series: [[20, "a"], [60, "c"], [120, "p"]].map(([n, col]) => ({ name: `${n}-session`, color: col, y: v[n].filter((x) => x != null).slice(-504), width: n === 20 ? 2 : 1.3 })), h: 320 }],
    NOTE("Volatility is how much the price has moved, not which way. A low reading after a long calm period often precedes a bigger move, in either direction.")];
}
function TRA(C) {
  const h = hist2(C.H, "5y", "2y"); if (!h) return [NA("Total return", "Yahoo daily price history")];
  const c = h.c, dm = new Map(); for (const [d, a] of C.divs || []) dm.set(d, (dm.get(d) || 0) + a);
  const dv = h.d.map((d) => dm.get(d) || 0), tr = [1]; for (let i = 1; i < c.length; i++) tr.push(tr[i - 1] * ((c[i] + dv[i]) / c[i - 1]));
  const rows = [];
  for (let [lab, n] of [["1 year", 252], ["3 years", 756], ["5 years", 1240]]) {
    if (c.length <= n) n = lab === "5 years" && c.length > 800 ? c.length - 1 : null;
    if (!n) continue;
    const pr = (c.at(-1) / c.at(-1 - n) - 1) * 100, tt = (tr.at(-1) / tr.at(-1 - n) - 1) * 100;
    rows.push({ label: lab, values: [`${fx(pr, 1, false, true)}%`, `${fx(tt, 1, false, true)}%`, `${fx(tt - pr, 1, false, true)} pp`, `₹${fx(sum(dv.slice(-n)), 2)}`, `${fx(((1 + tt / 100) ** (1 / (n / 252)) - 1) * 100, 1, false, true)}%`] });
  }
  return [H_("Total return · price change plus dividends reinvested on the ex-date", rows.length ? "live" : "unavailable",
    (C.divs || []).length ? "Dividends from Yahoo. Splits and bonuses are already in Yahoo's prices." : "Yahoo returned no dividend history: total return equals price return here."),
    rows.length ? T(["PRICE RETURN", "TOTAL RETURN", "FROM DIVIDENDS", "DIVIDENDS PAID / SHARE", "TOTAL RETURN PER YEAR"], rows, ["1 year"]) : NA("Total return", "At least 1 year of price history")];
}
function DCF(C) {
  const B = C.B, s = B.summary || {}, mcap = f(s.market_cap), npq = nums(row(B.quarterly, "Net Profit", "Profit for the period"));
  const ttm = npq.length >= 4 && npq.slice(-4).every((v) => v != null) ? sum(npq.slice(-4)) : null;
  if (!mcap || !ttm || ttm <= 0) return [NA("Reverse DCF", "Market cap and a positive trailing-4-quarter net profit (Screener)", "A discounted cash flow needs positive earnings to start from.")];
  const base = C.o.dcfcf ?? +fx(ttm, 1, false), r = (C.o.dcfr ?? 12) / 100, tg = (C.o.dcftg ?? 5) / 100, out = [["dcfin", { base, r: r * 100, tg: tg * 100 }]];
  if (base <= 0 || r <= tg + 0.005) return [...out, NA("Reverse DCF", "A positive cash flow and a discount rate above long-run growth")];
  let lo = -0.5, hi = 1.0, implied = null;
  if (E.dcfValue(base, hi, r, tg) >= mcap) { for (let i = 0; i < 60; i++) { const mid = (lo + hi) / 2; if (E.dcfValue(base, mid, r, tg) < mcap) lo = mid; else hi = mid; } implied = (lo + hi) / 2; }
  const m = annualMap(B.yearly, "Net Profit", "Profit for the period"), ks = ((B.yearly || {}).periods || []).filter((p) => p in m);
  const past = ks.length >= 4 && m[ks.at(-4)] > 0 && m[ks.at(-1)] > 0 ? ((m[ks.at(-1)] / m[ks.at(-4)]) ** (1 / 3) - 1) * 100 : null, gap = implied != null && past != null ? implied * 100 - past : null;
  out.push(H_("Reverse DCF · what growth does today's price already assume?", "live", "The market value is taken as given and solved for the growth rate that justifies it. Growth holds for 5 years, then fades to the long-run rate by year 10."),
    K([["Market value", `₹${fx(mcap, 0)} Cr`, "Screener"], ["Starting cash flow", `₹${fx(base, 0)} Cr`, `market value is ${fx(mcap / base, 1, false)}x this`],
      ["Growth the price assumes", implied != null ? pct(implied * 100, 1) : "> 100%", "per year, for 5 years", gap != null && gap > 5 ? "r" : gap != null && gap < -5 ? "g" : null],
      ["Actual profit growth, 3 yrs", pct(past, 1, true), "per year · Screener"], ["Gap", gap != null ? pct(gap, 1, true) : "N/A", "assumed minus achieved"]], 5));
  const grid = [0.05, 0.1, 0.15, 0.2, 0.25, 0.3];
  out.push(H_("Value vs today's market value, for other growth assumptions"), T(grid.map((x) => `GROWTH ${fx(x * 100, 0, false)}%`),
    [r - 0.02, r, r + 0.02].filter((rr) => rr > tg + 0.005).map((rr) => ({ label: `Discount rate ${fx(rr * 100, 0, false)}%`, values: grid.map((gg) => `${fx((E.dcfValue(base, gg, rr, tg) / mcap - 1) * 100, 0, false, true)}%`) })),
    [`Discount rate ${fx(r * 100, 0, false)}%`]),
    NOTE("A calculator, not a target price. Net profit stands in for cash flow; use FH to see how much profit turns into cash, and lower the starting figure if it does not. Small changes in the discount rate move the answer a lot."));
  return out;
}
function DUP(C) {
  const y = C.B.yearly, bs = C.B.balance_sheet, npm = annualMap(y, "Net Profit", "Profit for the period"), sal = annualMap(y, "Sales", "Revenue");
  const ta = annualMap(bs, "Total Assets", "Total Liabilities"), rsv = annualMap(bs, "Reserves"), eq = annualMap(bs, "Equity Capital", "Share Capital");
  const yrs = ((bs || {}).periods || []).filter((p) => p in npm && p in sal && p in ta && p in rsv && p in eq && sal[p] && ta[p] && rsv[p] + eq[p] > 0).slice(-6);
  if (yrs.length < 2) return [NA("DuPont", "At least two years with sales, profit, assets and equity (Screener)")];
  const m = yrs.map((p) => (npm[p] / sal[p]) * 100), t = yrs.map((p) => sal[p] / ta[p]), l = yrs.map((p) => ta[p] / (rsv[p] + eq[p])), roe = m.map((a, i) => (a / 100) * t[i] * l[i] * 100);
  const cand = [["margin", m[0] ? m.at(-1) / m[0] : 1], ["asset turnover", t.at(-1) / t[0]], ["leverage", l.at(-1) / l[0]]];
  const drv = cand.reduce((b, z) => (Math.abs(z[1] - 1) > Math.abs(b[1] - 1) ? z : b));
  return [H_("DuPont · what return on equity is made of", "live", "ROE = net margin × asset turnover × leverage. Year-end balances."),
    K([["Return on equity", `${fx(roe.at(-1), 1, false)}%`, `${fx(roe[0], 1, false)}% in ${yrs[0]}`, roe.at(-1) >= roe[0] ? "g" : "r"], ["Net margin", `${fx(m.at(-1), 1, false)}%`, `${fx(m[0], 1, false)}% in ${yrs[0]}`],
      ["Asset turnover", `${fx(t.at(-1), 2, false)}x`, `${fx(t[0], 2, false)}x in ${yrs[0]}`], ["Leverage", `${fx(l.at(-1), 2, false)}x`, `${fx(l[0], 2, false)}x in ${yrs[0]}`],
      ["Biggest change", drv[0][0].toUpperCase() + drv[0].slice(1), `${fx((drv[1] - 1) * 100, 0, false, true)}% since ${yrs[0]}`]], 5),
    T(yrs, [{ label: "Net margin %", values: m.map((v) => fx(v, 1, false)) }, { label: "Asset turnover x", values: t.map((v) => fx(v, 2, false)) }, { label: "Leverage x", values: l.map((v) => fx(v, 2, false)) },
      { label: "Return on equity %", values: roe.map((v) => fx(v, 1, false)) }], ["Return on equity"], `${C.sym}_dupont`),
    NOTE("ROE that rises because leverage rose is lower quality than ROE that rises because margin or turnover rose.")];
}
function GRW(C) {
  const y = C.B.yearly; if (!y || !y.rows || !y.rows.length) return [NA("Growth", "Screener yearly results", C.B.reason)];
  const per = y.periods.filter((p) => !String(p).toUpperCase().includes("TTM")), rows = [];
  for (const lab of ["Sales", "Revenue", "Operating Profit", "Financing Profit", "Net Profit", "EPS"]) {
    const r = row(y, lab); if (!r || rows.some((x) => x.label === r.label)) continue;
    const mp = {}; y.periods.forEach((p, i) => { if (i < r.values.length && per.includes(p)) mp[p] = f(r.values[i]); });
    rows.push({ label: r.label, values: [1, 3, 5, 10].map((n) => { const [a, b] = per.length > n ? [mp[per.at(-1)], mp[per[per.length - 1 - n]]] : [null, null];
      return a && b && a > 0 && b > 0 ? `${fx(((a / b) ** (1 / n) - 1) * 100, 1, false, true)}%` : "—"; }) });
  }
  const out = [H_("Compound growth per year · to the latest full year", "live"), T(["1 YEAR", "3 YEARS", "5 YEARS", "10 YEARS"], rows, ["Net Profit"])];
  const sal = row(y, "Sales", "Revenue");
  if (sal) {
    const sv = sal.values.map(f), cs = [];
    for (const r of y.rows) {
      if (r.label === sal.label || r.label.includes("%") || /^(eps|dividend)/.test(r.label.toLowerCase())) continue;
      cs.push({ label: r.label, values: r.values.map((v, i) => (i < sv.length && f(v) != null && sv[i] ? fx((f(v) / sv[i]) * 100, 1, false) : "—")).slice(0, sv.length).slice(-7) });
    }
    out.push(H_("Common size · each line as % of sales"), T(y.periods.slice(-7), cs, ["Net Profit", "Operating Profit"], `${C.sym}_common_size`));
  }
  const gb = C.B.growth || {};
  if (Object.keys(gb).length) { const heads = [...new Set(Object.values(gb).flatMap((r) => Object.keys(r)))];
    out.push(H_("Screener's compounded growth boxes"), T(heads, Object.entries(gb).map(([t, r]) => ({ label: t, values: heads.map((k) => r[k] ?? "—") })))); }
  return out;
}
function EVTS(C) {
  const ev = [], ed = C.Y && C.Y.earnings;
  if (ed) ev.push([ed, "NEXT RESULTS", "Expected earnings date (Yahoo calendar)", ""]);
  for (const [d, a] of [...(C.divs || [])].reverse().slice(0, 12)) ev.push([d, "DIVIDEND", `₹${fx(a, 2)} per share, ex-date`, ""]);
  for (const [d, r] of [...(C.splits || [])].reverse().slice(0, 12)) ev.push([d, "SPLIT / BONUS", `${g(r)} : 1`, ""]);
  const want = /result|board meeting|dividend|bonus|split|buy ?back|agm|egm|record date|acquisition|order|rating|allot/i;
  for (const a of C.anns || []) if (want.test(a.title + " " + a.category + " " + a.sub)) ev.push([a.dt.slice(0, 10), (a.category || "FILING").toUpperCase().slice(0, 22), a.title.slice(0, 140), a.url]);
  if (!ev.length) return [NA("Events", "Yahoo corporate actions or BSE filings")];
  ev.sort((a, b) => (a[0] < b[0] ? 1 : a[0] > b[0] ? -1 : 0));
  const up = ev.filter((e) => e[0] >= C.today), kinds = ["All", ...[...new Set(ev.map((e) => e[1]))].sort()], pick = C.o.evk || "All";
  return [H_(`Events timeline · ${ev.length} items · ${up.length} upcoming`, "live", "Results, board meetings, dividends, splits and other material filings, newest first."),
    ["events", ev.filter((e) => pick === "All" || e[1] === pick).slice(0, 80), kinds, C.today]];
}

// ── SWG / PB / SC / FNO ──
function SWG(C) {
  const S = E.swingStats(hist2(C.H, "2y", "1y"), bench(C, "NIFTY 500")); if (!S) return [NA("Swing setup", "At least 210 sessions of Yahoo daily history")];
  const av = S.checks.filter((x) => x[1] != null), ok = av.filter((x) => x[1]).length;
  const [stage, col] = ok === av.length ? ["In an uptrend", "g"] : ok >= av.length - 2 ? ["Mostly in gear", "g"] : ok >= Math.floor(av.length / 2) ? ["Mixed", "a"] : ["In a downtrend", "r"];
  const px = S.px, atr = S.atr, stop = Math.max(px - 2 * atr, 0.01), cap = C.o.cap ?? 100000, risk = C.o.risk ?? 1, per = px - stop;
  const q = per > 0 && px > 0 ? Math.trunc(Math.min((cap * risk) / 100 / per, (cap * 0.25) / px)) : 0;
  return [H_("Swing setup · trend, levels and risk", "live", "The trend checklist is the well-known trend template used by momentum traders. It describes where the stock is now; it is not a signal and has not been backtested here."),
    K([["Trend", stage, `${ok} of ${av.length} checks pass`, col], ["Stretch above the 50-day", pct(S.ext50, 1, true), "above +15% is extended, wait for a pause", S.ext50 > 15 ? "r" : null],
      ["Breakout level", rs(S.hi20, 2), "highest high of the previous 20 sessions"], ["Last 10 sessions' range", pct(S.tight, 1), "tight ranges come before clean breakouts"],
      ["ATR 14", rs(atr, 2), `${fx(S.atr_pct, 2, false)}% of price · a normal day's range`], ["Volume, 10 vs 50 sessions", S.vol_dry ? `${fx(S.vol_dry, 2, false)}×` : "N/A", "below 0.8× = volume drying up in the base"],
      ["Stop at 2 × ATR", rs(stop, 2), `${fx((stop / px - 1) * 100, 1, false, true)}% from the price`], ["Nearest support", rs(Math.max(S.lo10, 0), 2), `10-session low · 20-session low ${rs(S.lo20, 0)}`]], 4),
    ["cols", [[["check", "Trend checklist", S.checks, ["Pass", "Fail", "N/A"]]],
      [["mini", "If it goes wrong · last year", [["A bad day (1 in 20)", pct(S.var95, 2, true), "r"], ["Worst 5 sessions", pct(S.worst5, 1, true), "r"], ["Deepest fall from a peak", pct(S.dd, 1, true), "r"],
        ["6 months vs NIFTY 500", S.rs6 != null ? `${fx(S.rs6, 1, false, true)} pp` : "N/A"]], ""], ["sizer", { cap, risk }],
       ["mini", "Position size for that stop", [["Shares", fx(q, 0)], ["Money in the trade", rs(q * px, 0)], ["Loss if the stop is hit", rs(q * per, 0), "r"], ["Reward at 2 × the risk", rs(px + 2 * per, 2), "g"]], ""]]]],
    NOTE("Size = (capital × risk %) ÷ (price − stop), capped at a quarter of capital in one stock. Levels use Yahoo daily prices; check the live price before acting.")];
}
function PB(C) {
  const h = hist2(C.H, "2y", "1y"); if (!h || h.d.length < 25) return [NA("Price behaviour", "Yahoo daily price history")];
  const pick = C.o.pbwin || "20", n = pick === "Custom" ? Math.max(5, Math.min(1000, h.d.length - 1, C.o.pbn ?? Math.min(45, h.d.length - 1))) : Math.min(+pick, h.d.length - 1);
  const mode = C.o.pbmode === "Candle colour" ? "candle" : "day", S = E.behaviourStats(h, n, mode, C.splits, bench(C, "NIFTY 50"), C.dlv);
  if (!S || !S.used) return [NA("Price behaviour", `At least a few sessions in the last ${n}`)];
  const uw = mode === "day" ? "up days" : "green candles", dw = mode === "day" ? "down days" : "red candles", vr = S.vol_ratio, cap = (x) => x[0].toUpperCase() + x.slice(1);
  const out = [["pbin", { pick, n, mode: C.o.pbmode || "Close vs previous close" }],
    H_(`Price behaviour · last ${S.n} sessions · ${dfmt(S.first, "d b")} to ${dfmt(S.last, "d b Y")}`, "live",
      (mode === "day" ? "Close vs previous close: the day's real gain or loss." : "Candle colour: close vs the same day's open. A gap-up day that closes below its open counts as red.")
      + (S.excluded ? ` ${S.excluded} split / bonus day(s) left out (prices are unadjusted).` : ""))];
  const rd = E.pbReading(S, C.sym); if (rd) out.push(["reading", rd]);
  out.push(K([[cap(uw), String(S.up), `${fx(S.up_pct, 0, false)}% of ${S.used} · ${S.flat} flat`, "g"], [cap(dw), String(S.down), `${fx(100 - S.up_pct - (S.used ? (S.flat / S.used) * 100 : 0), 0, false)}% of ${S.used}`, "r"],
    [`Average ${uw.slice(0, -1)}`, pct(S.avg_up, 2, true), `median ${pct(S.med_up, 2, true)}`, "g"], [`Average ${dw.slice(0, -1)}`, pct(S.avg_dn, 2, true), `median ${pct(S.med_dn, 2, true)}`, "r"],
    ["Biggest up", pct(S.best[1], 2, true), dfmt(S.best[0], "a d b Y"), "g"], ["Biggest down", pct(S.worst[1], 2, true), dfmt(S.worst[0], "a d b Y"), "r"],
    ["Up / down volume", vr ? `${fx(vr, 2, false)}×` : "N/A", "volume on up days ÷ down days · above 1.25 = demand", vr && vr >= 1.25 ? "g" : vr && vr <= 0.8 ? "r" : null],
    ["Average up ÷ average down", S.payoff ? `${fx(S.payoff, 2, false)}×` : "N/A", "size of the typical up day vs down day"]], 4));
  out.push(["html", "moves", S.frame.map((x) => [x.d, x.move, x.v, x.up])]);
  const su = S.streak_now, bn = S.bench;
  const rng = [["Average daily range", pct(S.avg_range, 2)], ["ATR 14", `${fx(S.atr_pct, 2, false)}% of price`], ["Return over the window", pct(S.ret, 1, true)]];
  if (bn) rng.push(["NIFTY 50 up days", mode === "day" ? `${fx(bn.up_pct, 0, false)}% (stock ${fx(S.up_pct, 0, false)}%)` : `${fx(bn.up_pct, 0, false)}% (by close)`],
    ["NIFTY 50 average up / down", `${pct(bn.avg_up, 2, true)} / ${pct(bn.avg_dn, 2, true)}`], ["NIFTY 50 over the window", pct(bn.ret, 1, true)]);
  const dv = S.deliv;
  out.push(["row3", [["mini", "Streaks and gaps", [["Longest up streak", `${S.streak_up} sessions`, "g"], ["Longest down streak", `${S.streak_dn} sessions`, "r"],
      ["Current run", S.streak_now_up != null ? `${su} ${S.streak_now_up ? "up" : "down"}` : "flat", S.streak_now_up ? "g" : S.streak_now_up === false ? "r" : null],
      ["Gap ups of 1%+", `${S.gap_up} · ${S.gap_up_held} closed above the open`], ["Gap downs of 1%+", `${S.gap_dn} · ${S.gap_dn_held} closed below the open`]], ""],
    ["mini", "Range and the index", rng, ""],
    dv ? ["stack", [["mini", "Delivery on up vs down days", [["Delivered qty, up ÷ down", `${fx(dv.qty_ratio, 2, false)}×`, dv.qty_ratio >= 1.25 ? "g" : dv.qty_ratio <= 0.8 ? "r" : null], ["Average delivery %, up days", pct(dv.pct_up, 1)],
      ["Average delivery %, down days", pct(dv.pct_dn, 1)], ["Sessions with delivery data", `${dv.days} (${dv.up_days} up · ${dv.dn_days} down)`]], ""],
      NOTE("Above 1.25×: more shares were taken home on up days than on down days, a sign of quiet accumulation. Delivery is not only institutions; read it together with price.")]]
      : ["text", "Delivery on up vs down days", "Needs NSE delivery data for these sessions. It is saved each evening by the Market data job."]]]);
  const fr = S.frame.filter((x) => !x.bad && isFinite(x.move)), dmap = new Map(C.dlv ? C.dlv.d.map((d, i) => [d, C.dlv.pct[i]]) : []);
  const top = [...[...fr].sort((a, b) => b.move - a.move).slice(0, 5), ...[...fr].sort((a, b) => a.move - b.move).slice(0, 5)];
  out.push(["cols", [[H_("How big the moves were"), T([cap(uw), cap(dw)], S.buckets.map(([lo, hi, u, d]) => ({ label: hi < 1e8 ? `${g(lo)}% to ${g(hi)}%` : `${g(lo)}% or more`, values: [String(u), String(d)] })))],
    [H_("Biggest days"), T(["Move", "Volume vs 50-day avg", "Delivery %"], top.map((x) => ({ label: dfmt(x.d, "a d b Y"), values: [`${fx(x.move, 2, false, true)}%`, x.vavg50 && isFinite(x.vavg50) ? `${fx(x.v / x.vavg50, 2, false)}×` : "—",
      dmap.get(x.d) != null && isFinite(dmap.get(x.d)) ? pct(dmap.get(x.d), 1) : "—"] })))]]],
    NOTE("Descriptive statistics of past sessions, not a forecast. Prices are Yahoo daily data (unadjusted); today's candle is included while it is still forming during market hours."));
  return out;
}
function SC(C) {
  const c5 = closeOf(C), b = bench(C, "NIFTY 500") || bench(C, "NIFTY 50"), SCx = E.scorecard(C.B, c5, b, C.dlv);
  if (SCx.overall == null) return [NA("Scorecard", "Screener fundamentals or Yahoo price history", C.B.reason)];
  const good = [], bad = [];
  for (const p of SCx.pillars) for (const i of p.items) { if (i.score >= 75) good.push([i.score, i.label, i.text]); if (i.score <= 25) bad.push([-i.score, i.label, i.text]); }
  for (const [text, ok, detail] of E.flags(C.B)) (ok ? good : bad).push([ok ? 50 : -50, text, detail]);
  const [pe, pp] = c5 ? E.peStats(C.B, c5) : [null, null];
  for (const c of E.evidence(C.B, c5, pe, pp, C.dlv).contradictions) bad.push([-60, c.name, c.numbers]);
  const top = (a) => [...a].sort((x, y) => y[0] - x[0]).slice(0, 7);
  const rows = [];
  for (const p of SCx.pillars) { rows.push({ label: `${p.name} · ${fx(p.score, 0, false)}`, values: ["", "", ""] }); for (const i of p.items) rows.push({ label: "   " + i.label, values: [i.text, fx(i.score, 0, false), i.rule] }); }
  const out = [H_("Scorecard · five pillars, each out of 100", "live", "Every input is scored between two fixed anchors shown in the table below, and a pillar is the plain average of its inputs. It sums up the numbers on this page. It is not a forecast, not a recommendation, and it has not been backtested."),
    ["scorestrip", SCx], ["standout", top(good), top(bad)], H_("How each score is made"), T(["Reading", "Score", "How it is scored"], rows, SCx.pillars.map((p) => p.name + " ·"), `${C.sym}_scorecard`)];
  if (SCx.missing.length) out.push(NOTE("No data for: " + SCx.missing.join(", ") + ". Those pillars are left out of the average rather than guessed."));
  out.push(NOTE("Delivery data (saved nightly) adds an input to Ownership. Anchors are the same for every company, so compare banks with banks and makers with makers."));
  return out;
}
function FNO(C) {
  const d = C.fnoDetail;
  if (!d) return [NA("F&O positioning", "a stock that trades in futures and options, and the stored F&O history (arka_data/fno)", C.fnoWhy || "Run the F&O data workflow once, then open this screen again.")];
  return [["fno", d]];
}

export const SCREENS = { OV, SC, DES, SYN, FA, BS, CF, RAT, GRW, ERN, HC, FH, DUP, VAL, DCF, RV, ANR, GP, SWG, PB, HP, TECH, SEAS, TRA, QR, COMP, BETA, HVG, PERF, HDS, DLV, FNO, MF, EVTS, CN, CACS, DVD, NOTE: NOTEFN, SRC };
export function build(code, C) { try { return SCREENS[code](C); } catch (e) { return [NA(FN[code] ? FN[code].name : code, "the data for this screen", `Could not be computed: ${e.message}`)]; } }

// ── the stock page header numbers (app.py _sw_metrics) ──
export function swMetrics(H) {
  if (!H || H.d.length < 2) return {};
  const c = H.c, hi = H.h, lo = H.l, v = H.v, n = c.length, px = c.at(-1), prev = c.at(-2);
  const m = { price: px, prev, chg_abs: px - prev, chg: prev ? ((px - prev) / prev) * 100 : null, open: H.o ? H.o.at(-1) : null, day_hi: hi.at(-1), day_lo: lo.at(-1), pdh: hi.at(-2), pdl: lo.at(-2), n, date: dfmt(H.d.at(-1), "d b Y") };
  m.hi52 = Math.max(...hi.slice(-252)); m.lo52 = Math.min(...lo.slice(-252));
  if (n >= 21) { m.hi20 = Math.max(...hi.slice(-21, -1)); m.lo20 = Math.min(...lo.slice(-21, -1)); }
  const rets = {};
  for (const [lab, k] of [["1W", 5], ["1M", 21], ["3M", 63], ["6M", 126], ["1Y", 252]]) if (n > k && c[n - 1 - k]) rets[lab] = (px / c[n - 1 - k] - 1) * 100;
  const yr = H.d.at(-1).slice(0, 4), before = c.filter((_, i) => H.d[i].slice(0, 4) < yr), base = before.length ? before.at(-1) : c[H.d.findIndex((d) => d.slice(0, 4) === yr)];
  if (base) rets.YTD = (px / base - 1) * 100;
  m.rets = rets;
  if (n >= 10) m.ema10 = ewm(c, { span: 10 }).at(-1);
  if (n >= 21) m.ema21 = ewm(c, { span: 21 }).at(-1);
  if (n >= 50) m.sma50 = mean(c.slice(-50));
  if (n >= 200) m.sma200 = mean(c.slice(-200));
  if (n >= 15) {
    const d = c.map((x, i) => (i ? x - c[i - 1] : NaN)), up = ewm(d.map((x) => (isNaN(x) ? null : Math.max(x, 0))), { alpha: 1 / 14 }), dn = ewm(d.map((x) => (isNaN(x) ? null : Math.max(-x, 0))), { alpha: 1 / 14 });
    m.rsi = dn.at(-1) === 0 ? 100 : 100 - 100 / (1 + up.at(-1) / dn.at(-1));
    m.atr = atrOf(H); m.atr_pct = px ? (m.atr / px) * 100 : null;
  }
  if (v && n >= 21) { const avg = mean(v.slice(-21, -1)); m.vol = v.at(-1); m.vol_avg = avg; m.rel_vol = avg ? v.at(-1) / avg : null; }
  return m;
}

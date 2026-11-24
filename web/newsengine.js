// Arka Trades · news engine. A port of news_feed.py v12.1 (tone, story types, sectors and themes, stock tagging, merging the
// same story from several outlets, importance and priority, the desk views and summaries). Pure functions, no network.
export const MACRO_QUERIES = ["RBI monetary policy repo rate", "Nifty Sensex stock market today", "FII DII flows India equities", "US Federal Reserve rates markets",
  "crude oil prices India", "rupee dollar forex India", "India inflation GDP economy", "global markets Wall Street Asia"];
export const ALIASES = { RELIANCE: ["reliance industries", "ril", "reliance jio", "jio platforms", "reliance retail"], TCS: ["tata consultancy"], INFY: ["infosys"], HDFCBANK: ["hdfc bank"], ICICIBANK: ["icici bank"],
  SBIN: ["state bank of india", "sbi"], AXISBANK: ["axis bank"], KOTAKBANK: ["kotak mahindra bank", "kotak bank"], BAJFINANCE: ["bajaj finance"], BAJAJFINSV: ["bajaj finserv"], LT: ["larsen & toubro", "larsen and toubro", "l&t"],
  HINDUNILVR: ["hindustan unilever", "hul"], BHARTIARTL: ["bharti airtel", "airtel"], WIPRO: ["wipro"], HCLTECH: ["hcltech", "hcl tech", "hcl technologies"], TECHM: ["tech mahindra"], LTIM: ["ltimindtree"],
  MARUTI: ["maruti suzuki", "maruti"], TATAMOTORS: ["tata motors", "jaguar land rover", "jlr"], "M&M": ["mahindra & mahindra", "mahindra and mahindra", "m&m"], "BAJAJ-AUTO": ["bajaj auto"],
  HEROMOTOCO: ["hero motocorp"], EICHERMOT: ["eicher motors", "royal enfield"], SUNPHARMA: ["sun pharma"], DRREDDY: ["dr reddy", "dr. reddy"], CIPLA: ["cipla"], DIVISLAB: ["divi's lab", "divis lab"],
  APOLLOHOSP: ["apollo hospitals"], TATASTEEL: ["tata steel"], JSWSTEEL: ["jsw steel"], HINDALCO: ["hindalco"], VEDL: ["vedanta"], COALINDIA: ["coal india"], ONGC: ["oil and natural gas", "ongc"], NTPC: ["ntpc"],
  POWERGRID: ["power grid"], TATAPOWER: ["tata power"], IOC: ["indian oil"], BPCL: ["bharat petroleum", "bpcl"], ADANIENT: ["adani enterprises"], ADANIPORTS: ["adani ports"], ADANIGREEN: ["adani green"],
  ASIANPAINT: ["asian paints"], TITAN: ["titan company"], NESTLEIND: ["nestle india"], BRITANNIA: ["britannia"], ULTRACEMCO: ["ultratech"], GRASIM: ["grasim"], HAL: ["hindustan aeronautics"], BEL: ["bharat electronics"],
  BHEL: ["bharat heavy electricals", "bhel"], IRFC: ["indian railway finance", "irfc"], RVNL: ["rail vikas nigam", "rvnl"], ETERNAL: ["zomato", "eternal ltd", "blinkit"], ZOMATO: ["zomato"], PAYTM: ["paytm", "one 97"],
  NYKAA: ["nykaa", "fsn e-commerce"], DMART: ["avenue supermarts", "dmart", "d-mart"], TRENT: ["trent ltd", "zudio"], DIXON: ["dixon technologies"], PERSISTENT: ["persistent systems"], HDFCLIFE: ["hdfc life"],
  SBILIFE: ["sbi life"], ICICIPRULI: ["icici prudential life"], INDUSINDBK: ["indusind bank"], BANKBARODA: ["bank of baroda"], PNB: ["punjab national bank"], CANBK: ["canara bank"], IDEA: ["vodafone idea"],
  YESBANK: ["yes bank"], DLF: ["dlf"], LICI: ["life insurance corporation", "lic"], ITC: ["itc ltd", "itc hotels"], JIOFIN: ["jio financial"], SHRIRAMFIN: ["shriram finance"], CHOLAFIN: ["cholamandalam"] };
const SECTORS = { "BANKS & FIN": "banks?|banking|lenders?|nbfcs?|insurers?|insurance|fintech|microfinance|credit growth|deposits?", IT: "it services|it stocks|it sector|software|tech services|it majors?|outsourcing",
  AUTO: "autos?|automakers?|car sales|two-wheelers?|passenger vehicles?|ev makers?|electric vehicles?|tractors?", PHARMA: "pharma|drugmakers?|usfda|drug|hospitals?|healthcare|api makers?",
  METALS: "steel|metals?|aluminium|copper|mining|iron ore|zinc", ENERGY: "crude|oil marketing|refiners?|opec|petrol|diesel|lng|natural gas|city gas", FMCG: "fmcg|consumer goods|staples|rural demand",
  REALTY: "real estate|realty|housing sales|property market", POWER: "power demand|electricity|renewables?|solar|discoms?|power sector|power stocks",
  "CAP GOODS": "defence|defense|capital goods|railways?|engineering|order book", TELECOM: "telecom|5g|spectrum|arpu", CEMENT: "cement", CHEMICALS: "chemicals?|agrochemicals?|fertilisers?|fertilizers?" };
const MACRO_TAGS = { RBI: "rbi|repo rate|monetary policy|mpc", FED: "fed|federal reserve|fomc|powell", INFLATION: "inflation|cpi|wpi", CRUDE: "crude|brent|opec", RUPEE: "rupee|forex|inr", FII: "fiis?|fpis?|foreign investors|dii",
  GDP: "gdp|growth forecast", TRADE: "tariffs?|trade deal|trade war|exports?|imports?", BUDGET: "budget|fiscal deficit|gst", GLOBAL: "wall street|nasdaq|dow|s&p 500|china|nikkei" };
const EVENTS = [["REGULATORY", "sebi|penalty|probe|raid|show[- ]cause|banned|ban on|regulatory|compliance|enforcement directorate|\\bed\\b|cbi|court|tribunal|nclt"], ["FILING", "regulation 30|intimation|disclosure under"],
  ["ORDER", "orders?|contracts?|letter of award|bags|wins .*?(deal|order|contract)|work order|loa"], ["STAKE", "stake|acqui(re|res|red|sition)|merger|demerger|block deal|bulk deal|open offer|takeover"],
  ["PROMOTER", "promoters?|pledge[ds]?|insider"], ["EARNINGS", "results?|earnings|q[1-4]\\b|quarterly|net profit|revenue|ebitda|margins?|guidance|profit (rises|falls|jumps|drops)"],
  ["CORP ACTION", "dividend|record date|bonus|stock split|split|buyback|rights issue|qip|fund raise|ncd"], ["IPO", "\\bipo\\b|listing|lists at|grey market|gmp|subscribed"],
  ["RATING", "upgrades?|downgrades?|target price|price target|rating|outperform|underperform|brokerage|initiates coverage|buy call|sell call"], ["CAPEX", "capex|expansion|new plant|capacity|commission(s|ed|ing)|greenfield|brownfield"],
  ["MANAGEMENT", "\\bceo\\b|\\bcfo\\b|\\bmd\\b|chairman|appoints?|resigns?|steps down|board meeting"]];
export const EVENT_WEIGHT = { REGULATORY: 3, ORDER: 3, STAKE: 3, PROMOTER: 2, EARNINGS: 3, "CORP ACTION": 2, FILING: 2, IPO: 1, RATING: 2, CAPEX: 2, MANAGEMENT: 2, MACRO: 1, NEWS: 0 };
const SENT = [["usfda (approval|nod|clearance)|eir from (the )?usfda|order inflows?|record order book|debt[- ]free|stake hike|special dividend|bonus issue|guidance raised|raises? full[- ]year|margin expansion|turns? profitable|rating upgrade|npas? (fall|falls|decline|declines|ease|eases)", 2],
  ["usfda (warning letter|import alert|observations?)|form 483|pledge[sd]? (more )?shares|rating downgrade|guidance cut|cuts? (guidance|outlook|forecast)|margin contraction|npas? (rise|rises|surge|surges|jump|jumps)|show[- ]cause notice|(tax|gst) demand|data breach|recalls?|write[- ]?offs?|qualified opinion|auditor (resigns|quits)|fire (at|in) (the )?(plant|factory|unit)|promoters? (sell|sells|offload|offloads)", -2],
  ["record (high|profit|revenue|sales)|all[- ]time high|52[- ]week high|upper circuit|multibagger", 2], ["beats? (estimates?|expectations?|street)|raises? (guidance|outlook|target)|target raised", 2],
  ["(wins?|bags?|secures?|receives?) (an? )?(?:[\\w₹$.,-]+ ){0,4}(orders?|contracts?|deals?)", 2], ["surges?|soars?|skyrockets?|zooms?|rall(y|ies)|profit (jumps|surges|soars|doubles)|upgraded?", 2],
  ["rises?|gains?|climbs?|jumps?|higher|growth|expands?|launch(es)?|partnership|approv(al|es|ed)|recover(s|y)|rebounds?|outperforms?|buy rating|strong demand|improves?|robust|tailwinds?", 1],
  ["crash(es)?|plung(e|es)|plummets?|collapses?|fraud|scam|defaults?|bankrupt(cy)?|insolvency|raid|scandal|lower circuit|52[- ]week low|profit warning|loss widens|misses (estimates?|expectations?)|downgraded?|resigns?", -2],
  ["slumps?|tumbles?|sinks?|penalty|banned|suspended|probe|sell[- ]?off", -2],
  ["falls?|declines?|drops?|dips?|slips?|slides?|weak(ens|er)?|concerns?|delay(ed|s)?|misses|lower|cuts?|sell rating|target cut|pressure|outflows?|headwinds?|losses|slowdown", -1]];
const NEGATION = /\b(not|no|never|fails? to|unlikely to|without)\b[\w\s,'-]{0,18}$/;
const AMOUNT = /(?:₹|rs\.?|inr)\s?([\d,]+(?:\.\d+)?)\s?(lakh crore|lakh cr|crore|cr\b|billion|bn\b|million|mn\b)/i;
const SENT_RX = SENT.map(([p, w]) => [new RegExp(`(?<![a-z0-9])(?:${p})(?![a-z0-9])`, "g"), w]);
const EVENT_RX = EVENTS.map(([e, p]) => [e, new RegExp(`(?<![a-z0-9])(?:${p})`, "i")]);
const SECTOR_RX = Object.entries(SECTORS).map(([k, p]) => [k, new RegExp(`(?<![a-z0-9])(?:${p})(?![a-z0-9])`)]);
const MACRO_RX = Object.entries(MACRO_TAGS).map(([k, p]) => [k, new RegExp(`(?<![a-z0-9])(?:${p})(?![a-z0-9])`)]);
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
const pyRound = (x) => { const f = Math.floor(x), d = x - f; return d > 0.5 ? f + 1 : d < 0.5 ? f : f % 2 === 0 ? f : f + 1; };   // Python's round(): halves to even

export function sentiment(text) {                           // −3 … +3 with negation handling
  const t = String(text || "").toLowerCase(), taken = []; let score = 0;
  for (const [rx, w] of SENT_RX) {
    rx.lastIndex = 0;
    for (const m of t.matchAll(rx)) {
      const s = m.index;
      if (taken.some(([a, b]) => a <= s && s < b)) continue;
      taken.push([s, s + m[0].length]);
      score += NEGATION.test(t.slice(Math.max(0, s - 26), s)) ? -w : w;
    }
  }
  return clamp(score, -3, 3);
}
export const sentKey = (s) => (s >= 2 ? "strong_positive" : s === 1 ? "mild_positive" : s <= -2 ? "strong_negative" : s === -1 ? "mild_negative" : "neutral");
export const eventOf = (text) => { for (const [n, rx] of EVENT_RX) if (rx.test(text || "")) return n; return ""; };
export const eventFor = (text, macro) => eventOf(text || "") || (macro ? "MACRO" : "NEWS");
export const topics = (low) => [SECTOR_RX.filter(([, rx]) => rx.test(low)).map(([k]) => k), MACRO_RX.filter(([, rx]) => rx.test(low)).map(([k]) => k)];
export function amountCr(text) {
  const m = String(text || "").match(AMOUNT); if (!m) return null;
  const v = parseFloat(m[1].replace(/,/g, "")); if (isNaN(v)) return null;
  const u = m[2].toLowerCase().replace(/ /g, "");
  return v * ({ lakhcrore: 1e5, lakhcr: 1e5, crore: 1, cr: 1, billion: 100, bn: 100, million: 0.1, mn: 0.1 }[u] ?? 1);
}
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
export function tagger(symbols, names = {}) {               // one matcher for tickers (case-sensitive, headline) and aliases / names (any case, headline + summary)
  const alias = {};
  for (const sym of symbols) {
    const pats = [...(ALIASES[sym] || [])], nm = names[sym];
    if (nm) { const words = nm.replace(/[^A-Za-z0-9& ]/g, " ").split(/\s+/).filter((w) => w && !["ltd", "limited", "the", "india", "co", "corporation"].includes(w.toLowerCase()));
      if (words.length && (words.length > 1 || words[0].length >= 5)) pats.push(words.slice(0, 2).join(" ").toLowerCase()); }
    for (const p of pats) { const l = (alias[p] = alias[p] || []); if (!l.includes(sym)) l.push(sym); }
  }
  const ticks = symbols.filter((s) => s.length >= 3).sort((a, b) => b.length - a.length);
  const tickRx = ticks.length ? new RegExp(`(?<![A-Za-z0-9])(${ticks.map(esc).join("|")})(?![A-Za-z0-9])`, "g") : null;
  const keys = Object.keys(alias).sort((a, b) => b.length - a.length);
  const aliasRx = keys.length ? new RegExp(`(?<![a-z0-9])(${keys.map(esc).join("|")})(?![a-z0-9])`, "g") : null;
  return { tickRx, aliasRx, alias, order: Object.fromEntries(symbols.map((s, i) => [s, i])) };
}
export function tag(title, summary, T) {
  const found = new Set();
  if (T.tickRx) for (const m of String(title).matchAll(T.tickRx)) found.add(m[1]);
  if (T.aliasRx) for (const m of `${title} ${summary}`.toLowerCase().matchAll(T.aliasRx)) for (const s of T.alias[m[1]]) found.add(s);
  return [...found].sort((a, b) => (T.order[a] ?? 1 << 30) - (T.order[b] ?? 1 << 30));
}
export const normTitle = (t) => String(t || "").toLowerCase().replace(/\[[^\]]+\]|\b(live updates?|live blog|breaking|watch|explained)\b/g, " ").replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();
export function similar(a, b) {                             // the same story told by two outlets?
  const wa = a.split(" ").filter(Boolean), wb = b.split(" ").filter(Boolean), sa = new Set(wa), sb = new Set(wb);
  if (!sa.size || !sb.size) return false;
  const inter = [...sa].filter((w) => sb.has(w)).length;
  if (inter < 4 || inter / Math.max(1, Math.min(sa.size, sb.size)) < 0.72) return false;
  if (!sb.has(wa[0]) || !sa.has(wb[0])) return false;
  const na = [...sa].filter((w) => /^\d/.test(w)), nb = new Set([...sb].filter((w) => /^\d/.test(w)));
  return !(na.length && nb.size && !na.some((w) => nb.has(w)));
}
function hash(s) { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return (h >>> 0).toString(36); }
export function make(row, kind, source, symbol, T) {        // one story from one feed row
  const dt = row.dt; let title = String(row.title || "").trim();
  if (!dt || !title) return null;
  let src = row.src || source;
  if (kind === "company" || kind === "macro") { const parts = title.split(/\s+-\s+/); if (parts.length >= 2 && parts[parts.length - 1].length <= 60) { src = row.src || parts[parts.length - 1].trim(); title = parts.slice(0, -1).join(" - ").trim(); } }
  let summary = row.summary || "";
  if (summary && (normTitle(summary).includes(normTitle(title).slice(0, 60)) || summary.length < 25)) summary = "";
  const text = `${title}. ${summary}`, low = text.toLowerCase();
  let score = kind === "filing" ? sentiment(`${title}. ${summary}`) : sentiment(title) * 2 + sentiment(summary);
  if (kind !== "filing") score = clamp(pyRound(score / 2), -3, 3);
  const syms = tag(title, kind !== "company" ? summary : "", T);
  if (symbol && !syms.includes(symbol) && kind === "company") syms.unshift(symbol);
  if (row.sym && !syms.includes(row.sym)) syms.unshift(row.sym);
  const [sectors, macro] = topics(low), event = eventFor(kind !== "filing" ? title : `${title} ${summary}`, kind === "macro" || (macro.length > 0 && !syms.length));
  const link = row.link || "";
  return { id: hash(link || title), title, link, source: src, sources: [src], source_count: 1, pub: dt, kind, summary, symbols: syms,
    symbol: syms[0] || (kind === "macro" || macro.length ? "MACRO" : "MARKET"), sectors, macro, event, score, sentiment: sentKey(score), amount_cr: amountCr(text), company: row.company || null };
}
export function cluster(items) {                             // merge the same story from several outlets
  items = items.slice().sort((a, b) => b.pub - a.pub);
  const out = [], keys = [], byLink = {};
  for (const a of items) {
    if (a.link && byLink[a.link] != null) continue;
    const k = normTitle(a.title), from = Math.max(0, keys.length - 150);
    let hit = null;
    for (let i = from; i < keys.length; i++) if (similar(k, keys[i]) && !(a.symbols.length && out[i].symbols.length && !a.symbols.some((s) => out[i].symbols.includes(s)))) { hit = i; break; }
    if (hit == null) { keys.push(k); out.push({ ...a, sources: [...a.sources], symbols: [...a.symbols], sectors: [...a.sectors] }); byLink[a.link] = out.length - 1; }
    else {
      const L = out[hit];
      L.sources = [...new Set([...L.sources, ...a.sources])].slice(0, 8); L.source_count = L.sources.length;
      L.symbols = [...new Set([...L.symbols, ...a.symbols])]; L.sectors = [...new Set([...L.sectors, ...a.sectors])];
      L.first = Math.min(L.first ?? L.pub, a.pub);
      if ((L.symbol === "MARKET" || L.symbol === "MACRO") && a.symbols.length) L.symbol = a.symbols[0];
    }
  }
  return out;
}
export function ago(dt, now) {
  const s = Math.floor((now - dt) / 1000);
  if (s < 60) return "now"; if (s < 3600) return Math.floor(s / 60) + "m"; if (s < 86400) return Math.floor(s / 3600) + "h";
  return new Date(dt + 330 * 60000).toLocaleDateString("en-IN", { day: "2-digit", month: "short", timeZone: "UTC" });
}
export function score(items, mine, current, now) {          // importance, priority, breaking, trending
  for (const a of items) {
    const age = Math.max(0, (now - a.pub) / 60000), w = EVENT_WEIGHT[a.event] ?? 0;
    const imp = Math.min(5, Math.max(1, (a.event === "NEWS" ? 1 : w <= 1 ? 2 : 3) + (Math.abs(a.score) >= 2 ? 1 : 0) + ((a.amount_cr || 0) >= 1000 ? 1 : 0) + (a.source_count >= 3 ? 1 : 0)));
    a.importance = imp; a.mine = a.symbols.some((s) => mine.has(s));
    a.priority = Math.trunc(Math.min(100, imp * 12 + Math.max(0, 30 - age / 15) + Math.min(12, (a.source_count - 1) * 4) + (a.mine ? 15 : 0) + (current && a.symbols.includes(current) ? 15 : 0)));
    a.is_breaking = age <= 60 && (imp >= 4 || a.source_count >= 2); a.hot = a.source_count >= 3 && age <= 180; a.age_min = age; a.time_str = ago(a.pub, now);
  }
  return items;
}
export function assemble(results, symbols, names, days, now, current = "") {   // results: [{kind, source, sym, rows}]
  const T = tagger(symbols, names), cutoff = now - days * 864e5, items = [];
  for (const r of results) {
    for (const row of r.rows || []) {
      const a = make(row, r.kind, r.source, r.sym, T);
      if (!a || a.pub < cutoff) continue;
      if (r.kind === "company" && r.sym && !(tag(a.title, "", T).length || a.title.toUpperCase().includes(r.sym)) && names[r.sym]) continue;
      items.push(a);
    }
  }
  const out = score(cluster(items), new Set(symbols), current, now);
  return out.sort((a, b) => b.priority - a.priority || b.pub - a.pub);
}
export const MODES = ["TOP", "LIVE", "MY STOCKS", "MOVERS", "DEVELOPING", "CATALYSTS", "RISK", "EARNINGS", "CORP ACTIONS", "FILINGS", "MACRO"];
export function view(items, mode, query = "", current = "", moves = {}) {
  const m = mode.toUpperCase(); let out;
  const big = (a) => Math.max(0, ...a.symbols.map((s) => Math.abs(moves[s] ?? 0)));
  if (m === "MOVERS") out = items.filter((a) => big(a) >= 2).sort((a, b) => big(b) - big(a) || b.pub - a.pub);
  else if (m === "DEVELOPING") out = items.filter((a) => a.source_count >= 3).sort((a, b) => b.source_count - a.source_count || b.pub - a.pub);
  else if (m === "LIVE") out = items.slice().sort((a, b) => b.pub - a.pub);
  else if (m === "MINE" || m === "MY STOCKS") out = items.filter((a) => a.mine);
  else if (m === "CATALYSTS") out = items.filter((a) => a.mine && (a.score > 0 || ["ORDER", "CAPEX", "STAKE", "CORP ACTION"].includes(a.event)) && a.score >= 0);
  else if (m === "RISK") out = items.filter((a) => a.score < 0 || a.event === "REGULATORY");
  else if (m === "EARNINGS") out = items.filter((a) => a.event === "EARNINGS");
  else if (m === "CORP ACTIONS") out = items.filter((a) => ["CORP ACTION", "STAKE", "PROMOTER", "MANAGEMENT", "IPO"].includes(a.event));
  else if (m === "FILINGS") out = items.filter((a) => a.kind === "filing").sort((a, b) => (a.mine === b.mine ? b.pub - a.pub : a.mine ? -1 : 1));
  else if (m === "MACRO") out = items.filter((a) => a.kind === "macro" || a.macro.length);
  else if (m === "SECURITY") out = items.filter((a) => current && a.symbols.includes(current));
  else out = items.filter((a) => a.kind !== "filing" || a.mine);
  const q = (query || "").trim().toLowerCase();
  if (q) out = out.filter((a) => a.title.toLowerCase().includes(q) || a.source.toLowerCase().includes(q) || a.event.toLowerCase().includes(q) || a.symbols.some((s) => s.toLowerCase() === q) || a.sectors.some((s) => s.toLowerCase().includes(q)));
  return out;
}
export function newsTone(items) {                            // −100 … +100, importance-weighted
  const w = items.filter((a) => a.kind !== "filing").map((a) => [a.score, a.importance]), tot = w.reduce((p, [, i]) => p + i, 0);
  return tot ? pyRound((w.reduce((p, [s, i]) => p + s * i, 0) / tot / 3) * 100) : 0;
}
export function pulse(items, symbols) {
  const rows = symbols.map((s) => { const st = items.filter((a) => a.symbols.includes(s));
    if (!st.length) return { symbol: s, stories: 0, tone: 0, pos: 0, neg: 0, latest: "", age: null, link: "" };
    const latest = st.reduce((a, b) => (b.pub > a.pub ? b : a));
    return { symbol: s, stories: st.length, tone: pyRound((st.reduce((p, a) => p + a.score, 0) / st.length) * 100) / 100, pos: st.filter((a) => a.score > 0).length, neg: st.filter((a) => a.score < 0).length, latest: latest.title, age: latest.time_str, link: latest.link }; });
  return rows.sort((a, b) => b.stories - a.stories || b.tone - a.tone);
}
export function sectorTone(items) {
  const acc = {}; for (const a of items) for (const s of a.sectors) (acc[s] = acc[s] || []).push(a.score);
  return Object.entries(acc).filter(([, v]) => v.length >= 2).map(([k, v]) => [k, v.length, v.reduce((p, q) => p + q, 0) / v.length]).sort((a, b) => b[1] - a[1]);
}
export function rotation(symbols, current, n, bucket) {      // which of your stocks get their own Google News search this cycle
  const rest = symbols.filter((s) => s !== current), pinned = current ? [current] : [], slots = Math.max(0, n - pinned.length);
  if (rest.length <= slots) return [...pinned, ...rest];
  const k = Math.ceil(rest.length / slots), off = (bucket % k) * slots, part = rest.slice(off, off + slots);
  return [...pinned, ...part, ...rest.slice(0, Math.max(0, slots - part.length))];
}
export function companyQuery(sym, name) {
  if (name) { const words = name.replace(/[^A-Za-z0-9& ]/g, " ").split(/\s+/).filter((w) => w && !["ltd", "limited"].includes(w.toLowerCase())); return `("${words.slice(0, 3).join(" ")}" OR "${sym}") (shares OR stock OR results OR order OR stake OR earnings)`; }
  const alias = (ALIASES[sym] || [])[0];
  return alias ? `("${alias}" OR "${sym}") (shares OR stock)` : `"${sym}" share price NSE`;
}

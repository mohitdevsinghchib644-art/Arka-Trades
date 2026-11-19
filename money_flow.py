"""
money_flow.py - Arka MONEY FLOW v8 · Sector rotation: where to be, and when

Same entry point as v7: render_money_flow(on_open=None). Needs arka_flow/rotation.py (new) next to the existing package.

What changed vs v7
  * v7 described every sector with rule states but never answered "which sectors should my money be in now, and when do I
    move?". v8 adds the ROTATION MODEL (arka_flow/rotation.py): a cross-sectional score built from published effects
    (risk-adjusted relative momentum as in NSE's momentum indices and Moskowitz & Grinblatt's industry momentum, RRG
    rotation speed, breadth, trend consistency, up-day turnover), a 200-session trend filter, a market-regime filter for
    stress periods, rank buffers against churn, and a model portfolio of the top N sectors reviewed every K sessions.
  * DOES IT WORK? tests the model walk-forward on the loaded history: information coefficient per component, quintile
    spread, equity curve vs the benchmark, an equal-weight of all sectors and a plain 6-month momentum ranking, both halves
    of the history, and costs. Nothing is fitted to the data.
  * Every number on the screen comes with the rule that produced it (Sector view and Method).
  * Redesigned: Where to be · Rotation map · Sector · Does it work? · Breadth & activity · Rule states · FPI flows · Method.
  * v7's rule states, breadth, activity, FPI and data-quality views are kept.

Research tool built on daily price, breadth and traded value. Not order-flow data and not investment advice.
"""
from __future__ import annotations

import html
import time
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import streamlit as st

import sector_map as smap
from arka_flow import MODEL_VERSION, backtest as bt, fpi, history, loader, report, rotation as rot, state as rstate, universe
from arka_flow.calc import relative_strength as rs
from arka_flow.config import MIN_SECTOR_COVERAGE
from arka_flow.data import validation as val

try:
    import plotly.graph_objects as go
except Exception:  # pragma: no cover
    go = None

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
LOCAL_DATA = Path("data")
DISK_CACHE = Path(".cache") / "nse"
SECTOR_MAP_FILE = LOCAL_DATA / "sector_map.csv"
ARCHIVE_MIRRORS = ("https://nsearchives.nseindia.com/content/indices/", "https://archives.nseindia.com/content/indices/",
                   "https://www.niftyindices.com/IndexConstituent/")
UNIVERSE_SETS = (("ind_niftytotalmarket_list",), ("ind_nifty500list", "ind_niftymicrocap250_list"), ("ind_nifty500list",))
MAX_SYMBOLS = 1500
BENCH_TICKERS = {"NIFTY 500": "^CRSLDX", "NIFTY 50": "^NSEI"}
EW_MARKET = "Equal-weight market"
INDEX_REG = {**universe.OFFICIAL_SECTOR_INDICES, **universe.THEMATIC_INDICES}
VIEWS = ["Where to be", "Rotation map", "Sector", "Does it work?", "Breadth & activity", "Rule states", "FPI flows", "Method & data"]
KINDS = {"Arka groups": "Arka's ~50 rotation groups (sector_map.py), equal-weight baskets of today's members. Breadth available for every group.",
         "NSE indices": "Official NSE sector indices. No survivorship bias, but fewer sectors; breadth needs each index's constituent file in data/."}
_ARCH_DOWN: dict = {}

GREEN, RED, AMBER, CYAN, YELLOW, PURPLE, T2, T3 = "#30D158", "#FF453A", "#FF9F0A", "#5AC8FA", "#FFD60A", "#BF5AF2", "#A1A1AA", "#71717A"
QUAD_C = {"Leading": GREEN, "Weakening": YELLOW, "Lagging": RED, "Improving": CYAN, "": T3}
ACT_C = {"Rotate in": GREEN, "Hold": "#8AD66B", "Rotate out": RED, "Watch: improving": CYAN, "": T3}
REG_C = {"Risk-on": GREEN, "Neutral": YELLOW, "Risk-off": RED, "Stress": RED, "Unknown": T3}
STATE_C = {"LEADERSHIP": GREEN, "MATURE LEADERSHIP": YELLOW, "PARTICIPATION CONFIRMATION": "#8AD66B", "EARLY IMPROVEMENT": CYAN,
           "RECOVERY": PURPLE, "DETERIORATION": RED, rstate.NONE: T2, rstate.NOHIST: T3}
BOARD_ORDER = ("EARLY IMPROVEMENT", "PARTICIPATION CONFIRMATION", "LEADERSHIP", "MATURE LEADERSHIP", "RECOVERY", "DETERIORATION")
SOURCES = [
    ("Moskowitz & Grinblatt (1999), Do Industries Explain Momentum?, Journal of Finance", "https://doi.org/10.1111/0022-1082.00146"),
    ("NSE Indices, Nifty200 Momentum 30: Normalised Momentum Score (6 and 12-month return / volatility)", "https://niftyindices.com/indices/equity/strategy-indices/nifty200-momentum-30"),
    ("de Kempenaer, Relative Rotation Graphs (JdK RS-Ratio / RS-Momentum; Bloomberg RRG <GO>)", "https://chartschool.stockcharts.com/table-of-contents/chart-analysis/chart-types/relative-rotation-graphs-rrg-charts"),
    ("Da, Gurun & Warachka (2014), Frog in the Pan: Continuous Information and Momentum, Review of Financial Studies", "https://doi.org/10.1093/rfs/hhu003"),
    ("Faber (2007), A Quantitative Approach to Tactical Asset Allocation (10-month trend rule)", "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=962461"),
    ("Moskowitz, Ooi & Pedersen (2012), Time Series Momentum, Journal of Financial Economics", "https://www.aqr.com/Insights/Datasets/Time-Series-Momentum-Original-Paper-Data"),
    ("Daniel & Moskowitz (2016), Momentum Crashes, Journal of Financial Economics", "https://www.nber.org/papers/w20439"),
]


def _e(x):
    return html.escape("" if x is None else str(x))


def _f(v, fmt="+.1f", suf=""):
    try:
        if v is None or pd.isna(v):
            return "n/a"
        return format(float(v), fmt) + suf
    except (TypeError, ValueError):
        return "n/a"


def _phone():
    return bool(st.session_state.get("_ak_phone"))


# ═════════════════════════ universe (unchanged from v7) ═════════════════════════
def _parse_csv(text: str):
    df = pd.read_csv(StringIO(text))
    df.columns = [str(c).strip() for c in df.columns]
    return df if "Symbol" in df and len(df) else None


@st.cache_data(ttl=6 * 3600, show_spinner=False, max_entries=16)
def _archive_net(slug: str) -> pd.DataFrame:
    hdr = {"User-Agent": UA, "Accept": "text/csv,text/plain,*/*", "Accept-Language": "en-US,en;q=0.9"}
    last = "unreachable"
    for base in ARCHIVE_MIRRORS:
        try:
            r = requests.get(f"{base}{slug}.csv", headers=hdr, timeout=8)
            if r.status_code == 200:
                df = _parse_csv(r.text)
                if df is not None:
                    try:
                        DISK_CACHE.mkdir(parents=True, exist_ok=True)
                        (DISK_CACHE / f"{slug}.csv").write_text(r.text)
                    except Exception:
                        pass
                    return df
            last = f"HTTP {r.status_code}"
        except Exception as exc:
            last = type(exc).__name__
    raise RuntimeError(last)


def _read_local(slug: str):
    for folder in (LOCAL_DATA, DISK_CACHE):
        f = folder / f"{slug}.csv"
        if f.exists():
            try:
                return _parse_csv(f.read_text(encoding="utf-8", errors="ignore"))
            except Exception:
                pass
    return None


def _read_archive(slug: str):
    local = LOCAL_DATA / f"{slug}.csv"
    if local.exists():
        try:
            return _parse_csv(local.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            pass
    if time.time() >= _ARCH_DOWN.get(slug, 0):
        try:
            return _archive_net(slug)
        except Exception:
            _ARCH_DOWN[slug] = time.time() + 600
    return _read_local(slug)


def _read_map():
    if not SECTOR_MAP_FILE.exists():
        return None
    try:
        mp = pd.read_csv(SECTOR_MAP_FILE)
        mp.columns = [str(c).strip().lower().replace(" ", "_") for c in mp.columns]
        if "symbol" not in mp:
            return None
        mp["symbol"] = mp["symbol"].astype(str).str.upper().str.strip()
        return mp.drop_duplicates("symbol").fillna("")
    except Exception:
        return None


@st.cache_data(ttl=86400, show_spinner=False, max_entries=2)
def _universe_cached():
    df, source = None, ""
    for slugs in UNIVERSE_SETS:
        parts = [p for p in (_read_archive(s) for s in slugs) if p is not None]
        if parts and sum(len(p) for p in parts) >= 300:
            df, source = pd.concat(parts, ignore_index=True), " + ".join(slugs)
            break
    if df is None:
        raise RuntimeError("stock universe unavailable")
    if "Series" in df:
        df = df[~df["Series"].astype(str).str.strip().str.upper().isin(["RR", "IV"])]
    out = pd.DataFrame({"symbol": df["Symbol"].astype(str).str.upper().str.strip(),
                        "company": df["Company Name"].astype(str).str.strip() if "Company Name" in df else "",
                        "nse_sector": df["Industry"].astype(str).str.strip() if "Industry" in df else ""})
    out = out[~out["symbol"].str.startswith("DUMMY")].drop_duplicates("symbol")
    mp = _read_map()
    blank = lambda: pd.Series("", index=out.index)
    basic, industry, group = blank(), blank(), blank()
    if mp is not None and len(mp):
        mpi = mp.set_index("symbol")
        extra = mpi.loc[~mpi.index.isin(out["symbol"])]
        if len(extra):
            add = pd.DataFrame({"symbol": extra.index, "company": extra["company"].values if "company" in extra else "",
                                "nse_sector": extra["sector"].values if "sector" in extra else ""})
            out = pd.concat([out, add], ignore_index=True).head(MAX_SYMBOLS)
            source += f" + {len(add)} from sector_map.csv"
        col = lambda c: out["symbol"].map(mpi[c]).fillna("").astype(str) if c in mpi else pd.Series("", index=out.index)
        basic, industry, group = col("basic_industry"), col("industry"), col("group")
    out["group"] = [smap.classify(s, c, n, b, i, g) for s, c, n, b, i, g in
                    zip(out["symbol"], out["company"], out["nse_sector"], basic, industry, group)]
    return out.reset_index(drop=True), source




# ═════════════════════════ data + engine ═════════════════════════
@st.cache_data(ttl=3 * 3600, show_spinner=False, max_entries=2)
def _load(symbols: tuple, period: str) -> dict:
    tickers = list(BENCH_TICKERS.values()) + [t for _, t, _ in INDEX_REG.values() if t]
    return loader.load_prices(list(symbols), tickers, period)


def _index_members() -> dict:
    """Constituents of official indices, only from files already in data/ or the disk cache. No guessing."""
    out = {}
    for name, _, slug in INDEX_REG.values():
        df = _read_local(slug)
        if df is not None:
            out[name] = df["Symbol"].astype(str).str.upper().str.strip().tolist()
    return out


@st.cache_resource(ttl=3 * 3600, show_spinner=False, max_entries=6)
def _engine(symbols: tuple, period: str, kind: str, bench_name: str, min_turn: float) -> dict:
    meta, _ = _universe_cached()
    P = dict(_load(symbols, period))
    ss = val.session_state()
    P, dropped = loader.drop_partial_session(P, ss["last_complete_session"])
    close, volume, high, index = P["close"], P["volume"], P["high"], P["index"]
    if close.shape[1] < 30:
        raise RuntimeError(f"Yahoo returned usable history for only {close.shape[1]} stocks")
    tv = (close * volume).tail(20).median()
    illiquid = sorted(tv.index[tv < min_turn * 1e7])
    close, volume, high = (df.drop(columns=illiquid, errors="ignore") for df in (close, volume, high))

    benches = {n: index[t].dropna() for n, t in BENCH_TICKERS.items() if t in index and index[t].notna().sum() >= 60}
    benches[EW_MARKET] = history.equal_weight_level(close, list(close.columns)).dropna()
    if bench_name not in benches:
        raise RuntimeError(f"{bench_name} history did not download (Yahoo ticker {BENCH_TICKERS.get(bench_name)}). "
                           f"Pick another benchmark: {', '.join(benches)}.")
    grp = meta.set_index("symbol")["group"]
    small = []
    if kind == "Arka groups":
        known = grp[grp != "Unclassified"]
        members = {g: sorted(s.index) for g, s in known.groupby(known) if len(s)}
        small = sorted(g for g, m in members.items() if sum(x in close.columns for x in m) < 3)
        members = {g: m for g, m in members.items() if g not in small}
        levels, level_src = None, "equal-weight basket of today's members (Yahoo adjusted closes)"
    else:
        names = {t: n for n, t, _ in INDEX_REG.values() if t}
        levels = index[[t for t in names if t in index and index[t].notna().sum() >= 120]].rename(columns=names)
        if levels.shape[1] < 5:
            raise RuntimeError("fewer than 5 NSE sector indices returned history from Yahoo")
        members = {k: v for k, v in _index_members().items() if k in levels.columns}
        level_src = "official index close from Yahoo (price index, no dividends)"
    H = history.build(close, volume, members, benches[bench_name], levels=levels)
    H["bench_name"] = bench_name
    cl = rstate.classify(H)
    F = rot.factors(H)
    reg = rot.regime(H, close)
    return {"H": H, "cl": cl, "F": F, "reg": reg, "table": report.sector_table(H, cl), "close": close, "high": high, "volume": volume,
            "benches": benches, "members": members, "kind": kind, "level_src": level_src, "_pf": {},
            "q": {"dropped_sessions": dropped, "session": ss, "illiquid": illiquid, "small_groups": small,
                  "failures": P["failures"], "stale": P["stale"], "jumps": P["jumps"], "requested": P["requested"],
                  "loaded": int(P["close"].shape[1]), "used": int(close.shape[1]), "loaded_at": P["loaded_at"],
                  "source": P["source"], "adjusted": P["adjusted"], "period": period, "min_turn": min_turn,
                  "duplicates_dropped": P["duplicates_dropped"], "non_positive": P["non_positive"],
                  "index_missing": sorted(n for n, t, _ in INDEX_REG.values() if t and t not in index)}}


def _params(E):
    n_sec = len(E["H"]["sectors"])
    default_n = 5 if n_sec >= 30 else 4 if n_sec >= 15 else 3
    return {"n": int(st.session_state.get("mf8_n") or default_n), "every": int(st.session_state.get("mf8_every") or 21),
            "cost": float(st.session_state.get("mf8_cost", 0.30)), "regime": bool(st.session_state.get("mf8_regime", True))}


def _portfolio(E, p, score_key="score", use_regime=None, start=None, end=None):
    """Model portfolio for these settings (memoised inside the engine result, so a click never recomputes it)."""
    ur = p["regime"] if use_regime is None else use_regime
    k = (p["n"], p["every"], p["cost"], ur, score_key, str(start), str(end))
    if k not in E["_pf"]:
        E["_pf"][k] = rot.run_portfolio(E["H"], E["F"], E["reg"], n=p["n"], every=p["every"], cost_pct=p["cost"], use_regime=ur,
                                        score_key=score_key, start=start, end=end)
    return E["_pf"][k]


def _snap(E, p):
    k = ("snap", p["n"], p["every"], p["cost"], p["regime"])
    if k not in E["_pf"]:
        E["_pf"][k] = rot.snapshot(E["H"], E["F"], E["reg"], _portfolio(E, p), p["n"])
    return E["_pf"][k]


def _publish(E, snap):
    """Snapshot for HOME's rotation panel (keys used by home_page.py)."""
    act = {"Rotate in": "ROTATING IN", "Hold": "LEADING", "Rotate out": "WEAKENING", "Watch: improving": "WATCH"}
    st.session_state["mf_rot"] = {"asof": E["H"]["asof"], "bench": E["H"]["bench_name"], "table": pd.DataFrame({
        "sector": snap["sector"], "action": [act.get(a) or (q.upper() if q else "—") for a, q in zip(snap["action"], snap["quadrant"])],
        "phase": snap["quadrant"].str.upper(), "score": snap["rank_pct"], "ex1m": snap["rel_21"], "r1w": np.nan})}


# ═════════════════════════ look ═════════════════════════
CSS = """<style>
.mf{font-family:var(--sans,Inter,system-ui,sans-serif);font-variant-numeric:tabular-nums;}
.mf-status{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 12px;}
.mf-chip{display:inline-flex;align-items:center;gap:7px;padding:4px 11px;border-radius:999px;background:var(--s2,#161618);font:500 12.5px var(--sans);color:var(--tx2,#A1A1AA);white-space:nowrap;}
.mf-chip b{color:var(--tx,#EDEDEF);font-weight:600;}
.mf-chip i{width:7px;height:7px;border-radius:50%;display:inline-block;background:var(--c,#52525B);}
.mf-h{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin:28px 0 10px;}
.mf-h b{font:600 17px var(--sans);letter-spacing:-.2px;color:var(--tx);} .mf-h span{font:13px var(--sans);color:var(--tx3,#71717A);}
.mf-note{font:13.5px/1.65 var(--sans);color:var(--tx3);background:var(--s1,#111113);border:1px solid var(--line,#232326);border-radius:12px;padding:12px 16px;margin:8px 0 12px;}
.mf-note b{color:var(--tx2);font-weight:600;}
.mf-regime{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(0,2fr);gap:18px;align-items:center;background:var(--s1);border:1px solid var(--line);
  border-radius:16px;padding:18px 20px;position:relative;overflow:hidden;margin-top:4px;}
.mf-regime:before{content:"";position:absolute;inset:0 auto 0 0;width:4px;background:var(--c);}
@media(max-width:820px){.mf-regime{grid-template-columns:1fr;}}
.mf-regime .k{font:500 12.5px var(--sans);color:var(--tx3);} .mf-regime .v{font:600 26px var(--sans);letter-spacing:-.5px;color:var(--c);margin-top:2px;}
.mf-regime .d{font:13.5px/1.55 var(--sans);color:var(--tx2);margin-top:6px;}
.mf-facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;}
.mf-fact{background:var(--s2);border-radius:10px;padding:10px 12px;} .mf-fact small{display:block;font:500 12px var(--sans);color:var(--tx3);}
.mf-fact b{display:block;font:600 17px var(--sans);color:var(--tx);margin-top:3px;} .mf-fact em{display:block;font:12px var(--sans);font-style:normal;color:var(--tx3);margin-top:2px;}
.mf-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px;}
.mf-card{background:var(--s1);border:1px solid var(--line);border-radius:14px;padding:14px 16px;position:relative;overflow:hidden;}
.mf-card .top{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;}
.mf-card .nm{font:600 15.5px var(--sans);color:var(--tx);line-height:1.3;} .mf-card .rk{font:500 12.5px var(--sans);color:var(--tx3);margin-top:2px;}
.mf-card .sc{text-align:right;font:600 22px var(--sans);color:var(--tx);letter-spacing:-.4px;} .mf-card .sc small{display:block;font:500 11.5px var(--sans);color:var(--tx3);letter-spacing:0;}
.mf-card .why{font:13px/1.55 var(--sans);color:var(--tx2);margin-top:10px;}
.mf-card .chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px;}
.mf-pill{display:inline-block;padding:3px 10px;border-radius:999px;font:600 12px var(--sans);color:var(--c);background:color-mix(in srgb,var(--c) 14%,transparent);white-space:nowrap;}
.mf-tw{border:1px solid var(--line);border-radius:14px;background:var(--s1);overflow:auto;}
.mf-t{width:100%;border-collapse:separate;border-spacing:0;font:13.5px var(--sans);}
.mf-t th{font:500 12px var(--sans);color:var(--tx3);text-align:right;padding:10px 12px;border-bottom:1px solid var(--line);white-space:nowrap;position:sticky;top:0;background:var(--s1);}
.mf-t th:nth-child(-n+2),.mf-t td:nth-child(-n+2){text-align:left;}
.mf-t td{padding:10px 12px;border-bottom:1px solid var(--line);text-align:right;color:var(--tx2);white-space:nowrap;}
.mf-t tr:last-child td{border-bottom:0;} .mf-t td.n{color:var(--tx);font-weight:600;} .mf-t tr:hover td{background:rgba(255,255,255,.02);}
.mf-t tr.held td:first-child{box-shadow:inset 3px 0 0 #30D158;}
.mf-bar{display:inline-flex;align-items:center;gap:8px;} .mf-bar i{display:inline-block;width:64px;height:6px;border-radius:3px;background:#232326;position:relative;overflow:hidden;}
.mf-bar i:after{content:"";position:absolute;left:0;top:0;bottom:0;width:var(--w);background:var(--c);border-radius:3px;}
.up{color:#30D158 !important;} .dn{color:#FF453A !important;}
.mf-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:8px 0 6px;}
.mf-kpi{background:var(--s1);border:1px solid var(--line);border-radius:12px;padding:12px 14px;} .mf-kpi small{display:block;font:500 12px var(--sans);color:var(--tx3);}
.mf-kpi b{display:block;font:600 21px var(--sans);color:var(--tx);margin-top:4px;letter-spacing:-.3px;} .mf-kpi em{display:block;font:12px var(--sans);font-style:normal;color:var(--tx3);margin-top:2px;}
.mf-why{list-style:none;margin:0;padding:0;} .mf-why li{display:flex;gap:10px;padding:8px 0;border-bottom:1px solid var(--line);font:14px/1.55 var(--sans);color:var(--tx2);}
.mf-why li:last-child{border-bottom:0;} .mf-why li:before{content:"";width:6px;height:6px;border-radius:50%;background:#FF9F0A;margin-top:8px;flex-shrink:0;}
.mf-contrib{display:flex;flex-direction:column;gap:8px;}
.mf-contrib .r{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,2fr) 56px;gap:10px;align-items:center;font:13px var(--sans);color:var(--tx2);}
.mf-contrib .tr{position:relative;height:8px;background:#1C1C1F;border-radius:4px;}
.mf-contrib .tr i{position:absolute;top:0;bottom:0;border-radius:4px;} .mf-contrib .tr s{position:absolute;left:50%;top:-3px;bottom:-3px;width:1px;background:#3a3a3e;}
.mf-contrib b{font:600 13px var(--sans);text-align:right;color:var(--tx);}
.mf-col{background:var(--s1);border:1px solid var(--line);border-radius:14px;padding:14px;}
.mf-col h4{font:600 14px var(--sans) !important;color:var(--c) !important;margin:0 0 4px;} .mf-col p{font:12.5px/1.5 var(--sans);color:var(--tx3);margin:0 0 10px;}
.mf-col .it{border-top:1px solid var(--line);padding:9px 0;font:13px/1.5 var(--sans);color:var(--tx2);} .mf-col .it b{color:var(--tx);font-weight:600;}
.mf-board{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;}
.mf-src a{color:#FF9F0A;}
</style>"""


def _h(title, sub=""):
    st.markdown(f'<div class="mf-h"><b>{_e(title)}</b>{f"<span>{_e(sub)}</span>" if sub else ""}</div>', unsafe_allow_html=True)


def _note(t):
    st.markdown(f'<div class="mf-note">{t}</div>', unsafe_allow_html=True)


def _pill(text, color):
    return f'<span class="mf-pill" style="--c:{color}">{_e(text)}</span>'


def _bar(v, lo=0, hi=100, color=None):
    if v is None or pd.isna(v):
        return '<span style="color:#71717A">n/a</span>'
    w = max(0.0, min(100.0, (float(v) - lo) / (hi - lo) * 100))
    c = color or (GREEN if w >= 70 else "#8AD66B" if w >= 55 else AMBER if w >= 40 else RED)
    return f'<span class="mf-bar" style="--w:{w:.0f}%;--c:{c}"><i></i>{float(v):.0f}</span>'


def _pc(v, d=1, suf="%"):
    if v is None or pd.isna(v):
        return '<span style="color:#71717A">n/a</span>'
    return f'<span class="{"up" if v > 0 else "dn" if v < 0 else ""}">{v:+.{d}f}{suf}</span>'


def _theme(fig, h=320):
    fig.update_layout(height=h, margin=dict(l=8, r=8, t=26, b=8), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="Inter, sans-serif", size=12, color="#A1A1AA"), legend=dict(orientation="h", y=1.08, x=0, font=dict(size=12)),
                      hoverlabel=dict(bgcolor="#161618", bordercolor="#2E2E33", font=dict(family="Inter, sans-serif", size=12, color="#EDEDEF")))
    fig.update_xaxes(showgrid=False, linecolor="#232326", zeroline=False)
    fig.update_yaxes(gridcolor="#1A1A1D", zeroline=False)
    return fig


def _line(df, key, colors=None, h=300, hline=None):
    if df is None or df.dropna(how="all").empty:
        st.caption("Not enough history for this chart.")
        return
    if go is None:
        st.line_chart(df)
        return
    fig = go.Figure()
    pal = colors or [AMBER, "#D4D4D8", CYAN, PURPLE, GREEN]
    for i, c in enumerate(df.columns):
        fig.add_trace(go.Scatter(x=df.index, y=df[c], name=str(c), mode="lines", line=dict(color=pal[i % len(pal)], width=2.2 if i == 0 else 1.4)))
    if hline is not None:
        fig.add_hline(y=hline, line_color="#3a3a3e", line_width=1, line_dash="dot")
    _theme(fig, h).update_layout(hovermode="x unified")
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=key)


# ═════════════════════════ header ═════════════════════════
def _status(E, p):
    H, q, reg = E["H"], E["q"], E["reg"]
    rg = reg["regime"].iloc[-1]
    chips = [f'<span class="mf-chip">As of <b>{H["asof"]}</b> close</span>',
             f'<span class="mf-chip"><b>{len(H["sectors"])}</b> sectors · {_e(E["kind"])}</span>',
             f'<span class="mf-chip">vs <b>{_e(H["bench_name"])}</b></span>',
             f'<span class="mf-chip" style="--c:{REG_C.get(rg, T3)}"><i></i>Market <b>{_e(rg)}</b></span>',
             f'<span class="mf-chip">Model: top <b>{p["n"]}</b>, review every <b>{p["every"]}</b> sessions</span>',
             f'<span class="mf-chip"><b>{q["used"]}</b> stocks used</span>']
    st.markdown('<div class="mf-status">' + "".join(chips) + "</div>", unsafe_allow_html=True)
    if q["dropped_sessions"]:
        st.caption(f"Today's unfinished session ({', '.join(q['dropped_sessions'])}) is left out, so nothing on this page changes during the day.")
    stale_by = val.stale_sessions(H["asof"], q["session"]["last_complete_session"])
    if stale_by:
        st.warning(f"Latest data is {stale_by} session(s) behind the last completed session ({q['session']['last_complete_session']}).")
    if q["failures"]:
        st.caption(f"{len(q['failures'])} ticker(s) returned no data from Yahoo; affected sectors are marked in Method & data.")


# ═════════════════════════ view · WHERE TO BE ═════════════════════════
REG_TXT = {"Risk-on": "Benchmark above its 200-session average and most stocks above theirs. All model slots are filled.",
           "Neutral": "Mixed: benchmark and market breadth disagree. All slots are filled, trend filter still applies.",
           "Risk-off": "Benchmark below its 200-session average and fewer than 40% of stocks above theirs. Half the slots stay in cash.",
           "Stress": "Benchmark 10%+ below its 1-year high with volatility in the top 20% of the year: the 'panic' state where momentum "
                     "strategies historically lose most (Daniel & Moskowitz). Half the slots stay in cash.",
           "Unknown": "Not enough history to judge the market trend yet."}


def _regime_card(E, p):
    r = E["reg"].iloc[-1]
    rg = r["regime"]
    slots = max(1, int(round(p["n"] * rot.REGIME_SLOTS.get(rg, 1.0)))) if p["regime"] else p["n"]
    facts = [("Benchmark vs 200-session avg", _f((r["bench"] / r["sma200"] - 1) * 100 if pd.notna(r["sma200"]) else np.nan, "+.1f", "%"),
              "above" if r["above_sma200"] == 1 else "below" if r["above_sma200"] == 0 else "n/a"),
             ("Stocks above 200-EMA", _f(r["breadth200"], ".0f", "%"), "50%+ healthy · under 40% weak"),
             ("Volatility, 20 sessions", _f(r["vol20"], ".0f", "%"), f'{_f(r["vol_pct"], ".0f")}th percentile of its year'),
             ("From 1-year high", _f(r["drawdown"], "+.1f", "%"), "stress if 10%+ down and volatile")]
    st.markdown(f'<div class="mf-regime" style="--c:{REG_C.get(rg, T3)}"><div><div class="k">Market regime</div><div class="v">{_e(rg)}</div>'
                f'<div class="d">{_e(REG_TXT.get(rg, ""))}<br><b style="color:var(--tx)">Model slots now: {slots} of {p["n"]}</b>'
                f'{"" if p["regime"] else " (regime filter switched off)"}</div></div><div class="mf-facts">'
                + "".join(f'<div class="mf-fact"><small>{a}</small><b>{b}</b><em>{c}</em></div>' for a, b, c in facts) + "</div></div>",
                unsafe_allow_html=True)


def _card(r, bench):
    why = (f'Relative to {_e(bench)}: {_pc(r["rel_63"])} in 3 months, {_pc(r["rel_126"])} in 6. '
           + (f'{_f(r["pct50"], ".0f")}% of members above 50-EMA ({_f(r["d50"], "+.0f")} pts in 20 sessions).' if pd.notna(r["pct50"]) else ""))
    rc = r["rank_change_20"]
    move = "" if pd.isna(rc) or rc == 0 else (f" · up {int(rc)}" if rc > 0 else f" · down {int(-rc)}")
    return (f'<div class="mf-card"><div class="top"><div><div class="nm">{_e(r["sector"])}</div><div class="rk">Rank {int(r["rank"])}{move} in 20 sessions</div></div>'
            f'<div class="sc">{r["rank_pct"]:.0f}<small>score</small></div></div><div class="why">{why}</div>'
            f'<div class="chips">{_pill(r["action"] or "—", ACT_C.get(r["action"], T3))}{_pill(r["quadrant"] or "n/a", QUAD_C.get(r["quadrant"], T3))}'
            f'{_pill("Trend pass" if r["trend_ok"] == 1 else "Trend fail" if r["trend_ok"] == 0 else "Trend n/a", GREEN if r["trend_ok"] == 1 else RED if r["trend_ok"] == 0 else T3)}</div></div>')


def _rank_table(snap, bench, limit=None):
    rows = []
    for r in (snap.head(limit) if limit else snap).to_dict("records"):
        tr = "Pass" if r["trend_ok"] == 1 else "Fail" if r["trend_ok"] == 0 else "n/a"
        rows.append(f'<tr class="{"held" if r["held"] else ""}"><td class="n">{int(r["rank"]) if pd.notna(r["rank"]) else "—"}</td>'
                    f'<td class="n">{_e(r["sector"])}</td><td>{_bar(r["rank_pct"])}</td><td>{_pill(r["quadrant"] or "n/a", QUAD_C.get(r["quadrant"], T3))}</td>'
                    f'<td>{_pc(r["rel_21"])}</td><td>{_pc(r["rel_63"])}</td><td>{_pc(r["rel_126"])}</td>'
                    f'<td>{_f(r["pct50"], ".0f", "%")}</td><td>{_pc(r["d50"], 0, "")}</td>'
                    f'<td class="{"up" if tr == "Pass" else "dn" if tr == "Fail" else ""}">{tr}</td>'
                    f'<td>{_pill(r["action"], ACT_C.get(r["action"], T3)) if r["action"] else ""}</td></tr>')
    head = ["#", "Sector", "Score", "Rotation map", "vs bench 1M", "3M", "6M", "Above 50-EMA", "Δ 20 sessions", "Trend", "Model"]
    return ('<div class="mf-tw"><table class="mf-t"><thead><tr>' + "".join(f"<th>{h}</th>" for h in head) + "</tr></thead><tbody>"
            + "".join(rows) + "</tbody></table></div>")


def _view_now(E, p):
    H = E["H"]
    snap = _snap(E, p)
    pf = _portfolio(E, p)
    _regime_card(E, p)
    target = snap[snap["target"]]
    last = next((x for x in reversed(pf.get("log", [])) if not x.get("pending")), None) if pf.get("ok") else None
    _h("Model portfolio", f"top {p['n']} by rotation score that pass the trend filter · equal weight · "
       + (f"last review {last['date']:%d %b %Y}" if last else "no review yet"))
    if len(target):
        st.markdown('<div class="mf-cards">' + "".join(_card(r, H["bench_name"]) for r in target.to_dict("records")) + "</div>", unsafe_allow_html=True)
    else:
        st.info("No sector passes the trend filter today: the model would hold cash.")
    ins, outs, watch = snap[snap["action"] == "Rotate in"], snap[snap["action"] == "Rotate out"], snap[snap["action"] == "Watch: improving"]
    if len(ins) or len(outs) or len(watch):
        _h("If the model were reviewed today", "the next scheduled review uses the same rules")
        cols = [("Rotate in", GREEN, "New to the top slots, above trend.", ins), ("Rotate out", RED, "Fell below the rank buffer or the trend filter.", outs),
                ("Watch: improving", CYAN, "Improving quadrant and climbing the ranking, not in the slots yet. The early part of a rotation.", watch)]
        st.markdown('<div class="mf-board">' + "".join(
            f'<div class="mf-col" style="--c:{c}"><h4>{t} · {len(d)}</h4><p>{p_}</p>'
            + ("".join(f'<div class="it"><b>{_e(r["sector"])}</b> · rank {int(r["rank"])} · {_e(r["quadrant"])} · 3M {_pc(r["rel_63"])}</div>' for r in d.head(6).to_dict("records"))
               or '<div class="it">None.</div>') + "</div>" for t, c, p_, d in cols) + "</div>", unsafe_allow_html=True)
    if pf.get("ok") and pf["metrics"]:
        m, bm = pf["metrics"], pf["bench_metrics"]
        kk = ("ic", p["every"])
        if kk not in E["_pf"]:
            E["_pf"][kk] = rot.score_test(H, E["F"], h=p["every"])
        st_ = E["_pf"][kk]
        sc = st_["ic"].set_index("factor") if len(st_["ic"]) else None
        ic = sc.at["score", "mean_ic"] if sc is not None and "score" in sc.index else np.nan
        _note(f'<b>On this history</b> ({len(pf["returns"])} sessions, walk-forward, costs {p["cost"]:.2f}% round trip): the model returned '
              f'<b>{_f(m["cagr"], "+.1f", "%")}</b> a year vs <b>{_f(bm.get("cagr"), "+.1f", "%")}</b> for {_e(H["bench_name"])}, worst fall '
              f'{_f(m["max_dd"], ".1f", "%")} vs {_f(bm.get("max_dd"), ".1f", "%")}. Score IC {_f(ic, "+.3f")} (above +0.05 is useful, near 0 is no edge). '
              'Open <b>Does it work?</b> for the full test before acting on any of this.')
    _h("All sectors", f"ranked by rotation score · green edge = held by the model · vs {H['bench_name']}")
    if _phone():
        st.markdown('<div class="mf-cards">' + "".join(_card(r, H["bench_name"]) for r in snap.head(20).to_dict("records")) + "</div>", unsafe_allow_html=True)
        st.caption("Top 20 shown on a phone. The full table is on the desktop layout and in the download below.")
    else:
        st.markdown(_rank_table(snap, H["bench_name"]), unsafe_allow_html=True)
    st.download_button("Download ranking (CSV)", snap.drop(columns=["held", "target"]).to_csv(index=False), "arka_rotation_ranking.csv", "text/csv", key="mf8_dl_rank")
    _publish(E, snap)


# ═════════════════════════ view · ROTATION MAP ═════════════════════════
def _view_map(E, p):
    F, snap = E["F"], _snap(E, p)
    c1, c2 = st.columns([2, 1])
    show = c1.radio("Show", ["Top 15 by score", "Model holdings + watch", "All sectors"], horizontal=True, key="mf8_map_show")
    tail = c2.selectbox("Tail", [5, 10, 20], index=1, key="mf8_map_tail", format_func=lambda n: f"last {n} sessions")
    if show.startswith("Top"):
        secs = snap.head(15)["sector"].tolist()
    elif show.startswith("Model"):
        secs = snap[snap["target"] | (snap["action"] == "Watch: improving")]["sector"].tolist() or snap.head(8)["sector"].tolist()
    else:
        secs = snap["sector"].tolist()
    _note("<b>How to read it.</b> Right of 100 = outperforming the benchmark (RS-Ratio). Above 100 = that outperformance is accelerating "
          "(RS-Momentum). Sectors usually turn clockwise: <b style='color:#5AC8FA'>Improving</b> → <b style='color:#30D158'>Leading</b> → "
          "<b style='color:#FFD60A'>Weakening</b> → <b style='color:#FF453A'>Lagging</b>. The best time to be in a sector is the move from "
          "Improving into Leading; the time to leave is when it rolls into Weakening and its rank falls. The map shows direction; the score and "
          "trend filter decide.")
    R, M = F["rs_ratio"], F["rs_mom"]
    if go is None:
        st.dataframe(snap[snap["sector"].isin(secs)][["sector", "rs_ratio", "rs_mom", "quadrant"]], hide_index=True, use_container_width=True)
        return
    fig = go.Figure()
    xs = [R[s].iloc[-tail:].dropna() for s in secs]
    ys = [M[s].iloc[-tail:].dropna() for s in secs]
    allx = pd.concat(xs) if xs else pd.Series([100.0])
    ally = pd.concat(ys) if ys else pd.Series([100.0])
    dx = max(abs(allx - 100).max(), 1.5) * 1.15
    dy = max(abs(ally - 100).max(), 1.0) * 1.15
    for x0, x1, y0, y1, c, lab, ax, ay in ((100, 100 + dx, 100, 100 + dy, GREEN, "Leading", 1, 1), (100, 100 + dx, 100 - dy, 100, YELLOW, "Weakening", 1, 0),
                                            (100 - dx, 100, 100 - dy, 100, RED, "Lagging", 0, 0), (100 - dx, 100, 100, 100 + dy, CYAN, "Improving", 0, 1)):
        fig.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1, fillcolor=c, opacity=0.05, line=dict(width=0), layer="below")
        fig.add_annotation(x=x1 if ax else x0, y=y1 if ay else y0, text=lab, showarrow=False, xanchor="right" if ax else "left",
                           yanchor="top" if ay else "bottom", font=dict(color=c, size=13))
    for s, x, y in zip(secs, xs, ys):
        common = x.index.intersection(y.index)
        if not len(common):
            continue
        x, y = x.loc[common], y.loc[common]
        q = F["quadrant"][s].iloc[-1]
        c = QUAD_C.get(q, T3)
        fig.add_trace(go.Scatter(x=x, y=y, mode="lines", line=dict(color=c, width=1.4), opacity=0.55, showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=[x.iloc[-1]], y=[y.iloc[-1]], mode="markers+text", text=[s], textposition="top center", showlegend=False,
                                 textfont=dict(size=11, color="#D4D4D8"), marker=dict(size=11, color=c, line=dict(color="#0B0B0C", width=1.5)),
                                 hovertemplate=f"<b>{_e(s)}</b><br>RS-Ratio %{{x:.2f}}<br>RS-Momentum %{{y:.2f}}<extra></extra>"))
    fig.add_vline(x=100, line_color="#3a3a3e", line_width=1)
    fig.add_hline(y=100, line_color="#3a3a3e", line_width=1)
    _theme(fig, 520 if _phone() else 640)
    fig.update_xaxes(title="RS-Ratio (relative strength)", range=[100 - dx, 100 + dx])
    fig.update_yaxes(title="RS-Momentum (its rate of change)", range=[100 - dy, 100 + dy])
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key="mf8_rrg")
    moved = snap[(snap["quadrant"] != snap["quadrant_10_ago"]) & (snap["quadrant_10_ago"] != "") & (snap["quadrant"] != "")]
    if len(moved):
        _h("Quadrant changes in the last 10 sessions")
        st.markdown('<div class="mf-tw"><table class="mf-t"><thead><tr><th>Rank</th><th>Sector</th><th>From</th><th>To</th><th>Score</th></tr></thead><tbody>'
                    + "".join(f'<tr><td class="n">{int(r["rank"])}</td><td class="n">{_e(r["sector"])}</td><td>{_pill(r["quadrant_10_ago"], QUAD_C.get(r["quadrant_10_ago"], T3))}</td>'
                              f'<td>{_pill(r["quadrant"], QUAD_C.get(r["quadrant"], T3))}</td><td>{_bar(r["rank_pct"])}</td></tr>' for r in moved.to_dict("records"))
                    + "</tbody></table></div>", unsafe_allow_html=True)


# ═════════════════════════ view · SECTOR ═════════════════════════
def _contrib_html(row):
    con = rot.contributions(pd.Series(row))
    out = []
    for k in rot.WEIGHTS:
        z = row.get(k)
        if z is None or pd.isna(z):
            out.append(f'<div class="r"><span>{_e(rot.FACTOR_LABEL[k])}</span><div class="tr"><s></s></div><b style="color:#71717A">n/a</b></div>')
            continue
        v = con[k]
        w = min(50.0, abs(v) / 1.5 * 50)
        side = f"left:50%;width:{w:.0f}%" if v >= 0 else f"right:50%;width:{w:.0f}%"
        out.append(f'<div class="r"><span>{_e(rot.FACTOR_LABEL[k])} <span style="color:#71717A">· z {z:+.2f}</span></span>'
                   f'<div class="tr"><s></s><i style="{side};background:{GREEN if v >= 0 else RED}"></i></div><b>{v:+.2f}</b></div>')
    return '<div class="mf-contrib">' + "".join(out) + "</div>"


def _view_sector(E, p, on_open):
    H, F = E["H"], E["F"]
    snap = _snap(E, p)
    opts = snap["sector"].tolist()
    if st.session_state.get("mf8_pick") not in opts:
        st.session_state["mf8_pick"] = opts[0]
    pick = st.selectbox("Sector", opts, key="mf8_pick", format_func=lambda s: f"{s} · rank {int(snap.set_index('sector').at[s, 'rank'])}")
    r = snap.set_index("sector").loc[pick].to_dict()
    r["sector"] = pick
    rg = E["reg"]["regime"].iloc[-1]
    a, b = (st.container(), st.container()) if _phone() else st.columns([1.05, 1], gap="large")
    with a:
        st.markdown(_card(r, H["bench_name"]), unsafe_allow_html=True)
        _h("Why it ranks here", "every sentence is computed from the numbers")
        st.markdown('<ul class="mf-why">' + "".join(f"<li>{_e(x)}</li>" for x in rot.explain(pd.Series(r), len(snap), H["bench_name"], rg)) + "</ul>",
                    unsafe_allow_html=True)
    with b:
        _h("Score breakdown", f"weight × z-score · {int(r['n_parts'] or 0)} of 5 components available")
        st.markdown(_contrib_html(r), unsafe_allow_html=True)
        st.caption("Each bar is that component's contribution to the final score (weights: momentum 35%, rotation 20%, breadth 20%, consistency 15%, "
                   "activity 10%). z = how many standard deviations the sector is above (+) or below (−) the average sector today.")
    c1, c2 = (st.container(), st.container()) if _phone() else st.columns(2, gap="medium")
    with c1:
        _h("Price vs benchmark", "both rebased to 100 one year ago")
        lv = pd.DataFrame({pick: H["levels"][pick], H["bench_name"]: H["bench"], "200-session average": F["trend_sma"][pick]}).tail(252).dropna(subset=[pick])
        if len(lv):
            b0, s0 = lv[H["bench_name"]].iloc[0], lv[pick].iloc[0]
            lv = pd.DataFrame({pick: lv[pick] / s0 * 100, H["bench_name"]: lv[H["bench_name"]] / b0 * 100, "200-session average": lv["200-session average"] / s0 * 100})
        _line(lv, "mf8_s_lv", [AMBER, "#A1A1AA", "#52525B"], 280, 100)
    with c2:
        _h("Score rank over time", "percentile among all sectors (100 = best)")
        _line(pd.DataFrame({"Score percentile": F["rank_pct"][pick]}).tail(252), "mf8_s_rank", [CYAN], 280, 50)
    c3, c4 = (st.container(), st.container()) if _phone() else st.columns(2, gap="medium")
    with c3:
        _h("Rotation path", "RS-Ratio and RS-Momentum")
        _line(pd.DataFrame({"RS-Ratio": F["rs_ratio"][pick], "RS-Momentum": F["rs_mom"][pick]}).tail(180), "mf8_s_rrg", [AMBER, CYAN], 260, 100)
    with c4:
        _h("Breadth", "% of members above their 20 / 50 / 200-EMA")
        _line(pd.DataFrame({"20-EMA": H["pct20"][pick], "50-EMA": H["pct50"][pick], "200-EMA": H["pct200"][pick]}).tail(250), "mf8_s_br", [CYAN, GREEN, PURPLE], 260)
    mem = E["members"].get(pick, [])
    _h("Strongest stocks inside it", f"{len(mem)} members · sorted by 60-session return vs benchmark")
    if not mem:
        st.info("Constituents are NOT AVAILABLE for this index (no constituent file in data/).")
        return
    stk = report.stock_table(E["close"], E["high"], E["volume"], mem, H["bench"], H["levels"][pick])
    names = {"symbol": "Symbol", "price": "Price ₹", "ret_20": "Return 20d %", "ret_60": "Return 60d %", "rel_bench_60": "vs benchmark 60d pp",
             "rel_sector_60": "vs sector 60d pp", "above_50": "Above 50-EMA", "above_200": "Above 200-EMA", "from_52w_high": "From 52w high %",
             "rel_volume": "Volume × 20d", "avg_value_cr": "Avg value ₹Cr", "setup": "Chart (descriptive)"}
    show = stk[[c for c in names if c in stk.columns]].rename(columns=names)
    num = show.select_dtypes("number").columns
    st.dataframe(show.style.format({c: "{:,.1f}" for c in num}, na_rep="n/a"), use_container_width=True, hide_index=True, height=min(42 + 35 * len(show), 560))
    st.caption("A strong sector does not make every member a good trade: the leaders inside it are usually where the sector's move is concentrated.")
    if on_open and len(stk):
        x, y = st.columns([3, 1])
        sym = x.selectbox("Open a stock", stk["symbol"].tolist(), key="mf8_sym", label_visibility="collapsed")
        if y.button("Open stock", key="mf8_open", use_container_width=True, type="primary"):
            on_open(str(sym))


# ═════════════════════════ view · DOES IT WORK? ═════════════════════════
def _kpis(items):
    return '<div class="mf-kpis">' + "".join(f'<div class="mf-kpi"><small>{a}</small><b>{b}</b><em>{c}</em></div>' for a, b, c in items) + "</div>"


IC_MEANING = {"score": "the full rotation score", "momentum": "risk-adjusted relative momentum", "rotation": "RS-Momentum (rotation speed)",
              "breadth": "breadth level and change", "consistency": "steady vs jumpy relative gains", "activity": "up-day turnover share",
              "momentum_only": "baseline: plain 6-month relative return"}


def _view_proof(E, p):
    H, F = E["H"], E["F"]
    st.warning(("Arka groups use TODAY's member lists for the whole history, so stocks that failed or were delisted are missing and past results "
                "look better than they were. " if E["kind"] == "Arka groups" else
                "NSE index levels have no survivorship bias but there are few of them, so the samples are small. ")
               + "Sector baskets cannot be bought directly. This is evidence about the rules, not a promise about the future.")
    pf = _portfolio(E, p)
    if not pf.get("ok"):
        st.info(f"Not enough history to test the model ({pf.get('why')}). Load 5 years.")
        return
    base = _portfolio(E, p, score_key="momentum_only", use_regime=False)
    noreg = _portfolio(E, p, use_regime=not p["regime"])
    m, bm, em = pf["metrics"], pf["bench_metrics"], pf["ew_metrics"]
    yrs = len(pf["returns"]) / 252
    st.markdown(_kpis([
        ("Model, per year", _f(m["cagr"], "+.1f", "%"), f'{_f(m.get("excess_cagr"), "+.1f")} pts vs {_e(H["bench_name"])}'),
        ("Benchmark, per year", _f(bm.get("cagr"), "+.1f", "%"), f'all sectors equal-weight {_f(em.get("cagr"), "+.1f", "%")}'),
        ("Worst fall", _f(m["max_dd"], ".1f", "%"), f'benchmark {_f(bm.get("max_dd"), ".1f", "%")}'),
        ("Sharpe (no risk-free)", _f(m["sharpe"], ".2f"), f'benchmark {_f(bm.get("sharpe"), ".2f")}'),
        ("Months beating benchmark", _f(m.get("months_beat"), ".0f", "%"), f'information ratio {_f(m.get("info_ratio"), ".2f")}'),
        ("Turnover", _f(pf["turnover_per_year"], ".1f", "×/yr"), f'{pf["reviews"]} reviews over {yrs:.1f} years')]), unsafe_allow_html=True)
    _h("Growth of ₹100", f"walk-forward · signal at the close, trade next close · {p['cost']:.2f}% round-trip cost on every change")
    curves = pd.DataFrame({"Model": (1 + pf["returns"]).cumprod() * 100, H["bench_name"]: (1 + pf["bench_returns"]).cumprod() * 100,
                           "All sectors equal-weight": (1 + pf["ew_returns"]).cumprod() * 100})
    if base.get("ok"):
        curves["Plain 6-month momentum"] = (1 + base["returns"].reindex(curves.index).fillna(0)).cumprod() * 100
    _line(curves, "mf8_eq", [AMBER, "#D4D4D8", "#71717A", CYAN], 360, 100)
    alt = [("Model (these settings)", pf), ("Plain 6-month momentum, no filters", base),
           (f"Model with regime filter {'off' if p['regime'] else 'on'}", noreg)]
    st.markdown('<div class="mf-tw"><table class="mf-t"><thead><tr><th>Variant</th><th>Per year</th><th>vs benchmark</th><th>Worst fall</th><th>Sharpe</th><th>Months beating</th></tr></thead><tbody>'
                + "".join(f'<tr><td class="n">{_e(n)}</td><td>{_pc(x["metrics"].get("cagr"))}</td><td>{_pc(x["metrics"].get("excess_cagr"), 1, " pts")}</td>'
                          f'<td>{_f(x["metrics"].get("max_dd"), ".1f", "%")}</td><td>{_f(x["metrics"].get("sharpe"), ".2f")}</td><td>{_f(x["metrics"].get("months_beat"), ".0f", "%")}</td></tr>'
                          for n, x in alt if x.get("ok") and x["metrics"]) + "</tbody></table></div>", unsafe_allow_html=True)
    st.caption("If the plain 6-month ranking does as well as the model, the extra components add nothing on this data and the simpler rule is the better one.")

    h = p["every"]
    if ("ic", h) not in E["_pf"]:
        E["_pf"][("ic", h)] = rot.score_test(H, F, h=h)
    stt = E["_pf"][("ic", h)]
    _h("Does the score predict the next move?", f"rank correlation (IC) between each date's score and the NEXT {h} sessions' return vs benchmark")
    if len(stt["ic"]):
        ic = stt["ic"].copy()
        st.markdown('<div class="mf-tw"><table class="mf-t"><thead><tr><th>Component</th><th>What it measures</th><th>Mean IC</th><th>t-stat</th><th>IC above 0</th><th>Dates</th></tr></thead><tbody>'
                    + "".join(f'<tr><td class="n">{_e(rot.FACTOR_LABEL.get(x["factor"], "Rotation score" if x["factor"] == "score" else "6-month momentum"))}</td>'
                              f'<td style="text-align:left">{_e(IC_MEANING.get(x["factor"], ""))}</td><td>{_pc(x["mean_ic"], 3, "")}</td><td>{_f(x["t_stat"], ".1f")}</td>'
                              f'<td>{_f(x["pct_positive"], ".0f", "%")}</td><td>{int(x["dates"])}</td></tr>' for x in ic.to_dict("records"))
                    + "</tbody></table></div>", unsafe_allow_html=True)
        st.caption("IC +0.05 or more with a t-stat above 2 is a useful signal; around 0 means no measurable edge on this history. Sectors move together, "
                   "so the t-stat overstates certainty a little.")
    q = stt["quintiles"]
    if q["mean_excess"].notna().any():
        _h("Top fifth vs bottom fifth", f"average return vs benchmark over the next {h} sessions, by score quintile")
        if go is not None:
            fig = go.Figure(go.Bar(x=q["quintile"], y=q["mean_excess"], marker_color=[RED, "#E07A5F", "#71717A", "#8AD66B", GREEN],
                                   text=[_f(v, "+.2f", " pp") for v in q["mean_excess"]], textposition="outside"))
            _theme(fig, 300)
            fig.update_yaxes(title="pp vs benchmark")
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key="mf8_quint")
        else:
            st.bar_chart(q.set_index("quintile")["mean_excess"])
        st.caption(f"Top minus bottom: {_f(stt['spread_mean'], '+.2f')} pp per {h} sessions, positive on {_f(stt['spread_beat'], '.0f')}% of {stt['dates']} dates. "
                   "A useful score shows the bars rising from bottom to top.")

    _h("Stability", "the same test on the first 60% and the last 40% of the history")
    idx = F["score"].dropna(how="all").index
    if len(idx) > 120:
        cut = idx[int(len(idx) * 0.6)]
        rows = []
        for lab, kw in ((f"First 60% (to {cut:%b %Y})", {"end": cut}), (f"Last 40% (from {cut:%b %Y})", {"start": cut})):
            x = _portfolio(E, p, start=kw.get("start"), end=kw.get("end"))
            t = rot.score_test(H, F, h=h, keys=["score"], **kw)
            icv = t["ic"]["mean_ic"].iloc[0] if len(t["ic"]) else np.nan
            mm = x.get("metrics") or {}
            rows.append(f'<tr><td class="n">{_e(lab)}</td><td>{_pc(mm.get("cagr"))}</td><td>{_pc(mm.get("excess_cagr"), 1, " pts")}</td>'
                        f'<td>{_f(mm.get("max_dd"), ".1f", "%")}</td><td>{_pc(icv, 3, "")}</td></tr>')
        st.markdown('<div class="mf-tw"><table class="mf-t"><thead><tr><th>Period</th><th>Per year</th><th>vs benchmark</th><th>Worst fall</th><th>Score IC</th></tr></thead><tbody>'
                    + "".join(rows) + "</tbody></table></div>", unsafe_allow_html=True)
        st.caption("Nothing was tuned on either half. A result that flips sign between halves is not reliable.")
    _h("Review log", "the last 12 reviews: what the model held and what it changed")
    log = [x for x in pf["log"]][-12:][::-1]
    st.markdown('<div class="mf-tw"><table class="mf-t"><thead><tr><th>Review</th><th>Regime</th><th>Slots</th><th>In</th><th>Out</th><th>Held after</th></tr></thead><tbody>'
                + "".join(f'<tr><td class="n">{x["date"]:%d %b %Y}{" (pending)" if x.get("pending") else ""}</td><td>{_e(x.get("regime") or "—")}</td><td>{x["slots"]}</td>'
                          f'<td class="up" style="text-align:left;white-space:normal">{_e(", ".join(x["in"]) or "—")}</td>'
                          f'<td class="dn" style="text-align:left;white-space:normal">{_e(", ".join(x["out"]) or "—")}</td>'
                          f'<td style="text-align:left;white-space:normal">{_e(", ".join(x["holdings"]) or "cash")}</td></tr>' for x in log)
                + "</tbody></table></div>", unsafe_allow_html=True)
    if pf.get("missing_bar_days"):
        st.caption(f"{pf['missing_bar_days']} holding-days had no price bar and were counted as 0% for that day.")


# ═════════════════════════ view · BREADTH & ACTIVITY / RULE STATES (v7) ═════════════════════════
COLS = {"sector": "Sector", "state": "State", "n_bar": "Stocks", "pct20": "% above 20-EMA", "d20_stocks_10": "Stocks above 20-EMA, Δ 10d",
        "pct50": "% above 50-EMA", "d50_stocks_20": "Stocks above 50-EMA, Δ 20d", "pct200": "% above 200-EMA", "adv": "Advancing", "dec": "Declining",
        "new_high20": "New 20d highs", "new_low20": "New 20d lows", "breakout20": "Breakouts", "coverage_status": "Data",
        "traded_value_cr": "Traded value ₹Cr", "rel_activity": "Activity × 20d", "turnover_accel": "Turnover 5d / 20d",
        "up_share5": "Up-close turnover 5d %", "down_share5": "Down-close turnover 5d %", "abs_5": "Return 5d %", "rel_20": "vs bench 20d pp"}


def _table(tbl, keys, key, sort=None):
    df = tbl[keys].rename(columns=COLS)
    if sort:
        df = df.sort_values(COLS[sort], ascending=False, na_position="last")
    num = df.select_dtypes("number").columns
    st.dataframe(df.style.format({c: "{:,.1f}" for c in num}, na_rep="n/a"), use_container_width=True, hide_index=True,
                 height=min(42 + 35 * len(df), 760), key=key)


def _view_breadth(E):
    _h("Participation by sector", "level and direction are separate columns")
    _table(E["table"], ["sector", "n_bar", "pct20", "d20_stocks_10", "pct50", "d50_stocks_20", "pct200", "adv", "dec", "new_high20", "new_low20",
                        "breakout20", "coverage_status"], "mf8_br", "pct50")
    _h("Whole market", "% of all loaded stocks above their 20 / 50 / 200-EMA")
    c = E["close"]
    u = pd.DataFrame({f"{n}-EMA": ((c > c.ewm(span=n, adjust=False, min_periods=n).mean()) & c.notna()).sum(axis=1)
                      / c.notna().sum(axis=1).replace(0, np.nan) * 100 for n in (20, 50, 200)}).tail(250)
    _line(u, "mf8_br_mkt", [CYAN, GREEN, PURPLE], 280, 50)
    _h("Trading activity", f"traded value basis: {E['H']['value_basis']}")
    _table(E["table"], ["sector", "traded_value_cr", "rel_activity", "turnover_accel", "up_share5", "down_share5", "abs_5", "rel_20"], "mf8_act", "rel_activity")
    st.caption("Activity shows how much traded and on what kind of day. It does not show who bought or sold.")


def _view_states(E):
    tbl = E["table"]
    _note("These are v7's <b>rule states</b>: a description of each sector from written rules (arka_flow/state.py). They are kept for reference. "
          "The rotation model on <b>Where to be</b> is what ranks sectors and is tested on <b>Does it work?</b>.")
    help_ = {"EARLY IMPROVEMENT": "Behind over 60 sessions; relative strength has turned up.", "PARTICIPATION CONFIRMATION": "Turning up with more stocks joining.",
             "LEADERSHIP": "Ahead over 60 sessions, above its 50-EMA.", "MATURE LEADERSHIP": "Still leading, but extended or slowing.",
             "RECOVERY": "Price trend damaged; relative strength stabilising.", "DETERIORATION": "Relative strength falling, confirmed by breadth or price."}
    cols = []
    for s in BOARD_ORDER:
        rows = tbl[tbl.state == s].sort_values("pctile_20", ascending=False).to_dict("records")
        items = "".join(f'<div class="it"><b>{_e(r["sector"])}</b> · RS 60d {_pc(r["rel_60"], 1, " pp")} · {_f(r["pct50"], ".0f")}% above 50-EMA · '
                        f'{_e(r["confidence"])}</div>' for r in rows[:8]) or '<div class="it">None.</div>'
        cols.append(f'<div class="mf-col" style="--c:{STATE_C[s]}"><h4>{s.capitalize()} · {len(rows)}</h4><p>{help_[s]}</p>{items}</div>')
    st.markdown('<div class="mf-board">' + "".join(cols) + "</div>", unsafe_allow_html=True)
    with st.expander("How states are decided"):
        st.code(rstate.__doc__)


# ═════════════════════════ view · FPI ═════════════════════════
@st.cache_data(ttl=12 * 3600, show_spinner=False, max_entries=64)
def _nsdl_fetch(d: str):
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})
    for url in fpi.nsdl_urls(pd.Timestamp(d)):
        try:
            r = sess.get(url, timeout=12)
            if r.status_code == 200 and "Sector" in r.text:
                p = fpi.parse_nsdl_sector_html(r.text)
                if not p.empty:
                    return p
        except requests.RequestException:
            continue
    return None


def _view_fpi():
    _note("NSDL's fortnightly report of <b>foreign portfolio investor</b> net investment by sector: recorded money, but FPIs only, NSDL's own sector list, "
          "and published after the fortnight ends. Shown as context; <b>not used in the score or the test</b>.")
    c1, c2 = st.columns([1, 3])
    n = c1.selectbox("Fortnights", [12, 24, 48], index=1, key="mf8_fpi_n")
    with c2:
        st.write("")
        if st.button("Load FPI sector flows", type="primary", key="mf8_fpi_load"):
            dates = [str(d)[:10] for d in fpi.nsdl_report_dates(int(n))]
            with st.spinner("Downloading NSDL fortnightly reports…"):
                with ThreadPoolExecutor(max_workers=4) as pool:
                    got = list(pool.map(_nsdl_fetch, dates))
            st.session_state["mf8_fpi"] = (fpi.nsdl_panels(got), sum(g is not None for g in got), len(dates))
    got = st.session_state.get("mf8_fpi")
    if not got:
        return
    (net, auc), ok, tried = got
    if net.empty:
        st.warning(f"FPI data NOT AVAILABLE: no NSDL report could be read ({ok} of {tried}).")
        return
    age = (pd.Timestamp.today().normalize() - net.index[-1]).days
    st.caption(f"{ok} of {tried} reports loaded · latest fortnight ended {str(net.index[-1])[:10]} ({age} days ago) · ₹ crore, equity.")
    rows = []
    for s in net.columns:
        x = net[s].dropna()
        streak = 0
        for v in x.iloc[::-1]:
            if streak >= 0 and v > 0:
                streak += 1
            elif streak <= 0 and v < 0:
                streak -= 1
            else:
                break
        a = auc[s].dropna() if s in auc else pd.Series(dtype=float)
        aum = float(a.iloc[-1]) if len(a) else np.nan
        rows.append({"NSDL sector": s, "Last fortnight ₹Cr": float(x.iloc[-1]) if len(x) else np.nan, "Last 6 ₹Cr": x.tail(6).sum(),
                     "Last 12 ₹Cr": x.tail(12).sum(), "Last 6 as % of FPI holdings": x.tail(6).sum() / aum * 100 if aum and aum > 0 else np.nan,
                     "Fortnights in a row (+ in / − out)": streak, "FPI holdings ₹Cr": aum})
    tb = pd.DataFrame(rows).sort_values("Last 6 as % of FPI holdings", ascending=False, na_position="last")
    st.dataframe(tb.style.format({c: "{:+,.0f}" for c in tb.columns if "₹Cr" in c and "holdings" not in c}
                                 | {"Last 6 as % of FPI holdings": "{:+.2f}", "FPI holdings ₹Cr": "{:,.0f}"}, na_rep="n/a"),
                 use_container_width=True, hide_index=True, height=min(42 + 35 * len(tb), 760))


# ═════════════════════════ view · METHOD & DATA ═════════════════════════
def _view_method(E, source):
    H, q, tbl = E["H"], E["q"], E["table"]
    _h("How the rotation model works")
    st.markdown("""
**1 · Score every sector against every other sector, every day.** Five components, each a z-score across sectors on that date (so a sector is only ever compared with its peers on the same day), combined with weights fixed in advance:

| Component | Weight | What it is | Why it is there |
|---|---|---|---|
| Relative momentum | 35% | Sector ÷ benchmark change over 3, 6 and 12 months, each divided by its volatility | Industry momentum is one of the most replicated effects in equities; NSE's own momentum indices rank on 6 and 12-month return ÷ volatility |
| Rotation speed | 20% | RS-Momentum from the Relative Rotation Graph | Catches relative strength that is *accelerating*, the early part of a rotation |
| Breadth | 20% | % of members above their 50-EMA and its 20-session change | A move carried by many stocks lasts longer than one carried by a few |
| Trend consistency | 15% | Up days minus down days in relative return, 6 months | Steady gains persist longer than jumpy ones ("frog in the pan") |
| Up-day turnover | 10% | Share of traded value in stocks that closed up, 5 sessions | Where the day's money was traded |

**2 · Filter.** A sector below its 200-session average gets no slot, however high it ranks (trend rule). When the market is Risk-off or in Stress, only half the slots are filled (momentum's worst losses happen in panic markets).

**3 · Hold the top N, change slowly.** Reviewed every K sessions. A held sector stays while its rank is within N + N (the buffer), so a sector hovering around #5 is not traded every review.

**4 · Prove it.** *Does it work?* replays all of the above walk-forward on the loaded history, with costs, against the benchmark, all sectors equal-weight, and plain 6-month momentum, and reports the information coefficient and quintile spread for every component.
""")
    _h("Sources")
    st.markdown('<div class="mf-note mf-src">' + "<br>".join(f'<a href="{u}" target="_blank">{_e(t)}</a>' for t, u in SOURCES) + "</div>", unsafe_allow_html=True)
    with st.expander("Full rules (arka_flow/rotation.py)"):
        st.code(rot.__doc__)
    _h("What was loaded")
    st.markdown(
        f"- Source: **{q['source']}** · prices {q['adjusted']} · history {q['period']} · loaded {q['loaded_at']}\n"
        f"- Universe: `{source}` · requested **{q['requested']}**, received **{q['loaded']}**, used **{q['used']}**\n"
        f"- Last completed session **{q['session']['last_complete_session']}** · holiday calendar: {q['session']['holiday_calendar']}\n"
        f"- Excluded as illiquid (median value under ₹{q['min_turn']:g} Cr/day): **{len(q['illiquid'])}** · stale tickers: **{len(q['stale'])}**\n"
        f"- Groups with fewer than 3 priced members: {', '.join(q['small_groups']) or 'none'} · NSE index series missing: {', '.join(q['index_missing']) or 'none'}\n"
        f"- Model versions: rotation {rot.ROT_VERSION} · engine {MODEL_VERSION}")
    cov = tbl[["sector", "members", "with_prices", "coverage", "coverage_status"]].copy()
    cov["coverage"] = cov["coverage"] * 100
    st.dataframe(cov.rename(columns={"sector": "Sector", "members": "Members", "with_prices": "With prices", "coverage": "Coverage %", "coverage_status": "Status"})
                 .sort_values("Coverage %").style.format({"Coverage %": "{:.0f}"}, na_rep="n/a"), use_container_width=True, hide_index=True, height=300)
    st.caption(f"A sector with under {MIN_SECTOR_COVERAGE * 100:.0f}% of members priced is marked INCOMPLETE.")
    if q["failures"]:
        with st.expander(f"Tickers with no data · {len(q['failures'])}"):
            st.dataframe(pd.DataFrame([{"Ticker": k, "Reason": v} for k, v in q["failures"].items()]), use_container_width=True, hide_index=True)
    if q["jumps"]:
        with st.expander(f"One-session moves above 25% · {len(q['jumps'])} (check for unadjusted splits)"):
            st.dataframe(pd.DataFrame(q["jumps"]), use_container_width=True, hide_index=True)


# ═════════════════════════ page ═════════════════════════
def _universe_missing():
    st.warning("The stock universe could not be loaded: NSE and niftyindices.com are refusing this server and no copy is in the repo yet.")
    _note('<b>Fix:</b> download <a href="https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv" target="_blank">ind_nifty500list.csv</a> and '
          '<a href="https://www.niftyindices.com/IndexConstituent/ind_niftymicrocap250_list.csv" target="_blank">ind_niftymicrocap250_list.csv</a> '
          "and put them in a <code>data</code> folder next to the app.")
    if st.button("Retry now", key="mf8_retry"):
        _ARCH_DOWN.clear()
        _universe_cached.clear()
        st.rerun()


def _settings(E):
    n_sec = len(E["H"]["sectors"]) if E else 50
    default_n = 5 if n_sec >= 30 else 4 if n_sec >= 15 else 3
    st.session_state.setdefault("mf8_n", default_n)
    st.session_state.setdefault("mf8_every", 21)
    st.session_state.setdefault("mf8_cost", 0.30)
    st.session_state.setdefault("mf8_regime", True)
    with st.expander("Model settings"):
        a, b, c, d = st.columns(4)
        a.selectbox("Sectors to hold", [2, 3, 4, 5, 6, 8, 10], key="mf8_n")
        b.selectbox("Review every", [10, 21, 42, 63], key="mf8_every", format_func=lambda k: f"{k} sessions" + (" (monthly)" if k == 21 else " (fortnightly)" if k == 10 else ""))
        c.number_input("Round-trip cost %", 0.0, 2.0, step=0.05, key="mf8_cost", help="Brokerage + STT + stamp + slippage, both sides. Check your contract notes.")
        d.toggle("Regime filter", key="mf8_regime", help="Fill only half the slots when the market is Risk-off or in Stress.")
        st.caption("Defaults: review monthly (the holding period used in the industry-momentum research) and hold roughly the top tenth of sectors. "
                   "Changing a setting changes both the model and its test on Does it work?.")


def render_money_flow(on_open=None):
    """Arka MONEY FLOW workspace."""
    st.markdown(CSS, unsafe_allow_html=True)
    if st.session_state.get("mf8_view") not in VIEWS:
        st.session_state["mf8_view"] = VIEWS[0]
    view = st.radio("Money flow view", VIEWS, horizontal=True, key="mf8_view", label_visibility="collapsed")
    if view == "FPI flows":
        return _view_fpi()
    try:
        meta, source = _universe_cached()
    except Exception:
        return _universe_missing()
    ready = st.session_state.get("mf8_ready", False)
    if _phone():
        load = st.button("Refresh data" if ready else "Load sector data", type="primary", use_container_width=True, key="mf8_load")
        with st.expander("Data options"):
            kind = st.radio("Sectors", list(KINDS), horizontal=True, key="mf8_kind", help=" ".join(f"{k}: {v}" for k, v in KINDS.items()))
            bench = st.selectbox("Compare against", ["NIFTY 500", "NIFTY 50", EW_MARKET], key="mf8_bench")
            period = st.selectbox("History", ["5y", "3y", "2y"], key="mf8_period")
            min_turn = st.selectbox("Min value / day", [0.5, 0.25, 1.0, 2.0, 5.0], key="mf8_minturn", format_func=lambda v: f"₹{v:g} Cr")
    else:
        c1, c2, c3, c4, c5 = st.columns([1.2, 1.5, 1.6, 0.9, 1], gap="small")
        with c1:
            st.write("")
            load = st.button("Refresh data" if ready else "Load sector data", type="primary", use_container_width=True, key="mf8_load")
        kind = c2.radio("Sectors", list(KINDS), horizontal=True, key="mf8_kind", help=" ".join(f"{k}: {v}" for k, v in KINDS.items()))
        bench = c3.selectbox("Compare against", ["NIFTY 500", "NIFTY 50", EW_MARKET], key="mf8_bench")
        period = c4.selectbox("History", ["5y", "3y", "2y"], key="mf8_period", help="5 years gives the model test a far better sample.")
        min_turn = c5.selectbox("Min value / day", [0.5, 0.25, 1.0, 2.0, 5.0], key="mf8_minturn", format_func=lambda v: f"₹{v:g} Cr")
    if load:
        if ready:
            _load.clear()
            _engine.clear()
        st.session_state["mf8_ready"] = ready = True
    if not ready:
        _note(f"Press <b>Load sector data</b>. Arka downloads daily prices for <b>{len(meta)}</b> stocks ({_e(source)}) and the NSE indices, scores every "
              "sector, builds the model portfolio and tests it on the history. The first load takes a few minutes, then it is cached for 3 hours. "
              "5 years of history is recommended so the test has enough data.")
        return
    with st.spinner(f"Loading {len(meta)} stocks, scoring sectors and testing the model…"):
        try:
            E = _engine(tuple(meta["symbol"]), period, kind, bench, float(min_turn))
        except Exception as exc:
            st.error(f"Sector data unavailable: {type(exc).__name__}: {exc}")
            st.caption("If Yahoo is rate-limiting, wait a minute and press Refresh data. Nothing is shown from partial or substituted data.")
            return
    _settings(E)
    p = _params(E)
    _status(E, p)
    if view == "Where to be":
        _view_now(E, p)
    elif view == "Rotation map":
        _view_map(E, p)
    elif view == "Sector":
        _view_sector(E, p, on_open)
    elif view == "Does it work?":
        _view_proof(E, p)
    elif view == "Breadth & activity":
        _view_breadth(E)
    elif view == "Rule states":
        _view_states(E)
    else:
        _view_method(E, source)
    if view != "Where to be":
        _publish(E, _snap(E, p))
    st.caption("Research tool built on daily price, breadth and traded value. Rankings and tests describe the past; they are not forecasts or investment advice.")

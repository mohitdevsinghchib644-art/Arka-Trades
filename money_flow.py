"""
money_flow.py - Arka MONEY FLOW v7 · Sector Rotation Research

Drop-in replacement for the v6 page. Same entry point: render_money_flow(on_open=None).
All numbers come from the arka_flow package (pure Python, unit-tested). This file only loads data and draws.

What changed vs v6
  * One engine for the screen and for the signal tests (v6 showed labels from one formula and tested another).
  * No BUY ZONE / HOLD / AVOID / EXIT board. Each sector gets a rotation STATE decided by written rules
    (arka_flow/state.py), with supporting, contradicting and missing evidence, and a contradiction panel.
  * A sector that is ahead over 60 sessions and above its 50-EMA is LEADERSHIP or MATURE LEADERSHIP. It cannot be
    called deteriorating because its pace slowed. The weekly RRG quadrant no longer decides anything.
  * Only completed sessions are used. A part-day bar is dropped, so labels do not repaint intraday.
  * Failed downloads are listed by ticker and by sector. Nothing is skipped silently.
  * SIGNAL TESTS shows what followed each state on the loaded history (next-session entry).
  * Names: "up-close turnover share", "relative activity". Nothing here is called buying, accumulation or distribution.

Views: OVERVIEW · SECTOR DETAIL · RANKING · BREADTH · ACTIVITY · SIGNAL TESTS · FPI FLOWS · DATA & METHOD

Data: NSE constituent CSVs for the universe (data/ folder or NSE archives), Yahoo Finance daily bars, NSDL for FPI.
Research evidence from price, breadth and traded value. Not order-flow data, not forecasts, not investment advice.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import streamlit as st

import mf_ui as ui
import sector_map as smap
from arka_flow import MODEL_VERSION, backtest as bt, fpi, history, loader, report, state as rstate, universe
from arka_flow.calc import relative_strength as rs
from arka_flow.config import HORIZONS, MIN_SECTOR_COVERAGE
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
EW_MARKET = "EQUAL-WEIGHT MARKET"
INDEX_REG = {**universe.OFFICIAL_SECTOR_INDICES, **universe.THEMATIC_INDICES}
VIEWS = ["OVERVIEW", "SECTOR DETAIL", "RANKING", "BREADTH", "ACTIVITY", "SIGNAL TESTS", "FPI FLOWS", "DATA & METHOD"]
KINDS = {"ARKA GROUPS": "Arka's rotation groups from sector_map.py: equal-weight baskets of today's members.",
         "NSE INDICES": "Official NSE sector indices: the index level itself. Breadth needs the index's constituent file in data/."}

GREEN, RED, AMBER, CYAN, YELLOW, PURPLE, T2, T3 = ui.GREEN, ui.RED, ui.AMBER, ui.CYAN, ui.YELLOW, ui.PURPLE, ui.T2, ui.T3
STATE_C = {"LEADERSHIP": GREEN, "MATURE LEADERSHIP": YELLOW, "PARTICIPATION CONFIRMATION": "#8AD66B", "EARLY IMPROVEMENT": CYAN,
           "RECOVERY": PURPLE, "DETERIORATION": RED, rstate.NONE: T2, rstate.NOHIST: T3}
STATE_HELP = {"EARLY IMPROVEMENT": "Still behind over 60 sessions; relative strength has turned up. Early, unconfirmed.",
              "PARTICIPATION CONFIRMATION": "Turning up with more stocks joining and 20-session relative return positive.",
              "LEADERSHIP": "Ahead over 60 sessions, above its 50-EMA, relative-strength trend not down.",
              "MATURE LEADERSHIP": "Still a leader, but two or more of: extended, slowing, breadth falling, 20-session RS negative.",
              "DETERIORATION": "20-session RS negative, RS trend down, confirmed by breadth or price.",
              "RECOVERY": "Price trend still damaged; relative strength and breadth are stabilising."}
BOARD_ORDER = ("EARLY IMPROVEMENT", "PARTICIPATION CONFIRMATION", "LEADERSHIP", "MATURE LEADERSHIP", "RECOVERY", "DETERIORATION")
_ARCH_DOWN: dict = {}
_e = ui.esc


# ═════════════════════════ style + helpers ═════════════════════════
def _style():
    ui.inject()
    st.markdown("""<style>
    .arka-mf-section{font:700 11px 'JetBrains Mono',monospace;letter-spacing:1.2px;color:#d6d6d6;margin:24px 0 10px;padding-bottom:8px;border-bottom:1px solid #262626;text-transform:uppercase}
    .arka-mf-note{font:12px/1.75 'Inter',sans-serif;color:#9a9a9a;border-left:2px solid #3a3a3a;padding:8px 12px;background:rgba(255,255,255,.02);margin:8px 0 14px}
    .arka-mf-note b{color:#e2e2e2}
    .rd-status{display:flex;flex-wrap:wrap;gap:6px 18px;font:600 11px 'JetBrains Mono',monospace;color:#8a8a8a;padding:8px 2px;border-bottom:1px solid #1f1f1f;margin-bottom:6px}
    .rd-status b{color:#e6e6e6}
    .sr-board{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:6px 0 10px}
    @media(max-width:1150px){.sr-board{grid-template-columns:1fr}}
    .sr-col{background:#080808;border:1px solid #1f1f1f;border-top:3px solid var(--c);padding:12px 12px 6px;min-width:0}
    .sr-col h4{font:800 12px 'JetBrains Mono',monospace;letter-spacing:1.2px;color:var(--c);margin:0}
    .sr-col p{font:11.5px 'Inter',sans-serif;color:#8a8a8a;margin:4px 0 10px}
    .sr-card{border:1px solid #242424;border-left:3px solid var(--c);background:rgba(255,255,255,.02);padding:8px 10px;margin-bottom:8px;border-radius:3px}
    .sr-card .n{font:700 13px 'Inter',sans-serif;color:#f2f2f2;display:flex;justify-content:space-between;gap:8px}
    .sr-card .n span{font:600 10px 'JetBrains Mono',monospace;color:#8a8a8a}
    .sr-card .m{font:11px/1.6 'JetBrains Mono',monospace;color:#a0a0a0;margin-top:4px}
    .sr-card .w{font:11px/1.5 'Inter',sans-serif;color:#e0ad4c;margin-top:4px}
    .sr-empty{font:12px 'Inter',sans-serif;color:#666;padding:2px 0 10px}
    .sr-pill{display:inline-block;padding:2px 9px;border-radius:999px;font:800 10px 'JetBrains Mono',monospace;letter-spacing:.7px;border:1px solid currentColor}
    </style>""", unsafe_allow_html=True)


def _h(t):
    st.markdown(f'<div class="arka-mf-section">{t}</div>', unsafe_allow_html=True)


def _note(t):
    st.markdown(f'<div class="arka-mf-note">{t}</div>', unsafe_allow_html=True)


def _f(v, fmt="+.1f", suf=""):
    try:
        if v is None or pd.isna(v):
            return "n/a"
        return format(float(v), fmt) + suf
    except (TypeError, ValueError):
        return "n/a"


def _pill(text):
    return f'<span class="sr-pill" style="color:{STATE_C.get(text, T2)}">{_e(text)}</span>'


def _line(df: pd.DataFrame, key: str, colors=None, h=300, hline=None):
    """Line chart: plotly when available, otherwise Streamlit's own. A chart error never breaks the page."""
    if df is None or df.dropna(how="all").empty:
        st.caption("Not enough history for this chart.")
        return
    if go is not None:
        try:
            fig = go.Figure()
            for i, c in enumerate(df.columns):
                col = (colors or [AMBER, "#D0D0D0", CYAN, PURPLE, GREEN])[i % 5]
                fig.add_trace(go.Scatter(x=df.index, y=df[c], name=str(c), mode="lines", line=dict(color=col, width=2 if i == 0 else 1.3)))
            if hline is not None:
                fig.add_hline(y=hline, line_color="#444", line_width=1, line_dash="dot")
            ui._dark(fig, h)
            fig.update_layout(hovermode="x unified")
            fig.update_xaxes(showgrid=False)
            fig.update_yaxes(gridcolor="#161616", zeroline=False)
            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=key)
            return
        except Exception:
            pass
    st.line_chart(df)


# ═════════════════════════ universe (unchanged logic from v6) ═════════════════════════
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
    if kind == "ARKA GROUPS":
        known = grp[grp != "Unclassified"]
        members = {g: sorted(s.index) for g, s in known.groupby(known) if len(s)}
        small = sorted(g for g, m in members.items() if sum(x in close.columns for x in m) < 3)
        members = {g: m for g, m in members.items() if g not in small}
        levels, level_src = None, "equal-weight basket of today's members (Yahoo adjusted closes)"
    else:
        names = {t: n for n, t, _ in INDEX_REG.values() if t}
        levels = index[[t for t in names if t in index and index[t].notna().sum() >= 120]].rename(columns=names)
        if levels.shape[1] < 3:
            raise RuntimeError("fewer than 3 NSE sector indices returned history from Yahoo")
        members = {k: v for k, v in _index_members().items() if k in levels.columns}
        level_src = "official index close from Yahoo (price index, no dividends)"
    H = history.build(close, volume, members, benches[bench_name], levels=levels)
    H["bench_name"] = bench_name
    cl = rstate.classify(H)
    return {"H": H, "cl": cl, "table": report.sector_table(H, cl), "close": close, "high": high, "volume": volume,
            "benches": benches, "members": members, "kind": kind, "level_src": level_src,
            "q": {"dropped_sessions": dropped, "session": ss, "illiquid": illiquid, "small_groups": small,
                  "failures": P["failures"], "stale": P["stale"], "jumps": P["jumps"], "requested": P["requested"],
                  "loaded": int(P["close"].shape[1]), "used": int(close.shape[1]), "loaded_at": P["loaded_at"],
                  "source": P["source"], "adjusted": P["adjusted"], "period": period, "min_turn": min_turn,
                  "duplicates_dropped": P["duplicates_dropped"], "non_positive": P["non_positive"],
                  "index_missing": sorted(n for n, t, _ in INDEX_REG.values() if t and t not in index)}}


def _publish(E):
    """Snapshot for HOME's rotation panel (same keys as v6: action = state, phase = RS trend, score = RS percentile)."""
    t = E["table"]
    st.session_state["mf_rot"] = {"asof": E["H"]["asof"], "bench": E["H"]["bench_name"], "table": pd.DataFrame({
        "sector": t["sector"], "action": t["state"], "phase": t["rs_trend"].fillna("—").astype(str).str.upper(),
        "score": t["pctile_20"], "ex1m": t["rel_20"], "r1w": t["abs_5"]})}
    st.session_state["mf_broad_rs"] = {}


def _status(E):
    H, q = E["H"], E["q"]
    n_fail = len(q["failures"])
    st.markdown(f'<div class="rd-status"><span>AS OF <b>{H["asof"]}</b> close (completed sessions only)</span>'
                f'<span><b>{len(H["sectors"])}</b> sectors · {_e(E["kind"])}</span><span>vs <b>{_e(H["bench_name"])}</b></span>'
                f'<span><b>{q["used"]}</b> stocks used of {q["requested"]}</span><span>loaded {q["loaded_at"][11:16]} IST</span>'
                f'<span>model {MODEL_VERSION}</span></div>', unsafe_allow_html=True)
    if q["dropped_sessions"]:
        st.caption(f"Today's unfinished session ({', '.join(q['dropped_sessions'])}) is excluded so that labels do not change intraday.")
    stale_by = val.stale_sessions(H["asof"], q["session"]["last_complete_session"])
    if stale_by:
        st.warning(f"Latest data is {stale_by} session(s) behind the last completed session ({q['session']['last_complete_session']}). "
                   "Readings below describe the older date.")
    if n_fail:
        st.warning(f"{n_fail} ticker(s) returned no data from Yahoo. Affected sectors are marked INCOMPLETE. See DATA & METHOD.")


# ═════════════════════════ view · OVERVIEW ═════════════════════════
def _card(r) -> str:
    c = STATE_C.get(r["state"], T3)
    warn = ""
    if r["coverage_status"] == "INCOMPLETE":
        warn += f'<div class="w">INCOMPLETE: {r["with_prices"]} of {r["members"]} members have prices</div>'
    for x in (r["contradictions"] or [])[:2]:
        warn += f'<div class="w">{_e(x["name"])}</div>'
    return (f'<div class="sr-card" style="--c:{c}"><div class="n">{_e(r["sector"])}<span>{_e(r["confidence"])} · {int(r["sessions"])} d</span></div>'
            f'<div class="m">RS 20d {_f(r["rel_20"])} pp · 60d {_f(r["rel_60"])} pp · rank {_f(r["pctile_20"], ".0f")}<br>'
            f'{_f(r["pct50"], ".0f")}% above 50-EMA ({_f(r["d50_stocks_20"], "+.0f")} stocks / 20d) · '
            f'evidence {r["n_support"]}+ / {r["n_contra"]}−' + (f' / {r["n_missing"]} missing' if r["n_missing"] else "")
            + f'</div>{warn}</div>')


def _view_overview(E):
    H, tbl = E["H"], E["table"]
    close = E["close"]
    e50 = close.ewm(span=50, adjust=False, min_periods=50).mean()
    ok = close.iloc[-1].notna() & e50.iloc[-1].notna()
    mb = float((close.iloc[-1] > e50.iloc[-1])[ok].mean() * 100) if ok.any() else np.nan
    ok5 = close.iloc[-6].notna() & e50.iloc[-6].notna() if len(close) > 6 else ok
    mb5 = float((close.iloc[-6] > e50.iloc[-6])[ok5].mean() * 100) if len(close) > 6 and ok5.any() else np.nan
    cnt = tbl["state"].value_counts()
    ahead = int((tbl["rel_60"] > 0).sum())
    spread = tbl["rel_20"].quantile(0.9) - tbl["rel_20"].quantile(0.1)
    names = lambda s: ", ".join(tbl[tbl.state == s].sort_values("pctile_20", ascending=False)["sector"].head(3)) or "none"
    st.markdown(ui.kpi_strip([
        ("Market breadth", _f(mb, ".0f", "%"), f"of {int(ok.sum())} stocks above their 50-EMA · {_f(mb - mb5, '+.0f')} pts in 5 sessions", GREEN if mb >= 50 else RED),
        ("Leading", int(cnt.get("LEADERSHIP", 0) + cnt.get("MATURE LEADERSHIP", 0)), _e(names("LEADERSHIP")), GREEN),
        ("Improving", int(cnt.get("EARLY IMPROVEMENT", 0) + cnt.get("PARTICIPATION CONFIRMATION", 0) + cnt.get("RECOVERY", 0)),
         _e(names("EARLY IMPROVEMENT")), CYAN),
        ("Deteriorating", int(cnt.get("DETERIORATION", 0)), _e(names("DETERIORATION")), RED),
        ("Leadership concentration", f"{ahead} / {len(tbl)}", f"sectors ahead of {_e(H['bench_name'])} over 60 sessions", T2),
        ("RS spread", _f(spread, ".1f", " pp"), "gap between the 90th and 10th percentile sector, 20 sessions", T2)]), unsafe_allow_html=True)

    _h(f"1 · Rotation states · every sector, with its evidence (vs {_e(H['bench_name'])})")
    html = ""
    for s in BOARD_ORDER:
        rows = tbl[tbl.state == s].sort_values("pctile_20", ascending=False).to_dict("records")
        body = "".join(_card(r) for r in rows[:8]) or '<div class="sr-empty">None right now.</div>'
        more = f'<div class="sr-empty">+ {len(rows) - 8} more in RANKING</div>' if len(rows) > 8 else ""
        html += f'<div class="sr-col" style="--c:{STATE_C[s]}"><h4>{s} · {len(rows)}</h4><p>{_e(STATE_HELP[s])}</p>{body}{more}</div>'
    st.markdown(f'<div class="sr-board">{html}</div>', unsafe_allow_html=True)
    rest = int(cnt.get(rstate.NONE, 0) + cnt.get(rstate.NOHIST, 0))
    st.caption(f"{rest} sector(s) have no clear state or not enough history. A state is a description decided by written rules "
               "(see DATA & METHOD). It is not an instruction. SIGNAL TESTS shows what each state was followed by on this history.")

    recent = tbl[(tbl["sessions"] <= 5) & tbl["previous"].notna()]
    if len(recent):
        _h("2 · State changes in the last 5 sessions")
        st.markdown("  \n".join(f"**{_e(r['sector'])}**: {r['previous']} → {r['state']} on {r['since']}" for r in recent.to_dict("records")))
    con = [(r["sector"], c) for r in tbl.to_dict("records") for c in (r["contradictions"] or [])]
    if con:
        _h("3 · Contradictions · where the measurements disagree")
        st.dataframe(pd.DataFrame([{"Sector": s, "Contradiction": c["name"], "Numbers": c["numbers"]} for s, c in con]),
                     use_container_width=True, hide_index=True)
    _h("4 · Relative-strength distribution · 20-session return minus benchmark, all sectors (pp)")
    st.bar_chart(tbl.set_index("sector")["rel_20"].sort_values(ascending=False))


# ═════════════════════════ view · RANKING / BREADTH / ACTIVITY ═════════════════════════
COLS = {
    "sector": "Sector", "state": "State", "confidence": "Confidence", "since": "Since", "sessions": "Sessions in state",
    "previous": "Previous state", "abs_5": "Return 5d %", "abs_20": "Return 20d %", "abs_60": "Return 60d %", "abs_120": "Return 120d %",
    "abs_252": "Return 252d %", "rel_5": "RS 5d pp", "rel_20": "RS 20d pp", "rel_60": "RS 60d pp", "rel_120": "RS 120d pp",
    "rel_252": "RS 252d pp", "pctile_20": "RS percentile", "pctile_chg_20": "RS percentile change 20d", "rs_trend": "RS trend",
    "rs_accel": "RS acceleration pp", "leadership": "Leadership", "pct20": "% above 20-EMA", "pct50": "% above 50-EMA",
    "pct200": "% above 200-EMA", "d20_stocks_10": "Stocks above 20-EMA, change 10d", "d50_stocks_20": "Stocks above 50-EMA, change 20d",
    "new_high20": "New 20d highs", "new_low20": "New 20d lows", "breakout20": "Breakouts today", "adv": "Advancing", "dec": "Declining",
    "n_bar": "Stocks", "up_share5": "Up-close turnover share 5d %", "down_share5": "Down-close turnover share 5d %",
    "rel_activity": "Relative activity (x 20d)", "turnover_accel": "Turnover 5d / 20d", "traded_value_cr": "Traded value ₹Cr",
    "dist50": "Level vs 50-EMA %", "ext_pctile": "Extension percentile", "ew_minus_level_60": "Equal-weight minus index 60d pp",
    "coverage_status": "Data", "with_prices": "Members with prices", "members": "Members",
}
SETS = {
    "OVERVIEW": ["sector", "state", "confidence", "since", "abs_20", "abs_60", "rel_20", "rel_60", "pctile_20", "pctile_chg_20",
                 "pct50", "d50_stocks_20", "rel_activity", "coverage_status"],
    "RELATIVE STRENGTH": ["sector", "state", "rel_5", "rel_20", "rel_60", "rel_120", "rel_252", "pctile_20", "pctile_chg_20",
                          "rs_trend", "rs_accel", "leadership", "dist50", "ext_pctile"],
    "RETURNS": ["sector", "state", "abs_5", "abs_20", "abs_60", "abs_120", "abs_252", "ew_minus_level_60"],
}
BREADTH_COLS = ["sector", "state", "n_bar", "pct20", "d20_stocks_10", "pct50", "d50_stocks_20", "pct200", "adv", "dec",
                "new_high20", "new_low20", "breakout20", "coverage_status"]
ACTIVITY_COLS = ["sector", "state", "traded_value_cr", "rel_activity", "turnover_accel", "up_share5", "down_share5", "abs_5", "rel_20"]


def _table(tbl, keys, key, sort=None, height=None):
    df = tbl[keys].rename(columns=COLS)
    if sort:
        df = df.sort_values(COLS[sort], ascending=False, na_position="last")
    num = df.select_dtypes("number").columns
    st.dataframe(df.style.format({c: "{:,.1f}" for c in num}, na_rep="n/a"), use_container_width=True, hide_index=True,
                 height=height or min(42 + 35 * len(df), 900), key=key)


def _view_ranking(E):
    tbl = E["table"]
    c1, c2 = st.columns([3, 2])
    cset = c1.radio("Columns", list(SETS), horizontal=True, key="mf7_rank_set")
    pick = c2.multiselect("States", list(BOARD_ORDER) + [rstate.NONE], default=[], key="mf7_rank_states")
    view = tbl[tbl["state"].isin(pick)] if pick else tbl
    st.caption(f"{len(view)} sectors, ranked across all {len(tbl)}. Click a column header to sort. RS = sector return minus "
               f"{E['H']['bench_name']} return, in percentage points. RS percentile: 100 = strongest of {int(E['H']['n_ranked'].iloc[-1])} ranked sectors.")
    _table(view, SETS[cset], "mf7_rank_tbl", sort="pctile_20")
    st.download_button("⤓ DOWNLOAD SECTOR TABLE (CSV)",
                       tbl.drop(columns=["evidence", "contradictions", "situations"], errors="ignore").to_csv(index=False),
                       "arka_sector_states.csv", "text/csv", key="mf7_rank_dl")


def _view_breadth(E):
    _h("Participation by sector · level and direction are separate columns")
    _table(E["table"], BREADTH_COLS, "mf7_br_tbl", sort="pct50")
    _note("<b>Level</b> is the % of a sector's stocks above their own EMA. <b>Direction</b> is the change in the NUMBER of stocks, so a "
          "sector of 5 and a sector of 40 are judged the same way. A move of one stock is treated as noise. "
          "80% above the 50-EMA is broad even if it was 90% last month; 30% and rising is weak but improving.")
    if E["kind"] == "NSE INDICES" and E["table"]["pct50"].isna().all():
        st.info("Breadth is NOT AVAILABLE for NSE indices until their constituent files (ind_*list.csv from niftyindices.com) are in the data/ folder.")
    _h("Whole market · % of all loaded stocks above their 20 / 50 / 200-EMA")
    c = E["close"]
    u = pd.DataFrame({f"Above {n}-EMA %": ((c > c.ewm(span=n, adjust=False, min_periods=n).mean()) & c.notna()).sum(axis=1)
                      / c.notna().sum(axis=1).replace(0, np.nan) * 100 for n in (20, 50, 200)}).tail(250)
    _line(u, "mf7_br_mkt", [CYAN, GREEN, PURPLE], 280)


def _view_activity(E):
    _h("Trading activity by sector")
    _table(E["table"], ACTIVITY_COLS, "mf7_act_tbl", sort="rel_activity")
    _note(f"Traded value basis: <b>{_e(E['H']['value_basis'])}</b>. <b>Relative activity</b> = today's traded value ÷ the average of the previous "
          "20 sessions. <b>Up-close turnover share</b> = share of 5-session traded value in stocks that closed above their previous close. "
          "These show how much traded and on what kind of day. They do not show who bought or sold. "
          "Delivery quantity and delivery % are NOT AVAILABLE in this build (they need NSE's bhavcopy).")


# ═════════════════════════ view · SECTOR DETAIL ═════════════════════════
def _view_detail(E, on_open):
    H, cl, tbl = E["H"], E["cl"], E["table"]
    opts = tbl.sort_values("pctile_20", ascending=False)["sector"].tolist()
    state_of = tbl.set_index("sector")["state"]
    if st.session_state.get("mf7_detail_pick") not in opts:          # selection left over from the other sector list
        st.session_state.pop("mf7_detail_pick", None)
    pick = st.selectbox("Sector", opts, key="mf7_detail_pick", format_func=lambda s: f"{s} · {state_of.get(s, '')}")
    r = tbl[tbl["sector"] == pick].iloc[0].to_dict()
    a, b = st.columns([1, 1.25], gap="medium")
    with a:
        st.markdown(_pill(r["state"]) + f' &nbsp;<span style="color:#8a8a8a;font:12px Inter">{_e(STATE_HELP.get(r["state"], ""))}</span>',
                    unsafe_allow_html=True)
        st.markdown(report.explain(r, H["bench_name"]))
        if r["coverage_status"] != "ok":
            st.warning(f"Data: {r['coverage_status']}. {r['with_prices']} of {r['members']} members have prices"
                       + (f"; missing: {', '.join(H['coverage'][pick]['missing'][:12])}" if H["coverage"][pick]["missing"] else "") + ".")
        st.caption(f"Level: {E['level_src']}. As of {H['asof']}. Model {MODEL_VERSION}.")
    with b:
        lv = pd.DataFrame({pick: H["levels"][pick], H["bench_name"]: H["bench"]}).tail(252).dropna()
        st.caption("Sector level vs benchmark, both rebased to 100 one year ago")
        _line(lv / lv.iloc[0] * 100 if len(lv) else lv, "mf7_d_lv", [AMBER, "#D0D0D0"], 250, 100)
        st.caption("Relative-strength line (sector ÷ benchmark) and its 50-session average")
        _line(pd.DataFrame({"RS line": H["ratio"][pick], "50-session average": H["ratio_sma50"][pick]}).tail(252), "mf7_d_rs", [AMBER, CYAN], 220)

    _h("Returns and relative strength · every horizon, every benchmark")
    rows = []
    for bn, bser in E["benches"].items():
        t = rs.rs_table(H["levels"], bser)
        for h in HORIZONS:
            rows.append({"Benchmark": bn, "Sessions": h, "Sector return %": t.at[pick, f"abs_{h}"], "Benchmark return %": t.at[pick, f"bench_{h}"],
                         "Relative return pp": t.at[pick, f"rel_{h}"], "Ratio change %": t.at[pick, f"ratio_chg_{h}"],
                         "Percentile": t.at[pick, f"pctile_{h}"], "Ranked": t.at[pick, f"n_ranked_{h}"]})
    st.dataframe(pd.DataFrame(rows).style.format({c: "{:+.1f}" for c in ("Sector return %", "Benchmark return %", "Relative return pp", "Ratio change %")}
                                                 | {"Percentile": "{:.0f}"}, na_rep="n/a"), use_container_width=True, hide_index=True, height=300)
    st.caption("Relative return = sector return minus benchmark return. Ratio change = % change in (sector ÷ benchmark). They are different numbers.")

    c1, c2 = st.columns(2, gap="medium")
    with c1:
        _h("Breadth history · % of members above their EMA")
        _line(pd.DataFrame({"Above 20-EMA %": H["pct20"][pick], "Above 50-EMA %": H["pct50"][pick], "Above 200-EMA %": H["pct200"][pick]}).tail(250),
              "mf7_d_br", [CYAN, GREEN, PURPLE], 240)
    with c2:
        _h("State history · last 10 changes")
        s = cl["state"][pick]
        chg = s[s.ne(s.shift(1))].tail(10)[::-1]
        st.dataframe(pd.DataFrame({"From": [str(d.date()) for d in chg.index], "State": chg.values}), use_container_width=True, hide_index=True, height=240)

    if r["state"] in rstate.STATES:
        _h(f"What followed {r['state']} on this history · all sectors, non-overlapping windows, entry next session")
        t = bt.state_table(cl["state"], H["levels"], H["bench"], mode="all", states=[r["state"]])
        st.dataframe(t.rename(columns={"horizon": "Sessions held", "n": "Observations", "mean": "Mean excess pp", "median": "Median excess pp",
                                       "beat_pct": "Beat benchmark %", "abs_mean": "Mean return %", "mae": "Avg worst point %", "mfe": "Avg best point %",
                                       "t_stat": "t-stat (overstated)"}).drop(columns=["state"]).style.format("{:,.1f}", na_rep="n/a"),
                     use_container_width=True, hide_index=True)

    mem = E["members"].get(pick, [])
    _h(f"Constituents · {len(mem)} members")
    if not mem:
        st.info("Constituents are NOT AVAILABLE for this index (no constituent file in data/).")
    else:
        stk = report.stock_table(E["close"], E["high"], E["volume"], mem, H["bench"], H["levels"][pick])
        names = {"symbol": "Symbol", "price": "Price ₹", "ret_20": "Return 20d %", "ret_60": "Return 60d %", "rel_bench_20": "vs benchmark 20d pp",
                 "rel_bench_60": "vs benchmark 60d pp", "rel_sector_20": "vs sector 20d pp", "rel_sector_60": "vs sector 60d pp",
                 "above_20": "> 20-EMA", "above_50": "> 50-EMA", "above_200": "> 200-EMA", "from_52w_high": "From 52w high %",
                 "rel_volume": "Volume x 20d", "avg_value_cr": "Avg value ₹Cr", "volatility_20": "Daily volatility %", "range_20": "20d range %", "setup": "Setup (descriptive)"}
        show = stk.rename(columns=names)
        num = show.select_dtypes("number").columns
        st.dataframe(show.style.format({c: "{:,.1f}" for c in num}, na_rep="n/a"), use_container_width=True, hide_index=True,
                     height=min(42 + 35 * len(show), 620))
        st.caption("A strong sector does not make every member a good trade. Setups are rule-based descriptions of the chart, not entries. "
                   "Avg value is close × volume (approximate).")
        if on_open and len(stk):
            x, y = st.columns([3, 1])
            if st.session_state.get("mf7_d_sym") not in stk["symbol"].tolist():
                st.session_state.pop("mf7_d_sym", None)
            sym = x.selectbox("Open a stock in Arka", stk["symbol"].tolist(), key="mf7_d_sym", label_visibility="collapsed")
            if y.button("OPEN STOCK ▸", key="mf7_d_open", use_container_width=True):
                on_open(str(sym))
    with st.expander("STRUCTURED DATA FOR ARKA AI · facts, rules and unknowns for this sector"):
        st.json(report.ai_payload(r, H["asof"], H["bench_name"], E["level_src"]))
        st.caption("Arka AI should be given this object and asked to explain it. It must not add numbers of its own.")


# ═════════════════════════ view · SIGNAL TESTS ═════════════════════════
def _view_tests(E):
    H, cl = E["H"], E["cl"]
    st.warning("Read before trusting any number here. "
               + ("ARKA GROUPS use today's member lists for the whole history, so failed and delisted stocks are missing "
                  "(survivorship bias) and results look better than they were. " if E["kind"] == "ARKA GROUPS" else
                  "NSE index levels are free of survivorship bias, but there are few of them, so samples are small. ")
               + "Sector baskets cannot be bought directly. Past behaviour is evidence, not a promise.")
    valid = cl["state"].ne(rstate.NOHIST).any(axis=1)
    first = valid.idxmax()
    n_sess = int(valid.sum())
    c1, c2, c3 = st.columns([1.4, 1, 2])
    mode = c1.radio("Sample", ["Every h-th session in the state", "First session of each new state"], key="mf7_t_mode")
    h = c2.selectbox("Horizon for comparisons (sessions)", list(bt.HORIZONS), index=2, key="mf7_t_h")
    c3.caption(f"{n_sess} sessions with states, from {first.date()} to {H['asof']}. Entry is the NEXT session's close after the state is known; "
               "exit h sessions later. Load 5 years for a more useful sample.")
    m = "all" if mode.startswith("Every") else "entries"
    if n_sess < 150:
        st.info("Fewer than 150 sessions with states. Load a longer history before reading these tables.")

    _h("1 · How often each state occurs")
    st.dataframe(bt.state_stats(cl["state"].loc[first:]).rename(columns={
        "state": "State", "share_of_time_pct": "Share of time %", "entries": "Entries", "avg_sessions": "Avg sessions per spell",
        "entries_per_sector_year": "Entries per sector per year"}).style.format({"Share of time %": "{:.1f}", "Avg sessions per spell": "{:.0f}",
                                                                                "Entries per sector per year": "{:.1f}"}, na_rep="n/a"),
                 use_container_width=True, hide_index=True)

    _h(f"2 · Forward return minus {_e(H['bench_name'])} after each state (percentage points)")
    full = bt.state_table(cl["state"], H["levels"], H["bench"], mode=m, start=first, states=list(rstate.ALL_STATES))
    piv = lambda col: full.pivot(index="state", columns="horizon", values=col).reindex(list(rstate.ALL_STATES)).rename(
        columns=lambda c: f"{c} sessions").rename_axis(index="State", columns=None)
    a, b = st.columns(2)
    with a:
        st.caption("Mean excess return (pp) by sessions held")
        st.dataframe(piv("mean").style.format("{:+.2f}", na_rep="n/a"), use_container_width=True)
        st.caption("Observations")
        st.dataframe(piv("n").style.format("{:.0f}", na_rep="0"), use_container_width=True)
    with b:
        st.caption("Share of observations that beat the benchmark (%)")
        st.dataframe(piv("beat_pct").style.format("{:.0f}", na_rep="n/a"), use_container_width=True)
        st.caption("Average worst point reached while holding (%, sector's own price)")
        st.dataframe(piv("mae").style.format("{:+.1f}", na_rep="n/a"), use_container_width=True)
    _note("If the states carry information, LEADERSHIP and PARTICIPATION CONFIRMATION should show positive excess and beat rates above 50%, "
          "and DETERIORATION should show negative excess. Where a row has few observations or sits near zero, that state has shown "
          "<b>no measurable edge</b> on this history and should be read as a description only.")

    _h(f"3 · Stability · the same test on each half of the history ({h} sessions held)")
    cut = bt.split_point(cl["state"].loc[first:].index)
    halves = []
    for lab, kw in ((f"First 60% (to {cut.date()})", {"start": first, "end": cut}), (f"Last 40% (from {cut.date()})", {"start": cut})):
        t = bt.state_table(cl["state"], H["levels"], H["bench"], horizons=(h,), mode=m, states=list(rstate.ALL_STATES), **kw)
        halves.append(t.set_index("state")[["n", "mean", "beat_pct"]].rename(columns={"n": f"{lab} · n", "mean": f"{lab} · mean pp", "beat_pct": f"{lab} · beat %"}))
    st.dataframe(pd.concat(halves, axis=1).style.format("{:,.1f}", na_rep="n/a"), use_container_width=True)
    st.caption("The rules were written before seeing this data and were not tuned on it. A result that flips sign between the two halves is not reliable.")

    _h(f"4 · Against simple alternatives · same dates, {h} sessions held")
    bl = bt.baseline_table(H, cl["state"], h, start=first)
    st.dataframe(bl.rename(columns={"selection": "Selection", "n": "Observations", "mean": "Mean excess pp", "median": "Median excess pp",
                                    "beat_pct": "Beat benchmark %", "t_stat": "t-stat (overstated)"}).style.format(
        {"Mean excess pp": "{:+.2f}", "Median excess pp": "{:+.2f}", "Beat benchmark %": "{:.0f}", "t-stat (overstated)": "{:.1f}"}, na_rep="n/a"),
        use_container_width=True, hide_index=True)
    st.caption("If a state does no better than 'top third by 20-session relative return', the extra rules add nothing and the simple rank is the better tool.")

    _h("5 · Costs of acting on a signal")
    x, y = st.columns(2)
    brk = x.number_input("Brokerage per side %", 0.0, 1.0, 0.0, 0.01, key="mf7_t_brk")
    slp = y.number_input("Slippage per side %", 0.0, 1.0, 0.05, 0.01, key="mf7_t_slp")
    cst = bt.round_trip_cost(brk, slp)
    st.markdown(f"Estimated round trip for delivery equity: **{cst['total_pct']:.2f}%** of trade value "
                f"(STT {cst['stt']:.2f}%, stamp duty {cst['stamp']:.3f}%, brokerage + exchange + SEBI + GST {cst['brokerage_exchange_sebi_gst']:.3f}%, "
                f"slippage {cst['slippage']:.2f}%). Subtract this from any excess return above. Rates are assumptions: check your contract note.")


# ═════════════════════════ view · FPI FLOWS ═════════════════════════
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
    _note("NSDL's fortnightly report of <b>foreign portfolio investor</b> net investment by sector. This is recorded money, but it is FPIs only "
          "(no mutual funds or other domestic institutions), it uses NSDL's own sector list, and it is published after the fortnight ends. "
          "It is shown as context and is <b>not used in any state or test</b> in this build.")
    c1, c2 = st.columns([1, 3])
    n = c1.selectbox("Fortnights", [12, 24, 48], index=1, key="mf7_fpi_n")
    with c2:
        st.write("")
        if st.button("LOAD FPI SECTOR FLOWS", type="primary", key="mf7_fpi_load"):
            dates = [str(d)[:10] for d in fpi.nsdl_report_dates(int(n))]
            with st.spinner("Downloading NSDL fortnightly reports…"):
                with ThreadPoolExecutor(max_workers=4) as pool:
                    got = list(pool.map(_nsdl_fetch, dates))
            st.session_state["mf7_fpi"] = (fpi.nsdl_panels(got), sum(g is not None for g in got), len(dates))
    got = st.session_state.get("mf7_fpi")
    if not got:
        return
    (net, auc), ok, tried = got
    if net.empty:
        st.warning(f"FPI data NOT AVAILABLE: no NSDL report could be read ({ok} of {tried}). The site may be blocking this server or its file names changed.")
        return
    age = (pd.Timestamp.today().normalize() - net.index[-1]).days
    st.caption(f"{ok} of {tried} reports loaded · latest fortnight ended {str(net.index[-1])[:10]} ({age} days ago) · ₹ crore, equity.")
    if age > 25:
        st.warning("This FPI data is stale: the latest fortnight loaded ended more than 25 days ago.")
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
        rows.append({"NSDL sector": s, "Last fortnight ₹Cr": float(x.iloc[-1]) if len(x) else np.nan, "Last 2 ₹Cr": x.tail(2).sum(),
                     "Last 6 ₹Cr": x.tail(6).sum(), "Last 12 ₹Cr": x.tail(12).sum(),
                     "Last 6 as % of FPI holdings": x.tail(6).sum() / aum * 100 if aum and aum > 0 else np.nan,
                     "Fortnights in a row (+ in / − out)": streak, "FPI holdings ₹Cr": aum})
    tb = pd.DataFrame(rows).sort_values("Last 6 as % of FPI holdings", ascending=False, na_position="last")
    st.dataframe(tb.style.format({c: "{:+,.0f}" for c in tb.columns if "₹Cr" in c and "holdings" not in c}
                                 | {"Last 6 as % of FPI holdings": "{:+.2f}", "FPI holdings ₹Cr": "{:,.0f}"}, na_rep="n/a"),
                 use_container_width=True, hide_index=True, height=min(42 + 35 * len(tb), 760))
    st.caption("Holdings (assets under custody) move with prices as well as flows, so a change in holdings is not shown as buying or selling.")
    st.download_button("⤓ FPI FLOWS (CSV)", net.to_csv(), "nsdl_fpi_sector_flows.csv", "text/csv", key="mf7_fpi_dl")


# ═════════════════════════ view · DATA & METHOD ═════════════════════════
def _view_data(E, source):
    H, q, tbl = E["H"], E["q"], E["table"]
    _h("1 · What was loaded")
    st.markdown(
        f"- Source: **{q['source']}** · prices {q['adjusted']} · history requested: {q['period']} · loaded at {q['loaded_at']}\n"
        f"- Universe: `{source}` · requested **{q['requested']}** stocks, received **{q['loaded']}**, used **{q['used']}**\n"
        f"- Session: last completed session **{q['session']['last_complete_session']}** · holiday calendar: {q['session']['holiday_calendar']} · "
        f"part-day sessions dropped: {', '.join(q['dropped_sessions']) or 'none'}\n"
        f"- Excluded as illiquid (median daily value under ₹{q['min_turn']:g} Cr): **{len(q['illiquid'])}**\n"
        f"- Stale tickers excluded (last bar more than 5 sessions old): **{len(q['stale'])}** {', '.join(q['stale'][:15])}\n"
        f"- Groups with fewer than 3 priced members (not analysed): {', '.join(q['small_groups']) or 'none'}\n"
        f"- NSE index series that did not download: {', '.join(q['index_missing']) or 'none'}\n"
        f"- Duplicate dates dropped: {q['duplicates_dropped']} · non-positive prices removed: {q['non_positive']}")
    if q["failures"]:
        _h(f"2 · Tickers with no data · {len(q['failures'])}")
        st.dataframe(pd.DataFrame([{"Ticker": k, "Reason": v} for k, v in q["failures"].items()]), use_container_width=True, hide_index=True, height=240)
    _h("3 · Coverage by sector")
    cov = tbl[["sector", "members", "with_prices", "coverage", "coverage_status"]].copy()
    cov["missing"] = [", ".join(H["coverage"][s]["missing"][:10]) for s in cov["sector"]]
    cov["coverage"] = cov["coverage"] * 100
    st.dataframe(cov.rename(columns={"sector": "Sector", "members": "Members", "with_prices": "With prices", "coverage": "Coverage %",
                                     "coverage_status": "Status", "missing": "Missing members"}).sort_values("Coverage %").style.format(
        {"Coverage %": "{:.0f}"}, na_rep="n/a"), use_container_width=True, hide_index=True, height=320)
    st.caption(f"A sector with under {MIN_SECTOR_COVERAGE * 100:.0f}% of its members priced is marked INCOMPLETE.")
    if q["jumps"]:
        _h(f"4 · One-session moves above 25% · {len(q['jumps'])} · check for unadjusted splits or bonuses")
        st.dataframe(pd.DataFrame(q["jumps"]).rename(columns={"symbol": "Symbol", "date": "Date", "move_pct": "Move %"}),
                     use_container_width=True, hide_index=True, height=220)
        st.caption("These are listed, not altered. If one is a missed corporate action, that stock's returns and its sector basket are wrong around that date.")
    with st.expander("HOW STATES ARE DECIDED · the full rules"):
        st.code(rstate.__doc__)
    with st.expander("FORMULAS · relative strength, breadth, activity, signal tests"):
        from arka_flow.calc import activity, breadth
        st.code("\n".join(m.__doc__ for m in (rs, breadth, activity, bt)))
    with st.expander("SOURCES AND LIMITS"):
        st.markdown("""
- **Prices:** Yahoo Finance daily bars. Unofficial and delayed; adjustments for splits and dividends are Yahoo's and are not cross-checked.
- **Traded value:** close × volume, an approximation. Exchange-reported value and delivery data need NSE's bhavcopy and are not loaded in this build.
- **Sectors:** ARKA GROUPS are custom baskets from `sector_map.py`, equal-weighted, built from today's members. NSE INDICES are official index levels (price indices, cap-weighted). The two are never mixed in one ranking.
- **History:** today's member lists are applied to the past (survivorship bias). Point-in-time membership is NOT AVAILABLE.
- **Not in this build:** delivery participation, mutual-fund and other institutional disclosures, fundamentals, earnings revisions, macro drivers. They are shown as unknown, not estimated.
- **Thresholds** in the state rules (60% / 40% breadth, 2 stocks, top 10% extension, 3-session persistence) are provisional. SIGNAL TESTS is where they are judged.
""")


# ═════════════════════════ page ═════════════════════════
def _universe_missing():
    st.warning("The stock universe could not be loaded: NSE and niftyindices.com are refusing this server and no copy is in the repo yet.")
    _note('<b>Fix:</b> download <a href="https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv" target="_blank">ind_nifty500list.csv</a> and '
          '<a href="https://www.niftyindices.com/IndexConstituent/ind_niftymicrocap250_list.csv" target="_blank">ind_niftymicrocap250_list.csv</a> '
          "and put them in a <code>data</code> folder next to the app. Money Flow reads them first.")
    if st.button("RETRY NOW", key="mf7_retry"):
        _ARCH_DOWN.clear()
        _universe_cached.clear()
        st.rerun()


def render_money_flow(on_open=None):
    """Arka MONEY FLOW workspace."""
    _style()
    if st.session_state.get("mf7_view") not in VIEWS:
        st.session_state["mf7_view"] = VIEWS[0]
    view = st.radio("Money Flow view", VIEWS, horizontal=True, key="mf7_view", label_visibility="collapsed")
    if view == "FPI FLOWS":
        return _view_fpi()

    try:
        meta, source = _universe_cached()
    except Exception:
        return _universe_missing()

    ready = st.session_state.get("mf7_ready", False)
    c1, c2, c3, c4, c5 = st.columns([1.2, 1.6, 2.2, 1, 1], gap="small")
    with c1:
        st.write("")
        load = st.button("LOAD / REFRESH" if ready else "LOAD SECTOR DATA", type="primary", use_container_width=True, key="mf7_load")
    kind = c2.radio("Sectors", list(KINDS), horizontal=True, key="mf7_kind", help=" ".join(f"{k}: {v}" for k, v in KINDS.items()))
    bench = c3.radio("Compare against", ["NIFTY 500", "NIFTY 50", EW_MARKET], horizontal=True, key="mf7_bench",
                     help="NIFTY 500 and NIFTY 50 are cap-weighted official indices. EQUAL-WEIGHT MARKET is every loaded stock, equal-weighted.")
    period = c4.selectbox("History", ["3y", "5y", "2y"], key="mf7_period", help="5y gives the signal tests a more useful sample; it takes longer to load.")
    min_turn = c5.selectbox("Min value / day", [0.5, 0.25, 1.0, 2.0, 5.0], key="mf7_minturn", format_func=lambda v: f"₹{v:g} Cr")
    if load:
        if ready:
            _load.clear()
            _engine.clear()
        st.session_state["mf7_ready"] = ready = True
    if not ready:
        _note(f"Press <b>LOAD SECTOR DATA</b>. Arka downloads daily prices for <b>{len(meta)}</b> stocks ({_e(source)}) and the NSE indices from Yahoo. "
              "The first load takes a few minutes, then it is cached for 3 hours.")
        return

    with st.spinner(f"Loading {len(meta)} stocks and computing sector states…"):
        try:
            E = _engine(tuple(meta["symbol"]), period, kind, bench, float(min_turn))
        except Exception as exc:
            st.error(f"Sector data unavailable: {type(exc).__name__}: {exc}")
            st.caption("If Yahoo is rate-limiting, wait a minute and press LOAD / REFRESH. Nothing is shown from partial or substituted data.")
            return
    _publish(E)
    _status(E)
    if view == "OVERVIEW":
        _view_overview(E)
    elif view == "SECTOR DETAIL":
        _view_detail(E, on_open)
    elif view == "RANKING":
        _view_ranking(E)
    elif view == "BREADTH":
        _view_breadth(E)
    elif view == "ACTIVITY":
        _view_activity(E)
    elif view == "SIGNAL TESTS":
        _view_tests(E)
    else:
        _view_data(E, source)
    st.caption("Research tool. States and tests describe price, breadth and traded value. They are not forecasts or investment advice.")

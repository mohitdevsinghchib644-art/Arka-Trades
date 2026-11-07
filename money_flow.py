"""
money_flow.py — Arka MONEY FLOW v6 · Sector Rotation Desk

Rebuilt around one question: WHERE IS MONEY MOVING, AND IS IT EARLY OR LATE TO FOLLOW IT?

Views (only the open view is built)
  ROTATION DESK    action board (buy zone / hold / avoid), weekly Relative Rotation Graph with tails,
                   rotation-score history, weekly rank race, sector detail with its strongest stocks
  SECTOR RANKING   every sector, sortable (click a header), four column sets: overview / performance / money / breadth
  MONEY FLOW       turnover share and its change, up- vs down-turnover, ₹-weighted Chaikin Money Flow heat-map
  BREADTH          % of stocks above 20 / 50 / 200-EMA, breakouts, new highs, A/D, whole-market breadth
  STOCKS           drill into one sector: every stock with RS rating, trend, distance from highs, volume
  NSE INDICES      official NSE sector indices on the same rotation map (Yahoo history; works when NSE blocks)
  FPI FLOWS        NSDL fortnightly sector-wise foreign investment — real recorded money

What changed vs v5
  * ~50 granular sectors (sector_map.py) instead of NSE's 22 broad ones: PSU Banks, Private Banks, NBFC,
    Capital Markets, Electrical Equipment, Cables, Defence, Railways … are now separate.
  * Equal-weight sector indices measured against an equal-weight MARKET (like-for-like); money flow is
    ₹-weighted; illiquid stocks are excluded; weekly JdK-style RRG.
  * Every sector gets one action label with the reasons behind it.
  * Removed: the backtest lab, broad index snapshot tables, constituent explorer and the NSE-live-only
    views that failed on Streamlit Cloud. FPI flows moved here from the lab.

Data: NSE index constituent CSVs (data/ folder or NSE archives) for the universe, Yahoo Finance daily prices,
NSDL for FPI flows. Evidence from price, volume and breadth: not order-flow data, not investment advice.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import streamlit as st

import mf_ui as ui
import rotation_engine as rot
import sector_map as smap

try:
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None
try:
    import plotly.graph_objects as go
except Exception:  # pragma: no cover
    go = None

IST = timezone(timedelta(hours=5, minutes=30))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
LOCAL_DATA = Path("data")
DISK_CACHE = Path(".cache") / "nse"
SECTOR_MAP_FILE = LOCAL_DATA / "sector_map.csv"
ARCHIVE_MIRRORS = ("https://nsearchives.nseindia.com/content/indices/", "https://archives.nseindia.com/content/indices/",
                   "https://www.niftyindices.com/IndexConstituent/")
UNIVERSE_SETS = (("ind_niftytotalmarket_list",), ("ind_nifty500list", "ind_niftymicrocap250_list"), ("ind_nifty500list",))
MAX_SYMBOLS = 1500
BENCH_TICKERS = {"NIFTY 50": "^NSEI", "NIFTY 500": "^CRSLDX"}
BENCH_HELP = ("MARKET = every stock in the universe, equal-weighted exactly like the sectors: the fair yardstick. "
              "NIFTY 50 / NIFTY 500 are cap-weighted, so small and mid-cap sectors look weak whenever large caps lead.")
OFFICIAL = {"NIFTY BANK": "^NSEBANK", "NIFTY PVT BANK": "NIFTY_PVT_BANK.NS", "NIFTY PSU BANK": "^CNXPSUBANK",
            "NIFTY FIN SERVICE": "NIFTY_FIN_SERVICE.NS", "NIFTY IT": "^CNXIT", "NIFTY AUTO": "^CNXAUTO",
            "NIFTY FMCG": "^CNXFMCG", "NIFTY PHARMA": "^CNXPHARMA", "NIFTY HEALTHCARE": "NIFTY_HEALTHCARE.NS",
            "NIFTY METAL": "^CNXMETAL", "NIFTY REALTY": "^CNXREALTY", "NIFTY ENERGY": "^CNXENERGY",
            "NIFTY OIL & GAS": "NIFTY_OIL_AND_GAS.NS", "NIFTY MEDIA": "^CNXMEDIA", "NIFTY INFRA": "^CNXINFRA",
            "NIFTY CONSUMER DURABLES": "NIFTY_CONSR_DURBL.NS", "NIFTY PSE": "^CNXPSE", "NIFTY MNC": "^CNXMNC",
            "NIFTY SERVICES": "^CNXSERVICE", "NIFTY COMMODITIES": "^CNXCMDT", "NIFTY CONSUMPTION": "^CNXCONSUM"}
VIEWS = ["ROTATION DESK", "SECTOR RANKING", "MONEY FLOW", "BREADTH", "STOCKS", "NSE INDICES", "FPI FLOWS"]

GREEN, RED, AMBER, CYAN, YELLOW, PURPLE, T2, T3 = ui.GREEN, ui.RED, ui.AMBER, ui.CYAN, ui.YELLOW, ui.PURPLE, ui.T2, ui.T3
PHASE_C = {"LEADING": GREEN, "WEAKENING": AMBER, "LAGGING": RED, "IMPROVING": CYAN, "—": T3}
ACTION_C = {"ROTATING IN": GREEN, "LEADING": "#8AD66B", "STRETCHED": YELLOW, "WATCH": CYAN, "WEAKENING": AMBER, "LAGGING": RED}
MONEY_C = {"BUYING ON VOLUME": GREEN, "QUIET BUYING": CYAN, "MIXED": T2, "QUIET SELLING": AMBER, "SELLING ON VOLUME": RED, "—": T3}
CMF_C = {"ACCUMULATING": GREEN, "IMPROVING": CYAN, "NEUTRAL": T2, "WEAKENING": AMBER, "DISTRIBUTING": RED, "—": T3}
_LAST_RES: dict = {}
_ARCH_DOWN: dict = {}


# ═════════════════════════ style + small helpers ═════════════════════════
def _style():
    ui.inject()
    st.markdown("""<style>
    .arka-mf-section{font:700 11px 'JetBrains Mono',monospace;letter-spacing:1.2px;color:#d6d6d6;margin:24px 0 10px;padding-bottom:8px;border-bottom:1px solid #262626;text-transform:uppercase}
    .arka-mf-note{font:12px/1.75 'Inter',sans-serif;color:#9a9a9a;border-left:2px solid #3a3a3a;padding:8px 12px;background:rgba(255,255,255,.02);margin:8px 0 14px}
    .arka-mf-note b{color:#e2e2e2}
    div[class*="st-key-mf_viewbar"] [role="radiogroup"]{gap:6px !important}
    div[class*="st-key-mf_viewbar"] label[data-baseweb="radio"]{padding:8px 14px !important}
    div[class*="st-key-mf_viewbar"] label[data-baseweb="radio"] p{font-size:11px !important}
    .rd-status{display:flex;flex-wrap:wrap;gap:6px 18px;font:600 11px 'JetBrains Mono',monospace;color:#8a8a8a;padding:8px 2px;border-bottom:1px solid #1f1f1f;margin-bottom:6px}
    .rd-status b{color:#e6e6e6}
    .rd-board{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:6px 0 10px}
    @media(max-width:1150px){.rd-board{grid-template-columns:1fr}}
    .rd-col{background:#080808;border:1px solid #1f1f1f;border-top:3px solid var(--c);padding:12px 12px 4px;min-width:0}
    .rd-ch{display:flex;flex-direction:column;gap:3px;margin-bottom:2px}
    .rd-ch b{font:800 13px 'JetBrains Mono',monospace;letter-spacing:1.3px;color:var(--c)}
    .rd-ch span{font:12px 'Inter',sans-serif;color:#8a8a8a}
    .rd-sub{font:700 10px 'JetBrains Mono',monospace;letter-spacing:1px;color:#777;margin:14px 0 7px;text-transform:uppercase}
    .rd-card{border:1px solid #242424;border-left:3px solid var(--c);background:rgba(255,255,255,.02);padding:10px 12px;margin-bottom:9px;border-radius:3px}
    .rd-card .h{display:flex;justify-content:space-between;align-items:center;gap:8px}
    .rd-card .n{font:700 14px 'Inter',sans-serif;color:#f2f2f2}
    .rd-card .m{font:11.5px/1.65 'JetBrains Mono',monospace;color:#a0a0a0;margin-top:5px}
    .rd-card ul{margin:7px 0 0 16px;padding:0;font:12px/1.6 'Inter',sans-serif;color:#c4c4c4}
    .rd-card ul.w{color:#e0ad4c}
    .rd-card .l{font:11px 'JetBrains Mono',monospace;color:#7a7a7a;margin-top:7px}
    .rd-card .l b{color:#FF9F0A}
    .rd-pill{display:inline-block;padding:2px 9px;border-radius:999px;font:800 9.5px 'JetBrains Mono',monospace;letter-spacing:.7px;border:1px solid currentColor;white-space:nowrap}
    .rd-sc{display:inline-flex;align-items:center;gap:7px;font:700 13px 'JetBrains Mono',monospace}
    .rd-sc i{display:inline-block;width:48px;height:5px;background:#1c1c1c}
    .rd-sc i b{display:block;height:100%}
    .rd-empty{font:12px 'Inter',sans-serif;color:#666;padding:2px 0 10px}
    .rd-moves{font:12.5px/1.9 'Inter',sans-serif;color:#bbb;margin:4px 0 8px}
    .rd-hw{overflow:auto;max-height:760px;border:1px solid #1f1f1f;background:#070707;margin:6px 0 12px}
    .rd-ht{width:100%;border-collapse:separate;border-spacing:2px;font:12px 'JetBrains Mono',monospace}
    .rd-ht th{position:sticky;top:0;background:#0c0c0c;font:700 10px 'JetBrains Mono',monospace;color:#7a7a7a;letter-spacing:.8px;text-transform:uppercase;padding:8px;text-align:center;white-space:nowrap;z-index:2}
    .rd-ht th:first-child{text-align:left}
    .rd-ht td{padding:7px 9px;text-align:center;color:#f0f0f0;white-space:nowrap;border-radius:2px;font-variant-numeric:tabular-nums}
    .rd-ht td.s{text-align:left;background:transparent;color:#e6e6e6;font:600 12.5px 'Inter',sans-serif}
    </style>""", unsafe_allow_html=True)


def _h(t):
    st.markdown(f'<div class="arka-mf-section">{t}</div>', unsafe_allow_html=True)


def _note(t):
    st.markdown(f'<div class="arka-mf-note">{t}</div>', unsafe_allow_html=True)


_e, _f = ui.esc, rot._f


def _isna(v) -> bool:
    return v is None or (not isinstance(v, str) and bool(pd.isna(v)))


def _num(v, d=1, suf="", sign=False) -> str:
    v = _f(v)
    if v is None:
        return "—"
    return f"{v:+,.{d}f}{suf}" if sign else f"{v:,.{d}f}{suf}"


def _sg(v, d=2, suf="") -> str:
    v = _f(v)
    if v is None:
        return '<span class="mf-na">—</span>'
    c = GREEN if v > 0 else RED if v < 0 else T2
    return f'<span style="color:{c};font:600 11px JetBrains Mono,monospace">{v:+.{d}f}{suf}</span>'


def _pill(text, color) -> str:
    return f'<span class="rd-pill" style="color:{color}">{_e(text)}</span>'


def _score(v) -> str:
    v = _f(v)
    if v is None:
        return '<span class="rd-sc">—</span>'
    c = GREEN if v >= 65 else AMBER if v >= 45 else RED
    return f'<span class="rd-sc" style="color:{c}"><i><b style="width:{v:.0f}%;background:{c}"></b></i>{v:.0f}</span>'


def _heat_bg(d) -> str:
    if d is None:
        return "#121212"
    d = max(-1.0, min(1.0, d))
    if abs(d) < 0.04:
        return "#1b1b1b"
    a = 0.14 + 0.56 * abs(d)
    return f"rgba(48,209,88,{a:.2f})" if d > 0 else f"rgba(255,69,58,{a:.2f})"


def _market_live() -> bool:
    try:
        from home_page import nse_session
        return nse_session()["cls"] in ("live", "pre")
    except Exception:
        n = datetime.now(IST)
        t = n.hour * 60 + n.minute
        return n.weekday() < 5 and 9 * 60 <= t <= 15 * 60 + 40


def _bucket() -> int:
    return int(time.time() // (1800 if _market_live() else 3 * 3600))


# ═════════════════════════ universe ═════════════════════════
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


def _read_archive(slug: str):
    """Constituent CSV: repo copy in data/ -> NSE mirrors (10-min cool-down after a failure) -> last good disk copy."""
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
    disk = DISK_CACHE / f"{slug}.csv"
    if disk.exists():
        try:
            return _parse_csv(disk.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            pass
    return None


def _read_map():
    """Optional data/sector_map.csv: symbol + any of company, group, basic_industry, industry, sector."""
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
        df = df[~df["Series"].astype(str).str.strip().str.upper().isin(["RR", "IV"])]      # REITs / InvITs are not stocks
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
    out["family"] = [smap.family(g) for g in out["group"]]
    return out.reset_index(drop=True), source


# ═════════════════════════ prices + analysis ═════════════════════════
def _split(raw, tickers) -> dict:
    out = {}
    if raw is None or getattr(raw, "empty", True):
        return out
    if isinstance(raw.columns, pd.MultiIndex):
        lv0 = set(raw.columns.get_level_values(0))
        if "Close" in lv0 and not (set(tickers) & lv0):
            raw = raw.swaplevel(axis=1).sort_index(axis=1)
        top = set(raw.columns.get_level_values(0))
        for t in tickers:
            if t in top:
                f = raw[t].dropna(how="all")
                if "Close" in f and f["Close"].notna().sum() >= 60:
                    out[t] = f
    elif len(tickers) == 1 and "Close" in raw:
        out[tickers[0]] = raw.dropna(how="all")
    return out


def _yf_batch(tickers, period="2y", chunk=100) -> dict:
    out = {}
    for i in range(0, len(tickers), chunk):
        part = tickers[i:i + chunk]
        try:
            raw = yf.download(part, period=period, interval="1d", group_by="ticker", auto_adjust=True,
                              progress=False, threads=True)
        except Exception:
            continue
        out.update(_split(raw, part))
        time.sleep(0.4)
    return out


@st.cache_data(ttl=3 * 3600, show_spinner=False, max_entries=2)
def _prices(symbols: tuple, bucket: int) -> dict:
    if yf is None:
        raise RuntimeError("yfinance is not installed")
    frames = _yf_batch([f"{s}.NS" for s in symbols] + list(BENCH_TICKERS.values()))
    n = sum(1 for t in frames if t.endswith(".NS"))
    if n < 50:
        raise RuntimeError(f"Yahoo returned history for only {n} stocks")
    return frames


@st.cache_data(ttl=3 * 3600, show_spinner=False, max_entries=8)
def _analysis(symbols: tuple, bench: str, weighting: str, min_turn: float, bucket: int) -> dict:
    meta, _ = _universe_cached()
    frames = _prices(symbols, bucket)
    pan = rot.build_panels(frames, meta, min_turn)
    benches = {k: frames[t]["Close"] for k, t in BENCH_TICKERS.items() if t in frames}
    res = rot.analyse(pan, benches, bench, weighting)
    nse = meta.set_index("symbol")["nse_sector"]
    s = res["stocks"].assign(nse_sector=lambda d: d["symbol"].map(nse))
    res["broad_rs"] = s.groupby("nse_sector")["ex1m"].median().to_dict()       # used by FPI FLOWS
    return res


def _get_analysis(symbols, bench, weighting, min_turn):
    key = (bench, weighting, min_turn)
    try:
        res = _analysis(symbols, bench, weighting, min_turn, _bucket())
        _LAST_RES[key] = res
        return res, None
    except Exception as exc:
        if key in _LAST_RES:
            return _LAST_RES[key], f"{type(exc).__name__}: {exc}"
        raise


def _publish(res):
    """Snapshot for HOME's rotation panel and the FPI view."""
    t = res["table"]
    st.session_state["mf_rot"] = {"asof": res["asof"], "bench": res["bench_name"],
                                  "table": t[["sector", "action", "phase", "score", "ex1m", "r1w"]].copy()}
    st.session_state["mf_broad_rs"] = res.get("broad_rs", {})


# ═════════════════════════ shared blocks ═════════════════════════
def _card(r: dict, bench: str, full: bool = False) -> str:
    c = ACTION_C.get(r.get("action"), T3)
    ph = r.get("phase") or "—"
    why = list(r.get("why") or [])
    warn = list(r.get("warn") or [])
    n_why, n_warn = (len(why), len(warn)) if full else (3, 2)
    stocks = _f(r.get("stocks"))
    meta = (f'{_pill(r.get("action"), c)} <span style="color:{PHASE_C.get(ph, T3)}">{rot.compass(r.get("heading"))} '
            f'{_e(ph.title())} {int(_f(r.get("in_phase")) or 0)} wk</span>' + (f" · {int(stocks)} stocks" if stocks else ""))
    nums = (f'RS 1M {_num(r.get("ex1m"), 1, " pp", True)} · 3M {_num(r.get("ex3m"), 1, " pp", True)} · '
            f'1W {_num(r.get("r1w"), 1, "%", True)}<br>{_num(r.get("pct20"), 0, "%")} above 20-EMA · '
            f'{_num(r.get("up_pct5"), 0, "%")} up-turnover · CMF {_num(r.get("cmf"), 2, "", True)}')
    lead = f'<div class="l">LEADERS · <b>{_e(r.get("leaders"))}</b></div>' if r.get("leaders") else ""
    ul = "".join(f"<li>{_e(x)}</li>" for x in why[:n_why])
    wl = "".join(f"<li>{_e(x)}</li>" for x in warn[:n_warn])
    return (f'<div class="rd-card" style="--c:{c}"><div class="h"><span class="n">{_e(r.get("sector"))}</span>{_score(r.get("score"))}</div>'
            f'<div class="m">{meta}</div><div class="m">{nums}</div><ul>{ul}</ul>'
            + (f'<ul class="w">{wl}</ul>' if wl else "") + lead + "</div>")


def _board(tbl: pd.DataFrame, bench: str) -> str:
    recs = tbl.to_dict("records")
    by = lambda a: [r for r in recs if r.get("action") == a]
    sc = lambda r: _f(r.get("score")) or 0.0
    drop = lambda r: (_f(r.get("score_2w")) or 0.0) - sc(r)
    cols = [("BUY ZONE", GREEN, "Money arriving, trend confirmed, not stretched yet.",
             [("★ Rotating in", by("ROTATING IN")[:6]), ("Watch next · turning up, not confirmed", by("WATCH")[:4])]),
            ("HOLD", "#8AD66B", "Current leaders. Keep; add only on pullbacks to the 20-EMA.",
             [("Leading", by("LEADING")[:6]), ("Stretched · wait for a pullback", by("STRETCHED")[:4])]),
            ("AVOID / EXIT", RED, "Leadership fading or money leaving. No fresh buying.",
             [("Weakening · biggest deterioration first", sorted(by("WEAKENING"), key=drop, reverse=True)[:5]),
              ("Lagging · weakest first", sorted(by("LAGGING"), key=sc)[:5])])]
    html = ""
    for title, c, sub, parts in cols:
        body = ""
        for lab, rows in parts:
            body += f'<div class="rd-sub">{_e(lab)}</div>'
            body += "".join(_card(r, bench) for r in rows) or '<div class="rd-empty">None right now.</div>'
        html += f'<div class="rd-col" style="--c:{c}"><div class="rd-ch"><b>{title}</b><span>{_e(sub)}</span></div>{body}</div>'
    return f'<div class="rd-board">{html}</div>'


def _heat_table(df: pd.DataFrame, kind: str, extra: dict | None = None) -> str:
    """df: sectors (index) x period columns -> coloured HTML grid. extra: {header: fn(sector) -> html}."""
    n = max(len(df), 2)
    cols = list(df.columns)
    head = "<th>Sector</th>" + "".join(f"<th>{_e(c)}</th>" for c in cols) + "".join(f"<th>{_e(k)}</th>" for k in (extra or {}))
    body = []
    for s, r in df.iterrows():
        tds = []
        for c in cols:
            v = _f(r[c])
            if v is None:
                tds.append('<td style="background:#121212;color:#555">—</td>')
                continue
            if kind == "score":
                d, txt = (v - 50) / 50, f"{v:.0f}"
            elif kind == "pct":
                d, txt = (v - 50) / 50, f"{v:.0f}%"
            elif kind == "cmf":
                d, txt = v / 0.15, f"{v:+.2f}"
            elif kind == "rank":
                d, txt = ((n + 1) / 2 - v) / ((n - 1) / 2), f"{v:.0f}"
            else:
                d, txt = 0.0, f"{v:.2f}"
            tds.append(f'<td style="background:{_heat_bg(d)}">{txt}</td>')
        for fn in (extra or {}).values():
            tds.append(f"<td>{fn(s)}</td>")
        body.append(f'<tr><td class="s">{_e(s)}</td>{"".join(tds)}</tr>')
    return f'<div class="rd-hw"><table class="rd-ht"><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def _rrg_fig(tbl: pd.DataFrame, tails: dict, highlight: list, label_mode: str, bench: str, unit: str = "weeks"):
    """Relative Rotation Graph: dot = today, tail = path over the last periods, bigger dot = higher score."""
    if go is None:
        return None
    recs = tbl.set_index("sector").to_dict("index")
    cur = {s: t[-1] for s, t in tails.items() if t and s in recs}
    if not cur:
        return None
    xs, ys = [p[0] for p in cur.values()], [p[1] for p in cur.values()]
    for s in highlight:
        for p in tails.get(s, []):
            xs.append(p[0])
            ys.append(p[1])
    dx = max(1.5, max(abs(x - 100) for x in xs) * 1.15)
    dy = max(1.5, max(abs(y - 100) for y in ys) * 1.15)
    rgb = {"LEADING": "48,209,88", "WEAKENING": "255,159,10", "LAGGING": "255,69,58", "IMPROVING": "90,200,250"}
    fig = go.Figure()
    for name, x0, x1, y0, y1, xa, ya in (("IMPROVING", 100 - dx, 100, 100, 100 + dy, "left", "top"),
                                         ("LEADING", 100, 100 + dx, 100, 100 + dy, "right", "top"),
                                         ("LAGGING", 100 - dx, 100, 100 - dy, 100, "left", "bottom"),
                                         ("WEAKENING", 100, 100 + dx, 100 - dy, 100, "right", "bottom")):
        fig.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1, fillcolor=f"rgba({rgb[name]},0.06)", line_width=0, layer="below")
        fig.add_annotation(x=x0 if xa == "left" else x1, y=y1 if ya == "top" else y0, xanchor=xa, yanchor=ya, showarrow=False,
                           text=f"<b>{name}</b>", font=dict(size=13, color=f"rgb({rgb[name]})"))
    for s in highlight:
        t = tails.get(s) or []
        if len(t) < 2 or s not in recs:
            continue
        col = PHASE_C.get(recs[s].get("phase"), T2)
        fig.add_trace(go.Scatter(x=[p[0] for p in t], y=[p[1] for p in t], mode="lines+markers", showlegend=False,
                                 hoverinfo="skip", opacity=0.9, line=dict(color=col, width=2),
                                 marker=dict(size=[3 + 4 * i / len(t) for i in range(len(t))], color=col)))
        fig.add_annotation(x=t[-1][0], y=t[-1][1], ax=t[-2][0], ay=t[-2][1], xref="x", yref="y", axref="x", ayref="y",
                           showarrow=True, arrowhead=2, arrowsize=1.3, arrowwidth=2, arrowcolor=col, text="")
    order = sorted(cur, key=lambda s: -(_f(recs[s].get("score")) or 0))
    if label_mode == "All":
        labels = set(cur)
    elif label_mode == "None":
        labels = set()
    else:
        labels = set(highlight) | set(order[:6]) | {s for s in cur if recs[s].get("action") == "ROTATING IN"}
    pos = ["top center", "bottom center", "middle right", "middle left"]
    names = sorted(cur, key=lambda s: cur[s][0])
    sizes = [10 + (_f(recs[s].get("score")) or 40) / 100 * 12 for s in names]
    cols_ = [PHASE_C.get(recs[s].get("phase"), T2) for s in names]
    hov = [f"<b>{_e(s)}</b><br>{_e(recs[s].get('action', ''))} · {_e(recs[s].get('phase', ''))}"
           + (f" · score {_f(recs[s].get('score')):.0f}" if _f(recs[s].get("score")) is not None else "")
           + f"<br>RS-Ratio {cur[s][0]:.2f} · RS-Momentum {cur[s][1]:.2f}" for s in names]
    fig.add_trace(go.Scatter(x=[cur[s][0] for s in names], y=[cur[s][1] for s in names], mode="markers+text",
                             text=[(s if len(s) <= 22 else s[:21] + "…") if s in labels else "" for s in names],
                             textposition=[pos[i % 4] for i in range(len(names))], textfont=dict(size=11, color=cols_),
                             hovertext=hov, hoverinfo="text", showlegend=False,
                             marker=dict(size=sizes, color=cols_, line=dict(color="#000", width=1))))
    ui._dark(fig, 660, legend=False, margin=(8, 8, 8, 8))
    fig.update_xaxes(range=[100 - dx, 100 + dx], gridcolor="#151515", zeroline=False,
                     title=f"RS-RATIO ·  ◀ weaker than {bench}   |   stronger than {bench} ▶")
    fig.update_yaxes(range=[100 - dy, 100 + dy], gridcolor="#151515", zeroline=False,
                     title=f"RS-MOMENTUM ·  ◀ slowing   |   accelerating ▶  ({unit})")
    fig.add_vline(x=100, line_color="#3a3a3a", line_width=1)
    fig.add_hline(y=100, line_color="#3a3a3a", line_width=1)
    return fig


def _show_fig(fig, key):
    if fig is None:
        st.info("Install plotly (add `plotly` to requirements.txt) to see this chart.")
        return
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False}, key=key)


def _stock_rows(df: pd.DataFrame) -> list:
    return [{k: (None if (not isinstance(v, (list, tuple, dict, str)) and pd.isna(v)) else v) for k, v in r.items()}
            for r in df.to_dict("records")]


def _open_picker(symbols: list, key: str, on_open):
    if not on_open or not symbols:
        return
    a, b = st.columns([3, 1])
    sym = a.selectbox("Open a stock in Arka", symbols, key=f"{key}_sym", label_visibility="collapsed")
    if b.button("OPEN STOCK ▸", key=f"{key}_btn", use_container_width=True):
        on_open(str(sym))


# ═════════════════════════ view · ROTATION DESK ═════════════════════════
def _view_desk(res: dict, on_open):
    tbl, uni, bench = res["table"], res["universe"], res["bench_name"]
    u, u5 = uni.iloc[-1], uni.iloc[-6] if len(uni) > 5 else uni.iloc[0]
    acts = tbl["action"].value_counts()
    names = lambda a, n=3: ", ".join(tbl.loc[tbl.action == a, "sector"].head(n)) or "none confirmed"
    st.markdown(ui.kpi_strip([
        ("Market breadth", f"{u['above50']:.0f}%", f"stocks above their 50-EMA · {u['above50'] - u5['above50']:+.0f} pts in 5 days",
         GREEN if u["above50"] >= 50 else RED),
        ("Advance / decline", f"{int(u['adv'])} / {int(u['dec'])}", "stocks up / down on the last session", T2),
        ("Money tide", _num(u["up_turn5"], 0, "%"), "of 5-day turnover went into rising stocks",
         GREEN if (_f(u["up_turn5"]) or 0) >= 50 else RED),
        ("★ Rotating in", int(acts.get("ROTATING IN", 0)), _e(names("ROTATING IN")), GREEN),
        ("Leading", int(acts.get("LEADING", 0) + acts.get("STRETCHED", 0)), _e(names("LEADING")), "#8AD66B"),
        ("Weakening / lagging", int(acts.get("WEAKENING", 0) + acts.get("LAGGING", 0)),
         f"of {len(tbl)} sectors", RED)]), unsafe_allow_html=True)

    _h(f"1 · Action board · what to do with each sector (vs {_e(bench)})")
    st.markdown(_board(tbl, bench), unsafe_allow_html=True)
    moves = tbl[(tbl["in_phase"] <= 1) & ~tbl["phase_prev"].isin(["—", None]) & (tbl["phase_prev"] != tbl["phase"])]
    if len(moves):
        value = {"LEADING": 3, "IMPROVING": 2, "WEAKENING": 1, "LAGGING": 0}
        bits = []
        for r in moves.to_dict("records"):
            up = value.get(r["phase"], 0) > value.get(r["phase_prev"], 0) or (r["phase_prev"] == "LAGGING" and r["phase"] == "IMPROVING")
            bits.append(f'<span style="color:{GREEN if up else RED}">{"▲" if up else "▼"} <b>{_e(r["sector"])}</b></span> '
                        f'<span style="color:#777">{_e(str(r["phase_prev"]).title())} → {_e(str(r["phase"]).title())}</span>')
        st.markdown('<div class="rd-moves"><b style="color:#ddd">Changed phase this week:</b> ' + " &nbsp;·&nbsp; ".join(bits) + "</div>",
                    unsafe_allow_html=True)
    _note("<b>How the labels are decided.</b> ROTATING IN = improving or leading on the weekly rotation map, heading up-and-right, "
          "sector index above its 20-EMA, at least half its stocks above their 20-EMA, most of the week's turnover going into rising "
          "stocks, score 55+, not stretched and no distribution warning. LEADING = top-right of the map. STRETCHED = leading but more "
          f"than {rot.STRETCH_PCT:.0f}% above its 20-EMA or RSI above {rot.STRETCH_RSI:.0f}. WATCH = improving but not confirmed (or "
          "bottoming with breadth and money turning). WEAKENING / LAGGING = right-bottom / left-bottom of the map.")

    _h("2 · Relative rotation graph · where every sector sits and where it is heading")
    c1, c2, c3 = st.columns([1.2, 1.6, 4])
    tf = c1.radio("Timeframe", ["Weekly", "Daily"], horizontal=True, key="mf6_rrg_tf")
    lab = c2.radio("Names", ["Key", "All", "None"], horizontal=True, key="mf6_rrg_lbl")
    fq = "W" if tf == "Weekly" else "D"
    summ, tails = res["rrg"][fq]
    t2 = tbl.copy()
    t2["phase"] = [summ.get(s, {}).get("phase", "—") for s in t2["sector"]]
    options = tbl["sector"].tolist()
    key = "mf6_rrg_hl"
    if key not in st.session_state:
        st.session_state[key] = list(dict.fromkeys(tbl.loc[tbl.action.isin(["ROTATING IN", "WATCH"]), "sector"].head(4).tolist()
                                                   + options[:2]))[:6]
    st.session_state[key] = [v for v in st.session_state[key] if v in options]
    hl = c3.multiselect("Show the path (tail) for", options, key=key)
    _show_fig(_rrg_fig(t2, tails, hl, lab, bench, "weeks" if fq == "W" else "sessions"), "mf6_rrg")
    _note("Read it clockwise: <b>IMPROVING → LEADING → WEAKENING → LAGGING → IMPROVING</b>. Right of centre = beating the benchmark, "
          "above centre = that out-performance is accelerating. The tail shows the last weeks: a tail pointing up-and-right is "
          "gaining, down-and-left is losing. The best entries are usually sectors crossing from IMPROVING into LEADING with a long, "
          "steady tail; sectors far out in LEADING have usually already moved. Weekly is the standard view for rotation; daily is noisier.")

    _h("3 · Rotation score history · who is climbing, who is fading")
    hist = res["hist"]["score"].reindex(tbl["sector"])
    act = tbl.set_index("sector")["action"]

    def delta(s):
        a, b = _f(hist.loc[s, "Today"]), _f(hist.loc[s, "2W ago"])
        if a is None or b is None:
            return "—"
        d = a - b
        return f'<span style="color:{GREEN if d > 5 else RED if d < -5 else T2}">{d:+.0f}</span>'

    st.markdown(_heat_table(hist, "score", {"Δ 2W": delta, "Action": lambda s: _pill(act[s], ACTION_C.get(act[s], T3))}),
                unsafe_allow_html=True)
    with st.expander("RANK RACE · weekly rank by rotation score, last 12 weeks (1 = strongest)"):
        rw = res["rank_weeks"].reindex(tbl["sector"]).dropna(how="all")
        st.markdown(_heat_table(rw, "rank"), unsafe_allow_html=True)
        st.caption("A sector whose row turns from red to green over several weeks is where money has been rotating in.")
    _note("Score (0-100) ranks sectors against each other: relative strength 35% (1W, 1M, 3M vs the benchmark), breadth 25% "
          "(% of stocks above their 50 / 20-EMA and its change), money 25% (share of turnover in rising stocks, change in turnover "
          "share, ₹-weighted CMF) and acceleration 15%. It is relative: in a falling market the top score is the least bad sector.")

    _h("4 · Sector detail · why it is rated this way, and its strongest stocks")
    pick = st.selectbox("Sector", options, key="mf6_desk_pick", format_func=lambda s: f"{s} · {act.get(s, '')}")
    r = tbl[tbl["sector"] == pick].iloc[0].to_dict()
    a, b = st.columns([1, 1.7], gap="medium")
    a.markdown(_card(r, bench, full=True), unsafe_allow_html=True)
    sub = res["stocks"][res["stocks"]["sector"] == pick].sort_values("rs_rating", ascending=False)
    with b:
        ui.show_table([ui.C("Stock", "symbol", "sector", sub="company"), ui.C("Price", "price", "num", pre="₹"),
                       ui.C("1W", "r1w", "pct", scale=6.0), ui.C("1M", "r1m", "pct", scale=12.0),
                       ui.C("RS", "rs_rating", "bar", max=99, color=CYAN, tip="RS rating 0-99 across the whole universe"),
                       ui.C("RSI", "rsi", "rsi"), ui.C("EMA 20·50·200", t="ema", spans=(20, 50, 200)),
                       ui.C("From 52W high", "from_hi", "pct", d=1, scale=20.0), ui.C("Vol ×", "vol_x", "num", d=2, suf="×")],
                      _stock_rows(sub.head(12)), max_height=520)
        _open_picker(sub["symbol"].tolist(), "mf6_desk_open", on_open)


# ═════════════════════════ view · SECTOR RANKING ═════════════════════════
RANK_COLS = {
    "sector": ("Sector", "text"), "stocks": ("Stocks", "int"), "action": ("Action", "action"), "phase": ("Phase", "phase"),
    "score": ("Score", "score"), "r1d": ("1D %", "spct"), "r1w": ("1W %", "spct"), "r1m": ("1M %", "spct"),
    "r3m": ("3M %", "spct"), "r6m": ("6M %", "spct"), "ex1w": ("RS 1W (pp)", "spp"), "ex1m": ("RS 1M (pp)", "spp"),
    "ex3m": ("RS 3M (pp)", "spp"), "ex6m": ("RS 6M (pp)", "spp"), "dist20": ("vs 20-EMA %", "spct1"),
    "dist50": ("vs 50-EMA %", "spct1"), "idx_rsi": ("RSI", "rsi"), "dd52": ("From 52W high %", "spct1"),
    "pct20": ("% > 20-EMA", "heat"), "pct50": ("% > 50-EMA", "heat"), "pct200": ("% > 200-EMA", "heat"),
    "dpct20_5": ("Δ % > 20-EMA (5D)", "spp0"), "up_pct1d": ("Up-turnover 1D", "heat"), "up_pct5": ("Up-turnover 5D", "heat"),
    "turnover_cr": ("Turnover ₹Cr", "cr"), "share": ("Share %", "num2"), "dshare_d": ("Δ Share 1D", "spp2"),
    "dshare_w": ("Δ Share 1W", "spp2"), "rel_turn": ("Turnover × 20D", "ratio"), "cmf": ("CMF 20", "s2"),
    "dcmf5": ("Δ CMF 5D", "s2"), "money_signal": ("Money signal", "money"), "cmf_trend": ("CMF trend", "cmft"),
    "adv": ("Adv", "int"), "dec": ("Dec", "int"), "ad_net": ("A/D net 1D", "s2"), "ad_net5": ("A/D net 5D", "s2"),
    "avg_rsi": ("Avg stock RSI", "rsi"), "bo10": ("% breakout 10D", "pct0"), "nh5": ("New highs 5D", "int"),
    "near_hi": ("% near 52W high", "pct0"), "leaders": ("Leaders", "text"),
}
RANK_SETS = {
    "OVERVIEW": ["sector", "stocks", "action", "phase", "score", "r1d", "r1w", "r1m", "ex1m", "ex3m", "pct50", "up_pct5",
                 "dshare_w", "leaders"],
    "PERFORMANCE": ["sector", "action", "r1d", "r1w", "r1m", "r3m", "r6m", "ex1w", "ex1m", "ex3m", "ex6m", "dist20", "dist50",
                    "idx_rsi", "dd52"],
    "MONEY": ["sector", "action", "turnover_cr", "share", "dshare_d", "dshare_w", "rel_turn", "up_pct1d", "up_pct5", "cmf",
              "dcmf5", "money_signal", "cmf_trend"],
    "BREADTH": ["sector", "stocks", "adv", "dec", "ad_net", "ad_net5", "pct20", "dpct20_5", "pct50", "pct200", "avg_rsi", "bo10",
                "nh5", "near_hi"],
}


def _fm(fmt):
    return lambda v: "—" if _isna(v) else fmt.format(v)


_FORMATS = {"int": _fm("{:,.0f}"), "score": _fm("{:.0f}"), "spct": _fm("{:+.2f}%"), "spct1": _fm("{:+.1f}%"),
            "spp": _fm("{:+.1f}"), "spp0": _fm("{:+.0f}"), "spp2": _fm("{:+.2f}"), "rsi": _fm("{:.0f}"), "heat": _fm("{:.0f}%"),
            "cr": _fm("{:,.0f}"), "num2": _fm("{:.2f}"), "ratio": _fm("{:.2f}×"), "s2": _fm("{:+.2f}"), "pct0": _fm("{:.0f}%")}


def _css_signed(v):
    return "" if _isna(v) else f"color:{GREEN if v > 0 else RED if v < 0 else T2}"


def _css_heat(v):
    return "" if _isna(v) else f"background-color:{_heat_bg((v - 50) / 50)};color:#f2f2f2"


def _css_rsi(v):
    return "" if _isna(v) else f"color:{ui.rsi_color(float(v))}"


def _css_label(cmap):
    return lambda v: f"color:{cmap.get(v, T2)};font-weight:700"


_STYLERS = {"spct": _css_signed, "spct1": _css_signed, "spp": _css_signed, "spp0": _css_signed, "spp2": _css_signed,
            "s2": _css_signed, "heat": _css_heat, "score": _css_heat, "rsi": _css_rsi, "action": _css_label(ACTION_C),
            "phase": _css_label(PHASE_C), "money": _css_label(MONEY_C), "cmft": _css_label(CMF_C)}


def _styled(df: pd.DataFrame, keys: list):
    disp = df[keys].rename(columns={k: RANK_COLS[k][0] for k in keys})
    sty = disp.style.format({RANK_COLS[k][0]: _FORMATS[RANK_COLS[k][1]] for k in keys if RANK_COLS[k][1] in _FORMATS})
    for k in keys:
        lab, kind = RANK_COLS[k]
        fn = _STYLERS.get(kind)
        if fn:
            sty = (getattr(sty, "map", None) or sty.applymap)(fn, subset=[lab])
    return sty


GLOSSARY = """
- **Action / Phase** — see ROTATION DESK. Phase is the quadrant on the weekly rotation map.
- **Score** — 0-100 relative ranking: relative strength 35%, breadth 25%, money 25%, acceleration 15%.
- **RS (pp)** — sector return minus benchmark return over the period, in percentage points.
- **vs 20-EMA / 50-EMA** — how far the sector index is from its average. Above +8% = stretched.
- **% > 20 / 50 / 200-EMA** — share of the sector's stocks above their own average (participation).
- **Up-turnover** — share of the sector's ₹ turnover that traded in stocks that closed up. Above 55% = buyers in control.
- **Share % / Δ Share** — sector turnover as % of all market turnover, and its change vs the previous day / previous week (5-day averages).
- **Turnover × 20D** — today's turnover ÷ its 20-day average. Above 1.3× = unusual activity.
- **CMF 20** — Chaikin Money Flow over 20 sessions, ₹-weighted across stocks. Above +0.05 accumulation-like, below −0.05 distribution-like.
- **Money signal** — BUYING ON VOLUME = rising share of turnover and mostly up-turnover; SELLING ON VOLUME = rising share but mostly down-turnover.
- **% breakout 10D** — stocks that closed above their previous 20-day high at least once in the last 10 sessions.
- **New highs 5D / % near 52W high** — stocks at a new 52-week closing high in the last 5 sessions / within 5% of it.
"""


def _view_ranking(res: dict):
    tbl = res["table"].copy()
    c1, c2 = st.columns([3, 1.4])
    cset = c1.radio("Columns", list(RANK_SETS), horizontal=True, key="mf6_rank_set")
    fam = c2.selectbox("Family", ["All"] + sorted(tbl["family"].dropna().astype(str).unique()), key="mf6_rank_fam")
    if fam != "All":
        tbl = tbl[tbl["family"] == fam]
    st.caption("Click any column header to sort. Rows start in rotation-score order. Hover a column for the full name.")
    st.dataframe(_styled(tbl, RANK_SETS[cset]), use_container_width=True, hide_index=True,
                 height=min(42 + 35 * len(tbl), 940))
    with st.expander("WHAT EVERY COLUMN MEANS"):
        st.markdown(GLOSSARY)
    st.download_button("⤓ DOWNLOAD SECTOR TABLE (CSV)", tbl.drop(columns=["why", "warn"], errors="ignore").to_csv(index=False),
                       "arka_sector_rotation.csv", "text/csv", key="mf6_rank_dl")


# ═════════════════════════ view · MONEY FLOW ═════════════════════════
def _view_money(res: dict):
    tbl = res["table"]
    _h("1 · Where turnover is moving · share of all market turnover and which side it traded on")
    rows = _stock_rows(tbl.sort_values("dshare_w", ascending=False, na_position="last"))
    for r in rows:
        r["sig_h"] = _pill(r.get("money_signal") or "—", MONEY_C.get(r.get("money_signal"), T3))
        r["dsd_h"], r["dsw_h"], r["cmf_h"] = _sg(r.get("dshare_d")), _sg(r.get("dshare_w")), _sg(r.get("cmf"))
    ui.show_table([ui.C("#", t="rank"), ui.C("Sector", "sector", "sector", sub="stocks", sub_fmt="{:.0f} stocks"),
                   ui.C("Signal", "sig_h", "html"), ui.C("Turnover ₹Cr", "turnover_cr", "num", d=0),
                   ui.C("× 20D avg", "rel_turn", "num", d=2, suf="×"), ui.C("Share %", "share", "num", d=2),
                   ui.C("Δ Share 1D", "dsd_h", "html", al="r"), ui.C("Δ Share 1W", "dsw_h", "html", al="r"),
                   ui.C("Up-turnover 1D", "up_pct1d", "bar", suf="%", color=CYAN),
                   ui.C("Up-turnover 5D", "up_pct5", "bar", suf="%", color=GREEN),
                   ui.C("1W", "r1w", "pct", scale=4.0), ui.C("CMF 20", "cmf_h", "html", al="r")], rows, max_height=760)
    _note("A rising <b>share</b> of turnover only says activity moved there; the <b>up-turnover</b> column says which side won. "
          "BUYING ON VOLUME (more of the market's money AND most of it in rising stocks) is the footprint of rotation in. "
          "SELLING ON VOLUME is the opposite: heavy trading while prices fall. Every trade has a buyer and a seller, so this is "
          "evidence of who was more aggressive, not a count of who bought.")

    _h("2 · Chaikin money flow (₹-weighted, 20 sessions) · 1 month ago → today")
    cm = res["hist"]["cmf"].reindex(tbl.sort_values("cmf", ascending=False)["sector"])
    trend = tbl.set_index("sector")["cmf_trend"]
    st.markdown(_heat_table(cm, "cmf", {"Trend": lambda s: _pill(trend.get(s, "—"), CMF_C.get(trend.get(s), T3))}),
                unsafe_allow_html=True)
    _note("Green = closes landing in the upper part of the day's range on heavy ₹ volume (accumulation-like); red = the lower part "
          "(distribution-like). The most useful pattern is a row turning from red to green while the sector's relative strength "
          "improves: money arriving before the price trend is obvious.")

    _h("3 · Turnover share trend · 5-day average, last 90 sessions")
    opts = tbl["sector"].tolist()
    default = tbl.sort_values("dshare_w", ascending=False)["sector"].head(5).tolist()
    pick = st.multiselect("Sectors", opts, default=default, key="mf6_share_pick")
    if pick:
        st.line_chart(res["share"][pick].rolling(5).mean().tail(90).rename_axis("date"))


# ═════════════════════════ view · BREADTH ═════════════════════════
def _breadth_flag(r) -> str:
    p20, d5 = _f(r.get("pct20")), _f(r.get("dpct20_5"))
    if p20 is None or d5 is None:
        return _pill("—", T3)
    if p20 >= 70 and d5 >= 30:
        return _pill("BREADTH THRUST", GREEN)
    if d5 >= 10:
        return _pill("BROADENING", CYAN)
    if d5 <= -25:
        return _pill("COLLAPSING", RED)
    if d5 <= -10:
        return _pill("NARROWING", AMBER)
    return _pill("STEADY", T2)


def _view_breadth(res: dict):
    tbl = res["table"]
    _h("1 · Participation by sector · how many of its stocks are actually moving")
    rows = _stock_rows(tbl.sort_values("dpct20_5", ascending=False, na_position="last"))
    for r in rows:
        r["flag"], r["d5_h"], r["d20_h"] = _breadth_flag(r), _sg(r.get("dpct20_5"), 0), _sg(r.get("dpct50_20"), 0)
        r["ad_h"], r["ad5_h"] = _sg(r.get("ad_net")), _sg(r.get("ad_net5"))
    ui.show_table([ui.C("#", t="rank"), ui.C("Sector", "sector", "sector", sub="stocks", sub_fmt="{:.0f} stocks"),
                   ui.C("Breadth", "flag", "html"), ui.C("% > 20-EMA", "pct20", "bar", suf="%", color=CYAN),
                   ui.C("Δ 5D", "d5_h", "html", al="r"), ui.C("% > 50-EMA", "pct50", "bar", suf="%", color=GREEN),
                   ui.C("Δ 20D", "d20_h", "html", al="r", tip="Change in % above the 50-EMA over 20 sessions"),
                   ui.C("% > 200-EMA", "pct200", "bar", suf="%", color=PURPLE), ui.C("Adv", "adv", "int"), ui.C("Dec", "dec", "int"),
                   ui.C("A/D 1D", "ad_h", "html", al="r"), ui.C("A/D 5D", "ad5_h", "html", al="r"), ui.C("Avg RSI", "avg_rsi", "rsi"),
                   ui.C("% breakout 10D", "bo10", "num", d=0, suf="%"), ui.C("New highs 5D", "nh5", "int"),
                   ui.C("% near 52W high", "near_hi", "num", d=0, suf="%")], rows, max_height=760)
    _note("Participation usually turns before the sector index does. A <b>BREADTH THRUST</b> (70%+ of stocks above the 20-EMA after a "
          "30-point jump in 5 sessions) is one of the strongest early signs that a group is being accumulated. Leadership with "
          "<b>NARROWING</b> breadth means a few stocks are carrying the sector — late-stage behaviour.")

    _h("2 · % of stocks above the 20-EMA · 1 month ago → today")
    st.markdown(_heat_table(res["hist"]["pct20"].reindex(tbl["sector"]), "pct"), unsafe_allow_html=True)

    _h("3 · Whole-market breadth · % of all stocks above their 20 / 50 / 200-EMA")
    u = res["universe"].tail(160)
    st.line_chart(u[["above20", "above50", "above200"]].rename(
        columns={"above20": "Above 20-EMA %", "above50": "Above 50-EMA %", "above200": "Above 200-EMA %"}).rename_axis("date"))
    st.caption("Rotation works best when the whole market is healthy (50-EMA breadth above ~50% and rising). In weak markets, "
               "the 'best' sector is often just the one falling least.")


# ═════════════════════════ view · STOCKS ═════════════════════════
def _view_stocks(res: dict, on_open):
    tbl, stk = res["table"], res["stocks"]
    act = tbl.set_index("sector")["action"]
    c1, c2, c3 = st.columns([2.2, 1.4, 1.8])
    pick = c1.selectbox("Sector", tbl["sector"].tolist(), key="mf6_stk_sector", format_func=lambda s: f"{s} · {act.get(s, '')}")
    sort = c2.selectbox("Sort by", ["RS rating", "1D %", "1W %", "1M %", "Turnover", "Volume ×", "Near 52W high"], key="mf6_stk_sort")
    show = c3.radio("Show", ["All", "Uptrend only", "Breakouts (10D)"], horizontal=True, key="mf6_stk_show")
    r = tbl[tbl["sector"] == pick].iloc[0].to_dict()
    st.markdown(_card(r, res["bench_name"], full=True), unsafe_allow_html=True)
    sub = stk[stk["sector"] == pick].copy()
    if show == "Uptrend only":
        sub = sub[(sub["ab50"] == 1) & (sub["ab200"] == 1)]
    elif show == "Breakouts (10D)":
        sub = sub[sub["bo10"] == 1]
    col = {"RS rating": "rs_rating", "1D %": "r1d", "1W %": "r1w", "1M %": "r1m", "Turnover": "avg_turn_cr",
           "Volume ×": "vol_x", "Near 52W high": "from_hi"}[sort]
    sub = sub.sort_values(col, ascending=False, na_position="last")
    view = pd.DataFrame({
        "Symbol": sub["symbol"], "Company": sub["company"], "Price": sub["price"], "1D %": sub["r1d"], "1W %": sub["r1w"],
        "1M %": sub["r1m"], "3M %": sub["r3m"], "RS rating": sub["rs_rating"], "vs market 1M (pp)": sub["ex1m"],
        "RSI": sub["rsi"], "Trend": sub["trend"], "vs 20-EMA %": sub["dist20"], "From 52W high %": sub["from_hi"],
        "Volume ×": sub["vol_x"], "Avg turnover ₹Cr": sub["avg_turn_cr"], "Breakout 10D": np.where(sub["bo10"] == 1, "✔", ""),
        "CMF 20": sub["cmf"]}).reset_index(drop=True)
    cc = st.column_config
    cfg = {"Price": cc.NumberColumn(format="₹%.2f"), "1D %": cc.NumberColumn(format="%+.2f%%"),
           "1W %": cc.NumberColumn(format="%+.2f%%"), "1M %": cc.NumberColumn(format="%+.2f%%"),
           "3M %": cc.NumberColumn(format="%+.2f%%"),
           "RS rating": cc.ProgressColumn(min_value=0, max_value=99, format="%d", help="0-99 vs every stock in the universe (1M/3M/6M)"),
           "vs market 1M (pp)": cc.NumberColumn(format="%+.1f"), "RSI": cc.NumberColumn(format="%.0f"),
           "vs 20-EMA %": cc.NumberColumn(format="%+.1f%%"), "From 52W high %": cc.NumberColumn(format="%+.1f%%"),
           "Volume ×": cc.NumberColumn(format="%.2f×", help="Today's volume ÷ 20-day average"),
           "Avg turnover ₹Cr": cc.NumberColumn(format="%.1f"), "CMF 20": cc.NumberColumn(format="%+.2f")}
    st.caption(f"{len(view)} stocks. Click a row, then OPEN STOCK. The best rotation candidates are usually high RS rating, "
               "uptrend, within ~10% of the 52-week high, in a sector that is ROTATING IN or LEADING.")
    event = st.dataframe(view, use_container_width=True, hide_index=True, column_config=cfg, height=min(42 + 35 * len(view), 760),
                         on_select="rerun", selection_mode="single-row", key="mf6_stk_tbl")
    rows = getattr(getattr(event, "selection", None), "rows", []) if event is not None else []
    if rows and on_open:
        sym = str(view.iloc[int(rows[0])]["Symbol"])
        if st.button(f"OPEN {sym} ▸", type="primary", key="mf6_stk_open"):
            on_open(sym)


# ═════════════════════════ view · NSE INDICES ═════════════════════════
@st.cache_data(ttl=6 * 3600, show_spinner=False, max_entries=2)
def _official(bucket: int):
    if yf is None:
        raise RuntimeError("yfinance is not installed")
    frames = _yf_batch(list(OFFICIAL.values()) + ["^NSEI"], "3y", chunk=50)
    if "^NSEI" not in frames:
        raise RuntimeError("Yahoo returned no NIFTY 50 history")
    closes = pd.DataFrame({n: rot.norm_series(frames[t]["Close"]) for n, t in OFFICIAL.items() if t in frames})
    last = closes.apply(lambda c: c.last_valid_index())
    closes = closes[[c for c in closes.columns if last[c] is not None and (last.max() - last[c]).days <= 10]]
    return closes, rot.norm_series(frames["^NSEI"]["Close"])


def _view_indices():
    _note("Official NSE sector indices (Yahoo daily closes) on the same weekly rotation map, measured against NIFTY 50. They are "
          "cap-weighted and broad, so they move later than the stock-built sectors — use them to confirm, not to find, rotation.")
    if st.button("LOAD / REFRESH NSE INDICES", type="primary", key="mf6_ix_load"):
        if st.session_state.get("mf6_ix_ready"):
            _official.clear()
        st.session_state["mf6_ix_ready"] = True
    if not st.session_state.get("mf6_ix_ready"):
        return
    with st.spinner("Downloading sector index history…"):
        try:
            closes, bench = _official(int(time.time() // (6 * 3600)))
            tbl, tails = rot.index_view(closes, bench)
        except Exception as exc:
            st.error(f"Index history unavailable right now: {exc}. Yahoo may be rate-limiting; try again in a minute.")
            return
    tbl = tbl.sort_values("ex1m", ascending=False, na_position="last")
    rows = _stock_rows(tbl)
    for r in rows:
        r["ph_h"] = _pill(f'{rot.compass(r.get("heading"))} {r.get("phase")} {int(_f(r.get("in_phase")) or 0)}w',
                          PHASE_C.get(r.get("phase"), T3))
    _h("1 · Official sector indices vs NIFTY 50")
    ui.show_table([ui.C("#", t="rank"), ui.C("Index", "index", "sector"), ui.C("Weekly phase", "ph_h", "html"),
                   ui.C("Price", "price", "num", d=1), ui.C("1D", "r1d", "pct", scale=2.0), ui.C("1W", "r1w", "pct", scale=4.0),
                   ui.C("1M", "r1m", "pct", scale=8.0), ui.C("3M", "r3m", "pct", scale=15.0),
                   ui.C("RS 1M (pp)", "ex1m", "pct", scale=5.0), ui.C("RS 3M (pp)", "ex3m", "pct", scale=10.0),
                   ui.C("RSI", "rsi_d", "rsi"), ui.C("EMA 10·20·50·200", t="ema"), ui.C("vs 20", "dist20", "dist"),
                   ui.C("vs 50", "dist50", "dist", scale=12.0)], rows, max_height=760)
    _h("2 · Rotation map · official indices")
    t2 = tbl.rename(columns={"index": "sector"}).assign(score=np.nan, action="")
    _show_fig(_rrg_fig(t2, tails, [], "All", "NIFTY 50"), "mf6_ix_rrg")


# ═════════════════════════ view · FPI FLOWS ═════════════════════════
@st.cache_data(ttl=12 * 3600, show_spinner=False, max_entries=64)
def _nsdl_fetch(d: str):
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA})
    for url in rot.nsdl_urls(pd.Timestamp(d)):
        try:
            r = sess.get(url, timeout=12)
            if r.status_code == 200 and "Sector" in r.text:
                p = rot.parse_nsdl_sector_html(r.text)
                if not p.empty:
                    return p
        except requests.RequestException:
            continue
    return None


def _view_fpi():
    _note("NSDL publishes how much <b>foreign portfolio investors</b> put into or took out of each sector every fortnight — recorded "
          "money, not a price-based estimate. Limits: FPIs only (not mutual funds or DIIs), published with a lag, NSE's broad "
          "22-sector classification. Source: fpi.nsdl.co.in (Fortnightly Sector-wise FPI Investment Data).")
    c1, c2 = st.columns([1, 3])
    n = c1.selectbox("Fortnights", [12, 24, 48], index=1, key="mf6_fpi_n")
    with c2:
        st.write("")
        if st.button("LOAD FPI SECTOR FLOWS", type="primary", key="mf6_fpi_load"):
            dates = [str(d)[:10] for d in rot.nsdl_report_dates(int(n))]
            with st.spinner("Downloading NSDL fortnightly reports…"):
                with ThreadPoolExecutor(max_workers=6) as pool:
                    got = list(pool.map(_nsdl_fetch, dates))
            st.session_state["mf6_fpi"] = (rot.nsdl_panels(got), sum(g is not None for g in got), len(dates))
    got = st.session_state.get("mf6_fpi")
    if not got:
        return
    (net, auc), ok, tried = got
    if net.empty:
        st.warning(f"No NSDL report could be read ({ok} of {tried}). The site may be blocking this server or its file names changed.")
        return
    st.caption(f"{ok} of {tried} reports loaded · latest fortnight ending {str(net.index[-1])[:10]} · ₹ crore, equity.")
    broad = {rot.norm_name(k): v for k, v in (st.session_state.get("mf_broad_rs") or {}).items()}
    rows = []
    for s in net.columns:
        x = net[s].dropna()
        a = auc[s].dropna() if s in auc else pd.Series(dtype=float)
        aum = float(a.iloc[-1]) if len(a) else np.nan
        f2, f6, f12 = x.tail(2).sum(), x.tail(6).sum(), x.tail(12).sum()
        streak = 0
        for v in x.iloc[::-1]:
            if streak >= 0 and v > 0:
                streak += 1
            elif streak <= 0 and v < 0:
                streak -= 1
            else:
                break
        rows.append({"sector": s, "last": float(x.iloc[-1]) if len(x) else np.nan, "f2": f2, "f6": f6, "f12": f12,
                     "pct_hold": f6 / aum * 100 if aum and aum > 0 else np.nan, "streak": streak, "aum": aum,
                     "price_rs": broad.get(rot.norm_name(s))})
    tb = pd.DataFrame(rows).sort_values("pct_hold", ascending=False, na_position="last")

    def cr(v):
        v = _f(v)
        if v is None:
            return '<span class="mf-na">—</span>'
        return f'<span class="mf-pct" style="color:{GREEN if v >= 0 else RED};background:{ui._heat(v, 20000)}">{v:+,.0f}</span>'

    recs = _stock_rows(tb)
    for r in recs:
        for k in ("last", "f2", "f6", "f12"):
            r[k + "_h"] = cr(r.get(k))
        sv = int(r.get("streak") or 0)
        r["streak_h"] = (f'<span class="mf-chip {"up" if sv > 0 else "dn"}">{"▲" if sv > 0 else "▼"} {abs(sv)}</span>'
                         if sv else '<span class="mf-na">0</span>')
    cols = [ui.C("#", t="rank"), ui.C("Sector", "sector", "sector"), ui.C("Last fortnight", "last_h", "html", al="r"),
            ui.C("1M ₹Cr", "f2_h", "html", al="r"), ui.C("3M ₹Cr", "f6_h", "html", al="r"), ui.C("6M ₹Cr", "f12_h", "html", al="r"),
            ui.C("3M % of holdings", "pct_hold", "pct", scale=2.0, tip="3-month net flow ÷ FPI holdings in the sector"),
            ui.C("Streak", "streak_h", "html", tip="+3 = three fortnights of buying in a row; −2 = two of selling"),
            ui.C("FPI holdings ₹Cr", "aum", "num", d=0)]
    if any(r.get("price_rs") is not None for r in recs):
        cols.append(ui.C("Price RS 1M (pp)", "price_rs", "pct", d=1, scale=5.0,
                         tip="Median stock in this NSE sector vs the market over 1 month (from the sector data you loaded)"))
    _h("1 · FPI money by sector · net equity investment")
    ui.show_table(cols, recs, max_height=760)
    _note("'% of holdings' scales flows by how much FPIs already own, so ₹2,000 Cr into a small sector counts for more than into "
          "Financial Services. The strongest confirmation is when foreign money (positive streak) and price (positive RS) agree.")
    if go is not None:
        _h(f"2 · Flow heat-map · last 12 fortnights (₹ Cr)")
        hm = net.tail(12).T
        hm = hm.loc[hm.sum(axis=1).sort_values(ascending=False).index]
        z = hm.values
        lim = float(np.nanpercentile(np.abs(z), 90)) or 1.0
        fig = go.Figure(go.Heatmap(z=z, x=[d.strftime("%d %b %y") for d in hm.columns], y=list(hm.index),
                                   colorscale=[[0, RED], [0.5, "#151515"], [1, GREEN]], zmin=-lim, zmax=lim, showscale=False,
                                   text=np.round(z / 100) / 10, texttemplate="%{text}k",
                                   hovertemplate="%{y} · %{x}: ₹%{z:,.0f} Cr<extra></extra>"))
        fig.update_yaxes(autorange="reversed")
        ui._dark(fig, 26 * len(hm) + 70, False)
        _show_fig(fig, "mf6_fpi_hm")
    st.download_button("⤓ FPI FLOWS (CSV)", net.to_csv(), "nsdl_fpi_sector_flows.csv", "text/csv", key="mf6_fpi_dl")


# ═════════════════════════ page ═════════════════════════
def _view_selector() -> str:
    if st.session_state.get("mf6_view") not in VIEWS:
        st.session_state["mf6_view"] = VIEWS[0]
    with st.container(key="mf_viewbar"):
        return st.radio("Money Flow view", VIEWS, horizontal=True, key="mf6_view", label_visibility="collapsed")


def _universe_missing():
    st.warning("The stock universe could not be loaded: NSE and niftyindices.com are refusing this server and no copy is in "
               "the repo yet. The app retries every 10 minutes.")
    _note("<b>Permanent fix (2 minutes):</b> download "
          '<a href="https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv" target="_blank">ind_nifty500list.csv</a> and '
          '<a href="https://www.niftyindices.com/IndexConstituent/ind_niftymicrocap250_list.csv" target="_blank">ind_niftymicrocap250_list.csv</a> '
          "on your computer and upload them to a <code>data</code> folder in the GitHub repo. Money Flow reads them first.")
    if st.button("RETRY NOW", key="mf6_retry"):
        _ARCH_DOWN.clear()
        _universe_cached.clear()
        st.rerun()


def _status(res: dict, source: str, err):
    q = res["quality"]
    today = datetime.now(IST).date().isoformat()
    w = "equal-weight" if q.get("weighting") == "equal" else "√turnover-weighted"
    st.markdown(f'<div class="rd-status"><span>AS OF <b>{q["asof"]}</b>{" (intraday)" if q["asof"] == today and _market_live() else " close"}</span>'
                f'<span><b>{q["in_sectors"]}</b> stocks in <b>{q["sectors"]}</b> sectors</span><span>vs <b>{_e(res["bench_name"])}</b></span>'
                f'<span>{w} sector indices</span><span>coverage <b>{q["coverage"] * 100:.0f}%</b></span></div>', unsafe_allow_html=True)
    if q["asof"] == today and _market_live():
        st.caption("Market is open: today's bar is still forming, so 1-day turnover and money readings compare a partial day "
                   "with full days. The 5-day, weekly and monthly readings are the reliable ones intraday.")
    if err:
        st.warning(f"Showing the last good load — the refresh failed ({err}).")
    with st.expander("DATA CHECK · what was loaded, skipped and why"):
        st.markdown(
            f"- Universe: `{source}` · requested **{q['stocks_requested']}**, price history for **{q['stocks_loaded']}**, "
            f"used **{q['stocks_used']}** (liquid), in sector tables **{q['in_sectors']}**\n"
            f"- Excluded as illiquid (median turnover under ₹{q['min_turnover_cr']:g} Cr/day): **{len(q['illiquid'])}**\n"
            f"- No Yahoo data: **{len(q['missing'])}** {', '.join(q['missing'][:15])}{'…' if len(q['missing']) > 15 else ''}\n"
            f"- Stale tickers dropped: **{len(q['stale'])}** {', '.join(q['stale'][:10])}\n"
            f"- Half-loaded sessions dropped: {', '.join(q['dropped_sessions']) or 'none'} · bad ticks repaired: {q['bad_ticks']}\n"
            f"- Small groups folded into their family: {', '.join(q['folded_groups']) or 'none'}\n"
            f"- Sector of every stock: see `sector_map.py` (edit it, or add `data/sector_map.csv` with a `group` column to override).")


def render_money_flow(on_open=None):
    """Arka MONEY FLOW workspace."""
    _style()
    view = _view_selector()
    if view == "NSE INDICES":
        _view_indices()
        return _footer()
    if view == "FPI FLOWS":
        _view_fpi()
        return _footer()

    try:
        meta, source = _universe_cached()
    except Exception:
        meta, source = None, ""
    if meta is None:
        return _universe_missing()

    ready = st.session_state.get("mf6_ready", False)
    c1, c2, c3, c4 = st.columns([1.3, 2.4, 1.9, 1.2], gap="small")
    with c1:
        st.write("")
        load = st.button("LOAD / REFRESH" if ready else "LOAD SECTOR DATA", type="primary", use_container_width=True, key="mf6_load")
    bench = c2.radio("Compare against", ["MARKET", "NIFTY 50", "NIFTY 500"], horizontal=True, key="mf6_bench", help=BENCH_HELP)
    wt = c3.radio("Sector index", ["Equal weight", "Turnover weight"], horizontal=True, key="mf6_wt",
                  help="Equal weight = every stock counts the same (best for spotting rotation). Turnover weight = √turnover, "
                       "closer to how the big money is positioned.")
    min_turn = c4.selectbox("Min turnover / day", [0.25, 0.5, 1.0, 2.0, 5.0], index=1, key="mf6_minturn",
                            format_func=lambda v: f"₹{v:g} Cr")
    if load:
        if ready:
            _prices.clear()
            _analysis.clear()
        st.session_state["mf6_ready"] = ready = True
    if not ready:
        _note(f"Press <b>LOAD SECTOR DATA</b>. Arka downloads ~2 years of daily prices for <b>{len(meta)}</b> stocks "
              f"({_e(source)}) from Yahoo in batches — the first load takes 1-3 minutes, then it is cached for everyone "
              "(30 minutes in market hours, 3 hours after the close).")
        return _footer()

    with st.spinner(f"Loading {len(meta)} stocks and building {meta['group'].nunique()} sectors…"):
        try:
            res, err = _get_analysis(tuple(meta["symbol"]), bench, "turnover" if wt.startswith("Turnover") else "equal", float(min_turn))
        except Exception as exc:
            st.error(f"Sector data unavailable right now: {type(exc).__name__}: {exc}. Yahoo may be rate-limiting; try again in a minute.")
            return _footer()
    _publish(res)
    _status(res, source, err)

    if view == "ROTATION DESK":
        _view_desk(res, on_open)
    elif view == "SECTOR RANKING":
        _view_ranking(res)
    elif view == "MONEY FLOW":
        _view_money(res)
    elif view == "BREADTH":
        _view_breadth(res)
    else:
        _view_stocks(res, on_open)
    _footer()


def _footer():
    with st.expander("HOW THIS WORKS · method, sources and limits"):
        st.markdown("""
**Universe.** NIFTY 500 + NIFTY MICROCAP 250 (or NIFTY TOTAL MARKET if that file is in `data/`), minus illiquid stocks.
Each stock is placed in one of ~50 rotation groups (`sector_map.py`).

**Sector index.** Equal-weight average of the members' daily returns (moves above ±20% in a day are clipped), compounded.
**Benchmark MARKET** is built the same way from every stock, so the comparison is like-for-like.

**Rotation map (RRG).** Weekly closes. RS = sector ÷ benchmark. RS-Ratio = z-score of the smoothed RS over 12 weeks, centred on 100.
RS-Momentum = the same treatment applied to RS-Ratio's rate of change. This follows the published open approximations of the
JdK method; the exact JdK formula is proprietary, so values differ slightly from paid RRG tools, but the quadrants and direction agree.

**Breadth.** % of the sector's stocks above their own 20 / 50 / 200-day EMA, advance-decline, breakouts above the prior 20-day high,
new 52-week closing highs.

**Money.** ₹ turnover = close × volume. Share = sector turnover ÷ all turnover. Up-turnover = turnover in stocks that closed up.
CMF = Σ(close-location × ₹ turnover) ÷ Σ ₹ turnover over 20 sessions, summed across the sector.

**FPI flows.** NSDL fortnightly sector-wise report (equity, ₹ crore).

**Limits.** Yahoo data is delayed and occasionally has gaps; turnover is computed, not exchange-reported; every trade has a buyer
and a seller, so "money flow" here means which side was more aggressive. Signals are research evidence, not forecasts or advice.
""")

"""
chart_engine.py — Arka Trades pro chart (v3)

A full trading chart, laid out the way traders expect from a professional terminal, built on TradingView's open-source
"Lightweight Charts" library (v4.2.0) and drawn in the browser. Nothing here reruns Streamlit or downloads again:
every button works instantly on the data already sent.

What v3 adds over v2
  * Top toolbar: symbol, Daily / Weekly / Monthly candles, chart type (candles, hollow candles, bars, line, area,
    Heikin Ashi), an Indicators menu, snapshot, settings and full screen.
  * Volume on the chart with its own average line, and the volume in the legend.
  * Indicators worked out in the browser with your own lengths: five moving-average slots (EMA or SMA), Bollinger
    bands, RSI (Wilder, the same maths as TradingView) and MACD, each lower study in its own pane, all panes scrolling
    and zooming together with a shared crosshair.
  * Drawing tools: horizontal line, trend line and a measure tool (price change, percent, bars, days). Drawings are
    remembered per stock on this device.
  * Bottom bar: 1M … 5Y / All ranges, percent scale, log scale, auto fit and a "latest" button.
  * Levels: previous day high / low and 52-week high / low.
  * v4 — Relative strength line, in the style swing traders know from the TraderLion and MarketSmith charts: the stock
    divided by an index you choose, drawn over the price chart (or in its own pane). The line changes colour with its
    direction (rising = stronger than the index, falling = weaker). A pink dot marks an RS new high BEFORE the price makes
    one; a small blue dot marks an RS new high together with price. The legend shows an RS score (above 100 = beating
    the index over the past year, recent months weighted more). Index, placement, colours and dots are in the settings.
  * Full screen: fills the app window (and the whole screen where the browser allows it). Esc leaves it.
  * Settings: the gear on the chart, or Account → Chart settings (render_chart_settings). Saved in this browser, so
    every chart on this device opens the way you set it.

Usage:  render_pro_chart("RELIANCE")            render_chart_settings()
"""
from __future__ import annotations

import json
import re

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from data_engine import get_history

CHART_VERSION = 5

# Indices the relative-strength line can be measured against (label → Yahoo tickers to try, in order).
RS_INDICES = {
    "NIFTY 50": ("^NSEI",), "NIFTY 500": ("^CRSLDX",), "NIFTY NEXT 50": ("^NSMIDCP",), "NIFTY MIDCAP 100": ("NIFTY_MIDCAP_100.NS", "^CRSMID"),
    "NIFTY SMALLCAP 100": ("^CNXSC",), "BANK NIFTY": ("^NSEBANK",), "NIFTY IT": ("^CNXIT",), "NIFTY AUTO": ("^CNXAUTO",),
    "NIFTY PHARMA": ("^CNXPHARMA",), "NIFTY FMCG": ("^CNXFMCG",), "NIFTY METAL": ("^CNXMETAL",), "NIFTY ENERGY": ("^CNXENERGY",),
    "NIFTY MIDSMALLCAP 400": ("NIFTYMIDSML400.NS", "NIFTY_MIDSML_400.NS"), "NIFTY PSU BANK": ("^CNXPSUBANK",), "NIFTY REALTY": ("^CNXREALTY",),
}
RS_DEFAULT = "NIFTY 50"
RS_KEY = "ak_rs_index"            # st.session_state key holding the index the RS line is measured against


# ───────────────────────── data preparation ─────────────────────────
def _prep(h: pd.DataFrame) -> pd.DataFrame:
    d = h[["Open", "High", "Low", "Close", "Volume"]].astype(float).copy()
    idx = pd.to_datetime(d.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    d.index = idx.normalize()
    d = d.dropna(subset=["Open", "High", "Low", "Close"])
    d = d[(d["Close"] > 0) & (d["High"] > 0)]
    d = d[~d.index.duplicated(keep="last")].sort_index()
    d["Volume"] = d["Volume"].fillna(0)
    return d


def _group(d: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Weekly ("W-FRI") or monthly ("M") candles, dated on the last traded day of each period."""
    key = d.index.to_period(freq)
    g = d.groupby(key)
    w = g.agg(Open=("Open", "first"), High=("High", "max"), Low=("Low", "min"), Close=("Close", "last"), Volume=("Volume", "sum"))
    last_days = d.index.to_series().groupby(key).max()
    w.index = pd.DatetimeIndex(last_days.values)
    return w


def _rows(d: pd.DataFrame) -> list:
    """[date, open, high, low, close, volume] per candle: compact, the browser builds everything else."""
    t = d.index.strftime("%Y-%m-%d").tolist()
    return [[a, round(float(o), 2), round(float(hh), 2), round(float(l), 2), round(float(c), 2), float(v)]
            for a, o, hh, l, c, v in zip(t, d["Open"], d["High"], d["Low"], d["Close"], d["Volume"])]


def _yf():
    """yfinance with downloads taking turns (the other pages wrap yf.download the same way)."""
    import yfinance as yf
    for mod in ("quant_data", "home_page"):
        try:
            getattr(__import__(mod), "_lock_yfinance")()
            break
        except Exception:
            continue
    return yf


def _close_from(df, ticker):
    """The Close column out of whatever layout this yfinance version returns (flat, or two-level in either order)."""
    if df is None or len(df) == 0:
        return None
    try:
        if isinstance(df.columns, pd.MultiIndex):
            for lvl in (0, 1):
                names = [str(x) for x in df.columns.get_level_values(lvl)]
                if "Close" in names:
                    sub = df.xs("Close", axis=1, level=lvl)
                    c = sub[ticker] if (hasattr(sub, "columns") and ticker in sub.columns) else (sub.iloc[:, 0] if hasattr(sub, "columns") else sub)
                    break
            else:
                return None
        else:
            c = df["Close"]
        c = pd.to_numeric(c, errors="coerce").dropna()
        if len(c) < 100:
            return None
        idx = pd.to_datetime(c.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
        c.index = idx.normalize()
        return c[~c.index.duplicated(keep="last")].sort_index()
    except Exception:
        return None


def rs_candidates(name: str) -> tuple:
    """Yahoo tickers to try for an index name: a known label, or what the user typed (NSE:NIFTYMIDSML400, ^CNXIT, NIFTY_FIN_SERVICE.NS …)."""
    raw = str(name or "").strip()
    if raw.upper() in RS_INDICES:
        return RS_INDICES[raw.upper()]
    t = raw.upper().replace("NSE:", "").replace("BSE:", "").strip()
    if not t or not re.fullmatch(r"[\^A-Z0-9_.&\- ]{1,40}", t):
        return ()
    if t.startswith("^") or t.endswith((".NS", ".BO")):
        return (t,)
    compact = t.replace(" ", "")
    under = t.replace(" ", "_")
    return tuple(dict.fromkeys([compact + ".NS", under + ".NS", "^" + compact, "^CNX" + compact.replace("NIFTY", "")]))


@st.cache_data(ttl=6 * 3600, show_spinner=False, max_entries=24)
def _index_close(tickers: tuple, day: str):
    """Daily closes of an index (first ticker that answers). Raises when none does, so a failure is not remembered."""
    yf = _yf()
    for t in tickers:
        c = None
        try:
            c = _close_from(yf.download(t, period="5y", interval="1d", auto_adjust=True, progress=False, threads=False), t)
        except Exception:
            c = None
        if c is None:
            try:
                c = _close_from(yf.Ticker(t).history(period="5y", interval="1d", auto_adjust=True), t)
            except Exception:
                c = None
        if c is not None:
            return t, c
    raise ValueError("index unavailable")


def rs_index_name() -> str:
    """The index the RS line is measured against (set in Account; NIFTY 50 until changed)."""
    try:
        v = str(st.session_state.get(RS_KEY) or "").strip()
    except Exception:
        v = ""
    return v or RS_DEFAULT


def index_series(name: str):
    """(label, Series) for an index name, or (label, None) when Yahoo has no data for it."""
    label = str(name or RS_DEFAULT).strip()
    label = label.upper() if label.upper() in RS_INDICES else label
    cands = rs_candidates(label)
    if not cands:
        return label, None
    try:
        _, c = _index_close(cands, pd.Timestamp.now().strftime("%Y-%m-%d"))
        return label, c
    except Exception:
        return label, None


def index_payload(dates: pd.DatetimeIndex, name: str):
    """The index lined up with the stock's own trading days: {"label", "v": [close or None per day]} or None."""
    label, c = index_series(name)
    if c is None:
        return {"label": label, "v": None}
    a = c.reindex(dates.union(c.index)).ffill().reindex(dates)
    if a.notna().sum() < 60:
        return {"label": label, "v": None}
    return {"label": label, "v": [None if pd.isna(v) else round(float(v), 2) for v in a.tolist()]}


def render_rs_index_setting() -> None:
    """Account → the index the RS line compares every stock with. Pick one, or type any index symbol."""
    cur = rs_index_name()
    known = list(RS_INDICES)
    other = "Another index (type its symbol)"
    is_known = cur.upper() in RS_INDICES
    pick = st.selectbox("Compare every stock with", known + [other], index=known.index(cur.upper()) if is_known else len(known), key="ak_rs_pick")
    name = pick
    if pick == other:
        name = st.text_input("Index symbol", value="" if is_known else cur, key="ak_rs_custom", placeholder="e.g. NIFTYMIDSML400, NIFTY_FIN_SERVICE.NS, ^CNXIT",
                             help="The NSE index symbol as TradingView shows it (without NSE:), or a Yahoo Finance ticker.").strip()
    if name and name != other:
        label, c = index_series(name)
        if c is None:
            st.warning(f"No price history found for \u201c{name}\u201d. Check the symbol; the RS line keeps using {cur} until one loads.")
        else:
            if label != cur:
                st.session_state[RS_KEY] = label
            st.caption(f"RS line is measured against {label}: {len(c):,} days of history loaded, latest {c.index[-1]:%d %b %Y} at {float(c.iloc[-1]):,.2f}. "
                       "Open any chart to see it; the choice is remembered on this device.")


def build_payload(h: pd.DataFrame, index_name: str | None = RS_DEFAULT) -> dict:
    d = _prep(h)
    if len(d) < 5:
        return {}
    y = d.tail(252)
    out = {"D": _rows(d), "W": _rows(_group(d, "W-FRI")), "M": _rows(_group(d, "M")),
           "hi52": round(float(y["High"].max()), 2), "lo52": round(float(y["Low"].min()), 2), "pdh": None, "pdl": None,
           "ix": index_payload(d.index, index_name) if index_name else None}
    if len(d) >= 2:
        out["pdh"], out["pdl"] = round(float(d["High"].iloc[-2]), 2), round(float(d["Low"].iloc[-2]), 2)
    return out


@st.cache_data(ttl=120, show_spinner=False, max_entries=24)
def _payload_json(symbol: str, index_name: str = RS_DEFAULT, v: int = CHART_VERSION) -> str:
    h = get_history(symbol, "5y")
    if h is None or h.empty:
        raise ValueError("no history")
    p = build_payload(h, index_name)
    if not p:
        raise ValueError("too little history")
    return json.dumps(p, separators=(",", ":"))


_LIB = ('<script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>\n'
        '<script>if(!window.LightweightCharts){document.write(\'<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/'
        'lightweight-charts.standalone.production.js"><\\/script>\');}</script>')

# ───────────────────────── browser side ─────────────────────────
_TEMPLATE = r"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
__LIB__
<style>
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:#0E0E10;color:#B2B5BE;font-family:Inter,-apple-system,'Segoe UI',Roboto,sans-serif;overflow:hidden}
body{display:flex;flex-direction:column;border:1px solid #1E1E22;border-radius:14px;font-size:13px}
body.fs{border-radius:0;border:0}
button{font:500 13px Inter,-apple-system,'Segoe UI',sans-serif;color:#B2B5BE;background:transparent;border:0;cursor:pointer;border-radius:6px;padding:0}
button:focus-visible,select:focus-visible,input:focus-visible{outline:2px solid rgba(255,159,10,.6);outline-offset:1px}
svg{display:block}
/* top toolbar */
#top{flex:0 0 auto;display:flex;align-items:center;gap:2px;height:42px;padding:0 6px;border-bottom:1px solid #1E1E22;overflow-x:auto;overflow-y:hidden;scrollbar-width:none;white-space:nowrap}
#top::-webkit-scrollbar{display:none}
#sym{font-weight:650;font-size:14px;color:#ECECEF;padding:0 10px 0 6px}
.sep{flex:0 0 1px;height:22px;background:#26262B;margin:0 6px}
.tb{height:32px;min-width:32px;padding:0 9px;display:inline-flex;align-items:center;justify-content:center;gap:6px;flex:0 0 auto}
.tb:hover{background:#1B1B1F;color:#ECECEF}
.tb.on{color:#FF9F0A}
.tb.act{background:rgba(255,159,10,.14);color:#FF9F0A}
.tb svg{width:18px;height:18px;stroke:currentColor;fill:none;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round}
.grow{flex:1 1 auto}
#tr{position:sticky;right:-6px;display:inline-flex;gap:2px;flex:0 0 auto;background:#0E0E10;padding:0 6px 0 4px;box-shadow:-10px 0 10px -6px #0E0E10}
/* body: tools + panes */
#mid{flex:1 1 auto;display:flex;min-height:0}
#tools{flex:0 0 40px;border-right:1px solid #1E1E22;display:flex;flex-direction:column;align-items:center;gap:2px;padding:6px 0}
#tools .tb{width:32px;padding:0}
#panes{flex:1 1 auto;display:flex;flex-direction:column;min-width:0;min-height:0;position:relative}
.pane{position:relative;min-height:0}
.pane+.pane{border-top:1px solid #1E1E22}
#p-main{flex:1 1 auto;min-height:150px}
.sub{flex:0 0 23%;min-height:84px;max-height:190px}
.cv{position:absolute;inset:0}
.lg{position:absolute;left:10px;top:6px;right:84px;z-index:5;pointer-events:none;font-size:12px;line-height:1.75;color:#868993;
    text-shadow:0 0 5px #0E0E10,0 0 5px #0E0E10;font-variant-numeric:tabular-nums}
.lg b{color:#ECECEF;font-weight:650;font-size:13px}.lg i{font-style:normal;color:#D1D4DC}.lg .mut{color:#6E6E76}
.lg .row{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#latest{position:absolute;right:92px;bottom:34px;z-index:6;width:30px;height:30px;border-radius:50%;background:#1B1B1F;border:1px solid #2E2E33;display:none;align-items:center;justify-content:center}
#latest:hover{background:#26262B;color:#fff}
#latest svg{width:16px;height:16px;stroke:currentColor;fill:none;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
#msr{position:absolute;z-index:7;pointer-events:none;display:none;background:rgba(41,98,255,.14);border:1px solid rgba(41,98,255,.55)}
#msr.dn{background:rgba(255,69,58,.13);border-color:rgba(255,69,58,.55)}
#msrl{position:absolute;z-index:8;pointer-events:none;display:none;transform:translate(-50%,8px);background:#2962FF;color:#fff;border-radius:6px;padding:5px 9px;
     font-size:12px;line-height:1.5;text-align:center;white-space:nowrap;font-variant-numeric:tabular-nums}
#msrl.dn{background:#D93A30}
#hint{position:absolute;left:50%;top:10px;transform:translateX(-50%);z-index:9;display:none;background:#1B1B1F;border:1px solid #2E2E33;color:#D1D4DC;border-radius:999px;
      padding:5px 14px;font-size:12px;white-space:nowrap}
/* bottom bar */
#bot{flex:0 0 auto;display:flex;align-items:center;gap:2px;height:36px;padding:0 6px;border-top:1px solid #1E1E22;overflow-x:auto;scrollbar-width:none;white-space:nowrap}
#bot::-webkit-scrollbar{display:none}
#bot .tb{height:26px;min-width:0;padding:0 8px;font-size:12.5px}
#clock{font-size:12px;color:#6E6E76;padding:0 8px;font-variant-numeric:tabular-nums}
#attr{font-size:11.5px;color:#55555c;text-decoration:none;padding:0 8px;white-space:nowrap}#attr:hover{color:#B2B5BE}
/* menus */
.menu{position:absolute;z-index:30;display:none;min-width:210px;max-height:76%;overflow:auto;background:#17171A;border:1px solid #2E2E33;border-radius:10px;padding:6px;
      box-shadow:0 14px 40px rgba(0,0,0,.55)}
.menu.open{display:block}
.mi{display:flex;align-items:center;gap:10px;width:100%;height:34px;padding:0 10px;border-radius:7px;text-align:left;color:#D1D4DC;font-size:13px}
.mi:hover{background:#222226}
.mi .ck{flex:0 0 16px;width:16px;height:16px;border-radius:4px;border:1.5px solid #4A4A52;display:inline-flex;align-items:center;justify-content:center;font-size:11px;color:transparent}
.mi.on .ck{background:#FF9F0A;border-color:#FF9F0A;color:#111}
.mi .sw{flex:0 0 10px;width:10px;height:10px;border-radius:50%}
.mi small{margin-left:auto;color:#6E6E76;font-size:12px}
.mh{padding:8px 10px 4px;font-size:11.5px;color:#6E6E76}
/* settings */
#setwrap{position:absolute;inset:0;z-index:40;display:none;align-items:center;justify-content:center;background:rgba(0,0,0,.55)}
#setwrap.open{display:flex}
#set{width:min(560px,94%);max-height:92%;display:flex;flex-direction:column;background:#141416;border:1px solid #2E2E33;border-radius:14px;box-shadow:0 20px 60px rgba(0,0,0,.6)}
body.page #setwrap{position:static;display:block;background:transparent}
body.page #set{width:100%;max-height:none;border:0;box-shadow:none;background:transparent;border-radius:0}
body.page{display:block;height:auto;min-height:100%;border:1px solid #232326;border-radius:14px;overflow:auto;background:#111113;padding:2px 18px 10px}
html.page{overflow:auto;background:#0B0B0C}
.sh{display:flex;align-items:center;justify-content:space-between;padding:14px 18px;border-bottom:1px solid #232326;font-size:15px;font-weight:600;color:#ECECEF}
body.page .sh{display:none}
.sb{overflow:auto;padding:6px 18px 14px}
body.page .sb{padding:0}
.ss{font-size:13px;font-weight:600;color:#ECECEF;padding:16px 0 6px}
.sr{display:flex;align-items:center;gap:10px;min-height:42px;border-bottom:1px solid #1E1E21;color:#B2B5BE;font-size:13.5px}
.sr .l{flex:1 1 auto;min-width:0}
.sr .l small{display:block;color:#6E6E76;font-size:12px;margin-top:1px}
select,input[type=number]{height:32px;background:#1B1B1F;border:1px solid #2E2E33;border-radius:8px;color:#ECECEF;font:500 13px Inter,-apple-system,'Segoe UI',sans-serif;padding:0 8px}
input[type=number]{width:68px}
input[type=color]{width:32px;height:32px;padding:0;border:1px solid #2E2E33;border-radius:8px;background:#1B1B1F;cursor:pointer}
.tg{position:relative;flex:0 0 40px;width:40px;height:24px;border-radius:999px;background:#2E2E33;transition:background .15s}
.tg::after{content:"";position:absolute;left:3px;top:3px;width:18px;height:18px;border-radius:50%;background:#B2B5BE;transition:transform .15s,background .15s}
.tg.on{background:#FF9F0A}.tg.on::after{transform:translateX(16px);background:#fff}
.sf{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:12px 18px;border-top:1px solid #232326}
body.page .sf{padding:14px 0 4px;border-top:0}
.btn{height:36px;padding:0 16px;border-radius:10px;border:1px solid #2E2E33;color:#D1D4DC;font-size:13px}
.btn:hover{border-color:#4A4A52;color:#fff}
.btn.pri{background:#FF9F0A;border-color:#FF9F0A;color:#111;font-weight:600}
#saved{font-size:12.5px;color:#30D158;opacity:0;transition:opacity .2s}
#saved.show{opacity:1}
#err{padding:18px;color:#B2B5BE;font-size:13.5px;display:none}
@media (max-width:560px){#b-ind span,#b-draw span{display:none}#tools{display:none}.only-wide{display:none!important}.lg{font-size:11px;right:70px}}
@media (min-width:561px){.only-narrow{display:none!important}}
</style></head><body>
<div id="top">
  <span id="sym"></span><span class="sep"></span>
  <button class="tb" data-iv="D" title="Daily candles">D</button><button class="tb" data-iv="W" title="Weekly candles">W</button><button class="tb" data-iv="M" title="Monthly candles">M</button>
  <span class="sep"></span>
  <button class="tb" id="b-type" title="Chart type"><svg viewBox="0 0 24 24"><path d="M8 3v4M8 15v6M16 3v6M16 17v4"/><rect x="6" y="7" width="4" height="8" rx="1"/><rect x="14" y="9" width="4" height="8" rx="1"/></svg><span id="typ-l" class="only-wide"></span></button>
  <button class="tb" id="b-ind" title="Indicators"><svg viewBox="0 0 24 24"><path d="M3 17l5-6 4 3 4-7 5 5"/><path d="M3 21h18"/></svg><span>Indicators</span></button>
  <button class="tb only-narrow" id="b-draw" title="Draw"><svg viewBox="0 0 24 24"><path d="M4 20l4-1L19 8l-3-3L5 16z"/></svg><span>Draw</span></button>
  <span class="grow"></span><span id="tr">
  <button class="tb" id="b-shot" title="Save a picture of the chart"><svg viewBox="0 0 24 24"><path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/></svg></button>
  <button class="tb" id="b-set" title="Chart settings"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/></svg></button>
  <button class="tb" id="b-fs" title="Full screen"><svg viewBox="0 0 24 24"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg></button></span>
</div>
<div id="mid">
  <div id="tools">
    <button class="tb" data-tool="cursor" title="Crosshair"><svg viewBox="0 0 24 24"><path d="M12 3v18M3 12h18"/></svg></button>
    <button class="tb" data-tool="hline" title="Horizontal line: click a price"><svg viewBox="0 0 24 24"><path d="M3 12h18"/><circle cx="12" cy="12" r="2.2"/></svg></button>
    <button class="tb" data-tool="trend" title="Trend line: click two points"><svg viewBox="0 0 24 24"><path d="M5 18L19 6"/><circle cx="5" cy="18" r="2"/><circle cx="19" cy="6" r="2"/></svg></button>
    <button class="tb" data-tool="measure" title="Measure: click two points"><svg viewBox="0 0 24 24"><path d="M4 16L16 4l4 4L8 20z"/><path d="M8 12l2 2M11 9l2 2M14 6l2 2"/></svg></button>
    <button class="tb" data-act="undo" title="Remove the last drawing"><svg viewBox="0 0 24 24"><path d="M9 7L4 12l5 5"/><path d="M4 12h10a6 6 0 010 12"/></svg></button>
    <button class="tb" data-act="clear" title="Remove all drawings"><svg viewBox="0 0 24 24"><path d="M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13"/></svg></button>
  </div>
  <div id="panes">
    <div id="err"></div>
    <div id="hint"></div><div id="msr"></div><div id="msrl"></div>
    <button id="latest" title="Go to the latest candle"><svg viewBox="0 0 24 24"><path d="M6 6l6 6-6 6M13 6l6 6-6 6"/></svg></button>
    <div id="menu-type" class="menu"></div><div id="menu-ind" class="menu"></div><div id="menu-draw" class="menu"></div>
    <div id="setwrap"><div id="set"><div class="sh"><span>Chart settings</span><button class="tb" id="set-x" title="Close"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg></button></div>
      <div class="sb" id="set-body"></div>
      <div class="sf"><button class="btn" id="set-reset">Reset to defaults</button><span id="saved">Saved on this device</span><button class="btn pri" id="set-done">Done</button></div></div></div>
  </div>
</div>
<div id="bot">
  <button class="tb" data-rng="1M">1M</button><button class="tb" data-rng="3M">3M</button><button class="tb" data-rng="6M">6M</button><button class="tb" data-rng="YTD">YTD</button>
  <button class="tb" data-rng="1Y">1Y</button><button class="tb" data-rng="2Y">2Y</button><button class="tb" data-rng="5Y">5Y</button><button class="tb" data-rng="ALL">All</button>
  <span class="grow"></span><a id="attr" href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer" title="Charts use TradingView Lightweight Charts">Charts by TradingView</a><span id="clock"></span><span class="sep"></span>
  <button class="tb" data-sc="pct" title="Percent scale">%</button><button class="tb" data-sc="log" title="Logarithmic scale">log</button><button class="tb" data-sc="auto" title="Fit the price scale to the candles on screen">auto</button>
</div>
<script>
(function(){
"use strict";
var P = __PAYLOAD__, SYM = __SYM__, MODE = "__MODE__", DEF_RNG = "__RANGE__";
var LW = window.LightweightCharts;
var $ = function(id){ return document.getElementById(id); };
var KEY = "arka_chart_v3";
function esc(t){ return String(t).replace(/[&<>"]/g, function(c){ return {"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;"}[c]; }); }
var SYMH = esc(SYM);

// ───────── settings (saved in this browser) ─────────
var DEFAULTS = {iv:"D", rng:"", type:"candles", palette:"green", log:false, pct:false, grid:true, wm:false, pline:true,
  ma:[{on:false,kind:"EMA",len:10,color:"#FFD60A"},{on:true,kind:"EMA",len:21,color:"#F2F2F5"},{on:false,kind:"SMA",len:20,color:"#5AC8FA"},
      {on:true,kind:"SMA",len:50,color:"#FF9F0A"},{on:true,kind:"SMA",len:200,color:"#BF5AF2"}],
  vol:true, volma:true, volmaLen:20, bb:false, bbLen:20, bbK:2, rsi:true, rsiLen:14, macd:false, pd:true, wk52:false,
  rs:true, rsPlace:"above", rsColor:"ma", rsDir:10, rsMa:true, rsCloud:true, rsCross:true, rsLen:21, rsKind:"EMA", rsHi:true, rsHiAll:false,
  rsUp:"#2196F3", rsDn:"#E040FB", rsMaCol:"#F23645"};
var RS_NAME = (P && P.ix && P.ix.label) || "the index";
function clone(o){ return JSON.parse(JSON.stringify(o)); }
function loadS(){
  var s = clone(DEFAULTS);
  try {
    var raw = window.localStorage.getItem(KEY);
    if (raw) {
      var u = JSON.parse(raw);
      Object.keys(DEFAULTS).forEach(function(k){
        if (u[k] === undefined || typeof u[k] !== typeof DEFAULTS[k]) return;
        if (k === "ma") { if (Array.isArray(u.ma)) u.ma.slice(0, 5).forEach(function(m, i){ if (m && typeof m === "object") s.ma[i] = Object.assign(s.ma[i], m); }); }
        else s[k] = u[k];
      });
    }
  } catch (e) {}
  s.ma.forEach(function(m){ m.len = clampInt(m.len, 2, 400, 20); m.kind = m.kind === "SMA" ? "SMA" : "EMA"; });
  s.volmaLen = clampInt(s.volmaLen, 2, 200, 20); s.bbLen = clampInt(s.bbLen, 2, 200, 20); s.rsiLen = clampInt(s.rsiLen, 2, 100, 14);
  s.bbK = Math.min(5, Math.max(0.5, Number(s.bbK) || 2));
  s.rsDir = clampInt(s.rsDir, 1, 100, 10); if (["above","pane","price"].indexOf(s.rsPlace) < 0) s.rsPlace = "above"; if (["dir","ma"].indexOf(s.rsColor) < 0) s.rsColor = "dir";
  s.rsLen = clampInt(s.rsLen, 2, 200, 21); s.rsKind = s.rsKind === "SMA" ? "SMA" : "EMA"; 
  if (["D","W","M"].indexOf(s.iv) < 0) s.iv = "D";
  return s;
}
function clampInt(v, lo, hi, d){ v = parseInt(v, 10); if (isNaN(v)) return d; return Math.min(hi, Math.max(lo, v)); }
function saveS(){ try { window.localStorage.setItem(KEY, JSON.stringify(S)); } catch (e) {} }
var S = loadS();
var PAL = {green:{up:"#30D158", dn:"#FF453A"}, teal:{up:"#26A69A", dn:"#EF5350"}, blue:{up:"#2962FF", dn:"#F23645"}, mono:{up:"#E6E6EA", dn:"#6E6E76"}};
function pal(){ return PAL[S.palette] || PAL.green; }
var C = {bg:"#0E0E10", grid:"#17171A", border:"#26262B", text:"#868993"};
var FONT = "Inter,-apple-system,'Segoe UI',Roboto,sans-serif";
var TYPES = [["candles","Candles"],["hollow","Hollow candles"],["bars","Bars"],["line","Line"],["area","Area"],["heikin","Heikin Ashi"]];
var IVN = {D:"1D", W:"1W", M:"1M"};

// ───────── settings panel (used in the chart and on the Account page) ─────────
function el(tag, cls, txt){ var e = document.createElement(tag); if (cls) e.className = cls; if (txt != null) e.textContent = txt; return e; }
function flashSaved(){ var s = $("saved"); s.classList.add("show"); clearTimeout(flashSaved.t); flashSaved.t = setTimeout(function(){ s.classList.remove("show"); }, 1400); }
function changed(){ saveS(); flashSaved(); if (MODE === "chart") dirty = true; }
var dirty = false;
function rowToggle(label, sub, get, set){
  var r = el("div", "sr"), l = el("div", "l", label); if (sub) l.appendChild(el("small", "", sub));
  var t = el("button", "tg" + (get() ? " on" : "")); t.setAttribute("role", "switch"); t.setAttribute("aria-label", label);
  t.onclick = function(){ set(!get()); t.classList.toggle("on", !!get()); changed(); };
  r.appendChild(l); r.appendChild(t); return r;
}
function rowSelect(label, opts, get, set){
  var r = el("div", "sr"), l = el("div", "l", label), s = el("select"); s.setAttribute("aria-label", label);
  opts.forEach(function(o){ var op = el("option", "", o[1]); op.value = o[0]; s.appendChild(op); });
  s.value = get(); s.onchange = function(){ set(s.value); changed(); };
  r.appendChild(l); r.appendChild(s); return r;
}
function numInput(label, get, set, lo, hi, step){
  var n = el("input"); n.type = "number"; n.min = lo; n.max = hi; n.step = step || 1; n.value = get(); n.setAttribute("aria-label", label);
  n.onchange = function(){ var v = Number(n.value); if (isNaN(v)) v = get(); v = Math.min(hi, Math.max(lo, v)); if (!step) v = Math.round(v); n.value = v; set(v); changed(); };
  return n;
}
function buildSettings(){
  var b = $("set-body"); b.innerHTML = "";
  b.appendChild(el("div", "ss", "Chart"));
  b.appendChild(rowSelect("Chart type", TYPES, function(){ return S.type; }, function(v){ S.type = v; }));
  b.appendChild(rowSelect("Candles when a chart opens", [["D","Daily"],["W","Weekly"],["M","Monthly"]], function(){ return S.iv; }, function(v){ S.iv = v; }));
  b.appendChild(rowSelect("Range when a chart opens", [["","Chosen by the page"],["1M","1 month"],["3M","3 months"],["6M","6 months"],["YTD","This year"],["1Y","1 year"],["2Y","2 years"],["5Y","5 years"],["ALL","All"]],
    function(){ return S.rng; }, function(v){ S.rng = v; }));
  b.appendChild(rowSelect("Colours", [["green","Green and red"],["teal","Teal and red"],["blue","Blue and red"],["mono","White and grey"]], function(){ return S.palette; }, function(v){ S.palette = v; }));
  b.appendChild(rowToggle("Logarithmic price scale", "", function(){ return S.log; }, function(v){ S.log = v; if (v) S.pct = false; }));
  b.appendChild(rowToggle("Grid lines", "", function(){ return S.grid; }, function(v){ S.grid = v; }));
  b.appendChild(rowToggle("Last price line", "", function(){ return S.pline; }, function(v){ S.pline = v; }));
  b.appendChild(rowToggle("Symbol watermark", "", function(){ return S.wm; }, function(v){ S.wm = v; }));
  b.appendChild(el("div", "ss", "Moving averages"));
  S.ma.forEach(function(m, i){
    var r = el("div", "sr"), l = el("div", "l", "Average " + (i + 1));
    var k = el("select"); k.setAttribute("aria-label", "Average " + (i + 1) + " type");
    [["EMA","EMA"],["SMA","SMA"]].forEach(function(o){ var op = el("option", "", o[1]); op.value = o[0]; k.appendChild(op); });
    k.value = m.kind; k.onchange = function(){ m.kind = k.value; changed(); };
    var n = numInput("Average " + (i + 1) + " length", function(){ return m.len; }, function(v){ m.len = v; }, 2, 400);
    var c = el("input"); c.type = "color"; c.value = m.color; c.setAttribute("aria-label", "Average " + (i + 1) + " colour"); c.onchange = function(){ m.color = c.value; changed(); };
    var t = el("button", "tg" + (m.on ? " on" : "")); t.setAttribute("role", "switch"); t.setAttribute("aria-label", "Show average " + (i + 1));
    t.onclick = function(){ m.on = !m.on; t.classList.toggle("on", m.on); changed(); };
    r.appendChild(l); r.appendChild(k); r.appendChild(n); r.appendChild(c); r.appendChild(t); b.appendChild(r);
  });
  b.appendChild(el("div", "ss", "Volume"));
  b.appendChild(rowToggle("Volume bars", "", function(){ return S.vol; }, function(v){ S.vol = v; }));
  var vr = rowToggle("Volume average", "", function(){ return S.volma; }, function(v){ S.volma = v; });
  vr.insertBefore(numInput("Volume average length", function(){ return S.volmaLen; }, function(v){ S.volmaLen = v; }, 2, 200), vr.lastChild); b.appendChild(vr);
  b.appendChild(el("div", "ss", "Studies"));
  var br = rowToggle("Bollinger bands", "Length and width", function(){ return S.bb; }, function(v){ S.bb = v; });
  br.insertBefore(numInput("Bollinger length", function(){ return S.bbLen; }, function(v){ S.bbLen = v; }, 2, 200), br.lastChild);
  br.insertBefore(numInput("Bollinger width", function(){ return S.bbK; }, function(v){ S.bbK = v; }, 0.5, 5, 0.1), br.lastChild); b.appendChild(br);
  var rr = rowToggle("RSI", "Wilder's RSI in its own pane", function(){ return S.rsi; }, function(v){ S.rsi = v; });
  rr.insertBefore(numInput("RSI length", function(){ return S.rsiLen; }, function(v){ S.rsiLen = v; }, 2, 100), rr.lastChild); b.appendChild(rr);
  b.appendChild(rowToggle("MACD", "12, 26, 9 in its own pane", function(){ return S.macd; }, function(v){ S.macd = v; }));
  b.appendChild(el("div", "ss", "Relative strength line"));
  b.appendChild(rowToggle("Show the RS line", "The stock's price divided by an index. Choose the index in Account → Relative strength index", function(){ return S.rs; }, function(v){ S.rs = v; }));
  b.appendChild(rowSelect("Where to draw it", [["above","In a pane above the chart"],["pane","In a pane below the chart"],["price","On the price chart"]], function(){ return S.rsPlace; }, function(v){ S.rsPlace = v; }));
  b.appendChild(rowSelect("Colour the line by", [["ma","Above or below its average"],["dir","Its direction: rising or falling"]], function(){ return S.rsColor; }, function(v){ S.rsColor = v; }));
  var rc = el("div", "sr"), rcl = el("div", "l", "Colours"); rcl.appendChild(el("small", "", "line up, line down, average"));
  rc.appendChild(rcl);
  [["rsUp", "RS line up colour"], ["rsDn", "RS line down colour"], ["rsMaCol", "RS average colour"]].forEach(function(x){ var c = el("input"); c.type = "color"; c.value = S[x[0]]; c.setAttribute("aria-label", x[1]); c.onchange = function(){ S[x[0]] = c.value; changed(); }; rc.appendChild(c); });
  b.appendChild(rc);
  var rq = rowToggle("Average of the RS line", "Type and length", function(){ return S.rsMa; }, function(v){ S.rsMa = v; });
  var rqk = el("select"); rqk.setAttribute("aria-label", "RS average type"); [["EMA","EMA"],["SMA","SMA"]].forEach(function(o){ var op = el("option", "", o[1]); op.value = o[0]; rqk.appendChild(op); });
  rqk.value = S.rsKind; rqk.onchange = function(){ S.rsKind = rqk.value; changed(); };
  rq.insertBefore(rqk, rq.lastChild); rq.insertBefore(numInput("RS average length", function(){ return S.rsLen; }, function(v){ S.rsLen = v; }, 2, 200), rq.lastChild); b.appendChild(rq);
  b.appendChild(rowToggle("Shade between the line and its average", "Shown when the RS line has its own pane", function(){ return S.rsCloud; }, function(v){ S.rsCloud = v; }));
  b.appendChild(rowToggle("Triangle when the line crosses above its average", "", function(){ return S.rsCross; }, function(v){ S.rsCross = v; }));
  b.appendChild(rowToggle("Pink dot: RS new high before price", "The RS line makes a 52-week high while the price has not yet", function(){ return S.rsHi; }, function(v){ S.rsHi = v; }));
  b.appendChild(rowToggle("Small dot: RS new high together with price", "", function(){ return S.rsHiAll; }, function(v){ S.rsHiAll = v; }));
  var rd = el("div", "sr"), rdl = el("div", "l", "Direction look-back"); rdl.appendChild(el("small", "", "only when colouring by direction: rising means higher than this many candles ago"));
  rd.appendChild(rdl); rd.appendChild(numInput("Direction look-back", function(){ return S.rsDir; }, function(v){ S.rsDir = v; }, 1, 100)); b.appendChild(rd);
  b.appendChild(el("div", "ss", "Lines on the chart"));
  b.appendChild(rowToggle("Previous day high and low (PDH / PDL)", "Turn off to hide the two dashed lines", function(){ return S.pd; }, function(v){ S.pd = v; }));
  b.appendChild(rowToggle("52-week high and low", "Turn off to hide them", function(){ return S.wk52; }, function(v){ S.wk52 = v; }));
}
$("set-reset").onclick = function(){ S = clone(DEFAULTS); buildSettings(); changed(); };

if (MODE === "settings") {            // the Account page: only the settings, no chart
  document.documentElement.classList.add("page"); document.body.classList.add("page");
  ["top","bot","tools"].forEach(function(id){ $(id).style.display = "none"; });
  $("set-done").style.display = "none";
  buildSettings();
  return;
}

// ───────── chart ─────────
$("sym").textContent = SYM;
if (!LW) { var er = $("err"); er.style.display = "block"; er.textContent = "The chart library could not load. Check your connection and refresh."; return; }

// maths on plain arrays; null where there is not enough history yet
function sma(a, n){ var o = new Array(a.length).fill(null), s = 0; for (var i = 0; i < a.length; i++){ s += a[i]; if (i >= n) s -= a[i - n]; if (i >= n - 1) o[i] = s / n; } return o; }
function emaFrom(a, n){            // EMA of a series that starts with nulls
  var o = new Array(a.length).fill(null), k = 2 / (n + 1), s = 0, c = 0, p = null;
  for (var i = 0; i < a.length; i++){ var v = a[i]; if (v == null) continue; if (p === null){ s += v; c++; if (c === n){ p = s / n; o[i] = p; } } else { p = v * k + p * (1 - k); o[i] = p; } }
  return o; }
function rsiW(a, n){               // Wilder's RSI: first average is a simple mean, then Wilder smoothing
  var o = new Array(a.length).fill(null), g = 0, l = 0;
  for (var i = 1; i < a.length; i++){
    var d = a[i] - a[i - 1], up = d > 0 ? d : 0, dn = d < 0 ? -d : 0;
    if (i <= n){ g += up; l += dn; if (i === n){ g /= n; l /= n; o[i] = l === 0 ? 100 : 100 - 100 / (1 + g / l); } }
    else { g = (g * (n - 1) + up) / n; l = (l * (n - 1) + dn) / n; o[i] = l === 0 ? 100 : 100 - 100 / (1 + g / l); }
  }
  return o; }
function stdev(a, n){ var o = new Array(a.length).fill(null); for (var i = n - 1; i < a.length; i++){ var m = 0, j; for (j = i - n + 1; j <= i; j++) m += a[j]; m /= n; var v = 0; for (j = i - n + 1; j <= i; j++) v += (a[j] - m) * (a[j] - m); o[i] = Math.sqrt(v / n); } return o; }
function heikin(R){ var o = [], po = null, pc = null; for (var i = 0; i < R.length; i++){ var r = R[i], c = (r[1] + r[2] + r[3] + r[4]) / 4, op = po === null ? (r[1] + r[4]) / 2 : (po + pc) / 2;
  o.push({time:r[0], open:op, high:Math.max(r[2], op, c), low:Math.min(r[3], op, c), close:c}); po = op; pc = c; } return o; }
function pts(T, a){ return T.map(function(t, i){ return a[i] == null ? {time:t} : {time:t, value:Math.round(a[i] * 100) / 100}; }); }

var D = {};                          // per interval: rows, times, closes
function dataFor(iv){
  if (!D[iv]) { var R = P[iv] || []; D[iv] = {R:R, T:R.map(function(r){ return r[0]; }), close:R.map(function(r){ return r[4]; }), vol:R.map(function(r){ return r[5]; }), idx:new Map(R.map(function(r, i){ return [r[0], i]; }))}; }
  return D[iv];
}

var IXMAP = null;                    // Map(date -> index close), built once from the daily rows
function indexFor(T){
  var arr = P.ix && P.ix.v; if (!arr) return null;
  if (!IXMAP) { IXMAP = new Map(); var DT = dataFor("D").T; for (var i = 0; i < DT.length; i++) if (arr[i] != null) IXMAP.set(DT[i], arr[i]); }
  return T.map(function(t){ var v = IXMAP.get(t); return v == null ? null : v; });
}
function rsCalc(d){
  var ix = indexFor(d.T); if (!ix) return null;
  var raw = d.close.map(function(c, i){ return ix[i] ? c / ix[i] : null; }), base = null, i;
  for (i = 0; i < raw.length; i++) if (raw[i] != null) { base = raw[i]; break; }
  if (base === null) return null;
  var line = raw.map(function(v){ return v == null ? null : v / base * 100; });
  for (i = 1; i < line.length; i++) if (line[i] == null && line[i - 1] != null) line[i] = line[i - 1];      // a day the index did not trade
  var filled = line.map(function(v){ return v == null ? 0 : v; }), first = line.findIndex(function(v){ return v != null; });
  var ma = (S.rsKind === "SMA" ? sma(filled, S.rsLen) : emaFrom(line, S.rsLen)).map(function(v, k){ return k < first + S.rsLen - 1 ? null : v; });
  var win = iv === "D" ? 252 : iv === "W" ? 52 : 12, hi = new Array(line.length).fill(false);
  for (i = first + Math.min(win, 20); i < line.length; i++) { var mx = -Infinity; for (var j = Math.max(first, i - win + 1); j < i; j++) if (line[j] > mx) mx = line[j]; hi[i] = line[i] != null && line[i] > mx; }
  var up = line.map(function(v, k){ if (v == null) return true; if (S.rsColor === "ma") return ma[k] == null ? true : v >= ma[k]; var b0 = line[Math.max(first, k - S.rsDir)]; return b0 == null ? true : v >= b0; });
  var bp = new Array(line.length).fill(false);            // RS at a new high while the price is not: the "before price" case
  for (i = 0; i < line.length; i++) if (hi[i]) { var ph = -Infinity; for (var q = Math.max(0, i - win + 1); q < i; q++) if (d.R[q][2] > ph) ph = d.R[q][2]; bp[i] = !(d.R[i][2] >= ph); }
  var per = iv === "D" ? 63 : iv === "W" ? 13 : 3, n = line.length - 1, sc = null;
  var perf = function(a, k){ var b1 = a[n - k]; return (b1 == null || !b1 || a[n] == null) ? null : a[n] / b1; };
  var w = [[per, 0.4], [per * 2, 0.2], [per * 3, 0.2], [per * 4, 0.2]], num = 0, den = 0, ws = 0;
  w.forEach(function(x){ if (n - x[0] < 0) return; var ps = perf(d.close, x[0]), pi = perf(ix, x[0]); if (ps == null || pi == null) return; num += ps * x[1]; den += pi * x[1]; ws += x[1]; });
  if (ws > 0 && den > 0) sc = num / den * 100;
  var cross = line.map(function(v, k){ return k > 0 && v != null && ma[k] != null && ma[k - 1] != null && line[k - 1] != null && v > ma[k] && line[k - 1] <= ma[k - 1]; });
  return {line:line, ma:ma, hi:hi, bp:bp, up:up, cross:cross, win:win, score:sc};
}
function rsSeries(chart, R, T, extra, cloud){
  var sid = extra && extra.priceScaleId ? {priceScaleId:extra.priceScaleId} : {};
  var quiet = {priceLineVisible:false, lastValueVisible:false, crosshairMarkerVisible:false};
  if (cloud && S.rsMa && S.rsCloud) {          // shade between the line and its average: colour above, then the pane colour painted back in below the lower of the two
    var area = function(colTop, vals){ var a = chart.addAreaSeries(Object.assign({lineColor:"rgba(0,0,0,0)", lineWidth:1, topColor:colTop, bottomColor:colTop}, quiet, sid)); a.setData(pts(T, vals)); };
    var upper = function(want){ return R.line.map(function(v, k){ if (v == null || R.ma[k] == null) return null; var isUp = v >= R.ma[k]; return isUp === want ? Math.max(v, R.ma[k]) : null; }); };
    area(hexA(S.rsUp, 0.30), upper(true)); area(hexA(S.rsDn, 0.30), upper(false));
    area(C.bg, R.line.map(function(v, k){ return v == null ? null : (R.ma[k] == null ? v : Math.min(v, R.ma[k])); }));
  }
  if (S.rsMa) chart.addLineSeries(Object.assign({color:S.rsMaCol, lineWidth:1}, quiet, sid)).setData(pts(T, R.ma));
  var sr = chart.addLineSeries(Object.assign({color:S.rsUp, lineWidth:2, priceFormat:{type:"price", precision:2, minMove:0.01}}, quiet, extra || {}));
  sr.setData(T.map(function(t, i){ var v = R.line[i]; return v == null ? {time:t} : {time:t, value:Math.round(v * 100) / 100, color:R.up[i] ? S.rsUp : S.rsDn}; }));
  var mk = [];
  R.line.forEach(function(_, i){
    if (R.hi[i]) { if (R.bp[i]) { if (S.rsHi) mk.push({time:T[i], position:"inBar", color:"#FF4FD8", shape:"circle", size:1.3}); } else if (S.rsHiAll) mk.push({time:T[i], position:"inBar", color:S.rsUp, shape:"circle", size:0.5}); }
    if (S.rsCross && S.rsMa && R.cross[i]) mk.push({time:T[i], position:"belowBar", color:"#2962FF", shape:"arrowUp", size:0.6});
  });
  sr.setMarkers(mk);
  return sr;
}
function rsLegend(i){
  var R = ind.rs, v = R.line[i], strong = R.up[i], cc = strong ? S.rsUp : S.rsDn;
  var word = S.rsColor === "ma" ? (strong ? "above its average: stronger than the index" : "below its average: weaker than the index") : (strong ? "rising: stronger than the index" : "falling: weaker than the index");
  return 'RS line <span class="mut">vs ' + esc(RS_NAME) + '</span> <span style="color:' + cc + '">' + fmt(v) + " " + (v == null ? "" : word) + "</span>" +
    (R.score == null ? "" : ' &nbsp;<span class="mut">RS score</span> <span style="color:' + (R.score >= 100 ? S.rsUp : S.rsDn) + '">' + R.score.toFixed(0) + "</span>") +
    (S.rsMa ? ' &nbsp;<span style="color:' + S.rsMaCol + '">' + S.rsKind + " " + S.rsLen + " " + fmt(R.ma[i]) + "</span>" : "") +
    (R.hi[i] ? (R.bp[i] ? ' &nbsp;<span style="color:#FF4FD8">● RS new high before price</span>' : ' &nbsp;<span style="color:#5AC8FA">● RS new high</span>') : "");
}
var iv = S.iv, rng = S.rng || DEF_RNG || "6M", tool = "cursor";
if (!(P[iv] && P[iv].length)) iv = "D";
var charts = [], main = null, mainSeries = null, volSeries = null, ind = {}, lines = [], drawSeries = [], xs = false, syncing = false, pend = null, msr = null;
var DKEY = "arka_draw_" + SYM, draws = {h:[], t:[]};
try { var dr = JSON.parse(window.localStorage.getItem(DKEY) || "null"); if (dr && Array.isArray(dr.h) && Array.isArray(dr.t)) draws = dr; } catch (e) {}
function saveDraws(){ try { window.localStorage.setItem(DKEY, JSON.stringify(draws)); } catch (e) {} }

function fmt(v, d){ d = (d == null ? 2 : d); return (v == null || isNaN(v)) ? "—" : Number(v).toLocaleString("en-IN", {minimumFractionDigits:d, maximumFractionDigits:d}); }
function fmtVol(v){ if (v == null) return "—"; if (v >= 1e7) return (v / 1e7).toFixed(2) + " Cr"; if (v >= 1e5) return (v / 1e5).toFixed(2) + " L"; if (v >= 1e3) return (v / 1e3).toFixed(1) + " K"; return String(Math.round(v)); }
function tkey(t){ if (t == null) return null; if (typeof t === "string") return t; if (typeof t === "number") return new Date(t * 1000).toISOString().slice(0, 10);
  if (t.year) return t.year + "-" + String(t.month).padStart(2, "0") + "-" + String(t.day).padStart(2, "0"); return null; }

function opts(elm, showTime){
  return {width:elm.clientWidth || 800, height:elm.clientHeight || 300,
    layout:{background:{type:"solid", color:C.bg}, textColor:C.text, fontFamily:FONT, fontSize:11, attributionLogo:false},
    grid:{vertLines:{color:C.grid, visible:!!S.grid}, horzLines:{color:C.grid, visible:!!S.grid}},
    rightPriceScale:{borderColor:C.border, minimumWidth:76},
    timeScale:{borderColor:C.border, rightOffset:8, barSpacing:7, minBarSpacing:0.8, timeVisible:false, visible:showTime},
    crosshair:{mode:LW.CrosshairMode.Normal, vertLine:{color:"#5A5A63", labelBackgroundColor:"#2E2E33"}, horzLine:{color:"#5A5A63", labelBackgroundColor:"#2E2E33"}},
    handleScroll:{mouseWheel:true, pressedMouseMove:true, horzTouchDrag:true, vertTouchDrag:true},
    handleScale:{axisPressedMouseMove:{time:true, price:true}, axisDoubleClickReset:{time:true, price:true}, mouseWheel:true, pinch:true},
    localization:{dateFormat:"dd MMM yyyy", priceFormatter:function(p){ return p.toFixed(2); }}};
}
function addPane(id, cls){
  var p = el("div", "pane " + cls); p.id = "p-" + id; var cv = el("div", "cv"), lg = el("div", "lg"); lg.id = "lg-" + id; p.appendChild(cv); p.appendChild(lg);
  $("panes").appendChild(p); return {id:id, el:p, cv:cv, lg:lg};
}
function lineOn(chart, color, w, extra){ return chart.addLineSeries(Object.assign({color:color, lineWidth:w || 1, priceLineVisible:false, lastValueVisible:false, crosshairMarkerVisible:false}, extra || {})); }

function teardown(){
  charts.forEach(function(c){ try { c.chart.remove(); } catch (e) {} if (c.el.parentNode) c.el.parentNode.removeChild(c.el); });
  charts = []; main = null; mainSeries = null; volSeries = null; ind = {}; lines = []; drawSeries = [];
}

function build(keep){
  var prev = null;
  if (keep && main) { try { prev = main.timeScale().getVisibleLogicalRange(); } catch (e) {} }
  teardown();
  var d = dataFor(iv), T = d.T, cl = d.close, col = pal();
  var subs = [];
  ind.rs = S.rs ? rsCalc(d) : null;
  var rsPane = !!ind.rs && S.rsPlace !== "price";
  if (rsPane && S.rsPlace === "pane") subs.push("rs");
  if (S.rsi) subs.push("rsi"); if (S.macd) subs.push("macd");
  var nSub = subs.length + (rsPane && S.rsPlace === "above" ? 1 : 0), basis = (nSub >= 3 ? 17 : nSub === 2 ? 20 : 24) + "%";
  var pm = addPane("main", ""); pm.el.id = "p-main";
  main = LW.createChart(pm.cv, opts(pm.cv, subs.length === 0));
  if (S.wm) main.applyOptions({watermark:{visible:true, text:SYM, color:"rgba(255,255,255,.045)", fontSize:64, fontFamily:FONT, horzAlign:"center", vertAlign:"center"}});
  charts.push({id:"main", chart:main, el:pm.el, cv:pm.cv, lg:pm.lg});

  // price series
  var pf = {type:"price", precision:2, minMove:0.05}, base = {priceFormat:pf, priceLineVisible:!!S.pline, lastValueVisible:true};
  var ohlc = S.type === "heikin" ? heikin(d.R) : d.R.map(function(r){ return {time:r[0], open:r[1], high:r[2], low:r[3], close:r[4]}; });
  if (S.type === "line") { mainSeries = main.addLineSeries(Object.assign({color:"#2962FF", lineWidth:2}, base)); mainSeries.setData(d.R.map(function(r){ return {time:r[0], value:r[4]}; })); }
  else if (S.type === "area") { mainSeries = main.addAreaSeries(Object.assign({lineColor:"#2962FF", topColor:"rgba(41,98,255,.32)", bottomColor:"rgba(41,98,255,.02)", lineWidth:2}, base)); mainSeries.setData(d.R.map(function(r){ return {time:r[0], value:r[4]}; })); }
  else if (S.type === "bars") { mainSeries = main.addBarSeries(Object.assign({upColor:col.up, downColor:col.dn, thinBars:false}, base)); mainSeries.setData(ohlc); }
  else { mainSeries = main.addCandlestickSeries(Object.assign({upColor:S.type === "hollow" ? C.bg : col.up, downColor:col.dn, borderUpColor:col.up, borderDownColor:col.dn, wickUpColor:col.up, wickDownColor:col.dn}, base)); mainSeries.setData(ohlc); }
  d.ohlc = ohlc;
  mainSeries.priceScale().applyOptions({scaleMargins:{top:0.08, bottom:S.vol ? 0.24 : 0.06}, mode:S.pct ? 2 : (S.log ? 1 : 0)});

  // volume on the chart
  if (S.vol) {
    volSeries = main.addHistogramSeries({priceScaleId:"vol", priceFormat:{type:"volume"}, lastValueVisible:false, priceLineVisible:false});
    volSeries.priceScale().applyOptions({scaleMargins:{top:0.8, bottom:0}});
    volSeries.setData(d.R.map(function(r, i){ var up = i === 0 ? r[4] >= r[1] : r[4] >= d.R[i - 1][4]; return {time:r[0], value:r[5], color:hexA(up ? col.up : col.dn, 0.5)}; }));
    if (S.volma) { ind.volma = sma(d.vol, S.volmaLen); var vm = main.addLineSeries({priceScaleId:"vol", color:"#5AC8FA", lineWidth:1, priceLineVisible:false, lastValueVisible:false, crosshairMarkerVisible:false, priceFormat:{type:"volume"}}); vm.setData(pts(T, ind.volma)); }
  }
  // relative strength line drawn over the price chart, on its own hidden scale in the lower part of the pane
  if (ind.rs && S.rsPlace === "price") {
    var rso = rsSeries(main, ind.rs, T, {priceScaleId:"rs"});
    rso.priceScale().applyOptions({scaleMargins:{top:0.52, bottom:S.vol ? 0.24 : 0.08}});
  }
  // moving averages and bands
  ind.ma = [];
  S.ma.forEach(function(m){
    if (!m.on) return;
    var a = m.kind === "SMA" ? sma(cl, m.len) : emaFrom(cl, m.len);
    lineOn(main, m.color, m.len >= 100 ? 2 : 1).setData(pts(T, a)); ind.ma.push({m:m, a:a});
  });
  if (S.bb) {
    var mid = sma(cl, S.bbLen), sd = stdev(cl, S.bbLen), up = mid.map(function(v, i){ return v == null ? null : v + S.bbK * sd[i]; }), lo = mid.map(function(v, i){ return v == null ? null : v - S.bbK * sd[i]; });
    lineOn(main, "rgba(41,98,255,.85)", 1).setData(pts(T, up)); lineOn(main, "rgba(41,98,255,.45)", 1, {lineStyle:2}).setData(pts(T, mid)); lineOn(main, "rgba(41,98,255,.85)", 1).setData(pts(T, lo));
    ind.bb = {u:up, m:mid, l:lo};
  }
  // lower panes
  subs.forEach(function(id, n){
    var p = addPane(id, "sub"), last = n === subs.length - 1; p.el.style.flexBasis = basis;
    var ch = LW.createChart(p.cv, opts(p.cv, last)), rec = {id:id, chart:ch, el:p.el, cv:p.cv, lg:p.lg};
    if (id === "rs") {
      ch.applyOptions({grid:{vertLines:{visible:false}, horzLines:{visible:false}}});
      rec.series = rsSeries(ch, ind.rs, T, {lastValueVisible:true}, true);
    } else if (id === "rsi") {
      ind.rsi = rsiW(cl, S.rsiLen); ind.rsima = sma(ind.rsi.map(function(v){ return v == null ? 0 : v; }), 14).map(function(v, i){ return i < S.rsiLen + 13 ? null : v; });
      rec.series = ch.addLineSeries({color:"#BF5AF2", lineWidth:2, priceLineVisible:false, lastValueVisible:true, priceFormat:{type:"price", precision:2, minMove:0.01},
        autoscaleInfoProvider:function(){ return {priceRange:{minValue:0, maxValue:100}}; }});
      rec.series.setData(pts(T, ind.rsi));
      ch.addLineSeries({color:"#FF9F0A", lineWidth:1, priceLineVisible:false, lastValueVisible:false, crosshairMarkerVisible:false}).setData(pts(T, ind.rsima));
      [[70, "rgba(255,69,58,.55)"], [50, "rgba(120,120,128,.45)"], [30, "rgba(48,209,88,.55)"]].forEach(function(x){ rec.series.createPriceLine({price:x[0], color:x[1], lineWidth:1, lineStyle:2, axisLabelVisible:true, title:""}); });
    } else {
      var e12 = emaFrom(cl, 12), e26 = emaFrom(cl, 26), mac = cl.map(function(_, i){ return e12[i] == null || e26[i] == null ? null : e12[i] - e26[i]; }), sig = emaFrom(mac, 9),
          hist = mac.map(function(v, i){ return v == null || sig[i] == null ? null : v - sig[i]; });
      ind.macd = {m:mac, s:sig, h:hist};
      var hs = ch.addHistogramSeries({priceLineVisible:false, lastValueVisible:false, priceFormat:{type:"price", precision:2, minMove:0.01}});
      hs.setData(T.map(function(t, i){ return hist[i] == null ? {time:t} : {time:t, value:Math.round(hist[i] * 100) / 100, color:hexA(hist[i] >= 0 ? col.up : col.dn, hist[i] >= (hist[i - 1] == null ? hist[i] : hist[i - 1]) ? 0.85 : 0.4)}; }));
      rec.series = ch.addLineSeries({color:"#2962FF", lineWidth:2, priceLineVisible:false, lastValueVisible:true, priceFormat:{type:"price", precision:2, minMove:0.01}});
      rec.series.setData(pts(T, mac));
      ch.addLineSeries({color:"#FF9F0A", lineWidth:1, priceLineVisible:false, lastValueVisible:false, crosshairMarkerVisible:false}).setData(pts(T, sig));
    }
    ch.priceScale("right").applyOptions({scaleMargins:{top:0.12, bottom:0.12}});
    charts.push(rec);
  });
  if (rsPane && S.rsPlace === "above") {
    var pa = addPane("rs", "sub"); pa.el.style.flexBasis = basis; pm.el.parentNode.insertBefore(pa.el, pm.el);
    pa.el.style.borderTop = "0"; pm.el.style.borderTop = "1px solid #1E1E22";
    var cha = LW.createChart(pa.cv, opts(pa.cv, false));
    cha.applyOptions({grid:{vertLines:{visible:false}, horzLines:{visible:false}}});
    cha.priceScale("right").applyOptions({scaleMargins:{top:0.3, bottom:0.12}});
    charts.push({id:"rs", chart:cha, el:pa.el, cv:pa.cv, lg:pa.lg, series:rsSeries(cha, ind.rs, T, {lastValueVisible:true}, true)});
  }
  charts[0].series = mainSeries;

  levels(); drawAll(); wire();
  if (prev) { try { main.timeScale().setVisibleLogicalRange(prev); } catch (e) { setRange(rng); } } else setRange(rng);
  legend(d.R.length - 1); mark(); fit();
}
function hexA(hex, a){ var h = hex.replace("#", ""); return "rgba(" + parseInt(h.slice(0, 2), 16) + "," + parseInt(h.slice(2, 4), 16) + "," + parseInt(h.slice(4, 6), 16) + "," + a + ")"; }

function levels(){
  lines.forEach(function(l){ try { mainSeries.removePriceLine(l); } catch (e) {} }); lines = [];
  var add = function(price, color, title, style){ if (price == null) return; lines.push(mainSeries.createPriceLine({price:price, color:color, lineWidth:1, lineStyle:style == null ? 2 : style, axisLabelVisible:true, title:title})); };
  if (S.pd) { add(P.pdh, "#FF9F0A", "PDH"); add(P.pdl, "#FF9F0A", "PDL"); }
  if (S.wk52) { add(P.hi52, "#30D158", "52W high"); add(P.lo52, "#FF453A", "52W low"); }
  draws.h.forEach(function(p){ add(p, "#2962FF", "", 0); });
}
function snap(t){ var d = dataFor(iv), T = d.T; if (d.idx.has(t)) return t; for (var i = 0; i < T.length; i++) if (T[i] >= t) return T[i]; return T[T.length - 1]; }
function drawAll(){
  drawSeries.forEach(function(s){ try { main.removeSeries(s); } catch (e) {} }); drawSeries = [];
  draws.t.forEach(function(x){
    var a = {time:snap(x.t1), value:x.p1}, b = {time:snap(x.t2), value:x.p2}; if (a.time === b.time) return; if (a.time > b.time) { var c = a; a = b; b = c; }
    var s = lineOn(main, "#2962FF", 2, {autoscaleInfoProvider:function(){ return null; }}); s.setData([a, b]); drawSeries.push(s);
  });
}

// keep every pane scrolled, zoomed and pointed at the same candle
function wire(){
  charts.forEach(function(c){
    c.chart.timeScale().subscribeVisibleLogicalRangeChange(function(r){
      if (!r || syncing) return; syncing = true;
      charts.forEach(function(o){ if (o !== c) { try { o.chart.timeScale().setVisibleLogicalRange(r); } catch (e) {} } });
      syncing = false;
      if (c.id === "main") { var n = dataFor(iv).R.length; $("latest").style.display = r.to < n - 2 ? "flex" : "none"; placeMeasure(); }
    });
    c.chart.subscribeCrosshairMove(function(p){
      if (xs) return; var k = tkey(p.time), d = dataFor(iv), i = k ? d.idx.get(k) : undefined; xs = true;
      try {
        charts.forEach(function(o){
          if (o === c) return;
          if (i === undefined) { o.chart.clearCrosshairPosition(); return; }
          var v = o.id === "main" ? d.close[i] : o.id === "rsi" ? ind.rsi[i] : o.id === "rs" ? ind.rs.line[i] : ind.macd.m[i];
          if (v != null && o.series) o.chart.setCrosshairPosition(v, k, o.series);
        });
        legend(i === undefined ? d.R.length - 1 : i);
        if (c.id === "main") hover(p);
      } catch (e) {}
      xs = false;
    });
  });
  main.subscribeClick(onClick);
}

function legend(i){
  var d = dataFor(iv), r = d.R[i]; if (!r) return;
  var o = S.type === "heikin" && d.ohlc[i] ? [d.ohlc[i].open, d.ohlc[i].high, d.ohlc[i].low, d.ohlc[i].close] : [r[1], r[2], r[3], r[4]];
  var pc = i > 0 ? d.R[i - 1][4] : r[1], ch = r[4] - pc, pct = pc ? ch / pc * 100 : 0, col = pal(), k = ch >= 0 ? col.up : col.dn;
  var s = '<div class="row"><b>' + SYMH + '</b> <span class="mut">· ' + IVN[iv] + ' · NSE</span> &nbsp;O <i style="color:' + k + '">' + fmt(o[0]) + '</i> H <i style="color:' + k + '">' + fmt(o[1]) +
          '</i> L <i style="color:' + k + '">' + fmt(o[2]) + '</i> C <i style="color:' + k + '">' + fmt(o[3]) + '</i> <span style="color:' + k + '">' + (ch >= 0 ? "+" : "") + fmt(ch) + " (" + (ch >= 0 ? "+" : "") + pct.toFixed(2) + '%)</span></div>';
  var parts = [];
  (ind.ma || []).forEach(function(x){ parts.push('<span style="color:' + x.m.color + '">' + x.m.kind + " " + x.m.len + " " + fmt(x.a[i]) + "</span>"); });
  if (ind.bb) parts.push('<span style="color:#5B8DEF">BB ' + S.bbLen + ", " + S.bbK + " &nbsp;" + fmt(ind.bb.u[i]) + " / " + fmt(ind.bb.m[i]) + " / " + fmt(ind.bb.l[i]) + "</span>");
  if (parts.length) s += '<div class="row">' + parts.join(" &nbsp; ") + "</div>";
  if (S.vol) s += '<div class="row">Vol <i style="color:' + k + '">' + fmtVol(r[5]) + "</i>" + (ind.volma && ind.volma[i] != null ? ' &nbsp;<span style="color:#5AC8FA">Average ' + S.volmaLen + " " + fmtVol(ind.volma[i]) +
       "</span> &nbsp;<span class=\"mut\">" + (r[5] / ind.volma[i]).toFixed(2) + "× average</span>" : "") + "</div>";
  if (ind.rs && S.rsPlace === "price") s += '<div class="row">' + rsLegend(i) + "</div>";
  charts[0].lg.innerHTML = s;
  charts.forEach(function(c){
    if (c.id === "rs") c.lg.innerHTML = rsLegend(i);
    if (c.id === "rsi") c.lg.innerHTML = 'RSI <span class="mut">' + S.rsiLen + '</span> <span style="color:#BF5AF2">' + fmt(ind.rsi[i]) + '</span> &nbsp;<span style="color:#FF9F0A">Average <span class="mut">14</span> ' + fmt(ind.rsima[i]) + "</span>";
    if (c.id === "macd") c.lg.innerHTML = 'MACD <span class="mut">12, 26, 9</span> <span style="color:#2962FF">' + fmt(ind.macd.m[i]) + '</span> &nbsp;<span style="color:#FF9F0A">Signal ' + fmt(ind.macd.s[i]) +
       '</span> &nbsp;<span class="mut">Histogram ' + fmt(ind.macd.h[i]) + "</span>";
  });
}

function setRange(r){
  var arr = dataFor(iv).T; if (!arr.length) return;
  if (r === "ALL") { main.timeScale().fitContent(); return; }
  var last = new Date(arr[arr.length - 1] + "T00:00:00Z"), from;
  if (r === "YTD") from = last.getUTCFullYear() + "-01-01";
  else { var m = {"1M":1, "3M":3, "6M":6, "1Y":12, "2Y":24, "5Y":60}[r] || 6; last.setUTCMonth(last.getUTCMonth() - m); from = last.toISOString().slice(0, 10); }
  var i = arr.findIndex(function(x){ return x >= from; }); if (i < 0) i = 0;
  main.timeScale().setVisibleLogicalRange({from:i - 1, to:arr.length - 1 + 8});
}
function fit(){ charts.forEach(function(c){ var w = c.cv.clientWidth, h = c.cv.clientHeight; if (w > 0 && h > 0) c.chart.resize(w, h); }); placeMeasure(); }

function mark(){
  document.querySelectorAll("[data-iv]").forEach(function(b){ b.classList.toggle("act", b.dataset.iv === iv); });
  document.querySelectorAll("[data-rng]").forEach(function(b){ b.classList.toggle("act", b.dataset.rng === rng); });
  document.querySelectorAll("[data-tool]").forEach(function(b){ b.classList.toggle("act", b.dataset.tool === tool); });
  document.querySelector('[data-sc="pct"]').classList.toggle("act", !!S.pct); document.querySelector('[data-sc="log"]').classList.toggle("act", !!S.log);
  var t = TYPES.filter(function(x){ return x[0] === S.type; })[0]; $("typ-l").textContent = t ? t[1] : "";
  $("p-main").style.cursor = tool === "cursor" ? "" : "crosshair";
}

// ───────── drawing and measuring ─────────
function hint(t){ var h = $("hint"); if (!t) { h.style.display = "none"; return; } h.textContent = t; h.style.display = "block"; }
function setTool(t){ tool = t; pend = null; if (t !== "measure") clearMeasure();
  hint(t === "hline" ? "Click a price to place a horizontal line" : t === "trend" ? "Click the first point of the trend line" : t === "measure" ? "Click the first point to measure from" : ""); mark(); }
function pointOf(p){ if (!p || !p.point) return null; var k = tkey(p.time); if (!k) return null; var price = mainSeries.coordinateToPrice(p.point.y); if (price == null) return null; return {t:k, p:Math.round(price * 100) / 100}; }
function onClick(p){
  if (tool === "cursor") { if (msr && msr.done) clearMeasure(); return; }
  var pt = pointOf(p); if (!pt) return;
  if (tool === "hline") { draws.h.push(pt.p); saveDraws(); levels(); setTool("cursor"); return; }
  if (tool === "trend") {
    if (!pend) { pend = pt; hint("Click the second point"); return; }
    if (pend.t !== pt.t) { draws.t.push({t1:pend.t, p1:pend.p, t2:pt.t, p2:pt.p}); saveDraws(); drawAll(); }
    setTool("cursor"); return;
  }
  if (tool === "measure") {
    if (!msr || msr.done) { msr = {a:pt, b:pt, done:false}; hint("Click the second point"); placeMeasure(); return; }
    msr.b = pt; msr.done = true; hint(""); placeMeasure(); tool = "cursor"; mark();
  }
}
function hover(p){ if (tool === "measure" && msr && !msr.done) { var pt = pointOf(p); if (pt) { msr.b = pt; placeMeasure(); } } }
function clearMeasure(){ msr = null; $("msr").style.display = "none"; $("msrl").style.display = "none"; }
function placeMeasure(){
  if (!msr || !main) return;
  var box = $("msr"), lab = $("msrl"), ts = main.timeScale(), x1 = ts.timeToCoordinate(snap(msr.a.t)), x2 = ts.timeToCoordinate(snap(msr.b.t)), y1 = mainSeries.priceToCoordinate(msr.a.p), y2 = mainSeries.priceToCoordinate(msr.b.p);
  if (x1 == null || x2 == null || y1 == null || y2 == null) { box.style.display = "none"; lab.style.display = "none"; return; }
  var off = $("p-main").offsetTop, d = dataFor(iv), ch = msr.b.p - msr.a.p, pct = msr.a.p ? ch / msr.a.p * 100 : 0, bars = Math.abs((d.idx.get(snap(msr.b.t)) || 0) - (d.idx.get(snap(msr.a.t)) || 0)),
      days = Math.round(Math.abs(new Date(msr.b.t) - new Date(msr.a.t)) / 864e5), dn = ch < 0;
  box.style.display = "block"; box.style.left = Math.min(x1, x2) + "px"; box.style.top = (off + Math.min(y1, y2)) + "px"; box.style.width = Math.max(1, Math.abs(x2 - x1)) + "px"; box.style.height = Math.max(1, Math.abs(y2 - y1)) + "px";
  box.className = dn ? "dn" : ""; lab.className = dn ? "dn" : "";
  lab.style.display = "block"; lab.style.left = ((x1 + x2) / 2) + "px"; lab.style.top = (off + Math.max(y1, y2)) + "px";
  lab.innerHTML = (ch >= 0 ? "+" : "") + fmt(ch) + " (" + (ch >= 0 ? "+" : "") + pct.toFixed(2) + "%)<br>" + bars + " bars · " + days + " days";
}
function undo(){ if (msr) { clearMeasure(); return; } if (draws.t.length) { draws.t.pop(); drawAll(); } else if (draws.h.length) { draws.h.pop(); levels(); } saveDraws(); }
function clearAll(){ draws = {h:[], t:[]}; saveDraws(); clearMeasure(); levels(); drawAll(); }
document.querySelectorAll("[data-tool]").forEach(function(b){ b.onclick = function(){ setTool(b.dataset.tool); }; });
document.querySelector('[data-act="undo"]').onclick = undo; document.querySelector('[data-act="clear"]').onclick = clearAll;

// ───────── toolbar ─────────
document.querySelectorAll("[data-iv]").forEach(function(b){ b.onclick = function(){ if (!(P[b.dataset.iv] && P[b.dataset.iv].length)) return; iv = b.dataset.iv; clearMeasure(); build(false); }; });
document.querySelectorAll("[data-rng]").forEach(function(b){ b.onclick = function(){ rng = b.dataset.rng; mark(); setRange(rng); }; });
document.querySelector('[data-sc="pct"]').onclick = function(){ S.pct = !S.pct; if (S.pct) S.log = false; saveS(); scale(); };
document.querySelector('[data-sc="log"]').onclick = function(){ S.log = !S.log; if (S.log) S.pct = false; saveS(); scale(); };
document.querySelector('[data-sc="auto"]').onclick = function(){ charts.forEach(function(c){ c.chart.priceScale("right").applyOptions({autoScale:true}); }); };
function scale(){ mainSeries.priceScale().applyOptions({mode:S.pct ? 2 : (S.log ? 1 : 0)}); mark(); }
$("latest").onclick = function(){ main.timeScale().scrollToRealTime(); };

function closeMenus(){ document.querySelectorAll(".menu").forEach(function(m){ m.classList.remove("open"); }); }
function openMenu(id, btn){
  var m = $(id), was = m.classList.contains("open"); closeMenus(); if (was) return;
  var pr = $("panes").getBoundingClientRect(), br = btn.getBoundingClientRect();
  m.classList.add("open"); m.style.top = "4px"; m.style.left = Math.max(4, Math.min(br.left - pr.left, pr.width - m.offsetWidth - 4)) + "px";
}
function item(label, on, fn, sw, right){
  var b = el("button", "mi" + (on ? " on" : "")); if (on !== null) b.appendChild(el("span", "ck", "✓"));
  if (sw) { var d = el("span", "sw"); d.style.background = sw; b.appendChild(d); }
  b.appendChild(el("span", "", label)); if (right) b.appendChild(el("small", "", right)); b.onclick = function(e){ e.stopPropagation(); fn(); }; return b;
}
function menus(){
  var mt = $("menu-type"); mt.innerHTML = "";
  TYPES.forEach(function(t){ mt.appendChild(item(t[1], S.type === t[0], function(){ S.type = t[0]; saveS(); closeMenus(); build(true); menus(); })); });
  var mi = $("menu-ind"); mi.innerHTML = "";
  var tg = function(label, key, sw, right){ mi.appendChild(item(label, !!S[key], function(){ S[key] = !S[key]; saveS(); build(true); menus(); $("menu-ind").classList.add("open"); }, sw, right)); };
  mi.appendChild(el("div", "mh", "Moving averages"));
  S.ma.forEach(function(m){ mi.appendChild(item(m.kind + " " + m.len, m.on, function(){ m.on = !m.on; saveS(); build(true); menus(); $("menu-ind").classList.add("open"); }, m.color)); });
  mi.appendChild(el("div", "mh", "On the chart"));
  tg("Volume", "vol"); tg("Volume average", "volma", "#5AC8FA", String(S.volmaLen)); tg("Bollinger bands", "bb", "#2962FF", S.bbLen + ", " + S.bbK);
  mi.appendChild(el("div", "mh", "Below the chart"));
  tg("RSI", "rsi", "#BF5AF2", String(S.rsiLen)); tg("MACD", "macd", "#2962FF", "12, 26, 9");
  mi.appendChild(el("div", "mh", "Relative strength line"));
  tg("RS line", "rs", S.rsUp, P.ix && P.ix.v ? "vs " + RS_NAME : "index not loaded");
  [["above","Pane above the chart"],["pane","Pane below the chart"],["price","On the price chart"]].forEach(function(x){
    var it = item(x[1], S.rsPlace === x[0], function(){ S.rsPlace = x[0]; S.rs = true; saveS(); build(true); menus(); $("menu-ind").classList.add("open"); }); it.querySelector(".ck").style.borderRadius = "50%"; mi.appendChild(it); });
  mi.appendChild(el("div", "mh", "Lines on the chart"));
  tg("Previous day high and low (PDH / PDL)", "pd", "#FF9F0A"); tg("52-week high and low", "wk52", "#30D158");
  mi.appendChild(item("Lengths and colours…", null, function(){ closeMenus(); openSettings(); }));
  var md = $("menu-draw"); md.innerHTML = "";
  [["hline","Horizontal line"],["trend","Trend line"],["measure","Measure"]].forEach(function(t){ md.appendChild(item(t[1], null, function(){ closeMenus(); setTool(t[0]); })); });
  md.appendChild(item("Remove the last drawing", null, function(){ closeMenus(); undo(); })); md.appendChild(item("Remove all drawings", null, function(){ closeMenus(); clearAll(); }));
}
$("b-type").onclick = function(e){ e.stopPropagation(); openMenu("menu-type", this); };
$("b-ind").onclick = function(e){ e.stopPropagation(); openMenu("menu-ind", this); };
$("b-draw").onclick = function(e){ e.stopPropagation(); openMenu("menu-draw", this); };
document.addEventListener("click", function(e){ if (!e.target.closest || !e.target.closest(".menu")) closeMenus(); });

function openSettings(){ dirty = false; buildSettings(); $("setwrap").classList.add("open"); }
function closeSettings(){ $("setwrap").classList.remove("open"); if (dirty) { if (["D","W","M"].indexOf(S.iv) >= 0 && P[S.iv] && P[S.iv].length) iv = S.iv; if (S.rng) rng = S.rng; build(false); menus(); } }
$("b-set").onclick = openSettings; $("set-x").onclick = closeSettings; $("set-done").onclick = closeSettings;
$("setwrap").addEventListener("click", function(e){ if (e.target === this) closeSettings(); });

$("b-shot").onclick = function(){
  try { var cv = main.takeScreenshot(); cv.toBlob(function(b){ if (!b) return; var a = document.createElement("a"); a.href = URL.createObjectURL(b); a.download = SYM + "_" + IVN[iv] + "_" + dataFor(iv).T.slice(-1)[0] + ".png";
    document.body.appendChild(a); a.click(); setTimeout(function(){ URL.revokeObjectURL(a.href); a.remove(); }, 500); }); } catch (e) {}
};

// ───────── full screen: fill the app window, and the whole screen where the browser allows ─────────
var fsOn = false, fsOld = null;
function frame(){ try { return window.frameElement; } catch (e) { return null; } }
function setFS(on){
  var f = frame(); fsOn = on; document.body.classList.toggle("fs", on); $("b-fs").classList.toggle("act", on);
  if (f) {
    if (on) { fsOld = f.getAttribute("style") || ""; f.style.cssText = fsOld + ";position:fixed !important;inset:0 !important;top:0 !important;left:0 !important;width:100vw !important;height:100vh !important;height:100dvh !important;z-index:2147483000 !important;border:0 !important;background:#0E0E10;max-width:none !important"; }
    else if (fsOld !== null) { f.setAttribute("style", fsOld); fsOld = null; }
  }
  try {
    var de = document.documentElement;
    if (on && de.requestFullscreen && !document.fullscreenElement) { var pr = de.requestFullscreen(); if (pr && pr.catch) pr.catch(function(){}); }
    if (!on && document.fullscreenElement && document.exitFullscreen) { var px = document.exitFullscreen(); if (px && px.catch) px.catch(function(){}); }
  } catch (e) {}
  setTimeout(fit, 60); setTimeout(fit, 350);
}
$("b-fs").onclick = function(){ setFS(!fsOn); };
document.addEventListener("fullscreenchange", function(){ if (!document.fullscreenElement && fsOn) setFS(false); });
document.addEventListener("keydown", function(e){
  if (e.key === "Escape") { if ($("setwrap").classList.contains("open")) closeSettings(); else if (tool !== "cursor" || msr) { setTool("cursor"); clearMeasure(); } else if (fsOn) setFS(false); closeMenus(); }
});

function clock(){ try { $("clock").textContent = new Date().toLocaleTimeString("en-GB", {timeZone:"Asia/Kolkata", hour:"2-digit", minute:"2-digit", second:"2-digit"}) + " IST"; } catch (e) {} }
clock(); setInterval(clock, 1000);

window.addEventListener("resize", fit);
if (window.ResizeObserver) new ResizeObserver(fit).observe($("panes"));
menus(); build(false);
setTimeout(function(){ fit(); setRange(rng); }, 60);
if (S.rs && !(P.ix && P.ix.v)) { hint("No data for " + RS_NAME + ", so the RS line is hidden. Choose the index in Account → Relative strength index."); setTimeout(function(){ if (tool === "cursor") hint(""); }, 9000); }
window.__arkaChart = {S:function(){ return S; }, charts:function(){ return charts; }, ind:function(){ return ind; }, draws:function(){ return draws; }};
})();
</script></body></html>"""


def _doc(mode: str, payload: str = "{}", sym: str = "", default_range: str = "6M") -> str:
    return (_TEMPLATE.replace("__LIB__", _LIB if mode == "chart" else "")
            .replace("__PAYLOAD__", payload)
            .replace("__SYM__", json.dumps(sym).replace("</", "<\\/"))
            .replace("__MODE__", mode)

            .replace("__RANGE__", default_range))


def render_pro_chart(symbol: str, height: int = 660, default_range: str = "6M") -> bool:
    """Render the chart. Returns False (and shows a warning) if no data."""
    sym = str(symbol or "").upper().strip()
    if not sym:
        return False
    try:
        payload = _payload_json(sym, rs_index_name())
    except Exception:
        st.warning(f"No chart for {sym}: Yahoo Finance returned no price history. Try again in a minute.")
        return False
    if default_range not in {"1M", "3M", "6M", "YTD", "1Y", "2Y", "5Y", "ALL"}:
        default_range = "6M"
    components.html(_doc("chart", payload, sym, default_range), height=height, scrolling=False)
    return True


def render_chart_settings(height: int = 1640) -> None:
    """Chart settings for the Account page. Saved in this browser; every chart on this device opens with them."""
    components.html(_doc("settings"), height=height, scrolling=True)

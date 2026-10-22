"""Arka Trades — Market Breadth (Chartink dashboard 86550).

Loads only local history on page open. Chartink is contacted only when the
user explicitly presses SYNC CHARTINK. Compatible with the existing app route.
"""
from __future__ import annotations

import html
import pandas as pd
import streamlit as st

from breadth_engine import HISTORY_FILE, load_history, sync_chartink

_MAS = (20, 50, 200)
_REQUIRED = ["date"] + [f"{side}_{ma}dma" for ma in _MAS for side in ("above", "below")]


def _css() -> None:
    st.markdown("""
    <style>
    .stApp{background:#050607;color:#dce1e6}
    .block-container{padding:12px 16px 30px;max-width:1500px}
    section[data-testid="stSidebar"]{display:none}
    .mb-top{display:flex;justify-content:space-between;align-items:center;gap:12px;
      border-top:1px solid #252a30;border-bottom:1px solid #252a30;padding:10px 4px;margin-bottom:18px;
      font:11px monospace;letter-spacing:.8px}
    .mb-brand{font-weight:800;color:#e6e9ed}.mb-tag{color:#e7a51a;font-weight:800}
    .mb-status{color:#6aa978;font-size:9px}
    .mb-title{font:800 20px monospace;letter-spacing:.4px;margin:2px 0 4px;color:#edf0f3}
    .mb-sub{font:10px monospace;letter-spacing:.35px;color:#77818c;margin-bottom:16px}
    .mb-card{height:100%;min-height:100px;background:#0a0c0f;border:1px solid #252b31;padding:12px}
    .mb-label{font:9px monospace;letter-spacing:.8px;color:#818b96}
    .mb-value{font:700 23px monospace;color:#edf0f3;margin-top:7px}
    .mb-meta{font:10px monospace;color:#8a949e;margin-top:6px;line-height:1.5}
    .mb-up{color:#5cc080}.mb-down{color:#df6d6d}.mb-flat{color:#a1a8b0}.mb-amber{color:#e7a51a}
    .mb-section{font:700 12px monospace;color:#e7a51a;letter-spacing:.8px;margin:22px 0 8px;
      padding-bottom:8px;border-bottom:1px solid #252a30}
    .mb-note{border:1px solid #252a30;background:#090b0e;padding:9px 11px;color:#84909b;
      font:10px monospace;line-height:1.6;margin:8px 0}
    .mb-warn{border:1px solid #5a4917;background:#100d06;color:#e1bd4a;padding:11px;
      font:10px monospace;line-height:1.6;margin:10px 0}
    .mb-error{border:1px solid #663737;background:#150909;color:#ef8989;padding:11px;
      font:10px monospace;line-height:1.6;white-space:pre-wrap;margin:10px 0}
    .mb-table-wrap{width:100%;overflow-x:auto;border:1px solid #262b31;background:#080a0c}
    table.mb-table{width:100%;border-collapse:collapse;font:10px monospace;white-space:nowrap}
    .mb-table th,.mb-table td{padding:8px 9px;border-right:1px solid #242a30;border-bottom:1px solid #242a30;text-align:right}
    .mb-table th{background:#101318;color:#e7a51a;font-size:9px;letter-spacing:.4px}
    .mb-table td{color:#d8dde2}.mb-table td.date,.mb-table th.date{text-align:left;position:sticky;left:0;background:#0b0d10}
    .mb-table tr:nth-child(even) td{background:#0b0d10}.mb-table tr:hover td{background:#11161b}
    .stButton>button{border:1px solid #363d45;border-radius:2px;background:#0b0d10;color:#dce1e6;
      font:10px monospace;min-height:34px}
    .stButton>button:hover{border-color:#e7a51a;color:#e7a51a;background:#101216}
    div[data-baseweb="select"]>div{background:#0a0c0f;border-color:#303740;border-radius:2px}
    </style>""", unsafe_allow_html=True)


def _number(value):
    try:
        n = pd.to_numeric(value, errors="coerce")
        return None if pd.isna(n) else float(n)
    except Exception:
        return None


def _fmt(value, decimals=0):
    n = _number(value)
    if n is None:
        return "—"
    return f"{n:,.{decimals}f}"


def _delta(value):
    n = _number(value)
    if n is None:
        return "—"
    return f"{n:+,.0f}"


def _delta_class(value):
    n = _number(value)
    if n is None or n == 0:
        return "mb-flat"
    return "mb-up" if n > 0 else "mb-down"


def _prepare_history(history: pd.DataFrame) -> pd.DataFrame:
    if history is None or history.empty:
        return pd.DataFrame(columns=_REQUIRED)
    df = history.copy()
    missing = [c for c in _REQUIRED if c not in df.columns]
    if missing:
        raise ValueError("Local breadth history is missing columns: " + ", ".join(missing))
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in _REQUIRED[1:]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date").drop_duplicates("date", keep="last")
    return df.reset_index(drop=True)


def _card(label, value, meta, color=""):
    st.markdown(f'<div class="mb-card"><div class="mb-label">{html.escape(label)}</div>'
                f'<div class="mb-value {color}">{value}</div>'
                f'<div class="mb-meta">{meta}</div></div>', unsafe_allow_html=True)


def _render_summary(history: pd.DataFrame) -> None:
    latest = history.iloc[-1]
    prev = history.iloc[-2] if len(history) > 1 else None
    cols = st.columns(4)
    for col, ma in zip(cols[:3], _MAS):
        above, below = _number(latest[f"above_{ma}dma"]), _number(latest[f"below_{ma}dma"])
        total = (above or 0) + (below or 0)
        pct = above / total * 100 if above is not None and total > 0 else None
        prior = _number(prev[f"above_{ma}dma"]) if prev is not None else None
        delta = above - prior if above is not None and prior is not None else None
        color = "mb-up" if pct is not None and pct >= 55 else "mb-down" if pct is not None and pct < 45 else "mb-amber"
        meta = f'{_fmt(pct, 1)}% of {_fmt(total)} stocks · '
        meta += f'<span class="{_delta_class(delta)}">1-session change {_delta(delta)}</span>'
        with col:
            _card(f"ABOVE {ma} DMA", _fmt(above), meta, color)
    with cols[3]:
        above20, below20 = _number(latest["above_20dma"]), _number(latest["below_20dma"])
        total20 = (above20 or 0) + (below20 or 0)
        pct20 = above20 / total20 * 100 if above20 is not None and total20 else None
        state = "BROAD STRENGTH" if pct20 is not None and pct20 >= 70 else "POSITIVE" if pct20 is not None and pct20 >= 55 else "MIXED" if pct20 is not None and pct20 >= 45 else "WEAK" if pct20 is not None else "NO DATA"
        date = pd.Timestamp(latest["date"]).strftime("%d %b %Y")
        with col:
            _card("BREADTH CONDITION · 20 DMA", html.escape(state), f"Session {date} · {_fmt(len(history))} stored rows", "mb-amber")
    st.markdown('<div class="mb-note">Participation thresholds are descriptive heuristics, not trading signals. Counts reflect the Chartink dashboard series, not a live exchange-wide advance/decline feed.</div>', unsafe_allow_html=True)


def _render_trend(history: pd.DataFrame, lookback: int) -> None:
    chart = history.sort_values("date").tail(lookback).copy().set_index("date")
    for ma in _MAS:
        a = chart[f"above_{ma}dma"]
        b = chart[f"below_{ma}dma"]
        total = (a + b).where((a + b) != 0)
        chart[f"Above {ma} DMA %"] = a / total * 100
    st.markdown('<div class="mb-section">PARTICIPATION TREND · % OF TRACKED STOCKS</div>', unsafe_allow_html=True)
    st.line_chart(chart[[f"Above {ma} DMA %" for ma in _MAS]], height=280, use_container_width=True)
    latest = history.iloc[-1]
    previous = history.iloc[-2] if len(history) > 1 else None
    kpi_cols = st.columns(3)
    for col, ma in zip(kpi_cols, _MAS):
        a = _number(latest[f"above_{ma}dma"])
        b = _number(latest[f"below_{ma}dma"])
        total = (a or 0) + (b or 0)
        p = a / total * 100 if a is not None and total else None
        old = _number(previous[f"above_{ma}dma"]) if previous is not None else None
        d = a - old if a is not None and old is not None else None
        with col:
            st.markdown(f'<div class="mb-note"><b>ABOVE {ma} DMA</b> · {_fmt(p,1)}%<br>'
                        f'Count {_fmt(a)} / {_fmt(total)} · Last-session change '
                        f'<span class="{_delta_class(d)}">{_delta(d)}</span></div>', unsafe_allow_html=True)


def _render_history_table(history: pd.DataFrame, lookback: int) -> None:
    df = history.sort_values("date").tail(lookback).copy()
    for ma in _MAS:
        for side in ("above", "below"):
            col = f"{side}_{ma}dma"
            df[f"{col}_delta"] = df[col].diff()
    df = df.sort_values("date", ascending=False)
    rows = []
    for _, r in df.iterrows():
        date = pd.Timestamp(r["date"]).strftime("%d %b %Y")
        cells = [f'<td class="date">{date}</td>']
        for ma in _MAS:
            for side in ("above", "below"):
                val, delta = r[f"{side}_{ma}dma"], r[f"{side}_{ma}dma_delta"]
                cells.append(f'<td>{_fmt(val)}</td>')
                cells.append(f'<td class="{_delta_class(delta)}">{_delta(delta)}</td>')
        rows.append("<tr>" + "".join(cells) + "</tr>")
    groups = "".join(f'<th colspan="4">{ma} DMA PARTICIPATION</th>' for ma in _MAS)
    subs = "".join('<th>ABOVE</th><th>Δ</th><th>BELOW</th><th>Δ</th>' for _ in _MAS)
    st.markdown('<div class="mb-section">DAILY BREADTH HISTORY</div>'
                '<div class="mb-table-wrap"><table class="mb-table"><thead>'
                f'<tr><th class="date" rowspan="2">DATE</th>{groups}</tr><tr>{subs}</tr>'
                f'</thead><tbody>{"".join(rows)}</tbody></table></div>'
                '<div class="mb-note">Δ = change versus the previous stored Chartink session. '
                'A positive change in BELOW counts is not automatically bullish or bearish by itself.</div>',
                unsafe_allow_html=True)


def render_market_breadth() -> None:
    """Render the page; this function name preserves the existing app integration."""
    _css()
    st.markdown('<div class="mb-top"><div class="mb-brand">ARKA TRADES</div>'
                '<div class="mb-tag">F7 · MARKET BREADTH</div>'
                '<div class="mb-status">CHARTINK · DAILY EOD</div></div>', unsafe_allow_html=True)
    st.markdown('<div class="mb-title">MARKET BREADTH</div>'
                '<div class="mb-sub">DAILY STOCK PARTICIPATION · 20 / 50 / 200 DMA · LOCAL HISTORY</div>',
                unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns([1.15, 1.05, 1.0, 3.2])
    with c1:
        sync = st.button("↻  SYNC CHARTINK", use_container_width=True, key="mb_sync")
    with c2:
        clear = st.button("CLEAR LOCAL", use_container_width=True, key="mb_clear")
    with c3:
        lookback = st.selectbox("LOOKBACK", [20, 50, 90, 180], index=0, label_visibility="collapsed", key="mb_lookback")
    with c4:
        st.markdown('<div class="mb-note">PAGE OPEN + LOOKBACK CHANGES READ LOCAL HISTORY ONLY. SYNC IS THE ONLY CHARTINK REQUEST.</div>', unsafe_allow_html=True)

    if clear:
        try:
            HISTORY_FILE.unlink(missing_ok=True)
        except Exception as exc:
            st.error(f"Could not clear local history: {exc}")
        st.rerun()

    if sync:
        with st.spinner("Syncing Chartink daily breadth history…"):
            try:
                result = sync_chartink(force=True)
                rows = result.get("rows", "—") if isinstance(result, dict) else "—"
                latest = result.get("latest_date", "—") if isinstance(result, dict) else "—"
                st.success(f"Chartink sync complete · {rows} source rows · latest session {latest}")
            except Exception as exc:
                st.markdown(f'<div class="mb-error">CHARTINK SYNC FAILED<br><br>{html.escape(str(exc))}</div>', unsafe_allow_html=True)
                return

    try:
        history = _prepare_history(load_history())
    except Exception as exc:
        st.markdown(f'<div class="mb-error">COULD NOT READ LOCAL BREADTH HISTORY<br><br>{html.escape(str(exc))}</div>', unsafe_allow_html=True)
        return

    if history.empty:
        st.markdown('<div class="mb-warn">NO LOCAL BREADTH HISTORY FOUND. PRESS SYNC CHARTINK TO IMPORT THE SERIES FROM DASHBOARD 86550.</div>', unsafe_allow_html=True)
        return

    _render_summary(history)
    _render_trend(history, int(lookback))
    _render_history_table(history, int(lookback))
    source = html.escape(str(history.iloc[-1].get("source", "Chartink dashboard 86550")))
    last = pd.Timestamp(history.iloc[-1]["date"]).strftime("%d %b %Y")
    st.markdown(f'<div class="mb-note">SOURCE · {source} · LATEST SESSION · {last} · '
                f'LOCAL ROWS · {len(history):,} · LOOKBACK · {min(int(lookback), len(history))} SESSIONS</div>',
                unsafe_allow_html=True)


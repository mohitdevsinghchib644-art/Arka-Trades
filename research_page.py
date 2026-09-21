"""Arka Trades — Research Terminal v10 / ScanX data edition.

Design goals
------------
* Keep the existing Arka terminal shell; do not create a separate dashboard.
* Use the public ScanX company page as the primary research-data source.
* Keep yfinance/Screener as fallbacks for price history and datasets ScanX does
  not expose in the public company page.
* Lazy-load the ScanX page and only render the selected research function.
* Never invent missing data: unavailable fields are clearly marked.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from io import StringIO
import html
import re
from typing import Any

import pandas as pd
import streamlit as st
import yfinance as yf

from news_feed import get_security_news
from scanx_data import fetch_scanx_company

try:
    from screener_scraper import resolve_symbol, get_summary, get_sector_info
except Exception:  # pragma: no cover - lets the module still import in isolation
    resolve_symbol = None
    get_summary = None
    get_sector_info = None

IST = timezone(timedelta(hours=5, minutes=30))

T0 = {
    "bg": "#000000", "panel": "#080808", "panel2": "#0E0E0E", "row_alt": "#0B0B0B",
    "border": "#242424", "amber": "#FF9F0A", "ivory": "#E8E8E8", "t2": "#9A9A9A",
    "t3": "#5E5E5E", "green": "#30D158", "red": "#FF453A", "cyan": "#5AC8FA",
    "blue": "#5AC8FA", "mono": "'JetBrains Mono','Consolas',monospace",
}


def _theme(T):
    x = dict(T0)
    if T:
        x.update(T)
    return x


def _esc(x: Any) -> str:
    return html.escape(str(x))


def _tag(status: str | None, T, text: str | None = None):
    if status == "live":
        label, color = text or "● SCANX LIVE", T["green"]
    elif status == "cached":
        label, color = text or "◐ CACHED", T["amber"]
    else:
        label, color = text or "✕ N/A", T["t3"]
    return f'<span style="color:{color};font:700 9px {T["mono"]};letter-spacing:.8px">{_esc(label)}</span>'


def _header(title, T, status=None, note=None):
    right = _tag(status, T) if status else ""
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid {T["border"]};padding:9px 0 6px;margin:13px 0 8px">'
        f'<span style="font:700 11px {T["mono"]};letter-spacing:1px;color:{T["amber"]}">{_esc(title).upper()}</span>{right}</div>',
        unsafe_allow_html=True,
    )
    if note:
        st.markdown(f'<div style="font:9px {T["mono"]};color:{T["t3"]};margin:3px 0 8px">{_esc(note)}</div>', unsafe_allow_html=True)


def _panel(title, body, T, dashed=False):
    border = f"1px dashed {T['border']}" if dashed else f"1px solid {T['border']}"
    st.markdown(
        f'<div style="border:{border};background:{T["panel"]};padding:11px 13px;margin:7px 0">'
        f'<div style="font:700 10px {T["mono"]};color:{T["ivory"]};margin-bottom:7px">{_esc(title).upper()}</div>{body}</div>',
        unsafe_allow_html=True,
    )


def _unavailable(title, requirement, T, detail=None):
    detail = detail or "No verified connected dataset returned this field."
    _panel(
        title,
        f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.75">'
        f'<b style="color:{T["t3"]}">STATUS:</b> DATA UNAVAILABLE<br>'
        f'<b style="color:{T["t3"]}">REQUIRED:</b> {_esc(requirement)}<br>{_esc(detail)}</div>',
        T,
        dashed=True,
    )


def _kpis(items, T, cols=4):
    c = st.columns(cols)
    for i, (label, value, sub) in enumerate(items):
        with c[i % cols]:
            st.markdown(
                f'<div style="border:1px solid {T["border"]};background:{T["panel"]};padding:10px 11px;min-height:67px;margin-bottom:7px">'
                f'<div style="font:9px {T["mono"]};color:{T["t3"]};letter-spacing:.6px">{_esc(label.upper())}</div>'
                f'<div style="font:700 15px {T["mono"]};color:{T["ivory"]};margin-top:4px">{_esc(value)}</div>'
                f'<div style="font:9px {T["mono"]};color:{T["t3"]};margin-top:3px">{_esc(sub or "")}</div></div>',
                unsafe_allow_html=True,
            )


def _fmt(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return str(v)


def _df_to_rows(df: pd.DataFrame | None, max_rows=30, max_cols=12):
    if df is None or df.empty:
        return [], []
    x = df.copy()
    if isinstance(x.index, pd.MultiIndex):
        x.index = [" · ".join(str(z) for z in idx) for idx in x.index]
    periods = [str(c)[:18] for c in x.columns[:max_cols]]
    rows=[]
    for idx, row in x.head(max_rows).iterrows():
        vals=[]
        for v in row.iloc[:max_cols].tolist():
            if pd.isna(v): vals.append("—")
            elif isinstance(v, (pd.Timestamp, datetime)): vals.append(str(v)[:19])
            elif isinstance(v, float): vals.append(f"{v:,.2f}")
            else: vals.append(str(v))
        rows.append({"label":str(idx),"values":vals})
    return periods, rows


def _table(periods, rows, T, highlights=(), links=False):
    if not rows:
        _unavailable("No rows returned", "Selected ScanX / provider dataset", T)
        return
    heads = "".join(f'<th style="text-align:right;padding:7px 8px;color:{T["t3"]};font:700 9px {T["mono"]};white-space:nowrap">{_esc(p)}</th>' for p in periods)
    out=[]
    for i,r in enumerate(rows):
        label=str(r.get("label", "")); hl=any(x.lower() in label.lower() for x in highlights)
        vals=r.get("values",[])
        cells="".join(f'<td style="text-align:right;padding:7px 8px;color:{T["ivory"] if hl else T["t2"]};font:{"700" if hl else "500"} 10px {T["mono"]};white-space:nowrap">{_esc(v)}</td>' for v in vals)
        out.append(f'<tr style="background:{T["row_alt"] if i%2 else "transparent"};border-bottom:1px solid {T["border"]}"><td style="padding:7px 8px;color:{T["amber"] if hl else T["t2"]};font:{"700" if hl else "500"} 10px {T["mono"]};white-space:nowrap">{_esc(label)}</td>{cells}</tr>')
    st.markdown(
        f'<div style="overflow-x:auto;border:1px solid {T["border"]};background:{T["panel"]};max-width:100%"><table style="width:100%;border-collapse:collapse">'
        f'<thead><tr style="background:{T["panel2"]};border-bottom:1px solid {T["border"]}"><th style="text-align:left;padding:7px 8px;color:{T["t3"]};font:700 9px {T["mono"]}">METRIC</th>{heads}</tr></thead>'
        f'<tbody>{"".join(out)}</tbody></table></div>', unsafe_allow_html=True)


def _rows_from_df(df, label_col=0, max_rows=80):
    if df is None or df.empty:
        return [], []
    x=df.head(max_rows).copy()
    cols=list(x.columns)
    periods=[str(c) for c in cols[1:]]
    rows=[]
    for _,r in x.iterrows():
        rows.append({"label":str(r.iloc[label_col]),"values":[_fmt(v) for v in r.iloc[1:].tolist()]})
    return periods, rows


def _table_to_period_rows(df, max_rows=100, max_cols=12):
    if df is None or df.empty:
        return [], []
    x=df.copy()
    cols=list(x.columns)
    if len(cols) < 2:
        return [], []
    periods=[str(c) for c in cols[1:max_cols+1]]
    rows=[]
    for _,r in x.head(max_rows).iterrows():
        rows.append({"label":str(r.iloc[0]),"values":[_fmt(v) for v in r.iloc[1:max_cols+1].tolist()]})
    return periods, rows


def _scanx_df(data, key):
    return ((data.get("tables") or {}).get(key))


def _scanx_header(data, T):
    s=data.get("snapshot") or {}
    name=data.get("name") or s.get("company_name") or data.get("symbol")
    _header("ScanX security snapshot",T,"live" if data.get("status")=="live" else "unavailable",data.get("url"))
    price=s.get("price","N/A")
    change=s.get("change","N/A")
    color=T["green"] if str(change).startswith("+") else T["red"] if str(change).startswith("-") else T["ivory"]
    st.markdown(
        f'<div style="border:1px solid {T["border"]};border-top:2px solid {T["amber"]};background:{T["panel"]};padding:12px 14px;margin:8px 0">'
        f'<div style="display:flex;justify-content:space-between;align-items:end;gap:15px;flex-wrap:wrap">'
        f'<div><span style="font:800 18px {T["mono"]};color:{T["ivory"]}">{_esc(name)}</span><span style="font:10px {T["mono"]};color:{T["t3"]};margin-left:10px">{_esc(data.get("symbol",""))} · NSE/BSE</span></div>'
        f'<div style="text-align:right"><span style="font:800 22px {T["mono"]};color:{T["ivory"]}">₹{_esc(price)}</span><span style="font:700 11px {T["mono"]};color:{color};margin-left:10px">{_esc(change)}</span></div>'
        f'</div></div>', unsafe_allow_html=True)


def _chart(symbol, T, period="1y"):
    import plotly.graph_objects as go
    h=_yf_history(symbol,period)
    if h is None or h.empty:
        _unavailable("Price chart","Yahoo Finance OHLCV history",T)
        return
    close=h["Close"].dropna()
    fig=go.Figure()
    fig.add_trace(go.Scatter(x=close.index,y=close,name="Price",mode="lines",line=dict(color=T["red"],width=2)))
    ma50=close.rolling(50).mean(); ma200=close.rolling(200).mean()
    fig.add_trace(go.Scatter(x=ma50.index,y=ma50,name="50 DMA",mode="lines",line=dict(color=T["amber"],width=1.4)))
    fig.add_trace(go.Scatter(x=ma200.index,y=ma200,name="200 DMA",mode="lines",line=dict(color=T["cyan"],width=1.4)))
    fig.update_layout(height=390,margin=dict(l=0,r=45,t=10,b=0),paper_bgcolor=T["bg"],plot_bgcolor=T["bg"],font=dict(color=T["t2"],family="JetBrains Mono, Consolas, monospace",size=9),showlegend=True,legend=dict(orientation="h",y=1.04,x=0),hovermode="x unified")
    fig.update_xaxes(showgrid=True,gridcolor="#181818",nticks=10)
    fig.update_yaxes(showgrid=True,gridcolor="#181818",side="right")
    st.plotly_chart(fig,use_container_width=True,config={"displayModeBar":True,"scrollZoom":True},key=f"scanx_chart_{symbol}_{period}")
    return h


@st.cache_data(ttl=900,show_spinner=False)
def _yf_history(symbol: str, period: str="1y"):
    try:
        return yf.Ticker(symbol.upper().strip()+".NS").history(period=period,interval="1d",auto_adjust=False)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=900,show_spinner=False)
def _yf_info(symbol: str):
    try: return dict(yf.Ticker(symbol.upper().strip()+".NS").info or {})
    except Exception: return {}


@st.cache_data(ttl=900,show_spinner=False)
def _yf_targets(symbol: str):
    try: return dict(yf.Ticker(symbol.upper().strip()+".NS").analyst_price_targets or {})
    except Exception: return {}


def _core(symbol):
    if not resolve_symbol:
        return {"symbol":symbol,"name":symbol,"url":""}
    try:
        r=resolve_symbol(symbol)
    except Exception:
        r=None
    if not r:
        return {"symbol":symbol,"name":symbol,"url":""}
    url=r.get("url","")
    summary={}; sector={}
    try: summary=get_summary(symbol,url=url) or {}
    except Exception: pass
    try: sector=get_sector_info(symbol,url=url) or {}
    except Exception: pass
    return {"symbol":symbol,"name":r.get("name",symbol),"url":url,"summary":summary,"sector":sector}


@st.cache_data(ttl=3600,show_spinner=False)
def _yf_company_name(symbol: str):
    try:
        info = yf.Ticker(symbol.upper().strip()+".NS").info or {}
        return info.get("longName") or info.get("shortName") or ""
    except Exception:
        return ""


def _load_data(symbol):
    core=_core(symbol)
    company_name=core.get("name") or ""
    # If the optional Screener resolver is unavailable, use Yahoo's public
    # company name so ScanX can build its name-based company slug.
    if not company_name or company_name.upper().strip() == symbol.upper().strip():
        company_name=_yf_company_name(symbol) or company_name or symbol
    with st.spinner("Loading public ScanX research data…"):
        scanx=fetch_scanx_company(symbol,company_name=company_name)
    return core,scanx


def _overview(data,T):
    s=data.get("snapshot") or {}
    info=_yf_info(data["symbol"])
    sec=((data.get("core") or {}).get("sector") or {}).get("data") or {}
    items=[
        ("Market Cap",s.get("Market Cap") or "N/A","ScanX"),
        ("P/E",s.get("PE Ratio") or "N/A","ScanX"),
        ("EPS",s.get("EPS") or "N/A","ScanX"),
        ("P/B",s.get("PB Ratio") or "N/A","ScanX"),
        ("Book Value",s.get("Book Value") or "N/A","ScanX"),
        ("EBITDA",s.get("EBITDA") or "N/A","ScanX"),
        ("ROE",s.get("Return on Equity") or "N/A","ScanX"),
        ("Debt / Equity",s.get("Debt to Equity") or "N/A","ScanX"),
    ]
    _kpis(items,T,4)
    a,b=st.columns([3.2,1.25])
    with a:
        _header("Price / 50 DMA / 200 DMA",T,"live")
        _chart(data["symbol"],T,st.session_state.get("research_chart_period","1y"))
    with b:
        _header("Key fundamentals",T,"live")
        rows=[]
        for k in ["Market Cap","EPS","PE Ratio","PB Ratio","Book Value","EBITDA","Dividend Yield","Return on Equity","Debt to Equity"]:
            rows.append({"label":k,"values":[s.get(k,"N/A")]})
        _table(["VALUE"],rows,T,["Market Cap","PE Ratio","Return"])
    _header("Growth / quality snapshot",T)
    # ScanX automatic screener metrics are represented in the public page as text.
    about=(data.get("about") or {}).get("description")
    if about:
        _panel("Business context",f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.8">{_esc(about)}</div>',T)
    else:
        _unavailable("Business context","ScanX About section",T)
    peers=_scanx_df(data,"peers")
    if peers is not None and not peers.empty:
        _header("Peer snapshot",T,"live")
        p,r=_table_to_period_rows(peers,10,8); _table(p,r,T)


def _financials(data,T):
    df=_scanx_df(data,"financials")
    _header("Quarterly financial results · ScanX",T,"live" if df is not None and not df.empty else "unavailable","Consolidated/standalone selector remains a source-side control; public page data is shown as returned.")
    p,r=_table_to_period_rows(df,60,14); _table(p,r,T,["Revenue","EBITDA","Net Profit","EPS"])
    if df is None or df.empty:
        return
    # A compact recent-period view prevents the terminal from becoming an unreadable wall.
    recent=df.iloc[:,max(0,len(df.columns)-9):].copy()
    _header("Recent periods",T,"live")
    p,r=_table_to_period_rows(recent,30,9); _table(p,r,T,["Revenue","EBITDA","Net Profit","EPS"])


def _balance(data,T):
    df=_scanx_df(data,"balance_sheet")
    _header("Balance sheet · ScanX",T,"live" if df is not None and not df.empty else "unavailable")
    p,r=_table_to_period_rows(df,60,14); _table(p,r,T,["Total Assets","Total Equity","Current Assets","Current Liabilities"])


def _cashflow(data,T):
    df=_scanx_df(data,"cash_flow")
    _header("Cash flow · ScanX",T,"live" if df is not None and not df.empty else "unavailable")
    p,r=_table_to_period_rows(df,30,14); _table(p,r,T,["Operating Activities","Investing Activities","Financing Activities","Net Cash Flow"])


def _shareholding(data,T):
    df=_scanx_df(data,"shareholding")
    _header("Shareholding history · ScanX",T,"live" if df is not None and not df.empty else "unavailable")
    p,r=_table_to_period_rows(df,20,26); _table(p,r,T,["Promoter","FIIs","DIIs","Public"])
    if df is None or df.empty: return
    # Plot the ownership lines from the same ScanX rows.
    import plotly.graph_objects as go
    x=df.iloc[:,0].astype(str)
    fig=go.Figure()
    for name in ["Promoter","FIIs","DIIs","Government","Public / Retail","Others"]:
        row=df[df.iloc[:,0].astype(str).str.lower().eq(name.lower())]
        if row.empty: continue
        vals=pd.to_numeric(row.iloc[0,1:].astype(str).str.replace("%","",regex=False).str.replace(",","",regex=False),errors="coerce")
        fig.add_trace(go.Scatter(x=x.iloc[:len(vals)],y=vals,mode="lines",name=name))
    fig.update_layout(height=360,margin=dict(l=0,r=20,t=10,b=0),paper_bgcolor=T["bg"],plot_bgcolor=T["bg"],font=dict(color=T["t2"],family="JetBrains Mono, Consolas, monospace",size=9),legend=dict(orientation="h",y=1.05,x=0))
    fig.update_xaxes(showgrid=True,gridcolor="#181818")
    fig.update_yaxes(showgrid=True,gridcolor="#181818",ticksuffix="%",side="right")
    st.plotly_chart(fig,use_container_width=True,config={"displayModeBar":False},key=f"scanx_ownership_{data['symbol']}")


def _mf(data,T):
    df=_scanx_df(data,"mf_holdings")
    _header("Mutual fund holdings",T,"live" if df is not None and not df.empty else "unavailable","Primary table from the public ScanX company page. Current holding, 1M/3M changes and six-month trend are retained where exposed.")
    p,r=_table_to_period_rows(df,80,8); _table(p,r,T,["0.00%"])
    if df is None or df.empty: return
    st.caption("Use the ScanX fund name links for deeper scheme-level history. Arka does not fabricate a six-month sparkline when the source does not expose the series in HTML.")


def _peers(data,T):
    df=_scanx_df(data,"peers")
    _header("Peer comparison",T,"live" if df is not None and not df.empty else "unavailable")
    p,r=_table_to_period_rows(df,40,10); _table(p,r,T,[data.get("name","")])
    if df is None or df.empty:
        _unavailable("Peer set","ScanX comparable-company table",T)


def _dividend(data,T):
    df=_scanx_df(data,"dividend")
    _header("Dividend history",T,"live" if df is not None and not df.empty else "unavailable")
    p,r=_table_to_period_rows(df,20,12); _table(p,r,T,["Dividend Per Share","Dividend Yield"])


def _actions(data,T):
    df=_scanx_df(data,"corporate_actions")
    _header("Corporate actions",T,"live" if df is not None and not df.empty else "unavailable")
    p,r=_table_to_period_rows(df,80,8); _table(p,r,T)


def _announcements(data,T):
    anns=data.get("announcements") or []
    _header("Exchange announcements",T,"live" if anns else "unavailable","Links are carried from the public ScanX page to the originating exchange document where ScanX exposes it.")
    if not anns:
        _unavailable("Announcements","Public ScanX announcement section",T)
        return
    for a in anns[:60]:
        title=_esc(a.get("title"))
        url=html.escape(str(a.get("url","")),quote=True)
        st.markdown(f'<div style="padding:8px 0;border-bottom:1px solid {T["border"]};font:10px {T["mono"]}"><a href="{url}" target="_blank" style="color:{T["ivory"]};text-decoration:none">{title}</a><div style="color:{T["t3"]};margin-top:3px">OPEN SOURCE DOCUMENT ↗</div></div>',unsafe_allow_html=True)


def _news(data,T,news_fetch_fn=None):
    symbol=data["symbol"]
    news=[]
    try: news=get_security_news(symbol,days=7) or []
    except Exception: news=[]
    if not news and news_fetch_fn:
        try: news=news_fetch_fn(symbol,days=7) or []
        except TypeError:
            try: news=news_fetch_fn(symbol) or []
            except Exception: news=[]
        except Exception: news=[]
    if not news:
        _unavailable("Security news","Arka News Intelligence / connected news provider",T)
        return
    _header("Security news timeline",T,"live")
    for item in news[:30]:
        title=item.get("title") or item.get("headline") or "Untitled"
        source=item.get("source") or item.get("publisher") or "Source"
        ts=item.get("time_str") or item.get("published") or item.get("published_at") or item.get("time") or ""
        sentiment=item.get("sentiment_label") or item.get("sentiment") or "—"
        url=item.get("url") or item.get("link")
        link=f'<a href="{html.escape(str(url),quote=True)}" target="_blank" style="color:{T["ivory"]};text-decoration:none">{_esc(title)}</a>' if url else _esc(title)
        st.markdown(f'<div style="padding:8px 0;border-bottom:1px solid {T["border"]};font:10px {T["mono"]}"><div>{link}</div><div style="color:{T["t3"]};margin-top:3px">{_esc(source)} · {_esc(ts)} · {_esc(sentiment)}</div></div>',unsafe_allow_html=True)


def _technical(data,T):
    tech=data.get("technical") or []
    _header("Technical indicators · ScanX",T,"live" if tech else "unavailable")
    if tech:
        rows=[{"label":x["indicator"],"values":[x.get("value","—"),x.get("state","—")]} for x in tech]
        _table(["VALUE","STATE"],rows,T)
    else:
        _unavailable("Technical indicator set","ScanX public Technical Indicators section",T)
    _header("Arka price/volume context",T,"live")
    period=st.selectbox("Chart range",["1mo","3mo","6mo","1y","2y","5y"],index=3,key="research_scanx_technical_range")
    h=_chart(data["symbol"],T,period)
    if h is not None and not h.empty:
        close=h["Close"].dropna(); vol=h["Volume"].fillna(0)
        rsi=None
        if len(close)>=15:
            d=close.diff(); g=d.clip(lower=0).rolling(14).mean(); l=(-d.clip(upper=0)).rolling(14).mean(); rs=g/l.replace(0,float("nan")); rsi=float((100-100/(1+rs)).iloc[-1])
        _kpis([("Close",f"₹{close.iloc[-1]:,.2f}","Yahoo fallback/market history"),("SMA20",f"₹{close.rolling(20).mean().iloc[-1]:,.2f}",""),("SMA50",f"₹{close.rolling(50).mean().iloc[-1]:,.2f}",""),("RSI14",f"{rsi:.2f}" if rsi is not None else "N/A","")],T,4)


def _forecast(data,T):
    df=_scanx_df(data,"analyst")
    _header("Analyst rating / forecast",T,"live" if df is not None and not df.empty else "unavailable","ScanX public page exposes a provider rating block. Arka displays the source observations without turning them into a recommendation.")
    if df is not None and not df.empty:
        p,r=_table_to_period_rows(df,20,8); _table(p,r,T)
    else:
        targets=_yf_targets(data["symbol"])
        if targets:
            _table(["VALUE"],[{"label":str(k),"values":[str(v)]} for k,v in targets.items()],T)
        else:
            _unavailable("Forecast dataset","ScanX forecast block or provider analyst targets",T)


def _valuation(data,T):
    s=data.get("snapshot") or {}
    _header("Valuation monitor",T,"live")
    _kpis([("P/E",s.get("PE Ratio","N/A"),"ScanX"),("P/B",s.get("PB Ratio","N/A"),"ScanX"),("EPS",s.get("EPS","N/A"),"ScanX"),("Book Value",s.get("Book Value","N/A"),"ScanX")],T,4)
    _panel("Relative valuation",'<div style="font:10px '+T["mono"]+';color:'+T["t2"]+';line-height:1.7">Peer valuation is available in the PEERS function. A conventional DCF is intentionally not presented as a sourced ScanX field; any DCF added later should be an explicit scenario model with editable assumptions.</div>',T)


def _risk(data,T):
    s=data.get("snapshot") or {}
    rows=[
        {"label":"Debt to Equity","values":[s.get("Debt to Equity","N/A"),"ScanX"]},
        {"label":"Return on Equity","values":[s.get("Return on Equity","N/A"),"ScanX"]},
        {"label":"P/E","values":[s.get("PE Ratio","N/A"),"ScanX"]},
        {"label":"52W High-Low","values":[s.get("52W High-Low","N/A"),"ScanX"]},
    ]
    _header("Risk monitor",T,"live"); _table(["VALUE","SOURCE"],rows,T)
    for title,req in [("Business concentration","Segment-level concentration disclosure"),("Regulatory risk","Exchange/regulatory event dataset"),("Commodity exposure","Company commodity-sensitivity disclosures"),("Liquidity / impact","Turnover/free-float impact dataset")]:
        _unavailable(title,req,T)


def _what_changed(data,T):
    df=_scanx_df(data,"financials")
    if df is None or df.empty:
        _unavailable("Change engine","At least two dated ScanX financial periods",T); return
    # Find the two latest columns and calculate changes for numeric rows.
    latest=df.columns[-1]; prev=df.columns[-2]
    rows=[]
    for _,r in df.iterrows():
        label=str(r.iloc[0])
        a=pd.to_numeric(str(r.iloc[-1]).replace(",",""),errors="coerce")
        b=pd.to_numeric(str(r.iloc[-2]).replace(",",""),errors="coerce")
        if pd.notna(a) and pd.notna(b) and b!=0:
            rows.append({"label":label,"values":[str(r.iloc[-1]),str(r.iloc[-2]),f"{(a/abs(b)-1)*100:+.2f}%"]})
    _header("What changed?",T,"live",f"Adjacent ScanX observations: {latest} vs {prev}")
    _table([str(latest),str(prev),"CHANGE"],rows,T,["Revenue","EBITDA","Net Profit","EPS"])


def _thesis(data,T):
    symbol=data["symbol"]
    st.markdown(f'<div style="font:10px {T["mono"]};color:{T["t2"]};margin:6px 0">ARKA RESEARCH NOTEBOOK · {_esc(symbol)}</div>',unsafe_allow_html=True)
    fields=[("Bull case","research_bull_scanx"),("Bear case","research_bear_scanx"),("Catalysts","research_catalysts_scanx"),("Risks","research_risks_scanx")]
    for title,key in fields: st.text_area(title,value=st.session_state.get(key,""),height=105,key=key)
    if st.button("SAVE RESEARCH NOTES",key="research_notes_save_scanx",type="primary"): st.success("Research notes saved for this session.")
    _panel("Source discipline",'<div style="font:10px '+T["mono"]+';color:'+T["t2"]+';line-height:1.7">Use dated source observations in each note. The notebook is a workspace, not an investment recommendation engine.</div>',T)


def _about(data,T):
    desc=(data.get("about") or {}).get("description")
    _header("About company",T,"live" if desc else "unavailable")
    if desc:
        _panel("ScanX company description",f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.8">{_esc(desc)}</div>',T)
    else:
        _unavailable("Company description","ScanX About section",T)
    s=data.get("snapshot") or {}
    rows=[
        {"label":"Website","values":[s.get("website") or "See source page"]},
        {"label":"ScanX source","values":[data.get("url","N/A")]},
    ]
    _table(["VALUE"],rows,T)


def _sources(data,T):
    rows=[
        {"label":"ScanX public company page","values":[data.get("url","N/A"),data.get("status","unavailable")]},
        {"label":"Price / OHLCV","values":["Yahoo Finance / yfinance","fallback"]},
        {"label":"Arka News","values":["news_feed.py","security timeline"]},
        {"label":"Screener","values":["security resolution / fallback","connected if available"]},
    ]
    _header("Data provenance",T)
    _table(["SOURCE","STATUS"],rows,T)
    _panel("Integrity rule",'<div style="font:10px '+T["mono"]+';color:'+T["t2"]+';line-height:1.8">ScanX values are displayed only when the public page returns them. Arka does not manufacture missing MF, ownership, filings, analyst or segment data.</div>',T)
    _panel("Source note",'<div style="font:10px '+T["mono"]+';color:'+T["t3"]+';line-height:1.8">This adapter reads the public company HTML. It does not log in or bypass private APIs. If ScanX changes its page structure or blocks automated requests, the connector will show DATA UNAVAILABLE rather than silently substituting invented values.</div>',T)


def render_research_page(T=None, news_fetch_fn=None):
    T=_theme(T)
    st.markdown(f'''<style>
    [data-testid="stAppViewContainer"]{{background:{T["bg"]}}}
    .arka-research-tabs div[role="radiogroup"]{{gap:2px;overflow-x:auto;flex-wrap:nowrap;padding-bottom:5px}}
    .arka-research-tabs label{{background:{T["panel"]};border:1px solid {T["border"]};padding:7px 10px!important;font:700 9px {T["mono"]}!important;color:{T["t2"]}!important;white-space:nowrap}}
    .arka-research-tabs label[data-checked="true"]{{border-color:{T["amber"]};color:{T["amber"]}!important;background:{T["panel2"]}}}
    div[data-testid="stTextInput"] input{{font-family:{T["mono"]};background:{T["panel"]};color:{T["ivory"]};border:1px solid {T["border"]}}}
    div[data-testid="stSelectbox"] div[data-baseweb="select"] > div{{background:{T["panel"]};border-color:{T["border"]};font-family:{T["mono"]}}}
    </style>''',unsafe_allow_html=True)

    now=datetime.now(IST)
    opened=now.weekday()<5 and now.replace(hour=9,minute=15,second=0,microsecond=0)<=now<=now.replace(hour=15,minute=30,second=0,microsecond=0)
    color=T["green"] if opened else T["red"]
    label="MARKET OPEN" if opened else "MARKET CLOSED"
    st.markdown(f'<div style="display:flex;justify-content:space-between;border:1px solid {T["border"]};border-bottom:2px solid {T["amber"]};background:{T["panel"]};padding:7px 12px;margin-bottom:10px;font:10px {T["mono"]}"><span style="color:{T["amber"]};font-weight:800;letter-spacing:1px">ARKA TERMINAL · RESEARCH · SCANX DATA</span><span style="color:{color};font-weight:700">● {label}</span><span style="color:{T["t3"]}">{now.strftime("%d %b %Y · %H:%M:%S IST")}</span></div>',unsafe_allow_html=True)

    active=st.session_state.get("active_security") or st.session_state.get("research_last_query") or ""
    query=st.text_input("Research security",value=active,placeholder="RELIANCE / HDFCBANK / TCS",label_visibility="collapsed",key="research_scanx_query")
    c1,c2=st.columns([5,1])
    with c1:
        st.caption("ScanX is the primary research source. Detailed sections are rendered only after a security is loaded.")
    with c2:
        if st.button("LOAD SECURITY",key="research_scanx_load",use_container_width=True,type="primary"):
            q=query.strip().upper()
            if q:
                st.session_state.active_security=q
                st.session_state.research_last_query=q
                st.session_state.pop("research_scanx_data",None)
                st.rerun()

    symbol=st.session_state.get("active_security") or st.session_state.get("research_last_query") or ""
    if not symbol:
        _panel("Research ready",'<div style="font:11px '+T["mono"]+';color:'+T["t3"]+'">Load a security to initialize the ScanX research workspace.</div>',T,dashed=True)
        return

    cached=st.session_state.get("research_scanx_data")
    if not cached or cached.get("symbol")!=symbol:
        core,scanx=_load_data(symbol)
        if scanx.get("status")!="live":
            # One retry with the core company name when the first slug failed.
            scanx=fetch_scanx_company(symbol,company_name=core.get("name") or symbol)
        cached={**scanx,"core":core,"symbol":symbol}
        st.session_state.research_scanx_data=cached
    data=cached

    _scanx_header(data,T)
    periods=["3mo","6mo","1y","2y","5y"]
    if "research_chart_period" not in st.session_state: st.session_state.research_chart_period="1y"
    st.selectbox("Chart period",periods,index=periods.index(st.session_state.research_chart_period),key="research_chart_period")

    functions=[
        "OVERVIEW","FORECAST","NEWS","PEERS","FINANCIALS","BALANCE SHEET","CASH FLOWS",
        "SHAREHOLDING","DIVIDEND","CORPORATE ACTIONS","ANNOUNCEMENTS","MF HOLDINGS","TECHNICALS",
        "VALUATION","RISK","WHAT CHANGED","THESIS","ABOUT","DATA / SOURCES"
    ]
    active_fn=st.radio("RESEARCH FUNCTION",functions,index=0,horizontal=True,label_visibility="collapsed",key="research_scanx_function")
    st.markdown('<div class="arka-research-tabs">',unsafe_allow_html=True)
    st.markdown('</div>',unsafe_allow_html=True)

    dispatch={
        "OVERVIEW":lambda:_overview(data,T),"FORECAST":lambda:_forecast(data,T),"NEWS":lambda:_news(data,T,news_fetch_fn),
        "PEERS":lambda:_peers(data,T),"FINANCIALS":lambda:_financials(data,T),"BALANCE SHEET":lambda:_balance(data,T),
        "CASH FLOWS":lambda:_cashflow(data,T),"SHAREHOLDING":lambda:_shareholding(data,T),"DIVIDEND":lambda:_dividend(data,T),
        "CORPORATE ACTIONS":lambda:_actions(data,T),"ANNOUNCEMENTS":lambda:_announcements(data,T),"MF HOLDINGS":lambda:_mf(data,T),
        "TECHNICALS":lambda:_technical(data,T),"VALUATION":lambda:_valuation(data,T),"RISK":lambda:_risk(data,T),
        "WHAT CHANGED":lambda:_what_changed(data,T),"THESIS":lambda:_thesis(data,T),"ABOUT":lambda:_about(data,T),"DATA / SOURCES":lambda:_sources(data,T),
    }
    dispatch[active_fn]()

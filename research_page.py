"""Arka Trades — Deep Research Terminal.

The page deliberately exposes the COMPLETE research architecture even when the
current provider cannot supply a dataset. Missing/unsupported datasets are
shown as N/A with the connector/source requirement; no values are fabricated.
"""
import re
import html
from datetime import datetime, timezone, timedelta

import streamlit as st
import pandas as pd
import yfinance as yf

try:
    from streamlit_option_menu import option_menu
except Exception:
    option_menu = None

from screener_scraper import (
    get_full_research, get_factors, get_earnings_date,
    get_leverage_ratios, get_peer_comparison, get_summary, resolve_symbol,
    get_hot_ticker_quotes,
)

IST = timezone(timedelta(hours=5, minutes=30))
_HOT_TICKERS = ["RELIANCE", "HDFCBANK", "TCS", "INFY", "ICICIBANK", "TATAMOTORS", "SBIN"]

_DEFAULT_T = {
    "bg": "#000000", "panel": "#0A0A0A", "panel2": "#111111", "row_alt": "#0D0D0D",
    "border": "#262626", "amber": "#FFB000", "ivory": "#E8E6E0", "t2": "#A8A8A0",
    "t3": "#6B6B65", "green": "#00C853", "red": "#FF3B30",
    "mono": "'IBM Plex Mono', 'Consolas', monospace",
}


def _theme(T):
    x = dict(_DEFAULT_T)
    if T: x.update(T)
    return x


def _num(v):
    if v is None: return None
    s = str(v).replace(",", "").replace("₹", "").replace("%", "").replace("Cr", "").replace("x", "").strip()
    if s.lower() in {"", "—", "-", "nan", "none", "n/a"}: return None
    try: return float(s)
    except Exception: return None


def _row(section, names):
    d = (section or {}).get("data") or {}
    for r in d.get("rows", []):
        label = str(r.get("label", "")).lower()
        if any(n.lower() in label for n in names): return r
    return None


def _values(r):
    if not r: return []
    return [_num(x) for x in r.get("values", [])]


def _latest2(r):
    vals = [x for x in _values(r) if x is not None]
    if not vals: return None, None
    return vals[-1], vals[-2] if len(vals) > 1 else None


def _latest(r):
    a, _ = _latest2(r)
    return a


def _pct_change(a, b):
    if a is None or b in (None, 0): return None
    return (a / abs(b) - 1) * 100


def _esc(x):
    return html.escape(str(x))


def _age(seconds):
    if seconds is None: return ""
    if seconds < 3600: return f"{int(seconds // 60)}m ago"
    if seconds < 172800: return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def _tag(status, T, age_seconds=None):
    status = status or "unavailable"
    if status == "live": text, color = "● LIVE", T["green"]
    elif status == "stale": text, color = f"◐ CACHED · {_age(age_seconds)}", T["amber"]
    elif status == "partial": text, color = "◐ PARTIAL", T["amber"]
    else: text, color = "✕ N/A", T["t3"]
    return f'<span style="color:{color};font:700 9px {T["mono"]};letter-spacing:.8px">{text}</span>'


def _header(title, T, status=None, note=None):
    right = _tag(status, T) if status else ""
    st.markdown(
        f'<div style="display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid {T["border"]};padding:8px 0 6px;margin:13px 0 8px;">'
        f'<span style="font:700 11px {T["mono"]};letter-spacing:1px;color:{T["amber"]}">{_esc(title).upper()}</span>{right}</div>',
        unsafe_allow_html=True,
    )
    if note:
        st.markdown(f'<div style="font:9px {T["mono"]};color:{T["t3"]};margin:3px 0 8px">{_esc(note)}</div>', unsafe_allow_html=True)


def _panel(title, body, T, dashed=False):
    border = "1px dashed " + T["border"] if dashed else "1px solid " + T["border"]
    st.markdown(
        f'<div style="border:{border};background:{T["panel"]};padding:11px 13px;margin:7px 0;">'
        f'<div style="font:700 10px {T["mono"]};color:{T["ivory"]};margin-bottom:7px">{_esc(title).upper()}</div>{body}</div>',
        unsafe_allow_html=True,
    )


def _kpis(items, T, cols=4):
    c = st.columns(cols)
    for i, (label, value, sub) in enumerate(items):
        with c[i % cols]:
            st.markdown(
                f'<div style="border:1px solid {T["border"]};background:{T["panel"]};padding:9px 11px;min-height:60px;margin-bottom:7px">'
                f'<div style="font:9px {T["mono"]};color:{T["t3"]};letter-spacing:.6px">{_esc(label.upper())}</div>'
                f'<div style="font:700 15px {T["mono"]};color:{T["ivory"]};margin-top:4px">{_esc(value)}</div>'
                f'<div style="font:9px {T["mono"]};color:{T["t3"]};margin-top:3px">{_esc(sub or "")}</div></div>',
                unsafe_allow_html=True,
            )


def _table(periods, rows, T, highlights=()):
    if not rows:
        _panel("Data unavailable", '<div style="font:10px '+T["mono"]+';color:'+T["t3"]+'">No rows were returned by the current provider.</div>', T, dashed=True)
        return
    heads = ''.join(f'<th style="text-align:right;padding:6px 9px;color:{T["t3"]};font:700 9px {T["mono"]};white-space:nowrap">{_esc(p)}</th>' for p in periods)
    body = []
    for i, r in enumerate(rows):
        label = str(r.get("label", "")); hl = any(x.lower() in label.lower() for x in highlights)
        vals = ''.join(f'<td style="text-align:right;padding:6px 9px;color:{T["ivory"] if hl else T["t2"]};font:{"700" if hl else "500"} 10px {T["mono"]};white-space:nowrap">{_esc(v)}</td>' for v in r.get("values", []))
        body.append(f'<tr style="background:{T["row_alt"] if i%2 else "transparent"};border-bottom:1px solid {T["border"]}"><td style="padding:6px 9px;color:{T["amber"] if hl else T["t2"]};font:{"700" if hl else "500"} 10px {T["mono"]};white-space:nowrap">{_esc(label)}</td>{vals}</tr>')
    st.markdown(
        f'<div style="overflow-x:auto;border:1px solid {T["border"]};background:{T["panel"]}"><table style="width:100%;border-collapse:collapse"><thead><tr style="background:{T["panel2"]};border-bottom:1px solid {T["border"]}"><th style="text-align:left;padding:6px 9px;color:{T["t3"]};font:700 9px {T["mono"]}">METRIC</th>{heads}</tr></thead><tbody>{"".join(body)}</tbody></table></div>',
        unsafe_allow_html=True,
    )


def _unavailable(title, requirement, T, detail=None):
    detail = detail or "The feature remains visible so the Research architecture does not depend on today's data coverage."
    body = f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.7"><b style="color:{T["t3"]}">STATUS:</b> DATA UNAVAILABLE<br><b style="color:{T["t3"]}">REQUIRED:</b> {_esc(requirement)}<br>{_esc(detail)}</div>'
    _panel(title, body, T, dashed=True)


def _deep(data):
    q, y, bs, cf = data.get("quarterly") or {}, data.get("yearly") or {}, data.get("balance_sheet") or {}, data.get("cash_flow") or {}
    s = (data.get("summary") or {}).get("data") or {}
    sales = _latest(_row(q, ["sales", "revenue"])); op = _latest(_row(q, ["operating profit"])); pat = _latest(_row(q, ["net profit", "profit for the period"]))
    cfo = _latest(_row(cf, ["cash from operating activity", "cash from operating activities"])); capex = _latest(_row(cf, ["capital expenditure"]))
    # Some providers report investing cash flow rather than a capex line. Do not assume its sign is capex.
    fcf = cfo - capex if cfo is not None and capex is not None else None
    lev = get_leverage_ratios(data.get("symbol", ""), full_research=data)
    pe = _num(s.get("pe_ratio")); px = _num(s.get("current_price")); bv = _num(s.get("book_value"))
    pb = px / bv if px is not None and bv not in (None, 0) else None
    return {
        "sales": sales, "op": op, "pat": pat, "cfo": cfo, "capex": capex, "fcf": fcf,
        "op_margin": op / sales * 100 if op is not None and sales not in (None, 0) else None,
        "net_margin": pat / sales * 100 if pat is not None and sales not in (None, 0) else None,
        "ocf_pat": cfo / pat if cfo is not None and pat not in (None, 0) else None,
        "fcf_pat": fcf / pat if fcf is not None and pat not in (None, 0) else None,
        "de": lev.get("debt_to_equity"), "ic": lev.get("interest_coverage"), "pe": pe, "pb": pb,
        "earnings_yield": 100 / pe if pe not in (None, 0) else None,
        "div_yield": _num(s.get("dividend_yield")), "roe": _num(s.get("roe")), "roce": _num(s.get("roce")),
    }


def _status_strip(T):
    now = datetime.now(IST); wd = now.weekday(); open_t = now.replace(hour=9, minute=15, second=0, microsecond=0); close_t = now.replace(hour=15, minute=30, second=0, microsecond=0)
    opened = wd < 5 and open_t <= now <= close_t
    color = T["green"] if opened else T["red"]
    label = "MARKET OPEN" if opened else "MARKET CLOSED"
    st.markdown(f'<div style="display:flex;justify-content:space-between;border:1px solid {T["border"]};border-bottom:2px solid {T["amber"]};background:{T["panel"]};padding:7px 12px;margin-bottom:12px;font:10px {T["mono"]}"><span style="color:{T["amber"]};font-weight:800;letter-spacing:1px">ARKA TERMINAL · RESEARCH</span><span style="color:{color};font-weight:700">● {label}</span><span style="color:{T["t3"]}">{now.strftime("%d %b %Y · %H:%M:%S IST")}</span></div>', unsafe_allow_html=True)


def _blotter(T):
    try:
        quotes = get_hot_ticker_quotes(_HOT_TICKERS)
    except Exception:
        quotes = {}
    cells = []
    for sym in _HOT_TICKERS:
        q = quotes.get(sym, {}); price = q.get("price", "—"); pct = q.get("pct_change")
        pc = T["green"] if pct is not None and pct >= 0 else T["red"] if pct is not None else T["t3"]
        delta = f'{pct:+.2f}%' if pct is not None else '—'
        cells.append(f'<div style="min-width:120px;padding:6px 10px;border-right:1px solid {T["border"]};font:9px {T["mono"]}"><span style="color:{T["t3"]}">{sym}</span><br><span style="color:{T["ivory"]};font-weight:700">₹{_esc(price)}</span> <span style="color:{pc}">{delta}</span></div>')
    st.markdown(f'<div style="display:flex;overflow-x:auto;border:1px solid {T["border"]};background:{T["panel"]};margin-bottom:12px">{"".join(cells)}</div>', unsafe_allow_html=True)


def _chart(symbol, T, period="6mo"):
    try:
        import yfinance as yf
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
        h = yf.Ticker(symbol.upper().strip()+".NS").history(period=period, interval="1d")
        if h is None or h.empty: raise RuntimeError("No price history")
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0, row_heights=[.8,.2])
        fig.add_trace(go.Candlestick(x=h.index, open=h.Open, high=h.High, low=h.Low, close=h.Close, increasing_line_color=T["green"], decreasing_line_color=T["red"], increasing_fillcolor=T["green"], decreasing_fillcolor=T["red"], name="Price"), row=1,col=1)
        fig.add_trace(go.Bar(x=h.index,y=h.Volume,name="Volume", marker_color=[T["green"] if c>=o else T["red"] for c,o in zip(h.Close,h.Open)]),row=2,col=1)
        fig.update_layout(height=440,margin=dict(l=0,r=45,t=5,b=0),paper_bgcolor=T["bg"],plot_bgcolor=T["bg"],font=dict(color=T["t2"],size=10,family="IBM Plex Mono, Consolas, monospace"),showlegend=False,xaxis_rangeslider_visible=False)
        fig.update_yaxes(showgrid=True,gridcolor="#1A1A1A",side="right",row=1,col=1); fig.update_yaxes(showgrid=False,side="right",showticklabels=False,row=2,col=1)
        st.plotly_chart(fig,use_container_width=True,config={"displayModeBar":False})
        return h
    except Exception as e:
        _unavailable("Price chart", "Yahoo Finance/NSE price history", T, str(e))
        return None



def _company(data,T):
    s=(data.get("summary") or {}).get("data") or {}
    sec=(data.get("sector") or {}).get("data") or {}
    items=[
        ("Company",data.get("name") or "N/A"),
        ("Symbol",data.get("symbol") or "N/A"),
        ("Resolved",data.get("resolved") and "YES" or "NO"),
        ("Sector",sec.get("Sector") or sec.get("Broad Sector") or "N/A"),
        ("Industry",sec.get("Industry") or sec.get("Broad Industry") or "N/A"),
        ("Market Cap",s.get("market_cap","N/A")),
        ("Face Value",s.get("face_value","N/A")),
        ("Book Value",s.get("book_value","N/A")),
        ("Dividend Yield",s.get("dividend_yield","N/A")),
    ]
    _header("Company profile",T)
    rows=[{"label":k,"values":[str(v)]} for k,v in items]
    _table(["VALUE"],rows,T)
    _header("Business identity",T)
    desc=f'{data.get("name") or data.get("symbol")} is classified under {sec.get("Sector") or sec.get("Broad Sector") or "the reported sector"} / {sec.get("Industry") or sec.get("Broad Industry") or "the reported industry"}. Detailed business description is shown only where the connected source discloses it.'
    _panel("Description",f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.8">{_esc(desc)}</div>',T)
    _unavailable("Management / employee profile", "Company filing or verified corporate profile source", T)


def _business(data,T):
    sec=(data.get("sector") or {}).get("data") or {}
    _header("Business model",T)
    _panel("Reported classification",f'<div style="font:10px {T["mono"]};color:{T["ivory"]}">Sector: {_esc(sec.get("Sector") or sec.get("Broad Sector") or "N/A")} · Industry: {_esc(sec.get("Industry") or sec.get("Broad Industry") or "N/A")}</div>',T)
    q=data.get("quarterly") or {}; y=data.get("yearly") or {}
    for title, section in [("Quarterly operating history",q),("Annual operating history",y)]:
        rows=[]
        for label,names in [("Revenue",["sales","revenue"]),("Operating Profit",["operating profit"]),("Net Profit",["net profit","profit for the period"]),("EPS",["eps"])]:
            r=_row(section,names)
            if r: rows.append(r)
        if rows:
            _header(title,T); _table(section.get("periods",[]),rows,T)
    for title,req in [
        ("Products / services", "Company annual report / investor presentation"),
        ("Customer concentration", "Company filing with customer concentration disclosure"),
        ("Geographic exposure", "Annual report geographic revenue tables"),
        ("Capacity / utilisation", "Company operational disclosure"),
        ("Order book", "Company order-book disclosure, where applicable"),
        ("Competitive landscape", "Industry/company research dataset"),
    ]: _unavailable(title,req,T)


def _filings(data,T):
    _header("Corporate filings",T)
    _unavailable("Exchange announcements", "NSE/BSE corporate announcement feed", T, "Architecture is ready; the current free stack does not expose a normalized filing history for every security.")
    _unavailable("Financial results filings", "NSE/BSE results filing feed", T)
    _unavailable("Annual reports", "Company/NSE/BSE document repository", T)
    _unavailable("Investor presentations", "Company investor-relations document feed", T)
    _unavailable("Credit ratings", "Exchange/company credit-rating disclosure feed", T)
    _unavailable("Insider disclosures", "Exchange insider-trading disclosure feed", T)


def _technical(data,T):
    symbol=data["symbol"]
    period=st.selectbox("Technical range",["1mo","3mo","6mo","1y","2y"],index=2,key="research_technical_range")
    _header("Price structure",T)
    try:
        h=yf.Ticker(symbol+".NS").history(period=period,interval="1d",auto_adjust=False)
    except Exception:
        h=pd.DataFrame()
    if h.empty:
        _unavailable("OHLCV history", "Yahoo Finance / exchange market-data feed", T); return
    close=h["Close"].dropna(); vol=h["Volume"].fillna(0)
    def last(series): return float(series.iloc[-1]) if len(series) else None
    def pct(a,b): return ((a/b)-1)*100 if a is not None and b not in (None,0) else None
    ma20=close.rolling(20).mean(); ma50=close.rolling(50).mean(); ma200=close.rolling(200).mean()
    rsi=_rsi_series(close,14).iloc[-1] if len(close)>=15 else None
    atr=_atr_series(h,14).iloc[-1] if len(h)>=15 else None
    avg20=vol.rolling(20).mean().iloc[-1] if len(vol)>=20 else None
    relvol=(vol.iloc[-1]/avg20) if avg20 else None
    items=[("Close",last(close),"₹"),("1D",pct(last(close),float(close.iloc[-2])) if len(close)>1 else None,"%"),("SMA20",last(ma20),"₹"),("SMA50",last(ma50),"₹"),("SMA200",last(ma200),"₹"),("RSI14",rsi,""),("ATR14",atr,"₹"),("Relative Volume",relvol,"x")]
    _kpis([(a,"N/A" if b is None else f"{b:,.2f}",c) for a,b,c in items],T,4)
    _chart(symbol,T,period)
    rows=[]
    for name,val in [("PDH",float(h["High"].iloc[-2]) if len(h)>1 else None),("PDL",float(h["Low"].iloc[-2]) if len(h)>1 else None),("52W High",float(h["High"].tail(252).max()) if len(h) else None),("52W Low",float(h["Low"].tail(252).min()) if len(h) else None)]:
        rows.append({"label":name,"values":[f"₹{val:,.2f}" if val is not None else "N/A"]})
    _table(["VALUE"],rows,T)
    _unavailable("Advanced indicators", "Additional indicator library / explicit formula configuration", T, "The terminal currently calculates core trend, RSI, ATR and relative-volume observations.")


def _rsi_series(close,period=14):
    d=close.diff(); g=d.clip(lower=0).rolling(period).mean(); l=(-d.clip(upper=0)).rolling(period).mean(); rs=g/l.replace(0,float("nan")); return 100-100/(1+rs)


def _atr_series(h,period=14):
    prev=h["Close"].shift(1); tr=pd.concat([(h["High"]-h["Low"]),(h["High"]-prev).abs(),(h["Low"]-prev).abs()],axis=1).max(axis=1); return tr.rolling(period).mean()


def _relative_strength(data,T):
    symbol=data["symbol"]
    period=st.selectbox("Relative-strength window",["1mo","3mo","6mo","1y","2y"],index=2,key="research_rs_range")
    benchmarks={"NIFTY 50":"^NSEI","NIFTY 500":"^CRSLDX","S&P 500":"^GSPC"}
    try: stock=yf.Ticker(symbol+".NS").history(period=period,interval="1d")["Close"].dropna()
    except Exception: stock=pd.Series(dtype=float)
    if stock.empty:
        _unavailable("Relative strength", "Market-data history for stock and benchmark", T); return
    rows=[]
    stock_ret=(stock.iloc[-1]/stock.iloc[0]-1)*100 if len(stock)>1 else None
    rows.append({"label":symbol,"values":[f"{stock_ret:+.2f}%" if stock_ret is not None else "N/A","BASE"]})
    for label,ticker in benchmarks.items():
        try: b=yf.Ticker(ticker).history(period=period,interval="1d")["Close"].dropna(); r=(b.iloc[-1]/b.iloc[0]-1)*100 if len(b)>1 else None
        except Exception: r=None
        excess=(stock_ret-r) if stock_ret is not None and r is not None else None
        rows.append({"label":label,"values":[f"{r:+.2f}%" if r is not None else "N/A",f"{excess:+.2f}%" if excess is not None else "N/A"]})
    _header("Relative performance",T); _table(["RETURN","EXCESS VS STOCK"],rows,T)
    _unavailable("Peer relative strength", "Synchronized peer price history", T)


def _data_sources(data,T):
    s=data.get("summary") or {}; sources=[
        ("Fundamentals","Screener / company financial disclosures",s.get("status","unavailable")),
        ("Price / OHLCV","Yahoo Finance (current stack)","live/cached"),
        ("Sector / Industry","Screener",(data.get("sector") or {}).get("status","unavailable")),
        ("Peers","Screener / normalized peer data",(data.get("peers") or {}).get("status","unavailable")),
        ("Shareholding","Screener / exchange disclosures",(data.get("shareholding") or {}).get("status","unavailable")),
        ("News","Existing Arka news connector","callback"),
        ("DCF / Factors / Technical","ARKA calculation engine","computed"),
        ("Consensus estimates","Premium/estimates connector","required"),
        ("MF fund-level holdings","Dedicated ownership dataset","required"),
        ("Earnings-call transcripts","Transcript/company disclosure source","required"),
        ("Supply-chain relationships","Company filings / research dataset","required"),
        ("Historical valuation series","Point-in-time market-data history","required"),
    ]
    _header("Data provenance",T)
    rows=[{"label":a,"values":[b,c]} for a,b,c in sources]
    _table(["SOURCE / ENGINE","STATUS"],rows,T)
    _panel("Integrity rule",f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.8">No unavailable dataset is synthesized. Every section either renders connected data, a calculation derived from connected data, or an explicit source requirement.</div>',T)
    _unavailable("Point-in-time audit trail", "Stored timestamped snapshots of each source response", T)

def _overview(data,T):
    m=_deep(data); s=(data.get("summary") or {}).get("data") or {}
    _kpis([("Market Cap",s.get("market_cap","N/A"),"₹ Cr"),("Price",s.get("current_price","N/A"),"₹"),("P/E",f'{m["pe"]:.2f}x' if m["pe"] is not None else "N/A","reported"),("P/B",f'{m["pb"]:.2f}x' if m["pb"] is not None else "N/A","derived"),("ROE",f'{m["roe"]:.2f}%' if m["roe"] is not None else "N/A","reported"),("ROCE",f'{m["roce"]:.2f}%' if m["roce"] is not None else "N/A","reported"),("D/E",f'{m["de"]:.2f}x' if m["de"] is not None else "N/A","computed"),("FCF",f'{m["fcf"]:,.0f} Cr' if m["fcf"] is not None else "N/A","CFO − capex")],T,4)
    _header("What changed · latest quarter",T)
    rows=[]
    for label, r in [("Sales",_row(data.get("quarterly"),["sales","revenue"])),("Operating Profit",_row(data.get("quarterly"),["operating profit"])),("Net Profit",_row(data.get("quarterly"),["net profit","profit for the period"])),("EPS",_row(data.get("quarterly"),["eps"]))]:
        a,b=_latest2(r); rows.append((label,a,b,_pct_change(a,b)))
    body=''.join(f'<tr><td>{_esc(a)}</td><td>{b:,.2f}</td><td>{c:,.2f}</td><td style="color:{T["green"] if d is not None and d>=0 else T["red"] if d is not None else T["t3"]}">{f"{d:+.1f}%" if d is not None else "—"}</td></tr>' for a,b,c,d in rows if b is not None and c is not None)
    if not body: body='<tr><td colspan="4">Not enough quarterly data to calculate the change table.</td></tr>'
    st.markdown(f'<div style="border:1px solid {T["border"]};background:{T["panel"]}"><table style="width:100%;border-collapse:collapse;font:10px {T["mono"]}"><tr style="background:{T["panel2"]};color:{T["t3"]}"><th>METRIC</th><th>LATEST</th><th>PREV</th><th>Q/Q</th></tr>{body}</table></div>',unsafe_allow_html=True)
    sec=(data.get("sector") or {}).get("data") or {}; chain=" → ".join(sec.get(k,"") for k in ("Broad Sector","Sector","Broad Industry","Industry") if sec.get(k))
    if chain: _panel("Classification",f'<div style="font:11px {T["mono"]};color:{T["t2"]}">{_esc(chain)}</div>',T)


def _financials(data,T):
    for title,key,hl in [("P&L · Quarterly","quarterly",["sales","operating profit","net profit","eps"]),("P&L · Annual","yearly",["sales","operating profit","net profit","eps"]),("Balance Sheet","balance_sheet",["borrowings","reserves","total assets"]),("Cash Flow","cash_flow",["cash from operating","cash from investing","net cash flow"])]:
        sec=data.get(key) or {}; _header(title,T,sec.get("status")); d=sec.get("data") or {}; _table(d.get("periods",[]),d.get("rows",[]),T,hl)
    m=_deep(data); _header("Financial quality ratios",T)
    _kpis([("Operating Margin",f'{m["op_margin"]:.2f}%' if m["op_margin"] is not None else "N/A","latest quarter"),("Net Margin",f'{m["net_margin"]:.2f}%' if m["net_margin"] is not None else "N/A","latest quarter"),("OCF/PAT",f'{m["ocf_pat"]:.2f}x' if m["ocf_pat"] is not None else "N/A","cash conversion"),("FCF/PAT",f'{m["fcf_pat"]:.2f}x' if m["fcf_pat"] is not None else "N/A","cash conversion")],T,4)
    _panel("Methodology",f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.7">Ratios marked computed are derived from the latest available provider rows. OCF/PAT and FCF/PAT should be interpreted alongside the underlying cash-flow table.</div>',T)


def _earnings(data,T):
    _header("Reported earnings",T,(data.get("quarterly") or {}).get("status")); q=data.get("quarterly") or {}; d=q.get("data") or {}; _table(d.get("periods",[]),d.get("rows",[]),T,["sales","operating profit","net profit","eps"])
    _header("Annual earnings",T,(data.get("yearly") or {}).get("status")); y=data.get("yearly") or {}; d=y.get("data") or {}; _table(d.get("periods",[]),d.get("rows",[]),T,["sales","operating profit","net profit","eps"])
    _unavailable("Consensus estimates", "Point-in-time analyst consensus feed", T, "Needed for forward revenue/EBITDA/EPS, consensus dispersion and target-price data.")
    _unavailable("Actual vs estimate", "Historical consensus snapshots", T, "The terminal will calculate surprise % only when an estimate existed before the result release.")
    _unavailable("Estimate revisions", "Timestamped analyst estimate history", T, "This must preserve point-in-time estimates to avoid look-ahead bias.")
    ed=get_earnings_date(data["symbol"])
    if ed.get("date"): _panel("Next earnings",f'<div style="font:14px {T["mono"]};color:{T["ivory"]}">{_esc(ed["date"])}</div>',T)


def _valuation(data,T):
    m=_deep(data); s=(data.get("summary") or {}).get("data") or {}
    _kpis([("P/E",f'{m["pe"]:.2f}x' if m["pe"] is not None else "N/A","current"),("P/B",f'{m["pb"]:.2f}x' if m["pb"] is not None else "N/A","derived"),("EV/EBITDA","N/A","connector required"),("EV/Sales","N/A","connector required"),("Earnings Yield",f'{m["earnings_yield"]:.2f}%' if m["earnings_yield"] is not None else "N/A","derived"),("FCF Yield","N/A","connector required"),("Dividend Yield",f'{m["div_yield"]:.2f}%' if m["div_yield"] is not None else "N/A","reported"),("5Y P/E Percentile","N/A","history required")],T,4)
    peers=get_peer_comparison(data["symbol"])
    _header("Relative valuation",T,peers.get("status"))
    if peers.get("rows"):
        rows=[]
        for r in peers["rows"]:
            rows.append({"label":f'{r["name"]} ({r["symbol"]})',"values":[r.get("pe","—"),r.get("market_cap","—"),r.get("div_yield","—"),r.get("roce","—")]})
        _table(["P/E","M.CAP ₹Cr","DIV YIELD %","ROCE %"],rows,T)
    else: _unavailable("Peer valuation", "Reliable comparable-company dataset", T)
    _unavailable("Historical multiples", "Point-in-time valuation history", T)
    _unavailable("DCF", "Forward free-cash-flow / WACC assumptions", T, "The DCF workspace is reserved; assumptions should be editable and saved per security.")
    _unavailable("Sensitivity", "DCF model with editable WACC and terminal-growth ranges", T)


def _ownership(data,T):
    sh=data.get("shareholding") or {}; d=sh.get("data") or {}; _header("Ownership structure",T,sh.get("status")); _table(d.get("periods",[]),d.get("rows",[]),T,["promoter","fii","dii","public","mutual fund"])
    _unavailable("Individual institutional holders", "Quarterly institutional holder dataset", T)
    _unavailable("Mutual-fund ownership", "MF portfolio/holding disclosures with fund-level history", T)
    _unavailable("MF entrants / exits", "Fund-level quarterly holdings with previous-period matching", T)
    _unavailable("Promoter intelligence", "Insider transaction + pledge/encumbrance disclosures", T)
    _unavailable("Ownership change history", "Longitudinal shareholder-category history", T)


def _flows(data,T):
    _unavailable("Institutional flow analysis", "FII/DII daily/weekly net-flow dataset plus security-level attribution", T)
    _unavailable("Mutual-fund accumulation", "Fund-level transaction/holding history", T)
    _unavailable("Block / bulk deal intelligence", "Exchange bulk/block-deal feed linked to holders", T)
    _unavailable("Ownership vs price", "Historical ownership observations aligned to price dates", T)


def _segments(data,T):
    _unavailable("Segment revenue", "Company filing / annual-report segment tables", T)
    _unavailable("Segment EBITDA", "Segment-level operating-profit/EBITDA disclosures", T)
    _unavailable("Segment growth & mix", "Multi-period segment history", T)
    _unavailable("Geographic exposure", "Geographic revenue/profit disclosures", T)


def _peers(data,T):
    result=get_peer_comparison(data["symbol"]); _header("Peer table",T,result.get("status"))
    if result.get("rows"):
        rows=[]
        for r in result["rows"]:
            rows.append({"label":f'{r["name"]} ({r["symbol"]})',"values":[r.get("cmp","—"),r.get("pe","—"),r.get("market_cap","—"),r.get("div_yield","—"),r.get("roce","—")]})
        _table(["CMP ₹","P/E","M.CAP ₹Cr","DIV YIELD %","ROCE %"],rows,T)
    else: _unavailable("Peer comparison", "Comparable-company universe", T)
    _unavailable("Peer growth / quality", "Comparable-company financial history", T)
    _unavailable("Peer percentile", "Synchronized peer metrics across valuation and quality factors", T)


def _supply_chain(data,T):
    _unavailable("Supplier map", "Company filings, supplier disclosures and supply-chain relationship dataset", T)
    _unavailable("Customer map", "Customer concentration / relationship disclosures", T)
    _unavailable("Commodity exposure", "Segment-level commodity sensitivity and input-cost history", T)
    _unavailable("Supply-chain news", "Entity-linked news/event graph", T)


def _management(data,T):
    _unavailable("Earnings-call transcripts", "Timestamped earnings-call transcript/audio source", T)
    _unavailable("Management guidance", "Transcript / investor-presentation extraction", T)
    _unavailable("Commentary changes", "Quarter-over-quarter management-language history", T)
    _unavailable("Filings & presentations", "Exchange/company filing document feed", T)


def _events(data,T):
    ed=get_earnings_date(data["symbol"])
    _panel("Next earnings",f'<div style="font:12px {T["mono"]};color:{T["ivory"]}">{_esc(ed.get("date","N/A"))}</div>',T)
    _unavailable("Corporate event calendar", "Exchange/company event feed", T, "Results, AGM, board meetings, investor days, dividends and other corporate actions should populate this timeline.")
    _unavailable("Event price impact", "Event timestamps aligned with intraday price/volume", T)
    _unavailable("Dividend / split history", "Corporate-actions history", T)


def _news(data,T,news_fetch_fn):
    if not news_fetch_fn:
        _unavailable("News timeline", "News provider callback", T)
        return
    try:
        news=news_fetch_fn(data["symbol"]) or []
    except Exception:
        news=[]
    if not news:
        _unavailable("News timeline", "Working news feed", T)
        return
    _header("News timeline",T)
    for item in news[:30]:
        title=item.get("title") or item.get("headline") or "Untitled"
        source=item.get("source") or item.get("publisher") or "Source unavailable"
        ts=item.get("published") or item.get("published_at") or item.get("time") or ""
        url=item.get("url") or item.get("link")
        link=f'<a href="{html.escape(str(url),quote=True)}" target="_blank" style="color:{T["ivory"]};text-decoration:none">{_esc(title)}</a>' if url else _esc(title)
        st.markdown(f'<div style="padding:8px 0;border-bottom:1px solid {T["border"]};font:10px {T["mono"]}"><div>{link}</div><div style="color:{T["t3"]};margin-top:3px">{_esc(source)} · {_esc(ts)}</div></div>',unsafe_allow_html=True)
    _unavailable("News → price impact", "Timestamped market-price/event alignment", T)
    _unavailable("News sentiment / attention", "News classification and volume history", T)


def _risk(data,T):
    m=_deep(data)
    checks=[("Leverage",m["de"],"computed D/E"),("Interest coverage",m["ic"],"computed"),("Cash conversion",m["ocf_pat"],"OCF/PAT"),("Valuation",m["pe"],"P/E"),("Net margin",m["net_margin"],"latest quarter")]
    rows=[{"label":a,"values":[f'{b:.2f}' if b is not None else "N/A",c]} for a,b,c in checks]
    _header("Risk monitor",T); _table(["VALUE","BASIS"],rows,T)
    for title,req in [("Business risk","Business/segment concentration dataset"),("Regulatory risk","Regulatory event/entity feed"),("Commodity risk","Commodity sensitivity model"),("Currency risk","FX exposure disclosures"),("Liquidity risk","Turnover, free float and market-impact history"),("Governance risk","Filings, related-party and governance event dataset")]:
        _unavailable(title,req,T)


def _factors(data,T):
    factors=get_factors(data["symbol"],full_research=data)
    _header("Current factor observations",T,factors.get("status"))
    if factors.get("items"):
        rows=[{"label":x["label"],"values":[f'{x["latest"]:,.2f}{x["unit"]}',f'{x["previous"]:,.2f}{x["unit"]}' if x.get("previous") is not None else "—"]} for x in factors["items"]]
        _table(["LATEST","PREVIOUS"],rows,T)
    else: _unavailable("Factor exposure", "Sufficient price/fundamental history", T)
    _unavailable("Cross-sectional factor score", "Peer universe + standardized factor history", T)
    _unavailable("Factor exposure vs NIFTY", "Benchmark factor model", T)


def _price_fundamentals(data,T):
    period=st.selectbox("Chart range",["3mo","6mo","1y","2y","5y"],index=1,key="research_pf_range")
    h=_chart(data["symbol"],T,period)
    _unavailable("Price vs EPS", "Synchronized historical EPS and price series", T)
    _unavailable("Price vs valuation", "Historical P/E or EV/EBITDA series", T)
    _unavailable("Price vs ownership", "Historical FII/DII/MF ownership observations", T)
    return h


def _what_changed(data,T):
    m=_deep(data); q=data.get("quarterly") or {}
    rows=[]
    for label,r in [("Revenue",_row(q,["sales","revenue"])),("Operating Profit",_row(q,["operating profit"])),("Net Profit",_row(q,["net profit","profit for the period"])),("EPS",_row(q,["eps"]))]:
        a,b=_latest2(r); rows.append((label,a,b,_pct_change(a,b)))
    body=''.join(f'<tr><td>{_esc(a)}</td><td>{b:,.2f}</td><td>{c:,.2f}</td><td style="color:{T["green"] if d is not None and d>=0 else T["red"] if d is not None else T["t3"]}">{f"{d:+.2f}%" if d is not None else "—"}</td></tr>' for a,b,c,d in rows if b is not None and c is not None)
    if not body: body='<tr><td colspan="4">Insufficient quarterly history.</td></tr>'
    st.markdown(f'<div style="border:1px solid {T["border"]};background:{T["panel"]}"><table style="width:100%;border-collapse:collapse;font:10px {T["mono"]}"><tr style="background:{T["panel2"]};color:{T["t3"]}"><th>METRIC</th><th>LATEST</th><th>PREVIOUS</th><th>CHANGE</th></tr>{body}</table></div>',unsafe_allow_html=True)
    _unavailable("Estimate changes", "Analyst revision history", T)
    _unavailable("Ownership changes", "Fund/institutional history", T)
    _unavailable("Valuation changes", "Historical multiple series", T)


def _thesis(data,T):
    _panel("Thesis workspace",f'<div style="font:10px {T["mono"]};color:{T["t2"]};line-height:1.8">This is a research notebook, not a recommendation engine. Populate Bull Case, Bear Case, Catalysts and Risks from sourced facts and user notes.</div>',T)
    for title,req in [("Bull case","Analyst/user-entered evidence and source links"),("Bear case","Analyst/user-entered evidence and source links"),("Catalysts","Corporate-event and earnings calendar"),("Risks","Sourced business/financial/regulatory risk data")]: _unavailable(title,req,T)


def _graph(data,T):
    symbol=data["symbol"]
    nodes=["FINANCIALS","EARNINGS","VALUATION","OWNERSHIP","FLOWS","SEGMENTS","PEERS","SUPPLY CHAIN","MANAGEMENT","EVENTS","NEWS","RISK"]
    # Simple terminal relationship map; it intentionally does not claim relationships that aren't sourced yet.
    cols=st.columns(3)
    for i,n in enumerate(nodes):
        with cols[i%3]:
            st.markdown(f'<div style="border:1px solid {T["border"]};background:{T["panel"]};padding:10px;margin:4px 0;font:700 10px {T["mono"]};color:{T["ivory"]}">{_esc(symbol)}<span style="color:{T["amber"]}"> → {_esc(n)}</span><div style="font:9px {T["mono"]};color:{T["t3"]};margin-top:4px">DRILL-DOWN NODE</div></div>',unsafe_allow_html=True)
    _panel("Graph status",f'<div style="font:10px {T["mono"]};color:{T["t2"]}">The architecture is live; relationship edges are populated only when a verified source exists.</div>',T)


def _reserved(data,T):
    _unavailable("Insider transactions", "Exchange/company insider transaction feed", T)
    _unavailable("Historical valuation percentiles", "Point-in-time market-data history", T)
    _unavailable("Institutional flows", "Security-level institutional flow dataset", T)


def render_research_page(T=None, news_fetch_fn=None):
    T=_theme(T)
    st.markdown(f'''<style>
    [data-testid="stAppViewContainer"]{{background:{T["bg"]}}}
    [data-testid="stHeader"]{{background:{T["bg"]}}}
    div[data-testid="stTextInput"] input{{font-family:{T["mono"]};background:{T["panel"]};color:{T["ivory"]};border:1px solid {T["border"]}}}
    div[data-testid="stTextInput"] input:focus{{border-color:{T["amber"]}}}
    button[data-baseweb="tab"]{{font-family:{T["mono"]}!important;font-size:10px!important;color:{T["t2"]}!important}}
    .research-nav-wrap{{overflow-x:auto;white-space:nowrap;}}
    </style>''',unsafe_allow_html=True)
    _status_strip(T); _blotter(T)
    st.markdown(f'<div style="font:9px {T["mono"]};color:{T["t3"]};margin-bottom:3px">RESEARCH SECURITY</div>',unsafe_allow_html=True)
    query=st.text_input("Security",placeholder="RELIANCE / HDFCBANK / TCS",label_visibility="collapsed",key="research_query_input")
    if query and query.strip().upper()!=st.session_state.get("research_last_query","").upper():
        st.session_state["research_last_query"]=query.strip(); st.session_state.pop("research_data",None)
    active=st.session_state.get("research_last_query","")
    if not active:
        st.markdown(f'<div style="padding:55px 20px;text-align:center;border:1px dashed {T["border"]};color:{T["t3"]};font:11px {T["mono"]}">ENTER A SECURITY TO INITIALIZE THE COMPLETE RESEARCH WORKSPACE.</div>',unsafe_allow_html=True)
        return
    if "research_data" not in st.session_state:
        with st.spinner(f"Loading research dataset · {active.upper()}…"):
            try: st.session_state["research_data"]=get_full_research(active)
            except Exception as e: st.session_state["research_data"]={"resolved":False,"reason":str(e)}
    data=st.session_state["research_data"]
    if not data.get("resolved"):
        st.error(f'SYMBOL NOT FOUND / FETCH FAILED: {data.get("reason","")}'); return
    s=(data.get("summary") or {}).get("data") or {}
    st.markdown(f'<div style="border:1px solid {T["border"]};border-top:2px solid {T["amber"]};background:{T["panel"]};padding:10px 13px;margin:10px 0"><span style="font:800 15px {T["mono"]};color:{T["ivory"]}">{_esc(data["name"])}</span><span style="font:10px {T["mono"]};color:{T["t3"]};margin-left:8px">{_esc(data["symbol"])} · NSE/BSE</span><span style="float:right">{_tag((data.get("summary") or {}).get("status"),T,(data.get("summary") or {}).get("age_seconds"))}</span></div>',unsafe_allow_html=True)
    if s:
        _kpis([("Price",s.get("current_price","N/A"),"₹"),("Market Cap",s.get("market_cap","N/A"),"₹ Cr"),("P/E",s.get("pe_ratio","N/A"),"x"),("Book Value",s.get("book_value","N/A"),"₹"),("ROE",s.get("roe","N/A"),"%"),("ROCE",s.get("roce","N/A"),"%"),("Dividend Yield",s.get("dividend_yield","N/A"),"%"),("Face Value",s.get("face_value","N/A"),"₹")],T,4)
    st.markdown(f'<div style="border-bottom:1px solid {T["border"]};margin:12px 0"></div>',unsafe_allow_html=True)
    tabs=["01 Overview","02 Company","03 Business","04 Financials","05 Earnings","06 Valuation","07 Ownership","08 Flows","09 Segments","10 Peers","11 Supply Chain","12 Management","13 Filings","14 Events","15 News","16 Technical","17 Relative Strength","18 Factors","19 Risk","20 Price + Fundamentals","21 What Changed?","22 Thesis","23 Research Graph","24 Data / Sources"]
    if option_menu:
        selected=option_menu(None,tabs,icons=["grid","building","briefcase","bar-chart","graph-up","cash","people","activity","diagram-3","columns","truck","mic","file-earmark-text","calendar","newspaper","candlestick-chart","graph-up-arrow","bullseye","shield","graph-up","lightning","journal-text","diagram-2","database"],default_index=0,orientation="horizontal",styles={"container":{"padding":"0!important","background-color":"transparent"},"icon":{"color":T["amber"],"font-size":"12px"},"nav-link":{"font-size":"9px","text-align":"center","margin":"0px","padding":"7px 4px","font-family":T["mono"]},"nav-link-selected":{"background-color":"transparent","color":T["amber"],"border-bottom":f"2px solid {T["amber"]}"}})
    else:
        selected=st.selectbox("Research function",tabs,key="research_function")
    st.markdown('<div style="height:5px"></div>',unsafe_allow_html=True)
    dispatch={
        "01 Overview":lambda:_overview(data,T),"02 Company":lambda:_company(data,T),"03 Business":lambda:_business(data,T),"04 Financials":lambda:_financials(data,T),
        "05 Earnings":lambda:_earnings(data,T),"06 Valuation":lambda:_valuation(data,T),"07 Ownership":lambda:_ownership(data,T),"08 Flows":lambda:_flows(data,T),
        "09 Segments":lambda:_segments(data,T),"10 Peers":lambda:_peers(data,T),"11 Supply Chain":lambda:_supply_chain(data,T),"12 Management":lambda:_management(data,T),
        "13 Filings":lambda:_filings(data,T),"14 Events":lambda:_events(data,T),"15 News":lambda:_news(data,T,news_fetch_fn),"16 Technical":lambda:_technical(data,T),
        "17 Relative Strength":lambda:_relative_strength(data,T),"18 Factors":lambda:_factors(data,T),"19 Risk":lambda:_risk(data,T),"20 Price + Fundamentals":lambda:_price_fundamentals(data,T),
        "21 What Changed?":lambda:_what_changed(data,T),"22 Thesis":lambda:_thesis(data,T),"23 Research Graph":lambda:_graph(data,T),"24 Data / Sources":lambda:_data_sources(data,T),
    }
    dispatch[selected]()

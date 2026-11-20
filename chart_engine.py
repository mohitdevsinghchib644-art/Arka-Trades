"""
chart_engine.py — Arka Trades pro chart (v2)

v2: restyled to the app (Inter, rounded pill controls, sentence-case labels, softer grid) and two swing-trading
averages added: EMA 10 and EMA 21 (EMA 21 on by default). Everything still switches in the browser with no rerun.

TradingView's open-source "Lightweight Charts" (v4.2.0) rendered client-side.
  * Candles + volume + MA 20/50/200 on top
  * RSI(14) pane underneath with a 14-period MA of the RSI (+ 70/50/30 levels)
  * D / W interval, 1M…5Y ranges, LOG scale, PDH/PDL lines — all switch in the
    browser instantly: NO Streamlit rerun, NO re-download.
  * RSI is Wilder's RSI (identical to TradingView), so numbers match your TV.

Usage:  render_pro_chart("RELIANCE")
"""
from __future__ import annotations

import html as _html
import json

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from data_engine import get_history, rsi_wilder


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


def _weekly(d: pd.DataFrame) -> pd.DataFrame:
    key = d.index.to_period("W-FRI")
    g = d.groupby(key)
    w = g.agg(Open=("Open", "first"), High=("High", "max"), Low=("Low", "min"),
              Close=("Close", "last"), Volume=("Volume", "sum"))
    last_days = d.index.to_series().groupby(key).max()
    w.index = pd.DatetimeIndex(last_days.values)
    return w


def _pack(d: pd.DataFrame) -> dict:
    close = d["Close"]
    times = d.index.strftime("%Y-%m-%d").tolist()

    def line(s: pd.Series):
        # Leading NaNs become whitespace points so every series shares the same
        # time axis (keeps the price pane and RSI pane perfectly aligned).
        return [({"time": t, "value": round(float(v), 2)} if pd.notna(v) else {"time": t})
                for t, v in zip(times, s.tolist())]

    rsi = rsi_wilder(close, 14)
    rsi_ma = rsi.rolling(14).mean()
    up, dn = "rgba(48,209,88,0.45)", "rgba(255,69,58,0.45)"
    return {
        "c": [{"time": t, "open": round(o, 2), "high": round(h, 2), "low": round(l, 2), "close": round(c, 2)}
              for t, o, h, l, c in zip(times, d["Open"], d["High"], d["Low"], d["Close"])],
        "v": [{"time": t, "value": float(v), "color": up if c >= o else dn}
              for t, v, c, o in zip(times, d["Volume"], d["Close"], d["Open"])],
        "m20": line(close.rolling(20).mean()),
        "e10": line(close.ewm(span=10, adjust=False).mean()),
        "e21": line(close.ewm(span=21, adjust=False).mean()),
        "m50": line(close.rolling(50).mean()),
        "m200": line(close.rolling(200).mean()),
        "r": line(rsi),
        "rm": line(rsi_ma),
    }


def build_payload(h: pd.DataFrame) -> dict:
    d = _prep(h)
    if len(d) < 5:
        return {}
    pdh = pdl = None
    if len(d) >= 2:
        pdh, pdl = round(float(d["High"].iloc[-2]), 2), round(float(d["Low"].iloc[-2]), 2)
    return {"D": _pack(d), "W": _pack(_weekly(d)), "pdh": pdh, "pdl": pdl}


@st.cache_data(ttl=120, show_spinner=False, max_entries=24)
def _payload_json(symbol: str, v: int = 2) -> str:
    h = get_history(symbol, "5y")
    if h is None or h.empty:
        raise ValueError("no history")
    p = build_payload(h)
    if not p:
        raise ValueError("too little history")
    return json.dumps(p, separators=(",", ":"))


# ───────────────────────── browser side ─────────────────────────
_TEMPLATE = r"""<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
<script>if(!window.LightweightCharts){document.write('<script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"><\/script>');}</script>
<style>
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:#0E0E10;color:#A9A9B0;font-family:Inter,-apple-system,'Segoe UI',Roboto,sans-serif;overflow:hidden}
body{display:flex;flex-direction:column;border:1px solid #1E1E22;border-radius:14px}
#bar{display:flex;flex-wrap:wrap;gap:8px 18px;align-items:center;padding:10px 12px;border-bottom:1px solid #1E1E22;flex:0 0 auto}
.grp{display:flex;gap:4px;align-items:center;flex-wrap:wrap}
.grp .lbl{font-size:12px;color:#6E6E76;margin-right:4px}
button{background:#151518;color:#A9A9B0;border:1px solid #26262B;font:500 12px Inter,-apple-system,'Segoe UI',sans-serif;padding:5px 10px;cursor:pointer;
       border-radius:999px;transition:background .15s,border-color .15s,color .15s}
button:hover{border-color:#3A3A40;color:#ECECEF}
button.on{background:rgba(255,159,10,.14);color:#FF9F0A;border-color:rgba(255,159,10,.4)}
#wrap{flex:1 1 auto;display:flex;flex-direction:column;min-height:0}
#mainwrap{position:relative;flex:1 1 auto;min-height:180px}
#rsiwrap{position:relative;flex:0 0 150px;border-top:1px solid #1E1E22}
.cv{position:absolute;inset:0}
.lg{position:absolute;left:12px;top:8px;z-index:5;pointer-events:none;font-size:12px;line-height:1.7;color:#8A8A92;
    text-shadow:0 0 6px #0E0E10,0 0 6px #0E0E10;font-variant-numeric:tabular-nums}
.lg b{color:#ECECEF;font-weight:650}.lg i{font-style:normal;color:#DADADF}.up{color:#30D158!important}.dn{color:#FF453A!important}.mut{color:#6E6E76}
</style></head><body>
<div id="bar">
  <div class="grp"><span class="lbl">Range</span>
    <button data-rng="1M">1M</button><button data-rng="3M">3M</button><button data-rng="6M">6M</button>
    <button data-rng="1Y">1Y</button><button data-rng="2Y">2Y</button><button data-rng="5Y">5Y</button></div>
  <div class="grp"><span class="lbl">Candles</span><button data-iv="D">Daily</button><button data-iv="W">Weekly</button></div>
  <div class="grp"><span class="lbl">Studies</span>
    <button data-tg="e10">EMA 10</button><button data-tg="e21">EMA 21</button>
    <button data-tg="ma20">SMA 20</button><button data-tg="ma50">SMA 50</button><button data-tg="ma200">SMA 200</button>
    <button data-tg="vol">Volume</button><button data-tg="rsi">RSI</button>
    <button data-tg="pd">PDH / PDL</button><button data-tg="log">Log scale</button></div>
</div>
<div id="wrap">
  <div id="mainwrap"><div id="main" class="cv"></div><div id="lg" class="lg"></div></div>
  <div id="rsiwrap"><div id="rsi" class="cv"></div><div id="rlg" class="lg"></div></div>
</div>
<script>
(function(){
const P = __PAYLOAD__;
const SYM = "__SYM__";
const LW = window.LightweightCharts;
const $ = id => document.getElementById(id);
if(!LW){ $('lg').textContent = 'The chart library could not load. Check your connection and refresh.'; return; }
const C = {up:'#30D158', dn:'#FF453A', amber:'#FF9F0A', cyan:'#5AC8FA', purple:'#BF5AF2', grid:'#17171A', border:'#26262B', text:'#8A8A92', bg:'#0E0E10',
           ma20:'#5AC8FA', ma50:'#FF9F0A', ma200:'#BF5AF2', e10:'#FFD60A', e21:'#F2F2F5'};
const S = {iv:'D', rng:'__RANGE__', e10:false, e21:true, ma20:false, ma50:true, ma200:true, vol:true, rsi:true, pd:true, log:false};
const FONT = "Inter,-apple-system,'Segoe UI',Roboto,sans-serif";

function opts(el, showTime){ return {
  width: el.clientWidth || 800, height: el.clientHeight || 300,
  layout:{background:{type:'solid', color:C.bg}, textColor:C.text, fontFamily:FONT, fontSize:11},
  grid:{vertLines:{color:C.grid}, horzLines:{color:C.grid}},
  rightPriceScale:{borderColor:C.border, minimumWidth:72},
  timeScale:{borderColor:C.border, rightOffset:6, barSpacing:7, minBarSpacing:1.2, timeVisible:false, visible:showTime},
  crosshair:{mode:LW.CrosshairMode.Normal, vertLine:{color:'#4A4A52', labelBackgroundColor:'#26262B'}, horzLine:{color:'#4A4A52', labelBackgroundColor:'#26262B'}},
  handleScroll:{mouseWheel:true, pressedMouseMove:true, horzTouchDrag:true, vertTouchDrag:false},
  handleScale:{axisPressedMouseMove:true, mouseWheel:true, pinch:true},
  localization:{dateFormat:'dd MMM yyyy', priceFormatter:p => p.toFixed(2)}
};}

const main = LW.createChart($('main'), opts($('main'), false));
const rsiC = LW.createChart($('rsi'),  opts($('rsi'), true));

const candle = main.addCandlestickSeries({upColor:C.up, downColor:C.dn, borderUpColor:C.up, borderDownColor:C.dn, wickUpColor:C.up, wickDownColor:C.dn, priceFormat:{type:'price', precision:2, minMove:0.05}});
candle.priceScale().applyOptions({scaleMargins:{top:0.06, bottom:0.2}});
const vol = main.addHistogramSeries({priceScaleId:'vol', priceFormat:{type:'volume'}, lastValueVisible:false, priceLineVisible:false});
vol.priceScale().applyOptions({scaleMargins:{top:0.82, bottom:0}});
function ln(color, w){ return main.addLineSeries({color, lineWidth:w||1, priceLineVisible:false, lastValueVisible:false, crosshairMarkerVisible:false}); }
const ma20 = ln(C.ma20), ma50 = ln(C.ma50), ma200 = ln(C.ma200, 2), e10 = ln(C.e10), e21 = ln(C.e21, 1.5);

const rsiL = rsiC.addLineSeries({color:C.purple, lineWidth:2, priceLineVisible:false, lastValueVisible:true, priceFormat:{type:'price', precision:2, minMove:0.01},
  autoscaleInfoProvider:()=>({priceRange:{minValue:0, maxValue:100}})});
const rsiM = rsiC.addLineSeries({color:C.amber, lineWidth:1, priceLineVisible:false, lastValueVisible:true, priceFormat:{type:'price', precision:2, minMove:0.01}});
[[70,'#FF453A88'],[50,'#55555588'],[30,'#30D15888']].forEach(([p,c]) => rsiL.createPriceLine({price:p, color:c, lineWidth:1, lineStyle:2, axisLabelVisible:true, title:''}));
rsiC.priceScale('right').applyOptions({scaleMargins:{top:0.1, bottom:0.1}});

let pdhL=null, pdlL=null, tIndex=new Map(), xs=false;

function fmt(v,d){ d=(d==null?2:d); return (v==null||isNaN(v))?'—':Number(v).toLocaleString('en-IN',{minimumFractionDigits:d,maximumFractionDigits:d}); }
function fmtVol(v){ if(v==null) return '—'; if(v>=1e7) return (v/1e7).toFixed(2)+'Cr'; if(v>=1e5) return (v/1e5).toFixed(2)+'L'; if(v>=1e3) return (v/1e3).toFixed(1)+'K'; return String(Math.round(v)); }
function tkey(t){ if(t==null) return null; if(typeof t==='string') return t; if(typeof t==='number') return new Date(t*1000).toISOString().slice(0,10); if(t.year) return t.year+'-'+String(t.month).padStart(2,'0')+'-'+String(t.day).padStart(2,'0'); return null; }

function legend(i){
  const d = P[S.iv], c = d.c[i]; if(!c) return;
  const pc = i>0 ? d.c[i-1].close : c.open, chg = (c.close-pc)/pc*100, k = chg>=0?'up':'dn';
  const g = a => (a[i] && a[i].value!=null) ? fmt(a[i].value) : '—';
  let s = `<b>${SYM}</b> <span class="mut">${S.iv==='D'?'Daily':'Weekly'}</span> &nbsp;O <i>${fmt(c.open)}</i> H <i>${fmt(c.high)}</i> L <i>${fmt(c.low)}</i> C <i class="${k}">${fmt(c.close)}</i> <span class="${k}">${chg>=0?'+':''}${chg.toFixed(2)}%</span> &nbsp;Vol <i>${fmtVol(d.v[i]?d.v[i].value:null)}</i><br>`;
  if(S.e10) s += `<span style="color:${C.e10}">EMA 10 ${g(d.e10)}</span> &nbsp;`;
  if(S.e21) s += `<span style="color:${C.e21}">EMA 21 ${g(d.e21)}</span> &nbsp;`;
  if(S.ma20) s += `<span style="color:${C.ma20}">SMA 20 ${g(d.m20)}</span> &nbsp;`;
  if(S.ma50) s += `<span style="color:${C.ma50}">SMA 50 ${g(d.m50)}</span> &nbsp;`;
  if(S.ma200) s += `<span style="color:${C.ma200}">SMA 200 ${g(d.m200)}</span>`;
  $('lg').innerHTML = s;
  $('rlg').innerHTML = `RSI <span class="mut">14</span> <span style="color:${C.purple}">${g(d.r)}</span> &nbsp;<span style="color:${C.amber}">Average <span class="mut">14</span> ${g(d.rm)}</span>`;
}

function setPD(){
  if(pdhL){ candle.removePriceLine(pdhL); candle.removePriceLine(pdlL); pdhL=pdlL=null; }
  if(S.pd && P.pdh!=null){
    pdhL = candle.createPriceLine({price:P.pdh, color:C.amber, lineWidth:1, lineStyle:2, axisLabelVisible:true, title:'PDH'});
    pdlL = candle.createPriceLine({price:P.pdl, color:C.amber, lineWidth:1, lineStyle:2, axisLabelVisible:true, title:'PDL'});
  }
}

function setRange(r){
  const arr = P[S.iv].c; if(!arr.length) return;
  if(r==='ALL'){ main.timeScale().fitContent(); return; }
  const m = {'1M':1,'3M':3,'6M':6,'1Y':12,'2Y':24,'5Y':60}[r] || 6;
  const d = new Date(arr[arr.length-1].time+'T00:00:00Z'); d.setUTCMonth(d.getUTCMonth()-m);
  const from = d.toISOString().slice(0,10);
  let i = arr.findIndex(x => x.time >= from); if(i<0) i=0;
  main.timeScale().setVisibleLogicalRange({from:i-1, to:arr.length-1+6});
}

function toggles(){
  ma20.applyOptions({visible:S.ma20}); ma50.applyOptions({visible:S.ma50}); ma200.applyOptions({visible:S.ma200});
  e10.applyOptions({visible:S.e10}); e21.applyOptions({visible:S.e21});
  vol.applyOptions({visible:S.vol});
  candle.priceScale().applyOptions({scaleMargins:{top:0.06, bottom:S.vol?0.2:0.05}});
  main.priceScale('right').applyOptions({mode:S.log?1:0});
  $('rsiwrap').style.display = S.rsi ? 'block' : 'none';
  main.applyOptions({timeScale:{visible:!S.rsi}});
  setPD(); fit(); legend(P[S.iv].c.length-1);
}

function data(){
  const d = P[S.iv];
  candle.setData(d.c); vol.setData(d.v); ma20.setData(d.m20); ma50.setData(d.m50); ma200.setData(d.m200); e10.setData(d.e10); e21.setData(d.e21);
  rsiL.setData(d.r); rsiM.setData(d.rm);
  tIndex = new Map(d.c.map((x,i)=>[x.time,i]));
  setRange(S.rng); legend(d.c.length-1);
}

function mark(){
  document.querySelectorAll('[data-rng]').forEach(b => b.classList.toggle('on', b.dataset.rng===S.rng));
  document.querySelectorAll('[data-iv]').forEach(b => b.classList.toggle('on', b.dataset.iv===S.iv));
  document.querySelectorAll('[data-tg]').forEach(b => b.classList.toggle('on', !!S[b.dataset.tg]));
}

function fit(){
  const a = $('main'), b = $('rsi');
  main.resize(a.clientWidth || 800, a.clientHeight || 300);
  if(S.rsi) rsiC.resize(b.clientWidth || 800, b.clientHeight || 150);
}

document.querySelectorAll('[data-rng]').forEach(b => b.onclick = () => { S.rng=b.dataset.rng; mark(); setRange(S.rng); });
document.querySelectorAll('[data-iv]').forEach(b => b.onclick = () => { S.iv=b.dataset.iv; mark(); data(); });
document.querySelectorAll('[data-tg]').forEach(b => b.onclick = () => { const k=b.dataset.tg; S[k]=!S[k]; mark(); toggles(); });

// keep price pane and RSI pane scrolled/zoomed together
let sync=false;
main.timeScale().subscribeVisibleLogicalRangeChange(r => { if(!r||sync) return; sync=true; try{ rsiC.timeScale().setVisibleLogicalRange(r); }catch(e){} sync=false; });
rsiC.timeScale().subscribeVisibleLogicalRangeChange(r => { if(!r||sync) return; sync=true; try{ main.timeScale().setVisibleLogicalRange(r); }catch(e){} sync=false; });

// crosshair sync + legends
main.subscribeCrosshairMove(p => {
  if(xs) return;
  const k = tkey(p.time), i = k ? tIndex.get(k) : undefined;
  xs = true;
  try{
    if(i===undefined){ rsiC.clearCrosshairPosition(); legend(P[S.iv].c.length-1); }
    else { legend(i); const r = P[S.iv].r[i]; if(r && r.value!=null) rsiC.setCrosshairPosition(r.value, k, rsiL); }
  }catch(e){}
  xs = false;
});
rsiC.subscribeCrosshairMove(p => {
  if(xs) return;
  const k = tkey(p.time), i = k ? tIndex.get(k) : undefined;
  xs = true;
  try{
    if(i===undefined){ main.clearCrosshairPosition(); legend(P[S.iv].c.length-1); }
    else { legend(i); const c = P[S.iv].c[i]; if(c) main.setCrosshairPosition(c.close, k, candle); }
  }catch(e){}
  xs = false;
});

window.addEventListener('resize', fit);
if(window.ResizeObserver) new ResizeObserver(fit).observe(document.body);

mark(); data(); toggles();
setTimeout(() => { fit(); setRange(S.rng); }, 60);
})();
</script></body></html>"""


def render_pro_chart(symbol: str, height: int = 660, default_range: str = "6M") -> bool:
    """Render the chart. Returns False (and shows a warning) if no data."""
    sym = str(symbol or "").upper().strip()
    if not sym:
        return False
    try:
        payload = _payload_json(sym)
    except Exception:
        st.warning(f"No chart for {sym}: Yahoo Finance returned no price history. Try again in a minute.")
        return False
    if default_range not in {"1M", "3M", "6M", "1Y", "2Y", "5Y", "ALL"}:
        default_range = "6M"
    doc = (_TEMPLATE.replace("__PAYLOAD__", payload)
           .replace("__SYM__", _html.escape(sym))
           .replace("__RANGE__", default_range))
    components.html(doc, height=height, scrolling=False)
    return True

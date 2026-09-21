"""ScanX data adapter for Arka Trades.

Primary mode uses the same public web-service calls observed in the ScanX
browser application. HTML parsing remains a fallback for sections which are
already server-rendered.

Known browser-observed endpoints:
  POST https://ow-static-scanx.dhan.co/staticscanx/mftransaction
  POST https://openweb-ticks.dhan.co/getDataH

No login/session token is required by the observed calls. The adapter does
not attempt to bypass authentication or access controls.
"""
from __future__ import annotations
from io import StringIO
import json, re, time
from typing import Any
from urllib.parse import urljoin
import pandas as pd
import requests
from bs4 import BeautifulSoup

SCANX_BASE = "https://scanx.trade/company/"
SCANX_HOST = "https://scanx.trade"
MF_TX_URL = "https://ow-static-scanx.dhan.co/staticscanx/mftransaction"
PRICE_URL = "https://openweb-ticks.dhan.co/getDataH"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")

_SYMBOL_ALIASES = {
    "RELIANCE":"reliance-industries-ltd","HDFCBANK":"hdfc-bank-ltd","ICICIBANK":"icici-bank-ltd",
    "AXISBANK":"axis-bank-ltd","SBIN":"state-bank-of-india-ltd","INFY":"infosys-ltd",
    "TCS":"tcs-tata-consultancy-services-ltd","ITC":"itc-ltd","LT":"larsen-toubro-ltd",
    "BHARTIARTL":"bharti-airtel-ltd","KOTAKBANK":"kotak-mahindra-bank-ltd","MARUTI":"maruti-suzuki-india-ltd",
    "TATAMOTORS":"tata-motors-ltd","TATASTEEL":"tata-steel-ltd","SUNPHARMA":"sun-pharmaceutical-industries-ltd",
    "HINDUNILVR":"hindustan-unilever-ltd","BAJFINANCE":"bajaj-finance-ltd","ADANIENT":"adani-enterprises-ltd",
    "ADANIPORTS":"adani-ports-and-special-economic-zone-ltd","WIPRO":"wipro-ltd","TECHM":"tech-mahindra-ltd",
    "HCLTECH":"hcl-technologies-ltd","FCL":"fineotex-chemical-ltd",
}
_ISIN = {
    # Browser-observed from ScanX MF request for Reliance.
    "RELIANCE":"INE002A01018",
}


def _slugify(v):
    s=re.sub(r"[^a-z0-9]+","-",str(v or "").lower()).strip("-")
    return re.sub(r"-limited$","-ltd",s)

def _name_candidates(name):
    if not name:return []
    raw=str(name).strip(); s=_slugify(raw); out=[s] if s else []
    base=re.sub(r"\s+(limited|ltd\.?|pvt\.?\s*ltd\.?)$","",raw,flags=re.I).strip()
    bs=_slugify(base)
    if bs: out += [bs+"-ltd",bs]
    return list(dict.fromkeys(out))

def _company_url_candidates(symbol, company_name=None):
    sym=str(symbol or '').strip().upper(); slugs=[]
    if sym in _SYMBOL_ALIASES: slugs.append(_SYMBOL_ALIASES[sym])
    slugs += _name_candidates(company_name)
    if sym: slugs += [_slugify(sym)+"-ltd",_slugify(sym)]
    return list(dict.fromkeys(SCANX_BASE+s for s in slugs if s))

def company_url(symbol, company_name=None):
    c=_company_url_candidates(symbol,company_name)
    return c[0] if c else SCANX_BASE+_slugify(symbol)

def _headers(referer="https://scanx.trade/"):
    return {"User-Agent":_UA,"Accept":"application/json,text/plain,*/*","Content-Type":"application/json",
            "Origin":"https://scanx.trade","Referer":referer,"Cache-Control":"no-cache"}

def _post_json(url,payload,timeout=20):
    try:
        r=requests.post(url,json=payload,headers=_headers(),timeout=timeout)
        if 200 <= r.status_code < 300:
            try:return r.json(),r.status_code,None
            except Exception:return r.text,r.status_code,None
        return None,r.status_code,(r.text or '')[:500]
    except Exception as e:return None,None,str(e)

def _get(url,timeout=20):
    h=_headers(); h["Accept"]="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    try:
        r=requests.get(url,headers=h,timeout=timeout,allow_redirects=True)
        if r.status_code==200 and r.text:return r
    except Exception:pass
    return None

def _clean_frame(df):
    x=df.copy(); x.columns=[str(c).strip() for c in x.columns]
    return x.dropna(axis=0,how='all').dropna(axis=1,how='all').reset_index(drop=True)

def _table_text(df):
    return ' '.join([str(c) for c in df.columns]+df.astype(str).head(8).values.flatten().tolist()).lower()

def _classify_tables(tables):
    out={}
    for raw in tables:
        df=_clean_frame(raw)
        if df.empty: continue
        text=_table_text(df); cols=' '.join(str(c).lower() for c in df.columns)
        if 'quarterly financials' in text or (all(k in text for k in ('revenue','expenses','ebitda')) and len(df.columns)>=5): out.setdefault('financials',df)
        elif 'balance sheet' in text or all(k in text for k in ('total assets','total equity')): out.setdefault('balance_sheet',df)
        elif 'cash flow' in text or all(k in text for k in ('operating activities','financing activities')): out.setdefault('cash_flow',df)
        elif '% holding' in text or all(k in text for k in ('promoter','fiis','diis')) or all(k in text for k in ('promoters','fii','dii')): out.setdefault('shareholding',df)
        elif 'funding house' in text and 'current holding' in text: out.setdefault('mf_holdings',df)
        elif 'competitors' in text and 'market cap' in text and ('p/e' in text or 'pe ratio' in text): out.setdefault('peers',df)
        elif 'record date' in text and ('corporate action' in text or 'ltp at announcement' in text): out.setdefault('corporate_actions',df)
        elif 'dividend per share' in text or ('dividend' in text and 'record date' in text): out.setdefault('dividend',df)
        elif 'market cap' in cols and ('pe ratio' in cols or 'p/e' in cols): out.setdefault('snapshot',df)
    return out

def _extract_metric(text,label):
    labels="Market Cap|PE Ratio|Volume|Day High - Low|52W High-Low|EPS|PB Ratio|Book Value|EBITDA|Dividend Yield|Industry|Sector|Return on Equity|Debt to Equity|Analyst Rating"
    m=re.search(rf"{re.escape(label)}\s*([₹\d,\.\-+%()A-Za-z ]{{1,80}}?)(?=\s+(?:{labels})\b|$)",text,re.I)
    return m.group(1).strip() if m else None

def _extract_snapshot(soup):
    text=' '.join(soup.stripped_strings); out={}
    for label in ['Market Cap','PE Ratio','Volume','Day High - Low','52W High-Low','EPS','PB Ratio','Book Value','EBITDA','Dividend Yield','Return on Equity','Debt to Equity','Industry','Sector']:
        v=_extract_metric(text,label)
        if v:out[label]=v
    h=soup.find('h1')
    if h:out['company_name']=h.get_text(' ',strip=True)
    if h:
        strings=list(soup.stripped_strings); name=h.get_text(' ',strip=True)
        try:i=strings.index(name); tail=' '.join(strings[i+1:i+8])
        except Exception:tail=''
        m=re.search(r'(\d[\d,]*\.\d+)\s+([+-]\d[\d,.]*?)\s*\(([-+]?\d[\d.]+%)\)',tail)
        if m:out['price']=m.group(1);out['change']=f'{m.group(2)} ({m.group(3)})'
    return out

def _extract_about(soup):
    return {}

def _section_links(soup,heading_text,limit=100):
    heading=None
    for h in soup.find_all(['h2','h3']):
        if h.get_text(' ',strip=True).lower()==heading_text.lower():heading=h;break
    if heading is None:return []
    out=[]
    for n in heading.find_all_next():
        if getattr(n,'name',None) in {'h2','h3'} and n is not heading:break
        if getattr(n,'name',None)!='a':continue
        title=n.get_text(' ',strip=True); href=n.get('href')
        if not title or not href:continue
        out.append({'title':title,'url':urljoin(SCANX_HOST,href)})
        if len(out)>=limit:break
    return out

def _df_records(df,limit=500):
    if df is None or df.empty:return []
    return df.head(limit).to_dict(orient='records')

def _normalize_mf_response(raw):
    """Keep ScanX's native schema while also exposing a DataFrame-friendly list.
    The endpoint response is intentionally not position-mapped because the web
    application can change field ordering. All raw rows are preserved.
    """
    if raw is None:return {'raw':None,'rows':[]}
    obj=raw
    if isinstance(obj,str):
        try:obj=json.loads(obj)
        except Exception:return {'raw':obj,'rows':[]}
    rows=[]
    def walk(x):
        if isinstance(x,list):
            for v in x: walk(v)
        elif isinstance(x,dict):
            # likely row objects; retain dicts that have fund-ish fields
            if any(k.lower() in {'fundname','fund_name','fundinghouse','scheme','transaction','isin'} for k in x): rows.append(x)
            for v in x.values():
                if isinstance(v,(list,dict)):walk(v)
    walk(obj)
    # If endpoint returns positional arrays, retain them as raw rows too.
    def find_arrays(x):
        if isinstance(x,list):
            if x and all(isinstance(v,list) for v in x): return x
            for v in x:
                r=find_arrays(v)
                if r is not None:return r
        elif isinstance(x,dict):
            for v in x.values():
                r=find_arrays(v)
                if r is not None:return r
        return None
    arrays=find_arrays(obj)
    return {'raw':obj,'rows':rows,'arrays':arrays or []}

def fetch_mf_transactions(isin,page=1,page_size=25,timeout=20):
    payload={'data':{'isin':str(isin),'page':int(page),'pageSize':int(page_size)}}
    raw,status,error=_post_json(MF_TX_URL,payload,timeout)
    if raw is None:return {'status':'unavailable','endpoint':MF_TX_URL,'http_status':status,'error':error,'payload':payload}
    norm=_normalize_mf_response(raw)
    return {'status':'live','endpoint':MF_TX_URL,'http_status':status,'payload':payload,**norm}

def _find_isin(symbol,company_name=None):
    sym=str(symbol or '').upper().strip()
    if sym in _ISIN:return _ISIN[sym]
    # Optional environment override lets deployment add an ISIN without code changes.
    import os
    env=os.getenv('ARKA_SCANX_ISIN_'+re.sub('[^A-Z0-9]','',sym),'').strip()
    return env or None

def fetch_price_history(sec_id,symbol,interval='D',start=None,end=None,delivery=True,timeout=20):
    payload={'EXCH':'NSE','SYM':str(symbol).upper(),'SEG':'E','INST':'EQUITY','SEC_ID':int(sec_id),'EXPCODE':0,'INTERVAL':interval,
             'START':int(start) if start is not None else int(time.time())-365*86400,
             'END':int(end) if end is not None else int(time.time()),'DeliveryPer':bool(delivery)}
    raw,status,error=_post_json(PRICE_URL,payload,timeout)
    if raw is None:return {'status':'unavailable','endpoint':PRICE_URL,'http_status':status,'error':error,'payload':payload}
    return {'status':'live','endpoint':PRICE_URL,'http_status':status,'payload':payload,'raw':raw}

def _looks_like_scanx(soup):
    text=' '.join(soup.stripped_strings).lower()
    return bool(soup.find('h1')) and ('key fundamentals' in text or 'quarterly financial results' in text) and 'market cap' in text

def fetch_scanx_company(symbol,company_name=None,timeout=20):
    sym=str(symbol or '').strip().upper()
    candidates=_company_url_candidates(sym,company_name)
    last_url=candidates[0] if candidates else company_url(sym,company_name)
    resp=None
    for url in candidates:
        last_url=url; r=_get(url,timeout)
        if r is None:continue
        soup=BeautifulSoup(r.text,'html.parser')
        if _looks_like_scanx(soup):resp=r;break
    result={'status':'unavailable','symbol':sym,'name':company_name or sym,'url':last_url,'snapshot':{},'about':{},'announcements':[], 'news':[], 'technical':[], 'tables':{},'table_records':{},'source_mode':'api+html'}
    if resp is not None:
        soup=BeautifulSoup(resp.text,'html.parser')
        try:tables=pd.read_html(StringIO(resp.text))
        except Exception:tables=[]
        classified=_classify_tables(tables)
        result.update({'status':'live','name':soup.find('h1').get_text(' ',strip=True) if soup.find('h1') else company_name or sym,
                       'url':resp.url,'snapshot':_extract_snapshot(soup),'tables':classified,
                       'table_records':{k:_df_records(v) for k,v in classified.items()},
                       'announcements':_section_links(soup,'Announcements',100),
                       'news':_section_links(soup,'Company News',30)})
    # API-first data layers discovered from the browser.
    isin=_find_isin(sym,result.get('name'))
    if isin:
        result['mf_transactions']=fetch_mf_transactions(isin,page=1,page_size=100,timeout=timeout)
        # If the page itself did not expose MF Holdings, keep the raw API result
        # available to the UI rather than manufacturing a table.
    else:
        result['mf_transactions']={'status':'unavailable','reason':'No ISIN mapping available for this symbol. Set ARKA_SCANX_ISIN_<SYMBOL> in deployment secrets/environment.'}
    return result

"""
NSDL fortnightly sector-wise FPI investment data (equity, Rs crore). Parser carried over unchanged from the
previous rotation_engine.py.

What it is: recorded FPI net investment per NSDL sector for each fortnight, and FPI assets under custody.
What it is not: total institutional activity (no mutual funds, insurers or other domestic institutions).
Format: since 31 Aug 2024 the report has four header rows (period, currency, instrument group, instrument) and no
<thead>; it is read straight from the HTML (see parse_nsdl_sector_html).
Dates: each report is keyed by the END of its fortnight. NSDL publishes it later; the file does not carry its
publication date, so this data must not be used in a backtest as if it were known on the period-end date.
AUC changes mix price moves with flows and are not shown as buying or selling.
"""
from __future__ import annotations

import re
from html import unescape

import pandas as pd

NSDL_BASE = "https://www.fpi.nsdl.co.in/web/StaticReports/Fortnightly_Sector_wise_FII_Investment_Data/"


def nsdl_report_dates(n=24, today=None) -> list:
    """Fortnight end dates (15th and month end), newest first, at least 3 days old."""
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    out, m = [], today.to_period("M")
    while len(out) < n:
        me = m.to_timestamp(how="end").normalize()
        for d in (me, pd.Timestamp(m.year, m.month, 15)):
            if d <= today - pd.Timedelta(days=3) and len(out) < n:
                out.append(d)
        m -= 1
    return out


def nsdl_urls(d) -> list:
    d = pd.Timestamp(d)
    v = [d.strftime("%B"), d.strftime("%b"), d.strftime("%b").upper(), d.strftime("%B").upper()]
    if d.month == 9:
        v += ["Sept", "SEPT"]
    return [f"{NSDL_BASE}FIIInvestSector_{m}{d.day:02d}{d.year}.html" for m in dict.fromkeys(v)]


def _parse_mdy(mon, day, year):
    mon = {"Sept": "Sep", "SEPT": "SEP"}.get(mon.strip().rstrip("."), mon.strip().rstrip("."))
    for fmt in ("%B %d %Y", "%b %d %Y"):
        try:
            return pd.to_datetime(f"{mon.title()} {int(day)} {year}", format=fmt)
        except ValueError:
            continue
    return None


_TR_RX = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_TD_RX = re.compile(r"<t([dh])([^>]*)>(.*?)</t[dh]>", re.S | re.I)
_TABLE_RX = re.compile(r"<table[^>]*>(.*?)</table>", re.S | re.I)
_NOT_EQUITY = re.compile(r"mutual|alternative|\baif|hybrid|debt|total|solution|other", re.I)


def _cell_text(inner: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", inner))).replace("\xa0", " ").strip()


def _span(attrs: str, name: str) -> int:
    m = re.search(name + r"\s*=\s*['\"]?(\d+)", attrs, re.I)
    return max(1, int(m.group(1))) if m else 1


def _grid(table_html: str) -> list:
    """Rows of a table with colspan / rowspan expanded, the way a browser lays it out (each spanned cell repeated)."""
    grid, carry = [], {}                                    # carry: column -> [text, rows left]
    for tr in _TR_RX.findall(table_html):
        row, col = [], 0
        cells = [(_cell_text(inner), _span(attrs, "colspan"), _span(attrs, "rowspan")) for _, attrs, inner in _TD_RX.findall(tr)]
        k = 0
        while k < len(cells) or col in carry:
            if col in carry:
                text, left = carry[col]
                row.append(text)
                if left <= 1:
                    del carry[col]
                else:
                    carry[col] = [text, left - 1]
                col += 1
                continue
            text, cs, rs = cells[k]
            k += 1
            for _ in range(cs):
                row.append(text)
                if rs > 1:
                    carry[col] = [text, rs - 1]
                col += 1
        grid.append(row)
    return grid


def _value(raw: str):
    t = raw.replace(",", "").replace("\xa0", "").strip()
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()").strip()
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def parse_nsdl_sector_html(text: str) -> pd.DataFrame:
    """Long frame [date, sector, kind ('net' | 'auc'), value (₹ crore, equity)] from one NSDL report.

    Read straight from the HTML (no pandas.read_html, which gives up on the page's small side tables). The header
    rows are every row above the first sector row: the period ('AUC as on ...' / 'Net Investment ...'), the currency
    ('IN INR Cr.'), and since 31 Aug 2024 the instrument group (Equity, Debt, Hybrid, Mutual Funds, AIFs) above the
    instrument (Equity, Debt General Limit, ...). The FPI equity column is the INR column whose deepest header is
    'Equity' and that is not under Mutual Funds, AIFs, Debt, Hybrid or Total."""
    recs = []
    for table in _TABLE_RX.findall(text or ""):
        g = _grid(table)
        top = next((i for i, r in enumerate(g) if any(c.lower() in ("sector", "sectors") for c in r)), None)
        if top is None:
            continue
        sc = next(k for k, c in enumerate(g[top]) if c.lower() in ("sector", "sectors"))
        hdr = top                                           # the header ends at the row above the first sector row with a number
        while hdr + 1 < len(g) and (sc >= len(g[hdr + 1]) or g[hdr + 1][sc].lower() in ("sector", "sectors", "")
                                     or not any(_value(c) is not None for c in g[hdr + 1][sc + 1:])):
            hdr += 1
        head = g[:hdr + 1]
        n = max(len(r) for r in g)
        parts = [[r[i] for r in head if i < len(r) and r[i]] for i in range(n)]
        sec = next((i for i, p in enumerate(parts) if any(x.lower() in ("sector", "sectors") for x in p)), None)
        if sec is None:
            continue
        cols = []
        for i, p in enumerate(parts):
            if not p or p[-1].lower() != "equity" or not any("inr" in x.lower() for x in p) or any(_NOT_EQUITY.search(x) for x in p[:-1] if x.lower() != "equity"):
                continue
            head_txt = p[0]
            mm = re.search(r"Net\s+Investment\s+([A-Za-z]+)\.?\s+\d+\s*-\s*(\d+)\s*,?\s*(\d{4})", head_txt, re.I)
            kind = "net"
            if not mm:
                mm = re.search(r"AUC\s+as\s+on\s+([A-Za-z]+)\.?\s+(\d+)\s*,?\s*(\d{4})", head_txt, re.I)
                kind = "auc"
            if not mm:
                continue
            dt = _parse_mdy(mm.group(1), mm.group(2), mm.group(3))
            if dt is not None:
                cols.append((i, dt, kind))
        for r in g[hdr + 1:]:
            if sec >= len(r):
                continue
            s_ = r[sec].strip()
            if not s_ or s_.lower() == "nan":
                continue
            for i, dt, kind in cols:
                v = _value(r[i]) if i < len(r) else None
                if v is not None:
                    recs.append({"date": dt, "sector": s_, "kind": kind, "value": v})
        if recs:
            break
    if recs:
        return pd.DataFrame(recs).drop_duplicates(["date", "sector", "kind"], keep="first")
    return pd.DataFrame(columns=["date", "sector", "kind", "value"])


def nsdl_panels(parsed: list) -> tuple:
    frames = [p for p in parsed if p is not None and not p.empty]
    if not frames:
        return pd.DataFrame(), pd.DataFrame()
    df = pd.concat(frames).drop_duplicates(["date", "sector", "kind"], keep="first")
    df = df[~df["sector"].str.contains(r"total|sovereign|others", case=False, regex=True)]
    net = df[df.kind == "net"].pivot_table(index="date", columns="sector", values="value", aggfunc="last").sort_index()
    auc = df[df.kind == "auc"].pivot_table(index="date", columns="sector", values="value", aggfunc="last").sort_index()
    return net, auc


def norm_name(s: str) -> str:
    return re.sub(r"[^a-z]", "", str(s).lower())
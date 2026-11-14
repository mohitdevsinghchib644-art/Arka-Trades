"""
NSDL fortnightly sector-wise FPI investment data (equity, Rs crore). Parser carried over unchanged from the
previous rotation_engine.py.

What it is: recorded FPI net investment per NSDL sector for each fortnight, and FPI assets under custody.
What it is not: total institutional activity (no mutual funds, insurers or other domestic institutions).
Dates: each report is keyed by the END of its fortnight. NSDL publishes it later; the file does not carry its
publication date, so this data must not be used in a backtest as if it were known on the period-end date.
AUC changes mix price moves with flows and are not shown as buying or selling.
"""
from __future__ import annotations

import re
from io import StringIO

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


def parse_nsdl_sector_html(text: str) -> pd.DataFrame:
    """Long frame [date, sector, kind ('net' | 'auc'), value (₹ crore, equity)] from one NSDL report."""
    for kw in ({}, {"header": [0, 1, 2]}):
        try:
            tables = pd.read_html(StringIO(text), **kw)
        except Exception:
            tables = []
        recs = []
        for t in tables:
            cols = [" | ".join(str(x) for x in (c if isinstance(c, tuple) else (c,))) for c in t.columns]
            sec = next((c for c in cols if "sector" in c.lower()), None)
            if sec is None:
                continue
            t = t.copy()
            t.columns = cols
            for c in cols:
                parts = [p.strip() for p in c.split(" | ")]
                if parts[-1].lower() != "equity" or not any("inr" in p.lower() for p in parts):
                    continue
                head = parts[0]
                mm = re.search(r"Net\s+Investment\s+([A-Za-z]+)\.?\s+\d+\s*-\s*(\d+)\s*,?\s*(\d{4})", head, re.I)
                kind = "net"
                if not mm:
                    mm = re.search(r"AUC\s+as\s+on\s+([A-Za-z]+)\.?\s+(\d+)\s*,?\s*(\d{4})", head, re.I)
                    kind = "auc"
                if not mm:
                    continue
                dt = _parse_mdy(mm.group(1), mm.group(2), mm.group(3))
                if dt is None:
                    continue
                vals = pd.to_numeric(t[c].astype(str).str.replace(",", "", regex=False).str.strip(), errors="coerce")
                for s_, v in zip(t[sec].astype(str), vals):
                    s_ = s_.strip()
                    if s_ and s_.lower() != "nan" and pd.notna(v):
                        recs.append({"date": dt, "sector": s_, "kind": kind, "value": float(v)})
        if recs:
            return pd.DataFrame(recs).drop_duplicates(["date", "sector", "kind"], keep="last")
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
"""
fno_check.py — Arka Trades · F&O data source check (run once, then it can be deleted)

Before the F&O engine is built, this asks every free source whether it answers from the machine it runs on and
shows what the files really look like (column names, a few rows). Nothing is stored and no secret is needed.

Run:  python fno_check.py            (the GitHub workflow fno-check.yml does this for you)
The report is printed and, on GitHub, also shown on the run's Summary page.
"""
from __future__ import annotations

import io
import os
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone

import requests

IST = timezone(timedelta(hours=5, minutes=30))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
HEAD = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/all-reports-derivatives"}
ARCH = "https://nsearchives.nseindia.com"
MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def last_weekdays(n: int, today: date | None = None) -> list:
    d = today or datetime.now(IST).date()
    out = []
    while len(out) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out


def describe(content: bytes, lines: int = 4, width: int = 230) -> tuple:
    """-> (kind, first lines) for a csv, a zip holding a csv, json or html."""
    if content[:2] == b"PK":
        try:
            z = zipfile.ZipFile(io.BytesIO(content))
            name = z.namelist()[0]
            raw = z.read(name)
            text = raw.decode("utf-8", "replace").splitlines()
            return f"zip -> {name} ({len(raw):,} bytes, {len(text):,} lines)", [l[:width] for l in text[:lines]]
        except Exception as e:
            return f"zip that could not be opened ({type(e).__name__})", []
    text = content[:6000].decode("utf-8", "replace")
    low = text.lstrip().lower()
    if low.startswith("<!doctype") or low.startswith("<html"):
        return "html page (not data)", [" ".join(text.split())[:width]]
    if low.startswith("{") or low.startswith("["):
        return "json", [" ".join(text.split())[:width * 2]]
    return "text / csv", [l[:width] for l in text.splitlines()[:lines]]


def fetch(session, url: str, timeout: int = 25) -> dict:
    try:
        r = session.get(url, headers=HEAD, timeout=timeout)
        kind, sample = describe(r.content) if r.status_code == 200 else ("", [])
        return {"url": url, "status": r.status_code, "bytes": len(r.content), "kind": kind, "sample": sample}
    except Exception as e:
        return {"url": url, "status": f"{type(e).__name__}", "bytes": 0, "kind": "", "sample": []}


def first_ok(session, urls: list) -> dict:
    """Try the candidates in order (usually the same file for the last few trading days); keep the first that answers."""
    res = None
    for u in urls:
        res = fetch(session, u)
        if res["status"] == 200 and res["bytes"] > 200 and not res["kind"].startswith("html"):
            res["tried"] = urls.index(u) + 1
            return res
    res = res or {"url": urls[0] if urls else "", "status": "no candidates", "bytes": 0, "kind": "", "sample": []}
    res["tried"] = len(urls)
    return res


def tests(days: list) -> list:
    """(name, why it matters, [candidate urls])"""
    def ymd(d):
        return d.strftime("%Y%m%d")

    def dmy(d):
        return d.strftime("%d%m%Y")
    old = date(2024, 6, 28)
    return [
        ("F&O bhavcopy (current format)", "Every future and option: price, open interest, change in OI, volume. The backbone.",
         [f"{ARCH}/content/fo/BhavCopy_NSE_FO_0_0_0_{ymd(d)}_F_0000.csv.zip" for d in days]),
        ("F&O bhavcopy (format before July 2024)", "Lets the backtest reach further back than July 2024.",
         [f"{ARCH}/content/historical/DERIVATIVES/{old.year}/{MON[old.month - 1]}/fo{old.day:02d}{MON[old.month - 1]}{old.year}bhav.csv.zip"]),
        ("Participant-wise open interest", "FII, DII, Pro and Client long and short positions.",
         [f"{ARCH}/content/nsccl/fao_participant_oi_{dmy(d)}.csv" for d in days]),
        ("Participant-wise volume", "What each group bought and sold today.",
         [f"{ARCH}/content/nsccl/fao_participant_vol_{dmy(d)}.csv" for d in days]),
        ("Cash market bhavcopy", "Spot prices and delivery for the underlying stocks.",
         [f"{ARCH}/content/cm/BhavCopy_NSE_CM_0_0_0_{ymd(d)}_F_0000.csv.zip" for d in days]),
        ("F&O ban list", "Stocks above 95% of the market-wide position limit.", [f"{ARCH}/content/fo/fo_secban.csv"]),
        ("Lot sizes", "Contract sizes, to turn contracts into rupees.", [f"{ARCH}/content/fo/fo_mktlots.csv"]),
        ("Market-wide position limits", "How full each stock's limit is.",
         [f"{ARCH}/content/nsccl/mwpl_cli_{dmy(d)}.xls" for d in days[:2]] + [f"{ARCH}/archives/nsccl/mwpl/nseoi_{dmy(d)}.zip" for d in days[:2]]),
        ("India VIX history", "Volatility history from NSE.", [f"{ARCH}/content/indices/hist_vix_data.csv", f"{ARCH}/content/vix/histdata/hist_india_vix.csv"]),
    ]


def live_chain(session) -> list:
    """The live option chain needs cookies from the website first; it is the source most often blocked for servers."""
    out = []
    home = fetch(session, "https://www.nseindia.com/option-chain", timeout=20)
    out.append(("NSE website (cookies for the live chain)", home))
    for name, url in (("Live option chain, NIFTY (older address)", "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"),
                      ("Live option chain, contract info (newer address)", "https://www.nseindia.com/api/option-chain-contract-info?symbol=NIFTY"),
                      ("Live market status", "https://www.nseindia.com/api/marketStatus")):
        out.append((name, fetch(session, url, timeout=20)))
    return out


def run(session=None, today: date | None = None) -> tuple:
    session = session or requests.Session()
    days = last_weekdays(6, today)
    rows = []
    for name, why, urls in tests(days):
        res = first_ok(session, urls)
        rows.append((name, why, res))
    live = [(name, "Needed only for the live intraday view.", res) for name, res in live_chain(session)]
    return rows, live


def verdict(res: dict) -> str:
    if res["status"] == 200 and res["bytes"] > 200 and not res["kind"].startswith("html"):
        return "OK"
    if res["status"] == 200:
        return "ANSWERED, NOT DATA"
    return f"FAILED ({res['status']})"


def report(rows: list, live: list) -> str:
    out = ["# F&O data source check", f"Run at {datetime.now(IST):%d %b %Y %H:%M} IST", "",
           "| Source | Result | Size | What came back |", "| --- | --- | --- | --- |"]
    for name, _, res in rows + live:
        out.append(f"| {name} | {verdict(res)} | {res['bytes']:,} | {res['kind'] or '-'} |")
    out += ["", "## Details"]
    for name, why, res in rows + live:
        out += ["", f"### {name}: {verdict(res)}", why, f"Address tried last: `{res['url']}`" + (f" (attempt {res['tried']})" if res.get("tried") else "")]
        if res["sample"]:
            out += ["```"] + res["sample"] + ["```"]
    return "\n".join(out) + "\n"


def main() -> int:
    rows, live = run()
    text = report(rows, live)
    print(text)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

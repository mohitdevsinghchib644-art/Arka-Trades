"""
build_sector_map.py — OPTIONAL. Run on YOUR computer, not on Streamlit Cloud (NSE blocks cloud servers).

    python tools/build_sector_map.py          # every symbol in data/ind_*.csv   (~750 stocks, about 5 minutes)
    python tools/build_sector_map.py --all    # every NSE-listed equity          (~2,200 stocks, about 15 minutes)

Asks NSE for each stock's official classification (macro sector, sector, industry, basic industry) and writes
data/sector_map.csv. Commit that file to GitHub. Money Flow then
  * classifies any stock that is not in sector_map.py's curated list from NSE's Basic Industry, and
  * with --all, adds every listed stock to the universe (the liquidity filter still applies; capped at 1,500).

To force a stock into a particular group, fill the "group" column with a group name from sector_map.GROUPS.
Re-run every few months. Rows already fetched are kept, so an interrupted run can simply be started again.
"""
from __future__ import annotations

import argparse
import csv
import glob
import io
import os
import sys
import time

import requests

NSE = "https://www.nseindia.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
           "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9",
           "Referer": NSE + "/get-quotes/equity"}
OUT = os.path.join("data", "sector_map.csv")
FIELDS = ["symbol", "company", "macro", "sector", "industry", "basic_industry", "group"]


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    for url in (NSE, NSE + "/market-data/live-equity-market"):      # NSE sets the cookies its API needs here
        try:
            s.get(url, timeout=15)
        except requests.RequestException:
            pass
    return s


def symbols_from_index_files() -> list:
    out = []
    for path in sorted(glob.glob(os.path.join("data", "ind_*.csv"))):
        with open(path, newline="", encoding="utf-8", errors="ignore") as fh:
            for row in csv.DictReader(fh):
                row = {str(k).strip(): (v or "").strip() for k, v in row.items()}
                sym = row.get("Symbol", "").upper()
                if sym and not sym.startswith("DUMMY") and row.get("Series", "EQ") not in ("RR", "IV"):
                    out.append(sym)
    return list(dict.fromkeys(out))


def symbols_all_listed(s: requests.Session) -> list:
    r = s.get("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv", timeout=30)
    r.raise_for_status()
    out = []
    for row in csv.DictReader(io.StringIO(r.text)):
        row = {str(k).strip(): (v or "").strip() for k, v in row.items()}
        if row.get("SERIES") in ("EQ", "BE"):
            out.append(row["SYMBOL"].upper())
    return out


def fetch(s: requests.Session, sym: str) -> dict:
    r = s.get(NSE + "/api/quote-equity", params={"symbol": sym}, timeout=15)
    if r.status_code in (401, 403):
        raise PermissionError(r.status_code)
    r.raise_for_status()
    j = r.json()
    ii, info, meta = j.get("industryInfo") or {}, j.get("info") or {}, j.get("metadata") or {}
    return {"symbol": sym, "company": info.get("companyName", ""), "macro": ii.get("macro", ""),
            "sector": ii.get("sector", ""), "industry": ii.get("industry", "") or meta.get("industry", ""),
            "basic_industry": ii.get("basicIndustry", "")}


def load_existing() -> dict:
    if not os.path.exists(OUT):
        return {}
    with open(OUT, newline="", encoding="utf-8") as fh:
        return {r["symbol"].upper(): {k: r.get(k, "") for k in FIELDS} for r in csv.DictReader(fh) if r.get("symbol")}


def save(rows: dict):
    os.makedirs("data", exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for sym in sorted(rows):
            w.writerow({k: rows[sym].get(k, "") for k in FIELDS})


def main():
    ap = argparse.ArgumentParser(description="Download NSE industry classification into data/sector_map.csv")
    ap.add_argument("--all", action="store_true", help="every NSE-listed equity, not only the index files in data/")
    ap.add_argument("--delay", type=float, default=0.4, help="seconds between requests (be polite to NSE)")
    args = ap.parse_args()

    rows = load_existing()
    s = new_session()
    symbols = symbols_all_listed(s) if args.all else symbols_from_index_files()
    if not symbols:
        print("No symbols found. Put NSE index CSVs (ind_nifty500list.csv …) in data/ or run with --all.")
        sys.exit(1)
    todo = [x for x in symbols if not rows.get(x, {}).get("basic_industry")]
    print(f"{len(symbols)} symbols, {len(todo)} to fetch. Writing {OUT}")
    for i, sym in enumerate(todo, 1):
        for attempt in range(3):
            try:
                rows[sym] = {**fetch(s, sym), "group": rows.get(sym, {}).get("group", "")}
                break
            except PermissionError:
                time.sleep(3)
                s = new_session()
            except Exception as exc:
                if attempt == 2:
                    print(f"  skipped {sym}: {type(exc).__name__}")
                time.sleep(1.5)
        if i % 25 == 0:
            save(rows)
            print(f"  {i}/{len(todo)} done")
        time.sleep(args.delay)
    save(rows)
    filled = sum(1 for r in rows.values() if r.get("basic_industry"))
    print(f"Done: {filled} of {len(rows)} symbols have a Basic Industry. Commit {OUT} to GitHub.")


if __name__ == "__main__":
    main()

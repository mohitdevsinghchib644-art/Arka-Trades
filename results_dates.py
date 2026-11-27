"""Arka Trades · results dates.

When did each F&O company publish its quarterly results? This reads the public announcement list of BSE (category "Result")
for every F&O stock and writes  arka_data/results/dates.csv :  sym, when (Indian time), filings, headline.
The model cannot test anything around results (the drift after a good or bad result, or staying out just before one)
until this history exists. Public data only, no login, no secret.

    python results_dates.py                 first run: back to 2016. Later runs: the last 150 days, merged into the file.
    python results_dates.py --only SBIN,TCS a quick test
"""
from __future__ import annotations
import argparse, csv, datetime as dt, glob, io, json, os, sys, time, urllib.error, urllib.parse, urllib.request
from pathlib import Path

BSE = os.environ.get("ARKA_BSE", "https://api.bseindia.com/BseIndiaAPI/api")
NSE_LIST = os.environ.get("ARKA_NSE_LIST", "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv")
HEAD = {"user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36", "referer": "https://www.bseindia.com/", "origin": "https://www.bseindia.com", "accept": "application/json, text/plain, */*"}
OUT, GAP, FIRST = Path("arka_data") / "results", float(os.environ.get("ARKA_GAP", "0.6")), dt.date(2016, 1, 1)
STATS = {"calls": 0, "refused": 0}


def get(url: str, tries: int = 3) -> str | None:
    for k in range(tries):
        STATS["calls"] += 1
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEAD), timeout=40) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            if e.code in (401, 403): STATS["refused"] += 1
            time.sleep(2 * (k + 1))
        except Exception:
            time.sleep(2 * (k + 1))
    return None


def nse_isin() -> dict:
    txt = get(NSE_LIST); out = {}
    if txt:
        for r in csv.DictReader(io.StringIO(txt)):
            r = {k.strip().upper(): (v or "").strip() for k, v in r.items()}
            if r.get("SYMBOL") and r.get("ISIN NUMBER"): out[r["SYMBOL"]] = r["ISIN NUMBER"]
    return out


def bse_codes() -> tuple[dict, dict]:
    txt = get(f"{BSE}/ListofScripData/w?Group=&Scripcode=&industry=&segment=Equity&status=Active"); by_isin, by_id = {}, {}
    try: rows = json.loads(txt) if txt else []
    except Exception: rows = []
    for r in rows if isinstance(rows, list) else rows.get("Table", []):
        code = str(r.get("SCRIP_CD") or "").strip()
        if not code.isdigit(): continue
        if r.get("ISIN_NUMBER"): by_isin[str(r["ISIN_NUMBER"]).strip()] = code
        if r.get("scrip_id"): by_id[str(r["scrip_id"]).strip().upper()] = code
    return by_isin, by_id


def fno_symbols(root: Path) -> list:
    files = sorted(glob.glob(str(root / "arka_data" / "fno" / "u" / "*.csv"))); out = set()
    if not files: raise SystemExit("No F&O history found in arka_data/fno/u: run this from the repository.")
    for f in files[-3:]:
        with open(f, newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("kind") == "STK" and r.get("sym"): out.add(r["sym"])
    return sorted(out)


def when_of(r: dict) -> str:
    for k in ("DissemDT", "DT_TM", "NEWS_DT", "News_submission_dt"):
        v = str(r.get(k) or "")
        if len(v) >= 16 and v[:4].isdigit(): return v[:16].replace("T", " ")
    return ""


def is_result(r: dict) -> bool:
    cat, head = str(r.get("CATEGORYNAME") or "").strip().lower(), (str(r.get("HEADLINE") or "") + " " + str(r.get("NEWSSUB") or "") + " " + str(r.get("SUBCATNAME") or "")).lower()
    if "intimation" in head or "notice of board meeting" in head or "board meeting to consider" in head: return False       # the notice that results will come, not the results
    return cat == "result" or "financial result" in head or "results for the quarter" in head


def filings(code: str, d0: dt.date, d1: dt.date, cat: str) -> list | None:
    rows = []
    for page in range(1, 31):
        txt = get(f"{BSE}/AnnSubCategoryGetData/w?pageno={page}&strCat={urllib.parse.quote(cat)}&strPrevDate={d0:%Y%m%d}&strScrip={code}&strSearch=P&strToDate={d1:%Y%m%d}&strType=C&subcategory=-1"); time.sleep(GAP)
        if txt is None: return None
        try: tab = (json.loads(txt) or {}).get("Table") or []
        except Exception: return None
        rows += tab
        if len(tab) < 40: break
    return rows


def cluster(rows: list) -> list:
    """Several filings belong to one set of results (the results, a press release, a presentation): keep the first of each run of filings within 20 days."""
    out = []
    for w, h, n in sorted(rows):
        if out and (dt.datetime.fromisoformat(w) - dt.datetime.fromisoformat(out[-1][0])).days <= 20: out[-1][2] += n; continue
        out.append([w, h, n])
    return out


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--root", default="."); ap.add_argument("--only", default=""); ap.add_argument("--days", type=int, default=0); a = ap.parse_args()
    root = Path(a.root); f = root / OUT / "dates.csv"; have = {}
    if f.exists():
        with open(f, newline="") as fh:
            for r in csv.DictReader(fh): have.setdefault(r["sym"], []).append((r["when"], r.get("headline", ""), int(r.get("filings") or 1)))
    today = dt.date.today(); start = today - dt.timedelta(days=a.days) if a.days else (today - dt.timedelta(days=150) if have and not a.only else FIRST)
    syms = [x for x in a.only.upper().split(",") if x] or fno_symbols(root); isin = nse_isin(); by_isin, by_id = bse_codes()
    print(f"{len(syms)} symbols · NSE list {len(isin)} · BSE list {len(by_isin)} · from {start}")
    if not by_isin and not by_id:
        print("BSE did not answer the company list" + (" (refused: BSE is blocking this server)" if STATS["refused"] else "") + ". Nothing was changed."); return 1
    spans, d = [], start
    while d <= today: e = min(dt.date(d.year, 12, 31), today); spans.append((d, e)); d = e + dt.timedelta(days=1)
    mode, no_code, failed, got = "Result", [], [], 0
    for n, sym in enumerate(syms, 1):
        code = by_isin.get(isin.get(sym, "")) or by_id.get(sym)
        if not code: no_code.append(sym); continue
        rows, bad = [], False
        for d0, d1 in spans:
            tab = filings(code, d0, d1, mode)
            if tab is not None and not tab and mode == "Result" and got == 0 and n <= 3:       # the category filter may not be accepted: read everything once and filter here
                alt = filings(code, d0, d1, "-1")
                if alt: mode, tab = "-1", alt; print("  the category filter returned nothing; reading all announcements and filtering here")
            if tab is None: bad = True; break
            rows += [(when_of(r), str(r.get("HEADLINE") or r.get("NEWSSUB") or "").strip().replace("\n", " ")[:120], 1) for r in tab if is_result(r) and when_of(r)]
        if bad: failed.append(sym); continue
        old = [x for x in have.get(sym, []) if x[0][:10] < str(start)]; have[sym] = [tuple(x) for x in cluster(old + rows)] if rows or old else have.get(sym, []); got += len(rows)
        if n % 20 == 0 or n == len(syms): print(f"  {n}/{len(syms)} · {got} result filings read · {len(failed)} failed", flush=True)
        if n == 3 and got == 0 and len(failed) == 3:
            print("The first three companies all failed" + (" (refused: BSE is blocking this server)" if STATS["refused"] else "") + ". Stopping; nothing was changed."); return 1
    (root / OUT).mkdir(parents=True, exist_ok=True); total = 0
    with open(f, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n"); w.writerow(["sym", "when", "filings", "headline"])
        for sym in sorted(have):
            for when, h, n_ in sorted(have[sym]): w.writerow([sym, when, n_, h]); total += 1
    per = sorted((len(v) for v in have.values() if v)); info = {"built": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "symbols": len([v for v in have.values() if v]), "results": total, "median_per_symbol": per[len(per) // 2] if per else 0, "from": str(start), "no_bse_code": no_code, "failed": failed, "mode": mode, "calls": STATS["calls"]}
    (root / OUT / "status.json").write_text(json.dumps(info, indent=1))
    print(f"Done: {total} sets of results for {info['symbols']} companies (median {info['median_per_symbol']} each) · no BSE code for {len(no_code)}: {', '.join(no_code[:15])} · failed {len(failed)}: {', '.join(failed[:15])}")
    return 0 if total and len(failed) < max(5, len(syms) // 3) else 1


if __name__ == "__main__":
    sys.exit(main())

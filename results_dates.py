"""Arka Trades · results dates.

When did each F&O company publish its quarterly results, and how far was the profit per share from what analysts expected?
This reads Yahoo Finance's earnings dates for every F&O stock and writes  arka_data/results/dates.csv :
    sym, when (Indian time), eps_est, eps, surprise (%)
The model cannot test anything around results (the drift after a good or a bad result, staying out just before one) until this
history exists. Public data only, no login, no secret. (BSE refuses calls from GitHub's servers, so it is not used.)

Two ways to ask Yahoo are tried, and the one that answers is used for the rest of the run:
    1. the yfinance package (the same one "Market data" uses for prices)
    2. Yahoo's own earnings list, asked for directly

    python results_dates.py                 every F&O stock; rows already stored are kept and new ones are merged in
    python results_dates.py --only SBIN,TCS a quick test
"""
from __future__ import annotations
import argparse, csv, datetime as dt, glob, http.cookiejar, json, math, os, sys, time, urllib.error, urllib.request
from pathlib import Path

OUT, GAP = Path("arka_data") / "results", float(os.environ.get("ARKA_GAP", "1.2"))
FC, Q1, Q2 = os.environ.get("ARKA_YF_FC", "https://fc.yahoo.com/"), os.environ.get("ARKA_YF_Q1", "https://query1.finance.yahoo.com"), os.environ.get("ARKA_YF_Q2", "https://query2.finance.yahoo.com")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def num(x):
    try:
        v = float(x); return None if math.isnan(v) or math.isinf(v) else round(v, 4)
    except Exception:
        return None


def fno_symbols(root: Path) -> list:
    files = sorted(glob.glob(str(root / "arka_data" / "fno" / "u" / "*.csv"))); out = set()
    if not files: raise SystemExit("No F&O history found in arka_data/fno/u: run this from the repository.")
    for f in files[-3:]:
        with open(f, newline="") as fh:
            for r in csv.DictReader(fh):
                if r.get("kind") == "STK" and r.get("sym"): out.add(r["sym"])
    return sorted(out)


def via_package(sym: str) -> list:
    """yfinance: a table with one row per results date."""
    import yfinance as yf
    t = yf.Ticker(sym + ".NS"); df = t.get_earnings_dates(limit=100)
    if df is None or len(df) == 0: return []
    col = lambda *words: next((c for c in df.columns if all(w in str(c).lower() for w in words)), None)
    ce, ca, cs = col("estimate"), col("reported") or col("actual"), col("surprise"); rows = []
    for when, r in df.iterrows():
        try: w = when.tz_convert("Asia/Kolkata") if getattr(when, "tzinfo", None) is not None else when
        except Exception: w = when
        rows.append((w.strftime("%Y-%m-%d %H:%M"), num(r[ce]) if ce else None, num(r[ca]) if ca else None, num(r[cs]) if cs else None))
    return rows


class Direct:
    """Yahoo's earnings list asked for directly: a visit for the cookie, a token, then one request a stock."""
    def __init__(self):
        self.jar = http.cookiejar.CookieJar(); self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar)); self.crumb = ""

    def _open(self, url, data=None):
        req = urllib.request.Request(url, data=data, headers={"user-agent": UA, "accept": "application/json, text/plain, */*", **({"content-type": "application/json"} if data else {})})
        return self.op.open(req, timeout=40)

    def sign_in(self):
        try: self._open(FC).read()
        except urllib.error.HTTPError: pass                    # this address answers "not found" on purpose; the cookie still arrives
        self.crumb = self._open(Q2 + "/v1/test/getcrumb").read().decode().strip()
        if not self.crumb or "<" in self.crumb or len(self.crumb) > 40: raise RuntimeError("Yahoo did not give a token")

    def rows(self, sym: str) -> list:
        if not self.crumb: self.sign_in()
        body = {"size": 100, "query": {"operator": "eq", "operands": ["ticker", sym + ".NS"]}, "sortField": "startdatetime", "sortType": "DESC", "entityIdType": "earnings",
                "includeFields": ["startdatetime", "timeZoneShortName", "epsestimate", "epsactual", "epssurprisepct"]}
        d = json.loads(self._open(f"{Q1}/v1/finance/visualization?lang=en-US&region=US&crumb={urllib.request.quote(self.crumb)}", json.dumps(body).encode()).read().decode())
        res = ((d.get("finance") or {}).get("result") or [{}])[0]; doc = (res.get("documents") or [{}])[0]; cols = [str(c.get("id") or c.get("label") or "").lower().replace(" ", "") for c in doc.get("columns") or []]
        ix = lambda *names: next((i for i, c in enumerate(cols) if any(n in c for n in names)), None); iw, ie, ia, isp = ix("startdatetime", "eventstart"), ix("epsestimate"), ix("epsactual", "reportedeps"), ix("surprise")
        out = []
        for r in doc.get("rows") or []:
            if iw is None or not r[iw]: continue
            try: w = dt.datetime.fromisoformat(str(r[iw]).replace("Z", "+00:00")).astimezone(IST)
            except Exception: continue
            out.append((w.strftime("%Y-%m-%d %H:%M"), num(r[ie]) if ie is not None else None, num(r[ia]) if ia is not None else None, num(r[isp]) if isp is not None else None))
        return out


def ask(fn, sym: str):
    """-> (rows, None) or (None, reason). A request to slow down is waited out and tried again."""
    why = ""
    for k in range(4):
        try: return fn(sym), None
        except Exception as e:
            why = f"{type(e).__name__}: {str(e)[:140]}"
            if any(w in why.lower() for w in ("429", "too many", "rate", "timed out", "timeout", "temporar", "503", "502")): time.sleep(float(os.environ.get("ARKA_WAIT", "1")) * 30 * (k + 1)); continue
            return None, why
    return None, why


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--root", default="."); ap.add_argument("--only", default=""); ap.add_argument("--minutes", type=float, default=100); a = ap.parse_args()
    root = Path(a.root); f = root / OUT / "dates.csv"; have = {}
    if f.exists():
        with open(f, newline="") as fh:
            for r in csv.DictReader(fh): have.setdefault(r["sym"], {})[r["when"][:10]] = (r["when"], num(r.get("eps_est")), num(r.get("eps")), num(r.get("surprise")))
    syms = [x for x in a.only.upper().split(",") if x] or fno_symbols(root); direct = Direct(); ways = {"yfinance": via_package, "direct": direct.rows}; way = None; t0 = time.time()
    print(f"{len(syms)} symbols · {sum(len(v) for v in have.values())} results dates stored already")
    got, empty, failed, notes = 0, [], [], {}
    for n, sym in enumerate(syms, 1):
        if time.time() - t0 > a.minutes * 60: print("  stopped at the time limit; the rest comes next run"); break
        rows = None
        for name in ([way] if way else list(ways)):
            rows, why = ask(ways[name], sym)
            if rows is None:
                notes.setdefault(name, why)
                if not way: print(f"  {name} did not answer for {sym}: {why}")
                continue
            if rows and not way: way = name; print(f"  using {name}: {sym} has {len(rows)} dates, for example {rows[0]}")
            if rows or way: break
        time.sleep(GAP)
        if rows is None: failed.append(sym)
        elif not rows: empty.append(sym)
        else:
            cur = have.setdefault(sym, {})
            for w, e, x, s in rows: cur[w[:10]] = (w, e, x, s); got += 1
        if not way and n >= 8:
            print("Neither way of asking Yahoo gave any results date for the first eight companies. Nothing was changed.\n  " + "\n  ".join(f"{k}: {v}" for k, v in notes.items())); return 1
        if n % 25 == 0 or n == len(syms): print(f"  {n}/{len(syms)} · {got} dates read · {len(empty)} with none · {len(failed)} failed", flush=True)
    (root / OUT).mkdir(parents=True, exist_ok=True); total, years, with_s, first = 0, {}, 0, None; today = dt.datetime.now(IST).strftime("%Y-%m-%d")
    with open(f, "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n"); w.writerow(["sym", "when", "eps_est", "eps", "surprise"])
        for sym in sorted(have):
            for day in sorted(have[sym]):
                when, e, x, s = have[sym][day]; w.writerow([sym, when, "" if e is None else e, "" if x is None else x, "" if s is None else s]); total += 1
                if day <= today: years[day[:4]] = years.get(day[:4], 0) + 1; with_s += s is not None; first = day if first is None or day < first else first
    per = sorted(len(v) for v in have.values() if v); info = {"built": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "source": "Yahoo Finance (" + (way or "none") + ")", "symbols": len(per), "results": total, "median_per_symbol": per[len(per) // 2] if per else 0,
                                                          "first": first, "by_year": dict(sorted(years.items())), "with_surprise": with_s, "none": empty, "failed": failed}
    (root / OUT / "status.json").write_text(json.dumps(info, indent=1))
    print(f"Done: {total} results dates for {len(per)} companies (median {info['median_per_symbol']} each), from {first} · {with_s} have the surprise number · none for {len(empty)}: {', '.join(empty[:12])} · failed {len(failed)}: {', '.join(failed[:12])}")
    print("  by year: " + " ".join(f"{y}: {c}" for y, c in sorted(years.items())))
    return 0 if total and got else 1


if __name__ == "__main__":
    sys.exit(main())

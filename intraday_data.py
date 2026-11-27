"""Arka Trades · intraday candles collector.

Reads five-minute candles for every F&O stock (plus NIFTY and BANK NIFTY) from Angel One SmartAPI and keeps them as one
file a month:  m5/YYYY-MM.csv.gz  with the columns  sym,t,o,h,l,c,v   (t is Indian time, "YYYY-MM-DD HH:MM").
The files live on the "intraday-data" branch. They are the history any intraday rule has to be tested on before it is shown.

What this script may call, and nothing else:
    1. loginByPassword          to get a session
    2. getCandleData            historical candles
It never calls an order, GTT, funds, holdings or position endpoint, and never prints or stores the session.

    python intraday_data.py --out DIR              the last 7 days (the daily run)
    python intraday_data.py --out DIR --days 730   go back two years (the first run; about 20 minutes)

Needs four environment values (GitHub Actions secrets): ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PIN, ANGEL_TOTP_SECRET.
"""
from __future__ import annotations
import argparse, base64, csv, datetime as dt, glob, gzip, hashlib, hmac, json, os, struct, sys, time, urllib.request, urllib.error
from pathlib import Path

BASE = os.environ.get("ARKA_ANGEL_BASE", "https://apiconnect.angelone.in")
MASTER = os.environ.get("ARKA_ANGEL_MASTER", "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json")
INTERVAL, WINDOW, GAP = "FIVE_MINUTE", 90, float(os.environ.get("ARKA_ANGEL_GAP", "0.4"))    # Angel One allows 100 days a call for this interval and 3 calls a second
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
INDICES = {"NIFTY": ("NIFTY", "NIFTY50"), "BANKNIFTY": ("BANKNIFTY", "NIFTYBANK")}
ALLOWED = ("/rest/auth/angelbroking/user/v1/loginByPassword", "/rest/secure/angelbroking/historical/v1/getCandleData")


def totp(secret: str, at: float | None = None) -> str:
    """The six-digit code an authenticator app would show (RFC 6238), without any extra package."""
    s = "".join(secret.split()).upper(); key = base64.b32decode(s + "=" * (-len(s) % 8))
    mac = hmac.new(key, struct.pack(">Q", int((time.time() if at is None else at) // 30)), hashlib.sha1).digest(); o = mac[-1] & 15
    return f"{(struct.unpack('>I', mac[o:o + 4])[0] & 0x7FFFFFFF) % 1000000:06d}"


def call(path: str, body: dict, key: str, jwt: str = "") -> dict:
    if path not in ALLOWED:                                                 # a hard stop: this script can only log in and read candles
        raise RuntimeError("This script does not call " + path)
    head = {"content-type": "application/json", "accept": "application/json", "x-usertype": "USER", "x-sourceid": "WEB", "x-clientlocalip": "127.0.0.1",
            "x-clientpublicip": "127.0.0.1", "x-macaddress": "00:00:00:00:00:00", "x-privatekey": key, "user-agent": "Mozilla/5.0"}
    if jwt:
        head["authorization"] = "Bearer " + jwt
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers=head, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        txt = e.read().decode(errors="replace")[:200]
        try:
            return json.loads(txt)
        except Exception:
            return {"status": False, "message": f"HTTP {e.code}", "errorcode": "HTTP" + str(e.code)}


def login(env: dict) -> str:
    d = call(ALLOWED[0], {"clientcode": env["ANGEL_CLIENT_CODE"], "password": env["ANGEL_PIN"], "totp": totp(env["ANGEL_TOTP_SECRET"])}, env["ANGEL_API_KEY"])
    jwt = (d.get("data") or {}).get("jwtToken") if d.get("status") else None
    if not jwt:
        raise SystemExit("Angel One login refused: " + str(d.get("message") or d.get("errorcode") or "no reason given")[:120])
    return jwt


def tokens() -> tuple[dict, dict]:
    """{SYMBOL: token} for NSE shares and {NAME: token} for indices: the file "Market data" publishes, or Angel One's full list."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if repo and not os.environ.get("ARKA_ANGEL_MASTER"):
        try:
            with urllib.request.urlopen(f"https://raw.githubusercontent.com/{repo}/market-data/angel_tokens.json", timeout=30) as r:
                d = json.loads(r.read().decode())
            if len(d.get("s") or {}) > 500:
                return d["s"], d.get("i") or {}
        except Exception as e:
            print("  the published symbol list could not be read, using Angel One's full list:", str(e)[:80])
    req = urllib.request.Request(MASTER, headers={"user-agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
        rows = json.loads(r.read().decode())
    s, ix = {}, {}
    for x in rows:
        if x.get("exch_seg") != "NSE":
            continue
        sym, tk = str(x.get("symbol", "")), str(x.get("token", ""))
        if sym.endswith("-EQ") and tk.isdigit():
            s[sym[:-3]] = tk
        elif x.get("instrumenttype") == "AMXIDX" and tk.isdigit():
            for label in (sym, str(x.get("name", ""))):
                k = "".join(ch for ch in label.upper() if ch.isalnum())
                if k:
                    ix.setdefault(k, tk)
    return s, ix


def fno_symbols(root: Path) -> list[str]:
    """The stocks in the newest month of the F&O history (arka_data/fno/u)."""
    files = sorted(glob.glob(str(root / "arka_data" / "fno" / "u" / "*.csv")))
    if not files:
        raise SystemExit("No F&O history found in arka_data/fno/u: run this from the repository.")
    out = set()
    with open(files[-1], newline="") as f:
        for r in csv.DictReader(f):
            if r.get("kind") == "STK" and r.get("sym"):
                out.add(r["sym"])
    return sorted(out)


def candles(jwt: str, key: str, token: str, a: dt.datetime, b: dt.datetime) -> list | None:
    """One window of candles. None means the call failed for good; [] means there were none."""
    body = {"exchange": "NSE", "symboltoken": str(token), "interval": INTERVAL, "fromdate": a.strftime("%Y-%m-%d %H:%M"), "todate": b.strftime("%Y-%m-%d %H:%M")}
    for attempt in range(5):
        time.sleep(GAP)
        d = call(ALLOWED[1], body, key, jwt)
        if d.get("status"):
            return d.get("data") or []
        msg = str(d.get("message") or d.get("errorcode") or "")
        if "rate" in msg.lower() or "HTTP4" in str(d.get("errorcode")) or "HTTP5" in str(d.get("errorcode")) or not msg:
            time.sleep(1.5 * (attempt + 1)); continue                       # too fast or a passing error: wait and try again
        if "no data" in msg.lower():
            return []
        print("    refused:", msg[:100]); return None
    return None


def load_month(out: Path, m: str) -> dict:
    """One stored month: {"SYM,YYYY-MM-DD HH:MM": "o,h,l,c,v"}."""
    f = out / "m5" / f"{m}.csv.gz"; d = {}
    if f.exists():
        with gzip.open(f, "rt", newline="") as g:
            next(g, None)
            for line in g:
                p = line.rstrip("\n").split(",", 2)
                if len(p) == 3:
                    d[p[0] + "," + p[1]] = p[2]
    return d


def save_month(out: Path, m: str, d: dict) -> None:
    (out / "m5").mkdir(parents=True, exist_ok=True)
    with open(out / "m5" / f"{m}.csv.gz", "wb") as f:                       # mtime 0 keeps the file identical when nothing changed
        with gzip.GzipFile(fileobj=f, mode="wb", mtime=0) as g:
            g.write(b"sym,t,o,h,l,c,v\n")
            for k in sorted(d):
                g.write((k + "," + d[k] + "\n").encode())


def summary(out: Path) -> dict:
    rows, syms, first, last = {}, set(), None, None
    for f in sorted(glob.glob(str(out / "m5" / "*.csv.gz"))):
        n = 0
        with gzip.open(f, "rt", newline="") as g:
            next(g, None)
            for line in g:
                p = line.split(",", 2)
                if len(p) == 3:
                    n += 1; syms.add(p[0]); first = p[1] if first is None or p[1] < first else first; last = p[1] if last is None or p[1] > last else last
        rows[Path(f).name[:7]] = n
    return {"symbols": len(syms), "first": first, "last": last, "rows": rows}


def main() -> int:
    ap = argparse.ArgumentParser(); ap.add_argument("--out", required=True); ap.add_argument("--days", type=int, default=7); ap.add_argument("--root", default=".")
    ap.add_argument("--only", default="", help="comma-separated symbols, for a quick test"); a = ap.parse_args()
    env = {k: os.environ.get(k, "").strip() for k in ("ANGEL_API_KEY", "ANGEL_CLIENT_CODE", "ANGEL_PIN", "ANGEL_TOTP_SECRET")}
    missing = [k for k, v in env.items() if not v]
    if missing:
        raise SystemExit("Missing in GitHub Actions secrets: " + ", ".join(missing))
    out = Path(a.out); days = max(1, min(2000, a.days)); now = dt.datetime.now(IST).replace(tzinfo=None, second=0, microsecond=0)
    S, IX = tokens(); syms = [x for x in (a.only.upper().split(",") if a.only else fno_symbols(Path(a.root))) if x]
    want = [(s, S[s]) for s in syms if s in S] + ([] if a.only else [(n, next((IX[k] for k in keys if k in IX), None)) for n, keys in INDICES.items()])
    want = [(s, t) for s, t in want if t]; skipped = [s for s in syms if s not in S]
    print(f"{len(want)} symbols, {days} days back, five-minute candles" + (f" · no Angel One token for: {', '.join(skipped[:20])}" if skipped else ""))
    jwt = login(env); print("Logged in.")
    # Newest window first, every stock inside it: only about four months are ever held in memory, however far back the run goes.
    added, failed, done, end, floor = 0, set(), set(), now, now - dt.timedelta(days=days)
    while end > floor and len(done) + len(failed) < len(want):
        start = max(end - dt.timedelta(days=WINDOW), floor); M, touched, got = {}, set(), 0
        for sym, tk in want:
            if sym in done or sym in failed:
                continue
            rows = candles(jwt, env["ANGEL_API_KEY"], tk, start, end)
            if rows is None:
                failed.add(sym); continue
            if not rows and (now - end).days >= WINDOW:                     # nothing in a window well in the past: Angel One has nothing older for this stock
                done.add(sym); continue
            for r in rows:
                t = str(r[0])[:16].replace("T", " "); m = t[:7]; k = sym + "," + t; v = ",".join(f"{float(x):g}" for x in r[1:5]) + "," + str(int(float(r[5] or 0)))
                if m not in M:
                    M[m] = load_month(out, m)
                if M[m].get(k) != v:
                    M[m][k] = v; touched.add(m); got += 1
        for m in sorted(touched):
            save_month(out, m, M[m])
        added += got; print(f"  {start:%d %b %Y} to {end:%d %b %Y}: {got:,} candles new or changed · {len(done)} stocks have no older history · {len(failed)} failed")
        end = start
    info = {"built": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), "interval": INTERVAL, **summary(out), "added": added, "failed": sorted(failed), "no_token": skipped}
    (out / "m5").mkdir(parents=True, exist_ok=True); (out / "m5" / "index.json").write_text(json.dumps(info, indent=1))
    print(f"Done: {sum(info['rows'].values()):,} candles stored, {info['first']} to {info['last']}, {len(failed)} symbols failed" + (": " + ", ".join(sorted(failed)[:15]) if failed else ""))
    return 0 if len(failed) < max(5, len(want) // 4) else 1


if __name__ == "__main__":
    sys.exit(main())

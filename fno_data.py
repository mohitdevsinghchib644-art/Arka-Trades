"""
fno_data.py — Arka Trades · F&O data builder, v1

What it does
  Every evening it downloads NSE's free end-of-day derivatives files and reduces them to a small history the app can
  open instantly. Run by .github/workflows/fno-data.yml on GitHub's servers; the app itself never downloads these.

  Sources (all confirmed reachable by fno_check.py on 22 Nov 2026)
    * F&O bhavcopy, current "UDiFF" format (8 Jul 2024 onward) and the older format before that
    * Participant-wise open interest (FII, DII, Pro, Client)
    * F&O ban list

  What it keeps (folder data/fno/)
    u/YYYY-MM.csv         one row per underlying per day: futures price and open interest, rollover, put and call
                          open interest and volume, max pain, the biggest call and put strikes, implied volatility
                          (solved from option prices with Black-76), skew and the at-the-money straddle
    participants.csv      four rows a day: Client, DII, FII, Pro with their long and short contracts
    chain_latest.csv      the latest day's option chain near the money, for every underlying
    skipped.csv           weekdays with no file (holidays), so they are not asked for again
    meta.json             latest date, ban list, how many days are stored
  Plain CSV on purpose: each evening only appends rows, so the repository grows by a few kilobytes a day.

Run
  python fno_data.py                     the last few missing days (what the evening schedule does)
  python fno_data.py --years 5           build history, newest day first; safe to run again, it continues where it stopped
  python fno_data.py --years 5 --minutes 90      stop cleanly after 90 minutes

No secrets are used. Needs: requests, pandas, numpy.
"""
from __future__ import annotations

import io
import json
import math
import sys
import time
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

VERSION = 1
IST = timezone(timedelta(hours=5, minutes=30))
ARCH = "https://nsearchives.nseindia.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
HEAD = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/all-reports-derivatives"}
MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
UDIFF_FROM = date(2024, 7, 8)
ROOT = Path("data") / "fno"
RATE = 0.065                                # risk-free rate used to discount option prices
INDEX_SYMS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}

U_COLS = ["date", "sym", "kind", "spot", "fut", "fut_next", "m_exp", "dte", "lot", "fut_oi", "fut_oi_chg", "fut_oi_near", "fut_oi_next",
          "fut_vol", "ce_oi", "pe_oi", "ce_oi_chg", "pe_oi_chg", "ce_vol", "pe_vol", "m_ce_oi", "m_pe_oi", "max_pain", "call_wall",
          "put_wall", "call_wall_oi", "put_wall_oi", "iv", "iv_dte", "straddle", "iv_put", "iv_call", "w_exp", "w_ce_oi", "w_pe_oi",
          "w_max_pain", "w_call_wall", "w_put_wall"]
P_COLS = ["date", "who", "fi_long", "fi_short", "fs_long", "fs_short", "oi_call_long", "oi_put_long", "oi_call_short", "oi_put_short",
          "os_call_long", "os_put_long", "os_call_short", "os_put_short"]
C_COLS = ["sym", "exp", "strike", "ce_oi", "pe_oi", "ce_chg", "pe_chg", "ce_px", "pe_px", "ce_vol", "pe_vol"]


# ═════════════════════════ option maths (Black-76 on the futures price) ═════════════════════════
def _ncdf(x: float) -> float:
    return 0.5 * math.erfc(-x / math.sqrt(2.0))


def b76(F: float, K: float, T: float, s: float, call: bool, r: float = RATE) -> float:
    """Option price from the futures price F, strike K, years to expiry T and volatility s."""
    if T <= 0 or s <= 0:
        return max(0.0, (F - K) if call else (K - F))
    v = s * math.sqrt(T)
    d1 = (math.log(F / K) + 0.5 * s * s * T) / v
    d2 = d1 - v
    df = math.exp(-r * T)
    return df * (F * _ncdf(d1) - K * _ncdf(d2)) if call else df * (K * _ncdf(-d2) - F * _ncdf(-d1))


def implied_vol(price, F, K, T, call: bool, r: float = RATE):
    """Volatility that reproduces `price`; None when the price carries no information (at or below intrinsic value)."""
    try:
        price, F, K, T = float(price), float(F), float(K), float(T)
    except (TypeError, ValueError):
        return None
    if not (price > 0 and F > 0 and K > 0 and T > 0):
        return None
    intrinsic = math.exp(-r * T) * max(0.0, (F - K) if call else (K - F))
    if price <= intrinsic * 1.0005 + 1e-9:
        return None
    lo, hi = 0.005, 5.0
    if b76(F, K, T, hi, call, r) < price:
        return None
    for _ in range(64):
        mid = 0.5 * (lo + hi)
        if b76(F, K, T, mid, call, r) > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)


def max_pain(strikes: np.ndarray, ce_oi: np.ndarray, pe_oi: np.ndarray):
    """The settlement price at which option buyers together get paid the least."""
    if len(strikes) == 0 or (ce_oi.sum() + pe_oi.sum()) <= 0:
        return None
    s = strikes[:, None]
    k = strikes[None, :]
    pain = (np.maximum(s - k, 0.0) * ce_oi[None, :]).sum(axis=1) + (np.maximum(k - s, 0.0) * pe_oi[None, :]).sum(axis=1)
    return float(strikes[int(np.argmin(pain))])


# ═════════════════════════ reading NSE's files ═════════════════════════
def _unzip(content: bytes) -> bytes:
    z = zipfile.ZipFile(io.BytesIO(content))
    return z.read(z.namelist()[0])


def parse_udiff(csv_bytes: bytes) -> pd.DataFrame:
    """Current format -> common columns: typ sym exp strike opt close settle oi oi_chg vol und lot."""
    use = ["FinInstrmTp", "TckrSymb", "XpryDt", "StrkPric", "OptnTp", "ClsPric", "SttlmPric", "UndrlygPric", "OpnIntrst",
           "ChngInOpnIntrst", "TtlTradgVol", "NewBrdLotQty"]
    d = pd.read_csv(io.BytesIO(csv_bytes), usecols=lambda c: c.strip() in use, dtype=str)
    d.columns = [c.strip() for c in d.columns]
    out = pd.DataFrame({"typ": d["FinInstrmTp"].str.strip(), "sym": d["TckrSymb"].str.strip(),
                        "exp": pd.to_datetime(d["XpryDt"], errors="coerce").dt.date,
                        "strike": pd.to_numeric(d["StrkPric"], errors="coerce"), "opt": d["OptnTp"].fillna("").str.strip()})
    for a, b in (("close", "ClsPric"), ("settle", "SttlmPric"), ("und", "UndrlygPric"), ("oi", "OpnIntrst"), ("oi_chg", "ChngInOpnIntrst"),
                 ("vol", "TtlTradgVol"), ("lot", "NewBrdLotQty")):
        out[a] = pd.to_numeric(d[b], errors="coerce")
    return out[out["typ"].isin(["IDF", "STF", "IDO", "STO"]) & out["exp"].notna()].reset_index(drop=True)


_OLD_TYP = {"FUTIDX": "IDF", "FUTSTK": "STF", "OPTIDX": "IDO", "OPTSTK": "STO"}


def parse_old(csv_bytes: bytes) -> pd.DataFrame:
    """Format used before July 2024 -> the same common columns (it has no underlying price and no lot size)."""
    d = pd.read_csv(io.BytesIO(csv_bytes), dtype=str)
    d.columns = [c.strip() for c in d.columns]
    typ = d["INSTRUMENT"].str.strip().map(_OLD_TYP)
    out = pd.DataFrame({"typ": typ, "sym": d["SYMBOL"].str.strip(),
                        "exp": pd.to_datetime(d["EXPIRY_DT"].str.strip(), format="%d-%b-%Y", errors="coerce").dt.date,
                        "strike": pd.to_numeric(d["STRIKE_PR"], errors="coerce"),
                        "opt": d["OPTION_TYP"].fillna("").str.strip().replace({"XX": ""})})
    for a, b in (("close", "CLOSE"), ("settle", "SETTLE_PR"), ("oi", "OPEN_INT"), ("oi_chg", "CHG_IN_OI"), ("vol", "CONTRACTS")):
        out[a] = pd.to_numeric(d[b], errors="coerce")
    out["und"], out["lot"] = np.nan, np.nan
    return out[out["typ"].notna() & out["exp"].notna()].reset_index(drop=True)


def parse_bhav(content: bytes) -> pd.DataFrame:
    raw = _unzip(content) if content[:2] == b"PK" else content
    head = raw[:400].decode("utf-8", "replace")
    return parse_udiff(raw) if "FinInstrmTp" in head else parse_old(raw)


_P_KEYS = [("fi_long", ("future", "index", "long")), ("fi_short", ("future", "index", "short")), ("fs_long", ("future", "stock", "long")),
           ("fs_short", ("future", "stock", "short")), ("oi_call_long", ("option", "index", "call", "long")),
           ("oi_put_long", ("option", "index", "put", "long")), ("oi_call_short", ("option", "index", "call", "short")),
           ("oi_put_short", ("option", "index", "put", "short")), ("os_call_long", ("option", "stock", "call", "long")),
           ("os_put_long", ("option", "stock", "put", "long")), ("os_call_short", ("option", "stock", "call", "short")),
           ("os_put_short", ("option", "stock", "put", "short"))]


def parse_participants(csv_bytes: bytes, day: date) -> list:
    """-> rows [date, who, 12 numbers] for Client, DII, FII, Pro. [] when the layout is not recognised."""
    lines = [l for l in csv_bytes.decode("utf-8", "replace").splitlines() if l.strip()]
    hi = next((i for i, l in enumerate(lines) if l.lower().replace(" ", "").startswith("clienttype")), None)
    if hi is None:
        return []
    head = [h.strip().lower() for h in lines[hi].split(",")]
    pos = {}
    for key, words in _P_KEYS:
        for i, h in enumerate(head):
            parts = h.split()
            if all(w in parts for w in words) and len(parts) == len(words):
                pos[key] = i
                break
    if len(pos) != len(_P_KEYS):
        return []
    rows = []
    for l in lines[hi + 1:]:
        c = [x.strip() for x in l.split(",")]
        who = c[0].upper()
        if who not in ("CLIENT", "DII", "FII", "PRO"):
            continue
        try:
            vals = [int(float(c[pos[k]])) for k, _ in _P_KEYS]
        except (ValueError, IndexError):
            return []
        rows.append([day.isoformat(), {"CLIENT": "Client", "DII": "DII", "FII": "FII", "PRO": "Pro"}[who]] + vals)
    return rows if len(rows) == 4 else []


def parse_ban(csv_bytes: bytes) -> tuple:
    """-> (trade date text, [symbols])."""
    lines = [l.strip() for l in csv_bytes.decode("utf-8", "replace").splitlines() if l.strip()]
    when = lines[0].split("Date", 1)[-1].strip(" :") if lines else ""
    syms = []
    for l in lines[1:]:
        parts = [p.strip() for p in l.split(",")]
        if len(parts) >= 2 and parts[0].isdigit() and parts[1]:
            syms.append(parts[1].upper())
    return when, syms


# ═════════════════════════ one day -> one row per underlying ═════════════════════════
CH_COLS = ["strike", "ce_oi", "pe_oi", "ce_chg", "pe_chg", "ce_px", "pe_px", "ce_vol", "pe_vol"]
_EMPTY_CH = pd.DataFrame(columns=CH_COLS)


def _chains(opts: pd.DataFrame) -> dict:
    """All options of one day -> {(symbol, expiry): one row per strike with call and put side by side}.
    The price used for an option is its traded close, else the exchange's settlement price."""
    if opts.empty:
        return {}
    c, st_, v = (opts[k].fillna(0).to_numpy(float) for k in ("close", "settle", "vol"))
    o = opts.assign(px=np.where((v > 0) & (c > 0), c, np.where(st_ > 0, st_, np.where(c > 0, c, 0.0))))
    o = o[o["opt"].isin(["CE", "PE"])]
    g = o.groupby(["sym", "exp", "strike", "opt"], sort=True).agg(oi=("oi", "sum"), chg=("oi_chg", "sum"), px=("px", "max"), vol=("vol", "sum"))
    w = g.unstack("opt")
    flat = pd.DataFrame(index=w.index)
    for field in ("oi", "chg", "px", "vol"):
        for side, pre in (("CE", "ce"), ("PE", "pe")):
            flat[f"{pre}_{field}"] = w[(field, side)].fillna(0.0) if (field, side) in w.columns else 0.0
    flat = flat.reset_index()
    return {key: sub[CH_COLS].reset_index(drop=True) for key, sub in flat.groupby(["sym", "exp"], sort=False)}


def _iv_block(ch: pd.DataFrame, F: float, dte: int, otm: float) -> dict:
    """At-the-money implied volatility, the straddle as a share of the futures price, and out-of-the-money put and call volatility."""
    out = {"iv": None, "straddle": None, "iv_put": None, "iv_call": None}
    if ch.empty or not F or F <= 0:
        return out
    T = max(dte, 0.5) / 365.0
    both = ch[(ch["ce_px"] > 0) & (ch["pe_px"] > 0)]
    if not both.empty:
        r = both.iloc[(both["strike"] - F).abs().values.argmin()]
        ivs = [x for x in (implied_vol(r["ce_px"], F, r["strike"], T, True), implied_vol(r["pe_px"], F, r["strike"], T, False)) if x]
        if ivs and abs(r["strike"] / F - 1) < 0.06:
            out["iv"] = float(np.mean(ivs))
            out["straddle"] = float((r["ce_px"] + r["pe_px"]) / F)
    puts = ch[(ch["pe_px"] > 0) & (ch["strike"] < F)]
    if not puts.empty:
        r = puts.iloc[(puts["strike"] - F * (1 - otm)).abs().values.argmin()]
        if abs(r["strike"] / F - (1 - otm)) < otm * 0.6:
            out["iv_put"] = implied_vol(r["pe_px"], F, r["strike"], T, False)
    calls = ch[(ch["ce_px"] > 0) & (ch["strike"] > F)]
    if not calls.empty:
        r = calls.iloc[(calls["strike"] - F * (1 + otm)).abs().values.argmin()]
        if abs(r["strike"] / F - (1 + otm)) < otm * 0.6:
            out["iv_call"] = implied_vol(r["ce_px"], F, r["strike"], T, True)
    return out


def _walls(ch: pd.DataFrame) -> dict:
    if ch.empty or (ch["ce_oi"].sum() + ch["pe_oi"].sum()) <= 0:
        return {"ce_oi": 0.0, "pe_oi": 0.0, "max_pain": None, "call_wall": None, "put_wall": None, "call_wall_oi": None, "put_wall_oi": None}
    k = ch["strike"].to_numpy(float)
    c, p = ch["ce_oi"].to_numpy(float), ch["pe_oi"].to_numpy(float)
    ci, pi = int(np.argmax(c)), int(np.argmax(p))
    return {"ce_oi": float(c.sum()), "pe_oi": float(p.sum()), "max_pain": max_pain(k, c, p),
            "call_wall": float(k[ci]) if c[ci] > 0 else None, "put_wall": float(k[pi]) if p[pi] > 0 else None,
            "call_wall_oi": float(c[ci]) if c[ci] > 0 else None, "put_wall_oi": float(p[pi]) if p[pi] > 0 else None}


def _r(x, n):
    return None if x is None else round(float(x), n)


def reduce_day(df: pd.DataFrame, day: date) -> tuple:
    """-> (rows for U_COLS, latest-chain rows for C_COLS)."""
    rows, chain = [], []
    df = df[df["exp"] >= day]
    is_fut = df["typ"].isin(["IDF", "STF"])
    opts_all = df[~is_fut]
    chains = _chains(opts_all)
    tot = opts_all.groupby(["sym", "opt"])[["oi", "oi_chg", "vol"]].sum() if len(opts_all) else None
    exps = {}
    for sym, exp in chains:
        exps.setdefault(sym, []).append(exp)
    und_px = df[df["und"] > 0].groupby("sym")["und"].median()

    def total(sym, side, col):
        try:
            return float(tot.loc[(sym, side), col])
        except Exception:
            return 0.0
    for sym, futs in df[is_fut].sort_values("exp").groupby("sym", sort=True):
        kind = "IDX" if (futs["typ"].iloc[0] == "IDF" or sym in INDEX_SYMS) else "STK"
        near = futs.iloc[0]
        fut = float(near["close"]) if near["close"] and near["close"] > 0 else float(near["settle"] or 0)
        if not fut > 0:
            continue
        nxt = futs.iloc[1] if len(futs) > 1 else None
        fut_next = None
        if nxt is not None:
            fut_next = float(nxt["close"]) if nxt["close"] and nxt["close"] > 0 else (float(nxt["settle"]) if nxt["settle"] and nxt["settle"] > 0 else None)
        m_exp = near["exp"]
        dte = (m_exp - day).days
        spot = float(und_px[sym]) if sym in und_px.index else None
        lot = float(near["lot"]) if near["lot"] and near["lot"] > 0 else None
        m_ch = chains.get((sym, m_exp), _EMPTY_CH)
        mw = _walls(m_ch)
        # implied volatility from the front month, or the next month when the front month has under 5 days left
        iv_exp, iv_F = m_exp, fut
        if dte < 5 and nxt is not None and fut_next:
            iv_exp, iv_F = nxt["exp"], fut_next
        iv_dte = (iv_exp - day).days
        ivb = _iv_block(chains.get((sym, iv_exp), _EMPTY_CH), iv_F, iv_dte, 0.03 if kind == "IDX" else 0.05)
        # nearest expiry of any kind (the weekly one for indices)
        w_exp = min(exps[sym]) if sym in exps else None
        ww = {"ce_oi": None, "pe_oi": None, "max_pain": None, "call_wall": None, "put_wall": None}
        w_ch = None
        if w_exp is not None and w_exp != m_exp:
            w_ch = chains[(sym, w_exp)]
            ww = _walls(w_ch)
        rows.append([day.isoformat(), sym, kind, spot, fut, fut_next, m_exp.isoformat(), dte, lot,
                     float(futs["oi"].sum()), float(futs["oi_chg"].sum()), float(near["oi"] or 0), float(nxt["oi"] or 0) if nxt is not None else 0.0,
                     float(futs["vol"].sum()), total(sym, "CE", "oi"), total(sym, "PE", "oi"), total(sym, "CE", "oi_chg"), total(sym, "PE", "oi_chg"),
                     total(sym, "CE", "vol"), total(sym, "PE", "vol"), mw["ce_oi"], mw["pe_oi"], mw["max_pain"], mw["call_wall"], mw["put_wall"],
                     mw["call_wall_oi"], mw["put_wall_oi"], _r(ivb["iv"], 4), iv_dte, _r(ivb["straddle"], 5), _r(ivb["iv_put"], 4), _r(ivb["iv_call"], 4),
                     w_exp.isoformat() if (w_exp is not None and w_exp != m_exp) else None, ww["ce_oi"], ww["pe_oi"], ww["max_pain"],
                     ww["call_wall"], ww["put_wall"]])
        band = 0.10 if kind == "IDX" else 0.18
        for exp, ch in ((m_exp, m_ch), (w_exp, w_ch)):
            if ch is None or ch.empty:
                continue
            near_money = ch[(ch["strike"] >= fut * (1 - band)) & (ch["strike"] <= fut * (1 + band)) & ((ch["ce_oi"] + ch["pe_oi"]) > 0)]
            for r in near_money.itertuples(index=False):
                chain.append([sym, exp.isoformat(), r.strike, r.ce_oi, r.pe_oi, r.ce_chg, r.pe_chg, round(r.ce_px, 2), round(r.pe_px, 2), r.ce_vol, r.pe_vol])
    return rows, chain


# ═════════════════════════ storage ═════════════════════════
def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _fmt(df: pd.DataFrame) -> str:
    return df.to_csv(index=False, float_format="%.8g", lineterminator="\n")


class Book:
    """The stored history, loaded once, changed in memory, written back month by month."""

    def __init__(self, root: Path = ROOT):
        self.root = Path(root)
        self.u = {}                          # month -> DataFrame
        self.dirty = set()
        for f in sorted((self.root / "u").glob("*.csv")):
            try:
                self.u[f.name[:7]] = pd.read_csv(f, dtype={"date": str, "sym": str, "kind": str, "m_exp": str, "w_exp": str})
            except Exception:
                continue
        pf = self.root / "participants.csv"
        try:
            self.p = pd.read_csv(pf, dtype={"date": str, "who": str})
        except Exception:
            self.p = pd.DataFrame(columns=P_COLS)
        sf = self.root / "skipped.csv"
        try:
            self.skipped = dict(pd.read_csv(sf, dtype=str).values.tolist())
        except Exception:
            self.skipped = {}
        self.p_dirty = self.s_dirty = False
        self.chain = None                    # (date, rows) of the newest day handled in this run
        self.ban = None

    def days(self) -> set:
        out = set()
        for d in self.u.values():
            out.update(d["date"].unique().tolist())
        return out

    def p_days(self) -> set:
        return set(self.p["date"].unique().tolist())

    def add_day(self, day: date, rows: list):
        m = day.strftime("%Y-%m")
        new = pd.DataFrame(rows, columns=U_COLS)
        old = self.u.get(m)
        if old is not None and len(old):
            old = old[old["date"] != day.isoformat()]
            new = pd.concat([old, new], ignore_index=True)
        self.u[m] = new.sort_values(["date", "sym"]).reset_index(drop=True)
        self.dirty.add(m)

    def add_participants(self, rows: list):
        if not rows:
            return
        new = pd.DataFrame(rows, columns=P_COLS)
        self.p = pd.concat([self.p[self.p["date"] != rows[0][0]], new], ignore_index=True).sort_values(["date", "who"]).reset_index(drop=True)
        self.p_dirty = True

    def skip(self, day: date, why: str):
        self.skipped[day.isoformat()] = why
        self.s_dirty = True

    def save(self):
        for m in sorted(self.dirty):
            _write(self.root / "u" / f"{m}.csv", _fmt(self.u[m][U_COLS]))
        self.dirty.clear()
        self.root.mkdir(parents=True, exist_ok=True)
        if self.p_dirty:
            _write(self.root / "participants.csv", _fmt(self.p[P_COLS]))
            self.p_dirty = False
        if self.s_dirty:
            rows = sorted(self.skipped.items())
            _write(self.root / "skipped.csv", "date,why\n" + "".join(f"{d},{w}\n" for d, w in rows))
            self.s_dirty = False
        days = sorted(self.days())
        meta = {}
        try:
            meta = json.loads((self.root / "meta.json").read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        if self.chain is not None and (not meta.get("chain_date") or self.chain[0] >= meta["chain_date"]):
            _write(self.root / "chain_latest.csv", _fmt(pd.DataFrame(self.chain[1], columns=C_COLS)))
            meta["chain_date"] = self.chain[0]
        if self.ban is not None:
            meta["ban_for"], meta["ban"] = self.ban
        meta.update({"v": VERSION, "asof": days[-1] if days else None, "first": days[0] if days else None, "days": len(days),
                     "participant_days": len(self.p_days()), "built": datetime.now(IST).isoformat(timespec="seconds")})
        _write(self.root / "meta.json", json.dumps(meta, indent=1) + "\n")


# ═════════════════════════ downloading ═════════════════════════
def bhav_urls(day: date) -> list:
    new = f"{ARCH}/content/fo/BhavCopy_NSE_FO_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
    old = f"{ARCH}/content/historical/DERIVATIVES/{day.year}/{MON[day.month - 1]}/fo{day.day:02d}{MON[day.month - 1]}{day.year}bhav.csv.zip"
    return [new, old] if day >= UDIFF_FROM else [old, new]


def participant_url(day: date) -> str:
    return f"{ARCH}/content/nsccl/fao_participant_oi_{day:%d%m%Y}.csv"


class Net:
    """Polite downloader: one request at a time with a short pause; remembers when the server starts refusing."""

    def __init__(self, session=None, pause: float = 0.35):
        self.s = session or requests.Session()
        self.pause = pause
        self.refused = 0                     # refusals in a row (not counting "no such file")

    def get(self, url: str):
        """-> (bytes, None) · (None, 'missing') when the file does not exist · (None, 'refused') on anything else."""
        time.sleep(self.pause)
        try:
            r = self.s.get(url, headers=HEAD, timeout=40)
        except Exception:
            self.refused += 1
            return None, "refused"
        if r.status_code == 200 and len(r.content) > 50 and not r.content[:15].lstrip().lower().startswith((b"<!doctype", b"<html")):
            self.refused = 0
            return r.content, None
        if r.status_code == 404:
            self.refused = 0
            return None, "missing"
        self.refused += 1
        return None, "refused"


def weekdays_back(today: date, n_days: int) -> list:
    """Weekdays from `today` backwards, newest first."""
    out, d = [], today
    for _ in range(n_days):
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out


def build(book: Book, net: Net, today: date, lookback_days: int, minutes: float = 0, say=print, published: bool = True) -> dict:
    """Fill in every missing weekday in the window, newest first. Stops cleanly on the time budget or when NSE refuses."""
    t0 = time.time()
    have, have_p = book.days(), book.p_days()
    stats = {"added": 0, "participants": 0, "holidays": 0, "stopped": ""}
    todo = weekdays_back(today, lookback_days)
    if not published and todo and todo[0] == today:
        todo = todo[1:]
    for i, day in enumerate(todo):
        iso = day.isoformat()
        fresh = (today - day).days <= 6                      # recent "missing" answers are retried: files can be late
        if minutes and (time.time() - t0) > minutes * 60:
            stats["stopped"] = "time budget reached"
            break
        if net.refused >= 8:
            stats["stopped"] = "NSE stopped answering; run again later"
            break
        if iso not in have and not (iso in book.skipped and not fresh):
            content, why = None, "missing"
            for u in bhav_urls(day):
                content, why = net.get(u)
                if content is not None or why == "refused":
                    break
            if content is not None:
                try:
                    rows, chain = reduce_day(parse_bhav(content), day)
                except Exception as e:
                    rows, chain = [], []
                    say(f"{iso}: file could not be read ({type(e).__name__}: {str(e)[:80]})")
                if len(rows) >= 20:
                    book.add_day(day, rows)
                    have.add(iso)
                    stats["added"] += 1
                    if book.chain is None or iso > book.chain[0]:
                        book.chain = (iso, chain)
                    if stats["added"] % 25 == 0:
                        book.save()
                        say(f"{stats['added']} days stored, now at {iso}")
                else:
                    book.skip(day, "unreadable")
            elif why == "missing":
                stats["holidays"] += 1
                if not fresh:
                    book.skip(day, "no file")
        if iso in have and iso not in have_p and not (("p" + iso) in book.skipped and not fresh):
            content, why = net.get(participant_url(day))
            if content is not None:
                rows = parse_participants(content, day)
                if rows:
                    book.add_participants(rows)
                    have_p.add(iso)
                    stats["participants"] += 1
                else:
                    book.skipped["p" + iso] = "unreadable"
                    book.s_dirty = True
            elif why == "missing" and not fresh:
                book.skipped["p" + iso] = "no file"
                book.s_dirty = True
    content, _ = net.get(f"{ARCH}/content/fo/fo_secban.csv")
    if content is not None:
        book.ban = parse_ban(content)
    book.save()
    return stats


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])

    def opt(name, default):
        if name in args:
            try:
                return float(args[args.index(name) + 1])
            except (IndexError, ValueError):
                return default
        return default
    years, minutes = opt("--years", 0), opt("--minutes", 0)
    now = datetime.now(IST)
    published = now.hour >= 18                                # the day's files appear in the evening
    book = Book(ROOT)
    before = len(book.days())
    look = int(years * 366) + 12 if years > 0 else 12
    stats = build(book, Net(), now.date(), look, minutes, published=published)
    days = sorted(book.days())
    print(f"Stored days: {before} -> {len(days)}" + (f" ({days[0]} to {days[-1]})" if days else ""))
    print(f"Added {stats['added']} days, {stats['participants']} participant files; {stats['holidays']} weekdays had no file.")
    if stats["stopped"]:
        print("Stopped early:", stats["stopped"], "- run the workflow again to continue.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

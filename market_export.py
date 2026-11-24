"""
Arka Trades — market_export.py (v1)

Runs the same Python engines the Streamlit app uses and saves their results as small JSON files in arka_data/market/,
so the new app shows exactly the same readings without running Python itself. Started by .github/workflows/market-data.yml
after the market closes.

    python market_export.py regime            # Regime for Nifty 500, Nifty 200 and Nifty 100
    python market_export.py regime --out DIR

Only public market data is used (NSE index lists, Yahoo Finance prices). Nothing here touches a broker account.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import quant_data as qd
import regime_engine as rg

OUT = Path("arka_data/market")
REGIME_UNIVERSES = ["Nifty 500", "Nifty 200", "Nifty 100"]
SERIES = ["close", "ema50", "sma200", "score", "pct50", "pct200", "nh", "nl", "mcclellan"]


def _num(v, d=2):
    """JSON-safe number: NaN and infinity become null."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, d) if math.isfinite(f) else None


def _day(t) -> str:
    return pd.Timestamp(t).strftime("%Y-%m-%d")


def subset_panel(p: dict, members: list, name: str, note: str) -> dict:
    """A smaller universe cut out of a bigger panel. Same prices, same indicators, so the result equals a separate download."""
    keep = [j for j, s in enumerate(p["symbols"]) if s in set(members)]
    T, N = len(p["dates"]), len(p["symbols"])
    out = {}
    for k, v in p.items():
        if isinstance(v, np.ndarray) and v.ndim == 2 and v.shape == (T, N):
            out[k] = v[:, keep]
        else:
            out[k] = v
    out["symbols"] = [p["symbols"][j] for j in keep]
    out["industry"] = [p["industry"][j] for j in keep]
    out["name"], out["note"] = name, note
    out["missing"] = [s for s in (p.get("missing") or []) if s in set(members)]
    return out


def regime_json(p: dict) -> dict:
    """Everything the Regime page shows, computed by regime_engine exactly as in Streamlit."""
    df = rg.compute(p)
    if df.empty:
        raise RuntimeError("Not enough history to read the regime yet.")
    row = df.iloc[-1]
    eps = rg.episodes(df)
    state = row["state"]
    fwd = {}
    for hz in (10, 20, 60):
        ft = rg.forward_table(df, hz)
        fwd[str(hz)] = [{"state": r["state"], "days": int(r["days"]), **({k: _num(r[k]) for k in ("share", "nifty_avg", "nifty_up", "ew_avg", "ew_up", "worst_avg")}
                                                                       if r["days"] else {})} for _, r in ft.iterrows()]
    dd_days = df.index[-25:][df["dd_flag"].iloc[-25:].to_numpy()]
    ftds = df.index[df["ftd"].to_numpy()][-3:]
    keys = ["close", "ret", "score", "pct20", "pct50", "pct200", "nh", "nl", "dd_count", "vix", "vix_pct", "vix_chg", "adv", "dec", "mcclellan",
            "dd_from_hi", "ema21", "ema50", "sma200"] + [f"c_{k}" for k in rg.WEIGHTS]
    return {
        "universe": p.get("name", ""), "note": p.get("note", ""), "n": len(p["symbols"]), "missing": len(p.get("missing") or []),
        "built": p.get("built", ""), "asof": _day(df.index[-1]), "years": round(len(df) / 252, 1),
        "state": state, "exposure": int(rg.EXPOSURE[state] * 100), "guide": rg.GUIDE[state],
        "since": _day(eps[-1][1]), "days_in": int(eps[-1][3]),
        "row": {k: _num(row[k], 4) for k in keys if k in row.index},
        "weights": rg.WEIGHTS, "states": rg.STATES,
        "reasons": [[t, x] for t, x in rg.reasons(row, df)],
        "forward": fwd,
        "episodes": [[s, _day(a), _day(b), int(n), _num(mv)] for s, a, b, n, mv in eps[-10:][::-1]],
        "dd_days": [_day(x) for x in dd_days], "ftd": [_day(x) for x in ftds],
        "series": {"d": [_day(x) for x in df.index], "state": [rg.STATES.index(s) for s in df["state"]],
                   **{k: [_num(v, 2) for v in df[k].to_numpy()] for k in SERIES}},
    }


def run_regime(out: Path, panel=None) -> list:
    """Download Nifty 500 once, cut Nifty 200 and Nifty 100 out of it, write one file per universe."""
    out.mkdir(parents=True, exist_ok=True)
    big = panel or qd.load_panel("Nifty 500", None, 10, None, True)
    written = []
    for name in REGIME_UNIVERSES:
        if name == "Nifty 500":
            p = big
        else:
            members, note = qd.universe(name)
            p = subset_panel(big, list(members), name, note)
        try:
            data = regime_json(p)
        except Exception as exc:                                   # keep yesterday's file rather than writing a broken one
            print(f"{name}: skipped ({exc})", file=sys.stderr)
            continue
        f = out / f"regime_{name.replace(' ', '').lower()}.json"
        f.write_text(json.dumps(data, separators=(",", ":"), allow_nan=False))
        written.append(f.name)
        print(f"{name}: {data['state']} · score {data['row']['score']} · {data['n']} stocks · {f.stat().st_size // 1024} KB")
    meta = out / "meta.json"
    m = json.loads(meta.read_text()) if meta.exists() else {}
    m["regime"] = {"built": datetime.now(qd.IST).isoformat(timespec="minutes"), "files": written}
    meta.write_text(json.dumps(m, indent=1))
    return written


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["regime"])
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    got = run_regime(Path(a.out))
    sys.exit(0 if got else 1)

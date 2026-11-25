"""
Arka Trades · Options map export (fno_export.py)

Runs the F&O engine (fno_engine.py, unchanged) on the stored history and writes what the new app's "Options map" tab
shows, so the website never has to do the heavy work:

  arka_data/fno/om.json          both indexes (levels, option chains, six months of history) and the F&O stock table
  arka_data/fno/om/SYMBOL.json   one small file per F&O stock (levels, option chain, six months of history)

Run by the "F&O data" workflow right after fno_data.py. No secrets, no outside service.
    python fno_export.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import fno_engine as E

OUT = E.ROOT


def _clean(x):
    """Plain JSON: no NaN or infinity, numpy numbers as Python numbers."""
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if hasattr(x, "item") and not isinstance(x, (str, bytes)):
        try:
            x = x.item()
        except Exception:
            return str(x)
    if isinstance(x, float):
        return x if math.isfinite(x) else None
    if isinstance(x, (int, str, bool)) or x is None:
        return x
    return str(x)


def export(root=OUT) -> dict:
    res = E.run(root)
    if not res.get("ok") or not res.get("indexes"):
        print("Options map: no F&O history yet, nothing written.")
        return {"ok": False}
    idx = {s: {"sym": x["sym"], "name": x["name"], "levels": x["levels"], "chain": x.get("chain") or {}, "hist": x.get("hist") or {}} for s, x in res["indexes"].items()}
    stocks = [{k: s.get(k) for k in ("sym", "level", "verdict", "score", "rank", "on", "buildup", "levels")} for s in res.get("stocks", [])]
    main = _clean({"v": 1, "ok": True, "asof": res.get("asof"), "indexes": idx, "stocks": stocks})
    Path(root).mkdir(parents=True, exist_ok=True)
    (Path(root) / "om.json").write_text(json.dumps(main, separators=(",", ":"), allow_nan=False))
    folder = Path(root) / "om"
    folder.mkdir(exist_ok=True)
    keep = set()
    for s in stocks:
        d = E.stock_detail(res, s["sym"])
        if not d:
            continue
        name = s["sym"].replace("/", "_") + ".json"
        keep.add(name)
        (folder / name).write_text(json.dumps(_clean({"sym": s["sym"], "levels": d["levels"], "chain": d.get("chain"), "hist": d.get("hist") or {}}), separators=(",", ":"), allow_nan=False))
    for old in folder.glob("*.json"):                       # a stock that left F&O: its file goes too
        if old.name not in keep:
            old.unlink()
    print(f"Options map: {len(idx)} indexes, {len(keep)} stocks, prices to {res.get('asof')}.")
    return {"ok": True, "stocks": len(keep)}


if __name__ == "__main__":
    sys.exit(0 if export().get("ok") else 0)                # never fail the workflow: the page says when the file is missing

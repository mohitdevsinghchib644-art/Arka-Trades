"""
Arka Trades — F7 Market Breadth
Chartink-backed DAILY/EOD engine.

Source:
    Chartink Atlas dashboard 86550

The engine deliberately does NOT use Yahoo Finance/NSE OHLC downloads to
recalculate breadth. It requests the Chartink breadth widget data and stores
that result locally for a fast Streamlit UI.

Primary fields:
    Above20dma / Below20dma
    Above50dma / Below50dma
    Above200dma / Below200dma

The first explicit SYNC pulls the Chartink series. Opening F7 and changing
20/50/90/180 lookback only reads the local history file.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import requests
import streamlit as st


# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------

CHARTINK_DASHBOARD_URL = "https://chartink.com/dashboard/86550"
CHARTINK_WIDGET_URL = "https://chartink.com/widget/process"

IST = timezone(timedelta(hours=5, minutes=30))

CACHE_DIR = Path(".cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

HISTORY_FILE = CACHE_DIR / "chartink_breadth_86550.json"
RESPONSE_FILE = CACHE_DIR / "chartink_breadth_86550_raw.json"

CACHE_TTL_SECONDS = 10 * 60
MAX_LOCAL_ROWS = 500

# This query is used only when the dashboard configuration cannot be
# discovered from the public page. It still executes on Chartink, so the
# source remains Chartink rather than Yahoo/NSE calculations.
FALLBACK_QUERY = """select
    groupcount( {cash} 1 where latest close > latest sma( close , 20 ) ) as 'Above20dma',
    groupcount( {cash} 1 where latest close <= latest sma( close , 20 ) ) as 'Below20dma',
    groupcount( {cash} 1 where latest close > latest sma( close , 50 ) ) as 'Above50dma',
    groupcount( {cash} 1 where latest close <= latest sma( close , 50 ) ) as 'Below50dma',
    groupcount( {cash} 1 where latest close > latest sma( close , 200 ) ) as 'Above200dma',
    groupcount( {cash} 1 where latest close <= latest sma( close , 200 ) ) as 'Below200dma'""".strip()

TARGET_ALIASES = {
    "Above20dma": "above_20dma",
    "Below20dma": "below_20dma",
    "Above50dma": "above_50dma",
    "Below50dma": "below_50dma",
    "Above200dma": "above_200dma",
    "Below200dma": "below_200dma",
}

ALIASES_LOWER = {
    k.lower(): v
    for k, v in TARGET_ALIASES.items()
}


# ---------------------------------------------------------------------
# HTTP SESSION
# ---------------------------------------------------------------------

def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/153.0 Safari/537.36"
        ),
        "Accept-Language": "en-IN,en;q=0.9",
        "Referer": CHARTINK_DASHBOARD_URL,
    })
    return s


def _get_csrf_token(html: str) -> Optional[str]:
    m = re.search(
        r'<meta[^>]+name=["\']csrf-token["\'][^>]+content=["\']([^"\']+)',
        html,
        flags=re.I,
    )
    return m.group(1) if m else None


# ---------------------------------------------------------------------
# DISCOVER THE DASHBOARD QUERY
# ---------------------------------------------------------------------

def _decode_js_string(value: str) -> str:
    value = value.replace("\\/", "/")
    try:
        return bytes(value, "utf-8").decode("unicode_escape")
    except Exception:
        return value


def _discover_market_breadth_query(html: str) -> Optional[str]:
    """
    Try to locate the Market Breadth widget query embedded in the public
    dashboard configuration. Chartink can change its JS serialization, so
    several conservative patterns are attempted.
    """
    candidates: list[str] = []

    # 1) JSON-ish "query": "..."
    patterns = [
        r'["\']query["\']\s*:\s*["\']((?:\\.|[^"\'])+)["\']',
        r'query\s*[:=]\s*(["\'])(.*?)\1',
    ]

    for pattern in patterns:
        try:
            for m in re.finditer(pattern, html, flags=re.I | re.S):
                raw = m.group(2) if len(m.groups()) == 2 else m.group(1)
                q = _decode_js_string(raw)
                low = q.lower()
                if "groupcount" in low and "dma" in low:
                    candidates.append(q)
        except Exception:
            continue

    # 2) Search for strings containing all six aliases. This catches some
    # current Chartink serializations that do not use a simple query key.
    alias_needles = [x.lower() for x in TARGET_ALIASES]
    for needle in ["above20dma", "market breadth"]:
        idx = html.lower().find(needle)
        if idx >= 0:
            window = html[max(0, idx - 8000): idx + 12000]
            for m in re.finditer(
                r'query\s*[:=]\s*(["\'])(.*?)\1',
                window,
                flags=re.I | re.S,
            ):
                q = _decode_js_string(m.group(2))
                low = q.lower()
                if sum(a in low for a in alias_needles) >= 2 and "groupcount" in low:
                    candidates.append(q)

    # Prefer a candidate which actually mentions all target columns.
    def quality(q: str) -> tuple[int, int]:
        low = q.lower()
        return (
            sum(a in low for a in alias_needles),
            len(q),
        )

    if not candidates:
        return None

    candidates = sorted(candidates, key=quality, reverse=True)
    best = candidates[0]

    # Do not accidentally use a non-breadth query.
    if "groupcount" not in best.lower():
        return None

    return best.strip()


# ---------------------------------------------------------------------
# CHARTINK REQUEST
# ---------------------------------------------------------------------

@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def _fetch_chartink_raw_cached(cache_buster: int) -> tuple[Any, str]:
    """
    One Chartink request per TTL.

    The cache key is explicit so the Streamlit page never re-runs this on
    normal lookback changes.
    """
    del cache_buster

    s = _session()

    page = s.get(
        CHARTINK_DASHBOARD_URL,
        timeout=(8, 20),
    )
    page.raise_for_status()

    csrf = _get_csrf_token(page.text)
    if csrf:
        s.headers["X-CSRF-TOKEN"] = csrf

    query = _discover_market_breadth_query(page.text)
    source = "Chartink dashboard 86550 query"

    if not query:
        query = FALLBACK_QUERY
        source = "Chartink dashboard 86550 · Chartink groupcount fallback query"

    # Headers used by the browser for AJAX requests.
    s.headers.update({
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    })

    response = s.post(
        CHARTINK_WIDGET_URL,
        data={"query": query},
        timeout=(8, 35),
    )
    response.raise_for_status()

    try:
        payload = response.json()
    except Exception:
        snippet = response.text[:500].replace("\n", " ")
        raise RuntimeError(
            f"Chartink did not return JSON (HTTP {response.status_code}). Response: {snippet}"
        )

    # Chartink has used both object and array-like response envelopes over
    # time. Do not reject a valid JSON list here; the normalizer handles it.
    if not isinstance(payload, (dict, list)):
        raise RuntimeError(
            f"Chartink returned unsupported JSON type: {type(payload).__name__}"
        )

    return payload, source


def fetch_chartink_breadth(force: bool = False) -> tuple[pd.DataFrame, str]:
    """Fetch and normalize the Market Breadth series from Chartink."""
    if force:
        _fetch_chartink_raw_cached.clear()

    payload, source = _fetch_chartink_raw_cached(
        int(datetime.now(IST).timestamp() // CACHE_TTL_SECONDS)
    )

    df = _normalise_chartink_response(payload)

    if df.empty:
        raise RuntimeError(
            "Chartink responded, but no Above/Below DMA series could be parsed. "
            "Open dashboard 86550 and verify the Market Breadth widget is available."
        )

    # Store the raw response for debugging without using it as the UI source.
    try:
        RESPONSE_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, default=str)
        )
    except Exception:
        pass

    return df, source


# ---------------------------------------------------------------------
# CHARTINK RESPONSE PARSER
# ---------------------------------------------------------------------

def _flatten_aliases(meta: dict[str, Any]) -> list[str]:
    raw = meta.get("columnAliases") or meta.get("columnNames") or []
    if isinstance(raw, dict):
        raw = list(raw.values())
    return [str(x) for x in raw]


def _normalise_times(values: Any) -> pd.DatetimeIndex:
    if values is None:
        return pd.DatetimeIndex([])

    if not isinstance(values, (list, tuple)):
        values = [values]

    vals: list[Any] = []
    for v in values:
        try:
            x = int(v)
            # Chartink returns milliseconds in the documented response shape.
            if x > 10**11:
                vals.append(pd.to_datetime(x, unit="ms", utc=True))
            else:
                vals.append(pd.to_datetime(x, unit="s", utc=True))
        except Exception:
            vals.append(v)

    dt = pd.to_datetime(vals, errors="coerce", utc=True)
    if len(dt):
        # Chartink timestamps shown in the UI are usually session timestamps;
        # keeping UTC here avoids accidental host-local timezone conversion.
        return dt.tz_convert(IST).tz_localize(None)
    return pd.DatetimeIndex([])


def _find_alias_series(obj: Any, aliases: list[str]) -> dict[str, list[Any]]:
    """Recursively search dict/list response shapes for alias-keyed arrays."""
    target = {a.lower(): a for a in aliases}
    found: dict[str, list[Any]] = {}

    def visit(node: Any) -> None:
        if len(found) == len(aliases):
            return

        if isinstance(node, dict):
            for k, v in node.items():
                kl = str(k).strip().lower()
                if kl in target and isinstance(v, (list, tuple)):
                    found[target[kl]] = list(v)
            for v in node.values():
                visit(v)

        elif isinstance(node, list):
            for item in node:
                visit(item)

    visit(obj)
    return found


def _normalise_chartink_response(payload: Any) -> pd.DataFrame:
    """Normalize Chartink's trend response.

    The important Chartink shape is:
        metaData[0].columnAliases
        metaData[0].tradeTimes
        groupData[*].results[*][alias] -> series

    Some responses put the same objects under ``data`` or return a list
    envelope, so those forms are handled too.
    """
    root = payload
    if isinstance(root, list):
        # Prefer a dict element containing Chartink metadata/groupData.
        dict_items = [x for x in root if isinstance(x, dict)]
        if dict_items:
            root = next(
                (x for x in dict_items if "metaData" in x or "groupData" in x or "data" in x),
                dict_items[0],
            )
        else:
            return pd.DataFrame()

    if not isinstance(root, dict):
        return pd.DataFrame()

    meta_list = root.get("metaData") or root.get("metadata") or []
    meta = meta_list[0] if isinstance(meta_list, list) and meta_list else {}
    if not isinstance(meta, dict):
        meta = {}

    aliases = _flatten_aliases(meta)
    if not aliases:
        aliases = list(TARGET_ALIASES.keys())

    times = _normalise_times(
        meta.get("tradeTimes")
        or meta.get("trade_times")
        or root.get("tradeTimes")
    )

    # First try the normal Chartink groupData structure.
    series: dict[str, list[Any]] = {}
    group_data = root.get("groupData")
    if group_data is not None:
        series.update(_find_alias_series(group_data, aliases))
        if not series:
            series.update(_find_alias_series(group_data, list(TARGET_ALIASES)))

    # Then try the generic data field used by some widget versions.
    if not series and root.get("data") is not None:
        series.update(_find_alias_series(root.get("data"), aliases))
        if not series:
            series.update(_find_alias_series(root.get("data"), list(TARGET_ALIASES)))

    # Finally search the entire response recursively.
    if not series:
        series.update(_find_alias_series(root, aliases))
        if not series:
            series.update(_find_alias_series(root, list(TARGET_ALIASES)))

    canonical: dict[str, list[Any]] = {}
    for raw_alias, values in series.items():
        key = str(raw_alias).strip().lower().replace(" ", "")
        # tolerate Chartink variations such as Above 20dma / above20dma
        normalized_key = re.sub(r"[^a-z0-9]", "", key)
        for target, col in ALIASES_LOWER.items():
            if normalized_key == re.sub(r"[^a-z0-9]", "", target):
                canonical[col] = list(values)
                break

    if not canonical:
        return pd.DataFrame()

    lengths = [len(v) for v in canonical.values() if isinstance(v, list)]
    if not lengths:
        return pd.DataFrame()
    n = min(lengths)

    # Chartink trend data should have one tradeTimes entry per value.
    if len(times) != n:
        return pd.DataFrame()

    data: dict[str, Any] = {"date": times[:n]}
    for col in TARGET_ALIASES.values():
        values = canonical.get(col)
        if values is None:
            data[col] = [pd.NA] * n
        else:
            data[col] = pd.to_numeric(
                pd.Series(values[:n]), errors="coerce"
            ).round().astype("Int64")

    df = pd.DataFrame(data)
    numeric_cols = list(TARGET_ALIASES.values())
    df = df.dropna(subset=numeric_cols, how="all")
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    df = df.sort_values("date").drop_duplicates("date", keep="last")
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------
# LOCAL HISTORY
# ---------------------------------------------------------------------

def _write_history(df: pd.DataFrame, source: str) -> None:
    clean = df.copy()
    clean = clean.sort_values("date")

    records: list[dict[str, Any]] = []
    for _, row in clean.iterrows():
        rec = {
            "date": (
                pd.Timestamp(row["date"]).strftime("%Y-%m-%d")
                if pd.notna(row["date"])
                else None
            ),
            "above_20dma": _as_int_or_none(row.get("above_20dma")),
            "below_20dma": _as_int_or_none(row.get("below_20dma")),
            "above_50dma": _as_int_or_none(row.get("above_50dma")),
            "below_50dma": _as_int_or_none(row.get("below_50dma")),
            "above_200dma": _as_int_or_none(row.get("above_200dma")),
            "below_200dma": _as_int_or_none(row.get("below_200dma")),
            "source": source,
        }
        if rec["date"]:
            records.append(rec)

    records = records[-MAX_LOCAL_ROWS:]

    HISTORY_FILE.write_text(
        json.dumps(records, ensure_ascii=False, indent=2)
    )


def _as_int_or_none(value: Any) -> Optional[int]:
    try:
        if pd.isna(value):
            return None
        return int(round(float(value)))
    except Exception:
        return None


def load_history() -> pd.DataFrame:
    if not HISTORY_FILE.exists():
        return pd.DataFrame()

    try:
        rows = json.loads(HISTORY_FILE.read_text())
        if not isinstance(rows, list) or not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(
            df["date"],
            errors="coerce",
        )
        df = df.dropna(subset=["date"])
        df = df.sort_values("date").reset_index(drop=True)

        for c in (
            "above_20dma", "below_20dma",
            "above_50dma", "below_50dma",
            "above_200dma", "below_200dma",
        ):
            if c in df.columns:
                df[c] = pd.to_numeric(
                    df[c],
                    errors="coerce",
                ).round().astype("Int64")

        return df

    except Exception:
        return pd.DataFrame()


# ---------------------------------------------------------------------
# SYNC
# ---------------------------------------------------------------------

def sync_chartink(force: bool = True) -> dict[str, Any]:
    """Pull Chartink history once and merge it into Arka's local history."""
    df, source = fetch_chartink_breadth(force=force)

    if df.empty:
        raise RuntimeError("Chartink returned no breadth history.")

    existing = load_history()

    if existing.empty:
        merged = df.copy()
    else:
        # Chartink data is authoritative for these fields. New data replaces
        # existing rows with the same session date.
        merged = pd.concat(
            [existing, df],
            ignore_index=True,
        )
        merged = (
            merged
            .sort_values("date")
            .drop_duplicates("date", keep="last")
        )

    _write_history(merged, source)

    return {
        "rows": len(df),
        "local_rows": len(merged),
        "latest_date": (
            pd.Timestamp(merged.iloc[-1]["date"]).strftime("%d %b %Y")
            if len(merged)
            else "-"
        ),
        "source": source,
    }


# ---------------------------------------------------------------------
# COMPATIBILITY HELPERS
# ---------------------------------------------------------------------

def get_nse_universe(*_args, **_kwargs):
    """
    Compatibility shim for older breadth_page imports.
    Market breadth no longer downloads an NSE universe directly.
    """
    return [], "Chartink Market Breadth source"


def compute_breadth_snapshot(*_args, **_kwargs) -> dict[str, Any]:
    """Return the latest LOCAL Chartink observation only."""
    history = load_history()
    if history.empty:
        return {"error": "No local Chartink breadth history. Sync Chartink first."}

    row = history.iloc[-1]
    result: dict[str, Any] = {
        "date": pd.Timestamp(row["date"]).strftime("%d %b %Y"),
        "source": row.get("source", "Chartink dashboard 86550"),
    }

    for c in (
        "above_20dma", "below_20dma",
        "above_50dma", "below_50dma",
        "above_200dma", "below_200dma",
    ):
        result[c] = _as_int_or_none(row.get(c))

    for ma in (20, 50, 200):
        a = result[f"above_{ma}dma"]
        b = result[f"below_{ma}dma"]
        result[f"above_{ma}dma_denom"] = (
            (a or 0) + (b or 0)
        )

    return result


if __name__ == "__main__":
    print(sync_chartink(force=True))

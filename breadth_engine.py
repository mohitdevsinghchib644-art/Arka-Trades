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
FALLBACK_QUERY = """
select
    groupcount( {cash} 1 where close > sma( close , 20 ) ) as 'Above20dma',
    groupcount( {cash} 1 where close <= sma( close , 20 ) ) as 'Below20dma',
    groupcount( {cash} 1 where close > sma( close , 50 ) ) as 'Above50dma',
    groupcount( {cash} 1 where close <= sma( close , 50 ) ) as 'Below50dma',
    groupcount( {cash} 1 where close > sma( close , 200 ) ) as 'Above200dma',
    groupcount( {cash} 1 where close <= sma( close , 200 ) ) as 'Below200dma'
""".strip()

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
def _fetch_chartink_raw_cached(cache_buster: int) -> tuple[dict[str, Any], str]:
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

    payload = response.json()

    if not isinstance(payload, dict):
        raise RuntimeError("Chartink returned an unexpected response format.")

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


def _normalise_chartink_response(payload: dict[str, Any]) -> pd.DataFrame:
    meta_list = payload.get("metaData") or payload.get("metadata") or []
    meta = meta_list[0] if isinstance(meta_list, list) and meta_list else {}

    aliases = _flatten_aliases(meta)
    aliases = [a for a in aliases if a]

    if not aliases:
        aliases = list(TARGET_ALIASES.keys())

    times = _normalise_times(
        meta.get("tradeTimes")
        or meta.get("trade_times")
        or payload.get("tradeTimes")
    )

    raw = _find_alias_series(payload, aliases)

    # Fallback: find aliases case-insensitively even if Chartink changes their
    # exact capitalization/spacing.
    canonical: dict[str, list[Any]] = {}
    for raw_alias, values in raw.items():
        key = raw_alias.strip().lower().replace(" ", "")
        if key in ALIASES_LOWER:
            canonical[ALIASES_LOWER[key]] = values

    if not canonical:
        # Search under all target aliases explicitly.
        raw2 = _find_alias_series(payload, list(TARGET_ALIASES))
        for raw_alias, values in raw2.items():
            canonical[TARGET_ALIASES.get(raw_alias, raw_alias)] = values

    if not canonical:
        return pd.DataFrame()

    # Determine the usable series length.
    lengths = [len(v) for v in canonical.values() if isinstance(v, list)]
    if not lengths:
        return pd.DataFrame()

    n = min(lengths)

    if len(times) != n:
        # If the response shape does not provide timestamps, align to the
        # number of values and leave date blank rather than inventing dates.
        times = pd.DatetimeIndex([pd.NaT] * n)
    else:
        times = times[:n]

    data: dict[str, Any] = {"date": times}

    for col in (
        "above_20dma",
        "below_20dma",
        "above_50dma",
        "below_50dma",
        "above_200dma",
        "below_200dma",
    ):
        values = canonical.get(col)
        if values is None:
            data[col] = [pd.NA] * n
        else:
            data[col] = pd.to_numeric(
                pd.Series(values[:n]),
                errors="coerce",
            ).round().astype("Int64")

    df = pd.DataFrame(data)

    # Remove rows which have no DMA counts at all.
    numeric_cols = [c for c in data if c != "date"]
    df = df.dropna(
        subset=numeric_cols,
        how="all",
    )

    if "date" in df.columns:
        df["date"] = pd.to_datetime(
            df["date"],
            errors="coerce",
        )
        df = df.sort_values("date")

    df = df.drop_duplicates(
        subset=["date"],
        keep="last",
    )

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

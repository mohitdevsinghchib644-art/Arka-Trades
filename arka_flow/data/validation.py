"""
Validation. Every series that enters the engine gets a QualityRecord; nothing is repaired silently.

Checks
  duplicate dates            dropped (last kept) and counted
  unsorted dates             sorted and flagged
  non-positive prices        set to NaN and counted (never to zero)
  missing values             counted, left as NaN
  one-session jumps          moves larger than JUMP_PCT are LISTED for corporate-action review, not changed
  staleness                  last date vs the latest completed session
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta, timezone

import pandas as pd

from ..config import JUMP_PCT, SESSION_CLOSE, SESSION_OPEN, SESSION_SETTLE_MIN

IST = timezone(timedelta(hours=5, minutes=30))


@dataclass
class QualityRecord:
    series_id: str
    source: str
    frequency: str = "1d"
    rows: int = 0
    first_date: str | None = None
    last_date: str | None = None
    duplicates_dropped: int = 0
    was_unsorted: bool = False
    non_positive: int = 0
    missing: int = 0
    jumps: list = field(default_factory=list)
    adjusted: str = "unknown"          # "adjusted" | "unadjusted" | "unknown"
    stale_sessions: int | None = None
    status: str = "ok"                 # ok | warning | failed
    notes: list = field(default_factory=list)
    checked_at: str = field(default_factory=lambda: datetime.now(IST).isoformat(timespec="seconds"))

    def to_dict(self):
        return asdict(self)


def clean_series(s: pd.Series, series_id: str, source: str, adjusted: str = "unknown") -> tuple[pd.Series, QualityRecord]:
    q = QualityRecord(series_id=series_id, source=source, adjusted=adjusted)
    if s is None or len(s) == 0:
        q.status, q.notes = "failed", ["no data returned"]
        return pd.Series(dtype=float), q
    x = pd.to_numeric(s, errors="coerce")
    idx = pd.to_datetime(x.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    x.index = idx.normalize()
    if not x.index.is_monotonic_increasing:
        q.was_unsorted = True
        x = x.sort_index()
    dup = x.index.duplicated(keep="last")
    q.duplicates_dropped = int(dup.sum())
    x = x[~dup]
    bad = x <= 0
    q.non_positive = int(bad.sum())
    x = x.mask(bad)
    q.missing = int(x.isna().sum())
    r = (x / x.shift(1) - 1) * 100
    q.jumps = [{"date": str(d.date()), "move_pct": round(float(v), 2)} for d, v in r[r.abs() > JUMP_PCT].items()]
    q.rows = int(x.notna().sum())
    if q.rows:
        q.first_date, q.last_date = str(x.first_valid_index().date()), str(x.last_valid_index().date())
    if q.duplicates_dropped or q.non_positive or q.jumps or q.was_unsorted:
        q.status = "warning"
    if q.jumps:
        q.notes.append(f"{len(q.jumps)} one-session move(s) above {JUMP_PCT:.0f}%: check for split / bonus / bad tick")
    if q.rows == 0:
        q.status = "failed"
    return x.astype(float), q


def session_state(now: datetime | None = None, holidays: set | None = None) -> dict:
    """Whether NSE cash market is open and which session is the latest COMPLETE one.
    holidays: set of datetime.date. Without it, weekday-only logic is used and the result says so."""
    now = (now or datetime.now(IST)).astimezone(IST)
    hol = holidays or set()
    is_day = now.weekday() < 5 and now.date() not in hol
    o, c = time(*SESSION_OPEN), time(*SESSION_CLOSE)
    settle = (datetime.combine(now.date(), c) + timedelta(minutes=SESSION_SETTLE_MIN)).time()
    t = now.time()
    is_open = is_day and o <= t < c
    today_complete = is_day and t >= settle
    d = now.date() if today_complete else now.date() - timedelta(days=1)
    while d.weekday() >= 5 or d in hol:
        d -= timedelta(days=1)
    return {"now_ist": now.isoformat(timespec="minutes"), "market_open": is_open, "today_partial": is_day and o <= t < settle,
            "last_complete_session": d.isoformat(), "holiday_calendar": "supplied" if holidays else "NOT AVAILABLE (weekdays only)"}


def stale_sessions(last_date: str | None, last_complete: str, calendar: pd.DatetimeIndex | None = None) -> int | None:
    """Sessions between a series' last date and the latest complete session (business days if no calendar)."""
    if not last_date:
        return None
    a, b = pd.Timestamp(last_date), pd.Timestamp(last_complete)
    if a >= b:
        return 0
    if calendar is not None:
        return int(((calendar > a) & (calendar <= b)).sum())
    return int(len(pd.bdate_range(a + pd.Timedelta(days=1), b)))

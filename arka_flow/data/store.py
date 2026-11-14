"""
Local SQLite store (default file: arka_flow.db, kept out of git). No credentials needed.

Tables
  prices           daily bars per instrument and source (incremental upsert)
  quality          one QualityRecord per load
  sector_snapshot  one row per sector per session per model version (all metrics as JSON)
  signal_history   descriptive situations per sector per session (Phase 5 states will go here too)
  runs             every pipeline run: model version, start/end, status, errors
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
  instrument TEXT NOT NULL, source TEXT NOT NULL, date TEXT NOT NULL,
  open REAL, high REAL, low REAL, close REAL, volume REAL, traded_value REAL, deliv_qty REAL,
  adjusted TEXT, loaded_at TEXT NOT NULL,
  PRIMARY KEY (instrument, source, date));
CREATE TABLE IF NOT EXISTS quality (
  series_id TEXT NOT NULL, source TEXT NOT NULL, checked_at TEXT NOT NULL, status TEXT, record TEXT,
  PRIMARY KEY (series_id, source, checked_at));
CREATE TABLE IF NOT EXISTS sector_snapshot (
  sector_id TEXT NOT NULL, date TEXT NOT NULL, model_version TEXT NOT NULL, benchmark TEXT NOT NULL,
  computed_at TEXT NOT NULL, metrics TEXT NOT NULL,
  PRIMARY KEY (sector_id, date, model_version, benchmark));
CREATE TABLE IF NOT EXISTS signal_history (
  sector_id TEXT NOT NULL, date TEXT NOT NULL, model_version TEXT NOT NULL, kind TEXT NOT NULL,
  name TEXT NOT NULL, evidence TEXT, PRIMARY KEY (sector_id, date, model_version, kind, name));
CREATE TABLE IF NOT EXISTS runs (
  run_id INTEGER PRIMARY KEY AUTOINCREMENT, model_version TEXT, started_at TEXT, finished_at TEXT,
  status TEXT, detail TEXT);
"""
PRICE_COLS = ("open", "high", "low", "close", "volume", "traded_value", "deliv_qty")


class Store:
    def __init__(self, path: str | Path = "arka_flow.db"):
        self.path = str(path)
        with closing(self._con()) as c, c:
            c.executescript(SCHEMA)

    def _con(self):
        return sqlite3.connect(self.path)

    # ---- prices ----
    def upsert_prices(self, instrument: str, source: str, df: pd.DataFrame, adjusted: str, loaded_at: str) -> int:
        """df: date index, any of PRICE_COLS (lower-case). NaN is stored as NULL, never as 0."""
        if df is None or len(df) == 0:
            return 0
        rows = []
        for d, r in df.iterrows():
            vals = [None if c not in df.columns or pd.isna(r[c]) else float(r[c]) for c in PRICE_COLS]
            rows.append((instrument, source, str(pd.Timestamp(d).date()), *vals, adjusted, loaded_at))
        with closing(self._con()) as c, c:
            c.executemany(f"INSERT OR REPLACE INTO prices VALUES ({','.join('?' * 12)})", rows)
        return len(rows)

    def last_date(self, instrument: str, source: str):
        with closing(self._con()) as c:
            r = c.execute("SELECT MAX(date) FROM prices WHERE instrument=? AND source=?", (instrument, source)).fetchone()
        return r[0] if r else None

    def load_prices(self, instruments, source: str, field: str = "close") -> pd.DataFrame:
        if field not in PRICE_COLS:
            raise ValueError(field)
        q = f"SELECT instrument, date, {field} AS v FROM prices WHERE source=? AND instrument IN ({','.join('?' * len(instruments))})"
        with closing(self._con()) as c:
            df = pd.read_sql_query(q, c, params=[source, *instruments])
        if df.empty:
            return pd.DataFrame()
        out = df.pivot(index="date", columns="instrument", values="v")
        out.index = pd.to_datetime(out.index)
        return out.sort_index()

    # ---- quality, snapshots, signals, runs ----
    def log_quality(self, rec) -> None:
        d = rec.to_dict()
        with closing(self._con()) as c, c:
            c.execute("INSERT OR REPLACE INTO quality VALUES (?,?,?,?,?)",
                      (d["series_id"], d["source"], d["checked_at"], d["status"], json.dumps(d)))

    def save_snapshot(self, sector_id, date, model_version, benchmark, computed_at, metrics: dict) -> None:
        clean = {k: (None if isinstance(v, float) and v != v else v) for k, v in metrics.items()}
        with closing(self._con()) as c, c:
            c.execute("INSERT OR REPLACE INTO sector_snapshot VALUES (?,?,?,?,?,?)",
                      (sector_id, date, model_version, benchmark, computed_at, json.dumps(clean, default=str)))

    def save_signals(self, sector_id, date, model_version, kind, items: list) -> None:
        with closing(self._con()) as c, c:
            c.executemany("INSERT OR REPLACE INTO signal_history VALUES (?,?,?,?,?,?)",
                          [(sector_id, date, model_version, kind, i["name"], json.dumps(i)) for i in items])

    def start_run(self, model_version, started_at) -> int:
        with closing(self._con()) as c, c:
            return c.execute("INSERT INTO runs (model_version, started_at, status) VALUES (?,?,?)",
                             (model_version, started_at, "running")).lastrowid

    def finish_run(self, run_id, finished_at, status, detail: dict) -> None:
        with closing(self._con()) as c, c:
            c.execute("UPDATE runs SET finished_at=?, status=?, detail=? WHERE run_id=?",
                      (finished_at, status, json.dumps(detail, default=str), run_id))

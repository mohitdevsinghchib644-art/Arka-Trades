"""
user_data.py — Arka Trades persistent user data (watchlist + portfolio)

Everything the user types in is saved to Supabase immediately and stays until
the user removes it. If Supabase is not configured the data is kept for the
browser session only and the UI says so.

Tables
------
watchlist        (already exists)  id, symbol
admin_watchlist  (already exists)  id, symbol          <- "Arka desk list"
portfolio        (NEW — run the SQL below once)

    create table if not exists portfolio (
      id          bigint generated always as identity primary key,
      symbol      text not null,
      name        text,
      quantity    double precision not null,
      buy_price   double precision not null,
      buy_date    date,
      note        text,
      created_at  timestamptz default now()
    );

    -- Same access settings as your other tables. If your other tables have
    -- Row Level Security OFF, leave it off here too; if ON, add a policy.
    alter table portfolio disable row level security;
"""
from __future__ import annotations

import re
import time

import streamlit as st

W_TABLE = "watchlist"
A_TABLE = "admin_watchlist"
P_TABLE = "portfolio"

SQL_PORTFOLIO = """create table if not exists portfolio (
  id          bigint generated always as identity primary key,
  symbol      text not null,
  name        text,
  quantity    double precision not null,
  buy_price   double precision not null,
  buy_date    date,
  note        text,
  created_at  timestamptz default now()
);
alter table portfolio disable row level security;"""


# ═════════════════════ symbols ═════════════════════
def clean_symbol(raw) -> str:
    """'NSE:tcs' / 'TCS.NS' / ' tcs ' -> 'TCS'."""
    s = str(raw or "").strip().upper()
    if ":" in s:
        s = s.split(":")[-1]
    for suf in (".NS", ".BO", ".NSE", ".BSE"):
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    return re.sub(r"[^A-Z0-9&\-_]", "", s)


def split_tokens(text: str) -> list:
    """Split pasted input on commas / semicolons / new lines / tabs."""
    return [t.strip() for t in re.split(r"[,;\n\t]+", text or "") if t.strip()]


def _state_key(table: str) -> str:
    return "admin_watchlist" if table == A_TABLE else "watchlist"


def table_missing(err) -> bool:
    e = str(err).lower()
    return any(k in e for k in ("does not exist", "pgrst205", "schema cache", "could not find the table"))


# ═════════════════════ watchlist ═════════════════════
def load_symbols(sb, table: str = W_TABLE) -> list:
    key = _state_key(table)
    if sb is None:
        return list(st.session_state.get(key, []))
    try:
        res = sb.table(table).select("symbol").order("id").execute()
        syms = list(dict.fromkeys(clean_symbol(r["symbol"]) for r in (res.data or []) if r.get("symbol")))
        st.session_state[key] = syms
        return syms
    except Exception:
        return list(st.session_state.get(key, []))


def add_symbols(sb, symbols, table: str = W_TABLE):
    """Insert new symbols (skips ones already saved). Returns (added, error|None)."""
    key = _state_key(table)
    existing = load_symbols(sb, table)
    new = [s for s in dict.fromkeys(clean_symbol(x) for x in symbols) if s and s not in existing]
    if not new:
        return [], None
    if sb is None:
        st.session_state[key] = existing + new
        return new, "Supabase is not configured — saved for this session only."
    try:
        sb.table(table).insert([{"symbol": s} for s in new]).execute()
        st.session_state[key] = existing + new
        return new, None
    except Exception as e:
        return [], f"Could not save: {e}"


def remove_symbol(sb, symbol: str, table: str = W_TABLE):
    key = _state_key(table)
    symbol = clean_symbol(symbol)
    if sb is not None:
        try:
            sb.table(table).delete().eq("symbol", symbol).execute()
        except Exception as e:
            return f"Could not remove: {e}"
    st.session_state[key] = [s for s in st.session_state.get(key, []) if s != symbol]
    return None


# ═════════════════════ portfolio ═════════════════════
def _norm_lot(r: dict) -> dict:
    return {
        "id": r.get("id"),
        "symbol": clean_symbol(r.get("symbol")),
        "name": (r.get("name") or "").strip(),
        "quantity": float(r.get("quantity") or 0),
        "buy_price": float(r.get("buy_price") or 0),
        "buy_date": str(r.get("buy_date") or "")[:10],
        "note": (r.get("note") or "").strip(),
    }


def load_portfolio(sb):
    """Returns (lots, warning|None). Also refreshes st.session_state['pf_lots']."""
    if sb is None:
        return list(st.session_state.get("pf_lots", [])), "Supabase is not configured — holdings are kept for this session only."
    try:
        res = sb.table(P_TABLE).select("*").order("id").execute()
        lots = [_norm_lot(r) for r in (res.data or [])]
        st.session_state["pf_lots"] = lots
        return lots, None
    except Exception as e:
        return list(st.session_state.get("pf_lots", [])), str(e)


def add_lot(sb, symbol, quantity, buy_price, buy_date=None, note="", name=""):
    """Returns (ok, message|None)."""
    row = {"symbol": clean_symbol(symbol), "name": name or None, "quantity": float(quantity),
           "buy_price": float(buy_price), "buy_date": str(buy_date) if buy_date else None, "note": note or None}
    if sb is not None:
        try:
            sb.table(P_TABLE).insert(row).execute()
            load_portfolio(sb)
            return True, None
        except Exception as e:
            if table_missing(e):
                return False, "MISSING_TABLE"
            return False, f"Could not save: {e}"
    lots = list(st.session_state.get("pf_lots", []))
    lots.append(_norm_lot({**row, "id": int(time.time() * 1000)}))
    st.session_state["pf_lots"] = lots
    return True, "Supabase is not configured — saved for this session only."


def delete_lot(sb, lot_id):
    if sb is not None:
        try:
            sb.table(P_TABLE).delete().eq("id", lot_id).execute()
            load_portfolio(sb)
            return None
        except Exception as e:
            return f"Could not delete: {e}"
    st.session_state["pf_lots"] = [l for l in st.session_state.get("pf_lots", []) if l["id"] != lot_id]
    return None


def delete_holding(sb, symbol):
    symbol = clean_symbol(symbol)
    if sb is not None:
        try:
            sb.table(P_TABLE).delete().eq("symbol", symbol).execute()
            load_portfolio(sb)
            return None
        except Exception as e:
            return f"Could not delete: {e}"
    st.session_state["pf_lots"] = [l for l in st.session_state.get("pf_lots", []) if l["symbol"] != symbol]
    return None


def holding_symbols() -> list:
    return sorted({l["symbol"] for l in st.session_state.get("pf_lots", []) if l.get("symbol")})


# ═════════════════════ navigation helpers ═════════════════════
def go_research(sym: str):
    sym = clean_symbol(sym)
    st.session_state.update(active_security=sym, research_last_query=sym, m1_ticker=sym, page="research")
    st.session_state.pop("research_data", None)
    st.rerun()


def go_analysis(sym: str):
    """Open Arka AI on this stock and start the analysis automatically."""
    sym = clean_symbol(sym)
    st.session_state.update(
        active_security=sym, research_last_query=sym, m1_ticker=sym,
        ai_mode_v8="Mode 1 — Setup Analysis", m1_ticker_v8=sym, m1_loaded_ticker_v8=sym,
        m1_chart_fetched_v8=False, arka_autorun_v8=sym, page="analysis")
    st.session_state.pop("m1_result_v8", None)
    st.session_state.pop("m1_annotated_v8", None)
    st.rerun()

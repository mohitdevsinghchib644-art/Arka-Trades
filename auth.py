"""
auth.py — "keep me signed in on this device" for Arka Trades.

Why logins kept repeating: st.session_state lives only as long as one browser session. A phone reload or a
killed background tab starts a new session, so `logged_in` was False again.

Fix: after a good login the server hands the browser a signed token (cookie, 30 days). On the next visit the
cookie arrives with the first request (st.context.cookies, Streamlit >= 1.37), is verified here, and the user
is signed in before the login page is ever drawn. No IP matching (phone IPs change and are shared).

Token = role.expiry.HMAC-SHA256. Nothing is stored server-side.
  * Tamper-proof: cannot be forged without the key.
  * Log out every device: change AUTH_SECRET (or any login password) in Streamlit secrets.
"""
from __future__ import annotations

import hashlib
import hmac
import time

import streamlit as st
import streamlit.components.v1 as components

COOKIE_NAME = "arka_auth"
COOKIE_DAYS = 30


def derive_key(secret, *parts) -> bytes:
    """AUTH_SECRET if set, else derived from the login credentials (so changing a password also revokes tokens)."""
    base = str(secret) if secret else "|".join(str(p) for p in parts)
    return hashlib.sha256(base.encode()).digest()


def same(a: str, b: str) -> bool:
    return hmac.compare_digest(str(a).encode(), str(b).encode())


def _sig(key: bytes, payload: str) -> str:
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def make_token(role: str, key: bytes, days: int = COOKIE_DAYS, now: float | None = None) -> str:
    payload = f"{role}.{int((now or time.time()) + days * 86400)}"
    return f"{payload}.{_sig(key, payload)}"


def verify_token(token, key: bytes, now: float | None = None):
    """-> 'admin' | 'member' | None"""
    try:
        role, exp, sig = str(token).split(".")
        if role not in ("admin", "member") or not same(sig, _sig(key, f"{role}.{exp}")):
            return None
        return role if int(exp) >= (now or time.time()) else None
    except Exception:
        return None


def _cookie():
    try:
        return st.context.cookies.get(COOKIE_NAME)
    except Exception:           # Streamlit < 1.37 or no request context
        return None


def auto_login(key: bytes) -> None:
    """Call once per run, before the login page decision."""
    ss = st.session_state
    if ss.get("logged_in") or ss.get("_no_autologin"):
        return
    role = verify_token(_cookie(), key)
    if role:
        ss.logged_in, ss.is_admin = True, role == "admin"


def remember(role: str, key: bytes) -> None:
    """After a successful login: queue the cookie (written by flush() on the next run)."""
    st.session_state["_set_cookie"] = make_token(role, key)
    st.session_state["_no_autologin"] = False


def forget() -> None:
    """On sign-out: queue cookie removal and block auto-login for the rest of this session."""
    st.session_state["_clear_cookie"] = True
    st.session_state["_no_autologin"] = True


def flush() -> None:
    """Write / clear the cookie. Streamlit cannot set cookies itself, so a 0-height script does it (same origin)."""
    ss = st.session_state
    tok, clear = ss.pop("_set_cookie", None), ss.pop("_clear_cookie", False)
    if not tok and not clear:
        return
    value, age = (tok, COOKIE_DAYS * 86400) if tok else ("", 0)
    components.html(
        "<script>try{const p=window.parent,d=p.document,s=p.location.protocol==='https:'?'; Secure':'';"
        f"d.cookie='{COOKIE_NAME}={value}; max-age={age}; path=/; SameSite=Lax'+s;}}catch(e){{}}</script>",
        height=0)

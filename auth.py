
"""
auth.py — Persistent 30-day login for Arka Trades.

Uses a signed HMAC-SHA256 cookie.
Compatible with the existing app.py integration:
    auth.derive_key(...)
    auth.auto_login(...)
    auth.flush()
    auth.same(...)
    auth.remember(...)
    auth.forget(...)

No Google login. No database dependency.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time

import streamlit as st
import streamlit.components.v1 as components


COOKIE_NAME = "arka_auth"
COOKIE_DAYS = 30
COOKIE_MAX_AGE = COOKIE_DAYS * 86400
VALID_ROLES = {"admin", "member"}


def derive_key(secret, *parts) -> bytes:
    """
    Derive a signing key from AUTH_SECRET and login credentials.

    Changing AUTH_SECRET or any login credential invalidates
    previously issued tokens.
    """
    values = [str(secret or ""), *(str(p) for p in parts)]
    material = "arka-auth-v1\0" + "\0".join(values)

    return hashlib.sha256(material.encode("utf-8")).digest()


def same(a: str, b: str) -> bool:
    """Constant-time string comparison."""
    return hmac.compare_digest(
        str(a).encode("utf-8"),
        str(b).encode("utf-8")
    )


def _sig(key: bytes, payload: str) -> str:
    """Generate HMAC-SHA256 signature."""
    return hmac.new(
        key,
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def make_token(
    role: str,
    key: bytes,
    days: int = COOKIE_DAYS,
    now: float | None = None
) -> str:
    """Create a signed role and expiry token."""

    if role not in VALID_ROLES:
        raise ValueError("Invalid authentication role.")

    if days <= 0:
        raise ValueError("Token lifetime must be positive.")

    current_time = time.time() if now is None else now
    expiry = int(current_time + days * 86400)

    payload = f"{role}.{expiry}"
    signature = _sig(key, payload)

    return f"{payload}.{signature}"


def verify_token(
    token,
    key: bytes,
    now: float | None = None
):
    """
    Validate token signature, role and expiry.

    Returns:
        'admin'
        'member'
        None
    """

    try:
        if not token:
            return None

        token = str(token)

        # Reject unexpectedly large or malformed cookies.
        if len(token) > 512:
            return None

        role, exp, signature = token.split(".")

        if role not in VALID_ROLES:
            return None

        if not exp.isdigit():
            return None

        if not re.fullmatch(r"[0-9a-f]{64}", signature):
            return None

        payload = f"{role}.{exp}"
        expected_signature = _sig(key, payload)

        if not same(signature, expected_signature):
            return None

        current_time = time.time() if now is None else now

        if int(exp) <= current_time:
            return None

        return role

    except (ValueError, TypeError, OverflowError):
        return None


def _cookie():
    """Read the authentication cookie from the current request."""

    try:
        return st.context.cookies.get(COOKIE_NAME)
    except Exception:
        return None


def auto_login(key: bytes) -> None:
    """
    Restore login before rendering the login page.
    Call once per Streamlit run.
    """

    ss = st.session_state

    if ss.get("logged_in") or ss.get("_no_autologin"):
        return

    role = verify_token(_cookie(), key)

    if role:
        ss.logged_in = True
        ss.is_admin = role == "admin"


def remember(role: str, key: bytes) -> None:
    """Queue a persistent login cookie."""

    if role not in VALID_ROLES:
        raise ValueError("Invalid authentication role.")

    st.session_state["_set_cookie"] = make_token(
        role,
        key,
        COOKIE_DAYS
    )

    st.session_state["_no_autologin"] = False


def forget() -> None:
    """Queue cookie deletion and prevent automatic login."""

    ss = st.session_state

    ss["_set_cookie"] = None
    ss["_clear_cookie"] = True
    ss["_no_autologin"] = True

    ss["logged_in"] = False
    ss["is_admin"] = False


def flush() -> None:
    """
    Write or delete the cookie using a same-origin script.

    Must be called during the Streamlit run after auto_login().
    """

    ss = st.session_state

    token = ss.pop("_set_cookie", None)
    clear = ss.pop("_clear_cookie", False)

    if not token and not clear:
        return

    if token:
        value = (
            f"{COOKIE_NAME}={token}; "
            f"Max-Age={COOKIE_MAX_AGE}; "
            "Path=/; SameSite=Lax"
        )
    else:
        value = (
            f"{COOKIE_NAME}=; "
            "Max-Age=0; "
            "Path=/; SameSite=Lax"
        )

    components.html(
        f"""
        <script>
        (() => {{
            try {{
                const p = window.parent;
                const secure = p.location.protocol === "https:"
                    ? "; Secure"
                    : "";

                p.document.cookie = {value!r} + secure;
            }} catch (e) {{
                console.error("Arka Trades cookie update failed:", e);
            }}
        }})();
        </script>
        """,
        height=0
    )

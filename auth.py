
"""
auth.py — Persistent 30-day login for Arka Trades.

Features:
- Signed HMAC-SHA256 authentication tokens.
- Persistent browser cookie for 30 days.
- Admin and member roles.
- Password/key changes invalidate existing tokens.
- Logout deletes the browser cookie.
- Compatible with existing app.py integration.

No Google login.
No database dependency.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time

import streamlit as st
import streamlit.components.v1 as components


COOKIE_NAME = "arka_auth"
COOKIE_DAYS = 30
COOKIE_MAX_AGE = COOKIE_DAYS * 86400
VALID_ROLES = {"admin", "member"}


# ---------------------------------------------------------
# SIGNING KEY
# ---------------------------------------------------------

def derive_key(secret, *parts) -> bytes:
    """
    Derive signing key from AUTH_SECRET and login credentials.

    Changing AUTH_SECRET or any credential invalidates
    previously issued authentication tokens.
    """

    values = [str(secret or ""), *(str(p) for p in parts)]

    material = "arka-auth-v1\0" + "\0".join(values)

    return hashlib.sha256(
        material.encode("utf-8")
    ).digest()


# ---------------------------------------------------------
# CONSTANT-TIME COMPARISON
# ---------------------------------------------------------

def same(a: str, b: str) -> bool:
    return hmac.compare_digest(
        str(a).encode("utf-8"),
        str(b).encode("utf-8")
    )


# ---------------------------------------------------------
# TOKEN SIGNATURE
# ---------------------------------------------------------

def _sig(key: bytes, payload: str) -> str:

    return hmac.new(
        key,
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


# ---------------------------------------------------------
# CREATE TOKEN
# ---------------------------------------------------------

def make_token(
    role: str,
    key: bytes,
    days: int = COOKIE_DAYS,
    now: float | None = None
) -> str:

    if role not in VALID_ROLES:
        raise ValueError("Invalid authentication role.")

    if days <= 0:
        raise ValueError("Token lifetime must be positive.")

    current_time = time.time() if now is None else now

    expiry = int(current_time + days * 86400)

    payload = f"{role}.{expiry}"

    signature = _sig(key, payload)

    return f"{payload}.{signature}"


# ---------------------------------------------------------
# VERIFY TOKEN
# ---------------------------------------------------------

def verify_token(
    token,
    key: bytes,
    now: float | None = None
):

    """
    Returns:
        'admin'
        'member'
        None
    """

    try:

        if not token:
            return None

        token = str(token)

        if len(token) > 512:
            return None

        role, expiry, signature = token.split(".")

        if role not in VALID_ROLES:
            return None

        if not expiry.isdigit():
            return None

        if not re.fullmatch(
            r"[0-9a-f]{64}",
            signature
        ):
            return None

        payload = f"{role}.{expiry}"

        expected_signature = _sig(
            key,
            payload
        )

        if not same(signature, expected_signature):
            return None

        current_time = time.time() if now is None else now

        if int(expiry) <= current_time:
            return None

        return role

    except (ValueError, TypeError, OverflowError):

        return None


# ---------------------------------------------------------
# READ BROWSER COOKIE
# ---------------------------------------------------------

def _cookie():

    try:

        return st.context.cookies.get(COOKIE_NAME)

    except Exception:

        return None


# ---------------------------------------------------------
# AUTOMATIC LOGIN
# ---------------------------------------------------------

def auto_login(key: bytes) -> None:

    """
    Restore login before the login page is displayed.
    Call once per Streamlit run.
    """

    ss = st.session_state

    if ss.get("logged_in") or ss.get("_no_autologin"):
        return

    role = verify_token(
        _cookie(),
        key
    )

    if role:

        ss.logged_in = True

        ss.is_admin = role == "admin"


# ---------------------------------------------------------
# REMEMBER LOGIN
# ---------------------------------------------------------

def remember(role: str, key: bytes) -> None:

    """
    Queue the authentication cookie for writing.
    """

    if role not in VALID_ROLES:
        raise ValueError("Invalid authentication role.")

    token = make_token(
        role,
        key,
        COOKIE_DAYS
    )

    st.session_state["_set_cookie"] = token

    st.session_state["_clear_cookie"] = False

    st.session_state["_no_autologin"] = False


# ---------------------------------------------------------
# LOGOUT
# ---------------------------------------------------------

def forget() -> None:

    """
    Queue cookie deletion and block automatic login
    for the remainder of the current Streamlit session.
    """

    ss = st.session_state

    ss["_set_cookie"] = None

    ss["_clear_cookie"] = True

    ss["_no_autologin"] = True

    ss["logged_in"] = False

    ss["is_admin"] = False


# ---------------------------------------------------------
# WRITE / DELETE COOKIE
# ---------------------------------------------------------

def flush() -> None:

    """
    Write or delete the browser authentication cookie.

    Uses the existing Streamlit HTML component.
    """

    ss = st.session_state

    token = ss.pop("_set_cookie", None)

    clear = ss.pop("_clear_cookie", False)

    if not token and not clear:
        return

    if token:

        age = COOKIE_MAX_AGE

    else:

        age = 0

    # JSON encoding safely passes the cookie value to JavaScript.
    cookie_name = json.dumps(COOKIE_NAME)

    cookie_value = json.dumps(token or "")

    script = f"""
    <script>
    (() => {{

        try {{

            const p = window.parent;

            const secure =
                p.location.protocol === "https:"
                ? "; Secure"
                : "";

            const name = {cookie_name};

            const value = {cookie_value};

            const age = {age};

            const cookie =
                name + "=" + value +
                "; Max-Age=" + age +
                "; Path=/" +
                "; SameSite=Lax" +
                secure;

            // Write cookie to the main Streamlit page.
            p.document.cookie = cookie;

            // Verify whether the browser accepted it.
            const saved = p.document.cookie
                .split(";")
                .map(x => x.trim())
                .some(x => x.startsWith(name + "="));

            console.log(
                "Arka Trades cookie:",
                saved ? "WRITE SUCCESS" : "WRITE FAILED"
            );

            // Notify the browser console if writing failed.
            if (!saved && age > 0) {{

                console.error(
                    "Arka Trades: authentication cookie was not saved."
                );

            }}

        }} catch (error) {{

            console.error(
                "Arka Trades cookie error:",
                error
            );

        }}

    }})();
    </script>
    """

    components.html(
        script,
        height=1
    )

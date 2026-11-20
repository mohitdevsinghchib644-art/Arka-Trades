"""
auth.py — Arka Trades sign-in that survives reloads, plus the site icon (v2)

WHY v1 NEVER KEPT YOU SIGNED IN
  v1 wrote a cookie from JavaScript and read it back with st.context.cookies. On Streamlit Community Cloud the proxy
  removes cookies from the connection the app sees, so st.context.cookies is always empty there: the cookie was
  saved in your browser but the app could never read it, and you were asked to sign in every time.

HOW v2 WORKS
  * A tiny built-in component (no extra package) keeps a signed 30-day token in the browser's localStorage and
    hands it to Python over Streamlit's own message channel, which Community Cloud does not filter.
  * Signing in on a device keeps that device signed in for 30 days; each visit in the last 25 days renews it,
    so a device you use regularly never asks again. Sign out removes the token from that device.
  * Changing AUTH_SECRET, or any login in Streamlit secrets, signs out every device.
  * The cookie is still written and read too, so self-hosted / local runs restore even faster.

LOGO AND SITE ICON (v2.1)
  * arka_logo.png  the wordmark shown in the header and on the landing / sign-in pages (white on transparent).
  * arka_icon.png  the square app icon: browser tab, iPhone "Add to Home Screen" and Android home screen.
  The component replaces Streamlit's icon with arka_icon.png and hides Streamlit Cloud's floating badges for
  visitors. Without the files a text wordmark / icon is drawn.

Public API used by app.py
  derive_key(secret, *parts) · same(a, b) · make_token · verify_token
  bridge(key)             call once near the top of EVERY run, before deciding what to show
  ready()                 True once the browser has answered (or after a short timeout)
  remember(role, key)     after a successful sign-in ("keep me signed in")
  forget()                sign out on this device
  viewport_width()        browser width in CSS px reported by the device, or None
  page_icon()             PIL image for st.set_page_config(page_icon=...)
  logo_data_uri(px)       the logo as a data: URI for <img> tags
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import re
import tempfile
import time
import uuid
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

COOKIE_NAME = "arka_auth"            # also the localStorage key
COOKIE_DAYS = 30
RENEW_BELOW_DAYS = 25
COOKIE_MAX_AGE = COOKIE_DAYS * 86400
VALID_ROLES = {"admin", "member"}
APP_NAME, SHORT_NAME = "Arka Trades", "Arka"
THEME_BG = "#000000"
WAIT_S = 4.0                         # longest the app waits for the browser before showing the sign-in page


# ═════════════════════════ tokens (unchanged format: v1 tokens stay valid) ═════════════════════════
def derive_key(secret, *parts) -> bytes:
    """Signing key from AUTH_SECRET and the logins: changing any of them invalidates every issued token."""
    material = "arka-auth-v1\0" + "\0".join([str(secret or ""), *(str(p) for p in parts)])
    return hashlib.sha256(material.encode("utf-8")).digest()


def same(a: str, b: str) -> bool:
    return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))


def _sig(key: bytes, payload: str) -> str:
    return hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def make_token(role: str, key: bytes, days: int = COOKIE_DAYS, now: float | None = None) -> str:
    if role not in VALID_ROLES:
        raise ValueError("Invalid authentication role.")
    if days <= 0:
        raise ValueError("Token lifetime must be positive.")
    expiry = int((time.time() if now is None else now) + days * 86400)
    payload = f"{role}.{expiry}"
    return f"{payload}.{_sig(key, payload)}"


def verify_token(token, key: bytes, now: float | None = None):
    """-> 'admin' | 'member' | None"""
    try:
        if not token:
            return None
        token = str(token)
        if len(token) > 512:
            return None
        role, expiry, signature = token.split(".")
        if role not in VALID_ROLES or not expiry.isdigit() or not re.fullmatch(r"[0-9a-f]{64}", signature):
            return None
        if not same(signature, _sig(key, f"{role}.{expiry}")):
            return None
        if int(expiry) <= (time.time() if now is None else now):
            return None
        return role
    except (ValueError, TypeError, OverflowError):
        return None


def days_left(token, now: float | None = None):
    try:
        return (int(str(token).split(".")[1]) - (time.time() if now is None else now)) / 86400
    except Exception:
        return None


# ═════════════════════════ logo ═════════════════════════
_LOGO_FILES = ("arka_icon.png", "assets/arka_icon.png", "icon.png", "arka_logo.png", "logo.png", "assets/logo.png")   # square app icon
_WORDMARK_FILES = ("arka_logo.png", "assets/arka_logo.png", "logo.png", "assets/logo.png")


# The Arka mark (orange square, black A with an underline), built in so the icon is right even without arka_icon.png.
_MARK_PNG = "iVBORw0KGgoAAAANSUhEUgAAAQAAAAEACAMAAABrrFhUAAAASFBMVEX/nRT+mhT6mBT4lhT3lhT2lhT2lRT1lRT1lBT0lBTljBS8dRVzTBc7LBkiHhodHBocGxobGxobGhoaGhoZGhoYGRoVFxoPFBoBikVNAAAIlUlEQVR42u2dXXuiMBBGX0GgoMiX6P//p5tga20ricjMJK6Tu92nF83J6RBmMgHbNx9QAApAASgABaAAFIACUAAKQAEoAAWgABSAAlAACkABKAAFoAAUgAJQAApAASgABaAAFIACUAAKQAEoAAWgABSAAlAACkABKAAFoAAUgAJQAApAASgABaAAFADFyN4dQJK8NYAC+x3K9wVQJFVTV0n+vgCwP5/34RUIBaDY7JrhdKiS4l0BGAGaJgIFEEqA6jAO3XioNsV7ArACDINV4C0B2Agw9kMMCoQCMAkQgwJBAHxsdt3Rzn/ogysQxoCkvggQgQIhAJTYndrhc4zNLqgCIQCkWX2+AjAKvBsAI8D3/G0YCPpOFABAWtanGwDNuU7eyoBfApjNwCmkAuIAsl8CDEN7rrP0fQD8EcASCKiANIA8+S3ApECevguAEvuvPVAcUQDiibA/AkwKvIsBdwUIqgCkBTicur8AjALFWxjwcV+AkA8CiCfCursATnWZZv89gFkBAiqAKASYFAhTJYF8JvT+CJUih2gm9GAzoXEpgACZ0DkFgkQBSApwHBzDPggCKCAHIHcLEEoBCKbCB/cIEwXkDEg8AgR6EEAwFd7FqADkayFxKYB4BLBvxfInJoQApHl9br0AJgU+/kcAdzKhsSggAiC7kwmNJQogJgFCKICoBAigAKISIIACkBCgelgAmxqSPTGBuASQPzQDgWLYEgHED80gVC0kFgUQphgWTxRAbAJIK4BAxbBoogAEUuHtsvnLKgCBWki/EIDo0TkErIXMD8HTkxA5FR6xAohRAPtOIKUA2AVwBDu3Avn/AMApwOj665A6QAteAYaja8PnyBOKKYBQxbDWvPg73xKFFABvKrx3bvdcbwlSDwIEqoXYWvDW84zYbT5eGYBfgDKJQAGw9oU086k/Wwb0vCmKRAGESYRdkr+X/lHHzyTZ6wIwbQHuR4DN/bo3CiIHaBFQgK1XAYFmGjAK0LojgH+v2AsogJACTD839k4FspcEkDuLYe1t9cd5cEJAAQTIhN7W/3yq1NxnZsCWCX0gAmy9dTN+BRBWAP/P1tnrGTAVw7oH67+evDn7MXoEFsDZRCDRUgfpWsgUAYrHM+fcCiC0AL7M6XSG+qUAPCBAviR1yqwAJBuDZs4Auc+RmyhQpi8EwNUY9GsT+OAxUl4FIJsKv38IzPnqbPcCjAqAPhXejku7QjyniFgVQAQCXPbDXRgFQC/AcXlDgCc5yKkAJBuDZo+B5p79sP3LyV4BgM1vPNUR8uFTgO0ALYgPhT4lgC85yNlMA7laiHMWua+rkksB0ApQPyeAfzPEFgUg1hjk0Tgt6iB7AXBdkrZ0Cr7NEJcCYLolb/kdETaRHOCNAJEI4N8MMSkAKQG821l3KplNAcgUw8wCun/9vCj9CnDsBSBTDHPeGGcmnyXAJnNtI7n2ApDpC5kTIMvLcmsmn1S7/b5uendvNUc/FUQyoXcjQFaURbr5nPxhPJ9PY4CWOoj0hfwOYHbh88RMvjCTr6fJD03TeVvqanoFICTAdwS4WXg7+dM0+bYL1VUJib6Q61mX/HPy+fQn31vr2wcnP5NTjwSAt7yVXMPdpqy+Fv5oJt8vbqYpIwRgBRidAmzK9DvWn8/nsTN/8ssbCRgaayEiwAab/BruxsULz6kA2GshVoDqO9w9P3keBcDfGXZsDu3F+rYb1g5yBcB9SZodq6y/o0AeGQBvY1BPM3kWBcDdGEQ9qKMAuBuDyAexAuCthTwxehMvBBUA+y15i/w2kx/N47KTu20JsQjQm8n3Zu7npt47s6PTx4noFABrLWSB9XbhT4d6v9uVcO6riBWAxCVpHut7s0E+t/V+v6vs21Liu3OB9PtUWN8Z1q4Kd9eFt5NPtmVZZN4HK6UCCCTAV7g7Tgu/tQufmSzRY09WSgXAWAuZDXfNNdzdLvzPyNoLKYC1qfBusfXHL+urcvNj4R+/f5JQAfDVQu4sfDc95L/DncmQ5U9dPkV31Q5E7sj6sfC7cvPX+mWVUsJ7VsAuwNdDvp8WvrALn9+zftntQ2QKgFOAi/U/wl1auBb+0WNjhAqA7ZrE293dJdzZhc+oLqCium0JXAJ0Zu7NZH1urH9w4ZdcQER01Q6YqqGmhmHDXfLM5B9LMxC114OpGGbOc3zt7jKmy9jHlkIBsLQGXqqhzyz8ouv4KaIA2ARYe6DHfepyUoDiQQCWaijNeR7fmyaJAmAphpGc6PLuM0j2AohXAP9Ok2I7CKkDMU8q0HArAIZyONWJvstWo58fFFEAPBGA5sNpVrR2dAyC7SCerYZKNHxb00xB/Xu0n+P6z8NqBUBeDCPt9LQnqXaVHbvf4/N/qlT8T6Bwb1JJz/RmycYzEvmngGirb154Ri4OwLNFDfoVZREA7s6wsN/RlgDgE4Dh2peMZFAByJ0peyMA/SIlJIMIQIAIYFopKEZGZEDqafWnDgBZWu5rirG7v2MC6Ssqx8VPdj98Wj/m1MTS5XC3BTBcADjlh9vuMr72wt3S0ZxIAPhS4Sw3f5EcQ+tIALhvyeO6AZLkGBINAE+5huvqt2zNMRTf7wa6WgjbdeDrDyIRAfAIcOy5vgng6SyXAvCAAEUMH+tiA+AToOX7KMSC7/XxASiCCUChAAEA30XgnB+Gcd9RKQPAUw1lvgu+xMrN0HoA068wm6HvBt4vA9kjM6OrROAdawFMAgyzCfqO+2MAH74SgWcc1wIwApwOzdw4dDXz18HsK9HQHOZ/hZ+/z83PHRr7j0O79nXYZugrx2C/BNz8AtWaMZOrfvwp4M7Q83813l8icI90bUYoY03PU5QI3CPsx9f/p7qAAlAACkABKAAFoAAUgAJQAApAASgABaAAFIACUAAKQAEoAAWgABSAAlAACkABKAAFoAAUgAJQAC81/gGuBiKUUCxFcwAAAABJRU5ErkJggg=="


def _default_mark(size: int):
    """Built-in app icon: the Arka mark."""
    from PIL import Image
    im = Image.open(io.BytesIO(base64.b64decode(_MARK_PNG))).convert("RGBA")
    return im.resize((size, size), Image.LANCZOS)


def _logo_source(names=_LOGO_FILES):
    from PIL import Image
    here = Path(__file__).resolve().parent
    for name in names:
        for base in (Path.cwd(), here):
            p = base / name
            if p.exists():
                try:
                    return Image.open(p).convert("RGBA"), p
                except Exception:
                    continue
    return None, None


_ICON_CACHE: dict = {}


def _logo_stamp() -> tuple:
    """Changes whenever a logo file is added, replaced or removed, so a new logo shows without restarting the app."""
    out = []
    here = Path(__file__).resolve().parent
    for name in _LOGO_FILES + _WORDMARK_FILES:
        for base in (Path.cwd(), here):
            p = base / name
            try:
                out.append((str(p), p.stat().st_mtime_ns))
            except OSError:
                pass
    return tuple(out)


def _fresh_cache():
    stamp = _logo_stamp()
    if _ICON_CACHE.get("_stamp") != stamp:
        _ICON_CACHE.clear()
        _ICON_CACHE["_stamp"] = stamp


def logo_image(size: int):
    """Square RGBA logo of `size` px: your file centred on the dark background (transparency filled), or the Arka mark."""
    _fresh_cache()
    if size in _ICON_CACHE:
        return _ICON_CACHE[size]
    from PIL import Image
    src, _ = _logo_source()
    if src is None:
        im = _default_mark(size)
    else:
        side = max(src.size)
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 255))
        canvas.alpha_composite(src, ((side - src.width) // 2, (side - src.height) // 2))
        im = canvas.resize((size, size), Image.LANCZOS)
    _ICON_CACHE[size] = im
    return im


def page_icon():
    """For st.set_page_config(page_icon=...). Falls back to an emoji if Pillow is unavailable."""
    try:
        return logo_image(64)
    except Exception:
        return "📈"


def wordmark_html(height: int = 18) -> str:
    """The ARKA TRADES wordmark: arka_logo.png as an <img>, or crisp HTML text when the file is missing."""
    try:
        src, _ = _logo_source(_WORDMARK_FILES)
    except Exception:
        src = None
    _fresh_cache()
    if src is not None and src.width > src.height * 2:            # a real wordmark (wide), not a square icon
        if "wm" not in _ICON_CACHE:
            im = src.copy()
            if im.height > 160:
                im = im.resize((int(im.width * 160 / im.height), 160), 1)
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            _ICON_CACHE["wm"] = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
        return f'<img class="ak-wm" src="{_ICON_CACHE["wm"]}" alt="Arka Trades" style="height:{height}px;width:auto;display:block">'
    return (f'<span class="ak-wm" style="font:700 {height}px Inter,system-ui,sans-serif;letter-spacing:.14em;color:#fff;white-space:nowrap">'
            f'ARKA<span style="font-weight:500;margin-left:.42em">TRADES</span></span>')


def logo_data_uri(px: int = 64) -> str:
    try:
        buf = io.BytesIO()
        logo_image(px).save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return ""


# ═════════════════════════ the browser bridge component ═════════════════════════
_BRIDGE_HTML = r"""<!doctype html><html><head><meta charset="utf-8"></head><body style="margin:0;background:transparent">
<script>
(function () {
  function send(type, data) {
    window.parent.postMessage(Object.assign({isStreamlitMessage: true, type: type}, data || {}), "*");
  }
  var lastNonce = null, branded = false;

  function storage() { try { var s = window.localStorage; s.getItem("__arka"); return s; } catch (e) { return null; } }
  function readCookie(name) {
    try {
      var hit = document.cookie.split(";").map(function (s) { return s.trim(); })
                .filter(function (s) { return s.indexOf(name + "=") === 0; })[0];
      return hit ? decodeURIComponent(hit.slice(name.length + 1)) : null;
    } catch (e) { return null; }
  }
  function writeCookie(name, value, maxAge) {
    try {
      var secure = location.protocol === "https:" ? "; Secure" : "";
      document.cookie = name + "=" + encodeURIComponent(value) + "; Max-Age=" + maxAge + "; Path=/; SameSite=Lax" + secure;
    } catch (e) {}
  }
  function hostWindows() {                         // the page the visitor sees (top) and the app frame (parent)
    var out = [];
    [function () { return window.top; }, function () { return window.parent; }].forEach(function (get) {
      try { var w = get(); if (w && w.document && w.document.head && out.indexOf(w) < 0) out.push(w); } catch (e) {}
    });
    return out;
  }
  function viewport() {
    var ws = hostWindows();
    try { return ws.length ? ws[0].innerWidth : (screen && screen.width) || null; } catch (e) { return null; }
  }
  function brand(b) {
    var abs = function (f) { return new URL(f + "?v=" + b.v, location.href).href; };
    hostWindows().forEach(function (w) {
      try {
        var d = w.document, head = d.head;
        var put = function (selector, tag, attrs) {
          d.querySelectorAll(selector).forEach(function (n) { n.parentNode.removeChild(n); });
          var el = d.createElement(tag);
          Object.keys(attrs).forEach(function (k) { el.setAttribute(k, attrs[k]); });
          el.setAttribute("data-arka", "1");
          head.appendChild(el);
        };
        put('link[rel="icon"],link[rel="shortcut icon"]', "link", {rel: "icon", type: "image/png", href: abs("icon-64.png")});
        put('link[rel="apple-touch-icon"],link[rel="apple-touch-icon-precomposed"]', "link", {rel: "apple-touch-icon", sizes: "180x180", href: abs("icon-180.png")});
        put('meta[name="theme-color"]', "meta", {name: "theme-color", content: b.theme});
        put('style[data-arka-css]', "style", {"data-arka-css": "1"});
        d.querySelector('style[data-arka-css]').textContent =
          '[class*="viewerBadge"],[class*="profileContainer"],[class*="profilePreview"],[data-testid="stAppDeployButton"],' +
          '[data-testid="manage-app-button"]{display:none !important;}html,body{background:' + b.theme + ';}';
        put('meta[name="apple-mobile-web-app-title"]', "meta", {name: "apple-mobile-web-app-title", content: b.short});
        put('meta[name="application-name"]', "meta", {name: "application-name", content: b.short});
        put('meta[name="apple-mobile-web-app-capable"]', "meta", {name: "apple-mobile-web-app-capable", content: "yes"});
        put('meta[name="mobile-web-app-capable"]', "meta", {name: "mobile-web-app-capable", content: "yes"});
        put('meta[name="apple-mobile-web-app-status-bar-style"]', "meta", {name: "apple-mobile-web-app-status-bar-style", content: "black-translucent"});
        var start = w.location.origin + w.location.pathname;
        var manifest = {name: b.name, short_name: b.short, start_url: start, scope: w.location.origin + "/", display: "standalone",
                        background_color: b.theme, theme_color: b.theme,
                        icons: [{src: abs("icon-192.png"), sizes: "192x192", type: "image/png"},
                                {src: abs("icon-512.png"), sizes: "512x512", type: "image/png"}]};
        var url = URL.createObjectURL(new Blob([JSON.stringify(manifest)], {type: "application/manifest+json"}));
        put('link[rel="manifest"]', "link", {rel: "manifest", href: url});
      } catch (e) {}
    });
  }
  function fx(w) {                                  // premium tap: ripple + light haptic on every button
    try {
      var d = w.document;
      if (d.__arkaFx) return;
      d.__arkaFx = 1;
      var css = d.createElement("style");
      css.setAttribute("data-arka-fx", "1");
      css.textContent = ".ak-ripple{position:absolute;border-radius:50%;pointer-events:none;background:rgba(255,255,255,.16);transform:scale(0);" +
        "animation:akrip .55s ease-out forwards}@keyframes akrip{to{transform:scale(1);opacity:0}}" +
        "button{-webkit-tap-highlight-color:transparent}@media (prefers-reduced-motion:reduce){.ak-ripple{display:none}}";
      d.head.appendChild(css);
      d.addEventListener("pointerdown", function (e) {
        var b = e.target && e.target.closest ? e.target.closest("button") : null;
        if (!b || b.disabled) return;
        try { if (e.pointerType !== "mouse" && w.navigator.vibrate) w.navigator.vibrate(8); } catch (_) {}
        try {
          var r = b.getBoundingClientRect(), z = Math.max(r.width, r.height) * 2.2, s = d.createElement("span");
          s.className = "ak-ripple";
          s.style.cssText = "width:" + z + "px;height:" + z + "px;left:" + (e.clientX - r.left - z / 2) + "px;top:" + (e.clientY - r.top - z / 2) + "px";
          if (w.getComputedStyle(b).position === "static") b.style.position = "relative";
          b.style.overflow = "hidden";
          b.appendChild(s);
          setTimeout(function () { if (s.parentNode) s.parentNode.removeChild(s); }, 650);
        } catch (_) {}
      }, {passive: true, capture: true});
    } catch (e) {}
  }
  function handle(args) {
    if (!branded && args.brand) { brand(args.brand); branded = true; try { fx(window.parent); } catch (e) {} }
    var nonce = args.nonce;
    if (!nonce || nonce === lastNonce) return;          // nothing new to do
    lastNonce = nonce;
    var ls = storage(), token = null, ok = true, err = "", key = args.skey || "arka_auth";
    try {
      if (args.op === "get") {
        token = ls ? ls.getItem(key) : null;
        if (!token) token = readCookie(key);
      } else if (args.op === "set") {
        if (ls) ls.setItem(key, args.value);
        writeCookie(key, args.value, args.max_age || 2592000);
        token = args.value;
      } else if (args.op === "del") {
        if (ls) ls.removeItem(key);
        writeCookie(key, "", 0);
      }
    } catch (e) { ok = false; err = String(e); }
    var standalone = false;
    try { standalone = !!(window.top.navigator.standalone || window.top.matchMedia("(display-mode: standalone)").matches); } catch (e) {}
    send("streamlit:setComponentValue", {dataType: "json",
         value: {nonce: nonce, op: args.op, token: token, ok: ok, err: err, storage: !!ls, width: viewport(), standalone: standalone}});
  }
  window.addEventListener("message", function (ev) {
    if (ev.data && ev.data.type === "streamlit:render") handle(ev.data.args || {});
  });
  send("streamlit:componentReady", {apiVersion: 1});
  send("streamlit:setFrameHeight", {height: 0});
})();
</script></body></html>"""


def _component_dir() -> Path:
    """Write the component (HTML + icons) next to this file; use a temp folder if the repo is read-only."""
    files = {"index.html": _BRIDGE_HTML.encode("utf-8")}
    try:
        for px in (64, 180, 192, 512):
            buf = io.BytesIO()
            logo_image(px).convert("RGB").save(buf, format="PNG")
            files[f"icon-{px}.png"] = buf.getvalue()
    except Exception:
        pass
    for base in (Path(__file__).resolve().parent / "arka_bridge", Path(tempfile.gettempdir()) / "arka_bridge"):
        try:
            base.mkdir(parents=True, exist_ok=True)
            for name, data in files.items():
                p = base / name
                if not p.exists() or p.read_bytes() != data:
                    p.write_bytes(data)
            return base
        except Exception:
            continue
    raise RuntimeError("no writable folder for the auth component")


try:
    _DIR = _component_dir()
    _ICON_VERSION = hashlib.md5(b"".join((_DIR / f"icon-{px}.png").read_bytes() for px in (64, 180, 192, 512)
                                         if (_DIR / f"icon-{px}.png").exists())).hexdigest()[:8]
    _bridge_component = components.declare_component("arka_bridge", path=str(_DIR))
except Exception:  # pragma: no cover - the app still works, it just cannot remember devices
    _DIR, _ICON_VERSION, _bridge_component = None, "0", None


# ═════════════════════════ session logic ═════════════════════════
def _cookie_token():
    try:
        return st.context.cookies.get(COOKIE_NAME)
    except Exception:
        return None


def _queue(op: str, value: str | None = None):
    st.session_state["_ak_cmd"] = {"op": op, "value": value, "nonce": uuid.uuid4().hex[:12]}


def _accept(token, key: bytes):
    """Sign in from a stored token (renews it when it is older than 5 days)."""
    ss = st.session_state
    role = verify_token(token, key)
    if not role or ss.get("logged_in") or ss.get("_no_autologin"):
        return False
    ss["logged_in"], ss["is_admin"] = True, role == "admin"
    ss["_ak_token_days"] = days_left(token)
    left = days_left(token)
    if left is not None and left < RENEW_BELOW_DAYS:
        _queue("set", make_token(role, key, COOKIE_DAYS))
    return True


def bridge(key: bytes) -> None:
    """Call once near the top of every run. Restores the session from this device and runs pending storage commands."""
    ss = st.session_state
    if "_ak_started" not in ss:
        ss["_ak_started"] = time.time()
        ss["_ak_ready"] = False
        _queue("get")
        if _accept(_cookie_token(), key):                   # self-hosted / local: the cookie is readable, no wait
            ss["_ak_ready"] = True
    if _bridge_component is None:
        ss["_ak_ready"] = True
        return
    cmd = ss.get("_ak_cmd") or {}
    try:
        val = _bridge_component(op=cmd.get("op", "noop"), value=cmd.get("value"), nonce=cmd.get("nonce"), skey=COOKIE_NAME,
                                max_age=COOKIE_MAX_AGE, brand={"name": APP_NAME, "short": SHORT_NAME, "theme": THEME_BG, "v": _ICON_VERSION},
                                default=None, key="ak_bridge")
    except Exception:
        ss["_ak_ready"] = True
        return
    if isinstance(val, dict):
        if val.get("width"):
            ss["_ak_width"] = val["width"]
        if cmd and val.get("nonce") == cmd.get("nonce"):    # the browser finished the pending command
            ss.pop("_ak_cmd", None)
            ss["_ak_storage_ok"] = bool(val.get("storage"))
            if val.get("op") == "get":
                ss["_ak_ready"] = True
                _accept(val.get("token"), key)
            elif val.get("op") == "set":
                ss["_ak_ready"] = True
    if not ss["_ak_ready"] and time.time() - ss["_ak_started"] > WAIT_S:
        ss["_ak_ready"] = True


def ready() -> bool:
    return bool(st.session_state.get("_ak_ready"))


def waited() -> float:
    return time.time() - st.session_state.get("_ak_started", time.time())


def remember(role: str, key: bytes) -> None:
    """Keep this device signed in for 30 days (renewed on each visit)."""
    if role not in VALID_ROLES:
        raise ValueError("Invalid authentication role.")
    _queue("set", make_token(role, key, COOKIE_DAYS))
    st.session_state["_no_autologin"] = False
    st.session_state["_ak_token_days"] = COOKIE_DAYS


def forget() -> None:
    """Sign out on this device: remove the stored token and do not restore it again in this session."""
    ss = st.session_state
    _queue("del")
    ss["_no_autologin"] = True
    ss["logged_in"] = False
    ss["is_admin"] = False
    ss.pop("_ak_token_days", None)


def viewport_width():
    w = st.session_state.get("_ak_width")
    try:
        return int(w) if w else None
    except (TypeError, ValueError):
        return None


def device_status() -> str:
    """Plain-English line for the Account page."""
    ss = st.session_state
    if _bridge_component is None:
        return "This server cannot keep devices signed in (the auth component could not be set up)."
    if ss.get("_ak_storage_ok") is False:
        return "This browser blocks site storage (private mode?), so it cannot stay signed in."
    d = ss.get("_ak_token_days")
    return f"This device stays signed in for about {d:.0f} more days, renewed on every visit." if d else "This device is not set to stay signed in."


# Backwards compatibility with app.py v8.1
def auto_login(key: bytes) -> None:
    bridge(key)


def flush() -> None:
    """v1 wrote cookies here; v2 does it inside bridge()."""
    return None

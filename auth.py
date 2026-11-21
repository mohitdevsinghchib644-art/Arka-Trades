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
_MARK_PNG = "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"


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
  var lastPrefs = null;
  var lastResp = {}, navCur = null, navPending = null, navPendAt = 0, seq = 0, expectPop = null, usedBack = null;
  function emit(extra) {                           // report an event to Python without losing the last storage answer
    send("streamlit:setComponentValue", {dataType: "json", value: Object.assign({}, lastResp, extra || {})});
  }
  function tag() { seq += 1; return Date.now().toString(36) + "-" + seq; }

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
        "button{-webkit-tap-highlight-color:transparent}" +
        "[data-ak-sym]{cursor:pointer;-webkit-tap-highlight-color:transparent;transition:opacity .15s,transform .15s}" +
        "[data-ak-sym]:active{transform:scale(.985)}.ak-opening{opacity:.55}@media (prefers-reduced-motion:reduce){.ak-ripple{display:none}}";
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
  // ── in-app history: every page / stock the user opens becomes a browser history entry, so the phone's
  //    back swipe (and the back button) walks back through the app instead of leaving it
  function appWin() {
    try { var w = window.parent; if (w && w.history && w.location && w.location.href) return w; } catch (e) {}
    return null;
  }
  function navParse(search) {
    try {
      var q = new URLSearchParams(search || ""), p = q.get("p");
      return p ? p + "|" + (q.get("s") || "") : null;
    } catch (e) { return null; }
  }
  function urlFor(w, state) {
    var u = new URL(w.location.href), a = state.split("|");
    u.searchParams.set("p", a[0]);
    if (a[1]) u.searchParams.set("s", a[1]); else u.searchParams.delete("s");
    return u.pathname + u.search + u.hash;
  }
  // The last screen is remembered per TAB (sessionStorage): it survives a reload and the phone putting the tab to
  // sleep, and it is gone once the tab is closed, so a newly opened tab starts on Home.
  function tabStore() { try { var s = window.sessionStorage; s.getItem("__arka"); return s; } catch (e) { return null; } }
  function saveNav(state) {
    try { var ts = tabStore(); if (ts) ts.setItem("arka_nav", JSON.stringify({s: state, t: Date.now()})); } catch (e) {}
  }
  function savedNav() {
    try { var ls = storage(); if (ls) ls.removeItem("arka_nav"); } catch (e) {}     // earlier versions kept it for every tab
    try { var ts = tabStore(), raw = ts ? ts.getItem("arka_nav") : null; return raw ? JSON.parse(raw) : null; } catch (e) { return null; }
  }
  function onPop(w) {
    var st = null;
    try { st = (w.history.state && w.history.state.ak) || navParse(w.location.search); } catch (e) {}
    if (expectPop) {                                  // our own history.back() for the in-app Back button: nothing to report
      var exp = expectPop; expectPop = null;
      if (st !== exp) { try { w.history.replaceState(Object.assign({}, w.history.state || {}, {ak: exp}), "", urlFor(w, exp)); } catch (e) {} }
      return;
    }
    if (!st) return;
    navCur = st; navPending = st; navPendAt = Date.now();
    saveNav(st);
    emit({nav: st, navNonce: tag()});
  }
  function navRender(state, back) {
    if (!state) return;                               // Python sends nothing until it has restored the last page
    var w = appWin();
    if (!w) return;
    w.__arkaPop = function () { onPop(w); };          // the newest bridge instance always answers
    if (!w.__arkaPopHooked) {
      w.__arkaPopHooked = 1;
      w.addEventListener("popstate", function () { try { if (w.__arkaPop) w.__arkaPop(); } catch (e) {} });
    }
    if (navPending) {                                 // a back step is on its way to Python: ignore older renders
      if (state === navPending || Date.now() - navPendAt > 5000) navPending = null; else return;
    }
    if (state === navCur) return;
    try {
      var cur = w.history.state || {}, depth = cur.akn || 0, data = Object.assign({}, cur, {ak: state});
      if (navCur === null) { data.akn = depth; w.history.replaceState(data, "", urlFor(w, state)); }
      else if (back && back !== usedBack && depth > 0) {   // the in-app Back button: step the browser back too
        usedBack = back; expectPop = state;
        setTimeout(function () { expectPop = null; }, 1500);
        w.history.back();
      } else { data.akn = depth + 1; w.history.pushState(data, "", urlFor(w, state)); }
    } catch (e) {}
    if (back) usedBack = back;
    navCur = state;
    saveNav(state);
  }
  function tapOpen(w) {                             // any element with data-ak-sym opens that stock
    try {
      var d = w.document;
      d.__arkaOpen = function (sym) { emit({open: sym, openNonce: tag()}); };
      if (d.__arkaTapHooked) return;
      d.__arkaTapHooked = 1;
      d.addEventListener("click", function (e) {
        var t = e.target;
        if (!t || !t.closest) return;
        var el = t.closest("[data-ak-sym]");
        if (!el) return;
        var inner = t.closest("details,summary,button,input,select,textarea,a,label");
        if (inner && inner !== el && el.contains(inner)) return;   // chips and buttons inside a row keep their own job
        var sym = (el.getAttribute("data-ak-sym") || "").trim();
        if (!sym) return;
        e.preventDefault(); e.stopPropagation();
        try { if (w.navigator.vibrate) w.navigator.vibrate(8); } catch (_) {}
        el.classList.add("ak-opening");
        setTimeout(function () { try { el.classList.remove("ak-opening"); } catch (_) {} }, 1200);
        try { if (d.__arkaOpen) d.__arkaOpen(sym); } catch (_) {}
      }, true);
    } catch (e) {}
  }
  function handle(args) {
    if (!branded && args.brand) { brand(args.brand); branded = true; try { fx(window.parent); } catch (e) {} }
    try { var aw = appWin(); if (aw) tapOpen(aw); } catch (e) {}
    navRender(args.nav || "", args.navback || "");
    if (typeof args.prefs === "string" && args.prefs && args.prefs !== lastPrefs) {      // small per-device settings (e.g. the RS index)
      try { var pls = storage(); if (pls) pls.setItem("arka_prefs", args.prefs); } catch (e) {}
      lastPrefs = args.prefs;
    }
    var nonce = args.nonce;
    if (!nonce || nonce === lastNonce) return;          // nothing new to do
    lastNonce = nonce;
    var ls = storage(), token = null, ok = true, err = "", key = args.skey || "arka_auth", loc = null, saved = null, prefs = null;
    try {
      if (args.op === "get") {
        token = ls ? ls.getItem(key) : null;
        if (!token) token = readCookie(key);
        try { var hw = appWin(); loc = hw ? navParse(hw.location.search) : null; } catch (e) {}
        saved = savedNav();
        try { prefs = ls ? ls.getItem("arka_prefs") : null; } catch (e) {}
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
    lastResp = {nonce: nonce, op: args.op, token: token, ok: ok, err: err, storage: !!ls, width: viewport(), standalone: standalone,
                loc: loc, saved: saved, prefs: prefs};
    emit({});
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
    # the name carries a hash of the bridge code, so a tab left open across a deploy reloads the bridge
    _bridge_component = components.declare_component("arka_bridge_" + hashlib.md5(_BRIDGE_HTML.encode("utf-8")).hexdigest()[:6],
                                                     path=str(_DIR))
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


def bridge(key: bytes, nav: str | None = None, back: str | None = None, prefs: str | None = None) -> None:
    """Call once near the top of every run. Restores the session from this device and runs pending storage commands.

    nav: "page|SYMBOL" for the screen being shown (empty until the app has restored the last screen). The browser
    turns each new value into a history entry, so back / the phone's back swipe return to the previous screen.
    back: a one-off tag set when the in-app Back button caused this screen (the browser then steps back instead).
    prefs: a small JSON string of per-device settings to remember in this browser (handed back once in "_ak_boot").
    Events from the browser land in session state: "_ak_nav_in" (back/forward to a screen), "_ak_open_in" (a stock
    was tapped) and "_ak_boot" (where this device was last, read once with the sign-in token)."""
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
                                nav=nav or "", navback=back or "", prefs=prefs or "", default=None, key="ak_bridge")
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
                ss["_ak_boot"] = {"loc": val.get("loc"), "saved": val.get("saved"), "prefs": val.get("prefs")}
                _accept(val.get("token"), key)
            elif val.get("op") == "set":
                ss["_ak_ready"] = True
    if isinstance(val, dict):                               # browser events, each handled once
        nn = val.get("navNonce")
        if nn and nn != ss.get("_ak_nav_seen"):
            ss["_ak_nav_seen"] = nn
            if isinstance(val.get("nav"), str):
                ss["_ak_nav_in"] = val["nav"]
        on = val.get("openNonce")
        if on and on != ss.get("_ak_open_seen"):
            ss["_ak_open_seen"] = on
            if isinstance(val.get("open"), str):
                ss["_ak_open_in"] = val["open"]
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

"""
command_bar.py — Arka Trades command bar ("/" to jump anywhere)

Fluere-style: press "/" anywhere on the page, an overlay opens, type
a page name or a symbol from either watchlist, arrow keys move the
selection, Enter jumps there. Esc closes without navigating.

WHY THIS IS A COMPONENT, NOT st.text_input:
A global "/" keypress listener has to be attached to the document,
active whether or not any Streamlit widget has focus. Streamlit's
native widgets only capture keystrokes while focused — there's no
way to catch a bare "/" press from a plain st.text_input. This uses
components.v1.html to inject real JS with a document-level keydown
listener, which is the only way to get that behavior in Streamlit.

HOW NAVIGATION IS COMMUNICATED BACK TO PYTHON:
No custom bidirectional component build step (that needs a JS build
pipeline this project doesn't have). Instead, selecting a result sets
`window.top.location.href` with a `?goto=<page_key>` or `?jump=<symbol>`
query param and reloads — app.py reads st.query_params once per run
(see consume_command_bar_navigation() below) and clears it after
acting, the same pattern app.py already uses for `?login=1` on the
landing page. This is a full page reload per navigation, not a soft
SPA transition — an accepted tradeoff for zero new build tooling.

SEARCHABLE ITEMS:
- Every real nav page (label + the page key app.py already uses)
- Every symbol in the user's watchlist and the Arka admin watchlist,
  routed to page=research with the symbol pre-filled (so jumping to
  a ticker means "show me its research page", the closest equivalent
  to Fluere's "$ + ticker = chart" behavior available without a
  dedicated per-symbol chart route existing yet)
"""

import json
import streamlit as st
import streamlit.components.v1 as components

# (label, page_key) — page_key must match app.py's st.session_state.page values
_NAV_ITEMS = [
    ("Dashboard", "home"),
    ("Scanner", "scanner"),
    ("Alerts", "alerts"),
    ("Research", "research"),
    ("Arka AI", "analysis"),
    ("Smart Screener", "smart_scan"),
    ("Market Breadth", "breadth"),
    ("Heatmap", "heatmap"),
    ("Profile", "profile"),
    ("Settings", "settings"),
    ("Contact", "contact"),
]


def render_command_bar(watchlist: list[str] = None, admin_watchlist: list[str] = None, tokens: dict = None):
    """
    Renders the invisible-until-summoned command bar overlay. Call
    this ONCE per script run, anywhere after st.set_page_config —
    position in the page doesn't matter since it's a fixed overlay.

    tokens: the same TERM_TOKENS dict app.py builds for research_page.py,
    reused here so the overlay's colors never drift from the rest of
    the terminal reskin.
    """
    t = tokens or {}
    amber = t.get("amber", "#FF9500")
    panel = t.get("panel", "#0A0A0A")
    border = t.get("border", "#2A2A2A")
    ivory = t.get("ivory", "#E8E8E8")
    t2 = t.get("t2", "#8A8A8A")
    mono = t.get("mono", "'JetBrains Mono',monospace")
    font = t.get("font", "'Plus Jakarta Sans','Inter',sans-serif")

    symbols = sorted(set((watchlist or []) + (admin_watchlist or [])))

    items = [{"type": "page", "label": label, "key": key, "sub": "PAGE"} for label, key in _NAV_ITEMS]
    items += [{"type": "symbol", "label": sym, "key": sym, "sub": "SYMBOL"} for sym in symbols]
    items_json = json.dumps(items)

    html = f"""
<div id="cmdbar-root"></div>
<style>
#cmdbar-overlay {{
    position: fixed; inset: 0; background: rgba(0,0,0,0.75); z-index: 99999;
    display: none; align-items: flex-start; justify-content: center;
    padding-top: 12vh; font-family: {font};
}}
#cmdbar-box {{
    width: 560px; max-width: 90vw; background: {panel}; border: 1px solid {amber};
    box-shadow: 0 8px 40px rgba(0,0,0,0.6);
}}
#cmdbar-input-row {{ display: flex; align-items: center; padding: 12px 16px; border-bottom: 1px solid {border}; }}
#cmdbar-prefix {{ color: {amber}; font-family: {mono}; font-size: 15px; margin-right: 10px; font-weight: 700; }}
#cmdbar-input {{
    flex: 1; background: transparent; border: none; outline: none; color: {ivory};
    font-family: {mono}; font-size: 15px;
}}
#cmdbar-hint {{ color: {t2}; font-size: 10px; font-family: {mono}; }}
#cmdbar-results {{ max-height: 320px; overflow-y: auto; }}
.cmdbar-item {{
    display: flex; justify-content: space-between; align-items: center;
    padding: 9px 16px; cursor: pointer; border-bottom: 1px solid {border};
}}
.cmdbar-item.active {{ background: rgba(255,149,0,0.12); }}
.cmdbar-item .lbl {{ color: {ivory}; font-size: 13px; font-weight: 600; }}
.cmdbar-item .sub {{ color: {t2}; font-size: 9px; font-family: {mono}; letter-spacing: 1px; }}
.cmdbar-item.active .sub {{ color: {amber}; }}
#cmdbar-empty {{ padding: 20px 16px; color: {t2}; font-size: 12px; text-align: center; }}
</style>
<div id="cmdbar-overlay">
  <div id="cmdbar-box">
    <div id="cmdbar-input-row">
      <span id="cmdbar-prefix">/</span>
      <input id="cmdbar-input" autocomplete="off" placeholder="Jump to a page or symbol..." />
      <span id="cmdbar-hint">ESC</span>
    </div>
    <div id="cmdbar-results"></div>
  </div>
</div>
<script>
(function() {{
    const ALL_ITEMS = {items_json};
    const overlay = document.getElementById('cmdbar-overlay');
    const input = document.getElementById('cmdbar-input');
    const results = document.getElementById('cmdbar-results');
    let filtered = [];
    let activeIdx = 0;

    function render(list) {{
        results.innerHTML = '';
        if (list.length === 0) {{
            results.innerHTML = '<div id="cmdbar-empty">No matches</div>';
            return;
        }}
        list.forEach((item, i) => {{
            const row = document.createElement('div');
            row.className = 'cmdbar-item' + (i === activeIdx ? ' active' : '');
            row.innerHTML = '<span class="lbl">' + item.label + '</span><span class="sub">' + item.sub + '</span>';
            row.addEventListener('click', () => navigate(item));
            results.appendChild(row);
        }});
    }}

    function filterItems(q) {{
        q = q.trim().toLowerCase();
        if (!q) return ALL_ITEMS.slice(0, 12);
        return ALL_ITEMS.filter(it => it.label.toLowerCase().includes(q)).slice(0, 12);
    }}

    function navigate(item) {{
        const target = window.top || window;
        const url = new URL(target.location.href);
        if (item.type === 'page') {{
            url.searchParams.set('goto', item.key);
        }} else {{
            url.searchParams.set('jump', item.key);
        }}
        target.location.href = url.toString();
    }}

    function open() {{
        overlay.style.display = 'flex';
        input.value = '';
        filtered = filterItems('');
        activeIdx = 0;
        render(filtered);
        setTimeout(() => input.focus(), 30);
    }}

    function close() {{
        overlay.style.display = 'none';
    }}

    document.addEventListener('keydown', function(e) {{
        // Only trigger on bare "/" when not already typing in some
        // other input/textarea on the page (avoids hijacking normal
        // text fields elsewhere in the Streamlit app).
        const tag = (e.target.tagName || '').toLowerCase();
        const typing = tag === 'input' || tag === 'textarea' || e.target.isContentEditable;
        if (e.key === '/' && !typing && overlay.style.display !== 'flex') {{
            e.preventDefault();
            open();
        }} else if (e.key === 'Escape' && overlay.style.display === 'flex') {{
            close();
        }}
    }});

    input.addEventListener('input', function() {{
        filtered = filterItems(input.value);
        activeIdx = 0;
        render(filtered);
    }});

    input.addEventListener('keydown', function(e) {{
        if (e.key === 'ArrowDown') {{
            e.preventDefault();
            activeIdx = Math.min(activeIdx + 1, filtered.length - 1);
            render(filtered);
        }} else if (e.key === 'ArrowUp') {{
            e.preventDefault();
            activeIdx = Math.max(activeIdx - 1, 0);
            render(filtered);
        }} else if (e.key === 'Enter') {{
            e.preventDefault();
            if (filtered[activeIdx]) navigate(filtered[activeIdx]);
        }} else if (e.key === 'Escape') {{
            close();
        }}
    }});

    overlay.addEventListener('click', function(e) {{
        if (e.target === overlay) close();
    }});
}})();
</script>
"""
    # height=0 + a fixed-position overlay inside means this renders
    # invisibly until summoned, without reserving layout space on the
    # page the way a normal-height component would.
    components.html(html, height=0)


def consume_command_bar_navigation():
    """
    Call ONCE near the top of app.py, after st.session_state defaults
    are set but before the page router runs. Reads ?goto=<page_key> or
    ?jump=<symbol> left by the command bar's navigate() call, applies
    it to session_state, and clears the query param — same pattern
    app.py already uses for ?login=1 on the landing page.

    Returns True if a navigation was consumed (caller may want to
    st.rerun() in that case, though clearing query_params and letting
    the natural page-load proceed is usually enough since this runs
    before the router reads st.session_state.page).
    """
    goto = st.query_params.get("goto")
    jump = st.query_params.get("jump")

    if goto:
        st.session_state.page = goto
        st.query_params.clear()
        return True

    if jump:
        # Route a symbol jump to the Research page with the symbol
        # pre-filled and auto-triggered, mirroring Fluere's "type a
        # ticker, get taken straight to its data" behavior.
        st.session_state.page = "research"
        st.session_state["research_query"] = jump
        st.session_state["research_last_query"] = jump
        st.session_state.pop("research_data", None)  # force a fresh fetch for the jumped-to symbol
        st.query_params.clear()
        return True

    return False

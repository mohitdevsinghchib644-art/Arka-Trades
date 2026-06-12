"""
smart_scan_page.py  —  Arka Trades Smart Screener
===================================================
Step 1 : Setup Manager  — create / edit / delete setups with reference image
Step 2 : Math Filter    — fast pandas indicator scan across NSE universe
Step 3 : AI Vision      — Gemini Vision audits shortlisted charts

Dependencies (already in your app):
    yfinance, pandas, numpy, google-generativeai, matplotlib, streamlit, supabase
"""

import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import base64, io, time
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import google.generativeai as genai
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

try:
    from PIL import Image as PILImage
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ── Colours (match Arka Trades theme) ────────────────────────────────────────
NAVY   = "#0A1D4B"
IVORY  = "#F7EBE0"
GOLD   = "#C8A96A"
GREEN  = "#00B37A"
RED    = "#E84545"
DARK   = "#04080F"
DARK2  = "#060D1A"
DARK3  = "#091525"
BORDER = "#0F2040"
T2     = "#8A9AB5"

# ── NSE Universe ─────────────────────────────────────────────────────────────
NSE_UNIVERSE = [
    "RELIANCE","TCS","HDFCBANK","INFY","ICICIBANK","HINDUNILVR","SBIN",
    "BHARTIARTL","KOTAKBANK","BAJFINANCE","LT","WIPRO","HCLTECH","ASIANPAINT",
    "AXISBANK","MARUTI","NESTLEIND","SUNPHARMA","ULTRACEMCO","TITAN","TECHM",
    "POWERGRID","NTPC","ONGC","BAJAJFINSV","TATAMOTORS","DIVISLAB","DRREDDY",
    "CIPLA","ADANIPORTS","INDUSINDBK","JSWSTEEL","TATASTEEL","HINDALCO",
    "COALINDIA","BRITANNIA","GRASIM","BPCL","HEROMOTOCO","IOC","EICHERMOT",
    "SHREECEM","APOLLOHOSP","BAJAJ-AUTO","TATACONSUM","M&M",
    "BANDHANBNK","FEDERALBNK","IDFCFIRSTB","PNB","BANKBARODA","CANBK",
    "UNIONBANK","AUBANK","DCBBANK","CHOLAFIN","MUTHOOTFIN","MANAPPURAM",
    "SBICARD","HDFCLIFE","ICICIPRULI","HDFCAMC","LICHSGFIN","PNBHOUSING",
    "NAUKRI","MPHASIS","COFORGE","PERSISTENT","LTIM","LTTS","KPITTECH",
    "HAPPSTMNDS","TATAELXSI","CYIENT","BIRLASOFT","SONATSOFTW",
    "ALKEM","AUROPHARMA","IPCALAB","LALPATHLAB","BIOCON","GLENMARK",
    "ERIS","AJANTPHARM","GRANULES","LAURUSLABS","LUPIN","TORNTPHARM",
    "FORTIS","MAXHEALTH","NARAYANA","KIMS",
    "TVSMOTOR","ASHOKLEY","ESCORTS","MRF","APOLLOTYRE","CEAT",
    "BALKRISIND","BOSCHLTD","MOTHERSON","SONACOMS","MINDAIND",
    "ACC","AMBUJACEM","RAMCOCEM","JKCEMENT","DALMIABL",
    "NCC","IRCON","RVNL","HGINFRA","PNCINFRA",
    "TATAPOWER","TORNTPOWER","CESC","NHPC","SJVN","RECLTD","PFC",
    "ADANIGREEN","ADANIENT","JIOFIN",
    "VEDL","NMDC","SAIL","HINDZINC","NATIONALUM",
    "ITC","EMAMILTD","RADICO","COLPAL","PIDILITIND","GODREJCP",
    "MARICO","DABUR","BERGEPAINT","HAVELLS","VOLTAS","TRENT","DMART",
    "SRF","PIIND","FINEORG","VINATI","DEEPAKNTR","NAVINFLUOR",
    "HAL","BEL","BHEL","SIEMENS","ABB","CUMMINSIND","THERMAX",
    "DLF","GODREJPROP","OBEROIRLTY","PRESTIGE","BRIGADE","LODHA",
    "IRCTC","ZOMATO","NYKAA","DELHIVERY","PVRINOX",
    "IRFC","GMRINFRA","HUDCO",
]
NSE_UNIVERSE = list(dict.fromkeys(NSE_UNIVERSE))


# ══════════════════════════════════════════════════════════════════════════════
# SUPABASE HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _load_setups(supabase) -> list:
    try:
        res = (supabase.table("scan_setups")
               .select("*")
               .order("created_at", desc=True)
               .execute())
        return res.data or []
    except Exception as e:
        st.error(f"Load error: {e}")
        return []


def _save_setup(supabase, data: dict) -> bool:
    try:
        sid = data.pop("id", None)
        if sid:
            supabase.table("scan_setups").update(data).eq("id", sid).execute()
        else:
            supabase.table("scan_setups").insert(data).execute()
        return True
    except Exception as e:
        st.error(f"Save error: {e}")
        return False


def _delete_setup(supabase, setup_id) -> bool:
    try:
        supabase.table("scan_setups").delete().eq("id", setup_id).execute()
        return True
    except Exception as e:
        st.error(f"Delete error: {e}")
        return False


def _upload_image(supabase, file_bytes: bytes, filename: str) -> str:
    """Upload image to Supabase Storage and return public URL."""
    try:
        path = f"setups/{filename}"
        supabase.storage.from_("setup-images").upload(
            path, file_bytes,
            file_options={"content-type": "image/png", "upsert": "true"}
        )
        res = supabase.storage.from_("setup-images").get_public_url(path)
        return res
    except Exception as e:
        st.error(f"Image upload error: {e}")
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# MATH ENGINE  (pure pandas — no TA-Lib)
# ══════════════════════════════════════════════════════════════════════════════

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _ema(close: pd.Series, period: int) -> pd.Series:
    return close.ewm(span=period, adjust=False).mean()


def _sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period).mean()


def _atr(high, low, close, period: int = 14) -> pd.Series:
    prev = close.shift(1)
    tr   = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_bulk(symbols_tuple: tuple, period: str = "60d") -> dict:
    symbols  = list(symbols_tuple)
    ns_syms  = [s + ".NS" for s in symbols]
    results  = {}
    BATCH    = 150

    for start in range(0, len(ns_syms), BATCH):
        batch_ns    = ns_syms[start:start + BATCH]
        batch_plain = symbols[start:start + BATCH]

        if len(batch_ns) == 1:
            try:
                df = yf.download(batch_ns[0], period=period, interval="1d",
                                 auto_adjust=True, progress=False)
                if not df.empty and len(df) >= 20:
                    results[batch_plain[0]] = df
            except Exception:
                pass
            continue

        try:
            raw = yf.download(batch_ns, period=period, interval="1d",
                              auto_adjust=True, progress=False, threads=True)
            if raw.empty:
                continue

            if isinstance(raw.columns, pd.MultiIndex):
                available = raw.columns.get_level_values(1).unique().tolist()
                for sym, ns in zip(batch_plain, batch_ns):
                    if ns not in available:
                        continue
                    try:
                        df = raw.xs(ns, level=1, axis=1).dropna(how="all")
                        if not df.empty and len(df) >= 20 and "Close" in df.columns:
                            results[sym] = df
                    except Exception:
                        pass
        except Exception:
            pass

        time.sleep(0.3)

    return results


def _calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["rsi"]       = _rsi(df["Close"], 14)
    df["sma_20"]    = _sma(df["Close"], 20)
    df["sma_50"]    = _sma(df["Close"], 50)
    df["ema_20"]    = _ema(df["Close"], 20)
    df["atr"]       = _atr(df["High"], df["Low"], df["Close"], 14)
    df["vol_avg20"] = df["Volume"].rolling(20).mean()
    df["vol_ratio"] = df["Volume"] / df["vol_avg20"].replace(0, np.nan)
    df["roc_5"]     = df["Close"].pct_change(5) * 100
    return df


def _apply_filter(df: pd.DataFrame, setup: dict, symbol: str):
    if df is None or len(df) < 22:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    def safe(col):
        v = last.get(col, np.nan)
        return float(v) if pd.notna(v) else np.nan

    rsi       = safe("rsi")
    sma_20    = safe("sma_20")
    sma_50    = safe("sma_50")
    vol_ratio = safe("vol_ratio")
    roc_5     = safe("roc_5")
    atr       = safe("atr")
    close      = float(last["Close"])
    prev_close = float(prev["Close"])
    prev_high  = float(prev["High"])
    prev_low   = float(prev["Low"])

    if np.isnan(rsi):
        return None

    price_min = float(setup.get("price_min") or 0)
    price_max = float(setup.get("price_max") or 99999)
    if not (price_min <= close <= price_max):
        return None

    rsi_min = float(setup.get("rsi_min") or 0)
    rsi_max = float(setup.get("rsi_max") or 100)
    if not (rsi_min <= rsi <= rsi_max):
        return None

    vol_min = float(setup.get("volume_multiplier") or 0)
    if vol_min > 0 and not np.isnan(vol_ratio):
        if vol_ratio < vol_min:
            return None

    if setup.get("require_above_sma20") and not np.isnan(sma_20):
        if close <= sma_20:
            return None
    if setup.get("require_above_sma50") and not np.isnan(sma_50):
        if close <= sma_50:
            return None
    if setup.get("require_below_sma20") and not np.isnan(sma_20):
        if close >= sma_20:
            return None
    if setup.get("require_breakout"):
        if close <= prev_high:
            return None

    roc_min = float(setup.get("roc_min") or -999)
    roc_max = float(setup.get("roc_max") or 999)
    if not np.isnan(roc_5):
        if not (roc_min <= roc_5 <= roc_max):
            return None

    chg_pct = ((close - prev_close) / prev_close) * 100 if prev_close else 0
    atr_pct = (atr / close * 100) if (not np.isnan(atr) and close > 0) else 0

    return {
        "symbol":    symbol,
        "close":     round(close, 2),
        "chg_pct":   round(chg_pct, 2),
        "rsi":       round(rsi, 1),
        "vol_ratio": round(vol_ratio, 2) if not np.isnan(vol_ratio) else 0.0,
        "roc_5":     round(roc_5, 2) if not np.isnan(roc_5) else 0.0,
        "atr_pct":   round(atr_pct, 2),
        "pdh":       round(prev_high, 2),
        "pdl":       round(prev_low, 2),
        "sma_20":    round(sma_20, 2) if not np.isnan(sma_20) else 0.0,
        "sma_50":    round(sma_50, 2) if not np.isnan(sma_50) else 0.0,
        "df":        df,
    }


def run_math_scan(symbols: list, setup: dict, progress_cb=None) -> tuple:
    if progress_cb:
        progress_cb(0.0, f"Downloading data for {len(symbols)} stocks…")

    data_dict = _fetch_bulk(tuple(symbols), "60d")

    if progress_cb:
        progress_cb(0.35, f"Data ready for {len(data_dict)} stocks — running filter…")

    shortlist, failed = [], []
    total = len(data_dict) or 1

    for i, (sym, df) in enumerate(data_dict.items()):
        try:
            df_ind = _calculate_indicators(df)
            result = _apply_filter(df_ind, setup, sym)
            if result:
                shortlist.append(result)
        except Exception:
            pass

        if progress_cb and i % 15 == 0:
            progress_cb(0.35 + 0.55 * (i / total), f"Scanning… {i}/{total}")

    fetched = set(data_dict.keys())
    failed  = [s for s in symbols if s not in fetched]

    if progress_cb:
        progress_cb(0.95, f"Filter done — {len(shortlist)} stocks passed")

    shortlist.sort(key=lambda x: x["rsi"])
    return shortlist, failed


# ══════════════════════════════════════════════════════════════════════════════
# CHART GENERATOR  (matplotlib — no kaleido, works on Streamlit Cloud)
# ══════════════════════════════════════════════════════════════════════════════

def _make_chart_image(symbol: str, df: pd.DataFrame) -> bytes:
    df_plot = df.tail(60).copy()
    n       = len(df_plot)
    fig_w   = max(14, n * 0.20)

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(fig_w, 5.5),
        gridspec_kw={"height_ratios": [3, 1]},
        facecolor="#04080F"
    )

    for ax in (ax1, ax2):
        ax.set_facecolor("#04080F")
        for spine in ax.spines.values():
            spine.set_color("#0F2040")
        ax.tick_params(colors="#8A9AB5", labelsize=8)
        ax.grid(axis="y", color="#0F2040", linewidth=0.5, linestyle="--", alpha=0.7)

    cw = 0.55
    for i, (_, row) in enumerate(df_plot.iterrows()):
        o, h, l, c = row["Open"], row["High"], row["Low"], row["Close"]
        col = "#00B37A" if c >= o else "#E84545"
        ax1.plot([i, i], [l, h], color=col, linewidth=0.9, zorder=1)
        rect = mpatches.Rectangle(
            (i - cw / 2, min(o, c)), cw, max(abs(c - o), (h - l) * 0.008),
            facecolor=col, edgecolor=col, linewidth=0, zorder=2
        )
        ax1.add_patch(rect)

    xs = list(range(n))
    if "sma_20" in df_plot.columns:
        ax1.plot(xs, df_plot["sma_20"].values, color="#C8A96A", linewidth=1.1,
                 alpha=0.85, label="SMA 20")
    if "sma_50" in df_plot.columns:
        ax1.plot(xs, df_plot["sma_50"].values, color="#7B9FFF", linewidth=1.1,
                 alpha=0.85, label="SMA 50")

    ax1.set_xlim(-1, n + 1)
    pmin = df_plot["Low"].min(); pmax = df_plot["High"].max()
    pad  = (pmax - pmin) * 0.05
    ax1.set_ylim(pmin - pad, pmax + pad)

    step  = max(1, n // 8)
    ticks = list(range(0, n, step))
    ax1.set_xticks(ticks)
    ax1.set_xticklabels([df_plot.index[i].strftime("%d %b") for i in ticks],
                        rotation=45, ha="right", fontsize=7)
    ax1.legend(fontsize=7, framealpha=0.3, labelcolor="#8A9AB5")
    ax1.set_title(f"{symbol}  ·  NSE Daily  ·  60 Days",
                  color="#C8A96A", fontsize=10, fontweight="bold",
                  fontfamily="monospace", pad=6)

    vol_colors = ["#00B37A" if c >= o else "#E84545"
                  for c, o in zip(df_plot["Close"], df_plot["Open"])]
    ax2.bar(xs, df_plot["Volume"].values, color=vol_colors, alpha=0.65, width=0.7)
    if "vol_avg20" in df_plot.columns:
        ax2.plot(xs, df_plot["vol_avg20"].values, color="#8A9AB5",
                 linewidth=0.9, linestyle="--")
    ax2.set_xlim(-1, n + 1)
    ax2.set_xticks([])
    ax2.set_ylabel("Vol", color="#8A9AB5", fontsize=7)

    plt.tight_layout(pad=1.0)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, facecolor="#04080F", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


# ══════════════════════════════════════════════════════════════════════════════
# GEMINI VISION AUDIT
# ══════════════════════════════════════════════════════════════════════════════

_PROMPT_TEMPLATE = """\
You are a professional technical analyst for NSE Indian equity markets.

Analyse this 60-day daily candlestick chart for: {symbol}

Chart panels:
• Top    → Candlestick + SMA-20 (gold line) + SMA-50 (blue line)
• Bottom → Volume bars with 20-day average line

━━━ REFERENCE SETUP DESCRIPTION ━━━
{visual_rules}

━━━ TASK ━━━
Does this chart visually match the described setup?
Respond in EXACTLY this format (one value per line):

VERDICT: STRONG BUY | WATCH | REJECT
SCORE: [0-10]
PATTERN: [pattern name]
KEY_FINDING: [one sentence — most important observation]
VISUAL_ANALYSIS: [2-3 sentences: trend, structure, key levels]
RISK: [main risk visible in chart]
ACTION: [specific note e.g. Entry above ₹X, stop ₹Y]

Be direct. Base verdict ONLY on the chart.\
"""


def _audit_one(symbol: str, chart_bytes: bytes, visual_rules: str,
               ref_image_url: str, gemini_key: str) -> dict:
    genai.configure(api_key=gemini_key)
    model  = genai.GenerativeModel("gemini-2.5-flash-preview-05-20")
    prompt = _PROMPT_TEMPLATE.format(
        symbol=symbol,
        visual_rules=visual_rules.strip() or "General technical analysis — identify best setups."
    )

    content = []

    # Reference setup image (if saved)
    if ref_image_url and HAS_PIL and HAS_REQUESTS:
        try:
            r = _requests.get(ref_image_url, timeout=8)
            if r.status_code == 200:
                ref_img = PILImage.open(io.BytesIO(r.content)).convert("RGB")
                content.append(ref_img)
                content.append("↑ This is the REFERENCE PATTERN IMAGE the user uploaded as their ideal setup.")
        except Exception:
            pass

    # Stock chart
    if HAS_PIL:
        chart_img = PILImage.open(io.BytesIO(chart_bytes)).convert("RGB")
        content.append(chart_img)
    else:
        # Fallback: base64 inline data
        content.append({
            "mime_type": "image/png",
            "data": base64.b64encode(chart_bytes).decode("utf-8")
        })

    content.append(prompt)

    try:
        response = model.generate_content(content)
        raw = response.text.strip()
        return _parse_audit(symbol, raw)
    except Exception as exc:
        return {
            "symbol": symbol, "verdict": "ERROR", "score": 0,
            "key_finding": str(exc)[:120], "pattern": "N/A",
            "visual_analysis": "", "risk": "", "action": "", "raw": str(exc)
        }


def _parse_audit(symbol: str, text: str) -> dict:
    result = dict(symbol=symbol, verdict="UNKNOWN", score=5.0,
                  pattern="N/A", key_finding="", visual_analysis="",
                  risk="", action="", raw=text)
    for line in text.strip().splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().upper(); val = val.strip()
        if key == "VERDICT":
            v = val.upper()
            if "STRONG BUY" in v:  result["verdict"] = "STRONG BUY"
            elif "WATCH"    in v:  result["verdict"] = "WATCH"
            elif "REJECT"   in v:  result["verdict"] = "REJECT"
        elif key == "SCORE":
            try: result["score"] = float(val.split("/")[0])
            except: pass
        elif key == "PATTERN":         result["pattern"]         = val
        elif key == "KEY_FINDING":     result["key_finding"]     = val
        elif key == "VISUAL_ANALYSIS": result["visual_analysis"] = val
        elif key == "RISK":            result["risk"]            = val
        elif key == "ACTION":          result["action"]          = val
    return result


def run_ai_audit(candidates: list, setup: dict, gemini_key: str,
                 max_stocks: int = 15, progress_cb=None) -> list:
    top          = candidates[:max_stocks]
    visual_rules = setup.get("visual_rules", "")
    ref_url      = setup.get("reference_image_url", "") or ""
    results      = []

    def _process(candidate):
        sym = candidate["symbol"]
        try:
            chart_bytes = _make_chart_image(sym, candidate["df"])
            audit       = _audit_one(sym, chart_bytes, visual_rules, ref_url, gemini_key)
        except Exception as exc:
            audit = {
                "symbol": sym, "verdict": "ERROR", "score": 0,
                "key_finding": str(exc)[:120], "pattern": "N/A",
                "visual_analysis": "", "risk": "", "action": "", "raw": ""
            }
        return {**candidate, **audit}

    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = {ex.submit(_process, c): c for c in top}
        done    = 0
        for future in as_completed(futures):
            done += 1
            if progress_cb:
                progress_cb(done / len(top), f"AI auditing… {done}/{len(top)}")
            try:
                results.append(future.result())
            except Exception:
                pass

    if progress_cb:
        progress_cb(1.0, f"✅ Gemini audit complete — {len(results)} charts analysed")

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results


# ══════════════════════════════════════════════════════════════════════════════
# UI HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _section(title: str):
    st.markdown(f"""
    <div style="display:flex;align-items:center;gap:16px;margin:26px 0 14px;">
        <div style="flex:1;height:1px;background:{BORDER};"></div>
        <div style="font-family:'Bebas Neue',sans-serif;font-size:15px;
             letter-spacing:5px;color:{GOLD};white-space:nowrap;">{title}</div>
        <div style="flex:1;height:1px;background:{BORDER};"></div>
    </div>""", unsafe_allow_html=True)


def _verdict_colors(verdict: str):
    if verdict == "STRONG BUY":
        return GREEN, "rgba(0,179,122,0.07)", "rgba(0,179,122,0.4)"
    elif verdict == "WATCH":
        return GOLD,  "rgba(200,169,106,0.06)", "rgba(200,169,106,0.35)"
    else:
        return RED,   "rgba(232,69,69,0.06)",  "rgba(232,69,69,0.35)"


# ══════════════════════════════════════════════════════════════════════════════
# SETUP MANAGER
# ══════════════════════════════════════════════════════════════════════════════

def _render_setup_form(supabase, existing: dict = None):
    is_edit = existing is not None
    prefix  = f"edit_{existing.get('id','x')}" if is_edit else "new"
    btn_lbl = "💾 Update Setup" if is_edit else "✅ Save Setup"

    with st.form(f"form_{prefix}", clear_on_submit=not is_edit):

        c1, c2 = st.columns(2)
        name = c1.text_input(
            "Setup Name *",
            value=existing.get("name", "") if is_edit else "",
            placeholder="e.g. Bull Flag, Oversold Bounce",
            key=f"name_{prefix}",
        )
        desc = c2.text_input(
            "Short Description",
            value=existing.get("description", "") if is_edit else "",
            placeholder="e.g. RSI pullback + volume surge",
            key=f"desc_{prefix}",
        )

        # ── Reference Image ──────────────────────────────────────────────────
        st.markdown(
            f"<div style='font-family:Bebas Neue;font-size:12px;letter-spacing:3px;"
            f"color:{GOLD};margin:14px 0 6px;'>📸 REFERENCE CHART IMAGE</div>",
            unsafe_allow_html=True
        )
        img_col1, img_col2 = st.columns([2, 1])
        with img_col1:
            uploaded_img = st.file_uploader(
                "Upload your ideal setup chart",
                type=["png", "jpg", "jpeg"],
                key=f"img_{prefix}",
                help="Screenshot of what your ideal setup looks like. Gemini will compare every stock chart against this."
            )
        with img_col2:
            if is_edit and existing.get("reference_image_url"):
                st.markdown(f"<div style='font-size:11px;color:{T2};margin-top:8px;'>Current image:</div>",
                            unsafe_allow_html=True)
                st.image(existing["reference_image_url"], width=120)

        # ── Visual Rules ─────────────────────────────────────────────────────
        st.markdown(
            f"<div style='font-family:Bebas Neue;font-size:12px;letter-spacing:3px;"
            f"color:{GOLD};margin:14px 0 6px;'>🤖 VISUAL RULES (Plain English for Gemini)</div>",
            unsafe_allow_html=True
        )
        visual_rules = st.text_area(
            "Describe what the ideal chart should look like",
            value=existing.get("visual_rules", "") if is_edit else "",
            height=130,
            key=f"vr_{prefix}",
            placeholder=(
                "Examples:\n"
                "• Look for a clean double bottom or cup & handle pattern\n"
                "• Price should form higher highs and higher lows\n"
                "• Volume should spike on green breakout candles\n"
                "• Avoid stocks with large overhead rejection wicks\n"
                "• Prefer smooth trending charts — reject choppy sideways action"
            ),
        )

        # ── Price Range ──────────────────────────────────────────────────────
        st.markdown(
            f"<div style='font-family:Bebas Neue;font-size:12px;letter-spacing:3px;"
            f"color:{GOLD};margin:14px 0 6px;'>💰 PRICE RANGE  (₹)</div>",
            unsafe_allow_html=True
        )
        p1, p2 = st.columns(2)
        price_min = p1.number_input(
            "Min Price (₹)", 0.0, 100000.0,
            float(existing.get("price_min") or 0) if is_edit else 0.0,
            50.0, key=f"pmin_{prefix}"
        )
        price_max = p2.number_input(
            "Max Price (₹)", 0.0, 100000.0,
            float(existing.get("price_max") or 99999) if is_edit else 99999.0,
            50.0, key=f"pmax_{prefix}"
        )

        # ── Math Rules ───────────────────────────────────────────────────────
        st.markdown(
            f"<div style='font-family:Bebas Neue;font-size:12px;letter-spacing:3px;"
            f"color:{GOLD};margin:14px 0 6px;'>📐 MATHEMATICAL RULES</div>",
            unsafe_allow_html=True
        )
        r1, r2 = st.columns(2)
        rsi_min = r1.slider(
            "RSI — Minimum", 0, 100,
            int(existing.get("rsi_min") or 0) if is_edit else 0,
            key=f"rmin_{prefix}"
        )
        rsi_max = r2.slider(
            "RSI — Maximum", 0, 100,
            int(existing.get("rsi_max") or 100) if is_edit else 100,
            key=f"rmax_{prefix}"
        )

        v1, v2 = st.columns(2)
        vol_mult = v1.number_input(
            "Min Volume Multiplier (vs 20-day avg)", 0.0, 20.0,
            float(existing.get("volume_multiplier") or 0) if is_edit else 0.0,
            0.1, key=f"vol_{prefix}",
            help="1.5 = today's volume ≥ 1.5× the 20-day average. 0 = ignore."
        )
        roc_min = v2.number_input(
            "Min 5-day ROC %", -30.0, 30.0,
            float(existing.get("roc_min") or -999) if is_edit else -999.0,
            0.5, key=f"rocmin_{prefix}"
        )

        t1, t2, t3, t4 = st.columns(4)
        above_sma20 = t1.checkbox(
            "Above SMA(20)",
            value=bool(existing.get("require_above_sma20")) if is_edit else False,
            key=f"asma20_{prefix}"
        )
        above_sma50 = t2.checkbox(
            "Above SMA(50)",
            value=bool(existing.get("require_above_sma50")) if is_edit else False,
            key=f"asma50_{prefix}"
        )
        breakout = t3.checkbox(
            "Close > Prev High",
            value=bool(existing.get("require_breakout")) if is_edit else False,
            key=f"bo_{prefix}"
        )
        below_sma20 = t4.checkbox(
            "Below SMA(20) ↓",
            value=bool(existing.get("require_below_sma20")) if is_edit else False,
            key=f"bsma_{prefix}"
        )

        submitted = st.form_submit_button(btn_lbl, use_container_width=True, type="primary")

        if submitted:
            if not name.strip():
                st.error("Setup Name is required.")
                return

            ref_url = existing.get("reference_image_url", "") if is_edit else ""
            if uploaded_img is not None:
                img_bytes = uploaded_img.read()
                filename  = f"{name.strip().replace(' ','_')}_{int(time.time())}.png"
                new_url   = _upload_image(supabase, img_bytes, filename)
                if new_url:
                    ref_url = new_url

            payload = dict(
                name=name.strip(),
                description=desc.strip(),
                reference_image_url=ref_url,
                visual_rules=visual_rules.strip(),
                price_min=float(price_min),
                price_max=float(price_max),
                rsi_min=float(rsi_min),
                rsi_max=float(rsi_max),
                volume_multiplier=float(vol_mult),
                roc_min=float(roc_min),
                require_above_sma20=above_sma20,
                require_above_sma50=above_sma50,
                require_below_sma20=below_sma20,
                require_breakout=breakout,
            )
            if is_edit:
                payload["id"] = existing["id"]

            if _save_setup(supabase, payload):
                st.success(f"✅ {'Updated' if is_edit else 'Created'}: {name}")
                time.sleep(0.5)
                st.rerun()


def _render_setup_manager(supabase):
    st.markdown(f"""
    <div style="background:{DARK2};border:1px solid {BORDER};
         border-left:4px solid {GOLD};border-radius:14px;
         padding:16px 22px;margin-bottom:20px;">
        <div style="font-family:'Bebas Neue',sans-serif;font-size:18px;
             letter-spacing:4px;color:{GOLD};margin-bottom:4px;">⚙️ SETUP MANAGER</div>
        <div style="font-size:12px;color:{T2};line-height:1.7;">
            Create your scanning setups here. Each setup has its own
            <strong style="color:{IVORY}">reference image</strong>,
            <strong style="color:{IVORY}">visual rules</strong>, and
            <strong style="color:{IVORY}">math filters</strong>.
            Gemini compares every shortlisted chart against your saved setup.
        </div>
    </div>""", unsafe_allow_html=True)

    setups = _load_setups(supabase)

    with st.expander("➕  Create New Setup", expanded=len(setups) == 0):
        _render_setup_form(supabase)

    if not setups:
        st.info("No setups yet — create your first one above!")
        return

    _section(f"{len(setups)} SAVED SETUPS")

    for setup in setups:
        with st.expander(f"📋  {setup['name']}  ·  {setup.get('description','')}", expanded=False):
            img_col, form_col = st.columns([1, 3])

            with img_col:
                if setup.get("reference_image_url"):
                    st.image(setup["reference_image_url"], use_container_width=True)
                    st.caption("Reference image")
                else:
                    st.markdown(
                        f"<div style='background:{DARK3};border:1px dashed {BORDER};"
                        f"border-radius:8px;padding:24px;text-align:center;"
                        f"font-size:11px;color:{T2};'>No image yet</div>",
                        unsafe_allow_html=True
                    )

            with form_col:
                _render_setup_form(supabase, existing=setup)

            st.markdown(
                f"<div style='font-size:11px;color:{T2};margin-top:4px;'>"
                f"RSI {setup.get('rsi_min',0):.0f}–{setup.get('rsi_max',100):.0f}  ·  "
                f"₹{setup.get('price_min',0):.0f}–₹{setup.get('price_max',99999):.0f}  ·  "
                f"Vol ≥{setup.get('volume_multiplier',0):.1f}×</div>",
                unsafe_allow_html=True
            )

            if st.button("🗑️ Delete Setup", key=f"del_{setup['id']}"):
                if _delete_setup(supabase, setup["id"]):
                    st.success("Deleted")
                    time.sleep(0.4)
                    st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# RESULT CARDS
# ══════════════════════════════════════════════════════════════════════════════

def _render_result_card(res: dict, setup: dict):
    verdict = res.get("verdict", "UNKNOWN")
    score   = res.get("score", 0)
    vc, vbg, vbd = _verdict_colors(verdict)

    chg = res.get("chg_pct", 0)
    cc  = GREEN if chg >= 0 else RED
    arr = "▲" if chg >= 0 else "▼"

    label = f"{res['symbol']}  ·  {verdict}  ·  Score {score}/10  ·  {res.get('pattern','')}"

    with st.expander(label, expanded=(verdict == "STRONG BUY")):
        left, right = st.columns([1, 2])

        with left:
            st.markdown(f"""
            <div style="background:{vbg};border:1px solid {vbd};
                 border-radius:12px;padding:16px 14px;">
                <div style="font-family:'Bebas Neue';font-size:20px;
                     letter-spacing:3px;color:{vc};margin-bottom:6px;">{verdict}</div>
                <div style="font-family:'JetBrains Mono';font-size:26px;
                     font-weight:700;color:{IVORY};line-height:1.1;margin-bottom:6px;">
                     ₹{res.get('close',0):,.2f}</div>
                <div style="font-size:13px;color:{cc};margin-bottom:10px;">
                     {arr} {abs(chg):.2f}%  ·  RSI {res.get('rsi',0):.0f}</div>
                <div style="font-family:'Inter';font-size:10px;letter-spacing:2px;
                     color:{T2};margin-bottom:2px;">AI SCORE</div>
                <div style="font-family:'JetBrains Mono';font-size:28px;
                     font-weight:900;color:{vc};line-height:1;">
                     {score:.0f}<span style="font-size:16px;color:{T2};">/10</span></div>
                <hr style="border-color:{BORDER};margin:10px 0;">
                <div style="font-size:11px;color:{T2};line-height:1.9;">
                    Vol ratio: <strong style="color:{IVORY};">{res.get('vol_ratio',0):.1f}×</strong><br>
                    5D ROC: <strong style="color:{cc};">{res.get('roc_5',0):.1f}%</strong><br>
                    ATR: <strong style="color:{IVORY};">{res.get('atr_pct',0):.2f}%</strong><br>
                    PDH: <strong style="color:{IVORY};">₹{res.get('pdh',0):,.2f}</strong>
                </div>
            </div>""", unsafe_allow_html=True)

        with right:
            def _row(color, heading, body):
                st.markdown(
                    f"<div style='font-size:10px;letter-spacing:2px;color:{color};"
                    f"font-weight:700;margin-top:10px;'>{heading}</div>"
                    f"<div style='font-size:13px;color:{IVORY};line-height:1.6;"
                    f"margin-bottom:2px;'>{body}</div>",
                    unsafe_allow_html=True
                )

            if setup.get("reference_image_url"):
                rcol1, rcol2 = st.columns([1, 2])
                with rcol1:
                    st.image(setup["reference_image_url"], caption="Your reference", width=120)
                with rcol2:
                    _row(GOLD, "🔑 KEY FINDING",    res.get("key_finding", "—"))
                    _row(GOLD, "📈 VISUAL ANALYSIS", res.get("visual_analysis", "—"))
            else:
                _row(GOLD, "🔑 KEY FINDING",    res.get("key_finding", "—"))
                _row(GOLD, "📈 VISUAL ANALYSIS", res.get("visual_analysis", "—"))

            _row(RED,   "⚠️ RISK",   res.get("risk", "—"))
            _row(GREEN, "🎯 ACTION", res.get("action", "—"))

        if "df" in res:
            try:
                chart_bytes = _make_chart_image(res["symbol"], res["df"])
                st.image(chart_bytes, use_container_width=True)
            except Exception:
                st.caption("Chart unavailable.")


# ══════════════════════════════════════════════════════════════════════════════
# SCAN PAGE
# ══════════════════════════════════════════════════════════════════════════════

def _render_scan_page(supabase, gemini_key: str):
    setups = _load_setups(supabase)

    if not setups:
        st.warning("No setups found. Go to **⚙️ Manage Setups** tab and create one first!")
        return

    st.markdown(f"""
    <div style="background:{DARK2};border:1px solid {BORDER};
         border-left:4px solid {GOLD};border-radius:14px;
         padding:14px 20px;margin-bottom:18px;">
        <div style="font-family:'Bebas Neue';font-size:18px;
             letter-spacing:4px;color:{GOLD};margin-bottom:2px;">🔍 SMART SCAN</div>
        <div style="font-size:12px;color:{T2};">
            Pick a saved setup → Run Scan → Fast math filter → Gemini Vision audits top candidates
        </div>
    </div>""", unsafe_allow_html=True)

    _section("YOUR SETUPS — TAP TO SELECT")

    cols         = st.columns(min(len(setups), 3))
    selected_key = st.session_state.get("selected_setup_id")

    for i, setup in enumerate(setups):
        with cols[i % 3]:
            is_sel  = str(setup["id"]) == str(selected_key)
            bd      = GOLD if is_sel else BORDER
            bg      = "rgba(200,169,106,0.08)" if is_sel else DARK2
            sel_txt = "✅ SELECTED" if is_sel else "TAP TO SELECT"
            sel_col = GOLD if is_sel else T2

            if setup.get("reference_image_url"):
                st.image(setup["reference_image_url"], use_container_width=True)

            st.markdown(f"""
            <div style="background:{bg};border:1px solid {bd};
                 border-radius:12px;padding:14px;margin-bottom:8px;text-align:center;">
                <div style="font-family:'Bebas Neue';font-size:16px;
                     letter-spacing:3px;color:{GOLD};margin-bottom:4px;">{setup['name']}</div>
                <div style="font-size:11px;color:{T2};margin-bottom:8px;">
                    {setup.get('description','') or ''}</div>
                <div style="font-size:10px;color:{T2};line-height:1.8;">
                    RSI {setup.get('rsi_min',0):.0f}–{setup.get('rsi_max',100):.0f}
                    &nbsp;·&nbsp;
                    ₹{setup.get('price_min',0):.0f}–₹{setup.get('price_max',99999):.0f}
                </div>
                <div style="font-size:9px;letter-spacing:2px;color:{sel_col};
                     margin-top:8px;font-weight:700;">{sel_txt}</div>
            </div>""", unsafe_allow_html=True)

            if st.button("Select", key=f"sel_{setup['id']}", use_container_width=True):
                st.session_state["selected_setup_id"] = str(setup["id"])
                st.rerun()

    selected_setup = None
    if selected_key:
        selected_setup = next((s for s in setups if str(s["id"]) == str(selected_key)), None)

    if not selected_setup:
        st.info("Select a setup above to start scanning.")
        return

    _section(f"SCAN WITH: {selected_setup['name'].upper()}")

    c1, c2 = st.columns([2, 1])
    with c1:
        universe_opt = st.selectbox(
            "Scan Universe",
            ["Full NSE (~420 stocks)", "Your Watchlist", "Arka Watchlist"],
            key="scan_universe"
        )
    with c2:
        max_ai = st.number_input(
            "Max AI Audits", 3, 20, 10, 1, key="scan_max_ai",
            help="After math filter, top N charts sent to Gemini Vision"
        )

    if universe_opt.startswith("Full"):
        universe = NSE_UNIVERSE
    elif universe_opt == "Your Watchlist":
        universe = st.session_state.get("watchlist", [])
        if not universe:
            st.warning("Upload your watchlist in Scanner first.")
            return
    else:
        universe = st.session_state.get("admin_watchlist", [])
        if not universe:
            st.warning("Arka Watchlist not available yet.")
            return

    if not gemini_key:
        st.warning("⚠️ GEMINI_KEY not found in secrets — AI vision audit will be skipped.")

    if st.button("🚀  RUN SCAN", type="primary", use_container_width=True, key="run_scan"):

        for k in ("scan_math_results", "scan_ai_results"):
            st.session_state.pop(k, None)

        prog = st.progress(0.0)
        stat = st.empty()

        def _prog(pct, msg):
            prog.progress(min(float(pct), 1.0))
            stat.markdown(f"**{msg}**")

        shortlist, failed = run_math_scan(universe, selected_setup, _prog)

        if not shortlist:
            prog.progress(1.0); stat.empty()
            st.warning("No stocks passed the math filter. Try relaxing the rules.")
            if failed:
                with st.expander(f"{len(failed)} symbols had no data"):
                    st.write(", ".join(failed[:60]))
            st.stop()

        st.session_state["scan_math_results"] = shortlist
        stat.markdown(f"✅ Math filter: **{len(shortlist)} stocks** qualified from {len(universe)} scanned")

        if gemini_key and selected_setup.get("visual_rules"):
            ai_prog = st.progress(0.0)
            ai_stat = st.empty()

            def _ai_prog(pct, msg):
                ai_prog.progress(min(float(pct), 1.0))
                ai_stat.markdown(f"**{msg}**")

            ai_results = run_ai_audit(shortlist, selected_setup, gemini_key,
                                      int(max_ai), _ai_prog)

            ai_prog.empty(); ai_stat.empty()
            st.session_state["scan_ai_results"] = ai_results
            strong = sum(1 for r in ai_results if r.get("verdict") == "STRONG BUY")
            st.success(
                f"🎯 Gemini audit complete — {len(ai_results)} charts analysed, "
                f"**{strong} Strong Buy** candidates."
            )
        else:
            reason = "no GEMINI_KEY" if not gemini_key else "no visual rules in this setup"
            st.info(f"AI vision skipped ({reason}).")

        prog.empty(); stat.empty()
        time.sleep(0.3)
        st.rerun()

    math_results = st.session_state.get("scan_math_results")
    ai_results   = st.session_state.get("scan_ai_results")

    if math_results is None:
        return

    _section("SCAN SUMMARY")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Universe Scanned", len(universe))
    m2.metric("Math Filter ✓",   len(math_results))
    m3.metric("AI Audited",      len(ai_results) if ai_results else "—")
    if ai_results:
        strong = sum(1 for r in ai_results if r.get("verdict") == "STRONG BUY")
        m4.metric("Strong Buys 🎯", strong)
    else:
        m4.metric("AI Status", "Skipped")

    if ai_results:
        _section("AI VISION RESULTS")

        sort_opt = st.selectbox(
            "Sort by",
            ["AI Score ▼", "RSI ▲ (oversold first)", "Volume Ratio ▼", "% Change ▼"],
            key="sort_results"
        )
        filt_v = st.radio(
            "Show",
            ["All", "Strong Buy only", "Watch only", "Reject only"],
            horizontal=True, key="filt_verdict"
        )

        ordered = ai_results[:]
        if "Score"   in sort_opt: ordered.sort(key=lambda x: x.get("score", 0),     reverse=True)
        elif "RSI"   in sort_opt: ordered.sort(key=lambda x: x.get("rsi", 50))
        elif "Volume"in sort_opt: ordered.sort(key=lambda x: x.get("vol_ratio", 0), reverse=True)
        else:                     ordered.sort(key=lambda x: x.get("chg_pct", 0),   reverse=True)

        if filt_v == "Strong Buy only": ordered = [r for r in ordered if r.get("verdict") == "STRONG BUY"]
        elif filt_v == "Watch only":    ordered = [r for r in ordered if r.get("verdict") == "WATCH"]
        elif filt_v == "Reject only":   ordered = [r for r in ordered if r.get("verdict") == "REJECT"]

        if not ordered:
            st.info("No results match this filter.")
        else:
            for res in ordered:
                _render_result_card(res, selected_setup)

    _section(f"MATH SHORTLIST  ({len(math_results)} STOCKS)")
    rows = []
    for r in math_results:
        chg = r["chg_pct"]
        rows.append({
            "Symbol":    r["symbol"],
            "Price":     f"₹{r['close']:,.2f}",
            "Chg %":     f"{'▲' if chg>=0 else '▼'} {abs(chg):.2f}%",
            "RSI":       f"{r['rsi']:.0f}",
            "Vol Ratio": f"{r['vol_ratio']:.2f}×",
            "5D ROC":    f"{r['roc_5']:.1f}%",
            "ATR %":     f"{r['atr_pct']:.2f}%",
            "PDH":       f"₹{r['pdh']:,.2f}",
            "PDL":       f"₹{r['pdl']:,.2f}",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True,
                 hide_index=True, height=320)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def render_smart_scanner(supabase):
    """Call this from app.py when page == 'smart_scan'."""
    gemini_key = st.secrets.get("GEMINI_KEY", "")

    scan_tab, setup_tab = st.tabs(["🔍  Run Scan", "⚙️  Manage Setups"])

    with scan_tab:
        _render_scan_page(supabase, gemini_key)

    with setup_tab:
        _render_setup_manager(supabase)

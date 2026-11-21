"""
Arka Trades — setup_language.py (v1): reads a trading setup written in plain words and turns it into exact, testable rules.

    parse("Close above 50 EMA and 200 SMA, breakout of 20 day high, volume 1.5x average.
           Stop loss below the signal candle low. Exit when close below 21 EMA.")

Nothing is guessed silently. parse() returns
    rules     conditions that must all be true at a day's close for a buy signal (backtest_engine rule dicts)
    ex        how the trade is entered, stopped and exited (backtest_engine exit settings)
    book      capital settings found in the text (risk per trade)
    read      [(your words, what it means, note)]   every phrase that was understood; note says when a number was assumed
    missed    [(your words, why)]                   every phrase that was NOT understood and is therefore not tested
    assumed   [text]                                defaults used because the setup did not say (stop, exit)

It is a fixed set of patterns, not an AI model: the same sentence always gives the same rules, and the page shows the
reading before anything runs. Long trades only, daily candles.
"""
from __future__ import annotations

import re

import numpy as np

import backtest_engine as bt
import quant_data as qd

# ═════════════════════════ extra conditions the plain-language reader can produce ═════════════════════════


def _col(x):
    return np.asarray(x)[:, None]


def _vol_avg(p, n):
    return bt._cached(p, f"vavg{int(n)}", lambda: qd.shift(qd.sma(p["v"].astype(np.float64), int(n)), 1))


def _streak(p, a):
    c = p["c"].astype(np.float64)
    hit = (c < qd.shift(c, 1)) if a["dir"] == "down" else (c > qd.shift(c, 1))
    return qd.roll_min(hit.astype(np.float64), int(a["n"])) >= 1


def _idx_ret(p, n):
    ic = p["idx_c"].astype(np.float64)[:, None]
    return ic / qd.shift(ic, int(n)) - 1


def _macd(p, a):
    if "x_macd_line" not in p:
        c = p["c"].astype(np.float64)
        line = qd.ema(c, 12) - qd.ema(c, 26)
        p["x_macd_line"], p["x_macd_sig"] = line, qd.ema(line, 9)
    m, g = p["x_macd_line"], p["x_macd_sig"]
    if a["how"] == "zero":
        return m > 0
    if a["how"] == "above":
        return m > g
    return (m > g) & (qd.shift(m, 1) <= qd.shift(g, 1))


EXTRA = {
    "x_macd": ("Momentum", "MACD", [], _macd),
    "x_close_below_ma": ("Trend", "Close below a moving average", [], lambda p, a, s: p["c"] < bt.ma(p, a["kind"], a["n"])),
    "x_cross_above_ma": ("Trend", "Close crosses above a moving average", [],
                         lambda p, a, s: (p["c"] > bt.ma(p, a["kind"], a["n"])) & (qd.shift(p["c"], 1) <= qd.shift(bt.ma(p, a["kind"], a["n"]), 1))),
    "x_ma_above_ma": ("Trend", "One average above another", [], lambda p, a, s: bt.ma(p, a["k1"], a["n1"]) > bt.ma(p, a["k2"], a["n2"])),
    "x_ma_cross_up": ("Trend", "One average crosses above another", [],
                      lambda p, a, s: (bt.ma(p, a["k1"], a["n1"]) > bt.ma(p, a["k2"], a["n2"]))
                      & (qd.shift(bt.ma(p, a["k1"], a["n1"]), 1) <= qd.shift(bt.ma(p, a["k2"], a["n2"]), 1))),
    "x_beat_nifty": ("Momentum", "Return beats NIFTY 50", [], lambda p, a, s: (p["c"] / qd.shift(p["c"], int(a["n"])) - 1) > _idx_ret(p, a["n"])),
    "x_red": ("Candle", "Red candle", [], lambda p, a, s: p["c"] < p["o"]),
    "x_vol_mult_n": ("Volume", "Volume against its N-day average", [], lambda p, a, s: p["v"] >= a["x"] * _vol_avg(p, a["n"])),
    "x_vol_gt_prev": ("Volume", "Volume above the previous day", [], lambda p, a, s: p["v"] > qd.shift(p["v"], 1)),
    "x_rsi_cross_up": ("Momentum", "RSI crosses above a level", [],
                       lambda p, a, s: (bt.rsi(p, a["n"]) > a["x"]) & (qd.shift(bt.rsi(p, a["n"]), 1) <= a["x"])),
    "x_rsi_between": ("Momentum", "RSI between two levels", [], lambda p, a, s: (bt.rsi(p, a["n"]) >= a["lo"]) & (bt.rsi(p, a["n"]) <= a["hi"])),
    "x_streak": ("Candle", "Closes lower or higher several days running", [], _streak),
    "x_close_above_prev_close": ("Candle", "Close above the previous close", [], lambda p, a, s: p["c"] > qd.shift(p["c"], 1)),
    "x_price_max": ("Volume", "Price at most", [], lambda p, a, s: p["c"] <= a["x"]),
}
for _k, _v in EXTRA.items():
    bt.RULES.setdefault(_k, _v)

NO_TIME_STOP = 100000          # sessions; effectively "hold until another exit"
ASSUMED_HOLD = 60

# ═════════════════════════ words → normal form ═════════════════════════
NUM = r"(\d+(?:\.\d+)?)"
MA = r"(\d+)\s*(ema|sma)"
CMP_UP = r"(?:above|over|>=|>|greater than|more than|higher than|at least|atleast|crossed above|crosses above|cross above|exceeds?)"
CMP_DN = r"(?:below|under|<=|<|less than|lower than|at most|atmost)"


def _n(x):
    f = float(x)
    return int(f) if f == int(f) else f


def normalise(text: str) -> str:
    t = " " + str(text or "").lower().replace("’", "'").replace("×", "x").replace("₹", " rs ").replace("–", "-").replace("—", "-") + " "
    t = re.sub(r"\bper ?cent\b|\bpercent\b|\bpct\b", "%", t)
    t = re.sub(r"\bstoploss\b|\bstop-loss\b", "stop loss", t)
    t = re.sub(r"\btake profit\b|\bbook profits?\b|\bprofit target\b|\btgt\b|\btp\b", "target", t)
    t = re.sub(r"\byesterday'?s?\b|\bprior day'?s?\b|\bprevious day'?s?\b|\bprev\.? day'?s?\b|\blast day'?s?\b", "previous day", t)
    t = re.sub(r"\b52[- ]?(?:w|wk|wks|weeks?)\b|\bone year\b|\b1 year\b|\byearly\b", "52 week", t)
    t = re.sub(r"\ball[- ]time high\b|\bath\b", "all time high", t)
    t = re.sub(r"\bexponential moving averages?\b|\bexponential averages?\b|\bemas\b", "ema", t)
    t = re.sub(r"\bsimple moving averages?\b|\bmoving averages?\b|\bdmas?\b|\bsmas\b|\bday averages?\b", "sma", t)
    t = re.sub(r"\b(ema|sma)\s*\(?\s*(\d+)\s*\)?", r"\2 \1 ", t)                         # ema21, ema(21) -> 21 ema
    t = re.sub(r"\b(\d+)\s*-?\s*(?:day|days|d|period|periods)\s*-?\s*(ema|sma)\b", r"\1 \2", t)
    t = re.sub(r"\b(\d+)\s*-?\s*(?:week|weeks|wk)\s*-?\s*(ema|sma)\b", lambda m: f"{int(m.group(1)) * 5} {m.group(2)}", t)
    t = re.sub(r"\b(\d+)\s*ma\b", r"\1 sma", t)                                            # 20ma, 20 ma
    t = re.sub(r"\b(\d+)(ema|sma)\b", r"\1 \2", t)
    t = re.sub(r"\b(\d+)\s*-?\s*(?:week|weeks|wk|wks)\b(?!\s*(?:high|low))", lambda m: f"{int(m.group(1)) * 5} day", t)
    t = re.sub(r"\b(\d+)\s*-?\s*(?:month|months|mo|mth)\b", lambda m: f"{int(m.group(1)) * 21} day", t)
    t = re.sub(r"\b(?!52)(\d+)\s*-?\s*(?:week|weeks|wk|wks)\b(?=\s*(?:high|low))", lambda m: f"{int(m.group(1)) * 5} day", t)
    t = re.sub(r"\b(\d+)\s*-?\s*(?:days|day|d|sessions?|bars?|candles)\b", r"\1 day", t)
    t = re.sub(r"\b(\d+(?:\.\d+)?)\s*(?:x|times)\b", r"\1 x", t)
    t = re.sub(r"\b1\s*:\s*(\d+(?:\.\d+)?)\b", r"target \1 r", t)                           # 1:2 risk reward
    t = re.sub(r"\b(\d+(?:\.\d+)?)\s*r\b", r"\1 r", t)
    t = re.sub(r"\bbetween\s+" + NUM + r"\s*%?\s*(?:and|to|-)\s*" + NUM, r"between \1 to \2", t)
    t = re.sub(r"\b" + NUM + r"\s*(?:and|&|,|/)\s*" + NUM + r"\s*(ema|sma)\b", r"\1 \3 and \2 \3", t)   # 50 and 200 sma
    t = re.sub(r"(\d)\s*%", r"\1 %", t)
    return re.sub(r"[ \t]+", " ", t)


def clauses(text: str) -> list:
    """[(line number, clause)] split on new lines, full stops, commas, semicolons, 'and', 'with', 'then'."""
    out = []
    for ln, line in enumerate(re.split(r"[\n\r]+", text)):
        parts = re.split(r";|\.(?!\d)|,(?!\d)|\band\b|&|\+|\bthen\b|\bwith\b|\bwhile\b|\bbut\b|\balso\b|\bplus\b|\bon\b(?=\s+\w+\s+vol)", line)
        for part in parts:
            c = part.strip(" \t:;-.,!?()[]\"'*•")
            if c:
                out.append((ln, c))
    return out


# ═════════════════════════ reading one clause ═════════════════════════
def _ma(m, i=1):
    return ("EMA" if m.group(i + 1) == "ema" else "SMA"), int(m.group(i))


def _ma_txt(kind, n):
    return f"{n}-day {'EMA' if kind == 'EMA' else 'average (SMA)'}"


def _rule(rtype, **kw):
    r = {"type": rtype}
    r.update(kw)
    return r


EXIT_WORDS = re.compile(r"\b(stop loss|stop|sl|target|exit|sell|trail|trailing|square off|book|hold|holding|time stop|breakeven|break even|risk reward|risk|rr|skip|avoid)\b")


def _entry(c: str):
    """→ (rule, meaning, note) or None."""
    s = re.sub(r"^(?:buy|entry|enter|go long|long|signal)\s*(?:when|if|on|:|-)?\s*", "", c).strip()
    s = re.sub(r"^(?:when|if|the|stock|share|it|should be|must be|is|has|have|a|an)\s+", "", s).strip()
    mkt = bool(re.search(r"\b(nifty|market|index|sensex)\b", s))

    # relative strength / market
    m = re.search(r"(?:outperform\w*|beat\w*|stronger than|strength (?:vs|versus|against|over)|rs (?:vs|versus|against))\s*(?:the\s*)?(?:nifty|market|index)(?:.*?(\d+) day)?", s)
    if m:
        n = int(m.group(1)) if m.group(1) else 63
        return (_rule("x_beat_nifty", n=n), f"Return over the last {n} sessions is higher than NIFTY 50's",
                None if m.group(1) else "No period given, so I used 63 sessions (about 3 months).")
    if re.search(r"\brs\s*(?:rating|rank)\b|\brelative strength (?:rating|rank)\b", s):
        return ("miss", "RS rating ranks a stock against a whole index, which this test does not load. Write \"outperforming Nifty over 3 months\" instead.")
    if mkt:
        m = re.search(r"(?:nifty|market|index)\D*?" + CMP_UP + r"\s*(?:its|the|their)?\s*" + MA, s)
        if m:
            k, n = _ma(m)
            return (_rule("nifty_above_ma", kind=k, n=n), f"NIFTY 50 closes above its {_ma_txt(k, n)}", None)
        if re.search(r"(?:uptrend|bullish|positive|strong)", s):
            return (_rule("nifty_above_ma", kind="EMA", n=50), "NIFTY 50 closes above its 50-day EMA", "\"Market in uptrend\" has no exact meaning, so I used NIFTY above its 50-day EMA.")
        return None

    # moving averages
    m = re.search(r"golden cross", s)
    if m:
        return (_rule("x_ma_cross_up", k1="SMA", n1=50, k2="SMA", n2=200), "50-day average crosses above the 200-day average today", None)
    m = re.search(MA + r"\s*(?:has |have |just )?(?:cross\w*|crossover)\s*(?:above|over|up through)?\s*(?:the\s*)?" + MA, s)
    if m:
        k1, n1 = _ma(m, 1); k2, n2 = _ma(m, 3)
        return (_rule("x_ma_cross_up", k1=k1, n1=n1, k2=k2, n2=n2), f"{_ma_txt(k1, n1)} crosses above the {_ma_txt(k2, n2)} today", None)
    m = re.search(MA + r"\s*(?:is |stays |remains )?" + CMP_UP + r"\s*(?:the\s*)?" + MA, s)
    if m:
        k1, n1 = _ma(m, 1); k2, n2 = _ma(m, 3)
        return (_rule("x_ma_above_ma", k1=k1, n1=n1, k2=k2, n2=n2), f"{_ma_txt(k1, n1)} is above the {_ma_txt(k2, n2)}", None)
    m = re.search(MA + r"\s*(?:is |are )?(?:rising|sloping up\w*|slop\w* up|trending up|pointing up|going up|upward\w*|turning up)(?:.*?(\d+) day)?", s)
    if m:
        k, n = _ma(m)
        d = int(m.group(3)) if m.group(3) else 20
        return (_rule("ma_rising", kind=k, n=n, days=d), f"{_ma_txt(k, n)} is higher than it was {d} sessions ago",
                None if m.group(3) else "\"Rising\" needs a look-back, so I compare with 20 sessions ago.")
    m = re.search(r"(?:cross\w*|crossover|reclaim\w*|moves? back)\s*(?:above|over|up through)?\s*(?:the\s*)?" + MA, s)
    if m:
        k, n = _ma(m)
        return (_rule("x_cross_above_ma", kind=k, n=n), f"Close crosses above the {_ma_txt(k, n)} today (it closed at or below it the day before)", None)
    m = re.search(r"(?:not more than|no more than|at most|within|less than|max(?:imum)?)\s*" + NUM + r"\s*%\s*(?:above|over|from|of|away from)\s*(?:the\s*)?" + MA, s)
    if m:
        k, n = _ma(m, 2)
        return (_rule("not_extended", kind=k, n=n, pct=float(m.group(1))), f"Close is at most {_n(m.group(1))}% above the {_ma_txt(k, n)}", None)
    m = re.search(r"(?:pull\w*\s*back|pullback|dip\w*|retrace\w*|retest\w*|touch\w*|tests?|tag\w*|bounce\w*|support|comes? (?:back )?(?:down )?to|near|at)\s*(?:to|from|off|at|on|of|near|the|its|\s)*" + MA, s)
    if m:
        k, n = _ma(m)
        return (_rule("touch_ma", kind=k, n=n, pct=1.5, days=3), f"The low came within 1.5% of the {_ma_txt(k, n)} in the last 3 sessions",
                "\"Pullback to\" needs numbers, so I used: low within 1.5% of the average, any of the last 3 sessions.")
    m = re.search(CMP_UP + r"\s*(?:the|its|all|both)?\s*" + MA, s)
    if m:
        k, n = _ma(m)
        return (_rule("close_above_ma", kind=k, n=n), f"Close is above the {_ma_txt(k, n)}", None)
    m = re.search(CMP_DN + r"\s*(?:the|its)?\s*" + MA, s)
    if m:
        k, n = _ma(m)
        return (_rule("x_close_below_ma", kind=k, n=n), f"Close is below the {_ma_txt(k, n)}", None)

    # 52-week levels
    if re.search(r"all time high", s):
        return ("miss", "All-time high needs the full price history, which is not loaded. Write \"new 52 week high\" instead.")
    m = re.search(r"(?:within|inside|less than|not more than|at most)\s*" + NUM + r"\s*%\s*(?:of|from|below|off)?\s*(?:the|its)?\s*52 week high", s)
    if m:
        return (_rule("near_52w_high", pct=float(m.group(1))), f"Close is within {_n(m.group(1))}% of the 52-week high", None)
    m = re.search(NUM + r"\s*%\s*(?:or more\s*)?(?:above|over|off|from|up from)\s*(?:the|its)?\s*52 week low", s)
    if m:
        return (_rule("above_52w_low", pct=float(m.group(1))), f"Close is at least {_n(m.group(1))}% above the 52-week low", None)
    if re.search(r"(?:new|fresh|makes?|making|hits?|hitting|at|breaks?|breakout\w*|above|cross\w*)\s*(?:a|the|its|of|to|out)?\s*(?:new\s*)?52 week high", s):
        return (_rule("new_52w_high"), "Makes a new 52-week high today", None)
    if re.search(r"(?:near|close to|around)\s*(?:the|its)?\s*52 week high", s):
        return (_rule("near_52w_high", pct=10.0), "Close is within 10% of the 52-week high", "\"Near\" has no exact meaning, so I used within 10%.")

    # breakouts
    if re.search(r"(?:above|over|>|cross\w*|break\w*|clos\w* above)\s*(?:the\s*)?previous day\s*(?:'s)?\s*high", s):
        return (_rule("close_above_prev_high"), "Close is above the previous day's high", None)
    if re.search(r"(?:above|over|>|higher than)\s*(?:the\s*)?previous day\s*(?:'s)?\s*close", s) or re.search(r"\bhigher close\b", s):
        return (_rule("x_close_above_prev_close"), "Close is above the previous day's close", None)
    m = re.search(r"(\d+) day\s*(?:high|breakout|closing high|range breakout)|(?:highest|high|breakout|break)\s*(?:close|high)?\s*(?:of|in|over|above|out of)?\s*(?:the\s*)?(?:last|past|previous|prior)?\s*(\d+) day", s)
    if m and re.search(r"high|break", s):
        n = int(m.group(1) or m.group(2))
        return (_rule("breakout_n", n=n), f"Close is above the highest high of the previous {n} sessions", None)
    if re.search(r"\bbreak\s*out\b|\bbreakout\b|\bbreaks out\b", s):
        return (_rule("breakout_n", n=20), "Close is above the highest high of the previous 20 sessions", "No period given for the breakout, so I used a 20-day high.")

    # consolidation / contraction
    m = re.search(r"(?:tight|narrow|base|consolidat\w*|range|sideways)\D*?(\d+) day\D*?" + NUM + r"\s*%", s) or \
        re.search(r"(?:tight|narrow|base|consolidat\w*|range|sideways)\D*?" + NUM + r"\s*%\D*?(\d+) day", s)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        n, pct = (int(a), b) if "day" in s[m.start(1):m.start(2)] else (int(b), a)
        return (_rule("range_tight", n=n, pct=pct), f"High-to-low range of the previous {n} sessions is at most {_n(pct)}% of the price", None)
    if re.search(r"\bvcp\b|volatility contract\w*|contraction", s):
        return (_rule("atr_contract", x=0.8), "Volatility has contracted: 14-day ATR is at most 0.8 times the 50-day ATR", "\"Contraction\" needs a number, so I used ATR 14 ÷ ATR 50 at most 0.8.")
    m = re.search(r"(?:pull\w*\s*back|pullback|correct\w*|dip\w*|down|off)\D*?between " + NUM + " to " + NUM + r"\s*%\D*?(?:(\d+) day)?", s)
    if m:
        n = int(m.group(3)) if m.group(3) else 20
        return (_rule("pullback_depth", n=n, lo=float(m.group(1)), hi=float(m.group(2))),
                f"Close is {_n(m.group(1))}% to {_n(m.group(2))}% below the {n}-day high", None if m.group(3) else "No period given, so I measured from the 20-day high.")

    # volume
    if re.search(r"\bvol(?:ume)?\b", s):
        m = re.search(NUM + r" x\D*?(\d+) (?:day|sma)", s) or re.search(r"(\d+) (?:day|sma)\D*?" + NUM + r" x", s)
        if m:
            a, b = m.group(1), m.group(2)
            x, n = (float(a), int(b)) if re.search(NUM + r" x\D*?(\d+) (?:day|sma)", s) else (float(b), int(a))
            return (_rule("x_vol_mult_n", x=x, n=n), f"Volume is at least {_n(x)} times its {n}-day average", None)
        m = re.search(NUM + r" x", s)
        if m:
            return (_rule("vol_mult", x=float(m.group(1))), f"Volume is at least {_n(m.group(1))} times its 50-day average", None)
        m = re.search(NUM + r"\s*%\s*(?:above|over|more than|higher than)", s)
        if m and re.search(r"avg|average", s):
            x = 1 + float(m.group(1)) / 100
            return (_rule("vol_mult", x=x), f"Volume is at least {x:.2f} times its 50-day average", None)
        if re.search(r"dry|dries|dried|low|light|thin|contract\w*|shrink\w*|declin\w*|falling", s):
            return (_rule("vol_dry", x=0.8, days=3), "Volume was at most 0.8 times its 50-day average on each of the 3 sessions before today",
                    "\"Volume dry-up\" needs numbers, so I used 0.8 times average for 3 sessions.")
        if re.search(r"(?:above|over|more than|higher than|greater than|>)\s*(?:the\s*)?previous day", s):
            return (_rule("x_vol_gt_prev"), "Volume is higher than the previous day's", None)
        m = re.search(r"(?:above|over|more than|higher than|greater than|>)\s*(?:the|its)?\s*(?:(\d+) day\s*)?(?:avg|average|sma)", s) or re.search(r"(?:above|over)\s*(?:the|its)?\s*(\d+) sma", s)
        if m:
            n = int(m.group(1)) if m.group(1) else 50
            return (_rule("x_vol_mult_n", x=1.0, n=n), f"Volume is above its {n}-day average", None)
        if re.search(r"spike|surge|burst|explos\w*|huge|heavy", s):
            return (_rule("vol_mult", x=2.0), "Volume is at least 2 times its 50-day average", "\"Volume spike\" needs a number, so I used 2 times the 50-day average.")
        if re.search(r"high|strong|good|big|above|expansion|increase\w*|rising|pick\w* up", s):
            return (_rule("vol_mult", x=1.5), "Volume is at least 1.5 times its 50-day average", "\"High volume\" needs a number, so I used 1.5 times the 50-day average.")
        return None
    m = re.search(r"turnover\D*?" + NUM + r"\s*(?:cr|crore)", s)
    if m:
        return (_rule("turnover_min", x=float(m.group(1))), f"Trades at least ₹{_n(m.group(1))} crore a day (20-day average)", None)

    # MACD (12, 26, 9)
    if re.search(r"\bmacd\b", s):
        if re.search(r"cross\w*|crossover|buy|bullish|turns? (?:up|positive)", s) and not re.search(r"zero|\b0\b", s):
            return (_rule("x_macd", how="cross"), "MACD (12, 26, 9) crosses above its signal line today", None)
        if re.search(r"zero|\b0\b|positive", s):
            return (_rule("x_macd", how="zero"), "MACD (12, 26, 9) is above zero", None)
        if re.search(r"above|over|>", s):
            return (_rule("x_macd", how="above"), "MACD (12, 26, 9) is above its signal line", None)
        return None
    # RSI
    if re.search(r"\brsi\b", s):
        n = 14
        mlen = re.search(r"rsi\s*\(?\s*(\d+)\s*\)?\s*(?:is|has|day|>|<|above|below|over|under|cross|between|greater|less|more)", s)
        rest = s
        if mlen and int(mlen.group(1)) <= 30 and re.search(r"rsi\s*\(?\s*\d+\s*\)?.*\d", s[mlen.start():].replace(mlen.group(1), "", 1)):
            n = int(mlen.group(1))
            rest = s[:mlen.start()] + "rsi " + s[mlen.start():].split(mlen.group(1), 1)[1]
        m = re.search(r"between " + NUM + " to " + NUM, rest)
        if m:
            return (_rule("x_rsi_between", n=n, lo=float(m.group(1)), hi=float(m.group(2))), f"RSI {n} is between {_n(m.group(1))} and {_n(m.group(2))}", None)
        m = re.search(r"cross\w*\s*(?:above|over)?\s*" + NUM, rest)
        if m:
            return (_rule("x_rsi_cross_up", n=n, x=float(m.group(1))), f"RSI {n} crosses above {_n(m.group(1))} today", None)
        m = re.search(CMP_UP + r"\s*" + NUM, rest)
        if m:
            return (_rule("rsi_above", n=n, x=float(m.group(1))), f"RSI {n} is above {_n(m.group(1))}", None)
        m = re.search(CMP_DN + r"\s*" + NUM, rest)
        if m:
            return (_rule("rsi_below", n=n, x=float(m.group(1))), f"RSI {n} is below {_n(m.group(1))}", None)
        if re.search(r"oversold", rest):
            return (_rule("rsi_below", n=n, x=30.0), f"RSI {n} is below 30", "\"Oversold\" is usually RSI below 30, so I used that.")
        return None

    # candles
    m = re.search(r"(\d+)\s*(?:day\s*)?(?:consecutive|straight|in a row)?\s*(red|down|lower|green|up|higher)\s*(?:day|candle|close)?s?\s*(?:in a row|consecutive|straight|running)?", s)
    if m and re.search(r"in a row|consecutive|straight|running", s):
        d = "down" if m.group(2) in ("red", "down", "lower") else "up"
        return (_rule("x_streak", dir=d, n=int(m.group(1))), f"Closed {'lower' if d == 'down' else 'higher'} than the day before on each of the last {m.group(1)} sessions", None)
    if re.search(r"inside (?:day|bar|candle)", s):
        return (_rule("inside_day"), "Inside day: today's range sits within the previous day's range", None)
    if re.search(r"\bnr ?7\b|narrowest range", s):
        return (_rule("nr7"), "Today's range is the narrowest of the last 7 sessions (NR7)", None)
    m = re.search(r"gap\w*\s*up(?:\D*?" + NUM + r"\s*%)?", s)
    if m:
        x = float(m.group(1)) if m.group(1) else 1.0
        return (_rule("gap_up", pct=x), f"Opens at least {_n(x)}% above the previous close", None if m.group(1) else "No size given for the gap, so I used 1%.")
    m = re.search(r"clos\w*\s*(?:in|within|at)?\s*(?:the\s*)?(?:top|upper)\s*" + NUM + r"\s*%", s)
    if m:
        return (_rule("close_top", pct=float(m.group(1))), f"Close is in the top {_n(m.group(1))}% of the day's range", None)
    if re.search(r"clos\w*\s*(?:near|at|close to)\s*(?:the|its|day'?s?)?\s*(?:day\s*)?high|strong close", s):
        return (_rule("close_top", pct=25.0), "Close is in the top 25% of the day's range", "\"Closes near the high\" needs a number, so I used the top 25% of the range.")
    m = re.search(r"between " + NUM + " to " + NUM + r"\s*%", s)
    if m and re.search(r"today|day|change|move|up|gain|rise", s):
        return (_rule("day_change", lo=float(m.group(1)), hi=float(m.group(2))), f"Today's change is between {_n(m.group(1))}% and {_n(m.group(2))}%", None)
    m = re.search(r"(?:up|gain\w*|rise\w*|rose|rall\w*|return\w*|moved?|advance\w*)\D*?" + NUM + r"\s*%\D*?(\d+) day", s) or None
    if m:
        return (_rule("ret_n", n=int(m.group(2)), pct=float(m.group(1))), f"Up at least {_n(m.group(1))}% over the last {m.group(2)} sessions", None)
    m = re.search(r"(\d+) day\s*(?:return|gain|change|performance)\D*?" + NUM + r"\s*%", s)
    if m:
        return (_rule("ret_n", n=int(m.group(1)), pct=float(m.group(2))), f"Up at least {_n(m.group(2))}% over the last {m.group(1)} sessions", None)
    m = re.search(r"(?:up|gain\w*|rise\w*|rose|move\w*|change\w*|closes? up)\D*?" + NUM + r"\s*%", s)
    if m:
        return (_rule("day_change", lo=float(m.group(1)), hi=100.0), f"Today's close is at least {_n(m.group(1))}% above the previous close", None)
    if re.search(r"green|bullish candle|up day|positive (?:day|close|candle)|close\w* above (?:the\s*)?open", s):
        return (_rule("green"), "Green candle: close above the open", None)
    if re.search(r"red|bearish candle|down day|negative (?:day|close|candle)|close\w* below (?:the\s*)?open", s):
        return (_rule("x_red"), "Red candle: close below the open", None)

    # price
    m = re.search(r"(?:price|close|cmp|ltp)?\s*" + CMP_UP + r"\s*(?:rs\.?\s*)?" + NUM + r"\s*(?:rs|rupees)?\s*$", s)
    if m and re.search(r"price|close|cmp|ltp|rs|rupee", s):
        return (_rule("price_min", x=float(m.group(1))), f"Price is at least ₹{_n(m.group(1))}", None)
    m = re.search(r"(?:price|close|cmp|ltp)?\s*" + CMP_DN + r"\s*(?:rs\.?\s*)?" + NUM + r"\s*(?:rs|rupees)?\s*$", s)
    if m and re.search(r"price|close|cmp|ltp|rs|rupee", s):
        return (_rule("x_price_max", x=float(m.group(1))), f"Price is at most ₹{_n(m.group(1))}", None)
    if re.search(r"\b(?:uptrend|up trend|trending up)\b", s):
        return (_rule("x_ma_above_ma", k1="EMA", n1=50, k2="SMA", n2=200), "50-day EMA is above the 200-day average",
                "\"Uptrend\" has no exact meaning, so I used 50-day EMA above the 200-day average. Write your own definition to replace it.")
    return None


def _exit(c: str, ex: dict, book: dict):
    """Updates ex / book in place. → (meaning, note) or ("miss", why) or None."""
    s = c
    # entry style
    if re.search(r"\b(?:buy|entry|enter)\b", s) and not re.search(r"\b(?:stop loss|sl|target|exit|sell)\b", s):
        if re.search(r"(?:above|over|break\w*|cross\w*)\s*(?:of\s*)?(?:the\s*)?(?:signal|breakout|setup|trigger|previous day|that|entry)?\s*(?:day|candle|bar)?(?:'s)?\s*high", s) or re.search(r"buy stop", s):
            ex["entry"] = bt.ENTRIES[1]
            return ("Buy the next day only if price trades above the signal day's high", None)
        if re.search(r"next\s*(?:day|session|morning)?(?:'s)?\s*open|at (?:the )?open|market open", s):
            ex["entry"] = bt.ENTRIES[0]
            return ("Buy at the next day's open", None)
    m = re.search(r"(?:skip|avoid|ignore|no trade|don'?t (?:buy|trade))\D*gap\D*?" + NUM + r"\s*%", s)
    if m:
        ex["gap_max"] = float(m.group(1))
        return (f"Skip the trade if it opens more than {_n(m.group(1))}% above the signal day's close", None)
    m = re.search(r"risk\s*(?:per trade)?\s*(?:of|is|=|:)?\s*" + NUM + r"\s*%(?!\s*(?:above|below))", s)
    if m and not re.search(r"reward|stop", s):
        book["risk_pct"] = float(m.group(1))
        return (f"Risk {_n(m.group(1))}% of capital on each trade", None)
    # breakeven
    m = re.search(r"(?:breakeven|break even|stop to (?:entry|cost|buy price))\D*?" + NUM + r" r", s) or re.search(NUM + r" r\D*?(?:breakeven|break even|stop to (?:entry|cost))", s)
    if m:
        ex["breakeven_r"] = float(m.group(1))
        return (f"Move the stop to the entry price once the trade is up {_n(m.group(1))}R", None)
    # trailing exit on an average
    m = re.search(r"(?:exit|sell|trail\w*|square off|close (?:the )?(?:trade|position)|get out|book)\D*?" + MA, s)
    if m and not re.search(r"\babove\b", s):
        k, n = _ma(m)
        ex["trail_kind"], ex["trail_n"] = k, n
        return (f"Exit at the next open after a close below the {_ma_txt(k, n)}", None)
    m = re.search(r"stop\w*(?: loss)?\D*?(?:below|under|at)\s*(?:the\s*)?" + MA, s)
    if m:
        k, n = _ma(m)
        ex["trail_kind"], ex["trail_n"] = k, n
        return (f"Exit at the next open after a close below the {_ma_txt(k, n)}",
                "A stop \"below an average\" moves every day, so it is tested as an exit on a close below it. The first stop is still needed: see below.")
    # stop loss
    if re.search(r"\bstop loss\b|\bstop\b|\bsl\b", s) and not re.search(r"time stop", s):
        m = re.search(NUM + r"\s*(?:x\s*)?atr|atr\D*?" + NUM, s)
        if m or re.search(r"\batr\b", s):
            x = float((m.group(1) or m.group(2))) if m else 1.5
            ex["stop"], ex["stop_atr"] = bt.STOPS[2], x
            return (f"Stop loss {_n(x)} × ATR(14) below the entry price", None if m else "No ATR multiple given, so I used 1.5.")
        m = re.search(NUM + r"\s*%", s)
        if m:
            ex["stop"], ex["stop_pct"] = bt.STOPS[3], float(m.group(1))
            return (f"Stop loss {_n(m.group(1))}% below the entry price", None)
        m = re.search(r"(?:low(?:est low)? of (?:the )?(?:last|past|previous|prior)?\s*(\d+) day|(\d+) day low)", s)
        if m:
            n = int(m.group(1) or m.group(2))
            ex["stop"], ex["stop_n"] = bt.STOPS[1], n
            return (f"Stop loss at the lowest low of the last {n} sessions (signal day included)", None)
        if re.search(r"swing low|recent low|pivot low", s):
            ex["stop"], ex["stop_n"] = bt.STOPS[1], 10
            return ("Stop loss at the lowest low of the last 10 sessions", "\"Swing low\" needs a look-back, so I used the lowest low of 10 sessions.")
        if re.search(r"low", s):
            ex["stop"] = bt.STOPS[0]
            return ("Stop loss at the low of the signal day", None)
        return ("miss", "I could not read the stop. Write it as \"stop loss 5%\", \"stop at signal candle low\", \"stop at 10 day low\" or \"stop 2 ATR\".")
    # target
    if re.search(r"\btarget\b|\brisk reward\b|\brr\b", s):
        m = re.search(NUM + r" r\b", s) or re.search(r"(?:risk reward|rr)\D*?" + NUM, s)
        if m:
            ex["target_r"] = float(m.group(1))
            return (f"Take profit at {_n(m.group(1))}R (that many times the risk)", None)
        m = re.search(NUM + r"\s*%", s)
        if m:
            ex["_target_pct"] = float(m.group(1))
            return (f"Take profit {_n(m.group(1))}% above the entry price", None)
        return ("miss", "I could not read the target. Write it as \"target 2R\", \"1:3\" or \"target 10%\".")
    # time
    m = re.search(r"(?:exit|sell|close|square off|hold|holding|time stop|max\w*)\D*?(\d+) day", s)
    if m:
        ex["max_days"] = int(m.group(1))
        return (f"Exit after {m.group(1)} sessions if nothing else has closed the trade", None)
    if re.search(r"\b(?:short|sell short|shorting)\b", s):
        return ("miss", "Only long (buy) trades are tested.")
    return None


EXAMPLES = {
    "Breakout": "Close above 50 EMA and 200 SMA\nBreakout of 20 day high with volume 1.5x average\nClose in top 30% of the day's range\n"
                "Stop loss at signal candle low\nExit when close below 21 EMA",
    "Pullback": "21 EMA above 50 EMA, close above 200 SMA\nPullback to 21 EMA\nGreen candle, close above previous day high\n"
                "Stop loss at 5 day low\nTarget 3R, exit after 30 days",
    "52-week high": "New 52 week high\nVolume 1.5x average\nOutperforming Nifty over 3 months\nStop loss 7%\nExit when close below 21 EMA",
    "Average crossover": "21 EMA crosses above 50 EMA\nClose above 200 SMA\nStop loss 2 ATR\nExit when close below 50 EMA",
    "RSI dip": "Close above 200 SMA\nRSI below 35\n3 down days in a row\nStop loss 6%\nTarget 2R, exit after 15 days",
}

VOCAB = [
    ("Trend", ["close above 50 EMA", "21 EMA above 50 EMA", "50 EMA rising", "close crosses above 20 SMA", "21 EMA crosses above 50 EMA", "golden cross",
               "within 10% of 52 week high", "30% above 52 week low", "not more than 8% above 21 EMA"]),
    ("Breakout", ["breakout of 20 day high", "new 52 week high", "close above previous day high", "gap up 2%", "tight range 15 days within 12%"]),
    ("Pullback", ["pullback to 21 EMA", "pullback between 3 and 12% from 20 day high", "3 down days in a row", "RSI below 35"]),
    ("Momentum", ["up 20% in 3 months", "outperforming Nifty over 3 months", "RSI above 60", "RSI crosses above 50", "MACD bullish crossover", "MACD above zero", "up 3% today"]),
    ("Volume", ["volume 1.5x average", "volume 2x 20 day average", "volume above previous day", "volume dry up", "turnover above 5 crore"]),
    ("Candle", ["green candle", "close in top 25% of range", "inside day", "NR7"]),
    ("Market", ["Nifty above 50 EMA"]),
    ("Buying", ["buy at next day open", "buy above signal candle high", "skip if gap up more than 4%", "risk 1% per trade"]),
    ("Stop loss", ["stop loss 5%", "stop loss at signal candle low", "stop loss at 10 day low", "stop loss 2 ATR", "move stop to breakeven at 1R"]),
    ("Exit", ["exit when close below 21 EMA", "target 2R", "target 10%", "exit after 20 days"]),
]


def parse(text: str) -> dict:
    ex = dict(bt.EXIT_DEFAULT)
    ex.update(entry=bt.ENTRIES[0], stop=None, trail_n=0, target_r=0.0, breakeven_r=0.0, max_days=None, gap_max=100.0, max_risk=100.0)
    book, rules, read, missed, assumed = {}, [], [], [], []
    norm = normalise(text)
    exit_line, last_rule = None, None
    for ln, c in clauses(norm):
        is_exit_word = bool(EXIT_WORDS.search(c)) or (bool(re.search(r"\b(?:buy|entry|enter)\b", c)) and bool(re.search(r"open|high|buy stop", c)) and not re.search(MA, c))
        if re.search(r"\b(?:buy|entry|enter|long)\b\s*(?:when|if|:)", c):
            exit_line = None
        in_exit = is_exit_word or exit_line == ln
        got = None
        if re.search(r"\b(?:short|shorting|sell short)\b", c):
            missed.append((c, "Only long (buy) trades are tested."))
            continue
        if in_exit:
            got = _exit(c, ex, book)
            if got is not None:
                exit_line = ln if re.search(r"\b(exit|sell|square off)\b", c) else exit_line
                if got[0] == "miss":
                    missed.append((c, got[1]))
                else:
                    read.append((c, got[0], got[1], "exit"))
                last_rule = None
                continue
        got = _entry(c)
        if got is None and last_rule is not None:                     # "…above 50 EMA and 200 SMA": the second half borrows the verb
            m = re.fullmatch(r"(?:the\s*)?" + MA, c.strip())
            if m and last_rule["type"] in ("close_above_ma", "x_close_below_ma", "x_cross_above_ma", "touch_ma"):
                k, n = _ma(m)
                r2 = dict(last_rule, kind=k, n=n)
                verb = {"close_above_ma": "Close is above the", "x_close_below_ma": "Close is below the", "x_cross_above_ma": "Close crosses above the",
                        "touch_ma": "The low came within 1.5% of the"}[r2["type"]]
                got = (r2, f"{verb} {_ma_txt(k, n)}", None)
        if got is None:
            if in_exit:
                missed.append((c, "I could not read this exit rule."))
            elif len(re.sub(r"[^a-z0-9]", "", c)) >= 3 and not re.fullmatch(r"(?:buy|entry|enter|long|setup|rules?|my setup|when|if|exit|sell)\W*", c):
                missed.append((c, "I do not know this phrase yet. See \"Words I understand\"."))
            continue
        if got[0] == "miss":
            missed.append((c, got[1]))
            continue
        rule, meaning, note = got
        if any(r == rule for r in rules):
            continue
        rules.append(rule)
        read.append((c, meaning, note, "entry"))
        last_rule = rule

    # defaults, stated openly
    if ex["stop"] is None:
        ex["stop"] = bt.STOPS[0]
        assumed.append("No stop loss written, so the stop is the low of the signal day. Write e.g. \"stop loss 5%\" to change it.")
    tp = ex.pop("_target_pct", None)
    if tp:
        if ex["stop"] == bt.STOPS[3] and ex.get("stop_pct"):
            ex["target_r"] = round(tp / ex["stop_pct"], 3)
            ex["target_pct"] = tp
        else:
            missed.append((f"target {_n(tp)} %", "A target in % can only be tested together with a stop in % (e.g. \"stop loss 5%\"). Use \"target 2R\" with other stops."))
            read[:] = [x for x in read if not x[1].startswith("Take profit") or "R (" in x[1]]
    if ex["max_days"] is None:
        if ex["trail_n"] or ex["target_r"]:
            ex["max_days"] = NO_TIME_STOP
        else:
            ex["max_days"] = ASSUMED_HOLD
            assumed.append(f"No exit written, so a trade is closed after {ASSUMED_HOLD} sessions unless the stop is hit first. "
                           "Write e.g. \"exit when close below 21 EMA\" or \"target 2R\".")
    return {"rules": rules, "ex": ex, "book": book, "read": read, "missed": missed, "assumed": assumed}


def exit_summary(ex: dict) -> list:
    """Plain sentences describing how a trade is taken and closed."""
    out = ["Buy at the next day's open" if ex["entry"] == bt.ENTRIES[0] else "Buy the next day only if price trades above the signal day's high"]
    out.append({bt.STOPS[0]: "Stop loss at the low of the signal day", bt.STOPS[1]: f'Stop loss at the lowest low of the last {ex.get("stop_n")} sessions',
                bt.STOPS[2]: f'Stop loss {_n(ex.get("stop_atr"))} × ATR(14) below entry', bt.STOPS[3]: f'Stop loss {_n(ex.get("stop_pct"))}% below entry'}[ex["stop"]])
    if ex.get("breakeven_r"):
        out.append(f'Stop moves to entry at +{_n(ex["breakeven_r"])}R')
    if ex.get("target_r"):
        out.append(f'Take profit {_n(ex["target_pct"])}% above entry ({_n(ex["target_r"])}R)' if ex.get("target_pct") else f'Take profit at {_n(ex["target_r"])}R')
    if ex.get("trail_n"):
        out.append(f'Exit after a close below the {_ma_txt(ex["trail_kind"], ex["trail_n"])}')
    if ex.get("max_days") and ex["max_days"] < NO_TIME_STOP:
        out.append(f'Exit after {ex["max_days"]} sessions')
    if ex.get("gap_max", 100) < 100:
        out.append(f'Skip if it opens more than {_n(ex["gap_max"])}% above the signal close')
    return out

def trend_label(ab20, ab50, ab200, pct_above20=None, pct_above50=None) -> str:
    a, b, c = _b(ab20), _b(ab50), _b(ab200)
    breadth20 = _f(pct_above20)
    breadth50 = _f(pct_above50)

    if a and b and c:
        return "STRONG UPTREND"
    if a and b:
        return "UPTREND"
    if b and not a:
        return "PULLBACK"
    if a and not b:
        return "RECOVERING"

    breadth_support = (
        (breadth20 is not None and breadth20 >= 55)
        or (breadth50 is not None and breadth50 >= 55)
    )
    if breadth_support:
        return "PULLBACK"

    if a is None and b is None:
        return "—"
    return "DOWNTREND"

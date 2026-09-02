"""Display formatting — the only place numbers become strings.

Port of Helpers/CurrencyFormat.cs, Helpers/CurrencyHelper.cs, Models/UiQuote.cs
and Models/UiCandleSeries.cs. Culture-invariant on purpose: the Windows
extension formatted the same way regardless of locale, and so does this.
"""

import os
import time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# CurrencyFormat.Symbols — trailing spaces are part of the symbol on purpose
# ("CHF 12.00", "kr 12.00"), matching the C# table.
SYMBOLS = {
    "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥",
    "AUD": "A$", "CAD": "C$", "NZD": "NZ$", "HKD": "HK$", "SGD": "S$",
    "CHF": "CHF ", "SEK": "kr ", "NOK": "kr ", "DKK": "kr ",
    "PLN": "zł ", "ZAR": "R ", "MXN": "Mex$", "INR": "₹", "BRL": "R$",
    "KRW": "₩",
}

ZERO_DECIMAL_CODES = ("JPY", "KRW")


def decimals(code):
    return 0 if str(code or "").upper() in ZERO_DECIMAL_CODES else 2


def number(amount, places):
    """"#,##0.00"-style: thousands separators, fixed decimals, half-up rounding
    (what .NET's decimal.ToString does; Python's float formatting would round
    half-even and disagree on x.xx5 boundaries)."""
    try:
        d = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        d = Decimal(0)
    if not d.is_finite():
        d = Decimal(0)
    q = d.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return f"{q:,.{places}f}"


def money(amount, code):
    """CurrencyFormat.Format: "$1,234.56", "¥1,234", or "1,234.56 SGD" for an unknown code."""
    code = str(code or "USD").upper()
    n = number(amount, decimals(code))
    symbol = SYMBOLS.get(code)
    return symbol + n if symbol is not None else f"{n} {code}"


def money_signed(amount, code):
    """CurrencyFormat.FormatSigned: sign before the symbol — "+$12.05", "-$8.40"."""
    return ("-" if amount < 0 else "+") + money(abs(amount), code)


def pct_signed(pct):
    """"+1.20" / "-0.80" — the "+0.00;-0.00" pattern the Windows extension used everywhere."""
    try:
        f = float(pct)
    except (TypeError, ValueError):
        f = 0.0
    if f != f or f in (float("inf"), float("-inf")):
        f = 0.0
    return f"{f:+.2f}"


def quantity(amount):
    """"0.########": up to eight decimals, no trailing zeros, no grouping —
    10, not 10.00; 0.5 stays 0.5. What a holding shows and what the
    quantity field is prefilled with."""
    try:
        d = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        d = Decimal(0)
    if not d.is_finite():
        d = Decimal(0)
    q = d.quantize(Decimal("1e-8"), rounding=ROUND_HALF_UP).normalize()
    if q == 0:
        return "0"
    text = f"{q:f}"
    return text


def unit_label(category):
    """UiPosition.UnitLabel: shares for stocks, units for everything else (FX is a notional holding)."""
    return "sh" if category == "stock" else "units"


def holding_text(symbol, amount, category):
    """UiPosition.FormatHolding: "AAPL · 10 sh" / "BTC · 0.5 units"."""
    return f"{symbol} · {quantity(amount)} {unit_label(category)}"


def money_compact(amount, code):
    """For the bar strip: whole units at >= 1000, four decimals under 1 (DOGE),
    two otherwise. Never more precision than the bar has room for."""
    code = str(code or "USD").upper()
    if decimals(code) == 0:
        places = 0
    elif abs(amount) >= 1000:
        places = 0
    elif abs(amount) < 1:
        places = 4
    else:
        places = 2
    n = number(amount, places)
    symbol = SYMBOLS.get(code)
    return symbol + n if symbol is not None else f"{n} {code}"


# Minor-unit currency codes some exchanges quote in: the major code and the
# scale to it. Yahoo spells pence "GBp" (case is the signal: "GBP" is pounds),
# other feeds "GBX"; South African cents and Israeli agorot likewise.
MINOR_UNITS = {
    "GBp": ("GBP", 0.01),
    "GBX": ("GBP", 0.01),
    "ZAc": ("ZAR", 0.01),
    "ILA": ("ILS", 0.01),
}
_MINOR_UPPER = {k.upper(): v for k, v in MINOR_UNITS.items() if k.isupper()}


def minor_unit(code):
    """(major code, scale) when `code` is a minor-unit spelling, else None.
    "GBp" and "ZAc" match exactly; the all-caps codes match any case."""
    code = str(code or "").strip()
    return MINOR_UNITS.get(code) or _MINOR_UPPER.get(code.upper())


def is_pence(code):
    unit = minor_unit(code)
    return bool(unit) and unit[0] == "GBP"


def normalize_stock_quote(raw_currency, price, change):
    """CurrencyHelper.NormalizeStockQuote: LSE quotes arrive in pence (GBp/GBX);
    convert to pounds so the row shows £ and a sane number."""
    code, scale = currency_scale(raw_currency)
    return code, price * scale, change * scale


def currency_scale(raw_currency):
    """("GBP", 0.01) for pence, (code, 1.0) otherwise — for scaling whole series."""
    code = (raw_currency or "").strip()
    if not code:
        return "USD", 1.0
    unit = minor_unit(code)
    if unit:
        return unit
    return code.upper(), 1.0


def normalize_code(raw_currency):
    return currency_scale(raw_currency)[0]


def quote_currency_of_pair(pair):
    """EURUSD -> USD. Anything that is not a 6-letter pair -> USD."""
    p = str(pair or "").strip()
    if len(p) != 6:
        return "USD"
    return p[3:].upper()


def direction(change):
    if change > 0:
        return "up"
    if change < 0:
        return "down"
    return "flat"


def arrow(change):
    """UiQuote.IsUp: flat counts as up, so it gets the ▲."""
    return "▲" if change >= 0 else "▼"


def price_text(price, currency, category, valid=True):
    """UiQuote.FormatPrice: "—" when invalid; FX rates as 0.0000 with no symbol
    (a rate is not an amount of money); everything else via money()."""
    if not valid:
        return "—"
    if category == "currency":
        return f"{float(price):.4f}"
    return money(price, currency)


def change_text(change, change_pct, valid=True):
    """UiQuote.FormatChange: "▲ +1.20%" / "▼ -0.80%"; empty when invalid."""
    if not valid:
        return ""
    return f"{arrow(change)} {float(change_pct):+.2f}%"


def range_change_text(first, last, range_label, currency="USD"):
    """UiCandleSeries.FormatRangeChange: "▲ +$3.20 (+1.71%) · 1M" —
    the move across the whole selected range, Robinhood-style."""
    change = last - first
    pct = 0.0 if first == 0 else change / first * 100.0
    return f"{'▲' if last >= first else '▼'} {money_signed(change, currency)} ({pct:+.2f}%) · {range_label}"


def strip_value_text(quote, show_price=True):
    """What one bar-strip entry shows after its label: "$77,356 ▲ +0.0%" or just "▲ +0.0%"."""
    if not quote.valid:
        return "—"
    change = f"{arrow(quote.change)} {float(quote.change_pct):+.1f}%"
    if not show_price:
        return change
    if quote.category == "currency":
        return f"{float(quote.price):.4f} {change}"
    return f"{money_compact(quote.price, quote.currency)} {change}"


# Chart axis stamps, in the machine's local time. Per range, what a reader
# needs to place the ends of the line: a clock for a day, weekday
# and date for a week, day and month for a month, month and year beyond that.
TIME_LABEL_FORMATS = {"1D": "%H:%M", "1W": "%a %-d %b", "1M": "%-d %b", "1Y": "%b %Y", "5Y": "%b %Y"}


def time_label(ts, range_label):
    """"14:30", "Wed 2 Sep", "4 Aug", "Sep 2025" — the first/last stamps under a chart."""
    try:
        t = time.localtime(int(ts))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""
    return time.strftime(TIME_LABEL_FORMATS.get(range_label, "%-d %b %Y"), t)


def age_text(seconds):
    """"12 s ago", "3 min ago", "2 h ago" — how old the newest quote is, for the Data sources page."""
    if seconds is None:
        return "no quotes fetched yet"
    try:
        s = max(0, int(seconds))
    except (TypeError, ValueError, OverflowError):
        return "no quotes fetched yet"
    if s < 90:
        return f"{s} s ago"
    if s < 90 * 60:
        return f"{s // 60} min ago"
    return f"{s // 3600} h ago"


def display_path(path, home=None):
    """A path with the home directory shortened to ~, for display only."""
    path = str(path or "")
    home = home if home is not None else os.path.expanduser("~")
    if home and home != "/" and (path == home or path.startswith(home.rstrip("/") + "/")):
        return "~" + path[len(home.rstrip("/")):]
    return path


def chart_price_text(amount, currency, category):
    """The min/max/previous-close labels on a chart: FX as a rate, the rest as money."""
    if category == "currency":
        return f"{float(amount):.4f}"
    return money(amount, currency)

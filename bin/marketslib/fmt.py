"""Display formatting — the only place numbers become strings.

Port of Helpers/CurrencyFormat.cs, Helpers/CurrencyHelper.cs, Models/UiQuote.cs
and Models/UiCandleSeries.cs. Culture-invariant on purpose: the Windows
extension formatted the same way regardless of locale, and so does this.
"""

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


def is_pence(code):
    return code == "GBp" or str(code).upper() == "GBX"


def normalize_stock_quote(raw_currency, price, change):
    """CurrencyHelper.NormalizeStockQuote: LSE quotes arrive in pence (GBp/GBX);
    convert to pounds so the row shows £ and a sane number."""
    code = (raw_currency or "").strip()
    if not code:
        return "USD", price, change
    if is_pence(code):
        return "GBP", price / 100.0, change / 100.0
    return code.upper(), price, change


def normalize_code(raw_currency):
    code = (raw_currency or "").strip()
    if not code:
        return "USD"
    return "GBP" if is_pence(code) else code.upper()


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

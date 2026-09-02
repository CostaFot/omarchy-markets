"""The priced portfolio (port of Models/UiPosition.cs and UiPortfolio.cs).

A holding is an instrument plus how much of it is held; here it meets its
quote and the FX rate into the reporting currency and becomes a row with
every string the panel shows, and the rows roll up into the totals. Two
flavours of gain: DAILY P&L is quantity × today's per-unit change, TOTAL
RETURN is quantity × (price − cost basis) and only exists when a basis was
recorded. A holding counts toward the totals only when it is priced AND
convertible; a priced one whose currency has no rate stays in the list,
shows its native value only, and is counted as "not converted".
"""

from . import fmt


def position_row(holding, quote, preferred, rate):
    """One priced holding. `rate` is units of `preferred` per one unit of the
    quote's currency (1 when they match), or None when unknown."""
    qty = float(holding.get("quantity") or 0.0)
    basis = holding.get("cost_basis")
    basis = float(basis) if basis is not None and float(basis) > 0 else None
    valid = bool(quote and quote.valid)
    native = str(quote.currency if quote else "USD").upper()
    preferred = str(preferred or "USD").upper()
    needs_conversion = native != preferred
    converted = valid and rate is not None

    market_value = qty * quote.price if valid else 0.0
    daily_pnl = qty * quote.change if valid else 0.0
    conv_value = market_value * rate if converted else None
    conv_daily = daily_pnl * rate if converted else None

    total_cost = qty * basis if basis is not None else None
    total_return = market_value - total_cost if valid and total_cost is not None else None
    return_pct = (quote.price - basis) / basis * 100.0 if valid and basis is not None else None
    conv_cost = total_cost * rate if total_cost is not None and converted else None
    conv_return = total_return * rate if total_return is not None and converted else None

    # Value: native, with the converted approximation appended when the
    # currencies differ and a rate is known — "£75.00 (≈$95.20)".
    if not valid:
        value_text = "—"
    else:
        value_text = fmt.money(market_value, native)
        if needs_conversion and conv_value is not None:
            value_text += f" (≈{fmt.money(conv_value, preferred)})"

    # Daily P&L and total return show in the preferred currency when
    # convertible (so they match the totals), else in the native one.
    if valid:
        amount, code = (conv_daily, preferred) if needs_conversion and conv_daily is not None else (daily_pnl, native)
        daily_text = f"{fmt.arrow(quote.change)} {fmt.money_signed(amount, code)} ({fmt.pct_signed(quote.change_pct)}%)"
    else:
        daily_text = ""
    if valid and total_return is not None:
        amount, code = (conv_return, preferred) if needs_conversion and conv_return is not None else (total_return, native)
        return_text = f"{fmt.arrow(total_return)} {fmt.money_signed(amount, code)} ({fmt.pct_signed(return_pct)}%)"
    else:
        return_text = ""

    return {
        "symbol": holding["symbol"],
        "name": holding.get("name") or holding["symbol"],
        "category": holding.get("category") or "stock",
        "quantity": qty,
        "quantity_text": fmt.quantity(qty),
        "cost_basis": basis,
        "cost_basis_text": fmt.quantity(basis) if basis is not None else "",
        "cost_text": fmt.money(basis, native) if basis is not None else "",
        "currency": native,
        "valid": valid,
        "stale": bool(quote.stale) if quote else False,
        "converted": converted,
        "counts": valid and converted,
        "market_value": market_value if valid else None,
        "daily_pnl": daily_pnl if valid else None,
        "converted_value": conv_value,
        "converted_daily_pnl": conv_daily,
        "converted_cost": conv_cost,
        "converted_return": conv_return,
        "total_return": total_return,
        "holding_text": fmt.holding_text(holding["symbol"], qty, holding.get("category")),
        "amount_text": f"{fmt.quantity(qty)} {fmt.unit_label(holding.get('category'))}",
        "value_text": value_text,
        "daily_text": daily_text,
        "return_text": return_text,
        "dir": fmt.direction(quote.change) if valid else "flat",
        "return_dir": fmt.direction(total_return) if total_return is not None else "flat",
        "price_text": quote.to_dict()["price_text"] if quote else "—",
    }


def totals(rows, preferred):
    """UiPortfolio.From: the counted rows' converted values summed, the daily
    percent measured against yesterday's close value (today's value minus
    today's gain), total return over the counted rows that carry a basis."""
    preferred = str(preferred or "USD").upper()
    counted = [r for r in rows if r["counts"]]
    value = sum(r["converted_value"] or 0.0 for r in counted)
    daily = sum(r["converted_daily_pnl"] or 0.0 for r in counted)
    unconverted = sum(1 for r in rows if r["valid"] and not r["converted"])
    with_basis = [r for r in counted if r["converted_cost"] is not None]
    cost = sum(r["converted_cost"] for r in with_basis)
    gain = sum(r["converted_return"] or 0.0 for r in with_basis)
    previous = value - daily
    daily_pct = 0.0 if previous == 0 else daily / previous * 100.0
    return_pct = 0.0 if cost == 0 else gain / cost * 100.0

    change_text = f"{fmt.arrow(daily)} {fmt.money_signed(daily, preferred)} ({fmt.pct_signed(daily_pct)}%) today"
    return_note = ""
    if with_basis:
        return_note = f" · Total {fmt.arrow(gain)} {fmt.money_signed(gain, preferred)} ({fmt.pct_signed(return_pct)}%)"
    if unconverted == 0:
        unconverted_note = ""
    elif unconverted == 1:
        unconverted_note = " · 1 holding not converted"
    else:
        unconverted_note = f" · {unconverted} holdings not converted"

    return {
        "currency": preferred,
        "has_holdings": len(rows) > 0,
        "counted": len(counted),
        "unconverted": unconverted,
        "value": value,
        "daily_pnl": daily,
        "daily_pct": daily_pct,
        "cost": cost if with_basis else None,
        "total_return": gain if with_basis else None,
        "return_pct": return_pct if with_basis else None,
        "has_cost_basis": bool(with_basis),
        "dir": fmt.direction(daily),
        "return_dir": fmt.direction(gain) if with_basis else "flat",
        "value_text": fmt.money(value, preferred),
        "value_compact_text": fmt.money_compact(value, preferred),
        "change_text": change_text,
        "change_compact_text": f"{fmt.arrow(daily)} {float(daily_pct):+.1f}%",
        "return_note": return_note,
        "unconverted_note": unconverted_note,
        "stale": any(r["stale"] for r in counted),
    }

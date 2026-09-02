"""Provider-agnostic domain types. No formatting here beyond to_dict(),
which stamps the display strings from fmt.py so QML never formats numbers.
"""

from dataclasses import dataclass, field

CATEGORIES = ("stock", "crypto", "currency")
CATEGORY_ORDER = {c: i for i, c in enumerate(CATEGORIES)}
CATEGORY_LABELS = {"stock": "Stocks", "crypto": "Crypto", "currency": "Currency"}

RANGES = ("1D", "1W", "1M", "1Y", "5Y")
RANGE_DAYS = {"1D": 1, "1W": 7, "1M": 31, "1Y": 365, "5Y": 365 * 5}
MAX_POINTS = 300  # what one chart carries to QML, whatever the provider returned


def downsample(points, limit=MAX_POINTS):
    """Evenly thin a series to `limit` points, always keeping the first and last."""
    n = len(points)
    if n <= limit:
        return points
    step = (n - 1) / (limit - 1)
    return [points[round(i * step)] for i in range(limit)]


def normalize(symbol):
    """The cache/watchlist key: trimmed, upper-cased (WatchlistStore.Normalize)."""
    return str(symbol or "").strip().upper()


def is_category(value):
    return value in CATEGORIES


@dataclass
class Instrument:
    symbol: str
    name: str
    category: str
    # Per-provider identifiers learned from a search or a quote, e.g.
    # {"coingecko": "bitcoin"}. Symbols stay neutral (BTC); providers that
    # need their own id look here first.
    provider_ids: dict = field(default_factory=dict)

    def __post_init__(self):
        self.symbol = normalize(self.symbol)
        self.name = str(self.name or self.symbol)

    def to_dict(self):
        return {
            "symbol": self.symbol,
            "name": self.name,
            "category": self.category,
            "provider_ids": dict(self.provider_ids),
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            symbol=normalize(d.get("symbol")),
            name=str(d.get("name") or d.get("symbol") or ""),
            category=str(d.get("category") or "stock"),
            provider_ids=dict(d.get("provider_ids") or {}),
        )


@dataclass
class Quote:
    symbol: str
    name: str
    category: str
    price: float = 0.0
    change: float = 0.0
    change_pct: float = 0.0
    currency: str = "USD"
    valid: bool = True
    updated_at: int = 0
    stale: bool = False

    @classmethod
    def invalid(cls, instrument):
        return cls(instrument.symbol, instrument.name, instrument.category, valid=False)

    def to_dict(self):
        from . import fmt

        return {
            "symbol": self.symbol,
            "name": self.name,
            "category": self.category,
            "price": self.price if self.valid else None,
            "change": self.change if self.valid else None,
            "change_pct": self.change_pct if self.valid else None,
            "currency": self.currency,
            "valid": self.valid,
            "stale": self.stale,
            "updated_at": self.updated_at,
            "price_text": fmt.price_text(self.price, self.currency, self.category, self.valid),
            "change_text": fmt.change_text(self.change, self.change_pct, self.valid),
            "dir": fmt.direction(self.change) if self.valid else "flat",
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            symbol=normalize(d.get("symbol")),
            name=str(d.get("name") or ""),
            category=str(d.get("category") or "stock"),
            price=float(d.get("price") or 0.0),
            change=float(d.get("change") or 0.0),
            change_pct=float(d.get("change_pct") or 0.0),
            currency=str(d.get("currency") or "USD"),
            valid=bool(d.get("valid", False)),
            updated_at=int(d.get("updated_at") or 0),
            stale=bool(d.get("stale", False)),
        )


@dataclass
class CandleSeries:
    symbol: str
    range: str
    points: list = field(default_factory=list)  # [[unix_seconds, close], ...] oldest first
    valid: bool = True
    message: str = ""
    currency: str = "USD"
    # The close before the window opened, when the provider states one
    # (Yahoo's chartPreviousClose); the 1D chart draws it as a reference
    # line. None when unknown.
    previous_close: float = None
    # "currency" instruments are labelled as rates, everything else as money.
    category: str = "stock"

    @classmethod
    def invalid(cls, symbol, rng, message=""):
        return cls(symbol, rng, [], valid=False, message=message)

    @property
    def has_data(self):
        return self.valid and len(self.points) > 0

    def to_dict(self):
        from . import fmt

        has = self.has_data
        first = self.points[0][1] if has else None
        last = self.points[-1][1] if has else None
        closes = [p[1] for p in self.points] if has else []
        low = min(closes) if has else None
        high = max(closes) if has else None
        pc = self.previous_close if has and self.previous_close is not None else None
        label = lambda v: fmt.chart_price_text(v, self.currency, self.category)  # noqa: E731
        return {
            "symbol": self.symbol,
            "range": self.range,
            "valid": has,
            "message": self.message,
            "currency": self.currency,
            "category": self.category,
            "points": self.points,
            "n": len(self.points),
            "first": first,
            "last": last,
            "min": low,
            "max": high,
            "previous_close": pc,
            "dir": ("up" if last >= first else "down") if has else "flat",
            "price_text": fmt.money(last, self.currency) if has else "—",
            "range_change_text": fmt.range_change_text(first, last, self.range, self.currency) if has else "",
            # Every string the chart shows; QML formats no number and no date.
            "min_text": label(low) if has else "",
            "max_text": label(high) if has else "",
            "previous_close_text": label(pc) if pc is not None else "",
            "first_label": fmt.time_label(self.points[0][0], self.range) if has else "",
            "last_label": fmt.time_label(self.points[-1][0], self.range) if has else "",
        }

    @classmethod
    def from_dict(cls, d):
        return cls(
            symbol=normalize(d.get("symbol")),
            range=str(d.get("range") or "1D"),
            points=list(d.get("points") or []),
            valid=bool(d.get("valid", False)),
            message=str(d.get("message") or ""),
            currency=str(d.get("currency") or "USD"),
            previous_close=float(d["previous_close"]) if d.get("previous_close") is not None else None,
            category=str(d.get("category") or "stock"),
        )

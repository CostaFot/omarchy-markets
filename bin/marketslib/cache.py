"""Keep-last-good caches, persisted as JSON in the state dir.

Port of Data/InMemoryQuoteCacheDataSource.cs: an invalid quote never
overwrites a valid cached one — the cached one is served with stale=true
instead. That single rule is why a rate limit or a dead network never
blanks the bar. Persisting the cache also lets several bar instances (one
per monitor) share one fetch: `snapshot --max-age S` is a cache read when
every symbol is younger than S.
"""

from .models import CandleSeries, Quote, normalize
from .state import read_json, write_json_atomic

CANDLE_TTL_SECONDS = 300


class QuoteCache:
    def __init__(self, path):
        self.path = path
        try:
            data = read_json(path, {})
        except ValueError:
            data = {}
        self.entries = data if isinstance(data, dict) else {}
        self.dirty = False

    def get(self, symbol):
        e = self.entries.get(normalize(symbol))
        if not e:
            return None
        q = Quote.from_dict(e.get("quote") or {})
        q.stale = bool(e.get("stale", False))
        return q

    def fetched_at(self, symbol):
        e = self.entries.get(normalize(symbol))
        return int(e.get("fetched_at") or 0) if e else 0

    def fresh(self, symbols, max_age, now):
        """True when every symbol was fetched (or attempted) within max_age seconds."""
        for s in symbols:
            if now - self.fetched_at(s) > max_age:
                return False
        return True

    def upsert(self, quote, now, keep_last_good=True):
        """Returns the quote actually stored (the old valid one when a new
        invalid one is rejected, marked stale)."""
        key = normalize(quote.symbol)
        current = self.entries.get(key)
        if keep_last_good and not quote.valid and current and current.get("quote", {}).get("valid"):
            current["stale"] = True
            current["fetched_at"] = now
            self.dirty = True
            return self.get(key)
        stored = quote.to_dict()
        for k in ("price_text", "change_text", "dir"):
            stored.pop(k, None)
        stored["price"] = quote.price
        stored["change"] = quote.change
        stored["change_pct"] = quote.change_pct
        self.entries[key] = {"quote": stored, "fetched_at": now, "stale": False}
        self.dirty = True
        return self.get(key)

    def clear(self):
        self.entries = {}
        self.dirty = True

    def save(self):
        if self.dirty:
            write_json_atomic(self.path, self.entries)
            self.dirty = False


class CandleCache:
    def __init__(self, path):
        self.path = path
        try:
            data = read_json(path, {})
        except ValueError:
            data = {}
        self.entries = data if isinstance(data, dict) else {}
        self.dirty = False

    @staticmethod
    def key(symbol, rng):
        return f"{normalize(symbol)}|{rng}"

    def get(self, symbol, rng, now, ttl=CANDLE_TTL_SECONDS):
        e = self.entries.get(self.key(symbol, rng))
        if not e or now - int(e.get("fetched_at") or 0) > ttl:
            return None
        return CandleSeries.from_dict(e.get("series") or {})

    def put(self, series, now):
        self.entries[self.key(series.symbol, series.range)] = {
            "series": {
                "symbol": series.symbol,
                "range": series.range,
                "points": series.points,
                "valid": series.valid,
                "message": series.message,
                "currency": series.currency,
            },
            "fetched_at": now,
        }
        self.dirty = True

    def save(self):
        if self.dirty:
            write_json_atomic(self.path, self.entries)
            self.dirty = False

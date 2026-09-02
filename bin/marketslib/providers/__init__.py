"""Provider seam (port of Data/IMarketDataProvider.cs).

A provider says which asset categories it serves; the repository routes
each instrument to the first active provider that supports its category.
Everything is optional except quotes: search defaults to nothing, candles
to an invalid series, news to unsupported.

What a provider learns while running (CoinGecko's coin ids, Yahoo's
per-symbol currency and name) it keeps in a dict the repository injected
from `cache_file` in the state dir; `learned_cache()` says what changed so
the repository writes the file back once per run. `learned_ids()` is the
per-symbol provider id that goes onto the watchlist entry.
"""

from ..models import CandleSeries, Quote


class Provider:
    id = "base"
    attribution = None  # {"label": ..., "url": ...} — shown wherever this provider's data shows
    is_exclusive = False  # True = only this provider serves (Demo mode)
    supports_news = False
    cache_file = None  # basename in the state dir of this provider's learned dict, or None

    def supports(self, category):
        return False

    def quotes(self, instruments, now):
        """One batch. May raise http.FetchError; the repository turns that into
        invalid placeholders and an envelope error. A provider that made
        several requests and lost only some of them returns the invalid rows
        and reports the failures through take_errors() instead."""
        return [Quote.invalid(i) for i in instruments]

    def search(self, query):
        return []

    def candles(self, instrument, rng):
        return CandleSeries.invalid(instrument.symbol, rng, "No chart source for this instrument")

    def learned_ids(self):
        """{symbol: provider_id} discovered during this run, for the watchlist's provider_ids."""
        return {}

    def learned_cache(self):
        """Entries added to the injected cache dict this run; non-empty means 'write cache_file'."""
        return {}

    def take_errors(self):
        """FetchErrors from partial failures inside quotes(); cleared on read."""
        return []

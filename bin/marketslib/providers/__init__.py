"""Provider seam (port of Data/IMarketDataProvider.cs).

A provider says which asset categories it serves; the repository routes
each instrument to the first active provider that supports its category.
Everything is optional except quotes: search defaults to nothing, candles
to an invalid series, news to unsupported.
"""

from ..models import CandleSeries, Quote


class Provider:
    id = "base"
    attribution = None  # {"label": ..., "url": ...} — shown wherever this provider's data shows
    is_exclusive = False  # True = only this provider serves (Demo mode)
    supports_news = False

    def supports(self, category):
        return False

    def quotes(self, instruments, now):
        """One batch. May raise http.FetchError; the repository turns that into
        invalid placeholders and an envelope error."""
        return [Quote.invalid(i) for i in instruments]

    def search(self, query):
        return []

    def candles(self, instrument, rng):
        return CandleSeries.invalid(instrument.symbol, rng, "No chart source for this instrument")

    def learned_ids(self):
        """{symbol: provider_id} discovered during this run, for the repo to persist."""
        return {}

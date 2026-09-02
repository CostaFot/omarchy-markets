"""The coordinator (port of Data/MarketRepository.cs).

Routes each instrument to the first active provider that supports its
category, fetches one batch per provider, merges back into the caller's
order, and writes through the keep-last-good quote cache. Every priced
surface reads the same cache, so the bar and the panel can never show two
different prices for one symbol.
"""

import os

from . import fmt, http
from .cache import CandleCache, QuoteCache
from .models import CATEGORIES, CandleSeries, Instrument, Quote, normalize
from .providers.coingecko import CoinGecko
from .state import read_json, state_dir, write_json_atomic
from .store import Watchlist

SETTING_DEFAULTS = {
    "demoMode": False,
    "portfolioCurrency": "USD",
    "showRateLimitErrors": True,
    "strip": "favorites",  # favorites | watchlist | portfolio | favorites+portfolio
    "stripShowPrice": True,
    "stripMax": 6,
}

# 3-letter codes we accept as halves of an FX pair when guessing a category.
FX_CODES = {
    "USD", "EUR", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD", "CNY", "HKD", "SGD", "SEK", "NOK", "DKK",
    "PLN", "ZAR", "MXN", "INR", "BRL", "KRW", "TRY", "CZK", "HUF", "ILS", "THB", "IDR", "MYR", "PHP",
}


class Settings(dict):
    def __init__(self, overrides=None):
        super().__init__(SETTING_DEFAULTS)
        for k, v in (overrides or {}).items():
            if k in SETTING_DEFAULTS:
                self[k] = v

    @property
    def demo(self):
        return bool(self.get("demoMode"))


class Repository:
    def __init__(self, settings=None, directory=None):
        self.settings = settings if isinstance(settings, Settings) else Settings(settings)
        self.dir = directory or state_dir()
        self.watchlist = Watchlist(os.path.join(self.dir, "watchlist.json"))
        self.quote_cache = QuoteCache(os.path.join(self.dir, "quotes-cache.json"))
        self.candle_cache = CandleCache(os.path.join(self.dir, "candles-cache.json"))
        self.coin_ids_path = os.path.join(self.dir, "coin-ids.json")
        try:
            ids = read_json(self.coin_ids_path, {})
        except ValueError:
            ids = {}
        self.coin_ids = ids if isinstance(ids, dict) else {}
        self.providers = self._build_providers()
        self.errors = []  # FetchError dicts collected during this run
        self.served_by = set()  # provider ids that returned valid data this run

    def _build_providers(self):
        # Fixed order. Later sessions insert Demo (exclusive), Twelve Data and
        # Finnhub (keyed) and Frankfurter ahead of CoinGecko; CoinGecko stays
        # last so a keyless install still prices crypto.
        return [CoinGecko(id_cache=self.coin_ids)]

    # ---- providers -------------------------------------------------------
    def active_providers(self):
        exclusive = [p for p in self.providers if p.is_exclusive]
        return exclusive or self.providers

    def provider_for(self, category):
        for p in self.active_providers():
            if p.supports(category):
                return p
        return None

    def attribution(self):
        seen = []
        for p in self.active_providers():
            if p.id in self.served_by and p.attribution and p.attribution not in seen:
                seen.append(p.attribution)
        return seen

    def status_rows(self):
        rows = []
        if http.RATE_LIMITED and self.settings.get("showRateLimitErrors") and not self.settings.demo:
            rows.append({"kind": "rate_limited", "text": "Rate-limited — showing last known prices"})
        unpriced = [c for c in CATEGORIES if self.provider_for(c) is None]
        tracked = {i.category for i in self.watchlist.tracked()}
        if any(c in tracked for c in unpriced):
            rows.append({
                "kind": "no_provider",
                "text": "Stocks and currencies aren't priced yet — no provider is configured for them",
            })
        return rows

    def persist_learned(self):
        changed = False
        for p in self.active_providers():
            for sym, pid in p.learned_ids().items():
                if self.coin_ids.get(sym) != pid:
                    self.coin_ids[sym] = pid
                    changed = True
                self.watchlist.merge_provider_ids(sym, {p.id: pid})
        if changed:
            write_json_atomic(self.coin_ids_path, self.coin_ids)

    def flush(self):
        self.quote_cache.save()
        self.candle_cache.save()
        self.persist_learned()

    # ---- instruments -----------------------------------------------------
    def guess_category(self, symbol):
        s = normalize(symbol)
        if len(s) == 6 and s[:3] in FX_CODES and s[3:] in FX_CODES:
            return "currency"
        return "stock"

    def instrument_for(self, spec):
        """'BTC' or 'DOGE:crypto' -> Instrument, preferring the tracked entry."""
        sym, _, cat = str(spec).partition(":")
        sym = normalize(sym)
        cat = cat.strip().lower()
        known = self.watchlist.instrument(sym)
        if known and (not cat or cat == known.category):
            return known
        if cat not in CATEGORIES:
            cat = self.guess_category(sym)
        ids = {}
        if cat == "crypto" and self.coin_ids.get(sym):
            ids["coingecko"] = self.coin_ids[sym]
        return Instrument(sym, known.name if known else sym, cat, ids)

    # ---- quotes ----------------------------------------------------------
    def fetch_quotes(self, instruments, now):
        """Route → one batch per provider → merge in the caller's order.
        Provider failures become invalid placeholders plus an entry in self.errors."""
        batches = {}
        unserviceable = []
        for inst in instruments:
            p = self.provider_for(inst.category)
            if p is None:
                unserviceable.append(inst)
            else:
                batches.setdefault(p.id, (p, []))[1].append(inst)

        by_symbol = {}
        for p, batch in batches.values():
            try:
                for q in p.quotes(batch, now):
                    by_symbol[normalize(q.symbol)] = q
                    if q.valid:
                        self.served_by.add(p.id)
            except http.FetchError as e:
                self.errors.append({**e.to_dict(), "provider": p.id})
                for inst in batch:
                    by_symbol[normalize(inst.symbol)] = Quote.invalid(inst)
        for inst in unserviceable:
            by_symbol[normalize(inst.symbol)] = Quote.invalid(inst)
        return [by_symbol.get(normalize(i.symbol)) or Quote.invalid(i) for i in instruments]

    def refresh(self, instruments, now, keep_last_good=True):
        """Fetch and write through the cache; returns the cached (possibly
        last-good) quotes in order."""
        fetched = self.fetch_quotes(instruments, now)
        return [self.quote_cache.upsert(q, now, keep_last_good) for q in fetched]

    def observed(self, extra=()):
        """The union every poll refreshes: watchlist ∪ favorites (∪ portfolio later) ∪ extra."""
        seen = {}
        for inst in self.watchlist.tracked():
            seen[inst.symbol] = inst
        for spec in extra:
            inst = self.instrument_for(spec)
            seen.setdefault(inst.symbol, inst)
        return list(seen.values())

    # ---- documents -------------------------------------------------------
    def snapshot(self, now, max_age=None, extra=()):
        instruments = self.observed(extra)
        symbols = [i.symbol for i in instruments]
        cached = max_age is not None and bool(symbols) and self.quote_cache.fresh(symbols, max_age, now)
        if cached:
            quotes = [self.quote_cache.get(s) or Quote.invalid(i) for s, i in zip(symbols, instruments)]
            for q in quotes:
                p = self.provider_for(q.category) if q.valid else None
                if p:
                    self.served_by.add(p.id)
        else:
            quotes = self.refresh(instruments, now)
        by_symbol = {q.symbol: q for q in quotes}
        return {
            "cached": cached,
            "quotes": {s: q.to_dict() for s, q in by_symbol.items()},
            "instruments": self.watchlist.rows(),
            "favorites": [i.symbol for i in self.watchlist.favorites()],
            "strip": self.strip(by_symbol),
        }

    def strip(self, by_symbol):
        mode = str(self.settings.get("strip") or "favorites")
        show_price = bool(self.settings.get("stripShowPrice", True))
        try:
            limit = max(0, int(self.settings.get("stripMax", 6)))
        except (TypeError, ValueError):
            limit = 6
        source = self.watchlist.watchlist() if mode.startswith("watchlist") else self.watchlist.favorites()
        out = []
        for inst in source:
            q = by_symbol.get(inst.symbol)
            if q is None:
                q = self.quote_cache.get(inst.symbol) or Quote.invalid(inst)
            out.append({
                "symbol": inst.symbol,
                "label": inst.symbol,
                "value_text": fmt.strip_value_text(q, show_price),
                "dir": fmt.direction(q.change) if q.valid else "flat",
                "valid": q.valid,
                "stale": q.stale,
            })
            if len(out) >= limit:
                break
        return out

    def quotes(self, specs, now):
        instruments = [self.instrument_for(s) for s in specs]
        quotes = self.refresh(instruments, now)
        return {"quotes": [q.to_dict() for q in quotes]}

    def search(self, query):
        query = str(query or "").strip()
        merged = {}
        for p in self.active_providers():
            try:
                for inst in p.search(query):
                    merged.setdefault(inst.symbol, inst)
                    if inst.provider_ids:
                        self.served_by.add(p.id)
            except http.FetchError as e:
                self.errors.append({**e.to_dict(), "provider": p.id})
        results = []
        for inst in merged.values():
            in_wl, is_fav = self.watchlist.flags(inst.symbol)
            parts = []
            if in_wl:
                parts.append("On watchlist")
            if is_fav:
                parts.append("★ Favorite")
            parts.append("Enter for details")
            results.append({
                **inst.to_dict(),
                "in_watchlist": in_wl,
                "is_favorite": is_fav,
                "in_portfolio": False,
                "subtitle_text": " · ".join(parts),
            })
        return {"query": query, "results": results}

    def candles(self, spec, rng, now):
        inst = self.instrument_for(spec)
        rng = str(rng or "").upper()
        cached = self.candle_cache.get(inst.symbol, rng, now)
        if cached is not None:
            if cached.valid:
                p = self.provider_for(inst.category)
                if p:
                    self.served_by.add(p.id)
            return {"cached": True, "series": cached.to_dict()}
        p = self.provider_for(inst.category)
        if p is None:
            series = CandleSeries.invalid(inst.symbol, rng, "No chart source for this instrument")
        else:
            series = p.candles(inst, rng)
            if series.valid:
                self.served_by.add(p.id)
        self.candle_cache.put(series, now)
        return {"cached": False, "series": series.to_dict()}

    # ---- membership ------------------------------------------------------
    def membership_payload(self, now):
        by_symbol = {}
        for inst in self.watchlist.tracked():
            q = self.quote_cache.get(inst.symbol)
            if q:
                by_symbol[inst.symbol] = q
        return {
            "instruments": self.watchlist.rows(),
            "favorites": [i.symbol for i in self.watchlist.favorites()],
            "strip": self.strip(by_symbol),
        }

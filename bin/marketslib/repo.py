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
from .providers.yahoo import Yahoo, plain_symbol
from .state import read_json, state_dir, write_json_atomic
from .store import Watchlist

SEARCH_LIMIT = 15

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
        self.caches = {}  # cache_file basename -> the dict injected into that provider
        self.coin_ids = self._load_cache(CoinGecko.cache_file)
        self.yahoo_meta = self._load_cache(Yahoo.cache_file)
        self.providers = self._build_providers()
        self.errors = []  # FetchError dicts collected during this run
        self.served_by = set()  # provider ids that returned valid data this run

    def _load_cache(self, name):
        """A provider's learned dict from the state dir; a corrupt file is an empty dict."""
        try:
            data = read_json(os.path.join(self.dir, name), {})
        except ValueError:
            data = {}
        cache = data if isinstance(data, dict) else {}
        self.caches[name] = cache
        return cache

    def _build_providers(self):
        # Fixed order: first provider that supports a category wins. Later
        # sessions insert Demo (exclusive), Twelve Data and Finnhub (keyed)
        # ahead of Yahoo; CoinGecko stays last so a keyless install still
        # prices crypto.
        return [Yahoo(meta_cache=self.yahoo_meta), CoinGecko(id_cache=self.coin_ids)]

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
        for p in self.active_providers():
            for sym, pid in p.learned_ids().items():
                self.watchlist.merge_provider_ids(sym, {p.id: pid})
            if p.cache_file and p.learned_cache():
                cache = self.caches.setdefault(p.cache_file, {})
                cache.update(p.learned_cache())
                write_json_atomic(os.path.join(self.dir, p.cache_file), cache)

    def flush(self):
        self.quote_cache.save()
        self.candle_cache.save()
        self.persist_learned()

    # ---- instruments -----------------------------------------------------
    def canonical_symbol(self, symbol):
        """The neutral symbol plus any provider id the spelling implied:
        'eurusd=x' -> ('EURUSD', {'yahoo': 'EURUSD=X'}); '^GSPC' -> ('^GSPC', {})."""
        s = normalize(symbol)
        plain = plain_symbol(s)
        if plain != s:
            return plain, {"yahoo": s}
        return s, {}

    def guess_category(self, symbol):
        s, ids = self.canonical_symbol(symbol)
        if ids.get("yahoo"):
            return "currency"
        if len(s) == 6 and s[:3] in FX_CODES and s[3:] in FX_CODES:
            return "currency"
        return "stock"

    def instrument_for(self, spec):
        """'BTC', 'DOGE:crypto' or 'EURUSD=X' -> Instrument, preferring the tracked entry."""
        sym, _, cat = str(spec).partition(":")
        sym, ids = self.canonical_symbol(sym)
        cat = cat.strip().lower()
        known = self.watchlist.instrument(sym)
        if known and (not cat or cat == known.category):
            return known
        if cat not in CATEGORIES:
            cat = self.guess_category(sym if not ids else ids["yahoo"])
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
            for e in p.take_errors():
                self.errors.append({**e.to_dict(), "provider": p.id})
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
        """With --max-age only the symbols attempted longer than S seconds ago
        are fetched; the rest are cache reads. A detail page for an untracked
        symbol (`--extra`) therefore costs one call for that symbol, not a
        refetch of the whole watchlist, and a second bar polling inside S
        fetches nothing at all (`cached: true`)."""
        instruments = self.observed(extra)
        if max_age is None:
            stale = list(instruments)
        else:
            stale = [i for i in instruments if now - self.quote_cache.fetched_at(i.symbol) > max_age]
        fetched = {q.symbol: q for q in self.refresh(stale, now)} if stale else {}
        quotes = []
        for inst in instruments:
            q = fetched.get(inst.symbol)
            if q is None:
                q = self.quote_cache.get(inst.symbol) or Quote.invalid(inst)
                p = self.provider_for(q.category) if q.valid else None
                if p:
                    self.served_by.add(p.id)
            quotes.append(q)
        cached = bool(instruments) and not stale
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
        """Every active provider's results merged, at most SEARCH_LIMIT rows.

        Order: an exact symbol match that its own provider ranked in its top
        three comes first (`sol` → SOL the coin, `hsbc` → HSBC the stock),
        then the rest alternate between providers so a 15-row stock list
        cannot push every coin past the cap. A low-ranked exact match stays
        low: CoinGecko lists a junk coin whose symbol is APPLE, and `apple`
        must still find AAPL first. Duplicates collapse per (symbol,
        category): SOL the stock and SOL the coin both survive, while two
        stock providers dedupe. Measured against live results 2026-09-03."""
        query = str(query or "").strip()
        wanted = normalize(query)
        lists = []
        for p in self.active_providers():
            try:
                found = p.search(query)
            except http.FetchError as e:
                self.errors.append({**e.to_dict(), "provider": p.id})
                continue
            if any(i.provider_ids for i in found):
                self.served_by.add(p.id)
            lists.append(found)
        promoted, rest = [], []
        for found in lists:
            for rank, inst in enumerate(found):
                (promoted if rank < 3 and inst.symbol == wanted else rest).append((rank, inst))
        rest.sort(key=lambda r: r[0])  # round-robin: every provider's first, then every second, ...
        merged = {}
        for _, inst in promoted + rest:
            merged.setdefault((inst.symbol, inst.category), inst)
        results = []
        for inst in list(merged.values())[:SEARCH_LIMIT]:
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
        """What a mutation returns so the panel re-renders with no second
        call: the tracked set, the strip, and the cached quotes for the
        tracked symbols (a just-added symbol was priced on the way in)."""
        by_symbol = {}
        for inst in self.watchlist.tracked():
            q = self.quote_cache.get(inst.symbol)
            if q:
                by_symbol[inst.symbol] = q
        return {
            "quotes": {s: q.to_dict() for s, q in by_symbol.items()},
            "instruments": self.watchlist.rows(),
            "favorites": [i.symbol for i in self.watchlist.favorites()],
            "strip": self.strip(by_symbol),
        }

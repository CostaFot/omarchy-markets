"""The coordinator (port of Data/MarketRepository.cs).

Routes each instrument to the first active provider that supports its
category, fetches one batch per provider, merges back into the caller's
order, and writes through the keep-last-good quote cache. Every priced
surface reads the same cache, so the bar and the panel can never show two
different prices for one symbol.
"""

import os
import time

from . import fmt, http, portfolio
from .cache import CandleCache, QuoteCache
from .models import CATEGORIES, CandleSeries, Instrument, Quote, normalize
from .providers.coingecko import CoinGecko
from .providers.frankfurter import Frankfurter
from .providers.yahoo import Yahoo, plain_symbol
from .state import read_json, state_dir, write_json_atomic
from .store import Portfolio, Watchlist

SEARCH_LIMIT = 15

SETTING_DEFAULTS = {
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


class Repository:
    def __init__(self, settings=None, directory=None):
        self.settings = settings if isinstance(settings, Settings) else Settings(settings)
        self.dir = directory or state_dir()
        self.watchlist = Watchlist(os.path.join(self.dir, "watchlist.json"))
        self.portfolio = Portfolio(os.path.join(self.dir, "portfolio.json"))
        self.quote_cache = QuoteCache(os.path.join(self.dir, "quotes-cache.json"))
        self.candle_cache = CandleCache(os.path.join(self.dir, "candles-cache.json"))
        self.caches = {}  # cache_file basename -> the dict injected into that provider
        self.coin_ids = self._load_cache(CoinGecko.cache_file)
        self.yahoo_meta = self._load_cache(Yahoo.cache_file)
        self.providers = self._build_providers()
        # Rates only, never quotes: the portfolio's converter (see the module).
        self.fx = Frankfurter(cache=self._load_cache("fx-rates.json"))
        self.errors = []  # FetchError dicts collected during this run
        self.served_by = set()  # provider ids that returned valid data this run
        self._rate_limited = None  # decided once per run by rate_limited()

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
        # sessions insert Twelve Data and Finnhub (keyed)
        # ahead of Yahoo; CoinGecko stays last so a keyless install still
        # prices crypto.
        return [Yahoo(meta_cache=self.yahoo_meta), CoinGecko(id_cache=self.coin_ids)]

    # ---- providers -------------------------------------------------------
    def provider_for(self, category):
        for p in self.providers:
            if p.supports(category):
                return p
        return None

    def attribution(self):
        seen = []
        for p in self.providers:
            if p.id in self.served_by and p.attribution and p.attribution not in seen:
                seen.append(p.attribution)
        if self.fx.served:
            seen.append(self.fx.attribution)
        return seen

    # ---- the rate-limit latch --------------------------------------------
    # Port of RateLimitSignal: a 429 that survived the retries sets a flag
    # that only a later successful request clears. The helper is a new
    # process per call, so the flag lives in `rate-limit.json`; a cached
    # `snapshot --max-age` (no request at all) keeps reporting it, which is
    # what makes the panel banner stay up until a fetch actually succeeds.
    # A latch older than an hour is ignored: nothing polls for that long
    # without making a request, so it can only be a leftover.
    RATE_LIMIT_LATCH_SECONDS = 3600

    def rate_limited(self, now=None):
        if self._rate_limited is not None:
            return self._rate_limited
        now = int(time.time()) if now is None else int(now)
        path = os.path.join(self.dir, "rate-limit.json")
        try:
            latch = read_json(path, {})
        except ValueError:
            latch = {}
        since = int(latch.get("since") or 0) if isinstance(latch, dict) else 0
        if http.RATE_LIMITED:
            result = True
            if not since:
                write_json_atomic(path, {"since": now})
        elif http.SUCCEEDED:
            result = False
        else:
            result = since > 0 and 0 <= now - since < self.RATE_LIMIT_LATCH_SECONDS
        if not result and since:
            try:
                os.remove(path)
            except OSError:
                pass
        self._rate_limited = result
        return result

    def status_rows(self):
        rows = []
        if self.rate_limited() and self.settings.get("showRateLimitErrors"):
            rows.append({
                "kind": "rate_limited",
                "text": "Rate-limited — showing last known prices",
                "detail": "Will refresh automatically once the limit clears.",
            })
        unpriced = [c for c in CATEGORIES if self.provider_for(c) is None]
        tracked = {i.category for i in self.watchlist.tracked()}
        if any(c in tracked for c in unpriced):
            rows.append({
                "kind": "no_provider",
                "text": "Stocks and currencies aren't priced yet — no provider is configured for them",
            })
        return rows

    def persist_learned(self):
        for p in self.providers:
            for sym, pid in p.learned_ids().items():
                self.watchlist.merge_provider_ids(sym, {p.id: pid})
                self.portfolio.merge_provider_ids(sym, {p.id: pid})
            if p.cache_file and p.learned_cache():
                cache = self.caches.setdefault(p.cache_file, {})
                cache.update(p.learned_cache())
                write_json_atomic(os.path.join(self.dir, p.cache_file), cache)
        if self.fx.dirty:
            write_json_atomic(os.path.join(self.dir, "fx-rates.json"), self.fx.cache)
            self.fx.dirty = False

    def flush(self):
        self.quote_cache.save()
        self.candle_cache.save()
        self.persist_learned()
        self.rate_limited()

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
        known = self.watchlist.instrument(sym) or self.portfolio.instrument(sym)
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
        """The union every poll refreshes: watchlist ∪ favorites ∪ portfolio ∪ extra."""
        seen = {}
        for inst in self.watchlist.tracked():
            seen[inst.symbol] = inst
        for inst in self.portfolio.instruments():
            seen.setdefault(inst.symbol, inst)
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
        return {"cached": cached, **self.document_sections(by_symbol, now)}

    def document_sections(self, by_symbol, now):
        """Everything a document carries besides the envelope: the quotes it
        priced or read, the tracked set, the favorites, the portfolio and the
        strip. Snapshots and mutations share it so the panel merges one
        shape."""
        held = self.portfolio_payload(by_symbol, now)
        held_symbols = {p["symbol"] for p in held["positions"]}
        return {
            "quotes": {s: q.to_dict() for s, q in by_symbol.items()},
            "instruments": self.instrument_rows(),
            "favorites": [i.symbol for i in self.watchlist.favorites()],
            "portfolio": held,
            "strip": self.strip(by_symbol, held),
            "held": sorted(held_symbols),
        }

    def instrument_rows(self):
        rows = self.watchlist.rows()
        for r in rows:
            r["in_portfolio"] = self.portfolio.contains(r["symbol"])
        return rows

    # The strip's portfolio entry: the bank glyph (nf-fa-bank, U+F19C, the
    # hub's icon for the page) as the label, the total and today's move as
    # the value. Written as an escape so no editor can strip it.
    PORTFOLIO_LABEL = "\uf19c"

    def strip(self, by_symbol, held=None):
        mode = str(self.settings.get("strip") or "favorites")
        show_price = bool(self.settings.get("stripShowPrice", True))
        try:
            limit = max(0, int(self.settings.get("stripMax", 6)))
        except (TypeError, ValueError):
            limit = 6
        out = []
        if "portfolio" in mode:
            held = held if held is not None else self.portfolio_payload(by_symbol, now=int(time.time()))
            t = held["totals"]
            if t["has_holdings"]:
                priced = t["counted"] > 0
                if not priced:
                    value = "—"
                elif show_price:
                    value = f"{t['value_compact_text']} {t['change_compact_text']}"
                else:
                    value = t["change_compact_text"]
                out.append({
                    "symbol": "PORTFOLIO",
                    "label": self.PORTFOLIO_LABEL,
                    "value_text": value,
                    "dir": t["dir"] if priced else "flat",
                    "valid": priced,
                    "stale": t["stale"] or t["unconverted"] > 0,
                })
        if mode.startswith("watchlist"):
            source = self.watchlist.watchlist()
        elif "favorites" in mode:
            source = self.watchlist.favorites()
        else:
            source = []
        for inst in source:
            if len(out) >= limit:
                break
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
        return out[:limit]

    # ---- portfolio -------------------------------------------------------
    def portfolio_payload(self, by_symbol, now):
        """The holdings priced from `by_symbol` (else the cache), converted
        into the reporting currency with at most one Frankfurter call, and
        rolled up. `note` says when the rates could not be fetched."""
        preferred = str(self.settings.get("portfolioCurrency") or "USD").upper()
        holdings = self.portfolio.positions()
        quotes = {}
        for h in holdings:
            q = by_symbol.get(h["symbol"]) or self.quote_cache.get(h["symbol"])
            if q is not None:
                quotes[h["symbol"]] = q
        natives = sorted({str(q.currency).upper() for q in quotes.values() if q.valid})
        rates = self.fx.rates_to(preferred, natives, now) if natives else {}
        rows = []
        for h in holdings:
            q = quotes.get(h["symbol"])
            rate = rates.get(str(q.currency).upper()) if q is not None and q.valid else None
            rows.append(portfolio.position_row(h, q, preferred, rate))
        t = portfolio.totals(rows, preferred)
        note = ""
        if self.fx.error is not None and t["unconverted"] > 0:
            note = f"Exchange rates unavailable ({self.fx.error.message}); the total leaves out what could not be converted."
        return {"currency": preferred, "positions": rows, "totals": t, "note": note}

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
        for p in self.providers:
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
                "in_portfolio": self.portfolio.contains(inst.symbol),
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
        call: the tracked set, the portfolio, the strip, and the cached
        quotes for the tracked and held symbols (a just-added symbol was
        priced on the way in)."""
        by_symbol = {}
        for inst in self.watchlist.tracked() + self.portfolio.instruments():
            q = self.quote_cache.get(inst.symbol)
            if q:
                by_symbol[inst.symbol] = q
        return self.document_sections(by_symbol, now)

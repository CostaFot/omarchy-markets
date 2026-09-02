"""CoinGecko — keyless crypto quotes, search and price history.

Endpoints (public API, no key):
  GET /coins/markets?vs_currency=usd&ids=bitcoin,ethereum         quotes by id
  GET /coins/markets?vs_currency=usd&symbols=btc&include_tokens=top quotes by symbol (top-ranked coin wins)
  GET /search?query=sol                                             identity lookup
  GET /coins/{id}/market_chart?vs_currency=usd&days=N              history; auto granularity
                                                                   (1 day = 5 min, <= 90 = hourly, else daily)
Verified live 2026-09-02: the public tier refuses days > 365 with HTTP 401
("limited to querying historical data within the past 365 days"), so 5Y
is served as one year with a note. Rate limit is unpublished and low
(roughly 5–15 calls/min); the repository budgets at most two calls per
poll and one per chart. An optional demo key raises that to 30/min and
is sent as the x-cg-demo-api-key header, never in the URL.
"""

import os
import urllib.parse
from datetime import datetime, timezone

from .. import http
from ..models import RANGE_DAYS, CandleSeries, Instrument, Quote, normalize
from . import Provider

DEFAULT_BASE = "https://api.coingecko.com/api/v3"
MAX_POINTS = 300
MAX_DAYS_PUBLIC = 365
SEARCH_LIMIT = 15


def _to_epoch(iso):
    if not iso:
        return 0
    try:
        return int(datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0


def downsample(points, limit=MAX_POINTS):
    """Evenly thin a series to `limit` points, always keeping the first and last."""
    n = len(points)
    if n <= limit:
        return points
    step = (n - 1) / (limit - 1)
    return [points[round(i * step)] for i in range(limit)]


class CoinGecko(Provider):
    id = "coingecko"
    attribution = {"label": "Data by CoinGecko", "url": "https://www.coingecko.com"}

    def __init__(self, base_url=None, api_key=None, id_cache=None):
        self.base = (base_url or os.environ.get("MARKETS_COINGECKO_URL") or DEFAULT_BASE).rstrip("/")
        self.api_key = api_key or None
        self.id_cache = id_cache if id_cache is not None else {}
        self._learned = {}

    def supports(self, category):
        return category == "crypto"

    def learned_ids(self):
        return dict(self._learned)

    # ---- helpers -----------------------------------------------------------
    def _headers(self):
        return {"x-cg-demo-api-key": self.api_key} if self.api_key else {}

    def _url(self, path, **params):
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        return f"{self.base}{path}" + (f"?{query}" if query else "")

    def _known_id(self, instrument):
        cid = (instrument.provider_ids or {}).get("coingecko")
        if cid:
            return cid
        return self.id_cache.get(normalize(instrument.symbol))

    def _remember(self, symbol, coin_id):
        symbol = normalize(symbol)
        if coin_id and self.id_cache.get(symbol) != coin_id:
            self.id_cache[symbol] = coin_id
            self._learned[symbol] = coin_id

    def _markets(self, **params):
        data = http.get_json(
            self._url("/coins/markets", vs_currency="usd", price_change_percentage="24h", per_page=250, **params),
            headers=self._headers(),
            tag="coingecko",
        )
        return data if isinstance(data, list) else []

    @staticmethod
    def _quote_from_row(instrument, row, now):
        price = row.get("current_price")
        if price is None:
            return Quote.invalid(instrument)
        return Quote(
            symbol=instrument.symbol,
            name=str(row.get("name") or instrument.name or instrument.symbol),
            category="crypto",
            price=float(price),
            change=float(row.get("price_change_24h") or 0.0),
            change_pct=float(row.get("price_change_percentage_24h") or 0.0),
            currency="USD",
            valid=True,
            updated_at=_to_epoch(row.get("last_updated")) or now,
        )

    # ---- Provider ----------------------------------------------------------
    def quotes(self, instruments, now):
        by_symbol = {normalize(i.symbol): i for i in instruments}
        with_id = {}
        without = []
        for sym, inst in by_symbol.items():
            cid = self._known_id(inst)
            if cid:
                with_id[sym] = cid
            else:
                without.append(sym)

        rows_by_id = {}
        rows_by_symbol = {}
        if with_id:
            for row in self._markets(ids=",".join(sorted(set(with_id.values())))):
                rows_by_id[str(row.get("id") or "")] = row
        if without:
            for row in self._markets(symbols=",".join(s.lower() for s in without), include_tokens="top"):
                rows_by_symbol.setdefault(normalize(row.get("symbol")), row)

        out = []
        for sym, inst in by_symbol.items():
            row = rows_by_id.get(with_id.get(sym)) if sym in with_id else rows_by_symbol.get(sym)
            if row is None:
                out.append(Quote.invalid(inst))
                continue
            self._remember(sym, row.get("id"))
            out.append(self._quote_from_row(inst, row, now))
        return out

    def search(self, query):
        data = http.get_json(self._url("/search", query=query), headers=self._headers(), tag="coingecko")
        coins = data.get("coins") if isinstance(data, dict) else None
        out = []
        seen = set()
        for c in coins or []:
            sym = normalize(c.get("symbol"))
            cid = str(c.get("id") or "")
            if not sym or not cid or sym in seen:
                continue
            seen.add(sym)
            out.append(Instrument(sym, str(c.get("name") or sym), "crypto", {"coingecko": cid}))
            if len(out) >= SEARCH_LIMIT:
                break
        return out

    def _resolve_id(self, instrument):
        cid = self._known_id(instrument)
        if cid:
            return cid
        sym = normalize(instrument.symbol)
        for cand in self.search(sym):
            if cand.symbol == sym:
                cid = cand.provider_ids.get("coingecko")
                self._remember(sym, cid)
                return cid
        return None

    def candles(self, instrument, rng):
        days = RANGE_DAYS.get(rng)
        if days is None:
            return CandleSeries.invalid(instrument.symbol, rng, f"Unknown range {rng}")
        note = ""
        if days > MAX_DAYS_PUBLIC:
            days = MAX_DAYS_PUBLIC
            note = "CoinGecko's public API only serves one year of history; showing 1Y"
        try:
            cid = self._resolve_id(instrument)
            if not cid:
                return CandleSeries.invalid(instrument.symbol, rng, f"CoinGecko does not know {instrument.symbol}")
            data = http.get_json(
                self._url(f"/coins/{urllib.parse.quote(cid)}/market_chart", vs_currency="usd", days=days),
                headers=self._headers(),
                tag="coingecko",
            )
        except http.FetchError as e:
            return CandleSeries.invalid(instrument.symbol, rng, e.message)
        prices = data.get("prices") if isinstance(data, dict) else None
        points = []
        for p in prices or []:
            try:
                ts, close = int(p[0]) // 1000, float(p[1])
            except (TypeError, ValueError, IndexError):
                continue
            points.append([ts, close])
        if not points:
            return CandleSeries.invalid(instrument.symbol, rng, "No chart data for this range")
        return CandleSeries(instrument.symbol, rng, downsample(points), valid=True, message=note, currency="USD")

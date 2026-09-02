"""Yahoo Finance — keyless stocks, indices and FX: quotes, search, 1D–5Y candles.

Unofficial. There is no API contract, no terms for third parties and no
promise the shapes below survive; the rule is that anything unexpected
becomes an invalid row, never an exception. Verified live 2026-09-03:

  GET /v8/finance/spark?symbols=A,B,C&range=1d&interval=5m
      one call for many symbols: {SYM: {close[], timestamp[], previousClose}}.
      No currency, no name; unknown symbols are silently dropped.
  GET /v8/finance/chart/{SYM}?range=1d&interval=5m
      meta.regularMarketPrice / previousClose / currency / longName /
      instrumentType / exchangeName / regularMarketTime, plus the series.
      Unknown symbol → 404 with a JSON error. range=5y&interval=1wk is a
      real five years (CoinGecko's public tier stops at one).
  GET /v1/finance/search?q=&quotesCount=15&newsCount=0&enableFuzzyQuery=true
      quotes[] with symbol / shortname / longname / quoteType / exchDisp.
  v7/finance/quote needs a cookie and a crumb (401): not used.

Yahoo refuses the default library user agents with 429; http.get sends
costafot.markets/<version>, which is accepted.

Quotes are two-tier so the steady state is one request per poll: a symbol
whose currency is already in the meta cache (yahoo-meta.json) rides the
spark batch; a symbol seen for the first time gets its own chart call,
which prices it and learns its name and currency for next time.
"""

import os
import urllib.parse

from .. import fmt, http
from ..models import RANGES, CandleSeries, Instrument, Quote, downsample, normalize
from . import Provider

DEFAULT_BASE = "https://query1.finance.yahoo.com"
SEARCH_LIMIT = 15

# Our range → (Yahoo range, interval). 1D at 5 min, a week at 15 min,
# a month and a year daily, five years weekly (263 points).
RANGE_PARAMS = {
    "1D": ("1d", "5m"),
    "1W": ("5d", "15m"),
    "1M": ("1mo", "1d"),
    "1Y": ("1y", "1d"),
    "5Y": ("5y", "1wk"),
}

STOCK_TYPES = {"EQUITY", "ETF", "INDEX", "MUTUALFUND", "FUTURE"}
FX_SUFFIX = "=X"


def wire_symbol(instrument):
    """What Yahoo calls this instrument: the learned id, else EURUSD → EURUSD=X, else as-is."""
    wire = (instrument.provider_ids or {}).get("yahoo")
    if wire:
        return str(wire)
    sym = normalize(instrument.symbol)
    if instrument.category == "currency" and len(sym) == 6 and sym.isalpha():
        return sym + FX_SUFFIX
    return sym


def plain_symbol(wire):
    """The neutral symbol for a wire one: EURUSD=X → EURUSD, everything else unchanged."""
    wire = normalize(wire)
    if wire.endswith(FX_SUFFIX) and len(wire) == 6 + len(FX_SUFFIX):
        return wire[: -len(FX_SUFFIX)]
    return wire


def _float(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN guard


def _last_close(closes):
    for c in reversed(closes or []):
        f = _float(c)
        if f is not None:
            return f
    return None


class Yahoo(Provider):
    id = "yahoo"
    attribution = {"label": "Data by Yahoo Finance", "url": "https://finance.yahoo.com"}
    cache_file = "yahoo-meta.json"  # wire symbol -> {name, currency, type, exchange}

    def __init__(self, base_url=None, meta_cache=None):
        self.base = (base_url or os.environ.get("MARKETS_YAHOO_URL") or DEFAULT_BASE).rstrip("/")
        self.meta = meta_cache if meta_cache is not None else {}
        self._learned_meta = {}
        self._learned_ids = {}
        self._errors = []

    def supports(self, category):
        return category in ("stock", "currency")

    def learned_ids(self):
        return dict(self._learned_ids)

    def learned_cache(self):
        return dict(self._learned_meta)

    def take_errors(self):
        errors, self._errors = self._errors, []
        return errors

    # ---- helpers -----------------------------------------------------------
    def _url(self, path, **params):
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        return f"{self.base}{path}" + (f"?{query}" if query else "")

    def _chart_url(self, wire, rng, interval):
        return self._url(f"/v8/finance/chart/{urllib.parse.quote(wire, safe='')}", range=rng, interval=interval)

    def _meta_of(self, wire):
        m = self.meta.get(wire)
        return m if isinstance(m, dict) else {}

    def _remember(self, wire, **fields):
        current = self._meta_of(wire)
        merged = dict(current)
        for k, v in fields.items():
            if v not in (None, ""):
                merged[k] = v
        if merged != current:
            self.meta[wire] = merged
            self._learned_meta[wire] = merged

    def _get(self, url):
        return http.get_json(url, tag="yahoo")

    @staticmethod
    def _chart_result(data):
        """The first chart result, or None when the body is not shaped like one."""
        chart = data.get("chart") if isinstance(data, dict) else None
        results = chart.get("result") if isinstance(chart, dict) else None
        if isinstance(results, list) and results and isinstance(results[0], dict):
            return results[0]
        return None

    @staticmethod
    def _closes(result):
        """[[unix_seconds, close], ...] with the null closes dropped."""
        stamps = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        closes = quote.get("close") or [] if isinstance(quote, dict) else []
        points = []
        for ts, close in zip(stamps, closes):
            c = _float(close)
            if c is None:
                continue
            try:
                points.append([int(ts), round(c, 6)])  # 324.9599914550781 is float noise, not a price
            except (TypeError, ValueError):
                continue
        return points

    def _quote(self, instrument, raw_currency, price, prev, updated_at, name=None):
        code, scale = fmt.currency_scale(raw_currency)
        price = price * scale
        change = (price - prev * scale) if prev is not None else 0.0
        pct = (change / (prev * scale) * 100.0) if prev else 0.0
        return Quote(
            symbol=instrument.symbol,
            name=str(name or instrument.name or instrument.symbol),
            category=instrument.category,
            price=price,
            change=change,
            change_pct=pct,
            currency=code,
            valid=True,
            updated_at=int(updated_at or 0),
        )

    # ---- quotes ------------------------------------------------------------
    def _spark(self, wires, by_wire, now):
        """One batch for the symbols whose currency is already known."""
        out = {}
        try:
            data = self._get(self._url("/v8/finance/spark", symbols=",".join(wires), range="1d", interval="5m"))
        except http.FetchError as e:
            self._errors.append(e)
            return {w: Quote.invalid(by_wire[w]) for w in wires}
        if not isinstance(data, dict):
            data = {}
        for wire in wires:
            inst = by_wire[wire]
            row = data.get(wire)
            price = _last_close(row.get("close")) if isinstance(row, dict) else None
            if price is None:
                out[wire] = Quote.invalid(inst)
                continue
            meta = self._meta_of(wire)
            stamps = row.get("timestamp") or []
            updated = stamps[-1] if stamps and isinstance(stamps[-1], (int, float)) else now
            prev = _float(row.get("previousClose"))
            if prev is None:
                prev = _float(row.get("chartPreviousClose"))
            out[wire] = self._quote(inst, meta.get("currency"), price, prev, updated, meta.get("name"))
            self._learned_ids[inst.symbol] = wire
        return out

    def _chart_quote(self, instrument, wire, now):
        """One symbol on its first sight: price it and learn its meta."""
        try:
            data = self._get(self._chart_url(wire, "1d", "5m"))
        except http.FetchError as e:
            if e.status != 404:
                self._errors.append(e)
            return Quote.invalid(instrument)
        result = self._chart_result(data)
        if result is None:
            return Quote.invalid(instrument)
        meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        price = _float(meta.get("regularMarketPrice"))
        if price is None:
            closes = self._closes(result)
            price = closes[-1][1] if closes else None
        if price is None:
            return Quote.invalid(instrument)
        prev = _float(meta.get("previousClose"))
        if prev is None:
            prev = _float(meta.get("chartPreviousClose"))
        name = meta.get("longName") or meta.get("shortName")
        self._remember(
            wire,
            name=name,
            currency=meta.get("currency"),
            type=meta.get("instrumentType"),
            exchange=meta.get("exchangeName"),
        )
        self._learned_ids[instrument.symbol] = wire
        return self._quote(instrument, meta.get("currency"), price, prev, meta.get("regularMarketTime") or now, name)

    def quotes(self, instruments, now):
        by_wire = {}
        for inst in instruments:
            by_wire.setdefault(wire_symbol(inst), inst)
        known = [w for w in by_wire if self._meta_of(w).get("currency")]
        unknown = [w for w in by_wire if w not in known]

        priced = {}
        if known:
            priced.update(self._spark(known, by_wire, now))
        for wire in unknown:
            if http.RATE_LIMITED:
                # One 429 is enough for this poll; the rest ride keep-last-good.
                priced[wire] = Quote.invalid(by_wire[wire])
                continue
            priced[wire] = self._chart_quote(by_wire[wire], wire, now)

        if self._errors and not any(q.valid for q in priced.values()):
            # Nothing came back at all: let the repository record the outage
            # for the whole batch, as it does for any provider.
            errors, self._errors = self._errors, []
            raise errors[0]
        return [priced.get(wire_symbol(i)) or Quote.invalid(i) for i in instruments]

    # ---- search ------------------------------------------------------------
    def search(self, query):
        data = self._get(self._url("/v1/finance/search", q=query, quotesCount=SEARCH_LIMIT, newsCount=0,
                                   enableFuzzyQuery="true"))
        rows = data.get("quotes") if isinstance(data, dict) else None
        out = []
        seen = set()
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            wire = normalize(row.get("symbol"))
            qtype = str(row.get("quoteType") or "").upper()
            if qtype == "CURRENCY":
                category = "currency"
            elif qtype in STOCK_TYPES:
                category = "stock"
            else:
                continue  # CRYPTOCURRENCY is CoinGecko's, anything unknown is nobody's
            sym = plain_symbol(wire) if category == "currency" else wire
            if not sym or sym in seen:
                continue
            seen.add(sym)
            name = str(row.get("longname") or row.get("shortname") or sym).strip()
            self._remember(wire, name=name, type=qtype, exchange=row.get("exchDisp"))
            out.append(Instrument(sym, name, category, {"yahoo": wire}))
            if len(out) >= SEARCH_LIMIT:
                break
        return out

    # ---- candles -----------------------------------------------------------
    def candles(self, instrument, rng):
        params = RANGE_PARAMS.get(rng)
        if params is None:
            return CandleSeries.invalid(instrument.symbol, rng, f"Unknown range {rng}")
        wire = wire_symbol(instrument)
        try:
            data = self._get(self._chart_url(wire, *params))
        except http.FetchError as e:
            message = "Not found" if e.status == 404 else e.message
            return CandleSeries.invalid(instrument.symbol, rng, message)
        result = self._chart_result(data)
        if result is None:
            return CandleSeries.invalid(instrument.symbol, rng, "No chart data for this range")
        meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        raw_currency = meta.get("currency") or self._meta_of(wire).get("currency")
        code, scale = fmt.currency_scale(raw_currency)
        points = [[ts, close * scale] for ts, close in self._closes(result)]
        if not points:
            return CandleSeries.invalid(instrument.symbol, rng, "No chart data for this range")
        self._remember(wire, name=meta.get("longName") or meta.get("shortName"), currency=meta.get("currency"),
                       type=meta.get("instrumentType"), exchange=meta.get("exchangeName"))
        return CandleSeries(instrument.symbol, rng, downsample(points), valid=True, currency=code)


assert set(RANGE_PARAMS) == set(RANGES)

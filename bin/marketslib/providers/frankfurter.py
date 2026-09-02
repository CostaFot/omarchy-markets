"""Frankfurter — keyless ECB reference rates, for converting portfolio
holdings into one reporting currency. Port of Helpers/CurrencyConverter.cs.

Never a quote source: the FX pairs on the watchlist are Yahoo's. This
class answers one question, "how many units of the portfolio currency is
one unit of each holding's currency", in ONE request per run at most:

  GET /v1/latest?base=USD&symbols=EUR,GBP
      {"amount":1.0,"base":"USD","date":"2026-09-02","rates":{"EUR":0.86371,"GBP":0.74167}}

so native→preferred is 1 / rates[native]. Verified live 2026-09-03: a
symbol the ECB does not publish is silently missing from `rates`; when
none of the requested symbols (or the base) is known the answer is a 404
`{"message":"not found"}`. Both mean "not convertible" and are cached as
null so a portfolio with an exotic currency does not refetch every poll.
A network failure or any other status caches nothing, so the next run
retries. Rates are ECB daily fixings, cached for an hour per pair.
"""

import os
import urllib.parse

from .. import http

DEFAULT_BASE = "https://api.frankfurter.dev"
TTL_SECONDS = 3600

def _code(value):
    return str(value or "").strip().upper()


def _rate(value):
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f and f > 0 else None


class Frankfurter:
    id = "frankfurter"
    attribution = {"label": "Rates by Frankfurter (ECB)", "url": "https://frankfurter.dev"}

    def __init__(self, base_url=None, cache=None):
        self.base = (base_url or os.environ.get("MARKETS_FRANKFURTER_URL") or DEFAULT_BASE).rstrip("/")
        # "FROM>TO" -> {"rate": float|None, "at": unix_seconds}; None is a
        # cached "not convertible".
        self.cache = cache if cache is not None else {}
        self.dirty = False
        self.served = False  # a request came back with rates this run
        self.error = None  # the FetchError of a failed request this run, if any

    @staticmethod
    def key(native, preferred):
        return f"{native}>{preferred}"

    def _fresh(self, key, now):
        e = self.cache.get(key)
        if not isinstance(e, dict):
            return None
        at = e.get("at")
        if not isinstance(at, (int, float)) or now - at >= TTL_SECONDS or now < at:
            return None
        return e

    def _store(self, native, preferred, rate, now):
        self.cache[self.key(native, preferred)] = {"rate": rate, "at": int(now)}
        self.dirty = True

    def rate(self, native, preferred, now):
        """Units of `preferred` per one `native`, from the cache: 1.0 for the
        same currency, None when unknown or not fresh."""
        f, t = _code(native), _code(preferred)
        if not f or not t:
            return None
        if f == t:
            return 1.0
        e = self._fresh(self.key(f, t), now)
        return _rate(e.get("rate")) if e else None

    def rates_to(self, preferred, natives, now):
        """{native: rate or None} for every native, fetching the ones not
        fresh in the cache in one request. Safe to call every run: a steady
        portfolio does no network."""
        to = _code(preferred)
        out = {}
        if not to:
            return {_code(n): None for n in natives}
        needed = []
        for n in natives:
            f = _code(n)
            if not f:
                continue
            if f == to:
                out[f] = 1.0
                continue
            e = self._fresh(self.key(f, to), now)
            if e is not None:
                out[f] = _rate(e.get("rate"))
            elif f not in needed:
                needed.append(f)
        if needed:
            fetched = self._fetch(to, needed, now)
            for f in needed:
                out[f] = fetched.get(f)
        return out

    def _fetch(self, to, needed, now):
        query = urllib.parse.urlencode({"base": to, "symbols": ",".join(needed)})
        try:
            data = http.get_json(f"{self.base}/v1/latest?{query}", tag="frankfurter")
        except http.FetchError as e:
            if e.status == 404:
                # Nothing requested is an ECB currency (or the base is not):
                # remember that rather than asking again next poll.
                data = {"rates": {}}
            else:
                self.error = e
                return {}
        rates = data.get("rates") if isinstance(data, dict) else None
        if not isinstance(rates, dict):
            self.error = http.FetchError("bad_response", "unexpected rates document")
            return {}
        self.served = True
        out = {}
        for f in needed:
            per_preferred = _rate(rates.get(f))  # units of `f` per one `to`
            rate = 1.0 / per_preferred if per_preferred else None
            self._store(f, to, rate, now)
            out[f] = rate
        return out

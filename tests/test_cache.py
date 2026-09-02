import os
import tempfile
import unittest

import _paths  # noqa: F401
from marketslib.cache import CandleCache, QuoteCache
from marketslib.models import CandleSeries, Instrument, Quote

BTC = Instrument("BTC", "Bitcoin", "crypto")


def good(price=100.0):
    return Quote("BTC", "Bitcoin", "crypto", price=price, change=1.0, change_pct=1.0)


class KeepLastGood(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "quotes-cache.json")
        self.cache = QuoteCache(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_an_invalid_quote_never_overwrites_a_valid_one(self):
        self.cache.upsert(good(), now=1000)
        served = self.cache.upsert(Quote.invalid(BTC), now=1100)
        self.assertTrue(served.valid)
        self.assertTrue(served.stale)
        self.assertEqual(served.price, 100.0)
        self.assertEqual(self.cache.fetched_at("BTC"), 1100)

    def test_a_valid_quote_replaces_a_stale_one(self):
        self.cache.upsert(good(), now=1000)
        self.cache.upsert(Quote.invalid(BTC), now=1100)
        served = self.cache.upsert(good(101.0), now=1200)
        self.assertFalse(served.stale)
        self.assertEqual(served.price, 101.0)

    def test_invalid_replaces_invalid(self):
        self.cache.upsert(Quote.invalid(BTC), now=1000)
        self.assertFalse(self.cache.get("BTC").valid)
        self.assertFalse(self.cache.get("BTC").stale)

    def test_hard_refresh_overwrites_even_with_invalid(self):
        self.cache.upsert(good(), now=1000)
        served = self.cache.upsert(Quote.invalid(BTC), now=1100, keep_last_good=False)
        self.assertFalse(served.valid)

    def test_fresh_requires_every_symbol_within_max_age(self):
        self.cache.upsert(good(), now=1000)
        self.assertTrue(self.cache.fresh(["btc"], 30, now=1020))
        self.assertFalse(self.cache.fresh(["BTC"], 30, now=1040))
        self.assertFalse(self.cache.fresh(["BTC", "ETH"], 30, now=1020))

    def test_persists_and_reloads(self):
        self.cache.upsert(good(), now=1000)
        self.cache.save()
        again = QuoteCache(self.path)
        self.assertEqual(again.get("BTC").price, 100.0)
        self.assertEqual(again.fetched_at("BTC"), 1000)

    def test_corrupt_cache_file_starts_empty(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("nope")
        self.assertIsNone(QuoteCache(self.path).get("BTC"))


class Candles(unittest.TestCase):
    def test_ttl(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = CandleCache(os.path.join(tmp, "c.json"))
            cache.put(CandleSeries("BTC", "1M", [[1, 1.0], [2, 2.0]]), now=1000)
            self.assertEqual(cache.get("BTC", "1M", now=1200).points, [[1, 1.0], [2, 2.0]])
            self.assertIsNone(cache.get("BTC", "1M", now=1400))
            self.assertIsNone(cache.get("BTC", "1W", now=1200))
            cache.save()
            self.assertIsNotNone(CandleCache(os.path.join(tmp, "c.json")).get("BTC", "1M", now=1200))


if __name__ == "__main__":
    unittest.main()

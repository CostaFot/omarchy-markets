import os
import tempfile
import unittest

import _paths  # noqa: F401
from marketslib import http
from marketslib.models import CandleSeries, Instrument, Quote
from marketslib.providers import Provider
from marketslib.state import write_json_atomic
from marketslib.repo import Repository, Settings


class FakeCrypto(Provider):
    id = "fakecrypto"
    attribution = {"label": "Data by Fake", "url": "https://example.test"}

    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail
        self.searched = []

    def supports(self, category):
        return category == "crypto"

    def quotes(self, instruments, now):
        self.calls += 1
        if self.fail:
            raise http.FetchError("network", "boom")
        return [Quote(i.symbol, i.name, "crypto", price=10.0 + n, change=1.0, change_pct=2.0)
                for n, i in enumerate(instruments)]

    def search(self, query):
        self.searched.append(query)
        return [Instrument("SOL", "Solana", "crypto", {"fake": "sol"}), Instrument("SLX", "Solstice", "crypto")]

    def candles(self, instrument, rng):
        self.calls += 1
        return CandleSeries(instrument.symbol, rng, [[1, 1.0], [2, 2.0]])


class OtherCrypto(FakeCrypto):
    id = "other"

    def search(self, query):
        return [Instrument("SOL", "Not Solana", "crypto"), Instrument("SOLO", "Solo", "crypto")]


class RepositoryRouting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        http.RATE_LIMITED = False
        http.SUCCEEDED = False
        self.repo = Repository(Settings(), directory=self.tmp.name)
        self.crypto = FakeCrypto()
        self.repo.providers = [self.crypto]

    def tearDown(self):
        self.tmp.cleanup()
        http.RATE_LIMITED = False
        http.SUCCEEDED = False

    def test_unserviceable_categories_become_invalid_placeholders_in_order(self):
        instruments = [Instrument("AAPL", "Apple", "stock"), Instrument("BTC", "Bitcoin", "crypto"),
                       Instrument("EURUSD", "", "currency")]
        quotes = self.repo.fetch_quotes(instruments, now=1000)
        self.assertEqual([q.symbol for q in quotes], ["AAPL", "BTC", "EURUSD"])
        self.assertEqual([q.valid for q in quotes], [False, True, False])
        self.assertEqual(self.crypto.calls, 1)

    def test_a_failing_provider_yields_placeholders_and_an_error(self):
        self.repo.providers = [FakeCrypto(fail=True)]
        quotes = self.repo.fetch_quotes([Instrument("BTC", "Bitcoin", "crypto")], now=1000)
        self.assertFalse(quotes[0].valid)
        self.assertEqual(self.repo.errors[0]["code"], "network")
        self.assertEqual(self.repo.errors[0]["provider"], "fakecrypto")

    def test_refresh_serves_last_good_through_a_failure(self):
        self.repo.refresh([Instrument("BTC", "Bitcoin", "crypto")], now=1000)
        self.repo.providers = [FakeCrypto(fail=True)]
        quotes = self.repo.refresh([Instrument("BTC", "Bitcoin", "crypto")], now=1100)
        self.assertTrue(quotes[0].valid)
        self.assertTrue(quotes[0].stale)

    def test_snapshot_lists_everything_and_builds_the_strip_from_favorites(self):
        doc = self.repo.snapshot(now=1000)
        self.assertFalse(doc["cached"])
        self.assertEqual(len(doc["quotes"]), 9)
        self.assertEqual(len(doc["instruments"]), 9)
        self.assertEqual([e["label"] for e in doc["strip"]], ["BTC", "ETH", "SOL"])
        self.assertTrue(all(e["valid"] for e in doc["strip"]))
        self.assertFalse(doc["quotes"]["AAPL"]["valid"])

    def test_snapshot_with_max_age_is_a_cache_read(self):
        self.repo.snapshot(now=1000)
        doc = self.repo.snapshot(now=1020, max_age=30)
        self.assertTrue(doc["cached"])
        self.assertEqual(self.crypto.calls, 1)
        doc = self.repo.snapshot(now=1100, max_age=30)
        self.assertFalse(doc["cached"])
        self.assertEqual(self.crypto.calls, 2)

    def test_extra_symbols_join_the_observed_set(self):
        doc = self.repo.snapshot(now=1000, extra=["DOGE:crypto"])
        self.assertIn("DOGE", doc["quotes"])
        self.assertEqual(len(doc["instruments"]), 9)  # extra is observed, not tracked

    def test_max_age_refreshes_only_the_symbols_older_than_it(self):
        self.repo.snapshot(now=1000)
        self.assertEqual(self.crypto.calls, 1)
        doc = self.repo.snapshot(now=1010, max_age=30, extra=["DOGE:crypto"])
        self.assertFalse(doc["cached"])
        self.assertEqual(self.crypto.calls, 2)
        self.assertTrue(doc["quotes"]["DOGE"]["valid"])
        self.assertTrue(doc["quotes"]["BTC"]["valid"])
        doc = self.repo.snapshot(now=1020, max_age=30, extra=["DOGE:crypto"])
        self.assertTrue(doc["cached"])
        self.assertEqual(self.crypto.calls, 2)

    def test_strip_modes_and_cap(self):
        self.repo.settings = Settings({"strip": "watchlist", "stripMax": 2, "stripShowPrice": False})
        doc = self.repo.snapshot(now=1000)
        self.assertEqual([e["label"] for e in doc["strip"]], ["AAPL", "MSFT"])
        self.assertEqual(doc["strip"][0]["value_text"], "—")

    def test_search_merges_and_stamps_membership(self):
        self.repo.providers = [self.crypto, OtherCrypto()]
        doc = self.repo.search("sol")
        symbols = [r["symbol"] for r in doc["results"]]
        self.assertEqual(symbols, ["SOL", "SLX", "SOLO"])
        sol = doc["results"][0]
        self.assertEqual(sol["name"], "Solana")
        self.assertTrue(sol["in_watchlist"] and sol["is_favorite"])
        self.assertEqual(sol["subtitle_text"], "On watchlist · ★ Favorite · Enter for details")
        self.assertEqual(doc["results"][1]["subtitle_text"], "Enter for details")

    def test_category_guessing(self):
        self.assertEqual(self.repo.guess_category("EURUSD"), "currency")
        self.assertEqual(self.repo.guess_category("AAPL"), "stock")
        self.assertEqual(self.repo.instrument_for("doge:crypto").category, "crypto")
        self.assertEqual(self.repo.instrument_for("BTC").provider_ids, {"coingecko": "bitcoin"})
        self.assertEqual(self.repo.instrument_for("BTC:stock").category, "stock")

    def test_candles_are_cached_per_symbol_and_range(self):
        first = self.repo.candles("BTC", "1M", now=1000)
        second = self.repo.candles("BTC", "1M", now=1100)
        self.assertFalse(first["cached"])
        self.assertTrue(second["cached"])
        self.assertEqual(self.crypto.calls, 1)
        self.assertTrue(second["series"]["valid"])
        none = self.repo.candles("AAPL", "1M", now=1000)
        self.assertFalse(none["series"]["valid"])
        self.assertIn("No chart source", none["series"]["message"])

    def test_status_rows(self):
        rows = self.repo.status_rows()
        self.assertEqual([r["kind"] for r in rows], ["no_provider"])
        http.RATE_LIMITED = True
        self.repo._rate_limited = None  # decided once per run; this test is several runs
        self.assertEqual([r["kind"] for r in self.repo.status_rows()], ["rate_limited", "no_provider"])
        self.repo.settings = Settings({"showRateLimitErrors": False})
        self.assertEqual([r["kind"] for r in self.repo.status_rows()], ["no_provider"])

    def test_rate_limit_latch_survives_runs_until_a_request_succeeds(self):
        # Run 1: a 429 survived the retries.
        http.RATE_LIMITED = True
        self.repo.flush()
        self.assertTrue(self.repo.rate_limited())
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "rate-limit.json")))
        # Run 2: a pure cache read makes no request; the latch still reports.
        http.RATE_LIMITED = False
        http.SUCCEEDED = False
        again = Repository(Settings(), directory=self.tmp.name)
        self.assertTrue(again.rate_limited())
        # Run 3: something came back 2xx; cleared and the file is gone.
        http.SUCCEEDED = True
        cleared = Repository(Settings(), directory=self.tmp.name)
        self.assertFalse(cleared.rate_limited())
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "rate-limit.json")))

    def test_rate_limit_latch_expires_after_an_hour(self):
        write_json_atomic(os.path.join(self.tmp.name, "rate-limit.json"), {"since": 1000})
        http.SUCCEEDED = False
        self.assertTrue(Repository(Settings(), directory=self.tmp.name).rate_limited(now=1000 + 600))
        self.assertFalse(Repository(Settings(), directory=self.tmp.name).rate_limited(now=1000 + 7200))

    def test_attribution_only_for_providers_that_served(self):
        self.assertEqual(self.repo.attribution(), [])
        self.repo.snapshot(now=1000)
        self.assertEqual(self.repo.attribution(), [FakeCrypto.attribution])

    def test_membership_payload_uses_cached_prices(self):
        self.repo.snapshot(now=1000)
        self.repo.watchlist.add_favorite(Instrument("DOGE", "Dogecoin", "crypto"))
        payload = self.repo.membership_payload(now=1000)
        labels = [e["label"] for e in payload["strip"]]
        self.assertEqual(labels, ["BTC", "DOGE", "ETH", "SOL"])
        self.assertEqual(payload["strip"][1]["value_text"], "—")
        self.assertTrue(payload["strip"][0]["valid"])

    def test_portfolio_holdings_are_observed_and_rolled_up_from_the_cache(self):
        self.repo.portfolio.set(Instrument("DOGE", "Dogecoin", "crypto"), 100, 5)
        doc = self.repo.snapshot(now=1000)
        self.assertIn("DOGE", doc["quotes"])  # held, not tracked, still priced
        self.assertEqual(len(doc["instruments"]), 9)
        self.assertEqual(doc["held"], ["DOGE"])
        pf = doc["portfolio"]
        self.assertEqual(pf["currency"], "USD")
        self.assertEqual(pf["positions"][0]["holding_text"], "DOGE · 100 units")
        self.assertEqual(pf["totals"]["counted"], 1)
        self.assertTrue(pf["totals"]["has_cost_basis"])
        self.assertEqual(pf["note"], "")
        # No FX call for USD holdings in USD, and the membership payload carries the same shape.
        payload = self.repo.membership_payload(now=1000)
        self.assertEqual(payload["portfolio"]["totals"]["value_text"], pf["totals"]["value_text"])
        self.assertIn("DOGE", payload["quotes"])

    def test_settings_ignore_unknown_keys_and_keep_defaults(self):
        s = Settings({"stripMax": 3, "bogus": 1})
        self.assertEqual(s["stripMax"], 3)
        self.assertNotIn("bogus", s)
        self.assertEqual(s["strip"], "favorites")
        self.assertNotIn("demoMode", Settings({"demoMode": True}))


if __name__ == "__main__":
    unittest.main()

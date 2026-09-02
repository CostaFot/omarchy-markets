import json
import os
import unittest
from unittest import mock

import _paths  # noqa: F401
from fakeserver import FakeServer, coingecko_routes, fixture
from marketslib import http
from marketslib.models import Instrument
from marketslib.providers.coingecko import CoinGecko, downsample

BTC = Instrument("BTC", "Bitcoin", "crypto", {"coingecko": "bitcoin"})
ETH = Instrument("ETH", "Ethereum", "crypto")
NOPE = Instrument("NOPE", "Nope", "crypto")

FAST = {"MARKETS_BACKOFF_SCALE": "0", "MARKETS_SOCKET_TIMEOUT": "5", "MARKETS_TOTAL_TIMEOUT": "5"}


class CoinGeckoParsing(unittest.TestCase):
    def setUp(self):
        self.server = coingecko_routes(FakeServer().start())
        self.cg = CoinGecko(base_url=self.server.base_url)
        http.RATE_LIMITED = False

    def tearDown(self):
        self.server.stop()
        http.RATE_LIMITED = False

    def test_quotes_by_known_id_use_the_ids_parameter(self):
        quotes = self.cg.quotes([BTC], now=5)
        self.assertEqual(self.server.hits("/coins/markets")[0][1]["ids"], "bitcoin")
        self.assertEqual(quotes[0].price, 77356)
        self.assertAlmostEqual(quotes[0].change_pct, 0.02132)
        self.assertEqual(quotes[0].currency, "USD")
        self.assertGreater(quotes[0].updated_at, 1_700_000_000)
        self.assertTrue(quotes[0].valid)

    def test_quotes_by_symbol_learn_the_id(self):
        quotes = self.cg.quotes([ETH], now=5)
        q = self.server.hits("/coins/markets")[0][1]
        self.assertEqual(q["symbols"], "eth")
        self.assertEqual(q["include_tokens"], "top")
        self.assertEqual(quotes[0].name, "Ethereum")
        self.assertEqual(self.cg.learned_ids(), {"ETH": "ethereum"})
        self.assertEqual(self.cg.id_cache["ETH"], "ethereum")

    def test_mixed_batch_makes_two_calls_and_keeps_order(self):
        quotes = self.cg.quotes([ETH, BTC, NOPE], now=5)
        self.assertEqual([q.symbol for q in quotes], ["ETH", "BTC", "NOPE"])
        self.assertEqual([q.valid for q in quotes], [True, True, False])
        self.assertEqual(len(self.server.hits("/coins/markets")), 2)

    def test_search_dedupes_by_symbol_and_carries_the_id(self):
        results = self.cg.search("sol")
        self.assertEqual(results[0].symbol, "SOL")
        self.assertEqual(results[0].provider_ids, {"coingecko": "solana"})
        self.assertEqual(len({r.symbol for r in results}), len(results))
        self.assertEqual(self.server.hits("/search")[0][1]["query"], "sol")

    def test_candles_parse_and_convert_to_seconds(self):
        series = self.cg.candles(BTC, "1M")
        self.assertTrue(series.valid)
        self.assertEqual(len(series.points), 49)
        self.assertLess(series.points[0][0], 10_000_000_000)
        self.assertEqual(self.server.hits("/coins/bitcoin/market_chart")[0][1]["days"], "31")
        doc = series.to_dict()
        self.assertIn("· 1M", doc["range_change_text"])

    def test_five_years_is_clamped_to_one_with_a_note(self):
        series = self.cg.candles(BTC, "5Y")
        self.assertEqual(self.server.hits("/coins/bitcoin/market_chart")[0][1]["days"], "365")
        self.assertIn("one year", series.message)
        self.assertTrue(series.valid)

    def test_candles_resolve_an_unknown_id_through_search(self):
        sol = Instrument("SOL", "Solana", "crypto")
        self.server.route("/coins/solana/market_chart", body=fixture("coingecko_chart.json"))
        series = self.cg.candles(sol, "1W")
        self.assertTrue(series.valid)
        self.assertEqual(len(self.server.hits("/search")), 1)
        self.assertEqual(self.cg.learned_ids(), {"SOL": "solana"})

    def test_unknown_range_and_unknown_coin_are_invalid_not_exceptions(self):
        self.assertFalse(self.cg.candles(BTC, "9Y").valid)
        self.server.route("/search", json_body={"coins": []})
        series = self.cg.candles(NOPE, "1D")
        self.assertFalse(series.valid)
        self.assertIn("NOPE", series.message)

    def test_provider_error_bodies_reach_the_message(self):
        self.server.route("/coins/bitcoin/market_chart", status=401,
                          json_body={"error": {"status": {"error_message": "Your request exceeds the allowed time range."}}})
        series = self.cg.candles(BTC, "1Y")
        self.assertFalse(series.valid)
        self.assertIn("allowed time range", series.message)

    def test_api_key_travels_as_a_header_never_in_the_url(self):
        keyed = CoinGecko(base_url=self.server.base_url, api_key="secret-key")
        seen = {}

        def capture(query, path):
            seen.update(query)
            return 200, {}, fixture("coingecko_markets.json")

        self.server.handler("/coins/markets", capture)
        keyed.quotes([BTC], now=5)
        self.assertNotIn("secret-key", json.dumps(seen))


class DownsampleAndRedaction(unittest.TestCase):
    def test_downsample_keeps_first_and_last(self):
        pts = [[i, float(i)] for i in range(1000)]
        out = downsample(pts, 300)
        self.assertEqual(len(out), 300)
        self.assertEqual(out[0], [0, 0.0])
        self.assertEqual(out[-1], [999, 999.0])
        self.assertEqual(downsample(pts[:10], 300), pts[:10])

    def test_redact_hides_key_parameters(self):
        self.assertEqual(http.redact("https://x/y?symbol=A&apikey=SECRET"), "https://x/y?symbol=A&apikey=%2A%2A%2A")
        self.assertNotIn("SECRET", http.redact("https://x/y?token=SECRET"))


class HttpHardening(unittest.TestCase):
    def setUp(self):
        self.server = FakeServer().start()
        self.cg = CoinGecko(base_url=self.server.base_url)
        http.RATE_LIMITED = False
        self.env = mock.patch.dict(os.environ, FAST)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.server.stop()
        http.RATE_LIMITED = False

    def test_429_is_retried_then_succeeds(self):
        calls = []

        def flaky(query, path):
            calls.append(1)
            if len(calls) < 3:
                return 429, {"Retry-After": "1"}, b"{}"
            return 200, {}, fixture("coingecko_markets.json")

        self.server.handler("/coins/markets", flaky)
        quotes = self.cg.quotes([BTC], now=5)
        self.assertTrue(quotes[0].valid)
        self.assertEqual(len(calls), 3)
        self.assertFalse(http.RATE_LIMITED)

    def test_persistent_429_gives_up_after_three_attempts(self):
        self.server.route("/coins/markets", status=429, body=b"{}")
        with self.assertRaises(http.FetchError) as ctx:
            self.cg.quotes([BTC], now=5)
        self.assertEqual(ctx.exception.code, "rate_limited")
        self.assertEqual(len(self.server.hits("/coins/markets")), 3)
        self.assertTrue(http.RATE_LIMITED)

    def test_a_long_retry_after_is_not_waited_for(self):
        self.server.route("/coins/markets", status=429, headers={"Retry-After": "30"}, body=b"{}")
        with self.assertRaises(http.FetchError):
            self.cg.quotes([BTC], now=5)
        self.assertEqual(len(self.server.hits("/coins/markets")), 1)
        self.assertTrue(http.RATE_LIMITED)

    def test_oversized_body_is_refused(self):
        self.server.route("/coins/markets", body=b"[" + b"1," * 5000 + b"1]")
        with mock.patch.dict(os.environ, {"MARKETS_MAX_RESPONSE_BYTES": "200"}):
            with self.assertRaises(http.FetchError) as ctx:
                self.cg.quotes([BTC], now=5)
        self.assertEqual(ctx.exception.code, "too_large")

    def test_redirects_are_not_followed(self):
        self.server.route("/coins/markets", status=302, headers={"Location": self.server.base_url + "/elsewhere"}, body=b"")
        self.server.route("/elsewhere", body=fixture("coingecko_markets.json"))
        with self.assertRaises(http.FetchError) as ctx:
            self.cg.quotes([BTC], now=5)
        self.assertEqual(ctx.exception.status, 302)
        self.assertEqual(self.server.hits("/elsewhere"), [])

    def test_connection_failure_is_a_network_error(self):
        self.server.stop()
        with self.assertRaises(http.FetchError) as ctx:
            self.cg.quotes([BTC], now=5)
        self.assertEqual(ctx.exception.code, "network")

    def test_invalid_json_is_a_bad_response(self):
        self.server.route("/coins/markets", body=b"<html>")
        with self.assertRaises(http.FetchError) as ctx:
            self.cg.quotes([BTC], now=5)
        self.assertEqual(ctx.exception.code, "bad_response")


if __name__ == "__main__":
    unittest.main()

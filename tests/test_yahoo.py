import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import _paths  # noqa: F401
from _paths import ROOT
from fakeserver import FakeServer, coingecko_routes, yahoo_routes
from marketslib import http
from marketslib.models import Instrument
from marketslib.providers.yahoo import Yahoo, plain_symbol, wire_symbol
from marketslib.repo import Repository, Settings

AAPL = Instrument("AAPL", "Apple Inc.", "stock")
HSBA = Instrument("HSBA.L", "HSBC", "stock")
SPX = Instrument("^GSPC", "S&P 500", "stock")
EURUSD = Instrument("EURUSD", "Euro / US Dollar", "currency")
NOPE = Instrument("ZZZZQQ", "Nope", "stock")
BTC = Instrument("BTC", "Bitcoin", "crypto", {"coingecko": "bitcoin"})

# What a second run would find in yahoo-meta.json after pricing these four once.
KNOWN_META = {
    "AAPL": {"name": "Apple Inc.", "currency": "USD", "type": "EQUITY", "exchange": "NMS"},
    "HSBA.L": {"name": "HSBC Holdings plc", "currency": "GBp", "type": "EQUITY", "exchange": "LSE"},
    "^GSPC": {"name": "S&P 500", "currency": "USD", "type": "INDEX", "exchange": "SNP"},
    "EURUSD=X": {"name": "EUR/USD", "currency": "USD", "type": "CURRENCY", "exchange": "CCY"},
}

FAST = {"MARKETS_BACKOFF_SCALE": "0", "MARKETS_SOCKET_TIMEOUT": "5", "MARKETS_TOTAL_TIMEOUT": "5"}


class SymbolMapping(unittest.TestCase):
    def test_fx_pairs_get_the_suffix_on_the_wire_and_lose_it_on_the_way_back(self):
        self.assertEqual(wire_symbol(EURUSD), "EURUSD=X")
        self.assertEqual(wire_symbol(AAPL), "AAPL")
        self.assertEqual(wire_symbol(SPX), "^GSPC")
        self.assertEqual(wire_symbol(Instrument("BRK-B", "", "stock")), "BRK-B")
        self.assertEqual(plain_symbol("EURUSD=X"), "EURUSD")
        self.assertEqual(plain_symbol("HSBA.L"), "HSBA.L")

    def test_a_learned_wire_symbol_wins(self):
        self.assertEqual(wire_symbol(Instrument("GOLD", "Gold", "stock", {"yahoo": "GC=F"})), "GC=F")


class YahooParsing(unittest.TestCase):
    def setUp(self):
        self.server = yahoo_routes(FakeServer().start())
        self.meta = {}
        self.yahoo = Yahoo(base_url=self.server.base_url, meta_cache=self.meta)
        http.RATE_LIMITED = False

    def tearDown(self):
        self.server.stop()
        http.RATE_LIMITED = False

    def test_first_sight_prices_through_chart_and_learns_meta(self):
        quotes = self.yahoo.quotes([AAPL], now=5)
        self.assertEqual([r[0] for r in self.server.requests], ["/v8/finance/chart/AAPL"])
        q = quotes[0]
        self.assertTrue(q.valid)
        self.assertEqual(q.price, 324.96)
        self.assertAlmostEqual(q.change, -0.17, places=2)
        self.assertAlmostEqual(q.change_pct, -0.0523, places=3)
        self.assertEqual(q.currency, "USD")
        self.assertEqual(q.name, "Apple Inc.")
        self.assertEqual(q.updated_at, 1788379201)
        self.assertEqual(self.meta["AAPL"]["currency"], "USD")
        self.assertEqual(self.yahoo.learned_cache()["AAPL"]["type"], "EQUITY")
        self.assertEqual(self.yahoo.learned_ids(), {"AAPL": "AAPL"})

    def test_pence_become_pounds(self):
        q = self.yahoo.quotes([HSBA], now=5)[0]
        self.assertTrue(q.valid)
        self.assertEqual(q.currency, "GBP")
        self.assertAlmostEqual(q.price, 15.452)
        self.assertAlmostEqual(q.change, 0.118)
        self.assertEqual(q.to_dict()["price_text"], "£15.45")
        self.assertEqual(self.meta["HSBA.L"]["currency"], "GBp")

    def test_fx_pair_rides_the_wire_symbol_and_comes_back_plain(self):
        q = self.yahoo.quotes([EURUSD], now=5)[0]
        self.assertEqual(self.server.requests[0][0], "/v8/finance/chart/EURUSD=X")
        self.assertEqual(q.symbol, "EURUSD")
        self.assertEqual(q.category, "currency")
        self.assertEqual(q.price, 1.1592)
        self.assertEqual(q.to_dict()["price_text"], "1.1592")
        self.assertEqual(self.yahoo.learned_ids(), {"EURUSD": "EURUSD=X"})

    def test_known_symbols_share_one_spark_call(self):
        self.meta.update(KNOWN_META)
        quotes = self.yahoo.quotes([AAPL, EURUSD, HSBA, SPX, NOPE], now=5)
        paths = [r[0] for r in self.server.requests]
        self.assertEqual(paths, ["/v8/finance/spark", "/v8/finance/chart/ZZZZQQ"])
        self.assertEqual(self.server.requests[0][1]["symbols"], "AAPL,EURUSD=X,HSBA.L,^GSPC")
        self.assertEqual([q.symbol for q in quotes], ["AAPL", "EURUSD", "HSBA.L", "^GSPC", "ZZZZQQ"])
        self.assertEqual([q.valid for q in quotes], [True, True, True, True, False])
        aapl, eurusd, hsba, spx = quotes[:4]
        self.assertEqual(aapl.price, 324.99)  # the last close is null in the fixture; the one before wins
        self.assertEqual(aapl.name, "Apple Inc.")
        self.assertAlmostEqual(aapl.change, -0.14, places=2)
        self.assertEqual(hsba.currency, "GBP")
        self.assertAlmostEqual(hsba.price, 15.452)
        self.assertEqual(eurusd.price, 1.1592)
        self.assertGreater(spx.updated_at, 1_700_000_000)
        self.assertEqual(self.yahoo.learned_cache(), {})

    def test_a_symbol_missing_from_spark_is_invalid_not_an_exception(self):
        self.meta.update(KNOWN_META)
        self.meta["MSFT"] = {"currency": "USD", "name": "Microsoft"}
        quotes = self.yahoo.quotes([AAPL, Instrument("MSFT", "Microsoft", "stock")], now=5)
        self.assertEqual([q.valid for q in quotes], [True, False])
        self.assertEqual(len(self.server.requests), 1)

    def test_404_is_an_invalid_quote_with_no_error(self):
        quotes = self.yahoo.quotes([NOPE], now=5)
        self.assertFalse(quotes[0].valid)
        self.assertEqual(self.yahoo.take_errors(), [])

    def test_partial_outage_keeps_what_came_back_and_reports_the_rest(self):
        self.meta.update(KNOWN_META)
        self.server.route("/v8/finance/chart/MSFT", status=500, body=b"{}")
        quotes = self.yahoo.quotes([AAPL, Instrument("MSFT", "Microsoft", "stock")], now=5)
        self.assertEqual([q.valid for q in quotes], [True, False])
        errors = self.yahoo.take_errors()
        self.assertEqual([e.code for e in errors], ["http"])
        self.assertEqual(self.yahoo.take_errors(), [])

    def test_total_outage_raises_like_any_provider(self):
        self.meta.update(KNOWN_META)
        self.server.route("/v8/finance/spark", status=500, body=b"{}")
        with self.assertRaises(http.FetchError):
            self.yahoo.quotes([AAPL, HSBA], now=5)

    def test_search_maps_types_and_strips_the_fx_suffix(self):
        results = self.yahoo.search("apple")
        self.assertEqual(self.server.requests[0][1]["q"], "apple")
        self.assertEqual(self.server.requests[0][1]["newsCount"], "0")
        self.assertEqual(results[0].symbol, "AAPL")
        self.assertEqual(results[0].category, "stock")
        self.assertEqual(results[0].name, "Apple Inc.")
        self.assertEqual(results[0].provider_ids, {"yahoo": "AAPL"})
        by_symbol = {r.symbol: r for r in results}
        self.assertEqual(by_symbol["EURUSD"].category, "currency")
        self.assertEqual(by_symbol["EURUSD"].provider_ids, {"yahoo": "EURUSD=X"})
        self.assertNotIn("BTC-USD", by_symbol)
        self.assertEqual(self.meta["AAPL"]["exchange"], "NASDAQ")
        self.assertNotIn("currency", self.meta["AAPL"])  # the first quote still needs one chart call

    def test_five_years_is_real_weekly_data_with_no_clamp_note(self):
        series = self.yahoo.candles(AAPL, "5Y")
        self.assertEqual(self.server.requests[0][1], {"range": "5y", "interval": "1wk"})
        self.assertTrue(series.valid)
        self.assertEqual(series.message, "")
        self.assertEqual(len(series.points), 262)  # 263 weekly closes, one null dropped
        self.assertLessEqual(len(series.points), 300)
        self.assertEqual(series.currency, "USD")
        doc = series.to_dict()
        self.assertIn("· 5Y", doc["range_change_text"])
        self.assertAlmostEqual(doc["last"], 324.96, places=4)
        self.assertEqual(doc["price_text"], "$324.96")

    def test_candle_ranges_map_to_yahoo_parameters(self):
        for rng, params in {"1D": ("1d", "5m"), "1W": ("5d", "15m"), "1M": ("1mo", "1d"), "1Y": ("1y", "1d")}.items():
            self.server.requests.clear()
            self.assertTrue(self.yahoo.candles(AAPL, rng).valid)
            self.assertEqual(self.server.requests[0][1], {"range": params[0], "interval": params[1]})
        self.assertFalse(self.yahoo.candles(AAPL, "9Y").valid)

    def test_pence_candles_are_scaled_too(self):
        series = self.yahoo.candles(HSBA, "1D")
        self.assertEqual(series.currency, "GBP")
        self.assertLess(series.points[-1][1], 100)
        self.assertEqual(series.to_dict()["price_text"], "£15.45")

    def test_unknown_symbol_chart_is_invalid_with_not_found(self):
        series = self.yahoo.candles(NOPE, "1D")
        self.assertFalse(series.valid)
        self.assertEqual(series.message, "Not found")

    def test_our_user_agent_is_sent_on_every_request(self):
        self.meta.update(KNOWN_META)
        self.yahoo.quotes([AAPL, NOPE], now=5)
        self.yahoo.search("apple")
        self.yahoo.candles(AAPL, "1M")
        agents = {r[2].get("user-agent") for r in self.server.requests}
        self.assertEqual(len(self.server.requests), 4)
        self.assertEqual(len(agents), 1)
        self.assertTrue(agents.pop().startswith("costafot.markets/"))

    def test_a_default_user_agent_would_be_refused(self):
        with mock.patch.dict(os.environ, FAST):
            with mock.patch.object(http, "USER_AGENT", "python-urllib/3.14"):
                with self.assertRaises(http.FetchError) as ctx:
                    self.yahoo.search("apple")
        self.assertEqual(ctx.exception.code, "rate_limited")

    def test_garbage_bodies_are_invalid_rows(self):
        self.meta.update(KNOWN_META)
        self.server.route("/v8/finance/spark", json_body={"AAPL": {"close": "nope"}})
        self.server.route("/v8/finance/chart/MSFT", json_body={"chart": {"result": [{"meta": None}]}})
        quotes = self.yahoo.quotes([AAPL, Instrument("MSFT", "Microsoft", "stock")], now=5)
        self.assertEqual([q.valid for q in quotes], [False, False])
        self.assertEqual(self.yahoo.take_errors(), [])
        self.server.route("/v8/finance/chart/AAPL", body=b"[]")
        self.assertFalse(self.yahoo.candles(AAPL, "1D").valid)

    def test_once_rate_limited_the_remaining_chart_calls_are_skipped(self):
        with mock.patch.dict(os.environ, FAST):
            self.server.route("/v8/finance/chart/AAPL", status=429, body=b"")
            with self.assertRaises(http.FetchError) as ctx:  # nothing priced: the whole batch is an outage
                self.yahoo.quotes([AAPL, HSBA], now=5)
        self.assertEqual(ctx.exception.code, "rate_limited")
        self.assertEqual({r[0] for r in self.server.requests}, {"/v8/finance/chart/AAPL"})
        self.assertTrue(http.RATE_LIMITED)


class RepositoryRouting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = yahoo_routes(coingecko_routes(FakeServer().start()))
        self.env = mock.patch.dict(os.environ, {
            "MARKETS_COINGECKO_URL": self.server.base_url,
            "MARKETS_YAHOO_URL": self.server.base_url,
            **FAST,
        })
        self.env.start()
        http.RATE_LIMITED = False
        self.repo = Repository(Settings(), directory=self.tmp.name)

    def tearDown(self):
        self.env.stop()
        self.server.stop()
        self.tmp.cleanup()
        http.RATE_LIMITED = False

    def test_stocks_and_fx_go_to_yahoo_and_crypto_stays_on_coingecko(self):
        quotes = self.repo.fetch_quotes([AAPL, BTC, EURUSD], now=5)
        self.assertEqual([q.valid for q in quotes], [True, True, True])
        self.assertEqual([q.currency for q in quotes], ["USD", "USD", "USD"])
        paths = sorted(r[0] for r in self.server.requests)
        self.assertEqual(paths, ["/coins/markets", "/v8/finance/chart/AAPL", "/v8/finance/chart/EURUSD=X"])
        self.assertEqual([a["label"] for a in self.repo.attribution()], ["Data by Yahoo Finance", "Data by CoinGecko"])

    def test_meta_is_persisted_once_and_the_second_run_is_one_spark_call(self):
        self.repo.refresh([AAPL, HSBA, EURUSD], now=5)
        self.repo.flush()
        path = os.path.join(self.tmp.name, "yahoo-meta.json")
        with open(path) as f:
            meta = json.load(f)
        self.assertEqual(set(meta), {"AAPL", "HSBA.L", "EURUSD=X"})
        self.assertEqual(meta["HSBA.L"]["currency"], "GBp")
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        mtime = os.stat(path).st_mtime_ns
        self.server.requests.clear()

        second = Repository(Settings(), directory=self.tmp.name)
        quotes = second.refresh([AAPL, HSBA, EURUSD], now=65)
        second.flush()
        self.assertEqual([r[0] for r in self.server.requests], ["/v8/finance/spark"])
        self.assertEqual([q.valid for q in quotes], [True, True, True])
        self.assertEqual(quotes[1].to_dict()["price_text"], "£15.45")
        self.assertEqual(os.stat(path).st_mtime_ns, mtime)

    def test_learned_wire_symbols_land_on_the_watchlist_entry(self):
        self.repo.refresh(self.repo.observed(), now=5)
        self.repo.flush()
        self.assertEqual(self.repo.watchlist.instrument("EURUSD").provider_ids, {"yahoo": "EURUSD=X"})
        self.assertEqual(self.repo.watchlist.instrument("BTC").provider_ids, {"coingecko": "bitcoin"})
        with open(os.path.join(self.tmp.name, "coin-ids.json")) as f:
            coin_ids = json.load(f)
        self.assertNotIn("EURUSD", coin_ids)

    def test_search_merges_both_providers_top_ranked_exact_symbols_first(self):
        self.server.route("/search", json_body={"coins": [
            {"symbol": "aapl", "id": "apple-coin", "name": "Apple Coin"},
            {"symbol": "sol", "id": "solana", "name": "Solana"},
        ]})
        doc = self.repo.search("aapl")
        rows = [(r["symbol"], r["category"]) for r in doc["results"]]
        self.assertEqual(rows[:3], [("AAPL", "stock"), ("AAPL", "crypto"), ("APLE", "stock")])
        self.assertIn(("SOL", "crypto"), rows)
        self.assertLessEqual(len(rows), 15)

    def test_search_alternates_providers_and_ignores_low_ranked_exact_matches(self):
        coins = [{"symbol": f"C{n}", "id": f"c{n}", "name": f"Coin {n}"} for n in range(12)]
        coins.append({"symbol": "apple", "id": "apple-coin", "name": "Apple Coin"})
        self.server.route("/search", json_body={"coins": coins})
        rows = [(r["symbol"], r["category"]) for r in self.repo.search("apple")["results"]]
        self.assertEqual(rows[:4], [("AAPL", "stock"), ("C0", "crypto"), ("APLE", "stock"), ("C1", "crypto")])
        self.assertEqual(len(rows), 15)
        self.assertNotIn(("APPLE", "crypto"), rows[:10])

    def test_spellings_yahoo_uses_are_understood(self):
        self.assertEqual(self.repo.guess_category("EURUSD=X"), "currency")
        self.assertEqual(self.repo.guess_category("^GSPC"), "stock")
        self.assertEqual(self.repo.guess_category("HSBA.L"), "stock")
        inst = self.repo.instrument_for("eurusd=x")
        self.assertEqual((inst.symbol, inst.name), ("EURUSD", "Euro / US Dollar"))  # the tracked entry wins
        inst = self.repo.instrument_for("gbpjpy=x")
        self.assertEqual((inst.symbol, inst.category, inst.provider_ids), ("GBPJPY", "currency", {"yahoo": "GBPJPY=X"}))
        inst = self.repo.instrument_for("USDZMW=X")
        self.assertEqual((inst.symbol, inst.category), ("USDZMW", "currency"))

    def test_status_no_longer_warns_about_unpriced_categories(self):
        self.assertEqual(self.repo.status_rows(), [])

    def test_candles_route_and_cache(self):
        doc = self.repo.candles("AAPL", "5Y", now=5)
        self.assertTrue(doc["series"]["valid"])
        self.assertFalse(doc["cached"])
        self.assertTrue(self.repo.candles("AAPL", "5Y", now=6)["cached"])
        doc = self.repo.candles("ZZZZQQ", "1D", now=5)
        self.assertFalse(doc["series"]["valid"])
        self.assertEqual(self.repo.errors, [])


class CliEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = yahoo_routes(coingecko_routes(FakeServer().start()))
        self.env = dict(os.environ, MARKETS_STATE_DIR=self.tmp.name, MARKETS_COINGECKO_URL=self.server.base_url,
                        MARKETS_YAHOO_URL=self.server.base_url, **FAST)

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()

    def run_cli(self, *args):
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "bin", "markets"), *args],
                              capture_output=True, text=True, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.splitlines()[0])

    def test_acceptance_commands(self):
        doc = self.run_cli("quotes", "AAPL", "EURUSD", "HSBA.L", "BTC")
        self.assertEqual([(q["symbol"], q["valid"]) for q in doc["quotes"]],
                         [("AAPL", True), ("EURUSD", True), ("HSBA.L", True), ("BTC", True)])
        self.assertEqual(doc["quotes"][2]["price_text"], "£15.45")
        self.assertTrue(doc["ok"])

        doc = self.run_cli("search", "apple")
        self.assertEqual((doc["results"][0]["symbol"], doc["results"][0]["category"]), ("AAPL", "stock"))

        doc = self.run_cli("candles", "AAPL", "5Y")
        self.assertTrue(doc["series"]["valid"])
        self.assertLessEqual(doc["series"]["n"], 300)
        self.assertEqual(doc["series"]["message"], "")

        doc = self.run_cli("candles", "ZZZZQQ", "1D")
        self.assertFalse(doc["series"]["valid"])
        self.assertTrue(doc["ok"])

        doc = self.run_cli("watchlist", "add", "eurusd=x", "currency")
        self.assertIn("EURUSD", [i["symbol"] for i in doc["instruments"]])
        doc = self.run_cli("watchlist", "add", "^GSPC", "stock", "S&P 500")
        self.assertIn("^GSPC", [i["symbol"] for i in doc["instruments"]])

    def test_second_snapshot_is_one_yahoo_call(self):
        self.run_cli("snapshot")
        self.server.requests.clear()
        doc = self.run_cli("snapshot")
        yahoo_paths = [r[0] for r in self.server.requests if "finance" in r[0]]
        self.assertEqual(yahoo_paths, ["/v8/finance/spark"])
        self.assertEqual(doc["status_rows"], [])
        self.assertEqual([a["label"] for a in doc["attribution"]], ["Data by Yahoo Finance", "Data by CoinGecko"])


if __name__ == "__main__":
    unittest.main()

"""End-to-end: run bin/markets as a subprocess, exactly as QML does."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

import _paths  # noqa: F401
from _paths import ROOT
from fakeserver import FakeServer, _Everything, coingecko_routes, yahoo_routes

BIN = os.path.join(ROOT, "bin", "markets")


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = yahoo_routes(coingecko_routes(FakeServer().start()))
        self.env = dict(os.environ)
        self.env.update({
            "MARKETS_STATE_DIR": self.tmp.name,
            "MARKETS_COINGECKO_URL": self.server.base_url,
            "MARKETS_YAHOO_URL": self.server.base_url,
            "MARKETS_BACKOFF_SCALE": "0",
            "MARKETS_SOCKET_TIMEOUT": "5",
            "MARKETS_TOTAL_TIMEOUT": "5",
        })

    def tearDown(self):
        self.server.stop()
        self.tmp.cleanup()

    def run_cli(self, *args, env=None):
        proc = subprocess.run([sys.executable, BIN, *args], capture_output=True, text=True, env=env or self.env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        lines = proc.stdout.splitlines()
        self.assertEqual(len(lines), 1, proc.stdout)
        doc = json.loads(lines[0])
        self.assertEqual(doc["schema_version"], 1)
        return doc

    def test_garbage_arguments_still_exit_zero_with_json(self):
        doc = self.run_cli("bogus", "--nope")
        self.assertFalse(doc["ok"])
        self.assertEqual(doc["error"]["code"], "bad_args")
        self.assertEqual(doc["command"], "bogus")

    def test_no_arguments_prints_usage_in_the_error(self):
        doc = self.run_cli()
        self.assertEqual(doc["error"]["code"], "bad_args")
        self.assertIn("snapshot", doc["error"]["message"])

    def test_bad_settings_json_is_bad_args(self):
        doc = self.run_cli("--settings", "{nope", "status")
        self.assertEqual(doc["error"]["code"], "bad_args")

    def test_internal_failures_are_reported_not_raised(self):
        blocker = os.path.join(self.tmp.name, "file-not-dir")
        with open(blocker, "w") as f:
            f.write("x")
        env = dict(self.env, MARKETS_STATE_DIR=blocker)
        doc = self.run_cli("status", env=env)
        self.assertFalse(doc["ok"])
        self.assertEqual(doc["error"]["code"], "internal")

    def test_snapshot_end_to_end(self):
        doc = self.run_cli("snapshot")
        self.assertTrue(doc["ok"])
        self.assertIsNone(doc["error"])
        self.assertEqual([e["label"] for e in doc["strip"]], ["BTC", "ETH", "SOL"])
        self.assertEqual(doc["quotes"]["BTC"]["price_text"], "$77,356.00")
        self.assertEqual(doc["quotes"]["AAPL"]["price_text"], "$324.96")
        self.assertEqual(doc["quotes"]["EURUSD"]["price_text"], "1.1592")
        self.assertTrue(doc["quotes"]["MSFT"]["valid"])
        self.assertEqual(len(doc["instruments"]), 9)
        self.assertEqual([a["label"] for a in doc["attribution"]], ["Data by Yahoo Finance", "Data by CoinGecko"])
        self.assertEqual(doc["status_rows"], [])
        self.assertFalse(doc["cached"])

    def test_snapshot_max_age_reuses_the_cache(self):
        self.run_cli("snapshot")
        doc = self.run_cli("snapshot", "--max-age", "60")
        self.assertTrue(doc["cached"])
        self.assertEqual(len(self.server.hits("/coins/markets")), 1)

    def test_quotes_keep_argument_order(self):
        doc = self.run_cli("quotes", "SOL", "BTC")
        self.assertEqual([q["symbol"] for q in doc["quotes"]], ["SOL", "BTC"])
        self.assertTrue(all(q["valid"] for q in doc["quotes"]))

    def test_search_and_candles(self):
        doc = self.run_cli("search", "sol")
        self.assertEqual(doc["results"][0]["symbol"], "SOL")
        doc = self.run_cli("candles", "BTC", "1m")
        self.assertTrue(doc["series"]["valid"])
        self.assertEqual(doc["series"]["n"], 49)
        self.assertTrue(self.run_cli("candles", "BTC", "1M")["cached"])
        self.assertEqual(self.run_cli("candles", "BTC", "9Y")["error"]["code"], "bad_args")

    def test_membership_round_trip(self):
        doc = self.run_cli("watchlist", "add", "DOGE", "crypto", "Dogecoin")
        self.assertEqual(len(doc["instruments"]), 10)
        doc = self.run_cli("favorite", "add", "doge")
        self.assertIn("DOGE", [e["label"] for e in doc["strip"]])
        doc = self.run_cli("watchlist", "remove", "DOGE")
        self.assertIn("DOGE", doc["favorites"])
        doc = self.run_cli("favorite", "remove", "DOGE")
        self.assertEqual(len(doc["instruments"]), 9)
        self.assertEqual(self.run_cli("watchlist", "add", "NEW")["error"]["code"], "bad_args")

    def test_membership_accepts_the_colon_spelling_and_learns_the_name(self):
        # TSLA reuses the AAPL fixture, so the learned name is Apple's: the
        # point is that a nameless add prices the symbol once and keeps the
        # provider's name instead of the bare ticker.
        self.server.handler("/v8/finance/chart/TSLA", self.server.routes["/v8/finance/chart/AAPL"])
        doc = self.run_cli("favorite", "add", "TSLA:stock")
        self.assertTrue(doc["ok"], doc["error"])
        tsla = [i for i in doc["instruments"] if i["symbol"] == "TSLA"][0]
        self.assertEqual(tsla["name"], "Apple Inc.")
        self.assertTrue(tsla["is_favorite"] and not tsla["in_watchlist"])
        self.assertIn("TSLA", [e["label"] for e in doc["strip"]])
        self.assertEqual(self.run_cli("favorite", "add", "NEWCOIN")["error"]["code"], "bad_args")
        doc = self.run_cli("watchlist", "add", "DOGE:crypto")
        doge = [i for i in doc["instruments"] if i["symbol"] == "DOGE"][0]
        self.assertEqual(doge["name"], "DOGE")  # not in the fixture: unpriced, still added

    def test_snapshot_extra_refreshes_only_the_stale_symbol(self):
        self.run_cli("snapshot")
        spark_hits = len(self.server.hits("/v8/finance/spark"))
        doc = self.run_cli("snapshot", "--max-age", "60", "--extra", "DOGE:crypto")
        self.assertFalse(doc["cached"])
        self.assertIn("DOGE", doc["quotes"])
        self.assertFalse(doc["quotes"]["DOGE"]["valid"])
        self.assertTrue(doc["quotes"]["AAPL"]["valid"])
        self.assertEqual(len(doc["instruments"]), 9)
        self.assertEqual(len(self.server.hits("/coins/markets")), 2)
        self.assertEqual(len(self.server.hits("/v8/finance/spark")), spark_hits)
        self.assertEqual(len([h for h in self.server.hits() if h[0].startswith("/v8/finance/chart/")]), 6)
        doc = self.run_cli("snapshot", "--max-age", "60", "--extra", "DOGE:crypto")
        self.assertTrue(doc["cached"])
        self.assertEqual(len(self.server.hits("/coins/markets")), 2)

    def test_candles_document_has_the_chart_strings_and_survives_the_cache(self):
        doc = self.run_cli("candles", "AAPL", "1D")
        series = doc["series"]
        self.assertTrue(series["valid"])
        for key in ("min_text", "max_text", "first_label", "last_label", "previous_close_text", "range_change_text"):
            self.assertNotEqual(series[key], "", key)
        self.assertEqual(series["previous_close"], 325.13)
        again = self.run_cli("candles", "AAPL", "1D")
        self.assertTrue(again["cached"])
        self.assertEqual(again["series"], series)
        rate = self.run_cli("candles", "EURUSD", "1M")["series"]
        self.assertEqual(rate["category"], "currency")
        self.assertNotIn("$", rate["min_text"])

    def test_rate_limit_keeps_prices_and_latches_until_a_fetch_succeeds(self):
        good = self.run_cli("snapshot")
        self.assertTrue(good["quotes"]["BTC"]["valid"])
        throttled = FakeServer()
        throttled.routes = _Everything(lambda q, p: (429, {"Retry-After": "60"}, b"{}"))
        throttled.start()
        try:
            env = dict(self.env, MARKETS_COINGECKO_URL=throttled.base_url, MARKETS_YAHOO_URL=throttled.base_url)
            doc = self.run_cli("snapshot", "--max-age", "0", env=env)
        finally:
            throttled.stop()
        self.assertFalse(doc["ok"])
        self.assertTrue(doc["rate_limited"])
        self.assertEqual(doc["error"]["code"], "rate_limited")
        self.assertTrue(doc["quotes"]["BTC"]["valid"])  # last good, kept
        self.assertTrue(doc["quotes"]["BTC"]["stale"])
        self.assertEqual([r["kind"] for r in doc["status_rows"]], ["rate_limited"])
        self.assertTrue([e for e in doc["strip"] if e["stale"]])
        # A cache read makes no request, so the latch still reports.
        cached = self.run_cli("snapshot", "--max-age", "600")
        self.assertTrue(cached["cached"])
        self.assertTrue(cached["rate_limited"])
        self.assertTrue(cached["ok"])
        # Hidden when the user turned the notice off; the flag still rides.
        quiet = self.run_cli("--settings", '{"showRateLimitErrors": false}', "snapshot", "--max-age", "600")
        self.assertTrue(quiet["rate_limited"])
        self.assertEqual(quiet["status_rows"], [])
        # A successful fetch clears it.
        fresh = self.run_cli("snapshot", "--max-age", "0")
        self.assertFalse(fresh["rate_limited"])
        self.assertFalse(fresh["quotes"]["BTC"]["stale"])

    def test_settings_shape_the_strip(self):
        doc = self.run_cli("--settings", '{"strip":"watchlist","stripMax":2}', "snapshot")
        self.assertEqual([e["label"] for e in doc["strip"]], ["AAPL", "MSFT"])

    def test_provider_outage_keeps_last_good_prices(self):
        self.run_cli("snapshot")
        self.server.route("/coins/markets", status=500, body=b"{}")
        doc = self.run_cli("snapshot")
        self.assertFalse(doc["ok"])
        self.assertEqual(doc["error"]["code"], "http")
        self.assertEqual(doc["error"]["provider"], "coingecko")
        btc = doc["quotes"]["BTC"]
        self.assertTrue(btc["valid"])
        self.assertTrue(btc["stale"])
        self.assertEqual(btc["price_text"], "$77,356.00")
        self.assertTrue(all(e["stale"] for e in doc["strip"]))

    def test_rate_limit_is_flagged_in_the_envelope(self):
        self.server.route("/coins/markets", status=429, body=b"{}")
        doc = self.run_cli("snapshot")
        self.assertTrue(doc["rate_limited"])
        self.assertEqual(doc["error"]["code"], "rate_limited")
        self.assertIn("rate_limited", [r["kind"] for r in doc["status_rows"]])

    def test_status_reports_providers_and_state_dir(self):
        doc = self.run_cli("status")
        self.assertEqual(doc["state_dir"], self.tmp.name)
        self.assertEqual([p["id"] for p in doc["providers"]], ["yahoo", "coingecko"])
        self.assertEqual(doc["providers"][0]["supports"], ["stock", "currency"])
        self.assertEqual(doc["tracked"], 9)


if __name__ == "__main__":
    unittest.main()

"""Holdings priced and rolled up (UiPosition/UiPortfolio) and the Frankfurter rates."""

import json
import os
import tempfile
import unittest

import _paths  # noqa: F401
from fakeserver import FakeServer, frankfurter_routes
from marketslib import http, portfolio
from marketslib.models import Instrument, Quote
from marketslib.providers.frankfurter import Frankfurter
from marketslib.store import Portfolio


def holding(symbol, qty, basis=None, category="stock", name=None):
    return {"symbol": symbol, "name": name or symbol, "category": category, "quantity": qty, "cost_basis": basis}


class Positions(unittest.TestCase):
    def test_same_currency_shows_native_only_and_counts(self):
        q = Quote("AAPL", "Apple", "stock", price=150.0, change=1.5, change_pct=1.0965, currency="USD")
        row = portfolio.position_row(holding("AAPL", 10), q, "USD", 1.0)
        self.assertEqual(row["holding_text"], "AAPL · 10 sh")
        self.assertEqual(row["value_text"], "$1,500.00")
        self.assertEqual(row["daily_text"], "▲ +$15.00 (+1.10%)")
        self.assertEqual(row["return_text"], "")
        self.assertTrue(row["counts"])
        self.assertEqual(row["dir"], "up")

    def test_foreign_holding_shows_the_converted_value_and_pnl_in_the_preferred_currency(self):
        q = Quote("HSBA.L", "HSBC", "stock", price=7.5, change=-0.1, change_pct=-1.3158, currency="GBP")
        row = portfolio.position_row(holding("HSBA.L", 10), q, "USD", 1.3483)
        self.assertEqual(row["value_text"], "£75.00 (≈$101.12)")
        self.assertEqual(row["daily_text"], "▼ -$1.35 (-1.32%)")
        self.assertTrue(row["converted"])

    def test_foreign_holding_without_a_rate_stays_native_and_is_not_counted(self):
        q = Quote("HSBA.L", "HSBC", "stock", price=7.5, change=-0.1, change_pct=-1.3, currency="GBP")
        row = portfolio.position_row(holding("HSBA.L", 10), q, "USD", None)
        self.assertEqual(row["value_text"], "£75.00")
        self.assertEqual(row["daily_text"], "▼ -£1.00 (-1.30%)")
        self.assertFalse(row["counts"])
        self.assertTrue(row["valid"])

    def test_invalid_quote_is_a_dash_with_nothing_else(self):
        row = portfolio.position_row(holding("ZZZZ", 3), Quote.invalid(Instrument("ZZZZ", "", "stock")), "USD", None)
        self.assertEqual((row["value_text"], row["daily_text"], row["return_text"]), ("—", "", ""))
        self.assertFalse(row["counts"])
        none = portfolio.position_row(holding("ZZZZ", 3), None, "USD", None)
        self.assertEqual(none["price_text"], "—")

    def test_cost_basis_gives_total_return_per_unit(self):
        q = Quote("BTC", "Bitcoin", "crypto", price=60000.0, change=600.0, change_pct=1.01, currency="USD")
        row = portfolio.position_row(holding("BTC", 0.5, 30000, category="crypto"), q, "USD", 1.0)
        self.assertEqual(row["holding_text"], "BTC · 0.5 units")
        self.assertEqual(row["amount_text"], "0.5 units")
        self.assertEqual(row["return_text"], "▲ +$15,000.00 (+100.00%)")
        self.assertEqual(row["return_dir"], "up")
        self.assertEqual((row["cost_basis_text"], row["cost_text"]), ("30000", "$30,000.00"))
        loss = portfolio.position_row(holding("BTC", 0.5, 70000, category="crypto"), q, "USD", 1.0)
        self.assertEqual(loss["return_text"], "▼ -$5,000.00 (-14.29%)")
        self.assertEqual(loss["return_dir"], "down")
        zero = portfolio.position_row(holding("BTC", 0.5, 0, category="crypto"), q, "USD", 1.0)
        self.assertEqual(zero["return_text"], "")
        self.assertIsNone(zero["cost_basis"])


class Totals(unittest.TestCase):
    def rows(self):
        usd = Quote("AAPL", "Apple", "stock", price=100.0, change=2.0, change_pct=2.0408, currency="USD")
        gbp = Quote("HSBA.L", "HSBC", "stock", price=10.0, change=-1.0, change_pct=-9.0909, currency="GBP")
        chf = Quote("NESN.SW", "Nestle", "stock", price=80.0, change=0.0, change_pct=0.0, currency="CHF")
        return [
            portfolio.position_row(holding("AAPL", 10, 50), usd, "USD", 1.0),        # $1,000, +$20, cost $500
            portfolio.position_row(holding("HSBA.L", 10), gbp, "USD", 1.25),          # £100 → $125, −$12.50
            portfolio.position_row(holding("NESN.SW", 1), chf, "USD", None),          # priced, not convertible
            portfolio.position_row(holding("ZZZZ", 1), None, "USD", None),            # not priced
        ]

    def test_totals_count_only_priced_convertible_rows(self):
        t = portfolio.totals(self.rows(), "USD")
        self.assertEqual(t["value_text"], "$1,125.00")
        self.assertEqual(t["counted"], 2)
        self.assertEqual(t["unconverted"], 1)
        # today's move measured against yesterday's value: 7.5 / (1125 − 7.5)
        self.assertEqual(t["change_text"], "▲ +$7.50 (+0.67%) today")
        self.assertEqual(t["return_note"], " · Total ▲ +$500.00 (+100.00%)")
        self.assertEqual(t["unconverted_note"], " · 1 holding not converted")
        self.assertEqual(t["change_compact_text"], "▲ +0.7%")
        self.assertEqual(t["value_compact_text"], "$1,125")
        self.assertEqual(t["dir"], "up")

    def test_empty_and_zero_guards(self):
        t = portfolio.totals([], "EUR")
        self.assertFalse(t["has_holdings"])
        self.assertEqual(t["value_text"], "€0.00")
        self.assertEqual(t["change_text"], "▲ +€0.00 (+0.00%) today")
        self.assertEqual((t["return_note"], t["unconverted_note"]), ("", ""))
        many = portfolio.totals(self.rows() + [self.rows()[2]], "USD")
        self.assertEqual(many["unconverted_note"], " · 2 holdings not converted")

    def test_stale_follows_the_counted_quotes(self):
        q = Quote("AAPL", "Apple", "stock", price=100.0, change=2.0, change_pct=2.0, currency="USD", stale=True)
        t = portfolio.totals([portfolio.position_row(holding("AAPL", 1), q, "USD", 1.0)], "USD")
        self.assertTrue(t["stale"])


class PortfolioStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "portfolio.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_starts_empty_and_writes_nothing_until_a_holding_is_set(self):
        pf = Portfolio(self.path)
        self.assertEqual(pf.positions(), [])
        self.assertFalse(os.path.exists(self.path))

    def test_set_keeps_insertion_order_and_replaces_in_place(self):
        pf = Portfolio(self.path)
        pf.set(Instrument("BTC", "Bitcoin", "crypto", {"coingecko": "bitcoin"}), 0.5, 30000)
        pf.set(Instrument("AAPL", "Apple", "stock"), 10)
        pf.set(Instrument("BTC", "Bitcoin", "crypto"), 0.75)  # quantity-only edit clears the basis (editor prefills it)
        again = Portfolio(self.path)
        self.assertEqual([p["symbol"] for p in again.positions()], ["BTC", "AAPL"])
        btc = again.position("btc")
        self.assertEqual((btc["quantity"], btc["cost_basis"]), (0.75, None))
        self.assertEqual(btc["provider_ids"], {"coingecko": "bitcoin"})
        self.assertTrue(again.contains("AAPL"))
        self.assertEqual(again.instrument("AAPL").category, "stock")

    def test_remove(self):
        pf = Portfolio(self.path)
        pf.set(Instrument("AAPL", "Apple", "stock"), 10)
        self.assertIsNotNone(pf.remove("aapl"))
        self.assertIsNone(pf.remove("AAPL"))
        self.assertEqual(Portfolio(self.path).positions(), [])

    def test_corrupt_file_is_set_aside_and_the_portfolio_starts_empty(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        pf = Portfolio(self.path)
        self.assertIsNotNone(pf.recovered_from)
        self.assertTrue(os.path.exists(pf.recovered_from))
        self.assertEqual(pf.positions(), [])

    def test_bad_entries_are_dropped_on_load(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump([{"symbol": "AAPL", "category": "stock", "quantity": 0},
                       {"symbol": "MSFT", "category": "stock", "quantity": "ten"},
                       {"symbol": "NVDA", "category": "stock", "quantity": 2, "cost_basis": -1},
                       "junk"], f)
        pf = Portfolio(self.path)
        self.assertEqual([p["symbol"] for p in pf.positions()], ["NVDA"])
        self.assertIsNone(pf.position("NVDA")["cost_basis"])


class FrankfurterRates(unittest.TestCase):
    def setUp(self):
        self.server = frankfurter_routes(FakeServer().start())
        self.cache = {}
        self.fx = Frankfurter(base_url=self.server.base_url, cache=self.cache)
        http.RATE_LIMITED = False
        http.SUCCEEDED = False

    def tearDown(self):
        self.server.stop()
        http.RATE_LIMITED = False
        http.SUCCEEDED = False

    def test_one_request_inverts_the_rates_and_caches_them(self):
        rates = self.fx.rates_to("USD", ["GBP", "EUR", "USD", "gbp"], now=1000)
        self.assertEqual(len(self.server.hits("/v1/latest")), 1)
        self.assertEqual(self.server.hits("/v1/latest")[0][1], {"base": "USD", "symbols": "GBP,EUR"})
        self.assertAlmostEqual(rates["GBP"], 1 / 0.74167, places=4)
        self.assertAlmostEqual(rates["EUR"], 1 / 0.86371, places=4)
        self.assertEqual(rates["USD"], 1.0)
        self.assertTrue(self.fx.served and self.fx.dirty)
        again = self.fx.rates_to("USD", ["GBP"], now=1000 + 3000)
        self.assertEqual(len(self.server.hits("/v1/latest")), 1)
        self.assertAlmostEqual(again["GBP"], 1 / 0.74167, places=4)
        self.assertAlmostEqual(self.fx.rate("gbp", "usd", now=2000), 1 / 0.74167, places=4)
        self.assertEqual(self.fx.rate("USD", "USD", now=2000), 1.0)

    def test_the_hour_expires(self):
        self.fx.rates_to("USD", ["GBP"], now=1000)
        self.fx.rates_to("USD", ["GBP"], now=1000 + 3600)
        self.assertEqual(len(self.server.hits("/v1/latest")), 2)
        self.assertIsNone(self.fx.rate("GBP", "USD", now=1000 + 7300))

    def test_unsupported_currencies_are_cached_as_not_convertible(self):
        rates = self.fx.rates_to("USD", ["GBP", "XXX"], now=1000)
        self.assertIsNone(rates["XXX"])
        self.assertIsNotNone(rates["GBP"])
        only = self.fx.rates_to("USD", ["YYY"], now=1000)  # 404: nothing known
        self.assertEqual(only, {"YYY": None})
        self.assertEqual(len(self.server.hits("/v1/latest")), 2)
        self.fx.rates_to("USD", ["XXX", "YYY"], now=1500)
        self.assertEqual(len(self.server.hits("/v1/latest")), 2)  # remembered, not retried
        self.assertIsNone(self.fx.error)

    def test_a_failure_caches_nothing_so_the_next_run_retries(self):
        self.server.route("/v1/latest", status=500, body=b"boom")
        rates = self.fx.rates_to("USD", ["GBP"], now=1000)
        self.assertEqual(rates, {"GBP": None})
        self.assertIsNotNone(self.fx.error)
        self.assertEqual(self.cache, {})
        frankfurter_routes(self.server)
        self.assertIsNotNone(self.fx.rates_to("USD", ["GBP"], now=1001)["GBP"])

    def test_other_bases_work_and_demo_needs_no_network(self):
        rates = self.fx.rates_to("EUR", ["USD", "GBP"], now=1000)
        self.assertAlmostEqual(rates["USD"], 0.86371, places=4)
        self.assertAlmostEqual(rates["GBP"], 0.86371 / 0.74167, places=3)  # euros per pound
        demo = Frankfurter(base_url=self.server.base_url, cache={}, demo=True)
        d = demo.rates_to("EUR", ["GBP", "XXX"], now=1000)
        self.assertAlmostEqual(d["GBP"], 1.27 / 1.08, places=4)
        self.assertIsNone(d["XXX"])
        self.assertEqual(len(self.server.hits("/v1/latest")), 1)
        self.assertFalse(demo.served)


if __name__ == "__main__":
    unittest.main()

import unittest

import _paths  # noqa: F401
from marketslib import fmt
from marketslib.models import Quote


class MoneyFormatting(unittest.TestCase):
    def test_known_symbols_prefix_the_amount(self):
        self.assertEqual(fmt.money(1234.5, "USD"), "$1,234.50")
        self.assertEqual(fmt.money(1234.5, "eur"), "€1,234.50")
        self.assertEqual(fmt.money(5, "CHF"), "CHF 5.00")

    def test_yen_and_won_have_no_decimals(self):
        self.assertEqual(fmt.money(1234.5, "JPY"), "¥1,235")
        self.assertEqual(fmt.money(1000, "KRW"), "₩1,000")

    def test_unknown_code_trails_the_amount(self):
        self.assertEqual(fmt.money(5, "SGD"), "S$5.00")
        self.assertEqual(fmt.money(5, "XYZ"), "5.00 XYZ")

    def test_rounds_half_up_like_dotnet(self):
        self.assertEqual(fmt.money(12.345, "USD"), "$12.35")
        self.assertEqual(fmt.money(2.675, "USD"), "$2.68")

    def test_signed_puts_the_sign_before_the_symbol(self):
        self.assertEqual(fmt.money_signed(12.05, "USD"), "+$12.05")
        self.assertEqual(fmt.money_signed(-8.4, "USD"), "-$8.40")

    def test_compact_drops_precision_the_bar_cannot_show(self):
        self.assertEqual(fmt.money_compact(77356.4, "USD"), "$77,356")
        self.assertEqual(fmt.money_compact(99.636, "USD"), "$99.64")
        self.assertEqual(fmt.money_compact(0.12345, "USD"), "$0.1235")
        self.assertEqual(fmt.money_compact(1234, "JPY"), "¥1,234")


class CurrencyNormalization(unittest.TestCase):
    def test_pence_become_pounds(self):
        self.assertEqual(fmt.normalize_stock_quote("GBp", 12345, 100), ("GBP", 123.45, 1.0))
        self.assertEqual(fmt.normalize_stock_quote("gbx", 200, 2), ("GBP", 2.0, 0.02))

    def test_missing_currency_defaults_to_usd(self):
        self.assertEqual(fmt.normalize_stock_quote("", 10, 1), ("USD", 10, 1))
        self.assertEqual(fmt.normalize_stock_quote(None, 10, 1), ("USD", 10, 1))
        self.assertEqual(fmt.normalize_code(" eur "), "EUR")

    def test_quote_currency_of_pair(self):
        self.assertEqual(fmt.quote_currency_of_pair("EURUSD"), "USD")
        self.assertEqual(fmt.quote_currency_of_pair("USDJPY"), "JPY")
        self.assertEqual(fmt.quote_currency_of_pair("BTC"), "USD")


class QuoteText(unittest.TestCase):
    def test_fx_rates_show_four_decimals_and_no_symbol(self):
        self.assertEqual(fmt.price_text(1.08423, "USD", "currency"), "1.0842")

    def test_invalid_quote_is_a_dash_with_no_change(self):
        self.assertEqual(fmt.price_text(1, "USD", "crypto", valid=False), "—")
        self.assertEqual(fmt.change_text(1, 1, valid=False), "")

    def test_change_text_arrows(self):
        self.assertEqual(fmt.change_text(1.0, 1.2), "▲ +1.20%")
        self.assertEqual(fmt.change_text(-1.0, -0.8), "▼ -0.80%")

    def test_flat_counts_as_up(self):
        self.assertEqual(fmt.change_text(0.0, 0.0), "▲ +0.00%")
        self.assertEqual(fmt.direction(0.0), "flat")
        self.assertEqual(fmt.direction(-0.001), "down")

    def test_range_change_measures_the_whole_range(self):
        self.assertEqual(fmt.range_change_text(100.0, 103.2, "1M"), "▲ +$3.20 (+3.20%) · 1M")
        self.assertEqual(fmt.range_change_text(100.0, 90.0, "1W"), "▼ -$10.00 (-10.00%) · 1W")
        self.assertEqual(fmt.range_change_text(0.0, 5.0, "1D"), "▲ +$5.00 (+0.00%) · 1D")

    def test_strip_value_text(self):
        q = Quote("BTC", "Bitcoin", "crypto", price=77356.4, change=16.4, change_pct=0.02132)
        self.assertEqual(fmt.strip_value_text(q), "$77,356 ▲ +0.0%")
        self.assertEqual(fmt.strip_value_text(q, show_price=False), "▲ +0.0%")
        fx = Quote("EURUSD", "", "currency", price=1.08423, change=-0.001, change_pct=-0.09)
        self.assertEqual(fmt.strip_value_text(fx), "1.0842 ▼ -0.1%")
        self.assertEqual(fmt.strip_value_text(Quote("X", "", "stock", valid=False)), "—")


class QuoteDocument(unittest.TestCase):
    def test_to_dict_stamps_display_fields(self):
        d = Quote("ETH", "Ethereum", "crypto", price=2391.22, change=-27.58, change_pct=-1.12675).to_dict()
        self.assertEqual(d["price_text"], "$2,391.22")
        self.assertEqual(d["change_text"], "▼ -1.13%")
        self.assertEqual(d["dir"], "down")

    def test_invalid_to_dict_has_null_numbers(self):
        d = Quote("AAPL", "Apple", "stock", valid=False).to_dict()
        self.assertIsNone(d["price"])
        self.assertEqual(d["price_text"], "—")
        self.assertEqual(d["dir"], "flat")


if __name__ == "__main__":
    unittest.main()

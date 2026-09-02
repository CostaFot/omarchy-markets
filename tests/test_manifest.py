"""manifest.json is the only place the settings are typed and bounded (the
shell does not render its schema; the Settings page and the helper repeat
the lists by hand). These tests tie the three copies together so a drift
between them fails here, not in a user's bar."""

import json
import os
import re
import tempfile
import unittest

import _paths  # noqa: F401
from _paths import ROOT
from marketslib import fmt, repo
from marketslib.models import Instrument
from marketslib.repo import Repository, Settings
from test_repo import FakeCrypto

with open(os.path.join(ROOT, "manifest.json"), encoding="utf-8") as f:
    MANIFEST = json.load(f)
WIDGET = MANIFEST["barWidget"]
SCHEMA = {e["key"]: e for e in WIDGET["schema"]}
# Store.qml's helperSettingKeys: what the panel sends the helper.
HELPER_KEYS = ["strip", "stripShowPrice", "stripMax", "portfolioCurrency", "showRateLimitErrors"]


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


class ManifestMatchesTheHelper(unittest.TestCase):
    def test_defaults_are_the_helpers_defaults(self):
        for key, value in repo.SETTING_DEFAULTS.items():
            self.assertEqual(WIDGET["defaults"][key], value, key)
            self.assertEqual(SCHEMA[key]["defaultValue"], value, key)
        self.assertEqual(list(WIDGET["defaults"]), [e["key"] for e in WIDGET["schema"]])
        self.assertEqual(WIDGET["defaults"]["refreshMinutes"], 10)

    def test_schema_covers_every_key_the_store_sends(self):
        self.assertTrue(set(HELPER_KEYS) <= set(SCHEMA), set(HELPER_KEYS) - set(SCHEMA))
        self.assertEqual(set(HELPER_KEYS), set(repo.SETTING_DEFAULTS))
        self.assertEqual(set(SCHEMA) - set(HELPER_KEYS), {"refreshMinutes"})

    def test_currency_options_are_the_codes_fmt_prints(self):
        options = SCHEMA["portfolioCurrency"]["options"]
        self.assertEqual(set(options), set(fmt.SYMBOLS))
        self.assertEqual(len(options), len(set(options)))
        self.assertEqual(options[0], "USD")

    def test_strip_options_are_the_modes_the_helper_builds(self):
        expected = {
            "favorites": ["BTC", "ETH", "SOL"],
            "watchlist": ["AAPL", "MSFT", "NVDA", "BTC", "ETH", "SOL", "EURUSD", "GBPUSD", "USDJPY"],
            "portfolio": ["PORTFOLIO"],
            "favorites+portfolio": ["PORTFOLIO", "BTC", "ETH", "SOL"],
        }
        self.assertEqual(list(expected), SCHEMA["strip"]["options"])
        with tempfile.TemporaryDirectory() as tmp:
            for mode, symbols in expected.items():
                r = Repository(Settings({"strip": mode, "stripMax": 12}), directory=tmp)
                r.providers = [FakeCrypto()]
                r.portfolio.set(Instrument("BTC", "Bitcoin", "crypto"), 1)
                doc = r.snapshot(now=1000, max_age=0)
                self.assertEqual([e["symbol"] for e in doc["strip"]], symbols, mode)

    def test_integer_bounds_match_the_page(self):
        self.assertEqual((SCHEMA["stripMax"]["min"], SCHEMA["stripMax"]["max"]), (1, 12))
        self.assertEqual((SCHEMA["refreshMinutes"]["min"], SCHEMA["refreshMinutes"]["max"]), (0, 120))
        for key in ("stripMax", "refreshMinutes"):
            self.assertEqual(SCHEMA[key]["type"], "integer")
        for key in ("stripShowPrice", "showRateLimitErrors"):
            self.assertEqual(SCHEMA[key]["type"], "boolean")
        for key in ("strip", "portfolioCurrency"):
            self.assertEqual(SCHEMA[key]["type"], "enum")


class ManifestMatchesThePanel(unittest.TestCase):
    """Panel.qml repeats the defaults, the options and the provider links."""

    def setUp(self):
        self.panel = read("Panel.qml")

    def test_panel_defaults_are_the_manifest_defaults(self):
        m = re.search(r"settingsDefaults:\s*\(\{(.*?)\}\)", self.panel, re.S)
        self.assertIsNotNone(m, "Panel.qml has no settingsDefaults literal")
        literal = "{" + re.sub(r"(\w+)\s*:", r'"\1":', m.group(1)) + "}"
        self.assertEqual(json.loads(literal), WIDGET["defaults"])

    def test_panel_offers_every_manifest_option(self):
        for code in SCHEMA["portfolioCurrency"]["options"]:
            self.assertIn(f'value: "{code}"', self.panel, code)
        for mode in SCHEMA["strip"]["options"]:
            self.assertIn(f'value: "{mode}"', self.panel, mode)
        for lo, hi in ((SCHEMA["stripMax"]["min"], SCHEMA["stripMax"]["max"]),
                       (SCHEMA["refreshMinutes"]["min"], SCHEMA["refreshMinutes"]["max"])):
            self.assertIn(f"from: {lo}; to: {hi}", self.panel)

    def test_panel_credits_every_provider_by_its_own_url(self):
        from marketslib.providers.coingecko import CoinGecko
        from marketslib.providers.frankfurter import Frankfurter
        from marketslib.providers.yahoo import Yahoo
        for provider in (Yahoo, CoinGecko, Frankfurter):
            self.assertIn(provider.attribution["url"], self.panel, provider.id)
            self.assertIn(provider.attribution["label"], self.panel, provider.id)


if __name__ == "__main__":
    unittest.main()

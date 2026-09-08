"""manifest.json is the only place the settings are typed and bounded (the
shell does not render its schema; the Settings page (Pages.qml) and the
helper repeat the lists by hand). These tests tie the three copies together
so a drift between them fails here, not in a user's bar, and pin the window
entry point to the manifest."""

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
# QML-only: the poll timer and which surface the one-key verbs open.
QML_ONLY_KEYS = {"refreshMinutes", "openAsWindow"}


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
        self.assertEqual(set(SCHEMA) - set(HELPER_KEYS), QML_ONLY_KEYS)
        store = read("Store.qml")
        m = re.search(r"helperSettingKeys:\s*\[(.*?)\]", store)
        self.assertEqual(re.findall(r'"(\w+)"', m.group(1)), HELPER_KEYS)

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
        for key in ("stripShowPrice", "showRateLimitErrors", "openAsWindow"):
            self.assertEqual(SCHEMA[key]["type"], "boolean")
        for key in ("strip", "portfolioCurrency"):
            self.assertEqual(SCHEMA[key]["type"], "enum")


class ManifestMatchesThePanel(unittest.TestCase):
    """Pages.qml (the content both hosts show) repeats the defaults, the
    options and the provider links."""

    def setUp(self):
        self.panel = read("Pages.qml")

    def test_panel_defaults_are_the_manifest_defaults(self):
        m = re.search(r"settingsDefaults:\s*\(\{(.*?)\}\)", self.panel, re.S)
        self.assertIsNotNone(m, "Pages.qml has no settingsDefaults literal")
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

    def test_open_as_window_is_wired(self):
        # The setting the manifest declares is on the form, in the save set,
        # and drives the bar glyph's clicks and the window root's verbs.
        self.assertIn("id: openAsWindowToggle", self.panel)
        self.assertIn("openAsWindow: pendingOpenAsWindow", self.panel)
        self.assertIn('action: "openwindow"', self.panel)
        bar = read("BarWidget.qml")
        self.assertIn('root.setting("openAsWindow", false)', bar)
        self.assertIn("(b === Qt.RightButton) === root.openAsWindow", bar)
        self.assertIn("openAsWindow ? openWindow", read("Window.qml"))


class TheWindowIsThePanelKind(unittest.TestCase):
    """The pages as a toplevel: the `panel` kind with Window.qml as its entry
    point, kept loaded and hidden until summoned. Panel.qml is the popup
    wrapper and Pages.qml the content, so no page exists twice."""

    def test_manifest_declares_the_window(self):
        self.assertEqual(MANIFEST["kinds"], ["bar-widget", "panel"])
        self.assertIs(MANIFEST["keepLoaded"], True)
        self.assertEqual(MANIFEST["entryPoints"], {"barWidget": "BarWidget.qml", "panel": "Window.qml"})
        for entry in MANIFEST["entryPoints"].values():
            self.assertTrue(os.path.exists(os.path.join(ROOT, entry)), entry)

    def test_the_window_starts_hidden_and_holds_the_ipc_target(self):
        window = read("Window.qml")
        self.assertIn("FloatingWindow {", window)
        self.assertIn('title: "Markets"', window)
        self.assertIn("visible: false", window)  # keepLoaded would show it at start
        self.assertIn("Pages {", window)
        self.assertEqual(window.count('target: "costafot.markets"'), 1)
        self.assertNotIn("IpcHandler", read("BarWidget.qml"))
        self.assertNotIn("IpcHandler", read("Panel.qml"))
        for verb in ("open", "show", "close", "hide", "toggle", "refresh", "status"):
            self.assertIn(f"function {verb}(): string", window, verb)
        for verb in ("page(name: string)", "window(mode: string)", "add(symbol: string, category: string)", "favorite(symbol: string)"):
            self.assertIn(f"function {verb}: string", window, verb)

    def test_the_pages_live_once(self):
        pages = read("Pages.qml")
        self.assertTrue(pages.startswith("pragma ComponentBehavior: Bound"))
        for fn in ("hubRows", "detailRows", "portfolioRows", "sourcesRows", "saveSettings", "saveHolding"):
            self.assertIn(f"function {fn}(", pages, fn)
            self.assertNotIn(f"function {fn}(", read("Panel.qml"), fn)
            self.assertNotIn(f"function {fn}(", read("Window.qml"), fn)
        self.assertIn("Pages {", read("Panel.qml"))
        # Nothing in the content reaches the bar: both hosts hand it what it needs.
        self.assertNotIn("bar.", pages)
        self.assertNotIn("switchPanel", pages)


class GlyphsSurviveEditing(unittest.TestCase):
    """Nerd Font glyphs are private-use characters; some editors and tools
    strip them and leave an empty string behind with no error. The strip's
    class glyph and pause mark were empty from 0.2.0 to 1.0.1 that way."""

    PUA = re.compile("[\ue000-\uf8ff]")

    def test_panel_keeps_its_literal_glyphs(self):
        self.assertEqual(len(self.PUA.findall(read("Pages.qml"))), 9)
        # The hosts and the newer rows use escapes; a literal here is a stray.
        for name in ("Panel.qml", "Window.qml", "BarWidget.qml"):
            self.assertEqual(self.PUA.findall(read(name)), [], name)
        self.assertIn('icon: "\\uf2d0", label: "Open as a window"', read("Pages.qml"))

    def test_bar_glyphs_are_escapes_and_not_empty(self):
        bar = read("BarWidget.qml")
        glyph = re.search(r'property string glyph: "([^"]*)"', bar)
        pause = re.search(r'property string pauseMark: "([^"]*)"', bar)
        self.assertEqual(glyph.group(1), "\\uf201")
        self.assertEqual(pause.group(1), " \\uf04c ")
        self.assertIn('advanceWidth(pauseMark)', bar)
        self.assertIn('text: pauseMark', bar)


if __name__ == "__main__":
    unittest.main()

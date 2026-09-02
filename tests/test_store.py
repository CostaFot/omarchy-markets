import json
import os
import stat
import tempfile
import unittest

import _paths  # noqa: F401
from marketslib.models import Instrument
from marketslib.store import SEED, Watchlist


class WatchlistStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "watchlist.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_first_run_seeds_nine_instruments_with_three_favorites(self):
        wl = Watchlist(self.path)
        self.assertEqual(len(wl.tracked()), len(SEED))
        self.assertEqual([i.symbol for i in wl.favorites()], ["BTC", "ETH", "SOL"])
        self.assertEqual(len(wl.watchlist()), 9)
        self.assertTrue(os.path.exists(self.path))
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_instruments_are_ordered_by_category_then_symbol(self):
        wl = Watchlist(self.path)
        self.assertEqual(
            [i.symbol for i in wl.tracked()],
            ["AAPL", "MSFT", "NVDA", "BTC", "ETH", "SOL", "EURUSD", "GBPUSD", "USDJPY"],
        )

    def test_seed_carries_coingecko_ids(self):
        wl = Watchlist(self.path)
        self.assertEqual(wl.instrument("BTC").provider_ids, {"coingecko": "bitcoin"})

    def test_symbols_are_normalized(self):
        wl = Watchlist(self.path)
        wl.add_to_watchlist(Instrument(" doge ", "Dogecoin", "crypto"))
        self.assertTrue(wl.flags("doge")[0])
        self.assertEqual(wl.instrument("DOGE").name, "Dogecoin")

    def test_entry_is_dropped_only_when_both_flags_are_false(self):
        wl = Watchlist(self.path)
        wl.remove_from_watchlist("BTC")
        self.assertEqual(wl.flags("BTC"), (False, True))
        wl.remove_favorite("BTC")
        self.assertIsNone(wl.entry("BTC"))
        self.assertEqual(len(wl.tracked()), 8)

    def test_changes_survive_a_reload(self):
        Watchlist(self.path).add_favorite(Instrument("DOGE", "Dogecoin", "crypto", {"coingecko": "dogecoin"}))
        wl = Watchlist(self.path)
        self.assertEqual(wl.flags("DOGE"), (False, True))
        self.assertEqual(wl.instrument("DOGE").provider_ids, {"coingecko": "dogecoin"})

    def test_removing_an_unknown_symbol_is_a_no_op(self):
        wl = Watchlist(self.path)
        self.assertIsNone(wl.remove_from_watchlist("NOPE"))
        self.assertEqual(len(wl.tracked()), 9)

    def test_corrupt_file_is_moved_aside_and_reseeded(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        wl = Watchlist(self.path)
        self.assertIsNotNone(wl.recovered_from)
        self.assertTrue(os.path.exists(wl.recovered_from))
        self.assertEqual(len(wl.tracked()), 9)

    def test_wrong_shape_is_treated_as_corrupt(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"symbol": "BTC"}, f)
        wl = Watchlist(self.path)
        self.assertIsNotNone(wl.recovered_from)
        self.assertEqual(len(wl.tracked()), 9)

    def test_merge_provider_ids_persists_only_changes(self):
        wl = Watchlist(self.path)
        self.assertFalse(wl.merge_provider_ids("BTC", {"coingecko": "bitcoin"}))
        self.assertTrue(wl.merge_provider_ids("AAPL", {"twelvedata": "AAPL"}))
        self.assertEqual(Watchlist(self.path).instrument("AAPL").provider_ids, {"twelvedata": "AAPL"})
        self.assertFalse(wl.merge_provider_ids("NOPE", {"x": "y"}))

    def test_writes_are_atomic_and_leave_no_temp_files(self):
        wl = Watchlist(self.path)
        wl.add_favorite(Instrument("DOGE", "Dogecoin", "crypto"))
        self.assertEqual(sorted(os.listdir(self.tmp.name)), ["watchlist.json"])

    def test_rows_expose_flags_for_the_panel(self):
        rows = Watchlist(self.path).rows()
        btc = next(r for r in rows if r["symbol"] == "BTC")
        self.assertEqual((btc["in_watchlist"], btc["is_favorite"], btc["in_portfolio"]), (True, True, False))


if __name__ == "__main__":
    unittest.main()

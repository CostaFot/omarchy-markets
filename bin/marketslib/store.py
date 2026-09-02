"""Watchlist + favorites on disk (port of Settings/WatchlistStore.cs).

One entry per tracked instrument with two flags; an entry whose flags are
both false is removed. First run seeds the Windows extension's nine
instruments, all in the watchlist. Favorites diverge from Windows on
purpose: BTC, ETH and SOL start starred so the bar strip has something
to show before the user has touched anything.
"""

import os
import time

from .models import CATEGORY_ORDER, Instrument, normalize
from .state import read_json, write_json_atomic

SEED = [
    Instrument("AAPL", "Apple Inc.", "stock"),
    Instrument("MSFT", "Microsoft Corp.", "stock"),
    Instrument("NVDA", "NVIDIA Corp.", "stock"),
    Instrument("BTC", "Bitcoin", "crypto", {"coingecko": "bitcoin"}),
    Instrument("ETH", "Ethereum", "crypto", {"coingecko": "ethereum"}),
    Instrument("SOL", "Solana", "crypto", {"coingecko": "solana"}),
    Instrument("EURUSD", "Euro / US Dollar", "currency"),
    Instrument("GBPUSD", "British Pound / US Dollar", "currency"),
    Instrument("USDJPY", "US Dollar / Japanese Yen", "currency"),
]
SEED_FAVORITES = ("BTC", "ETH", "SOL")


def _sort_key(entry):
    return (CATEGORY_ORDER.get(entry["category"], 99), entry["symbol"])


class Watchlist:
    def __init__(self, path):
        self.path = path
        self.recovered_from = None  # set when a corrupt file was moved aside
        self.entries = {}
        self._load()

    # ---- persistence -----------------------------------------------------
    def _load(self):
        try:
            data = read_json(self.path)
        except ValueError:
            data = self._quarantine()
        if data is None:
            self._seed()
            self.save()
            return
        if not isinstance(data, list):
            self._quarantine()
            self._seed()
            self.save()
            return
        for item in data:
            if not isinstance(item, dict) or not item.get("symbol"):
                continue
            inst = Instrument.from_dict(item)
            self.entries[inst.symbol] = {
                "symbol": inst.symbol,
                "name": inst.name,
                "category": inst.category,
                "in_watchlist": bool(item.get("in_watchlist", False)),
                "is_favorite": bool(item.get("is_favorite", False)),
                "provider_ids": inst.provider_ids,
            }

    def _quarantine(self):
        backup = f"{self.path}.bak.{int(time.time())}"
        try:
            os.replace(self.path, backup)
            self.recovered_from = backup
        except OSError:
            pass
        return None

    def _seed(self):
        self.entries = {}
        for inst in SEED:
            self.entries[inst.symbol] = {
                "symbol": inst.symbol,
                "name": inst.name,
                "category": inst.category,
                "in_watchlist": True,
                "is_favorite": inst.symbol in SEED_FAVORITES,
                "provider_ids": dict(inst.provider_ids),
            }

    def save(self):
        write_json_atomic(self.path, sorted(self.entries.values(), key=_sort_key))

    # ---- reads -----------------------------------------------------------
    def entry(self, symbol):
        return self.entries.get(normalize(symbol))

    def instrument(self, symbol):
        e = self.entry(symbol)
        return Instrument.from_dict(e) if e else None

    def _instruments(self, predicate):
        return [Instrument.from_dict(e) for e in sorted(self.entries.values(), key=_sort_key) if predicate(e)]

    def tracked(self):
        return self._instruments(lambda e: True)

    def watchlist(self):
        return self._instruments(lambda e: e["in_watchlist"])

    def favorites(self):
        return self._instruments(lambda e: e["is_favorite"])

    def flags(self, symbol):
        e = self.entry(symbol)
        if not e:
            return False, False
        return bool(e["in_watchlist"]), bool(e["is_favorite"])

    def rows(self):
        """Entries as the `instruments` section of a snapshot."""
        return [
            {
                "symbol": e["symbol"],
                "name": e["name"],
                "category": e["category"],
                "in_watchlist": bool(e["in_watchlist"]),
                "is_favorite": bool(e["is_favorite"]),
                "in_portfolio": False,
                "provider_ids": dict(e.get("provider_ids") or {}),
            }
            for e in sorted(self.entries.values(), key=_sort_key)
        ]

    # ---- writes ----------------------------------------------------------
    def set_flag(self, instrument, watchlist=None, favorite=None):
        """WatchlistStore.SetFlag: create the entry if an Instrument is given,
        apply the flags, drop the entry when neither remains. Returns the
        entry, or None when it was removed / never existed."""
        if isinstance(instrument, Instrument):
            key = normalize(instrument.symbol)
            e = self.entries.get(key)
            if e is None:
                e = self.entries[key] = {
                    "symbol": key,
                    "name": instrument.name or key,
                    "category": instrument.category,
                    "in_watchlist": False,
                    "is_favorite": False,
                    "provider_ids": dict(instrument.provider_ids),
                }
            elif instrument.provider_ids:
                e["provider_ids"].update(instrument.provider_ids)
        else:
            key = normalize(instrument)
            e = self.entries.get(key)
            if e is None:
                return None
        if watchlist is not None:
            e["in_watchlist"] = bool(watchlist)
        if favorite is not None:
            e["is_favorite"] = bool(favorite)
        if not e["in_watchlist"] and not e["is_favorite"]:
            del self.entries[key]
            e = None
        self.save()
        return e

    def add_to_watchlist(self, instrument):
        return self.set_flag(instrument, watchlist=True)

    def remove_from_watchlist(self, symbol):
        return self.set_flag(symbol, watchlist=False)

    def add_favorite(self, instrument):
        return self.set_flag(instrument, favorite=True)

    def remove_favorite(self, symbol):
        return self.set_flag(symbol, favorite=False)

    def merge_provider_ids(self, symbol, ids):
        """Remember a provider's id for a tracked symbol (learned from a quote or search)."""
        e = self.entry(symbol)
        if not e or not ids:
            return False
        changed = False
        for k, v in ids.items():
            if v and e["provider_ids"].get(k) != v:
                e["provider_ids"][k] = v
                changed = True
        if changed:
            self.save()
        return changed

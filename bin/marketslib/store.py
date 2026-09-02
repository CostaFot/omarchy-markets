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


class Portfolio:
    """Holdings on disk (port of Settings/PortfolioStore.cs): a quantity and an
    optional cost basis per instrument, in insertion order. Nothing is
    seeded — a fresh install holds nothing. A corrupt file is moved aside and
    the portfolio starts empty, reported once through `recovered_from`."""

    def __init__(self, path):
        self.path = path
        self.recovered_from = None
        self.entries = {}
        self._load()

    def _load(self):
        try:
            data = read_json(self.path)
        except ValueError:
            data = self._quarantine()
        if data is None:
            return
        if not isinstance(data, list):
            self._quarantine()
            return
        for item in data:
            if not isinstance(item, dict) or not item.get("symbol"):
                continue
            qty = _positive(item.get("quantity"))
            if qty is None:
                continue
            inst = Instrument.from_dict(item)
            self.entries[inst.symbol] = {
                "symbol": inst.symbol,
                "name": inst.name,
                "category": inst.category,
                "quantity": qty,
                "cost_basis": _positive(item.get("cost_basis")),
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

    def save(self):
        write_json_atomic(self.path, list(self.entries.values()))

    # ---- reads -----------------------------------------------------------
    def position(self, symbol):
        return self.entries.get(normalize(symbol))

    def contains(self, symbol):
        return normalize(symbol) in self.entries

    def positions(self):
        return list(self.entries.values())

    def instruments(self):
        return [Instrument.from_dict(e) for e in self.entries.values()]

    def instrument(self, symbol):
        e = self.position(symbol)
        return Instrument.from_dict(e) if e else None

    # ---- writes ----------------------------------------------------------
    def set(self, instrument, quantity, cost_basis=None):
        """Add or replace a holding. The cost basis is applied verbatim: a
        value sets it, None clears it (the editor prefills the current one,
        so a quantity-only edit round-trips it)."""
        key = normalize(instrument.symbol)
        current = self.entries.get(key) or {}
        ids = dict(current.get("provider_ids") or {})
        ids.update(instrument.provider_ids or {})
        entry = {
            "symbol": key,
            "name": instrument.name or current.get("name") or key,
            "category": instrument.category,
            "quantity": float(quantity),
            "cost_basis": float(cost_basis) if cost_basis is not None else None,
            "provider_ids": ids,
        }
        self.entries[key] = entry  # a replaced key keeps its insertion slot
        self.save()
        return entry

    def remove(self, symbol):
        e = self.entries.pop(normalize(symbol), None)
        if e is not None:
            self.save()
        return e

    def merge_provider_ids(self, symbol, ids):
        e = self.position(symbol)
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


def _positive(value):
    """A finite number above zero, else None (a holding with no quantity is no holding)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")) or f <= 0:
        return None
    return f

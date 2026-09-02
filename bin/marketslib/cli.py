"""`bin/markets` — the contract between the QML plugin and the data core.

    markets [--settings '<json>'] <command> [args...]

Every invocation prints exactly ONE line of JSON and exits 0, even when
it failed: errors ride inside the document under `error`, and a document
may carry both an error and last-good data. QML treats "has data" and
"has error" as independent facts. A helper that could exit non-zero or
print a traceback would be a helper that can blank the bar.

Envelope: schema_version, command, ok, error, generated_at, demo,
rate_limited, cached, attribution[], status_rows[], then the command's
own payload. See AGENTS.md for the per-command shapes.
"""

import json
import os
import sys
import time

from . import http, plugin_version
from .models import RANGES, is_category
from .models import Instrument
from .repo import Repository, Settings
from .state import state_dir

SCHEMA_VERSION = 1

USAGE = (
    "markets [--settings JSON] <command> [args]\n"
    "  status\n"
    "  snapshot [--max-age S] [--extra SYM[:CAT] ...]\n"
    "  quotes SYM[:CAT] ...\n"
    "  search QUERY\n"
    "  candles SYM[:CAT] 1D|1W|1M|1Y|5Y\n"
    "  watchlist add SYM[:CAT] [CAT] [NAME...] | watchlist remove SYM\n"
    "  favorite add SYM[:CAT] [CAT] [NAME...] | favorite remove SYM"
)


class BadArgs(Exception):
    pass


def envelope(command, ok=True, error=None, **payload):
    doc = {
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "ok": bool(ok),
        "error": error,
        "generated_at": int(time.time()),
        "demo": False,
        "rate_limited": bool(http.RATE_LIMITED),
        "cached": False,
        "attribution": [],
        "status_rows": [],
    }
    doc.update(payload)
    return doc


def _finish(repo, command, payload, ok=True, error=None):
    """Flush state, then fold the repository's run-wide facts into the envelope."""
    repo.flush()
    if error is None and repo.errors:
        error = dict(repo.errors[0])
        ok = False
    if repo.watchlist.recovered_from and error is None:
        error = {
            "code": "state_corrupt",
            "message": f"watchlist.json was unreadable and has been re-seeded (backup: {repo.watchlist.recovered_from})",
        }
    doc = envelope(command, ok=ok, error=error, **payload)
    doc["demo"] = repo.settings.demo
    doc["rate_limited"] = bool(http.RATE_LIMITED)
    doc["attribution"] = repo.attribution()
    doc["status_rows"] = repo.status_rows()
    return doc


def _parse_settings(argv):
    overrides = {}
    rest = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--settings":
            if i + 1 >= len(argv):
                raise BadArgs("--settings needs a JSON argument")
            try:
                parsed = json.loads(argv[i + 1])
            except ValueError as e:
                raise BadArgs(f"--settings is not valid JSON: {e}") from e
            if not isinstance(parsed, dict):
                raise BadArgs("--settings must be a JSON object")
            overrides.update(parsed)
            i += 2
            continue
        if a.startswith("--settings="):
            try:
                parsed = json.loads(a[len("--settings="):])
            except ValueError as e:
                raise BadArgs(f"--settings is not valid JSON: {e}") from e
            if not isinstance(parsed, dict):
                raise BadArgs("--settings must be a JSON object")
            overrides.update(parsed)
            i += 1
            continue
        rest.append(a)
        i += 1
    return Settings(overrides), rest


# ---- commands --------------------------------------------------------------

def cmd_status(repo, args, now):
    tracked = repo.watchlist.tracked()
    providers = []
    for p in repo.providers:
        providers.append({
            "id": p.id,
            "active": p in repo.active_providers(),
            "supports": [c for c in ("stock", "crypto", "currency") if p.supports(c)],
            "has_key": bool(getattr(p, "api_key", None)),
        })
    ages = [now - repo.quote_cache.fetched_at(i.symbol) for i in tracked if repo.quote_cache.fetched_at(i.symbol)]
    return {
        "version": plugin_version(),
        "python": sys.version.split()[0],
        "state_dir": repo.dir,
        "providers": providers,
        "tracked": len(tracked),
        "favorites": len(repo.watchlist.favorites()),
        "cache": {"quotes_age_s": max(ages) if ages else None},
    }


def cmd_snapshot(repo, args, now):
    max_age = None
    extra = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--max-age":
            if i + 1 >= len(args):
                raise BadArgs("--max-age needs a number of seconds")
            try:
                max_age = max(0, int(args[i + 1]))
            except ValueError as e:
                raise BadArgs("--max-age must be an integer") from e
            i += 2
        elif a == "--extra":
            i += 1
            while i < len(args) and not args[i].startswith("--"):
                extra.append(args[i])
                i += 1
        else:
            raise BadArgs(f"unknown snapshot option {a}")
    return repo.snapshot(now, max_age=max_age, extra=extra)


def cmd_quotes(repo, args, now):
    if not args:
        raise BadArgs("quotes needs at least one symbol")
    return repo.quotes(args, now)


def cmd_search(repo, args, now):
    query = " ".join(args).strip()
    if not query:
        raise BadArgs("search needs a query")
    return repo.search(query)


def cmd_candles(repo, args, now):
    if len(args) != 2:
        raise BadArgs("candles needs SYMBOL and RANGE")
    rng = args[1].upper()
    if rng not in RANGES:
        raise BadArgs(f"range must be one of {', '.join(RANGES)}")
    return repo.candles(args[0], rng, now)


def _instrument_args(repo, args, now):
    """SYM[:CAT] [CAT] [NAME...] -> Instrument. Falls back to the tracked
    entry's details; a new symbol needs its category. A new symbol added
    without a name is priced once so the entry carries the provider's name
    (and the strip has a price for it) instead of the bare ticker."""
    if not args:
        raise BadArgs("missing symbol")
    spec, _, spec_cat = str(args[0]).partition(":")
    sym, implied_ids = repo.canonical_symbol(spec)
    if not sym:
        raise BadArgs("missing symbol")
    cat = args[1].lower() if len(args) > 1 else spec_cat.strip().lower()
    name = " ".join(args[2:]).strip() if len(args) > 2 else ""
    known = repo.watchlist.instrument(sym)
    if cat and not is_category(cat):
        raise BadArgs("category must be stock, crypto or currency")
    if not cat:
        if known:
            cat = known.category
        else:
            raise BadArgs("category (stock|crypto|currency) is required for a new symbol")
    ids = dict(known.provider_ids) if known else {}
    ids.update(implied_ids)
    if cat == "crypto" and repo.coin_ids.get(sym):
        ids.setdefault("coingecko", repo.coin_ids[sym])
    inst = Instrument(sym, name or (known.name if known else sym), cat, ids)
    if not name and not known:
        q = repo.refresh([inst], now)[0]
        if q.valid and q.name:
            inst.name = q.name
    return inst


def cmd_watchlist(repo, args, now):
    if not args:
        raise BadArgs("watchlist add|remove ...")
    verb, rest = args[0], args[1:]
    if verb == "add":
        inst = _instrument_args(repo, rest, now)
        repo.watchlist.add_to_watchlist(inst)
    elif verb == "remove":
        if not rest:
            raise BadArgs("watchlist remove SYM")
        repo.watchlist.remove_from_watchlist(rest[0])
    else:
        raise BadArgs("watchlist add|remove ...")
    return repo.membership_payload(now)


def cmd_favorite(repo, args, now):
    if not args:
        raise BadArgs("favorite add|remove ...")
    verb, rest = args[0], args[1:]
    if verb == "add":
        inst = _instrument_args(repo, rest, now)
        repo.watchlist.add_favorite(inst)
    elif verb == "remove":
        if not rest:
            raise BadArgs("favorite remove SYM")
        repo.watchlist.remove_favorite(rest[0])
    else:
        raise BadArgs("favorite add|remove ...")
    return repo.membership_payload(now)


COMMANDS = {
    "status": cmd_status,
    "snapshot": cmd_snapshot,
    "quotes": cmd_quotes,
    "search": cmd_search,
    "candles": cmd_candles,
    "watchlist": cmd_watchlist,
    "favorite": cmd_favorite,
}


def dispatch(argv):
    settings, rest = _parse_settings(list(argv))
    if not rest or rest[0] in ("-h", "--help", "help"):
        raise BadArgs(USAGE)
    command, args = rest[0], rest[1:]
    handler = COMMANDS.get(command)
    if handler is None:
        raise BadArgs(f"unknown command '{command}'\n{USAGE}")
    now = int(time.time())
    repo = Repository(settings)
    try:
        payload = handler(repo, args, now)
    except BadArgs:
        raise
    except http.FetchError as e:
        return _finish(repo, command, {}, ok=False, error=e.to_dict())
    return _finish(repo, command, payload)


def emit(doc):
    line = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    try:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    except BrokenPipeError:
        pass


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    command = "?"
    try:
        # Best-effort command name for the error envelope (skip a --settings value).
        for i, a in enumerate(argv):
            if a == "--settings":
                continue
            if i > 0 and argv[i - 1] == "--settings":
                continue
            if not a.startswith("--"):
                command = a
                break
        doc = dispatch(argv)
    except BadArgs as e:
        doc = envelope(command, ok=False, error={"code": "bad_args", "message": str(e)})
    except BaseException as e:  # noqa: BLE001 — the never-crash rule
        doc = envelope(command, ok=False, error={"code": "internal", "message": f"{type(e).__name__}: {e}"})
        if os.environ.get("MARKETS_DEBUG"):
            import traceback

            traceback.print_exc()
    emit(doc)
    return 0

# omarchy-markets — agent notes

Before committing, re-read this file, the README and CHANGELOG against what actually changed and fix anything now stale. This file is the current-state reference: what the code does today and the rules for changing it safely. The session journal is the commit messages. Future work goes in `IDEAS.md`, never here.

The multi-session port plan (architecture, per-session scope, acceptance criteria) lives in `~/.claude/plans/alright-i-would-like-mossy-gosling.md`. The Windows original is `~/Work/MarketExtension` (C#); `~/Work/tickerbar` is a reference for the Quickshell side.

## What exists (0.1.0)

Only the data core. No widget yet — `BarWidget.qml` is a placeholder that makes the manifest validate.

```
bin/markets                  entry: fixes sys.path, calls marketslib.cli.main
bin/marketslib/cli.py        argv → one JSON line, exit 0, always
bin/marketslib/repo.py       provider routing, snapshot, strip, search, candles, membership
bin/marketslib/providers/    Provider base + coingecko.py
bin/marketslib/cache.py      QuoteCache (keep-last-good) + CandleCache (5 min TTL)
bin/marketslib/store.py      Watchlist: watchlist + favorites on disk, seed, corrupt-file recovery
bin/marketslib/fmt.py        every number → string (port of CurrencyFormat/UiQuote/UiCandleSeries)
bin/marketslib/http.py       capped, redirect-refusing GET with 429 back-off
bin/marketslib/models.py     Instrument / Quote / CandleSeries, categories, ranges
bin/marketslib/state.py      state dir, atomic 0600 JSON writes
tests/                       stdlib unittest; fakeserver.py + fixtures/, no real network
```

State dir: `${XDG_STATE_HOME:-~/.local/state}/omarchy/costafot.markets/` (`MARKETS_STATE_DIR` overrides). Files: `watchlist.json`, `quotes-cache.json`, `candles-cache.json`, `coin-ids.json`. QML never touches them.

## The helper contract

```
python3 bin/markets [--settings '<json>'] <command> [args]
```

`--settings` carries non-secret scalars from the shell.json entry (`strip`, `stripShowPrice`, `stripMax`, `demoMode`, `portfolioCurrency`, `showRateLimitErrors`); unknown keys are ignored. Commands: `status`, `snapshot [--max-age S] [--extra SYM[:CAT] ...]`, `quotes SYM[:CAT]...`, `search QUERY`, `candles SYM RANGE`, `watchlist add SYM CAT NAME... | remove SYM`, `favorite add SYM [CAT NAME...] | remove SYM`.

Envelope on every document: `schema_version:1, command, ok, error, generated_at, demo, rate_limited, cached, attribution[], status_rows[]`, then the payload. `error` is `{code, message, provider?, status?, retry_after?}` with codes `bad_args network rate_limited http too_large bad_response state_corrupt internal`. **A document can carry `ok:false` and data at the same time** (last-good prices during an outage); consumers treat "has data" and "has error" independently.

`Quote` fields: `symbol name category price change change_pct currency valid stale updated_at price_text change_text dir`. `dir` is `up|down|flat`; flat renders like up (▲, per `UiQuote.IsUp`). `strip[]` entries: `symbol label value_text dir valid stale`.

Category of a bare symbol: the tracked entry's category, else `currency` for a 6-letter pair of known codes, else `stock`. Pass `SYM:crypto` to force one. `watchlist add` of a new symbol requires the category.

## Hard-won constraints — do not re-litigate without re-testing

- **Never crash.** `cli.main` catches `BaseException`; a traceback on stdout would be parsed as garbage by QML and blank the bar. Errors ride inside the JSON. Verified: `bin/markets bogus; echo $?` → 0.
- **Keep-last-good lives in `cache.QuoteCache.upsert` only.** An invalid quote never overwrites a valid one; the old one is served with `stale:true` and `fetched_at` moves so `--max-age` still dedupes. `keep_last_good=False` is the hard-refresh path (source flips).
- **`snapshot --max-age S` is how multiple bars share one fetch.** It is a pure cache read when every observed symbol was attempted within S seconds. The QML poller uses 30.
- **CoinGecko public tier, verified live 2026-09-02:** `/coins/markets?symbols=btc&include_tokens=top` works (top-ranked coin per symbol); `ids=` is used whenever the id is known (seed, search result, learned). `market_chart?days>365` → HTTP 401, so 5Y clamps to 365 with a note in `series.message`. `days=30` returns hourly points (721), thinned to 300 by `downsample()`. Rate limit is unpublished and low: at most two calls per poll, one per chart.
- **All HTTP goes through `http.get_json`**: 1 MiB cap read one byte at a time (so the deadline is checked between reads), redirects refused, 429 retried at most 3 times honouring `Retry-After`, giving up when the wait would exceed 8 s. `http.RATE_LIMITED` is process-wide like the C# `RateLimitSignal`. `MARKETS_BACKOFF_SCALE=0` makes tests instant.
- **Keys never in a URL.** CoinGecko's optional key is a header (`x-cg-demo-api-key`); `http.redact()` masks `apikey/token/key` in `MARKETS_DEBUG` output. Nothing logs a body.
- **Formatting is Python's job** (`fmt.py`), rounding half-up to match .NET's `decimal.ToString`. QML renders strings and picks a colour from `dir`; it never formats a number.
- **Favorites seed diverges from Windows on purpose** (BTC/ETH/SOL starred) so the strip is not empty on first run.
- **State writes are atomic and 0600** (`state.write_json_atomic`); a corrupt `watchlist.json` is moved to `.bak.<ts>` and re-seeded, reported once as `error.code state_corrupt`.
- The manifest's `version` is the single source of truth (`marketslib.plugin_version()` reads it); bump it with the CHANGELOG in the same commit.

## Testing

```bash
python3 -m unittest discover -s tests -v          # offline, ~2 s
omarchy plugin validate /home/costa/Work/omarchy-markets
MARKETS_STATE_DIR=$(mktemp -d) bin/markets snapshot | jq '.strip'   # live
MARKETS_DEBUG=1 bin/markets quotes BTC >/dev/null                    # request log on stderr
```

## Dev loop (from session 2 on)

```bash
ln -s ~/Work/omarchy-markets ~/.config/omarchy/plugins/costafot.markets
omarchy plugin enable costafot.markets right
omarchy restart shell        # after EVERY QML edit — inotify does not follow the symlink
journalctl -t omarchy-shell -f | grep -i markets
```

Every QML `Text` sets `textFormat: Text.PlainText` (remote strings are rendered; the marketplace review blocks otherwise). Settings are written with one batched `updateEntryInline` per user action. No co-author trailers on commits; never amend; commit only when asked.

## Roadmap (one session each; details in the plan file)

2 bar strip + watchlist panel · 3 hub, search, favorites, detail, membership · 4 chart, ranges, rate-limit banner · 5 portfolio · 6 keys (secret-tool), demo mode, settings page · 7 Twelve Data, Frankfurter, Finnhub quotes · 8 news + ticker · 9 release polish 1.0.0

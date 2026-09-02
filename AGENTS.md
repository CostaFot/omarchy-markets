# omarchy-markets — agent notes

Before committing, re-read this file, the README and CHANGELOG against what actually changed and fix anything now stale. This file is the current-state reference: what the code does today and the rules for changing it safely. The session journal is the commit messages. Future work goes in `IDEAS.md`, never here.

The multi-session port plan (architecture, per-session scope, acceptance criteria) lives in `~/.claude/plans/alright-i-would-like-mossy-gosling.md`. The Windows original is `~/Work/MarketExtension` (C#); `~/Work/tickerbar` is a reference for the Quickshell side.

## What exists (0.3.0)

The data core with two keyless providers, plus the first QML: the bar strip and a one-page watchlist panel.

```
BarWidget.qml                strip: coloured PlainText runs, width degradation, Loader(Panel.qml), IpcHandler
Panel.qml                    Panel > KeyboardPanel > PanelKeyCatcher > Flickable > rows; owns the Store
Store.qml                    QtObject: runs bin/markets via Process, holds the snapshot, poll Timer, theme colours
bin/markets                  entry: fixes sys.path, calls marketslib.cli.main
bin/marketslib/cli.py        argv → one JSON line, exit 0, always
bin/marketslib/repo.py       provider routing, snapshot, strip, search, candles, membership
bin/marketslib/providers/    Provider base + coingecko.py (crypto) + yahoo.py (stocks, indices, FX)
bin/marketslib/cache.py      QuoteCache (keep-last-good) + CandleCache (5 min TTL)
bin/marketslib/store.py      Watchlist: watchlist + favorites on disk, seed, corrupt-file recovery
bin/marketslib/fmt.py        every number → string (port of CurrencyFormat/UiQuote/UiCandleSeries)
bin/marketslib/http.py       capped, redirect-refusing GET with 429 back-off
bin/marketslib/models.py     Instrument / Quote / CandleSeries, categories, ranges, downsample
bin/marketslib/state.py      state dir, atomic 0600 JSON writes
tests/                       stdlib unittest; fakeserver.py + fixtures/ (captured live, trimmed), no real network
```

State dir: `${XDG_STATE_HOME:-~/.local/state}/omarchy/costafot.markets/` (`MARKETS_STATE_DIR` overrides). Files: `watchlist.json`, `quotes-cache.json`, `candles-cache.json`, `coin-ids.json` (CoinGecko symbol → id), `yahoo-meta.json` (Yahoo wire symbol → name, currency, type, exchange). QML never touches them.

## The QML side

- **BarWidget.qml** loads `Panel.qml` once (`Loader { active: true }`) and injects `bar settings anchorItem hostWidget` on every `bar`/`settings` change. The panel is held as an untyped `var`: naming the type `Panel` collides with the `qs.Ui` base. The strip reads `store.strip` and paints `label` in the bar foreground and `value_text` in `store.dirColor(dir)`; invalid entries are dimmed. Width degradation (`stripMaxWidth → fitCount → pieces`) is tickerbar's model of Bar.qml's sections and centre anchor, copied; a truncated strip ends in `…`, a stale one in the pause glyph, and below the glyph's width the widget hides. `openPanelIndicatorWidth` tells the bar how wide the open-panel mark is. IPC target `costafot.markets`: `open close show hide toggle refresh`; `refresh` uses `broadcast()` so every monitor's instance refetches.
- **Store.qml** is the only place the helper is run. `run(args, onDone)`: one `Process` at a time through `sh -c 'exec "$0" "$@"'`, last-command-wins queue, both the exit code and the collected stdout must land before a run is finalised (300 ms fallback timer), 1 MiB tripwire on the collector. `refresh(force)` is `snapshot --max-age (force ? 0 : 30)`. A document with `strip`/`quotes` replaces the snapshot even when `ok:false`; a document with `error` sets `lastError`/`stale` but never blanks the prices. Non-snapshot documents merge `quotes instruments favorites strip` into the snapshot. `settingsJson` serialises only the keys the helper knows; a change of that string (not of the `settings` object) refetches. First fetch is `Component.onCompleted: Qt.callLater(refresh)`.
- **Theme colours** live in `Store.upColor`/`downColor`: a `FileView` on `Color.currentThemePath + "/colors.toml"` with `watchChanges`, parsed for `green`/`red`, then `color2`/`color1`, then `Color.accent`/`Color.urgent`. The ANSI fallback matters: Costa's own "Catppuccin Mocha" user theme has no `green`/`red` keys (verified 2026-09-03), only the stock themes do. `flat` colours like `up`.
- **Panel.qml** builds a flat `rows` array (`header sep instrument note attribution footer`) from the store and renders it in a `Repeater` inside a `Flickable`; instrument and attribution rows are `CursorSurface` and take the cursor; `ensureCursorVisible()` scrolls on j/k. `r` refreshes (force), Escape closes, Enter on an attribution row opens its URL; Enter on an instrument does nothing until the detail page (S4). Height cap `Style.space(760)` fits the seed watchlist without scrolling. Opening the panel refreshes with `--max-age 30`.

## The helper contract

```
python3 bin/markets [--settings '<json>'] <command> [args]
```

`--settings` carries non-secret scalars from the shell.json entry (`strip`, `stripShowPrice`, `stripMax`, `demoMode`, `portfolioCurrency`, `showRateLimitErrors`); unknown keys are ignored. Commands: `status`, `snapshot [--max-age S] [--extra SYM[:CAT] ...]`, `quotes SYM[:CAT]...`, `search QUERY`, `candles SYM RANGE`, `watchlist add SYM CAT NAME... | remove SYM`, `favorite add SYM [CAT NAME...] | remove SYM`.

Envelope on every document: `schema_version:1, command, ok, error, generated_at, demo, rate_limited, cached, attribution[], status_rows[]`, then the payload. `error` is `{code, message, provider?, status?, retry_after?}` with codes `bad_args network rate_limited http too_large bad_response state_corrupt internal`. **A document can carry `ok:false` and data at the same time** (last-good prices during an outage); consumers treat "has data" and "has error" independently.

`Quote` fields: `symbol name category price change change_pct currency valid stale updated_at price_text change_text dir`. `dir` is `up|down|flat`; flat renders like up (▲, per `UiQuote.IsUp`). `strip[]` entries: `symbol label value_text dir valid stale`.

Category of a bare symbol: the tracked entry's category, else `currency` for a 6-letter pair of known codes or anything spelled `XXXYYY=X`, else `stock` (`^GSPC`, `HSBA.L`, `BRK-B` pass through). Yahoo's `EURUSD=X` spelling is accepted anywhere a symbol is and stored as `EURUSD` with `provider_ids.yahoo`. Pass `SYM:crypto` to force a category. `watchlist add` of a new symbol requires the category.

Provider order is `[Yahoo, CoinGecko]`, first `supports(category)` wins: stocks and currencies go to Yahoo, crypto to CoinGecko. Attribution rows list only the providers that served valid data this run.

## Hard-won constraints — do not re-litigate without re-testing

- **Never crash.** `cli.main` catches `BaseException`; a traceback on stdout would be parsed as garbage by QML and blank the bar. Errors ride inside the JSON. Verified: `bin/markets bogus; echo $?` → 0.
- **Keep-last-good lives in `cache.QuoteCache.upsert` only.** An invalid quote never overwrites a valid one; the old one is served with `stale:true` and `fetched_at` moves so `--max-age` still dedupes. `keep_last_good=False` is the hard-refresh path (source flips).
- **`snapshot --max-age S` is how multiple bars share one fetch.** It is a pure cache read when every observed symbol was attempted within S seconds. The QML poller uses 30.
- **CoinGecko public tier, verified live 2026-09-02:** `/coins/markets?symbols=btc&include_tokens=top` works (top-ranked coin per symbol); `ids=` is used whenever the id is known (seed, search result, learned). `market_chart?days>365` → HTTP 401, so 5Y clamps to 365 with a note in `series.message`. `days=30` returns hourly points (721), thinned to 300 by `downsample()`. Rate limit is unpublished and low: at most two calls per poll, one per chart.
- **All HTTP goes through `http.get_json`**: 1 MiB cap read one byte at a time (so the deadline is checked between reads), redirects refused, 429 retried at most 3 times honouring `Retry-After`, giving up when the wait would exceed 8 s. `http.RATE_LIMITED` is process-wide like the C# `RateLimitSignal`. `MARKETS_BACKOFF_SCALE=0` makes tests instant.
- **Yahoo Finance refuses default library user agents.** `python-urllib/3.14` and curl's default get HTTP 429; our `costafot.markets/<version>` is accepted (verified 2026-09-03). `http.get` sends it on every request and the fake server answers 429 without it; do not drop it. Yahoo is unofficial: a 404, 429 or shape change must become `valid:false` rows, never an exception (`test_garbage_bodies_are_invalid_rows`).
- **Yahoo quotes are two-tier so steady state is one call per poll.** `v8/finance/spark?symbols=A,B,C` prices many symbols in one call but carries no currency or name and silently drops unknown symbols, so it is only used for symbols whose currency is already in `yahoo-meta.json`. First sight of a symbol is one `v8/finance/chart/{sym}?range=1d&interval=5m`, which prices it from `meta.regularMarketPrice`/`previousClose` and learns its meta. A symbol Yahoo does not know (404) learns nothing and costs one chart call every poll; bounded, accepted. `v7/finance/quote` is 401 (cookie + crumb): never use it.
- **Yahoo partial failures stay partial.** A failed chart call inside a batch becomes an invalid row plus a `Provider.take_errors()` entry that the repo folds into `errors`; only when nothing at all came back does `quotes()` raise, so the repo records one outage for the batch like any provider. After the first 429 in a run the remaining chart calls are skipped (`http.RATE_LIMITED`), so a rate-limited poll cannot take five symbols × three retries.
- **Pence.** LSE quotes arrive as `currency:"GBp"` in pence; `fmt.currency_scale` gives `("GBP", 0.01)` and both quotes and every candle point are scaled. `yahoo-meta.json` keeps the raw `GBp` so the spark path knows to scale.
- **Search merge order was measured, not guessed.** An exact symbol match that its own provider ranked in its top three leads (`sol` → SOL the coin, `hsbc` → HSBC the stock), the rest alternate between providers, capped at 15, deduped per (symbol, category). CoinGecko lists a junk coin whose symbol is `APPLE` and tokenised stocks like `AAPL` as crypto; `apple` must still return AAPL the stock first. Live results for eight queries are in the session-3 commit message.
- **Every test environment must set `MARKETS_YAHOO_URL`** as well as `MARKETS_COINGECKO_URL`: the seed watchlist has stocks and FX, so a `snapshot` in a test without it reaches the real Yahoo (it happened once, caught by an attribution assertion).
- **Keys never in a URL.** CoinGecko's optional key is a header (`x-cg-demo-api-key`); `http.redact()` masks `apikey/token/key` in `MARKETS_DEBUG` output. Nothing logs a body.
- **Formatting is Python's job** (`fmt.py`), rounding half-up to match .NET's `decimal.ToString`. QML renders strings and picks a colour from `dir`; it never formats a number.
- **Favorites seed diverges from Windows on purpose** (BTC/ETH/SOL starred) so the strip is not empty on first run.
- **State writes are atomic and 0600** (`state.write_json_atomic`); a corrupt `watchlist.json` is moved to `.bak.<ts>` and re-seeded, reported once as `error.code state_corrupt`.
- The manifest's `version` is the single source of truth (`marketslib.plugin_version()` reads it); bump it with the CHANGELOG in the same commit.
- **A fresh plugin dir needs `omarchy-shell shell rescanPlugins` before `omarchy plugin enable` knows it** (the enable otherwise says "not known" and the journal logs `PluginRegistry.setEnabled: unknown plugin`). Done once on 2026-09-03; the symlink is in place.
- **qmllint needs a `qs` import root.** `-I /usr/share/omarchy/shell` alone fails to import `qs.Commons`; make a directory containing a symlink `qs -> /usr/share/omarchy/shell` and pass that with `-I`. Baseline noise that cannot be fixed: `Member … not found on type "QObject"` for everything reached through `bar` (typed `QtObject` by the base) and the `QProcess::ExitStatus` warning on `onExited`. Yeet and tickerbar lint to the same set; anything else is ours.

## Testing

```bash
python3 -m unittest discover -s tests -v          # offline, ~2 s
omarchy plugin validate /home/costa/Work/omarchy-markets
mkdir -p /tmp/qmlimports && ln -sfn /usr/share/omarchy/shell /tmp/qmlimports/qs
/usr/lib/qt6/bin/qmllint -I /tmp/qmlimports *.qml | grep -v 'on type "QObject"'   # qmllint is not on PATH
MARKETS_STATE_DIR=$(mktemp -d) bin/markets snapshot | jq '.strip'   # live
MARKETS_DEBUG=1 bin/markets quotes BTC >/dev/null                    # request log on stderr
bin/markets quotes AAPL EURUSD HSBA.L BTC | jq -c '.quotes[] | [.symbol,.valid,.price_text]'   # 4 valid, HSBA in £
bin/markets candles AAPL 5Y | jq '.series | {valid, n, message}'    # real 5Y, 263 weekly points, no note
MARKETS_DEBUG=1 bin/markets snapshot 2>&1 >/dev/null | grep -c yahoo   # 1 on the second run (one spark call)
```

## Dev loop

```bash
ln -s ~/Work/omarchy-markets ~/.config/omarchy/plugins/costafot.markets   # done
omarchy-shell shell rescanPlugins && omarchy plugin enable costafot.markets right   # done
omarchy restart shell        # after EVERY QML edit — inotify does not follow the symlink
journalctl -t omarchy-shell -f | grep -i markets
omarchy-shell costafot.markets toggle; wtype j; wtype r; wtype -k Escape   # keyboard path without touching the keyboard
grim -g "1200,0 1360x800" -s 1 panel.png                                   # the panel opens under the widget, top right
```

Verified live on 2026-09-03 (single 2560×1440 monitor, Catppuccin Mocha): strip, panel, IPC toggle/refresh, j/k/r/Escape, theme switch to Gruvbox and back recolours without restart. Not verified: width degradation to fewer entries and the glyph (needs a narrower bar or a second monitor; the code is tickerbar's, unchanged in logic).

Every QML `Text` sets `textFormat: Text.PlainText` (remote strings are rendered; the marketplace review blocks otherwise). Settings are written with one batched `updateEntryInline` per user action. No co-author trailers on commits; never amend; commit only when asked.

## Roadmap (one session each; details in the plan file)

~~2 bar strip + watchlist panel~~ (done, 0.2.0) · ~~3 Yahoo Finance provider~~ (done, 0.3.0) · **4 hub, search, favorites, detail, membership** · 5 chart, ranges, rate-limit banner · 6 portfolio (Frankfurter rates only) · 7 keys (secret-tool), demo mode, settings page · 8 optional keyed providers: Twelve Data, Finnhub quotes · 9 news + ticker · 10 release polish 1.0.0

Revised 2026-09-03 after reviewing stochi, omarchy-stocks and OmaStockTicker; what was borrowed and what was rejected is in `~/.claude/plans/hey-i-found-3-ticklish-cake.md`.

# omarchy-markets — agent notes

Before committing, re-read this file, the README and CHANGELOG against what actually changed and fix anything now stale. This file is the current-state reference: what the code does today and the rules for changing it safely. The session journal is the commit messages. Future work goes in `IDEAS.md`, never here.

The multi-session port plan (architecture, per-session scope, acceptance criteria) lives in `~/.claude/plans/alright-i-would-like-mossy-gosling.md`. The Windows original is `~/Work/MarketExtension` (C#); `~/Work/tickerbar` is a reference for the Quickshell side.

## What exists (0.5.0)

The data core with two keyless providers, plus the QML: the bar strip and a multi-page panel (hub, search, watchlist, favorites, detail with a chart and membership), a rate-limit banner and a `status` IPC.

```
BarWidget.qml                strip: coloured PlainText runs, width degradation, Loader(Panel.qml), IpcHandler
Panel.qml                    Panel > KeyboardPanel > [TextField + Flickable > rows]; page stack, page renderers; owns the Store
Store.qml                    QtObject: runs bin/markets via Process, holds the snapshot, poll Timer, extras, theme colours, rateLimited
Chart.qml                    Canvas line chart for the detail page (port of ChartHelper.cs) + labels; formats nothing
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

State dir: `${XDG_STATE_HOME:-~/.local/state}/omarchy/costafot.markets/` (`MARKETS_STATE_DIR` overrides). Files: `watchlist.json`, `quotes-cache.json`, `candles-cache.json`, `coin-ids.json` (CoinGecko symbol → id), `yahoo-meta.json` (Yahoo wire symbol → name, currency, type, exchange), `rate-limit.json` (`{since}`, the rate-limit latch; absent when clear). QML never touches them.

## The QML side

- **BarWidget.qml** loads `Panel.qml` once (`Loader { active: true }`) and injects `bar settings anchorItem hostWidget` on every `bar`/`settings` change. The panel is held as an untyped `var`: naming the type `Panel` collides with the `qs.Ui` base. The strip reads `store.strip` and paints `label` in the bar foreground and `value_text` in `store.dirColor(dir)`; invalid entries are dimmed. Width degradation (`stripMaxWidth → fitCount → pieces`) is tickerbar's model of Bar.qml's sections and centre anchor, copied; a truncated strip ends in `…`, a stale one in the pause glyph (`stripStale`: the newest run failed, or `store.rateLimited`, or any entry shown is a kept last-good price), and below the glyph's width the widget hides. **Known wrong for a right-section widget:** with five favorites the strip painted over the centre clock (2026-09-03); the bar functions it reads all exist, the budget formula is what is off. Parked in `IDEAS.md`. `openPanelIndicatorWidth` tells the bar how wide the open-panel mark is. IPC target `costafot.markets`: `open close show hide toggle refresh page(name) add(symbol, category) favorite(symbol) status`; `refresh` uses `broadcast()` so every monitor's instance refetches, `page add favorite` call into the panel (`showPage addSymbol favoriteSymbol`), `status` returns one JSON line (`statusJson()`: page, staleness, the strip text and width budget, `chartStatus()`) for `omarchy-shell costafot.markets status | jq`.
- **Store.qml** is the only place the helper is run. `run(args, onDone)`: one `Process` at a time through `sh -c 'exec "$0" "$@"'`, last-command-wins queue, both the exit code and the collected stdout must land before a run is finalised (300 ms fallback timer), 1 MiB tripwire on the collector. `refresh(force)` is `snapshot --max-age (force ? 0 : 30)` plus `--extra` for `extras` (the panel binds it to the untracked symbols whose detail page is on the stack). A document with `strip`/`quotes` replaces the snapshot even when `ok:false`; a document with `error` sets `lastError`/`stale` but never blanks the prices. Non-snapshot documents merge `instruments favorites strip` into the snapshot, `quotes` additively (a membership document carries only tracked symbols and a detail page may be showing an untracked one), and `attribution` only when non-empty (a mutation that fetched nothing credits nobody). `rateLimited` is taken from every document (the helper's latch makes it consistent across runs); `rateLimitBanner = rateLimited && showRateLimitErrors && !demo` is the RateLimitHint rule, and `statusRows` always drops the helper's `rate_limited` row because the panel paints the banner instead. `settingsJson` serialises only the keys the helper knows; a change of that string (not of the `settings` object) refetches. First fetch is `Component.onCompleted: Qt.callLater(refresh)`.
- **Theme colours** live in `Store.upColor`/`downColor`/`warnColor`: a `FileView` on `Color.currentThemePath + "/colors.toml"` with `watchChanges`, parsed for `green`/`red`/`yellow`, then `color2`/`color1`/`color3`, then `Color.accent`/`Color.urgent`/the Windows banner's `#c87c00`. The ANSI fallback matters: Costa's own "Catppuccin Mocha" user theme has no `green`/`red` keys (verified 2026-09-03), only the stock themes do. `flat` colours like `up`.
- **Panel.qml** keeps a page `stack` (`[{page:"hub"}, {page:"watchlist", cursor, query}, {page:"detail", symbol, name, category}]`); `push` saves the cursor and filter text on the entry it leaves, `pop` restores them, `enterPage` resets the scroll, sets the field text and focuses the field or the catcher (`focusTarget` also follows `hasField`, because `KeyboardPanel` focuses its target on open through its own `Qt.callLater`). Every page is a renderer returning a flat `rows` array (`title header sep instrument action hero note attribution footer`); one `Repeater` in one `Flickable` paints them, and the only widget outside the list is the `TextField` the list pages start in. **Typing vs vim keys:** `PanelKeyCatcher.blocked: filterField.activeFocus`; the field's `Keys.onPressed` forwards Up/Down (cursor), Enter (activate), Escape (pop), Tab (focus the catcher) and Backspace-on-empty (pop); in the catcher `/` refocuses the field, `r` refreshes, Backspace pops through a `Keys.onPressed` on the catcher's parent `Item` (the catcher never accepts it). Hover moves the cursor only when the pointer really moved (`hoverRow` compares scene coordinates): rows re-laid out under a resting pointer get a synthetic move, and it once opened the seventh search result instead of the first. Search is Enter-only: the action row `Search markets for "q"` runs `store.run(["search", q])`; results show while the field still says the query they belong to, and the cursor jumps to the first result when they land. The detail page prices an untracked symbol on entry (`store.refresh(false)` with the symbol in `extras`), shows Add rows only once the symbol is priced (tracked symbols always get Remove), and `membership()` runs `watchlist|favorite add|remove` and shows a 3 s notice with the helper's answer. Height cap `Style.space(760)`. Opening the panel refreshes with `--max-age 30` and resets to the hub unless an IPC `page` set `pendingStack` first. The rate-limit banner is a `note` row with `warn:true` pushed right after the title on every page while `store.rateLimitBanner` holds.
- **The chart** (port of `SymbolChartForm`): `chartRange` is sticky while the shell runs, `chartSeries` is what is painted, `chartCache[symbol|range] = {series, at}` keeps series for `chartTtlMs` (5 min, the helper's TTL) so a revisited tab runs no process, `chartGeneration` drops the answer to a superseded request (`store.run`'s last-command-wins queue never calls a replaced job's callback, so only the newest answers). `loadChart(range, force)` runs `candles SYM:CAT RANGE`; a prior chart stays up while another range loads (`Loading 1M…` under it), and a range that comes back invalid keeps the chart and puts its message under it; only the first chart of a symbol shows the loading card. Entering a detail page loads the chart, `r` forces it, a poll landing (`Connections` on `store.generatedAt`) reloads it through the cache window. Keys in the catcher: `←/→` and `h/l` (`moveRequested(dx)`) step the range, `1`–`5` (`textKey`) pick one; the tabs row is not a cursor row, the mouse clicks it. Rows: `hero chart tabs sep action…`; the `chart` row is a `Loader` (one Canvas, only for that row kind) holding `Chart` plus the `range_change_text` in `dirColor` and the note. `Chart.qml` does geometry only: x proportional to timestamp, y normalised to the series' min/max (flat rides mid-height), quarter gridlines at 0.18, gradient 0.35→0.02, the dashed previous-close line when `previous_close` lies inside the bounds, min/max labels at the right edge and the previous-close label at the left on translucent `Color.popups.background` backings (the max label sits exactly where the line peaks), first/last stamps under the plot. Every string comes from the helper.

## The helper contract

```
python3 bin/markets [--settings '<json>'] <command> [args]
```

`--settings` carries non-secret scalars from the shell.json entry (`strip`, `stripShowPrice`, `stripMax`, `demoMode`, `portfolioCurrency`, `showRateLimitErrors`); unknown keys are ignored. Commands: `status`, `snapshot [--max-age S] [--extra SYM[:CAT] ...]` (`--max-age 0` fetches everything: stamps are whole seconds, so a forced refresh in the same second as a poll would otherwise be a cache read), `quotes SYM[:CAT]...`, `search QUERY`, `candles SYM RANGE`, `watchlist add SYM[:CAT] [CAT] [NAME...] | remove SYM`, `favorite add SYM[:CAT] [CAT] [NAME...] | remove SYM`. Membership commands answer with `quotes instruments favorites strip` (the tracked set, from the cache) so the panel re-renders with no second call; a new symbol added without a name is priced once on the way in and takes the provider's name (`TSLA` → `Tesla, Inc.`), an unpriced one is still added under its ticker.

Envelope on every document: `schema_version:1, command, ok, error, generated_at, demo, rate_limited, cached, attribution[], status_rows[]`, then the payload. `error` is `{code, message, provider?, status?, retry_after?}` with codes `bad_args network rate_limited http too_large bad_response state_corrupt internal`. **A document can carry `ok:false` and data at the same time** (last-good prices during an outage); consumers treat "has data" and "has error" independently.

`Quote` fields: `symbol name category price change change_pct currency valid stale updated_at price_text change_text dir`. `dir` is `up|down|flat`; flat renders like up (▲, per `UiQuote.IsUp`). `strip[]` entries: `symbol label value_text dir valid stale`. `candles` → `{cached, series}` with `series`: `symbol range valid message currency category points[[ts, close]] n first last min max previous_close dir price_text range_change_text min_text max_text previous_close_text first_label last_label`; `previous_close` only on a Yahoo 1D series (`chartPreviousClose`, pence-scaled), `min_text`/`max_text` are rates for `currency` and money otherwise, the time labels are local time (`fmt.time_label`: `HH:MM`, `Wed 2 Sep`, `4 Aug`, `Sep 2025`).

`rate_limited` is a latch (port of `RateLimitSignal`): a 429 that survived the retries writes `rate-limit.json`, and every later run reports `true` until one of its requests comes back 2xx (`http.SUCCEEDED`), which deletes the file; a run that makes no request (a cached snapshot) keeps reporting it. A latch older than an hour is ignored, and demo mode never reports one. Decided once per run in `Repository.rate_limited()` (memoised; tests that flip `http.RATE_LIMITED` mid-run reset `_rate_limited`). The `status_rows` entry of kind `rate_limited` carries `text` and `detail` and is only emitted when `showRateLimitErrors` is on; the flag itself always rides.

Category of a bare symbol: the tracked entry's category, else `currency` for a 6-letter pair of known codes or anything spelled `XXXYYY=X`, else `stock` (`^GSPC`, `HSBA.L`, `BRK-B` pass through). Yahoo's `EURUSD=X` spelling is accepted anywhere a symbol is and stored as `EURUSD` with `provider_ids.yahoo`. Pass `SYM:crypto` to force a category. `watchlist add` of a new symbol requires the category.

Provider order is `[Yahoo, CoinGecko]`, first `supports(category)` wins: stocks and currencies go to Yahoo, crypto to CoinGecko. Attribution rows list only the providers that served valid data this run.

## Hard-won constraints — do not re-litigate without re-testing

- **Never crash.** `cli.main` catches `BaseException`; a traceback on stdout would be parsed as garbage by QML and blank the bar. Errors ride inside the JSON. Verified: `bin/markets bogus; echo $?` → 0.
- **Keep-last-good lives in `cache.QuoteCache.upsert` only.** An invalid quote never overwrites a valid one; the old one is served with `stale:true` and `fetched_at` moves so `--max-age` still dedupes. `keep_last_good=False` is the hard-refresh path (source flips).
- **`snapshot --max-age S` is how multiple bars share one fetch.** Only the observed symbols attempted longer than S seconds ago are fetched; when that is none it is a pure cache read (`cached:true`). The QML poller uses 30, and a detail page's `--extra` symbol therefore costs one call for itself, not a refetch of the watchlist.
- **CoinGecko public tier, verified live 2026-09-02:** `/coins/markets?symbols=btc&include_tokens=top` works (top-ranked coin per symbol); `ids=` is used whenever the id is known (seed, search result, learned). `market_chart?days>365` → HTTP 401, so 5Y clamps to 365 with a note in `series.message`. `days=30` returns hourly points (721), thinned to 300 by `downsample()`. Rate limit is unpublished and low: at most two calls per poll, one per chart.
- **All HTTP goes through `http.get_json`**: 1 MiB cap read one byte at a time (so the deadline is checked between reads), redirects refused, 429 retried at most 3 times honouring `Retry-After`, giving up when the wait would exceed 8 s. `http.RATE_LIMITED` is process-wide like the C# `RateLimitSignal`. `MARKETS_BACKOFF_SCALE=0` makes tests instant.
- **Yahoo Finance refuses default library user agents.** `python-urllib/3.14` and curl's default get HTTP 429; our `costafot.markets/<version>` is accepted (verified 2026-09-03). `http.get` sends it on every request and the fake server answers 429 without it; do not drop it. Yahoo is unofficial: a 404, 429 or shape change must become `valid:false` rows, never an exception (`test_garbage_bodies_are_invalid_rows`).
- **Yahoo quotes are two-tier so steady state is one call per poll.** `v8/finance/spark?symbols=A,B,C` prices many symbols in one call but carries no currency or name and silently drops unknown symbols, so it is only used for symbols whose currency is already in `yahoo-meta.json`. First sight of a symbol is one `v8/finance/chart/{sym}?range=1d&interval=5m`, which prices it from `meta.regularMarketPrice`/`previousClose` and learns its meta. A symbol Yahoo does not know (404) learns nothing and costs one chart call every poll; bounded, accepted. `v7/finance/quote` is 401 (cookie + crumb): never use it.
- **Yahoo partial failures stay partial.** A failed chart call inside a batch becomes an invalid row plus a `Provider.take_errors()` entry that the repo folds into `errors`; only when nothing at all came back does `quotes()` raise, so the repo records one outage for the batch like any provider. After the first 429 in a run the remaining chart calls are skipped (`http.RATE_LIMITED`), so a rate-limited poll cannot take five symbols × three retries.
- **A rate-limited poll must leave the bar showing prices.** Verified end to end 2026-09-03 against `python3 tests/fakeserver.py --mode 429` on the real state dir: `ok:false`, `error.code rate_limited`, every strip entry `stale:true`, the latch written, the panel opened cached (`--max-age 30`) with the amber banner and the bar's pause glyph, `r` cleared it (`test_rate_limit_keeps_prices_and_latches_until_a_fetch_succeeds` is the offline copy).
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
bin/markets watchlist add TSLA stock | jq -c '.instruments[] | select(.symbol=="TSLA") | .name'   # "Tesla, Inc."
bin/markets watchlist remove TSLA | jq '.instruments | length'
bin/markets candles ZZZZ 1D | jq -c '{ok, valid: .series.valid, message: .series.message}'   # ok:true, valid:false, "Not found"
bin/markets candles AAPL 1D | jq -c '.series | {previous_close_text, first_label, last_label, min_text}'
bin/markets candles BTC 1M | jq .cached; bin/markets candles BTC 1M | jq .cached          # false, then true
python3 tests/fakeserver.py --mode 429 &   # prints its URL; MARKETS_COINGECKO_URL=… MARKETS_YAHOO_URL=… bin/markets snapshot --max-age 0
omarchy-shell costafot.markets status | jq '.chart'
```

## Dev loop

```bash
ln -s ~/Work/omarchy-markets ~/.config/omarchy/plugins/costafot.markets   # done
omarchy-shell shell rescanPlugins && omarchy plugin enable costafot.markets right   # done
omarchy restart shell        # after EVERY QML edit — inotify does not follow the symlink
journalctl -t omarchy-shell -f | grep -i markets
omarchy-shell costafot.markets toggle; sleep 2; wtype -k Return; wtype doge; wtype -k Return   # keyboard path without touching the keyboard
grim -g "1200,0 1360x800" -s 1 panel.png                                   # the panel opens under the widget, top right
grim -g "1337,28 370x562" -s 1 assets/detail-chart.png                     # the panel alone, for the README (three favorites in the strip)
omarchy-shell costafot.markets add TSLA stock; omarchy-shell costafot.markets page watchlist
```

**Wait ~8 s after `omarchy restart shell` and confirm the panel is open (screenshot) before `wtype`:** while the shell is still starting the IPC toggle does nothing and the keystrokes land in whatever has focus, which on 2026-09-03 was Costa's terminal. Undo test additions afterwards (`bin/markets favorite remove X; bin/markets watchlist remove X`); the dev symlink means the live state dir is his real one.

Verified live on 2026-09-03 (single 2560×1440 monitor, Catppuccin Mocha): strip, hub, search `doge` → detail with a price → Add to watchlist → Add to favorites → in the strip with no restart; Backspace back to the results with the query kept; watchlist filter `et` → ETH; Tab/`/`; IPC `add TSLA stock`, `page watchlist`, `favorite TSLA`; theme switch (S2). Width degradation is known broken for the right section (see BarWidget above). Session 5 (2026-09-03): BTC 1D chart within 2 s of opening the page, AAPL 1D with the dashed $325.13 previous close, AAPL 5Y with five real years (263 points), `5` then `h` → 5Y then 1Y, the 429 flow above.

Every QML `Text` sets `textFormat: Text.PlainText` (remote strings are rendered; the marketplace review blocks otherwise). Nerd Font glyphs in `Panel.qml` (hub icons, `+`/`−` on the membership rows, the banner's warning sign) are PUA characters `U+F002 F013 F03A F05A F067 F068 F071 F19C F1EA`; an editor or tool that strips them leaves empty strings silently, so check with `python3 -c` after touching those lines. Settings are written with one batched `updateEntryInline` per user action. No co-author trailers on commits; never amend; commit only when asked.

## Roadmap (one session each; details in the plan file)

~~2 bar strip + watchlist panel~~ (done, 0.2.0) · ~~3 Yahoo Finance provider~~ (done, 0.3.0) · ~~4 hub, search, favorites, detail, membership~~ (done, 0.4.0) · ~~5 chart, ranges, rate-limit banner~~ (done, 0.5.0) · **6 portfolio (Frankfurter rates only)** · 7 keys (secret-tool), demo mode, settings page · 8 optional keyed providers: Twelve Data, Finnhub quotes · 9 news + ticker · 10 release polish 1.0.0

Revised 2026-09-03 after reviewing stochi, omarchy-stocks and OmaStockTicker; what was borrowed and what was rejected is in `~/.claude/plans/hey-i-found-3-ticklish-cake.md`.

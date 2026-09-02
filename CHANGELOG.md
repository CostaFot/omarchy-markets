# Changelog

## 0.4.0

- The panel is now a hub: Search, Watchlist, Favorites, and placeholders for Portfolio, News, Data sources and Settings. Pages stack; Escape and Backspace walk back, Escape on the hub closes, and backing out of a page lands on the row that opened it with the filter still typed.
- Search: type a symbol or name and press Enter; one helper call, results from both providers with a Stock/Crypto/Currency tag and the row's current membership. Typing never touches the network.
- Watchlist and Favorites pages filter as you type (symbol or name); Tab moves from the box to the list for j/k, `/` goes back to the box. The star on a row toggles the favorite with the mouse.
- Detail page for any instrument: symbol, name, price, change, then Add/Remove for the watchlist and favorites, labelled for the current state, with a short confirmation. An untracked symbol is priced on the way in, and Add appears once it has a price. The chart comes in the next version.
- `omarchy-shell costafot.markets page watchlist`, `add DOGE crypto`, `favorite TSLA` (toggle; `favorite NEW:crypto` for a symbol not yet tracked).
- Helper: `snapshot --max-age S --extra SYM:CAT` now fetches only the symbols older than S (an untracked detail symbol costs one call, not a refetch of the watchlist); `watchlist add` and `favorite add` accept `SYM:CAT`, and a nameless add is priced once so the entry gets the provider's name; membership documents carry the tracked quotes so the panel re-renders with no second call.
- 115 offline tests.

## 0.3.0

- Stocks, indices and currencies are priced, searched and charted through Yahoo Finance with no key: `AAPL`, `HSBA.L` (pence converted to pounds), `^GSPC`, `EURUSD`. Crypto stays on CoinGecko.
- One request per poll once a symbol has been seen: the batch `spark` endpoint prices every known symbol, a first sighting costs one `chart` call that learns the name and currency into `yahoo-meta.json`.
- Real five-year charts for stocks and FX (weekly, 263 points); CoinGecko's one-year cap now applies to crypto only.
- Search merges both providers: `sol` finds Solana first, `apple` finds AAPL first, `hsbc` lists the NYSE, Hong Kong and London listings.
- Yahoo's `EURUSD=X` spelling is accepted everywhere a symbol is.
- Unknown symbols, outages and shape changes become unpriced rows, never errors that blank the bar; after one rate-limit answer the rest of the poll is skipped.
- 112 offline tests; the Yahoo fixtures are trimmed live captures. No QML change: the panel's stock and currency rows fill in by themselves.

## 0.2.0

- The bar strip: favorites as `BTC $77,250 ▲ +0.3%` runs, values in the theme's green and red, trimmed from the end to the room the bar has, down to a lone glyph. Left click opens the panel, middle click refreshes.
- The panel: the watchlist grouped into Stocks, Crypto and Currencies, with the helper's status lines, the data attribution and the update time under it. `j`/`k` move, `r` refreshes, Escape closes. Unpriced rows are dimmed and say why.
- `omarchy-shell costafot.markets open | close | toggle | refresh`; `refresh` reaches every monitor's bar.
- Settings on the shell.json entry: `refreshMinutes`, `strip`, `stripShowPrice`, `stripMax`, `showRateLimitErrors`.
- Direction colours come from the active theme's `colors.toml` (`green`/`red`, else `color2`/`color1`, else accent/urgent) and follow a theme switch without a restart.

## 0.1.0

- The data core: `bin/markets`, a stdlib-only Python helper that prices, searches and charts crypto through CoinGecko with no API key.
- Watchlist and favorites on disk, seeded with the nine instruments the Windows extension started with; BTC, ETH and SOL start starred.
- Keep-last-good quote cache, five-minute chart cache, 429 back-off with `Retry-After`, 1 MiB response cap, no redirects.
- Never-crash contract: every command prints one JSON line and exits 0.
- 83 offline unit tests. No widget yet — the manifest ships a placeholder so `omarchy plugin validate` passes.

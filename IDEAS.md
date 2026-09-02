# Ideas

Backlog, not commitments. Dead ideas stay here marked as such so they are not re-pitched.

- Elbstream asset logos in rows (needs the helper to download into a capped cache; the attribution row ships with it).
- Optional CoinGecko demo key (`x-cg-demo-api-key`) for 30 req/min — the header hook exists in `providers/coingecko.py`.
- Incremental news paging with Finnhub's `minId`.
- A sparkline per strip entry (`/coins/markets?sparkline=true` is free data).
- A `service` kind owning one poller for all monitors instead of `--max-age` dedupe. Changes how `summon`/`toggle` route; do not add casually.
- User-controlled ordering of favorites (today: category, then symbol).
- A scrolling ticker-tape strip mode (`strip: "tape"`): `FrameAnimation` advancing an offset modulo one run width, hover and right-click pause, `WidgetButton.fixedWidth` (omarchy-stocks does this). The shipped strip is static and width-degrading.
- Search-as-you-type with a ~260 ms debounce and Tab to fill the highlighted ticker (stochi). Rejected for 1.0 because search is Enter-only by design; cheap to revisit now that stock search is Yahoo's.
- Market state on the detail hero (OPEN / PRE / AFTER / CLOSED from Yahoo's `currentTradingPeriod`, as stochi shows it). Needs the per-symbol `chart` call, not the `spark` batch.
- Yahoo as a crypto fallback (`BTC-USD` form) when CoinGecko is rate-limited. Routing is first-`supports`, so this needs a "next provider on invalid" rule; not added casually.
- The Yahoo `spark` batch already returns a day of closes per symbol, so a per-strip sparkline costs no extra request once session 3 lands.

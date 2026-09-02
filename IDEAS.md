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
- The Yahoo `spark` batch already returns a day of closes per symbol, so a per-strip sparkline for stocks and FX costs no extra request (the helper drops them today).
- Remember Yahoo 404s in `yahoo-meta.json` with a timestamp so an unknown or delisted symbol is retried hourly instead of every poll.
- Strip width degradation does not engage for a right-section widget: with five favorites on a 2560 px bar the strip painted over the centre clock (seen 2026-09-03 when TSLA joined BTC/DOGE/ETH/SOL). The bar functions it reads (`moduleSlots`, `slotWindow`, `layoutEntries`, `entryId`) all exist, so the budget formula is what is wrong for the right section. Costa: fine for now; revisit with a second monitor or a narrower bar.
- Sub-cent prices on the detail hero read `$0.00` (`fmt.money` is the Windows two-decimal rule); the strip already uses four decimals under $1. Consider the same for the hero.
- A hover readout on the chart: the price and time under the pointer, as a crosshair. The helper would have to ship a label per point (it formats every string), so 300 more strings per chart.
- A previous-close line on the crypto day chart. CoinGecko states none; `price − change` from the quote would do, but the 24 h series is rolling, so it is just the first point.
- Reset the chart range to 1D when the panel opens (today it is sticky for the shell's lifetime; a 5Y left on Monday is still 5Y on Friday).

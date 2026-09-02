# Ideas

Backlog, not commitments. Dead ideas stay here marked as such so they are not re-pitched.

- Elbstream asset logos in rows (needs the helper to download into a capped cache; the attribution row ships with it).
- Optional CoinGecko demo key (`x-cg-demo-api-key`) for 30 req/min — the header hook exists in `providers/coingecko.py`.
- Incremental news paging with Finnhub's `minId`.
- A sparkline per strip entry (`/coins/markets?sparkline=true` is free data).
- A `service` kind owning one poller for all monitors instead of `--max-age` dedupe. Changes how `summon`/`toggle` route; do not add casually.
- User-controlled ordering of favorites (today: category, then symbol).

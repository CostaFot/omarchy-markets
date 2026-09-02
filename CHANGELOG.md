# Changelog

## 0.1.0

- The data core: `bin/markets`, a stdlib-only Python helper that prices, searches and charts crypto through CoinGecko with no API key.
- Watchlist and favorites on disk, seeded with the nine instruments the Windows extension started with; BTC, ETH and SOL start starred.
- Keep-last-good quote cache, five-minute chart cache, 429 back-off with `Retry-After`, 1 MiB response cap, no redirects.
- Never-crash contract: every command prints one JSON line and exits 0.
- 83 offline unit tests. No widget yet — the manifest ships a placeholder so `omarchy plugin validate` passes.

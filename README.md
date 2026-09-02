# Markets for Omarchy

Stocks, crypto and currencies in the [Omarchy](https://omarchy.org) bar. A live ticker strip, and a keyboard-driven panel with search, watchlist, favorites, portfolio, news and charts.

A port of the [Markets extension for Command Palette](https://github.com/CostaFot/MarketExtension) on Windows.

> Work in progress. Version 0.1.0 is the data core only; the bar widget arrives in 0.2.0.

## Install

```bash
omarchy plugin add https://github.com/CostaFot/omarchy-markets --enable
```

## Uninstall

```bash
omarchy plugin remove costafot.markets
rm -rf ~/.local/state/omarchy/costafot.markets
```

## The helper

Everything that touches the network or the disk is `bin/markets`, a Python 3 script with no dependencies. The widget runs it and draws what comes back. You can run it yourself:

```bash
bin/markets snapshot | jq '.strip'
bin/markets search sol | jq '.results[0]'
bin/markets candles BTC 1M | jq '.series.range_change_text'
```

**Leaves your machine:**

- The symbols you track go to CoinGecko (`api.coingecko.com`) to fetch prices, search results and chart history. No key, no account.

Nothing else. No telemetry.

## FAQ

**Is this financial advice?** No. Prices are delayed and best effort.

**Why is AAPL not priced?** Stocks and currencies need a provider that is not wired up yet. They arrive with Twelve Data and Frankfurter support.

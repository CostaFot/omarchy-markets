# Markets for Omarchy

Stocks, crypto and currencies in the [Omarchy](https://omarchy.org) bar. A live ticker strip, and a keyboard-driven panel with search, watchlist, favorites, portfolio, news and charts.

A port of the [Markets extension for Command Palette](https://github.com/CostaFot/MarketExtension) on Windows.

> Work in progress. Version 0.2.0 has the bar strip and the watchlist panel, with crypto priced through CoinGecko. Stocks and currencies show up in the list but are not priced until the next release.

## Install

```bash
omarchy plugin add https://github.com/CostaFot/omarchy-markets --enable
```

## Uninstall

```bash
omarchy plugin remove costafot.markets
rm -rf ~/.local/state/omarchy/costafot.markets
```

## In the bar

The strip lists your favorites as `BTC $77,250 ▲ +0.3%`, the value in your theme's green or red. When the bar runs out of room the strip drops entries from the end, then collapses to a single glyph; hover it for the full text. Left click opens the panel, middle click refreshes.

Out of the box the favorites are BTC, ETH and SOL. The panel lists the watchlist grouped into Stocks, Crypto and Currencies.

| Key | Does |
|---|---|
| `j` / `k`, arrows | Move |
| `r` | Refresh now |
| Esc | Close |

From a keybinding or a script:

```bash
omarchy-shell costafot.markets toggle    # also open, close, show, hide
omarchy-shell costafot.markets refresh
```

## Settings

Inline on the plugin's entry in `~/.config/omarchy/shell.json`, or through `omarchy bar set`:

| Key | Default | Meaning |
|---|---|---|
| `refreshMinutes` | `10` | Poll interval; `0` turns polling off (the panel still refreshes when opened) |
| `strip` | `"favorites"` | What the strip lists: `favorites` or `watchlist` |
| `stripShowPrice` | `true` | Off shows only the change percentage |
| `stripMax` | `6` | Most entries the strip lists before trimming for width |
| `showRateLimitErrors` | `true` | Off hides the provider's rate-limit line in the panel |

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

**Why is AAPL not priced?** Stocks and currencies need a provider that is not wired up yet. They arrive in the next data-core release, keyless, through Yahoo Finance.

**The strip is not green and red.** The colours come from the active theme's `colors.toml`: `green`/`red`, else the ANSI `color2`/`color1`, else the theme accent for up and urgent for down.

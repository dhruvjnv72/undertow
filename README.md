# Undertow: liquidation radar for Perpl (Monad)

*See the pull before it takes you.* A read-only risk screen for [Perpl](https://perpl.xyz), a perpetual-futures exchange that runs
fully on Monad. Every position on Perpl is public, so Undertow can compute where every trader would be liquidated, show where those
points bunch up, estimate how far a chain reaction could push the price, and warn a trader about other people's liquidations that sit
between the price and their own.

* **Live**: a terminal-style screen updated every block from Perpl's own public WebSocket (oracle, mark, last, order book, trades), with
  signals computed against the live mark. Needs `pip install websockets`.
* **Radar**: liquidation zones, a plain-language warning, a cascade simulator (a labelled worst-case estimate).
* **Trade**: order preview with liquidation price, zones in your path, fees, funding per day and slippage. It never sends an order.
* **Wallets**: look up any address: positions with when each was opened, liquidation distance, zones in its path, open (resting) orders, and its position history
  with win rate, profit factor, streaks and holding time (read straight from the chain into a local database).
* **Markets / Protocol / Bot**: market table; exchange-wide numbers (volume, fees, deposits, liquidations via the Envio
  indexer); wallet search with watchlist and compare; the market-making bot's state.
* **Checks**: `python -m undertow.verify` (or the button on the Protocol tab) compares our numbers with Perpl's own public API.

The app uses only the Python standard library, except the Live tab which needs `websockets` (`pip install -r requirements.txt`). Optional: `reportlab` and `pymupdf` (the PDFs in `docs/`), `eth-account` (`undertow/testnet.py`).
Needs `perpl-cli` (`cargo install perpl-cli`).

    start_dashboard.bat                      # Windows: double-click; starts the dashboard and opens the browser
    python -m undertow.server --refresh      # same thing from a terminal; data refreshes itself every ~45 s
    python -m undertow.history_loader --days 7   # load more wallet history (the dashboard loads 1 day by itself); --check verifies it
    python -m undertow.verify                # correctness checks against Perpl's REST API
    python -m undertow.bot --paper           # paper bot (reads the data the server keeps fresh)
    python -m unittest discover -s tests     # 112 tests

If the default Monad RPC is slow, rate-limited or unreachable, set `UNDERTOW_RPC` (for example `https://rpc2.monad.xyz`, or your own
Alchemy URL): `$env:UNDERTOW_RPC="https://rpc2.monad.xyz"`. On the machine this was built on, `rpc.monad.xyz` was unreachable at times.

## Live data vs snapshot data

Prices, the order book and trades on the **Live** tab are pushed by Perpl every block (about 0.4 s). Liquidation zones come from the positions
snapshot (refreshed every ~45 s) and are re-measured against the live mark on every update, so a zone's distance is always live but a
position opened a few seconds ago is not yet in it.

## Keeping the data fresh

The dashboard refreshes itself every ~45 seconds, but only while the computer is awake and the server window is open. If the computer sleeps the
feed pauses (the page then shows a red banner with the age) and it resumes by itself within about a minute of waking. For recording videos, set
Windows to never sleep while plugged in; for an always-on dashboard, host it (docs/DEPLOY.md).

## Networks

`UNDERTOW_NETWORK=testnet` switches every module to Perpl's testnet (separate data in `data/testnet/`; BTC is market 16, not 1).
Example: `$env:UNDERTOW_NETWORK="testnet"; python -m undertow.server --refresh --port 8001`.

## The bot (paper mode, live mode locked)

Post-only market maker that leans against inventory, widens or pulls quotes near big liquidation zones, and halts on a daily-loss,
leverage or own-liquidation-distance limit or a `KILL` file. Every order expires by itself in about a minute. Live mode needs
`UNDERTOW_ALLOW_LIVE=yes` and a key; mainnet additionally needs `UNDERTOW_ALLOW_MAINNET=yes`. It has **not** sent an order on any
network. Testnet runbook:

1. `python -m undertow.testnet new` creates a throwaway testnet key in `data/testnet/key.txt` (git-ignored) and prints its address.
2. Fund that address with testnet MON (gas) and at least 100 test aUSD (Perpl's testnet minimum). The test token cannot be minted freely.
3. `python -m undertow.testnet status`, then `python -m undertow.testnet fund 100`.
4. Keep `UNDERTOW_NETWORK=testnet python -m undertow.server --refresh --port 8001` running, then:
   `$env:UNDERTOW_NETWORK="testnet"; $env:UNDERTOW_ALLOW_LIVE="yes"; python -m undertow.bot --live --cycles 20`

Never commit keys. `data/` is git-ignored.

## Indexer and hosting

* `indexer/` is an Envio HyperIndex project (needs WSL 2 and Docker on Windows); see `indexer/README.md`.
* `Dockerfile` and `docs/DEPLOY.md` cover hosting the read-only dashboard.
* `docs/ARCHITECTURE.md`, `docs/SUBMISSION_CHECKLIST.md`, `docs/VIDEO_SCRIPTS.md`, `docs/logo.svg`.

## Honest limits

Data is a rolling snapshot (fresh to about a minute, not to the block). The cascade simulator is not validated against a real
historical liquidation. Liquidation-aware order rules are checked about once a minute and live inside Undertow, not on Perpl.
Wallet statistics cover only the history loaded so far and are price profit and funding only (no trading fees or rebates).

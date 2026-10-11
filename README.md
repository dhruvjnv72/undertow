# Undertow: liquidation radar for Perpl (Monad)

Local dashboard, data layer, risk engine, a market-making bot (paper mode, plus a locked live mode) and an Envio indexer.
The app itself uses only the Python standard library. `reportlab` is needed to rebuild the PDFs in `docs/`, `eth-account`
for `undertow/testnet.py`.

    python -m undertow.server --refresh      # dashboard at http://localhost:8000, data refreshes itself
    python -m undertow.bot --paper           # paper bot (reads the data the server keeps fresh)
    python -m undertow.refresher --once      # one data refresh without the dashboard
    python -m unittest discover -s tests     # 50 tests

Requires `perpl-cli` (cargo install perpl-cli). Pass `--rpc <your url>` (for example Alchemy) for faster refreshes.
Tabs: Radar (zones, cascade simulator), Markets, Protocol, Wallets (search, watchlist, compare), Bot.

## Networks

`UNDERTOW_NETWORK=testnet` switches every module to Perpl's testnet (separate data in `data/testnet/`, BTC is market 16).
In PowerShell: `$env:UNDERTOW_NETWORK="testnet"; python -m undertow.server --refresh --port 8001`.

## Testing the bot on testnet (before any mainnet use)

1. `python -m undertow.testnet new` creates a throwaway testnet key in `data/testnet/key.txt` and prints its address.
2. Send that address testnet MON (gas) from a faucet and at least 100 test aUSD (the testnet minimum account deposit).
   Test aUSD cannot be minted by anyone (the token's mint is role-gated); get it through Perpl's testnet app.
3. `python -m undertow.testnet status`, then `python -m undertow.testnet fund 100` (approve + create the Perpl account).
4. Keep `UNDERTOW_NETWORK=testnet python -m undertow.server --refresh --port 8001` running, then in another window:
   `$env:UNDERTOW_NETWORK="testnet"; $env:UNDERTOW_ALLOW_LIVE="yes"; python -m undertow.bot --live --cycles 20`
5. Watch the Bot tab on port 8001. Orders are post-only and expire by themselves in about a minute.

Mainnet live mode additionally needs `UNDERTOW_ALLOW_MAINNET=yes` and `PERPL_PRIVATE_KEY` for a SEPARATE small wallet.
Never commit keys; `data/` is git-ignored.

## Indexer

`indexer/` is an Envio HyperIndex project (needs WSL 2 + Docker on Windows). See `indexer/README.md`.

Layout: `undertow/` code, `undertow/web/index.html` dashboard, `data/` generated data, `abi/Exchange.json` Perpl contract ABI,
`indexer/` Envio indexer, `docs/` report builders. Progress PDFs are in the project root.

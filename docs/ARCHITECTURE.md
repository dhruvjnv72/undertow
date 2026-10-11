# Undertow architecture

Undertow is a risk screen for Perpl, a perpetual-futures exchange that runs fully on Monad. Because every position is public
on-chain, we can compute where every trader would be liquidated, show where those points bunch up, and warn a trader about
other people's liquidations that sit between the price and their own.

```
 Monad (chain 143)                         Perpl public REST API
   Perpl Exchange contract                   (used only to VERIFY our numbers)
        |                |                              |
        | perpl-cli      | Envio indexer                | undertow/verify.py
        | snapshot       | (events -> Postgres/GraphQL) |
        v                v                              v
  undertow/refresher.py   indexer/  --------------->  checks: ticker, order book,
  (every ~45 s)              |                         volume, funding, formula
        |                    |
        v                    v
  data/parsed/snapshot_latest.json     http://localhost:8080/v1/graphql
        |                    |
        +--------+-----------+
                 v
        undertow/risk.py   liquidation maths, zones, cascade simulator
        undertow/preview.py  order preview (liquidation, zones in path, fees, funding, slippage)
        undertow/protect.py  liquidation-aware order rules
                 |
        +--------+-------------------+
        v                            v
  undertow/server.py            undertow/bot.py
  dashboard (web/index.html)    market-making bot: paper mode, locked live mode
  Radar | Trade | Markets | Protocol | Wallets | Bot
```

## Layers (as in the roadmap)

1. **Data**: `perpl_cli.py`, `snapshot_parser.py`, `refresher.py`, `onchain.py`, `accounts.py`, `blocks.py`, and the Envio
   indexer in `indexer/`. A full snapshot of every account, position and resting order takes 35 to 70 seconds on a public RPC.
2. **Risk engine**: `risk.py`. Liquidation formula taken from the official SDK; zones; cascade simulator (a worst-case estimate
   with stated assumptions); the Radar sentence.
3. **Screens**: `server.py` plus `web/index.html` (Radar, Trade, Markets, Protocol, Wallets, Bot).
4. **Trading**: `preview.py` shows the risk of a trade before it is placed. The page does not send orders.
5. **Protection**: `bot.py` (post-only market maker with hard limits, widens or pulls near big zones) and `protect.py`
   (liquidation-aware order rules).

## Verified against Perpl itself (`python -m undertow.verify`)

* Mark price and open interest equal Perpl's REST ticker.
* Order book: top 10 levels per side identical in price, size and order count to Perpl's REST book, read at the same block
  (the REST book labelled block b is the contract state at block b-1).
* Our liquidation formula equals Perpl's own liquidation and bankruptcy prices for every open position.
* Hourly volume and funding events from the indexer are compared with Perpl's candles and funding series once the indexer has
  covered whole hours (see the report for the current result).

## Honest limits

* Data is a rolling snapshot, fresh to about a minute, not to the block. Liquidation-aware orders are therefore checked about
  once a minute and live inside Undertow; Perpl's contract does not know about them.
* The cascade simulator is a worst-case estimate, not a prediction. It assumes liquidated size goes through the order book
  (Perpl's docs say it does first, with the PLP vault taking what is too large), that makers do not pull orders, and (in stress
  mode) that the mark price follows the book. It has not been tested against a real historical liquidation.
* The bot has run only in paper mode. Live mode is locked behind environment switches and has not sent an order on any network.

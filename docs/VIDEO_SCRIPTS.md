# Video scripts

Record the screen at http://localhost:8000 (or the hosted link) with the dashboard already loaded and data fresh ("Data Ns old"
in the top right should be under a minute). Speak plainly. Each script lists what to show and what to say. Times are targets.

Rules for all videos: say only what the product does today (see "Things never to claim" in `SUBMISSION_CHECKLIST.md`).

## 0. Open every video on the Live tab

It is the most convincing screen: it moves. Say: "This is Perpl's own live feed: prices, order book and trades arrive every block. Our zones are re-measured against the live mark every half second." Do not call alerts predictions; they are rule-based risk flags.

## 1. Track 1 technical demo (up to 3:00)

| Time | Show | Say |
|---|---|---|
| 0:00 | Radar tab, BTC | "Undertow is a risk screen for Perpl on Monad. Perpl shows you your own liquidation price. It does not show you everyone else's." |
| 0:15 | Zone chart | "Because every position on Perpl is public on-chain, we read all of them and work out exactly where each trader gets liquidated. Red bars are longs closed if the price falls, green bars are shorts closed if it rises. Bar height is position value." |
| 0:40 | Headline sentence, then zone table | "This sentence names the biggest danger right now, with the order-book depth against it and whether one account holds most of it." |
| 1:00 | Move the cascade slider | "The cascade simulator asks: if the price reaches here, what is liquidated and how far could the forced orders push it? It is a worst-case estimate and the assumptions are listed under every result." |
| 1:30 | Trade tab: change leverage | "Before you trade, the order preview shows your liquidation, other traders' zones between you and it, fees, funding per day and slippage. Lower leverage is not always safer here, because it moves your liquidation past more zones." |
| 2:00 | Protocol tab, Envio panel | "The Protocol view is powered by an Envio indexer that records Perpl's events: volume, fees, deposits, withdrawals, liquidations." |
| 2:20 | Run `python -m undertow.verify` in a terminal | "We check ourselves against Perpl's own public API: mark price and open interest match, the order book matches level for level at the same block, and our liquidation formula matches Perpl's for every open position." |
| 2:45 | Bot tab | "A risk-aware market-making bot uses the same zone data: it widens or pulls its quotes near big zones. It is running in paper mode." |

## 2. Track 1 pitch (up to 2:00)

* **Problem (0:00 to 0:25).** "Perpl traders see their own liquidation price. The bigger danger is other traders' liquidations: when many positions are liquidated at nearly the same price, the forced selling can push the price down to you. On 10 October 2025 about $19 billion was liquidated in a day. Nobody shows this on Perpl."
* **Why only here (0:25 to 0:50).** "Perpl runs fully on Monad, so every position is public. Big exchanges keep positions private, so this exact map cannot be drawn for them."
* **Product (0:50 to 1:20).** "Undertow shows liquidation zones, simulates the cascade, warns you before you trade, and powers a bot that backs away from danger."
* **Honesty and traction (1:20 to 1:45).** "Perpl is young: zones are tens to hundreds of thousands of dollars today, not millions. We verified our numbers against Perpl's own API. Next step: let any trader connect their own account."
* **Close (1:45).** "See the pull before it takes you."

## 3. Bot video (up to 2:00), only record what is true

Show: the Bot tab, the hard-limit table in `docs` or the Step 4 report, `data/bot/log.jsonl`, and the unit tests passing
(`python -m unittest discover -s tests`).

Say: what it does (post-only quotes, widens or pulls near zones, halts on daily loss, leverage, liquidation distance or a kill file),
the safety design (orders expire by themselves, increasing request numbers, live mode locked behind two switches). State clearly
whether it ran in paper mode or on testnet or mainnet. Do **not** show or claim real on-chain trades unless they exist; if you
run it on testnet first, show the testnet order and fill.

## 4. Dashboard video (up to 2:00)

Radar (30 s) -> Wallets: search an address, show positions with when each opened, the "zones in your path" warning, the open orders and the history and statistics (state that they cover the loaded window and exclude fees), add two wallets to the watchlist and
show the compare table (50 s) -> Protocol (25 s) -> Markets (15 s). Mention that the page is read-only.
